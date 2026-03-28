"""La Marzocco Cloud API adapter.

Drop-in replacement for the BLE adapter when BLE is unreliable.
Uses pylamarzocco's cloud client with websocket for live state updates.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Callable

import aiohttp

from pylamarzocco import LaMarzoccoCloudClient
from pylamarzocco.const import MachineMode, SteamTargetLevel, WidgetType
from pylamarzocco.models import (
    CoffeeBoiler,
    MachineStatus,
    SteamBoilerLevel,
    ThingDashboardWebsocketConfig,
)
from pylamarzocco.util import InstallationKey, generate_installation_key

from espresso_bridge.core.models import LaMarzoccoState

logger = logging.getLogger(__name__)

STEAM_LEVEL_MAP = {
    1: SteamTargetLevel.LEVEL_1,
    2: SteamTargetLevel.LEVEL_2,
    3: SteamTargetLevel.LEVEL_3,
}
STEAM_LEVEL_REVERSE = {v: k for k, v in STEAM_LEVEL_MAP.items()}

COFFEE_TEMP_MIN = 85.0
COFFEE_TEMP_MAX = 104.0


class LaMarzoccoCloudAdapter:
    """Cloud API adapter for the La Marzocco, mirroring the BLE adapter interface."""

    def __init__(
        self,
        username: str,
        password: str,
        key_path: str = "/etc/espresso-bridge/lm_install_key.json",
        on_state_change: Callable[[LaMarzoccoState], None] | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self._key_path = Path(key_path)
        self._on_state_change = on_state_change

        self._state = LaMarzoccoState()
        self._client: LaMarzoccoCloudClient | None = None
        self._session: aiohttp.ClientSession | None = None
        self._serial: str | None = None
        self._ws_task: asyncio.Task | None = None

    @property
    def state(self) -> LaMarzoccoState:
        return self._state

    @property
    def connected(self) -> bool:
        return self._client is not None and self._serial is not None

    # -- Connection --

    async def connect(self) -> bool:
        """Connect to the La Marzocco cloud API.

        On first run, generates an installation key and registers the client.
        On subsequent runs, loads the saved key from disk.
        """
        try:
            key = self._load_or_generate_key()

            self._session = aiohttp.ClientSession()
            self._client = LaMarzoccoCloudClient(
                username=self._username,
                password=self._password,
                installation_key=key,
                client=self._session,
            )

            # Register client if this is a new key
            if not self._key_path.exists():
                logger.info("Cloud: registering new installation key")
                await self._client.async_register_client()
                self._save_key(key)

            # Discover the machine serial number
            things = await self._client.list_things()
            if not things:
                logger.error("Cloud: no machines found on account")
                await self._close_session()
                return False

            self._serial = things[0].serial_number
            logger.info(f"Cloud: found machine {things[0].name} ({self._serial})")

            # Get initial dashboard state
            dashboard = await self._client.get_thing_dashboard(self._serial)
            self._update_state_from_config(dashboard.config)

            # Start websocket for live updates
            self._ws_task = asyncio.create_task(self._run_websocket())

            logger.info("Cloud: connected successfully")
            return True

        except Exception:
            logger.exception("Cloud: failed to connect")
            await self._close_session()
            return False

    async def connect_silent(self, **kwargs: Any) -> bool:
        """Alias for connect — matches BLE adapter interface."""
        return await self.connect()

    async def disconnect(self) -> None:
        """Disconnect from the cloud API."""
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None

        await self._close_session()
        self._state = self._state.model_copy(update={"connected": False})
        self._notify_change()
        logger.info("Cloud: disconnected")

    # -- Commands --

    async def set_power(self, enabled: bool) -> bool:
        """Turn the machine on or off."""
        if not self._client or not self._serial:
            logger.error("Cloud: not connected")
            return False
        try:
            ok = await self._client.set_power(self._serial, enabled)
            if ok:
                self._state = self._state.model_copy(update={"turned_on": enabled})
                self._notify_change()
                logger.info(f"Cloud: power {'on' if enabled else 'off'}")
            return ok
        except Exception:
            logger.exception("Cloud: failed to set power")
            return False

    async def set_coffee_temp(self, temperature: float) -> bool:
        """Set the coffee boiler target temperature (85–104°C)."""
        if not self._client or not self._serial:
            logger.error("Cloud: not connected")
            return False
        if temperature < COFFEE_TEMP_MIN or temperature > COFFEE_TEMP_MAX:
            logger.error(f"Coffee temp must be {COFFEE_TEMP_MIN}–{COFFEE_TEMP_MAX}°C")
            return False
        try:
            ok = await self._client.set_coffee_target_temperature(
                self._serial, temperature
            )
            if ok:
                self._state = self._state.model_copy(
                    update={"coffee_temp_target": temperature}
                )
                self._notify_change()
                logger.info(f"Cloud: coffee boiler target: {temperature}°C")
            return ok
        except Exception:
            logger.exception("Cloud: failed to set coffee temp")
            return False

    async def set_steam_level(self, level: int) -> bool:
        """Set steam level (1, 2, or 3) for Linea Micra."""
        if not self._client or not self._serial:
            logger.error("Cloud: not connected")
            return False
        if level not in STEAM_LEVEL_MAP:
            logger.error("Steam level must be 1, 2, or 3")
            return False
        try:
            ok = await self._client.set_steam_target_level(
                self._serial, STEAM_LEVEL_MAP[level]
            )
            if ok:
                self._state = self._state.model_copy(update={"steam_level": level})
                self._notify_change()
                logger.info(f"Cloud: steam level: {level}")
            return ok
        except Exception:
            logger.exception("Cloud: failed to set steam level")
            return False

    async def set_steam_enabled(self, enabled: bool) -> bool:
        """Enable or disable the steam boiler."""
        if not self._client or not self._serial:
            logger.error("Cloud: not connected")
            return False
        try:
            ok = await self._client.set_steam(self._serial, enabled)
            if ok:
                self._state = self._state.model_copy(
                    update={"steam_enabled": enabled}
                )
                self._notify_change()
                logger.info(
                    f"Cloud: steam boiler {'enabled' if enabled else 'disabled'}"
                )
            return ok
        except Exception:
            logger.exception("Cloud: failed to set steam")
            return False

    async def refresh_state(self) -> LaMarzoccoState:
        """Refresh state from the cloud dashboard."""
        if not self._client or not self._serial:
            return self._state
        try:
            dashboard = await self._client.get_thing_dashboard(self._serial)
            self._update_state_from_config(dashboard.config)
        except Exception:
            logger.exception("Cloud: failed to refresh state")
            self._state = self._state.model_copy(update={"connected": False})
            self._notify_change()
        return self._state

    # -- Internal --

    def _load_or_generate_key(self) -> InstallationKey:
        """Load a saved installation key or generate a new one."""
        if self._key_path.exists():
            logger.info(f"Cloud: loading installation key from {self._key_path}")
            data = self._key_path.read_text()
            return InstallationKey.from_json(data)

        logger.info("Cloud: generating new installation key")
        installation_id = str(uuid.uuid4())
        return generate_installation_key(installation_id)

    def _save_key(self, key: InstallationKey) -> None:
        """Save the installation key to disk for reuse across restarts."""
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        self._key_path.write_text(key.to_json())
        logger.info(f"Cloud: saved installation key to {self._key_path}")

    def _update_state_from_config(
        self, config: dict[WidgetType, Any]
    ) -> None:
        """Extract machine state from the cloud dashboard widget config."""
        updates: dict[str, Any] = {"connected": True}

        machine_status = config.get(WidgetType.CM_MACHINE_STATUS)
        if isinstance(machine_status, MachineStatus):
            updates["turned_on"] = machine_status.mode == MachineMode.BREWING_MODE

        coffee_boiler = config.get(WidgetType.CM_COFFEE_BOILER)
        if isinstance(coffee_boiler, CoffeeBoiler):
            updates["coffee_boiler_enabled"] = coffee_boiler.enabled
            updates["coffee_temp_target"] = coffee_boiler.target_temperature

        steam_boiler = config.get(WidgetType.CM_STEAM_BOILER_LEVEL)
        if isinstance(steam_boiler, SteamBoilerLevel):
            updates["steam_enabled"] = steam_boiler.enabled
            if steam_boiler.target_level in STEAM_LEVEL_REVERSE:
                updates["steam_level"] = STEAM_LEVEL_REVERSE[
                    steam_boiler.target_level
                ]

        self._state = self._state.model_copy(update=updates)
        self._notify_change()

    def _on_websocket_message(
        self, msg: ThingDashboardWebsocketConfig
    ) -> None:
        """Handle a websocket dashboard update."""
        self._update_state_from_config(msg.config)

    def _on_websocket_connect(self) -> None:
        logger.debug("Cloud: websocket connected")

    def _on_websocket_disconnect(self) -> None:
        logger.warning("Cloud: websocket disconnected")

    async def _run_websocket(self) -> None:
        """Run the websocket connection for live state updates."""
        if not self._client or not self._serial:
            return
        try:
            await self._client.websocket_connect(
                self._serial,
                notification_callback=self._on_websocket_message,
                connect_callback=self._on_websocket_connect,
                disconnect_callback=self._on_websocket_disconnect,
                auto_reconnect=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Cloud: websocket error")

    async def _close_session(self) -> None:
        """Close the aiohttp session and clear client references."""
        self._client = None
        self._serial = None
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    def _notify_change(self) -> None:
        if self._on_state_change:
            self._on_state_change(self._state)

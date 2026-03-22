"""La Marzocco Linea Micra BLE adapter.

Communicates with the La Marzocco espresso machine over Bluetooth Low Energy
using the pylamarzocco library. Supports power control, boiler temperature,
steam control, and state reading.

Requires one-time credential setup:
  - communication_key: BLE auth token (extracted from cloud API — see docs)
"""

from __future__ import annotations

import logging
from typing import Callable

from bleak import BleakScanner
from bleak.backends.device import BLEDevice

from espresso_bridge.core.models import LaMarzoccoState

logger = logging.getLogger(__name__)

try:
    from pylamarzocco.clients._bluetooth import (
        LaMarzoccoBluetoothClient as PyLMBluetoothClient,
    )
    from pylamarzocco.const import BoilerType, MachineMode

    PYLAMARZOCCO_AVAILABLE = True
except ImportError:
    PYLAMARZOCCO_AVAILABLE = False
    logger.warning(
        "pylamarzocco not installed — La Marzocco features disabled. "
        "pip install pylamarzocco"
    )

# Linea Micra steam levels map to specific temperatures
STEAM_LEVEL_MAP = {
    1: 126,  # SteamLevel.LEVEL_1
    2: 128,  # SteamLevel.LEVEL_2
    3: 131,  # SteamLevel.LEVEL_3
}
STEAM_TEMP_TO_LEVEL = {v: k for k, v in STEAM_LEVEL_MAP.items()}

COFFEE_TEMP_MIN = 85.0
COFFEE_TEMP_MAX = 104.0

BT_MODEL_PREFIXES = ("MICRA", "MINI", "GS3", "LM")


class LaMarzoccoAdapter:
    """BLE adapter for the La Marzocco Linea Micra.

    Uses pylamarzocco which handles BLE connect/auth/retry internally.
    The adapter manages scanning and state tracking.
    """

    def __init__(
        self,
        communication_key: str,
        on_state_change: Callable[[LaMarzoccoState], None] | None = None,
        # Legacy params — kept for config compat but not used by pylamarzocco BLE
        username: str = "",
        serial_number: str = "",
    ):
        if not PYLAMARZOCCO_AVAILABLE:
            raise RuntimeError("pylamarzocco not installed. Run: pip install pylamarzocco")

        self._communication_key = communication_key
        self._on_state_change = on_state_change

        self._client: PyLMBluetoothClient | None = None
        self._device: BLEDevice | None = None
        self._state = LaMarzoccoState()

    @property
    def state(self) -> LaMarzoccoState:
        return self._state

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    # -- Scanning --

    @staticmethod
    async def scan(timeout: float = 10.0) -> list[BLEDevice]:
        """Scan for La Marzocco machines by name prefix."""
        devices = await BleakScanner.discover(timeout=timeout)
        return [
            d for d in devices
            if d.name and d.name.startswith(BT_MODEL_PREFIXES)
        ]

    # -- Connection --

    async def connect(
        self, device: BLEDevice | None = None, address: str | None = None
    ) -> bool:
        """Connect and verify by reading machine mode."""
        if not PYLAMARZOCCO_AVAILABLE:
            return False

        device = await self._resolve_device(device, address)
        if device is None:
            return False

        try:
            self._device = device
            self._client = PyLMBluetoothClient(
                ble_device=device,
                ble_token=self._communication_key,
            )

            mode = await self._client.get_machine_mode()
            turned_on = mode == MachineMode.BREWING_MODE
            self._state = self._state.model_copy(
                update={"connected": True, "turned_on": turned_on}
            )
            self._notify_change()
            logger.info(f"Connected to La Marzocco ({device.name}), mode={mode.value}")
            return True

        except Exception:
            logger.exception("Failed to connect to La Marzocco")
            self._client = None
            return False

    async def connect_silent(
        self, device: BLEDevice | None = None, address: str | None = None
    ) -> bool:
        """Set up client and verify connection via a lightweight read."""
        return await self.connect(device=device, address=address)

    async def disconnect(self) -> None:
        """Disconnect from the machine."""
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                logger.debug("Error during disconnect", exc_info=True)
        self._client = None
        self._state = self._state.model_copy(update={"connected": False})
        self._notify_change()

    # -- Commands --

    async def set_power(self, enabled: bool) -> bool:
        """Turn the machine on or off."""
        if not self._client:
            logger.error("Not connected")
            return False
        try:
            status = await self._client.set_power(enabled)
            logger.info(f"Power {'on' if enabled else 'off'}: {status.status}")
            self._state = self._state.model_copy(update={"turned_on": enabled})
            self._notify_change()
            return True
        except Exception:
            logger.exception("Failed to set power")
            self._mark_disconnected()
            return False

    async def set_coffee_temp(self, temperature: float) -> bool:
        """Set the coffee boiler target temperature (85–104°C)."""
        if not self._client:
            logger.error("Not connected")
            return False
        if temperature < COFFEE_TEMP_MIN or temperature > COFFEE_TEMP_MAX:
            logger.error(f"Coffee temp must be {COFFEE_TEMP_MIN}–{COFFEE_TEMP_MAX}°C")
            return False
        try:
            status = await self._client.set_temp(BoilerType.COFFEE, temperature)
            logger.info(f"Coffee boiler target: {temperature}°C ({status.status})")
            self._state = self._state.model_copy(
                update={"coffee_temp_target": temperature}
            )
            self._notify_change()
            return True
        except Exception:
            logger.exception("Failed to set coffee temp")
            self._mark_disconnected()
            return False

    async def set_steam_level(self, level: int) -> bool:
        """Set steam level (1, 2, or 3) for Linea Micra."""
        if not self._client:
            logger.error("Not connected")
            return False
        if level not in STEAM_LEVEL_MAP:
            logger.error("Steam level must be 1, 2, or 3")
            return False

        temp = STEAM_LEVEL_MAP[level]
        try:
            status = await self._client.set_temp(BoilerType.STEAM, float(temp))
            logger.info(f"Steam level: {level} ({temp}°C) ({status.status})")
            self._state = self._state.model_copy(
                update={"steam_level": level, "steam_temp_target": float(temp)}
            )
            self._notify_change()
            return True
        except Exception:
            logger.exception("Failed to set steam level")
            self._mark_disconnected()
            return False

    async def set_steam_enabled(self, enabled: bool) -> bool:
        """Enable or disable the steam boiler."""
        if not self._client:
            logger.error("Not connected")
            return False
        try:
            status = await self._client.set_steam(enabled)
            logger.info(f"Steam boiler {'enabled' if enabled else 'disabled'} ({status.status})")
            self._state = self._state.model_copy(update={"steam_enabled": enabled})
            self._notify_change()
            return True
        except Exception:
            logger.exception("Failed to set steam")
            self._mark_disconnected()
            return False

    async def refresh_state(self) -> LaMarzoccoState:
        """Read full machine state over BLE."""
        if not self._client:
            return self._state

        try:
            mode = await self._client.get_machine_mode()
            boilers = await self._client.get_boilers()

            updates: dict = {
                "connected": True,
                "turned_on": mode == MachineMode.BREWING_MODE,
            }

            for boiler in boilers:
                if boiler.id == BoilerType.COFFEE:
                    updates["coffee_boiler_enabled"] = boiler.is_enabled
                    updates["coffee_temp_target"] = float(boiler.target)
                    updates["coffee_temp_current"] = float(boiler.current)
                elif boiler.id == BoilerType.STEAM:
                    updates["steam_enabled"] = boiler.is_enabled
                    updates["steam_temp_target"] = float(boiler.target)
                    if boiler.target in STEAM_TEMP_TO_LEVEL:
                        updates["steam_level"] = STEAM_TEMP_TO_LEVEL[boiler.target]

            self._state = self._state.model_copy(update=updates)
            self._notify_change()

        except Exception:
            logger.exception("Failed to refresh state")
            self._mark_disconnected()

        return self._state

    # -- Helpers --

    async def _resolve_device(
        self, device: BLEDevice | None, address: str | None
    ) -> BLEDevice | None:
        """Find a BLEDevice from an address or by scanning."""
        if device is not None:
            return device

        if address is not None:
            logger.info(f"La Marzocco: finding device at {address}")
            found = await BleakScanner.find_device_by_address(address, timeout=15.0)
            if found is None:
                # Fall back to full scan (some bluez versions miss targeted scans)
                logger.info("Targeted scan missed, trying full scan...")
                devices = await BleakScanner.discover(timeout=10.0)
                for d in devices:
                    if d.address == address:
                        return d
                logger.error(f"Device not found at address {address}")
            return found

        logger.info("La Marzocco: scanning for devices...")
        return next(iter(await self.scan()), None)

    def _mark_disconnected(self) -> None:
        """Mark state as disconnected after a BLE failure."""
        self._state = self._state.model_copy(update={"connected": False})
        self._client = None
        self._notify_change()

    def _notify_change(self) -> None:
        if self._on_state_change:
            self._on_state_change(self._state)

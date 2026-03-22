"""La Marzocco Linea Micra BLE adapter.

Communicates with the La Marzocco espresso machine over Bluetooth Low Energy
using the lmcloud library. Supports power control, boiler temperature, and steam.

Requires one-time credential setup:
  - username: La Marzocco app account username
  - serial_number: Machine serial (printed on machine or from app)
  - communication_key: BLE auth token (extracted from cloud API — see docs)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from bleak import BleakScanner
from bleak.backends.device import BLEDevice

from espresso_bridge.core.models import LaMarzoccoState

logger = logging.getLogger(__name__)

# Re-export for type hints
try:
    from lmcloud import LaMarzoccoBluetoothClient, LaMarzoccoMachine
    from lmcloud.const import BoilerType, MachineModel

    LMCLOUD_AVAILABLE = True
except ImportError:
    LMCLOUD_AVAILABLE = False
    logger.warning("lmcloud not installed — La Marzocco features disabled. pip install lmcloud")

# Linea Micra steam levels map to specific temperatures
STEAM_LEVEL_MAP = {
    1: 126,  # SteamLevel.LEVEL_1
    2: 128,  # SteamLevel.LEVEL_2
    3: 131,  # SteamLevel.LEVEL_3
}
STEAM_TEMP_TO_LEVEL = {v: k for k, v in STEAM_LEVEL_MAP.items()}

# Brew boiler temp bounds (from lmcloud source)
COFFEE_TEMP_MIN = 85.0
COFFEE_TEMP_MAX = 104.0


class LaMarzoccoAdapter:
    """BLE adapter for the La Marzocco Linea Micra."""

    def __init__(
        self,
        username: str,
        serial_number: str,
        communication_key: str,
        on_state_change: Callable[[LaMarzoccoState], None] | None = None,
    ):
        if not LMCLOUD_AVAILABLE:
            raise RuntimeError("lmcloud not installed. Run: pip install lmcloud")

        self._username = username
        self._serial_number = serial_number
        self._communication_key = communication_key
        self._on_state_change = on_state_change

        self._bt_client: LaMarzoccoBluetoothClient | None = None
        self._machine: LaMarzoccoMachine | None = None
        self._device: BLEDevice | None = None
        self._state = LaMarzoccoState()
        self._reconnect_task: asyncio.Task | None = None

    @property
    def state(self) -> LaMarzoccoState:
        return self._state

    @property
    def connected(self) -> bool:
        return self._bt_client is not None and self._bt_client.connected

    # -- Scanning --

    @staticmethod
    async def scan(timeout: float = 10.0) -> list[BLEDevice]:
        """Scan for La Marzocco machines.

        Looks for BLE devices whose names start with MICRA, MINI, or GS3.
        """
        if not LMCLOUD_AVAILABLE:
            logger.error("lmcloud not installed")
            return []

        devices = await LaMarzoccoBluetoothClient.discover_devices()
        return devices

    # -- Connection --

    async def connect(
        self, device: BLEDevice | None = None, address: str | None = None
    ) -> bool:
        """Connect to the La Marzocco machine over BLE.

        Provide a BLEDevice from scan(), a known address, or neither to auto-discover.
        """
        if not LMCLOUD_AVAILABLE:
            return False

        if device is None and address is None:
            devices = await self.scan()
            if not devices:
                logger.error("No La Marzocco machine found")
                return False
            device = devices[0]
            logger.info(f"Auto-discovered: {device.name} ({device.address})")

        if device is None and address is not None:
            device = await BleakScanner.find_device_by_address(address, timeout=10.0)
            if device is None:
                logger.error(f"Device not found at address {address}")
                return False

        self._device = device

        try:
            self._bt_client = LaMarzoccoBluetoothClient(
                username=self._username,
                serial_number=self._serial_number,
                token=self._communication_key,
                address_or_ble_device=device,
            )

            self._machine = LaMarzoccoMachine(
                model=MachineModel.LINEA_MICRA,
                serial_number=self._serial_number,
                name="Linea Micra",
                bluetooth_client=self._bt_client,
            )

            # Try fetching config to validate the connection works
            # This triggers the BLE connect + auth internally
            await self._bt_client.set_power(True)
            # If we get here without exception, connection + auth succeeded
            # Read back won't work without local/cloud client, so we just mark connected
            self._state = self._state.model_copy(
                update={
                    "connected": True,
                    "turned_on": True,
                }
            )
            self._notify_change()
            logger.info(f"Connected to La Marzocco ({device.name})")
            return True

        except Exception:
            logger.exception("Failed to connect to La Marzocco")
            self._bt_client = None
            self._machine = None
            return False

    async def connect_silent(
        self, device: BLEDevice | None = None, address: str | None = None
    ) -> bool:
        """Connect without sending any command — just set up the clients.

        The actual BLE connection happens lazily on first command.
        """
        if not LMCLOUD_AVAILABLE:
            return False

        if device is None and address is None:
            devices = await self.scan()
            if not devices:
                logger.error("No La Marzocco machine found")
                return False
            device = devices[0]

        if device is None and address is not None:
            device = await BleakScanner.find_device_by_address(address, timeout=10.0)
            if device is None:
                logger.error(f"Device not found at address {address}")
                return False

        self._device = device

        self._bt_client = LaMarzoccoBluetoothClient(
            username=self._username,
            serial_number=self._serial_number,
            token=self._communication_key,
            address_or_ble_device=device,
        )

        self._machine = LaMarzoccoMachine(
            model=MachineModel.LINEA_MICRA,
            serial_number=self._serial_number,
            name="Linea Micra",
            bluetooth_client=self._bt_client,
        )

        self._state = self._state.model_copy(update={"connected": True})
        self._notify_change()
        logger.info(f"Prepared La Marzocco connection ({device.name})")
        return True

    async def disconnect(self) -> None:
        """Disconnect from the machine."""
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        # lmcloud manages its own BLE lifecycle; just clear our references
        self._bt_client = None
        self._machine = None
        self._state = self._state.model_copy(update={"connected": False})
        self._notify_change()

    # -- Commands --

    async def set_power(self, enabled: bool) -> bool:
        """Turn the machine on or off."""
        if not self._machine:
            logger.error("Not connected")
            return False
        try:
            result = await self._machine.set_power(enabled)
            if result:
                self._state = self._state.model_copy(update={"turned_on": enabled})
                self._notify_change()
                logger.info(f"Power {'on' if enabled else 'off'}")
            return result
        except Exception:
            logger.exception("Failed to set power")
            return False

    async def set_coffee_temp(self, temperature: float) -> bool:
        """Set the coffee boiler target temperature (85–104°C)."""
        if not self._machine:
            logger.error("Not connected")
            return False
        if temperature < COFFEE_TEMP_MIN or temperature > COFFEE_TEMP_MAX:
            logger.error(f"Coffee temp must be {COFFEE_TEMP_MIN}–{COFFEE_TEMP_MAX}°C")
            return False
        try:
            result = await self._machine.set_temp(BoilerType.COFFEE, temperature)
            if result:
                self._state = self._state.model_copy(
                    update={"coffee_temp_target": temperature}
                )
                self._notify_change()
                logger.info(f"Coffee boiler target: {temperature}°C")
            return result
        except Exception:
            logger.exception("Failed to set coffee temp")
            return False

    async def set_steam_level(self, level: int) -> bool:
        """Set steam level (1, 2, or 3) for Linea Micra."""
        if not self._machine:
            logger.error("Not connected")
            return False
        if level not in STEAM_LEVEL_MAP:
            logger.error("Steam level must be 1, 2, or 3")
            return False

        temp = STEAM_LEVEL_MAP[level]
        try:
            result = await self._machine.set_temp(BoilerType.STEAM, temp)
            if result:
                self._state = self._state.model_copy(
                    update={"steam_level": level, "steam_temp_target": float(temp)}
                )
                self._notify_change()
                logger.info(f"Steam level: {level} ({temp}°C)")
            return result
        except Exception:
            logger.exception("Failed to set steam level")
            return False

    async def set_steam_enabled(self, enabled: bool) -> bool:
        """Enable or disable the steam boiler."""
        if not self._machine:
            logger.error("Not connected")
            return False
        try:
            result = await self._machine.set_steam(enabled)
            if result:
                self._state = self._state.model_copy(update={"steam_enabled": enabled})
                self._notify_change()
                logger.info(f"Steam boiler {'enabled' if enabled else 'disabled'}")
            return result
        except Exception:
            logger.exception("Failed to set steam")
            return False

    async def refresh_state(self) -> LaMarzoccoState:
        """Attempt to refresh state from the machine.

        Note: Full state read requires local API or cloud client.
        BLE-only mode has limited read capability in lmcloud v1.x.
        State is primarily tracked via command acknowledgments.
        """
        if self._machine and self._machine.config:
            cfg = self._machine.config
            updates: dict = {"connected": True, "turned_on": cfg.turned_on}

            if hasattr(cfg, "boilers"):
                coffee = cfg.boilers.get(BoilerType.COFFEE)
                steam = cfg.boilers.get(BoilerType.STEAM)
                if coffee:
                    updates["coffee_temp_target"] = coffee.target_temperature
                    updates["coffee_temp_current"] = coffee.current_temperature
                    updates["coffee_boiler_enabled"] = coffee.enabled
                if steam:
                    updates["steam_temp_target"] = steam.target_temperature
                    updates["steam_enabled"] = steam.enabled
                    if steam.target_temperature in STEAM_TEMP_TO_LEVEL:
                        updates["steam_level"] = STEAM_TEMP_TO_LEVEL[
                            steam.target_temperature
                        ]

            self._state = self._state.model_copy(update=updates)
            self._notify_change()

        return self._state

    # -- Helpers --

    def _notify_change(self) -> None:
        if self._on_state_change:
            self._on_state_change(self._state)

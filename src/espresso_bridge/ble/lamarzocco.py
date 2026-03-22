"""La Marzocco Linea Micra BLE adapter.

Direct BLE communication with the La Marzocco espresso machine using bleak.
No auth required on this firmware — reads state from the status characteristic
and writes JSON commands to the write characteristic.

GATT Characteristics (service d10a7847-e12b-09a8-b04b-8e0922a9abab):
  STATUS (050b): read/write — returns full JSON state, accepts JSON commands
  WRITE  (0b0b): read/write — also accepts JSON commands
  READ   (0a0b): read/write — requires auth (not used)
  WIFI   (d60a): read — WiFi scan results
  SSID   (d70a): read — current WiFi SSID

Requires one-time setup:
  - BLE address from a scan (e.g. 70:B8:F6:AF:4A:FA)
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from espresso_bridge.core.models import LaMarzoccoState

logger = logging.getLogger(__name__)

# LM GATT characteristic UUIDs
STATUS_CHAR = "050b7847-e12b-09a8-b04b-8e0922a9abab"
WRITE_CHAR = "0b0b7847-e12b-09a8-b04b-8e0922a9abab"

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

    Uses direct bleak BLE communication. Reads state from the STATUS
    characteristic and writes JSON commands. No auth required.
    """

    def __init__(
        self,
        on_state_change: Callable[[LaMarzoccoState], None] | None = None,
        # Legacy params — kept for config compatibility
        communication_key: str = "",
        username: str = "",
        serial_number: str = "",
    ):
        self._on_state_change = on_state_change
        self._client: BleakClient | None = None
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
        """Connect to the LM machine and read initial state.

        On Linux/bluez, connecting by address works even if the device
        isn't actively advertising (uses cached device info).
        """
        # Clean up any stale client from a previous attempt
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None

        if device is None and address is not None:
            device = await self._resolve_device(None, address)

        if device is None and address is None:
            device = await self._resolve_device(None, None)

        try:
            target = device if device is not None else address
            if target is None:
                logger.error("No device or address to connect to")
                return False

            self._device = device
            self._client = BleakClient(target, timeout=20)
            await self._client.connect()

            if not self._client.is_connected:
                logger.error("BLE connect returned but not connected")
                return False

            await self._read_status()
            name = device.name if device else address
            addr_str = device.address if device else address
            logger.info(
                f"Connected to La Marzocco ({name} @ {addr_str}), "
                f"mode={'on' if self._state.turned_on else 'standby'}"
            )
            return True

        except Exception:
            logger.exception("Failed to connect to La Marzocco")
            self._client = None
            return False

    async def connect_silent(
        self, device: BLEDevice | None = None, address: str | None = None
    ) -> bool:
        """Alias for connect — all connections read state."""
        return await self.connect(device=device, address=address)

    async def disconnect(self) -> None:
        """Disconnect from the machine."""
        if self._client and self._client.is_connected:
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
        mode = "BrewingMode" if enabled else "StandBy"
        ok = await self._send_command(
            "MachineChangeMode", {"mode": mode}
        )
        if ok:
            self._state = self._state.model_copy(update={"turned_on": enabled})
            self._notify_change()
            logger.info(f"Power {'on' if enabled else 'off'}")
        return ok

    async def set_coffee_temp(self, temperature: float) -> bool:
        """Set the coffee boiler target temperature (85–104°C)."""
        if temperature < COFFEE_TEMP_MIN or temperature > COFFEE_TEMP_MAX:
            logger.error(f"Coffee temp must be {COFFEE_TEMP_MIN}–{COFFEE_TEMP_MAX}°C")
            return False
        ok = await self._send_command(
            "SettingBoilerTarget",
            {"identifier": "CoffeeBoiler1", "value": temperature},
        )
        if ok:
            self._state = self._state.model_copy(
                update={"coffee_temp_target": temperature}
            )
            self._notify_change()
            logger.info(f"Coffee boiler target: {temperature}°C")
        return ok

    async def set_steam_level(self, level: int) -> bool:
        """Set steam level (1, 2, or 3) for Linea Micra."""
        if level not in STEAM_LEVEL_MAP:
            logger.error("Steam level must be 1, 2, or 3")
            return False
        temp = STEAM_LEVEL_MAP[level]
        ok = await self._send_command(
            "SettingBoilerTarget",
            {"identifier": "SteamBoiler", "value": float(temp)},
        )
        if ok:
            self._state = self._state.model_copy(
                update={"steam_level": level, "steam_temp_target": float(temp)}
            )
            self._notify_change()
            logger.info(f"Steam level: {level} ({temp}°C)")
        return ok

    async def set_steam_enabled(self, enabled: bool) -> bool:
        """Enable or disable the steam boiler."""
        ok = await self._send_command(
            "SettingBoilerEnable",
            {"identifier": "SteamBoiler", "state": enabled},
        )
        if ok:
            self._state = self._state.model_copy(update={"steam_enabled": enabled})
            self._notify_change()
            logger.info(f"Steam boiler {'enabled' if enabled else 'disabled'}")
        return ok

    async def refresh_state(self) -> LaMarzoccoState:
        """Read full machine state over BLE."""
        if self._client and self._client.is_connected:
            try:
                await self._read_status()
            except Exception:
                logger.exception("Failed to refresh state")
                self._mark_disconnected()
        return self._state

    # -- Internal --

    async def _send_command(self, name: str, parameter: dict) -> bool:
        """Send a JSON command to the machine."""
        if not self._client or not self._client.is_connected:
            logger.error("Not connected")
            return False
        cmd = {"name": name, "parameter": parameter}
        payload = json.dumps(cmd, separators=(",", ":")).encode() + b"\x00"
        try:
            await self._client.write_gatt_char(WRITE_CHAR, payload, response=True)
            return True
        except Exception:
            logger.exception(f"Failed to send command: {name}")
            self._mark_disconnected()
            return False

    async def _read_status(self) -> None:
        """Read machine state from the STATUS characteristic."""
        if not self._client or not self._client.is_connected:
            return

        raw = await self._client.read_gatt_char(STATUS_CHAR)
        data = json.loads(raw.decode())

        updates: dict = {"connected": True}

        mode = data.get("machineMode", "")
        updates["turned_on"] = mode == "BrewingMode"

        for boiler in data.get("boilers", []):
            bid = boiler.get("id", "")
            if bid == "CoffeeBoiler1":
                updates["coffee_boiler_enabled"] = boiler.get("isEnabled", False)
                updates["coffee_temp_target"] = float(boiler.get("target", 93))
                updates["coffee_temp_current"] = float(boiler.get("temperature", 0))
            elif bid == "SteamBoiler":
                updates["steam_enabled"] = boiler.get("isEnabled", False)
                target = int(boiler.get("target", 128))
                updates["steam_temp_target"] = float(target)
                if target in STEAM_TEMP_TO_LEVEL:
                    updates["steam_level"] = STEAM_TEMP_TO_LEVEL[target]

        self._state = self._state.model_copy(update=updates)
        self._notify_change()

    async def _resolve_device(
        self, device: BLEDevice | None, address: str | None
    ) -> BLEDevice | None:
        """Try to find a BLEDevice from an address or by scanning.

        Returns None if not found — caller can fall back to direct address connection.
        """
        if device is not None:
            return device

        if address is not None:
            logger.info(f"La Marzocco: scanning for {address}")
            found = await BleakScanner.find_device_by_address(address, timeout=10.0)
            if found is not None:
                return found
            # Full scan fallback
            devices = await BleakScanner.discover(timeout=8.0)
            for d in devices:
                if d.address == address:
                    return d
            logger.info("Device not in scan — will try direct address connection")
            return None

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

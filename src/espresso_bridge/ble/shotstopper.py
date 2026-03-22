"""ShotStopper BLE adapter.

Communicates with the ShotStopper ESP32 brew-by-weight controller over Bluetooth Low Energy.
Protocol reverse-engineered from the ShotStopper companion app (icapurro/shotStopperCompanionApp).

BLE Service UUID: 00000000-0000-0000-0000-000000000ffe
Device advertises as: "shotStopper"
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from espresso_bridge.core.models import ScaleStatus, ShotStopperConfig, ShotStopperState

logger = logging.getLogger(__name__)

SERVICE_UUID = "00000000-0000-0000-0000-000000000ffe"
DEVICE_NAME = "shotStopper"

# fmt: off
@dataclass(frozen=True)
class Characteristics:
    """ShotStopper GATT characteristic UUIDs."""
    ENABLED           = "00000000-0000-0000-0000-00000000ff10"
    WEIGHT_VALUE      = "00000000-0000-0000-0000-00000000ff11"
    REED_SWITCH       = "00000000-0000-0000-0000-00000000ff12"
    MOMENTARY         = "00000000-0000-0000-0000-00000000ff13"
    AUTO_TARE         = "00000000-0000-0000-0000-00000000ff14"
    MIN_SHOT_DURATION = "00000000-0000-0000-0000-00000000ff15"
    MAX_SHOT_DURATION = "00000000-0000-0000-0000-00000000ff16"
    DRIP_DELAY        = "00000000-0000-0000-0000-00000000ff17"
    FIRMWARE_VERSION  = "00000000-0000-0000-0000-00000000ff18"
    SCALE_STATUS      = "00000000-0000-0000-0000-00000000ff19"
    SHOT_STATUS       = "00000000-0000-0000-0000-00000000ff20"
    OTA_MODE          = "00000000-0000-0000-0000-00000000ff21"
    WIFI_SSID         = "00000000-0000-0000-0000-00000000ff22"
    WIFI_PASSWORD     = "00000000-0000-0000-0000-00000000ff23"
    WIFI_IP           = "00000000-0000-0000-0000-00000000ff24"
# fmt: on

CHAR = Characteristics()

# Maps characteristic UUID → (model field name, parser)
_CHAR_TO_FIELD: dict[str, tuple[str, Callable]] = {
    CHAR.ENABLED: ("enabled", lambda b: bool(b[0])),
    CHAR.WEIGHT_VALUE: ("weight_target", lambda b: int(b[0])),
    CHAR.REED_SWITCH: ("reed_switch", lambda b: bool(b[0])),
    CHAR.MOMENTARY: ("momentary", lambda b: bool(b[0])),
    CHAR.AUTO_TARE: ("auto_tare", lambda b: bool(b[0])),
    CHAR.MIN_SHOT_DURATION: ("min_shot_duration", lambda b: int(b[0])),
    CHAR.MAX_SHOT_DURATION: ("max_shot_duration", lambda b: int(b[0])),
    CHAR.DRIP_DELAY: ("drip_delay", lambda b: int(b[0])),
    CHAR.FIRMWARE_VERSION: ("firmware_version", lambda b: int(b[0])),
    CHAR.SCALE_STATUS: ("scale_status", lambda b: ScaleStatus(int(b[0]))),
    CHAR.SHOT_STATUS: ("shot_active", lambda b: bool(b[0])),
    CHAR.WIFI_SSID: ("wifi_ssid", lambda b: b.decode("utf-8", errors="replace").rstrip("\x00")),
    CHAR.WIFI_IP: ("wifi_ip", lambda b: b.decode("utf-8", errors="replace").rstrip("\x00")),
}

# Maps model field name → (characteristic UUID, encoder)
_FIELD_TO_CHAR: dict[str, tuple[str, Callable]] = {
    "enabled": (CHAR.ENABLED, lambda v: bytes([int(v)])),
    "weight_target": (CHAR.WEIGHT_VALUE, lambda v: bytes([int(v)])),
    "reed_switch": (CHAR.REED_SWITCH, lambda v: bytes([int(v)])),
    "momentary": (CHAR.MOMENTARY, lambda v: bytes([int(v)])),
    "auto_tare": (CHAR.AUTO_TARE, lambda v: bytes([int(v)])),
    "min_shot_duration": (CHAR.MIN_SHOT_DURATION, lambda v: bytes([int(v)])),
    "max_shot_duration": (CHAR.MAX_SHOT_DURATION, lambda v: bytes([int(v)])),
    "drip_delay": (CHAR.DRIP_DELAY, lambda v: bytes([int(v)])),
}


class ShotStopperAdapter:
    """BLE adapter for the ShotStopper brew-by-weight controller."""

    def __init__(self, on_state_change: Callable[[ShotStopperState], None] | None = None):
        self._client: BleakClient | None = None
        self._device: BLEDevice | None = None
        self._state = ShotStopperState()
        self._on_state_change = on_state_change
        self._reconnect_task: asyncio.Task | None = None

    @property
    def state(self) -> ShotStopperState:
        return self._state

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    # -- Scanning --

    @staticmethod
    async def scan(timeout: float = 10.0) -> list[BLEDevice]:
        """Scan for ShotStopper devices."""
        found: list[BLEDevice] = []

        def _detection(device: BLEDevice, adv: AdvertisementData) -> None:
            if device.name and DEVICE_NAME.lower() in device.name.lower():
                found.append(device)
            elif SERVICE_UUID.lower() in [s.lower() for s in (adv.service_uuids or [])]:
                found.append(device)

        scanner = BleakScanner(detection_callback=_detection)
        await scanner.start()
        await asyncio.sleep(timeout)
        await scanner.stop()

        # Deduplicate by address
        seen = set()
        unique = []
        for d in found:
            if d.address not in seen:
                seen.add(d.address)
                unique.append(d)
        return unique

    # -- Connection --

    async def connect(self, device: BLEDevice | None = None, address: str | None = None) -> bool:
        """Connect to a ShotStopper device.

        Provide either a BLEDevice from scan() or a known BLE address string.
        """
        if device is None and address is None:
            # Auto-discover
            devices = await self.scan(timeout=8.0)
            if not devices:
                logger.error("No ShotStopper device found")
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
            self._client = BleakClient(
                device,
                disconnected_callback=self._on_disconnect,
            )
            await self._client.connect()
            logger.info(f"Connected to {device.name} ({device.address})")

            # Read initial state
            await self._read_all()

            # Subscribe to notifications for live data
            await self._subscribe_notifications()

            self._state = self._state.model_copy(update={"connected": True})
            self._notify_change()
            return True

        except Exception:
            logger.exception("Failed to connect to ShotStopper")
            self._client = None
            return False

    async def disconnect(self) -> None:
        """Disconnect from the ShotStopper."""
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None
        self._state = self._state.model_copy(update={"connected": False})
        self._notify_change()

    def _on_disconnect(self, client: BleakClient) -> None:
        """Handle unexpected disconnection."""
        logger.warning("ShotStopper disconnected")
        self._state = self._state.model_copy(update={"connected": False})
        self._notify_change()

        # Schedule reconnect
        if self._device is not None:
            self._reconnect_task = asyncio.get_event_loop().create_task(
                self._reconnect_loop()
            )

    async def _reconnect_loop(self, max_attempts: int = 0, interval: float = 5.0) -> None:
        """Attempt to reconnect periodically. max_attempts=0 means indefinite."""
        attempt = 0
        while max_attempts == 0 or attempt < max_attempts:
            attempt += 1
            logger.info(f"Reconnect attempt {attempt}...")
            await asyncio.sleep(interval)
            try:
                if await self.connect(device=self._device):
                    logger.info("Reconnected successfully")
                    return
            except Exception:
                logger.warning(f"Reconnect attempt {attempt} failed")
        logger.error("Gave up reconnecting to ShotStopper")

    # -- Reading --

    async def _read_all(self) -> None:
        """Read all characteristics and update state."""
        if not self._client or not self._client.is_connected:
            return

        updates: dict = {}
        for char_uuid, (field_name, parser) in _CHAR_TO_FIELD.items():
            try:
                data = await self._client.read_gatt_char(char_uuid)
                updates[field_name] = parser(data)
            except Exception:
                logger.debug(f"Failed to read {field_name} ({char_uuid})")

        self._state = self._state.model_copy(update=updates)

    async def read_state(self) -> ShotStopperState:
        """Read full state from device."""
        await self._read_all()
        return self._state

    # -- Writing --

    async def set_weight(self, grams: int) -> bool:
        """Set the target brew weight in grams."""
        return await self._write_field("weight_target", grams)

    async def set_enabled(self, enabled: bool) -> bool:
        """Enable or disable brew-by-weight."""
        return await self._write_field("enabled", enabled)

    async def apply_config(self, config: ShotStopperConfig) -> bool:
        """Apply a configuration update. Only non-None fields are written."""
        success = True
        for field_name, value in config.model_dump(exclude_none=True).items():
            if not await self._write_field(field_name, value):
                success = False
        return success

    async def _write_field(self, field_name: str, value) -> bool:
        """Write a single field to the device."""
        if not self._client or not self._client.is_connected:
            logger.error("Not connected")
            return False

        if field_name not in _FIELD_TO_CHAR:
            logger.error(f"Unknown writable field: {field_name}")
            return False

        char_uuid, encoder = _FIELD_TO_CHAR[field_name]
        try:
            await self._client.write_gatt_char(char_uuid, encoder(value))
            self._state = self._state.model_copy(update={field_name: value})
            self._notify_change()
            logger.info(f"Set {field_name} = {value}")
            return True
        except Exception:
            logger.exception(f"Failed to write {field_name}")
            return False

    # -- Notifications --

    async def _subscribe_notifications(self) -> None:
        """Subscribe to characteristics that support notifications."""
        notify_chars = [
            (CHAR.SCALE_STATUS, "scale_status"),
            (CHAR.SHOT_STATUS, "shot_active"),
        ]
        for char_uuid, field_name in notify_chars:
            try:
                await self._client.start_notify(
                    char_uuid,
                    lambda _, data, fn=field_name, cu=char_uuid: self._handle_notification(
                        cu, data
                    ),
                )
                logger.debug(f"Subscribed to notifications for {field_name}")
            except Exception:
                logger.debug(f"Could not subscribe to {field_name} (may not support notify)")

    def _handle_notification(self, char_uuid: str, data: bytearray) -> None:
        """Handle an incoming BLE notification."""
        if char_uuid in _CHAR_TO_FIELD:
            field_name, parser = _CHAR_TO_FIELD[char_uuid]
            try:
                value = parser(data)
                self._state = self._state.model_copy(update={field_name: value})
                self._notify_change()
                logger.debug(f"Notification: {field_name} = {value}")
            except Exception:
                logger.warning(f"Failed to parse notification for {char_uuid}")

    # -- Helpers --

    def _notify_change(self) -> None:
        if self._on_state_change:
            self._on_state_change(self._state)

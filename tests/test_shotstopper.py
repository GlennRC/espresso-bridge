"""Tests for ShotStopper BLE adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from espresso_bridge.ble.shotstopper import (
    CHAR,
    SERVICE_UUID,
    ShotStopperAdapter,
)
from espresso_bridge.core.models import ScaleStatus, ShotStopperConfig, ShotStopperState


class TestShotStopperState:
    """Test the state model."""

    def test_default_state(self):
        state = ShotStopperState()
        assert state.connected is False
        assert state.weight_target == 36
        assert state.enabled is True
        assert state.scale_status == ScaleStatus.DISCONNECTED
        assert state.shot_active is False

    def test_weight_bounds(self):
        state = ShotStopperState(weight_target=42)
        assert state.weight_target == 42

        with pytest.raises(Exception):
            ShotStopperState(weight_target=5)  # Below min

        with pytest.raises(Exception):
            ShotStopperState(weight_target=250)  # Above max


class TestShotStopperConfig:
    """Test the config model."""

    def test_partial_config(self):
        config = ShotStopperConfig(weight_target=40)
        dump = config.model_dump(exclude_none=True)
        assert dump == {"weight_target": 40}

    def test_empty_config(self):
        config = ShotStopperConfig()
        dump = config.model_dump(exclude_none=True)
        assert dump == {}


class TestShotStopperAdapter:
    """Test the BLE adapter with mocked bleak."""

    @pytest.fixture
    def adapter(self):
        return ShotStopperAdapter()

    @pytest.fixture
    def mock_client(self):
        client = AsyncMock()
        client.is_connected = True
        client.connect = AsyncMock()
        client.disconnect = AsyncMock()

        # Mock read_gatt_char to return reasonable defaults
        async def read_char(uuid):
            defaults = {
                CHAR.ENABLED: bytes([1]),
                CHAR.WEIGHT_VALUE: bytes([36]),
                CHAR.REED_SWITCH: bytes([0]),
                CHAR.MOMENTARY: bytes([0]),
                CHAR.AUTO_TARE: bytes([1]),
                CHAR.MIN_SHOT_DURATION: bytes([3]),
                CHAR.MAX_SHOT_DURATION: bytes([50]),
                CHAR.DRIP_DELAY: bytes([3]),
                CHAR.FIRMWARE_VERSION: bytes([2]),
                CHAR.SCALE_STATUS: bytes([1]),
                CHAR.SHOT_STATUS: bytes([0]),
                CHAR.WIFI_SSID: b"MyWiFi\x00",
                CHAR.WIFI_IP: b"192.168.1.100\x00",
            }
            return defaults.get(uuid, bytes([0]))

        client.read_gatt_char = AsyncMock(side_effect=read_char)
        client.write_gatt_char = AsyncMock()
        client.start_notify = AsyncMock()
        return client

    def test_initial_state(self, adapter):
        assert adapter.connected is False
        assert adapter.state.weight_target == 36

    @pytest.mark.asyncio
    async def test_scan_finds_device(self):
        mock_device = MagicMock()
        mock_device.name = "shotStopper"
        mock_device.address = "AA:BB:CC:DD:EE:FF"

        with patch("espresso_bridge.ble.shotstopper.BleakScanner") as mock_scanner_cls:
            scanner_instance = AsyncMock()
            mock_scanner_cls.return_value = scanner_instance

            # Simulate the detection callback being called
            def start_side_effect():
                # Get the callback that was passed to BleakScanner()
                callback = mock_scanner_cls.call_args[1]["detection_callback"]
                adv = MagicMock()
                adv.service_uuids = [SERVICE_UUID]
                callback(mock_device, adv)

            scanner_instance.start = AsyncMock(side_effect=start_side_effect)
            scanner_instance.stop = AsyncMock()

            devices = await ShotStopperAdapter.scan(timeout=0.1)
            assert len(devices) == 1
            assert devices[0].name == "shotStopper"

    @pytest.mark.asyncio
    async def test_read_all_updates_state(self, adapter, mock_client):
        adapter._client = mock_client
        await adapter._read_all()

        assert adapter.state.weight_target == 36
        assert adapter.state.enabled is True
        assert adapter.state.scale_status == ScaleStatus.CONNECTED
        assert adapter.state.firmware_version == 2
        assert adapter.state.wifi_ssid == "MyWiFi"

    @pytest.mark.asyncio
    async def test_set_weight(self, adapter, mock_client):
        adapter._client = mock_client
        result = await adapter.set_weight(42)

        assert result is True
        mock_client.write_gatt_char.assert_called_once_with(CHAR.WEIGHT_VALUE, bytes([42]))
        assert adapter.state.weight_target == 42

    @pytest.mark.asyncio
    async def test_set_weight_disconnected(self, adapter):
        result = await adapter.set_weight(42)
        assert result is False

    @pytest.mark.asyncio
    async def test_apply_config(self, adapter, mock_client):
        adapter._client = mock_client
        config = ShotStopperConfig(auto_tare=False, drip_delay=5)
        result = await adapter.apply_config(config)

        assert result is True
        assert mock_client.write_gatt_char.call_count == 2
        assert adapter.state.auto_tare is False
        assert adapter.state.drip_delay == 5

    @pytest.mark.asyncio
    async def test_notification_updates_state(self, adapter, mock_client):
        adapter._client = mock_client
        adapter._state = adapter._state.model_copy(update={"connected": True})

        # Simulate a shot_status notification
        adapter._handle_notification(CHAR.SHOT_STATUS, bytearray([1]))
        assert adapter.state.shot_active is True

        # Simulate scale disconnection
        adapter._handle_notification(CHAR.SCALE_STATUS, bytearray([0]))
        assert adapter.state.scale_status == ScaleStatus.DISCONNECTED

    def test_state_change_callback(self):
        changes = []
        adapter = ShotStopperAdapter(on_state_change=lambda s: changes.append(s))
        adapter._notify_change()
        assert len(changes) == 1

"""Tests for La Marzocco BLE adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from espresso_bridge.ble.lamarzocco import (
    STEAM_LEVEL_MAP,
    LaMarzoccoAdapter,
)
from espresso_bridge.core.models import LaMarzoccoState


class TestLaMarzoccoState:
    """Test the LM state model."""

    def test_default_state(self):
        state = LaMarzoccoState()
        assert state.connected is False
        assert state.turned_on is False
        assert state.coffee_temp_target == 93.0
        assert state.steam_level == 2

    def test_coffee_temp_bounds(self):
        state = LaMarzoccoState(coffee_temp_target=90.0)
        assert state.coffee_temp_target == 90.0

        with pytest.raises(Exception):
            LaMarzoccoState(coffee_temp_target=50.0)

        with pytest.raises(Exception):
            LaMarzoccoState(coffee_temp_target=120.0)


class TestLaMarzoccoAdapter:
    """Test the LM BLE adapter with mocked lmcloud."""

    @pytest.fixture
    def adapter(self):
        return LaMarzoccoAdapter(
            username="test@example.com",
            serial_number="LM12345",
            communication_key="test_key_123",
        )

    @pytest.fixture
    def mock_machine(self):
        machine = MagicMock()
        machine.set_power = AsyncMock(return_value=True)
        machine.set_temp = AsyncMock(return_value=True)
        machine.set_steam = AsyncMock(return_value=True)
        machine.config = None
        return machine

    def test_initial_state(self, adapter):
        assert adapter.connected is False
        assert adapter.state.turned_on is False

    @pytest.mark.asyncio
    async def test_set_power_on(self, adapter, mock_machine):
        adapter._machine = mock_machine
        result = await adapter.set_power(True)
        assert result is True
        assert adapter.state.turned_on is True
        mock_machine.set_power.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_set_power_off(self, adapter, mock_machine):
        adapter._machine = mock_machine
        adapter._state = adapter._state.model_copy(update={"turned_on": True})
        result = await adapter.set_power(False)
        assert result is True
        assert adapter.state.turned_on is False

    @pytest.mark.asyncio
    async def test_set_power_not_connected(self, adapter):
        result = await adapter.set_power(True)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_coffee_temp(self, adapter, mock_machine):
        adapter._machine = mock_machine
        result = await adapter.set_coffee_temp(93.0)
        assert result is True
        assert adapter.state.coffee_temp_target == 93.0

    @pytest.mark.asyncio
    async def test_set_coffee_temp_out_of_range(self, adapter, mock_machine):
        adapter._machine = mock_machine
        result = await adapter.set_coffee_temp(50.0)
        assert result is False

        result = await adapter.set_coffee_temp(120.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_steam_level(self, adapter, mock_machine):
        adapter._machine = mock_machine
        result = await adapter.set_steam_level(3)
        assert result is True
        assert adapter.state.steam_level == 3
        assert adapter.state.steam_temp_target == float(STEAM_LEVEL_MAP[3])

    @pytest.mark.asyncio
    async def test_set_steam_level_invalid(self, adapter, mock_machine):
        adapter._machine = mock_machine
        result = await adapter.set_steam_level(5)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_steam_enabled(self, adapter, mock_machine):
        adapter._machine = mock_machine
        result = await adapter.set_steam_enabled(True)
        assert result is True
        assert adapter.state.steam_enabled is True

    @pytest.mark.asyncio
    async def test_set_power_exception(self, adapter, mock_machine):
        mock_machine.set_power = AsyncMock(side_effect=Exception("BLE error"))
        adapter._machine = mock_machine
        result = await adapter.set_power(True)
        assert result is False

    def test_state_change_callback(self):
        changes = []
        adapter = LaMarzoccoAdapter(
            username="test@example.com",
            serial_number="LM12345",
            communication_key="test_key",
            on_state_change=lambda s: changes.append(s),
        )
        adapter._notify_change()
        assert len(changes) == 1

    @pytest.mark.asyncio
    async def test_disconnect(self, adapter):
        adapter._state = adapter._state.model_copy(update={"connected": True})
        await adapter.disconnect()
        assert adapter.state.connected is False
        assert adapter._machine is None
        assert adapter._bt_client is None

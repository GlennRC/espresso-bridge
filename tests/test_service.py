"""Tests for configuration, state store, device manager, and API server."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from espresso_bridge.core.config import AppConfig
from espresso_bridge.core.models import LaMarzoccoState, ShotStopperState
from espresso_bridge.core.state import StateStore


class TestAppConfig:
    def test_defaults(self):
        cfg = AppConfig()
        assert cfg.shotstopper.default_weight == 36
        assert cfg.server.port == 8080
        assert cfg.lamarzocco.is_configured is False

    def test_load_missing_file(self):
        cfg = AppConfig.load("/nonexistent/config.yaml")
        assert cfg.server.port == 8080

    def test_load_file(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
shotstopper:
  address: "AA:BB:CC:DD:EE:FF"
  default_weight: 40
lamarzocco:
  serial_number: "LM123"
  username: "user@test.com"
  communication_key: "key123"
server:
  port: 9090
""")
        cfg = AppConfig.load(config_file)
        assert cfg.shotstopper.address == "AA:BB:CC:DD:EE:FF"
        assert cfg.shotstopper.default_weight == 40
        assert cfg.lamarzocco.is_configured is True
        assert cfg.lamarzocco.serial_number == "LM123"
        assert cfg.server.port == 9090


class TestStateStore:
    def test_initial_state(self):
        store = StateStore()
        assert store.state.shotstopper.connected is False
        assert store.state.lamarzocco.connected is False

    def test_update_shotstopper(self):
        store = StateStore()
        ss = ShotStopperState(connected=True, weight_target=42)
        store.update_shotstopper(ss)
        assert store.state.shotstopper.weight_target == 42
        assert store.state.shotstopper.connected is True

    def test_update_lamarzocco(self):
        store = StateStore()
        lm = LaMarzoccoState(connected=True, turned_on=True, coffee_temp_target=95.0)
        store.update_lamarzocco(lm)
        assert store.state.lamarzocco.turned_on is True
        assert store.state.lamarzocco.coffee_temp_target == 95.0

    def test_to_dict(self):
        store = StateStore()
        d = store.state.to_dict()
        assert "shotstopper" in d
        assert "lamarzocco" in d
        assert d["shotstopper"]["weight_target"] == 36

    def test_subscribe_and_broadcast(self):
        store = StateStore()
        q = store.subscribe()

        ss = ShotStopperState(connected=True, weight_target=50)
        store.update_shotstopper(ss)

        assert not q.empty()
        state = q.get_nowait()
        assert state.shotstopper.weight_target == 50

    def test_unsubscribe(self):
        store = StateStore()
        q = store.subscribe()
        store.unsubscribe(q)

        store.update_shotstopper(ShotStopperState(weight_target=99))
        assert q.empty()

    def test_callback(self):
        store = StateStore()
        results = []
        store.on_change(lambda s: results.append(s.shotstopper.weight_target))

        store.update_shotstopper(ShotStopperState(weight_target=42))
        assert results == [42]

    def test_queue_overflow(self):
        store = StateStore()
        q = store.subscribe()

        # Fill queue beyond capacity (maxsize=16)
        for i in range(20):
            store.update_shotstopper(ShotStopperState(weight_target=10 + i))

        # Should not raise, latest values should be available
        count = 0
        while not q.empty():
            q.get_nowait()
            count += 1
        assert count <= 16


class TestAPI:
    """Test API endpoints with mocked device manager."""

    @pytest.fixture
    def setup(self):
        from espresso_bridge.api.server import create_app
        from espresso_bridge.ble.manager import DeviceManager
        from espresso_bridge.core.config import AppConfig

        store = StateStore()
        AppConfig()

        # Mock device manager
        manager = MagicMock(spec=DeviceManager)
        manager.ss_phase = MagicMock(value="connected")
        manager.lm_phase = MagicMock(value="disconnected")

        # Mock ShotStopper adapter
        manager.shotstopper = MagicMock()
        manager.shotstopper.set_weight = AsyncMock(return_value=True)
        manager.shotstopper.apply_config = AsyncMock(return_value=True)

        # Mock LM adapter
        manager.lamarzocco = MagicMock()
        manager.lamarzocco.set_power = AsyncMock(return_value=True)
        manager.lamarzocco.set_coffee_temp = AsyncMock(return_value=True)
        manager.lamarzocco.set_steam_level = AsyncMock(return_value=True)
        manager.lamarzocco.set_steam_enabled = AsyncMock(return_value=True)

        # Start/stop as no-ops for test
        manager.start = AsyncMock()
        manager.stop = AsyncMock()

        app = create_app(manager, store)
        client = TestClient(app)
        return client, manager, store

    def test_get_status(self, setup):
        client, _, _ = setup
        res = client.get("/api/status")
        assert res.status_code == 200
        data = res.json()
        assert "shotstopper" in data
        assert "lamarzocco" in data
        assert "connections" in data

    def test_set_weight(self, setup):
        client, manager, _ = setup
        res = client.post("/api/shotstopper/weight", json={"grams": 42})
        assert res.status_code == 200
        assert res.json()["ok"] is True
        manager.shotstopper.set_weight.assert_called_once_with(42)

    def test_set_weight_invalid(self, setup):
        client, _, _ = setup
        res = client.post("/api/shotstopper/weight", json={"grams": 5})
        assert res.status_code == 422

    def test_set_lm_power(self, setup):
        client, manager, _ = setup
        res = client.post("/api/lm/power", json={"on": True})
        assert res.status_code == 200
        manager.lamarzocco.set_power.assert_called_once_with(True)

    def test_set_lm_temperature(self, setup):
        client, manager, _ = setup
        res = client.post("/api/lm/temperature", json={"celsius": 95.0})
        assert res.status_code == 200
        manager.lamarzocco.set_coffee_temp.assert_called_once_with(95.0)

    def test_set_lm_temperature_invalid(self, setup):
        client, _, _ = setup
        res = client.post("/api/lm/temperature", json={"celsius": 50.0})
        assert res.status_code == 422

    def test_set_lm_steam(self, setup):
        client, manager, _ = setup
        res = client.post("/api/lm/steam", json={"level": 3})
        assert res.status_code == 200
        manager.lamarzocco.set_steam_level.assert_called_once_with(3)

    def test_shotstopper_settings(self, setup):
        client, manager, _ = setup
        res = client.post("/api/shotstopper/settings", json={"auto_tare": False})
        assert res.status_code == 200
        assert res.json()["ok"] is True

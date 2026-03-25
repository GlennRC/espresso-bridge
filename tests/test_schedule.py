"""Tests for the wake/sleep schedule feature."""

from datetime import date, datetime, time

import pytest

from espresso_bridge.core.models import (
    DaySchedule,
    ScheduleConfig,
    WeekSchedule,
)


# -- DaySchedule --


class TestDaySchedule:
    def test_defaults(self):
        ds = DaySchedule()
        assert ds.enabled is False
        assert ds.on_hour == 7
        assert ds.on_minute == 0
        assert ds.off_hour == 22
        assert ds.off_minute == 0
        assert ds.steam is True

    def test_time_properties(self):
        ds = DaySchedule(on_hour=6, on_minute=30, off_hour=21, off_minute=45)
        assert ds.on_time == time(6, 30)
        assert ds.off_time == time(21, 45)

    def test_validation(self):
        with pytest.raises(Exception):
            DaySchedule(on_hour=25)
        with pytest.raises(Exception):
            DaySchedule(on_minute=60)


# -- WeekSchedule --


class TestWeekSchedule:
    def test_defaults(self):
        ws = WeekSchedule()
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]:
            assert ws.get_day(day).enabled is False

    def test_get_day(self):
        ws = WeekSchedule(monday=DaySchedule(enabled=True))
        assert ws.get_day("monday").enabled is True
        assert ws.get_day("tuesday").enabled is False

    def test_get_day_case_insensitive(self):
        ws = WeekSchedule(friday=DaySchedule(enabled=True, on_hour=9))
        assert ws.get_day("Friday").enabled is True
        assert ws.get_day("FRIDAY").enabled is True


# -- ScheduleConfig: Week A/B calculation --


class TestScheduleWeekCalc:
    def test_even_weeks_are_a(self):
        sc = ScheduleConfig(reference_date="2025-01-06")
        assert sc.current_week(date(2025, 1, 6)) == "a"  # week 0
        assert sc.current_week(date(2025, 1, 20)) == "a"  # week 2
        assert sc.current_week(date(2025, 2, 3)) == "a"  # week 4

    def test_odd_weeks_are_b(self):
        sc = ScheduleConfig(reference_date="2025-01-06")
        assert sc.current_week(date(2025, 1, 13)) == "b"  # week 1
        assert sc.current_week(date(2025, 1, 27)) == "b"  # week 3
        assert sc.current_week(date(2025, 2, 10)) == "b"  # week 5

    def test_no_reference_date_defaults_to_a(self):
        sc = ScheduleConfig(reference_date="")
        assert sc.current_week() == "a"

    def test_mid_week_still_same_week(self):
        sc = ScheduleConfig(reference_date="2025-01-06")
        # Wed Jan 8 is still week 0 (A)
        assert sc.current_week(date(2025, 1, 8)) == "a"
        # Sun Jan 12 is still week 0 (A)
        assert sc.current_week(date(2025, 1, 12)) == "a"

    def test_week_boundary(self):
        sc = ScheduleConfig(reference_date="2025-01-06")
        # Day 6 (Jan 12) → week 0 (A)
        assert sc.current_week(date(2025, 1, 12)) == "a"
        # Day 7 (Jan 13) → week 1 (B)
        assert sc.current_week(date(2025, 1, 13)) == "b"


# -- ScheduleConfig: today_schedule --


class TestTodaySchedule:
    def test_correct_day(self):
        sc = ScheduleConfig(
            reference_date="2025-01-06",
            week_a=WeekSchedule(monday=DaySchedule(enabled=True, on_hour=6)),
        )
        # Jan 6, 2025 is Monday
        day = sc.today_schedule(datetime(2025, 1, 6, 10, 0))
        assert day.enabled is True
        assert day.on_hour == 6

    def test_uses_week_b(self):
        sc = ScheduleConfig(
            reference_date="2025-01-06",
            week_b=WeekSchedule(wednesday=DaySchedule(enabled=True, on_hour=9)),
        )
        # Jan 15, 2025 is Wednesday in week B
        day = sc.today_schedule(datetime(2025, 1, 15, 10, 0))
        assert day.enabled is True
        assert day.on_hour == 9


# -- ScheduleConfig: next_event --


class TestNextEvent:
    def test_disabled_returns_none(self):
        sc = ScheduleConfig(enabled=False)
        assert sc.next_event() is None

    def test_before_on_time(self):
        sc = ScheduleConfig(
            enabled=True,
            reference_date="2025-01-06",
            week_a=WeekSchedule(monday=DaySchedule(enabled=True, on_hour=7, off_hour=22)),
        )
        ev = sc.next_event(datetime(2025, 1, 6, 5, 0))
        assert ev["type"] == "on"
        assert ev["hour"] == 7

    def test_between_on_and_off(self):
        sc = ScheduleConfig(
            enabled=True,
            reference_date="2025-01-06",
            week_a=WeekSchedule(monday=DaySchedule(enabled=True, on_hour=7, off_hour=22)),
        )
        ev = sc.next_event(datetime(2025, 1, 6, 12, 0))
        assert ev["type"] == "off"
        assert ev["hour"] == 22

    def test_after_off_finds_next_day(self):
        sc = ScheduleConfig(
            enabled=True,
            reference_date="2025-01-06",
            week_a=WeekSchedule(
                monday=DaySchedule(enabled=True, on_hour=7, off_hour=22),
                tuesday=DaySchedule(enabled=True, on_hour=8, off_hour=20),
            ),
        )
        ev = sc.next_event(datetime(2025, 1, 6, 23, 0))
        assert ev["type"] == "on"
        assert ev["day"] == "tuesday"
        assert ev["hour"] == 8

    def test_no_enabled_days_returns_none(self):
        sc = ScheduleConfig(enabled=True, reference_date="2025-01-06")
        ev = sc.next_event(datetime(2025, 1, 6, 12, 0))
        assert ev is None


# -- Config YAML round-trip --


class TestSchedulePersistence:
    def test_round_trip(self, tmp_path):
        from espresso_bridge.core.config import AppConfig

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("server:\n  port: 8080\n")

        cfg = AppConfig.load(cfg_path)
        sched = ScheduleConfig(
            enabled=True,
            reference_date="2025-01-06",
            week_a=WeekSchedule(
                saturday=DaySchedule(enabled=True, on_hour=8, off_hour=20, steam=False),
            ),
        )
        cfg.save_schedule(sched)

        # Reload and verify
        cfg2 = AppConfig.load(cfg_path)
        assert cfg2.schedule.enabled is True
        assert cfg2.schedule.reference_date == "2025-01-06"
        assert cfg2.schedule.week_a.saturday.enabled is True
        assert cfg2.schedule.week_a.saturday.on_hour == 8
        assert cfg2.schedule.week_a.saturday.steam is False
        # Server config preserved
        assert cfg2.server.port == 8080

    def test_empty_schedule_in_yaml(self, tmp_path):
        from espresso_bridge.core.config import AppConfig

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("server:\n  port: 8080\n")

        cfg = AppConfig.load(cfg_path)
        assert cfg.schedule.enabled is False


# -- API endpoint tests --


class TestScheduleAPI:
    @pytest.fixture
    def client(self):
        from unittest.mock import AsyncMock, MagicMock

        from fastapi.testclient import TestClient

        from espresso_bridge.api.server import create_app
        from espresso_bridge.core.config import AppConfig
        from espresso_bridge.core.state import StateStore

        store = StateStore()
        config = AppConfig()

        manager = MagicMock()
        manager.ss_phase = MagicMock(value="disconnected")
        manager.lm_phase = MagicMock(value="disconnected")
        manager.shotstopper = MagicMock()
        manager.lamarzocco = MagicMock()
        manager.start = AsyncMock()
        manager.stop = AsyncMock()
        manager.update_schedule = MagicMock()

        app = create_app(manager, store, config=config)
        return TestClient(app), manager, config

    def test_get_schedule_default(self, client):
        tc, _, _ = client
        r = tc.get("/api/lm/schedule")
        assert r.status_code == 200
        data = r.json()
        assert data["schedule"]["enabled"] is False
        assert data["current_week"] in ("a", "b")

    def test_post_schedule(self, client):
        tc, manager, config = client
        payload = {
            "enabled": True,
            "reference_date": "2025-01-06",
            "week_a": {
                "monday": {"enabled": True, "on_hour": 6, "on_minute": 30, "off_hour": 21, "off_minute": 0, "steam": True},
                "tuesday": {"enabled": False, "on_hour": 7, "on_minute": 0, "off_hour": 22, "off_minute": 0, "steam": True},
                "wednesday": {"enabled": False, "on_hour": 7, "on_minute": 0, "off_hour": 22, "off_minute": 0, "steam": True},
                "thursday": {"enabled": False, "on_hour": 7, "on_minute": 0, "off_hour": 22, "off_minute": 0, "steam": True},
                "friday": {"enabled": False, "on_hour": 7, "on_minute": 0, "off_hour": 22, "off_minute": 0, "steam": True},
                "saturday": {"enabled": False, "on_hour": 7, "on_minute": 0, "off_hour": 22, "off_minute": 0, "steam": True},
                "sunday": {"enabled": False, "on_hour": 7, "on_minute": 0, "off_hour": 22, "off_minute": 0, "steam": True},
            },
            "week_b": {
                "monday": {"enabled": False, "on_hour": 7, "on_minute": 0, "off_hour": 22, "off_minute": 0, "steam": True},
                "tuesday": {"enabled": False, "on_hour": 7, "on_minute": 0, "off_hour": 22, "off_minute": 0, "steam": True},
                "wednesday": {"enabled": False, "on_hour": 7, "on_minute": 0, "off_hour": 22, "off_minute": 0, "steam": True},
                "thursday": {"enabled": False, "on_hour": 7, "on_minute": 0, "off_hour": 22, "off_minute": 0, "steam": True},
                "friday": {"enabled": False, "on_hour": 7, "on_minute": 0, "off_hour": 22, "off_minute": 0, "steam": True},
                "saturday": {"enabled": False, "on_hour": 7, "on_minute": 0, "off_hour": 22, "off_minute": 0, "steam": True},
                "sunday": {"enabled": False, "on_hour": 7, "on_minute": 0, "off_hour": 22, "off_minute": 0, "steam": True},
            },
        }
        r = tc.post("/api/lm/schedule", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["schedule"]["enabled"] is True
        manager.update_schedule.assert_called_once()

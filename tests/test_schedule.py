"""Tests for the Schedule models, config migration, persistence, and API endpoints."""

from datetime import date, datetime, time

import pytest

from espresso_bridge.core.models import Schedule, ScheduleConfig


# -- Schedule model --


class TestSchedule:
    def test_defaults(self):
        s = Schedule()
        assert s.id == ""
        assert s.name == ""
        assert s.enabled is True
        assert s.wake_hour == 4
        assert s.wake_minute == 50
        assert s.off_hour == 23
        assert s.off_minute == 0
        assert s.steam is True
        assert s.recurrence == "weekly"

    def test_time_properties(self):
        s = Schedule(wake_hour=6, wake_minute=30, off_hour=21, off_minute=45)
        assert s.wake_time == time(6, 30)
        assert s.off_time == time(21, 45)

    def test_fires_on_once(self):
        s = Schedule(recurrence="once", date="2026-04-15")
        assert s.fires_on(date(2026, 4, 15)) is True
        assert s.fires_on(date(2026, 4, 16)) is False

    def test_fires_on_daily(self):
        s = Schedule(recurrence="daily")
        assert s.fires_on(date(2026, 4, 15)) is True
        assert s.fires_on(date(2026, 4, 16)) is True
        assert s.fires_on(date(2026, 1, 1)) is True

    def test_fires_on_weekly(self):
        s = Schedule(recurrence="weekly", days=["monday", "friday"])
        assert s.fires_on(date(2026, 3, 30)) is True   # Monday
        assert s.fires_on(date(2026, 4, 3)) is True    # Friday
        assert s.fires_on(date(2026, 3, 31)) is False   # Tuesday

    def test_fires_on_biweekly(self):
        # reference_date 2026-03-30 is a Monday (week A start)
        s = Schedule(
            recurrence="biweekly",
            reference_date="2026-03-30",
            days=["saturday", "sunday"],
            days_b=["monday", "tuesday"],
        )
        # Week A (same week as ref): Sat Apr 4, Sun Apr 5
        assert s.fires_on(date(2026, 4, 4)) is True   # Sat week A
        assert s.fires_on(date(2026, 4, 5)) is True   # Sun week A
        # Week B (next week): Mon Apr 6, Tue Apr 7
        assert s.fires_on(date(2026, 4, 6)) is True   # Mon week B
        assert s.fires_on(date(2026, 4, 7)) is True   # Tue week B
        # Week B Sat → not in days_b
        assert s.fires_on(date(2026, 4, 11)) is False  # Sat week B
        # Week A Mon → not in days (week A)
        assert s.fires_on(date(2026, 3, 30)) is False  # Mon week A

    def test_fires_on_monthly(self):
        s = Schedule(recurrence="monthly", month_days=[1, 15])
        assert s.fires_on(date(2026, 4, 1)) is True
        assert s.fires_on(date(2026, 4, 15)) is True
        assert s.fires_on(date(2026, 4, 2)) is False

    def test_fires_on_disabled(self):
        s = Schedule(enabled=False, recurrence="daily")
        assert s.fires_on(date(2026, 4, 15)) is False
        assert s.fires_on(date(2026, 4, 16)) is False

    def test_summary_formats(self):
        assert "Once" in Schedule(recurrence="once", date="2026-04-15").summary()
        assert "Daily" in Schedule(recurrence="daily").summary()

        weekly = Schedule(recurrence="weekly", days=["monday", "friday"])
        s = weekly.summary()
        assert "Weekly" in s
        assert "Mon" in s
        assert "Fri" in s

        biweekly = Schedule(
            recurrence="biweekly",
            days=["saturday"], days_b=["monday"],
        )
        assert "Biweekly" in biweekly.summary()
        assert "2 days" in biweekly.summary()

        monthly = Schedule(recurrence="monthly", month_days=[1, 15])
        assert "Monthly" in monthly.summary()
        assert "2 days" in monthly.summary()


# -- ScheduleConfig --


class TestScheduleConfig:
    def test_resolve_empty(self):
        sc = ScheduleConfig()
        assert sc.resolve(date(2026, 4, 1)) is None

    def test_resolve_finds_match(self):
        s1 = Schedule(id="a", recurrence="weekly", days=["wednesday"], wake_hour=5)
        s2 = Schedule(id="b", recurrence="weekly", days=["wednesday"], wake_hour=9)
        sc = ScheduleConfig(schedules=[s1, s2])
        result = sc.resolve(date(2026, 4, 1))  # Wednesday
        assert result is not None
        assert result.id == "a"
        assert result.wake_hour == 5

    def test_resolve_skips_disabled(self):
        s1 = Schedule(id="off", enabled=False, recurrence="daily")
        s2 = Schedule(id="on", enabled=True, recurrence="daily", wake_hour=7)
        sc = ScheduleConfig(schedules=[s1, s2])
        result = sc.resolve(date(2026, 4, 1))
        assert result is not None
        assert result.id == "on"

    def test_next_event_before_wake(self):
        s = Schedule(recurrence="weekly", days=["monday"], wake_hour=4, wake_minute=50)
        sc = ScheduleConfig(schedules=[s])
        # 2026-03-30 is Monday, 3:00 AM is before 4:50
        ev = sc.next_event(datetime(2026, 3, 30, 3, 0))
        assert ev is not None
        assert ev["type"] == "on"
        assert ev["hour"] == 4
        assert ev["minute"] == 50

    def test_next_event_between(self):
        s = Schedule(recurrence="weekly", days=["monday"])
        sc = ScheduleConfig(schedules=[s])
        ev = sc.next_event(datetime(2026, 3, 30, 12, 0))
        assert ev is not None
        assert ev["type"] == "off"
        assert ev["hour"] == 23
        assert ev["minute"] == 0

    def test_next_event_future(self):
        s = Schedule(recurrence="weekly", days=["monday", "tuesday"])
        sc = ScheduleConfig(schedules=[s])
        # After 23:00 on Monday → should find Tuesday
        ev = sc.next_event(datetime(2026, 3, 30, 23, 30))
        assert ev is not None
        assert ev["type"] == "on"
        assert ev["day"] == "tuesday"

    def test_next_event_empty(self):
        sc = ScheduleConfig()
        assert sc.next_event(datetime(2026, 3, 30, 12, 0)) is None


# -- Config migration --


class TestConfigMigration:
    def test_v3_format(self):
        from espresso_bridge.core.config import _parse_schedule

        raw = {
            "schedules": [
                {
                    "id": "work",
                    "name": "Work",
                    "recurrence": "weekly",
                    "days": ["monday", "friday"],
                    "wake_hour": 5,
                    "wake_minute": 0,
                    "off_hour": 22,
                    "off_minute": 0,
                    "steam": True,
                },
            ],
        }
        sc = _parse_schedule(raw)
        assert len(sc.schedules) == 1
        assert sc.schedules[0].id == "work"
        assert sc.schedules[0].recurrence == "weekly"
        assert sc.schedules[0].wake_hour == 5

    def test_v2_migration(self):
        from espresso_bridge.core.config import _parse_schedule

        raw = {
            "enabled": True,
            "rules": [
                {
                    "id": "work",
                    "name": "Work",
                    "type": "weekly",
                    "days": ["monday", "friday"],
                    "entry": {"wake_hour": 5, "wake_minute": 0, "off_hour": 22},
                },
            ],
            "events": {
                "2026-04-01": {"wake_hour": 9, "wake_minute": 0},
            },
            "skips": [],
        }
        sc = _parse_schedule(raw)
        assert len(sc.schedules) >= 1
        rule_sched = sc.schedules[0]
        assert rule_sched.id == "work"
        assert rule_sched.recurrence == "weekly"
        assert "monday" in rule_sched.days
        # Event migrated as a once schedule
        event_scheds = [s for s in sc.schedules if s.recurrence == "once"]
        assert len(event_scheds) == 1
        assert event_scheds[0].date == "2026-04-01"

    def test_v1_migration(self):
        from espresso_bridge.core.config import _parse_schedule

        raw = {
            "enabled": True,
            "reference_date": "2026-03-30",
            "week_a": {
                "saturday": {
                    "enabled": True,
                    "on_hour": 8,
                    "on_minute": 0,
                    "off_hour": 20,
                    "steam": True,
                },
            },
            "week_b": {
                "monday": {
                    "enabled": True,
                    "on_hour": 7,
                    "on_minute": 0,
                    "off_hour": 22,
                    "steam": True,
                },
            },
        }
        sc = _parse_schedule(raw)
        assert len(sc.schedules) == 1
        sched = sc.schedules[0]
        assert sched.id == "migrated"
        assert sched.recurrence == "biweekly"
        assert "saturday" in sched.days
        assert "monday" in sched.days_b

    def test_empty(self):
        from espresso_bridge.core.config import _parse_schedule

        sc = _parse_schedule({})
        assert sc.schedules == []


# -- Config YAML round-trip --


class TestSchedulePersistence:
    def test_round_trip(self, tmp_path):
        from espresso_bridge.core.config import AppConfig

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("server:\n  port: 8080\n")

        cfg = AppConfig.load(cfg_path)
        sched = ScheduleConfig(schedules=[
            Schedule(
                id="weekend",
                name="Weekend",
                recurrence="weekly",
                days=["saturday", "sunday"],
                wake_hour=8,
                off_hour=20,
                steam=False,
            ),
        ])
        cfg.save_schedule(sched)

        cfg2 = AppConfig.load(cfg_path)
        assert len(cfg2.schedule.schedules) == 1
        s = cfg2.schedule.schedules[0]
        assert s.id == "weekend"
        assert s.wake_hour == 8
        assert s.off_hour == 20
        assert s.steam is False
        assert "saturday" in s.days

    def test_preserves_other_config(self, tmp_path):
        from espresso_bridge.core.config import AppConfig

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("server:\n  port: 9090\n")

        cfg = AppConfig.load(cfg_path)
        cfg.save_schedule(ScheduleConfig(schedules=[
            Schedule(id="test", recurrence="daily"),
        ]))

        cfg2 = AppConfig.load(cfg_path)
        assert cfg2.server.port == 9090
        assert len(cfg2.schedule.schedules) == 1


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
        config.schedule = ScheduleConfig(schedules=[
            Schedule(
                id="work",
                name="Work",
                recurrence="weekly",
                days=["monday", "tuesday", "wednesday", "thursday", "friday"],
            ),
        ])

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

    def test_get_schedules(self, client):
        tc, _, _ = client
        r = tc.get("/api/schedules")
        assert r.status_code == 200
        data = r.json()
        assert "schedules" in data
        assert len(data["schedules"]) == 1
        assert data["schedules"][0]["id"] == "work"
        assert "summary" in data["schedules"][0]
        assert "next_event" in data

    def test_create_schedule(self, client):
        tc, manager, _ = client
        payload = {
            "name": "Weekend",
            "recurrence": "weekly",
            "days": ["saturday", "sunday"],
            "wake_hour": 8,
            "off_hour": 20,
        }
        r = tc.post("/api/schedules", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        # Auto-generated id
        new_scheds = [s for s in data["schedules"] if s["name"] == "Weekend"]
        assert len(new_scheds) == 1
        assert new_scheds[0]["id"] != ""
        manager.update_schedule.assert_called_once()

    def test_delete_schedule(self, client):
        tc, manager, _ = client
        r = tc.delete("/api/schedules/work")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert len(data["schedules"]) == 0
        manager.update_schedule.assert_called_once()

    def test_toggle_schedule(self, client):
        tc, manager, _ = client
        r = tc.post("/api/schedules/work/toggle")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        toggled = [s for s in data["schedules"] if s["id"] == "work"]
        assert len(toggled) == 1
        assert toggled[0]["enabled"] is False
        manager.update_schedule.assert_called_once()

    def test_update_schedule(self, client):
        tc, manager, _ = client
        payload = {
            "name": "Updated Work",
            "recurrence": "weekly",
            "days": ["monday", "wednesday", "friday"],
            "wake_hour": 6,
            "off_hour": 22,
        }
        r = tc.put("/api/schedules/work", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        updated = [s for s in data["schedules"] if s["id"] == "work"]
        assert len(updated) == 1
        assert updated[0]["name"] == "Updated Work"
        assert updated[0]["wake_hour"] == 6
        manager.update_schedule.assert_called_once()

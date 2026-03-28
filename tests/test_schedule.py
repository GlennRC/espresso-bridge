"""Tests for the flexible schedule models, config migration, and API endpoints."""

from datetime import date, datetime, time

import pytest

from espresso_bridge.core.models import (
    RecurringRule,
    ScheduleConfig,
    ScheduleEntry,
)


# -- ScheduleEntry --


class TestScheduleEntry:
    def test_defaults(self):
        e = ScheduleEntry()
        assert e.wake_hour == 4
        assert e.wake_minute == 50
        assert e.off_hour == 23
        assert e.off_minute == 0
        assert e.steam is True

    def test_time_properties(self):
        e = ScheduleEntry(wake_hour=6, wake_minute=30, off_hour=21, off_minute=45)
        assert e.wake_time == time(6, 30)
        assert e.off_time == time(21, 45)

    def test_validation(self):
        with pytest.raises(Exception):
            ScheduleEntry(wake_hour=25)
        with pytest.raises(Exception):
            ScheduleEntry(wake_minute=60)


# -- RecurringRule --


class TestRecurringRule:
    def test_weekly_generates(self):
        rule = RecurringRule(
            id="work", name="Work days", type="weekly",
            days=["monday", "friday"], entry=ScheduleEntry(),
        )
        assert rule.generates(date(2026, 3, 30)) is True   # Monday
        assert rule.generates(date(2026, 4, 3)) is True    # Friday
        assert rule.generates(date(2026, 4, 1)) is False   # Wednesday

    def test_biweekly_generates_week_a(self):
        # reference_date 2026-03-30 is a Monday (week A)
        rule = RecurringRule(
            id="bi", name="Biweekly", type="biweekly",
            reference_date="2026-03-30",
            days=["saturday", "sunday"], days_b=[],
            entry=ScheduleEntry(),
        )
        # Apr 4 (Sat) is in week A (same week as ref) → generates
        assert rule.generates(date(2026, 4, 4)) is True
        # Apr 11 (Sat) is in week B → does not generate
        assert rule.generates(date(2026, 4, 11)) is False

    def test_biweekly_generates_week_b(self):
        rule = RecurringRule(
            id="bi", name="Biweekly", type="biweekly",
            reference_date="2026-03-30",
            days=["saturday"], days_b=["monday", "tuesday"],
            entry=ScheduleEntry(),
        )
        # Apr 6 (Mon) is in week B → generates via days_b
        assert rule.generates(date(2026, 4, 6)) is True

    def test_no_reference_date(self):
        rule = RecurringRule(
            id="bi", name="Biweekly", type="biweekly",
            reference_date="", days=["monday"],
            entry=ScheduleEntry(),
        )
        assert rule.generates(date(2026, 3, 30)) is False


# -- ScheduleConfig.resolve --


class TestScheduleResolve:
    def test_empty_schedule(self):
        sc = ScheduleConfig()
        entry, source = sc.resolve(date(2026, 4, 1))
        assert entry is None
        assert source == ""

    def test_rule_match(self):
        rule = RecurringRule(
            id="daily", name="Daily", type="weekly",
            days=["wednesday"], entry=ScheduleEntry(wake_hour=5),
        )
        sc = ScheduleConfig(rules=[rule])
        entry, source = sc.resolve(date(2026, 4, 1))  # Wednesday
        assert entry is not None
        assert entry.wake_hour == 5
        assert source == "rule:daily"

    def test_event_override(self):
        rule = RecurringRule(
            id="daily", name="Daily", type="weekly",
            days=["wednesday"], entry=ScheduleEntry(wake_hour=5),
        )
        manual = ScheduleEntry(wake_hour=9)
        sc = ScheduleConfig(
            rules=[rule],
            events={"2026-04-01": manual},
        )
        entry, source = sc.resolve(date(2026, 4, 1))  # Wednesday
        assert entry.wake_hour == 9
        assert source == "manual"

    def test_skip_overrides_rule(self):
        rule = RecurringRule(
            id="daily", name="Daily", type="weekly",
            days=["wednesday"], entry=ScheduleEntry(),
        )
        sc = ScheduleConfig(rules=[rule], skips=["2026-04-01"])
        entry, source = sc.resolve(date(2026, 4, 1))
        assert entry is None
        assert source == "skip"

    def test_skip_overrides_event(self):
        sc = ScheduleConfig(
            events={"2026-04-01": ScheduleEntry()},
            skips=["2026-04-01"],
        )
        entry, source = sc.resolve(date(2026, 4, 1))
        assert entry is None
        assert source == "skip"

    def test_multiple_rules_first_wins(self):
        rule_a = RecurringRule(
            id="first", name="First", type="weekly",
            days=["wednesday"], entry=ScheduleEntry(wake_hour=5),
        )
        rule_b = RecurringRule(
            id="second", name="Second", type="weekly",
            days=["wednesday"], entry=ScheduleEntry(wake_hour=9),
        )
        sc = ScheduleConfig(rules=[rule_a, rule_b])
        entry, source = sc.resolve(date(2026, 4, 1))
        assert entry.wake_hour == 5
        assert source == "rule:first"


# -- ScheduleConfig.next_event --


class TestNextEvent:
    def _make_config(self, **kwargs) -> ScheduleConfig:
        """Helper: weekly rule on Mon+Tue with wake=4:50, off=23:00."""
        rule = RecurringRule(
            id="work", name="Work", type="weekly",
            days=["monday", "tuesday"],
            entry=ScheduleEntry(),
        )
        return ScheduleConfig(enabled=True, rules=[rule], **kwargs)

    def test_disabled_returns_none(self):
        sc = ScheduleConfig(enabled=False)
        assert sc.next_event() is None

    def test_before_wake_time(self):
        sc = self._make_config()
        # 2026-03-30 is Monday, before 04:50
        ev = sc.next_event(datetime(2026, 3, 30, 3, 0))
        assert ev["type"] == "on"
        assert ev["hour"] == 4
        assert ev["minute"] == 50

    def test_between_wake_and_off(self):
        sc = self._make_config()
        ev = sc.next_event(datetime(2026, 3, 30, 12, 0))
        assert ev["type"] == "off"
        assert ev["hour"] == 23
        assert ev["minute"] == 0

    def test_after_off_finds_future(self):
        sc = self._make_config()
        # After 23:00 on Monday → should find Tuesday
        ev = sc.next_event(datetime(2026, 3, 30, 23, 30))
        assert ev["type"] == "on"
        assert ev["day"] == "tuesday"

    def test_no_events_returns_none(self):
        sc = ScheduleConfig(enabled=True)
        assert sc.next_event(datetime(2026, 3, 30, 12, 0)) is None

    def test_skipped_day_skipped(self):
        sc = self._make_config(skips=["2026-03-30"])
        # Monday is skipped → should find Tuesday
        ev = sc.next_event(datetime(2026, 3, 30, 3, 0))
        assert ev["type"] == "on"
        assert ev["day"] == "tuesday"

    def test_one_time_event_found(self):
        sc = ScheduleConfig(
            enabled=True,
            events={"2026-04-01": ScheduleEntry(wake_hour=7, wake_minute=0)},
        )
        ev = sc.next_event(datetime(2026, 3, 30, 12, 0))
        assert ev is not None
        assert ev["date"] == "2026-04-01"
        assert ev["hour"] == 7


# -- Config migration --


class TestConfigMigration:
    def test_new_format_parsed(self):
        from espresso_bridge.core.config import _parse_schedule

        raw = {
            "enabled": True,
            "rules": [{
                "id": "work", "name": "Work", "type": "weekly",
                "days": ["monday", "friday"],
                "entry": {"wake_hour": 5, "wake_minute": 0, "off_hour": 22, "off_minute": 0, "steam": True},
            }],
            "events": {"2026-04-01": {"wake_hour": 9}},
            "skips": ["2026-04-02"],
        }
        sc = _parse_schedule(raw)
        assert sc.enabled is True
        assert len(sc.rules) == 1
        assert sc.rules[0].id == "work"
        assert "2026-04-01" in sc.events
        assert "2026-04-02" in sc.skips

    def test_old_format_migrated(self):
        from espresso_bridge.core.config import _parse_schedule

        raw = {
            "enabled": True,
            "reference_date": "2026-03-30",
            "week_a": {
                "saturday": {"enabled": True, "on_hour": 8, "on_minute": 0, "off_hour": 20, "off_minute": 0, "steam": True},
            },
            "week_b": {
                "monday": {"enabled": True, "on_hour": 7, "on_minute": 0, "off_hour": 22, "off_minute": 0, "steam": True},
            },
        }
        sc = _parse_schedule(raw)
        assert sc.enabled is True
        assert len(sc.rules) == 1
        rule = sc.rules[0]
        assert rule.id == "migrated"
        assert rule.type == "biweekly"
        assert "saturday" in rule.days
        assert "monday" in rule.days_b

    def test_empty_schedule(self):
        from espresso_bridge.core.config import _parse_schedule

        sc = _parse_schedule({})
        assert sc.enabled is False
        assert sc.rules == []
        assert sc.events == {}
        assert sc.skips == []


# -- Config YAML round-trip --


class TestSchedulePersistence:
    def test_round_trip(self, tmp_path):
        from espresso_bridge.core.config import AppConfig

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("server:\n  port: 8080\n")

        cfg = AppConfig.load(cfg_path)
        rule = RecurringRule(
            id="weekend", name="Weekend", type="weekly",
            days=["saturday", "sunday"],
            entry=ScheduleEntry(wake_hour=8, off_hour=20, steam=False),
        )
        event_entry = ScheduleEntry(wake_hour=9)
        sched = ScheduleConfig(
            enabled=True,
            rules=[rule],
            events={"2026-04-01": event_entry},
            skips=["2026-04-02"],
        )
        cfg.save_schedule(sched)

        cfg2 = AppConfig.load(cfg_path)
        assert cfg2.schedule.enabled is True
        assert len(cfg2.schedule.rules) == 1
        assert cfg2.schedule.rules[0].id == "weekend"
        assert cfg2.schedule.rules[0].entry.wake_hour == 8
        assert cfg2.schedule.rules[0].entry.steam is False
        assert "2026-04-01" in cfg2.schedule.events
        assert "2026-04-02" in cfg2.schedule.skips

    def test_preserves_other_config(self, tmp_path):
        from espresso_bridge.core.config import AppConfig

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("server:\n  port: 9090\n")

        cfg = AppConfig.load(cfg_path)
        cfg.save_schedule(ScheduleConfig(enabled=True))

        cfg2 = AppConfig.load(cfg_path)
        assert cfg2.server.port == 9090
        assert cfg2.schedule.enabled is True


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
        config.schedule = ScheduleConfig(
            enabled=True,
            rules=[RecurringRule(
                id="work", name="Work", type="weekly",
                days=["monday", "tuesday", "wednesday", "thursday", "friday"],
                entry=ScheduleEntry(),
            )],
        )

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

    def test_get_schedule(self, client):
        tc, _, _ = client
        r = tc.get("/api/lm/schedule")
        assert r.status_code == 200
        data = r.json()
        assert data["schedule"]["enabled"] is True
        assert len(data["resolved"]) == 42
        assert "next_event" in data

    def test_post_schedule(self, client):
        tc, manager, _ = client
        payload = {
            "enabled": True,
            "rules": [{
                "id": "new", "name": "New Rule", "type": "weekly",
                "days": ["saturday"],
                "entry": {"wake_hour": 8, "off_hour": 20},
            }],
            "events": {},
            "skips": [],
        }
        r = tc.post("/api/lm/schedule", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["schedule"]["enabled"] is True
        assert data["schedule"]["rules"][0]["id"] == "new"
        manager.update_schedule.assert_called_once()

    def test_toggle_day_add(self, client):
        tc, manager, _ = client
        r = tc.post(
            "/api/lm/schedule/day/2026-04-15",
            json={"action": "add", "entry": {"wake_hour": 7, "off_hour": 18}},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert "2026-04-15" in data["schedule"]["events"]
        assert data["schedule"]["events"]["2026-04-15"]["wake_hour"] == 7
        manager.update_schedule.assert_called()

    def test_toggle_day_skip(self, client):
        tc, manager, _ = client
        # 2026-04-06 is a Monday → matches the weekly rule
        r = tc.post(
            "/api/lm/schedule/day/2026-04-06",
            json={"action": "skip"},
        )
        assert r.status_code == 200
        data = r.json()
        assert "2026-04-06" in data["schedule"]["skips"]

    def test_toggle_day_remove(self, client):
        tc, manager, config = client
        # First add an event, then remove it
        tc.post(
            "/api/lm/schedule/day/2026-04-15",
            json={"action": "add", "entry": {"wake_hour": 7}},
        )
        r = tc.post(
            "/api/lm/schedule/day/2026-04-15",
            json={"action": "remove"},
        )
        assert r.status_code == 200
        data = r.json()
        assert "2026-04-15" not in data["schedule"]["events"]

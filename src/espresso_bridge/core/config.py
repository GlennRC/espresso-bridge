"""Configuration management for espresso-bridge.

Loads settings from config.yaml with sensible defaults.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from espresso_bridge.core.models import (
    Schedule,
    ScheduleConfig,
)

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config.yaml")


@dataclass
class ShotStopperConfig:
    address: str = ""
    default_weight: int = 36
    auto_reconnect: bool = True
    reconnect_interval: float = 5.0


@dataclass
class LaMarzoccoConfig:
    address: str = ""
    serial_number: str = ""
    username: str = ""
    communication_key: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.serial_number and self.username and self.communication_key)


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass
class AppConfig:
    shotstopper: ShotStopperConfig = field(default_factory=ShotStopperConfig)
    lamarzocco: LaMarzoccoConfig = field(default_factory=LaMarzoccoConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    _config_path: Path = field(default=DEFAULT_CONFIG_PATH, repr=False)

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CONFIG_PATH) -> AppConfig:
        """Load config from YAML file, falling back to defaults."""
        path = Path(path)
        if not path.exists():
            logger.info(f"No config at {path}, using defaults")
            return cls(_config_path=path)

        with open(path) as f:
            raw = yaml.safe_load(f) or {}

        ss = raw.get("shotstopper", {})
        lm = raw.get("lamarzocco", {})
        srv = raw.get("server", {})
        sched = raw.get("schedule", {})

        return cls(
            shotstopper=ShotStopperConfig(
                address=ss.get("address", ""),
                default_weight=ss.get("default_weight", 36),
                auto_reconnect=ss.get("auto_reconnect", True),
                reconnect_interval=ss.get("reconnect_interval", 5.0),
            ),
            lamarzocco=LaMarzoccoConfig(
                address=lm.get("address", ""),
                serial_number=lm.get("serial_number", ""),
                username=lm.get("username", ""),
                communication_key=lm.get("communication_key", ""),
            ),
            server=ServerConfig(
                host=srv.get("host", "0.0.0.0"),
                port=srv.get("port", 8080),
            ),
            schedule=_parse_schedule(sched),
            _config_path=path,
        )

    def save_schedule(self, schedule: ScheduleConfig) -> None:
        """Update the schedule section in the config YAML file (preserves other sections)."""
        self.schedule = schedule
        path = self._config_path

        # Load existing YAML or start fresh
        if path.exists():
            with open(path) as f:
                raw = yaml.safe_load(f) or {}
        else:
            raw = {}

        # Serialize schedule
        raw["schedule"] = schedule.model_dump()

        with open(path, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Schedule saved to {path}")


def _parse_schedule(raw: dict) -> ScheduleConfig:
    """Parse schedule section from YAML. Auto-migrates old formats."""
    if not raw:
        return ScheduleConfig()
    try:
        # v1: week_a/week_b format
        if "week_a" in raw or "week_b" in raw:
            return _migrate_v1(raw)
        # v2: rules/events/skips format
        if "rules" in raw or "events" in raw:
            return _migrate_v2(raw)
        # v3: current schedules list format
        return ScheduleConfig(**raw)
    except Exception:
        logger.warning("Invalid schedule config, using defaults")
        return ScheduleConfig()


def _migrate_v1(raw: dict) -> ScheduleConfig:
    """Convert v1 week_a/week_b → Schedule list."""
    days_a: list[str] = []
    days_b: list[str] = []
    wake_h, wake_m, off_h, steam = 4, 50, 23, True

    for week_key, day_list in [("week_a", days_a), ("week_b", days_b)]:
        week = raw.get(week_key, {})
        for day_name, day_cfg in week.items():
            if isinstance(day_cfg, dict) and day_cfg.get("enabled"):
                day_list.append(day_name)
                if len(days_a) == 1 and week_key == "week_a":
                    wake_h = day_cfg.get("on_hour", 4)
                    wake_m = day_cfg.get("on_minute", 50)
                    off_h = day_cfg.get("off_hour", 23)
                    steam = day_cfg.get("steam", True)

    if not days_a and not days_b:
        return ScheduleConfig()

    sched = Schedule(
        id="migrated",
        name="Migrated Schedule",
        enabled=raw.get("enabled", False),
        wake_hour=wake_h,
        wake_minute=wake_m,
        off_hour=off_h,
        steam=steam,
        recurrence="biweekly",
        reference_date=raw.get("reference_date", ""),
        days=days_a,
        days_b=days_b,
    )
    return ScheduleConfig(schedules=[sched])


def _migrate_v2(raw: dict) -> ScheduleConfig:
    """Convert v2 rules/events/skips → Schedule list."""
    schedules: list[Schedule] = []

    for rule in raw.get("rules", []):
        entry = rule.get("entry", {})
        schedules.append(Schedule(
            id=rule.get("id", ""),
            name=rule.get("name", ""),
            enabled=raw.get("enabled", True),
            wake_hour=entry.get("wake_hour", 4),
            wake_minute=entry.get("wake_minute", 50),
            off_hour=entry.get("off_hour", 23),
            off_minute=entry.get("off_minute", 0),
            steam=entry.get("steam", True),
            recurrence=rule.get("type", "weekly"),
            reference_date=rule.get("reference_date", ""),
            days=rule.get("days", []),
            days_b=rule.get("days_b", []),
        ))

    for iso_date, evt in raw.get("events", {}).items():
        if isinstance(evt, dict):
            schedules.append(Schedule(
                id=f"evt-{iso_date}",
                name=f"Event {iso_date}",
                enabled=True,
                wake_hour=evt.get("wake_hour", 4),
                wake_minute=evt.get("wake_minute", 50),
                off_hour=evt.get("off_hour", 23),
                steam=evt.get("steam", True),
                recurrence="once",
                date=iso_date,
            ))

    return ScheduleConfig(schedules=schedules)

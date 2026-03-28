"""Configuration management for espresso-bridge.

Loads settings from config.yaml with sensible defaults.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from espresso_bridge.core.models import (
    RecurringRule,
    ScheduleConfig,
    ScheduleEntry,
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
    """Parse schedule section from YAML dict. Auto-migrates old week_a/week_b format."""
    if not raw:
        return ScheduleConfig()
    try:
        # Detect old format by presence of week_a/week_b keys
        if "week_a" in raw or "week_b" in raw:
            return _migrate_old_schedule(raw)
        return ScheduleConfig(**raw)
    except Exception:
        logger.warning("Invalid schedule config, using defaults")
        return ScheduleConfig()


def _migrate_old_schedule(raw: dict) -> ScheduleConfig:
    """Convert old week_a/week_b format to new rules+events format."""
    days_a: list[str] = []
    days_b: list[str] = []
    entry = ScheduleEntry()

    for week_key, day_list in [("week_a", days_a), ("week_b", days_b)]:
        week = raw.get(week_key, {})
        for day_name, day_cfg in week.items():
            if isinstance(day_cfg, dict) and day_cfg.get("enabled"):
                day_list.append(day_name)
                # Use first enabled day's time as the rule entry
                if not days_a or (week_key == "week_a" and len(days_a) == 1):
                    entry = ScheduleEntry(
                        wake_hour=day_cfg.get("on_hour", 4),
                        wake_minute=day_cfg.get("on_minute", 50),
                        off_hour=day_cfg.get("off_hour", 23),
                        off_minute=day_cfg.get("off_minute", 0),
                        steam=day_cfg.get("steam", True),
                    )

    rule = RecurringRule(
        id="migrated",
        name="Migrated Schedule",
        type="biweekly",
        reference_date=raw.get("reference_date", ""),
        days=days_a,
        days_b=days_b,
        entry=entry,
    )

    rules = [rule] if days_a or days_b else []
    return ScheduleConfig(enabled=raw.get("enabled", False), rules=rules)

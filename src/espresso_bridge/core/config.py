"""Configuration management for espresso-bridge.

Loads settings from config.yaml with sensible defaults.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from espresso_bridge.core.models import (
    DaySchedule,
    ScheduleConfig,
    WeekSchedule,
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
    """Parse schedule section from YAML dict."""
    if not raw:
        return ScheduleConfig()
    try:
        return ScheduleConfig(**raw)
    except Exception:
        logger.warning("Invalid schedule config, using defaults")
        return ScheduleConfig()

"""Data models for espresso-bridge."""

from __future__ import annotations

from datetime import date, datetime, time
from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, Field


class ScaleStatus(IntEnum):
    DISCONNECTED = 0
    CONNECTED = 1


class ShotStopperState(BaseModel):
    """Live state read from the ShotStopper BLE device."""

    connected: bool = False
    enabled: bool = True
    weight_target: int = Field(default=36, ge=10, le=200, description="Target weight in grams")
    scale_status: ScaleStatus = ScaleStatus.DISCONNECTED
    shot_active: bool = False
    firmware_version: int = 0

    # Configuration
    auto_tare: bool = True
    momentary: bool = False
    reed_switch: bool = False
    min_shot_duration: int = 3
    max_shot_duration: int = 50
    drip_delay: int = 3

    # WiFi (if configured on ShotStopper)
    wifi_ssid: str = ""
    wifi_ip: str = ""


class ShotStopperConfig(BaseModel):
    """Writable configuration for the ShotStopper."""

    enabled: bool | None = None
    weight_target: int | None = Field(default=None, ge=10, le=200)
    auto_tare: bool | None = None
    momentary: bool | None = None
    reed_switch: bool | None = None
    min_shot_duration: int | None = None
    max_shot_duration: int | None = None
    drip_delay: int | None = None


class LaMarzoccoState(BaseModel):
    """Live state of the La Marzocco Linea Micra."""

    connected: bool = False
    turned_on: bool = False

    # Coffee boiler
    coffee_boiler_enabled: bool = False
    coffee_temp_target: float = Field(default=93.0, ge=85.0, le=104.0)
    coffee_temp_current: float = 0.0

    # Steam boiler
    steam_enabled: bool = False
    steam_level: int = Field(default=2, ge=1, le=3)
    steam_temp_target: float = 128.0


# -- Schedule models --

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
WeekdayName = Literal[
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
]


class ScheduleEntry(BaseModel):
    """Configuration for a single ON day."""

    wake_hour: int = Field(default=4, ge=0, le=23)
    wake_minute: int = Field(default=50, ge=0, le=59)
    off_hour: int = Field(default=23, ge=0, le=23)
    off_minute: int = Field(default=0, ge=0, le=59)
    steam: bool = True

    @property
    def wake_time(self) -> time:
        return time(self.wake_hour, self.wake_minute)

    @property
    def off_time(self) -> time:
        return time(self.off_hour, self.off_minute)


class RecurringRule(BaseModel):
    """Generates schedule events on a repeating pattern."""

    id: str = ""
    name: str = ""
    type: Literal["weekly", "biweekly"] = "biweekly"
    reference_date: str = ""  # ISO Monday for biweekly alignment
    days: list[str] = []  # weekday names (Week A for biweekly)
    days_b: list[str] = []  # Week B days (biweekly only)
    entry: ScheduleEntry = Field(default_factory=ScheduleEntry)

    def generates(self, d: date) -> bool:
        """Does this rule generate an event on date d?"""
        day_name = WEEKDAYS[d.weekday()]
        if self.type == "weekly":
            return day_name in self.days
        elif self.type == "biweekly":
            if not self.reference_date:
                return False
            ref = date.fromisoformat(self.reference_date)
            weeks = (d - ref).days // 7
            if weeks % 2 == 0:
                return day_name in self.days
            else:
                return day_name in self.days_b
        return False


class ScheduleConfig(BaseModel):
    """Flexible schedule with recurring rules and per-date overrides."""

    enabled: bool = False
    rules: list[RecurringRule] = Field(default_factory=list)
    events: dict[str, ScheduleEntry] = Field(default_factory=dict)  # ISO date → one-time
    skips: list[str] = Field(default_factory=list)  # ISO dates to skip

    def resolve(self, d: date) -> tuple[ScheduleEntry | None, str]:
        """Get effective entry and source for a date.
        Priority: skips > events > rules > nothing.
        Returns (entry_or_None, source_string).
        source: 'skip', 'manual', 'rule:<id>', or '' (nothing).
        """
        iso = d.isoformat()
        if iso in self.skips:
            return None, "skip"
        if iso in self.events:
            return self.events[iso], "manual"
        for rule in self.rules:
            if rule.generates(d):
                return rule.entry, f"rule:{rule.id}"
        return None, ""

    def next_event(self, now: datetime | None = None) -> dict | None:
        """Find the next scheduled ON or OFF event within 60 days."""
        if not self.enabled:
            return None
        now = now or datetime.now()
        current_time = now.time()
        from datetime import timedelta

        for i in range(60):
            d = now.date() + timedelta(days=i)
            entry, source = self.resolve(d)
            if not entry:
                continue

            day_name = WEEKDAYS[d.weekday()]

            if i == 0:  # Today
                if current_time < entry.wake_time:
                    return {
                        "type": "on",
                        "day": day_name,
                        "date": d.isoformat(),
                        "hour": entry.wake_hour,
                        "minute": entry.wake_minute,
                    }
                if current_time < entry.off_time:
                    return {
                        "type": "off",
                        "day": day_name,
                        "date": d.isoformat(),
                        "hour": entry.off_hour,
                        "minute": entry.off_minute,
                    }
            else:
                return {
                    "type": "on",
                    "day": day_name,
                    "date": d.isoformat(),
                    "hour": entry.wake_hour,
                    "minute": entry.wake_minute,
                }

        return None

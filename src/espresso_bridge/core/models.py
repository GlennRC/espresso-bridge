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


class DaySchedule(BaseModel):
    """Schedule for a single day."""

    enabled: bool = False
    on_hour: int = Field(default=7, ge=0, le=23)
    on_minute: int = Field(default=0, ge=0, le=59)
    off_hour: int = Field(default=22, ge=0, le=23)
    off_minute: int = Field(default=0, ge=0, le=59)
    steam: bool = True

    @property
    def on_time(self) -> time:
        return time(self.on_hour, self.on_minute)

    @property
    def off_time(self) -> time:
        return time(self.off_hour, self.off_minute)


def _default_week() -> dict[str, DaySchedule]:
    return {day: DaySchedule() for day in WEEKDAYS}


class WeekSchedule(BaseModel):
    """Schedule for one week (Mon–Sun)."""

    monday: DaySchedule = Field(default_factory=DaySchedule)
    tuesday: DaySchedule = Field(default_factory=DaySchedule)
    wednesday: DaySchedule = Field(default_factory=DaySchedule)
    thursday: DaySchedule = Field(default_factory=DaySchedule)
    friday: DaySchedule = Field(default_factory=DaySchedule)
    saturday: DaySchedule = Field(default_factory=DaySchedule)
    sunday: DaySchedule = Field(default_factory=DaySchedule)

    def get_day(self, day_name: str) -> DaySchedule:
        """Get schedule for a day by name (lowercase)."""
        return getattr(self, day_name.lower())


class ScheduleConfig(BaseModel):
    """Biweekly schedule with two alternating week profiles."""

    enabled: bool = False
    week_a: WeekSchedule = Field(default_factory=WeekSchedule)
    week_b: WeekSchedule = Field(default_factory=WeekSchedule)
    reference_date: str = ""  # ISO date of a known Week A Monday

    def current_week(self, now: date | None = None) -> str:
        """Return 'a' or 'b' based on weeks since reference_date."""
        if not self.reference_date:
            return "a"
        now = now or date.today()
        ref = date.fromisoformat(self.reference_date)
        weeks = (now - ref).days // 7
        return "a" if weeks % 2 == 0 else "b"

    def current_week_schedule(self, now: date | None = None) -> WeekSchedule:
        """Get the active week schedule."""
        return self.week_a if self.current_week(now) == "a" else self.week_b

    def today_schedule(self, now: datetime | None = None) -> DaySchedule:
        """Get today's schedule entry."""
        now = now or datetime.now()
        day_name = WEEKDAYS[now.weekday()]
        week = self.current_week_schedule(now.date())
        return week.get_day(day_name)

    def next_event(self, now: datetime | None = None) -> dict | None:
        """Compute the next scheduled on/off event. Returns dict with type, day, time or None."""
        if not self.enabled:
            return None
        now = now or datetime.now()
        current_time = now.time()
        today = self.today_schedule(now)

        # Check today first
        if today.enabled:
            if current_time < today.on_time:
                return {
                    "type": "on",
                    "day": WEEKDAYS[now.weekday()],
                    "hour": today.on_hour,
                    "minute": today.on_minute,
                }
            if current_time < today.off_time:
                return {
                    "type": "off",
                    "day": WEEKDAYS[now.weekday()],
                    "hour": today.off_hour,
                    "minute": today.off_minute,
                }

        # Scan next 14 days
        for i in range(1, 15):
            future = datetime(now.year, now.month, now.day)
            from datetime import timedelta

            future = future + timedelta(days=i)
            day_name = WEEKDAYS[future.weekday()]
            week = self.current_week_schedule(future.date())
            day_sched = week.get_day(day_name)
            if day_sched.enabled:
                return {
                    "type": "on",
                    "day": day_name,
                    "hour": day_sched.on_hour,
                    "minute": day_sched.on_minute,
                }

        return None

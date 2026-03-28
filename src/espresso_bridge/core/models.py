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


class Schedule(BaseModel):
    """A single schedule defining when the machine turns on."""

    id: str = ""
    name: str = ""
    enabled: bool = True
    wake_hour: int = Field(default=4, ge=0, le=23)
    wake_minute: int = Field(default=50, ge=0, le=59)
    off_hour: int = Field(default=23, ge=0, le=23)
    off_minute: int = Field(default=0, ge=0, le=59)
    steam: bool = True

    recurrence: Literal["once", "daily", "weekly", "biweekly", "monthly"] = "weekly"

    # Once: specific date
    date: str = ""

    # Weekly / Biweekly Week A
    days: list[str] = Field(default_factory=list)

    # Biweekly Week B
    days_b: list[str] = Field(default_factory=list)
    reference_date: str = ""

    # Monthly
    month_days: list[int] = Field(default_factory=list)

    @property
    def wake_time(self) -> time:
        return time(self.wake_hour, self.wake_minute)

    @property
    def off_time(self) -> time:
        return time(self.off_hour, self.off_minute)

    def fires_on(self, d: date) -> bool:
        """Does this schedule fire on date d?"""
        if not self.enabled:
            return False
        if self.recurrence == "once":
            return self.date == d.isoformat()
        elif self.recurrence == "daily":
            return True
        elif self.recurrence == "weekly":
            return WEEKDAYS[d.weekday()] in self.days
        elif self.recurrence == "biweekly":
            if not self.reference_date:
                return False
            ref = date.fromisoformat(self.reference_date)
            weeks = (d - ref).days // 7
            day_name = WEEKDAYS[d.weekday()]
            return day_name in (self.days if weeks % 2 == 0 else self.days_b)
        elif self.recurrence == "monthly":
            return d.day in self.month_days
        return False

    def summary(self) -> str:
        """Human-readable summary."""
        hr = self.wake_hour % 12 or 12
        ampm = "AM" if self.wake_hour < 12 else "PM"
        t = f"{hr}:{self.wake_minute:02d} {ampm}"
        if self.recurrence == "once":
            return f"Once · {self.date} · {t}"
        elif self.recurrence == "daily":
            return f"Daily · {t}"
        elif self.recurrence == "weekly":
            abbrs = [d[:3].title() for d in self.days]
            return f"Weekly · {', '.join(abbrs)} · {t}"
        elif self.recurrence == "biweekly":
            n = len(self.days) + len(self.days_b)
            return f"Biweekly · {n} days · {t}"
        elif self.recurrence == "monthly":
            return f"Monthly · {len(self.month_days)} days · {t}"
        return t


class ScheduleConfig(BaseModel):
    """Collection of schedules."""

    schedules: list[Schedule] = Field(default_factory=list)

    def resolve(self, d: date) -> Schedule | None:
        """Get first enabled schedule that fires on date d."""
        for sched in self.schedules:
            if sched.enabled and sched.fires_on(d):
                return sched
        return None

    def next_event(self, now: datetime | None = None) -> dict | None:
        """Find the next scheduled ON or OFF event within 60 days."""
        if not self.schedules:
            return None
        now = now or datetime.now()
        current_time = now.time()
        from datetime import timedelta

        for i in range(60):
            d = now.date() + timedelta(days=i)
            sched = self.resolve(d)
            if not sched:
                continue

            day_name = WEEKDAYS[d.weekday()]

            if i == 0:
                if current_time < sched.wake_time:
                    return {
                        "type": "on",
                        "day": day_name,
                        "date": d.isoformat(),
                        "hour": sched.wake_hour,
                        "minute": sched.wake_minute,
                    }
                if current_time < sched.off_time:
                    return {
                        "type": "off",
                        "day": day_name,
                        "date": d.isoformat(),
                        "hour": sched.off_hour,
                        "minute": sched.off_minute,
                    }
            else:
                return {
                    "type": "on",
                    "day": day_name,
                    "date": d.isoformat(),
                    "hour": sched.wake_hour,
                    "minute": sched.wake_minute,
                }

        return None

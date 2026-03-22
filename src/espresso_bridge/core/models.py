"""Data models for espresso-bridge."""

from enum import IntEnum

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

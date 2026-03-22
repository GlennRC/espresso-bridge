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

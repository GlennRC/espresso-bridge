"""FastAPI server and API routes for espresso-bridge.

Exposes REST endpoints for device control and a WebSocket for live state streaming.
Serves the static touchscreen UI from /static.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from espresso_bridge.ble.manager import DeviceManager
from espresso_bridge.core.config import AppConfig
from espresso_bridge.core.models import ScheduleConfig, ScheduleEntry
from espresso_bridge.core.state import StateStore

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent.parent / "static"


# -- Request models (must be module-level for FastAPI) --


class WeightRequest(BaseModel):
    grams: int = Field(ge=10, le=200)


class ShotStopperSettingsRequest(BaseModel):
    enabled: bool | None = None
    auto_tare: bool | None = None
    momentary: bool | None = None
    reed_switch: bool | None = None
    min_shot_duration: int | None = None
    max_shot_duration: int | None = None
    drip_delay: int | None = None


class PowerRequest(BaseModel):
    on: bool


class TempRequest(BaseModel):
    celsius: float = Field(ge=85.0, le=104.0)


class SteamRequest(BaseModel):
    level: int | None = Field(default=None, ge=1, le=3)
    enabled: bool | None = None


def create_app(
    manager: DeviceManager,
    store: StateStore,
    config: AppConfig | None = None,
    watchdog_coro=None,
) -> FastAPI:
    """Create the FastAPI application with device manager and state store injected."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await manager.start()
        wd_task = None
        if watchdog_coro:
            wd_task = asyncio.create_task(watchdog_coro())
        yield
        if wd_task:
            wd_task.cancel()
        await manager.stop()

    app = FastAPI(title="espresso-bridge", version="0.1.0", lifespan=lifespan)

    # -- Status --

    @app.get("/api/status")
    async def get_status():
        """Full system state."""
        return {
            **store.state.to_dict(),
            "connections": {
                "shotstopper": manager.ss_phase.value,
                "lamarzocco": manager.lm_phase.value,
            },
        }

    # -- ShotStopper endpoints --

    @app.post("/api/shotstopper/weight")
    async def set_weight(req: WeightRequest):
        """Set the ShotStopper target brew weight."""
        ok = await manager.shotstopper.set_weight(req.grams)
        return {"ok": ok, "weight": req.grams}

    @app.post("/api/shotstopper/settings")
    async def set_shotstopper_settings(req: ShotStopperSettingsRequest):
        """Update ShotStopper configuration."""
        from espresso_bridge.core.models import ShotStopperConfig

        config = ShotStopperConfig(**req.model_dump(exclude_none=True))
        ok = await manager.shotstopper.apply_config(config)
        return {"ok": ok}

    # -- La Marzocco endpoints --

    @app.post("/api/lm/power")
    async def set_lm_power(req: PowerRequest):
        """Turn La Marzocco on or off."""
        if not manager.lamarzocco:
            return {"ok": False, "error": "La Marzocco not configured"}
        ok = await manager.lamarzocco.set_power(req.on)
        return {"ok": ok}

    @app.post("/api/lm/temperature")
    async def set_lm_temp(req: TempRequest):
        """Set La Marzocco brew boiler temperature."""
        if not manager.lamarzocco:
            return {"ok": False, "error": "La Marzocco not configured"}
        ok = await manager.lamarzocco.set_coffee_temp(req.celsius)
        return {"ok": ok, "temperature": req.celsius}

    @app.post("/api/lm/steam")
    async def set_lm_steam(req: SteamRequest):
        """Control La Marzocco steam."""
        if not manager.lamarzocco:
            return {"ok": False, "error": "La Marzocco not configured"}

        results = {}
        if req.enabled is not None:
            results["steam_enabled"] = await manager.lamarzocco.set_steam_enabled(req.enabled)
        if req.level is not None:
            results["steam_level"] = await manager.lamarzocco.set_steam_level(req.level)
        return {"ok": all(results.values()), **results}

    # -- Schedule endpoints --

    @app.get("/api/lm/schedule")
    async def get_schedule():
        """Get the full schedule config with resolved days."""
        sched = config.schedule if config else ScheduleConfig()
        from datetime import datetime, timedelta

        now = datetime.now()
        today = now.date()

        # Resolve next 42 days for the calendar strip
        resolved = []
        for i in range(42):
            d = today + timedelta(days=i)
            entry, source = sched.resolve(d)
            day_info = {
                "date": d.isoformat(),
                "weekday": d.strftime("%A").lower(),
                "source": source,
            }
            if entry:
                day_info["entry"] = entry.model_dump()
            resolved.append(day_info)

        return {
            "schedule": sched.model_dump(),
            "resolved": resolved,
            "next_event": sched.next_event(now),
        }

    @app.post("/api/lm/schedule")
    async def set_schedule(new_sched: ScheduleConfig):
        """Update the schedule config. Persists to config.yaml."""
        if config:
            config.save_schedule(new_sched)
            manager.update_schedule(new_sched)
        from datetime import datetime

        return {
            "ok": True,
            "schedule": new_sched.model_dump(),
            "next_event": new_sched.next_event(),
        }

    @app.post("/api/lm/schedule/day/{iso_date}")
    async def toggle_schedule_day(iso_date: str, body: dict | None = None):
        """Toggle a specific day. Body can include {action: 'add'|'skip'|'remove', entry: {...}}."""
        from datetime import date as date_type, datetime

        sched = config.schedule if config else ScheduleConfig()
        action = (body or {}).get("action", "toggle")
        entry_data = (body or {}).get("entry")

        if action == "skip":
            if iso_date not in sched.skips:
                sched.skips.append(iso_date)
            sched.events.pop(iso_date, None)
        elif action == "remove":
            sched.skips = [s for s in sched.skips if s != iso_date]
            sched.events.pop(iso_date, None)
        elif action == "add":
            sched.skips = [s for s in sched.skips if s != iso_date]
            entry = ScheduleEntry(**(entry_data or {}))
            sched.events[iso_date] = entry
        else:  # toggle
            current_entry, source = sched.resolve(date_type.fromisoformat(iso_date))
            if current_entry:
                # Currently ON → skip or remove
                if source == "manual":
                    sched.events.pop(iso_date, None)
                else:
                    if iso_date not in sched.skips:
                        sched.skips.append(iso_date)
            else:
                # Currently OFF → add one-time event
                sched.skips = [s for s in sched.skips if s != iso_date]
                entry = ScheduleEntry(**(entry_data or {}))
                sched.events[iso_date] = entry

        if config:
            config.save_schedule(sched)
            manager.update_schedule(sched)

        return {
            "ok": True,
            "schedule": sched.model_dump(),
            "next_event": sched.next_event(),
        }

    # -- WebSocket for live state --

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        """Stream live system state to connected UI clients."""
        await ws.accept()
        queue = store.subscribe()

        try:
            # Send current state immediately
            await ws.send_text(json.dumps(store.state.to_dict()))

            while True:
                state = await queue.get()
                await ws.send_text(json.dumps(state.to_dict()))
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.debug("WebSocket error", exc_info=True)
        finally:
            store.unsubscribe(queue)

    # -- Static files (touchscreen UI) --

    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app

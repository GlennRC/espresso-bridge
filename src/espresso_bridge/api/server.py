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
from espresso_bridge.core.models import Schedule, ScheduleConfig
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

    @app.post("/api/shotstopper/toggle")
    async def toggle_shotstopper():
        """Toggle brew-by-weight on the ShotStopper device via BLE."""
        ss = manager.shotstopper
        if not ss.connected:
            return {"ok": False, "error": "ShotStopper not connected"}
        new_val = not ss.state.enabled
        ok = await ss._write_field("enabled", new_val)
        return {"ok": ok, "enabled": new_val}

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

    def _schedule_response():
        """Build standard schedule response."""
        sched = config.schedule if config else ScheduleConfig()
        return {
            "schedules": [
                {**s.model_dump(), "summary": s.summary()} for s in sched.schedules
            ],
            "next_event": sched.next_event(),
        }

    @app.get("/api/schedules")
    async def get_schedules():
        """List all schedules with next event."""
        return _schedule_response()

    @app.post("/api/schedules")
    async def create_schedule(new_sched: Schedule):
        """Create a new schedule."""
        import uuid

        if not new_sched.id:
            new_sched.id = str(uuid.uuid4())[:8]
        sched_cfg = config.schedule if config else ScheduleConfig()
        sched_cfg.schedules.append(new_sched)
        if config:
            config.save_schedule(sched_cfg)
            manager.update_schedule(sched_cfg)
        return {"ok": True, **_schedule_response()}

    @app.put("/api/schedules/{sched_id}")
    async def update_schedule_endpoint(sched_id: str, updated: Schedule):
        """Update an existing schedule."""
        sched_cfg = config.schedule if config else ScheduleConfig()
        for i, s in enumerate(sched_cfg.schedules):
            if s.id == sched_id:
                updated.id = sched_id
                sched_cfg.schedules[i] = updated
                if config:
                    config.save_schedule(sched_cfg)
                    manager.update_schedule(sched_cfg)
                return {"ok": True, **_schedule_response()}
        return {"ok": False, "error": "not found"}

    @app.delete("/api/schedules/{sched_id}")
    async def delete_schedule(sched_id: str):
        """Delete a schedule."""
        sched_cfg = config.schedule if config else ScheduleConfig()
        sched_cfg.schedules = [s for s in sched_cfg.schedules if s.id != sched_id]
        if config:
            config.save_schedule(sched_cfg)
            manager.update_schedule(sched_cfg)
        return {"ok": True, **_schedule_response()}

    @app.post("/api/schedules/{sched_id}/toggle")
    async def toggle_schedule(sched_id: str):
        """Toggle a schedule's enabled state."""
        sched_cfg = config.schedule if config else ScheduleConfig()
        for s in sched_cfg.schedules:
            if s.id == sched_id:
                s.enabled = not s.enabled
                if config:
                    config.save_schedule(sched_cfg)
                    manager.update_schedule(sched_cfg)
                return {"ok": True, **_schedule_response()}
        return {"ok": False, "error": "not found"}

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

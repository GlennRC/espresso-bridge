"""FastAPI server and API routes for espresso-bridge.

Exposes REST endpoints for device control and a WebSocket for live state streaming.
Serves the static touchscreen UI from /static.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from espresso_bridge.ble.manager import DeviceManager
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


def create_app(manager: DeviceManager, store: StateStore) -> FastAPI:
    """Create the FastAPI application with device manager and state store injected."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await manager.start()
        yield
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

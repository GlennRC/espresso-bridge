"""Centralized state store with event bus for live updates.

Holds in-memory state for both devices and provides a pub/sub mechanism
so the WebSocket layer can push updates to connected UI clients.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from espresso_bridge.core.models import LaMarzoccoState, ShotStopperState

logger = logging.getLogger(__name__)

StateCallback = Callable[["SystemState"], Any]


class SystemState:
    """Aggregated system state for both devices."""

    def __init__(self) -> None:
        self.shotstopper = ShotStopperState()
        self.lamarzocco = LaMarzoccoState()

    def to_dict(self) -> dict:
        return {
            "shotstopper": self.shotstopper.model_dump(),
            "lamarzocco": self.lamarzocco.model_dump(),
        }


class StateStore:
    """In-memory state store with async event bus.

    Subscribers receive the full SystemState on every change.
    Used by the WebSocket endpoint to push live updates to the UI.
    """

    def __init__(self) -> None:
        self._state = SystemState()
        self._subscribers: list[asyncio.Queue[SystemState]] = []
        self._callbacks: list[StateCallback] = []

    @property
    def state(self) -> SystemState:
        return self._state

    # -- Updates from adapters --

    def update_shotstopper(self, ss_state: ShotStopperState) -> None:
        """Called by the ShotStopper adapter on state change."""
        self._state.shotstopper = ss_state
        self._broadcast()

    def update_lamarzocco(self, lm_state: LaMarzoccoState) -> None:
        """Called by the La Marzocco adapter on state change."""
        self._state.lamarzocco = lm_state
        self._broadcast()

    # -- Pub/sub for WebSocket clients --

    def subscribe(self) -> asyncio.Queue[SystemState]:
        """Create a new subscriber queue. Returns a queue that receives SystemState on changes."""
        q: asyncio.Queue[SystemState] = asyncio.Queue(maxsize=16)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, queue: asyncio.Queue[SystemState]) -> None:
        """Remove a subscriber queue."""
        try:
            self._subscribers.remove(queue)
        except ValueError:
            pass

    def on_change(self, callback: StateCallback) -> None:
        """Register a synchronous callback for state changes."""
        self._callbacks.append(callback)

    def _broadcast(self) -> None:
        """Push current state to all subscribers and callbacks."""
        for q in self._subscribers:
            try:
                q.put_nowait(self._state)
            except asyncio.QueueFull:
                # Drop oldest, push newest (UI only cares about latest)
                try:
                    q.get_nowait()
                    q.put_nowait(self._state)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

        for cb in self._callbacks:
            try:
                cb(self._state)
            except Exception:
                logger.exception("State change callback error")

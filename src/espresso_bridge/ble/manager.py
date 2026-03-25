"""BLE device manager — coordinates adapter lifecycles.

Handles scanning, connecting, health monitoring, and reconnection for both
the ShotStopper and La Marzocco adapters. Also runs the schedule engine.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from enum import StrEnum

from espresso_bridge.ble.lamarzocco import LaMarzoccoAdapter
from espresso_bridge.ble.shotstopper import ShotStopperAdapter
from espresso_bridge.core.config import AppConfig
from espresso_bridge.core.models import ScheduleConfig
from espresso_bridge.core.state import StateStore

logger = logging.getLogger(__name__)


class ConnectionPhase(StrEnum):
    DISCONNECTED = "disconnected"
    SCANNING = "scanning"
    CONNECTING = "connecting"
    CONNECTED = "connected"


class DeviceManager:
    """Manages BLE connections for ShotStopper and La Marzocco."""

    def __init__(self, config: AppConfig, store: StateStore) -> None:
        self._config = config
        self._store = store
        self._scan_lock = asyncio.Lock()  # BLE adapter only supports one scan at a time

        self._shotstopper = ShotStopperAdapter(
            on_state_change=store.update_shotstopper,
        )
        self._lamarzocco: LaMarzoccoAdapter | None = None

        if config.lamarzocco.is_configured:
            self._lamarzocco = LaMarzoccoAdapter(
                username=config.lamarzocco.username,
                serial_number=config.lamarzocco.serial_number,
                communication_key=config.lamarzocco.communication_key,
                on_state_change=store.update_lamarzocco,
            )

        self._ss_phase = ConnectionPhase.DISCONNECTED
        self._lm_phase = ConnectionPhase.DISCONNECTED
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._last_fired: str | None = None  # schedule de-dup key

    @property
    def shotstopper(self) -> ShotStopperAdapter:
        return self._shotstopper

    @property
    def lamarzocco(self) -> LaMarzoccoAdapter | None:
        return self._lamarzocco

    @property
    def ss_phase(self) -> ConnectionPhase:
        return self._ss_phase

    @property
    def lm_phase(self) -> ConnectionPhase:
        return self._lm_phase

    # -- Lifecycle --

    async def start(self) -> None:
        """Start the device manager — begins connecting to all configured devices."""
        self._running = True
        logger.info("Device manager starting")

        self._tasks.append(asyncio.create_task(self._manage_shotstopper()))

        if self._lamarzocco:
            self._tasks.append(asyncio.create_task(self._manage_lamarzocco()))
        else:
            logger.info("La Marzocco not configured — skipping")

        # Start schedule engine
        self._tasks.append(asyncio.create_task(self._schedule_engine()))

    async def stop(self) -> None:
        """Stop the device manager and disconnect all devices."""
        self._running = False
        logger.info("Device manager stopping")

        for task in self._tasks:
            task.cancel()

        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        await self._shotstopper.disconnect()
        if self._lamarzocco:
            await self._lamarzocco.disconnect()

        logger.info("Device manager stopped")

    # -- ShotStopper management --

    async def _manage_shotstopper(self) -> None:
        """Connection loop for the ShotStopper."""
        cfg = self._config.shotstopper
        addr = cfg.address or None
        failures = 0

        while self._running:
            if self._shotstopper.connected:
                self._ss_phase = ConnectionPhase.CONNECTED
                failures = 0
                await asyncio.sleep(2.0)
                continue

            self._ss_phase = ConnectionPhase.SCANNING

            self._ss_phase = ConnectionPhase.CONNECTING
            async with self._scan_lock:
                success = await self._shotstopper.connect(address=addr)

            if success:
                self._ss_phase = ConnectionPhase.CONNECTED
                failures = 0
                logger.info("ShotStopper: connected")
            else:
                failures += 1
                self._ss_phase = ConnectionPhase.DISCONNECTED
                # Exponential backoff: 5s, 10s, 20s, 30s max
                delay = min(cfg.reconnect_interval * (2 ** (failures - 1)), 30.0)
                if failures <= 3 or failures % 10 == 0:
                    logger.warning(
                        f"ShotStopper: connect failed #{failures}, retry in {delay:.0f}s"
                    )
                await asyncio.sleep(delay)

    # -- La Marzocco management --

    async def _manage_lamarzocco(self) -> None:
        """Connection loop for the La Marzocco.

        Once connected, periodically refreshes state from the machine.
        Detects BLE disconnects and reconnects automatically.
        """
        if not self._lamarzocco:
            return

        cfg = self._config.lamarzocco
        addr = cfg.address or None
        interval = self._config.shotstopper.reconnect_interval
        failures = 0

        while self._running:
            # Check actual BLE connection (not model state)
            if self._lamarzocco.connected:
                self._lm_phase = ConnectionPhase.CONNECTED
                failures = 0
                # Refresh state every 10s (also validates connection is alive)
                try:
                    await self._lamarzocco.refresh_state()
                except Exception:
                    logger.warning("La Marzocco: BLE connection lost during refresh")
                    self._lm_phase = ConnectionPhase.DISCONNECTED
                await asyncio.sleep(10.0)
                continue

            self._lm_phase = ConnectionPhase.SCANNING

            self._lm_phase = ConnectionPhase.CONNECTING
            async with self._scan_lock:
                success = await self._lamarzocco.connect_silent(address=addr)

            if success:
                self._lm_phase = ConnectionPhase.CONNECTED
                failures = 0
                logger.info("La Marzocco: connected")
            else:
                failures += 1
                self._lm_phase = ConnectionPhase.DISCONNECTED
                delay = min(interval * (2 ** (failures - 1)), 30.0)
                if failures <= 3 or failures % 10 == 0:
                    logger.warning(
                        f"La Marzocco: connect failed #{failures}, retry in {delay:.0f}s"
                    )
                await asyncio.sleep(delay)

    # -- Schedule engine --

    def update_schedule(self, schedule: ScheduleConfig) -> None:
        """Update the schedule config (called from API)."""
        self._config.schedule = schedule
        self._last_fired = None  # reset so new schedule can take effect immediately
        logger.info(f"Schedule updated: enabled={schedule.enabled}")

    async def _schedule_engine(self) -> None:
        """Background loop: checks clock every 30s, fires set_power at scheduled times."""
        logger.info("Schedule engine started")

        while self._running:
            try:
                await self._check_schedule()
            except Exception:
                logger.exception("Schedule engine error")
            await asyncio.sleep(30)

    async def _check_schedule(self) -> None:
        """Evaluate the schedule and fire power on/off if needed."""
        sched = self._config.schedule
        if not sched.enabled:
            return

        if not self._lamarzocco:
            return

        now = datetime.now()
        today = sched.today_schedule(now)

        if not today.enabled:
            return

        current_minutes = now.hour * 60 + now.minute
        on_minutes = today.on_hour * 60 + today.on_minute
        off_minutes = today.off_hour * 60 + today.off_minute

        # Determine desired state
        # Handle normal case (on_time < off_time)
        if on_minutes < off_minutes:
            should_be_on = on_minutes <= current_minutes < off_minutes
        else:
            # Wraps midnight (e.g., on=22:00, off=06:00)
            should_be_on = current_minutes >= on_minutes or current_minutes < off_minutes

        # Build a fire key to avoid re-triggering same event within the same minute
        fire_key = f"{now.date().isoformat()}:{current_minutes}:{'on' if should_be_on else 'off'}"

        if fire_key == self._last_fired:
            return

        # Only fire at transition boundaries (within 1 minute of on/off time)
        at_on = abs(current_minutes - on_minutes) <= 1
        at_off = abs(current_minutes - off_minutes) <= 1

        if at_on and should_be_on:
            logger.info(f"Schedule: turning machine ON ({today.on_hour:02d}:{today.on_minute:02d})")
            ok = await self._lamarzocco.set_power(True)
            if ok and today.steam:
                await self._lamarzocco.set_steam_enabled(True)
            self._last_fired = fire_key
        elif at_off and not should_be_on:
            logger.info(
                f"Schedule: turning machine OFF ({today.off_hour:02d}:{today.off_minute:02d})"
            )
            await self._lamarzocco.set_power(False)
            self._last_fired = fire_key

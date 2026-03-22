"""BLE device manager — coordinates adapter lifecycles.

Handles scanning, connecting, health monitoring, and reconnection for both
the ShotStopper and La Marzocco adapters.
"""

from __future__ import annotations

import asyncio
import logging
from enum import StrEnum

from espresso_bridge.ble.lamarzocco import LaMarzoccoAdapter
from espresso_bridge.ble.shotstopper import ShotStopperAdapter
from espresso_bridge.core.config import AppConfig
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

        while self._running:
            if self._shotstopper.connected:
                self._ss_phase = ConnectionPhase.CONNECTED
                await asyncio.sleep(2.0)
                continue

            # Attempt connection (acquire scan lock to prevent BLE overlap)
            self._ss_phase = ConnectionPhase.SCANNING
            logger.info("ShotStopper: scanning...")

            self._ss_phase = ConnectionPhase.CONNECTING
            async with self._scan_lock:
                success = await self._shotstopper.connect(address=addr)

            if success:
                self._ss_phase = ConnectionPhase.CONNECTED
                logger.info("ShotStopper: connected")
            else:
                self._ss_phase = ConnectionPhase.DISCONNECTED
                logger.warning(
                    f"ShotStopper: connection failed, retrying in {cfg.reconnect_interval}s"
                )
                await asyncio.sleep(cfg.reconnect_interval)

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

        while self._running:
            # Check actual BLE connection (not model state)
            if self._lamarzocco.connected:
                self._lm_phase = ConnectionPhase.CONNECTED
                # Refresh state every 10s (also validates connection is alive)
                try:
                    await self._lamarzocco.refresh_state()
                except Exception:
                    logger.warning("La Marzocco: BLE connection lost during refresh")
                    self._lm_phase = ConnectionPhase.DISCONNECTED
                await asyncio.sleep(10.0)
                continue

            self._lm_phase = ConnectionPhase.SCANNING
            logger.info("La Marzocco: scanning...")

            self._lm_phase = ConnectionPhase.CONNECTING
            async with self._scan_lock:
                success = await self._lamarzocco.connect_silent(address=addr)

            if success:
                self._lm_phase = ConnectionPhase.CONNECTED
                logger.info("La Marzocco: connected")
            else:
                self._lm_phase = ConnectionPhase.DISCONNECTED
                logger.warning(f"La Marzocco: connection failed, retrying in {interval}s")
                await asyncio.sleep(interval)

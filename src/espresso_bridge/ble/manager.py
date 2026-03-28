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
from espresso_bridge.cloud.lamarzocco import LaMarzoccoCloudAdapter
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

    def __init__(
        self,
        config: AppConfig,
        store: StateStore,
        lm_cloud_username: str = "",
        lm_cloud_password: str = "",
    ) -> None:
        self._config = config
        self._store = store
        self._scan_lock = asyncio.Lock()  # BLE adapter only supports one scan at a time

        self._shotstopper = ShotStopperAdapter(
            on_state_change=store.update_shotstopper,
        )
        self._lamarzocco: LaMarzoccoAdapter | LaMarzoccoCloudAdapter | None = None
        self._ble_adapter: LaMarzoccoAdapter | None = None
        self._cloud_adapter: LaMarzoccoCloudAdapter | None = None
        self._using_cloud = False

        if config.lamarzocco.is_configured:
            self._ble_adapter = LaMarzoccoAdapter(
                username=config.lamarzocco.username,
                serial_number=config.lamarzocco.serial_number,
                communication_key=config.lamarzocco.communication_key,
                on_state_change=store.update_lamarzocco,
            )

        if lm_cloud_username and lm_cloud_password:
            self._cloud_adapter = LaMarzoccoCloudAdapter(
                username=lm_cloud_username,
                password=lm_cloud_password,
                on_state_change=store.update_lamarzocco,
            )

        # Cloud is primary when configured; BLE is fallback.
        # If only BLE is configured, use BLE as primary (original behavior).
        if self._cloud_adapter:
            self._lamarzocco = self._cloud_adapter
            self._using_cloud = True
        elif self._ble_adapter:
            self._lamarzocco = self._ble_adapter

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
        if self._ble_adapter:
            await self._ble_adapter.disconnect()
        if self._cloud_adapter:
            await self._cloud_adapter.disconnect()

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

            # After 5 consecutive failures, reset bluetooth adapter
            if failures > 0 and failures % 5 == 0:
                await self._reset_bluetooth_adapter()

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

        Priority: cloud API first (more reliable on Pi Zero 2 W), BLE as
        local fallback when internet is unavailable.

        - Try cloud first when credentials are configured
        - If cloud fails, fall back to BLE immediately
        - If cloud drops mid-session, try BLE while cloud reconnects
        - When cloud reconnects, switch back from BLE
        - If only BLE is configured, use BLE-only (original behavior)
        """
        if not self._lamarzocco:
            return

        cfg = self._config.lamarzocco
        addr = cfg.address or None
        interval = self._config.shotstopper.reconnect_interval
        failures = 0
        ble_fallback_threshold = 3

        while self._running:
            # --- Cloud connected path (primary) ---
            if self._cloud_adapter and self._cloud_adapter.connected:
                if not self._using_cloud:
                    # Cloud reconnected while we were on BLE — switch back
                    logger.info("La Marzocco: cloud reconnected, switching back from BLE")
                    if self._ble_adapter:
                        await self._ble_adapter.disconnect()
                    self._lamarzocco = self._cloud_adapter
                    self._using_cloud = True

                self._lm_phase = ConnectionPhase.CONNECTED
                failures = 0
                await asyncio.sleep(30.0)
                continue

            # --- BLE connected path (fallback) ---
            if self._ble_adapter and self._ble_adapter.connected:
                self._lm_phase = ConnectionPhase.CONNECTED
                failures = 0
                try:
                    await self._ble_adapter.refresh_state()
                except Exception:
                    logger.warning("La Marzocco: BLE connection lost during refresh")
                    self._lm_phase = ConnectionPhase.DISCONNECTED

                # While on BLE fallback, periodically try to reconnect cloud
                if self._cloud_adapter and not self._using_cloud:
                    cloud_ok = await self._cloud_adapter.connect()
                    if cloud_ok:
                        continue  # next iteration picks up cloud connected path

                await asyncio.sleep(10.0)
                continue

            # --- Disconnected: try cloud first, then BLE ---
            self._lm_phase = ConnectionPhase.CONNECTING

            # Try cloud (primary)
            if self._cloud_adapter:
                cloud_ok = await self._cloud_adapter.connect()
                if cloud_ok:
                    self._lamarzocco = self._cloud_adapter
                    self._using_cloud = True
                    self._lm_phase = ConnectionPhase.CONNECTED
                    failures = 0
                    logger.info("La Marzocco: cloud connected")
                    continue

            # Cloud failed — try BLE (fallback)
            if self._ble_adapter:
                if failures > 0 and failures % 5 == 0:
                    await self._reset_bluetooth_adapter()

                self._lm_phase = ConnectionPhase.SCANNING
                async with self._scan_lock:
                    ble_ok = await self._ble_adapter.connect_silent(address=addr)

                if ble_ok:
                    self._lamarzocco = self._ble_adapter
                    self._using_cloud = False
                    self._lm_phase = ConnectionPhase.CONNECTED
                    failures = 0
                    logger.info(
                        "La Marzocco: cloud failed, falling back to BLE"
                        if self._cloud_adapter else
                        "La Marzocco: connected via BLE"
                    )
                    continue

            failures += 1
            self._lm_phase = ConnectionPhase.DISCONNECTED
            delay = min(interval * (2 ** (failures - 1)), 30.0)
            if failures <= 3 or failures % 5 == 0:
                logger.warning(
                    f"La Marzocco: connect failed #{failures}, retry in {delay:.0f}s"
                )
            await asyncio.sleep(delay)

    # -- Bluetooth adapter reset --

    async def _reset_bluetooth_adapter(self) -> None:
        """Reset the bluetooth adapter to recover from stale bluez state.

        When bluez holds a stale connection, new connect attempts get
        BleakDeviceNotFoundError indefinitely. Power-cycling the adapter
        clears this. Falls back to restarting the bluetooth service.
        """
        logger.warning("Resetting bluetooth adapter to recover from stale state")
        try:
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl", "power", "off",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            await asyncio.sleep(2)

            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl", "power", "on",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            await asyncio.sleep(3)
            logger.info("Bluetooth adapter reset complete")
        except Exception:
            logger.warning("bluetoothctl reset failed, trying systemctl restart")
            try:
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "systemctl", "restart", "bluetooth",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
                await asyncio.sleep(3)
                logger.info("Bluetooth service restarted")
            except Exception:
                logger.exception("Failed to reset bluetooth")

    # -- Schedule engine --

    def update_schedule(self, schedule: ScheduleConfig) -> None:
        """Update the schedule config (called from API)."""
        self._config.schedule = schedule
        self._last_fired = None  # reset so new schedule can take effect immediately
        logger.info(f"Schedule updated: {len(schedule.schedules)} schedule(s)")

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
        sched_cfg = self._config.schedule
        if not self._lamarzocco:
            return

        now = datetime.now()
        today = now.date()
        sched = sched_cfg.resolve(today)

        if not sched:
            return

        current_minutes = now.hour * 60 + now.minute
        on_minutes = sched.wake_hour * 60 + sched.wake_minute
        off_minutes = sched.off_hour * 60 + sched.off_minute

        # Determine desired state
        if on_minutes < off_minutes:
            should_be_on = on_minutes <= current_minutes < off_minutes
        else:
            should_be_on = current_minutes >= on_minutes or current_minutes < off_minutes

        fire_key = f"{today.isoformat()}:{current_minutes}:{'on' if should_be_on else 'off'}"

        if fire_key == self._last_fired:
            return

        at_on = abs(current_minutes - on_minutes) <= 1
        at_off = abs(current_minutes - off_minutes) <= 1

        if at_on and should_be_on:
            logger.info(f"Schedule: turning machine ON ({sched.wake_hour:02d}:{sched.wake_minute:02d})")
            ok = await self._lamarzocco.set_power(True)
            if ok and sched.steam:
                await self._lamarzocco.set_steam_enabled(True)
            self._last_fired = fire_key
        elif at_off and not should_be_on:
            logger.info(f"Schedule: turning machine OFF ({sched.off_hour:02d}:{sched.off_minute:02d})")
            await self._lamarzocco.set_power(False)
            self._last_fired = fire_key

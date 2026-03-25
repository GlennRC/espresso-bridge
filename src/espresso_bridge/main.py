"""Main entry point for the espresso-bridge service.

Starts the device manager and web server in a single asyncio event loop.
Supports systemd watchdog via sd_notify (if available).
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket

import uvicorn

from espresso_bridge.api.server import create_app
from espresso_bridge.ble.manager import DeviceManager
from espresso_bridge.core.config import AppConfig
from espresso_bridge.core.state import StateStore

logger = logging.getLogger(__name__)


def _sd_notify(state: str) -> None:
    """Send sd_notify message to systemd (no-op if not under systemd)."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        if addr.startswith("@"):
            addr = "\0" + addr[1:]
        sock.sendto(state.encode(), addr)
        sock.close()
    except Exception:
        pass


async def _watchdog_loop() -> None:
    """Send WATCHDOG=1 to systemd every WatchdogSec/2 seconds."""
    usec = os.environ.get("WATCHDOG_USEC")
    if not usec:
        return
    interval = int(usec) / 1_000_000 / 2  # half the watchdog period
    logger.info(f"Watchdog heartbeat every {interval:.0f}s")
    while True:
        _sd_notify("WATCHDOG=1")
        await asyncio.sleep(interval)


def main(config_path: str | None = None) -> None:
    """Start the espresso-bridge service."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info("espresso-bridge starting")

    # Resolve config path: explicit > env var > default
    if config_path is None:
        config_path = os.environ.get("ESPRESSO_CONFIG", "config.yaml")

    config = AppConfig.load(config_path)
    logger.info(f"Config: {config_path}")
    logger.info(f"Server: {config.server.host}:{config.server.port}")
    logger.info(f"ShotStopper: {'address=' + config.shotstopper.address or 'auto-scan'}")
    if config.lamarzocco.is_configured:
        logger.info(f"La Marzocco: serial={config.lamarzocco.serial_number}")
    else:
        logger.info("La Marzocco: not configured")

    # Create components
    store = StateStore()
    manager = DeviceManager(config, store)
    app = create_app(manager, store, config=config, watchdog_coro=_watchdog_loop)

    # Notify systemd we're ready
    _sd_notify("READY=1")

    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()

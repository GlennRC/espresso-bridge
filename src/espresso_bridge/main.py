"""Main entry point for the espresso-bridge service.

Starts the device manager and web server in a single asyncio event loop.
"""

from __future__ import annotations

import logging

import uvicorn

from espresso_bridge.api.server import create_app
from espresso_bridge.ble.manager import DeviceManager
from espresso_bridge.core.config import AppConfig
from espresso_bridge.core.state import StateStore

logger = logging.getLogger(__name__)


def main(config_path: str = "config.yaml") -> None:
    """Start the espresso-bridge service."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info("espresso-bridge starting")

    # Load configuration
    config = AppConfig.load(config_path)
    logger.info(f"Server: {config.server.host}:{config.server.port}")
    logger.info(f"ShotStopper: {'address=' + config.shotstopper.address or 'auto-scan'}")
    if config.lamarzocco.is_configured:
        logger.info(f"La Marzocco: serial={config.lamarzocco.serial_number}")
    else:
        logger.info("La Marzocco: not configured")

    # Create components
    store = StateStore()
    manager = DeviceManager(config, store)
    app = create_app(manager, store)

    # Run uvicorn
    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()

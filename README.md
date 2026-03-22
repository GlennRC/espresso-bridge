# espresso-bridge ☕

Dedicated Raspberry Pi Zero 2 W controller that replaces vendor mobile apps for an espresso machine setup. Connects to a [ShotStopper](https://github.com/tatemazer/AcaiaArduinoBLE) brew-by-weight controller and La Marzocco Linea Micra via Bluetooth, exposing a touchscreen web UI for daily use.

## Hardware

| Device | Role | Connection |
|--------|------|------------|
| Raspberry Pi Zero 2 W | Controller | — |
| 5" HDMI Touchscreen (800×480) | User interface | HDMI + USB |
| ShotStopper (ESP32) | Brew-by-weight | BLE |
| Bookoo Mini Scale | Weight measurement | BLE (via ShotStopper) |
| La Marzocco Linea Micra | Espresso machine | BLE |

## Quick Start

```bash
# Clone
git clone git@github.com:GlennRC/espresso-bridge.git
cd espresso-bridge

# Install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Scan for ShotStopper
espresso-bridge scan

# Read status
espresso-bridge status

# Set target weight
espresso-bridge set-weight 36
```

## Development

```bash
# Run tests
pytest tests/ -v

# Lint
ruff check src/ tests/
```

## License

MIT

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
git clone https://github.com/GlennRC/espresso-bridge.git
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

# Start the web server
espresso-bridge serve
```

## Pi Deployment

### Flash SD Card

The SD card should be flashed with **RPi OS Lite 64-bit** (Trixie or Bookworm). The `scripts/setup-pi.sh` handles all software setup.

### First Boot Setup

```bash
# SSH into the Pi (after first boot + WiFi connect)
ssh glenn@expresso.local

# Run the one-time setup
curl -sL https://raw.githubusercontent.com/GlennRC/espresso-bridge/main/scripts/setup-pi.sh | bash
```

Or if the repo is already cloned:

```bash
cd /opt/espresso-bridge
bash scripts/setup-pi.sh
```

This installs all dependencies, configures Bluetooth, creates systemd services, and sets up the Chromium kiosk.

### Configuration

Edit `/etc/espresso-bridge/config.yaml` with your device addresses:

```bash
# Find ShotStopper BLE address
cd /opt/espresso-bridge && .venv/bin/espresso-bridge scan

# Find La Marzocco (see docs/lamarzocco-setup.md for token)
cd /opt/espresso-bridge && .venv/bin/espresso-bridge lm scan
```

### Services

```bash
# Start everything
sudo systemctl start espresso-bridge   # API + BLE
sudo systemctl start espresso-kiosk    # Touchscreen UI

# Check status
systemctl status espresso-bridge
journalctl -u espresso-bridge -f

# Deploy updates
cd /opt/espresso-bridge && scripts/deploy.sh
```

### Architecture

```
┌──────────────────────────────────────────────┐
│           Pi Zero 2 W (expresso)             │
│                                              │
│  systemd ──► espresso-bridge.service         │
│              ├─ BLE Manager (bleak)          │
│              │  ├─ ShotStopper adapter       │
│              │  └─ La Marzocco adapter       │
│              ├─ FastAPI + WebSocket (:8080)   │
│              └─ State store                   │
│                                              │
│  systemd ──► espresso-kiosk.service          │
│              └─ Chromium → localhost:8080     │
│              └─ 5" HDMI touchscreen (800×480) │
└──────────────────────────────────────────────┘
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

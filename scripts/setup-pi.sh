#!/usr/bin/env bash
# setup-pi.sh — One-time Pi Zero 2 W setup for espresso-bridge
# Run this after first boot via SSH: ssh glenn@expresso.local
set -euo pipefail

REPO_URL="https://github.com/GlennRC/espresso-bridge.git"
INSTALL_DIR="/opt/espresso-bridge"
SERVICE_USER="glenn"

echo "=== Espresso Bridge — Pi Setup ==="
echo "Hostname: $(hostname)"
echo "Arch: $(uname -m)"
echo ""

# --- System packages ---
echo "[1/7] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    git \
    python3 \
    python3-venv \
    python3-pip \
    bluez \
    bluetooth \
    cage \
    chromium \
    wlr-randr \
    libdbus-1-dev \
    libglib2.0-dev \
    2>/dev/null

# --- Bluetooth ---
echo "[2/7] Configuring Bluetooth..."
sudo systemctl enable bluetooth
sudo systemctl start bluetooth

# Allow the service user to use BLE without root
sudo usermod -aG bluetooth "$SERVICE_USER" 2>/dev/null || true

# Increase BLE connection limit (Pi Zero 2 W needs 2 concurrent)
if ! grep -q "MaxConnected" /etc/bluetooth/main.conf 2>/dev/null; then
    sudo sed -i '/^\[General\]/a MaxConnected = 4' /etc/bluetooth/main.conf
    sudo systemctl restart bluetooth
fi

# --- Clone repo ---
echo "[3/7] Setting up application..."
if [ -d "$INSTALL_DIR" ]; then
    echo "  Updating existing installation..."
    cd "$INSTALL_DIR"
    git pull --ff-only
else
    sudo git clone "$REPO_URL" "$INSTALL_DIR"
    sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# --- Python venv ---
echo "[4/7] Creating Python virtual environment..."
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -e ".[dev]" -q 2>&1 | tail -1

# --- Config ---
echo "[5/7] Setting up configuration..."
if [ ! -f /etc/espresso-bridge/config.yaml ]; then
    sudo mkdir -p /etc/espresso-bridge
    sudo cp config.example.yaml /etc/espresso-bridge/config.yaml
    sudo chown -R "$SERVICE_USER:$SERVICE_USER" /etc/espresso-bridge
    echo "  Created /etc/espresso-bridge/config.yaml — edit with your device addresses"
fi

# --- systemd service ---
echo "[6/7] Installing systemd service..."
sudo cp scripts/espresso-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable espresso-bridge.service
echo "  Service enabled (will start on boot)"

# --- Kiosk mode (cage + Chromium, portrait) ---
echo "[7/7] Configuring kiosk mode..."

# Install kiosk scripts
sudo cp scripts/kiosk.sh "$INSTALL_DIR/kiosk.sh"
sudo cp scripts/rotate.sh "$INSTALL_DIR/rotate.sh"
sudo chmod +x "$INSTALL_DIR/kiosk.sh" "$INSTALL_DIR/rotate.sh"

# Chromium policies (suppress low-RAM dialog, first-run, etc.)
sudo mkdir -p /etc/chromium/policies/managed
sudo tee /etc/chromium/policies/managed/kiosk.json > /dev/null << 'POLICY'
{
  "SuppressUnsupportedOSWarning": true,
  "BrowserSignin": 0,
  "DefaultBrowserSettingEnabled": false,
  "MetricsReportingEnabled": false,
  "PromotionalTabsEnabled": false,
  "TranslateEnabled": false,
  "BookmarkBarEnabled": false,
  "PasswordManagerEnabled": false
}
POLICY

# Touchscreen calibration for 90° portrait rotation (Waveshare 5" / QDtech MPI5001)
sudo tee /etc/udev/rules.d/99-touchscreen-rotation.rules > /dev/null << 'UDEV'
ATTRS{idVendor}=="0483", ATTRS{idProduct}=="5750", ENV{LIBINPUT_CALIBRATION_MATRIX}="0 -1 1 1 0 0"
UDEV
sudo udevadm control --reload-rules

# Auto-login on tty1
sudo mkdir -p /etc/systemd/system/getty@tty1.service.d
sudo tee /etc/systemd/system/getty@tty1.service.d/autologin.conf > /dev/null << 'AUTOLOGIN'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin glenn --noclear %I $TERM
AUTOLOGIN

# Launch kiosk from .bash_profile on tty1
KIOSK_LINE='# Auto-start kiosk on tty1'
PROFILE="/home/$SERVICE_USER/.bash_profile"
if ! grep -q "$KIOSK_LINE" "$PROFILE" 2>/dev/null; then
    cat >> "$PROFILE" << 'BASHPROFILE'

# Auto-start kiosk on tty1
if [ "$(tty)" = "/dev/tty1" ] && [ -z "$WAYLAND_DISPLAY" ]; then
    exec /opt/espresso-bridge/kiosk.sh
fi
BASHPROFILE
fi

# Disable console screen blanking
if ! grep -q "consoleblank=0" /boot/firmware/cmdline.txt 2>/dev/null; then
    sudo sed -i 's/$/ consoleblank=0/' /boot/firmware/cmdline.txt
fi

sudo systemctl daemon-reload

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit /etc/espresso-bridge/config.yaml with your device addresses"
echo "     - Run 'cd $INSTALL_DIR && .venv/bin/espresso scan' to find ShotStopper"
echo "     - Run 'cd $INSTALL_DIR && .venv/bin/espresso lm scan' to find LM Micra"
echo "  2. Start the service: sudo systemctl start espresso-bridge"
echo "  3. Reboot for kiosk:  sudo reboot"
echo ""
echo "Service logs: journalctl -u espresso-bridge -f"

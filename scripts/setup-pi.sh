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
    chromium-browser \
    xdotool \
    unclutter \
    xserver-xorg \
    xinit \
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

# --- Kiosk mode ---
echo "[7/7] Configuring kiosk mode..."
KIOSK_DIR="/home/$SERVICE_USER"

# Create .xinitrc for Chromium kiosk
cat > "$KIOSK_DIR/.xinitrc" << 'XINITRC'
#!/bin/sh
# Disable screen blanking and power management
xset s off
xset -dpms
xset s noblank

# Hide cursor after 3 seconds of inactivity
unclutter -idle 3 -root &

# Wait for espresso-bridge service to be ready
sleep 5

# Launch Chromium in kiosk mode
chromium-browser \
    --noerrdialogs \
    --disable-infobars \
    --kiosk \
    --incognito \
    --disable-translate \
    --disable-features=TranslateUI \
    --disable-pinch \
    --overscroll-history-navigation=0 \
    --disable-session-crashed-bubble \
    --disable-component-update \
    --check-for-update-interval=31536000 \
    --js-flags="--max-old-space-size=128" \
    --disable-gpu-compositing \
    --window-size=800,480 \
    --window-position=0,0 \
    http://localhost:8080
XINITRC

# Create systemd service for kiosk (auto-start X + Chromium)
sudo tee /etc/systemd/system/espresso-kiosk.service > /dev/null << KIOSK
[Unit]
Description=Espresso Bridge Kiosk
After=espresso-bridge.service
Wants=espresso-bridge.service

[Service]
Type=simple
User=$SERVICE_USER
Environment=DISPLAY=:0
ExecStartPre=/bin/sleep 3
ExecStart=/usr/bin/xinit /home/$SERVICE_USER/.xinitrc -- :0 -nocursor
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical.target
KIOSK

sudo systemctl daemon-reload
sudo systemctl enable espresso-kiosk.service

# --- Disable screen blanking at console level ---
if ! grep -q "consoleblank=0" /boot/firmware/cmdline.txt 2>/dev/null; then
    sudo sed -i 's/$/ consoleblank=0/' /boot/firmware/cmdline.txt
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit /etc/espresso-bridge/config.yaml with your device addresses"
echo "     - Run 'cd $INSTALL_DIR && .venv/bin/espresso scan' to find ShotStopper"
echo "     - Run 'cd $INSTALL_DIR && .venv/bin/espresso lm scan' to find LM Micra"
echo "  2. Start the service: sudo systemctl start espresso-bridge"
echo "  3. Start the kiosk:   sudo systemctl start espresso-kiosk"
echo "  4. Or just reboot:    sudo reboot"
echo ""
echo "Service logs: journalctl -u espresso-bridge -f"
echo "Kiosk logs:   journalctl -u espresso-kiosk -f"

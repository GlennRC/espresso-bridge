#!/usr/bin/env bash
# deploy.sh — Pull latest code and restart service
# Usage: ssh glenn@expresso.local 'cd /opt/espresso-bridge && scripts/deploy.sh'
set -euo pipefail

INSTALL_DIR="/opt/espresso-bridge"

cd "$INSTALL_DIR"

echo "=== Deploying espresso-bridge ==="

# Pull latest
echo "[1/3] Pulling latest..."
git pull --ff-only

# Update deps
echo "[2/3] Updating dependencies..."
source .venv/bin/activate
pip install -e ".[dev]" -q 2>&1 | tail -1

# Restart services
echo "[3/3] Restarting services..."
sudo systemctl restart espresso-bridge
sleep 2

if systemctl is-active --quiet espresso-bridge; then
    echo "✓ espresso-bridge is running"
else
    echo "✗ espresso-bridge failed to start"
    journalctl -u espresso-bridge --no-pager -n 20
    exit 1
fi

echo "=== Deploy complete ==="

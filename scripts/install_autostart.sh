#!/usr/bin/env bash
# ==============================================================================
# One-Command Auto-Start Installer for Bandhu Elder Care Companion
# Installs and enables bandhu-companion.service in systemd
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SERVICE_SRC="$PROJECT_ROOT/services/bandhu-companion.service"
SERVICE_DEST="/etc/systemd/system/bandhu-companion.service"

echo "========================================================="
echo "  Installing Bandhu Companion Auto-Start Service"
echo "========================================================="

# Ensure scripts are executable
chmod +x "$PROJECT_ROOT/scripts/start_companion.sh"

# Copy systemd unit file
echo "[1/4] Copying service configuration to /etc/systemd/system/..."
sudo cp "$SERVICE_SRC" "$SERVICE_DEST"

# Reload systemd manager configuration
echo "[2/4] Reloading systemd daemon..."
sudo systemctl daemon-reload

# Enable service to run automatically on boot
echo "[3/4] Enabling bandhu-companion.service on boot..."
sudo systemctl enable bandhu-companion.service

# Start or restart service immediately
echo "[4/4] Starting bandhu-companion.service now..."
sudo systemctl restart bandhu-companion.service

echo "========================================================="
echo "  Bandhu Companion is now set to run on startup!"
echo "  Check live status : sudo systemctl status bandhu-companion.service"
echo "  View live logs    : journalctl -u bandhu-companion.service -f"
echo "  Web Dashboard     : http://$(hostname -I | awk '{print $1}'):5000"
echo "========================================================="

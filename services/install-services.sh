#!/bin/bash
# ============================================================
# Bandhu Companion — Service Installer
# Installs and enables systemd services for auto-start on boot.
# Usage: sudo bash services/install-services.sh
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_DIR="/etc/systemd/system"

echo "============================================================"
echo "  Bandhu Companion — Installing systemd services"
echo "============================================================"
echo ""

# Check for root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root (use sudo)."
    exit 1
fi

# Copy service files
echo "[1/4] Copying service files to ${SERVICE_DIR}..."
cp "${SCRIPT_DIR}/bandhu-companion.service" "${SERVICE_DIR}/"
cp "${SCRIPT_DIR}/bandhu-zeroclaw.service" "${SERVICE_DIR}/"
echo "      ✓ bandhu-companion.service"
echo "      ✓ bandhu-zeroclaw.service"
echo ""

# Reload systemd
echo "[2/4] Reloading systemd daemon..."
systemctl daemon-reload
echo "      ✓ daemon-reload complete"
echo ""

# Enable services
echo "[3/4] Enabling services for boot..."
systemctl enable bandhu-zeroclaw.service
systemctl enable bandhu-companion.service
echo "      ✓ bandhu-zeroclaw.service enabled"
echo "      ✓ bandhu-companion.service enabled"
echo ""

# Start services
echo "[4/4] Starting services now..."
systemctl start bandhu-zeroclaw.service || echo "      ⚠ bandhu-zeroclaw failed to start (check ExecStart command)"
sleep 2
systemctl start bandhu-companion.service || echo "      ⚠ bandhu-companion failed to start"
echo ""

# Print status
echo "============================================================"
echo "  Service Status"
echo "============================================================"
echo ""
systemctl status bandhu-zeroclaw.service --no-pager -l 2>/dev/null || true
echo ""
systemctl status bandhu-companion.service --no-pager -l 2>/dev/null || true
echo ""
echo "============================================================"
echo "  Installation complete!"
echo ""
echo "  Useful commands:"
echo "    sudo systemctl status bandhu-companion"
echo "    sudo systemctl status bandhu-zeroclaw"
echo "    journalctl -u bandhu-companion -f"
echo "    journalctl -u bandhu-zeroclaw -f"
echo "============================================================"

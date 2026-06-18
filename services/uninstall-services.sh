#!/bin/bash
# ============================================================
# Bandhu Companion — Service Uninstaller
# Stops, disables, and removes systemd services.
# Usage: sudo bash services/uninstall-services.sh
# ============================================================

set -e

SERVICE_DIR="/etc/systemd/system"

echo "============================================================"
echo "  Bandhu Companion — Uninstalling systemd services"
echo "============================================================"
echo ""

# Check for root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root (use sudo)."
    exit 1
fi

# Stop services
echo "[1/4] Stopping services..."
systemctl stop bandhu-companion.service 2>/dev/null || true
systemctl stop bandhu-zeroclaw.service 2>/dev/null || true
echo "      ✓ Services stopped"
echo ""

# Disable services
echo "[2/4] Disabling services from boot..."
systemctl disable bandhu-companion.service 2>/dev/null || true
systemctl disable bandhu-zeroclaw.service 2>/dev/null || true
echo "      ✓ Services disabled"
echo ""

# Remove service files
echo "[3/4] Removing service files..."
rm -f "${SERVICE_DIR}/bandhu-companion.service"
rm -f "${SERVICE_DIR}/bandhu-zeroclaw.service"
echo "      ✓ Service files removed"
echo ""

# Reload systemd
echo "[4/4] Reloading systemd daemon..."
systemctl daemon-reload
echo "      ✓ daemon-reload complete"
echo ""

echo "============================================================"
echo "  Uninstallation complete!"
echo "  Services will no longer start on boot."
echo "============================================================"

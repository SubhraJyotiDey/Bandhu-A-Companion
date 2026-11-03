#!/usr/bin/env bash
# ==============================================================================
# Bandhu Elder Care Companion - Startup Script
# Executed automatically by systemd (bandhu-companion.service) on boot
# ==============================================================================

PROJECT_DIR="/home/pi/joyful-meitner"
cd "$PROJECT_DIR" || exit 1

# Export environment paths so local binaries (llama, llama-cli, espeak) are found
export PATH="/home/pi/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export HOME="/home/pi"

# Audio & PulseAudio environment for user pi (UID 1000)
export XDG_RUNTIME_DIR="/run/user/1000"
export PULSE_SERVER="unix:/run/user/1000/pulse/native"

# Determine Python runtime: prioritize venv if present, otherwise system python3
if [ -f "$PROJECT_DIR/venv/bin/python3" ]; then
    PYTHON_BIN="$PROJECT_DIR/venv/bin/python3"
elif [ -f "$PROJECT_DIR/venv/bin/python" ]; then
    PYTHON_BIN="$PROJECT_DIR/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
else
    PYTHON_BIN="python"
fi

echo "[Bandhu Startup] Launching Elder Care Companion using $PYTHON_BIN..."
echo "[Bandhu Startup] Working Directory: $PROJECT_DIR"

# Launch main daemon (Servo kinematics + Vision + Voice AI + Medicine Scheduler + Web UI)
exec "$PYTHON_BIN" main.py run

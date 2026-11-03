#!/usr/bin/env bash
# ==============================================================================
# Configure Bluetooth Earphones as Both Microphone and Speaker
# ==============================================================================

PROJECT_DIR="/home/pi/joyful-meitner"
cd "$PROJECT_DIR" || exit 1

# Read MAC from argument or config.json
MAC="${1:-$(python3 -c "import json; print(json.load(open('config.json')).get('voice', {}).get('bluetooth_speaker_mac', '41:42:9D:09:4D:D3'))" 2>/dev/null || echo "41:42:9D:09:4D:D3")}"
CARD="bluez_card.${MAC//:/_}"

echo "========================================================="
echo "  Configuring Bluetooth Earphones: $MAC"
echo "========================================================="

# 1. Connect via bluetoothctl
echo "[1/4] Checking Bluetooth connection..."
bluetoothctl connect "$MAC" || true
sleep 3

# 2. Switch to Headset / Handsfree profile (enables both mic and speaker)
echo "[2/4] Setting Bluetooth profile to Headset (Mic + Speaker)..."
pactl set-card-profile "$CARD" headset-head-unit 2>/dev/null || \
pactl set-card-profile "$CARD" handsfree_head_unit 2>/dev/null || \
pactl set-card-profile "$CARD" a2dp_sink 2>/dev/null || true

# 3. Find and set default sink (Speaker)
echo "[3/4] Binding default audio output (Speaker)..."
BT_SINK=$(pactl list short sinks | grep "${MAC//:/_}" | awk '{print $2}' | head -n 1)
if [ -n "$BT_SINK" ]; then
    pactl set-default-sink "$BT_SINK"
    echo "  -> Default Speaker set to: $BT_SINK"
else
    echo "  -> Note: Bluetooth sink not ready yet or using default PulseAudio sink."
fi

# 4. Find and set default source (Microphone)
echo "[4/4] Binding default audio input (Microphone)..."
BT_SOURCE=$(pactl list short sources | grep "${MAC//:/_}" | awk '{print $2}' | head -n 1)
if [ -n "$BT_SOURCE" ]; then
    pactl set-default-source "$BT_SOURCE"
    echo "  -> Default Microphone set to: $BT_SOURCE"
else
    echo "  -> Note: Bluetooth source not ready yet. Ensure headset profile is supported."
fi

echo "========================================================="
echo "  Testing Audio Output (Saying 'Audio Ready')..."
espeak-ng -ven "Bluetooth audio test. Audio is ready." 2>/dev/null || true
echo "  Done!"
echo "========================================================="

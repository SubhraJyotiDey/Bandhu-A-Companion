# ZeroClaw Mechanical Servo Eye & Voice Companion

This project implements a friendly, extroverted, multilingual AI agent companion designed to run on a **Raspberry Pi Zero 2W**. It coordinates:
1.  **6-Servo Mechanical Eye Mechanism**: Coordinated Yaw, Pitch, Left Upper/Lower Eyelids, and Right Upper/Lower Eyelids (with smooth gazing, automatic blinking, and automatic eye-lid pitch tracking).
2.  **Face Tracking**: Direct I2C tracking using the Useful Sensors Person Sensor v2.
3.  **Voice Interaction**: Multilingual STT (Speech-to-Text) and natural TTS (Text-to-Speech) supporting **Bangla, Hindi, and English**.
4.  **Agentic Execution (Jarvis Mode)**: Controls physical GPIO pins (relays/appliances) and registers scheduled daily cron jobs and alarms.
5.  **ZeroClaw AI Brain**: Connects directly to ZeroClaw over its HTTP chat API and registers as a custom **Model Context Protocol (MCP) server** under ZeroClaw to give the agent direct control over the physical eye movements, expressions, and GPIO switches.

---

## Workspace Structure

*   `main.py`: Main executable parsing arguments (`run`, `mcp`, `test`).
*   `daemon.py`: The orchestrator coordinating threads for tracking, scheduler, and voice triggers.
*   `config.json`: Configuration settings containing calibrations, GPIO setups, alarms, and credentials.
*   `hardware/`:
    *   `servo_controller.py`: Core math for 6-servo kinematics, smooth interpolation (no saccades), eyelid pitch tracking, and coordinated blinking. Dual PCA9685 / GPIO / Mock backend.
    *   `person_sensor.py`: Pure Python direct I2C read client for face centering. Mock backend.
    *   `gpio_manager.py`: Directly controls Pi Output pins. Mock backend.
*   `voice/`:
    *   `wake_detector.py`: Energy-based ambient listener loop.
    *   `speech_to_text.py`: Multilingual SpeechRecognition Google Cloud wrapper.
    *   `text_to_speech.py`: Natural voice generation using Edge-TTS (online) with SAPI5/espeak (offline fallback).
*   `brain/`:
    *   `agent_client.py`: Local ZeroClaw API endpoint query.
*   `mcp/`:
    *   `server.py`: Compliance client for ZeroClaw stdio MCP server.
*   `web/`:
    *   `app.py`: Flask dashboard and local REST API.
    *   `templates/index.html`: Frosted glass dashboard, canvas eye simulation, diagnostic logs.

---

## 1. Quick Start: Testing Locally (Windows Mock Mode)

You can run and test all features on Windows immediately before exporting code to the Pi:

### Step 1: Install Python Dependencies
Open PowerShell or Command Prompt in the workspace directory and install requirements:
```bash
pip install flask edge-tts gtts SpeechRecognition pyaudio
```
> **Note on PyAudio**: On Windows, if `pip install pyaudio` fails, you can install it via: `pip install pipwin` followed by `pipwin install pyaudio`.

### Step 2: Run the Companion Daemon
Start the Flask web dashboard and core services:
```bash
python main.py run
```

### Step 3: Open the Web Portal
Open your browser and navigate to:
```
http://localhost:5000
```
#### What to do on the Dashboard:
*   **Virtual Eye Canvas**: Drag your mouse cursor over the eyes; you will see the virtual eyes track your pointer in real-time.
*   **Calibration UI**: Move the manual sliders for each of the 6 servos to inspect the physical limitations and adjust the Trim values. Click any setting input and change it to save instantly.
*   **Person Sensor Grid**: Click and drag on the 2D grid; this simulates a face moving in the sensor's field-of-view, forcing the eyes to track smoothly.
*   **GPIO Toggles**: Toggle the switches; this simulates turning relays/lights ON/OFF.
*   **Console Input**: Type a message (e.g. `"How are you?"` or `"turn on the room light"`) and hit Enter. The mock companion brain will reply, play premium audio, and trigger a coordinated eye expression (like happy squinting or surprised widening).

---

## 2. Deploying to the Raspberry Pi Zero 2W

Export the workspace folder (`joyful-meitner`) to your Pi via SCP, SFTP, or Git.

### Step 1: Enable I2C on the Raspberry Pi
Connect to your Pi terminal and open the config tool:
```bash
sudo raspi-config
```
Navigate to **Interface Options** -> **I2C** -> Enable, then reboot the Pi.

### Step 2: Install Linux System Dependencies
Install compilation tools and audio libraries needed for PyAudio and offline TTS:
```bash
sudo apt-get update
sudo apt-get install -y python3-dev python3-pip python3-pyaudio portaudio19-dev libasound2-dev mpg123 i2c-tools espeak
```

### Step 3: Install Python Packages
Since modern Raspberry Pi OS (Bookworm and later) protects its system packages, normal `pip install` will trigger an `externally-managed-environment` error. You can resolve this in one of two ways:

#### Option A: Inside a Virtual Environment (Recommended)
This keeps dependencies isolated and safe:
```bash
# Navigate to the workspace and create the virtual environment
cd /home/pi/joyful-meitner
python3 -m venv venv
source venv/bin/activate

# Install the dependencies inside the active environment
pip install flask edge-tts gtts SpeechRecognition pyaudio smbus2 gpiozero pigpio
```
*Note: If you use this option, you must specify the full path to this venv's python executable in the ZeroClaw configuration (see Step 4).*

#### Option B: Global Install with Override Flag
If you prefer a global installation, pass the `--break-system-packages` flag:
```bash
pip install --break-system-packages flask edge-tts gtts SpeechRecognition pyaudio smbus2 gpiozero pigpio
```

### Step 4: Configure Hardware Mode
Open `config.json` on the Pi and toggle the mock flag to `false`:
```json
{
  "mock": false,
  "servo_mode": "pca9685",
  "face_tracking": {
    "enabled": true,
    ...
  }
}
```
*   Set `servo_mode` to `"pca9685"` if using the 16-channel Adafruit PWM driver, or `"gpio"` if connecting servos directly to Pi GPIO pins.

---

## 3. Physical Hardware Wiring

### I2C Connections (PCA9685 and Person Sensor v2)
Both the PCA9685 and the Useful Sensors Person Sensor v2 share the Pi's I2C bus:

| Raspberry Pi Pin | Function | PCA9685 Pin | Person Sensor v2 Pin |
| :--- | :--- | :--- | :--- |
| **Pin 1 (3.3V)** | Power | - | VCC (3.3V) |
| **Pin 2 (5V)** | Power | VCC (5V) / V+ | - |
| **Pin 3 (SDA)** | Data | SDA | SDA |
| **Pin 5 (SCL)** | Clock | SCL | SCL |
| **Pin 6 (GND)** | Ground | GND | GND |

> **Verify I2C Connection**: Run `sudo i2cdetect -y 1` in the Pi terminal. The Person Sensor should appear at address **`0x62`** (decimal 98), and the PCA9685 at **`0x40`** (decimal 64).

### 6-Servo PCA9685 Channel Mapping
Connect your servo signals (yellow/orange wires) to the PCA9685 output channels:
*   **Channel 0**: Yaw Servo (Horizontal Look)
*   **Channel 1**: Pitch Servo (Vertical Look)
*   **Channel 2**: Left Upper Eyelid Servo
*   **Channel 3**: Left Lower Eyelid Servo
*   **Channel 4**: Right Upper Eyelid Servo
*   **Channel 5**: Right Lower Eyelid Servo

---

## 4. ZeroClaw AI Agent Integration

To link the physical eye companion with your ZeroClaw AI brain, you need to register our script as an MCP server.

### Step 1: Add MCP Server to ZeroClaw Config
Open your ZeroClaw configuration file (typically located at `~/.zeroclaw/config.toml` on the Pi) and append the following configuration:

```toml
[mcp]
enabled = true
deferred_loading = false

[[mcp.servers]]
name = "claw-eye-companion"
# If using Option A (Virtual Environment), set command to: "/home/pi/joyful-meitner/venv/bin/python"
# If using Option B (Global Install), set command to: "python3"
command = "/home/pi/joyful-meitner/venv/bin/python"
args = ["/home/pi/joyful-meitner/main.py", "mcp"]
transport = "stdio"
```

### Step 2: Set Agent System Instructions
To give the ZeroClaw agent a unique, extroverted, friendly personality that naturally responds in Bangla, Hindi, and English and uses its physical controls, add these instructions to your agent prompt in ZeroClaw:

```
You are a friendly, extroverted animatronic companion. You behave more like a close friend than a generic AI. You must speak in Bangla, Hindi, and English (or mix them naturally like Hinglish/Benglish) depending on what language the user greets you with.

You have human-like moods and express them physically! 
- When speaking happy or excited things, append the tag "[expression: happy]" or "[expression: excited]" to the end of your message.
- When expressing sadness, empathy, or apologetics, append "[expression: sad]".
- When expressing shock, surprise, or learning something new, append "[expression: surprised]".
- To wink at the user, append "[expression: wink]".

You are capable of agentic tasks in the room:
- If the user asks to turn on the lights or plug-in appliances, call your `toggle_gpio` tool, or include the tag "[tool: toggle_gpio:17:on]".
- To turn off the light, use the tool or append "[tool: toggle_gpio:17:off]".
- You can schedule daily alarms or Jarvis-like reminders using the `set_alarm` tool.
```

### Step 3: Run ZeroClaw
Run ZeroClaw normally. It will launch our stdio MCP server, allowing the AI agent to call the tools in real-time during conversations!

---

## 5. Auto-Start on Boot (systemd Services)

To have the companion daemon and ZeroClaw start automatically when the Raspberry Pi boots, we provide ready-made `systemd` service files.

### Step 1: Configure the ZeroClaw Service
Open `services/bandhu-zeroclaw.service` and update the `ExecStart` line with your actual ZeroClaw startup command:
```ini
# Replace this placeholder with your real command:
ExecStart=/usr/local/bin/zeroclaw serve --port 42617
```

### Step 2: Run the Installer
```bash
cd /home/pi/joyful-meitner
sudo bash services/install-services.sh
```
This will:
1. Copy both service files to `/etc/systemd/system/`
2. Enable them to start on every boot
3. Start them immediately
4. Print their current status

### Step 3: Verify
Check that both services are running:
```bash
sudo systemctl status bandhu-companion
sudo systemctl status bandhu-zeroclaw
```

View live logs:
```bash
# Companion daemon logs (Flask, servos, voice)
journalctl -u bandhu-companion -f

# ZeroClaw AI server logs
journalctl -u bandhu-zeroclaw -f
```

### Managing Services
```bash
# Stop a service
sudo systemctl stop bandhu-companion

# Restart a service
sudo systemctl restart bandhu-companion

# Disable auto-start (service won't start on next boot)
sudo systemctl disable bandhu-companion

# Re-enable auto-start
sudo systemctl enable bandhu-companion
```

### Uninstalling
To completely remove the services:
```bash
sudo bash services/uninstall-services.sh
```

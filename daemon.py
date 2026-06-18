import os
import json
import time
import threading
import sys
from datetime import datetime

_original_print = print
def print(*args, **kwargs):
    try:
        _original_print(*args, **kwargs)
    except UnicodeEncodeError:
        try:
            file = kwargs.get("file", sys.stdout)
            enc = getattr(file, "encoding", None) or "utf-8"
            safe_args = [str(arg).encode(enc, errors="replace").decode(enc) for arg in args]
            new_kwargs = kwargs.copy()
            _original_print(*safe_args, **new_kwargs)
        except Exception:
            pass
    except Exception:
        pass

# Import components
from hardware.servo_controller import ServoController
from hardware.person_sensor import PersonSensor
from hardware.gpio_manager import GPIOManager
from voice.text_to_speech import TTSManager
from voice.speech_to_text import STTManager
from voice.wake_detector import WakeWordDetector
from brain.agent_client import ZeroClawClient
from brain.games import CompanionGames

class ThreadSafeDict(dict):
    """A dictionary subclass that recursively wraps all dict-like children
    and implements thread-safe reads and writes using a reentrant lock.
    """
    def __init__(self, *args, **kwargs):
        raw_dict = dict(*args, **kwargs)
        converted = {}
        for k, v in raw_dict.items():
            if isinstance(v, dict):
                converted[k] = ThreadSafeDict(v)
            else:
                converted[k] = v
        super().__init__(converted)
        self._lock = threading.RLock()

    def __getitem__(self, key):
        with self._lock:
            val = super().__getitem__(key)
            if isinstance(val, dict) and not isinstance(val, ThreadSafeDict):
                val = ThreadSafeDict(val)
                super().__setitem__(key, val)
            return val

    def __setitem__(self, key, value):
        with self._lock:
            if isinstance(value, dict) and not isinstance(value, ThreadSafeDict):
                value = ThreadSafeDict(value)
            super().__setitem__(key, value)

    def get(self, key, default=None):
        with self._lock:
            try:
                return self[key]
            except KeyError:
                return default

    def __contains__(self, key):
        with self._lock:
            return super().__contains__(key)

    def __len__(self):
        with self._lock:
            return super().__len__()

    def __repr__(self):
        with self._lock:
            return super().__repr__()

    def copy(self):
        with self._lock:
            return ThreadSafeDict(super().copy())

    def update(self, *args, **kwargs):
        with self._lock:
            super().update(*args, **kwargs)
            for k, v in list(self.items()):
                if isinstance(v, dict) and not isinstance(v, ThreadSafeDict):
                    self[k] = ThreadSafeDict(v)

    def pop(self, key, *args):
        with self._lock:
            return super().pop(key, *args)

    def keys(self):
        with self._lock:
            return list(super().keys())

    def values(self):
        with self._lock:
            return list(super().values())

    def items(self):
        with self._lock:
            return list(super().items())

    def to_dict(self):
        with self._lock:
            res = {}
            for k, v in super().items():
                if isinstance(v, ThreadSafeDict):
                    res[k] = v.to_dict()
                else:
                    res[k] = v
            return res


class ConfigManager:
    """Manages thread-safe read and writes to config.json."""
    def __init__(self, filepath):
        self.filepath = filepath
        self.lock = threading.Lock()
        self.config = ThreadSafeDict()
        self.load_config()

    def load_config(self):
        with self.lock:
            if os.path.exists(self.filepath):
                try:
                    with open(self.filepath, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                        self.config = ThreadSafeDict(raw)
                except Exception as e:
                    print(f"[Config Error] Failed to read config.json: {e}")
                    self.config = ThreadSafeDict()
            else:
                self.config = ThreadSafeDict()

    def save_config(self):
        with self.lock:
            try:
                raw_dict = self.config.to_dict()
                with open(self.filepath, "w", encoding="utf-8") as f:
                    json.dump(raw_dict, f, indent=2)
            except Exception as e:
                print(f"[Config Error] Failed to write config.json: {e}")


class IntercomManager:
    def __init__(self, daemon):
        self.daemon = daemon
        self.buffer = bytearray()
        self.lock = threading.Lock()
        self.is_recording = False
        self.thread = None
        
    def start(self):
        with self.lock:
            if self.is_recording:
                return
            self.is_recording = True
            self.buffer.clear()
            self.daemon.wake_detector.pause()
            self.thread = threading.Thread(target=self._record_loop, name="IntercomRecordLoop", daemon=True)
            self.thread.start()
            self.daemon.log("[Intercom] Intercom Mode activated (wake word suspended).")
            
    def stop(self):
        with self.lock:
            if not self.is_recording:
                return
            self.is_recording = False
            self.daemon.wake_detector.resume()
            self.daemon.log("[Intercom] Intercom Mode deactivated (wake word resumed).")
            
    def get_audio(self):
        with self.lock:
            data = bytes(self.buffer)
            self.buffer.clear()
            return data
            
    def _record_loop(self):
        try:
            import pyaudio
        except ImportError:
            self.daemon.log("[Intercom Warning] PyAudio is missing. Running in mock intercom mode.")
            while self.is_recording:
                time.sleep(0.1)
            return
            
        p = pyaudio.PyAudio()
        try:
            stream = p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                frames_per_buffer=1024
            )
        except Exception as e:
            self.daemon.log(f"[Intercom Error] Failed to open microphone: {e}")
            p.terminate()
            return
            
        self.daemon.log("[Intercom] Companion microphone streaming active.")
        while self.is_recording:
            try:
                data = stream.read(1024, exception_on_overflow=False)
                if data:
                    with self.lock:
                        self.buffer.extend(data)
                        if len(self.buffer) > 160000:
                            self.buffer = self.buffer[-160000:]
            except Exception:
                time.sleep(0.01)
                
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
        p.terminate()
        self.daemon.log("[Intercom] Companion microphone streaming stopped.")


class CompanionDaemon:
    def __init__(self):
        # Resolve config.json path
        base_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(base_dir, "config.json")
        
        self.config_manager = ConfigManager(config_path)
        
        # Logs list for the portal console
        self.logs = []
        self.log_lock = threading.Lock()
        self.log("[Daemon] Initializing Companion Daemon subsystems...")
        
        # Instantiate managers
        self.servos = ServoController(self.config_manager)
        self.sensor = PersonSensor(self.config_manager)
        self.gpio = GPIOManager(self.config_manager)
        self.tts = TTSManager(self.config_manager)
        self.stt = STTManager(self.config_manager)
        self.brain = ZeroClawClient(self.config_manager)
        self.brain.daemon = self
        from hardware.crt_engine import VectorCrtEngine
        self.crt_engine = VectorCrtEngine(self.config_manager)
        self.crt_engine.daemon = self
        self.stt.crt_engine = self.crt_engine
        self.tts.crt_engine = self.crt_engine
        self.games = CompanionGames(self.tts, self.servos)
        self.active_game = None
        
        # Wake word detector
        self.wake_detector = WakeWordDetector(self.config_manager, self.stt, self.on_wake_trigger, self.trigger_audio_reactive_snap)
        
        # Intercom manager
        self.intercom_manager = IntercomManager(self)
        
        # Sleep mode status tracking
        self.sleep_active = False
        
        # Volume reconnect/monitor cache
        self.sink_volumes = ThreadSafeDict()
        
        # Control flags
        self.is_running = False
        self.voice_listening_active = False
        self.interrupt_speech_flag = False
        self.voice_flow_lock = threading.Lock()
        self._audio_snap_active = False
        
        # Initialize TP1 interrupt pin from config (defaults to GPIO 24)
        self.tp1_pin = None
        self.tp1_pin_num = self.config_manager.config.get("face_tracking", {}).get("tp1_pin", 24)
        if not self.sensor.mock:
            try:
                from gpiozero import DigitalInputDevice
                self.tp1_pin = DigitalInputDevice(self.tp1_pin_num, pull_up=False)
                self.log(f"[Sensor] TP1 Interrupt Pin initialized on GPIO {self.tp1_pin_num}")
            except Exception as e:
                self.log(f"[Sensor Error] Failed to initialize TP1 Interrupt Pin: {e}")

        # Background loops handles
        self.tracking_thread = None
        self.scheduler_thread = None
        
        # Dynamic tracking state
        self.last_face_time = 0.0

    def is_time_between(self, t, start, end):
        try:
            th, tm = map(int, t.split(":"))
            sh, sm = map(int, start.split(":"))
            eh, em = map(int, end.split(":"))
            t_min = th * 60 + tm
            s_min = sh * 60 + sm
            e_min = eh * 60 + em
            if s_min <= e_min:
                return s_min <= t_min < e_min
            else: # Crosses midnight
                return t_min >= s_min or t_min < e_min
        except Exception:
            return False

    def log(self, message):
        """Append a log message to the memory buffer."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted = f"[{timestamp}] {message}"
        print(formatted)
        
        with self.log_lock:
            self.logs.append(formatted)
            # Cap logs size
            if len(self.logs) > 150:
                self.logs.pop(0)

    def start(self):
        """Starts all companion threads."""
        self.is_running = True
        
        # Start hardware controllers
        self.servos.start()
        self.sensor.start()
        
        # Start voice wake detector
        self.wake_detector.start()
        
        # Start Bluetooth speaker reconnect monitor thread if MAC is configured
        self.bt_mac = self.config_manager.config.get("voice", {}).get("bluetooth_speaker_mac")
        if self.bt_mac:
            import re
            if not re.match(r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$', self.bt_mac):
                self.log(f"[Bluetooth Error] Invalid MAC address format in config: {self.bt_mac}")
                self.bt_mac = None
        if self.bt_mac:
            self.bt_thread = threading.Thread(target=self._bluetooth_monitor_loop, name="BluetoothMonitorLoop")
            self.bt_thread.daemon = True
            self.bt_thread.start()
            
        # Start Volume reconnect/monitor loop in background
        self.volume_thread = threading.Thread(target=self._volume_monitor_loop, name="VolumeMonitorLoop")
        self.volume_thread.daemon = True
        self.volume_thread.start()
        
        # Start tracking and scheduler threads
        self.tracking_thread = threading.Thread(target=self._tracking_loop, name="FaceTrackingLoop")
        self.tracking_thread.daemon = True
        self.tracking_thread.start()
        
        self.scheduler_thread = threading.Thread(target=self._scheduler_loop, name="SchedulerLoop")
        self.scheduler_thread.daemon = True
        self.scheduler_thread.start()
        
        # Start CRT deflection engine
        if hasattr(self, "crt_engine") and self.crt_engine:
            self.crt_engine.start()
        
        # Check initial sleep mode state
        sleep_cfg = self.config_manager.config.get("sleep_mode", {})
        if sleep_cfg.get("enabled", False):
            current_time = datetime.now().strftime("%H:%M")
            if self.is_time_between(current_time, sleep_cfg.get("sleep_time", "22:00"), sleep_cfg.get("wake_time", "07:00")):
                with self.voice_flow_lock:
                    self.sleep_active = True
                self.wake_detector.pause()
                self.servos.close_eyes()
                self.log("[Sleep Mode] Daemon started during sleep hours. Entering sleep mode.")
        
        # Trigger Startup eye gesture and CRT startup animation
        with self.voice_flow_lock:
            is_asleep = self.sleep_active
        if not is_asleep:
            self.servos.play_gesture("startup")
            if hasattr(self, "crt_engine") and self.crt_engine:
                self.crt_engine.set_mode("startup")
        
        self.log("[Daemon] Companion Daemon successfully started in background.")

    def stop(self):
        """Stops all threads."""
        self.is_running = False
        
        # Stop subsystems
        self.servos.stop()
        self.sensor.stop()
        self.wake_detector.stop()
        if hasattr(self, "crt_engine") and self.crt_engine:
            self.crt_engine.stop()
        
        # Close TP1 pin
        if self.tp1_pin:
            try:
                self.tp1_pin.close()
            except Exception:
                pass
        
        self.log("[Daemon] Companion Daemon stopped.")

    def _tracking_loop(self):
        """Connects the Person Sensor reading to the Servo Yaw/Pitch targets."""
        self.log("[Tracking] Face tracking loop active.")
        
        last_tp1_state = False
        self._prev_tracking_mood = None
        self.last_group_index = -1
        self.last_face_count = 0
        
        while self.is_running:
            enabled = self.config_manager.config.get("face_tracking", {}).get("enabled", True)
            
            # Read TP1 hardware interrupt state or fallback to sensor's mock face detection
            if self.tp1_pin is not None:
                tp1_active = self.tp1_pin.is_active
            else:
                tp1_active = self.sensor.face_detected
            
            # Run tracking if enabled and not in manual override or active gesture
            if enabled and not self.servos.manual_override and not self.servos.gesture_active:
                
                # Check for rising edge (TP1 transition: False -> True)
                if tp1_active and not last_tp1_state:
                    self.log("[Tracking] TP1 Interrupt: Face acquired! Triggering Pixar-style snap.")
                    
                    # Pixar suppression: perform a quick blink to cover the eyeball saccadic jump
                    self.servos.trigger_blink()
                    
                    # Set eye expression to excited to widen the eyelids and set eyebal stiffness to underdamped spring bounce
                    self._prev_tracking_mood = self.servos.mood
                    self.servos.mood = "excited"
                    
                last_tp1_state = tp1_active
                
                if tp1_active:
                    face = self.sensor.get_primary_face()
                    
                    if face:
                        self.last_face_time = time.time()
                        
                        # Detect if a face was just acquired (transition from 0 faces to >= 1)
                        import random
                        if self.last_face_count == 0:
                            self.log(f"[Tracking] Face acquired! Count: {face['total_group']}. Welcoming...")
                            self.servos.trigger_double_blink()
                            self.servos.mood = "excited"
                            # Play a quick nod to welcome the user
                            self.servos.play_gesture("nod")
                            
                        # Detect group conversation attention shifts
                        elif face["group_index"] != self.last_group_index:
                            self.log(f"[Tracking] Group Attention: shifting focus to person {face['group_index'] + 1} of {face['total_group']}!")
                            self.servos.trigger_blink()
                            self.servos.mood = "excited"
                            # 30% chance of a friendly wink when acknowledging a group member
                            if random.random() < 0.3:
                                self.servos.trigger_wink(random.choice(["left", "right"]))
                        
                        self.last_group_index = face["group_index"]
                        self.last_face_count = face["total_group"]
                        
                        if not self.servos.face_tracking_active:
                            self.servos.face_tracking_active = True
                            self.log("[Tracking] Face acquired! Centering eyes.")
                        
                        # 1. Map Face X (0..255) to Yaw servo bounds
                        invert_x = self.config_manager.config.get("face_tracking", {}).get("invert_x", False)
                        x_pct = face["x"] / 255.0
                        if invert_x:
                            x_pct = 1.0 - x_pct
                            
                        yaw_cfg = self.config_manager.config.get("servos", {}).get("yaw", {})
                        y_min = yaw_cfg.get("min_angle", 50.0)
                        y_max = yaw_cfg.get("max_angle", 130.0)
                        target_yaw = y_min + x_pct * (y_max - y_min)
                        
                        # 2. Map Face Y (0..255) to Pitch servo bounds
                        invert_y = self.config_manager.config.get("face_tracking", {}).get("invert_y", False)
                        y_pct = face["y"] / 255.0
                        if invert_y:
                            y_pct = 1.0 - y_pct
                            
                        pitch_cfg = self.config_manager.config.get("servos", {}).get("pitch", {})
                        p_min = pitch_cfg.get("min_angle", 60.0)
                        p_max = pitch_cfg.get("max_angle", 120.0)
                        target_pitch = p_min + y_pct * (p_max - p_min)
                        
                        # Apply exponential moving average (EMA) to smooth out face tracking coordinate noise
                        if not hasattr(self, "_smooth_yaw"):
                            self._smooth_yaw = target_yaw
                            self._smooth_pitch = target_pitch
                        else:
                            # Snappy Pixar ease: react quickly to changes (alpha = 0.25)
                            alpha = 0.25
                            self._smooth_yaw = self._smooth_yaw + alpha * (target_yaw - self._smooth_yaw)
                            self._smooth_pitch = self._smooth_pitch + alpha * (target_pitch - self._smooth_pitch)
                        
                        # Update targets
                        self.servos.set_target("yaw", self._smooth_yaw)
                        self.servos.set_target("pitch", self._smooth_pitch)
                        
                else:
                    # Face lost logic (wait 2 seconds before reverting to autopilot)
                    if self.servos.face_tracking_active:
                        if time.time() - self.last_face_time > 2.0:
                            self.servos.face_tracking_active = False
                            self.log("[Tracking] Face lost. Reverting to auto look-around.")
                            self.last_group_index = -1
                            self.last_face_count = 0
                            # Restore original mood
                            if self._prev_tracking_mood:
                                self.servos.mood = self._prev_tracking_mood
                            else:
                                self.servos.mood = "neutral"
                            # Clean up EMA smooth state variables
                            if hasattr(self, "_smooth_yaw"):
                                delattr(self, "_smooth_yaw")
                            if hasattr(self, "_smooth_pitch"):
                                delattr(self, "_smooth_pitch")
            else:
                self.servos.face_tracking_active = False
                # Clean up EMA smooth state variables
                if hasattr(self, "_smooth_yaw"):
                    delattr(self, "_smooth_yaw")
                if hasattr(self, "_smooth_pitch"):
                    delattr(self, "_smooth_pitch")
                
            time.sleep(0.05) # 20Hz mapping loop

    def _scheduler_loop(self):
        """Checks and runs scheduled alarms and daily cron tasks."""
        self.log("[Scheduler] Alarm scheduler loop active.")
        last_minute = ""
        
        while self.is_running:
            now = datetime.now()
            current_time_str = now.strftime("%H:%M") # "08:00"
            current_day = now.strftime("%A")         # "Monday"
            
            # Check once per minute to avoid duplicate triggers
            if current_time_str != last_minute:
                # Sleep Mode Schedule logic
                sleep_cfg = self.config_manager.config.get("sleep_mode", {})
                if sleep_cfg.get("enabled", False):
                    sleep_time = sleep_cfg.get("sleep_time", "22:00")
                    wake_time = sleep_cfg.get("wake_time", "07:00")
                    
                    with self.voice_flow_lock:
                        if current_time_str == sleep_time and not self.sleep_active:
                            self.sleep_active = True
                            self.wake_detector.pause()
                            self.servos.close_eyes()
                            self.log("[Sleep Mode] Scheduled time reached. Sleep mode activated.")
                            
                        elif current_time_str == wake_time and self.sleep_active:
                            self.sleep_active = False
                            self.servos.open_eyes()
                            self.wake_detector.resume()
                            self.servos.play_gesture("startup")
                            self.log("[Sleep Mode] Scheduled time reached. Sleep mode deactivated.")

                alarms = self.config_manager.config.get("alarms", [])
                for alarm in alarms:
                    if alarm.get("enabled", False) and alarm.get("time") == current_time_str:
                        # Check days list
                        days = alarm.get("days", [])
                        if not days or current_day in days:
                            self.log(f"[Scheduler] Triggering alarm: {alarm.get('id')} - Task: {alarm.get('task')}")
                            # Execute the alarm task in a separate thread
                            threading.Thread(target=self.execute_task, args=(alarm.get("task"),), daemon=True).start()
                            
                            # If not recurring, disable it
                            if not alarm.get("recurring", False):
                                alarm["enabled"] = False
                                self.config_manager.save_config()
                                
                last_minute = current_time_str
                
            time.sleep(5.0)

    def _bluetooth_monitor_loop(self):
        """Monitors and automatically reconnects the Bluetooth speaker."""
        self.log(f"[Bluetooth] Background reconnect monitor started for MAC: {self.bt_mac}")
        import subprocess
        
        last_connected_state = False
        
        # Don't run on Windows mock platform
        if sys.platform.startswith("win"):
            self.log("[Bluetooth Mock] Windows platform detected. Auto-reconnect monitor will run in dry mode.")
            return

        while self.is_running:
            try:
                # 1. Check if connected
                cmd_info = f"bluetoothctl info {self.bt_mac}"
                res = subprocess.run(cmd_info, shell=True, capture_output=True, text=True, timeout=5.0)
                is_connected = "Connected: yes" in res.stdout
                
                if not is_connected:
                    if last_connected_state:
                        self.log("[Bluetooth] Speaker disconnected. Attempting automatic reconnection...")
                    
                    # 2. Attempt to connect
                    cmd_connect = f'echo "connect {self.bt_mac}" | bluetoothctl'
                    connect_res = subprocess.run(cmd_connect, shell=True, capture_output=True, text=True, timeout=10.0)
                    
                    # Verify if reconnection succeeded
                    res_verify = subprocess.run(cmd_info, shell=True, capture_output=True, text=True, timeout=5.0)
                    if "Connected: yes" in res_verify.stdout:
                        self.log(f"[Bluetooth] Automatically reconnected to speaker {self.bt_mac}!")
                        
                        # Trigger PulseAudio echo cancellation reload
                        self.log("[Bluetooth] Re-initializing PulseAudio Echo Cancellation modules...")
                        subprocess.run("pulseaudio -k", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        time.sleep(2.0)
                        
                        # Re-load module manually if it didn't boot automatically
                        usb_mic = "alsa_input.usb-GeneralPlus_USB_Audio_Device-00.mono-fallback"
                        bt_sink = f"bluez_sink.{self.bt_mac.replace(':', '_')}.a2dp_sink"
                        cmd_load = (
                            f"pactl load-module module-echo-cancel source_name=aec_source sink_name=aec_sink "
                            f"aec_method=webrtc channels=1 sink_master={bt_sink} source_master={usb_mic}"
                        )
                        subprocess.run(cmd_load, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        subprocess.run("pactl set-default-sink aec_sink", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        subprocess.run("pactl set-default-source aec_source", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        self.log("[Bluetooth] PulseAudio Echo Cancellation modules successfully re-loaded.")
                        
                        is_connected = True
                
                last_connected_state = is_connected
                
            except Exception as e:
                self.log(f"[Bluetooth Error] Reconnect check exception: {e}")
                
            time.sleep(10.0)

    def _volume_monitor_loop(self):
        """Monitors PulseAudio sink volumes in the background to avoid blocking Flask api calls."""
        self.log("[Volume] Background volume monitor started.")
        if sys.platform.startswith("win"):
            self.log("[Volume Mock] Windows platform detected. Volume monitor running in dry mode.")
            return

        import subprocess
        import re

        sinks_to_query = [
            "bluez_sink.41_42_9D_09_4D_D3.a2dp_sink",
            "alsa_output.usb-GeneralPlus_USB_Audio_Device-00.analog-stereo",
            "aec_sink"
        ]

        while self.is_running:
            for sink in sinks_to_query:
                try:
                    result = subprocess.run(
                        ["pactl", "get-sink-volume", sink],
                        capture_output=True, text=True, timeout=1.5
                    )
                    if result.returncode == 0:
                        match = re.search(r'(\d+)%', result.stdout)
                        if match:
                            self.sink_volumes[sink] = int(match.group(1))
                except Exception as e:
                    pass
            time.sleep(2.0)

    def execute_task(self, task_str):
        """Executes scheduled tasks (speech, GPIO toggling)."""
        if not task_str:
            return
            
        task_str = task_str.strip()
        if task_str.startswith("say:"):
            msg = task_str[4:].strip()
            # Play scanning gesture during alarm speech to alert the user
            self.servos.play_gesture("scanning")
            self.tts.speak(msg)
            time.sleep(3)
            self.servos.mood = "neutral"
            
        elif task_str.startswith("toggle_gpio:"):
            # Format: toggle_gpio:pin:state (e.g. toggle_gpio:17:on)
            parts = task_str.split(":")
            if len(parts) >= 3:
                pin = int(parts[1])
                state = parts[2].lower() == "on"
                self.gpio.set_pin_state(pin, state)
                self.servos.play_gesture("nod") # nod to confirm GPIO action
                self.log(f"[Scheduler] Executed GPIO task: Pin {pin} -> {state}")

    def on_wake_trigger(self):
        """Callback run by wake detector thread when trigger word is captured."""
        with self.voice_flow_lock:
            if self.voice_listening_active:
                if self.tts.is_speaking:
                    self.log("[Voice] Interruption detected! Silencing speech.")
                    self.tts.stop()
                    self.interrupt_speech_flag = True
                return # Avoid nested listens
                
            self.voice_listening_active = True
            threading.Thread(target=self._voice_interaction_flow, daemon=True).start()

    def _wait_for_tts_interrupt(self):
        """Blocks while TTS is speaking, returning True if interrupted, False otherwise."""
        self.interrupt_speech_flag = False
        time.sleep(0.05) # Give the TTS thread a brief moment to transition self.tts.is_speaking
        while self.tts.is_speaking:
            if getattr(self, "interrupt_speech_flag", False):
                self.log("[Voice] Interrupt flag detected. Silencing speech.")
                self.tts.stop()
                self.interrupt_speech_flag = False
                return True
            time.sleep(0.05)
        return False

    def _apply_agent_response_effects(self, agent_reply):
        """Helper to parse mood, expressions, and tools from the agent's reply."""
        import re
        mood_tag = "neutral"
        
        # Parse eye expressions/moods
        expr_match = re.search(r'\[expression:\s*(\w+)\]', agent_reply)
        if expr_match:
            mood_tag = expr_match.group(1).lower()
            
        # Parse GPIO tools calls
        tool_matches = re.findall(r'\[tool:\s*toggle_gpio:\s*(\d+):\s*(\w+)\]', agent_reply)
        for match in tool_matches:
            pin = int(match[0])
            state = match[1].lower() == "on"
            self.gpio.set_pin_state(pin, state)
            self.servos.play_gesture("nod") # nod to confirm GPIO action
            self.log(f"[Voice] Agent triggered tool: GPIO Pin {pin} set to {state}")
            
        # Apply mood expression / gesture changes
        if mood_tag == "wink":
            self.servos.trigger_wink()
        elif mood_tag == "blink":
            self.servos.trigger_blink()
        elif mood_tag in ["nod", "shake", "think", "shock", "scanning"]:
            self.servos.play_gesture(mood_tag)
            self.log(f"[Voice] Triggered gesture: {mood_tag}")
        elif mood_tag in ["happy", "sad", "angry", "surprised", "bored", "excited", "neutral"]:
            self.servos.mood = mood_tag
            self.log(f"[Voice] Eye expression set to: {mood_tag}")
            
        # Parse CRT vector drawing tags
        show_text_match = re.search(r'\[show_text:\s*([^\]]+)\]', agent_reply)
        if show_text_match:
            text = show_text_match.group(1).strip()
            if hasattr(self, "crt_engine") and self.crt_engine:
                self.crt_engine.draw_momentary_text(text)
                
        show_shape_match = re.search(r'\[show_shape:\s*(\w+)\]', agent_reply)
        if show_shape_match:
            shape = show_shape_match.group(1).strip()
            if hasattr(self, "crt_engine") and self.crt_engine:
                self.crt_engine.draw_momentary_shape(shape)

    def _voice_interaction_flow(self):
        """Orchestrates continuous conversation session (Gemini Live style)."""
        self.voice_listening_active = True
        self.log("[Voice] Conversation session started.")
        
        # 1. Wake alert visual response (Eyelids center, rapid double-blink)
        self.servos.set_target("yaw", 90)
        self.servos.set_target("pitch", 90)
        self.servos.trigger_double_blink()
        
        # Wait for blink to finish and establish "attentive" pose
        time.sleep(0.4)
        
        lang = self.config_manager.config.get("voice", {}).get("language", "en-US")
        greeting = "Yes, friend?" if "en" in lang else "নমস্কার বন্ধু, বলুন?" if "bn" in lang else "हाँ दोस्त?"
        self.tts.speak(greeting, lang)
        
        # Wait for greeting to finish, but allow interrupt!
        interrupted = self._wait_for_tts_interrupt()
        
        silence_count = 0
        
        while self.is_running and self.voice_listening_active:
            # Reload language dynamically to handle auto-language detection changes
            lang = self.config_manager.config.get("voice", {}).get("language", "en-US")
            
            if interrupted:
                self.log("[Voice] Interrupted! Resuming listening immediately.")
                interrupted = False
                
            # 2. Listen to user response
            self.log("[Voice] Listening to user...")
            if hasattr(self, "crt_engine") and self.crt_engine:
                self.crt_engine.set_mode("stt_waveform")
            
            # Temporarily pause wake-word detector to prevent mic conflicts on Raspberry Pi
            self.wake_detector.pause()
            time.sleep(0.2) # Allow ALSA/PulseAudio to release the mic device before STT starts
            try:
                user_speech = self.stt.listen_and_transcribe(timeout=6, phrase_time_limit=10, lang=lang)
            finally:
                self.wake_detector.resume()
                
            if not user_speech:
                silence_count += 1
                if silence_count >= 1: # End session after 1 consecutive silence timeout
                    self.log("[Voice] No speech detected. Ending conversation session.")
                    break
                continue
                
            if user_speech == "__TOO_NOISY__":
                noisy_warning = ("Friends, it's a bit too noisy here. Please speak one at a time!" if "en" in lang 
                                 else "বন্ধুরা, এখানে অনেক গোলমাল হচ্ছে। দয়া করে একটু শান্ত হয়ে একজন বলুন, আমি ঠিক শুনতে পাবো। [expression: sad]" if "bn" in lang 
                                 else "दोस्तों, यहाँ बहुत शोर है। कृपया एक-एक करके बोलें, मैं सुन रहा हूँ।")
                self.tts.speak(noisy_warning, lang)
                self._wait_for_tts_interrupt()
                continue

            # Reset silence count on active speech
            silence_count = 0
            
            # Clean and check for exit commands
            cleanup_speech = user_speech.lower().strip()
            exit_phrases = ["goodbye", "exit", "stop conversation", "bye bye", "bye", "বিদায়", "अलविदा", "खत्म करो"]
            if any(p in cleanup_speech for p in exit_phrases):
                parting = "Goodbye!" if "en" in lang else "আবার দেখা হবে!" if "bn" in lang else "फिर मिलेंगे!"
                self.tts.speak(parting, lang)
                self._wait_for_tts_interrupt()
                self.log("[Voice] User requested exit. Ending session.")
                break
                
            self.log(f"[Voice] User spoke: \"{user_speech}\"")
            if hasattr(self, "crt_engine") and self.crt_engine:
                self.crt_engine.set_mode("idle")
            self.servos.mood = "excited"
            
            # Start pre-emptive voice filler and gesture to mask LLM/TTS generation latency
            self.tts.play_filler(lang)
            self.servos.play_gesture("think")
            
            # Send message to ZeroClaw brain
            self.log("[Voice] Querying ZeroClaw agent...")
            agent_reply = self.brain.send_message(user_speech)
            self.log(f"[Voice] Agent reply: \"{agent_reply}\"")
            
            if not agent_reply:
                agent_reply = "I'm sorry, I couldn't reach my brain." if "en" in lang else "দুঃখিত, আমি বুঝতে পারিনি।" if "bn" in lang else "माफ़ कीजिये, আমি समझ नहीं पाया।"
                
            # Parse and apply tags from agent reply
            self._apply_agent_response_effects(agent_reply)
            
            if "[stop_game]" in agent_reply:
                self.games.stop_game(lang)
                with self.voice_flow_lock:
                    self.active_game = None
                self._wait_for_tts_interrupt()
                continue
            
            if "[trigger_game:" in agent_reply:
                import re
                match = re.search(r'\[trigger_game:\s*(\w+)\]', agent_reply)
                if match:
                    g_name = match.group(1)
                    with self.voice_flow_lock:
                        self.active_game = g_name
                    self.games.start_game(g_name, lang)
                    self._wait_for_tts_interrupt()
                    continue
            
            # 4. Speak reply back to user
            if hasattr(self, "crt_engine") and self.crt_engine:
                self.crt_engine.set_mode("tts_mouth")
            self.tts.speak(agent_reply, lang)
            
            # Wait for speaking to finish, allowing interrupt
            interrupted = self._wait_for_tts_interrupt()
            if hasattr(self, "crt_engine") and self.crt_engine:
                self.crt_engine.set_mode("idle")
            
        self.servos.mood = "neutral"
        with self.voice_flow_lock:
            self.voice_listening_active = False
        self.log("[Voice] Conversation session ended. Reverting to wake word detection.")

    def trigger_audio_reactive_snap(self, volume=None):
        """Snaps gaze to a random direction and flutters eyelids on loud noise."""
        if self.voice_listening_active or self.tts.is_speaking:
            return # Don't disrupt active speaking/listening session
            
        if self._audio_snap_active:
            return # Throttling overlapping audio snaps
            
        # Select random gaze coordinates within safe bounds
        yaw_cfg = self.config_manager.config.get("servos", {}).get("yaw", {})
        pitch_cfg = self.config_manager.config.get("servos", {}).get("pitch", {})
        
        # Safe ranges for a fast look-away: 60-120 yaw, 75-105 pitch
        y_min = max(60.0, yaw_cfg.get("min_angle", 50.0))
        y_max = min(120.0, yaw_cfg.get("max_angle", 130.0))
        p_min = max(75.0, pitch_cfg.get("min_angle", 60.0))
        p_max = min(105.0, pitch_cfg.get("max_angle", 120.0))
        
        import random
        target_yaw = random.uniform(y_min, y_max)
        target_pitch = random.uniform(p_min, p_max)
        
        def snap_run():
            self.log(f"[Audio-Reactive] Loud noise spike detected! Snapping eyes to ({round(target_yaw, 1)}, {round(target_pitch, 1)})")
            
            orig_mood = self.servos.mood
            
            # Snap gaze target
            self.servos.set_target("yaw", target_yaw)
            self.servos.set_target("pitch", target_pitch)
            
            # Set mood to surprised to trigger eyelid flutter
            self.servos.mood = "surprised"
            
            # Wait for the snap and look hold (1.8 seconds)
            time.sleep(1.8)
            
            # Restore mood
            self.servos.mood = "neutral" if orig_mood == "surprised" else orig_mood
            self._audio_snap_active = False
            
        self._audio_snap_active = True
        threading.Thread(target=snap_run, daemon=True).start()

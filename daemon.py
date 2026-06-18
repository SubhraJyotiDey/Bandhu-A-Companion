import os
import json
import time
import threading
import sys
from datetime import datetime

# Import components
from hardware.servo_controller import ServoController
from hardware.person_sensor import PersonSensor
from hardware.gpio_manager import GPIOManager
from voice.text_to_speech import TTSManager
from voice.speech_to_text import STTManager
from voice.wake_detector import WakeWordDetector
from brain.agent_client import ZeroClawClient

class ConfigManager:
    """Manages thread-safe read and writes to config.json."""
    def __init__(self, filepath):
        self.filepath = filepath
        self.lock = threading.Lock()
        self.config = {}
        self.load_config()

    def load_config(self):
        with self.lock:
            if os.path.exists(self.filepath):
                try:
                    with open(self.filepath, "r", encoding="utf-8") as f:
                        self.config = json.load(f)
                except Exception as e:
                    print(f"[Config Error] Failed to read config.json: {e}")
                    self.config = {}
            else:
                self.config = {}

    def save_config(self):
        with self.lock:
            try:
                with open(self.filepath, "w", encoding="utf-8") as f:
                    json.dump(self.config, f, indent=2)
            except Exception as e:
                print(f"[Config Error] Failed to write config.json: {e}")


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
        
        # Wake word detector
        self.wake_detector = WakeWordDetector(self.config_manager, self.stt, self.on_wake_trigger, self.trigger_audio_reactive_snap)
        
        # Control flags
        self.is_running = False
        self.voice_listening_active = False
        self.interrupt_speech_flag = False
        
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
            self.bt_thread = threading.Thread(target=self._bluetooth_monitor_loop, name="BluetoothMonitorLoop")
            self.bt_thread.daemon = True
            self.bt_thread.start()
        
        # Start tracking and scheduler threads
        self.tracking_thread = threading.Thread(target=self._tracking_loop, name="FaceTrackingLoop")
        self.tracking_thread.daemon = True
        self.tracking_thread.start()
        
        self.scheduler_thread = threading.Thread(target=self._scheduler_loop, name="SchedulerLoop")
        self.scheduler_thread.daemon = True
        self.scheduler_thread.start()
        
        # Trigger Startup eye gesture
        self.servos.play_gesture("startup")
        
        self.log("[Daemon] Companion Daemon successfully started in background.")

    def stop(self):
        """Stops all threads."""
        self.is_running = False
        
        # Stop subsystems
        self.servos.stop()
        self.sensor.stop()
        self.wake_detector.stop()
        
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
                            threading.Thread(target=self.servos.play_gesture, args=("nod",), daemon=True).start()
                            
                        # Detect group conversation attention shifts
                        elif face["group_index"] != self.last_group_index:
                            self.log(f"[Tracking] Group Attention: shifting focus to person {face['group_index'] + 1} of {face['total_group']}!")
                            self.servos.trigger_blink()
                            self.servos.mood = "excited"
                            # 30% chance of a friendly wink when acknowledging a group member
                            if random.random() < 0.3:
                                threading.Thread(target=self.servos.trigger_wink, args=(random.choice(["left", "right"]),), daemon=True).start()
                        
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
                pass
                
            time.sleep(10.0)

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
        if self.voice_listening_active:
            if self.tts.is_speaking:
                self.log("[Voice] Interruption detected! Silencing speech.")
                self.tts.stop()
                self.interrupt_speech_flag = True
            return # Avoid nested listens
            
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
        greeting = "Yes, friend?" if "en" in lang else "জি বন্ধু?" if "bn" in lang else "हाँ दोस्त?"
        self.tts.speak(greeting, lang)
        
        # Wait for greeting to finish, but allow interrupt!
        interrupted = self._wait_for_tts_interrupt()
        
        silence_count = 0
        
        while self.is_running and self.voice_listening_active:
            if interrupted:
                self.log("[Voice] Interrupted! Resuming listening immediately.")
                interrupted = False
                
            # 2. Listen to user response
            self.log("[Voice] Listening to user...")
            
            # Temporarily pause wake-word detector to prevent mic conflicts on Raspberry Pi
            self.wake_detector.pause()
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
            self.servos.mood = "excited"
            
            # Send message to ZeroClaw brain
            self.log("[Voice] Querying ZeroClaw agent...")
            agent_reply = self.brain.send_message(user_speech)
            self.log(f"[Voice] Agent reply: \"{agent_reply}\"")
            
            if not agent_reply:
                agent_reply = "I'm sorry, I couldn't reach my brain." if "en" in lang else "দুঃখিত, আমি বুঝতে পারিনি।" if "bn" in lang else "माफ़ कीजिये, আমি समझ नहीं पाया।"
                
            # Parse and apply tags from agent reply
            self._apply_agent_response_effects(agent_reply)
            
            # 4. Speak reply back to user
            self.tts.speak(agent_reply, lang)
            
            # Wait for speaking to finish, allowing interrupt
            interrupted = self._wait_for_tts_interrupt()
            
        self.servos.mood = "neutral"
        self.voice_listening_active = False
        self.log("[Voice] Conversation session ended. Reverting to wake word detection.")

    def trigger_audio_reactive_snap(self, volume=None):
        """Snaps gaze to a random direction and flutters eyelids on loud noise."""
        if self.voice_listening_active or self.tts.is_speaking:
            return # Don't disrupt active speaking/listening session
            
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
            
        threading.Thread(target=snap_run, daemon=True).start()

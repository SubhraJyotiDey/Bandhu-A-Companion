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
            return # Avoid nested listens
            
        threading.Thread(target=self._voice_interaction_flow, daemon=True).start()

    def _voice_interaction_flow(self):
        """Orchestrates Wakeup -> Visual Alert -> Speech Capture -> AI Query -> Actions -> TTS reply."""
        self.voice_listening_active = True
        self.log("[Voice] Wake word detected! Starting interaction...")
        
        # 1. Wake alert visual response (Eyelids center, rapid double-blink)
        self.servos.set_target("yaw", 90)
        self.servos.set_target("pitch", 90)
        self.servos.trigger_double_blink()
        
        # Wait for blink to finish and establish "attentive" pose
        time.sleep(0.4)
        
        # Speak a short welcoming chime or hello
        lang = self.config_manager.config.get("voice", {}).get("language", "en-US")
        greeting = "Yes, friend?" if "en" in lang else "জি বন্ধু?" if "bn" in lang else "हाँ दोस्त?"
        self.tts.speak(greeting, lang)
        
        # Give TTS time to finish speaking greeting
        time.sleep(1.2)
        
        # 2. Listen to user response
        user_speech = self.stt.listen_and_transcribe(timeout=6, phrase_time_limit=10, lang=lang)
        
        if user_speech:
            self.log(f"[Voice] User spoke: \"{user_speech}\"")
            self.servos.mood = "excited"
            
            # Send message to ZeroClaw brain
            self.log("[Voice] Querying ZeroClaw agent...")
            agent_reply = self.brain.send_message(user_speech)
            self.log(f"[Voice] Agent reply: \"{agent_reply}\"")
            
            # 3. Parse tags from agent reply
            # Examples: [expression: happy], [tool: toggle_gpio:17:on]
            mood_tag = "neutral"
            
            import re
            
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
                
            # 4. Speak reply back to user
            # TTS speak will run in its own thread, but we track time to return to neutral afterward
            self.tts.speak(agent_reply, lang)
            
            # Estimate reading duration (approx 150 words per minute)
            word_count = len(agent_reply.split())
            read_duration = max(3.0, (word_count / 150.0) * 60.0)
            
            # Wait during speaking, then transition eye mood back to neutral/auto look around
            time.sleep(read_duration)
            self.servos.mood = "neutral"
            
        else:
            self.log("[Voice] No speech detected or not understood.")
            self.servos.mood = "neutral"
            
        self.voice_listening_active = False

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

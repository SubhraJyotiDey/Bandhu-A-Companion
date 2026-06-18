import time
import math
import threading
import sys
import random

class ServoController:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.config = config_manager.config
        self.mock = self.config.get("mock", True)
        
        # Detect if we are on Windows and force mock if so
        if sys.platform.startswith("win"):
            self.mock = True
            
        self.servo_mode = self.config.get("servo_mode", "pca9685")
        
        # Servos dictionary configuration
        self.servo_cfgs = self.config.get("servos", {})
        
        # State variables (current and target angles for the 6 servos)
        # 6 Servos: yaw, pitch, left_upper_eyelid, left_lower_eyelid, right_upper_eyelid, right_lower_eyelid
        self.names = [
            "yaw", "pitch", 
            "left_upper_eyelid", "left_lower_eyelid", 
            "right_upper_eyelid", "right_lower_eyelid"
        ]
        
        # Current actual positions (in degrees, 0 to 180)
        self.current_pos = {name: self.servo_cfgs.get(name, {}).get("center_angle", 90.0) for name in self.names}
        
        # Target positions requested by AI, face tracking, or portal
        self.target_pos = {name: self.servo_cfgs.get(name, {}).get("center_angle", 90.0) for name in self.names}
        
        # Mood defaults
        self.mood = self.config.get("personality", {}).get("mood", "neutral")
        self._prev_mood = self.mood  # Track mood transitions for flutter effect
        self.extroversion = self.config.get("personality", {}).get("extroversion", 0.7)
        
        # Flags
        self.is_running = False
        self.blink_active = False
        self.blink_side = "both" # Can be "both", "left", or "right"
        self.blink_progress = 0.0 # 0.0 (open) to 1.0 (fully closed)
        self.manual_override = False # If true, manual controls from portal override everything
        self.face_tracking_active = False
        self.gesture_active = False
        self.eyes_closed = False
        
        # Eyelid flutter state (triggered on mood transitions)
        self._flutter_active = False
        self._flutter_progress = 0.0
        
        # Calibration sweep variables
        self.calibration_active = False
        self.calibration_angle = 90.0
        
        # Curiosity perk-up state
        self._curiosity_active = False
        self._curiosity_phase = 0  # 0=snap, 1=hold, 2=return
        self._curiosity_timer = 0.0
        self._curiosity_target_yaw = 90.0
        self._curiosity_target_pitch = 90.0
        
        # Hardware interfaces
        self.bus = None
        self.gpio_servos = {}
        
        # Speed/stiffness factors (k) for exponential ease-out
        # Higher k = faster response, lower k = smoother/slower response
        # Decreased for ultimate smoothness and quiet servo operation
        self.speed_k = {
            "yaw": 1.4,
            "pitch": 1.4,
            "left_upper_eyelid": 1.2,
            "left_lower_eyelid": 1.2,
            "right_upper_eyelid": 1.2,
            "right_lower_eyelid": 1.2
        }
        self.run_time = 0.0
        self.stationary_time = {name: 0.0 for name in self.names}
        self._detached_channels = set()
        self.last_target_pos = {name: self.current_pos[name] for name in self.names}
        self.velocities = {name: 0.0 for name in self.names}


        # Initialize hardware if not mocking
        if not self.mock:
            self._init_hardware()
            
        # Thread handles
        self.loop_thread = None
        
    def _init_hardware(self):
        print(f"[Servo] Initializing physical hardware in '{self.servo_mode}' mode...")
        try:
            if self.servo_mode == "pca9685":
                import smbus2
                bus_num = self.config.get("pca9685", {}).get("bus", 1)
                self.bus = smbus2.SMBus(bus_num)
                addr = self.config.get("pca9685", {}).get("address", 0x40)
                
                # PCA9685 initialization sequence
                # Set MODE1 register to 0x00 to wake it up
                self.bus.write_byte_data(addr, 0x00, 0x00)
                time.sleep(0.005)
                
                # Set frequency to 50Hz (prescale value ~ 121)
                # Formula: prescale = round(25000000 / (4096 * 50)) - 1 = 121
                self.bus.write_byte_data(addr, 0x00, 0x10) # Go to sleep to set prescale
                time.sleep(0.005)
                self.bus.write_byte_data(addr, 0xFE, 121) # Set prescale
                time.sleep(0.005)
                self.bus.write_byte_data(addr, 0x00, 0xa0) # Wake up and enable auto-increment
                time.sleep(0.005)
                print("[Servo] PCA9685 initialized successfully on I2C address 0x40.")
                
            elif self.servo_mode == "gpio":
                from gpiozero import AngularServo
                from gpiozero.pins.pigpio import PiGPIOFactory
                # Try to use pigpio for hardware-timed smooth pulses if available
                try:
                    factory = PiGPIOFactory()
                except Exception:
                    factory = None
                    print("[Servo] pigpio daemon not running. Falling back to default GPIO pin factory.")
                
                for name in self.names:
                    cfg = self.servo_cfgs.get(name, {})
                    pin = cfg.get("pin")
                    if pin is not None:
                        # 500us to 2500us pulse widths
                        self.gpio_servos[name] = AngularServo(
                            pin, 
                            min_angle=-90, 
                            max_angle=90, 
                            min_pulse_width=0.0005, 
                            max_pulse_width=0.0025,
                            pin_factory=factory
                        )
                print("[Servo] GPIO software PWM servos initialized.")
        except Exception as e:
            print(f"[Servo Error] Failed to initialize hardware: {e}. Switching to Mock Mode.")
            self.mock = True

    def _write_servo_angle(self, name, angle):
        """Applies calibration (trim, min/max limits, inversion) and writes to hardware/mock."""
        if hasattr(self, "_detached_channels") and name in self._detached_channels:
            self._detached_channels.discard(name)
            
        cfg = self.servo_cfgs.get(name, {})
        if not cfg:
            return
            
        # 1. Apply safety clamps to the logical angle before inversion
        if self.manual_override or getattr(self, "calibration_active", False):
            # Relax limits during active calibration / manual alignment to allow finding physical bounds
            min_lim = 15.0
            max_lim = 165.0
        else:
            min_lim = cfg.get("min_angle", 40.0)
            max_lim = cfg.get("max_angle", 140.0)
            
        angle_clamped = max(min_lim, min(max_lim, angle))
        
        # 2. Apply inversion
        angle_inverted = angle_clamped
        if cfg.get("inverted", False):
            center = cfg.get("center_angle", 90.0)
            angle_inverted = center - (angle_clamped - center)
            
        # 3. Apply trim offset to the final physical output angle
        angle_final = angle_inverted + cfg.get("trim", 0.0)
        
        # Ensure raw output angle is within safe servo boundaries
        angle_final = max(5.0, min(175.0, angle_final))
        
        if self.mock:
            # On mock mode, we do not log every tick to prevent terminal flooding
            return
            
        # Write to physical devices
        try:
            if self.servo_mode == "pca9685":
                addr = self.config.get("pca9685", {}).get("address", 0x40)
                channel = cfg.get("pin", 0)
                
                # Map angle (0 to 180) to pulse width (500us to 2500us)
                pulse_us = 500.0 + (angle_final / 180.0) * 2000.0
                
                # Convert pulse width to 12-bit steps (0 to 4095)
                # 20ms cycle = 20,000us. steps = (pulse_us / 20000) * 4096
                steps = int((pulse_us / 20000.0) * 4096.0)
                steps = max(0, min(4095, steps))
                
                # Filter redundant writes to eliminate continuous register updates (which glitched the PCA9685 PWM output phase)
                if not hasattr(self, "_last_steps"):
                    self._last_steps = {}
                if self._last_steps.get(name) == steps:
                    return # Exit early, no redundant I2C traffic
                self._last_steps[name] = steps
                
                # PCA9685 Channel register offset = 0x06 + 4 * channel
                reg = 0x06 + 4 * channel
                # LED_ON = 0 (turn on at step 0)
                self.bus.write_byte_data(addr, reg, 0x00)
                self.bus.write_byte_data(addr, reg + 1, 0x00)
                # LED_OFF = steps
                self.bus.write_byte_data(addr, reg + 2, steps & 0xFF)
                self.bus.write_byte_data(addr, reg + 3, (steps >> 8) & 0xFF)
                
            elif self.servo_mode == "gpio":
                servo = self.gpio_servos.get(name)
                if servo:
                    # Map 0..180 to -90..90
                    gpio_angle = angle_final - 90.0
                    
                    # Filter redundant software PWM updates
                    if not hasattr(self, "_last_angles"):
                        self._last_angles = {}
                    if name in self._last_angles and abs(self._last_angles[name] - gpio_angle) < 0.05:
                        return
                    self._last_angles[name] = gpio_angle
                    
                    servo.angle = gpio_angle
        except Exception as e:
            # Quietly catch hardware write issues
            pass

    def start(self):
        """Starts the servo update background loop thread."""
        self.is_running = True
        self.loop_thread = threading.Thread(target=self._control_loop, name="ServoControlLoop")
        self.loop_thread.daemon = True
        self.loop_thread.start()
        print("[Servo] Background control loop started.")

    def stop(self):
        """Stops the loop and cleans up."""
        self.is_running = False
        if self.loop_thread:
            self.loop_thread.join(timeout=1.0)
        
        # Release GPIO pins if in GPIO mode
        if not self.mock and self.servo_mode == "gpio":
            for name, servo in self.gpio_servos.items():
                try:
                    servo.close()
                except Exception:
                    pass
        print("[Servo] Background control loop stopped.")

    def set_target(self, name, angle):
        """Exposed method to set individual targets clamped to absolute servo limits."""
        if name in self.target_pos:
            self.target_pos[name] = max(0.0, min(180.0, float(angle)))

    # ------------------------------------------------------------------
    # BLINK & WINK SYSTEM
    # ------------------------------------------------------------------

    def trigger_blink(self):
        """Triggers a coordinated blink sequence."""
        if not self.blink_active:
            self.blink_active = True
            self.blink_side = "both"
            threading.Thread(target=self._blink_sequence_thread, daemon=True).start()

    def _blink_sequence_thread(self):
        """Coordinated blink timing using easing curves: closes lids in 100ms, holds 30ms, opens in 250ms."""
        # 1. Close eyelids (100ms) using quadratic ease-in
        close_duration = 0.10
        start_time = time.time()
        while time.time() - start_time < close_duration:
            elapsed = time.time() - start_time
            t = min(1.0, elapsed / close_duration)
            self.blink_progress = t * t
            time.sleep(0.008)
        self.blink_progress = 1.0
        
        # 2. Hold closed briefly (30ms)
        time.sleep(0.03)
        
        # 3. Open eyelids (250ms) using cubic ease-out
        open_duration = 0.25
        start_time = time.time()
        while time.time() - start_time < open_duration:
            elapsed = time.time() - start_time
            t = min(1.0, elapsed / open_duration)
            self.blink_progress = (1.0 - t) ** 3
            time.sleep(0.008)
            
        self.blink_progress = 0.0
        self.blink_active = False

    def trigger_double_blink(self):
        """Triggers two rapid blinks in sequence, ~250ms apart (common human pattern)."""
        if not self.blink_active:
            self.blink_active = True
            self.blink_side = "both"
            threading.Thread(target=self._double_blink_thread, daemon=True).start()

    def _double_blink_thread(self):
        """Two quick blinks with a brief pause between them."""
        for i in range(2):
            # Close (80ms)
            close_dur = 0.08
            start = time.time()
            while time.time() - start < close_dur:
                t = min(1.0, (time.time() - start) / close_dur)
                self.blink_progress = t * t
                time.sleep(0.008)
            self.blink_progress = 1.0
            time.sleep(0.02)
            
            # Open (200ms)
            open_dur = 0.20
            start = time.time()
            while time.time() - start < open_dur:
                t = min(1.0, (time.time() - start) / open_dur)
                self.blink_progress = (1.0 - t) ** 3
                time.sleep(0.008)
            self.blink_progress = 0.0
            
            # Pause between blinks (only after first)
            if i == 0:
                time.sleep(random.uniform(0.25, 0.40))
        
        self.blink_active = False

    def trigger_wink(self, side="left"):
        """Triggers a single-sided wink."""
        if not self.blink_active:
            self.blink_active = True
            self.blink_side = side
            threading.Thread(target=self._wink_sequence_thread, args=(side,), daemon=True).start()

    def _wink_sequence_thread(self, side):
        # 1. Close eyelid (100ms) using quadratic ease-in
        close_duration = 0.10
        start_time = time.time()
        while time.time() - start_time < close_duration:
            elapsed = time.time() - start_time
            t = min(1.0, elapsed / close_duration)
            self.blink_progress = t * t
            time.sleep(0.008)
        self.blink_progress = 1.0
        
        # 2. Hold closed briefly (40ms)
        time.sleep(0.04)
        
        # 3. Open eyelid (250ms) using cubic ease-out
        open_duration = 0.25
        start_time = time.time()
        while time.time() - start_time < open_duration:
            elapsed = time.time() - start_time
            t = min(1.0, elapsed / open_duration)
            self.blink_progress = (1.0 - t) ** 3
            time.sleep(0.008)
            
        self.blink_progress = 0.0
        self.blink_active = False

    def _trigger_eyelid_flutter(self):
        """Rapid 3-flutter sequence on mood transitions (3 partial blinks at ~40% closure)."""
        if not self._flutter_active and not self.blink_active:
            self._flutter_active = True
            threading.Thread(target=self._flutter_thread, daemon=True).start()

    def _flutter_thread(self):
        """Three quick partial eye narrows over ~400ms."""
        for i in range(3):
            # Partial close to 40% (40ms)
            close_dur = 0.04
            start = time.time()
            while time.time() - start < close_dur:
                t = min(1.0, (time.time() - start) / close_dur)
                self._flutter_progress = t * 0.4
                time.sleep(0.008)
            self._flutter_progress = 0.4
            
            # Open back (60ms)
            open_dur = 0.06
            start = time.time()
            while time.time() - start < open_dur:
                t = min(1.0, (time.time() - start) / open_dur)
                self._flutter_progress = 0.4 * (1.0 - t)
                time.sleep(0.008)
            self._flutter_progress = 0.0
            
            # Brief gap between flutters
            if i < 2:
                time.sleep(0.03)
        
        self._flutter_active = False

    def play_gesture(self, name):
        """Plays a predefined eye gesture in a separate thread if not already running a gesture."""
        if self.gesture_active:
            return False
        name = name.lower()
        if name in [
            "startup", "nod", "shake", "think", "shock", "scanning",
            "idle_roll_eyes", "idle_giggle", "idle_yawn", "idle_daydream",
            "idle_insect_chase", "idle_shy_look", "idle_curious_scan", "idle_close_eyes"
        ]:
            threading.Thread(target=self._gesture_thread, args=(name,), daemon=True).start()
            return True
        return False

    def _gesture_thread(self, name):
        self.gesture_active = True
        try:
            if name == "startup":
                # Start with eyes closed and looking down-center
                self.target_pos["left_upper_eyelid"] = 125.0
                self.target_pos["right_upper_eyelid"] = 125.0
                self.target_pos["left_lower_eyelid"] = 80.0
                self.target_pos["right_lower_eyelid"] = 80.0
                self.target_pos["yaw"] = 90.0
                self.target_pos["pitch"] = 115.0 # looking down
                # Snap current position to target closed state instantly
                for k in self.names:
                    self.current_pos[k] = self.target_pos[k]
                time.sleep(0.6)
                
                # 1. Blink open eyes, look left
                self.target_pos["left_upper_eyelid"] = 50.0  # open wide
                self.target_pos["right_upper_eyelid"] = 50.0
                self.target_pos["left_lower_eyelid"] = 130.0
                self.target_pos["right_lower_eyelid"] = 130.0
                self.target_pos["yaw"] = 55.0  # look far left
                self.target_pos["pitch"] = 90.0
                time.sleep(0.6)
                
                # 2. Blink
                self.target_pos["left_upper_eyelid"] = 125.0
                self.target_pos["right_upper_eyelid"] = 125.0
                self.target_pos["left_lower_eyelid"] = 80.0
                self.target_pos["right_lower_eyelid"] = 80.0
                time.sleep(0.12)
                
                # 3. Look right (open eyes looking right)
                self.target_pos["yaw"] = 125.0  # look far right
                self.target_pos["left_upper_eyelid"] = 50.0
                self.target_pos["right_upper_eyelid"] = 50.0
                self.target_pos["left_lower_eyelid"] = 130.0
                self.target_pos["right_lower_eyelid"] = 130.0
                time.sleep(0.6)
                
                # 4. Notoriously blink (rapid double blink / flutter)
                # First rapid close/open
                self.target_pos["left_upper_eyelid"] = 125.0
                self.target_pos["right_upper_eyelid"] = 125.0
                self.target_pos["left_lower_eyelid"] = 80.0
                self.target_pos["right_lower_eyelid"] = 80.0
                time.sleep(0.08)
                self.target_pos["left_upper_eyelid"] = 80.0
                self.target_pos["right_upper_eyelid"] = 80.0
                self.target_pos["left_lower_eyelid"] = 110.0
                self.target_pos["right_lower_eyelid"] = 110.0
                time.sleep(0.1)
                # Second rapid close (and gaze starts shifting front during this close)
                self.target_pos["left_upper_eyelid"] = 125.0
                self.target_pos["right_upper_eyelid"] = 125.0
                self.target_pos["left_lower_eyelid"] = 80.0
                self.target_pos["right_lower_eyelid"] = 80.0
                self.target_pos["yaw"] = 90.0
                self.target_pos["pitch"] = 90.0
                time.sleep(0.08)
                
                # 5. Look front
                self.target_pos["left_upper_eyelid"] = 50.0
                self.target_pos["right_upper_eyelid"] = 50.0
                self.target_pos["left_lower_eyelid"] = 130.0
                self.target_pos["right_lower_eyelid"] = 130.0
                time.sleep(0.6)
                
                # 6. Blink once
                self.target_pos["left_upper_eyelid"] = 125.0
                self.target_pos["right_upper_eyelid"] = 125.0
                self.target_pos["left_lower_eyelid"] = 80.0
                self.target_pos["right_lower_eyelid"] = 80.0
                time.sleep(0.12)
                self.target_pos["left_upper_eyelid"] = 60.0
                self.target_pos["right_upper_eyelid"] = 60.0
                self.target_pos["left_lower_eyelid"] = 120.0
                self.target_pos["right_lower_eyelid"] = 120.0
                time.sleep(0.5)
                
            elif name == "nod":
                orig_pitch = self.target_pos["pitch"]
                # Nod using wider range and coordinated eyelids (pitch-tracking)
                for _ in range(2):
                    # Look down: pitch 112, lids close slightly
                    self.target_pos["pitch"] = 112.0
                    self.target_pos["left_upper_eyelid"] = 95.0
                    self.target_pos["right_upper_eyelid"] = 95.0
                    self.target_pos["left_lower_eyelid"] = 110.0
                    self.target_pos["right_lower_eyelid"] = 110.0
                    time.sleep(0.22)
                    
                    # Look up: pitch 68, lids widen
                    self.target_pos["pitch"] = 68.0
                    self.target_pos["left_upper_eyelid"] = 48.0
                    self.target_pos["right_upper_eyelid"] = 48.0
                    self.target_pos["left_lower_eyelid"] = 125.0
                    self.target_pos["right_lower_eyelid"] = 125.0
                    time.sleep(0.22)
                    
                self.target_pos["pitch"] = orig_pitch
                self.target_pos["left_upper_eyelid"] = 60.0
                self.target_pos["right_upper_eyelid"] = 60.0
                self.target_pos["left_lower_eyelid"] = 120.0
                self.target_pos["right_lower_eyelid"] = 120.0
                time.sleep(0.2)
                
            elif name == "shake":
                orig_yaw = self.target_pos["yaw"]
                # Shake using wider range (55 to 125) and slightly narrowed eyes for a determined look
                for _ in range(2):
                    self.target_pos["yaw"] = 55.0  # look far left
                    self.target_pos["left_upper_eyelid"] = 75.0
                    self.target_pos["right_upper_eyelid"] = 75.0
                    self.target_pos["left_lower_eyelid"] = 110.0
                    self.target_pos["right_lower_eyelid"] = 110.0
                    time.sleep(0.22)
                    
                    self.target_pos["yaw"] = 125.0 # look far right
                    self.target_pos["left_upper_eyelid"] = 75.0
                    self.target_pos["right_upper_eyelid"] = 75.0
                    self.target_pos["left_lower_eyelid"] = 110.0
                    self.target_pos["right_lower_eyelid"] = 110.0
                    time.sleep(0.22)
                    
                self.target_pos["yaw"] = orig_yaw
                self.target_pos["left_upper_eyelid"] = 60.0
                self.target_pos["right_upper_eyelid"] = 60.0
                self.target_pos["left_lower_eyelid"] = 120.0
                self.target_pos["right_lower_eyelid"] = 120.0
                time.sleep(0.2)
                
            elif name == "think":
                # Look far up and left
                self.target_pos["yaw"] = 60.0
                self.target_pos["pitch"] = 65.0
                # Expressive asymmetrical squint
                self.target_pos["left_upper_eyelid"] = 92.0
                self.target_pos["left_lower_eyelid"] = 98.0
                self.target_pos["right_upper_eyelid"] = 68.0
                self.target_pos["right_lower_eyelid"] = 110.0
                time.sleep(2.0)
                
                # Settle back to center
                self.target_pos["yaw"] = 90.0
                self.target_pos["pitch"] = 90.0
                self.target_pos["left_upper_eyelid"] = 60.0
                self.target_pos["left_lower_eyelid"] = 120.0
                self.target_pos["right_upper_eyelid"] = 60.0
                self.target_pos["right_lower_eyelid"] = 120.0
                time.sleep(0.3)
                
            elif name == "shock":
                orig_yaw = self.target_pos["yaw"]
                orig_pitch = self.target_pos["pitch"]
                
                # Startled look: snap eyes wide open and look slightly up
                self.target_pos["left_upper_eyelid"] = 40.0
                self.target_pos["left_lower_eyelid"] = 140.0
                self.target_pos["right_upper_eyelid"] = 40.0
                self.target_pos["right_lower_eyelid"] = 140.0
                self.target_pos["pitch"] = 65.0
                time.sleep(0.1)
                
                # Quick trembling micro-shake
                for _ in range(4):
                    self.target_pos["yaw"] = orig_yaw - 10.0
                    time.sleep(0.08)
                    self.target_pos["yaw"] = orig_yaw + 10.0
                    time.sleep(0.08)
                
                self.target_pos["yaw"] = orig_yaw
                self.target_pos["pitch"] = orig_pitch
                time.sleep(0.8)
                
                # Slow blink to recover
                self.trigger_blink()
                time.sleep(0.4)
                
            elif name == "scanning":
                # Look far left to far right with wide, alert eyes
                self.target_pos["left_upper_eyelid"] = 48.0
                self.target_pos["left_lower_eyelid"] = 132.0
                self.target_pos["right_upper_eyelid"] = 48.0
                self.target_pos["right_lower_eyelid"] = 132.0
                
                # Coordinated 2D sine-wave scan trajectory
                scan_points = [
                    (50.0, 80.0),   # top-left
                    (90.0, 95.0),   # mid-low
                    (130.0, 80.0),  # top-right
                    (90.0, 100.0),  # low-center
                    (50.0, 90.0),   # mid-left
                    (130.0, 90.0),  # mid-right
                ]
                for y, p in scan_points:
                    self.target_pos["yaw"] = y
                    self.target_pos["pitch"] = p
                    time.sleep(0.35)
                
                self.target_pos["yaw"] = 90.0
                self.target_pos["pitch"] = 90.0
                self.target_pos["left_upper_eyelid"] = 60.0
                self.target_pos["right_upper_eyelid"] = 60.0
                self.target_pos["left_lower_eyelid"] = 120.0
                self.target_pos["right_lower_eyelid"] = 120.0
                time.sleep(0.3)

            elif name == "idle_roll_eyes":
                # Roll eyes in a smooth circle
                steps = 45
                r_yaw = 22.0
                r_pitch = 14.0
                for i in range(steps):
                    theta = (i / float(steps)) * 2.0 * math.pi
                    self.target_pos["yaw"] = 90.0 + r_yaw * math.sin(theta)
                    self.target_pos["pitch"] = 90.0 + r_pitch * math.cos(theta)
                    time.sleep(0.04)
                
                # Perform a sigh look at the end
                self.target_pos["yaw"] = 90.0
                self.target_pos["pitch"] = 112.0
                self.target_pos["left_upper_eyelid"] = 95.0
                self.target_pos["right_upper_eyelid"] = 95.0
                self.target_pos["left_lower_eyelid"] = 105.0
                self.target_pos["right_lower_eyelid"] = 105.0
                time.sleep(0.9)
                
                # Settle
                self.target_pos["pitch"] = 90.0
                self.target_pos["left_upper_eyelid"] = 60.0
                self.target_pos["right_upper_eyelid"] = 60.0
                self.target_pos["left_lower_eyelid"] = 120.0
                self.target_pos["right_lower_eyelid"] = 120.0
                time.sleep(0.3)

            elif name == "idle_giggle":
                # Happy squinty eyes
                self.target_pos["left_upper_eyelid"] = 80.0
                self.target_pos["right_upper_eyelid"] = 80.0
                self.target_pos["left_lower_eyelid"] = 100.0
                self.target_pos["right_lower_eyelid"] = 100.0
                
                # Eyeball rapid vibration (jitter)
                for _ in range(8):
                    self.target_pos["yaw"] = 90.0 + random.uniform(-4.0, 4.0)
                    self.target_pos["pitch"] = 90.0 + random.uniform(-2.5, 2.5)
                    time.sleep(0.08)
                    
                # Center eyeballs
                self.target_pos["yaw"] = 90.0
                self.target_pos["pitch"] = 90.0
                time.sleep(0.1)
                
                # Playful wink
                self.target_pos["left_upper_eyelid"] = 125.0
                self.target_pos["left_lower_eyelid"] = 80.0
                time.sleep(0.18)
                
                # Restore
                self.target_pos["left_upper_eyelid"] = 60.0
                self.target_pos["left_lower_eyelid"] = 120.0
                self.target_pos["right_upper_eyelid"] = 60.0
                self.target_pos["right_lower_eyelid"] = 120.0
                time.sleep(0.3)

            elif name == "idle_yawn":
                # Drift up-center
                self.target_pos["yaw"] = 90.0
                self.target_pos["pitch"] = 80.0
                
                # Droop lids slowly (yawning starts)
                self.target_pos["left_upper_eyelid"] = 110.0
                self.target_pos["right_upper_eyelid"] = 110.0
                self.target_pos["left_lower_eyelid"] = 90.0
                self.target_pos["right_lower_eyelid"] = 90.0
                time.sleep(1.3)
                
                # Wide open eyes (mouth open equivalent)
                self.target_pos["left_upper_eyelid"] = 40.0
                self.target_pos["right_upper_eyelid"] = 40.0
                self.target_pos["left_lower_eyelid"] = 140.0
                self.target_pos["right_lower_eyelid"] = 140.0
                time.sleep(1.0)
                
                # Slow blink/shut down
                self.target_pos["left_upper_eyelid"] = 125.0
                self.target_pos["right_upper_eyelid"] = 125.0
                self.target_pos["left_lower_eyelid"] = 80.0
                self.target_pos["right_lower_eyelid"] = 80.0
                time.sleep(0.4)
                
                # Restore
                self.target_pos["left_upper_eyelid"] = 60.0
                self.target_pos["right_upper_eyelid"] = 60.0
                self.target_pos["left_lower_eyelid"] = 120.0
                self.target_pos["right_lower_eyelid"] = 120.0
                self.target_pos["pitch"] = 90.0
                time.sleep(0.3)

            elif name == "idle_daydream":
                # Gaze down-left, sleepy lids
                self.target_pos["yaw"] = 72.0
                self.target_pos["pitch"] = 108.0
                self.target_pos["left_upper_eyelid"] = 98.0
                self.target_pos["right_upper_eyelid"] = 98.0
                self.target_pos["left_lower_eyelid"] = 102.0
                self.target_pos["right_lower_eyelid"] = 102.0
                time.sleep(3.2) # daydream hold
                
                # Sudden startle (shake awake)
                self.target_pos["yaw"] = 90.0
                self.target_pos["pitch"] = 85.0
                self.target_pos["left_upper_eyelid"] = 45.0
                self.target_pos["right_upper_eyelid"] = 45.0
                self.target_pos["left_lower_eyelid"] = 135.0
                self.target_pos["right_lower_eyelid"] = 135.0
                time.sleep(0.2)
                
                # Rapid double-blink
                self.target_pos["left_upper_eyelid"] = 125.0
                self.target_pos["right_upper_eyelid"] = 125.0
                self.target_pos["left_lower_eyelid"] = 80.0
                self.target_pos["right_lower_eyelid"] = 80.0
                time.sleep(0.08)
                self.target_pos["left_upper_eyelid"] = 60.0
                self.target_pos["right_upper_eyelid"] = 60.0
                self.target_pos["left_lower_eyelid"] = 120.0
                self.target_pos["right_lower_eyelid"] = 120.0
                time.sleep(0.12)
                self.target_pos["left_upper_eyelid"] = 125.0
                self.target_pos["right_upper_eyelid"] = 125.0
                self.target_pos["left_lower_eyelid"] = 80.0
                self.target_pos["right_lower_eyelid"] = 80.0
                time.sleep(0.08)
                
                # Settle
                self.target_pos["left_upper_eyelid"] = 60.0
                self.target_pos["right_upper_eyelid"] = 60.0
                self.target_pos["left_lower_eyelid"] = 120.0
                self.target_pos["right_lower_eyelid"] = 120.0
                self.target_pos["pitch"] = 90.0
                time.sleep(0.3)

            elif name == "idle_insect_chase":
                # Look Point A
                self.target_pos["yaw"] = 62.0
                self.target_pos["pitch"] = 82.0
                time.sleep(0.5)
                # Look Point B
                self.target_pos["yaw"] = 118.0
                self.target_pos["pitch"] = 98.0
                time.sleep(0.45)
                # Quick double blink
                self.target_pos["left_upper_eyelid"] = 125.0
                self.target_pos["right_upper_eyelid"] = 125.0
                self.target_pos["left_lower_eyelid"] = 80.0
                self.target_pos["right_lower_eyelid"] = 80.0
                time.sleep(0.1)
                self.target_pos["left_upper_eyelid"] = 60.0
                self.target_pos["right_upper_eyelid"] = 60.0
                self.target_pos["left_lower_eyelid"] = 120.0
                self.target_pos["right_lower_eyelid"] = 120.0
                time.sleep(0.1)
                # Trace Point C
                self.target_pos["yaw"] = 78.0
                self.target_pos["pitch"] = 112.0
                time.sleep(0.65)
                # Settle
                self.target_pos["yaw"] = 90.0
                self.target_pos["pitch"] = 90.0
                time.sleep(0.3)

            elif name == "idle_shy_look":
                # Look down and away
                self.target_pos["yaw"] = 118.0
                self.target_pos["pitch"] = 114.0
                self.target_pos["left_upper_eyelid"] = 85.0
                self.target_pos["right_upper_eyelid"] = 85.0
                self.target_pos["left_lower_eyelid"] = 105.0
                self.target_pos["right_lower_eyelid"] = 105.0
                time.sleep(1.6)
                
                # Look back shyly
                self.target_pos["yaw"] = 90.0
                self.target_pos["pitch"] = 95.0
                time.sleep(0.65)
                
                # Playful wink
                self.target_pos["left_upper_eyelid"] = 125.0
                self.target_pos["left_lower_eyelid"] = 80.0
                time.sleep(0.18)
                
                # Restore
                self.target_pos["left_upper_eyelid"] = 60.0
                self.target_pos["right_upper_eyelid"] = 60.0
                self.target_pos["left_lower_eyelid"] = 120.0
                self.target_pos["right_lower_eyelid"] = 120.0
                time.sleep(0.3)

            elif name == "idle_curious_scan":
                # Look far left with alert eyes
                self.target_pos["yaw"] = 55.0
                self.target_pos["pitch"] = 80.0
                self.target_pos["left_upper_eyelid"] = 48.0
                self.target_pos["right_upper_eyelid"] = 48.0
                self.target_pos["left_lower_eyelid"] = 132.0
                self.target_pos["right_lower_eyelid"] = 132.0
                time.sleep(0.6)
                
                # Scan to far right, looking up
                self.target_pos["yaw"] = 125.0
                self.target_pos["pitch"] = 70.0
                time.sleep(0.8)
                
                # Blink
                self.target_pos["left_upper_eyelid"] = 125.0
                self.target_pos["right_upper_eyelid"] = 125.0
                self.target_pos["left_lower_eyelid"] = 80.0
                self.target_pos["right_lower_eyelid"] = 80.0
                time.sleep(0.12)
                
                self.target_pos["left_upper_eyelid"] = 48.0
                self.target_pos["right_upper_eyelid"] = 48.0
                self.target_pos["left_lower_eyelid"] = 132.0
                self.target_pos["right_lower_eyelid"] = 132.0
                
                # Scan back to center-down
                self.target_pos["yaw"] = 90.0
                self.target_pos["pitch"] = 100.0
                time.sleep(0.6)
                
                # Settle
                self.target_pos["yaw"] = 90.0
                self.target_pos["pitch"] = 90.0
                self.target_pos["left_upper_eyelid"] = 60.0
                self.target_pos["right_upper_eyelid"] = 60.0
                self.target_pos["left_lower_eyelid"] = 120.0
                self.target_pos["right_lower_eyelid"] = 120.0
                time.sleep(0.3)

            elif name == "idle_close_eyes":
                # Close eyes completely
                self.target_pos["left_upper_eyelid"] = 125.0
                self.target_pos["right_upper_eyelid"] = 125.0
                self.target_pos["left_lower_eyelid"] = 80.0
                self.target_pos["right_lower_eyelid"] = 80.0
                # Wait closed between 1.2 and 2.5 seconds
                time.sleep(random.uniform(1.2, 2.5))
                
                # Restore to neutral baseline
                self.target_pos["left_upper_eyelid"] = 60.0
                self.target_pos["right_upper_eyelid"] = 60.0
                self.target_pos["left_lower_eyelid"] = 120.0
                self.target_pos["right_lower_eyelid"] = 120.0
                time.sleep(0.3)
        finally:
            self.gesture_active = False

    def start_calibration_sweep(self, name, direction):
        """Starts a slow calibration sweep for a specific servo to auto-detect physical limits."""
        self.calibration_active = False # Stop any active sweep first
        time.sleep(0.05) # Give it a moment to stop
        
        if name in self.names:
            self.manual_override = True
            self.calibration_active = True
            threading.Thread(target=self._calibration_sweep_thread, args=(name, direction), daemon=True).start()
            return True
        return False

    def _calibration_sweep_thread(self, name, direction):
        # Start at the current target angle or center
        self.calibration_angle = self.current_pos.get(name, 90.0)
        
        while self.calibration_active:
            if direction == "min":
                self.calibration_angle -= 1.0
            elif direction == "max":
                self.calibration_angle += 1.0
                
            # Clamp to safe physical limits to prevent extreme servo damage
            self.calibration_angle = max(10.0, min(170.0, self.calibration_angle))
            
            # Update targets directly, bypassing smoothing to give immediate feedback
            self.target_pos[name] = self.calibration_angle
            self.current_pos[name] = self.calibration_angle
            
            # Slow rate: 10 degrees per second
            time.sleep(0.1)

    def stop_calibration_sweep(self):
        """Stops the sweep and returns the final calibrated angle reached."""
        self.calibration_active = False
        time.sleep(0.05)
        self.manual_override = False
        return self.calibration_angle

    # ------------------------------------------------------------------
    # MOOD-DEPENDENT EYELID BASELINES
    # ------------------------------------------------------------------

    def _get_eyelid_baselines(self):
        """Returns the normal open baseline angles based on the current mood.
        Format: (left_upper, left_lower, right_upper, right_lower)
        Upper lid: lower angle = more open, higher angle = more closed
        Lower lid: higher angle = more open, lower angle = more closed
        """
        # Neutral positions (mostly kept into the bored mood extent)
        lu_open, ll_open = 92.0, 116.0
        ru_open, rl_open = 92.0, 116.0
        
        # Adjust baselines depending on mood
        if self.mood == "happy":
            # Smiling squinted eyes: slightly lowered upper lids, raised lower lids
            lu_open, ll_open = 75.0, 105.0
            ru_open, rl_open = 75.0, 105.0
        elif self.mood == "angry":
            # Narrowed, intense eyes: upper eyelids lower, lower lids raise
            lu_open, ll_open = 95.0, 95.0
            ru_open, rl_open = 95.0, 95.0
        elif self.mood == "sad" or self.mood == "bored":
            # Drooped upper lids, relaxed lower lids
            lu_open, ll_open = 100.0, 115.0
            ru_open, rl_open = 100.0, 115.0
        elif self.mood == "surprised":
            # Wide open eyes!
            lu_open, ll_open = 45.0, 135.0
            ru_open, rl_open = 45.0, 135.0
        elif self.mood == "excited":
            # Bright, alert, slightly wider than normal
            lu_open, ll_open = 50.0, 130.0
            ru_open, rl_open = 50.0, 130.0
            
        return lu_open, ll_open, ru_open, rl_open

    def close_eyes(self):
        """Closes the eyes fully and keeps them closed."""
        self.eyes_closed = True
        self.manual_override = False
        self.gesture_active = False
        self.set_target("yaw", 90.0)
        self.set_target("pitch", 90.0)
        print("[Servo] Eyes closed command received.")

    def open_eyes(self):
        """Opens the eyes and resumes normal gaze and blinks."""
        self.eyes_closed = False
        print("[Servo] Eyes opened command received.")

    def get_close_angle(self, name):
        """Returns the calibrated closed angle for the given eyelid, or hardcoded defaults."""
        cfg = self.servo_cfgs.get(name, {})
        default_map = {
            "left_upper_eyelid": 125.0,
            "right_upper_eyelid": 125.0,
            "left_lower_eyelid": 80.0,
            "right_lower_eyelid": 80.0
        }
        return cfg.get("close_angle", default_map.get(name, 90.0))

    def _get_blink_interval(self):
        """Returns a random blink interval that varies by mood (in seconds).
        Anxious/excited moods blink more frequently; calm/bored moods blink less.
        """
        if self.mood in ["excited", "surprised", "angry"]:
            return random.uniform(2.5, 5.0)
        elif self.mood in ["sad", "bored"]:
            return random.uniform(6.0, 14.0)
        else:  # neutral, happy
            return random.uniform(4.0, 8.0)

    # ------------------------------------------------------------------
    # MICRO-SACCADE GENERATOR
    # ------------------------------------------------------------------

    def _micro_saccade(self, t):
        """Disabled to prevent constant servo micro-adjustments and buzzing noises."""
        return 0.0, 0.0

    # ------------------------------------------------------------------
    # BREATHING RHYTHM (slow sinusoidal eyelid oscillation)
    # ------------------------------------------------------------------

    def _breathing_offset(self, t):
        """Disabled to prevent constant eyelid motor updates and buzzing noises."""
        return 0.0

    def _detach_servo(self, name):
        """Disables the PWM signal to the servo to eliminate buzzing/noises when stationary."""
        if self.mock:
            return
        try:
            cfg = self.servo_cfgs.get(name, {})
            if not cfg:
                return
                
            if self.servo_mode == "pca9685":
                addr = self.config.get("pca9685", {}).get("address", 0x40)
                channel = cfg.get("pin", 0)
                reg = 0x06 + 4 * channel
                
                # If we've already detached this channel, don't repeat I2C write
                if not hasattr(self, "_detached_channels"):
                    self._detached_channels = set()
                if name in self._detached_channels:
                    return
                self._detached_channels.add(name)
                if hasattr(self, "_last_steps") and name in self._last_steps:
                    del self._last_steps[name] # Clear cache so next write works
                    
                # Set full OFF bit (bit 4 of LED_OFF_H register = 0x10)
                self.bus.write_byte_data(addr, reg + 3, 0x10)
                
            elif self.servo_mode == "gpio":
                servo = self.gpio_servos.get(name)
                if servo:
                    if not hasattr(self, "_detached_channels"):
                        self._detached_channels = set()
                    if name in self._detached_channels:
                        return
                    self._detached_channels.add(name)
                    if hasattr(self, "_last_angles") and name in self._last_angles:
                        del self._last_angles[name]
                        
                    servo.value = None
        except Exception:
            pass

    def _solve_spring_damper(self, x, v, t, dt, w, zeta=1.0):
        """Analytically solves a second-order spring-damper system for a time step dt.
        Highly stable and smooth. Returns (new_position, new_velocity).
        """
        if w <= 0.0:
            return t, 0.0
            
        if zeta >= 1.0:
            # Critically damped (zeta == 1) or overdamped (zeta > 1)
            # We use critically damped for zeta >= 1 for maximum smoothness
            c = math.exp(-w * dt)
            new_x = t + (x - t + (v + w * (x - t)) * dt) * c
            new_v = (v - w * dt * (v + w * (x - t))) * c
            return new_x, new_v
        else:
            # Underdamped (zeta < 1) - provides a slight elastic bounce / overshoot
            wd = w * math.sqrt(1.0 - zeta * zeta)
            g = zeta * w
            c = math.exp(-g * dt)
            s = math.sin(wd * dt)
            co = math.cos(wd * dt)
            
            dx = x - t
            new_x = t + c * (dx * co + (v + g * dx) / wd * s)
            new_v = -g * (new_x - t) + c * (-dx * wd * s + (v + g * dx) * co)
            return new_x, new_v

    # ------------------------------------------------------------------
    # MAIN CONTROL LOOP
    # ------------------------------------------------------------------


    def _control_loop(self):
        """Continuous background thread that smooths servo movements and handles
        all autonomous behaviors: drift gaze, micro-saccades, blinks, mood
        expressions, curiosity, and breathing rhythm.
        """
        last_time = time.time()
        drift_timer = time.time()
        
        # Random automatic blinking timer (mood-dependent interval)
        next_blink_time = time.time() + self._get_blink_interval()
        
        # Drift target variables for smooth looking around
        drift_yaw = 90.0
        drift_pitch = 90.0
        drift_start_yaw = 90.0
        drift_start_pitch = 90.0
        drift_duration = 1.5
        drift_elapsed = 1.5
        drift_type = "small"  # "small", "medium", "large"
        
        # Curiosity perk-up timer (fires when no face is present for a while)
        next_curiosity_time = time.time() + random.uniform(15.0, 30.0)
        
        # Autonomous idle gestures timer
        next_idle_gesture_time = time.time() + random.uniform(10.0, 20.0)
        
        while self.is_running:
            now = time.time()
            dt = now - last_time
            last_time = now
            
            # Avoid division by zero or large steps
            if dt <= 0:
                dt = 0.01
            if dt > 0.1:
                dt = 0.1
                
            # Reload configs if updated
            self.servo_cfgs = self.config.get("servos", {})
            self.mock = self.config.get("mock", True)
            if sys.platform.startswith("win"):
                self.mock = True
            
            # Increment continuous run time for biological oscillations
            self.run_time += dt

            # ----------------------------------------------------------
            # MOOD TRANSITION DETECTION (trigger eyelid flutter)
            # ----------------------------------------------------------
            if self.mood != self._prev_mood:
                self._trigger_eyelid_flutter()
                self._prev_mood = self.mood

            # ----------------------------------------------------------
            # 1. AUTONOMOUS ATTENTION SYSTEM (weighted gaze shifts)
            # ----------------------------------------------------------
            if not self.manual_override and not self.face_tracking_active and not self.gesture_active:
                idle_enabled = self.config_manager.config.get("personality", {}).get("idle_behaviors", True)
                if idle_enabled:
                    # --- Curiosity perk-up behavior (periodic alert snap) ---
                    if now > next_curiosity_time and not self._curiosity_active:
                        self._curiosity_active = True
                        self._curiosity_phase = 0
                        self._curiosity_timer = now
                        # Pick a random "attention" direction
                        gaze_range = 25.0
                        self._curiosity_target_yaw = 90.0 + random.uniform(-gaze_range, gaze_range)
                        self._curiosity_target_pitch = 90.0 + random.uniform(-8.0, 5.0)
                        next_curiosity_time = now + random.uniform(18.0, 40.0)
                    
                    # --- Periodic autonomous idle gestures / self-play ---
                    if now > next_idle_gesture_time and not self.gesture_active and not self._curiosity_active:
                        idle_g = random.choice([
                            "idle_roll_eyes", "idle_giggle", "idle_yawn", 
                            "idle_daydream", "idle_insect_chase", "idle_shy_look", 
                            "idle_curious_scan", "idle_close_eyes"
                        ])
                        self.play_gesture(idle_g)
                        next_idle_gesture_time = now + random.uniform(12.0, 24.0)
                    
                    if self._curiosity_active:
                        elapsed_c = now - self._curiosity_timer
                        if self._curiosity_phase == 0:
                            # Phase 0: Slow curious look to curiosity target (slower transition)
                            self.target_pos["yaw"] = self._curiosity_target_yaw
                            self.target_pos["pitch"] = self._curiosity_target_pitch
                            # Smooth transition speed
                            self.speed_k["yaw"] = 2.0
                            self.speed_k["pitch"] = 2.0
                            if elapsed_c > 1.0:
                                self._curiosity_phase = 1
                                self._curiosity_timer = now
                                self.trigger_blink()
                        elif self._curiosity_phase == 1:
                            # Phase 1: Hold and "inspect" (1.0-1.5s)
                            # Restore normal speed
                            self.speed_k["yaw"] = 2.0
                            self.speed_k["pitch"] = 2.0
                            if elapsed_c > random.uniform(1.5, 2.5):
                                self._curiosity_phase = 2
                                self._curiosity_timer = now
                        elif self._curiosity_phase == 2:
                            # Phase 2: Slow drift back toward center
                            self.speed_k["yaw"] = 1.5
                            self.speed_k["pitch"] = 1.5
                            self.target_pos["yaw"] = 90.0 + random.uniform(-4.0, 4.0)
                            self.target_pos["pitch"] = 90.0
                            if elapsed_c > 1.8:
                                self._curiosity_active = False
                                self.speed_k["yaw"] = 2.0
                                self.speed_k["pitch"] = 2.0
                                # Reset drift timer so normal drift resumes
                                drift_timer = now + random.uniform(2.0, 4.0)
                                drift_elapsed = drift_duration  # Mark drift as complete
                    
                    elif now > drift_timer:
                        # --- Weighted attention shift selection ---
                        drift_start_yaw = self.target_pos["yaw"]
                        drift_start_pitch = self.target_pos["pitch"]
                        
                        roll = random.random()
                        
                        if roll < 0.08:
                            # 8% — Extreme Curiosity Look (Dart eyes to extreme corners to "explore")
                            drift_type = "extreme_curiosity"
                            yaw_cfg = self.servo_cfgs.get("yaw", {})
                            pitch_cfg = self.servo_cfgs.get("pitch", {})
                            
                            corner = random.choice(["left_up", "left_down", "right_up", "right_down", "far_left", "far_right", "far_up", "far_down"])
                            
                            if corner == "left_up":
                                drift_yaw = yaw_cfg.get("min_angle", 50.0) + random.uniform(0.0, 5.0)
                                drift_pitch = pitch_cfg.get("min_angle", 60.0) + random.uniform(0.0, 4.0)
                            elif corner == "left_down":
                                drift_yaw = yaw_cfg.get("min_angle", 50.0) + random.uniform(0.0, 5.0)
                                drift_pitch = pitch_cfg.get("max_angle", 120.0) - random.uniform(0.0, 4.0)
                            elif corner == "right_up":
                                drift_yaw = yaw_cfg.get("max_angle", 130.0) - random.uniform(0.0, 5.0)
                                drift_pitch = pitch_cfg.get("min_angle", 60.0) + random.uniform(0.0, 4.0)
                            elif corner == "right_down":
                                drift_yaw = yaw_cfg.get("max_angle", 130.0) - random.uniform(0.0, 5.0)
                                drift_pitch = pitch_cfg.get("max_angle", 120.0) - random.uniform(0.0, 4.0)
                            elif corner == "far_left":
                                drift_yaw = yaw_cfg.get("min_angle", 50.0)
                                drift_pitch = 90.0
                            elif corner == "far_right":
                                drift_yaw = yaw_cfg.get("max_angle", 130.0)
                                drift_pitch = 90.0
                            elif corner == "far_up":
                                drift_yaw = 90.0
                                drift_pitch = pitch_cfg.get("min_angle", 60.0)
                            else:  # far_down
                                drift_yaw = 90.0
                                drift_pitch = pitch_cfg.get("max_angle", 120.0)
                                
                            # Widen eyes in curiosity (excited mood baselines)
                            self.mood = "excited"
                            
                            # Sometimes play a quick wink or double blink in self-play
                            action_roll = random.random()
                            if action_roll < 0.25:
                                threading.Thread(target=self.trigger_wink, args=(random.choice(["left", "right"]),), daemon=True).start()
                            elif action_roll < 0.50:
                                threading.Thread(target=self.trigger_double_blink, daemon=True).start()
                                
                        elif roll < 0.15:
                            # 7% — Suspicious / Narrows eyes to "inspect" something quietly
                            drift_type = "suspicious_think"
                            drift_yaw = 90.0 + random.uniform(-16.0, 16.0)
                            drift_pitch = 90.0 + random.uniform(-4.0, 2.0)
                            self.mood = "angry" # Narrows eyelids (angry mood baselines)
                            
                            if random.random() < 0.40:
                                threading.Thread(target=self.play_gesture, args=("think",), daemon=True).start()
                                
                        elif roll < 0.65:
                            # 50% — Micro-glance: subtle look shifts nearby
                            drift_type = "small"
                            drift_yaw = drift_start_yaw + random.uniform(-4.0, 4.0)
                            drift_pitch = drift_start_pitch + random.uniform(-2.5, 2.5)
                            self.mood = "neutral"
                            
                        elif roll < 0.85:
                            # 20% — Medium shift: purposeful looking around
                            drift_type = "medium"
                            gaze_range = 10.0 * self.extroversion
                            drift_yaw = 90.0 + random.uniform(-gaze_range, gaze_range)
                            drift_pitch = 90.0 + random.uniform(-gaze_range / 2.5, gaze_range / 3.5)
                            self.mood = "neutral"
                            
                        else:
                            # 10% — Large attention shift
                            drift_type = "large"
                            gaze_range = 18.0 * self.extroversion
                            drift_yaw = 90.0 + random.uniform(-gaze_range, gaze_range)
                            drift_pitch = 90.0 + random.uniform(-6.0, 4.0)
                            self.mood = "neutral"
                        
                        # Clamp drift targets to safe ranges
                        yaw_cfg = self.servo_cfgs.get("yaw", {})
                        pitch_cfg = self.servo_cfgs.get("pitch", {})
                        drift_yaw = max(yaw_cfg.get("min_angle", 50), min(yaw_cfg.get("max_angle", 130), drift_yaw))
                        drift_pitch = max(pitch_cfg.get("min_angle", 60), min(pitch_cfg.get("max_angle", 120), drift_pitch))
                        
                        # Mood-specific gaze bias
                        if self.mood == "sad":
                            drift_pitch = min(drift_pitch + 8.0, pitch_cfg.get("max_angle", 120))  # Look down
                        elif self.mood == "bored":
                            drift_pitch = min(drift_pitch + 5.0, pitch_cfg.get("max_angle", 120))  # Droop
                        
                        # Snap target instantly
                        self.target_pos["yaw"] = drift_yaw
                        self.target_pos["pitch"] = drift_pitch
                        
                        # Extroversion scaling factor: higher extroversion -> shorter hold times (active)
                        hold_multiplier = 1.0 / max(0.2, self.extroversion)
                        
                        # Set next drift timer based on shift type and personality
                        if drift_type in ["large", "extreme_curiosity"]:
                            drift_interval = random.uniform(1.2, 2.5) * hold_multiplier
                            if drift_type == "large" and random.random() < 0.40:
                                self.trigger_blink()
                        elif drift_type in ["medium", "suspicious_think"]:
                            drift_interval = random.uniform(0.7, 1.5) * hold_multiplier
                            if random.random() < 0.20:
                                self.trigger_blink()
                        else:
                            drift_interval = random.uniform(0.3, 0.8) * hold_multiplier
                        
                        drift_timer = now + drift_interval
                else:
                    self.target_pos["yaw"] = 90.0
                    self.target_pos["pitch"] = 90.0

            # ----------------------------------------------------------
            # Create effective target (add micro-saccades when idle)
            # ----------------------------------------------------------
            effective_target = self.target_pos.copy()
            
            # Add micro-saccades when not actively tracking or in manual mode
            if not self.manual_override:
                saccade_yaw, saccade_pitch = self._micro_saccade(self.run_time)
                effective_target["yaw"] += saccade_yaw
                effective_target["pitch"] += saccade_pitch

            # ----------------------------------------------------------
            # 2. EYELID TRACKING, MOOD, BLINK & FLUTTER OVERRIDES
            # ----------------------------------------------------------
            if not self.manual_override and not self.gesture_active:
                # Trigger automatic random blink (with mood-variable interval)
                if now > next_blink_time and not getattr(self, "eyes_closed", False):
                    # 25% chance of double-blink (natural human pattern)
                    if random.random() < 0.25:
                        self.trigger_double_blink()
                    else:
                        self.trigger_blink()
                    next_blink_time = now + self._get_blink_interval()
                
                if getattr(self, "eyes_closed", False):
                    lu_target = self.get_close_angle("left_upper_eyelid")
                    ll_target = self.get_close_angle("left_lower_eyelid")
                    ru_target = self.get_close_angle("right_upper_eyelid")
                    rl_target = self.get_close_angle("right_lower_eyelid")
                else:
                    # Get normal baseline angles for this mood
                    lu_base, ll_base, ru_base, rl_base = self._get_eyelid_baselines()
                    
                    # Apply Pitch-tracking to eyelids (highly realistic animatronic effect!)
                    pitch_diff = self.current_pos["pitch"] - 90.0
                    gain_upper = 0.75
                    gain_lower = 0.35
                    
                    lu_target = lu_base + (pitch_diff * gain_upper)
                    ll_target = ll_base + (pitch_diff * gain_lower)
                    ru_target = ru_base + (pitch_diff * gain_upper)
                    rl_target = rl_base + (pitch_diff * gain_lower)
                    
                    # Add breathing rhythm to upper eyelids (slow sinusoidal oscillation)
                    breath = self._breathing_offset(self.run_time)
                    lu_target += breath
                    ru_target += breath
                    
                    # Apply eyelid flutter overrides (mood transitions)
                    if self._flutter_active:
                        flutter_close = self._flutter_progress
                        lu_target = lu_target + (115.0 - lu_target) * flutter_close
                        ll_target = ll_target + (85.0 - ll_target) * flutter_close
                        ru_target = ru_target + (115.0 - ru_target) * flutter_close
                        rl_target = rl_target + (85.0 - rl_target) * flutter_close
                    
                    # Apply blink/wink overrides (takes priority over flutter)
                    if self.blink_active:
                        if self.blink_side in ["both", "left"]:
                            lu_target = lu_target + (self.get_close_angle("left_upper_eyelid") - lu_target) * self.blink_progress
                            ll_target = ll_target + (self.get_close_angle("left_lower_eyelid") - ll_target) * self.blink_progress
                        if self.blink_side in ["both", "right"]:
                            ru_target = ru_target + (self.get_close_angle("right_upper_eyelid") - ru_target) * self.blink_progress
                            rl_target = rl_target + (self.get_close_angle("right_lower_eyelid") - rl_target) * self.blink_progress
                
                # Curiosity perk-up: widen eyes briefly during snap phase
                if self._curiosity_active and self._curiosity_phase == 0:
                    lu_target = min(lu_target, 48.0)
                    ru_target = min(ru_target, 48.0)
                    ll_target = max(ll_target, 132.0)
                    rl_target = max(rl_target, 132.0)
                
                effective_target["left_upper_eyelid"] = lu_target
                effective_target["left_lower_eyelid"] = ll_target
                effective_target["right_upper_eyelid"] = ru_target
                effective_target["right_lower_eyelid"] = rl_target
            else:
                # Apply blink and flutter overrides even in manual override mode
                lu_target = effective_target["left_upper_eyelid"]
                ll_target = effective_target["left_lower_eyelid"]
                ru_target = effective_target["right_upper_eyelid"]
                rl_target = effective_target["right_lower_eyelid"]
                
                if self._flutter_active:
                    flutter_close = self._flutter_progress
                    lu_target = lu_target + (115.0 - lu_target) * flutter_close
                    ll_target = ll_target + (85.0 - ll_target) * flutter_close
                    ru_target = ru_target + (115.0 - ru_target) * flutter_close
                    rl_target = rl_target + (85.0 - rl_target) * flutter_close
                
                if self.blink_active:
                    if self.blink_side in ["both", "left"]:
                        lu_target = lu_target + (self.get_close_angle("left_upper_eyelid") - lu_target) * self.blink_progress
                        ll_target = ll_target + (self.get_close_angle("left_lower_eyelid") - ll_target) * self.blink_progress
                    if self.blink_side in ["both", "right"]:
                        ru_target = ru_target + (self.get_close_angle("right_upper_eyelid") - ru_target) * self.blink_progress
                        rl_target = rl_target + (self.get_close_angle("right_lower_eyelid") - rl_target) * self.blink_progress
                        
                effective_target["left_upper_eyelid"] = lu_target
                effective_target["left_lower_eyelid"] = ll_target
                effective_target["right_upper_eyelid"] = ru_target
                effective_target["right_lower_eyelid"] = rl_target
            
            # Map closed-eye constant targets to calibrated close_angle when not in manual override
            if not self.manual_override:
                for name in ["left_upper_eyelid", "right_upper_eyelid"]:
                    if effective_target.get(name) == 125.0:
                        effective_target[name] = self.get_close_angle(name)
                for name in ["left_lower_eyelid", "right_lower_eyelid"]:
                    if effective_target.get(name) == 80.0:
                        effective_target[name] = self.get_close_angle(name)
            
            # ----------------------------------------------------------
            # 3. EXPONENTIAL EASE-OUT INTERPOLATION & WRITES
            # ----------------------------------------------------------
            for name in self.names:
                cfg = self.servo_cfgs.get(name, {})
                speed_limit = cfg.get("speed_limit", 150.0) # Max degrees per second
                
                target = effective_target[name]
                current = self.current_pos[name]
                
                # If target changed, reset stationary timer and wake up the servo
                if not hasattr(self, "last_target_pos"):
                    self.last_target_pos = {n: self.current_pos[n] for n in self.names}
                if name not in self.last_target_pos or abs(target - self.last_target_pos[name]) > 0.01:
                    self.stationary_time[name] = 0.0
                    self.last_target_pos[name] = target
                
                is_eyelid = name in ["left_upper_eyelid", "left_lower_eyelid", "right_upper_eyelid", "right_lower_eyelid"]
                
                # If a blink, wink, or flutter is active, eyelids bypass spring-damper for exact curve reproduction
                if (self.blink_active or self._flutter_active) and is_eyelid:
                    new_pos = target
                    self.velocities[name] = 0.0
                elif name == "pitch":
                    # Bypass spring-damper for pitch; use standard exponential ease-out (no spring/damper bounce)
                    k = self.speed_k.get(name, 1.4)
                    if self.mood == "sad" or self.mood == "bored":
                        k *= 0.6
                    elif self.mood == "excited" or self.mood == "surprised":
                        k *= 1.4
                    
                    factor = 1.0 - math.exp(-k * dt)
                    step = (target - current) * factor
                    
                    max_step = speed_limit * dt
                    if self.mood == "sad" or self.mood == "bored":
                        max_step *= 0.6
                    elif self.mood == "excited" or self.mood == "surprised":
                        max_step *= 1.3
                        
                    step_clamped = max(-max_step, min(max_step, step))
                    new_pos = current + step_clamped
                    self.velocities[name] = 0.0
                else:
                    # Determine omega (stiffness) and zeta (damping) based on channel and mood
                    if is_eyelid:
                        # Eyelids are slower and lag behind eyeballs (secondary motion)
                        if self.mood == "sad" or self.mood == "bored":
                            w = 3.5
                            zeta = 1.1 # slightly overdamped, heavy lids
                        elif self.mood == "excited" or self.mood == "surprised":
                            w = 8.0
                            zeta = 0.85 # slightly underdamped, springy/alert
                        else:
                            w = 6.0
                            zeta = 1.0 # critically damped
                    else:
                        # Eyeballs (yaw/pitch) are faster and snappier
                        if self.mood == "sad" or self.mood == "bored":
                            w = 4.5
                            zeta = 1.05 # slightly overdamped, heavy/sluggish
                        elif self.mood == "excited" or self.mood == "surprised":
                            w = 12.0
                            zeta = 0.80 # springy overshoot, very alert
                        else:
                            w = 9.5
                            zeta = 0.88 # slightly underdamped, premium organic bounce!
                            
                    # Solve spring damper
                    new_pos, new_vel = self._solve_spring_damper(current, self.velocities[name], target, dt, w, zeta)
                    self.velocities[name] = new_vel
                
                # Clamp to absolute servo limits (safety calibration limits are applied to final output in _write_servo_angle)
                new_pos = max(0.0, min(180.0, new_pos))
                
                # Snap to target if very close to prevent tiny float adjustments
                if abs(target - new_pos) < 0.15:
                    new_pos = target
                    self.velocities[name] = 0.0
                    
                self.current_pos[name] = new_pos

                
                # Update stationary time and write/detach accordingly (always keep active during blink/flutter)
                if (self.blink_active or self._flutter_active) and is_eyelid:
                    self.stationary_time[name] = 0.0
                elif abs(target - new_pos) < 0.15:
                    self.stationary_time[name] += dt
                else:
                    self.stationary_time[name] = 0.0
                    
                if self.stationary_time[name] > 0.8:
                    self._detach_servo(name)
                else:
                    self._write_servo_angle(name, new_pos)
                    
                time.sleep(0.003) # Stagger writes to minimize power rail sag and servo noise
                
            time.sleep(0.01) # ~100Hz control loop

    def get_state(self):
        """Returns the current state for reporting to portal."""
        return {
            "current": {name: round(self.current_pos[name], 1) for name in self.names},
            "target": {name: round(self.target_pos[name], 1) for name in self.names},
            "mood": self.mood,
            "extroversion": self.extroversion,
            "face_tracking": self.face_tracking_active,
            "manual_override": self.manual_override,
            "mock": self.mock,
            "blink_active": self.blink_active,
            "blink_side": self.blink_side,
            "gesture_active": self.gesture_active,
            "eyes_closed": getattr(self, "eyes_closed", False)
        }

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
        self.extroversion = self.config.get("personality", {}).get("extroversion", 0.7)
        
        # Flags
        self.is_running = False
        self.blink_active = False
        self.blink_progress = 0.0 # 0.0 (open) to 1.0 (fully closed)
        self.manual_override = False # If true, manual controls from portal override everything
        self.face_tracking_active = False
        
        # Hardware interfaces
        self.bus = None
        self.gpio_servos = {}
        
        # Speed/stiffness factors (k) for exponential ease-out
        # Higher k = faster response, lower k = smoother/slower response
        self.speed_k = {
            "yaw": 5.0,
            "pitch": 5.0,
            "left_upper_eyelid": 4.0,
            "left_lower_eyelid": 4.0,
            "right_upper_eyelid": 4.0,
            "right_lower_eyelid": 4.0
        }
        self.run_time = 0.0

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
        cfg = self.servo_cfgs.get(name, {})
        if not cfg:
            return
            
        # 1. Apply inversion
        if cfg.get("inverted", False):
            center = cfg.get("center_angle", 90.0)
            angle = center - (angle - center)
            
        # 2. Apply trim offset
        angle_calibrated = angle + cfg.get("trim", 0.0)
        
        # 3. Apply safety clamps
        min_lim = cfg.get("min_angle", 40.0)
        max_lim = cfg.get("max_angle", 140.0)
        angle_final = max(min_lim, min(max_lim, angle_calibrated))
        
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
        """Exposed method to set individual targets."""
        if name in self.target_pos:
            cfg = self.servo_cfgs.get(name, {})
            # Clamp request to min/max safety limits
            min_lim = cfg.get("min_angle", 0)
            max_lim = cfg.get("max_angle", 180)
            self.target_pos[name] = max(min_lim, min(max_lim, float(angle)))

    def trigger_blink(self):
        """Triggers a coordinated blink sequence."""
        if not self.blink_active:
            self.blink_active = True
            # Spawn a thread to handle the blink timing so it doesn't block the loop
            threading.Thread(target=self._blink_sequence_thread, daemon=True).start()

    def _blink_sequence_thread(self):
        """Coordinated blink timing using easing curves: closes lids in 60ms, holds 20ms, opens in 200ms."""
        # 1. Close eyelids (60ms) using quadratic ease-in
        close_duration = 0.06
        start_time = time.time()
        while time.time() - start_time < close_duration:
            elapsed = time.time() - start_time
            t = min(1.0, elapsed / close_duration)
            self.blink_progress = t * t
            time.sleep(0.01)
        self.blink_progress = 1.0
        
        # 2. Hold closed briefly (20ms)
        time.sleep(0.02)
        
        # 3. Open eyelids (200ms) using cubic ease-out
        open_duration = 0.20
        start_time = time.time()
        while time.time() - start_time < open_duration:
            elapsed = time.time() - start_time
            t = min(1.0, elapsed / open_duration)
            self.blink_progress = (1.0 - t) ** 3
            time.sleep(0.01)
            
        self.blink_progress = 0.0
        self.blink_active = False

    def trigger_wink(self, side="left"):
        """Triggers a single-sided wink."""
        if not self.blink_active:
            self.blink_active = True
            threading.Thread(target=self._wink_sequence_thread, args=(side,), daemon=True).start()

    def _wink_sequence_thread(self, side):
        # 1. Close eyelid (60ms) using quadratic ease-in
        close_duration = 0.06
        start_time = time.time()
        while time.time() - start_time < close_duration:
            elapsed = time.time() - start_time
            t = min(1.0, elapsed / close_duration)
            self.blink_progress = t * t
            time.sleep(0.01)
        self.blink_progress = 1.0
        
        # 2. Hold closed briefly (40ms)
        time.sleep(0.04)
        
        # 3. Open eyelid (200ms) using cubic ease-out
        open_duration = 0.20
        start_time = time.time()
        while time.time() - start_time < open_duration:
            elapsed = time.time() - start_time
            t = min(1.0, elapsed / open_duration)
            self.blink_progress = (1.0 - t) ** 3
            time.sleep(0.01)
            
        self.blink_progress = 0.0
        self.blink_active = False

    def _get_eyelid_baselines(self):
        """Returns the normal open/closed baseline limits based on the current mood."""
        # Baseline limits represent the "Open" state.
        # Format: (left_upper, left_lower, right_upper, right_lower)
        # Standard center is 90. Eyelids close by moving toward each other:
        # Upper eyelid moves DOWN (angle increases, say from 50 to 110)
        # Lower eyelid moves UP (angle decreases, say from 130 to 95)
        # For a standard Will Cogley mechanism:
        # Upper lid fully open is ~60 deg, fully closed is ~120 deg.
        # Lower lid fully open is ~120 deg, fully closed is ~80 deg.
        
        # Let's define default offsets relative to their center (90)
        # Upper open is 60, Lower open is 120.
        # Upper closed is 120, Lower closed is 80.
        
        # We start with neutral positions
        lu_open, ll_open = 60.0, 120.0
        ru_open, rl_open = 60.0, 120.0
        
        # Adjust baselines depending on mood
        if self.mood == "angry":
            # Narrowed eyes: upper eyelids lower, lower lids raise
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
            # Slightly open
            lu_open, ll_open = 50.0, 130.0
            ru_open, rl_open = 50.0, 130.0
            
        return lu_open, ll_open, ru_open, rl_open

    def _control_loop(self):
        """Continuous background thread that smooths servo movements and handles features."""
        last_time = time.time()
        drift_timer = time.time()
        
        # Random automatic blinking timer
        next_blink_time = time.time() + random.uniform(4.0, 10.0)
        
        # Drift target variables for smooth looking around
        drift_yaw = 90.0
        drift_pitch = 90.0
        drift_start_yaw = 90.0
        drift_start_pitch = 90.0
        drift_duration = 1.5
        drift_elapsed = 1.5
        
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
            
            # Increment continuous run time for biological drift
            self.run_time += dt

            # ----------------------------------------------------
            # 1. AUTONOMOUS DRIFT GAZE (Smooth drifts, NO SACCADES)
            # ----------------------------------------------------
            if not self.manual_override and not self.face_tracking_active:
                # If it's time to choose a new gaze location
                if now > drift_timer:
                    drift_start_yaw = self.target_pos["yaw"]
                    drift_start_pitch = self.target_pos["pitch"]
                    
                    # Choose a smooth new look angle (close to center for realism)
                    # Extroverted personality will look around more frequently and wider
                    gaze_range = 18.0 * self.extroversion
                    drift_yaw = 90.0 + random.uniform(-gaze_range, gaze_range)
                    drift_pitch = 90.0 + random.uniform(-gaze_range / 2.0, gaze_range / 3.0)
                    
                    # Eyelids and speed values based on mood
                    if self.mood == "sad":
                        drift_pitch -= 10.0 # Sad companion looks down
                        
                    drift_duration = random.uniform(1.2, 2.5) # Smooth slow look shifts
                    drift_elapsed = 0.0
                    
                    # Set next look timer: extrovert looks around more often (3-6s), introvert stays still longer (6-12s)
                    drift_interval = random.uniform(3.0, 6.0) if self.extroversion > 0.5 else random.uniform(6.0, 12.0)
                    drift_timer = now + drift_interval
                
                # Perform smooth cosine interpolation for the gaze target
                if drift_elapsed < drift_duration:
                    drift_elapsed += dt
                    t = min(1.0, drift_elapsed / drift_duration)
                    # Cosine ease-in-out curve
                    factor = (1.0 - math.cos(t * math.pi)) / 2.0
                    self.target_pos["yaw"] = drift_start_yaw + (drift_yaw - drift_start_yaw) * factor
                    self.target_pos["pitch"] = drift_start_pitch + (drift_pitch - drift_start_pitch) * factor

            # Create an effective target copy (tremor disabled for ultra-smoothness)
            effective_target = self.target_pos.copy()

            # ----------------------------------------------------
            # 2. EYELID TRACKING & BLINK OVERRIDES
            # ----------------------------------------------------
            if not self.manual_override:
                # Trigger automatic random blink
                if now > next_blink_time:
                    self.trigger_blink()
                    next_blink_time = now + random.uniform(4.0, 10.0)
                
                # Get normal baseline angles for this mood
                lu_base, ll_base, ru_base, rl_base = self._get_eyelid_baselines()
                
                # Apply Pitch-tracking to eyelids (highly realistic animatronic effect!)
                # Looking UP (current pitch < 90) raises upper eyelids.
                # Looking DOWN (current pitch > 90) droops upper eyelids and raises lower eyelids.
                pitch_diff = self.current_pos["pitch"] - 90.0
                
                # Eyelid tracking gain
                gain_upper = 0.5
                gain_lower = 0.3
                
                lu_target = lu_base + (pitch_diff * gain_upper)
                ll_target = ll_base + (pitch_diff * gain_lower)
                ru_target = ru_base + (pitch_diff * gain_upper)
                rl_target = rl_base + (pitch_diff * gain_lower)
                
                # Apply blink overrides
                if self.blink_active:
                    # Blink closes both eyelids.
                    # Upper eyelids go to closed (~125), Lower eyelids go to closed (~80)
                    lu_target = lu_target + (125.0 - lu_target) * self.blink_progress
                    ll_target = ll_target + (80.0 - ll_target) * self.blink_progress
                    ru_target = ru_target + (125.0 - ru_target) * self.blink_progress
                    rl_target = rl_target + (80.0 - rl_target) * self.blink_progress
                
                effective_target["left_upper_eyelid"] = lu_target
                effective_target["left_lower_eyelid"] = ll_target
                effective_target["right_upper_eyelid"] = ru_target
                effective_target["right_lower_eyelid"] = rl_target
            
            # ----------------------------------------------------
            # 3. EXPONENTIAL EASE-OUT INTERPOLATION & WRITES
            # ----------------------------------------------------
            for name in self.names:
                cfg = self.servo_cfgs.get(name, {})
                speed_limit = cfg.get("speed_limit", 150.0) # Max degrees per second
                
                target = effective_target[name]
                current = self.current_pos[name]
                
                # Retrieve speed factor k
                k = self.speed_k.get(name, 5.0)
                if self.mood == "sad" or self.mood == "bored":
                    k *= 0.6
                elif self.mood == "excited" or self.mood == "surprised":
                    k *= 1.4
                
                # Calculate ease-out step
                factor = 1.0 - math.exp(-k * dt)
                step = (target - current) * factor
                
                # Clamp step to speed limit
                max_step = speed_limit * dt
                if self.mood == "sad" or self.mood == "bored":
                    max_step *= 0.6
                elif self.mood == "excited" or self.mood == "surprised":
                    max_step *= 1.3
                    
                step_clamped = max(-max_step, min(max_step, step))
                new_pos = current + step_clamped
                
                # Safety clamps from config parameters to prevent mechanical binding
                min_lim = cfg.get("min_angle", 40.0) if name in ["left_upper_eyelid", "left_lower_eyelid", "right_upper_eyelid", "right_lower_eyelid"] else cfg.get("min_angle", 0.0)
                max_lim = cfg.get("max_angle", 140.0) if name in ["left_upper_eyelid", "left_lower_eyelid", "right_upper_eyelid", "right_lower_eyelid"] else cfg.get("max_angle", 180.0)
                min_lim = cfg.get("min_angle", min_lim)
                max_lim = cfg.get("max_angle", max_lim)
                
                new_pos = max(min_lim, min(max_lim, new_pos))
                
                # Snap to target if very close to prevent tiny float adjustments
                if abs(target - new_pos) < 0.05:
                    new_pos = target
                    
                self.current_pos[name] = new_pos
                self._write_servo_angle(name, new_pos)
                
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
            "mock": self.mock
        }

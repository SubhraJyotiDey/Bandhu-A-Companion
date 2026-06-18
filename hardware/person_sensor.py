import io
import struct
import sys
import time
import threading
import random

# Try loading fcntl only on Linux/macOS
try:
    import fcntl
    I2C_SLAVE = 0x0703
except ImportError:
    fcntl = None
    I2C_SLAVE = None

# Person Sensor details
PERSON_SENSOR_I2C_ADDRESS = 0x62
PERSON_SENSOR_I2C_HEADER_FORMAT = "BBH"
PERSON_SENSOR_FACE_FORMAT = "BBBBBBbB"
PERSON_SENSOR_FACE_MAX = 4

# The complete binary packet format: 
# Header (4 bytes) + Num Faces (1 byte) + 4 Face records (8 bytes each) + Checksum (2 bytes)
# We use little-endian '<' to prevent structural alignment padding
PERSON_SENSOR_RESULT_FORMAT = "<" + PERSON_SENSOR_I2C_HEADER_FORMAT + "B" + (PERSON_SENSOR_FACE_FORMAT * PERSON_SENSOR_FACE_MAX) + "H"
PERSON_SENSOR_RESULT_BYTE_COUNT = struct.calcsize(PERSON_SENSOR_RESULT_FORMAT)

class PersonSensor:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.config = config_manager.config
        self.mock = self.config.get("mock", True)
        
        # If we are on Windows or fcntl is missing, force mock mode
        if sys.platform.startswith("win") or fcntl is None:
            self.mock = True
            
        self.bus_num = self.config.get("face_tracking", {}).get("i2c_bus", 1)
        self.sensor_address = self.config.get("face_tracking", {}).get("sensor_address", 0x62)
        
        # Live state
        self.faces = []
        self.face_detected = False
        self.last_read_time = 0.0
        
        # Mock variables
        self.mock_faces = [] # list of dicts: [{"box_left": X, "box_top": Y, "box_right": X, "box_bottom": Y, "confidence": C}]
        
        self.i2c_handle = None
        self.is_running = False
        self.thread = None
        
        if not self.mock:
            self._init_i2c()

    def _init_i2c(self):
        print(f"[Sensor] Initializing Person Sensor on /dev/i2c-{self.bus_num}...")
        try:
            # Open direct unbuffered read/write handle to I2C bus
            self.i2c_handle = io.open(f"/dev/i2c-{self.bus_num}", "rb+", buffering=0)
            # Set address
            fcntl.ioctl(self.i2c_handle, I2C_SLAVE, self.sensor_address)
            print("[Sensor] I2C connection initialized successfully.")
        except Exception as e:
            print(f"[Sensor Error] Failed to connect to I2C sensor: {e}. Switching to Mock Mode.")
            self.mock = True

    def start(self):
        """Starts background reader thread."""
        self.is_running = True
        self.thread = threading.Thread(target=self._read_loop, name="PersonSensorLoop")
        self.thread.daemon = True
        self.thread.start()
        print("[Sensor] Background read loop started.")

    def stop(self):
        """Stops background reader thread."""
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=1.0)
        if self.i2c_handle:
            try:
                self.i2c_handle.close()
            except Exception:
                pass
        print("[Sensor] Background read loop stopped.")

    def set_mock_face(self, x, y, size=40, detected=True):
        """Web portal interface to inject face coordinates when in mock mode."""
        if not detected:
            self.mock_faces = []
            return
            
        # Create a bounding box centered around x, y (0-255 grid)
        half_sz = size // 2
        box_left = max(0, int(x - half_sz))
        box_right = min(255, int(x + half_sz))
        box_top = max(0, int(y - half_sz))
        box_bottom = min(255, int(y + half_sz))
        
        self.mock_faces = [{
            "box_confidence": 99,
            "box_left": box_left,
            "box_top": box_top,
            "box_right": box_right,
            "box_bottom": box_bottom,
            "id_confidence": 0,
            "id": -1,
            "is_facing": 1
        }]

    def _read_loop(self):
        """Reads at ~10Hz (sensor refresh rate is 7Hz)."""
        while self.is_running:
            self.mock = self.config_manager.config.get("mock", True)
            if sys.platform.startswith("win") or fcntl is None:
                self.mock = True
                
            if self.mock:
                # Read from mock faces injected via portal
                self.faces = list(self.mock_faces)
                self.face_detected = len(self.faces) > 0
                self.last_read_time = time.time()
            else:
                # Read from physical I2C device
                try:
                    # Read the complete result packet (typically 39 bytes)
                    # The Useful Sensors chip outputs 39 bytes.
                    data = self.i2c_handle.read(PERSON_SENSOR_RESULT_BYTE_COUNT)
                    
                    if len(data) >= PERSON_SENSOR_RESULT_BYTE_COUNT:
                        # Unpack using format:
                        # header: BBH (4 bytes)
                        # num_faces: B (1 byte)
                        # faces: 4 * BBBBBBbB (32 bytes)
                        # checksum: H (2 bytes)
                        unpacked = struct.unpack(PERSON_SENSOR_RESULT_FORMAT, data)
                        
                        # Indices in unpacked:
                        # 0: header reserved 1
                        # 1: header reserved 2
                        # 2: data_length
                        # 3: num_faces
                        # 4..35: face fields (4 faces * 8 fields = 32 values)
                        # 36: checksum
                        
                        num_faces = unpacked[3]
                        temp_faces = []
                        
                        # Loop through detected faces (up to max 4)
                        for i in range(min(num_faces, PERSON_SENSOR_FACE_MAX)):
                            offset = 4 + (i * 8)
                            face = {
                                "box_confidence": unpacked[offset],
                                "box_left": unpacked[offset + 1],
                                "box_top": unpacked[offset + 2],
                                "box_right": unpacked[offset + 3],
                                "box_bottom": unpacked[offset + 4],
                                "id_confidence": unpacked[offset + 5],
                                "id": unpacked[offset + 6],
                                "is_facing": unpacked[offset + 7]
                            }
                            # Only include faces with reasonable confidence
                            if face["box_confidence"] > 40:
                                temp_faces.append(face)
                                
                        self.faces = temp_faces
                        self.face_detected = len(self.faces) > 0
                        self.last_read_time = time.time()
                except Exception as e:
                    # I2C read error, standard for floating pins or noise
                    self.face_detected = False
                    self.faces = []
                    
            time.sleep(0.1) # 10Hz read frequency

    def get_primary_face(self):
        """Returns the active tracked face, alternating attention if multiple are present."""
        if not self.face_detected or not self.faces:
            return None
            
        # Sort faces by bounding box area (largest/closest first)
        def get_area(f):
            return (f["box_right"] - f["box_left"]) * (f["box_bottom"] - f["box_top"])
            
        sorted_faces = sorted(self.faces, key=get_area, reverse=True)
        
        # Track switching state
        if not hasattr(self, "_last_switch_time"):
            self._last_switch_time = 0.0
            self._current_face_idx = 0
            
        now = time.time()
        num_faces = len(sorted_faces)
        
        if num_faces > 1:
            # Shift attention every 4-6 seconds
            if now - self._last_switch_time > random.uniform(4.0, 6.0):
                # Move to next face, with a bias towards looking at the closest (index 0)
                if random.random() < 0.65:
                    self._current_face_idx = 0 # Look at primary person
                else:
                    self._current_face_idx = random.randint(1, num_faces - 1)
                self._last_switch_time = now
                print(f"[Sensor] Gaze shifted to person index {self._current_face_idx} in a group of {num_faces}")
        else:
            self._current_face_idx = 0
            
        if self._current_face_idx >= num_faces:
            self._current_face_idx = 0
            
        face = sorted_faces[self._current_face_idx]
        
        # Calculate center
        x = (face["box_left"] + face["box_right"]) / 2.0
        y = (face["box_top"] + face["box_bottom"]) / 2.0
        
        return {
            "x": x,
            "y": y,
            "width": face["box_right"] - face["box_left"],
            "height": face["box_bottom"] - face["box_top"],
            "confidence": face["box_confidence"],
            "id": face["id"],
            "is_facing": face["is_facing"],
            "group_index": self._current_face_idx,
            "total_group": num_faces
        }

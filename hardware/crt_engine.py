import os
import sys
import time
import math
import queue
import threading
import numpy as np

# Try importing PyAudio. Warn if not available.
try:
    import pyaudio
    pyaudio_available = True
except ImportError:
    pyaudio_available = False

# Try importing svgpathtools. Warn if not available.
try:
    import svgpathtools
    svgpathtools_available = True
except ImportError:
    svgpathtools_available = False

# Pre-defined print override to prevent UnicodeEncodeError in logs on Windows
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

# ==============================================================================
# Vector Stroke Font
# ==============================================================================
STROKE_FONT = {
    'A': [(-1, -1), (-1, 0), (0, 1), (1, 0), (1, -1), (1, 0), (-1, 0)],
    'B': [(-1, -1), (-1, 1), (0.5, 1), (0, 0), (0.7, 0), (0.7, -1), (-1, -1), (-1, 0), (0, 0)],
    'C': [(1, 0.7), (0.5, 1), (-1, 1), (-1, -1), (0.5, -1), (1, -0.7)],
    'D': [(-1, -1), (-1, 1), (0.5, 1), (1, 0.5), (1, -0.5), (0.5, -1), (-1, -1)],
    'E': [(1, 1), (-1, 1), (-1, -1), (1, -1), (-1, -1), (-1, 0), (0.5, 0)],
    'F': [(1, 1), (-1, 1), (-1, -1), (-1, 0), (0.5, 0)],
    'G': [(1, 0.7), (0.5, 1), (-1, 1), (-1, -1), (1, -1), (1, 0), (0.2, 0)],
    'H': [(-1, 1), (-1, -1), (-1, 0), (1, 0), (1, 1), (1, -1)],
    'I': [(-0.5, 1), (0.5, 1), (0, 1), (0, -1), (-0.5, -1), (0.5, -1)],
    'J': [(-0.5, 1), (0.5, 1), (0, 1), (0, -0.7), (-0.7, -0.7), (-0.7, 0)],
    'K': [(-1, 1), (-1, -1), (-1, 0), (1, 1), (-1, 0), (1, -1)],
    'L': [(-1, 1), (-1, -1), (1, -1)],
    'M': [(-1, -1), (-1, 1), (0, 0.2), (1, 1), (1, -1)],
    'N': [(-1, -1), (-1, 1), (1, -1), (1, 1)],
    'O': [(-1, -1), (-1, 1), (1, 1), (1, -1), (-1, -1)],
    'P': [(-1, -1), (-1, 1), (1, 1), (1, 0), (-1, 0)],
    'Q': [(-1, -1), (-1, 1), (1, 1), (1, -1), (-1, -1), (1, -1), (0.3, -0.3), (1, -1)],
    'R': [(-1, -1), (-1, 1), (1, 1), (1, 0), (-1, 0), (1, -1)],
    'S': [(1, 0.7), (0.5, 1), (-1, 1), (-1, 0), (1, 0), (1, -1), (-0.5, -1), (-1, -0.7)],
    'T': [(-1, 1), (1, 1), (0, 1), (0, -1)],
    'U': [(-1, 1), (-1, -0.7), (-0.7, -1), (0.7, -1), (1, -0.7), (1, 1)],
    'V': [(-1, 1), (0, -1), (1, 1)],
    'W': [(-1, 1), (-1, -1), (0, -0.2), (1, -1), (1, 1)],
    'X': [(-1, 1), (1, -1), (0, 0), (1, 1), (-1, -1)],
    'Y': [(-1, 1), (0, 0), (1, 1), (0, 0), (0, -1)],
    'Z': [(-1, 1), (1, 1), (-1, -1), (1, -1)],
    '0': [(-1, -1), (-1, 1), (1, 1), (1, -1), (-1, -1), (1, 1)],
    '1': [(-0.5, 0.5), (0, 1), (0, -1), (-0.5, -1), (0.5, -1)],
    '2': [(-1, 0.7), (-0.5, 1), (1, 1), (1, 0.2), (-1, -1), (1, -1)],
    '3': [(-1, 1), (1, 1), (1, 0), (0, 0), (1, 0), (1, -1), (-1, -1)],
    '4': [(-1, 1), (-1, 0), (1, 0), (1, 1), (1, -1)],
    '5': [(1, 1), (-1, 1), (-1, 0), (1, 0), (1, -1), (-1, -1)],
    '6': [(1, 1), (-1, 1), (-1, -1), (1, -1), (1, 0), (-1, 0)],
    '7': [(-1, 1), (1, 1), (-0.5, -1)],
    '8': [(-1, 0), (-1, 1), (1, 1), (1, -1), (-1, -1), (-1, 0), (1, 0), (1, 0.1)],
    '9': [(-1, -1), (1, -1), (1, 1), (-1, 1), (-1, 0), (1, 0)],
    ' ': [(0, 0)],
    '-': [(-0.5, 0), (0.5, 0)],
    '!': [(0, 1), (0, -0.3), (0, -0.3), (0, -0.8), (0, -1)]
}

# ==============================================================================
# VectorCrtEngine Class
# ==============================================================================
class VectorCrtEngine:
    def __init__(self, config_manager, sample_rate=44100):
        self.config_manager = config_manager
        self.sample_rate = sample_rate
        
        # Audio streaming thread state
        self.is_running = False
        self.audio_thread = None
        self.animation_thread = None
        self.stream = None
        self.pya = None
        
        # Buffer coordinates (normalized -1.0 to 1.0)
        self.active_points = np.zeros((1, 2))
        self.buffer_lock = threading.Lock()
        
        # CRT Mode state
        # Modes: 'idle', 'stt_waveform', 'tts_waveform', 'tts_mouth', 'startup', 'temp_shape', 'temp_text'
        self.current_mode = "idle"
        self.temp_end_time = 0.0
        
        # Real-time audio wiggles
        self.mic_samples = np.zeros(512)
        self.speaker_samples = np.zeros(512)
        self.tts_rms = 0.0
        self.audio_lock = threading.Lock()
        
        # 3D Shape Definitions
        self.cube_vertices = np.array([
            [-0.5, -0.5, -0.5], [-0.5, -0.5,  0.5], [-0.5,  0.5, -0.5], [-0.5,  0.5,  0.5],
            [ 0.5, -0.5, -0.5], [ 0.5, -0.5,  0.5], [ 0.5,  0.5, -0.5], [ 0.5,  0.5,  0.5]
        ])
        self.cube_trace_indices = [0, 1, 3, 2, 0, 4, 5, 7, 6, 4, 5, 1, 3, 7, 6, 2]
        
        self.pyramid_vertices = np.array([
            [-0.6, -0.6, -0.6], [ 0.6, -0.6, -0.6], [ 0.6,  0.6, -0.6], [-0.6,  0.6, -0.6],
            [ 0.0,  0.0,  0.6]
        ])
        self.pyramid_trace_indices = [0, 1, 2, 3, 0, 4, 1, 4, 2, 4, 3, 0]
        
        # Animation rotation angles
        self.angle_x = 0.0
        self.angle_y = 0.0
        
    def start(self):
        """Launches the PyAudio stereo streaming output loop and the animation engine thread."""
        if not pyaudio_available:
            print("[CRT Warning] PyAudio is not available in environment. CRT Engine running in dry mode.")
            return False
            
        self.pya = pyaudio.PyAudio()
        try:
            self.stream = self.pya.open(
                format=pyaudio.paInt16,
                channels=2,
                rate=self.sample_rate,
                output=True,
                frames_per_buffer=1024
            )
        except Exception as e:
            print(f"[CRT Error] Failed to open audio output channel for CRT deflection: {e}")
            self.pya.terminate()
            self.pya = None
            return False
            
        self.is_running = True
        self.audio_thread = threading.Thread(target=self._audio_stream_loop, daemon=True)
        self.audio_thread.start()
        
        self.animation_thread = threading.Thread(target=self._animation_step_loop, daemon=True)
        self.animation_thread.start()
        
        print("[CRT Engine] Vector Deflection Engine started successfully on audio channels.")
        return True
        
    def stop(self):
        """Stops all background CRT threads and cleans up streams."""
        self.is_running = False
        if self.audio_thread:
            self.audio_thread.join(timeout=1.0)
        if self.animation_thread:
            self.animation_thread.join(timeout=1.0)
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
        if self.pya:
            self.pya.terminate()
        print("[CRT Engine] Vector Deflection Engine stopped cleanly.")

    def set_mode(self, mode):
        """Changes the current display mode of the CRT."""
        if mode in ["idle", "stt_waveform", "tts_waveform", "tts_mouth", "startup"]:
            self.current_mode = mode
            print(f"[CRT Engine] Mode updated to: {mode}")

    def draw_momentary_text(self, text, duration=4.0):
        """Displays a vector text momentarily on the screen before returning to idle mode."""
        self.temp_end_time = time.time() + duration
        self.temp_text_val = text
        self.current_mode = "temp_text"
        print(f"[CRT Engine] Momentary text scheduled: '{text}' for {duration}s")

    def draw_momentary_shape(self, shape_name, duration=4.0):
        """Displays a specific vector shape momentarily before returning to idle mode."""
        self.temp_end_time = time.time() + duration
        self.temp_shape_val = shape_name
        self.current_mode = "temp_shape"
        print(f"[CRT Engine] Momentary shape scheduled: '{shape_name}' for {duration}s")

    def feed_microphone_samples(self, samples_array):
        """Feeds raw float samples from the speech recognizer into the CRT oscilloscope buffer."""
        with self.audio_lock:
            # We want to keep a rolling 512 samples
            arr = np.array(samples_array, dtype=np.float32)
            if len(arr) >= 512:
                self.mic_samples = arr[-512:]
            else:
                self.mic_samples = np.roll(self.mic_samples, -len(arr))
                self.mic_samples[-len(arr):] = arr

    def feed_speaker_samples(self, samples_array):
        """Feeds raw speaker output samples (or volume levels) to calculate real-time mouth shapes."""
        with self.audio_lock:
            arr = np.array(samples_array, dtype=np.float32)
            if len(arr) >= 512:
                self.speaker_samples = arr[-512:]
            else:
                self.speaker_samples = np.roll(self.speaker_samples, -len(arr))
                self.speaker_samples[-len(arr):] = arr
            # Calculate RMS
            if len(arr) > 0:
                self.tts_rms = np.sqrt(np.mean(arr**2))
            else:
                self.tts_rms = 0.0

    # ==============================================================================
    # Drawing & Tracing Algorithms
    # ==============================================================================
    def make_circle(self, radius=0.7, steps=200):
        theta = np.linspace(0, 2*np.pi, steps)
        x = radius * np.cos(theta)
        y = radius * np.sin(theta)
        return np.column_stack((x, y))

    def make_lissajous(self, steps=400):
        t = np.linspace(0, 2*np.pi, steps)
        x = 0.75 * np.sin(5 * t + np.pi/2)
        y = 0.75 * np.sin(6 * t)
        return np.column_stack((x, y))

    def make_spiral(self, steps=400):
        t = np.linspace(0, 6 * np.pi, steps)
        r = 0.75 * (t / (6 * np.pi))
        x = r * np.cos(t)
        y = r * np.sin(t)
        return np.column_stack((x, y))

    def make_vector_text(self, text_str, char_scale=0.08, spacing=0.18):
        text_str = text_str.upper()
        all_points = []
        total_width = len(text_str) * spacing
        start_x = -total_width / 2.0 + (spacing / 2.0)
        
        for idx, char in enumerate(text_str):
            stroke_pts = STROKE_FONT.get(char, STROKE_FONT[' '])
            char_pts = []
            for stroke in stroke_pts:
                x = start_x + (idx * spacing) + (stroke[0] * char_scale)
                y = stroke[1] * char_scale * 1.5
                char_pts.append([x, y])
                
            if all_points and char_pts:
                # Fast travel path connection
                travel = np.linspace(all_points[-1], char_pts[0], 5)
                all_points.extend(travel.tolist())
                
            all_points.extend(char_pts)
            
        return np.array(all_points) if all_points else np.zeros((1, 2))

    def rotate_and_project_shape(self, vertices, indices, angle_x, angle_y):
        # Y rotation (yaw)
        cy, sy = math.cos(angle_y), math.sin(angle_y)
        rot_y = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
        
        # X rotation (pitch)
        cx, sx = math.cos(angle_x), math.sin(angle_x)
        rot_x = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
        
        rotated = vertices @ rot_y.T @ rot_x.T
        
        # Perspective projection
        d = 2.2
        projected = []
        for v in rotated:
            z_off = v[2] + d
            x_2d = v[0] * d / z_off
            y_2d = v[1] * d / z_off
            projected.append([x_2d, y_2d])
            
        pts = np.array(projected)
        return pts[indices]

    # ==============================================================================
    # Continuous Generation Loop
    # ==============================================================================
    def _animation_step_loop(self):
        """Background loop updating animation frame coordinates based on current mode."""
        idle_cycle = ["cube", "circle", "spiral", "lissajous", "pyramid"]
        cycle_idx = 0
        cycle_timer = 0.0
        
        while self.is_running:
            # Handle temporary mode timeouts
            if self.current_mode in ["temp_text", "temp_shape"] and time.time() > self.temp_end_time:
                self.current_mode = "idle"
                print("[CRT Engine] Momentary display timed out. Reverting to idle.")
                
            mode = self.current_mode
            
            # 1. Idle mode: slowly cycle shapes every 8 seconds
            if mode == "idle":
                if time.time() - cycle_timer > 8.0:
                    cycle_idx = (cycle_idx + 1) % len(idle_cycle)
                    cycle_timer = time.time()
                
                active_shape = idle_cycle[cycle_idx]
                if active_shape == "cube":
                    pts = self.rotate_and_project_shape(self.cube_vertices, self.cube_trace_indices, self.angle_x, self.angle_y)
                elif active_shape == "pyramid":
                    pts = self.rotate_and_project_shape(self.pyramid_vertices, self.pyramid_trace_indices, self.angle_x, self.angle_y)
                elif active_shape == "circle":
                    pts = self.make_circle(radius=0.6)
                elif active_shape == "spiral":
                    pts = self.make_spiral()
                elif active_shape == "lissajous":
                    pts = self.make_lissajous()
                    
                with self.buffer_lock:
                    self.active_points = pts
                    
            # 2. Temporary manual rendering shapes
            elif mode == "temp_shape":
                shape_name = getattr(self, "temp_shape_val", "cube")
                if shape_name == "cube":
                    pts = self.rotate_and_project_shape(self.cube_vertices, self.cube_trace_indices, self.angle_x, self.angle_y)
                elif shape_name == "pyramid":
                    pts = self.rotate_and_project_shape(self.pyramid_vertices, self.pyramid_trace_indices, self.angle_x, self.angle_y)
                elif shape_name == "circle":
                    pts = self.make_circle(radius=0.6)
                elif shape_name == "spiral":
                    pts = self.make_spiral()
                elif shape_name == "lissajous":
                    pts = self.make_lissajous()
                else:
                    pts = self.make_circle(radius=0.3)
                with self.buffer_lock:
                    self.active_points = pts
                    
            # 3. Temporary manual rendering text
            elif mode == "temp_text":
                text_str = getattr(self, "temp_text_val", "READY")
                pts = self.make_vector_text(text_str)
                with self.buffer_lock:
                    self.active_points = pts
                    
            # 4. Microphone STT real-time oscilloscope
            elif mode == "stt_waveform":
                with self.audio_lock:
                    samples = np.copy(self.mic_samples)
                # Normalize Y based on standard mic limits
                y = np.clip(samples * 15.0, -0.9, 0.9)
                x = np.linspace(-0.95, 0.95, len(y))
                pts = np.column_stack((x, y))
                with self.buffer_lock:
                    self.active_points = pts
                    
            # 5. Speaker TTS real-time oscilloscope
            elif mode == "tts_waveform":
                with self.audio_lock:
                    samples = np.copy(self.speaker_samples)
                y = np.clip(samples * 12.0, -0.9, 0.9)
                x = np.linspace(-0.95, 0.95, len(y))
                pts = np.column_stack((x, y))
                with self.buffer_lock:
                    self.active_points = pts
                    
            # 6. Speaker TTS mouth animation (expanding circle mapping speaker volume/RMS)
            elif mode == "tts_mouth":
                with self.audio_lock:
                    rms = self.tts_rms
                # Map RMS to circle radius (minimum size 0.15, maximum size 0.8)
                radius = 0.15 + np.clip(rms * 15.0, 0.0, 0.65)
                # Flatten circle slightly on the vertical scale if mouth is shut, expand round when talking
                pts = self.make_circle(radius=radius, steps=150)
                # Add a horizontal line in the middle if mouth is silent (horizontal slit)
                if rms < 0.01:
                    line_pts = np.column_stack((np.linspace(-radius, radius, 20), np.zeros(20)))
                    pts = np.vstack((pts, line_pts))
                with self.buffer_lock:
                    self.active_points = pts
                    
            # 7. Retro boot sequence: expand center point to circle, draw lines and spell message
            elif mode == "startup":
                # Sequenced boot: dot expands -> grid -> spells "READY"
                boot_time = getattr(self, "boot_start_time", 0.0)
                if boot_time == 0.0:
                    self.boot_start_time = time.time()
                    boot_time = self.boot_start_time
                    
                elapsed = time.time() - boot_time
                if elapsed < 1.0:
                    # Dot
                    pts = np.zeros((10, 2))
                elif elapsed < 2.0:
                    # Circle expanding
                    rad = 0.7 * (elapsed - 1.0)
                    pts = self.make_circle(radius=rad)
                elif elapsed < 4.0:
                    # Spell "BOOTING"
                    pts = self.make_vector_text("BOOTING")
                elif elapsed < 6.0:
                    # Spell "READY"
                    pts = self.make_vector_text("READY")
                else:
                    # Reset boot variable and drop to idle mode
                    self.boot_start_time = 0.0
                    self.current_mode = "idle"
                    pts = self.make_circle(radius=0.6)
                    
                with self.buffer_lock:
                    self.active_points = pts
            
            # Slowly update rotation angles for 3D shapes
            self.angle_x += 0.025
            self.angle_y += 0.035
            
            time.sleep(0.02) # Update coordinate structures at ~50 FPS

    def _audio_stream_loop(self):
        """Low-level PyAudio stereophonic frame loop."""
        chunk = 1024
        buf_idx = 0
        
        while self.is_running:
            if not self.stream:
                time.sleep(0.1)
                continue
                
            with self.buffer_lock:
                pts = np.copy(self.active_points)
                
            num_pts = len(pts)
            if num_pts == 0:
                silence = np.zeros(chunk * 2, dtype=np.int16)
                self.stream.write(silence.tobytes())
                time.sleep(0.01)
                continue
                
            # Interleave X/Y coordinates as L/R channel frames
            stereo_buf = np.zeros((chunk, 2), dtype=np.int16)
            for i in range(chunk):
                pt = pts[buf_idx % num_pts]
                stereo_buf[i, 0] = int(pt[0] * 32767) # L channel = Horizontal (X)
                stereo_buf[i, 1] = int(pt[1] * 32767) # R channel = Vertical (Y)
                buf_idx += 1
                
            self.stream.write(stereo_buf.flatten().tobytes())

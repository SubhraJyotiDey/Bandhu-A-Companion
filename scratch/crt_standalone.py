import time
import math
import sys
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

# ==============================================================================
# Stroke Font Vector Text Definition
# ==============================================================================
# Points are defined on a 3x3 grid:
# Top Row:    TL=(-1, 1),   TC=(0, 1),   TR=(1, 1)
# Middle Row: ML=(-1, 0),   MC=(0, 0),   MR=(1, 0)
# Bottom Row: BL=(-1, -1),  BC=(0, -1),  BR=(1, -1)
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
# Vector CRT Graphics Engine Class
# ==============================================================================
class VectorCrtEngine:
    def __init__(self, sample_rate=44100):
        self.sample_rate = sample_rate
        self.points_buffer = None
        self.points_lock = threading.Lock()
        
        # Audio thread state
        self.is_running = False
        self.audio_thread = None
        self.stream = None
        self.pya = None
        
        # 3D Wireframe models
        # Coordinates defined as (X, Y, Z) from -1.0 to 1.0
        self.cube_vertices = np.array([
            [-0.5, -0.5, -0.5], # 0
            [ 0.5, -0.5, -0.5], # 1
            [ 0.5,  0.5, -0.5], # 2
            [-0.5,  0.5, -0.5], # 3
            [-0.5, -0.5,  0.5], # 4
            [ 0.5, -0.5,  0.5], # 5
            [ 0.5,  0.5,  0.5], # 6
            [-0.5,  0.5,  0.5]  # 7
        ])
        
        # Continuous Tracing Path of a Cube:
        # A cube has all odd-degree vertices (degree 3). By Euler's theorem, we cannot
        # draw it without lifting the pen unless we double-trace some edges.
        # This path traces all 12 edges of the cube in one unbroken line of 15 segments.
        self.cube_trace_indices = [
            0, 1, 2, 3, 0,  # Bottom face loop
            4, 5, 1, 5, 6,  # Rise to top face, trace vertical pillars and edges
            2, 6, 7, 3, 7, 
            4              # Finish top face loop
        ]
        
        self.pyramid_vertices = np.array([
            [-0.6, -0.6, -0.6], # 0 (Base Base-Left)
            [ 0.6, -0.6, -0.6], # 1 (Base Base-Right)
            [ 0.6,  0.6, -0.6], # 2 (Base Top-Right)
            [-0.6,  0.6, -0.6], # 3 (Base Top-Left)
            [ 0.0,  0.0,  0.6]  # 4 (Apex Tip)
        ])
        
        # Continuous Tracing Path of a Pyramid:
        # Traces the square base, climbs to the apex, and descends to trace all side edges.
        self.pyramid_trace_indices = [
            0, 1, 2, 3, 0,  # Base square
            4, 1, 4, 2, 4,  # Climb to apex, double-trace side edges
            3, 0
        ]
        
    def start(self):
        """Initializes PyAudio and launches the audio streaming background thread."""
        if not pyaudio_available:
            print("[CRT Error] PyAudio not installed. Standalone mode cannot play sound.")
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
            print(f"[CRT Error] Failed to open PyAudio audio output device: {e}")
            return False
            
        self.is_running = True
        self.audio_thread = threading.Thread(target=self._audio_loop, daemon=True)
        self.audio_thread.start()
        print("[CRT Engine] Background stereo audio thread started at 44.1 kHz.")
        return True
        
    def stop(self):
        """Stops the audio thread and releases PyAudio resources."""
        self.is_running = False
        if self.audio_thread:
            self.audio_thread.join(timeout=1.0)
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
        if self.pya:
            self.pya.terminate()
        print("[CRT Engine] Audio thread stopped cleanly.")

    def set_points(self, points):
        """Sets the active array of X/Y coordinates to be generated as audio.
        points should be a numpy array of shape (N, 2) with values from -1.0 to 1.0."""
        with self.points_lock:
            self.points_buffer = np.clip(points, -1.0, 1.0)

    def _audio_loop(self):
        """Continuous background thread that loops the active point coordinates,
        rescaling them into 16-bit PCM stereo data and writing to PyAudio output."""
        chunk_size = 1024
        buffer_idx = 0
        
        while self.is_running:
            # Acquire lock to read active points
            with self.points_lock:
                if self.points_buffer is None or len(self.points_buffer) == 0:
                    # Write silence (zeros) if no shape is active
                    silence = np.zeros(chunk_size * 2, dtype=np.int16)
                    self.stream.write(silence.tobytes())
                    time.sleep(0.01)
                    continue
                active_pts = np.copy(self.points_buffer)
                
            num_pts = len(active_pts)
            
            # Tile/Loop coordinates to fill the audio chunk buffer (1024 samples)
            chunk_data = np.zeros((chunk_size, 2), dtype=np.int16)
            for i in range(chunk_size):
                pt = active_pts[buffer_idx % num_pts]
                # Scale normal floating coordinate range (-1.0 to 1.0) to 16-bit signed shorts
                # Horizontal = X = Left channel (index 0)
                # Vertical = Y = Right channel (index 1)
                chunk_data[i, 0] = int(pt[0] * 32767)
                chunk_data[i, 1] = int(pt[1] * 32767)
                buffer_idx += 1
                
            # Flatten to stereo interleave: [L0, R0, L1, R1, ...]
            stereo_flat = chunk_data.flatten()
            self.stream.write(stereo_flat.tobytes())
            
    # ==============================================================================
    # Math & Geometric Shapes Module
    # ==============================================================================
    def make_circle(self, radius=0.7, steps=250):
        """Generates a perfect vector circle using parametric polar math:
        x = R * cos(theta), y = R * sin(theta)"""
        theta = np.linspace(0, 2 * np.pi, steps)
        x = radius * np.cos(theta)
        y = radius * np.sin(theta)
        return np.column_stack((x, y))
        
    def make_lissajous(self, freq_x=5, freq_y=6, delta=np.pi/2, steps=600):
        """Generates a Lissajous knot which creates beautiful winding retro designs:
        x = A * sin(freq_x * t + delta), y = B * sin(freq_y * t)"""
        t = np.linspace(0, 2 * np.pi, steps)
        x = 0.7 * np.sin(freq_x * t + delta)
        y = 0.7 * np.sin(freq_y * t)
        return np.column_stack((x, y))
        
    def make_spiral(self, loops=5, steps=500):
        """Generates a spiral that winds outward by scaling radius linearly:
        R(t) = k * t"""
        t = np.linspace(0, loops * 2 * np.pi, steps)
        radius = 0.7 * (t / (loops * 2 * np.pi))
        x = radius * np.cos(t)
        y = radius * np.sin(t)
        return np.column_stack((x, y))
        
    # ==============================================================================
    # 3D Wireframe Module (Projection & Rotation)
    # ==============================================================================
    def project_3d_points(self, points_3d, camera_distance=2.0):
        """Applies basic 3D perspective projection. Coordinates are divided by Z
        depth so that further objects appear smaller, mimicking physical 3D:
        x = X * d / (Z + d), y = Y * d / (Z + d)"""
        projected = []
        for pt in points_3d:
            x_3d, y_3d, z_3d = pt
            # Add camera distance offset to avoid dividing by 0 or negative depth
            z_offset = z_3d + camera_distance
            x_2d = x_3d * camera_distance / z_offset
            y_2d = y_3d * camera_distance / z_offset
            projected.append([x_2d, y_2d])
        return np.array(projected)

    def rotate_3d_points(self, points_3d, angle_x, angle_y):
        """Rotates 3D points around Y-axis (yaw) and X-axis (pitch) dynamically
        using standard trigonometric rotation matrices."""
        # 1. Rotate around Y axis
        cos_y = math.cos(angle_y)
        sin_y = math.sin(angle_y)
        rot_y = np.array([
            [cos_y, 0, sin_y],
            [0, 1, 0],
            [-sin_y, 0, cos_y]
        ])
        
        # 2. Rotate around X axis
        cos_x = math.cos(angle_x)
        sin_x = math.sin(angle_x)
        rot_x = np.array([
            [1, 0, 0],
            [0, cos_x, -sin_x],
            [0, sin_x, cos_x]
        ])
        
        # Multiply vertices by rotation matrices
        rotated = points_3d @ rot_y.T @ rot_x.T
        return rotated

    def make_rotating_cube(self, angle_x, angle_y):
        """Calculates 3D rotation, projects vertices to 2D, and traces the cube's
        continuous single-stroke line path indices."""
        rotated = self.rotate_3d_points(self.cube_vertices, angle_x, angle_y)
        projected = self.project_3d_points(rotated)
        # Sequence the projected 2D coordinates according to our continuous path
        path = projected[self.cube_trace_indices]
        return path
        
    def make_rotating_pyramid(self, angle_x, angle_y):
        """Calculates 3D rotation, projects vertices to 2D, and traces the pyramid's
        continuous single-stroke line path indices."""
        rotated = self.rotate_3d_points(self.pyramid_vertices, angle_x, angle_y)
        projected = self.project_3d_points(rotated)
        path = projected[self.pyramid_trace_indices]
        return path

    # ==============================================================================
    # SVG Path Tracing Module
    # ==============================================================================
    def make_svg_path(self, svg_filepath, sample_steps=50):
        """Parses an SVG file using svgpathtools. Resolves shapes/Béziers to points,
        and links disjointed paths using rapid diagonal connecting strokes to
        minimize visual trace artifacts on the screen."""
        if not svgpathtools_available:
            print("[CRT Warning] svgpathtools library not found. Cannot parse SVG. Drawing standard circle instead.")
            return self.make_circle()
            
        try:
            paths, attributes = svgpathtools.svg2paths(svg_filepath)
            if not paths:
                print("[CRT Error] No paths found in SVG file.")
                return self.make_circle()
                
            all_points = []
            for path in paths:
                if len(path) == 0:
                    continue
                # Sample points along this specific path
                path_points = []
                for segment in path:
                    # Sample N points along each bezier curve or straight line segment
                    for t in np.linspace(0.0, 1.0, sample_steps):
                        pt_complex = segment.point(t)
                        # SVG coordinates are complex numbers (X + Yj)
                        path_points.append([pt_complex.real, pt_complex.imag])
                        
                # Connect last point of preceding path to start point of this path
                if all_points and path_points:
                    last_pt = all_points[-1]
                    first_pt = path_points[0]
                    # Connect with 5 rapid traveling points to minimize the visual glow of retrace lines
                    retrace_line = np.linspace(last_pt, first_pt, 5)
                    all_points.extend(retrace_line.tolist())
                    
                all_points.extend(path_points)
                
            pts = np.array(all_points)
            if len(pts) == 0:
                return self.make_circle()
                
            # Normalize SVG coordinate ranges to fit within our -0.7 to 0.7 box
            min_vals = pts.min(axis=0)
            max_vals = pts.max(axis=0)
            center = (min_vals + max_vals) / 2
            scale = max(max_vals - min_vals)
            if scale == 0:
                scale = 1
            pts_normalized = (pts - center) / (scale / 1.4)
            # Invert Y axis because SVGs are top-left-origin, but Cartesian is bottom-left-origin
            pts_normalized[:, 1] = -pts_normalized[:, 1]
            return pts_normalized
            
        except Exception as e:
            print(f"[CRT Error] Failed to parse SVG file: {e}")
            return self.make_circle()

    # ==============================================================================
    # Stroke Font Vector Text Module
    # ==============================================================================
    def make_vector_text(self, text_string, char_scale=0.08, spacing=0.18):
        """Compiles a string of characters into a single continuous vector waveform.
        Each character is loaded from the STROKE_FONT stroke map, scaled, offset
        horizontally, and chained together in one continuous trace."""
        text_string = text_string.upper()
        all_points = []
        
        # Calculate horizontal center offset
        total_width = len(text_string) * spacing
        start_x = -total_width / 2.0 + (spacing / 2.0)
        
        for idx, char in enumerate(text_string):
            stroke_pts = STROKE_FONT.get(char, STROKE_FONT[' '])
            
            # Map character strokes to coordinate offset
            char_pts = []
            for stroke in stroke_pts:
                # Scale bounding box and shift X position
                x = start_x + (idx * spacing) + (stroke[0] * char_scale)
                y = stroke[1] * char_scale * 1.5 # Stretched vertically slightly
                char_pts.append([x, y])
                
            if all_points and char_pts:
                # Rapid travel stroke from end of last letter to start of this letter
                retrace = np.linspace(all_points[-1], char_pts[0], 5)
                all_points.extend(retrace.tolist())
                
            all_points.extend(char_pts)
            
        return np.array(all_points) if all_points else np.zeros((1, 2))

# ==============================================================================
# Standalone CLI Main Menu
# ==============================================================================
def main():
    print("==========================================================")
    print("        ULTIMATE CRT VECTOR ENGINE STANDALONE TEST        ")
    print("==========================================================")
    
    engine = VectorCrtEngine()
    
    if not pyaudio_available:
        print("[Warning] PyAudio is not installed. You cannot output real-time sound waveforms.")
        print("  Install PyAudio via: pip install pyaudio")
        print("Running math validations only...\n")
    else:
        success = engine.start()
        if not success:
            print("[Error] Failed to initialize PyAudio device. Exiting.")
            sys.exit(1)
            
    # Set default shape (circle)
    engine.set_points(engine.make_circle())
    
    # Background animation thread for 3D spinning objects
    animation_active = True
    anim_shape = "circle" # "circle", "spiral", "lissajous", "cube", "pyramid", "text"
    anim_text_str = "READY"
    
    def animation_loop():
        angle_x = 0.0
        angle_y = 0.0
        
        while animation_active:
            if anim_shape == "cube":
                pts = engine.make_rotating_cube(angle_x, angle_y)
                engine.set_points(pts)
            elif anim_shape == "pyramid":
                pts = engine.make_rotating_pyramid(angle_x, angle_y)
                engine.set_points(pts)
            elif anim_shape == "spiral":
                # Static spiral
                pts = engine.make_spiral()
                engine.set_points(pts)
            elif anim_shape == "lissajous":
                # Static lissajous
                pts = engine.make_lissajous()
                engine.set_points(pts)
            elif anim_shape == "circle":
                # Static circle
                pts = engine.make_circle()
                engine.set_points(pts)
            elif anim_shape == "text":
                # Render text
                pts = engine.make_vector_text(anim_text_str)
                engine.set_points(pts)
                
            # Slowly update rotation angles
            angle_x += 0.03
            angle_y += 0.04
            time.sleep(0.02) # ~50 FPS animation updates
            
    anim_thread = threading.Thread(target=animation_loop, daemon=True)
    anim_thread.start()
    
    # --------------------------------------------------------------------------
    # USER MENU SELECTION LOOP
    # --------------------------------------------------------------------------
    try:
        while True:
            print("\n--- Shape Selection Menu ---")
            print("1. Circular Waveform (Perfect Circle)")
            print("2. Spiral Waveform (Grow/Shrink)")
            print("3. Lissajous Knot Figure")
            print("4. Spinning 3D Wireframe Cube")
            print("5. Spinning 3D Wireframe Pyramid")
            print("6. Vector Stroke Font Text")
            print("7. Exit")
            
            choice = input("Select a shape (1-7): ").strip()
            
            if choice == "1":
                print("[Action] Displaying perfect vector circle.")
                anim_shape = "circle"
            elif choice == "2":
                print("[Action] Displaying vector spiral.")
                anim_shape = "spiral"
            elif choice == "3":
                print("[Action] Displaying Lissajous knot (horizontal & vertical frequency ratio 5:6).")
                anim_shape = "lissajous"
            elif choice == "4":
                print("[Action] Drawing spinning 3D Wireframe Cube. Notice the double-traced corners!")
                anim_shape = "cube"
            elif choice == "5":
                print("[Action] Drawing spinning 3D Wireframe Pyramid.")
                anim_shape = "pyramid"
            elif choice == "6":
                txt = input("Enter a message to spell on CRT (A-Z, 0-9, max 12 chars): ").strip()
                if not txt:
                    txt = "HELLO"
                print(f"[Action] Rendering stroke vector text: '{txt}'")
                anim_text_str = txt
                anim_shape = "text"
            elif choice == "7":
                print("Stopping engine. Goodbye!")
                break
            else:
                print("Invalid selection. Please enter 1-7.")
                
    finally:
        animation_active = False
        anim_thread.join(timeout=1.0)
        engine.stop()

if __name__ == "__main__":
    main()

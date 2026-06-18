import os
import sys
import time
import threading
import json
from flask import Flask, render_template, jsonify, request

START_TIME = time.time()

def get_cpu_temp():
    if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp_raw = f.read().strip()
                return round(float(temp_raw) / 1000.0, 1)
        except Exception:
            pass
    return 42.5

def get_ram_usage():
    if os.path.exists("/proc/meminfo"):
        try:
            meminfo = {}
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        meminfo[parts[0].replace(":", "")] = int(parts[1])
            total = meminfo.get("MemTotal", 0)
            free = meminfo.get("MemFree", 0)
            buffers = meminfo.get("Buffers", 0)
            cached = meminfo.get("Cached", 0)
            used = total - (free + buffers + cached)
            if total > 0:
                percentage = round((used / total) * 100.0, 1)
                return {
                    "used_mb": round(used / 1024.0, 1),
                    "total_mb": round(total / 1024.0, 1),
                    "percent": percentage
                }
        except Exception:
            pass
    return {"used_mb": 1024.0, "total_mb": 4096.0, "percent": 25.0}

def get_uptime():
    if os.path.exists("/proc/uptime"):
        try:
            with open("/proc/uptime", "r") as f:
                uptime_seconds = float(f.readline().split()[0])
                hours = int(uptime_seconds // 3600)
                minutes = int((uptime_seconds % 3600) // 60)
                seconds = int(uptime_seconds % 60)
                if hours > 0:
                    return f"{hours}h {minutes}m"
                return f"{minutes}m {seconds}s"
        except Exception:
            pass
    uptime_seconds = time.time() - START_TIME
    hours = int(uptime_seconds // 3600)
    minutes = int((uptime_seconds % 3600) // 60)
    seconds = int(uptime_seconds % 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {seconds}s"

def run_web_portal(daemon, host="0.0.0.0", port=5000):
    # Disable flask logging to keep stdout/stderr clean
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    # Locate templates folder
    template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
    app = Flask(__name__, template_folder=template_dir)
    
    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/status", methods=["GET"])
    def get_status():
        # Read per-sink volumes from PulseAudio (cached asynchronously in daemon)
        sink_volumes = daemon.sink_volumes.to_dict() if hasattr(daemon, "sink_volumes") else {}

        status = {
            "servo": {**daemon.servos.get_state(), "blink_active": daemon.servos.blink_active, "blink_side": daemon.servos.blink_side},
            "sensor": {
                "face_detected": daemon.sensor.face_detected,
                "primary_face": daemon.sensor.get_primary_face(),
                "faces_count": len(daemon.sensor.faces),
                "mock": daemon.sensor.mock,
                "enabled": daemon.config_manager.config.get("face_tracking", {}).get("enabled", True),
                "invert_x": daemon.config_manager.config.get("face_tracking", {}).get("invert_x", False),
                "invert_y": daemon.config_manager.config.get("face_tracking", {}).get("invert_y", False)
            },
            "gpios": daemon.gpio.get_pins_status(),
            "voice": {
                "language": daemon.config_manager.config.get("voice", {}).get("language", "en-US"),
                "tts_provider": daemon.config_manager.config.get("voice", {}).get("tts_provider", "edge-tts"),
                "wake_word": daemon.config_manager.config.get("voice", {}).get("wake_word", "jarvis"),
                "wake_sensitivity": daemon.config_manager.config.get("voice", {}).get("wake_sensitivity", 0.5),
                "auto_language_detection": daemon.config_manager.config.get("voice", {}).get("auto_language_detection", True),
                "audio_output_sink": daemon.config_manager.config.get("voice", {}).get("audio_output_sink", "aec_sink"),
                "sound_reactions": daemon.config_manager.config.get("voice", {}).get("sound_reactions", True),
                "sink_volumes": sink_volumes,
                "listening": daemon.voice_listening_active
            },
            "personality": {
                "extroversion": daemon.config_manager.config.get("personality", {}).get("extroversion", 0.7),
                "mood": daemon.servos.mood,
                "idle_behaviors": daemon.config_manager.config.get("personality", {}).get("idle_behaviors", True)
            },
            "sleep_mode": {
                "enabled": daemon.config_manager.config.get("sleep_mode", {}).get("enabled", False),
                "sleep_time": daemon.config_manager.config.get("sleep_mode", {}).get("sleep_time", "22:00"),
                "wake_time": daemon.config_manager.config.get("sleep_mode", {}).get("wake_time", "07:00"),
                "active": daemon.sleep_active
            },
            "intercom": {
                "active": daemon.intercom_manager.is_recording
            },
            "system_health": {
                "cpu_temp": get_cpu_temp(),
                "ram": get_ram_usage(),
                "uptime": get_uptime()
            },
            "alarms": daemon.config_manager.config.get("alarms", []),
            "logs": list(daemon.logs),
            "games": {
                "active_game": daemon.games.active_game,
                "score": daemon.games.score,
                "round": daemon.games.round_num
            }
        }
        return jsonify(status)

    @app.route("/api/config", methods=["GET"])
    def get_config():
        """Returns servo configuration for dashboard calibration fields."""
        config = daemon.config_manager.config
        return jsonify({
            "servos": config.get("servos", {}),
            "face_tracking": config.get("face_tracking", {}),
            "personality": config.get("personality", {}),
            "mock": config.get("mock", True)
        })

    @app.route("/api/settings", methods=["POST"])
    def update_settings():
        """Updates and persists global parameters from the UI."""
        data = request.json or {}
        config = daemon.config_manager.config
        
        # Mock mode
        if "mock" in data:
            config["mock"] = bool(data["mock"])
            daemon.servos.mock = config["mock"]
            daemon.sensor.mock = config["mock"]
            daemon.gpio.mock = config["mock"]
            daemon.log(f"[Portal] Global Mock Mode toggled: {config['mock']}")
            
        # Face tracking enable
        if "face_tracking_enabled" in data:
            if "face_tracking" not in config:
                config["face_tracking"] = {}
            config["face_tracking"]["enabled"] = bool(data["face_tracking_enabled"])
            daemon.log(f"[Portal] Face Tracking enabled: {config['face_tracking']['enabled']}")
            
        # Invert X/Y
        if "invert_x" in data:
            config["face_tracking"]["invert_x"] = bool(data["invert_x"])
        if "invert_y" in data:
            config["face_tracking"]["invert_y"] = bool(data["invert_y"])
            
        # Voice Settings
        if "language" in data:
            if "voice" not in config:
                config["voice"] = {}
            config["voice"]["language"] = str(data["language"])
            daemon.log(f"[Portal] Voice Language set to: {data['language']}")
        if "auto_language_detection" in data:
            if "voice" not in config:
                config["voice"] = {}
            config["voice"]["auto_language_detection"] = bool(data["auto_language_detection"])
            daemon.log(f"[Portal] Auto Language Detection set to: {data['auto_language_detection']}")
        if "audio_output_sink" in data:
            if "voice" not in config:
                config["voice"] = {}
            sink_name = str(data["audio_output_sink"])
            import re
            if re.match(r'^[a-zA-Z0-9_\-\.]+$', sink_name):
                config["voice"]["audio_output_sink"] = sink_name
                daemon.log(f"[Portal] Setting default PulseAudio output sink to: {sink_name}")
                if not sys.platform.startswith("win"):
                    import subprocess
                    subprocess.run(["pactl", "set-default-sink", sink_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                daemon.log(f"[Portal Error] Rejected invalid PulseAudio output sink: {sink_name}")
        if "wake_word" in data:
            config["voice"]["wake_word"] = str(data["wake_word"]).lower()
            daemon.log(f"[Portal] Wake Word set to: {data['wake_word']}")
        if "wake_sensitivity" in data:
            config["voice"]["wake_sensitivity"] = float(data["wake_sensitivity"])
            
        # Extroversion
        if "extroversion" in data:
            if "personality" not in config:
                config["personality"] = {}
            config["personality"]["extroversion"] = float(data["extroversion"])
            daemon.servos.extroversion = float(data["extroversion"])
            daemon.log(f"[Portal] Extroversion level set to: {data['extroversion']}")
            
        # Idle behaviors
        if "idle_behaviors" in data:
            if "personality" not in config:
                config["personality"] = {}
            config["personality"]["idle_behaviors"] = bool(data["idle_behaviors"])
            daemon.log(f"[Portal] Idle Look-Around Behaviors set to: {config['personality']['idle_behaviors']}")
            
        # Sound reactions
        if "sound_reactions" in data:
            if "voice" not in config:
                config["voice"] = {}
            config["voice"]["sound_reactions"] = bool(data["sound_reactions"])
            daemon.log(f"[Portal] Ambient Sound Reactions set to: {config['voice']['sound_reactions']}")
            
        # Sleep Mode Schedule
        if "sleep_mode_enabled" in data:
            if "sleep_mode" not in config:
                config["sleep_mode"] = {}
            config["sleep_mode"]["enabled"] = bool(data["sleep_mode_enabled"])
            daemon.log(f"[Portal] Sleep Mode Schedule enabled: {config['sleep_mode']['enabled']}")
            if config["sleep_mode"]["enabled"]:
                from datetime import datetime
                current_time = datetime.now().strftime("%H:%M")
                if daemon.is_time_between(current_time, config["sleep_mode"].get("sleep_time", "22:00"), config["sleep_mode"].get("wake_time", "07:00")):
                    daemon.sleep_active = True
                    daemon.wake_detector.pause()
                    daemon.servos.close_eyes()
                else:
                    daemon.sleep_active = False
                    daemon.servos.open_eyes()
                    daemon.wake_detector.resume()
            else:
                daemon.sleep_active = False
                daemon.servos.open_eyes()
                daemon.wake_detector.resume()
                
        if "sleep_time" in data:
            if "sleep_mode" not in config:
                config["sleep_mode"] = {}
            config["sleep_mode"]["sleep_time"] = str(data["sleep_time"])
            daemon.log(f"[Portal] Sleep Mode start time set to: {data['sleep_time']}")
            
        if "wake_time" in data:
            if "sleep_mode" not in config:
                config["sleep_mode"] = {}
            config["sleep_mode"]["wake_time"] = str(data["wake_time"])
            daemon.log(f"[Portal] Sleep Mode wake time set to: {data['wake_time']}")

        # Mood
        if "mood" in data:
            mood = str(data["mood"]).lower()
            if mood in ["neutral", "happy", "sad", "angry", "bored", "excited", "surprised"]:
                daemon.servos.mood = mood
                daemon.servos.manual_override = False
                config["personality"]["mood"] = mood
                daemon.log(f"[Portal] Mood manually set to: {mood}")
                 
        daemon.config_manager.save_config()
        return jsonify({"success": True})

    @app.route("/api/servo/manual", methods=["POST"])
    def manual_servo():
        """Allows direct manual slider control from calibration dashboard."""
        data = request.json or {}
        name = data.get("name")
        angle = data.get("angle")
        override = data.get("override", True)
        
        daemon.servos.manual_override = bool(override)
        if name and angle is not None:
            daemon.servos.set_target(name, float(angle))
            # Directly snap current pos on manual override for instant calibration visual response
            daemon.servos.current_pos[name] = float(angle)
            return jsonify({"success": True})
        return jsonify({"success": False, "error": "Missing params"})

    @app.route("/api/servo/calibrate", methods=["POST"])
    def calibrate_servo():
        """Saves dynamic trim, limits, or pin mappings permanently."""
        data = request.json or {}
        name = data.get("name")
        trim = data.get("trim")
        min_ang = data.get("min_angle")
        max_ang = data.get("max_angle")
        pin = data.get("pin")
        close_ang = data.get("close_angle")
        
        if not name:
            return jsonify({"success": False, "error": "No servo name"})
            
        config = daemon.config_manager.config
        if "servos" not in config:
            config["servos"] = {}
        if name not in config["servos"]:
            config["servos"][name] = {}
            
        srv = config["servos"][name]
        
        if trim is not None and trim != "":
            srv["trim"] = float(trim)
        if min_ang is not None and min_ang != "":
            srv["min_angle"] = float(min_ang)
        if max_ang is not None and max_ang != "":
            srv["max_angle"] = float(max_ang)
        if pin is not None and pin != "":
            srv["pin"] = int(pin)
        if close_ang is not None and close_ang != "":
            srv["close_angle"] = float(close_ang)
            
        daemon.config_manager.save_config()
        daemon.log(f"[Portal] Saved calibration parameters for: {name}")
        return jsonify({"success": True})

    @app.route("/api/calibration/start", methods=["POST"])
    def start_calibration():
        """Starts slow servo sweep calibration to autodetect limits."""
        data = request.json or {}
        name = data.get("servo")
        direction = data.get("direction") # "min" or "max"
        
        if not name or direction not in ["min", "max"]:
            return jsonify({"success": False, "error": "Missing or invalid parameters"})
            
        success = daemon.servos.start_calibration_sweep(name, direction)
        return jsonify({"success": success})

    @app.route("/api/calibration/stop", methods=["POST"])
    def stop_calibration():
        """Stops servo sweep calibration and automatically saves the limit angle."""
        data = request.json or {}
        name = data.get("servo")
        direction = data.get("direction") # "min" or "max"
        save_as = data.get("save_as") # Optional: "min", "max", "close"
        
        if not name:
            return jsonify({"success": False, "error": "Missing servo parameter"})
            
        if not save_as:
            if direction not in ["min", "max"]:
                return jsonify({"success": False, "error": "Missing direction or save_as parameter"})
            save_as = "min" if direction == "min" else "max"
            
        if save_as not in ["min", "max", "close"]:
            return jsonify({"success": False, "error": "Invalid save_as value"})
            
        final_angle = daemon.servos.stop_calibration_sweep()
        rounded_angle = round(final_angle, 1)
        
        # Save to config.json
        config = daemon.config_manager.config
        if "servos" not in config:
            config["servos"] = {}
        if name not in config["servos"]:
            config["servos"][name] = {}
            
        srv = config["servos"][name]
        
        if save_as == "close":
            srv["close_angle"] = rounded_angle
        else:
            srv[f"{save_as}_angle"] = rounded_angle
            
        daemon.config_manager.save_config()
        daemon.log(f"[Calibration] Saved autodetected {save_as}_angle for {name}: {rounded_angle}")
        
        return jsonify({
            "success": True, 
            "servo": name, 
            "save_as": save_as, 
            "angle": rounded_angle
        })

    @app.route("/api/face/mock", methods=["POST"])
    def mock_face():
        """Receives click-and-drag coordinates from portal grid to mock tracking on Windows."""
        data = request.json or {}
        x = data.get("x", 128)
        y = data.get("y", 128)
        active = data.get("active", True)
        
        # Inject mock face into sensor loop
        daemon.sensor.set_mock_face(x, y, detected=active)
        return jsonify({"success": True})

    @app.route("/api/gpio/toggle", methods=["POST"])
    def toggle_gpio():
        """Direct portal button toggle for lights/relays."""
        data = request.json or {}
        pin = data.get("pin")
        state = data.get("state")
        
        if pin is not None and state is not None:
            success = daemon.gpio.set_pin_state(int(pin), bool(state))
            return jsonify({"success": success})
        return jsonify({"success": False, "error": "Missing params"})

    @app.route("/api/alarm/add", methods=["POST"])
    def add_alarm():
        """Adds a scheduled alarm."""
        data = request.json or {}
        alarm_id = data.get("id")
        time_str = data.get("time") # "HH:MM"
        task = data.get("task")
        recurring = data.get("recurring", True)
        
        if not alarm_id or not time_str or not task:
            return jsonify({"success": False, "error": "Missing params"})
            
        config = daemon.config_manager.config
        if "alarms" not in config:
            config["alarms"] = []
            
        # Remove existing if same ID
        config["alarms"] = [a for a in config["alarms"] if a.get("id") != alarm_id]
        
        new_alarm = {
            "id": alarm_id,
            "time": time_str,
            "recurring": bool(recurring),
            "enabled": True,
            "task": task,
            "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        }
        config["alarms"].append(new_alarm)
        daemon.config_manager.save_config()
        daemon.log(f"[Portal] Added scheduled alarm: {alarm_id} at {time_str}")
        return jsonify({"success": True})

    @app.route("/api/alarm/delete", methods=["POST"])
    def delete_alarm():
        """Removes an alarm."""
        data = request.json or {}
        alarm_id = data.get("id")
        
        if not alarm_id:
            return jsonify({"success": False, "error": "Missing ID"})
            
        config = daemon.config_manager.config
        if "alarms" in config:
            config["alarms"] = [a for a in config["alarms"] if a.get("id") != alarm_id]
            daemon.config_manager.save_config()
            daemon.log(f"[Portal] Deleted scheduled alarm: {alarm_id}")
            return jsonify({"success": True})
        return jsonify({"success": False})

    @app.route("/api/volume", methods=["POST"])
    def set_volume():
        """Sets the volume of a specific PulseAudio sink."""
        data = request.json or {}
        sink = data.get("sink")
        volume = data.get("volume")
        
        if not sink or volume is None:
            return jsonify({"success": False, "error": "Missing sink or volume"})
        
        volume = max(0, min(150, int(volume)))  # Clamp 0-150%
        daemon.log(f"[Portal] Setting volume for '{sink}' to {volume}%")
        
        if not sys.platform.startswith("win"):
            import re
            if not re.match(r'^[a-zA-Z0-9_\-\.]+$', sink):
                return jsonify({"success": False, "error": "Invalid sink name"})
            import subprocess
            result = subprocess.run(
                ["pactl", "set-sink-volume", sink, f"{volume}%"],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                daemon.log(f"[Portal] pactl set-sink-volume failed: {result.stderr.strip()}")
                return jsonify({"success": False, "error": result.stderr.strip()})
        
        return jsonify({"success": True, "sink": sink, "volume": volume})

    @app.route("/api/voice/trigger", methods=["POST"])
    def trigger_voice():
        """Simulates wake word trigger from UI."""
        daemon.on_wake_trigger()
        return jsonify({"success": True})

    @app.route("/api/voice/noise", methods=["POST"])
    def trigger_noise():
        """Simulates a sudden loud noise trigger from UI."""
        daemon.trigger_audio_reactive_snap(volume=4500.0)
        return jsonify({"success": True})

    @app.route("/api/system/update", methods=["POST"])
    def system_update():
        """Pull latest codebase changes from Git remote."""
        import subprocess
        daemon.log("[System] OTA Update triggered. Pulling code from git...")
        try:
            res = subprocess.run(["git", "pull"], capture_output=True, text=True, timeout=15.0)
            out = res.stdout + "\n" + res.stderr
            daemon.log(f"[System] Git pull result:\n{out}")
            return jsonify({"success": res.returncode == 0, "output": out})
        except Exception as e:
            daemon.log(f"[System Error] Git pull failed: {e}")
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/system/restart", methods=["POST"])
    def system_restart():
        """Restart Python daemon process."""
        daemon.log("[System] Restart request received from portal.")
        def do_restart():
            time.sleep(1.0)
            os.execv(sys.executable, [sys.executable] + sys.argv)
        threading.Thread(target=do_restart, daemon=True).start()
        return jsonify({"success": True, "message": "Restarting daemon..."})

    @app.route("/api/intercom/toggle", methods=["POST"])
    def intercom_toggle():
        """Activate/deactivate intercom mode (two-way audio streaming)."""
        data = request.json or {}
        active = bool(data.get("active", False))
        if active:
            daemon.intercom_manager.start()
        else:
            daemon.intercom_manager.stop()
        return jsonify({"success": True, "active": daemon.intercom_manager.is_recording})

    @app.route("/api/intercom/play", methods=["POST"])
    def intercom_play():
        """Play browser audio chunk on Pi speaker."""
        if 'file' in request.files:
            file = request.files['file']
            import tempfile
            import subprocess
            temp_dir = tempfile.gettempdir()
            temp_path = os.path.join(temp_dir, "intercom_play.wav")
            file.save(temp_path)
            
            if not sys.platform.startswith("win"):
                def run_play():
                    subprocess.run(["paplay", temp_path])
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
                threading.Thread(target=run_play, daemon=True).start()
            else:
                daemon.log("[Intercom Mock] Playing received browser audio chunk on Windows.")
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            return jsonify({"success": True})
        return jsonify({"success": False, "error": "No file provided"})

    @app.route("/api/intercom/receive", methods=["GET"])
    def intercom_receive():
        """Send recorded microphone buffer to dashboard as base64 WAV."""
        audio_bytes = daemon.intercom_manager.get_audio()
        if not audio_bytes:
            return jsonify({"audio": None})
            
        import io
        import wave
        import base64
        
        wav_buf = io.BytesIO()
        try:
            with wave.open(wav_buf, 'wb') as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(audio_bytes)
            b64_wav = base64.b64encode(wav_buf.getvalue()).decode('utf-8')
            return jsonify({"audio": b64_wav})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/voice/joke", methods=["POST"])
    def voice_joke():
        """Triggers a random verbal joke from the companion."""
        jokes = [
            "Why don't scientists trust atoms? Because they make up everything!",
            "What do you call a fake noodle? An impasta!",
            "Why did the scarecrow win an award? Because he was outstanding in his field!",
            "Why don't skeletons fight each other? They don't have the guts!",
            "What do you call a sleeping dinosaur? A dino-snore!",
            "Why did the bicycle fall over? Because it was two-tired!",
            "What do you call a cheese that isn't yours? Nacho cheese!",
            "How do you organize a space party? You planet!"
        ]
        import random
        joke = random.choice(jokes)
        daemon.log(f"[Entertainment] Telling joke: {joke}")
        daemon.servos.play_gesture("idle_giggle")
        daemon.tts.speak(joke)
        return jsonify({"success": True, "joke": joke})

    @app.route("/api/voice/fact", methods=["POST"])
    def voice_fact():
        """Triggers a random verbal fun fact from the companion."""
        facts = [
            "Honey never spoils. You could theoretically eat 3000-year-old honey!",
            "Bananas are berries, but strawberries aren't!",
            "Wombat poop is cube-shaped, which stops it from rolling away!",
            "There are more trees on Earth than stars in the Milky Way galaxy!",
            "Cows have best friends and get stressed when they are separated!",
            "Octopus has three hearts and blue blood!",
            "A day on Venus is longer than a year on Venus!",
            "Sea otters hold hands when they sleep so they don't drift apart!"
        ]
        import random
        fact = random.choice(facts)
        daemon.log(f"[Entertainment] Telling fun fact: {fact}")
        daemon.servos.play_gesture("idle_curious_scan")
        daemon.tts.speak("Did you know? " + fact)
        return jsonify({"success": True, "fact": fact})

    @app.route("/api/games/start", methods=["POST"])
    def start_game():
        data = request.json or {}
        game_name = data.get("game")
        if not game_name:
            return jsonify({"success": False, "error": "Missing game name"})
        lang = daemon.config_manager.config.get("voice", {}).get("language", "bn-IN")
        daemon.voice_listening_active = True
        prompt = daemon.games.start_game(game_name, lang)
        daemon.active_game = game_name
        return jsonify({"success": True, "prompt": prompt})

    @app.route("/api/games/stop", methods=["POST"])
    def stop_game():
        lang = daemon.config_manager.config.get("voice", {}).get("language", "bn-IN")
        prompt = daemon.games.stop_game(lang)
        daemon.active_game = None
        return jsonify({"success": True, "prompt": prompt})

    @app.route("/api/games/status", methods=["GET"])
    def get_game_status():
        return jsonify({
            "active_game": daemon.games.active_game,
            "score": daemon.games.score,
            "round": daemon.games.round_num
        })

    @app.route("/api/servo/gesture", methods=["POST"])
    def trigger_gesture():
        """Direct portal button gesture execution."""
        data = request.json or {}
        gesture = data.get("gesture")
        if gesture:
            success = daemon.servos.play_gesture(gesture)
            return jsonify({"success": success})
        return jsonify({"success": False, "error": "Missing gesture param"})

    @app.route("/api/voice/send", methods=["POST"])
    def send_text_direct():
        """Sends a text question directly, speaking response (useful for text-only mock testing)."""
        data = request.json or {}
        text = data.get("text", "")
        if not text:
            return jsonify({"success": False, "error": "Empty text"})
            
        with daemon.voice_flow_lock:
            if daemon.voice_listening_active:
                return jsonify({"success": False, "error": "Voice session is already active"})
            daemon.voice_listening_active = True
            
        def run_flow():
            try:
                daemon.log(f"[Console Input] User typed: \"{text}\"")
                daemon.servos.mood = "excited"
                
                # Query ZeroClaw
                daemon.log("[Console Input] Querying ZeroClaw agent...")
                agent_reply = daemon.brain.send_message(text)
                daemon.log(f"[Console Input] Agent reply: \"{agent_reply}\"")
                
                # Parse tags
                mood_tag = "neutral"
                import re
                expr_match = re.search(r'\[expression:\s*(\w+)\]', agent_reply)
                if expr_match:
                    mood_tag = expr_match.group(1).lower()
                    
                tool_matches = re.findall(r'\[tool:\s*toggle_gpio:\s*(\d+):\s*(\w+)\]', agent_reply)
                for match in tool_matches:
                    pin_num = int(match[0])
                    pin_state = match[1].lower() == "on"
                    daemon.gpio.set_pin_state(pin_num, pin_state)
                    daemon.servos.play_gesture("nod") # nod to confirm GPIO action
                    
                if mood_tag == "wink":
                    daemon.servos.trigger_wink()
                elif mood_tag == "blink":
                    daemon.servos.trigger_blink()
                elif mood_tag in ["nod", "shake", "think", "shock", "scanning"]:
                    daemon.servos.play_gesture(mood_tag)
                elif mood_tag in ["happy", "sad", "angry", "surprised", "bored", "excited", "neutral"]:
                    daemon.servos.mood = mood_tag
                    
                # Speak
                daemon.tts.speak(agent_reply)
                
                # Wait
                word_cnt = len(agent_reply.split())
                read_dur = max(3.0, (word_cnt / 150.0) * 60.0)
                time.sleep(read_dur)
                daemon.servos.mood = "neutral"
            finally:
                with daemon.voice_flow_lock:
                    daemon.voice_listening_active = False

        threading.Thread(target=run_flow, daemon=True).start()
        return jsonify({"success": True})

    @app.route("/api/mcp/execute", methods=["POST"])
    def mcp_execute():
        """Endpoint called by the stdio MCP server process to execute tools on the active daemon."""
        data = request.json or {}
        tool = data.get("tool")
        args = data.get("arguments", {})
        
        daemon.log(f"[MCP RPC] Invoking tool '{tool}' with args: {args}")
        
        try:
            if tool == "set_eye_mood":
                mood = args.get("mood", "neutral")
                if mood in ["neutral", "happy", "sad", "angry", "bored", "excited", "surprised"]:
                    daemon.servos.mood = mood
                    daemon.config_manager.config["personality"]["mood"] = mood
                    daemon.config_manager.save_config()
                    return jsonify({"success": True, "message": f"Mood set to {mood}."})
                return jsonify({"success": False, "error": "Invalid mood."})
                
            elif tool == "trigger_expression":
                expr = args.get("expression")
                if expr == "blink":
                    daemon.servos.trigger_blink()
                elif expr == "wink_left":
                    daemon.servos.trigger_wink("left")
                elif expr == "wink_right":
                    daemon.servos.trigger_wink("right")
                elif expr == "close_eyes":
                    daemon.servos.close_eyes()
                elif expr == "open_eyes":
                    daemon.servos.open_eyes()
                else:
                    return jsonify({"success": False, "error": "Invalid expression."})
                return jsonify({"success": True, "message": f"Triggered expression '{expr}'."})
                
            elif tool == "play_gesture":
                gesture = args.get("gesture")
                if gesture in ["startup", "nod", "shake", "think", "shock", "scanning"]:
                    success = daemon.servos.play_gesture(gesture)
                    if success:
                        return jsonify({"success": True, "message": f"Triggered gesture '{gesture}'."})
                    return jsonify({"success": False, "error": "Gesture system is busy."})
                return jsonify({"success": False, "error": "Invalid gesture name."})
                
            elif tool == "toggle_gpio":
                pin = int(args.get("pin"))
                state = args.get("state", "off").lower() == "on"
                success = daemon.gpio.set_pin_state(pin, state)
                if success:
                    return jsonify({"success": True, "message": f"Pin {pin} turned {'ON' if state else 'OFF'}."})
                return jsonify({"success": False, "error": "GPIO write failed."})
                
            elif tool == "set_alarm":
                alarm_id = args.get("id")
                time_str = args.get("time")
                task = args.get("task")
                
                config = daemon.config_manager.config
                if "alarms" not in config:
                    config["alarms"] = []
                # Clean duplicate
                config["alarms"] = [a for a in config["alarms"] if a.get("id") != alarm_id]
                
                config["alarms"].append({
                    "id": alarm_id,
                    "time": time_str,
                    "recurring": True,
                    "enabled": True,
                    "task": task,
                    "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                })
                daemon.config_manager.save_config()
                return jsonify({"success": True, "message": f"Alarm '{alarm_id}' set at {time_str}."})
                
            elif tool == "get_status":
                status_info = (
                    f"Mood: {daemon.servos.mood}\n"
                    f"Face Tracking: {'Active' if daemon.servos.face_tracking_active else 'Inactive'}\n"
                    f"Mock Mode: {daemon.servos.mock}\n"
                    f"GPIO Status: {json.dumps(daemon.gpio.get_pins_status())}\n"
                    f"Alarms: {len(daemon.config_manager.config.get('alarms', []))} set."
                )
                return jsonify({"success": True, "message": status_info})
                
        except Exception as ex:
            return jsonify({"success": False, "error": f"Exception in tool execution: {ex}"})
            
        return jsonify({"success": False, "error": f"Tool '{tool}' not implemented."})

    @app.route("/api/debug-skills", methods=["GET"])
    def debug_skills():
        """Lists files, permissions, and runs debug commands for claw-eye skill."""
        import subprocess
        import stat
        result = {}
        
        # 1. Check folder existence and list files
        paths = [
            "/home/pi/open-skills/skills/claw-eye",
            "/home/pi/.zeroclaw/workspace/skills/claw-eye"
        ]
        
        result["directories"] = {}
        for path in paths:
            if os.path.exists(path):
                files = []
                try:
                    for f in os.listdir(path):
                        fpath = os.path.join(path, f)
                        st = os.stat(fpath)
                        is_sym = os.path.islink(fpath)
                        files.append({
                            "name": f,
                            "size": st.st_size,
                            "mode": oct(st.st_mode),
                            "owner": st.st_uid,
                            "group": st.st_gid,
                            "is_symlink": is_sym,
                            "target": os.readlink(fpath) if is_sym else None
                        })
                except Exception as e:
                    files = {"error": str(e)}
                result["directories"][path] = {
                    "exists": True,
                    "files": files
                }
            else:
                result["directories"][path] = {
                    "exists": False
                }
                
        # 2. Try executing some zeroclaw debug commands
        result["commands"] = {}
        cmds = [
            ["zeroclaw", "--version"]
        ]
        for cmd in cmds:
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=5.0)
                result["commands"][" ".join(cmd)] = {
                    "stdout": res.stdout[:2000],
                    "stderr": res.stderr[:2000],
                    "code": res.returncode
                }
            except Exception as e:
                result["commands"][" ".join(cmd)] = {"error": str(e)}
                
        # 3. Read skill contents
        result["contents"] = {}
        for path in paths:
            toml_path = os.path.join(path, "SKILL.toml")
            md_path = os.path.join(path, "SKILL.md")
            if os.path.exists(toml_path):
                try:
                    with open(toml_path, "r") as f:
                        result["contents"][toml_path] = f.read()
                except Exception as e:
                    result["contents"][toml_path] = f"error: {e}"
            if os.path.exists(md_path):
                try:
                    with open(md_path, "r") as f:
                        result["contents"][md_path] = f.read()
                except Exception as e:
                    result["contents"][md_path] = f"error: {e}"
                    
        return jsonify(result)

    # Run the web server in a separate thread so it doesn't block the caller
    web_thread = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
        name="FlaskWebPortalThread"
    )
    web_thread.daemon = True
    web_thread.start()
    daemon.log(f"[Web Portal] Server successfully launched on http://{host}:{port}")

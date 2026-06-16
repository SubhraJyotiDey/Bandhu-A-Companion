import os
import sys
import time
import threading
from flask import Flask, render_template, jsonify, request

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
        """Aggregates all subsystem states for real-time frontend visualization."""
        status = {
            "servo": {**daemon.servos.get_state(), "blink_active": daemon.servos.blink_active, "blink_side": daemon.servos.blink_side},
            "sensor": {
                "face_detected": daemon.sensor.face_detected,
                "primary_face": daemon.sensor.get_primary_face(),
                "faces_count": len(daemon.sensor.faces),
                "mock": daemon.sensor.mock
            },
            "gpios": daemon.gpio.get_pins_status(),
            "voice": {
                "language": daemon.config_manager.config.get("voice", {}).get("language", "en-US"),
                "tts_provider": daemon.config_manager.config.get("voice", {}).get("tts_provider", "edge-tts"),
                "wake_word": daemon.config_manager.config.get("voice", {}).get("wake_word", "jarvis"),
                "wake_sensitivity": daemon.config_manager.config.get("voice", {}).get("wake_sensitivity", 0.5),
                "listening": daemon.voice_listening_active
            },
            "alarms": daemon.config_manager.config.get("alarms", []),
            "logs": list(daemon.logs)
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
            
        # Mood
        if "mood" in data:
            mood = str(data["mood"]).lower()
            if mood in ["neutral", "happy", "sad", "angry", "bored", "excited", "surprised"]:
                daemon.servos.mood = mood
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
        
        if not name:
            return jsonify({"success": False, "error": "No servo name"})
            
        config = daemon.config_manager.config
        if "servos" not in config:
            config["servos"] = {}
        if name not in config["servos"]:
            config["servos"][name] = {}
            
        srv = config["servos"][name]
        
        if trim is not None:
            srv["trim"] = float(trim)
        if min_ang is not None:
            srv["min_angle"] = float(min_ang)
        if max_ang is not None:
            srv["max_angle"] = float(max_ang)
        if pin is not None:
            srv["pin"] = int(pin)
            
        daemon.config_manager.save_config()
        daemon.log(f"[Portal] Saved calibration parameters for: {name}")
        return jsonify({"success": True})

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

    @app.route("/api/voice/trigger", methods=["POST"])
    def trigger_voice():
        """Simulates wake word trigger from UI."""
        daemon.on_wake_trigger()
        return jsonify({"success": True})

    @app.route("/api/voice/send", methods=["POST"])
    def send_text_direct():
        """Sends a text question directly, speaking response (useful for text-only mock testing)."""
        data = request.json or {}
        text = data.get("text", "")
        if not text:
            return jsonify({"success": False, "error": "Empty text"})
            
        def run_flow():
            daemon.voice_listening_active = True
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
                
            if mood_tag == "wink":
                daemon.servos.trigger_wink()
            elif mood_tag == "blink":
                daemon.servos.trigger_blink()
            elif mood_tag in ["happy", "sad", "angry", "surprised", "bored", "excited", "neutral"]:
                daemon.servos.mood = mood_tag
                
            # Speak
            daemon.tts.speak(agent_reply)
            
            # Wait
            word_cnt = len(agent_reply.split())
            read_dur = max(3.0, (word_cnt / 150.0) * 60.0)
            time.sleep(read_dur)
            daemon.servos.mood = "neutral"
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
                else:
                    return jsonify({"success": False, "error": "Invalid expression."})
                return jsonify({"success": True, "message": f"Triggered expression '{expr}'."})
                
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

    # Run the web server in a separate thread so it doesn't block the caller
    web_thread = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
        name="FlaskWebPortalThread"
    )
    web_thread.daemon = True
    web_thread.start()
    daemon.log(f"[Web Portal] Server successfully launched on http://{host}:{port}")

import sys
import json
import urllib.request
import urllib.error

# Write logs to stderr so they appear in ZeroClaw logs without interfering with stdio protocol messages
def log_err(message):
    sys.stderr.write(f"[Companion MCP] {message}\n")
    sys.stderr.flush()

def query_daemon(endpoint, data=None):
    """Sends a local HTTP POST request to the running Flask daemon to execute actions."""
    url = f"http://127.0.0.1:5000/api/mcp/{endpoint}"
    payload = json.dumps(data or {}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=2.0) as res:
            res_body = res.read().decode("utf-8")
            return json.loads(res_body)
    except urllib.error.URLError as e:
        log_err(f"Daemon connection failed: {e}")
        return {"success": False, "error": "Companion daemon is offline. Run 'python main.py run' to start it."}
    except Exception as e:
        log_err(f"Error querying daemon: {e}")
        return {"success": False, "error": str(e)}

def handle_request(req):
    method = req.get("method")
    req_id = req.get("id")
    
    # 1. Handle initialization handshake
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "claw-eye-mcp",
                    "version": "1.0.0"
                }
            }
        }
        
    elif method == "initialized" or method == "notifications/initialized":
        # Notification needs no response
        return None
        
    # 2. List available tools to the AI
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "set_eye_mood",
                        "description": "Adjust the overall emotional mood of the robot companion's mechanical eyes. This changes gaze velocity, look frequency, and baseline eyelid openings (angry, sad, excited, etc.).",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "mood": {
                                    "type": "string",
                                    "enum": ["neutral", "happy", "sad", "angry", "bored", "excited", "surprised"],
                                    "description": "The target emotional state."
                                }
                            },
                            "required": ["mood"]
                        }
                    },
                    {
                        "name": "trigger_expression",
                        "description": "Trigger an instantaneous, direct eye expression (blink, left wink, right wink) for emotional punctuation.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "expression": {
                                    "type": "string",
                                    "enum": ["blink", "wink_left", "wink_right", "close_eyes", "open_eyes"],
                                    "description": "The action to perform."
                                }
                            },
                            "required": ["expression"]
                        }
                    },
                    {
                        "name": "play_gesture",
                        "description": "Trigger a predefined eye gesture sequence (startup, nod, shake, think, shock, scanning) on the companion's mechanical eyes.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "gesture": {
                                    "type": "string",
                                    "enum": ["startup", "nod", "shake", "think", "shock", "scanning"],
                                    "description": "The gesture sequence to play."
                                }
                            },
                            "required": ["gesture"]
                        }
                    },
                    {
                        "name": "toggle_gpio",
                        "description": "Turn a physical Raspberry Pi GPIO output pin ON (high) or OFF (low). Use this to control relays, lights, appliances, or status LEDs.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "pin": {
                                    "type": "integer",
                                    "description": "The GPIO pin number on the Pi."
                                },
                                "state": {
                                    "type": "string",
                                    "enum": ["on", "off"],
                                    "description": "The desired pin state."
                                }
                            },
                            "required": ["pin", "state"]
                        }
                    },
                    {
                        "name": "set_alarm",
                        "description": "Schedule a daily recurring or one-shot alert alarm. The companion will run a task (such as speaking a sentence or triggering a GPIO output) at the scheduled 24h time.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": "A unique identifier key for the alarm (e.g. 'morning_wake')."
                                },
                                "time": {
                                    "type": "string",
                                    "description": "Time of day in 24h format HH:MM (e.g., '08:30')."
                                },
                                "task": {
                                    "type": "string",
                                    "description": "The action to run. Format is 'say: <message>' or 'toggle_gpio: <pin>:<state>'."
                                }
                            },
                            "required": ["id", "time", "task"]
                        }
                    },
                    {
                        "name": "get_status",
                        "description": "Get current status, calibration logs, GPIO pin states, and alarm lists.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {}
                        }
                    },
                    {
                        "name": "start_game",
                        "description": "Start an interactive vocal game on the companion robot.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "game": {
                                    "type": "string",
                                    "enum": ["riddle", "word_chain", "guess_number"],
                                    "description": "The name of the game to start: 'riddle' (ধাঁধা), 'word_chain' (শব্দ-শৃঙ্খল), or 'guess_number' (সংখ্যা খোঁজার খেলা)."
                                }
                            },
                            "required": ["game"]
                        }
                    },
                    {
                        "name": "stop_game",
                        "description": "Stop the currently active vocal game.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {}
                        }
                    },
                    {
                        "name": "get_game_status",
                        "description": "Retrieve the current game status, including active game name, current round, and user's score.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {}
                        }
                    },
                    {
                        "name": "submit_game_input",
                        "description": "Submit user's speech input/answer to the active vocal game for validation and scoring. Returns the game response message.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "user_input": {
                                    "type": "string",
                                    "description": "The transcribed speech text spoken by the user."
                                }
                            },
                            "required": ["user_input"]
                        }
                    },
                    {
                        "name": "crt_draw_shape",
                        "description": "Momentarily draw a vector shape (cube, pyramid, circle, spiral, lissajous) on the physical CRT monitor deflection coils.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "shape": {
                                    "type": "string",
                                    "enum": ["cube", "pyramid", "circle", "spiral", "lissajous"],
                                    "description": "The vector shape to render."
                                },
                                "duration": {
                                    "type": "number",
                                    "description": "Display duration in seconds (default is 4.0)."
                                }
                            },
                            "required": ["shape"]
                        }
                    },
                    {
                        "name": "crt_draw_text",
                        "description": "Momentarily draw uppercase alphanumeric vector text on the physical CRT monitor deflection coils.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "text": {
                                    "type": "string",
                                    "description": "The text message to spell out in vector stroke lines."
                                },
                                "duration": {
                                    "type": "number",
                                    "description": "Display duration in seconds (default is 4.0)."
                                }
                            },
                            "required": ["text"]
                        }
                    }
                ]
            }
        }
        
    # 3. Call tool executions
    elif method == "tools/call":
        params = req.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        
        log_err(f"Executing tool call: {tool_name} with arguments: {arguments}")
        
        # Route tool call to companion daemon
        daemon_res = query_daemon("execute", {"tool": tool_name, "arguments": arguments})
        
        if daemon_res.get("success", False):
            msg = daemon_res.get("message", "Tool executed successfully.")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": msg
                        }
                    ]
                }
            }
        else:
            err_msg = daemon_res.get("error", "Failed to execute tool.")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32603,
                    "message": err_msg
                }
            }
            
    # Default JSON-RPC method not found response
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {
            "code": -32601,
            "message": f"Method '{method}' not found."
        }
    }

def run_mcp_server():
    log_err("Ready and listening on stdin for JSON-RPC packets...")
    
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break # stdin closed
                
            line = line.strip()
            if not line:
                continue
                
            req = json.loads(line)
            res = handle_request(req)
            
            if res:
                # Write back response as single line on stdout
                sys.stdout.write(json.dumps(res) + "\n")
                sys.stdout.flush()
        except KeyboardInterrupt:
            break
        except Exception as e:
            log_err(f"Error handling stdin stream: {e}")
            # Try responding with parse error
            try:
                err_res = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32700,
                        "message": f"Parse error: {e}"
                    }
                }
                sys.stdout.write(json.dumps(err_res) + "\n")
                sys.stdout.flush()
            except Exception:
                pass

if __name__ == "__main__":
    run_mcp_server()

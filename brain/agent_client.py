import urllib.request
import urllib.parse
import json
import random
import time
from brain.local_llm import LocalLlamaClient

class ZeroClawClient:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.api_down_until = 0.0
        self.local_brain = LocalLlamaClient(config_manager)
        
    def send_message(self, message_text):
        """Sends a message to the agent.
        If ZeroClaw is disabled or offline, routes directly to LocalLlamaClient (Qwen2.5-0.5B + Elder Care).
        """
        cfg = self.config_manager.config.get("zeroclaw", {})
        zeroclaw_enabled = cfg.get("enabled", False)

        # If user disabled ZeroClaw (e.g. standalone Elder Care edge voice AI), use local brain directly
        if not zeroclaw_enabled:
            return self.local_brain.send_message(message_text)

        now = time.time()
        if now < self.api_down_until:
            msg = f"[AgentClient CircuitBreaker] API is offline. Bypassing request for another {int(self.api_down_until - now)}s."
            if hasattr(self, "daemon") and self.daemon:
                self.daemon.log(msg)
            else:
                print(msg)
            return self.local_brain.send_message(message_text)

        api_url = cfg.get("api_url", "http://127.0.0.1:42617/api/chat")
        token = cfg.get("api_token", "")
        
        # Prepare request
        data = json.dumps({"message": message_text}).encode("utf-8")
        req = urllib.request.Request(api_url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
            
        try:
            # Short timeout to prevent locking if offline
            with urllib.request.urlopen(req, timeout=3.0) as response:
                res_body = response.read().decode("utf-8")
                res_json = json.loads(res_body)
                reply = res_json.get("response", res_json.get("reply", res_json.get("message", "")))
                if reply:
                    return reply
        except Exception as e:
            self.api_down_until = time.time() + 60.0
            msg = f"[AgentClient Error] API request failed: {e}. Circuit breaker active for 60 seconds. Falling back to Local Elder Care Brain."
            if hasattr(self, "daemon") and self.daemon:
                self.daemon.log(msg)
            else:
                print(msg)
            
        return self.local_brain.send_message(message_text)

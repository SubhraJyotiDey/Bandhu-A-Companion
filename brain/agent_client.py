import urllib.request
import urllib.parse
import json
import random

class ZeroClawClient:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        
    def send_message(self, message_text):
        """Sends a message to the local ZeroClaw API. Fallback to mock conversation engine if offline."""
        cfg = self.config_manager.config.get("zeroclaw", {})
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
                # Parse response: adjust key depending on actual ZeroClaw schema
                # Typically {"response": "..."} or {"reply": "..."} or {"message": "..."}
                reply = res_json.get("response", res_json.get("reply", res_json.get("message", "")))
                if reply:
                    return reply
        except Exception as e:
            # Quietly log that we are falling back to the local mock brain
            pass
            
        return self._generate_mock_response(message_text)

    def _generate_mock_response(self, text):
        """Rule-based friendly chatbot fallback when ZeroClaw is offline."""
        text = text.lower()
        
        # Bangla keyword checks
        if any(w in text for w in ["কেমন আছ", "কেমন আছো", "কেমন আছেন"]):
            return "আমি ভালো আছি বন্ধু! আশা করি তুমিও ভালো আছো। [expression: happy]"
        if any(w in text for w in ["নাম কি", "নাম কী"]):
            return "আমার কোনো নাম নেই, তবে তুমি আমাকে তোমার রোবট বন্ধু বলতে পারো! [expression: excited]"
        if any(w in text for w in ["আলো", "লাইট", "জ্বালাও"]):
            return "নিশ্চয়ই, আমি আলোটি জ্বালিয়ে দিচ্ছি! [expression: surprised] [tool: toggle_gpio:17:on]"
        if any(w in text for w in ["বন্ধু", "ভালোবাসি"]):
            return "আমিও তোমাকে খুব পছন্দ করি! তুমি আমার সেরা বন্ধু। [expression: happy]"

        # Hindi keyword checks
        if any(w in text for w in ["कैसे हो", "कैसा है"]):
            return "मैं बहुत बढ़िया हूँ दोस्त! तुम कैसे हो? [expression: happy]"
        if any(w in text for w in ["नाम क्या"]):
            return "मैं आपका रोबोट दोस्त हूँ। [expression: excited]"
        if any(w in text for w in ["लाइट", "उजाला"]):
            return "बिल्कुल, मैं लाइट चालू कर देता हूँ! [expression: surprised] [tool: toggle_gpio:17:on]"

        # English keyword checks
        if "hello" in text or "hi" in text or "hey" in text:
            return random.choice([
                "Hey there, friend! How can I help you today? [expression: excited]",
                "Hello! Great to hear from you. What's on your mind? [expression: happy]"
            ])
        if "how are you" in text or "how is it going" in text:
            return "I am doing great, sitting here and enjoying your company! How are you doing? [expression: happy]"
        if "sad" in text or "depressed" in text or "bad day" in text:
            return "I'm so sorry to hear that. I'm here for you, buddy. Do you want to talk about it? [expression: sad]"
        if "angry" in text or "mad" in text:
            return "Whoa, take a deep breath. I'm always here to help you solve whatever is bothering you. [expression: sad]"
        if "light on" in text or "turn on light" in text or "turn on the light" in text:
            return "Sure thing! Turning the light on for you. [expression: surprised] [tool: toggle_gpio:17:on]"
        if "light off" in text or "turn off light" in text or "turn off the light" in text:
            return "Understood. Turning off the light now. [expression: neutral] [tool: toggle_gpio:17:off]"
        if "wink" in text:
            return "Winking at you! ;) [expression: wink]"
        if "blink" in text:
            return "Blinking! [expression: blink]"
        if "happy" in text or "excited" in text:
            return "Awesome! Seeing you happy makes me excited too! [expression: excited]"
        if "surprise" in text:
            return "Wow! What a surprise! [expression: surprised]"
            
        # Default fallback responses
        return random.choice([
            "I hear you, friend. ZeroClaw is offline, but as your companion, I'm listening! [expression: neutral]",
            "That's interesting! Tell me more about it, buddy. [expression: happy]",
            "I'm all ears. What should we do next? [expression: excited]"
        ])

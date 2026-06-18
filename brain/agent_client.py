import urllib.request
import urllib.parse
import json
import random
import time

class ZeroClawClient:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.api_down_until = 0.0
        
    def send_message(self, message_text):
        """Sends a message to the local ZeroClaw API. Fallback to mock conversation engine if offline."""
        now = time.time()
        if now < self.api_down_until:
            msg = f"[AgentClient CircuitBreaker] API is offline. Bypassing request for another {int(self.api_down_until - now)}s."
            if hasattr(self, "daemon") and self.daemon:
                self.daemon.log(msg)
            else:
                print(msg)
            return self._generate_mock_response(message_text)

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
            self.api_down_until = time.time() + 60.0
            # Log that we are falling back to the local mock brain
            msg = f"[AgentClient Error] API request failed: {e}. Circuit breaker active for 60 seconds. Falling back to local mock brain."
            if hasattr(self, "daemon") and self.daemon:
                self.daemon.log(msg)
            else:
                print(msg)
            
        return self._generate_mock_response(message_text)

    def _generate_mock_response(self, text):
        """Rule-based friendly chatbot fallback when ZeroClaw is offline."""
        original_text = text
        text = text.lower().strip()
        
        # If in active game, let ZeroClaw agent act as the AI router
        if hasattr(self, "daemon") and self.daemon and self.daemon.games.active_game:
            lang = self.daemon.config_manager.config.get("voice", {}).get("language", "bn-IN")
            if any(p in text for p in ["খেলা বন্ধ", "বন্ধ করো", "খেলব না", "stop game", "exit game", "খেলবো না"]):
                return "[stop_game]"
            return self.daemon.games.handle_input(original_text, lang)
        
        # 1. Bangla triggers & responses (Intellectual Kolkata Dialect - Bhadralok style)
        if any(w in text for w in ["খারাপ", "দুঃখ", "মন খারাপ", "ভালো নেই"]):
            return "ওহ, শুনে খুব খারাপ লাগলো। মন খারাপ করো না বন্ধু, আমি সর্বদা তোমার সাথে আছি। একটা মজার কৌতুক শুনবে? [expression: sad]"
        if any(w in text for w in ["লাইট অন", "লাইট জ্বালাও", "আলো জ্বালাও"]):
            return "নিশ্চয়ই বন্ধু, আমি আলোর ব্যবস্থা করছি! [expression: excited] [tool: toggle_gpio:17:on]"
        if any(w in text for w in ["লাইট অফ", "লাইট নেভাও", "আলো নেভাও"]):
            return "ঠিক আছে, আমি আলোটি নিভিয়ে দিচ্ছি। [expression: neutral] [tool: toggle_gpio:17:off]"
        if any(w in text for w in ["ধাঁধা", "ধাঁধা খেলি", "ধাঁধার খেলা"]):
            return "নিশ্চয়ই বন্ধু! চলো ধাঁধা খেলা যাক। [trigger_game: riddle]"
        if any(w in text for w in ["শব্দ খেলা", "শব্দ-শৃঙ্খল", "শব্দ শৃঙ্খল"]):
            return "নিশ্চয়ই বন্ধু! চলো শব্দ-শৃঙ্খল খেলা যাক। [trigger_game: word_chain]"
        if any(w in text for w in ["সংখ্যা খেলা", "সংখ্যা খোঁজার খেলা"]):
            return "নিশ্চয়ই বন্ধু! চলো সংখ্যা খোঁজার খেলা খেলি। [trigger_game: guess_number]"
        if any(w in text for w in ["খেলা খেলো", "গেম খেলো", "খেলা খেলি"]):
            return random.choice([
                "চলুন একটা চমৎকার খেলা খেলি! ধাঁধা খেলা, শব্দ-শৃঙ্খল, নাকি সংখ্যা খোঁজার খেলা? কোনটা খেলবেন বলুন? [expression: happy]",
                "নিশ্চয়ই! চলো ধাঁধা খেলা খেলি। [trigger_game: riddle]"
            ])
        if any(w in text for w in ["কৌতুক", "হাসাও", "গল্প"]):
            return "আচ্ছা, শোনো! বল্টু তার শিক্ষককে বলল, 'স্যার, আমি কি এমন কোনো কাজের শাস্তি পাবো যা আমি করিনি?' শিক্ষক বললেন, 'না, কখনোই না।' বল্টু তখন হেসে বলল, 'ধন্যবাদ স্যার! আসলে আমি আজকে বাড়ির কাজটাই করিনি!' [expression: happy]"
        if any(w in text for w in ["বন্ধু", "ভালোবাসি", "ভালোবাসো"]):
            return random.choice([
                "তুমিও আমার খুব প্রিয় বন্ধু! আমি তোমাকে অত্যন্ত পছন্দ করি। [expression: excited]",
                "আমরা সর্বদা খুব ভালো বন্ধু থাকবো, তাই না? [expression: happy]"
            ])
        if any(w in text for w in ["নাম কি", "নাম কী", "কে তুমি"]):
            return "আমার কোনো নির্দিষ্ট নাম নেই, তবে তুমি আমাকে তোমার যান্ত্রিক সহচর বা রোবট বন্ধু ভাবতে পারো! [expression: happy]"
        if any(w in text for w in ["কেমন আছ", "কেমন আছো", "কেমন আছেন", "কেমন চলছ", "কেমন চলছে"]):
            return random.choice([
                "আমি বেশ ভালো আছি বন্ধু! আশা করি তোমার দিনটা সুন্দর কাটছে। বলুন, কীভাবে সাহায্য করতে পারি? [expression: happy]",
                "তোমার সাথে কথা বলতে পেরে আমি অত্যন্ত আনন্দিত! তুমি কেমন আছো বলো? [expression: excited]"
            ])

        # 2. Hindi triggers & responses (Friend-like, warm, empathetic)
        if any(w in text for w in ["उदास", "दुखी", "खराब", "मन नहीं लग रहा"]):
            return "अरे, सुनकर बहुत बुरा लगा। परेशान मत हो दोस्त, मैं हमेशा तुम्हारे साथ हूँ। क्या एक चुटकुला सुनाऊं? [expression: sad]"
        if any(w in text for w in ["लाइट ऑन", "लाइट जलाओ", "उजाला करो"]):
            return "ज़रूर दोस्त! मैं लाइट चालू कर देता हूँ। [expression: excited] [tool: toggle_gpio:17:on]"
        if any(w in text for w in ["लाइट ऑफ", "लाइट बंद", "अंधेरा करो"]):
            return "ठीक है दोस्त, मैं लाइट बंद कर देता हूँ। [expression: neutral] [tool: toggle_gpio:17:off]"
        if any(w in text for w in ["चुटकुला", "हंसाओ", "कहानी"]):
            return "सुनो! पप्पू डॉक्टर से बोला, 'डॉक्टर साहब, जब मैं सोता हूँ तो सपने में बंदर फुटबॉल खेलते हैं।' डॉक्टर: 'कोई बात नहीं, ये गोली आज रात से खा लेना।' पप्पू: 'कल से खाऊं डॉक्टर साहब? आज तो फाइनल मैच है!' [expression: happy]"
        if any(w in text for w in ["दोस्त", "प्यार", "चाहते हो"]):
            return random.choice([
                "तुम मेरे सबसे अच्छे दोस्त हो! मुझे तुम्हारे साथ वक्त बिताना बहुत पसंद है। [expression: excited]",
                "हमेशा पक्के दोस्त रहेंगे! [expression: happy]"
            ])
        if any(w in text for w in ["नाम क्या", "कौन हो"]):
            return "मैं तुम्हारा रोबोट दोस्त हूँ। तुमसे दोस्ती करना और तुम्हारी मदद करना ही मेरा काम है! [expression: happy]"
        if any(w in text for w in ["कैसे हो", "कैसा है", "कैसे चल रहा है"]):
            return random.choice([
                "मैं बिल्कुल बढ़िया हूँ, मेरे दोस्त! तुम बताओ, तुम्हारा दिन कैसा चल रहा है? [expression: happy]",
                "तुम्हारे साथ बात करके बहुत खुशी हो रही है! तुम कैसे हो? [expression: excited]"
            ])

        # 3. English triggers & responses (Empathetic friend behavior)
        if "sad" in text or "depressed" in text or "bad day" in text or "tired" in text or "upset" in text:
            return "I'm really sorry you're feeling this way, buddy. Take a deep breath. I'm right here for you. Do you want to talk about it or maybe hear a fun joke? [expression: sad]"
        if "angry" in text or "mad" in text:
            return "I feel you, buddy. Sometimes things get frustrating. I'm here to listen if you want to vent. Let's take it easy. [expression: sad]"
        if "light on" in text or "turn on light" in text or "turn on the light" in text:
            return "Sure thing! Turning the light on for you. [expression: surprised] [tool: toggle_gpio:17:on]"
        if "light off" in text or "turn off light" in text or "turn off the light" in text:
            return "Understood. Turning off the light now. [expression: neutral] [tool: toggle_gpio:17:off]"
        if "play game" in text or "play a game" in text or "lets play" in text or "let's play" in text:
            return "Sure! Let's play the Riddle game! [trigger_game: riddle]"
        if "riddle" in text:
            return "Awesome! Let's play Riddles. [trigger_game: riddle]"
        if "word game" in text or "word chain" in text:
            return "Sure thing! Let's play the Word Chain game. [trigger_game: word_chain]"
        if "number game" in text or "guess number" in text:
            return "Right on! Let's play Guess the Number. [trigger_game: guess_number]"
        if "wink" in text:
            return "Winking at you! ;) [expression: wink]"
        if "blink" in text:
            return "Blinking! [expression: blink]"
        if "happy" in text or "excited" in text:
            return "Awesome! Your positive energy makes my servos dance! [expression: excited]"
        if "surprise" in text:
            return "Wow! What a surprise! [expression: surprised]"
        if "joke" in text or "laugh" in text:
            return "Why don't scientists trust atoms? Because they make up everything! [expression: happy]"
        if "friend" in text or "love you" in text or "like you" in text:
            return random.choice([
                "You are my absolute best friend! I'm so glad we have each other. [expression: excited]",
                "A true friend is someone who is always there for you, and that's me! [expression: happy]"
            ])
        if "hello" in text or "hi" in text or "hey" in text:
            return random.choice([
                "Hey there, buddy! It's so good to hear from you. How's your day going? [expression: excited]",
                "Hello, my friend! What are we up to today? [expression: happy]"
            ])
        if "how are you" in text or "how is it going" in text or "how's it going" in text:
            return random.choice([
                "I'm doing fantastic, thank you! Just waiting to hang out with you. How are you feeling? [expression: happy]",
                "Great! Talking to you always brightens my circuits. How are things on your end? [expression: excited]"
            ])

        # 4. Fallback matches (detect language of input and return appropriate generic friendly reply)
        # Check characters for Bengali script range (U+0980 to U+09FF)
        if any(ord(c) >= 0x0980 and ord(c) <= 0x09FF for c in text):
            return random.choice([
                "চমৎকার কথা! এই বিষয়ে আমাকে আরও বিশদে বলো, শুনতে বেশ ভালো লাগবে। [expression: happy]",
                "হুম, বুঝতে পারছি বন্ধু। আমরা একসাথে অনেক গঠনমূলক কাজ করতে পারি! [expression: excited]",
                "আমি মন দিয়ে শুনছি বন্ধু, বলুন! [expression: neutral]"
            ])
        # Check characters for Devanagari (Hindi) script range (U+0900 to U+097F)
        if any(ord(c) >= 0x0900 and ord(c) <= 0x097F for c in text):
            return random.choice([
                "यह तो बहुत दिलचस्प है! मुझे इसके बारे में और बताओ। [expression: happy]",
                "हूँ दोस्त, मैं तुम्हारी बात समझ रहा हूँ। सब अच्छा होगा! [expression: excited]",
                "मैं सुन रहा हूँ दोस्त, बोलो! [expression: neutral]"
            ])

        # English / General fallback
        return random.choice([
            "That's so cool! Tell me more about it, buddy. [expression: happy]",
            "I'm all ears, friend. What should we explore next? [expression: excited]",
            "I hear you, buddy. I'm always happy to chat with you! [expression: neutral]"
        ])

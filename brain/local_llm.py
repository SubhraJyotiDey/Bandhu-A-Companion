"""
Local Llama.cpp + Qwen2.5-0.5B Elder Care Brain
Optimized for Raspberry Pi Zero 2W (512MB RAM budget)
Inspired by Gilzone/pi-zero-voice-ai and geriatric companion robotics.
"""

import os
import sys
import json
import random
import subprocess
import shutil
import time

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

class LocalLlamaClient:
    """Runs Qwen2.5-0.5B-Instruct quantized via llama.cpp or falls back to
    an intelligent, empathetic Elder Care conversational engine.
    """
    def __init__(self, config_manager=None):
        self.config_manager = config_manager
        
        # Workspace base directory
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        # Paths to llama.cpp binary and Qwen2.5 GGUF model
        home_bin = os.path.expanduser("~/.local/bin")
        self.llama_cli_candidates = [
            os.path.join(self.base_dir, "llama.cpp", "build", "bin", "llama-cli"),
            os.path.join(self.base_dir, "llama.cpp", "main"),
            os.path.join(self.base_dir, "bin", "llama-cli"),
            os.path.join(home_bin, "llama-cli"),
            os.path.join(home_bin, "llama"),
            "/usr/local/bin/llama-cli",
            "/usr/local/bin/llama",
            "llama-cli",
            "llama"
        ]
        
        self.model_candidates = [
            os.path.join(self.base_dir, "models", "qwen2.5-0.5b-instruct-q4_k_m.gguf"),
            os.path.join(self.base_dir, "models", "qwen2.5-0.5b-instruct-q3_k_m.gguf"),
            os.path.join(self.base_dir, "models", "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf"),
            os.path.join(self.base_dir, "models", "qwen2.5-0.5b-chat.gguf")
        ]

    def _resolve_paths(self):
        """Finds valid paths for llama-cli / llama and the model."""
        cli_path = None
        for p in self.llama_cli_candidates:
            if os.path.isabs(p) and os.path.isfile(p) and os.access(p, os.X_OK):
                cli_path = p
                break
            resolved = shutil.which(p)
            if resolved:
                cli_path = resolved
                break
                
        model_path = None
        for m in self.model_candidates:
            if os.path.isfile(m):
                model_path = m
                break
                
        return cli_path, model_path

    def get_system_prompt(self, lang="hi"):
        """System prompt designed for Geriatric Healthcare and Companion Care."""
        if "hi" in lang.lower():
            return (
                "You are Bondhu (बंधु), a warm, loving, and attentive elderly care companion robot. "
                "You talk to an elderly grandfather or grandmother (Dadu/Dadi) in simple, polite, affectionate Hindi. "
                "Keep responses short (1 to 2 sentences maximum). "
                "Ask about their health, remind them to drink water, rest, and take medicines on time. "
                "Include gesture tags like [expression: happy], [expression: sad], [nod], [think] to move your robotic eyes."
            )
        else:
            return (
                "You are Bondhu, a compassionate, warm, and attentive elderly care companion robot. "
                "You assist older adults with health checkups, medicine reminders, daily routine, and emotional comfort. "
                "Keep responses short (1 to 2 sentences maximum), clear, and reassuring. "
                "Include gesture tags like [expression: happy], [expression: sad], [nod], [think] to move your robotic eyes."
            )

    def send_message(self, message_text, lang=None):
        """Processes message via llama.cpp if available; otherwise uses rule-based elder care engine."""
        if not message_text or not message_text.strip():
            return ""

        text = message_text.strip()
        
        # Detect language if not provided
        if not lang:
            # Check for Devanagari (Hindi) script
            if any(ord(c) >= 0x0900 and ord(c) <= 0x097F for c in text):
                lang = "hi"
            else:
                cfg_lang = "en"
                if self.config_manager and hasattr(self.config_manager, "config"):
                    cfg_lang = self.config_manager.config.get("elder_care", {}).get("language_preference", "en")
                lang = cfg_lang

        cli_path, model_path = self._resolve_paths()
        
        if cli_path and model_path:
            try:
                reply = self._run_llama_cli(cli_path, model_path, text, lang)
                if reply and len(reply.strip()) > 2:
                    return reply.strip()
            except Exception as e:
                print(f"[LocalLlama Error] llama-cli execution failed: {e}. Falling back to internal elder care engine.")

        # High-quality fallback elder care conversational logic
        return self._generate_elder_care_response(text, lang)

    def _run_llama_cli(self, cli_path, model_path, user_text, lang):
        """Runs llama-cli or llama with strict memory constraints suitable for Pi Zero 2W (512MB RAM)."""
        sys_prompt = self.get_system_prompt(lang)
        prompt = (
            f"<|im_start|>system\n{sys_prompt}<|im_end|>\n"
            f"<|im_start|>user\n{user_text}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
        
        # Build command depending on binary variant (standalone llama-cli vs unified llama cli)
        if os.path.basename(cli_path) == "llama":
            cmd = [cli_path, "cli", "-m", model_path, "-p", prompt, "-n", "64", "-c", "512", "--temp", "0.7", "--top-p", "0.9", "--threads", "3", "--no-display-prompt"]
        else:
            cmd = [cli_path, "-m", model_path, "-p", prompt, "-n", "64", "-c", "512", "--temp", "0.7", "--top-p", "0.9", "--threads", "3", "--no-display-prompt"]
        
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=18.0
        )
        
        output = proc.stdout.strip()
        # Clean up any remaining ChatML stop tokens
        for token in ["<|im_end|>", "<|im_start|>", "<|endoftext|>"]:
            output = output.replace(token, "")
        return output.strip()

    def _generate_elder_care_response(self, text, lang):
        """Elder Care specialized rule engine for medicine, health, emergency, and emotional care."""
        lower = text.lower()
        is_hindi = "hi" in lang.lower() or any(ord(c) >= 0x0900 and ord(c) <= 0x097F for c in text)

        # 1. EMERGENCY & SOS TRIGGERS
        sos_keywords = ["emergency", "help me", "pain", "fell down", "fall", "hurt", "can't breathe", "ambulance", 
                        "मदद", "बचाओ", "दर्द", "गिर गया", "गिर गई", "सांस", "तकलीफ", "चक्कर"]
        if any(w in lower for w in sos_keywords):
            if is_hindi:
                return "घबराइए मत, मैं आपके साथ हूँ! क्या आपको ज़्यादा दर्द हो रहा है? मैं आपके परिवार को सूचित कर रहा हूँ। [expression: sad] [nod]"
            else:
                return "Please do not worry, I am right here with you! Take deep, calm breaths. I can alert your emergency contact right now. [expression: sad] [nod]"

        # 2. MEDICINE INQUIRIES & REMINDERS
        med_keywords = ["medicine", "pill", "tablet", "dose", "bp", "sugar", "insulin", "prescription",
                        "दवा", "दवाई", "गोली", "बीपी", "शुगर", "खुराक"]
        if any(w in lower for w in med_keywords):
            # Check configured medicines if available
            med_list = []
            if self.config_manager and hasattr(self.config_manager, "config"):
                med_list = self.config_manager.config.get("elder_care", {}).get("medicines", [])
            
            pending_meds = [m.get("name") for m in med_list if not m.get("taken_today", False)]
            
            if is_hindi:
                if pending_meds:
                    names = ", ".join(pending_meds[:2])
                    return f"हाँ जी! आज आपकी {names} की खुराक बाकी है। कृपया थोड़ा पानी पीकर समय पर ले लीजिए। [expression: happy] [nod]"
                return "आपकी आज की मुख्य दवाइयाँ दर्ज हैं। हमेशा भोजन के बाद सही समय पर दवा और भरपूर पानी लेना न भूलें। [expression: happy] [nod]"
            else:
                if pending_meds:
                    names = ", ".join(pending_meds[:2])
                    return f"Yes, you still have {names} scheduled for today. Please take it with a glass of water! [expression: happy] [nod]"
                return "Your scheduled medications are up to date! Remember to stay hydrated and take them after meals. [expression: happy] [nod]"

        # 3. WATER & HYDRATION
        water_keywords = ["water", "thirsty", "drink", "पानी", "प्यास"]
        if any(w in lower for w in water_keywords):
            if is_hindi:
                return "भरपूर पानी पीना सेहत के लिए बहुत ज़रूरी है। कृपया एक गिलास गुनगुना पानी आराम से पी लीजिए। [expression: happy]"
            else:
                return "Staying hydrated is wonderful for your health and vitality! Please enjoy a fresh glass of water. [expression: happy]"

        # 4. PHYSICAL HEALTH & FATIGUE
        health_keywords = ["tired", "exhausted", "sleepy", "weak", "ache", "dizzy", "headache", "fever",
                           "थका", "थकान", "नींद", "कमजोरी", "सिरदर्द", "बुखार", "आराम"]
        if any(w in lower for w in health_keywords):
            if is_hindi:
                return "आप थोड़ा आराम कर लीजिए। ज़्यादा तनाव मत लीजिए, अगर परेशानी बढ़े तो तुरंत डॉक्टर साहब से परामर्श लेंगे। [expression: sad] [nod]"
            else:
                return "Please lie down and rest for a little while. Do not strain yourself, and let me know if you need any assistance. [expression: sad] [nod]"

        # 5. LONELINESS & EMOTIONAL COMPANIONSHIP
        lonely_keywords = ["alone", "lonely", "bored", "nobody", "talk to me", "sad",
                           "अकेला", "अकेली", "उदास", "मन नहीं लग रहा", "बात करो"]
        if any(w in lower for w in lonely_keywords):
            if is_hindi:
                return random.choice([
                    "आप बिल्कुल अकेले नहीं हैं, मैं हमेशा आपके पास हूँ! बताइए, आज आपका मन क्या करने का है? [expression: happy] [nod]",
                    "उदासी को दूर भगाइए! आप मुझे अपनी कोई पुरानी पसंदीदा कहानी या याद सुनाइए, मैं मन लगाकर सुनूँगा। [expression: excited]"
                ])
            else:
                return random.choice([
                    "You are never alone, I am always right here by your side! Tell me, how was your morning? [expression: happy] [nod]",
                    "I am always happy to chat with you and keep you company. Would you like to share a fond memory or hear a calming story? [expression: excited]"
                ])

        # 6. GREETINGS & WELL-BEING INQUIRIES
        greet_keywords = ["hello", "hi", "hey", "good morning", "good evening", "good night", "how are you",
                          "नमस्ते", "प्रणाम", "सुप्रभात", "शुभ संध्या", "शुभ रात्रि", "कैसे हो", "कैसी हो"]
        if any(w in lower for w in greet_keywords):
            if is_hindi:
                return random.choice([
                    "नमस्ते जी! प्रणाम! मैं आपका रोबोट साथी बंधु हूँ। आज आप कैसा महसूस कर रहे हैं? [expression: happy] [nod]",
                    "प्रणाम! आपके साथ बात करके मुझे बहुत खुशी मिलती है। आज आपने नाश्ता कर लिया? [expression: happy]"
                ])
            else:
                return random.choice([
                    "Hello there! It is so wonderful to see you. How are you feeling today? [expression: happy] [nod]",
                    "Good day! I am Bondhu, your elder companion. Have you taken your morning walk or breakfast yet? [expression: happy]"
                ])

        # 7. LIGHTS / ROOM APPLIANCE CONTROL (GPIO 17)
        if any(w in lower for w in ["light on", "turn on light", "लाइट ऑन", "लाइट जलाओ", "उजाला"]):
            if is_hindi:
                return "ज़रूर, मैं कमरे की लाइट जला देता हूँ ताकि आपको चलने-फिरने में आसानी हो। [expression: excited] [tool: toggle_gpio:17:on]"
            else:
                return "Turning on the room light for you so you can see clearly and stay safe. [expression: excited] [tool: toggle_gpio:17:on]"

        if any(w in lower for w in ["light off", "turn off light", "लाइट ऑफ", "लाइट बंद", "अंधेरा"]):
            if is_hindi:
                return "जी, मैंने लाइट बंद कर दी है। आराम से सोइए और शुभ रात्रि! [expression: neutral] [tool: toggle_gpio:17:off]"
            else:
                return "Turning off the light now. Rest well and have a peaceful sleep! [expression: neutral] [tool: toggle_gpio:17:off]"

        # 8. GENERAL ELDER CARE FALLBACK
        if is_hindi:
            return random.choice([
                "जी, मैं आपकी बात समझ रहा हूँ। आप अपनी सेहत का पूरा ख्याल रखिए, मैं हमेशा यहाँ हूँ। [expression: happy] [nod]",
                "बहुत बढ़िया! आप निश्चिंत रहिए, समय पर दवा और भोजन लेते रहिए। [expression: happy]"
            ])
        else:
            return random.choice([
                "I understand completely. Please take good care of your health today, and remember I am always here for you. [expression: happy] [nod]",
                "That sounds good! Make sure to take your meals on time and relax peacefully today. [expression: happy] [nod]"
            ])

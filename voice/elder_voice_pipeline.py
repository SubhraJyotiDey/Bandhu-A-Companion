#!/usr/bin/env python3
"""
Elder Care Offline Voice AI Pipeline for Raspberry Pi Zero 2W
Inspired by Gilzone/pi-zero-voice-ai
Sequential Pipeline: Microphone Audio -> Vosk STT (Hindi/English) -> llama.cpp (Qwen2.5-0.5B) -> eSpeak-NG/Edge-TTS
RAM Budget: <300 MB footprint (fits comfortably in 512 MB Pi Zero 2W RAM)
"""

import os
import sys
import argparse
import time
import subprocess
import json

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from brain.local_llm import LocalLlamaClient

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

class MockConfigManager:
    def __init__(self, config_path=None):
        if not config_path:
            config_path = os.path.join(PROJECT_ROOT, "config.json")
        self.config_path = config_path
        self.config = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            except Exception:
                pass

class ElderVoicePipeline:
    def __init__(self, lang="hi"):
        self.lang = lang
        self.config_mgr = MockConfigManager()
        self.brain = LocalLlamaClient(self.config_mgr)
        
        # Determine TTS engine (espeak-ng for offline, edge-tts for online)
        self.tts_provider = self.config_mgr.config.get("voice", {}).get("tts_provider", "espeak-ng")
        
    def speak(self, text):
        """Speaks text using offline eSpeak-NG or Edge-TTS."""
        if not text:
            return
            
        # Strip emotional tags like [expression: happy], [nod]
        import re
        clean_text = re.sub(r'\[.*?\]', '', text).strip()
        if not clean_text:
            return
            
        print(f"[ElderVoice TTS] Speaking: \"{clean_text}\" (lang={self.lang})")
        
        # 1. Try offline eSpeak-NG (zero memory footprint, runs natively on ARM)
        espeak_lang = "hi" if "hi" in self.lang.lower() else "en"
        try:
            res = subprocess.run(["which", "espeak-ng"], capture_output=True, text=True)
            if res.returncode == 0:
                subprocess.run(["espeak-ng", f"-v{espeak_lang}", "-s", "145", "-p", "50", clean_text], check=False)
                return
        except Exception:
            pass

        # 2. Try espeak
        try:
            res = subprocess.run(["which", "espeak"], capture_output=True, text=True)
            if res.returncode == 0:
                subprocess.run(["espeak", f"-v{espeak_lang}", clean_text], check=False)
                return
        except Exception:
            pass

        # 3. Fallback to Windows SAPI (if on Windows)
        if sys.platform.startswith("win"):
            try:
                import pyttsx3
                engine = pyttsx3.init()
                engine.say(clean_text)
                engine.runAndWait()
                return
            except Exception:
                pass

        print(f"[ElderVoice Audio Out]: >>> {clean_text} <<<")

    def transcribe_audio_file(self, wav_path):
        """Transcribes a WAV file using Vosk offline."""
        try:
            import vosk
            import wave
            
            # Resolve Vosk model path
            vosk_dir = os.path.join(PROJECT_ROOT, "voice", "models")
            model_subfolder = "vosk-hi" if "hi" in self.lang.lower() else "vosk-en"
            model_path = os.path.join(vosk_dir, model_subfolder)
            
            if not os.path.exists(model_path):
                print(f"[ElderVoice Vosk] Model directory not found: {model_path}. Please run setup script.")
                return ""
                
            model = vosk.Model(model_path)
            wf = wave.open(wav_path, "rb")
            rec = vosk.KaldiRecognizer(model, wf.getframerate())
            rec.SetWords(False)
            
            while True:
                data = wf.readframes(4000)
                if len(data) == 0:
                    break
                rec.AcceptWaveform(data)
                
            res = json.loads(rec.FinalResult())
            text = res.get("text", "")
            return text
        except Exception as e:
            print(f"[ElderVoice Vosk Error] Transcription error: {e}")
            return ""

    def process_query(self, user_text):
        """Full pipeline: Takes text, passes to Qwen2.5 Elder Care Brain, speaks response."""
        print(f"\n==========================================")
        print(f"[ElderVoice STT Captured]: \"{user_text}\"")
        start_t = time.time()
        
        reply = self.brain.send_message(user_text, lang=self.lang)
        elapsed = time.time() - start_t
        
        print(f"[ElderVoice Qwen2.5-0.5B Reply] ({elapsed:.2f}s): \"{reply}\"")
        print(f"==========================================")
        
        self.speak(reply)
        return reply

    def run_live_mic_loop(self):
        """Records from microphone sequentially, transcribing with Vosk and generating response."""
        print(f"[ElderVoice Pipeline] Initializing live microphone session (lang={self.lang})...")
        from voice.speech_to_text import STTManager
        stt = STTManager(self.config_mgr)
        
        print("[ElderVoice Pipeline] Ready! Speak into the microphone (Say 'exit' or 'अलविदा' to quit)...")
        while True:
            try:
                print("\n[ElderVoice Pipeline] Listening...")
                user_speech = stt.listen_and_transcribe(timeout=6, phrase_time_limit=10, lang=self.lang)
                if not user_speech:
                    print("[ElderVoice Pipeline] (Silence)")
                    continue
                    
                if user_speech.lower().strip() in ["exit", "quit", "stop", "अलविदा", "बंद करो"]:
                    self.speak("Goodbye, take care of your health!" if "en" in self.lang else "अलविदा, अपनी सेहत का ख्याल रखिएगा!")
                    break
                    
                self.process_query(user_speech)
                
            except KeyboardInterrupt:
                print("\n[ElderVoice Pipeline] Stopped by user.")
                break
            except Exception as e:
                print(f"[ElderVoice Error]: {e}")
                time.sleep(1)

def main():
    parser = argparse.ArgumentParser(description="Bandhu Elder Care Offline Voice AI Pipeline")
    parser.add_argument("--lang", default="hi", choices=["hi", "en"], help="Conversation language (hi=Hindi, en=English)")
    parser.add_argument("--text", type=str, help="Single-shot text query to test pipeline directly")
    parser.add_argument("--continuous", action="store_true", help="Start continuous live microphone loop")
    args = parser.parse_args()

    pipeline = ElderVoicePipeline(lang=args.lang)
    
    if args.text:
        pipeline.process_query(args.text)
    elif args.continuous:
        pipeline.run_live_mic_loop()
    else:
        # Default demo interaction
        test_queries = [
            "नमस्ते बंधु, मेरी आज की दवाइयों का समय क्या है?",
            "मुझे थोड़ा सिर में दर्द हो रहा है और चक्कर आ रहे हैं।",
            "कमरे की लाइट जला दो।"
        ] if args.lang == "hi" else [
            "Hello Bondhu, what medicines do I need to take today?",
            "I am feeling very tired and have a mild headache.",
            "Can you turn on the light?"
        ]
        
        print(f"[ElderVoice Demo] Running 3 test queries in language: {args.lang}")
        for q in test_queries:
            pipeline.process_query(q)
            time.sleep(1)

if __name__ == "__main__":
    main()

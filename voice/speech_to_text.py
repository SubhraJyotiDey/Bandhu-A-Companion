import sys
import time

class STTManager:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.config = config_manager.config
        
        # Audio library references
        self.recognizer = None
        self.microphone = None
        
        self._init_speech_recognition()

    def _init_speech_recognition(self):
        """Initializes the SpeechRecognition objects. Fallback if PyAudio is missing."""
        try:
            import speech_recognition as sr
            self.recognizer = sr.Recognizer()
            # Dynamic energy threshold adjustments
            self.recognizer.dynamic_energy_threshold = True
            self.recognizer.energy_threshold = 300 # Baseline sensitivity
            
            self.microphone = sr.Microphone()
            print("[STT] Microphone initialized successfully.")
        except ImportError:
            print("[STT Warning] speech_recognition or PyAudio is missing. STT will run in mock/text-only mode.")
            self.recognizer = None
            self.microphone = None
        except Exception as e:
            print(f"[STT Error] Microphone failed to initialize: {e}. STT running in mock mode.")
            self.recognizer = None
            self.microphone = None

    def listen_and_transcribe(self, timeout=8, phrase_time_limit=12, lang=None):
        """Listens to the microphone and transcribes to text in the selected language."""
        if not self.recognizer or not self.microphone:
            print("[STT MOCK] Listening... (Mock STT: enter your text in the Web Portal console).")
            # Wait for mock input from web portal or sleep
            time.sleep(3)
            return None

        if lang is None:
            lang = self.config_manager.config.get("voice", {}).get("language", "en-US")
            
        print(f"[STT] Listening for audio (language: {lang})...")
        
        try:
            import speech_recognition as sr
            with self.microphone as source:
                # Adjust for ambient noise once
                self.recognizer.adjust_for_ambient_noise(source, duration=0.8)
                
                # Record audio from mic
                try:
                    audio_data = self.recognizer.listen(
                        source, 
                        timeout=timeout, 
                        phrase_time_limit=phrase_time_limit
                    )
                except sr.WaitTimeoutError:
                    print("[STT] Listening timed out (no speech detected).")
                    return None
                    
            print("[STT] Speech captured. Transcribing...")
            
            # Perform transcription using Google's free cloud speech recognition API
            # Maps standard language strings to recognition locale codes
            lang_code = "en-US"
            lang_lower = lang.lower()
            if "bn" in lang_lower:
                lang_code = "bn-IN" if "in" in lang_lower else "bn-BD"
            elif "hi" in lang_lower:
                lang_code = "hi-IN"
            else:
                lang_code = "en-IN" if "in" in lang_lower else "en-US"
                
            transcription = self.recognizer.recognize_google(audio_data, language=lang_code)
            print(f"[STT] Result: \"{transcription}\"")
            return transcription
            
        except sr.UnknownValueError:
            print("[STT] Speech recognition could not understand the audio.")
            return ""
        except sr.RequestError as e:
            print(f"[STT Error] Google API request failed: {e}")
            return ""
        except Exception as e:
            print(f"[STT Error] Speech capture exception: {e}")
            return ""

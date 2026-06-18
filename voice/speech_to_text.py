import sys
import time
import os
import json

# Directory where manually-downloaded Vosk models are stored
# Expected structure: voice/models/vosk-<lang>/  (e.g. vosk-en, vosk-hi, vosk-bn)
VOSK_MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")

# Vosk language code mapping and download URLs
VOSK_LANG_MAP = {
    "en": {
        "code": "en-us",
        "auto_download": True,   # Vosk can auto-download this
        "model_url": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
        "dir_name": "vosk-en",
    },
    "hi": {
        "code": "hi",
        "auto_download": True,   # Vosk can auto-download this
        "model_url": "https://alphacephei.com/vosk/models/vosk-model-small-hi-0.22.zip",
        "dir_name": "vosk-hi",
    },
    "bn": {
        "code": "bn",
        "auto_download": False,  # Bengali is NOT auto-downloadable from Vosk
        "model_url": "https://huggingface.co/alphacep/vosk-model-small-streaming-bn/resolve/main/vosk-model-small-streaming-bn.zip",
        "dir_name": "vosk-bn",
    },
}

# Fallback order when the primary language model is unavailable
VOSK_FALLBACK_ORDER = ["hi", "en"]

class STTManager:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.config = config_manager.config
        
        # Audio library references
        self.recognizer = None
        self.microphone = None
        self.vosk_available = False
        self.pyaudio_instance = None
        
        # Cache loaded Vosk models to avoid reloading on every call
        self._vosk_model_cache = {}
        
        self._init_speech_recognition()

    def __del__(self):
        if hasattr(self, "pyaudio_instance") and self.pyaudio_instance is not None:
            try:
                self.pyaudio_instance.terminate()
            except Exception:
                pass

    def _init_speech_recognition(self):
        """Initializes the SpeechRecognition and Vosk objects. Fallback if libraries are missing."""
        # Check if Vosk is installed
        try:
            import vosk
            self.vosk_available = True
            print("[STT] Vosk offline speech recognition library is available.")
        except ImportError:
            print("[STT Warning] vosk is missing. Falling back to Google Cloud speech recognition.")
            
        try:
            import speech_recognition as sr
            self.recognizer = sr.Recognizer()
            # Dynamic energy threshold adjustments
            self.recognizer.dynamic_energy_threshold = True
            self.recognizer.energy_threshold = 300 # Baseline sensitivity
            
            self.microphone = sr.Microphone()
            print("[STT] Google SpeechRecognition initialized successfully.")
        except ImportError:
            print("[STT Warning] speech_recognition or PyAudio is missing. Cloud STT will run in mock/text-only mode.")
            self.recognizer = None
            self.microphone = None
        except Exception as e:
            print(f"[STT Error] Google SpeechRecognition failed to initialize: {e}.")
            self.recognizer = None
            self.microphone = None

    def listen_and_transcribe(self, timeout=8, phrase_time_limit=12, lang=None):
        """Listens to the microphone and transcribes to text. Prefers offline Vosk if available."""
        if lang is None:
            lang = self.config_manager.config.get("voice", {}).get("language", "en-US")
            
        auto_detect = self.config_manager.config.get("voice", {}).get("auto_language_detection", True)
        
        # If auto-detect is disabled, we prefer offline local Vosk if available.
        if self.vosk_available and not auto_detect:
            try:
                # Perform offline low-latency transcription using local Vosk
                return self._listen_and_transcribe_vosk(timeout, phrase_time_limit, lang)
            except Exception as e:
                print(f"[STT Warning] Vosk transcription failed: {e}. Falling back to Google Cloud...")
                
        # Google Cloud STT / Parallel Auto-detect
        if not self.recognizer or not self.microphone:
            print("[STT MOCK] Listening... (Mock STT: enter your text in the Web Portal console).")
            time.sleep(3)
            return None

        print(f"[STT Cloud] Listening for audio (language: {lang}, auto-detect: {auto_detect})...")
        
        try:
            import speech_recognition as sr
            with self.microphone as source:
                # Adjust for ambient noise once
                self.recognizer.adjust_for_ambient_noise(source, duration=0.8)
                
                try:
                    audio_data = self.recognizer.listen(
                        source, 
                        timeout=timeout, 
                        phrase_time_limit=phrase_time_limit
                    )
                except sr.WaitTimeoutError:
                    print("[STT Cloud] Listening timed out (no speech detected).")
                    return None
                    
            print("[STT Cloud] Speech captured. Transcribing...")
            
            if auto_detect:
                import concurrent.futures
                
                def transcribe_lang(lang_code):
                    try:
                        res = self.recognizer.recognize_google(audio_data, language=lang_code, show_all=True)
                        if not res or not isinstance(res, dict) or 'alternative' not in res:
                            return lang_code, "", 0.0
                        alternatives = res['alternative']
                        if not alternatives:
                            return lang_code, "", 0.0
                        best = alternatives[0]
                        text = best.get('transcript', '')
                        confidence = best.get('confidence', 0.8)
                        return lang_code, text, confidence
                    except Exception:
                        return lang_code, "", 0.0
                        
                candidate_langs = ["en-US", "bn-IN", "hi-IN"]
                results = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                    futures = {executor.submit(transcribe_lang, l): l for l in candidate_langs}
                    for future in concurrent.futures.as_completed(futures):
                        results.append(future.result())
                        
                valid_results = [r for r in results if r[1].strip()]
                if not valid_results:
                    print("[STT Cloud] Speech recognition could not understand the audio in any language.")
                    # Try to fall back to Vosk if available
                    if self.vosk_available:
                        print("[STT Warning] Falling back to offline Vosk...")
                        return self._listen_and_transcribe_vosk(timeout, phrase_time_limit, lang)
                    return ""
                    
                valid_results.sort(key=lambda x: x[2], reverse=True)
                best_lang, best_text, best_conf = valid_results[0]
                
                print(f"[STT Cloud] Detected language: {best_lang} (confidence: {best_conf:.2f})")
                print(f"[STT Cloud] Result: \"{best_text}\"")
                
                if best_lang != lang:
                    self.config_manager.config["voice"]["language"] = best_lang
                    self.config_manager.save_config()
                    print(f"[STT Cloud] Dynamic Language Switch: {lang} -> {best_lang}")
                    
                return best_text
                
            else:
                # Single language recognition
                lang_code = "en-US"
                lang_lower = lang.lower()
                if "bn" in lang_lower:
                    lang_code = "bn-IN" if "in" in lang_lower else "bn-BD"
                elif "hi" in lang_lower:
                    lang_code = "hi-IN"
                else:
                    lang_code = "en-IN" if "in" in lang_lower else "en-US"
                    
                transcription = self.recognizer.recognize_google(audio_data, language=lang_code)
                print(f"[STT Cloud] Result: \"{transcription}\"")
                return transcription
                
        except Exception as e:
            print(f"[STT Cloud Error] Speech recognition failed: {e}")
            if self.vosk_available:
                print("[STT Warning] Falling back to offline Vosk...")
                try:
                    return self._listen_and_transcribe_vosk(timeout, phrase_time_limit, lang)
                except Exception as ex:
                    print(f"[STT Error] Offline Vosk fallback failed: {ex}")
            return ""

    def _load_vosk_model(self, vosk_lang_key):
        """Loads a Vosk model by language key ('en', 'hi', 'bn') with local-path-first strategy.
        Returns (Model, actual_lang_key) or (None, None) if not available."""
        from vosk import Model
        
        # Return cached model if already loaded
        if vosk_lang_key in self._vosk_model_cache:
            return self._vosk_model_cache[vosk_lang_key], vosk_lang_key
        
        lang_info = VOSK_LANG_MAP.get(vosk_lang_key)
        if not lang_info:
            print(f"[STT Vosk] Unknown language key: {vosk_lang_key}")
            return None, None
        
        # Strategy 1: Check for a manually-downloaded model directory
        local_model_path = os.path.join(VOSK_MODELS_DIR, lang_info["dir_name"])
        if os.path.isdir(local_model_path):
            try:
                print(f"[STT Vosk] Loading local model from: {local_model_path}")
                model = Model(model_path=local_model_path)
                self._vosk_model_cache[vosk_lang_key] = model
                return model, vosk_lang_key
            except Exception as e:
                print(f"[STT Vosk] Failed to load local model '{local_model_path}': {e}")
        
        # Strategy 2: Try Vosk auto-download (only works for en, hi, etc.)
        if lang_info["auto_download"]:
            try:
                print(f"[STT Vosk] Auto-downloading model for: {lang_info['code']}...")
                model = Model(lang=lang_info["code"])
                self._vosk_model_cache[vosk_lang_key] = model
                return model, vosk_lang_key
            except Exception as e:
                print(f"[STT Vosk] Auto-download failed for '{lang_info['code']}': {e}")
        else:
            print(f"[STT Vosk] No auto-download available for '{vosk_lang_key}'. "
                  f"Please download the model manually:")
            print(f"  wget {lang_info['model_url']}")
            print(f"  unzip -d {local_model_path} <downloaded_file>.zip")
        
        return None, None

    def _listen_and_transcribe_vosk(self, timeout, phrase_time_limit, lang):
        """Listens and transcribes synchronously using the local offline Vosk engine."""
        import pyaudio
        import numpy as np
        from vosk import KaldiRecognizer
        
        lang_lower = lang.lower()
        vosk_lang_key = "en"
        if "bn" in lang_lower:
            vosk_lang_key = "bn"
        elif "hi" in lang_lower:
            vosk_lang_key = "hi"
            
        # Try loading the requested model, then fall back through the chain
        model, actual_lang = self._load_vosk_model(vosk_lang_key)
        
        if model is None:
            # Try fallback languages
            for fallback_lang in VOSK_FALLBACK_ORDER:
                if fallback_lang == vosk_lang_key:
                    continue
                print(f"[STT Vosk] Trying fallback language: {fallback_lang}")
                model, actual_lang = self._load_vosk_model(fallback_lang)
                if model is not None:
                    print(f"[STT Vosk] Using fallback model: {actual_lang} (requested: {vosk_lang_key})")
                    break
        
        if model is None:
            raise RuntimeError(f"No Vosk model available for '{vosk_lang_key}' or any fallback language. "
                             f"Download models to: {VOSK_MODELS_DIR}")
        
        print(f"[STT Vosk] Using model: {actual_lang}")
        rec = KaldiRecognizer(model, 16000)
        
        # Audio stream parameters
        CHANNELS = 1
        FORMAT = pyaudio.paInt16
        CHUNK = 1024
        RATE = 16000
        
        if self.pyaudio_instance is None:
            self.pyaudio_instance = pyaudio.PyAudio()
        p = self.pyaudio_instance
        stream = None
        opened_rate = RATE
        
        # Robust sample rate probing for ALSA devices
        rates_to_try = [16000, 48000, 44100]
        for r in rates_to_try:
            try:
                stream = p.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=r,
                    input=True,
                    frames_per_buffer=int(CHUNK * r / RATE)
                )
                opened_rate = r
                break
            except Exception:
                continue
                
        if not stream:
            raise RuntimeError("Vosk failed to open PyAudio input stream at any supported rate.")
            
        print(f"[STT Vosk] Listening (rate: {opened_rate} Hz)...")
        
        transcription = ""
        start_time = time.time()
        silence_start = None
        
        try:
            read_size = int(CHUNK * opened_rate / RATE)
            while time.time() - start_time < phrase_time_limit:
                data = stream.read(read_size, exception_on_overflow=False)
                if not data:
                    time.sleep(0.01)
                    continue
                    
                # Convert bytes to numpy array
                audio_frame = np.frombuffer(data, dtype=np.int16)
                
                # Resample using linear interpolation if device rate != 16000 Hz
                if len(audio_frame) != CHUNK and len(audio_frame) > 0:
                    indices = np.linspace(0, len(audio_frame) - 1, CHUNK)
                    audio_frame = np.interp(indices, np.arange(len(audio_frame)), audio_frame).astype(np.int16)
                    data = audio_frame.tobytes()
                    
                if rec.AcceptWaveform(data):
                    res = json.loads(rec.Result())
                    transcription = res.get("text", "")
                    if transcription:
                        break
                else:
                    partial = json.loads(rec.PartialResult())
                    partial_text = partial.get("partial", "")
                    if partial_text:
                        # Reset start_time since speech is actively occurring
                        start_time = time.time()
                        silence_start = None
                    else:
                        if silence_start is None:
                            silence_start = time.time()
                        elif time.time() - silence_start > timeout:
                            # Break if silence threshold reached
                            break
                            
            if not transcription:
                res = json.loads(rec.FinalResult())
                transcription = res.get("text", "")
                
            print(f"[STT Vosk] Result: \"{transcription}\"")
            return transcription
            
        finally:
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass

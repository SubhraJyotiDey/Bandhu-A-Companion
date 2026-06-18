import sys
import time
import os
import json

class STTManager:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.config = config_manager.config
        
        # Audio library references
        self.recognizer = None
        self.microphone = None
        self.vosk_available = False
        
        self._init_speech_recognition()

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
            
        if self.vosk_available:
            try:
                # Perform offline low-latency transcription using local Vosk
                return self._listen_and_transcribe_vosk(timeout, phrase_time_limit, lang)
            except Exception as e:
                print(f"[STT Warning] Vosk transcription failed: {e}. Falling back to Google Cloud...")
                
        # Google Cloud STT fallback
        if not self.recognizer or not self.microphone:
            print("[STT MOCK] Listening... (Mock STT: enter your text in the Web Portal console).")
            time.sleep(3)
            return None

        print(f"[STT Cloud] Listening for audio (language: {lang})...")
        
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
            
            # Map language to Google Cloud codes
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
            
        except sr.UnknownValueError:
            print("[STT Cloud] Speech recognition could not understand the audio.")
            return ""
        except sr.RequestError as e:
            print(f"[STT Cloud Error] Google API request failed: {e}")
            return ""
        except Exception as e:
            print(f"[STT Cloud Error] Speech capture exception: {e}")
            return ""

    def _listen_and_transcribe_vosk(self, timeout, phrase_time_limit, lang):
        """Listens and transcribes synchronously using the local offline Vosk engine."""
        import pyaudio
        import numpy as np
        from vosk import Model, KaldiRecognizer
        
        lang_lower = lang.lower()
        vosk_lang = "en-us"
        if "bn" in lang_lower:
            vosk_lang = "bn"
        elif "hi" in lang_lower:
            vosk_lang = "hi"
            
        # Initialize Vosk Model (downloads automatically from alphacephei.com to cache if missing)
        # en-us -> small english model (~40MB)
        # bn -> small bengali model (~30MB)
        # hi -> small hindi model (~40MB)
        print(f"[STT Vosk] Loading local model for: {vosk_lang}...")
        model = Model(lang=vosk_lang)
        rec = KaldiRecognizer(model, 16000)
        
        # Audio stream parameters
        CHANNELS = 1
        FORMAT = pyaudio.paInt16
        CHUNK = 1024
        RATE = 16000
        
        p = pyaudio.PyAudio()
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
            p.terminate()
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
            p.terminate()

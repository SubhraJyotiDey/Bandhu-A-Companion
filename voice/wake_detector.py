import os
import time
import threading
import sys

class WakeWordDetector:
    def __init__(self, config_manager, stt_manager, wake_callback, noise_callback=None):
        self.config_manager = config_manager
        self.stt_manager = stt_manager
        self.wake_callback = wake_callback
        self.noise_callback = noise_callback
        
        self.is_listening = False
        self.thread = None
        self.mic_available = (self.stt_manager.recognizer is not None or self.config_manager.config.get("voice", {}).get("wake_word_engine", "google").lower() == "openwakeword")

        # Determine wake word engine
        self.engine = self.config_manager.config.get("voice", {}).get("wake_word_engine", "google").lower()
        self.oww_model = None
        
        if self.engine == "openwakeword":
            self._init_openwakeword()

    def _init_openwakeword(self):
        try:
            import openwakeword
            import numpy as np
            from openwakeword.model import Model
            
            model_name = self.config_manager.config.get("voice", {}).get("wake_word", "jarvis").lower()
            
            # Check if custom model file exists in voice/models/
            custom_model_path = os.path.join(os.path.dirname(__file__), "models", f"{model_name}.onnx")
            
            if os.path.exists(custom_model_path):
                print(f"[Wake Detector] Loading custom openWakeWord model from: {custom_model_path}")
                self.oww_model = Model(wakeword_models=[custom_model_path], inference_framework="onnx")
            else:
                # Fallback to built-in openWakeWord models (alexa, hey_mycroft, ok_google, etc.)
                print(f"[Wake Detector] Custom model not found at {custom_model_path}. Trying built-in openWakeWord models...")
                try:
                    self.oww_model = Model(wakeword_models=[model_name], inference_framework="onnx")
                    print(f"[Wake Detector] Loaded built-in openWakeWord model: {model_name}")
                except Exception as e:
                    print(f"[Wake Detector Error] Failed to load built-in model {model_name}: {e}")
                    print("[Wake Detector] Falling back to 'google' wake word engine.")
                    self.engine = "google"
                    
        except ImportError:
            print("[Wake Detector Warning] 'openwakeword' or 'numpy' is not installed in virtual environment.")
            print("[Wake Detector] Falling back to 'google' wake word engine.")
            self.engine = "google"

    def start(self):
        """Starts the background wake word listener loop."""
        # OpenWakeWord directly uses PyAudio and doesn't require speech_recognition microphone object
        if self.engine == "google" and not (self.stt_manager.recognizer is not None and self.stt_manager.microphone is not None):
            print("[Wake Detector] Mic unavailable, wake word detection disabled. Trigger via web portal instead.")
            return
            
        self.is_listening = True
        self.thread = threading.Thread(target=self._listener_loop, name="WakeWordDetectorLoop")
        self.thread.daemon = True
        self.thread.start()
        print(f"[Wake Detector] Background wake word detector ({self.engine} engine) started.")

    def stop(self):
        """Stops the listener loop."""
        self.is_listening = False
        if self.thread:
            self.thread.join(timeout=1.0)
        print("[Wake Detector] Background wake word detector stopped.")

    def _listener_loop(self):
        """Dispatches to the selected wake word detection engine."""
        if self.engine == "openwakeword" and self.oww_model is not None:
            self._openwakeword_listener_loop()
        else:
            self._google_listener_loop()

    def _openwakeword_listener_loop(self):
        import pyaudio
        import numpy as np
        
        # Audio stream parameters required by openWakeWord
        RATE = 16000
        CHANNELS = 1
        FORMAT = pyaudio.paInt16
        CHUNK = 1280 # 80ms chunk
        
        p = pyaudio.PyAudio()
        stream = None
        
        try:
            stream = p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK
            )
            print("[Wake Detector] openWakeWord listener active. Monitoring audio...")
        except Exception as e:
            print(f"[Wake Detector Error] Failed to open PyAudio input stream for openWakeWord: {e}")
            self.is_listening = False
            p.terminate()
            return
            
        try:
            # oww_model.prediction_accumulators keys are the names of the loaded models (filenames or built-ins)
            model_keys = list(self.oww_model.prediction_accumulators.keys())
            print(f"[Wake Detector] Monitoring for custom wake words: {model_keys}")
            
            while self.is_listening:
                try:
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    if not data:
                        time.sleep(0.01)
                        continue
                        
                    # Convert raw bytes to Float32/Int16 numpy array
                    audio_frame = np.frombuffer(data, dtype=np.int16)
                    
                    # Run prediction
                    prediction = self.oww_model.predict(audio_frame)
                    
                    # Check threshold (usually 0.5 is a good baseline)
                    for model_key in model_keys:
                        score = prediction.get(model_key, 0.0)
                        if score > 0.5:
                            print(f"[Wake Detector] TRIGGERED! openWakeWord matched: \"{model_key}\" (score: {score:.2f})")
                            self.wake_callback()
                            time.sleep(5) # Prevent double trigger
                            break
                            
                except Exception as e:
                    time.sleep(0.1)
        finally:
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            p.terminate()
            print("[Wake Detector] openWakeWord listener stopped.")

    def _google_listener_loop(self):
        """Listens continuously to ambient noise. When volume spikes, validate using cloud STT."""
        import speech_recognition as sr
        import struct
        
        recognizer = self.stt_manager.recognizer
        microphone = self.stt_manager.microphone
        
        print("[Wake Detector] Calibrating ambient noise threshold...")
        try:
            with microphone as source:
                recognizer.adjust_for_ambient_noise(source, duration=1.0)
            print(f"[Wake Detector] Calibrated. Energy threshold set to: {recognizer.energy_threshold}")
        except Exception as e:
            print(f"[Wake Detector Error] Calibration failed: {e}")
            recognizer.energy_threshold = 300
            
        # Baseline threshold multiplier
        sensitivity_multiplier = self.config_manager.config.get("voice", {}).get("wake_sensitivity", 0.5)
        energy_trigger = recognizer.energy_threshold * (1.5 - sensitivity_multiplier)
        
        print(f"[Wake Detector] Monitoring audio... Trigger energy: {int(energy_trigger)}")
        
        while self.is_listening:
            cfg_wake_word = self.config_manager.config.get("voice", {}).get("wake_word", "jarvis").lower()
            lang = self.config_manager.config.get("voice", {}).get("language", "en-US")
            lang_lower = lang.lower()
            
            try:
                with microphone as source:
                    audio = recognizer.listen(source, timeout=1.0, phrase_time_limit=2.0)
                    
                raw_data = audio.get_raw_data()
                count = len(raw_data) // 2
                rms = 0.0
                if count > 0:
                    shorts = struct.unpack(f"{count}h", raw_data)
                    sum_squares = sum(s * s for s in shorts)
                    rms = (sum_squares / count) ** 0.5
                
                is_loud_noise = rms > 2500.0
                
                # Try to transcribe
                lang_code = "en-US"
                if "bn" in lang_lower:
                    lang_code = "bn-IN" if "in" in lang_lower else "bn-BD"
                elif "hi" in lang_lower:
                    lang_code = "hi-IN"
                else:
                    lang_code = "en-IN" if "in" in lang_lower else "en-US"
                    
                try:
                    text = recognizer.recognize_google(audio, language=lang_code).lower()
                    if text:
                        print(f"[Wake Detector] Heard: \"{text}\"")
                except sr.UnknownValueError:
                    text = ""
                except sr.RequestError as e:
                    print(f"[Wake Detector Error] Google Speech API request failed: {e}. Check your internet connection.")
                    text = ""
                except Exception:
                    text = ""
                
                # Check wake word matches
                is_wake_triggered = False
                
                bengali_matches = ["বন্ধু", "বন্ধুত্ব", "বন্ড", "বিন্দু", "বন্তু"]
                hindi_matches = ["बंधु", "बन्धु", "बन्दु", "बन्दू", "बांदु", "बंदू"]
                english_translit_matches = ["bandhu", "bondhu", "bando", "bandoo", "bambu", "bamboo", "bond"]
                
                standard_fallbacks = ["claw", "hello", "friend", "wake", "wake word"]
                
                all_possible_matches = english_translit_matches + standard_fallbacks
                if cfg_wake_word:
                    all_possible_matches.append(cfg_wake_word)
                    
                if "bn" in lang_lower:
                    all_possible_matches += bengali_matches
                elif "hi" in lang_lower:
                    all_possible_matches += hindi_matches
                else:
                    all_possible_matches += bengali_matches + hindi_matches
                    
                for match in all_possible_matches:
                    if match and match in text:
                        is_wake_triggered = True
                        break
                        
                if is_wake_triggered:
                    print(f"[Wake Detector] TRIGGERED! Heard wake word match in: \"{text}\"")
                    self.wake_callback()
                    time.sleep(5)
                elif is_loud_noise and self.noise_callback:
                    print(f"[Wake Detector] Loud noise detected (volume: {int(rms)}). Triggering snap callback.")
                    self.noise_callback(rms)
                    time.sleep(2.0)
                    
            except sr.WaitTimeoutError:
                pass
            except Exception:
                time.sleep(0.5)
            
            time.sleep(0.1)

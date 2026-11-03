import os
import time
import threading
import sys
import ctypes

def suppress_alsa_errors():
    """Silences low-level ALSA and JACK C library stderr warnings."""
    try:
        ERROR_HANDLER_FUNC = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p)
        def py_error_handler(filename, line, function, err, fmt):
            pass
        c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)
        asound = ctypes.cdll.LoadLibrary('libasound.so.2')
        asound.snd_lib_error_set_handler(c_error_handler)
    except Exception:
        pass

suppress_alsa_errors()


class WakeWordDetector:
    def __init__(self, config_manager, stt_manager, wake_callback, noise_callback=None):
        self.config_manager = config_manager
        self.stt_manager = stt_manager
        self.wake_callback = wake_callback
        self.noise_callback = noise_callback
        
        self.is_listening = False
        self.paused = False
        self.thread = None
        self.stream = None
        
        # Determine wake word engine ("swara", "openwakeword", "google")
        self.engine = self.config_manager.config.get("voice", {}).get("wake_word_engine", "swara").lower()
        self.oww_model = None
        self.swara_engine = None
        
        self.mic_available = True
        
        if self.engine in ("swara", "swara-kws"):
            self._init_swara_kws()
        elif self.engine == "openwakeword":
            self._init_openwakeword()

    def _init_swara_kws(self):
        """Initializes the Swara-KWS Few-Shot Metric Learning engine."""
        try:
            from voice.swara_kws.inference_engine import PrototypeEngine
            self.swara_engine = PrototypeEngine()
            self.engine = "swara"
            print(f"[Wake Detector] Swara-KWS Engine initialized with registered keywords: {list(self.swara_engine.prototypes.keys())}")
        except Exception as e:
            print(f"[Wake Detector Error] Failed to initialize Swara-KWS engine: {e}")
            print("[Wake Detector] Falling back to 'google' wake word engine.")
            self.engine = "google"

    def _init_openwakeword(self):
        try:
            import openwakeword
            try:
                openwakeword.utils.download_models()
            except Exception as e:
                print(f"[Wake Detector Warning] Failed to run download_models(): {e}")
                
            import numpy as np
            from openwakeword.model import Model
            
            wake_word_cfg = self.config_manager.config.get("voice", {}).get("wake_word", "jarvis")
            model_names = [name.strip().lower() for name in wake_word_cfg.split(",") if name.strip()]
            
            model_paths_or_names = []
            for model_name in model_names:
                custom_model_path = os.path.join(os.path.dirname(__file__), "models", f"{model_name}.onnx")
                if os.path.exists(custom_model_path):
                    print(f"[Wake Detector] Loading custom openWakeWord model from: {custom_model_path}")
                    model_paths_or_names.append(custom_model_path)
                else:
                    print(f"[Wake Detector] Custom model not found at {custom_model_path}. Loading built-in: {model_name}")
                    model_paths_or_names.append(model_name)
                    
            if model_paths_or_names:
                try:
                    self.oww_model = Model(wakeword_models=model_paths_or_names, inference_framework="onnx")
                    print(f"[Wake Detector] Loaded openWakeWord models: {model_paths_or_names}")
                except Exception as e:
                    print(f"[Wake Detector Error] Failed to load models {model_paths_or_names}: {e}")
                    self._fallback_to_swara_or_google()
            else:
                self._fallback_to_swara_or_google()
                    
        except ImportError:
            print("[Wake Detector Warning] 'openwakeword' is not installed in virtual environment.")
            self._fallback_to_swara_or_google()

    def _fallback_to_swara_or_google(self):
        """Attempts to fallback to Swara-KWS first before falling back to cloud Google STT."""
        print("[Wake Detector] Checking if Swara-KWS is available for local wake word spotting...")
        try:
            from voice.swara_kws.inference_engine import PrototypeEngine
            self._init_swara_kws()
            if self.swara_engine is not None:
                print("[Wake Detector] Successfully fell back to Swara-KWS offline wake word engine.")
                return
        except Exception:
            pass
        print("[Wake Detector] Falling back to 'google' wake word engine.")
        self.engine = "google"

    def start(self):
        """Starts the background wake word listener loop."""
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

    def pause(self):
        """Temporarily pauses wake word detection (releases mic/stream)."""
        self.paused = True
        if self.stream:
            try:
                if hasattr(self.stream, "stop_stream"):
                    self.stream.stop_stream()
                    self.stream.close()
                elif hasattr(self.stream, "stop"):
                    self.stream.stop()
                    self.stream.close()
            except Exception:
                pass
            self.stream = None
            print("[Wake Detector] Closed input stream synchronously on pause.")
        print("[Wake Detector] Suspended wake word detection.")

    def resume(self):
        """Resumes wake word detection."""
        self.paused = False
        print("[Wake Detector] Resumed wake word detection.")

    def _listener_loop(self):
        """Dispatches to the selected wake word detection engine."""
        if self.engine in ("swara", "swara-kws") and self.swara_engine is not None:
            self._swara_listener_loop()
        elif self.engine == "openwakeword" and self.oww_model is not None:
            self._openwakeword_listener_loop()
        else:
            self._google_listener_loop()

    def _swara_listener_loop(self):
        """Listens continuously using Swara-KWS metric prototype matching on streaming audio."""
        import numpy as np

        SAMPLE_RATE = 16000
        HOP_MS = 80
        HOP_SAMPLES = int(SAMPLE_RATE * (HOP_MS / 1000.0))
        BUFFER_SIZE = 16000  # 1.0 second sliding window

        sliding_buffer = np.zeros(BUFFER_SIZE, dtype=np.float32)

        # Get Swara KWS configuration
        voice_cfg = self.config_manager.config.get("voice", {})
        swara_cfg = voice_cfg.get("swara_kws", {})
        base_threshold = float(swara_cfg.get("threshold", 0.68))
        adaptive_thresh_on = bool(swara_cfg.get("adaptive_threshold", True))
        use_biometric_lock = bool(swara_cfg.get("use_biometric_lock", False))
        biometric_threshold = float(swara_cfg.get("biometric_threshold", 0.88))
        noise_margin = float(swara_cfg.get("noise_margin", 0.05))
        min_rms = float(swara_cfg.get("min_rms", 0.003))

        last_trigger_time = 0.0
        cooldown_period = 3.5  # seconds cooldown after successful trigger

        # Audio streaming using SoundDevice (primary) with PyAudio fallback
        use_sounddevice = False
        try:
            import sounddevice as sd
            use_sounddevice = True
        except ImportError:
            use_sounddevice = False

        print(f"[Wake Detector] Swara-KWS streaming active (Backend: {'sounddevice' if use_sounddevice else 'pyaudio'}, Threshold: {base_threshold:.2f}, Biometrics: {'ON' if use_biometric_lock else 'OFF'})")

        if use_sounddevice:
            def audio_cb(indata, frames, time_info, status):
                nonlocal sliding_buffer
                mono_chunk = indata[:, 0].copy()
                sliding_buffer[:-frames] = sliding_buffer[frames:]
                sliding_buffer[-frames:] = mono_chunk

            while self.is_listening:
                if self.paused:
                    if self.stream is not None:
                        try:
                            self.stream.stop()
                            self.stream.close()
                        except Exception:
                            pass
                        self.stream = None
                    time.sleep(0.1)
                    continue

                if self.stream is None:
                    try:
                        self.stream = sd.InputStream(
                            channels=1,
                            samplerate=SAMPLE_RATE,
                            blocksize=HOP_SAMPLES,
                            dtype="float32",
                            callback=audio_cb
                        )
                        self.stream.start()
                    except Exception as e:
                        print(f"[Wake Detector Error] sounddevice InputStream failed: {e}")
                        time.sleep(1.0)
                        continue

                # Process current buffer
                now = time.time()
                cfg_kw_str = self.config_manager.config.get("voice", {}).get("wake_word", "bondhu")
                target_whitelist = [w.strip().lower() for w in cfg_kw_str.split(",") if w.strip()]

                # Loud noise reaction
                sound_enabled = self.config_manager.config.get("voice", {}).get("sound_reactions", True)
                if sound_enabled and self.noise_callback:
                    rms = np.sqrt(np.mean(sliding_buffer**2))
                    if rms > 0.08:  # approx equivalent to int16 2600
                        print(f"[Wake Detector] Loud noise detected (volume RMS: {rms:.3f}). Triggering snap callback.")
                        self.noise_callback(rms * 32768.0)
                        time.sleep(1.5)

                matched_kw, sim, all_scores, rejection_reason, live_thresh, spk_sim = self.swara_engine.match(
                    sliding_buffer,
                    threshold=base_threshold,
                    margin_over_noise=noise_margin,
                    min_rms=min_rms,
                    adaptive_threshold=adaptive_thresh_on,
                    use_biometric_lock=use_biometric_lock,
                    biometric_threshold=biometric_threshold,
                    target_whitelist=target_whitelist if target_whitelist else None
                )

                if matched_kw and (now - last_trigger_time) > cooldown_period:
                    last_trigger_time = now
                    bio_str = f" | Biometric: {spk_sim:.2f}" if use_biometric_lock else ""
                    print(f"\n[Wake Detector] >>> TRIGGERED! Swara-KWS matched: '{matched_kw}' (Confidence: {sim:.3f}{bio_str}, Live Thresh: {live_thresh:.2f}) <<<")
                    self.wake_callback()
                    time.sleep(cooldown_period)

                time.sleep(HOP_MS / 1000.0)

            if self.stream is not None:
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None

        else:
            # PyAudio fallback path
            import pyaudio
            p = pyaudio.PyAudio()
            FORMAT = pyaudio.paInt16
            CHANNELS = 1

            try:
                while self.is_listening:
                    if self.paused:
                        if self.stream is not None:
                            try:
                                self.stream.stop_stream()
                                self.stream.close()
                            except Exception:
                                pass
                            self.stream = None
                        time.sleep(0.1)
                        continue

                    if self.stream is None:
                        try:
                            self.stream = p.open(format=FORMAT, channels=CHANNELS, rate=SAMPLE_RATE, input=True, frames_per_buffer=HOP_SAMPLES)
                        except Exception as e:
                            print(f"[Wake Detector Error] PyAudio open stream failed: {e}")
                            time.sleep(1.0)
                            continue

                    try:
                        data = self.stream.read(HOP_SAMPLES, exception_on_overflow=False)
                        if not data:
                            time.sleep(0.01)
                            continue
                        chunk_int16 = np.frombuffer(data, dtype=np.int16)
                        chunk_float = chunk_int16.astype(np.float32) / 32768.0

                        sliding_buffer[:-HOP_SAMPLES] = sliding_buffer[HOP_SAMPLES:]
                        sliding_buffer[-HOP_SAMPLES:] = chunk_float

                        now = time.time()
                        cfg_kw_str = self.config_manager.config.get("voice", {}).get("wake_word", "bondhu")
                        target_whitelist = [w.strip().lower() for w in cfg_kw_str.split(",") if w.strip()]

                        matched_kw, sim, all_scores, rejection_reason, live_thresh, spk_sim = self.swara_engine.match(
                            sliding_buffer,
                            threshold=base_threshold,
                            margin_over_noise=noise_margin,
                            min_rms=min_rms,
                            adaptive_threshold=adaptive_thresh_on,
                            use_biometric_lock=use_biometric_lock,
                            biometric_threshold=biometric_threshold,
                            target_whitelist=target_whitelist if target_whitelist else None
                        )

                        if matched_kw and (now - last_trigger_time) > cooldown_period:
                            last_trigger_time = now
                            print(f"\n[Wake Detector] >>> TRIGGERED! Swara-KWS matched: '{matched_kw}' (Score: {sim:.3f}) <<<")
                            self.wake_callback()
                            time.sleep(cooldown_period)

                    except Exception as e:
                        time.sleep(0.05)

            finally:
                if self.stream is not None:
                    try:
                        self.stream.stop_stream()
                        self.stream.close()
                    except Exception:
                        pass
                p.terminate()

        print("[Wake Detector] Swara-KWS listener loop stopped.")

    def _openwakeword_listener_loop(self):
        import pyaudio
        import numpy as np
        
        RATE = 16000
        CHANNELS = 1
        FORMAT = pyaudio.paInt16
        CHUNK = 1280
        
        p = pyaudio.PyAudio()
        self.stream = None
        opened_rate = 16000
        
        def open_stream(pa_instance):
            rates_to_try = [16000, 48000, 44100]
            for r in rates_to_try:
                try:
                    s = pa_instance.open(
                        format=FORMAT,
                        channels=CHANNELS,
                        rate=r,
                        input=True,
                        frames_per_buffer=int(CHUNK * r / RATE)
                    )
                    return s, r
                except Exception:
                    continue
            return None, RATE

        try:
            dummy_frame = np.zeros(CHUNK, dtype=np.int16)
            initial_prediction = self.oww_model.predict(dummy_frame)
            model_keys = list(initial_prediction.keys())
            print(f"[Wake Detector] Monitoring for custom wake words: {model_keys}")
            
            while self.is_listening:
                if self.paused:
                    if self.stream:
                        try:
                            self.stream.stop_stream()
                            self.stream.close()
                        except Exception:
                            pass
                        self.stream = None
                    time.sleep(0.1)
                    continue
                
                if not self.stream:
                    self.stream, opened_rate = open_stream(p)
                    if not self.stream:
                        print("[Wake Detector Error] Failed to open PyAudio stream.")
                        time.sleep(1.0)
                        continue
                
                try:
                    read_size = int(CHUNK * opened_rate / RATE)
                    data = self.stream.read(read_size, exception_on_overflow=False)
                    if not data:
                        time.sleep(0.01)
                        continue
                        
                    audio_frame = np.frombuffer(data, dtype=np.int16)
                    
                    sound_enabled = self.config_manager.config.get("voice", {}).get("sound_reactions", True)
                    if sound_enabled and self.noise_callback and len(audio_frame) > 0:
                        rms = np.sqrt(np.mean(audio_frame.astype(np.float32)**2))
                        if rms > 2500.0:
                            self.noise_callback(rms)
                            time.sleep(2.0)
                    
                    if len(audio_frame) != CHUNK and len(audio_frame) > 0:
                        indices = np.linspace(0, len(audio_frame) - 1, CHUNK)
                        audio_frame = np.interp(indices, np.arange(len(audio_frame)), audio_frame).astype(np.int16)
                    
                    prediction = self.oww_model.predict(audio_frame)
                    
                    for model_key in model_keys:
                        score = prediction.get(model_key, 0.0)
                        if score > 0.5:
                            print(f"[Wake Detector] TRIGGERED! openWakeWord matched: \"{model_key}\" (score: {score:.2f})")
                            self.wake_callback()
                            time.sleep(5)
                            break
                            
                except Exception as e:
                    time.sleep(0.1)
        finally:
            if self.stream:
                try:
                    self.stream.stop_stream()
                    self.stream.close()
                except Exception:
                    pass
            p.terminate()

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
            
        sensitivity_multiplier = self.config_manager.config.get("voice", {}).get("wake_sensitivity", 0.5)
        energy_trigger = recognizer.energy_threshold * (1.5 - sensitivity_multiplier)
        
        print(f"[Wake Detector] Monitoring audio... Trigger energy: {int(energy_trigger)}")
        
        while self.is_listening:
            if self.paused:
                time.sleep(0.1)
                continue
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
                
                is_loud_noise = rms > 2500.0 and self.config_manager.config.get("voice", {}).get("sound_reactions", True)
                
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
                    print(f"[Wake Detector Error] Google Speech API request failed: {e}")
                    text = ""
                except Exception:
                    text = ""
                
                is_wake_triggered = False
                bengali_matches = ["বন্ধু", "বন্ধুত্ব", "বন্ড", "বিন্দু", "বন্তু"]
                hindi_matches = ["बंधु", "बन्धु", "बन्दु", "बन्दू", "बांदु", "बंदू"]
                english_translit_matches = ["bandhu", "bondhu", "bando", "bandoo", "bambu", "bamboo", "bond"]
                standard_fallbacks = ["claw", "hello", "friend", "wake", "wake word"]
                
                all_possible_matches = english_translit_matches + standard_fallbacks
                if cfg_wake_word:
                    configured_words = [w.strip() for w in cfg_wake_word.split(",") if w.strip()]
                    all_possible_matches.extend(configured_words)
                    
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
            except Exception as e:
                time.sleep(0.5)
            
            time.sleep(0.1)

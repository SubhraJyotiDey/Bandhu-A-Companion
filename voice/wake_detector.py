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
        self.mic_available = (self.stt_manager.recognizer is not None and self.stt_manager.microphone is not None)

    def start(self):
        """Starts the background wake word listener loop."""
        if not self.mic_available:
            print("[Wake Detector] Mic unavailable, wake word detection disabled. Trigger via web portal instead.")
            return
            
        self.is_listening = True
        self.thread = threading.Thread(target=self._listener_loop, name="WakeWordDetectorLoop")
        self.thread.daemon = True
        self.thread.start()
        print("[Wake Detector] Background wake word detector started.")

    def stop(self):
        """Stops the listener loop."""
        self.is_listening = False
        if self.thread:
            self.thread.join(timeout=1.0)
        print("[Wake Detector] Background wake word detector stopped.")

    def _listener_loop(self):
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
        # Map 0..1 sensitivity to energy multiplier: 0.1 (sensitive) to 2.5 (hard to trigger)
        energy_trigger = recognizer.energy_threshold * (1.5 - sensitivity_multiplier)
        
        print(f"[Wake Detector] Monitoring audio... Trigger energy: {int(energy_trigger)}")
        
        while self.is_listening:
            # Refresh config settings
            cfg_wake_word = self.config_manager.config.get("voice", {}).get("wake_word", "jarvis").lower()
            lang = self.config_manager.config.get("voice", {}).get("language", "en-US")
            
            try:
                with microphone as source:
                    # Listen for a very short duration (1.5 seconds max) to catch quick wake words
                    audio = recognizer.listen(source, timeout=1.0, phrase_time_limit=2.0)
                    
                # Calculate volume (RMS) of raw audio bytes
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
                lang_lower = lang.lower()
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
                    # Speech was unintelligible (too quiet, noise, or muffled)
                    text = ""
                except sr.RequestError as e:
                    print(f"[Wake Detector Error] Google Speech API request failed: {e}. Check your internet connection.")
                    text = ""
                except Exception as e:
                    text = ""
                
                # Check wake word matches
                is_wake_triggered = False
                
                # Matches for "bandhu" / "bondhu" (Bengali/Hindi for Friend)
                bengali_matches = ["বন্ধু", "বন্ধুত্ব", "বন্ড", "বিন্দু", "বন্তু"]
                hindi_matches = ["बंधु", "बन्धु", "बन्दु", "बन्दू", "बांदु", "बंदू"]
                english_translit_matches = ["bandhu", "bondhu", "bando", "bandoo", "bambu", "bamboo", "bond"]
                
                # Standard fallback triggers
                standard_fallbacks = ["claw", "hello", "friend", "wake", "wake word"]
                
                # Combine all possible matches to trigger
                all_possible_matches = english_translit_matches + standard_fallbacks
                if cfg_wake_word:
                    all_possible_matches.append(cfg_wake_word)
                    
                if "bn" in lang_lower:
                    all_possible_matches += bengali_matches
                elif "hi" in lang_lower:
                    all_possible_matches += hindi_matches
                else:
                    # In English mode, also check native scripts just in case Google STT transcribes it directly
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
                # Normal timeout when no speech is detected
                pass
            except Exception as e:
                # General error, sleep briefly to prevent tight loops
                time.sleep(0.5)
            
            time.sleep(0.1)


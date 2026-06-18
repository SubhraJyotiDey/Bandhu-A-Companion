import os
import sys
import subprocess
import threading
import time

class PlaybackProcessWrapper:
    pass

class TTSManager:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.config = config_manager.config
        
        # Determine language and provider from config
        self.language = self.config.get("voice", {}).get("language", "en-US")
        self.provider = self.config.get("voice", {}).get("tts_provider", "edge-tts")
        
        # Audio lock to prevent speech overlapping
        self.speech_lock = threading.Lock()
        self.is_speaking = False
        
        # Cache paths
        self.fillers_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fillers")
        self.system_cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_cache")
        
        # Temporary file path for audio outputs
        self.temp_audio_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
            "temp_speech"
        )
        
        # Track current playback process to support sudden interruptions
        self.current_process = None
        
        # Pre-generate caches in the background
        self.pre_generate_all()

    def _get_voice_for_lang(self, lang):
        """Maps standard locale string to Edge-TTS voice names."""
        lang = lang.lower()
        if "bn" in lang: # Bangla
            # bn-IN or bn-BD
            if "bd" in lang:
                return "bn-BD-PradeepNeural"
            return "bn-IN-TanishaaNeural"
        elif "hi" in lang: # Hindi
            return "hi-IN-MadhurNeural"
        else: # Default to English
            if "in" in lang:
                return "en-IN-PrabhatNeural"
            return "en-US-GuyNeural"

    def _get_gtts_lang_code(self, lang):
        """Maps standard locale string to gTTS language codes."""
        lang = lang.lower()
        if "bn" in lang:
            return "bn"
        elif "hi" in lang:
            return "hi"
        return "en"

    def play_audio(self, filepath):
        """Plays an audio file in a way that allows interruption."""
        if sys.platform.startswith("win"):
            # Windows audio playback via PowerShell (avoiding external libraries)
            try:
                ps_cmd = (
                    f"Add-Type -AssemblyName PresentationCore; "
                    f"$player = New-Object System.Windows.Media.MediaPlayer; "
                    f"$player.Open('{filepath}'); "
                    f"$player.Play(); "
                    f"while ($player.NaturalDuration.HasTimeSpan -eq $false) {{ Start-Sleep -Milliseconds 50 }}; "
                    f"Start-Sleep -Seconds ($player.NaturalDuration.TimeSpan.TotalSeconds + 0.5)"
                )
                self.current_process = subprocess.Popen(["powershell", "-Command", ps_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.current_process.wait()
            except Exception as e:
                print(f"[TTS Play Error] Windows playback failed: {e}")
            finally:
                self.current_process = None
        else:
            # Linux / Raspberry Pi audio playback
            try:
                if filepath.endswith(".mp3"):
                    self.current_process = subprocess.Popen(["mpg123", "-q", filepath], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    self.current_process = subprocess.Popen(["aplay", "-q", filepath], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.current_process.wait()
            except (subprocess.SubprocessError, FileNotFoundError):
                # Try fallback general command line audio players
                try:
                    self.current_process = subprocess.Popen(["play", "-q", filepath], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) # SoX
                    self.current_process.wait()
                except Exception:
                    try:
                        self.current_process = subprocess.Popen(["omxplayer", filepath], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        self.current_process.wait()
                    except Exception:
                        print(f"[TTS Play Error] No command line audio player (mpg123, aplay, play) succeeded on Pi.")
            finally:
                self.current_process = None

    def stop(self):
        """Immediately interrupts and terminates any currently playing speech."""
        self.is_speaking = False
        if self.current_process:
            try:
                self.current_process.terminate()
                self.current_process.kill()
                print("[TTS] Speech playback interrupted and stopped.")
            except Exception as e:
                print(f"[TTS Error] Failed to interrupt speech process: {e}")
            finally:
                self.current_process = None

    def speak(self, text, lang=None):
        """Synthesizes text to speech and plays it. Respects locks to avoid overlap."""
        if not text or not text.strip():
            return
            
        # Allow overriding language per sentence
        if lang is None:
            lang = self.config_manager.config.get("voice", {}).get("language", "en-US")
            
        provider = self.config_manager.config.get("voice", {}).get("tts_provider", "edge-tts")
        
        # Check cache first
        lang_key = "en"
        lang_lower = lang.lower()
        if "bn" in lang_lower:
            lang_key = "bn"
        elif "hi" in lang_lower:
            lang_key = "hi"
            
        import hashlib
        import re
        # Clean text of bracketed tags like [expression: happy] before hashing/caching
        cleaned_text = re.sub(r'\[.*?\]', '', text).strip()
        
        h = hashlib.md5(f"{lang_key}:{cleaned_text}".encode("utf-8")).hexdigest()
        cached_filepath = os.path.join(self.system_cache_dir, f"{h}.mp3")
        
        if os.path.exists(cached_filepath) and os.path.getsize(cached_filepath) > 0:
            print(f"[TTS Cache Hit] Playing cached system phrase: {cleaned_text}")
            self.is_speaking = True
            threading.Thread(target=self._play_file_thread, args=(cached_filepath,), daemon=True).start()
            return

        # Set speaking status immediately to prevent checking race condition before thread starts
        self.is_speaking = True
        
        # Start speech thread
        threading.Thread(target=self._speak_thread, args=(text, lang, provider), daemon=True).start()

    def _speak_thread(self, text, lang, provider):
        self.is_speaking = True
        try:
            with self.speech_lock:
                print(f"[TTS] Speaking in ({lang}) via ({provider}): {text}")
            
            # Clean text of tags like [expression: happy] or [mood: excited]
            import re
            cleaned_text = re.sub(r'\[.*?\]', '', text).strip()
            if not cleaned_text:
                return

            success = False
            
            # 1. Try Premium Edge TTS (online)
            if provider == "edge-tts" or not success:
                try:
                    import edge_tts
                    import asyncio
                    
                    voice = self._get_voice_for_lang(lang)
                    filepath = self.temp_audio_file + ".mp3"
                    
                    # Run asyncio code inside synchronous thread
                    async def amain():
                        communicate = edge_tts.Communicate(cleaned_text, voice)
                        await communicate.save(filepath)
                        
                    asyncio.run(amain())
                    
                    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                        self.play_audio(filepath)
                        success = True
                except Exception as e:
                    print(f"[TTS Warning] Edge-TTS failed: {e}. Trying fallback...")
                    
            # 2. Try Google Translate TTS (online)
            if (provider == "gtts" or not success):
                try:
                    from gtts import gTTS
                    filepath = self.temp_audio_file + ".mp3"
                    gtts_lang = self._get_gtts_lang_code(lang)
                    
                    tts = gTTS(text=cleaned_text, lang=gtts_lang)
                    tts.save(filepath)
                    
                    if os.path.exists(filepath):
                        self.play_audio(filepath)
                        success = True
                except Exception as e:
                    print(f"[TTS Warning] gTTS failed: {e}. Trying offline fallback...")

            # 3. Try Offline pyttsx3 (local)
            if not success:
                try:
                    import pyttsx3
                    engine = pyttsx3.init()
                    
                    # On Windows this uses SAPI5, on Linux it uses espeak
                    # Basic voice configuration
                    voices = engine.getProperty('voices')
                    
                    # Try to match voice by language code
                    matched_voice = False
                    lang_lower = lang.lower()
                    for voice in voices:
                        if lang_lower[:2] in voice.id.lower() or (voice.languages and any(lang_lower[:2] in l.lower() for l in voice.languages)):
                            engine.setProperty('voice', voice.id)
                            matched_voice = True
                            break
                            
                    # Default matching fallback if language not found
                    if not matched_voice and len(voices) > 0:
                        engine.setProperty('voice', voices[0].id)
                        
                    # Lower speed for better clarity
                    engine.setProperty('rate', 140)
                    
                    # Speak synchronously inside this thread
                    engine.say(cleaned_text)
                    engine.runAndWait()
                    success = True
                except Exception as e:
                    print(f"[TTS Error] Offline TTS failed: {e}. Cannot output audio.")

            # Cleanup temp files
            for ext in [".mp3", ".wav"]:
                f = self.temp_audio_file + ext
                if os.path.exists(f):
                    try:
                        os.remove(f)
                    except Exception:
                        pass
        finally:
            self.is_speaking = False

    def _play_file_thread(self, filepath):
        self.is_speaking = True
        try:
            with self.speech_lock:
                self.play_audio(filepath)
        finally:
            self.is_speaking = False

    def play_filler(self, lang):
        """Plays a random pre-cached filler for the given language asynchronously."""
        lang_key = "en"
        lang_lower = lang.lower()
        if "bn" in lang_lower:
            lang_key = "bn"
        elif "hi" in lang_lower:
            lang_key = "hi"
            
        import random
        idx = random.randint(0, 2)
        filepath = os.path.join(self.fillers_dir, f"{lang_key}_filler_{idx}.mp3")
        
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            print(f"[TTS Filler] Playing filler for {lang_key}: {filepath}")
            threading.Thread(target=self._play_file_thread, args=(filepath,), daemon=True).start()
            return True
        return False

    def pre_generate_all(self):
        """Asynchronously pre-generates filler and system audio cache files."""
        threading.Thread(target=self._pre_generate_thread, daemon=True).start()

    def _pre_generate_thread(self):
        # Create directories
        os.makedirs(self.fillers_dir, exist_ok=True)
        os.makedirs(self.system_cache_dir, exist_ok=True)
        
        # Check internet / edge-tts availability
        try:
            import edge_tts
            import asyncio
        except ImportError:
            print("[TTS Cache Warning] edge_tts is missing. Skipping cache generation.")
            return

        # Fillers definition (Kolkata dialect / Intellectual phrasing)
        fillers = {
            "en": ["Hmm, let me think...", "Okay, let's see...", "Right, let me check..."],
            "bn": ["হুম, ভাবনাচিন্তা করতে দাও কিছুটা...", "আচ্ছা, বিষয়টি একটু তলিয়ে দেখি...", "ঠিক আছে, আমি এক মিনিট দেখছি..."],
            "hi": ["हूँ, सोचने दो...", "अच्छा, देखता हूँ...", "एक मिनट..."]
        }
        
        # System phrases definition (Kolkata dialect / Intellectual phrasing)
        system_phrases = {
            "en": ["Yes, friend?", "Goodbye!", "Understood.", "I'm sorry, I couldn't reach my brain."],
            "bn": ["নমস্কার বন্ধু, বলুন?", "নমস্কার বন্ধু, আবার দেখা হবে!", "হ্যাঁ বন্ধু, আমি বুঝতে পেরেছি।", "দুঃখিত বন্ধু, আমি বিষয়টি ঠিক বুঝতে পারলাম না।", "দুঃখিত বন্ধু, আমি আমার মস্তিষ্কের সাথে সংযোগ স্থাপন করতে পারছি না।"],
            "hi": ["हाँ दोस्त?", "फिर मिलेंगे!", "समझ गया।", "माफ़ कीजिये, मैं समझ नहीं पाया।"]
        }

        async def synthesize(text, voice, path):
            try:
                communicate = edge_tts.Communicate(text, voice)
                await communicate.save(path)
            except Exception as e:
                # Silently fail if network/DNS is resolving slow
                pass

        try:
            loop = asyncio.new_event_loop()
            
            # Synthesize fillers
            for lang, phrases in fillers.items():
                voice = self._get_voice_for_lang(lang)
                for idx, text in enumerate(phrases):
                    filename = f"{lang}_filler_{idx}.mp3"
                    filepath = os.path.join(self.fillers_dir, filename)
                    if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
                        loop.run_until_complete(synthesize(text, voice, filepath))

            # Synthesize system phrases
            import hashlib
            for lang, phrases in system_phrases.items():
                voice = self._get_voice_for_lang(lang)
                for text in phrases:
                    h = hashlib.md5(f"{lang}:{text.strip()}".encode("utf-8")).hexdigest()
                    filepath = os.path.join(self.system_cache_dir, f"{h}.mp3")
                    if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
                        loop.run_until_complete(synthesize(text, voice, filepath))
                        
            loop.close()
            print("[TTS Cache] Pre-generation completed successfully.")
        except Exception as e:
            print(f"[TTS Cache Warning] Pre-generation thread encountered error: {e}")

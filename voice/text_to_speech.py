import os
import sys
import subprocess
import threading
import time

class TTSManager:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.config = config_manager.config
        
        # Determine language and provider from config
        self.language = self.config.get("voice", {}).get("language", "en-US")
        self.provider = self.config.get("voice", {}).get("tts_provider", "edge-tts")
        
        # Audio lock to prevent speech overlapping
        self.speech_lock = threading.Lock()
        
        # Temporary file path for audio outputs
        self.temp_audio_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
            "temp_speech"
        )

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
        """Plays an audio file on Windows or Linux/Pi in a lightweight, non-blocking way."""
        if sys.platform.startswith("win"):
            # Windows audio playback via PowerShell (avoiding external libraries)
            try:
                # Use Windows Media Player COM object inside PowerShell to play MP3
                ps_cmd = (
                    f"Add-Type -AssemblyName PresentationCore; "
                    f"$player = New-Object System.Windows.Media.MediaPlayer; "
                    f"$player.Open('{filepath}'); "
                    f"$player.Play(); "
                    f"while ($player.NaturalDuration.HasTimeSpan -eq $false) {{ Start-Sleep -Milliseconds 50 }}; "
                    f"Start-Sleep -Seconds ($player.NaturalDuration.TimeSpan.TotalSeconds + 0.5)"
                )
                subprocess.run(["powershell", "-Command", ps_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                print(f"[TTS Play Error] Windows playback failed: {e}")
        else:
            # Linux / Raspberry Pi audio playback
            # Check if mpg123 is installed (which we recommend for MP3)
            try:
                if filepath.endswith(".mp3"):
                    subprocess.run(["mpg123", "-q", filepath], check=True)
                else:
                    subprocess.run(["aplay", "-q", filepath], check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                # Try fallback general command line audio players
                try:
                    subprocess.run(["play", "-q", filepath], check=True) # SoX
                except Exception:
                    try:
                        subprocess.run(["omxplayer", filepath], check=True)
                    except Exception:
                        print(f"[TTS Play Error] No command line audio player (mpg123, aplay, play) succeeded on Pi.")

    def speak(self, text, lang=None):
        """Synthesizes text to speech and plays it. Respects locks to avoid overlap."""
        if not text or not text.strip():
            return
            
        # Allow overriding language per sentence
        if lang is None:
            lang = self.config_manager.config.get("voice", {}).get("language", "en-US")
            
        provider = self.config_manager.config.get("voice", {}).get("tts_provider", "edge-tts")
        
        # Start speech thread
        threading.Thread(target=self._speak_thread, args=(text, lang, provider), daemon=True).start()

    def _speak_thread(self, text, lang, provider):
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

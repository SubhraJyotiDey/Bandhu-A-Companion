#!/usr/bin/env python3
"""
enroll_swara.py - Interactive Wake Word & Keyword Enrollment Tool for Swara-KWS
Supports recording K clean audio samples from microphone or enrolling from WAV files.
Updates voice/swara_kws/prototypes/keyword_prototypes.json dynamically.
"""

import os
import sys
import argparse
import time
import ctypes
import numpy as np
import wave

# Add project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

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

from voice.swara_kws.inference_engine import PrototypeEngine
from voice.swara_kws.audio_features import SAMPLE_RATE, NUM_SAMPLES, load_audio

# Silencing ALSA warnings
try:
    ERROR_HANDLER_FUNC = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p)
    def py_error_handler(filename, line, function, err, fmt):
        pass
    c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)
    asound = ctypes.cdll.LoadLibrary('libasound.so.2')
    asound.snd_lib_error_set_handler(c_error_handler)
except Exception:
    pass


def record_sample(sample_idx, total_k, keyword, duration=1.5):
    """Interactively records a single keyword utterance from the microphone."""
    import pyaudio

    CHUNK = 1024
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = SAMPLE_RATE

    p = pyaudio.PyAudio()

    print(f"\n---------------------------------------------------------")
    print(f"[Sample {sample_idx}/{total_k}] Prepare to say: \"{keyword.upper()}\"")
    print("Countdown: 3...", end="", flush=True)
    time.sleep(0.7)
    print(" 2...", end="", flush=True)
    time.sleep(0.7)
    print(" 1...", end="", flush=True)
    time.sleep(0.7)
    print(f" >>> SPEAK NOW: \"{keyword.upper()}\"! <<<", flush=True)

    stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)

    frames = []
    num_chunks = int(RATE / CHUNK * duration)
    for _ in range(num_chunks):
        data = stream.read(CHUNK, exception_on_overflow=False)
        frames.append(data)

    print("✓ Recording captured!", flush=True)
    stream.stop_stream()
    stream.close()
    p.terminate()

    # Convert raw bytes to float32 in [-1.0, 1.0]
    audio_int16 = np.frombuffer(b"".join(frames), dtype=np.int16)
    audio_float = audio_int16.astype(np.float32) / 32768.0

    # Calculate RMS to ensure speech was captured
    rms = float(np.sqrt(np.mean(audio_float ** 2)))
    if rms < 0.003:
        print(f"⚠️ Warning: Audio level was very low (RMS={rms:.4f}). Speak louder or move closer to mic.")

    # Center speech window to exact NUM_SAMPLES (16000 samples / 1.0 sec)
    if len(audio_float) >= NUM_SAMPLES:
        # Find maximum energy region
        window_size = 800  # 50ms
        energy = np.convolve(audio_float ** 2, np.ones(window_size), mode='valid')
        peak_idx = int(np.argmax(energy)) + (window_size // 2)

        start = max(0, peak_idx - (NUM_SAMPLES // 2))
        end = start + NUM_SAMPLES
        if end > len(audio_float):
            end = len(audio_float)
            start = max(0, end - NUM_SAMPLES)

        processed_audio = audio_float[start:end]
        if len(processed_audio) < NUM_SAMPLES:
            processed_audio = np.pad(processed_audio, (0, NUM_SAMPLES - len(processed_audio)))
    else:
        # Pad with zeros
        pad_len = NUM_SAMPLES - len(audio_float)
        processed_audio = np.pad(audio_float, (0, pad_len))

    return processed_audio


def main():
    parser = argparse.ArgumentParser(description="Swara-KWS Keyword & Wake Word Enrollment Tool")
    parser.add_argument("--keyword", type=str, help="Name of keyword to enroll (e.g. bondhu, jarvis, swara)")
    parser.add_argument("--record", action="store_true", help="Record live samples from microphone")
    parser.add_argument("--k", type=int, default=3, help="Number of samples to record (default: 3)")
    parser.add_argument("--duration", type=float, default=1.5, help="Recording duration per sample in seconds (default: 1.5)")
    parser.add_argument("--multi-template", action="store_true", help="Cluster samples into dual centroids (for K >= 4)")
    parser.add_argument("--files", nargs="+", help="WAV audio files to enroll from disk instead of recording")
    parser.add_argument("--delete", type=str, help="Delete an enrolled keyword by name")
    parser.add_argument("--list", action="store_true", help="List all currently enrolled keywords")
    parser.add_argument("--save-wavs", type=str, help="Directory to save the recorded WAV samples")

    args = parser.parse_args()

    engine = PrototypeEngine()

    if args.list:
        print("\n=========================================================")
        print("  Enrolled Swara-KWS Keywords:")
        print("=========================================================")
        for kw, templates in engine.prototypes.items():
            print(f"  • {kw:<15} ({len(templates)} centroid{'s' if len(templates)>1 else ''})")
        print("=========================================================\n")
        return

    if args.delete:
        if engine.delete_keyword(args.delete):
            engine.save_prototypes()
            print(f"✓ Keyword '{args.delete}' deleted successfully.")
        else:
            print(f"❌ Keyword '{args.delete}' not found in prototypes.")
        return

    if not args.keyword:
        print("Error: Please specify --keyword <name> to enroll, or use --list to view keywords.")
        parser.print_help()
        sys.exit(1)

    keyword_name = args.keyword.strip().lower()
    audio_samples = []

    if args.files:
        print(f"[Swara-KWS] Loading {len(args.files)} audio file(s) for keyword '{keyword_name}'...")
        for f in args.files:
            if not os.path.exists(f):
                print(f"Error: File not found: {f}")
                sys.exit(1)
            audio = load_audio(f)
            audio_samples.append(audio)

    elif args.record:
        print(f"\n=========================================================")
        print(f"  Swara-KWS Dynamic Enrollment for keyword: \"{keyword_name.upper()}\"")
        print(f"  Total samples to record: {args.k}")
        print(f"=========================================================")

        # Create output dir for WAVs if requested
        wav_dir = args.save_wavs or os.path.join(PROJECT_ROOT, "voice", "swara_kws", "enrollments", keyword_name)
        os.makedirs(wav_dir, exist_ok=True)

        for i in range(1, args.k + 1):
            audio = record_sample(i, args.k, keyword_name, duration=args.duration)
            audio_samples.append(audio)

            # Save WAV copy for record
            wav_path = os.path.join(wav_dir, f"{keyword_name}_sample_{i}.wav")
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes((audio * 32767).astype(np.int16).tobytes())

            if i < args.k:
                time.sleep(0.5)

        print(f"\n[Swara-KWS] Recorded {len(audio_samples)} samples (saved copies to: {wav_dir})")

    else:
        print("Error: Specify either --record to record from mic, or --files to load existing WAVs.")
        sys.exit(1)

    # Enroll into PrototypeEngine
    print(f"[Swara-KWS] Extracting neural metric embeddings and computing centroid...")
    templates, consistency = engine.enroll_keyword(
        keyword_name, 
        audio_samples, 
        multi_template=args.multi_template
    )

    # Save to JSON
    engine.save_prototypes()

    # Safe print without unencodable emojis
    print("\n=========================================================")
    print(f"  [SUCCESS] Keyword '{keyword_name}' enrolled successfully!")
    print(f"  Centroids   : {len(templates)}")
    print(f"  Consistency : {consistency * 100:.1f}%")
    print(f"  Prototypes  : voice/swara_kws/prototypes/keyword_prototypes.json")
    print("=========================================================\n")


if __name__ == "__main__":
    main()

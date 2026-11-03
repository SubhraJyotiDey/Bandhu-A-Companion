#!/usr/bin/env python3
"""
enroll_swara.py - Dynamic Keyword & Speaker Biometric Registration CLI for Swara-KWS.

Usage:
  # 1. Enroll a new custom wake word via microphone (e.g. 3 repetitions):
  python scripts/enroll_swara.py --keyword bondhu --record --k 3

  # 2. Enroll with Multi-Template Centroids (Normal vs Fast):
  python scripts/enroll_swara.py --keyword bondhu --record --k 4 --multi_template

  # 3. Enroll Owner Biometric Voiceprint Lock (Dual-Lock):
  python scripts/enroll_swara.py --enroll_owner --record --k 3

  # 4. List all active registered keywords:
  python scripts/enroll_swara.py --list

  # 5. Delete an enrolled keyword:
  python scripts/enroll_swara.py --delete jarvis

  # 6. Live test detection in terminal:
  python scripts/enroll_swara.py --test
"""

import os
import sys
import argparse
import time
import numpy as np

# Ensure project root is in python path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from voice.swara_kws.inference_engine import PrototypeEngine
from voice.swara_kws.audio_features import SAMPLE_RATE, NUM_SAMPLES, AUDIO_DURATION, load_audio
from voice.swara_kws.speaker_biometrics import SpeakerBiometrics


def record_sample(duration=AUDIO_DURATION, sr=SAMPLE_RATE):
    """Records audio from microphone and returns 1D float32 numpy array."""
    try:
        import sounddevice as sd
        num_frames = int(duration * sr)
        recording = sd.rec(num_frames, samplerate=sr, channels=1, dtype="float32")
        sd.wait()
        return recording.flatten()
    except Exception as e:
        print(f"[Record Error via sounddevice: {e}], trying pyaudio...")
        import pyaudio
        p = pyaudio.PyAudio()
        stream = p.open(format=pyaudio.paInt16, channels=1, rate=sr, input=True, frames_per_buffer=1024)
        frames = []
        for _ in range(0, int(sr / 1024 * duration)):
            data = stream.read(1024, exception_on_overflow=False)
            frames.append(data)
        stream.stop_stream()
        stream.close()
        p.terminate()
        int16_arr = np.frombuffer(b"".join(frames), dtype=np.int16)
        return int16_arr.astype(np.float32) / 32768.0


def main():
    parser = argparse.ArgumentParser(description="Swara-KWS Dynamic Enrollment & Management Utility")
    parser.add_argument("--keyword", type=str, default=None, help="Keyword name to enroll (e.g. 'bondhu')")
    parser.add_argument("--k", type=int, default=3, help="Number of repetitions to record (default: 3)")
    parser.add_argument("--record", action="store_true", help="Record audio directly from microphone")
    parser.add_argument("--audio_files", nargs="+", default=None, help="List of sample audio files to enroll")
    parser.add_argument("--multi_template", action="store_true", help="Cluster samples into 2 centroids (Normal vs Fast)")
    parser.add_argument("--enroll_owner", action="store_true", help="Enroll owner biometric voiceprint profile")
    parser.add_argument("--list", action="store_true", help="List all currently registered keywords")
    parser.add_argument("--delete", type=str, default=None, help="Delete an enrolled keyword by name")
    parser.add_argument("--test", action="store_true", help="Run live microphone streaming test")
    parser.add_argument("--threshold", type=float, default=0.68, help="Detection threshold for test mode")

    args = parser.parse_args()

    engine = PrototypeEngine()

    if args.list:
        print("\n" + "=" * 60)
        print(f" Swara-KWS Active Registered Keywords ({len(engine.prototypes)} keywords):")
        print("=" * 60)
        if not engine.prototypes:
            print("  (No keywords registered yet)")
        for kw, t_list in engine.prototypes.items():
            print(f"  * [{kw:<14}] Centroids: {len(t_list)} | Vector Dim: {len(t_list[0])}")
        owner_path = os.path.join(ROOT_DIR, "voice", "swara_kws", "prototypes", "owner_voiceprint.json")
        if os.path.exists(owner_path):
            print(f"  * [BIOMETRIC LOCK] Owner voiceprint profile enrolled ({owner_path})")
        print("=" * 60 + "\n")
        return

    if args.delete:
        if engine.delete_keyword(args.delete):
            engine.save_prototypes()
            print(f"[Swara-KWS] Successfully deleted keyword '{args.delete}'.")
        else:
            print(f"[Swara-KWS] Keyword '{args.delete}' not found.")
        return

    if args.enroll_owner:
        sb = SpeakerBiometrics()
        samples = []
        print("\n" + "=" * 60)
        print(" Swara-KWS: Owner Biometric Voiceprint Enrollment")
        print(f" Speak naturally {args.k} times to capture your unique vocal tract timbre.")
        print("=" * 60 + "\n")
        if args.record:
            for i in range(1, args.k + 1):
                input(f"Press [ENTER] to record voice sample {i}/{args.k} (speak naturally for 1 sec)...")
                print("  Recording 1.0s...", end="", flush=True)
                audio = record_sample(AUDIO_DURATION)
                samples.append(audio)
                print(" [Captured]")
                time.sleep(0.3)
        elif args.audio_files:
            samples = args.audio_files
        else:
            print("Please specify --record or --audio_files.")
            return

        sb.enroll_owner(samples)
        print("[Swara-KWS] Owner biometric lock saved successfully!\n")
        return

    if args.keyword:
        kw = args.keyword.strip().lower()
        samples = []
        print("\n" + "=" * 60)
        print(f" Swara-KWS: Enrolling Keyword '{kw}'")
        print(f" Pronounce '{kw}' clearly {args.k} times.")
        print("=" * 60 + "\n")

        if args.record:
            for i in range(1, args.k + 1):
                input(f"Press [ENTER] and speak '{kw}' now (sample {i}/{args.k})...")
                print(f"  Recording 1.0s...", end="", flush=True)
                audio = record_sample(AUDIO_DURATION)
                samples.append(audio)
                print(" [Done]")
                time.sleep(0.3)
        elif args.audio_files:
            samples = args.audio_files
        else:
            print("Please specify --record or --audio_files.")
            return

        templates, consistency = engine.enroll_keyword(kw, samples, multi_template=args.multi_template)
        engine.save_prototypes()
        print(f"\n[Swara-KWS] Keyword '{kw}' registered successfully! (Consistency: {consistency:.3f})")
        print(f"[Swara-KWS] All active keywords now: {list(engine.prototypes.keys())}\n")
        return

    if args.test:
        print("\n" + "=" * 60)
        print(f" Swara-KWS Live Test Mode (Base Threshold: {args.threshold})")
        print(f" Active keywords: {list(engine.prototypes.keys())}")
        print(" Press Ctrl+C to stop.")
        print("=" * 60 + "\n")

        import sounddevice as sd
        hop_samples = int(SAMPLE_RATE * 0.08)  # 80ms hop
        buffer = np.zeros(NUM_SAMPLES, dtype=np.float32)

        def audio_callback(indata, frames, time_info, status):
            mono = indata[:, 0].copy()
            buffer[:-frames] = buffer[frames:]
            buffer[-frames:] = mono

        stream = sd.InputStream(channels=1, samplerate=SAMPLE_RATE, blocksize=hop_samples, dtype="float32", callback=audio_callback)
        last_trig = 0.0

        with stream:
            while True:
                now = time.time()
                matched, sim, scores, reason, live_thresh, spk_sim = engine.match(
                    buffer,
                    threshold=args.threshold,
                    adaptive_threshold=True,
                    use_biometric_lock=False
                )
                target_scores = {k: v for k, v in scores.items() if not k.startswith(("bg", "noise"))}
                top_kw = max(target_scores, key=target_scores.get) if target_scores else "none"

                if matched and (now - last_trig) > 2.0:
                    last_trig = now
                    print(f"\n\033[1;92m>>> TRIGGER DETECTED! Keyword: '{matched}' (Score: {sim:.3f}) <<<\033[0m")

                status_txt = f"\r[Listen] Top: {top_kw:<8} Score: {sim:+.2f} (Tr:{live_thresh:.2f})"
                sys.stdout.write(status_txt)
                sys.stdout.flush()
                time.sleep(0.08)

    parser.print_help()


if __name__ == "__main__":
    main()

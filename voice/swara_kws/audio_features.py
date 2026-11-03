"""
audio_features.py - Standardized audio feature extraction for Swara-KWS Few-Shot Keyword Spotting.

Optimized for ultra-fast I/O and microcontroller feature extraction parity:
- Sample Rate: 16,000 Hz
- Duration: 1.0 second (16,000 samples)
- Window: 30 ms (480 samples)
- Hop / Stride: 20 ms (320 samples)
- Mel bands: 40
- Output feature matrix: (40, 49) representing (mel_bins, time_frames)
"""

import numpy as np
import soundfile as sf
import librosa
from scipy import signal
import torch

SAMPLE_RATE = 16000
AUDIO_DURATION = 1.0  # seconds
NUM_SAMPLES = int(SAMPLE_RATE * AUDIO_DURATION)  # 16000
N_MFCC = 40
N_MELS = 40
N_FFT = 512
WIN_LENGTH = 480  # 30 ms
HOP_LENGTH = 320  # 20 ms


def load_audio(file_path, target_sr=SAMPLE_RATE, target_duration=AUDIO_DURATION):
    """
    Fast audio loader using soundfile with resampling and mono conversion.
    """
    try:
        data, sr = sf.read(file_path, dtype="float32")
    except Exception:
        data, sr = librosa.load(file_path, sr=target_sr, mono=True)
        return _format_audio_length(data, target_sr, target_duration)

    # Convert to mono if multi-channel
    if data.ndim > 1:
        data = np.mean(data, axis=1)

    # Resample if sample rate does not match target_sr
    if sr != target_sr:
        num_resampled = int(len(data) * (target_sr / sr))
        data = signal.resample(data, num_resampled).astype(np.float32)

    return _format_audio_length(data, target_sr, target_duration)


def _format_audio_length(data, target_sr, target_duration):
    expected_len = int(target_sr * target_duration)
    if len(data) > expected_len:
        # Center crop
        start = (len(data) - expected_len) // 2
        data = data[start : start + expected_len]
    elif len(data) < expected_len:
        pad_before = (expected_len - len(data)) // 2
        pad_after = expected_len - len(data) - pad_before
        data = np.pad(data, (pad_before, pad_after), mode="constant")
    return data.astype(np.float32)


def extract_features(audio_buffer, sr=SAMPLE_RATE, feature_type="log_mel"):
    """
    Extracts 2D acoustic feature grid from 1D audio buffer.
    Returns: (40, 49) float32 matrix.
    """
    expected_len = int(sr * AUDIO_DURATION)
    if len(audio_buffer) < expected_len:
        audio_buffer = np.pad(audio_buffer, (0, expected_len - len(audio_buffer)), mode="constant")
    elif len(audio_buffer) > expected_len:
        audio_buffer = audio_buffer[-expected_len:]

    # Pre-emphasis filter
    pre_emphasis = 0.97
    emphasized = np.append(audio_buffer[0], audio_buffer[1:] - pre_emphasis * audio_buffer[:-1])

    if feature_type == "mfcc":
        features = librosa.feature.mfcc(
            y=emphasized,
            sr=sr,
            n_mfcc=N_MFCC,
            n_fft=N_FFT,
            hop_length=HOP_LENGTH,
            win_length=WIN_LENGTH,
            n_mels=N_MELS,
            fmin=20,
            fmax=4000,
        )
    else:
        mel_spec = librosa.feature.melspectrogram(
            y=emphasized,
            sr=sr,
            n_fft=N_FFT,
            hop_length=HOP_LENGTH,
            win_length=WIN_LENGTH,
            n_mels=N_MELS,
            fmin=20,
            fmax=4000,
            power=2.0,
        )
        features = librosa.power_to_db(mel_spec, ref=np.max)

    # Fixed grid size (40, 49)
    if features.shape[1] > 49:
        features = features[:, :49]
    elif features.shape[1] < 49:
        features = np.pad(features, ((0, 0), (0, 49 - features.shape[1])), mode="edge")

    # Standardize
    mean = np.mean(features)
    std = np.std(features) + 1e-6
    features = (features - mean) / std

    return features.astype(np.float32)


def audio_to_tensor(audio_buffer, sr=SAMPLE_RATE, feature_type="log_mel"):
    feat = extract_features(audio_buffer, sr=sr, feature_type=feature_type)
    return torch.from_numpy(feat).unsqueeze(0).unsqueeze(0)

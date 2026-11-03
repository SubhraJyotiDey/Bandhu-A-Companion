"""
audio_features.py - Standardized audio feature extraction for Swara-KWS Few-Shot Keyword Spotting.

Optimized for ultra-fast I/O and microcontroller feature extraction parity:
- Sample Rate: 16,000 Hz
- Duration: 1.0 second (16,000 samples)
- Window: 30 ms (480 samples)
- Hop / Stride: 20 ms (320 samples)
- Mel bands: 40
- Output feature matrix: (40, 49) representing (mel_bins, time_frames)

Runs on standard NumPy alone without requiring librosa, scipy, or soundfile!
"""

import os
import numpy as np

SAMPLE_RATE = 16000
AUDIO_DURATION = 1.0  # seconds
NUM_SAMPLES = int(SAMPLE_RATE * AUDIO_DURATION)  # 16000
N_MFCC = 40
N_MELS = 40
N_FFT = 512
WIN_LENGTH = 480  # 30 ms
HOP_LENGTH = 320  # 20 ms

# Precomputed Mel filterbank path
MEL_FILTER_PATH = os.path.join(os.path.dirname(__file__), "mel_filters.npy")
_MEL_BASIS = None

# Precomputed Hann window centered in 512-FFT frame
_WIN_RAW = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(WIN_LENGTH) / WIN_LENGTH)
_WINDOW_512 = np.zeros(N_FFT, dtype=np.float32)
_WINDOW_512[(N_FFT - WIN_LENGTH) // 2 : (N_FFT - WIN_LENGTH) // 2 + WIN_LENGTH] = _WIN_RAW


def get_mel_filterbank():
    """Loads or calculates 40x257 Mel filterbank matrix in pure NumPy."""
    global _MEL_BASIS
    if _MEL_BASIS is not None:
        return _MEL_BASIS

    if os.path.exists(MEL_FILTER_PATH):
        try:
            _MEL_BASIS = np.load(MEL_FILTER_PATH)
            return _MEL_BASIS
        except Exception:
            pass

    # Analytical Mel Filterbank calculation
    def hz_to_mel(hz):
        return 2595.0 * np.log10(1.0 + hz / 700.0)

    def mel_to_hz(mel):
        return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

    min_mel = hz_to_mel(20.0)
    max_mel = hz_to_mel(4000.0)
    mel_points = np.linspace(min_mel, max_mel, N_MELS + 2)
    hz_points = mel_to_hz(mel_points)
    bins = np.floor((N_FFT + 1) * hz_points / SAMPLE_RATE).astype(int)

    weights = np.zeros((N_MELS, N_FFT // 2 + 1), dtype=np.float32)
    for m in range(1, N_MELS + 1):
        f_m_minus = bins[m - 1]
        f_m = bins[m]
        f_m_plus = bins[m + 1]

        for k in range(f_m_minus, f_m):
            if f_m != f_m_minus:
                weights[m - 1, k] = (k - bins[m - 1]) / (bins[m] - bins[m - 1])
        for k in range(f_m, f_m_plus):
            if f_m_plus != f_m:
                weights[m - 1, k] = (bins[m + 1] - k) / (bins[m + 1] - bins[m])

    # Normalize area
    enorm = 2.0 / (hz_points[2 : N_MELS + 2] - hz_points[:N_MELS])
    weights *= enorm[:, np.newaxis]
    _MEL_BASIS = weights.astype(np.float32)
    return _MEL_BASIS


def load_audio(file_path, target_sr=SAMPLE_RATE, target_duration=AUDIO_DURATION):
    """
    Audio loader supporting wave files and librosa/soundfile if available.
    """
    data = None
    sr = target_sr

    try:
        import soundfile as sf
        data, sr = sf.read(file_path, dtype="float32")
    except Exception:
        data = None

    if data is None:
        try:
            import librosa
            data, sr = librosa.load(file_path, sr=target_sr, mono=True)
            return _format_audio_length(data, target_sr, target_duration)
        except Exception:
            try:
                import wave
                with wave.open(file_path, 'rb') as wf:
                    sr = wf.getframerate()
                    n_frames = wf.getnframes()
                    frames = wf.readframes(n_frames)
                    data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            except Exception as e:
                raise RuntimeError(f"Could not load audio file '{file_path}': {e}")

    # Convert to mono if multi-channel
    if data.ndim > 1:
        data = np.mean(data, axis=1)

    # Resample if sample rate does not match target_sr using linear interpolation
    if sr != target_sr:
        num_resampled = int(len(data) * (target_sr / sr))
        indices = np.linspace(0, len(data) - 1, num_resampled)
        data = np.interp(indices, np.arange(len(data)), data).astype(np.float32)

    return _format_audio_length(data, target_sr, target_duration)


def _format_audio_length(data, target_sr, target_duration):
    expected_len = int(target_sr * target_duration)
    if len(data) > expected_len:
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
    Pure NumPy implementation matching Librosa spectrogram.
    """
    if not isinstance(audio_buffer, np.ndarray):
        audio_buffer = np.array(audio_buffer, dtype=np.float32)
    elif audio_buffer.dtype != np.float32:
        audio_buffer = audio_buffer.astype(np.float32)

    expected_len = int(sr * AUDIO_DURATION)
    if len(audio_buffer) < expected_len:
        audio_buffer = np.pad(audio_buffer, (0, expected_len - len(audio_buffer)), mode="constant")
    elif len(audio_buffer) > expected_len:
        audio_buffer = audio_buffer[-expected_len:]

    # Pre-emphasis filter
    pre_emphasis = 0.97
    emphasized = np.append(audio_buffer[0], audio_buffer[1:] - pre_emphasis * audio_buffer[:-1])

    # STFT using pure NumPy
    y_pad = np.pad(emphasized, N_FFT // 2, mode="constant")
    num_frames = 1 + (len(y_pad) - N_FFT) // HOP_LENGTH
    frame_indices = np.arange(N_FFT)[None, :] + np.arange(num_frames)[:, None] * HOP_LENGTH
    framed = y_pad[frame_indices] * _WINDOW_512
    spec = np.abs(np.fft.rfft(framed, n=N_FFT).T) ** 2.0  # (257, frames)

    # Mel Spectrogram
    mel_basis = get_mel_filterbank()
    mel_spec = np.dot(mel_basis, spec)

    # Power to dB (log-mel)
    features = 10.0 * np.log10(np.maximum(mel_spec, 1e-10))
    features -= np.max(features)

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
    try:
        import torch
        return torch.from_numpy(feat).unsqueeze(0).unsqueeze(0)
    except ImportError:
        return np.expand_dims(np.expand_dims(feat, 0), 0)

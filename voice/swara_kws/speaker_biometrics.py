"""
speaker_biometrics.py - Acoustic Vocal Tract Biometric Voiceprint Extractor.

Extracts a 64-dimensional speaker voiceprint capturing vocal tract anatomy:
- 24 MFCC cepstral means (vocal tract transfer function)
- 24 MFCC cepstral stds (articulatory dynamics)
- 7 Spectral Contrast subbands (timbre & resonance ratios)
- Spectral Centroid, Bandwidth, and Rolloff (3 dims)
- Fundamental Frequency F0 pitch mean and variance (6 dims)
- Total: 64 dimensions normalized on the unit hypersphere (||v||_2 = 1.0)
"""

import os
import json
import numpy as np

try:
    import librosa
except ImportError:
    librosa = None

try:
    from voice.swara_kws.audio_features import SAMPLE_RATE, NUM_SAMPLES, load_audio
except ImportError:
    try:
        from .audio_features import SAMPLE_RATE, NUM_SAMPLES, load_audio
    except ImportError:
        from audio_features import SAMPLE_RATE, NUM_SAMPLES, load_audio


class SpeakerBiometrics:
    def __init__(self, profile_path=None, embed_dim=64):
        if profile_path is None:
            profile_path = os.path.join(os.path.dirname(__file__), "prototypes", "owner_voiceprint.json")
        self.profile_path = profile_path
        self.embed_dim = embed_dim
        self.owner_voiceprint = None
        self.load_profile()

    def extract_voiceprint(self, audio_buffer):
        """
        Extracts 64-dimensional acoustic timbre voiceprint from 1-second audio buffer.
        """
        if isinstance(audio_buffer, str):
            audio_buffer = load_audio(audio_buffer)

        expected_len = NUM_SAMPLES
        if len(audio_buffer) < expected_len:
            audio_buffer = np.pad(audio_buffer, (0, expected_len - len(audio_buffer)), mode="constant")
        elif len(audio_buffer) > expected_len:
            audio_buffer = audio_buffer[-expected_len:]

        if librosa is None:
            # Pure NumPy lightweight 64-dim spectral timbre fallback
            spec = np.abs(np.fft.rfft(audio_buffer, n=512))[: self.embed_dim]
            norm = np.linalg.norm(spec)
            if norm > 1e-7:
                spec = spec / norm
            return spec.astype(np.float32)

        # 1. 24 MFCCs (excluding C0 energy to isolate vocal tract shape from loudness)
        mfcc = librosa.feature.mfcc(y=audio_buffer, sr=SAMPLE_RATE, n_mfcc=25)[1:]
        mfcc_mean = np.mean(mfcc, axis=1)  # 24 dims
        mfcc_std = np.std(mfcc, axis=1)    # 24 dims

        # 2. 7 Spectral Contrast subbands (relative harmonic peaks to valleys)
        contrast = np.mean(librosa.feature.spectral_contrast(y=audio_buffer, sr=SAMPLE_RATE), axis=1)  # 7 dims

        # 3. Spectral Shape Descriptors
        cent = float(np.mean(librosa.feature.spectral_centroid(y=audio_buffer, sr=SAMPLE_RATE))) / 3000.0
        bw = float(np.mean(librosa.feature.spectral_bandwidth(y=audio_buffer, sr=SAMPLE_RATE))) / 3000.0
        roll = float(np.mean(librosa.feature.spectral_rolloff(y=audio_buffer, sr=SAMPLE_RATE))) / 5000.0
        shape_feats = np.array([cent, bw, roll], dtype=np.float32)  # 3 dims

        # 4. Fast Pitch Lag Autocorrelation (Fundamental Frequency F0)
        corr = np.correlate(audio_buffer, audio_buffer, mode="full")
        corr = corr[len(corr) // 2 :]
        d = np.diff(corr)
        start = np.where(d > 0)[0]
        f0_feats = np.zeros(6, dtype=np.float32)  # 6 dims
        if len(start) > 0 and start[0] < len(corr) - 1:
            peak_idx = np.argmax(corr[start[0] : min(start[0] + 300, len(corr))]) + start[0]
            f0_feats[0] = float(peak_idx) / 100.0
            f0_feats[1] = corr[peak_idx] / (corr[0] + 1e-6)
            for h in range(2, 6):
                h_idx = int(peak_idx / h)
                if h_idx < len(corr):
                    f0_feats[h] = corr[h_idx] / (corr[0] + 1e-6)

        descriptor = np.concatenate([mfcc_mean, mfcc_std, contrast, shape_feats, f0_feats]).astype(np.float32)
        if len(descriptor) > self.embed_dim:
            descriptor = descriptor[: self.embed_dim]
        elif len(descriptor) < self.embed_dim:
            descriptor = np.pad(descriptor, (0, self.embed_dim - len(descriptor)))

        norm = np.linalg.norm(descriptor)
        if norm > 1e-7:
            descriptor /= norm

        return descriptor

    def enroll_owner(self, audio_samples):
        voiceprints = []
        for sample in audio_samples:
            vp = self.extract_voiceprint(sample)
            voiceprints.append(vp)

        voiceprints = np.array(voiceprints)
        mean_vp = np.mean(voiceprints, axis=0)
        norm = np.linalg.norm(mean_vp)
        self.owner_voiceprint = mean_vp / norm if norm > 1e-7 else mean_vp

        os.makedirs(os.path.dirname(self.profile_path), exist_ok=True)
        with open(self.profile_path, "w") as f:
            json.dump({"owner_voiceprint": self.owner_voiceprint.tolist()}, f, indent=2)

        k = len(voiceprints)
        sim_matrix = np.dot(voiceprints, voiceprints.T)
        mask = ~np.eye(k, dtype=bool)
        consistency = float(np.mean(sim_matrix[mask])) if k > 1 else 1.0

        print(f"[Swara-KWS] Enrolled owner voiceprint with {k} samples (consistency: {consistency:.3f}).")
        print(f"[Swara-KWS] Saved biometric profile to {self.profile_path}")
        return self.owner_voiceprint, consistency

    def verify_speaker(self, audio_buffer, threshold=0.92):
        if self.owner_voiceprint is None:
            return True, 1.0

        live_vp = self.extract_voiceprint(audio_buffer)
        similarity = float(np.dot(live_vp, self.owner_voiceprint))
        is_owner = similarity >= threshold
        return is_owner, similarity

    def load_profile(self):
        if os.path.exists(self.profile_path):
            try:
                with open(self.profile_path, "r") as f:
                    data = json.load(f)
                self.owner_voiceprint = np.array(data["owner_voiceprint"], dtype=np.float32)
                print(f"[Swara-KWS] Loaded owner voiceprint profile from {self.profile_path}")
            except Exception as e:
                print(f"[Swara-KWS] Could not load biometric profile: {e}")

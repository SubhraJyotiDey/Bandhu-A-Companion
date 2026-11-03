"""
Swara-KWS: Few-Shot Dynamic Metric Learning Keyword Spotting & Biometric Verification Package.
"""

from voice.swara_kws.inference_engine import PrototypeEngine
from voice.swara_kws.model import DSCNNBackbone
from voice.swara_kws.audio_features import extract_features, load_audio, SAMPLE_RATE, NUM_SAMPLES
from voice.swara_kws.speaker_biometrics import SpeakerBiometrics

__all__ = [
    "PrototypeEngine",
    "DSCNNBackbone",
    "extract_features",
    "load_audio",
    "SAMPLE_RATE",
    "NUM_SAMPLES",
    "SpeakerBiometrics",
]

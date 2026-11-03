"""
inference_engine.py - Dynamic Prototype Enrollment and Robust Metric Matching Engine for Swara-KWS.

Production-Grade Features:
1. Multi-Template Centroids: Supports multiple pronunciation modes per keyword (e.g. Normal vs Fast cadence).
2. Dynamic Noise-Floor Thresholding (SNR-Adaptive): Automatically adjusts threshold based on ambient background noise.
3. Online Adaptive Self-Learning (EMA): Continuously personalizes the prototype on high-confidence verified hits.
4. Speaker Verification Biometric Lock: Dual-lock security gating based on vocal tract timbre.
5. Multi-Backend Support:
   - TFLite / tflite_runtime (Ultra-fast, lightweight for Raspberry Pi Zero 2W / Microcontrollers, NO torch needed!)
   - PyTorch (when available on desktop)
"""

import os
import json
import numpy as np

try:
    from voice.swara_kws.audio_features import extract_features, load_audio, SAMPLE_RATE, NUM_SAMPLES
    from voice.swara_kws.speaker_biometrics import SpeakerBiometrics
except ImportError:
    try:
        from .audio_features import extract_features, load_audio, SAMPLE_RATE, NUM_SAMPLES
        from .speaker_biometrics import SpeakerBiometrics
    except ImportError:
        from audio_features import extract_features, load_audio, SAMPLE_RATE, NUM_SAMPLES
        from speaker_biometrics import SpeakerBiometrics

REJECTION_CLASS_PREFIXES = ("bg", "background", "noise", "silence", "_")
DEFAULT_TFLITE_PATH = os.path.join(os.path.dirname(__file__), "models", "kws_backbone_fp32.tflite")
DEFAULT_TORCH_PATH = os.path.join(os.path.dirname(__file__), "models", "kws_backbone.pth")
DEFAULT_PROTOTYPES_PATH = os.path.join(os.path.dirname(__file__), "prototypes", "keyword_prototypes.json")


class PrototypeEngine:
    def __init__(self, model_checkpoint=None, embed_dim=64, device="cpu", prototypes_path=None):
        self.embed_dim = embed_dim
        self.prototypes = {}  # {keyword_name: [list of np.ndarray shape (embed_dim,)]}
        self.anchor_prototypes = {}  # Initial prototypes for drift bounding
        
        self.backend = None
        self.model = None
        self.tflite_interpreter = None

        if prototypes_path is None:
            prototypes_path = DEFAULT_PROTOTYPES_PATH
        self.prototypes_path = prototypes_path

        # Biometric voiceprint verifier
        self.biometrics = SpeakerBiometrics()

        # Rolling ambient noise floor tracker
        self.rolling_noise_scores = []

        # Initialize backend (TFLite preferred for Pi / lightweight, Torch fallback)
        self._init_backend(model_checkpoint, device)

        if self.prototypes_path and os.path.exists(self.prototypes_path):
            self.load_prototypes(self.prototypes_path)

    def _init_backend(self, model_checkpoint=None, device="cpu"):
        """Attempts to load TFLite runtime first (for Pi), then PyTorch."""
        tflite_path = model_checkpoint if (model_checkpoint and model_checkpoint.endswith(".tflite")) else DEFAULT_TFLITE_PATH
        torch_path = model_checkpoint if (model_checkpoint and model_checkpoint.endswith(".pth")) else DEFAULT_TORCH_PATH

        # 1. Try TFLite (ai_edge_litert, tflite_runtime, or tensorflow)
        if os.path.exists(tflite_path):
            interpreter = None
            try:
                from ai_edge_litert.interpreter import Interpreter
                interpreter = Interpreter(model_path=tflite_path)
            except ImportError:
                try:
                    import tflite_runtime.interpreter as tflite
                    interpreter = tflite.Interpreter(model_path=tflite_path)
                except ImportError:
                    try:
                        import tensorflow as tf
                        interpreter = tf.lite.Interpreter(model_path=tflite_path)
                    except ImportError:
                        pass

            if interpreter is not None:
                try:
                    interpreter.allocate_tensors()
                    self.tflite_interpreter = interpreter
                    self.tflite_input_details = interpreter.get_input_details()
                    self.tflite_output_details = interpreter.get_output_details()
                    self.backend = "tflite"
                    print(f"[Swara-KWS] Loaded lightweight TFLite backbone from {tflite_path} (dim={self.embed_dim}).")
                    return
                except Exception as e:
                    print(f"[Swara-KWS Warning] TFLite allocation failed: {e}")

        # 2. Try PyTorch if available
        try:
            import torch
            from voice.swara_kws.model import DSCNNBackbone
            if os.path.exists(torch_path):
                self.device = torch.device(device)
                checkpoint = torch.load(torch_path, map_location=self.device, weights_only=False)
                self.embed_dim = checkpoint.get("embed_dim", self.embed_dim)
                self.model = DSCNNBackbone(embed_dim=self.embed_dim).to(self.device)
                self.model.load_state_dict(checkpoint["model_state_dict"])
                self.model.eval()
                for param in self.model.parameters():
                    param.requires_grad = False
                self.backend = "torch"
                print(f"[Swara-KWS] Loaded PyTorch backbone from {torch_path} (dim={self.embed_dim}).")
                return
        except ImportError:
            pass

        if self.backend is None:
            print("[Swara-KWS Warning] No ML runtime found (install 'tflite_runtime' via pip: 'pip install tflite-runtime')")

    def extract_embedding(self, audio_buffer):
        """Runs audio buffer through frozen backbone to generate unit embedding vector."""
        feat = extract_features(audio_buffer, sr=SAMPLE_RATE)  # (40, 49)

        if self.backend == "tflite" and self.tflite_interpreter is not None:
            # Reshape to (1, 40, 49, 1) float32
            inp_data = np.expand_dims(np.expand_dims(feat, 0), -1).astype(np.float32)
            self.tflite_interpreter.set_tensor(self.tflite_input_details[0]['index'], inp_data)
            self.tflite_interpreter.invoke()
            emb_np = self.tflite_interpreter.get_tensor(self.tflite_output_details[0]['index']).flatten()
            norm = np.linalg.norm(emb_np)
            if norm > 1e-12:
                emb_np = emb_np / norm
            return emb_np

        elif self.backend == "torch" and self.model is not None:
            import torch
            tensor = torch.from_numpy(feat).unsqueeze(0).unsqueeze(0).float().to(self.device)
            with torch.no_grad():
                emb = self.model(tensor)
            emb_np = emb.squeeze(0).cpu().numpy()
            norm = np.linalg.norm(emb_np)
            if norm > 1e-12:
                emb_np = emb_np / norm
            return emb_np

        raise RuntimeError("No working Swara-KWS inference backend available. Please install tflite-runtime.")

    def enroll_keyword(self, keyword_name, audio_samples, multi_template=False):
        """
        Enrolls a new keyword dynamically from K recorded audio samples.
        If multi_template is True, clusters samples into 2 centroids (Normal vs Fast/Casual).
        """
        embeddings = []
        for sample in audio_samples:
            if isinstance(sample, str):
                audio = load_audio(sample)
            else:
                audio = sample
            emb = self.extract_embedding(audio)
            embeddings.append(emb)

        embeddings = np.array(embeddings)
        k = len(embeddings)

        templates = []
        if multi_template and k >= 4:
            dist_mat = 1.0 - np.dot(embeddings, embeddings.T)
            i1, i2 = np.unravel_index(np.argmax(dist_mat), dist_mat.shape)
            c1_samples = [embeddings[i1]]
            c2_samples = [embeddings[i2]]

            for idx in range(k):
                if idx in (i1, i2):
                    continue
                d1 = 1.0 - np.dot(embeddings[idx], embeddings[i1])
                d2 = 1.0 - np.dot(embeddings[idx], embeddings[i2])
                if d1 < d2:
                    c1_samples.append(embeddings[idx])
                else:
                    c2_samples.append(embeddings[idx])

            for cluster in [c1_samples, c2_samples]:
                mean_v = np.mean(cluster, axis=0)
                norm = np.linalg.norm(mean_v)
                templates.append(mean_v / norm if norm > 1e-12 else mean_v)
            print(f"[Swara-KWS] Enrolled keyword '{keyword_name}' with 2 multi-template centroids.")
        else:
            if k > 2:
                sim_matrix = np.dot(embeddings, embeddings.T)
                mask = ~np.eye(k, dtype=bool)
                peer_sims = np.array([np.mean(sim_matrix[i, mask[i]]) for i in range(k)])
                weights = np.maximum(0.1, peer_sims)
                weights /= np.sum(weights)
                mean_v = np.sum(embeddings * weights[:, None], axis=0)
            else:
                mean_v = np.mean(embeddings, axis=0)
            norm = np.linalg.norm(mean_v)
            proto = mean_v / norm if norm > 1e-12 else mean_v
            templates.append(proto)
            print(f"[Swara-KWS] Enrolled keyword '{keyword_name}' with single centroid.")

        self.prototypes[keyword_name] = templates
        self.anchor_prototypes[keyword_name] = [t.copy() for t in templates]

        sim_matrix = np.dot(embeddings, embeddings.T)
        mask = ~np.eye(k, dtype=bool)
        consistency = float(np.mean(sim_matrix[mask])) if k > 1 else 1.0

        return templates, consistency

    def adapt_prototype(self, keyword_name, live_embedding, confidence, alpha=0.95, max_drift_cos=0.15):
        """Online Adaptive Self-Learning (EMA Update)."""
        if confidence < 0.85 or keyword_name not in self.prototypes:
            return False

        templates = self.prototypes[keyword_name]
        anchors = self.anchor_prototypes.get(keyword_name, templates)

        sims = [float(np.dot(live_embedding, t)) for t in templates]
        best_idx = int(np.argmax(sims))

        updated = alpha * templates[best_idx] + (1.0 - alpha) * live_embedding
        norm = np.linalg.norm(updated)
        if norm > 1e-12:
            updated /= norm

        anchor_sim = float(np.dot(updated, anchors[best_idx]))
        if (1.0 - anchor_sim) > max_drift_cos:
            return False

        self.prototypes[keyword_name][best_idx] = updated
        return True

    def calculate_adaptive_threshold(self, base_threshold=0.68, noise_margin=0.08):
        """Calculates SNR-adaptive live threshold based on ambient background noise history."""
        if not self.rolling_noise_scores:
            return base_threshold

        recent_noise = np.mean(self.rolling_noise_scores[-15:])
        adaptive_thresh = max(base_threshold, recent_noise + noise_margin)
        return float(np.clip(adaptive_thresh, 0.62, 0.82))

    def match(
        self,
        live_audio_buffer,
        threshold=0.68,
        margin_over_noise=0.05,
        min_rms=0.0035,
        adaptive_threshold=True,
        use_biometric_lock=False,
        biometric_threshold=0.88,
        target_whitelist=None,
    ):
        """Compares live audio against prototypes."""
        if not self.prototypes:
            return None, 0.0, {}, "no_prototypes", threshold, 1.0

        rms = float(np.sqrt(np.mean(live_audio_buffer ** 2)))
        if min_rms > 0 and rms < min_rms:
            return None, 0.0, {}, "silence", threshold, 1.0

        live_emb = self.extract_embedding(live_audio_buffer)

        all_scores = {}
        target_scores = {}
        noise_scores = {}

        for kw_name, template_list in self.prototypes.items():
            sims = [float(np.dot(live_emb, t)) for t in template_list]
            best_sim = max(sims)
            all_scores[kw_name] = best_sim

            is_noise = any(kw_name.lower().startswith(p) for p in REJECTION_CLASS_PREFIXES)
            if is_noise:
                noise_scores[kw_name] = best_sim
            else:
                if target_whitelist is None or kw_name.lower() in [w.lower() for w in target_whitelist]:
                    target_scores[kw_name] = best_sim

        if not target_scores:
            return None, 0.0, all_scores, "no_target_keywords", threshold, 1.0

        best_target_kw = max(target_scores, key=target_scores.get)
        best_target_sim = target_scores[best_target_kw]

        highest_noise_sim = max(noise_scores.values()) if noise_scores else -1.0
        if highest_noise_sim > -1.0:
            self.rolling_noise_scores.append(highest_noise_sim)
            if len(self.rolling_noise_scores) > 60:
                self.rolling_noise_scores.pop(0)

        live_thresh = self.calculate_adaptive_threshold(base_threshold=threshold) if adaptive_threshold else threshold

        speaker_verified = True
        speaker_sim = 1.0
        if use_biometric_lock:
            speaker_verified, speaker_sim = self.biometrics.verify_speaker(live_audio_buffer, threshold=biometric_threshold)

        if best_target_sim < live_thresh:
            return None, best_target_sim, all_scores, "low_confidence", live_thresh, speaker_sim

        if highest_noise_sim > -1.0 and best_target_sim < (highest_noise_sim + margin_over_noise):
            return None, best_target_sim, all_scores, "noise_dominant", live_thresh, speaker_sim

        if use_biometric_lock and not speaker_verified:
            return None, best_target_sim, all_scores, "imposter_voice", live_thresh, speaker_sim

        self.adapt_prototype(best_target_kw, live_emb, best_target_sim)

        return best_target_kw, best_target_sim, all_scores, None, live_thresh, speaker_sim

    def save_prototypes(self, json_path=None):
        if json_path is None:
            json_path = self.prototypes_path
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        serializable = {k: [t.tolist() for t in v] for k, v in self.prototypes.items()}
        with open(json_path, "w") as f:
            json.dump(serializable, f, indent=2)
        print(f"[Swara-KWS] Saved {len(self.prototypes)} keywords to {json_path}")

    def load_prototypes(self, json_path=None):
        if json_path is None:
            json_path = self.prototypes_path
        if not os.path.exists(json_path):
            return
        with open(json_path, "r") as f:
            data = json.load(f)
        self.prototypes = {}
        for k, v in data.items():
            if isinstance(v[0], list):
                self.prototypes[k] = [np.array(t, dtype=np.float32) for t in v]
            else:
                self.prototypes[k] = [np.array(v, dtype=np.float32)]
        self.anchor_prototypes = {k: [t.copy() for t in v] for k, v in self.prototypes.items()}
        print(f"[Swara-KWS] Loaded {len(self.prototypes)} keywords from {json_path}: {list(self.prototypes.keys())}")

    def delete_keyword(self, keyword_name):
        if keyword_name in self.prototypes:
            del self.prototypes[keyword_name]
            print(f"[Swara-KWS] Removed keyword '{keyword_name}' from prototypes.")
            return True
        return False

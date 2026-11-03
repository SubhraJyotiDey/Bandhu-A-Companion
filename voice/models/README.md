# Voice Models & Wake Word Engines

## Swara-KWS Few-Shot Metric Keyword Spotting (Recommended)

Swara-KWS is an offline, few-shot metric learning wake word engine located in `voice/swara_kws/`.
It runs a frozen Depthwise Separable CNN (DS-CNN) backbone to project 1-second audio into a 64-dimensional embedding vector, and dynamically matches against enrolled centroid prototypes using cosine similarity with SNR-adaptive dynamic thresholding and optional speaker biometric verification.

### Pre-trained Model Checkpoints
Located in `voice/swara_kws/models/`:
- `kws_backbone.pth`: PyTorch frozen backbone (dim=64, ~170KB)
- `kws_backbone_fp32.tflite`: Edge TFLite model (~150KB)
- `kws_backbone_int8.tflite`: Quantized INT8 model for microcontrollers (~62KB)

### Enrolling Custom Wake Words Instantly (No Retraining)
```bash
# Enroll custom wake word with 3 repetitions:
python scripts/enroll_swara.py --keyword bondhu --record --k 3

# List all active registered keywords:
python scripts/enroll_swara.py --list

# Live test detection in terminal:
python scripts/enroll_swara.py --test
```

---

## Custom openWakeWord Models

Place your custom `.onnx` wake word model files in this directory.

For example, if you set `"wake_word": "bondhu"` in `config.json`, name your model file:
`bondhu.onnx`

### Training a Custom Wake Word
To train a custom wake word model for free:
1. Open the [OpenWakeWord ONNX Improved Google Colab Trainer](https://colab.research.google.com/drive/1zzKpSnqVkUDD3FyZ-Yxw3grF7L0R1rlk).
2. Follow the steps in the notebook to generate a model for your custom phrase (e.g. `"bondhu"` or `"bandhu"`).
3. Download the resulting `.onnx` model file.
4. Place the `.onnx` file directly into this folder.

---

## Vosk Offline STT Models

Place downloaded Vosk models in subdirectories here. The STT engine checks these directories
before attempting auto-download.

### Expected Directory Structure
```
voice/models/
├── bondhu.onnx          # Wake word model
├── vosk-en/             # English Vosk model
│   ├── am/
│   ├── conf/
│   ├── graph/
│   └── ...
├── vosk-hi/             # Hindi Vosk model
│   ├── am/
│   ├── conf/
│   ├── graph/
│   └── ...
└── vosk-bn/             # Bengali Vosk model (MANUAL DOWNLOAD REQUIRED)
    ├── am/
    ├── conf/
    ├── graph/
    └── ...
```

### Download Instructions

**English** (auto-downloads if missing, ~40MB):
```bash
# Usually auto-downloads, but you can pre-download:
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip
mv vosk-model-small-en-us-0.15 voice/models/vosk-en
```

**Hindi** (auto-downloads if missing, ~30MB):
```bash
wget https://alphacephei.com/vosk/models/vosk-model-small-hi-0.22.zip
unzip vosk-model-small-hi-0.22.zip
mv vosk-model-small-hi-0.22 voice/models/vosk-hi
```

**Bengali** ⚠️ MANUAL DOWNLOAD REQUIRED (~30MB):
Bengali is NOT in Vosk's auto-download registry. You MUST download it manually:
```bash
# Option 1: From HuggingFace
git clone https://huggingface.co/alphacep/vosk-model-small-streaming-bn voice/models/vosk-bn

# Option 2: Direct download
wget https://huggingface.co/alphacep/vosk-model-small-streaming-bn/resolve/main/vosk-model-small-streaming-bn.zip
unzip vosk-model-small-streaming-bn.zip
mv vosk-model-small-streaming-bn voice/models/vosk-bn
```

### Fallback Behavior
If a requested language model is missing, the STT engine will automatically fall back:
- Bengali (missing) → tries Hindi → tries English
- Hindi (missing) → tries English
- If no model is available at all, Vosk STT is skipped and Google Cloud STT is used instead.

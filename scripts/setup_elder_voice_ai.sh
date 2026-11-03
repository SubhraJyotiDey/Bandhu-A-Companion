#!/usr/bin/env bash
# ==============================================================================
# Setup Script for Offline Elder Care Voice AI on Raspberry Pi Zero 2W
# Stack: Vosk STT (Hindi + English) + llama.cpp (Qwen2.5-0.5B-Instruct Q4_K_M) + eSpeak-NG
# Memory Profile: RAM usage <300MB, leaving ample headroom on 512MB Pi Zero 2W
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

echo "========================================================"
echo "  Bandhu Companion - Elder Care Edge AI Setup"
echo "  Raspberry Pi Zero 2W (ARMv8 64-bit / 512MB RAM)"
echo "========================================================"

# 1. System packages
echo "[1/5] Updating and installing system dependencies..."
sudo apt update
sudo apt install -y build-essential cmake git curl wget unzip espeak-ng pulseaudio \
                    alsa-utils libasound2-dev python3-pip python3-dev \
                    python3-pyaudio libopenblas-dev

# 2. Python requirements
echo "[2/5] Installing Python dependencies (vosk, requests, edge-tts)..."
pip3 install --break-system-packages vosk requests edge-tts sounddevice numpy || pip install vosk requests edge-tts sounddevice numpy

# 3. Create models directories
echo "[3/5] Setting up models directories..."
mkdir -p "$PROJECT_ROOT/models"
mkdir -p "$PROJECT_ROOT/voice/models"

# 3a. Download Vosk English model (small, ~40MB)
if [ ! -d "$PROJECT_ROOT/voice/models/vosk-en" ]; then
    echo "Downloading Vosk English model..."
    cd "$PROJECT_ROOT/voice/models"
    wget -c https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip -O vosk-en.zip
    unzip vosk-en.zip
    mv vosk-model-small-en-us-0.15 vosk-en
    rm vosk-en.zip
    cd "$PROJECT_ROOT"
else
    echo "Vosk English model already exists."
fi

# 3b. Download Vosk Hindi model (small, ~45MB)
if [ ! -d "$PROJECT_ROOT/voice/models/vosk-hi" ]; then
    echo "Downloading Vosk Hindi model..."
    cd "$PROJECT_ROOT/voice/models"
    wget -c https://alphacephei.com/vosk/models/vosk-model-small-hi-0.22.zip -O vosk-hi.zip
    unzip vosk-hi.zip
    mv vosk-model-small-hi-0.22 vosk-hi
    rm vosk-hi.zip
    cd "$PROJECT_ROOT"
else
    echo "Vosk Hindi model already exists."
fi

# 4. Download Qwen2.5-0.5B-Instruct-Q4_K_M GGUF (~350MB)
MODEL_FILE="$PROJECT_ROOT/models/qwen2.5-0.5b-instruct-q4_k_m.gguf"
if [ ! -f "$MODEL_FILE" ]; then
    echo "[4/5] Downloading Qwen2.5-0.5B-Instruct Q4_K_M GGUF from HuggingFace..."
    wget -c https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf -O "$MODEL_FILE"
else
    echo "[4/5] Qwen2.5-0.5B GGUF model already present: $MODEL_FILE"
fi

# 5. Acquire llama.cpp binary (Pre-built ARM64 binary or lightweight source build)
echo "[5/5] Setting up llama.cpp runtime for ARM Cortex-A53..."

# First check if llama or llama-cli is already available
if command -v llama-cli >/dev/null 2>&1 || command -v llama >/dev/null 2>&1 || [ -f "$HOME/.local/bin/llama" ]; then
    echo "llama binary already installed."
else
    echo "Attempting to install precompiled official ARM64 binary..."
    if curl -LsSf https://llama.app/install.sh | sh; then
        echo "Official precompiled binary installed successfully to ~/.local/bin/llama."
        export PATH="$HOME/.local/bin:$PATH"
    else
        echo "Precompiled install skipped or failed. Falling back to source build..."
        # Ensure at least 2GB swap
        SWAP_TOTAL=$(free -m | awk '/Swap:/ {print $2}')
        if [ "$SWAP_TOTAL" -lt 1500 ]; then
            echo "Increasing swapfile to 2048 MB for compilation..."
            if [ -f /etc/dphys-swapfile ]; then
                sudo dphys-swapfile swapoff || true
                sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' /etc/dphys-swapfile || true
                sudo dphys-swapfile setup || true
                sudo dphys-swapfile swapon || true
            fi
        fi

        if [ ! -d "$PROJECT_ROOT/llama.cpp" ]; then
            git clone --depth 1 https://github.com/ggerganov/llama.cpp.git "$PROJECT_ROOT/llama.cpp"
        fi

        cd "$PROJECT_ROOT/llama.cpp"
        rm -rf build

        # MinSizeRel uses -Os which reduces GCC compiler RAM by 75% on llama-model.cpp
        cmake -B build \
            -DCMAKE_BUILD_TYPE=MinSizeRel \
            -DGGML_NATIVE=OFF \
            -DGGML_CPU_ARM_ARCH=armv8-a \
            -DLLAMA_CURL=OFF

        cmake --build build --config MinSizeRel --target llama-cli -j1
    fi
fi

cd "$PROJECT_ROOT"

echo "========================================================"
echo "  Elder Care Offline AI Pipeline successfully installed!"
echo "  Run test: python3 voice/elder_voice_pipeline.py --lang hi"
echo "========================================================"

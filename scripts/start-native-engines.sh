#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOCAL_TTS_DIR="$DIR/local-tts"
LOCALAI_DIR="$DIR/LocalAI"

echo "=== Starting Native macOS Engines (Metal GPU / Apple Silicon) ==="

# 1. Start local-tts natively with Apple MLX
if lsof -i :8880 >/dev/null 2>&1; then
    echo "✓ local-tts is already running on port 8880"
else
    echo "Starting native local-tts (MLX Metal GPU) on :8880..."
    nohup "$LOCAL_TTS_DIR/.venv/bin/local-tts" server \
        --host 127.0.0.1 \
        --port 8880 \
        --disable-qwen \
        > "$LOCAL_TTS_DIR/local-tts.log" 2>&1 &
    echo "✓ local-tts started (PID: $!)"
fi

# 2. Start LocalAI natively with Apple Silicon Metal acceleration
if lsof -i :8080 >/dev/null 2>&1; then
    echo "✓ LocalAI is already running on port 8080"
else
    echo "Starting native LocalAI on :8080..."
    mkdir -p "$LOCALAI_DIR/models"
    nohup "$LOCALAI_DIR/local-ai" run \
        --address "127.0.0.1:8080" \
        --models-path "$LOCALAI_DIR/models" \
        > "$LOCALAI_DIR/localai.log" 2>&1 &
    echo "✓ LocalAI started (PID: $!)"
fi

echo "=== Native engines running without Docker ==="
echo "LocalAI (LLM / STT):  http://127.0.0.1:8080"
echo "local-tts (Kokoro MLX): http://127.0.0.1:8880"

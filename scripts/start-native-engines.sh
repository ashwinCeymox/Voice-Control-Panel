#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [ -d "$DIR/arch/LocalAI" ]; then
    DIR="$DIR/arch"
fi
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
    mkdir -p "$HOME/.localai-docker"
    [ ! -f "$HOME/.localai-docker/config.json" ] && echo '{}' > "$HOME/.localai-docker/config.json"
    DOCKER_CONFIG="$HOME/.localai-docker" nohup "$LOCALAI_DIR/local-ai" run \
        --address "127.0.0.1:8080" \
        --models-path "$LOCALAI_DIR/models" \
        > "$LOCALAI_DIR/localai.log" 2>&1 &
    echo "✓ LocalAI started (PID: $!)"
fi

# 3. Start Streaming STT natively with sherpa-onnx Zipformer (FP32/F16 higher-precision weights)
VOICE_AGENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if lsof -i :8088 >/dev/null 2>&1; then
    echo "✓ Streaming STT is already running on port 8088"
else
    echo "Starting native Streaming STT (sherpa-onnx Zipformer FP32/F16) on :8088..."
    STREAMING_STT_PRECISION="${STREAMING_STT_PRECISION:-fp32}" \
    STREAMING_STT_PROVIDER="${STREAMING_STT_PROVIDER:-cpu}" \
    STREAMING_STT_CHUNK_MS="${STREAMING_STT_CHUNK_MS:-560}" \
    STREAMING_STT_LANGUAGE="${STREAMING_STT_LANGUAGE:-en}" \
    nohup "$VOICE_AGENT_DIR/.venv/bin/python" "$VOICE_AGENT_DIR/services/streaming_stt/server.py" \
        --port 8088 \
        --provider "${STREAMING_STT_PROVIDER:-cpu}" \
        --precision "${STREAMING_STT_PRECISION:-fp32}" \
        --chunk-ms "${STREAMING_STT_CHUNK_MS:-560}" \
        > "$VOICE_AGENT_DIR/streaming-stt.log" 2>&1 &
    echo "✓ Streaming STT started (PID: $!)"
fi

echo "=== Native engines running without Docker ==="
echo "LocalAI (LLM / STT):   http://127.0.0.1:8080"
echo "local-tts (Kokoro MLX):  http://127.0.0.1:8880"
echo "Streaming STT (Zipformer): ws://127.0.0.1:8088/ws"

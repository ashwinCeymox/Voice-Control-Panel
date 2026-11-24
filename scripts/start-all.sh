#!/usr/bin/env bash
# ==============================================================================
# Local Voice AI Agent - Start All Services (Production & Container Ready)
# Starts: LocalAI (8080), local-tts (8880), Sherpa-ONNX (8088), Gateway, Frontend
# Engines are STRICTLY bound to 127.0.0.1 in every environment.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

BOLD="\033[1m"
GREEN="\033[32m"
SKY="\033[36m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

log_info() { echo -e "${SKY}[INFO]${RESET} $1"; }
log_ok()   { echo -e "${GREEN}[OK]${RESET}   $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${RESET} $1"; }
log_err()  { echo -e "${RED}[ERROR]${RESET} $1"; }

# 1. Load Environment Configuration
if [ -f "${ROOT_DIR}/.env" ]; then
    log_info "Loading configuration from ${ROOT_DIR}/.env"
    set -a
    source "${ROOT_DIR}/.env"
    set +a
fi

GATEWAY_HOST="${GATEWAY_HOST:-127.0.0.1}"
GATEWAY_PORT="${GATEWAY_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

LOGS_DIR="${ROOT_DIR}/logs"
PID_DIR="${ROOT_DIR}/.pids"
mkdir -p "${LOGS_DIR}" "${PID_DIR}"

is_service_running() {
    local pid_file=$1
    if [ -f "${pid_file}" ]; then
        local pid
        pid=$(cat "${pid_file}" 2>/dev/null || true)
        if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

echo -e "${BOLD}${SKY}====================================================================${RESET}"
echo -e "${BOLD}       🎙️  Local Voice Agent - Starting All Services                 ${RESET}"
echo -e "${BOLD}${SKY}====================================================================${RESET}"

# Stop any running instances first
"${SCRIPT_DIR}/stop-all.sh" || true
mkdir -p "${LOGS_DIR}" "${PID_DIR}"

# 2. Start local-tts (Kokoro) on 127.0.0.1:8880
LOCAL_TTS_BIN=""
for candidate in "${ROOT_DIR}/../local-tts/.venv/bin/local-tts" \
                 "${ROOT_DIR}/local-tts/.venv/bin/local-tts" \
                 "${ROOT_DIR}/arch/local-tts/.venv/bin/local-tts" \
                 "${ROOT_DIR}/.tools/local-tts/.venv/bin/local-tts"; do
    if [ -x "${candidate}" ]; then
        LOCAL_TTS_BIN="${candidate}"
        break
    fi
done
if [ -z "${LOCAL_TTS_BIN}" ] && command -v local-tts >/dev/null 2>&1; then
    LOCAL_TTS_BIN="$(command -v local-tts)"
fi

if [ -x "${LOCAL_TTS_BIN}" ]; then
    log_info "Starting local-tts (Kokoro) on 127.0.0.1:8880..."
    if [ "$(uname -s)" = "Darwin" ] && [ -d "${HOME}/.cache/huggingface/hub/models--mlx-community--Kokoro-82M" ]; then
        export HF_HUB_OFFLINE=1
    fi
    nohup "${LOCAL_TTS_BIN}" server \
        --host 127.0.0.1 \
        --port 8880 \
        --disable-pocket \
        --disable-kitten \
        --disable-qwen \
        > "${LOGS_DIR}/local-tts.log" 2>&1 &
    echo $! > "${PID_DIR}/local-tts.pid"
    log_ok "local-tts started (PID $(cat "${PID_DIR}/local-tts.pid"))"
else
    log_warn "local-tts not installed. Gateway will handle TTS with 503 fallback."
fi

# 3. Start LocalAI strictly on 127.0.0.1:8080
LOCALAI_BIN=""
for candidate in "${ROOT_DIR}/arch/LocalAI/local-ai" \
                 "${ROOT_DIR}/../LocalAI/local-ai" \
                 "${ROOT_DIR}/LocalAI/local-ai" \
                 "${ROOT_DIR}/.tools/localai/local-ai" \
                 "/usr/local/bin/local-ai" \
                 "${HOME}/.local/bin/local-ai"; do
    if [ -x "${candidate}" ] || [ -f "${candidate}" ]; then
        LOCALAI_BIN="${candidate}"
        break
    fi
done
if [ -z "${LOCALAI_BIN}" ] && command -v local-ai >/dev/null 2>&1; then
    LOCALAI_BIN="$(command -v local-ai)"
fi

LOCALAI_MODELS="${ROOT_DIR}/models"
for candidate in "${ROOT_DIR}/models" \
                 "${ROOT_DIR}/arch/LocalAI/models" \
                 "${ROOT_DIR}/../LocalAI/models"; do
    if [ -d "${candidate}" ]; then
        LOCALAI_MODELS="${candidate}"
        break
    fi
done

if [ -n "${LOCALAI_BIN}" ] && [ -f "${LOCALAI_BIN}" ]; then
    log_info "Starting LocalAI strictly on 127.0.0.1:8080 using models at ${LOCALAI_MODELS}..."
    mkdir -p "${HOME}/.localai-docker"
    [ ! -f "${HOME}/.localai-docker/config.json" ] && echo '{}' > "${HOME}/.localai-docker/config.json"
    DOCKER_CONFIG="${HOME}/.localai-docker" nohup "${LOCALAI_BIN}" run \
        --address "127.0.0.1:8080" \
        --models-path "${LOCALAI_MODELS}" \
        > "${LOGS_DIR}/localai.log" 2>&1 &
    echo $! > "${PID_DIR}/localai.pid"
    log_ok "LocalAI started (PID $(cat "${PID_DIR}/localai.pid"))"
else
    log_warn "LocalAI binary not found. Checked: arch/LocalAI, ../LocalAI, .tools/localai, /usr/local/bin, PATH. STT/LLM fallback active."
fi

# 4. Start Sherpa-ONNX Streaming STT strictly on 127.0.0.1:8088
if [ -f "${ROOT_DIR}/services/streaming_stt/server.py" ]; then
    log_info "Starting Sherpa-ONNX Streaming STT strictly on 127.0.0.1:8088..."
    STREAMING_STT_PRECISION="${STREAMING_STT_PRECISION:-fp32}" \
    STREAMING_STT_PROVIDER="${STREAMING_STT_PROVIDER:-cpu}" \
    STREAMING_STT_CHUNK_MS="${STREAMING_STT_CHUNK_MS:-560}" \
    nohup "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/services/streaming_stt/server.py" \
        --host 127.0.0.1 \
        --port 8088 \
        --provider "${STREAMING_STT_PROVIDER:-cpu}" \
        --precision "${STREAMING_STT_PRECISION:-fp32}" \
        --chunk-ms "${STREAMING_STT_CHUNK_MS:-560}" \
        > "${LOGS_DIR}/streaming-stt.log" 2>&1 &
    echo $! > "${PID_DIR}/streaming-stt.pid"
    log_ok "Sherpa-ONNX Streaming STT started (PID $(cat "${PID_DIR}/streaming-stt.pid"))"
fi

# 5. Start FastAPI Gateway on GATEWAY_HOST:GATEWAY_PORT
log_info "Starting FastAPI Gateway on ${GATEWAY_HOST}:${GATEWAY_PORT}..."
if command -v fuser >/dev/null 2>&1; then
    fuser -k -9 "${GATEWAY_PORT}/tcp" 2>/dev/null || true
fi
(
    cd "${ROOT_DIR}"
    nohup "${ROOT_DIR}/.venv/bin/uvicorn" app.main:app \
        --host "${GATEWAY_HOST}" \
        --port "${GATEWAY_PORT}" \
        > "${LOGS_DIR}/gateway.log" 2>&1 &
    echo $! > "${PID_DIR}/gateway.pid"
)
log_ok "FastAPI Gateway started (PID $(cat "${PID_DIR}/gateway.pid"))"

# 6. Start Frontend (if frontend directory exists)
if [ -d "${ROOT_DIR}/frontend" ]; then
    log_info "Starting Frontend on port ${FRONTEND_PORT}..."
    (
        cd "${ROOT_DIR}/frontend"
        nohup npm run preview -- --host 127.0.0.1 --port "${FRONTEND_PORT}" \
            > "${LOGS_DIR}/frontend.log" 2>&1 || nohup npm run dev -- --host 127.0.0.1 --port "${FRONTEND_PORT}" > "${LOGS_DIR}/frontend.log" 2>&1 &
        echo $! > "${PID_DIR}/frontend.pid"
    )
    log_ok "Frontend started (PID $(cat "${PID_DIR}/frontend.pid" 2>/dev/null || echo 'running'))"
fi

echo -e "\n${BOLD}${GREEN}====================================================================${RESET}"
echo -e "${BOLD}${GREEN}  All Services Started!                                             ${RESET}"
echo -e "${BOLD}${GREEN}====================================================================${RESET}"
echo -e "  • Gateway:      http://${GATEWAY_HOST}:${GATEWAY_PORT} (docs: /docs)"
echo -e "  • Frontend:     http://127.0.0.1:${FRONTEND_PORT}"
echo -e "  • LocalAI:      http://127.0.0.1:8080 (loopback only)"
echo -e "  • local-tts:    http://127.0.0.1:8880 (loopback only)"
echo -e "  • Sherpa STT:   http://127.0.0.1:8088 (loopback only)"
echo -e "  • Logs:         ${LOGS_DIR}/*.log"
echo -e "  • Stop Command: ${SCRIPT_DIR}/stop-all.sh"
echo -e "${BOLD}${GREEN}====================================================================${RESET}"

#!/usr/bin/env bash
# ==============================================================================
# Local Voice AI Agent - Automated Environment & Engine Setup
# Cross-platform: macOS Apple Silicon (Metal) & Linux (NVIDIA CUDA / CPU)
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

echo -e "${BOLD}${SKY}====================================================================${RESET}"
echo -e "${BOLD}       🎙️  Local Voice Agent - Automated Platform Setup             ${RESET}"
echo -e "${BOLD}${SKY}====================================================================${RESET}"

# 1. Platform & Accelerator Detection
OS="$(uname -s)"
ARCH="$(uname -m)"
ACCEL="cpu"

log_info "Detecting platform: OS=${OS}, Architecture=${ARCH}"

if [ "${OS}" = "Darwin" ] && [ "${ARCH}" = "arm64" ]; then
    ACCEL="metal"
    log_ok "Platform detected: Apple Silicon (Metal acceleration)"
elif [ "${OS}" = "Linux" ]; then
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
        ACCEL="cuda"
        log_ok "Platform detected: Linux with NVIDIA CUDA GPU"
    else
        ACCEL="cpu"
        log_ok "Platform detected: Linux (CPU only)"
    fi
else
    log_warn "Unknown or unsupported platform: ${OS} ${ARCH}. Proceeding with standard CPU fallback."
fi

# 2. Python Virtual Environment Setup
log_info "Configuring Python virtual environment in ${ROOT_DIR}/.venv..."
if [ ! -d "${ROOT_DIR}/.venv" ]; then
    python3 -m venv "${ROOT_DIR}/.venv"
fi

"${ROOT_DIR}/.venv/bin/python" -m pip install --upgrade pip
if [ -f "${ROOT_DIR}/requirements.txt" ]; then
    log_info "Installing pinned Python dependencies from requirements.txt..."
    "${ROOT_DIR}/.venv/bin/pip" install -r "${ROOT_DIR}/requirements.txt"
fi
log_ok "Python environment configured."

# 3. Frontend Setup
log_info "Configuring Frontend dependencies in ${ROOT_DIR}/frontend..."
if [ -d "${ROOT_DIR}/frontend" ]; then
    cd "${ROOT_DIR}/frontend"
    if [ -f "package-lock.json" ]; then
        npm ci
    else
        npm install
    fi
    node scripts/copy-vad-assets.js
    npm run build
    cd "${ROOT_DIR}"
    log_ok "Frontend built successfully."
fi

# 4. Third-Party Engines Provisioning (LocalAI & local-tts)
ARCH_DIR="${ROOT_DIR}/arch"
mkdir -p "${ARCH_DIR}/LocalAI" "${ARCH_DIR}/local-tts" "${ROOT_DIR}/models" "${ROOT_DIR}/logs" "${ROOT_DIR}/.pids"

# 4.1 LocalAI Setup
LOCALAI_BIN="${ARCH_DIR}/LocalAI/local-ai"
LOCALAI_VERSION="v2.26.0"

if [ -f "${LOCALAI_BIN}" ]; then
    log_ok "LocalAI binary already exists at ${LOCALAI_BIN}"
else
    log_info "Provisioning LocalAI ${LOCALAI_VERSION}..."
    if [ "${ACCEL}" = "metal" ]; then
        LOCALAI_URL="https://github.com/mudler/LocalAI/releases/download/${LOCALAI_VERSION}/local-ai-Darwin-arm64"
    elif [ "${ACCEL}" = "cuda" ]; then
        LOCALAI_URL="https://github.com/mudler/LocalAI/releases/download/${LOCALAI_VERSION}/local-ai-cublas-cuda-12-Linux-x86_64"
    else
        LOCALAI_URL="https://github.com/mudler/LocalAI/releases/download/${LOCALAI_VERSION}/local-ai-cpu-Linux-x86_64"
    fi

    log_info "Downloading ${LOCALAI_URL}..."
    curl -sSL -o "${LOCALAI_BIN}" "${LOCALAI_URL}" || {
        log_warn "Failed to download LocalAI binary directly. You can manually install it into ${LOCALAI_BIN}."
    }
    if [ -f "${LOCALAI_BIN}" ]; then
        chmod +x "${LOCALAI_BIN}"
        log_ok "LocalAI provisioned at ${LOCALAI_BIN}"
    fi
fi

# 4.2 local-tts Setup
if [ "${ACCEL}" = "metal" ]; then
    LOCAL_TTS_BIN="${ARCH_DIR}/local-tts/.venv/bin/local-tts"
    if [ -f "${LOCAL_TTS_BIN}" ]; then
        log_ok "local-tts binary verified at ${LOCAL_TTS_BIN}"
    else
        log_info "Setting up local-tts in ${ARCH_DIR}/local-tts/.venv..."
        python3 -m venv "${ARCH_DIR}/local-tts/.venv"
        "${ARCH_DIR}/local-tts/.venv/bin/pip" install --upgrade pip
        "${ARCH_DIR}/local-tts/.venv/bin/pip" install "local-tts>=0.1.0" mlx || {
            log_warn "local-tts pip install failed; will verify if pre-existing in repository."
        }
    fi
else
    log_warn "NOTICE: Kokoro on Apple MLX is macOS-exclusive. local-tts will NOT be installed on Linux."
    log_warn "The gateway will cleanly report Text-to-Speech as unavailable (HTTP 503) without crashing."
fi

# 5. Default Models Download
log_info "Downloading default models..."
if [ -f "${ROOT_DIR}/scripts/download-streaming-stt-model.sh" ]; then
    bash "${ROOT_DIR}/scripts/download-streaming-stt-model.sh"
fi

# 6. Environment Configuration (.env)
if [ ! -f "${ROOT_DIR}/.env" ]; then
    log_info "Generating secure .env configuration from .env.example..."
    RAND_PASSWORD="$("${ROOT_DIR}/.venv/bin/python" -c "import secrets; print(secrets.token_urlsafe(16))")"
    RAND_ADMIN_KEY="$("${ROOT_DIR}/.venv/bin/python" -c "import secrets; print('va-admin-' + secrets.token_urlsafe(32))")"
    RAND_HASH_SECRET="$("${ROOT_DIR}/.venv/bin/python" -c "import secrets; print(secrets.token_hex(32))")"

    sed \
        -e "s|ADMIN_INITIAL_PASSWORD=.*|ADMIN_INITIAL_PASSWORD=${RAND_PASSWORD}|g" \
        -e "s|ADMIN_API_KEY=.*|ADMIN_API_KEY=${RAND_ADMIN_KEY}|g" \
        -e "s|API_KEY_HASH_SECRET=.*|API_KEY_HASH_SECRET=${RAND_HASH_SECRET}|g" \
        "${ROOT_DIR}/.env.example" > "${ROOT_DIR}/.env"

    chmod 600 "${ROOT_DIR}/.env"
    log_ok "Generated .env with cryptographically random secrets."
    echo -e "${BOLD}${YELLOW}--------------------------------------------------------------------${RESET}"
    echo -e "${BOLD}🔑 INITIAL SUPER ADMIN CREDENTIALS GENERATED:${RESET}"
    echo -e "   Username:         ${GREEN}admin${RESET}"
    echo -e "   Initial Password: ${GREEN}${RAND_PASSWORD}${RESET}"
    echo -e "   (You will be required to change this password on initial login)"
    echo -e "${BOLD}${YELLOW}--------------------------------------------------------------------${RESET}"
else
    log_ok ".env file already exists. Preserving existing secrets."
fi

# 7. Database Initialization
log_info "Initializing SQLite database schema and seeding super admin user..."
"${ROOT_DIR}/.venv/bin/python" -c "import asyncio; from app.storage.database import init_db; asyncio.run(init_db())"
log_ok "Database initialized."

echo -e "\n${BOLD}${GREEN}====================================================================${RESET}"
echo -e "${BOLD}${GREEN}  Setup Complete! Start stack with: ./scripts/start-all.sh          ${RESET}"
echo -e "${BOLD}${GREEN}====================================================================${RESET}"

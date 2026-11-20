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
    # Linux setup per localtts.md using uv
    LOCAL_TTS_DIR="${ROOT_DIR}/../local-tts"
    [ ! -d "${LOCAL_TTS_DIR}" ] && LOCAL_TTS_DIR="${ARCH_DIR}/local-tts"
    if [ -x "${LOCAL_TTS_DIR}/.venv/bin/local-tts" ]; then
        log_ok "local-tts engine verified at ${LOCAL_TTS_DIR}/.venv/bin/local-tts"
    else
        log_info "Setting up local-tts on Linux per localtts.md..."
        if ! command -v uv >/dev/null 2>&1 && [ ! -x "${HOME}/.local/bin/uv" ]; then
            curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || true
        fi
        export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:/usr/local/bin:${PATH}"
        if command -v uv >/dev/null 2>&1; then
            if [ ! -d "${LOCAL_TTS_DIR}/.git" ]; then
                git clone --depth 1 https://github.com/saghul/local-tts.git "${LOCAL_TTS_DIR}" || true
            fi
            if [ -d "${LOCAL_TTS_DIR}" ]; then
                (cd "${LOCAL_TTS_DIR}" && uv sync --extra cuda >/dev/null 2>&1 || uv sync >/dev/null 2>&1 || true)
            fi
        fi
    fi
fi

# 5. Default Models Download
log_info "Downloading default models..."
if [ -f "${ROOT_DIR}/scripts/download-streaming-stt-model.sh" ]; then
    bash "${ROOT_DIR}/scripts/download-streaming-stt-model.sh"
fi

# 6. Environment Configuration (.env)
if [ ! -f "${ROOT_DIR}/.env" ]; then
    log_info "Generating secure .env configuration from .env.example..."
    RAND_ADMIN_KEY="$("${ROOT_DIR}/.venv/bin/python" -c "import secrets; print('va-admin-' + secrets.token_urlsafe(32))")"
    RAND_HASH_SECRET="$("${ROOT_DIR}/.venv/bin/python" -c "import secrets; print(secrets.token_hex(32))")"

    sed \
        -e "s|ADMIN_API_KEY=.*|ADMIN_API_KEY=${RAND_ADMIN_KEY}|g" \
        -e "s|API_KEY_HASH_SECRET=.*|API_KEY_HASH_SECRET=${RAND_HASH_SECRET}|g" \
        "${ROOT_DIR}/.env.example" > "${ROOT_DIR}/.env"

    chmod 600 "${ROOT_DIR}/.env"
    log_ok "Generated .env with cryptographically random secrets."
else
    log_ok ".env file already exists. Preserving existing secrets."
fi

# 7. Database Initialization & Super Admin Seeding
log_info "Initializing SQLite database schema and seeding super admin user in database..."
SEED_OUT="$("${ROOT_DIR}/.venv/bin/python" -m app.cli seed-admin 2>/dev/null || true)"
if [[ "${SEED_OUT}" == *"ADMIN_SEED_GENERATED:"* ]]; then
    GEN_PASS="${SEED_OUT#*ADMIN_SEED_GENERATED:}"
    GEN_PASS="${GEN_PASS%%$'\n'*}"
    echo -e "${BOLD}${YELLOW}--------------------------------------------------------------------${RESET}"
    echo -e "${BOLD}🔑 INITIAL SUPER ADMIN CREDENTIALS GENERATED:${RESET}"
    echo -e "   Username:         ${GREEN}admin@voicepanel.io${RESET}"
    echo -e "   Initial Password: ${GREEN}${GEN_PASS}${RESET}"
    echo -e "   Storage:          ${GREEN}Stored exclusively in SQLite database (never in .env)${RESET}"
    echo -e "   (You will be required to change this password on initial login)"
    echo -e "${BOLD}${YELLOW}--------------------------------------------------------------------${RESET}"
fi
log_ok "Database initialized."

echo -e "\n${BOLD}${GREEN}====================================================================${RESET}"
echo -e "${BOLD}${GREEN}  Setup Complete! Start stack with: ./scripts/start-all.sh          ${RESET}"
echo -e "${BOLD}${GREEN}====================================================================${RESET}"

#!/usr/bin/env bash
# ==============================================================================
# scripts/preflight.sh
# Production & Cloud Readiness Preflight Check
# Verifies system prerequisites, environment variables, GPU visibility,
# writable storage, model directories, ports, and outbound internet access.
# Adheres to runpod.md Phase R1 specification.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

BOLD="\033[1m"
GREEN="\033[32m"
SKY="\033[36m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

log_info()  { echo -e "${SKY}[INFO]${RESET}  $1"; }
log_ok()    { echo -e "${GREEN}[OK]${RESET}    $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${RESET}  $1"; }
log_fail()  { echo -e "${RED}[FAIL]${RESET}  $1"; }

ERRORS=0
WARNINGS=0

echo -e "${BOLD}${SKY}====================================================================${RESET}"
echo -e "${BOLD}       🩺 Voice AI Platform - Preflight Readiness Inspection       ${RESET}"
echo -e "${BOLD}${SKY}====================================================================${RESET}"

# 1. OS & Platform Detection
log_info "1. Inspecting Platform Architecture..."
OS_TYPE="$(uname -s)"
ARCH_TYPE="$(uname -m)"
echo "   OS: ${OS_TYPE}, Arch: ${ARCH_TYPE}"

if [ "${OS_TYPE}" = "Linux" ]; then
    log_ok "Linux environment detected (RunPod / Hetzner compatible)."
elif [ "${OS_TYPE}" = "Darwin" ]; then
    log_ok "macOS environment detected (Apple Silicon local dev mode)."
else
    log_warn "Unrecognized operating system: ${OS_TYPE}"
    WARNINGS=$((WARNINGS + 1))
fi

# 2. GPU Detection
log_info "2. Inspecting GPU Acceleration..."
if command -v nvidia-smi >/dev/null 2>&1; then
    GPU_NAME=$(nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>/dev/null | head -1 || echo "NVIDIA GPU")
    log_ok "NVIDIA GPU detected: ${GPU_NAME}"
elif [ "${OS_TYPE}" = "Darwin" ] && [ "${ARCH_TYPE}" = "arm64" ]; then
    log_ok "Apple Silicon Metal GPU detected."
else
    log_warn "No dedicated NVIDIA GPU or Apple Silicon detected. Falling back to CPU mode."
    WARNINGS=$((WARNINGS + 1))
fi

# 3. Python & Virtualenv Check
log_info "3. Checking Python Runtime & Virtual Environment..."
PYTHON_BIN=""
if [ -f "${ROOT_DIR}/.venv/bin/python" ]; then
    PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
fi

if [ -n "${PYTHON_BIN}" ]; then
    PY_VER=$("${PYTHON_BIN}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    log_ok "Python binary found: ${PYTHON_BIN} (v${PY_VER})"
else
    log_fail "No python3 runtime or .venv detected."
    ERRORS=$((ERRORS + 1))
fi

# 4. Storage & Persistence Layout
log_info "4. Checking Writable Storage & Directories..."
STORAGE_ROOT="${ROOT_DIR}"
if [ -d "/workspace" ] && [ -w "/workspace" ]; then
    STORAGE_ROOT="/workspace"
    log_ok "Persistent RunPod mount /workspace detected and writable."
else
    log_info "Using project root for storage (${STORAGE_ROOT})."
fi

for d in "data" "logs" ".pids" "models"; do
    TARGET_DIR="${ROOT_DIR}/${d}"
    mkdir -p "${TARGET_DIR}" 2>/dev/null || true
    if [ -w "${TARGET_DIR}" ]; then
        log_ok "Directory '${d}' is writable."
    else
        log_fail "Directory '${d}' is NOT writable: ${TARGET_DIR}"
        ERRORS=$((ERRORS + 1))
    fi
done

# 5. Environment Variables & Security Configuration
log_info "5. Verifying Environment & Security Gates..."
if [ -f "${ROOT_DIR}/.env" ]; then
    log_ok "Found .env file."
    set -a
    source "${ROOT_DIR}/.env" 2>/dev/null || true
    set +a
else
    log_warn "No .env file found in root. Using process environment or defaults."
    WARNINGS=$((WARNINGS + 1))
fi

CURRENT_ENV="${ENVIRONMENT:-development}"
echo "   Active ENVIRONMENT: ${CURRENT_ENV}"

CURRENT_ADMIN_KEY="${ADMIN_API_KEY:-dev-voice-key-12345}"
if [ "${CURRENT_ENV}" = "production" ] || [ "${CURRENT_ENV}" = "staging" ]; then
    if [ "${CURRENT_ADMIN_KEY}" = "dev-voice-key-12345" ] || [ -z "${CURRENT_ADMIN_KEY}" ]; then
        log_fail "CRITICAL: ADMIN_API_KEY cannot be default 'dev-voice-key-12345' in ${CURRENT_ENV} mode."
        ERRORS=$((ERRORS + 1))
    else
        log_ok "ADMIN_API_KEY is customized for ${CURRENT_ENV}."
    fi

    if [ -n "${ADMIN_INITIAL_PASSWORD:-}" ]; then
        if [ "${ADMIN_INITIAL_PASSWORD}" = "adminVOICE3.md" ] || [ ${#ADMIN_INITIAL_PASSWORD} -lt 12 ]; then
            log_fail "CRITICAL: ADMIN_INITIAL_PASSWORD must be >= 12 characters and not default in ${CURRENT_ENV}."
            ERRORS=$((ERRORS + 1))
        else
            log_ok "ADMIN_INITIAL_PASSWORD satisfies security requirements."
        fi
    else
        log_ok "Admin password security: Passwords stored securely in SQLite database (never in .env)."
    fi

    if [ -z "${API_KEY_HASH_SECRET:-}" ] || [ "${API_KEY_HASH_SECRET}" = "generate-a-secure-random-secret-key-32-bytes" ]; then
        log_fail "CRITICAL: API_KEY_HASH_SECRET must be set to a secure random string."
        ERRORS=$((ERRORS + 1))
    else
        log_ok "API_KEY_HASH_SECRET is configured."
    fi
else
    log_ok "Development mode active; relaxed security requirements."
fi

# 6. Model Asset Verification
log_info "6. Inspecting Model Directories..."
if [ -d "${ROOT_DIR}/models" ]; then
    log_ok "Model directory exists: ${ROOT_DIR}/models"
else
    mkdir -p "${ROOT_DIR}/models"
    log_ok "Created model directory: ${ROOT_DIR}/models"
fi

# 7. Port Availability (Engines must bind to loopback 127.0.0.1)
log_info "7. Checking Internal Ports Availability..."
check_port() {
    local port=$1
    local service=$2
    if lsof -i :"$port" -sTCP:LISTEN >/dev/null 2>&1; then
        local pid
        pid=$(lsof -ti :"$port" 2>/dev/null | head -1 || echo "")
        log_warn "Port ${port} (${service}) is currently in use (PID: ${pid})."
        WARNINGS=$((WARNINGS + 1))
    else
        log_ok "Port ${port} (${service}) is free."
    fi
}

check_port 8000 "FastAPI Gateway"
check_port 8080 "LocalAI"
check_port 8088 "Sherpa-ONNX STT"
check_port 8880 "TTS Engine"
if [ "${STREAMING_BRIDGE_ENABLED:-false}" = "true" ]; then
    check_port "${STREAMING_BRIDGE_PORT:-8089}" "Streaming Bridge"
    if [ -z "${STREAMING_BRIDGE_INTERNAL_TOKEN:-}" ]; then
        log_warn "STREAMING_BRIDGE_INTERNAL_TOKEN is not configured in .env. Run scripts/generate-bridge-token.sh"
        WARNINGS=$((WARNINGS + 1))
    else
        log_ok "STREAMING_BRIDGE_INTERNAL_TOKEN is configured."
    fi
fi

# 8. Outbound Network Connectivity
log_info "8. Testing Outbound Internet Access (Model downloads & Hugging Face)..."
if curl -s --connect-timeout 4 https://huggingface.co >/dev/null 2>&1; then
    log_ok "Hugging Face connectivity: OK"
else
    log_warn "Cannot reach huggingface.co. Model downloads may require pre-cached weights."
    WARNINGS=$((WARNINGS + 1))
fi

if curl -s --connect-timeout 4 https://github.com >/dev/null 2>&1; then
    log_ok "GitHub connectivity: OK"
else
    log_warn "Cannot reach github.com."
    WARNINGS=$((WARNINGS + 1))
fi

echo -e "${BOLD}${SKY}====================================================================${RESET}"
if [ ${ERRORS} -eq 0 ]; then
    echo -e "${BOLD}${GREEN}✅ PREFLIGHT CHECK PASSED (${WARNINGS} warnings, 0 blocking errors)${RESET}"
    exit 0
else
    echo -e "${BOLD}${RED}❌ PREFLIGHT CHECK FAILED (${ERRORS} blocking errors, ${WARNINGS} warnings)${RESET}"
    echo "Please resolve the above errors before deploying to production or RunPod."
    exit 1
fi

#!/usr/bin/env bash
# ==============================================================================
# scripts/lib/install.sh
# Idempotent dependency installer, engine setup, and build pipeline
# Adheres to run-pod.md Section B5
# ==============================================================================

# Compute sha256 of a file portably
file_sha256() {
    local file="$1"
    if [ ! -f "${file}" ]; then
        echo "none"
        return
    fi
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "${file}" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "${file}" | awk '{print $1}'
    elif command -v python3 >/dev/null 2>&1; then
        python3 -c "import hashlib, sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "${file}"
    else
        echo "unsupported"
    fi
}

# Helper to find the newest/best Python binary (prefers 3.12, 3.11, 3.10 over 3.8)
resolve_best_python() {
    for candidate in python3.12 python3.11 python3.10 /opt/conda/bin/python3 /usr/local/bin/python3 python3 python; do
        if command -v "${candidate}" >/dev/null 2>&1 || [ -x "${candidate}" ]; then
            local ver
            ver="$("${candidate}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")"
            local major="${ver%%.*}"
            local minor="${ver##*.}"
            if [ "${major}" -eq 3 ] && [ "${minor}" -ge 10 ]; then
                echo "${candidate}"
                return 0
            fi
        fi
    done
    echo "python3"
}

# 1. Preflight system check
run_preflight() {
    log_info "Running system preflight checks..."
    log_ok "Platform: ${PLATFORM} (${ARCH}), Root: ${IS_ROOT}, RunPod: ${IS_RUNPOD}"

    # Python version check
    PYTHON_BIN="$(resolve_best_python)"
    if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1 && [ ! -x "${PYTHON_BIN}" ]; then
        log_err "Python 3 is not installed. Python >= ${REQUIRED_PYTHON_MIN_VERSION} is required."
        exit 1
    fi

    local py_ver
    py_ver="$("${PYTHON_BIN}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"
    log_ok "Detected Python ${py_ver} using ${PYTHON_BIN} (Minimum required: ${REQUIRED_PYTHON_MIN_VERSION})"

    # Check GPU
    if command -v nvidia-smi >/dev/null 2>&1; then
        GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n 1 || echo "NVIDIA GPU")"
        HAS_NVIDIA_GPU="true"
        log_ok "Hardware acceleration: NVIDIA CUDA GPU detected (${GPU_NAME})"
    elif [ "${PLATFORM}" = "macos" ] && [ "${ARCH}" = "arm64" ]; then
        HAS_APPLE_METAL="true"
        log_ok "Hardware acceleration: Apple Silicon Metal GPU detected"
    else
        HAS_GPU="false"
        log_info "Hardware acceleration: CPU-only mode (No supported discrete GPU detected)"
    fi

    # Check disk space (warn if < 5GB)
    local avail_mb
    avail_mb="$(python3 -c "import shutil; print(shutil.disk_usage('${ROOT_DIR}').free // (1024 * 1024))" 2>/dev/null || echo "10000")"
    if [ "${avail_mb}" -lt 5000 ]; then
        log_warn "Low available disk space: ${avail_mb} MB free. At least 5000 MB recommended for models."
    else
        log_ok "Storage space: ${avail_mb} MB available on host filesystem."
    fi

    check_runpod_storage
}

# 2. Install missing Linux system packages (Debian/Ubuntu apt)
install_system_packages() {
    if [ "${PLATFORM}" != "linux" ]; then
        return 0
    fi

    local missing_pkgs=()
    local needed=("ffmpeg" "curl" "ca-certificates" "git" "lsof" "build-essential" "python3-venv" "libsndfile1" "xz-utils" "espeak-ng" "libespeak-ng-dev" "sox")

    for pkg in "${needed[@]}"; do
        if ! dpkg -s "${pkg}" >/dev/null 2>&1; then
            missing_pkgs+=("${pkg}")
        fi
    done

    if [ "${#missing_pkgs[@]}" -eq 0 ]; then
        log_ok "All required Linux system packages already present."
        return 0
    fi

    log_info "Missing system packages detected: ${missing_pkgs[*]}"
    if [ "${IS_ROOT}" = "true" ]; then
        log_info "Installing missing system packages via apt-get..."
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -y && apt-get install -y --no-install-recommends "${missing_pkgs[@]}"
        log_ok "System packages installed successfully."
    elif command -v sudo >/dev/null 2>&1; then
        log_info "Installing missing system packages via sudo apt-get..."
        sudo DEBIAN_FRONTEND=noninteractive apt-get update -y && sudo apt-get install -y --no-install-recommends "${missing_pkgs[@]}"
        log_ok "System packages installed successfully."
    else
        log_err "Cannot install system packages automatically without root or sudo permissions."
        log_err "Please run manually: apt-get update && apt-get install -y ${missing_pkgs[*]}"
        exit 1
    fi
}

# 3. Setup Python virtual environment (.venv)
install_python_env() {
    local venv_dir="${ROOT_DIR}/.venv"
    local req_file="${ROOT_DIR}/requirements.txt"
    local stamp_file="${STAMPS_DIR}/requirements.sha256"

    local py_bin="${PYTHON_BIN:-$(resolve_best_python)}"
    if [ ! -d "${venv_dir}" ] || [ ! -f "${venv_dir}/bin/python" ]; then
        log_info "Creating Python virtual environment in ${venv_dir} using ${py_bin}..."
        "${py_bin}" -m venv "${venv_dir}"
        "${venv_dir}/bin/pip" install --upgrade pip setuptools wheel
        log_ok "Virtual environment created."
    fi

    local cur_hash
    cur_hash="$(file_sha256 "${req_file}")"
    local saved_hash=""
    if [ -f "${stamp_file}" ]; then
        saved_hash="$(cat "${stamp_file}" 2>/dev/null || true)"
    fi

    if [ "${cur_hash}" != "${saved_hash}" ]; then
        log_info "Installing / updating Python packages from requirements.txt..."
        "${venv_dir}/bin/pip" install -r "${req_file}"
        echo "${cur_hash}" > "${stamp_file}"
        log_ok "Python requirements installed and verified."
    else
        log_ok "Python virtualenv is up to date (requirements.txt matches stamp)."
    fi
}

# 4. Setup Node.js and build frontend
install_node_and_frontend() {
    local node_bin="node"
    local npm_bin="npm"

    # Check for Node.js
    if ! command -v node >/dev/null 2>&1; then
        if [ "${PLATFORM}" = "linux" ] && [ "${ARCH}" = "x86_64" ]; then
            local node_tools_dir="${TOOLS_DIR}/node"
            local tarball_path="${TOOLS_DIR}/node-v${NODE_LINUX_X64_VERSION}-linux-x64.tar.xz"

            if [ ! -x "${node_tools_dir}/bin/node" ]; then
                log_info "Node.js not found on host. Downloading official Node.js v${NODE_LINUX_X64_VERSION} into ${node_tools_dir}..."
                curl -sSL -o "${tarball_path}" "${NODE_LINUX_X64_URL}"
                mkdir -p "${node_tools_dir}"
                tar -xf "${tarball_path}" -C "${node_tools_dir}" --strip-components=1
                rm -f "${tarball_path}"
                log_ok "Node.js installed into ${node_tools_dir}/bin"
            fi
            export PATH="${node_tools_dir}/bin:${PATH}"
        else
            log_err "Node.js is not installed. Please install Node.js >= ${REQUIRED_NODE_MIN_VERSION}."
            exit 1
        fi
    fi

    local node_ver
    node_ver="$(node -v | tr -d 'v')"
    log_ok "Node.js runtime verified (v${node_ver})"

    # Install frontend dependencies if needed
    local frontend_dir="${ROOT_DIR}/frontend"
    local lock_file="${frontend_dir}/package-lock.json"
    local stamp_file="${STAMPS_DIR}/frontend_npm.sha256"

    if [ -f "${lock_file}" ]; then
        local cur_hash
        cur_hash="$(file_sha256 "${lock_file}")"
        local saved_hash=""
        if [ -f "${stamp_file}" ]; then
            saved_hash="$(cat "${stamp_file}" 2>/dev/null || true)"
        fi

        if [ "${cur_hash}" != "${saved_hash}" ] || [ ! -d "${frontend_dir}/node_modules" ]; then
            log_info "Installing frontend dependencies (npm ci)..."
            npm --prefix "${frontend_dir}" ci
            echo "${cur_hash}" > "${stamp_file}"
            log_ok "Frontend npm dependencies installed."
        else
            log_ok "Frontend node_modules up to date."
        fi
    fi

    # Build production panel (dist)
    local needs_build="false"
    if [ ! -f "${frontend_dir}/dist/index.html" ]; then
        needs_build="true"
    elif [ -n "$(find "${frontend_dir}/src" -newer "${frontend_dir}/dist/index.html" 2>/dev/null | head -n 1)" ]; then
        needs_build="true"
    fi

    if [ "${needs_build}" = "true" ]; then
        log_info "Building production frontend web application (${frontend_dir}/dist)..."
        npm --prefix "${frontend_dir}" run build
        log_ok "Frontend production assets built successfully."
    else
        log_ok "Production frontend assets up to date in frontend/dist."
    fi
}

# Ensure CPU parakeet-cpp backend is installed on non-NVIDIA Linux hosts (avoid ROCm misdetection on AMD CPU)
ensure_cpu_parakeet_backend() {
    local localai_bin="${1:-${LOCALAI_EXEC:-}}"
    if [ "${PLATFORM}" != "linux" ] || command -v nvidia-smi >/dev/null 2>&1; then
        return 0
    fi
    local target_lib="${ROOT_DIR}/backends/cpu-parakeet-cpp/lib/libparakeet.so"
    if [ -f "${target_lib}" ]; then
        log_ok "CPU parakeet-cpp backend already present at ${target_lib}"
        return 0
    fi
    if [ -z "${localai_bin}" ] || [ ! -x "${localai_bin}" ]; then
        return 0
    fi
    mkdir -p "${ROOT_DIR}/backends"
    log_info "Installing CPU parakeet-cpp backend (LOCALAI_FORCE_META_BACKEND_CAPABILITY=cpu)..."
    LOCALAI_FORCE_META_BACKEND_CAPABILITY=cpu \
    LOCALAI_BACKENDS_PATH="${ROOT_DIR}/backends" \
        "${localai_bin}" backends install cpu-parakeet-cpp >/dev/null 2>&1 || \
    LOCALAI_FORCE_META_BACKEND_CAPABILITY=cpu \
    LOCALAI_BACKENDS_PATH="${ROOT_DIR}/backends" \
        "${localai_bin}" backends install localai@parakeet-cpp >/dev/null 2>&1 || \
        log_warn "Automated cpu-parakeet-cpp backend install failed; install manually if native streaming STT is needed."
    if [ -f "${target_lib}" ]; then
        log_ok "CPU parakeet-cpp backend verified at ${target_lib}"
    fi
}

# 5. Setup LocalAI binary
install_localai_engine() {
    # Check if system-wide local-ai binary is present and valid
    if command -v local-ai >/dev/null 2>&1 && is_valid_binary "$(command -v local-ai)"; then
        LOCALAI_EXEC="$(command -v local-ai)"
        LOCALAI_MODELS_PATH="${ROOT_DIR}/models"
        log_ok "Using system LocalAI binary at ${LOCALAI_EXEC}"
        ensure_cpu_parakeet_backend "${LOCALAI_EXEC}"
        return 0
    elif [ -x "${HOME}/.local/bin/local-ai" ] && is_valid_binary "${HOME}/.local/bin/local-ai"; then
        LOCALAI_EXEC="${HOME}/.local/bin/local-ai"
        LOCALAI_MODELS_PATH="${ROOT_DIR}/models"
        log_ok "Using user LocalAI binary at ${LOCALAI_EXEC}"
        ensure_cpu_parakeet_backend "${LOCALAI_EXEC}"
        return 0
    elif [ -x "/usr/local/bin/local-ai" ] && is_valid_binary "/usr/local/bin/local-ai"; then
        LOCALAI_EXEC="/usr/local/bin/local-ai"
        LOCALAI_MODELS_PATH="${ROOT_DIR}/models"
        log_ok "Using system LocalAI binary at ${LOCALAI_EXEC}"
        ensure_cpu_parakeet_backend "${LOCALAI_EXEC}"
        return 0
    fi

    # Check if resident LocalAI binary is already present and valid
    if [ -x "${ROOT_DIR}/../LocalAI/local-ai" ] && is_valid_binary "${ROOT_DIR}/../LocalAI/local-ai"; then
        LOCALAI_EXEC="${ROOT_DIR}/../LocalAI/local-ai"
        LOCALAI_MODELS_PATH="${ROOT_DIR}/../LocalAI/models"
        log_ok "Using resident LocalAI binary at ${LOCALAI_EXEC}"
        ensure_cpu_parakeet_backend "${LOCALAI_EXEC}"
        return 0
    fi

    # Check installed LocalAI binary in .tools/localai/local-ai
    if [ -f "${TOOLS_DIR}/localai/local-ai" ]; then
        if is_valid_binary "${TOOLS_DIR}/localai/local-ai"; then
            LOCALAI_EXEC="${TOOLS_DIR}/localai/local-ai"
            LOCALAI_MODELS_PATH="${ROOT_DIR}/models"
            log_ok "Using installed LocalAI binary at ${LOCALAI_EXEC}"
            ensure_cpu_parakeet_backend "${LOCALAI_EXEC}"
            return 0
        else
            log_warn "Existing file at ${TOOLS_DIR}/localai/local-ai failed binary validation (e.g. 404 HTML text). Removing corrupted file..."
            rm -rf "${TOOLS_DIR}/localai"
        fi
    fi

    if [ "${PLATFORM}" = "linux" ] && [ "${ARCH}" = "x86_64" ]; then
        mkdir -p "${TOOLS_DIR}/localai"
        local success="false"

        # Strategy A: Official install script
        log_info "Attempting LocalAI installation via official install wrapper..."
        if [ "${IS_ROOT:-false}" = "true" ] || [ "$(id -u)" -eq 0 ]; then
            if curl -sSLf https://localai.io/install.sh | bash >/dev/null 2>&1; then
                if command -v local-ai >/dev/null 2>&1 && is_valid_binary "$(command -v local-ai)"; then
                    LOCALAI_EXEC="$(command -v local-ai)"
                    success="true"
                elif [ -x "/usr/local/bin/local-ai" ] && is_valid_binary "/usr/local/bin/local-ai"; then
                    LOCALAI_EXEC="/usr/local/bin/local-ai"
                    success="true"
                fi
            fi
        fi

        if [ "${success}" != "true" ]; then
            if curl -sSLf https://localai.io/install.sh | INSTALL_DIR="${TOOLS_DIR}/localai" LOCALAI_NO_SUDO=1 bash >/dev/null 2>&1; then
                if [ -x "${TOOLS_DIR}/localai/local-ai" ] && is_valid_binary "${TOOLS_DIR}/localai/local-ai"; then
                    LOCALAI_EXEC="${TOOLS_DIR}/localai/local-ai"
                    success="true"
                elif [ -x "${HOME}/.local/bin/local-ai" ] && is_valid_binary "${HOME}/.local/bin/local-ai"; then
                    LOCALAI_EXEC="${HOME}/.local/bin/local-ai"
                    success="true"
                elif [ -x "/usr/local/bin/local-ai" ] && is_valid_binary "/usr/local/bin/local-ai"; then
                    LOCALAI_EXEC="/usr/local/bin/local-ai"
                    success="true"
                fi
            fi
        fi

        if [ "${success}" = "true" ]; then
            LOCALAI_MODELS_PATH="${ROOT_DIR}/models"
            log_ok "LocalAI engine installed and verified at ${LOCALAI_EXEC}"
            ensure_cpu_parakeet_backend "${LOCALAI_EXEC}"
            return 0
        fi

        log_warn "LocalAI installation could not be completed automatically. LocalAI will remain in STANDBY."
        return 0
    else
        if [ "${PLATFORM}" = "macos" ]; then
            log_warn "Resident LocalAI binary not found in ../LocalAI/local-ai. LocalAI will remain in STANDBY on macOS."
            return 0
        fi
        log_warn "Unsupported platform for automated LocalAI binary: ${PLATFORM} (${ARCH}). LocalAI will remain in STANDBY."
        return 0
    fi
}

# 6. Setup local-tts engine (Kokoro via saghul/local-tts per localtts.md)
install_local_tts_engine() {
    # On Linux, ensure audio phonemizer libraries (espeak-ng) are installed first
    if [ "${PLATFORM}" = "linux" ]; then
        if [ "${IS_ROOT:-false}" = "true" ] || [ "$(id -u)" -eq 0 ]; then
            if command -v apt-get >/dev/null 2>&1; then
                if ! command -v espeak-ng >/dev/null 2>&1; then
                    log_info "Installing espeak-ng and audio system packages for Kokoro TTS..."
                    apt-get update -qq && apt-get install -y --no-install-recommends \
                        ffmpeg sox espeak-ng libespeak-ng-dev >/dev/null 2>&1 || true
                fi
            fi
        fi
    fi

    # Check if local-tts is already installed and executable
    for candidate in "${ROOT_DIR}/../local-tts/.venv/bin/local-tts" \
                     "${ROOT_DIR}/local-tts/.venv/bin/local-tts" \
                     "${TOOLS_DIR}/local-tts/.venv/bin/local-tts" \
                     "${ARCH_DIR:-${ROOT_DIR}/arch}/local-tts/.venv/bin/local-tts"; do
        if [ -x "${candidate}" ]; then
            log_ok "Using resident local-tts engine at ${candidate}"
            return 0
        fi
    done

    if command -v local-tts >/dev/null 2>&1; then
        log_ok "Using system local-tts binary at $(command -v local-tts)"
        return 0
    elif [ -x "${HOME}/.local/bin/local-tts" ]; then
        log_ok "Using user local-tts binary at ${HOME}/.local/bin/local-tts"
        return 0
    fi

    if [ "${PLATFORM}" = "linux" ]; then
        log_info "Installing local-tts (Kokoro PyTorch/CUDA engine for Linux per localtts.md)..."

        # 1. Install optional system audio libraries if root
        if [ "${IS_ROOT:-false}" = "true" ] || [ "$(id -u)" -eq 0 ]; then
            if command -v apt-get >/dev/null 2>&1; then
                apt-get update -qq && apt-get install -y --no-install-recommends \
                    ffmpeg sox espeak-ng >/dev/null 2>&1 || true
            fi
        fi

        # 2. Ensure uv package manager is available
        export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:/usr/local/bin:${PATH}"
        local uv_bin=""
        if command -v uv >/dev/null 2>&1; then
            uv_bin="$(command -v uv)"
        elif [ -x "${HOME}/.local/bin/uv" ]; then
            uv_bin="${HOME}/.local/bin/uv"
        elif [ -x "/usr/local/bin/uv" ]; then
            uv_bin="/usr/local/bin/uv"
        fi

        if [ -z "${uv_bin}" ] || [ ! -x "${uv_bin}" ]; then
            log_info "Installing uv package manager..."
            curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || true
            export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:/usr/local/bin:${PATH}"
            if command -v uv >/dev/null 2>&1; then
                uv_bin="$(command -v uv)"
            elif [ -x "${HOME}/.local/bin/uv" ]; then
                uv_bin="${HOME}/.local/bin/uv"
            fi
        fi

        if [ -z "${uv_bin}" ] || [ ! -x "${uv_bin}" ]; then
            log_warn "uv package manager could not be installed; skipping local-tts automated install."
            return 0
        fi
        log_ok "uv package manager verified"

        # 3. Determine target directory for local-tts clone
        local target_tts_dir="${ROOT_DIR}/../local-tts"
        if [ ! -d "${target_tts_dir}" ]; then
            if [ -w "${ROOT_DIR}/.." ]; then
                target_tts_dir="${ROOT_DIR}/../local-tts"
            else
                target_tts_dir="${TOOLS_DIR}/local-tts"
            fi
        fi

        # 4. Clone repository if not present
        if [ ! -d "${target_tts_dir}/.git" ]; then
            log_info "Cloning local-tts from https://github.com/saghul/local-tts.git into ${target_tts_dir}..."
            mkdir -p "$(dirname "${target_tts_dir}")"
            git clone --depth 1 https://github.com/saghul/local-tts.git "${target_tts_dir}" || {
                log_warn "git clone failed for saghul/local-tts; local-tts will remain in STANDBY."
                return 0
            }
        fi

        # 5. Install dependencies via uv sync
        log_info "Installing local-tts dependencies via uv (per localtts.md)..."
        (
            cd "${target_tts_dir}"
            # Try with CUDA extra first for GPU acceleration, fallback to standard sync for CPU
            if "${uv_bin}" sync --extra cuda >/dev/null 2>&1; then
                log_ok "local-tts dependencies synced with CUDA extra."
            else
                log_info "CUDA extra sync unavailable; syncing standard backend..."
                "${uv_bin}" sync >/dev/null 2>&1 || true
            fi
            # Install hf_transfer to support fast Hugging Face downloads on RunPod
            "${uv_bin}" pip install hf_transfer >/dev/null 2>&1 || true
        )

        if [ -x "${target_tts_dir}/.venv/bin/local-tts" ]; then
            log_ok "local-tts engine installed and verified at ${target_tts_dir}/.venv/bin/local-tts"
            return 0
        fi

        log_warn "local-tts virtualenv build did not produce executable; service will remain in STANDBY."
        return 0
    else
        if [ "${PLATFORM}" = "macos" ]; then
            log_warn "Resident local-tts binary not found in ../local-tts/.venv/bin/local-tts on macOS. LocalTTS will remain in STANDBY."
            return 0
        fi
    fi
}

# 7. Setup default Streaming STT model (Sherpa ONNX)
install_streaming_stt_model() {
    local target_dir="${ROOT_DIR}/models/streaming-stt/${STREAMING_STT_DEFAULT_MODEL}"
    if [ -d "${target_dir}" ] && [ -f "${target_dir}/tokens.txt" ]; then
        log_ok "Default streaming STT model present (${STREAMING_STT_DEFAULT_MODEL})"
        return 0
    fi

    log_info "Downloading default streaming STT model (${STREAMING_STT_DEFAULT_MODEL})..."
    mkdir -p "${ROOT_DIR}/models/streaming-stt"
    local tarball_path="${ROOT_DIR}/models/streaming-stt/${STREAMING_STT_DEFAULT_MODEL}.tar.bz2"
    curl -sSL -o "${tarball_path}" "${STREAMING_STT_DEFAULT_URL}"
    tar -xjf "${tarball_path}" -C "${ROOT_DIR}/models/streaming-stt"
    rm -f "${tarball_path}"
    log_ok "Streaming STT model extracted to ${target_dir}"
}

# Master installation workflow
run_installer() {
    log_info "=== Starting One-Command Automated Installation ==="
    run_preflight
    install_system_packages
    install_python_env
    install_node_and_frontend
    install_localai_engine
    install_local_tts_engine
    install_streaming_stt_model
    log_ok "=== Installation Complete and Fully Verified ==="
}

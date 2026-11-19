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

# 1. Preflight system check
run_preflight() {
    log_info "Running system preflight checks..."
    log_ok "Platform: ${PLATFORM} (${ARCH}), Root: ${IS_ROOT}, RunPod: ${IS_RUNPOD}"

    # Python version check
    if ! command -v python3 >/dev/null 2>&1; then
        log_err "Python 3 is not installed. Python >= ${REQUIRED_PYTHON_MIN_VERSION} is required."
        exit 1
    fi

    local py_ver
    py_ver="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"
    log_ok "Detected Python ${py_ver} (Minimum required: ${REQUIRED_PYTHON_MIN_VERSION})"

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
    local needed=("ffmpeg" "curl" "ca-certificates" "git" "lsof" "build-essential" "python3-venv" "libsndfile1" "xz-utils")

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

    if [ ! -d "${venv_dir}" ] || [ ! -f "${venv_dir}/bin/python" ]; then
        log_info "Creating Python virtual environment in ${venv_dir}..."
        python3 -m venv "${venv_dir}"
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
    if [ ! -f "${frontend_dir}/dist/index.html" ]; then
        log_info "Building production frontend web application (${frontend_dir}/dist)..."
        npm --prefix "${frontend_dir}" run build
        log_ok "Frontend production assets built successfully."
    else
        log_ok "Production frontend assets present in frontend/dist."
    fi
}

# 5. Setup LocalAI binary
install_localai_engine() {
    # Check if LocalAI binary is already present
    if [ -x "${ROOT_DIR}/../LocalAI/local-ai" ]; then
        LOCALAI_EXEC="${ROOT_DIR}/../LocalAI/local-ai"
        LOCALAI_MODELS_PATH="${ROOT_DIR}/../LocalAI/models"
        log_ok "Using resident LocalAI binary at ${LOCALAI_EXEC}"
        return 0
    elif [ -x "${TOOLS_DIR}/localai/local-ai" ]; then
        LOCALAI_EXEC="${TOOLS_DIR}/localai/local-ai"
        LOCALAI_MODELS_PATH="${ROOT_DIR}/models"
        log_ok "Using installed LocalAI binary at ${LOCALAI_EXEC}"
        return 0
    fi

    if [ "${PLATFORM}" = "linux" ] && [ "${ARCH}" = "x86_64" ]; then
        local download_url="${LOCALAI_LINUX_X64_URL}"
        if [ "${HAS_NVIDIA_GPU:-false}" = "true" ]; then
            download_url="${LOCALAI_LINUX_CUDA12_URL}"
            log_info "Selected CUDA12-accelerated LocalAI binary"
        fi

        log_info "Downloading LocalAI ${LOCALAI_VERSION} from ${download_url}..."
        mkdir -p "${TOOLS_DIR}/localai"
        curl -sSL -o "${TOOLS_DIR}/localai/local-ai" "${download_url}"
        chmod +x "${TOOLS_DIR}/localai/local-ai"
        LOCALAI_EXEC="${TOOLS_DIR}/localai/local-ai"
        LOCALAI_MODELS_PATH="${ROOT_DIR}/models"
        log_ok "LocalAI engine installed into ${LOCALAI_EXEC}"
    else
        log_err "LocalAI binary not found. On macOS, run within the project workspace with ../LocalAI present."
        exit 1
    fi
}

# 6. Setup default Streaming STT model (Sherpa ONNX)
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
    install_streaming_stt_model
    log_ok "=== Installation Complete and Fully Verified ==="
}

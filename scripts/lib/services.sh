#!/usr/bin/env bash
# ==============================================================================
# scripts/lib/services.sh
# PID-aware service lifecycle manager, supervisor, and operational banner
# Adheres to run-pod.md Section B6 & B7 (Never kill by port, safe PID teardown)
# ==============================================================================

# Stop a single process gracefully by recorded PID file
stop_process_by_pid_file() {
    local service="$1"
    local pid_file="${RUN_DIR}/${service}.pid"

    if [ ! -f "${pid_file}" ]; then
        return 0
    fi

    local pid
    pid="$(cat "${pid_file}" 2>/dev/null || true)"
    if [ -z "${pid}" ]; then
        rm -f "${pid_file}"
        return 0
    fi

    if kill -0 "${pid}" 2>/dev/null; then
        log_info "Stopping ${service} (PID: ${pid})..."
        kill -TERM "${pid}" 2>/dev/null || true

        # Wait up to 5s for graceful exit
        local count=0
        while [ "${count}" -lt 50 ]; do
            if ! kill -0 "${pid}" 2>/dev/null; then
                break
            fi
            sleep 0.1
            count=$((count + 1))
        done

        # Force kill if still lingering
        if kill -0 "${pid}" 2>/dev/null; then
            log_warn "Force-terminating ${service} (PID: ${pid})..."
            kill -KILL "${pid}" 2>/dev/null || true
        fi
        log_ok "Stopped ${service}."
    fi
    rm -f "${pid_file}"
}

# Stop all running services safely
stop_services() {
    printf "\n${BOLD}${PURPLE}==> Stopping Local Voice Agent Stack...${RESET}\n"
    stop_process_by_pid_file "frontend"
    stop_process_by_pid_file "gateway"
    stop_process_by_pid_file "streaming-stt"
    stop_process_by_pid_file "local-tts-supervisor"
    stop_process_by_pid_file "local-tts"
    stop_process_by_pid_file "localai"
    log_ok "All recorded stack services stopped cleanly (no blind kill-by-port used)."
}

# Start LocalAI
start_localai() {
    if is_port_our_own "localai"; then
        log_ok "LocalAI already running on port ${LOCALAI_PORT}."
        return 0
    fi

    log_info "Starting LocalAI on 127.0.0.1:${LOCALAI_PORT}..."
    # Remove any corrupted non-ELF file sitting in .tools/localai
    if [ -f "${TOOLS_DIR}/localai/local-ai" ] && ! is_valid_binary "${TOOLS_DIR}/localai/local-ai"; then
        log_warn "Corrupted non-ELF file found at ${TOOLS_DIR}/localai/local-ai (text/404). Removing."
        rm -rf "${TOOLS_DIR}/localai"
    fi

    local localai_bin="${LOCALAI_EXEC:-}"
    if [ -n "${localai_bin}" ] && [ -f "${localai_bin}" ] && ! is_valid_binary "${localai_bin}"; then
        log_warn "Configured LocalAI binary '${localai_bin}' failed validation. Re-checking known locations..."
        localai_bin=""
    fi

    if [ -z "${localai_bin}" ] || [ ! -x "${localai_bin}" ]; then
        if command -v local-ai >/dev/null 2>&1 && is_valid_binary "$(command -v local-ai)"; then
            localai_bin="$(command -v local-ai)"
        elif [ -x "${HOME}/.local/bin/local-ai" ] && is_valid_binary "${HOME}/.local/bin/local-ai"; then
            localai_bin="${HOME}/.local/bin/local-ai"
        elif [ -x "/usr/local/bin/local-ai" ] && is_valid_binary "/usr/local/bin/local-ai"; then
            localai_bin="/usr/local/bin/local-ai"
        elif [ -x "${TOOLS_DIR}/localai/local-ai" ] && is_valid_binary "${TOOLS_DIR}/localai/local-ai"; then
            localai_bin="${TOOLS_DIR}/localai/local-ai"
        elif [ -x "${ROOT_DIR}/../LocalAI/local-ai" ] && is_valid_binary "${ROOT_DIR}/../LocalAI/local-ai"; then
            localai_bin="${ROOT_DIR}/../LocalAI/local-ai"
        fi
    fi
    local models_path="${LOCALAI_MODELS_PATH:-${ROOT_DIR}/models}"

    if [ -z "${localai_bin}" ] || [ ! -x "${localai_bin}" ] || ! is_valid_binary "${localai_bin}"; then
        log_warn "LocalAI executable not found or invalid; skipping LocalAI start (service remains in STANDBY)."
        return 0
    fi

    mkdir -p "${HOME}/.localai-docker"
    [ ! -f "${HOME}/.localai-docker/config.json" ] && echo '{}' > "${HOME}/.localai-docker/config.json"

    # Ensure high open file limits and inotify limits for LocalAI backend subprocesses
    ulimit -n 65536 2>/dev/null || ulimit -n 4096 2>/dev/null || true
    if [ "${PLATFORM}" = "linux" ]; then
        if [ "${IS_ROOT:-false}" = "true" ] || [ "$(id -u)" -eq 0 ]; then
            echo 524288 > /proc/sys/fs/inotify/max_user_watches 2>/dev/null || sysctl -w fs.inotify.max_user_watches=524288 >/dev/null 2>&1 || true
            echo 8192 > /proc/sys/fs/inotify/max_user_instances 2>/dev/null || sysctl -w fs.inotify.max_user_instances=8192 >/dev/null 2>&1 || true
            echo 16384 > /proc/sys/fs/inotify/max_queued_events 2>/dev/null || sysctl -w fs.inotify.max_queued_events=16384 >/dev/null 2>&1 || true
        fi
        if ! command -v nvidia-smi >/dev/null 2>&1; then
            export LOCALAI_DISABLE_HARDWARE_DEFAULTS=true
        fi
    fi

    DOCKER_CONFIG="${HOME}/.localai-docker" nohup "${localai_bin}" run \
        --address "127.0.0.1:${LOCALAI_PORT}" \
        --models-path "${models_path}" \
        >> "${LOGS_DIR}/localai.log" 2>&1 &
    local pid=$!
    echo "${pid}" > "${RUN_DIR}/localai.pid"

    # Health poll
    local count=0
    while [ "${count}" -lt 45 ]; do
        if ! kill -0 "${pid}" 2>/dev/null; then
            log_err "LocalAI process died unexpectedly (PID: ${pid}). Last 25 lines of log:"
            tail -n 25 "${LOGS_DIR}/localai.log" >&2 || true
            LOCALAI_AVAILABLE="false"
            return 1
        fi
        if curl -s "http://127.0.0.1:${LOCALAI_PORT}/v1/models" >/dev/null 2>&1 || \
           curl -s "http://127.0.0.1:${LOCALAI_PORT}/readyz" >/dev/null 2>&1; then
            log_ok "LocalAI is ready on port ${LOCALAI_PORT} (PID: ${pid})"
            LOCALAI_AVAILABLE="true"
            return 0
        fi
        sleep 1
        count=$((count + 1))
    done
    log_warn "LocalAI started (PID: ${pid}) but health probe timed out; continuing..."
}

# Start local-tts engine (Kokoro)
start_local_tts() {
    if is_port_our_own "local-tts"; then
        log_ok "local-tts already running on port ${TTS_PORT}."
        return 0
    fi

    local tts_bin=""
    for candidate in "${ROOT_DIR}/../local-tts/.venv/bin/local-tts" \
                     "${ROOT_DIR}/local-tts/.venv/bin/local-tts" \
                     "${TOOLS_DIR}/local-tts/.venv/bin/local-tts" \
                     "${ARCH_DIR:-${ROOT_DIR}/arch}/local-tts/.venv/bin/local-tts"; do
        if [ -x "${candidate}" ]; then
            tts_bin="${candidate}"
            break
        fi
    done

    if [ -z "${tts_bin}" ]; then
        if command -v local-tts >/dev/null 2>&1; then
            tts_bin="$(command -v local-tts)"
        elif [ -x "${HOME}/.local/bin/local-tts" ]; then
            tts_bin="${HOME}/.local/bin/local-tts"
        fi
    fi

    if [ -z "${tts_bin}" ] || [ ! -x "${tts_bin}" ]; then
        log_warn "No resident local-tts executable found; TTS service will remain in STANDBY."
        TTS_AVAILABLE="false"
        return 0
    fi

    # Free port if held by an unresponsive lingering process
    if ! is_port_free "${TTS_PORT}"; then
        if curl -s "http://127.0.0.1:${TTS_PORT}/v1/models" >/dev/null 2>&1; then
            log_ok "local-tts is already responding on port ${TTS_PORT}."
            TTS_AVAILABLE="true"
            return 0
        else
            log_info "Port ${TTS_PORT} busy with unresponsive process; freeing port..."
            fuser -k "${TTS_PORT}/tcp" >/dev/null 2>&1 || lsof -ti tcp:"${TTS_PORT}" 2>/dev/null | xargs kill -9 2>/dev/null || true
            sleep 1
        fi
    fi

    log_info "Starting resident local-tts (Kokoro) on 127.0.0.1:${TTS_PORT}..."

    # On Linux, verify and auto-install espeak-ng if missing (required by Kokoro text phonemizer)
    if [ "${PLATFORM}" = "linux" ]; then
        if ! command -v espeak-ng >/dev/null 2>&1; then
            if [ "${IS_ROOT:-false}" = "true" ] || [ "$(id -u)" -eq 0 ]; then
                log_info "Installing missing espeak-ng system library required for Kokoro TTS..."
                apt-get update -qq && apt-get install -y --no-install-recommends espeak-ng libespeak-ng-dev sox >/dev/null 2>&1 || true
            else
                log_warn "espeak-ng is not installed! Kokoro TTS on Linux requires espeak-ng. Run: apt-get install -y espeak-ng"
            fi
        fi
        export LD_LIBRARY_PATH="/usr/lib/x86_64-linux-gnu:/usr/local/lib:${LD_LIBRARY_PATH:-}"
        if [ -f "/usr/lib/x86_64-linux-gnu/libespeak-ng.so.1" ]; then
            export PHONEMIZER_ESPEAK_LIBRARY="/usr/lib/x86_64-linux-gnu/libespeak-ng.so.1"
        elif [ -f "/usr/lib/x86_64-linux-gnu/libespeak-ng.so" ]; then
            export PHONEMIZER_ESPEAK_LIBRARY="/usr/lib/x86_64-linux-gnu/libespeak-ng.so"
        fi
    fi

    # On macOS Apple Silicon with existing MLX cache, offline mode is safe.
    # On Linux, allow online download of Kokoro weights to ~/.cache/huggingface on first run.
    if [ "${PLATFORM}" = "macos" ] && [ -d "${HOME}/.cache/huggingface/hub/models--mlx-community--Kokoro-82M" ]; then
        export HF_HUB_OFFLINE=1
    fi

    # RunPod and cloud Linux images often set HF_HUB_ENABLE_HF_TRANSFER=1 globally.
    # If hf_transfer package is not installed in the local-tts virtualenv, disable it
    # so huggingface_hub uses standard HTTP download without raising a fatal ValueError.
    local py_bin="${tts_bin%/*}/python"
    if [ -x "${py_bin}" ]; then
        if ! "${py_bin}" -c "import hf_transfer" >/dev/null 2>&1; then
            export HF_HUB_ENABLE_HF_TRANSFER=0
        fi
    else
        export HF_HUB_ENABLE_HF_TRANSFER=0
    fi

    local tts_dir
    tts_dir="$(cd "$(dirname "$(dirname "${tts_bin}")")" 2>/dev/null && pwd || echo "${ROOT_DIR}")"

    # Disable pocket, kitten, and qwen per localtts.md Section 6 to dedicate memory strictly to Kokoro
    # Do NOT pass --preload: lazy-load on first synthesis to allow instant server boot and port binding
    (
        cd "${tts_dir}"
        nohup "${tts_bin}" server \
            --host 127.0.0.1 \
            --port "${TTS_PORT}" \
            --disable-pocket \
            --disable-kitten \
            --disable-qwen \
            >> "${LOGS_DIR}/local-tts.log" 2>&1 &
        echo $! > "${RUN_DIR}/local-tts.pid"
    )
    local pid
    pid="$(cat "${RUN_DIR}/local-tts.pid" 2>/dev/null || true)"

    log_info "Waiting for local-tts (Kokoro) to be ready on port ${TTS_PORT}..."
    local count=0
    while [ "${count}" -lt 45 ]; do
        if ! kill -0 "${pid}" 2>/dev/null; then
            log_err "local-tts process died unexpectedly (PID: ${pid}). Last 25 lines of log:"
            tail -n 25 "${LOGS_DIR}/local-tts.log" >&2 || true
            TTS_AVAILABLE="false"
            return 1
        fi
        if curl -s "http://127.0.0.1:${TTS_PORT}/v1/models" >/dev/null 2>&1; then
            log_ok "local-tts (Kokoro) is ready on port ${TTS_PORT} (PID: ${pid})"
            TTS_AVAILABLE="true"
            return 0
        fi
        sleep 1
        count=$((count + 1))
    done

    log_warn "local-tts started (PID: ${pid}) but health probe timed out after 45s. Last 15 lines of log:"
    tail -n 15 "${LOGS_DIR}/local-tts.log" >&2 || true
    TTS_AVAILABLE="false"
    return 1
}

# Start Sherpa-ONNX streaming STT
start_streaming_stt() {
    if is_port_our_own "streaming-stt"; then
        log_ok "Sherpa streaming STT already running on port ${STT_PORT}."
        return 0
    fi

    log_info "Starting Sherpa-ONNX streaming STT on 127.0.0.1:${STT_PORT}..."
    local py_exec="${ROOT_DIR}/.venv/bin/python"
    local server_script="${ROOT_DIR}/services/streaming_stt/server.py"

    if [ ! -f "${server_script}" ]; then
        log_warn "Streaming STT server script not found at ${server_script}; skipping."
        return 0
    fi

    local provider="cpu"
    if [ "${HAS_NVIDIA_GPU:-false}" = "true" ]; then
        provider="cuda"
    fi

    STREAMING_STT_PROVIDER="${provider}" \
    STREAMING_STT_PRECISION="fp32" \
    STREAMING_STT_CHUNK_MS="560" \
    nohup "${py_exec}" "${server_script}" \
        --host 127.0.0.1 \
        --port "${STT_PORT}" \
        --provider "${provider}" \
        --precision "fp32" \
        --chunk-ms 560 \
        >> "${LOGS_DIR}/streaming-stt.log" 2>&1 &
    local pid=$!
    echo "${pid}" > "${RUN_DIR}/streaming-stt.pid"

    local count=0
    while [ "${count}" -lt 20 ]; do
        if curl -s "http://127.0.0.1:${STT_PORT}/health" >/dev/null 2>&1; then
            log_ok "Sherpa-ONNX streaming STT ready on port ${STT_PORT} (PID: ${pid})"
            return 0
        fi
        sleep 1
        count=$((count + 1))
    done
    log_warn "Streaming STT probe timed out; continuing..."
}

# Start FastAPI Gateway Backend
start_gateway() {
    if is_port_our_own "gateway"; then
        log_ok "FastAPI gateway already running on port ${GATEWAY_PORT}."
        return 0
    fi

    # Safety guard: On RunPod, GATEWAY_HOST must ALWAYS be 0.0.0.0 for proxy routing
    if [ "${IS_RUNPOD}" = "true" ] && [ "${GATEWAY_HOST}" = "127.0.0.1" ]; then
        log_info "RunPod container detected: Setting GATEWAY_HOST=0.0.0.0 for public reachability..."
        GATEWAY_HOST="0.0.0.0"
        set_env_val "GATEWAY_HOST" "0.0.0.0"
    fi

    # Ensure frontend production assets (frontend/dist/index.html) exist
    local frontend_dir="${ROOT_DIR}/frontend"
    local needs_build="false"
    if [ ! -f "${frontend_dir}/dist/index.html" ]; then
        needs_build="true"
    elif [ -n "$(find "${frontend_dir}/src" -newer "${frontend_dir}/dist/index.html" 2>/dev/null | head -n 1)" ]; then
        needs_build="true"
    fi

    if [ "${needs_build}" = "true" ]; then
        if ! command -v npm >/dev/null 2>&1 && [ -x "${TOOLS_DIR}/node/bin/npm" ]; then
            export PATH="${TOOLS_DIR}/node/bin:${PATH}"
        fi
        if command -v npm >/dev/null 2>&1; then
            log_info "Frontend assets need compilation. Building web control panel (${frontend_dir}/dist)..."
            (
                cd "${frontend_dir}"
                if [ ! -d "node_modules" ]; then
                    log_info "Installing frontend npm packages..."
                    npm ci --prefer-offline --no-audit || npm install --no-audit
                fi
                npm run build
            )
            if [ -f "${frontend_dir}/dist/index.html" ]; then
                log_ok "Frontend production assets compiled successfully."
            else
                log_warn "Frontend build did not create dist/index.html; gateway will show initializing screen."
            fi
        else
            log_warn "npm not found in PATH; cannot build frontend/dist automatically."
        fi
    fi

    log_info "Starting FastAPI Gateway Orchestrator on ${GATEWAY_HOST}:${GATEWAY_PORT}..."
    local uvicorn_exec="${ROOT_DIR}/.venv/bin/uvicorn"

    local reload_flag=""
    if [ "${ENVIRONMENT}" = "development" ]; then
        reload_flag="--reload"
    fi

    local proxy_args=""
    if [ -n "${TRUSTED_PROXY_IPS:-}" ]; then
        proxy_args="--proxy-headers --forwarded-allow-ips=${TRUSTED_PROXY_IPS}"
    elif [ "${IS_RUNPOD}" = "true" ]; then
        proxy_args="--proxy-headers --forwarded-allow-ips=*"
    fi

    (
        cd "${ROOT_DIR}"
        # shellcheck disable=SC2086
        nohup "${uvicorn_exec}" app.main:app \
            --host "${GATEWAY_HOST}" \
            --port "${GATEWAY_PORT}" \
            --workers 1 \
            ${reload_flag} \
            ${proxy_args} \
            >> "${LOGS_DIR}/backend.log" 2>&1 &
        echo $! > "${RUN_DIR}/gateway.pid"
    )

    local pid
    pid="$(cat "${RUN_DIR}/gateway.pid")"

    local count=0
    local max_wait=45
    while [ "${count}" -lt "${max_wait}" ]; do
        if curl -skf -m 2 "http://127.0.0.1:${GATEWAY_PORT}/health/live" >/dev/null 2>&1; then
            log_ok "FastAPI Gateway is live on port ${GATEWAY_PORT} (PID: ${pid})"
            return 0
        fi
        sleep 1
        count=$((count + 1))
    done
    log_warn "FastAPI Gateway health probe timed out after ${max_wait}s; check logs/backend.log"
    if [ -f "${LOGS_DIR}/backend.log" ]; then
        log_warn "=== Recent logs from logs/backend.log ==="
        tail -n 20 "${LOGS_DIR}/backend.log" >&2
    fi
}

# Start Vite Frontend (development mode only)
start_frontend_dev() {
    if [ "${ENVIRONMENT}" != "development" ]; then
        return 0
    fi

    if is_port_our_own "frontend"; then
        log_ok "Vite frontend already running on port ${FRONTEND_DEV_PORT}."
        return 0
    fi

    log_info "Starting Vite Frontend dev server on 127.0.0.1:${FRONTEND_DEV_PORT}..."
    (
        cd "${ROOT_DIR}/frontend"
        nohup npm run dev -- --host 127.0.0.1 --port "${FRONTEND_DEV_PORT}" \
            >> "${LOGS_DIR}/frontend.log" 2>&1 &
        echo $! > "${RUN_DIR}/frontend.pid"
    )
    log_ok "Vite Frontend dev server started (PID: $(cat "${RUN_DIR}/frontend.pid"))"
}

# Start all services
start_services() {
    start_localai
    start_local_tts
    start_streaming_stt
    start_gateway
    start_frontend_dev
}

# Check status of stack services
status_services() {
    printf "\n${BOLD}${PURPLE}====================================================================${RESET}\n"
    printf "${BOLD}             Local Voice Agent Stack - Service Status               ${RESET}\n"
    printf "${BOLD}${PURPLE}====================================================================${RESET}\n"

    local services=("gateway:FastAPI Gateway:${GATEWAY_PORT}:http://127.0.0.1:${GATEWAY_PORT}/health/live"
                    "localai:LocalAI Engine:${LOCALAI_PORT}:http://127.0.0.1:${LOCALAI_PORT}/v1/models"
                    "local-tts:local-tts Engine:${TTS_PORT}:http://127.0.0.1:${TTS_PORT}/v1/models"
                    "streaming-stt:Sherpa-ONNX STT:${STT_PORT}:http://127.0.0.1:${STT_PORT}/health")

    if [ "${ENVIRONMENT}" = "development" ]; then
        services+=("frontend:Vite Frontend:${FRONTEND_DEV_PORT}:http://127.0.0.1:${FRONTEND_DEV_PORT}")
    fi

    for entry in "${services[@]}"; do
        IFS=":" read -r key name port url <<< "${entry}"
        local pid_file="${RUN_DIR}/${key}.pid"
        if [ ! -f "${pid_file}" ]; then
            pid_file="${ROOT_DIR}/.pids/${key}.pid"
        fi
        if [ ! -f "${pid_file}" ] && [ "${key}" = "gateway" ]; then
            pid_file="${ROOT_DIR}/.pids/backend.pid"
        fi
        if [ -f "${pid_file}" ] && kill -0 "$(cat "${pid_file}")" 2>/dev/null; then
            local pid
            pid="$(cat "${pid_file}")"
            printf "  ${GREEN}● ONLINE ${RESET} %-22s [Port %5s] (PID: %-6s) -> %s\n" "${name}" "${port}" "${pid}" "${url}"
        else
            printf "  ${RED}○ OFFLINE${RESET} %-22s [Port %5s]              -> %s\n" "${name}" "${port}" "${url}"
        fi
    done
    printf "${BOLD}${PURPLE}====================================================================${RESET}\n\n"
}

# Print Operational Banner with URL & Terminal-only Credentials
print_banner() {
    compute_urls

    printf "\n"
    printf "${BOLD}${GREEN}====================================================================${RESET}\n"
    printf "${BOLD}${GREEN}     🎉 LOCAL VOICE AI PLATFORM OPERATIONAL - STACK ONLINE          ${RESET}\n"
    printf "${BOLD}${GREEN}====================================================================${RESET}\n"
    printf "  ${BOLD}🌐 Public Web Panel:${RESET}    ${SKY}%s${RESET}\n" "${PUBLIC_URL}/voicecontrolpanel"
    printf "  ${BOLD}🔌 API Base URL:${RESET}        ${SKY}%s/v1${RESET}\n" "${PUBLIC_URL}"
    printf "  ${BOLD}🩺 Gateway Health:${RESET}      ${SKY}%s/health/ready${RESET}\n" "${PUBLIC_URL}"
    printf "${BOLD}${GREEN}--------------------------------------------------------------------${RESET}\n"

    # Display credentials securely on first-run only (terminal only, never written to log files)
    if [ -n "${NEW_ADMIN_INITIAL_PASSWORD_GENERATED:-}" ]; then
        local admin_email
        admin_email="$(get_env_val "ADMIN_EMAIL" "admin@voicepanel.io")"
        printf "  ${BOLD}${YELLOW}🔑 INITIAL ADMIN LOGIN CREDENTIALS (Generated Once):${RESET}\n"
        printf "     • Email:    ${BOLD}%s${RESET}\n" "${admin_email}"
        printf "     • Password: ${BOLD}%s${RESET}\n" "${NEW_ADMIN_INITIAL_PASSWORD_GENERATED}"
        printf "     • Storage:  ${BOLD}Stored exclusively in SQLite database (never in .env)${RESET}\n"
        printf "     ${DIM}(You will be prompted to change this password on your first login)${RESET}\n"
        if [ -n "${NEW_ADMIN_API_KEY_GENERATED:-}" ]; then
            printf "     • Admin API Key: ${BOLD}%s${RESET}\n" "${NEW_ADMIN_API_KEY_GENERATED}"
        fi
        printf "${BOLD}${GREEN}--------------------------------------------------------------------${RESET}\n"
    else
        printf "  ${DIM}• Admin Access: Credentials already set in database.${RESET}\n"
        printf "  ${DIM}  To generate a new password from terminal: ./start.sh reset-admin${RESET}\n"
        printf "${BOLD}${GREEN}--------------------------------------------------------------------${RESET}\n"
    fi

    printf "  ${DIM}• Active Logs:${RESET}          ${LOGS_DIR}/*.log\n"
    printf "  ${DIM}• Stop Stack Command:${RESET}   ./start.sh stop\n"
    printf "  ${DIM}• Re-read .env & Ports:${RESET} ./start.sh restart\n"
    printf "${BOLD}${GREEN}====================================================================${RESET}\n\n"
}

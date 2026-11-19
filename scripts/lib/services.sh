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
    local localai_bin="${LOCALAI_EXEC:-}"
    if [ -z "${localai_bin}" ] || [ ! -x "${localai_bin}" ]; then
        if [ -x "${TOOLS_DIR}/localai/local-ai" ]; then
            localai_bin="${TOOLS_DIR}/localai/local-ai"
        elif [ -x "${ROOT_DIR}/../LocalAI/local-ai" ]; then
            localai_bin="${ROOT_DIR}/../LocalAI/local-ai"
        fi
    fi
    local models_path="${LOCALAI_MODELS_PATH:-${ROOT_DIR}/models}"

    if [ -z "${localai_bin}" ] || [ ! -x "${localai_bin}" ]; then
        log_warn "LocalAI executable not found; skipping LocalAI start."
        return 0
    fi

    mkdir -p "${HOME}/.localai-docker"
    [ ! -f "${HOME}/.localai-docker/config.json" ] && echo '{}' > "${HOME}/.localai-docker/config.json"

    DOCKER_CONFIG="${HOME}/.localai-docker" nohup "${localai_bin}" run \
        --address "127.0.0.1:${LOCALAI_PORT}" \
        --models-path "${models_path}" \
        >> "${LOGS_DIR}/localai.log" 2>&1 &
    local pid=$!
    echo "${pid}" > "${RUN_DIR}/localai.pid"

    # Health poll
    local count=0
    while [ "${count}" -lt 30 ]; do
        if curl -s "http://127.0.0.1:${LOCALAI_PORT}/v1/models" >/dev/null 2>&1; then
            log_ok "LocalAI is ready on port ${LOCALAI_PORT} (PID: ${pid})"
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

    # Check for Apple MLX local-tts binary on macOS
    local tts_bin="${ROOT_DIR}/../local-tts/.venv/bin/local-tts"
    if [ -x "${tts_bin}" ]; then
        log_info "Starting resident local-tts (MLX Kokoro) on 127.0.0.1:${TTS_PORT}..."
        export HF_HUB_OFFLINE=1
        nohup "${tts_bin}" server \
            --host 127.0.0.1 \
            --port "${TTS_PORT}" \
            --disable-qwen \
            >> "${LOGS_DIR}/local-tts.log" 2>&1 &
        local pid=$!
        echo "${pid}" > "${RUN_DIR}/local-tts.pid"

        local count=0
        while [ "${count}" -lt 30 ]; do
            if curl -s "http://127.0.0.1:${TTS_PORT}/v1/models" >/dev/null 2>&1; then
                log_ok "local-tts is ready on port ${TTS_PORT} (PID: ${pid})"
                TTS_AVAILABLE="true"
                return 0
            fi
            sleep 1
            count=$((count + 1))
        done
        TTS_AVAILABLE="true"
        return 0
    fi

    # Linux fallback check
    if [ "${PLATFORM}" = "linux" ]; then
        log_info "Linux TTS engine: MLX is not supported on Linux. Gateway will handle TTS requests with clean 503 fallback."
        TTS_AVAILABLE="false"
        return 0
    fi

    log_warn "No resident TTS engine executable found; TTS will report unavailable."
    TTS_AVAILABLE="false"
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

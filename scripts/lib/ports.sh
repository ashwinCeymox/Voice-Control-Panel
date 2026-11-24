#!/usr/bin/env bash
# ==============================================================================
# scripts/lib/ports.sh
# Portable port conflict detection, auto-migration, and validation
# Adheres to run-pod.md Section B4
# ==============================================================================

# Portable socket bind check (returns 0 if port can be bound on 127.0.0.1, 1 if busy)
is_port_free() {
    local port="$1"
    python3 -c "
import socket, sys
port = int(sys.argv[1])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('127.0.0.1', port))
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" "${port}" 2>/dev/null
}

# Check if port is held by our own recorded PID (checks .run/ and fallback .pids/)
is_port_our_own() {
    local service="$1"
    local pid_file="${RUN_DIR}/${service}.pid"
    if [ ! -f "${pid_file}" ]; then
        pid_file="${ROOT_DIR}/.pids/${service}.pid"
    fi
    if [ ! -f "${pid_file}" ] && [ "${service}" = "gateway" ]; then
        pid_file="${ROOT_DIR}/.pids/backend.pid"
    fi
    if [ ! -f "${pid_file}" ]; then
        return 1
    fi
    local pid
    pid="$(cat "${pid_file}" 2>/dev/null || true)"
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
        return 0
    fi
    return 1
}

# Resolve port for a given service
resolve_service_port() {
    local service="$1"
    local env_key="$2"
    local initial_port="$3"
    local is_public="${4:-false}"

    local port="${initial_port}"

    # Validate numeric
    if ! [[ "${port}" =~ ^[0-9]+$ ]] || [ "${port}" -lt 1 ] || [ "${port}" -gt 65535 ]; then
        log_err "Invalid port configured for ${service} (${env_key}=${port}). Must be 1-65535."
        exit 1
    fi

    # Privileged port check
    if [ "${port}" -lt 1024 ] && [ "${IS_ROOT}" != "true" ]; then
        log_err "Port ${port} for ${service} is a privileged port (<1024) and requires root privileges."
        exit 1
    fi

    if is_port_free "${port}"; then
        echo "${port}"
        return 0
    fi

    # If port is in use by our own recorded PID, it's safe for restart
    if is_port_our_own "${service}"; then
        echo "${port}"
        return 0
    fi

    # Port is in use by an unknown/external process
    if [ "${is_public}" = "true" ] && [ "${IS_RUNPOD}" = "true" ]; then
        log_err "CRITICAL: Public Gateway Port ${port} is currently occupied by an external process."
        log_err "On RunPod, the public HTTP port cannot be auto-migrated because it is fixed in the pod template."
        log_err "Action Required: Stop the conflicting process on port ${port}, or edit GATEWAY_PORT in .env and pod settings."
        exit 1
    fi

    if [ "${AUTO_PORTS}" = "true" ]; then
        log_warn "Port ${port} for ${service} is in use. Searching for next free port (range: ${PORT_SEARCH_RANGE})..."
        local max_port=$((port + PORT_SEARCH_RANGE))
        local candidate=$((port + 1))
        local found=""

        while [ "${candidate}" -le "${max_port}" ]; do
            if is_port_free "${candidate}"; then
                found="${candidate}"
                break
            fi
            candidate=$((candidate + 1))
        done

        if [ -n "${found}" ]; then
            log_ok "Auto-migrated ${service} from port ${port} to free port ${found}."
            set_env_val "${env_key}" "${found}"
            echo "${found}"
            return 0
        else
            log_err "Could not find a free port for ${service} within range ${port}-${max_port}."
            exit 1
        fi
    else
        log_err "Port ${port} for ${service} is in use and AUTO_PORTS=false."
        exit 1
    fi
}

# Resolve and validate all ports
resolve_all_ports() {
    log_info "Verifying and resolving network ports..."

    GATEWAY_PORT="$(resolve_service_port "gateway" "GATEWAY_PORT" "${GATEWAY_PORT}" "true")"
    LOCALAI_PORT="$(resolve_service_port "localai" "LOCALAI_PORT" "${LOCALAI_PORT}" "false")"
    TTS_PORT="$(resolve_service_port "local-tts" "TTS_PORT" "${TTS_PORT}" "false")"
    STT_PORT="$(resolve_service_port "streaming-stt" "STT_PORT" "${STT_PORT}" "false")"
    if [ "${STREAMING_BRIDGE_ENABLED:-false}" = "true" ]; then
        STREAMING_BRIDGE_PORT="$(resolve_service_port "streaming-bridge" "STREAMING_BRIDGE_PORT" "${STREAMING_BRIDGE_PORT:-8089}" "false")"
    fi
    if [ "${ENVIRONMENT}" = "development" ]; then
        FRONTEND_DEV_PORT="$(resolve_service_port "frontend" "FRONTEND_DEV_PORT" "${FRONTEND_DEV_PORT}" "false")"
    fi

    # Assert no duplicate ports across services
    local ports_str="${GATEWAY_PORT} ${LOCALAI_PORT} ${TTS_PORT} ${STT_PORT}"
    if [ "${STREAMING_BRIDGE_ENABLED:-false}" = "true" ]; then
        ports_str="${ports_str} ${STREAMING_BRIDGE_PORT}"
    fi
    if [ "${ENVIRONMENT}" = "development" ]; then
        ports_str="${ports_str} ${FRONTEND_DEV_PORT}"
    fi

    local unique_count
    unique_count="$(echo "${ports_str}" | tr ' ' '\n' | sort -u | wc -l | tr -d ' ')"
    local total_count
    total_count="$(echo "${ports_str}" | tr ' ' '\n' | wc -l | tr -d ' ')"

    if [ "${unique_count}" -ne "${total_count}" ]; then
        log_err "Conflicting duplicate ports detected across services: ${ports_str}"
        exit 1
    fi
}

print_ports_table() {
    printf "\n"
    printf "${BOLD}${PURPLE}====================================================================${RESET}\n"
    printf "${BOLD}                 Active Service Ports Configuration                 ${RESET}\n"
    printf "${BOLD}${PURPLE}====================================================================${RESET}\n"
    printf "  %-22s | %-8s | %-12s | %s\n" "Service" "Port" "Access" "Target URL"
    printf "  -----------------------+----------+--------------+-----------------------------------\n"
    printf "  %-22s | %-8s | %-12s | http://%s:%s\n" "FastAPI Gateway (Public)" "${GATEWAY_PORT}" "Public" "${GATEWAY_HOST}" "${GATEWAY_PORT}"
    printf "  %-22s | %-8s | %-12s | http://127.0.0.1:%s\n" "LocalAI Engine" "${LOCALAI_PORT}" "Internal" "${LOCALAI_PORT}"
    printf "  %-22s | %-8s | %-12s | http://127.0.0.1:%s\n" "local-tts Engine" "${TTS_PORT}" "Internal" "${TTS_PORT}"
    printf "  %-22s | %-8s | %-12s | http://127.0.0.1:%s\n" "Sherpa-ONNX STT" "${STT_PORT}" "Internal" "${STT_PORT}"
    if [ "${STREAMING_BRIDGE_ENABLED:-false}" = "true" ]; then
        printf "  %-22s | %-8s | %-12s | http://127.0.0.1:%s\n" "Streaming Bridge" "${STREAMING_BRIDGE_PORT}" "Internal" "${STREAMING_BRIDGE_PORT}"
    fi
    if [ "${ENVIRONMENT}" = "development" ]; then
        printf "  %-22s | %-8s | %-12s | http://127.0.0.1:%s\n" "Vite Dev Server" "${FRONTEND_DEV_PORT}" "Internal" "${FRONTEND_DEV_PORT}"
    fi
    printf "${BOLD}${PURPLE}====================================================================${RESET}\n"
    printf "${DIM}To customize ports, edit .env and run './start.sh restart'.${RESET}\n\n"
}

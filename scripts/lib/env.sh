#!/usr/bin/env bash
# ==============================================================================
# scripts/lib/env.sh
# Non-destructive .env management, secret generation, and configuration loading
# Adheres to run-pod.md Section B3 (Exist OK, never overwrite user values)
# ==============================================================================

ENV_FILE="${ROOT_DIR}/.env"
EXAMPLE_FILE="${ROOT_DIR}/.env.example"
VERSIONS_FILE="${ROOT_DIR}/deploy/versions.env"

# Load pinned versions if present
if [ -f "${VERSIONS_FILE}" ]; then
    # shellcheck disable=SC1090
    . "${VERSIONS_FILE}"
fi

# Generate a high-entropy random string (hex or alphanumeric)
generate_secret() {
    local length="${1:-32}"
    if command -v python3 >/dev/null 2>&1; then
        python3 -c "import secrets; print(secrets.token_hex(${length} // 2))"
    elif command -v openssl >/dev/null 2>&1; then
        openssl rand -hex "$((length / 2))"
    else
        LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "${length}"
    fi
}

# Read a specific key from .env without sourcing (safe against arbitrary commands)
get_env_val() {
    local key="$1"
    local default_val="${2:-}"
    if [ ! -f "${ENV_FILE}" ]; then
        echo "${default_val}"
        return
    fi
    local val
    val=$(grep -E "^[[:space:]]*${key}=" "${ENV_FILE}" 2>/dev/null | tail -n 1 | cut -d'=' -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")
    if [ -n "${val}" ]; then
        echo "${val}"
    else
        echo "${default_val}"
    fi
}

# Set or replace a single key in .env (leaves all other lines and comments untouched)
set_env_val() {
    local key="$1"
    local val="$2"
    if [ ! -f "${ENV_FILE}" ]; then
        touch "${ENV_FILE}"
        chmod 600 "${ENV_FILE}"
    fi
    if grep -q -E "^[[:space:]]*${key}=" "${ENV_FILE}"; then
        # Replace existing line in-place
        python3 -c "
import sys
key, val, path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, 'r') as f:
    lines = f.readlines()
new_lines = []
for line in lines:
    if line.strip().startswith(key + '='):
        new_lines.append(f'{key}={val}\n')
    else:
        new_lines.append(line)
with open(path, 'w') as f:
    f.writelines(new_lines)
" "${key}" "${val}" "${ENV_FILE}"
    else
        echo "${key}=${val}" >> "${ENV_FILE}"
    fi
    chmod 600 "${ENV_FILE}"
}

# Append key with default and comment only if missing
append_env_if_missing() {
    local key="$1"
    local default_val="$2"
    local comment="$3"

    if [ ! -f "${ENV_FILE}" ]; then
        touch "${ENV_FILE}"
        chmod 600 "${ENV_FILE}"
    fi

    if ! grep -q -E "^[[:space:]]*${key}=" "${ENV_FILE}"; then
        echo "" >> "${ENV_FILE}"
        if [ -n "${comment}" ]; then
            echo "# ${comment}" >> "${ENV_FILE}"
        fi
        echo "${key}=${default_val}" >> "${ENV_FILE}"
    fi
}

# Synchronize and initialize .env safely
init_env() {
    local is_first_run="false"

    if [ ! -f "${ENV_FILE}" ]; then
        is_first_run="true"
        if [ -f "${EXAMPLE_FILE}" ]; then
            cp "${EXAMPLE_FILE}" "${ENV_FILE}"
        else
            touch "${ENV_FILE}"
        fi
        chmod 600 "${ENV_FILE}"
        log_info "Created new .env file with mode 600"
    fi

    # Environment
    local default_env="development"
    if [ "${IS_RUNPOD}" = "true" ]; then
        default_env="staging"
    fi
    append_env_if_missing "ENVIRONMENT" "${default_env}" "Environment: development, staging, or production"

    # Gateway Public Port and Host
    local default_host="127.0.0.1"
    if [ "${IS_RUNPOD}" = "true" ]; then
        default_host="0.0.0.0"
    fi
    append_env_if_missing "GATEWAY_PORT" "8000" "Public gateway port (must match Expose HTTP Ports on RunPod)"
    append_env_if_missing "GATEWAY_HOST" "${default_host}" "Gateway listen host (0.0.0.0 on RunPod, 127.0.0.1 on Mac)"

    # RunPod container runtime adjustments (must bind 0.0.0.0 so proxy can route to pod)
    if [ "${IS_RUNPOD}" = "true" ]; then
        set_env_val "GATEWAY_HOST" "0.0.0.0"
        local cur_env
        cur_env="$(get_env_val "ENVIRONMENT")"
        if [ -z "${cur_env}" ] || [ "${cur_env}" = "development" ]; then
            set_env_val "ENVIRONMENT" "staging"
        fi
    fi

    # Internal Service Ports
    append_env_if_missing "LOCALAI_PORT" "8080" "Internal LocalAI engine port (loopback only)"
    append_env_if_missing "TTS_PORT" "8880" "Internal Kokoro TTS engine port (loopback only)"
    append_env_if_missing "STT_PORT" "8088" "Internal Sherpa-ONNX streaming STT port (loopback only)"
    append_env_if_missing "STT_STREAM_NATIVE_ENABLED" "false" "Enable native Parakeet streaming STT service (:8089)"
    append_env_if_missing "STREAMING_BRIDGE_PORT" "8089" "Internal native streaming STT service port (loopback only)"
    append_env_if_missing "STREAMING_BRIDGE_ENABLED" "false" "Legacy alias for STT_STREAM_NATIVE_ENABLED"
    append_env_if_missing "FRONTEND_DEV_PORT" "5173" "Frontend development dev-server port"

    # Dynamic Port Handling
    append_env_if_missing "AUTO_PORTS" "true" "Allow automatic incrementing of internal ports if busy"
    append_env_if_missing "PORT_SEARCH_RANGE" "100" "Max port search offset when resolving conflicts"

    # Gateway Frontend Serving
    append_env_if_missing "SERVE_FRONTEND" "true" "Whether FastAPI gateway serves built frontend on root origin"
    append_env_if_missing "COOKIE_SECURE" "auto" "Secure cookie flag (auto enables in staging/production)"
    append_env_if_missing "TRUSTED_PROXY_IPS" "" "Comma-separated trusted proxy IPs or CIDR for proxy headers"
    append_env_if_missing "AUTO_RESTART" "true" "Auto-restart crashed engines with exponential backoff"

    # Generate secrets ONLY if missing, empty, or placeholder
    local cur_hash_secret
    cur_hash_secret="$(get_env_val "API_KEY_HASH_SECRET")"
    if [ -z "${cur_hash_secret}" ] \
        || [ "${cur_hash_secret}" = "dev-secret-key-change-me-in-production-use-high-entropy" ] \
        || [ "${cur_hash_secret}" = "generate-a-32-byte-hex-or-base64-random-secret" ] \
        || [[ "${cur_hash_secret}" == *"change-me"* ]]; then
        local new_secret
        new_secret="$(generate_secret 48)"
        set_env_val "API_KEY_HASH_SECRET" "${new_secret}"
    fi

    local cur_admin_key
    cur_admin_key="$(get_env_val "ADMIN_API_KEY")"
    if [ -z "${cur_admin_key}" ] || [[ "${cur_admin_key}" == *"your_api_key_here"* ]] || [[ "${cur_admin_key}" == *"change-me"* ]]; then
        local new_admin_key
        new_admin_key="va_live_$(generate_secret 24)"
        set_env_val "ADMIN_API_KEY" "${new_admin_key}"
        NEW_ADMIN_API_KEY_GENERATED="${new_admin_key}"
    fi

    # Ensure passwords are never stored in .env (stored in SQLite db only)
    if grep -q -E "^[[:space:]]*ADMIN_INITIAL_PASSWORD" "${ENV_FILE}" 2>/dev/null; then
        python3 -c "
import sys
path = sys.argv[1]
with open(path, 'r') as f:
    lines = f.readlines()
with open(path, 'w') as f:
    for line in lines:
        if not line.strip().startswith('ADMIN_INITIAL_PASSWORD'):
            f.write(line)
" "${ENV_FILE}" 2>/dev/null || true
    fi

    # Read active values into global variables
    ENVIRONMENT="$(get_env_val "ENVIRONMENT" "development")"
    GATEWAY_PORT="$(get_env_val "GATEWAY_PORT" "8000")"
    GATEWAY_HOST="$(get_env_val "GATEWAY_HOST" "${default_host}")"
    LOCALAI_PORT="$(get_env_val "LOCALAI_PORT" "8080")"
    TTS_PORT="$(get_env_val "TTS_PORT" "8880")"
    STT_PORT="$(get_env_val "STT_PORT" "8088")"
    STT_STREAM_NATIVE_ENABLED="$(get_env_val "STT_STREAM_NATIVE_ENABLED" "false")"
    STREAMING_BRIDGE_PORT="$(get_env_val "STREAMING_BRIDGE_PORT" "8089")"
    STREAMING_BRIDGE_ENABLED="$(get_env_val "STREAMING_BRIDGE_ENABLED" "false")"
    if [ "${STT_STREAM_NATIVE_ENABLED}" = "true" ]; then
        STREAMING_BRIDGE_ENABLED="true"
    fi
    FRONTEND_DEV_PORT="$(get_env_val "FRONTEND_DEV_PORT" "5173")"
    AUTO_PORTS="$(get_env_val "AUTO_PORTS" "true")"
    PORT_SEARCH_RANGE="$(get_env_val "PORT_SEARCH_RANGE" "100")"
    SERVE_FRONTEND="$(get_env_val "SERVE_FRONTEND" "true")"
    AUTO_RESTART="$(get_env_val "AUTO_RESTART" "true")"
    PUBLIC_BASE_URL="$(get_env_val "PUBLIC_BASE_URL" "")"
    if [ -z "${PUBLIC_BASE_URL}" ] && [ "${IS_RUNPOD}" = "true" ] && [ -n "${RUNPOD_POD_ID:-}" ]; then
        local runpod_url="https://${RUNPOD_POD_ID}-${GATEWAY_PORT}.proxy.runpod.net"
        set_env_val "PUBLIC_BASE_URL" "${runpod_url}"
        PUBLIC_BASE_URL="${runpod_url}"
    fi
    TRUSTED_PROXY_IPS="$(get_env_val "TRUSTED_PROXY_IPS" "")"

    chmod 600 "${ENV_FILE}"
}

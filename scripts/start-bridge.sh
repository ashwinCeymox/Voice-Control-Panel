#!/usr/bin/env bash
# ==============================================================================
# scripts/start-bridge.sh
# Standalone launcher for Native Streaming STT service (port 8089)
# Loopback-only parakeet.cpp C-API streaming service (:8089).
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

LOGS_DIR="${ROOT_DIR}/logs"
PID_DIR="${ROOT_DIR}/.pids"
RUN_DIR="${ROOT_DIR}/.run"
mkdir -p "${LOGS_DIR}" "${PID_DIR}" "${RUN_DIR}"

# Source common helpers if present
if [ -f "${ROOT_DIR}/scripts/lib/common.sh" ]; then
    # shellcheck disable=SC1091
    . "${ROOT_DIR}/scripts/lib/common.sh"
else
    log_info() { echo "[INFO] $1"; }
    log_ok()   { echo "[OK]   $1"; }
    log_warn() { echo "[WARN] $1"; }
    log_err()  { echo "[ERR]  $1"; }
fi

if [ -f "${ROOT_DIR}/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "${ROOT_DIR}/.env" 2>/dev/null || true
    set +a
fi

BRIDGE_PORT="${STREAMING_BRIDGE_PORT:-8089}"
BRIDGE_PID_FILE="${RUN_DIR}/streaming-bridge.pid"

# Check if already running
if [ -f "${BRIDGE_PID_FILE}" ]; then
    PID="$(cat "${BRIDGE_PID_FILE}" 2>/dev/null || true)"
    if [ -n "${PID}" ] && kill -0 "${PID}" 2>/dev/null; then
        log_ok "Streaming STT Bridge is already running (PID: ${PID}, port: ${BRIDGE_PORT})"
        exit 0
    fi
fi

# Ensure token is generated
if [ -z "${STREAMING_BRIDGE_INTERNAL_TOKEN:-}" ]; then
    if [ -f "${ROOT_DIR}/scripts/generate-bridge-token.sh" ]; then
        log_info "STREAMING_BRIDGE_INTERNAL_TOKEN not found; generating via scripts/generate-bridge-token.sh..."
        bash "${ROOT_DIR}/scripts/generate-bridge-token.sh" >/dev/null 2>&1 || true
        if [ -f "${ROOT_DIR}/.env" ]; then
            set -a
            # shellcheck disable=SC1091
            source "${ROOT_DIR}/.env" 2>/dev/null || true
            set +a
        fi
    fi
fi

if [ -z "${STREAMING_BRIDGE_INTERNAL_TOKEN:-}" ]; then
    log_err "CRITICAL: STREAMING_BRIDGE_INTERNAL_TOKEN is required to start Streaming Bridge."
    log_err "Run: bash scripts/generate-bridge-token.sh"
    exit 1
fi

PY_EXEC="${ROOT_DIR}/.venv/bin/python"
if [ ! -f "${PY_EXEC}" ]; then
    PY_EXEC="$(command -v python3)"
fi

log_info "Starting Native Streaming STT service on 127.0.0.1:${BRIDGE_PORT}..."
(
    cd "${ROOT_DIR}"
    STREAMING_BRIDGE_PORT="${BRIDGE_PORT}" \
    STT_STREAM_NATIVE_ENABLED="true" \
    STREAMING_BRIDGE_ENABLED="true" \
    nohup "${PY_EXEC}" -m services.streaming_bridge.server \
        >> "${LOGS_DIR}/streaming-bridge.log" 2>&1 &
    PID=$!
    echo "${PID}" > "${BRIDGE_PID_FILE}"
    echo "${PID}" > "${PID_DIR}/streaming-bridge.pid"
)

PID="$(cat "${BRIDGE_PID_FILE}")"

# Health poll
COUNT=0
while [ "${COUNT}" -lt 25 ]; do
    if ! kill -0 "${PID}" 2>/dev/null; then
        log_err "Streaming Bridge process exited unexpectedly (PID: ${PID})."
        tail -n 25 "${LOGS_DIR}/streaming-bridge.log" >&2 || true
        exit 1
    fi
    HEALTH_JSON="$(curl -s "http://127.0.0.1:${BRIDGE_PORT}/health" 2>/dev/null || true)"
    if [ -n "${HEALTH_JSON}" ] && [[ "${HEALTH_JSON}" == *"healthy"* ]]; then
        MAX_STREAMS="$(echo "${HEALTH_JSON}" | "${PY_EXEC}" -c "import sys, json; print(json.load(sys.stdin).get('max_streams', 5))" 2>/dev/null || echo "5")"
        log_ok "Streaming STT Bridge operational on port ${BRIDGE_PORT} (PID: ${PID}, max_streams: ${MAX_STREAMS})"
        exit 0
    fi
    sleep 1
    COUNT=$((COUNT + 1))
done

log_warn "Streaming Bridge health check timed out after 25s. Check logs at ${LOGS_DIR}/streaming-bridge.log"
exit 0

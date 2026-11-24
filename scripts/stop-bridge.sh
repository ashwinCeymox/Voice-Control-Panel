#!/usr/bin/env bash
# ==============================================================================
# scripts/stop-bridge.sh
# Graceful stopper for Streaming STT Bridge daemon (PID-only, no kill-by-port)
# Adheres to run-pod.md and port-stt.md Phase B6 specification.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

PID_DIR="${ROOT_DIR}/.pids"
RUN_DIR="${ROOT_DIR}/.run"

STOPPED=0

for dir in "${RUN_DIR}" "${PID_DIR}"; do
    PID_FILE="${dir}/streaming-bridge.pid"
    if [ -f "${PID_FILE}" ]; then
        PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
        if [ -n "${PID}" ] && kill -0 "${PID}" 2>/dev/null; then
            echo "[INFO] Stopping Streaming STT Bridge (PID: ${PID})..."
            kill -TERM "${PID}" 2>/dev/null || true
            COUNT=0
            while [ "${COUNT}" -lt 50 ]; do
                if ! kill -0 "${PID}" 2>/dev/null; then
                    break
                fi
                sleep 0.1
                COUNT=$((COUNT + 1))
            done
            if kill -0 "${PID}" 2>/dev/null; then
                echo "[WARN] Force-terminating Streaming STT Bridge (PID: ${PID})..."
                kill -KILL "${PID}" 2>/dev/null || true
            fi
            echo "[OK]   Streaming STT Bridge stopped cleanly."
            STOPPED=1
        fi
        rm -f "${PID_FILE}"
    fi
done

if [ ${STOPPED} -eq 0 ]; then
    echo "[OK]   Streaming STT Bridge was not running."
fi

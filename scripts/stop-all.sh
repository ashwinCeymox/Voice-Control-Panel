#!/usr/bin/env bash
# ==============================================================================
# Local Voice AI Agent - Stop All Services
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RESET="\033[0m"

PID_DIR="${ROOT_DIR}/.pids"

stop_pid_file() {
    local pid_file=$1
    local name=$2
    if [ -f "${pid_file}" ]; then
        local pid
        pid=$(cat "${pid_file}" 2>/dev/null || true)
        if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
            echo -e "${YELLOW}Stopping ${name} (PID ${pid})...${RESET}"
            kill "${pid}" 2>/dev/null || true
            for _ in {1..5}; do
                if ! kill -0 "${pid}" 2>/dev/null; then
                    break
                fi
                sleep 0.5
            done
            if kill -0 "${pid}" 2>/dev/null; then
                kill -9 "${pid}" 2>/dev/null || true
            fi
        fi
        rm -f "${pid_file}"
    fi
}

kill_port() {
    local port=$1
    local name=$2
    local pids
    pids=$(lsof -ti :"$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo -e "${YELLOW}Cleaning up port ${port} (${name}, PID: ${pids})...${RESET}"
        kill $pids 2>/dev/null || true
        sleep 0.5
        kill -9 $pids 2>/dev/null || true
    fi
}

echo -e "${BOLD}Stopping all Local Voice Agent stack services...${RESET}"

stop_pid_file "${PID_DIR}/frontend.pid" "Frontend"
stop_pid_file "${PID_DIR}/gateway.pid" "FastAPI Gateway"
stop_pid_file "${PID_DIR}/streaming-stt.pid" "Sherpa-ONNX STT"
stop_pid_file "${PID_DIR}/localai.pid" "LocalAI"
stop_pid_file "${PID_DIR}/local-tts.pid" "local-tts"
stop_pid_file "${PID_DIR}/local-tts-supervisor.pid" "local-tts supervisor"

rm -rf "${PID_DIR}"
echo -e "${GREEN}✓ All services stopped successfully.${RESET}"

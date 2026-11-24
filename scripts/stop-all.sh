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
RUN_DIR="${ROOT_DIR}/.run"

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
    # 1. Try fuser first (standard on Linux/RunPod)
    if command -v fuser >/dev/null 2>&1; then
        fuser -k -9 "${port}/tcp" 2>/dev/null || true
    fi
    # 2. Try lsof (macOS / Linux)
    local pids
    pids=$(lsof -ti :"$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo -e "${YELLOW}Cleaning up port ${port} (${name}, PID: ${pids})...${RESET}"
        kill -9 $pids 2>/dev/null || true
    fi
}

echo -e "${BOLD}Stopping all Local Voice Agent stack services...${RESET}"

# Stop by recorded PID files (.pids and .run)
for dir in "${PID_DIR}" "${RUN_DIR}"; do
    stop_pid_file "${dir}/frontend.pid" "Frontend"
    stop_pid_file "${dir}/gateway.pid" "FastAPI Gateway"
    stop_pid_file "${dir}/backend.pid" "Backend Gateway"
    stop_pid_file "${dir}/streaming-bridge.pid" "Streaming Bridge"
    stop_pid_file "${dir}/streaming-stt.pid" "Sherpa-ONNX STT"
    stop_pid_file "${dir}/localai.pid" "LocalAI"
    stop_pid_file "${dir}/local-tts.pid" "local-tts"
    stop_pid_file "${dir}/local-tts-supervisor.pid" "local-tts supervisor"
done

# Ensure ports are completely freed
kill_port "${GATEWAY_PORT:-8000}" "FastAPI Gateway"
kill_port "${FRONTEND_PORT:-5173}" "Frontend"
kill_port 8080 "LocalAI"
kill_port 8880 "local-tts"
kill_port 8088 "Sherpa-ONNX STT"

rm -f "${PID_DIR}"/*.pid "${RUN_DIR}"/*.pid 2>/dev/null || true
mkdir -p "${PID_DIR}" "${RUN_DIR}"
echo -e "${GREEN}✓ All services stopped successfully.${RESET}"

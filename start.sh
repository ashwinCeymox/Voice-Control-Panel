#!/usr/bin/env bash
# ==============================================================================
# Local Voice AI Agent - Plug & Play Full Stack Launcher
# Starts: LocalAI (8080), local-tts (8880), FastAPI Gateway (8000), Frontend (5173)
# ==============================================================================

set -e

# --- Terminal Styling ---
BOLD="\033[1m"
GREEN="\033[32m"
SKY="\033[36m"
YELLOW="\033[33m"
RED="\033[31m"
PURPLE="\033[35m"
DIM="\033[2m"
RESET="\033[0m"

# --- Resolve Root Paths Dynamically ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -d "$SCRIPT_DIR/LocalAI" ] && [ -d "$SCRIPT_DIR/local-tts" ]; then
    ARCH_DIR="$SCRIPT_DIR"
    VOICE_AGENT_DIR="$SCRIPT_DIR/local-voice-agent"
elif [ -d "$SCRIPT_DIR/../LocalAI" ] && [ -d "$SCRIPT_DIR/../local-tts" ]; then
    ARCH_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
    VOICE_AGENT_DIR="$SCRIPT_DIR"
else
    ARCH_DIR="/Users/hashhwin047icloud.com/Downloads/test-pro/arch"
    VOICE_AGENT_DIR="$ARCH_DIR/local-voice-agent"
fi

LOCALAI_DIR="$ARCH_DIR/LocalAI"
LOCALAI_BIN="$LOCALAI_DIR/local-ai"
LOCALAI_MODELS="$LOCALAI_DIR/models"

LOCAL_TTS_DIR="$ARCH_DIR/local-tts"
LOCAL_TTS_VENV="$LOCAL_TTS_DIR/.venv"
LOCAL_TTS_BIN="$LOCAL_TTS_VENV/bin/local-tts"

LOGS_DIR="$VOICE_AGENT_DIR/logs"
PID_DIR="$VOICE_AGENT_DIR/.pids"

mkdir -p "$LOGS_DIR" "$PID_DIR"

# --- Helper Functions ---
log_info()  { echo -e "${SKY}[INFO]${RESET} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${RESET}   $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${RESET} $1"; }
log_err()   { echo -e "${RED}[ERROR]${RESET} $1"; }

is_port_in_use() {
    local port=$1
    lsof -i :"$port" -sTCP:LISTEN >/dev/null 2>&1
}

kill_port_process() {
    local port=$1
    local name=$2
    if is_port_in_use "$port"; then
        local pids=$(lsof -ti :"$port" 2>/dev/null || true)
        if [ -n "$pids" ]; then
            log_warn "Stopping existing process on port $port ($name, PID: $pids)..."
            kill $pids 2>/dev/null || true
            sleep 1
            kill -9 $pids 2>/dev/null || true
        fi
    fi
}

# --- Action: Stop All Services ---
stop_all() {
    echo -e "${BOLD}${PURPLE}==> Stopping all Local Voice Agent stack services...${RESET}"
    kill_port_process 5173 "Vite Frontend"
    kill_port_process 8000 "FastAPI Gateway"
    kill_port_process 8880 "local-tts MLX"
    kill_port_process 8080 "LocalAI"
    rm -rf "$PID_DIR"
    log_ok "All services stopped successfully."
}

# --- Action: Status Check ---
status_all() {
    echo -e "${BOLD}${PURPLE}====================================================================${RESET}"
    echo -e "${BOLD}               Local Voice Agent Stack - Service Status             ${RESET}"
    echo -e "${BOLD}${PURPLE}====================================================================${RESET}"
    
    local services=(
        "5173:Vite Frontend:http://127.0.0.1:5173"
        "8000:FastAPI Gateway:http://127.0.0.1:8000/v1/health"
        "8880:local-tts Engine:http://127.0.0.1:8880/v1/models"
        "8080:LocalAI (Metal):http://127.0.0.1:8080/v1/models"
    )

    for s in "${services[@]}"; do
        IFS=":" read -r port name url <<< "$s"
        if is_port_in_use "$port"; then
            local pid=$(lsof -ti :"$port" 2>/dev/null | head -n 1)
            echo -e "  ${GREEN}● ONLINE ${RESET} [Port $port] $name (PID: $pid) -> $url"
        else
            echo -e "  ${RED}○ OFFLINE${RESET} [Port $port] $name -> $url"
        fi
    done
    echo -e "${BOLD}${PURPLE}====================================================================${RESET}"
}

# Process argument commands
case "$1" in
    stop)
        stop_all
        exit 0
        ;;
    status)
        status_all
        exit 0
        ;;
    restart)
        stop_all
        sleep 1
        ;;
esac

# --- Trap Interrupts for Graceful Teardown ---
cleanup() {
    echo ""
    echo -e "${BOLD}${YELLOW}Received shutdown signal. Stopping all services...${RESET}"
    stop_all
    exit 0
}
trap cleanup SIGINT SIGTERM

# --- Step 1: Check Dependencies & Prerequisites ---
echo -e "${BOLD}${SKY}====================================================================${RESET}"
echo -e "${BOLD}       🎙️  Local Voice Agent - Plug & Play Full Stack Launcher       ${RESET}"
echo -e "${BOLD}${SKY}====================================================================${RESET}"

log_info "Verifying execution environment and Apple Silicon Metal acceleration..."

# 1.1 LocalAI Binary
if [ ! -f "$LOCALAI_BIN" ]; then
    log_err "LocalAI binary not found at: $LOCALAI_BIN"
    exit 1
fi
chmod +x "$LOCALAI_BIN"

# 1.2 local-tts Virtualenv
if [ ! -f "$LOCAL_TTS_BIN" ]; then
    log_err "local-tts binary not found at: $LOCAL_TTS_BIN"
    exit 1
fi

# 1.3 Backend Virtualenv
if [ ! -f "$VOICE_AGENT_DIR/.venv/bin/uvicorn" ]; then
    log_info "Setting up Python virtual environment for backend..."
    python3 -m venv "$VOICE_AGENT_DIR/.venv"
    "$VOICE_AGENT_DIR/.venv/bin/pip" install --upgrade pip
    if [ -f "$VOICE_AGENT_DIR/requirements.txt" ]; then
        "$VOICE_AGENT_DIR/.venv/bin/pip" install -r "$VOICE_AGENT_DIR/requirements.txt"
    fi
fi

# 1.4 Frontend Dependencies
if [ ! -d "$VOICE_AGENT_DIR/frontend/node_modules" ]; then
    log_info "Installing frontend node_modules..."
    npm --prefix "$VOICE_AGENT_DIR/frontend" install
fi

log_ok "All prerequisite binaries and environments verified."

# --- Step 2: Clear Stale Processes ---
log_info "Checking port availability..."
kill_port_process 8080 "LocalAI"
kill_port_process 8880 "local-tts"
kill_port_process 8000 "FastAPI Backend"
kill_port_process 5173 "Vite Frontend"
sleep 1

# --- Step 3: Launch LocalAI (Port 8080) ---
log_info "Starting LocalAI (Metal GPU Speech-to-Text & LLM Engine)..."
"$LOCALAI_BIN" run \
    --address "127.0.0.1:8080" \
    --models-path "$LOCALAI_MODELS" \
    > "$LOGS_DIR/localai.log" 2>&1 &
LOCALAI_PID=$!
echo $LOCALAI_PID > "$PID_DIR/localai.pid"

# Wait for LocalAI
echo -ne "${DIM}Waiting for LocalAI to initialize on port 8080...${RESET}"
for i in {1..30}; do
    if curl -s "http://127.0.0.1:8080/v1/models" >/dev/null 2>&1; then
        echo -e " ${GREEN}Ready!${RESET}"
        break
    fi
    sleep 1
    echo -ne "."
done
log_ok "LocalAI running on http://127.0.0.1:8080 (PID: $LOCALAI_PID)"

# --- Step 4: Launch local-tts (Port 8880) ---
log_info "Starting local-tts (Apple MLX Metal 24kHz Speech Synthesis Engine)..."
"$LOCAL_TTS_BIN" server \
    --host 127.0.0.1 \
    --port 8880 \
    --disable-qwen \
    > "$LOGS_DIR/local-tts.log" 2>&1 &
LOCAL_TTS_PID=$!
echo $LOCAL_TTS_PID > "$PID_DIR/local-tts.pid"

# Wait for local-tts
echo -ne "${DIM}Waiting for local-tts to initialize on port 8880...${RESET}"
for i in {1..30}; do
    if curl -s "http://127.0.0.1:8880/v1/models" >/dev/null 2>&1; then
        echo -e " ${GREEN}Ready!${RESET}"
        break
    fi
    sleep 1
    echo -ne "."
done
log_ok "local-tts running on http://127.0.0.1:8880 (PID: $LOCAL_TTS_PID)"

# --- Step 5: Launch FastAPI Gateway Backend (Port 8000) ---
log_info "Starting FastAPI Orchestrator Gateway (Port 8000)..."
(
    cd "$VOICE_AGENT_DIR"
    exec "$VOICE_AGENT_DIR/.venv/bin/uvicorn" app.main:app \
        --host 127.0.0.1 \
        --port 8000 \
        --reload \
        > "$LOGS_DIR/backend.log" 2>&1
) &
BACKEND_PID=$!
echo $BACKEND_PID > "$PID_DIR/backend.pid"

# Wait for Backend
echo -ne "${DIM}Waiting for FastAPI Gateway to initialize on port 8000...${RESET}"
for i in {1..20}; do
    if curl -s "http://127.0.0.1:8000/v1/health" >/dev/null 2>&1; then
        echo -e " ${GREEN}Ready!${RESET}"
        break
    fi
    sleep 1
    echo -ne "."
done
log_ok "FastAPI Gateway running on http://127.0.0.1:8000 (PID: $BACKEND_PID)"

# --- Step 6: Launch Vite Frontend (Port 5173) ---
log_info "Starting Vite Control Panel Frontend (Port 5173)..."
(
    cd "$VOICE_AGENT_DIR/frontend"
    exec npm run dev -- --host 127.0.0.1 --port 5173 \
        > "$LOGS_DIR/frontend.log" 2>&1
) &
FRONTEND_PID=$!
echo $FRONTEND_PID > "$PID_DIR/frontend.pid"

# Wait for Frontend
echo -ne "${DIM}Waiting for Vite Frontend on port 5173...${RESET}"
for i in {1..20}; do
    if is_port_in_use 5173; then
        echo -e " ${GREEN}Ready!${RESET}"
        break
    fi
    sleep 1
    echo -ne "."
done
log_ok "Vite Frontend running on http://127.0.0.1:5173 (PID: $FRONTEND_PID)"

# --- Final Live Dashboard Banner ---
echo ""
echo -e "${BOLD}${GREEN}====================================================================${RESET}"
echo -e "${BOLD}${GREEN}     🎉 ALL SERVICES OPERATIONAL - PLUG & PLAY VOICE AGENT READY     ${RESET}"
echo -e "${BOLD}${GREEN}====================================================================${RESET}"
echo -e "  ${BOLD}🎙️  Voice Assistant UI:${RESET}       ${SKY}http://127.0.0.1:5173${RESET}"
echo -e "  ${BOLD}⚙️   FastAPI Gateway Docs:${RESET}     ${SKY}http://127.0.0.1:8000/docs${RESET}"
echo -e "  ${BOLD}🧠  LocalAI Metal Engine:${RESET}     ${SKY}http://127.0.0.1:8080/v1/models${RESET}"
echo -e "  ${BOLD}🔊  local-tts MLX Engine:${RESET}     ${SKY}http://127.0.0.1:8880/v1/models${RESET}"
echo -e "${BOLD}${GREEN}====================================================================${RESET}"
echo -e "  ${DIM}• Logs Directory:${RESET}             $LOGS_DIR/*.log"
echo -e "  ${DIM}• Live Log Stream:${RESET}            tail -f $LOGS_DIR/backend.log"
echo -e "  ${DIM}• Stop Stack Command:${RESET}         $0 stop"
echo -e "  ${BOLD}${YELLOW}Press [Ctrl+C] at any time to halt all services cleanly.${RESET}"
echo -e "${BOLD}${GREEN}====================================================================${RESET}"
echo ""

# Keep process alive and monitor child processes
while true; do
    # Verify child processes are alive
    if ! kill -0 "$LOCALAI_PID" 2>/dev/null; then
        log_err "LocalAI process died unexpectedly! Check $LOGS_DIR/localai.log"
        cleanup
    fi
    if ! kill -0 "$LOCAL_TTS_PID" 2>/dev/null; then
        log_err "local-tts process died unexpectedly! Check $LOGS_DIR/local-tts.log"
        cleanup
    fi
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        log_err "FastAPI backend process died unexpectedly! Check $LOGS_DIR/backend.log"
        cleanup
    fi
    if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
        log_err "Frontend process died unexpectedly! Check $LOGS_DIR/frontend.log"
        cleanup
    fi
    sleep 2
done

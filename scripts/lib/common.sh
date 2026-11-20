#!/usr/bin/env bash
# ==============================================================================
# scripts/lib/common.sh
# Shared logging, paths, and platform detection for One-Command Installer
# Compatible with POSIX sh and Bash 3.2+ (macOS & Linux)
# ==============================================================================

# Terminal colors (if stdout is a tty)
if [ -t 1 ]; then
    BOLD="\033[1m"
    DIM="\033[2m"
    RED="\033[31m"
    GREEN="\033[32m"
    YELLOW="\033[33m"
    SKY="\033[36m"
    PURPLE="\033[35m"
    RESET="\033[0m"
else
    BOLD=""
    DIM=""
    RED=""
    GREEN=""
    YELLOW=""
    SKY=""
    PURPLE=""
    RESET=""
fi

log_info()  { printf "${SKY}[INFO]${RESET} %s\n" "$*" >&2; }
log_ok()    { printf "${GREEN}[OK]${RESET}   %s\n" "$*" >&2; }
log_warn()  { printf "${YELLOW}[WARN]${RESET} %s\n" "$*" >&2; }
log_err()   { printf "${RED}[ERROR]${RESET} %s\n" "$*" >&2; }

# Resolve absolute root directory
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${LIB_DIR}/../.." && pwd)"

RUN_DIR="${ROOT_DIR}/.run"
LOGS_DIR="${ROOT_DIR}/logs"
TOOLS_DIR="${ROOT_DIR}/.tools"
STAMPS_DIR="${ROOT_DIR}/.install-stamps"
ARCH_DIR="${ARCH_DIR:-${ROOT_DIR}/arch}"

mkdir -p "${RUN_DIR}" "${LOGS_DIR}" "${TOOLS_DIR}" "${STAMPS_DIR}" "${ARCH_DIR}"

if [ -d "${TOOLS_DIR}/node/bin" ]; then
    export PATH="${TOOLS_DIR}/node/bin:${PATH}"
fi

if [ -d "${HOME}/.local/bin" ]; then
    export PATH="${HOME}/.local/bin:${PATH}"
fi

if [ -d "/usr/local/bin" ]; then
    export PATH="/usr/local/bin:${PATH}"
fi

# Detect Platform
detect_platform() {
    OS_TYPE="$(uname -s | tr '[:upper:]' '[:lower:]')"
    ARCH_TYPE="$(uname -m)"

    case "${OS_TYPE}" in
        darwin)
            PLATFORM="macos"
            ;;
        linux)
            PLATFORM="linux"
            ;;
        *)
            PLATFORM="unknown"
            ;;
    esac

    case "${ARCH_TYPE}" in
        x86_64|amd64)
            ARCH="x86_64"
            ;;
        arm64|aarch64)
            ARCH="arm64"
            ;;
        *)
            ARCH="${ARCH_TYPE}"
            ;;
    esac

    if [ -n "${RUNPOD_POD_ID:-}" ]; then
        IS_RUNPOD="true"
    else
        IS_RUNPOD="false"
    fi

    if [ "$(id -u)" -eq 0 ]; then
        IS_ROOT="true"
    else
        IS_ROOT="false"
    fi
}

detect_platform

# On Linux, optimize file descriptor limits and kernel inotify watchers for model runtimes
if [ "${PLATFORM}" = "linux" ]; then
    ulimit -n 65536 2>/dev/null || ulimit -n 4096 2>/dev/null || true
    if [ "${IS_ROOT}" = "true" ] || [ "$(id -u)" -eq 0 ]; then
        sysctl -w fs.inotify.max_user_watches=524288 >/dev/null 2>&1 || true
        sysctl -w fs.inotify.max_user_instances=8192 >/dev/null 2>&1 || true
        sysctl -w fs.inotify.max_queued_events=16384 >/dev/null 2>&1 || true
    fi
fi

# Verify if a target file is an actual native executable binary (ELF on Linux, Mach-O on macOS)
is_valid_binary() {
    local target="$1"
    [ -f "${target}" ] || return 1
    [ -s "${target}" ] || return 1
    [ -x "${target}" ] || return 1

    # Python-based foolproof binary check
    local py_bin=""
    if [ -x "${ROOT_DIR}/.venv/bin/python" ]; then
        py_bin="${ROOT_DIR}/.venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        py_bin="python3"
    fi

    if [ -n "${py_bin}" ]; then
        "${py_bin}" -c "
import sys
try:
    with open(sys.argv[1], 'rb') as f:
        magic = f.read(4)
        if magic == b'\x7fELF':
            sys.exit(0)
        if magic in (b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe'):
            sys.exit(0)
except Exception:
    pass
sys.exit(1)
" "${target}" 2>/dev/null && return 0
        return 1
    fi

    # Fallback if Python is unavailable
    local magic
    magic="$(head -c 4 "${target}" 2>/dev/null || true)"
    if [ "${magic}" = "$(printf '\x7fELF')" ]; then
        return 0
    fi

    return 1
}

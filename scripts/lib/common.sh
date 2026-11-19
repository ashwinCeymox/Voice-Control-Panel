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

mkdir -p "${RUN_DIR}" "${LOGS_DIR}" "${TOOLS_DIR}" "${STAMPS_DIR}"

if [ -d "${TOOLS_DIR}/node/bin" ]; then
    export PATH="${TOOLS_DIR}/node/bin:${PATH}"
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

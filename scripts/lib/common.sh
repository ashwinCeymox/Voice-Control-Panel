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

# Verify if a target file is an actual native executable binary and not an HTML/text error page
is_valid_binary() {
    local target="$1"
    [ -f "${target}" ] || return 1
    [ -s "${target}" ] || return 1

    # Check if first lines contain HTML, 404, or "Not Found" error strings
    if head -n 5 "${target}" 2>/dev/null | grep -qiE "Not Found|<html|<!doctype|404: Not Found"; then
        return 1
    fi

    # Check via `file` utility if available
    if command -v file >/dev/null 2>&1; then
        local desc
        desc="$(file -b "${target}" 2>/dev/null || true)"
        if echo "${desc}" | grep -qiE "ELF|Mach-O|executable"; then
            return 0
        fi
        return 1
    fi

    # Direct magic byte check (Linux ELF starts with \x7fELF)
    local magic
    magic="$(head -c 4 "${target}" 2>/dev/null || true)"
    if [ "${magic}" = "$(printf '\x7fELF')" ]; then
        return 0
    fi

    [ -x "${target}" ]
}

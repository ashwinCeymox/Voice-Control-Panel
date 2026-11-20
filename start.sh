#!/usr/bin/env bash
# ==============================================================================
# start.sh
# End-to-End One-Command Installer and Unified Stack Launcher
# Works identically on RunPod Cloud (Linux/CUDA) and Local Development (macOS/Metal)
# Adheres to run-pod.md (POSIX & Bash 3.2+ compatible)
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export ROOT_DIR="${SCRIPT_DIR}"

# Source modular libraries
# shellcheck disable=SC1091
. "${ROOT_DIR}/scripts/lib/common.sh"
# shellcheck disable=SC1091
. "${ROOT_DIR}/scripts/lib/env.sh"
# shellcheck disable=SC1091
. "${ROOT_DIR}/scripts/lib/ports.sh"
# shellcheck disable=SC1091
. "${ROOT_DIR}/scripts/lib/runpod.sh"
# shellcheck disable=SC1091
. "${ROOT_DIR}/scripts/lib/install.sh"
# shellcheck disable=SC1091
. "${ROOT_DIR}/scripts/lib/services.sh"

# Parse CLI flags
DRY_RUN="false"
SKIP_INSTALL="false"
SKIP_MODELS="false"
FOREGROUND="false"
CUSTOM_ADMIN_PASSWORD=""

COMMAND="up"

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)
            DRY_RUN="true"
            shift
            ;;
        --no-install)
            SKIP_INSTALL="true"
            shift
            ;;
        --skip-models)
            SKIP_MODELS="true"
            shift
            ;;
        --foreground|-f)
            FOREGROUND="true"
            shift
            ;;
        reset-admin)
            COMMAND="$1"
            shift
            if [ $# -gt 0 ] && [[ "$1" != --* ]]; then
                CUSTOM_ADMIN_PASSWORD="$1"
                shift
            fi
            ;;
        up|start|stop|restart|status|install|ports|url|doctor|logs)
            COMMAND="$1"
            shift
            ;;
        *)
            log_err "Unknown argument: $1"
            echo "Usage: ./start.sh [up|start|stop|restart|status|install|ports|url|doctor|logs|reset-admin] [--dry-run] [--no-install] [--foreground]"
            exit 1
            ;;
    esac
done

# Initialize configuration and ports
init_env

if [ "${DRY_RUN}" = "true" ]; then
    log_info "=== [DRY RUN] Plan for command: ${COMMAND} ==="
    log_info "Platform: ${PLATFORM} (${ARCH}), RunPod: ${IS_RUNPOD}"
    log_info "Configured Ports: Gateway=${GATEWAY_PORT}, LocalAI=${LOCALAI_PORT}, TTS=${TTS_PORT}, STT=${STT_PORT}"
    log_info "No filesystem or process modifications executed in dry-run mode."
    exit 0
fi

case "${COMMAND}" in
    stop)
        stop_services
        exit 0
        ;;
    status)
        status_services
        exit 0
        ;;
    ports)
        resolve_all_ports
        print_ports_table
        exit 0
        ;;
    url)
        compute_urls
        printf "\n  ${BOLD}🌐 Public Web Panel:${RESET} %s/voicecontrolpanel\n" "${PUBLIC_URL}"
        printf "  ${BOLD}🔌 API Base URL:${RESET}     %s/v1\n" "${PUBLIC_URL}"
        printf "\n"
        exit 0
        ;;
    doctor)
        log_info "=== Running Doctor Diagnostic Audit ==="
        run_preflight
        resolve_all_ports
        print_ports_table
        status_services
        log_ok "Doctor audit completed with zero fatal blocking issues."
        exit 0
        ;;
    reset-admin)
        log_info "=== Break-Glass Admin Password Reset ==="
        NEW_PASS="${CUSTOM_ADMIN_PASSWORD:-}"
        if [ -z "${NEW_PASS}" ]; then
            NEW_PASS="$(generate_secret 16)"
        fi
        if [ -f "${ROOT_DIR}/.venv/bin/python" ]; then
            "${ROOT_DIR}/.venv/bin/python" -m app.cli reset-admin --password "${NEW_PASS}"
        else
            python3 -m app.cli reset-admin --password "${NEW_PASS}"
        fi
        admin_email="$(get_env_val "ADMIN_EMAIL" "admin@voicepanel.io")"
        printf "\n  ${BOLD}${YELLOW}🔑 NEW ADMIN CREDENTIALS GENERATED:${RESET}\n"
        printf "     • Email:    ${BOLD}%s${RESET}\n" "${admin_email}"
        printf "     • Password: ${BOLD}%s${RESET}\n" "${NEW_PASS}"
        printf "     • Storage:  ${BOLD}Stored exclusively in SQLite database (never in .env)${RESET}\n"
        printf "     ${DIM}(Password must be changed at next login)${RESET}\n\n"
        exit 0
        ;;
    logs)
        local_log="${LOGS_DIR}/backend.log"
        if [ ! -f "${local_log}" ]; then
            touch "${local_log}"
        fi
        tail -f "${LOGS_DIR}"/*.log
        exit 0
        ;;
    restart)
        stop_services
        sleep 1
        init_env
        if [ "${SKIP_INSTALL}" != "true" ]; then
            install_localai_engine
        fi
        resolve_all_ports

        # Seed initial super admin in SQLite DB if table is empty (never written to .env)
        if [ -f "${ROOT_DIR}/.venv/bin/python" ]; then
            seed_res="$("${ROOT_DIR}/.venv/bin/python" -m app.cli seed-admin 2>/dev/null || true)"
            if [[ "${seed_res}" == *"ADMIN_SEED_GENERATED:"* ]]; then
                NEW_ADMIN_INITIAL_PASSWORD_GENERATED="${seed_res#*ADMIN_SEED_GENERATED:}"
                NEW_ADMIN_INITIAL_PASSWORD_GENERATED="${NEW_ADMIN_INITIAL_PASSWORD_GENERATED%%$'\n'*}"
                export NEW_ADMIN_INITIAL_PASSWORD_GENERATED
            fi
        fi

        start_services
        print_banner
        exit 0
        ;;
    install)
        run_installer
        exit 0
        ;;
    up|start)
        if [ "${SKIP_INSTALL}" != "true" ]; then
            run_installer
        fi
        resolve_all_ports

        # Seed initial super admin in SQLite DB if table is empty (never written to .env)
        if [ -f "${ROOT_DIR}/.venv/bin/python" ]; then
            seed_res="$("${ROOT_DIR}/.venv/bin/python" -m app.cli seed-admin 2>/dev/null || true)"
            if [[ "${seed_res}" == *"ADMIN_SEED_GENERATED:"* ]]; then
                NEW_ADMIN_INITIAL_PASSWORD_GENERATED="${seed_res#*ADMIN_SEED_GENERATED:}"
                NEW_ADMIN_INITIAL_PASSWORD_GENERATED="${NEW_ADMIN_INITIAL_PASSWORD_GENERATED%%$'\n'*}"
                export NEW_ADMIN_INITIAL_PASSWORD_GENERATED
            fi
        fi

        start_services
        print_banner

        # Optional public reachability ping check
        compute_urls
        check_public_reachability "${PUBLIC_URL}" 20 || true

        if [ "${FOREGROUND}" = "true" ]; then
            log_info "Running in foreground mode. Press Ctrl+C to halt stack."
            trap 'stop_services; exit 0' SIGINT SIGTERM
            tail -f "${LOGS_DIR}/backend.log"
        fi
        exit 0
        ;;
esac

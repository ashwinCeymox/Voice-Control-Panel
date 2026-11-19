#!/usr/bin/env bash
# ==============================================================================
# tests/shell/test_installer_lib.sh
# Automated regression and isolation tests for One-Command Installer libraries
# ==============================================================================

set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${TEST_DIR}/../.." && pwd)"

# Setup isolated sandbox
TMP_WORKSPACE="$(mktemp -d "/tmp/installer_test_XXXXXX")"
trap 'rm -rf "${TMP_WORKSPACE}"' EXIT

export ROOT_DIR="${TMP_WORKSPACE}"
export RUN_DIR="${TMP_WORKSPACE}/.run"
export LOGS_DIR="${TMP_WORKSPACE}/logs"
export TOOLS_DIR="${TMP_WORKSPACE}/.tools"
export STAMPS_DIR="${TMP_WORKSPACE}/.install-stamps"

mkdir -p "${RUN_DIR}" "${LOGS_DIR}" "${TOOLS_DIR}" "${STAMPS_DIR}"

# Source libraries from actual repository
# shellcheck disable=SC1091
. "${REPO_ROOT}/scripts/lib/common.sh"
# shellcheck disable=SC1091
. "${REPO_ROOT}/scripts/lib/env.sh"
# shellcheck disable=SC1091
. "${REPO_ROOT}/scripts/lib/ports.sh"
# shellcheck disable=SC1091
. "${REPO_ROOT}/scripts/lib/runpod.sh"

echo "=== Running Installer Shell Library Tests ==="

# ------------------------------------------------------------------------------
# Test 1: .env creation from scratch (mode 600, secrets generated)
# ------------------------------------------------------------------------------
echo "• Test 1: .env initialization when missing..."
ENV_FILE="${TMP_WORKSPACE}/.env"
EXAMPLE_FILE="${TMP_WORKSPACE}/.env.example"
touch "${EXAMPLE_FILE}"

IS_RUNPOD="false"
init_env

if [ ! -f "${ENV_FILE}" ]; then
    echo "FAIL: .env was not created"
    exit 1
fi

mode=$(python3 -c "import os; print(oct(os.stat('${ENV_FILE}').st_mode)[-3:])")
if [ "${mode}" != "600" ]; then
    echo "FAIL: .env mode is ${mode}, expected 600"
    exit 1
fi

admin_key="$(get_env_val "ADMIN_API_KEY")"
if [[ "${admin_key}" != va_live_* ]]; then
    echo "FAIL: ADMIN_API_KEY was not properly generated (${admin_key})"
    exit 1
fi

if grep -q "ADMIN_INITIAL_PASSWORD" "${ENV_FILE}"; then
    echo "FAIL: ADMIN_INITIAL_PASSWORD found in .env (must be database-only)"
    exit 1
fi
echo "  ✓ .env initialized with mode 600, random keys, and passwords excluded from .env"

# ------------------------------------------------------------------------------
# Test 2: .env preserves existing user values (Non-destructive)
# ------------------------------------------------------------------------------
echo "• Test 2: .env preserves user customization..."
set_env_val "GATEWAY_PORT" "9123"
set_env_val "CUSTOM_USER_KEY" "my-secret-value"
old_admin_key="${admin_key}"

init_env

gw_port="$(get_env_val "GATEWAY_PORT")"
user_val="$(get_env_val "CUSTOM_USER_KEY")"
cur_admin_key="$(get_env_val "ADMIN_API_KEY")"

if [ "${gw_port}" != "9123" ]; then
    echo "FAIL: GATEWAY_PORT was overwritten (${gw_port})"
    exit 1
fi
if [ "${user_val}" != "my-secret-value" ]; then
    echo "FAIL: CUSTOM_USER_KEY was lost"
    exit 1
fi
if [ "${cur_admin_key}" != "${old_admin_key}" ]; then
    echo "FAIL: ADMIN_API_KEY was regenerated when already set"
    exit 1
fi
echo "  ✓ Existing keys and customized ports preserved without modification"

# ------------------------------------------------------------------------------
# Test 3: Port conflict resolution & auto-migration
# ------------------------------------------------------------------------------
echo "• Test 3: Port conflict detection and auto-migration..."
# Start a dummy listener on a high port
TEST_PORT=28190
python3 -c "
import socket, time
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', ${TEST_PORT}))
s.listen(1)
time.sleep(3)
" &
DUMMY_PID=$!
sleep 0.5

AUTO_PORTS="true"
PORT_SEARCH_RANGE=10
new_port="$(resolve_service_port "test-service" "TEST_PORT" "${TEST_PORT}" "false")"

kill "${DUMMY_PID}" 2>/dev/null || true

if [ "${new_port}" -le "${TEST_PORT}" ]; then
    echo "FAIL: Port was not migrated to free port (got ${new_port})"
    exit 1
fi

written_port="$(get_env_val "TEST_PORT")"
if [ "${written_port}" != "${new_port}" ]; then
    echo "FAIL: New port was not written to .env (${written_port})"
    exit 1
fi
echo "  ✓ Busy internal port auto-migrated from ${TEST_PORT} to ${new_port} and written to .env"

# ------------------------------------------------------------------------------
# Test 4: RunPod public port is NOT moved silently
# ------------------------------------------------------------------------------
echo "• Test 4: RunPod public gateway port protection..."
# Start another dummy listener
TEST_PUBLIC_PORT=28192
python3 -c "
import socket, time
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', ${TEST_PUBLIC_PORT}))
s.listen(1)
time.sleep(3)
" &
DUMMY_PID=$!
sleep 0.5

IS_RUNPOD="true"
RUNPOD_POD_ID="testpod123"

set +e
err_output="$(resolve_service_port "gateway" "GATEWAY_PORT" "${TEST_PUBLIC_PORT}" "true" 2>&1)"
exit_code=$?
set -e

kill "${DUMMY_PID}" 2>/dev/null || true

if [ "${exit_code}" -eq 0 ]; then
    echo "FAIL: Public port on RunPod was erroneously allowed to move!"
    exit 1
fi
if [[ "${err_output}" != *"Public Gateway Port"* ]]; then
    echo "FAIL: Missing descriptive error message on RunPod port clash: ${err_output}"
    exit 1
fi
echo "  ✓ Public gateway port on RunPod halted with clear error when occupied"

# ------------------------------------------------------------------------------
# Test 5: Dynamic RunPod URL derivation
# ------------------------------------------------------------------------------
echo "• Test 5: RunPod proxy URL derivation..."
IS_RUNPOD="true"
RUNPOD_POD_ID="abc999"
GATEWAY_PORT="8000"
PUBLIC_BASE_URL=""

compute_urls

if [ "${PUBLIC_URL}" != "https://abc999-8000.proxy.runpod.net" ]; then
    echo "FAIL: Incorrect RunPod URL (${PUBLIC_URL})"
    exit 1
fi

IS_RUNPOD="false"
compute_urls
if [ "${PUBLIC_URL}" != "http://127.0.0.1:8000" ]; then
    echo "FAIL: Incorrect local URL (${PUBLIC_URL})"
    exit 1
fi
echo "  ✓ URLs computed dynamically for both RunPod and local environments"

echo ""
echo "=========================================================="
echo "    🎉 ALL INSTALLER LIBRARY TESTS PASSED (5/5)           "
echo "=========================================================="

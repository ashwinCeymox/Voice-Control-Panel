#!/usr/bin/env bash
# scripts/generate-bridge-token.sh
# Generates a persistent high-entropy internal token for the Streaming Bridge once.
# Written strictly to .env (mode 600). Never printed, never logged, never committed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

ENV_FILE="${ROOT_DIR}/.env"
ENV_EXAMPLE="${ROOT_DIR}/.env.example"

if [[ ! -f "${ENV_FILE}" ]]; then
    touch "${ENV_FILE}"
fi

# Ensure .env is mode 600
chmod 600 "${ENV_FILE}"

if grep -q "^STREAMING_BRIDGE_INTERNAL_TOKEN=" "${ENV_FILE}" && [[ -n "$(grep "^STREAMING_BRIDGE_INTERNAL_TOKEN=" "${ENV_FILE}" | cut -d'=' -f2-)" ]]; then
    echo "STREAMING_BRIDGE_INTERNAL_TOKEN already configured in .env (mode 600)."
    exit 0
fi

# Generate 64-char high-entropy token via python secrets
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi

TOKEN="$("${PYTHON_BIN}" -c "import secrets; print(secrets.token_hex(32))")"

# Remove any empty entry
if grep -q "^STREAMING_BRIDGE_INTERNAL_TOKEN=" "${ENV_FILE}"; then
    grep -v "^STREAMING_BRIDGE_INTERNAL_TOKEN=" "${ENV_FILE}" > "${ENV_FILE}.tmp"
    mv "${ENV_FILE}.tmp" "${ENV_FILE}"
fi

# Append token to .env
echo "STREAMING_BRIDGE_INTERNAL_TOKEN=${TOKEN}" >> "${ENV_FILE}"
chmod 600 "${ENV_FILE}"
unset TOKEN

echo "STREAMING_BRIDGE_INTERNAL_TOKEN generated once and written to .env (mode 600)."

# Ensure entry in .env.example (without token value)
if [[ -f "${ENV_EXAMPLE}" ]] && ! grep -q "^STREAMING_BRIDGE_INTERNAL_TOKEN=" "${ENV_EXAMPLE}"; then
    echo "STREAMING_BRIDGE_INTERNAL_TOKEN=" >> "${ENV_EXAMPLE}"
fi

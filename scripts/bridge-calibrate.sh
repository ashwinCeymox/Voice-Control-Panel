#!/usr/bin/env bash
# scripts/bridge-calibrate.sh
# Offline host-gated calibration for Streaming STT Bridge.
# Run by the system owner/admin to evaluate host viability.
# NEVER run automatically at startup.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${ROOT_DIR}"

PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi

exec "${PYTHON_BIN}" -m services.streaming_bridge.calibrate "$@"

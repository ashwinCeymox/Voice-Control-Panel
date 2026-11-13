#!/usr/bin/env bash
# ==============================================================================
# Download and install pre-trained streaming STT models for sherpa-onnx
# Default model: sherpa-onnx-streaming-zipformer-en-20M-2023-02-17 (Apache-2.0)
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TARGET_DIR="${ROOT_DIR}/models/streaming-stt"

MODEL_NAME="sherpa-onnx-streaming-zipformer-en-20M-2023-02-17"
TARBALL="${MODEL_NAME}.tar.bz2"
URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/${TARBALL}"

echo "=== Downloading Streaming STT Model ==="
echo "Model: ${MODEL_NAME}"
echo "Target directory: ${TARGET_DIR}"

mkdir -p "${TARGET_DIR}"
cd "${TARGET_DIR}"

if [ -d "${MODEL_NAME}" ]; then
    echo "Model directory already exists at ${TARGET_DIR}/${MODEL_NAME}. Skipping download."
    exit 0
fi

if [ ! -f "${TARBALL}" ]; then
    echo "Downloading ${URL}..."
    curl -SL --progress-bar -O "${URL}"
fi

echo "Extracting ${TARBALL}..."
tar xvf "${TARBALL}"
rm -f "${TARBALL}"

echo "=== Download and verification complete ==="
ls -lh "${TARGET_DIR}/${MODEL_NAME}"

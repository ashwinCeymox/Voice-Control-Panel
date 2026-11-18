# Version Pinning and Checksum Manifest

This document records the exact pinned versions, source repositories, download URLs, and verified checksums for all third-party engines, runtime libraries, and AI model weights utilized by the Local Voice Agent platform across target operating environments.

---

## 1. Engine and Runtime Software

### 1.1 LocalAI (Speech-to-Text & LLM Engine)
- **Upstream Project**: [mudler/LocalAI](https://github.com/mudler/LocalAI)
- **Pinned Version**: `v2.26.0`
- **Default Port**: `8080` (strictly bound to `127.0.0.1`)
- **Platform Binaries**:
  - **macOS Apple Silicon (Metal)**:
    - URL: `https://github.com/mudler/LocalAI/releases/download/v2.26.0/local-ai-Darwin-arm64`
    - SHA256: `6e5e8c1ea99994fbc266fdbd420f8623bcaecf048d0cf3b1e3bbbfd965e6c7eb`
  - **Linux x86_64 (NVIDIA CUDA 12)**:
    - URL: `https://github.com/mudler/LocalAI/releases/download/v2.26.0/local-ai-cublas-cuda-12-Linux-x86_64`
    - SHA256: `4184c8a29a0f074d001275d8d85fbfd0f39fb4a45a64eebe536c478a2f43a6cb`

### 1.2 local-tts (Speech Synthesis / Kokoro MLX)
- **Upstream Project**: [saghul/local-tts](https://github.com/saghul/local-tts)
- **Pinned Release / Commit**: `v0.1.0` (commit `62adbc6b9319e7145e69bfd68cb5a3d077478051`)
- **Default Port**: `8880` (strictly bound to `127.0.0.1`)
- **Platform Availability**:
  - **macOS Apple Silicon**: Supported via Apple MLX (`mlx`, `kokoro`). Runs inside a dedicated virtual environment.
  - **Linux (Hetzner / RunPod)**: **Not Supported**. Kokoro on Apple MLX is macOS-exclusive. The gateway returns HTTP 503 cleanly (`TTS unavailable on Linux`).

### 1.3 Sherpa-ONNX (Real-Time Streaming STT)
- **Upstream Project**: [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
- **Pinned Python Package**: `sherpa-onnx==1.10.35`
- **Default Port**: `8088` (strictly bound to `127.0.0.1`)
- **Platform Availability**: Cross-platform (macOS Apple Silicon, Linux x86_64 CPU/CUDA).

### 1.4 Frontend VAD (Client-Side Voice Activity Detection)
- **Upstream Project**: [ricky0123/vad](https://github.com/ricky0123/vad) & [microsoft/onnxruntime](https://github.com/microsoft/onnxruntime)
- **Pinned NPM Packages**:
  - `@ricky0123/vad-web`: `^0.0.31`
  - `onnxruntime-web`: `^1.31.1`
- **Asset Provisioning**: Automatically copied from `frontend/node_modules/` to `frontend/public/vad/` via `frontend/scripts/copy-vad-assets.js` on `npm install` (postinstall) and `npm run build` (prebuild). Never committed to git.

---

## 2. Pinned Model Weights

### 2.1 Streaming STT: Zipformer 20M (Default)
- **Model Name**: `sherpa-onnx-streaming-zipformer-en-20M-2023-02-17`
- **License**: Apache-2.0
- **URL**: `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17.tar.bz2`
- **SHA256**: `144a04d57a9cf675a363717df3f482d72110c7e0cceea7e20ecb2ce4837599cb`
- **Disk Path**: `models/streaming-stt/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17/`

### 2.2 Batch STT: Whisper Base (English)
- **Model Name**: `whisper-base.en` (ggml-base.en.bin)
- **License**: MIT
- **URL**: `https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin`
- **SHA256**: `60ed5bc3dd14eea856493d334349b405782ddcaf00e087db02d94a6328103749`
- **Disk Path**: `models/whisper/ggml-base.en.bin`

### 2.3 LLM: Llama-3.2-1B-Instruct (Q4_K_M)
- **Model Name**: `Llama-3.2-1B-Instruct-Q4_K_M.gguf`
- **License**: Llama 3.2 Community License Agreement
- **URL**: `https://huggingface.co/unsloth/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_K_M.gguf`
- **SHA256**: `d06283b584d4fa716b9cb45d2b7bb5ff48cb66b9be2c24efd6e06161c5df60e2`
- **Disk Path**: `models/llm/Llama-3.2-1B-Instruct-Q4_K_M.gguf`

### 2.4 Client-Side VAD: Silero VAD v5
- **Model Name**: `silero_vad_v5.onnx`
- **License**: MIT
- **Packaged In**: `@ricky0123/vad-web` v0.0.31
- **Disk Path**: `frontend/public/vad/silero_vad_v5.onnx` (transient build artifact)

---

## 3. Platform Detection Matrix

| Operating System | Architecture | Accelerator | LocalAI Binary Target | local-tts Status | Streaming STT Provider |
|---|---|---|---|---|---|
| macOS (Darwin) | arm64 (Apple Silicon) | Apple Metal | `local-ai-Darwin-arm64` | Supported (`saghul/local-tts` via MLX) | `cpu` / `coreml` |
| Linux | x86_64 | NVIDIA CUDA 12 | `local-ai-cublas-cuda-12-Linux-x86_64` | Unsupported (503 Clean Gateway Fallback) | `cuda` / `cpu` |
| Linux | x86_64 | CPU Only | `local-ai-cpu-Linux-x86_64` | Unsupported (503 Clean Gateway Fallback) | `cpu` |

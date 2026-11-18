# 7. The Engines (3 minutes)

> **TL;DR:** Three native AI engines running without Docker. LocalAI (:8080) handles LLM generation and batch Whisper STT. local-tts (:8880) synthesizes speech with Kokoro at 24 kHz. Sherpa-ONNX (:8088) streams realtime partial transcription at 16 kHz. All engines bind to loopback 127.0.0.1.

---

## 7.1 LocalAI (LLM & Batch STT)

- **Port & Host:** `127.0.0.1:8080`
- **Underlying Engines:** `llama.cpp` (LLM inference) and `whisper.cpp` (batch audio transcription).
- **Primary Roles:**
  - `POST /v1/chat/completions`: Text conversation turns with system prompts and streaming token deltas.
  - `POST /v1/audio/transcriptions`: High-accuracy batch transcription of uploaded audio files.
  - `POST /models/apply`: Model gallery installation.
- **Security & Authorization:**
  - Protected with internal Bearer authentication (`LOCALAI_API_KEY`).
  - The gateway acts as a strict security barrier: dangerous admin endpoints (`/models/apply`, `/models/delete/*`, `/backend/*`, `/system`, `/metrics`) are **never routed to external clients**.
- **Cold Start Behavior:** First request after startup loads weights into memory and may take 3–8 seconds. The gateway lifespan automatically pre-warms the default model at startup to prevent client 502/504 errors.

---

## 7.2 local-tts (Kokoro Speech Synthesis)

- **Port & Host:** `127.0.0.1:8880`
- **Underlying Engine:** Kokoro TTS (82M parameter model, Apache-2.0 license).
  - Apple Silicon macOS: Native Metal acceleration via Apple MLX (`local-tts server`).
  - Linux Bare Metal: PyTorch CUDA backend.
- **Audio Output:** **24,000 Hz (24 kHz), 16-bit PCM mono** (`pcm_s16le`).
- **Endpoints:**
  - `GET /v1/voices`: List available voices (e.g. `af_bella`, `af_sarah`, `am_adam`, `am_michael`).
  - `POST /v1/text-to-speech/{voice_id}/stream`: Real-time streaming audio chunk synthesis.
  - `GET /v1/models`: List loaded TTS voices and engine variants.
- **Latency:** Synthesizes first audio chunk in ~80–140 ms, enabling near-instant voice playback.

---

## 7.3 Streaming STT (Sherpa-ONNX Zipformer)

- **Port & Host:** `127.0.0.1:8088`
- **Service Script:** [`services/streaming_stt/server.py`](../../services/streaming_stt/server.py)
- **Model:** `sherpa-onnx-streaming-zipformer-en-20M-2023-02-17` (Apache-2.0).
- **Audio Input:** **16,000 Hz (16 kHz), 16-bit PCM mono** (`pcm_s16le`).
- **Protocol:**
  - `WS /ws`: Accepts continuous binary audio chunks (default chunk size: 560 ms = 17,920 bytes).
  - Emits real-time JSON partial transcripts:
    ```json
    {
      "segment": 0,
      "text": "hello how can i help you today",
      "is_final": false
    }
    ```
- **Precision & Provider:** Configurable via environment variables:
  - `STREAMING_STT_PRECISION`: `fp32` (default high accuracy), `int8`, `f16`.
  - `STREAMING_STT_PROVIDER`: `cpu` (default), `cuda` (NVIDIA Linux), `coreml` (Apple).
- **Fallback Logic:** If the streaming server is temporarily unreachable or throws an error, the gateway automatically falls back to LocalAI batch Whisper STT without terminating the conversation.

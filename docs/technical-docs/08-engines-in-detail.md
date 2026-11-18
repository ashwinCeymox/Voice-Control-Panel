# 8. Engines in Detail

## 8.1 LocalAI (LLM & Batch STT)

- **Port & Host:** `127.0.0.1:8080`
- **Underlying Core:** Go server orchestrating native `llama.cpp` (LLM inference) and `whisper.cpp` (audio transcription) backends.
- **Hardware Acceleration:**
  - Apple Silicon macOS: Apple Metal Performance Shaders.
  - Linux Bare Metal: NVIDIA CUDA 12+ with Tensor Core acceleration.
- **Primary Routes Used by the Gateway:**
  - `POST /v1/chat/completions`: Text generation supporting system prompts and token streaming.
  - `POST /v1/audio/transcriptions`: Batch audio file transcription via Whisper models.
  - `POST /models/apply`: Model gallery installation jobs.
  - `GET /models/jobs/{id}`: Polling model installation progress.
  - `DELETE /models/delete/{name}`: Model deletion.
  - `GET /v1/models`: List loaded LocalAI models.
  - `GET /readyz`: Engine readiness check.
- **Security Boundaries:**
  - LocalAI includes dangerous administrative endpoints (`/models/apply`, `/models/delete/*`, `/backend/*`, `/system`, `/metrics`). The gateway acts as a strict security barrier, ensuring these internal endpoints are **never reachable by external clients**.
  - All calls from the gateway pass `Authorization: Bearer ${LOCALAI_API_KEY}` headers.
- **Cold-Start Pre-warming:** Loading large model weights into VRAM can take 3–8 seconds upon cold start. The gateway lifespan (`app/main.py`) dispatches an automatic pre-warming query to the default model on startup to ensure zero initial latency for users.

---

## 8.2 local-tts (Kokoro Speech Synthesis)

- **Port & Host:** `127.0.0.1:8880`
- **Underlying Model:** Kokoro TTS (82 million parameters, Apache-2.0 license).
  - Apple Silicon macOS: Native Metal acceleration via Apple MLX (`local-tts server`).
  - Linux Bare Metal: PyTorch CUDA backend.
- **Audio Output:** **24,000 Hz (24 kHz), 16-bit PCM mono** (`pcm_s16le`).
- **Endpoints:**
  - `GET /v1/voices`: List available voices (e.g. `af_bella`, `af_sarah`, `am_adam`, `am_michael`).
  - `POST /v1/text-to-speech/{voice_id}/stream`: Real-time streaming audio chunk synthesis.
  - `GET /v1/models`: List loaded TTS voices and engine variants.
- **Latency & Performance:** Kokoro delivers first synthesized audio chunks in **~80–140 ms**, enabling near-instant vocal responses as LLM tokens are produced.
- **Voice Metadata Fallback:** Kokoro voice IDs encode gender in the prefix (`af_` for American Female, `am_` for American Male). For third-party voices that lack explicit metadata, the gateway maintains an "Unknown" fallback with administrative metadata overrides (`PUT /v1/voices/{id}/meta`).

---

## 8.3 Streaming STT (Sherpa-ONNX Zipformer)

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
- **Graceful Fallback Logic:** If the streaming STT server is unreachable or throws a network error, the gateway automatically falls back to LocalAI batch Whisper STT and notifies the client via `stt.fallback` event, ensuring conversations are never dropped.

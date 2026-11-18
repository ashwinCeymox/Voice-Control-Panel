# 11. Configuration Reference

## 11.1 Complete Environment Variable Reference

All platform configuration parameters are defined and validated using Pydantic Settings in [`app/core/config.py`](../../app/core/config.py). Variables are read from the environment or `.env` file **[Confirmed]**.

| Variable | Type | Default | Production Value | Description |
|---|---|---|---|---|
| `ENVIRONMENT` | string | `development` | `production` | Deployment mode. When set to `production`, triggers startup failsafe checks. |
| `ADMIN_API_KEY` | string | `dev-voice-key-12345` | `openssl rand -hex 32` | Master administrative API key. Production startup **fails immediately** if default is left in place. |
| `API_KEY_HASH_SECRET` | string | `dev-secret-key-12345678901234567890123456789012` | `openssl rand -hex 32` | HMAC secret for hashing API keys. **Must be securely backed up**; losing it invalidates all keys. |
| `LOCALAI_API_KEY` | string | `""` | `openssl rand -hex 32` | Shared secret sent by gateway to LocalAI in `Authorization: Bearer` headers. |
| `LOCALAI_BASE_URL` | string | `http://127.0.0.1:8080` | `http://127.0.0.1:8080` | Base HTTP endpoint for the LocalAI inference engine. |
| `LOCAL_TTS_HTTP_BASE_URL` | string | `http://127.0.0.1:8880` | `http://127.0.0.1:8880` | Base HTTP endpoint for the local-tts (Kokoro) synthesis engine. |
| `STREAMING_STT_URL` | string | `http://127.0.0.1:8088` | `http://127.0.0.1:8088` | Base HTTP endpoint for the Sherpa-ONNX streaming STT service. |
| `STREAMING_STT_WS_URL` | string | `ws://127.0.0.1:8088/ws` | `ws://127.0.0.1:8088/ws` | WebSocket endpoint for Sherpa-ONNX streaming STT sessions. |
| `STREAMING_STT_PROVIDER` | string | `cpu` | `cuda` (Linux) / `cpu` | Hardware acceleration backend for streaming STT (`cpu`, `cuda`, `coreml`). |
| `STREAMING_STT_PRECISION`| string | `fp32` | `fp32` / `int8` | Model weights precision (`fp32`, `int8`, `f16`). |
| `STREAMING_STT_CHUNK_MS` | integer| `560` | `560` | Audio frame duration in milliseconds for partial transcript emission. |
| `CORS_ALLOWED_ORIGINS` | list | `["http://localhost:5173", "http://127.0.0.1:5173"]` | `["https://yourdomain.com"]` | Whitelisted browser origins allowed to make cross-origin requests. |
| `ENABLE_DOCS` | boolean| `False` | `False` | Enables Swagger UI (`/docs`) and ReDoc (`/redoc`). Must remain `False` in production. |
| `LOG_TRANSCRIPTS` | boolean| `False` | `False` | Enables logging of full speech transcripts in stdout. Keep `False` to prevent sensitive data leaks. |
| `MAX_AUDIO_UPLOAD_MB` | integer| `20` | `20` | Maximum allowable payload size in megabytes for batch audio upload endpoints. |
| `MAX_ACTIVE_STT_STREAMS` | integer| `5` | `5` (tune per GPU) | Maximum global concurrent streaming STT sessions. Excess requests receive HTTP 503. |
| `MAX_ACTIVE_VOICE_TURNS` | integer| `1` | `1` (or 2 per GPU) | Global concurrency semaphore for simultaneous end-to-end voice turns. |
| `CUDA_VISIBLE_DEVICES` | string | unset | `0` | Specifies which physical NVIDIA GPU to expose to the inference processes. |
| `DB_PATH` | string | `voice_platform.db` | `/opt/voice-agent/data/voice_platform.db` | Absolute path to the primary SQLite database file. |
| `LOCALAI_LLM_MODEL` | string | `qwen2.5-0.5b-instruct` | Preferred LLM model | Default model identifier used for background pre-warming upon gateway startup. |
| `LOCALAI_STT_MODEL` | string | `whisper-base-en` | Preferred STT model | Default Whisper model identifier used for background pre-warming upon startup. |

---

## 11.2 Production Configuration Template (`.env`)

```bash
# ==============================================================================
# Voice AI Platform: Production Environment Configuration
# Target: /opt/voice-agent/.env (File mode: 600, Owner: voiceagent:voiceagent)
# ==============================================================================

# Core Environment
ENVIRONMENT=production
ENABLE_DOCS=false
LOG_TRANSCRIPTS=false

# Security & Master Secrets (Generated via 'openssl rand -hex 32')
ADMIN_API_KEY=0a1b2c3d4e5f67890123456789abcdef0123456789abcdef0123456789abcdef
API_KEY_HASH_SECRET=fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210
LOCALAI_API_KEY=5566778899aabbccddeeff00112233445566778899aabbccddeeff0011223344

# Loopback Engine Endpoints
LOCALAI_BASE_URL=http://127.0.0.1:8080
LOCAL_TTS_HTTP_BASE_URL=http://127.0.0.1:8880
STREAMING_STT_URL=http://127.0.0.1:8088
STREAMING_STT_WS_URL=ws://127.0.0.1:8088/ws

# Engine Parameters & Acceleration
STREAMING_STT_PROVIDER=cuda
STREAMING_STT_PRECISION=fp32
STREAMING_STT_CHUNK_MS=560
CUDA_VISIBLE_DEVICES=0

# Concurrency & Payload Limits
MAX_AUDIO_UPLOAD_MB=20
MAX_ACTIVE_STT_STREAMS=5
MAX_ACTIVE_VOICE_TURNS=1

# CORS Whitelist
CORS_ALLOWED_ORIGINS=["https://xyz.sdjfj.app"]

# Database Path
DB_PATH=/opt/voice-agent/data/voice_platform.db
```

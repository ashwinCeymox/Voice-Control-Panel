# 10. Settings Cheat Sheet (2 minutes)

> **TL;DR:** All gateway settings are defined in `app/core/config.py` using Pydantic Settings. Override via environment variables or `.env`. Never commit `.env` to version control.

---

## 10.1 Complete Configuration Reference

| Environment Variable | Type | Default Value | Purpose & Production Rule |
|---|---|---|---|
| `ENVIRONMENT` | `string` | `"development"` | Set to `"production"` on Hetzner server. Enables startup security checks. |
| `ADMIN_API_KEY` | `string` | `"dev-voice-key-12345"` | Root administrator key. **MUST be changed** to random 256-bit string in production. |
| `API_KEY_HASH_SECRET` | `string` | `"dev-secret-key-change-in-production"` | Secret seed for HMAC-SHA256 key hashing. **Back this up safely.** |
| `LOCALAI_API_KEY` | `string` | `""` | Internal authentication token shared between gateway and LocalAI. |
| `LOCALAI_BASE_URL` | `string` | `"http://127.0.0.1:8080"` | Loopback URL for LocalAI service. |
| `LOCAL_TTS_HTTP_BASE_URL` | `string` | `"http://127.0.0.1:8880"` | Loopback URL for local-tts service. |
| `STREAMING_STT_URL` | `string` | `"http://127.0.0.1:8088"` | Loopback HTTP URL for Sherpa-ONNX service. |
| `STREAMING_STT_WS_URL` | `string` | `"ws://127.0.0.1:8088/ws"` | Loopback WebSocket URL for streaming STT recognizer. |
| `STREAMING_STT_PROVIDER` | `string` | `"cpu"` | Sherpa inference backend: `"cpu"`, `"cuda"`, or `"coreml"`. |
| `STREAMING_STT_PRECISION` | `string` | `"fp32"` | Model weight precision: `"fp32"` (best accuracy), `"int8"`, `"f16"`. |
| `STREAMING_STT_CHUNK_MS` | `integer` | `560` | Duration of each streaming audio window in milliseconds. |
| `STREAMING_STT_DEFAULT_MODEL` | `string` | `"sherpa-onnx-streaming-zipformer-en-20M"` | Active streaming STT model identifier. |
| `ENABLE_DOCS` | `boolean` | `False` | Disables `/docs`, `/redoc`, and `/openapi.json` to prevent schema enumeration. |
| `LOG_TRANSCRIPTS` | `boolean` | `False` | Privacy guard: prevents logging spoken speech text to logs/journal. |
| `CORS_ALLOWED_ORIGINS` | `JSON list` | `["http://localhost:5173", ...]` | Permitted web origins. Set to `["https://api.yourdomain.com"]` in production. |
| `MAX_ACTIVE_STT_STREAMS` | `integer` | `5` | Maximum simultaneous streaming STT WebSocket connections. |
| `MAX_ACTIVE_VOICE_TURNS` | `integer` | `5` | Maximum simultaneous end-to-end voice turns processed. |
| `MAX_AUDIO_UPLOAD_MB` | `integer` | `20` | File upload ceiling for batch Whisper STT. |
| `CUDA_VISIBLE_DEVICES` | `string` | `unset` | Explicitly bind services to target NVIDIA GPU IDs (e.g. `"0"`). |

---

## 10.2 Production `.env` Template

```ini
# Production Environment Configuration
ENVIRONMENT=production

# 256-bit Random Secrets (Generated with: openssl rand -hex 32)
ADMIN_API_KEY=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
API_KEY_HASH_SECRET=fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210
LOCALAI_API_KEY=abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789

# Localhost Engine Endpoints
LOCALAI_BASE_URL=http://127.0.0.1:8080
LOCAL_TTS_HTTP_BASE_URL=http://127.0.0.1:8880
STREAMING_STT_URL=http://127.0.0.1:8088
STREAMING_STT_WS_URL=ws://127.0.0.1:8088/ws

# Sherpa-ONNX Streaming Engine Settings
STREAMING_STT_PROVIDER=cuda
STREAMING_STT_PRECISION=fp32
STREAMING_STT_CHUNK_MS=560

# Hardening Flags
ENABLE_DOCS=false
LOG_TRANSCRIPTS=false
CORS_ALLOWED_ORIGINS=["https://api.yourdomain.com"]
```

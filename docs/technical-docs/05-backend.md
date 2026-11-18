# 5. Backend Architecture & API

## 5.1 Layered Design Architecture

The gateway adheres strictly to a four-tier architecture separating presentation, domain rules, engine communication, and error translation:

```text
HTTP / WS Request
       │
       ▼
 [ 1. Route Layer ] ──► Validates inputs against Pydantic schemas (app/schemas/)
       │
       ▼
 [ 2. Service Layer ] ──► Executes business rules, auth scopes, & turn pipelines (app/services/)
       │
       ▼
 [ 3. Provider Layer ] ──► Adapts internal schemas to engine protocols (app/providers/)
       │
       ▼
 [ 4. Error Layer ] ──► Translates engine failures to standardized AppErrors (app/core/errors.py)
       │
       ▼
Standardized JSON Response with x-request-id
```

---

## 5.2 Authentication and Authorization Model

- **Supported Request Headers:**
  - `Authorization: Bearer <key>`
  - `xi-api-key: <key>` (ElevenLabs compatibility)
  - `?ticket=<single_use_ticket>` (for WebSocket handshakes)
- **Cryptographic Key Storage:** Keys are hashed using **HMAC-SHA256** seeded by `settings.API_KEY_HASH_SECRET`. Plaintext keys are never stored on disk or logged to stdout.
- **Constant-Time Verification:** Validated via `hmac.compare_digest` to prevent timing attacks.
- **Dual-Role Model:**
  - `admin`: Superuser role granting full control over keys, profiles, model management, and system settings.
  - `caller`: Restricted role requiring explicit scopes to access inference endpoints.
- **Scope Hierarchy:**
  - `llm`: Allows access to `POST /v1/chat/completions`.
  - `stt`: Allows access to `POST /v1/audio/transcriptions`.
  - `tts`: Allows access to `POST /v1/text-to-speech/*`, `GET /v1/voices`, `POST /v1/voices/*/preview`.
  - `voice`: Allows access to `POST /v1/voice/turn`.
  - `voice_stream`: Allows access to `WS /v1/voice/stream`.
  - `stt_stream`: Allows access to `WS /v1/audio/transcriptions/stream`.
- **Single-Use WebSocket Tickets:** To prevent exposing API keys in browser WebSocket URLs or proxy query logs, the client calls `POST /v1/auth/ticket` to obtain an ephemeral 60-second ticket stored in memory.

---

## 5.3 Complete API Endpoint Reference (42 Endpoints)

### 5.3.1 Public & Health Check Endpoints

| Method | Path | Required Auth | Response / Behavior |
|---|---|---|---|
| `GET` | `/health/live` | None (Public) | `{"status": "ok"}` (Liveness probe) |
| `GET` | `/health/ready` | None (Public) | Returns 200 when LocalAI, local-tts, and streaming STT respond |
| `GET` | `/health/detail` | None (Public) | Component-by-component latency and operational status |
| `GET` | `/` | None (Public) | Welcome banner with API version and docs route |
| `GET` | `/metrics` | None (Public) | Prometheus exposition format. **Blocked at Caddy reverse proxy** |

### 5.3.2 Inference Endpoints (Caller Scopes)

| Method | Path | Required Scope | Description |
|---|---|---|---|
| `POST` | `/v1/chat/completions` | `llm` | OpenAI-compatible chat completion with streaming token deltas |
| `POST` | `/v1/audio/transcriptions` | `stt` | Batch audio file transcription via Whisper |
| `POST` | `/v1/text-to-speech/{voice_id}/stream` | `tts` | ElevenLabs-compatible streaming PCM audio chunk synthesis |
| `GET` | `/v1/voices` | `tts` | List installed Kokoro and local-tts voice profiles |
| `POST` | `/v1/voices/{voice_id}/preview` | `tts` | Synthesize short instant voice preview sample |
| `GET` | `/v1/tts/models` | `tts` | List available TTS synthesis backends |
| `POST` | `/v1/voice/turn` | `voice` **[Confirmed]** | Multi-modal turn: audio input $\rightarrow$ transcript + LLM text + audio reply |

### 5.3.3 Discovery, Telemetry & Authentication

| Method | Path | Required Role / Scope | Description |
|---|---|---|---|
| `GET` | `/v1/models` | Any Valid Key | Unified multi-engine catalog of available models |
| `GET` | `/v1/models/installed` | Any Valid Key | Currently loaded and installed models across providers |
| `GET` | `/v1/models/install/{job_id}` | Any Valid Key | Polling endpoint for model gallery download jobs |
| `GET` | `/v1/models/classifications` | Any Valid Key | List all task classification tags (LLM, STT, TTS) |
| `GET` | `/v1/models/classifications/stats` | Any Valid Key | Distribution of installed models by task |
| `GET` | `/v1/gallery/models` | Any Valid Key | Browse installable model gallery catalog |
| `GET` | `/v1/gallery/models/{model_id}` | Any Valid Key | Detailed metadata for a specific gallery model |
| `GET` | `/v1/gallery/updates` | Any Valid Key | Check for remote catalog updates |
| `GET` | `/v1/system/resources` | Any Valid Key | Host hardware telemetry (CPU, RAM, GPU VRAM usage) |
| `POST` | `/v1/auth/ticket` | Any Valid Key | Mint single-use WebSocket ticket (60-second TTL) |

### 5.3.4 Model Management (Admin Only)

| Method | Path | Required Role | Description |
|---|---|---|---|
| `POST` | `/v1/models/install` | `admin` | Initiate asynchronous download and installation from gallery |
| `DELETE` | `/v1/models/{model_id}` | `admin` **[Confirmed]** | Delete model files and remove from manifest |
| `PATCH` | `/v1/models/{model_id}/classification` | `admin` **[Confirmed]** | Override model task classification |
| `POST` | `/v1/gallery/refresh` | `admin` | Force refresh of gallery model definitions |
| `PUT` | `/v1/voices/{voice_id}/meta` | `admin` | Update custom voice metadata, description, and traits |

### 5.3.5 Administration & Platform Management (Admin Only)

| Method | Path | Required Role | Description |
|---|---|---|---|
| `GET` | `/v1/agents` | `admin` | List saved voice agent configurations |
| `POST` | `/v1/agents` | `admin` | Create a new custom voice agent configuration |
| `GET` | `/v1/agents/{agent_id}` | `admin` | Retrieve specific voice agent by ID |
| `PUT` | `/v1/agents/{agent_id}` | `admin` | Update voice agent prompt, voice, and VAD parameters |
| `DELETE` | `/v1/agents/{agent_id}` | `admin` | Delete voice agent and cascade associated sessions |
| `POST` | `/v1/agents/{agent_id}/active` | `admin` | Set active default agent persona |
| `GET` | `/v1/admin/keys` | `admin` | List all issued API keys, quotas, and expiration |
| `POST` | `/v1/admin/keys` | `admin` | Generate new scoped API key |
| `GET` | `/v1/admin/keys/{key_id}` | `admin` | Inspect specific key details and usage telemetry |
| `PATCH` | `/v1/admin/keys/{key_id}` | `admin` | Modify key rate limits, quotas, scopes, or status |
| `DELETE` | `/v1/admin/keys/{key_id}` | `admin` | Revoke API key immediately |
| `POST` | `/v1/admin/keys/{key_id}/rotate` | `admin` | Rotate key with configurable dual-active grace window |
| `GET` | `/v1/admin/profiles` | `admin` | List application billing profiles |
| `POST` | `/v1/admin/profiles` | `admin` | Create application billing profile |
| `GET` | `/v1/admin/profiles/{profile_id}` | `admin` | Get specific billing profile details |
| `PUT` | `/v1/admin/profiles/{profile_id}` | `admin` | Update billing profile metadata |
| `DELETE` | `/v1/admin/profiles/{profile_id}` | `admin` | Remove application billing profile |
| `GET` | `/v1/admin/settings` | `admin` | Read global concurrency and rate limiting defaults |
| `PUT` | `/v1/admin/settings` | `admin` | Update global concurrency and rate limiting defaults |
| `GET` | `/v1/admin/settings/stt` | `admin` | Read streaming STT kill switch and buffer parameters |
| `PUT` | `/v1/admin/settings/stt` | `admin` | Live update streaming STT kill switch, precision, chunk size |
| `GET` | `/v1/admin/usage` | `admin` | Query granular token and audio usage events |
| `GET` | `/v1/admin/usage/summary` | `admin` | Retrieve rolled-up hourly usage statistics |
| `GET` | `/v1/admin/usage/export` | `admin` | Export usage log as CSV/JSON |

### 5.3.6 Real-Time WebSocket Protocols

| Protocol | Path | Required Scope | Description |
|---|---|---|---|
| `WS` | `/v1/voice/stream` | `voice_stream` | Bi-directional interactive voice assistant |
| `WS` | `/v1/audio/transcriptions/stream` | `stt_stream` | Low-latency live speech transcription stream |

---

## 5.4 WebSocket Event Protocol (`app/schemas/events.py`)

All real-time messages adhere strictly to Pydantic schemas:

### Client $\rightarrow$ Server Event Types

```json
// 1. Initialize session with custom agent and audio settings
{"type": "session.start", "session_id": "sess-123", "agent_id": "default", "stt_kind": "streaming"}

// 2. User starts speaking (emitted by Silero VAD)
{"type": "vad.speech_start"}

// 3. Binary audio frame: 16 kHz, 16-bit PCM little-endian (raw binary websocket frame)

// 4. Utterance complete (emitted when user stops speaking)
{"type": "audio.commit"}

// 5. Barge-in / Interruption (cancels ongoing assistant speech generation)
{"type": "turn.cancel", "turn_id": "turn-456"}

// 6. Terminate session
{"type": "session.end"}
```

### Server $\rightarrow$ Client Event Types

```json
// 1. Session confirmation
{"type": "session.created", "session_id": "sess-123", "agent_name": "Assistant"}

// 2. Real-time partial transcript
{"type": "transcript.partial", "seq": 1, "confirmed": "hello", "tentative": " there"}

// 3. Final recognized speech
{"type": "transcript.completed", "turn_id": "turn-456", "text": "Hello there!"}

// 4. Streaming LLM response tokens
{"type": "response.text.delta", "turn_id": "turn-456", "delta": "Hi! How can I help?"}

// 5. Synthesized audio stream header
{"type": "audio.started", "turn_id": "turn-456", "sample_rate": 24000, "sample_format": "s16le", "channels": 1}

// 6. Binary audio chunk: 24 kHz, 16-bit PCM (raw binary websocket frame)

// 7. Turn finished
{"type": "turn.completed", "turn_id": "turn-456"}

// 8. Turn cancelled (barge-in acknowledgement)
{"type": "turn.cancelled", "turn_id": "turn-456"}

// 9. Error event
{"type": "error", "message": "Backend engine timeout", "code": "BACKEND_TIMEOUT", "request_id": "req-8f3a9b1c"}
```

---

## 5.5 Dual-Layer Concurrency Control (`app/core/limits.py`)

Concurrency is managed through `BackendLimiter`:

1. **Global Backend Capacity Semaphores:**
   - Limits concurrent requests dispatched to any individual native engine to **5 concurrent slots**.
   - If all slots are occupied, incoming requests wait in an async queue for up to **5.0 seconds**.
   - If the queue wait expires, the gateway returns **HTTP 503 Service Unavailable** with header `Retry-After: 5`.
2. **Per-Key Concurrency Caps:**
   - Every key has a configurable concurrent connection ceiling (`api_keys.max_concurrent`, default 5).
   - If a single caller opens more than their allowed connections, the gateway immediately rejects additional requests with **HTTP 429 Too Many Requests** (`RATE_LIMIT_EXCEEDED`).

---

## 5.6 Standard Error Handling & Tracing

All error responses return structured JSON with an embedded request tracking ID:

```json
{
  "code": "RATE_LIMIT_EXCEEDED",
  "message": "Concurrency limit of 5 reached for key 'va-key-123' on backend 'stt'",
  "request_id": "req-9c2b4d8a"
}
```

The gateway sets `x-request-id: req-9c2b4d8a` on all HTTP responses and error events, enabling full correlation across reverse proxy access logs, gateway application logs, and engine journal entries.

---

## 5.7 Observability & Prometheus Metrics

Exposed internally at `GET /metrics` in Prometheus format via `app/services/metrics.py`:

- `voice_agent_requests_total{endpoint, status}`: Cumulative HTTP/WS request counter.
- `voice_agent_active_streams{type}`: Gauge of live WebSocket streams (voice, stt).
- `voice_agent_tokens_total{endpoint}`: Total LLM tokens processed.
- `voice_agent_audio_seconds_total{endpoint}`: Total audio duration processed.
- `voice_agent_stt_fallbacks_total{reason}`: Number of fallback events to batch Whisper STT.
- `voice_agent_turn_latency_seconds`: Latency histogram with buckets: `(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, +Inf)`.

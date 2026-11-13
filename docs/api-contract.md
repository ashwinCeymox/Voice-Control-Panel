# API Contract & Protocol Specification

This document is the **frozen single source of truth** for all client-facing and internal communication contracts. No worker or subagent may alter these contracts without an architecture review.

---

## 1. Authentication & Security

All non-health endpoints require authentication.

### 1.1 HTTP Authentication Headers

Clients must provide an API key via either:

1. `Authorization: Bearer <API_KEY>` (Standard OpenAI style)
2. `xi-api-key: <API_KEY>` (ElevenLabs style)

Both headers map internally to the same authenticated principal.

### 1.2 WebSocket Authentication (Single-Use Ticket)

Browsers cannot reliably send custom HTTP headers during WebSocket connection handshakes. To prevent exposing permanent API keys in query parameters:

1. The browser requests a short-lived ticket over HTTPS:
   - `POST /v1/auth/ticket`
   - Response: `{"ticket": "uuid4_str", "expires_in": 60}`
2. The browser connects to the WebSocket with:
   - `WS /v1/voice/stream?ticket={ticket}`
3. The server validates and immediately invalidates the single-use ticket. Permanent keys passed in WebSocket query parameters will be rejected.

---

## 2. Public REST API Endpoints

### 2.1 Health & Provider Discovery

#### `GET /health/live`

Process liveness check. Does not ping providers.

- **Auth:** None
- **Response `200 OK`:**

```json
{
  "status": "live"
}
```

#### `GET /health/ready`

Readiness check verifying backend reachability to LocalAI and local-tts.

- **Auth:** None
- **Response `200 OK`:**

```json
{
  "status": "ready",
  "providers": {
    "localai": {
      "reachable": true,
      "url": "http://127.0.0.1:8080"
    },
    "localtts": {
      "reachable": true,
      "url": "http://127.0.0.1:8880"
    }
  }
}
```

#### `GET /v1/providers`

Authenticated provider configuration and health details.

- **Auth:** Required
- **Response `200 OK`:**

```json
{
  "localai": {
    "configured": true,
    "reachable": true,
    "default_llm_model": "llama-3.2-3b-instruct",
    "default_stt_model": "whisper-1"
  },
  "localtts": {
    "configured": true,
    "reachable": true,
    "default_model": "kokoro",
    "default_voice_id": "af_heart"
  }
}
```

#### `GET /v1/models`

Returns installed LocalAI LLM and STT models.

- **Auth:** Required
- **Response `200 OK`:**

```json
{
  "object": "list",
  "data": [
    {
      "id": "llama-3.2-3b-instruct",
      "object": "model",
      "owned_by": "localai",
      "type": "llm"
    },
    {
      "id": "whisper-1",
      "object": "model",
      "owned_by": "localai",
      "type": "stt"
    }
  ]
}
```

#### `GET /v1/tts/models`

Returns available local-tts synthesis engines.

- **Auth:** Required
- **Response `200 OK`:**

```json
{
  "models": [
    {"id": "kokoro", "name": "Kokoro TTS"},
    {"id": "pocket", "name": "Pocket TTS"}
  ]
}
```

#### `GET /v1/voices`

Returns available voices from local-tts for the requested or default engine.

- **Auth:** Required
- **Query Params:** `model_id` (optional, string)
- **Response `200 OK`:**

```json
{
  "voices": [
    {
      "voice_id": "af_heart",
      "name": "Heart (American Female)",
      "category": "premade",
      "settings": {
        "speed": 1.0
      }
    }
  ]
}
```

---

### 2.2 Speech-to-Text (STT)

#### `POST /v1/audio/transcriptions`

OpenAI-compatible multipart audio transcription route.

- **Auth:** Required
- **Content-Type:** `multipart/form-data`
- **Fields:**
  - `file`: Binary audio file (WAV, WebM, MP3, OGG, FLAC) (required)
  - `model`: LocalAI STT model ID (optional; defaults to `LOCALAI_STT_MODEL`)
  - `language`: ISO language code e.g. "en" (optional)
  - `response_format`: `json` | `verbose_json` | `text` (optional; default `json`)
- **Response `200 OK`:**

```json
{
  "text": "Hello, how can I help you today?",
  "language": "en",
  "duration": 2.45
}
```

---

### 2.3 LLM Chat Completions

#### `POST /v1/chat/completions`

OpenAI-compatible chat completion route.

- **Auth:** Required
- **Content-Type:** `application/json`
- **Body:**

```json
{
  "model": "llama-3.2-3b-instruct",
  "messages": [
    {"role": "system", "content": "You are a concise voice assistant."},
    {"role": "user", "content": "What is 2+2?"}
  ],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": 150
}
```

- **Response (non-streaming) `200 OK`:**

```json
{
  "id": "chatcmpl-uuid",
  "object": "chat.completion",
  "created": 1740000000,
  "model": "llama-3.2-3b-instruct",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "2 + 2 equals 4."
      },
      "finish_reason": "stop"
    }
  ]
}
```

- **Response (streaming `stream: true`) `200 OK`:**
  - Server-Sent Events (`text/event-stream`):

  ```
  data: {"id":"chatcmpl-uuid","choices":[{"delta":{"content":"2"}}]}

  data: {"id":"chatcmpl-uuid","choices":[{"delta":{"content":" + 2 = 4."}}]}

  data: [DONE]
  ```

---

### 2.4 Text-to-Speech (TTS)

#### `POST /v1/text-to-speech/{voice_id}/stream`

ElevenLabs-compatible HTTP streaming synthesis.

- **Auth:** Required
- **Content-Type:** `application/json`
- **Body:**

```json
{
  "text": "Hello, this is a speech test.",
  "model_id": "kokoro",
  "voice_settings": {
    "speed": 1.0
  }
}
```

- **Response `200 OK`:**
  - Headers:
    - `Content-Type: audio/pcm;rate=24000;encoding=signed-int;bits=16`
    - `x-audio-sample-rate: 24000`
    - `x-audio-format: s16le`
    - `x-audio-channels: 1`
  - Body: Raw binary bytes of signed 16-bit, 24kHz mono PCM.

---

### 2.5 Combined Synchronous Voice Turn

#### `POST /v1/voice/turn`

One-shot audio in $\to$ STT $\to$ LLM $\to$ TTS $\to$ audio out.

- **Auth:** Required
- **Content-Type:** `multipart/form-data`
- **Fields:**
  - `file`: Binary audio upload (required)
  - `session_id`: UUID string (optional; created if omitted)
  - `llm_model`: string (optional)
  - `voice_id`: string (optional)
  - `tts_model`: string (optional)
- **Response `200 OK`:**

```json
{
  "turn_id": "turn-uuid4",
  "session_id": "session-uuid4",
  "request_id": "req-uuid4",
  "transcript": "What is machine learning?",
  "response_text": "Machine learning is a field of artificial intelligence focused on building systems that learn from data.",
  "audio_base64": "<base64_encoded_raw_pcm_bytes>",
  "audio_format": {
    "sample_rate": 24000,
    "sample_format": "s16le",
    "channels": 1
  },
  "metrics": {
    "stt_ms": 312,
    "llm_ms": 450,
    "tts_ms": 280,
    "total_ms": 1042
  }
}
```

---

## 3. Real-Time WebSocket Protocol (`WS /v1/voice/stream`)

Connection URL: `WS /v1/voice/stream?ticket={short_lived_ticket}`

### 3.1 Framing Standard

- **Control & Text Data:** Encoded as UTF-8 JSON text frames.
- **Inbound Audio (Client $\to$ Server):** Binary frames containing containerized audio chunks (e.g. WebM/Opus or WAV).
- **Outbound Audio (Server $\to$ Client):** Binary frames containing raw signed 16-bit, 24kHz mono PCM bytes, sent strictly between `audio.started` and `audio.completed` JSON control events.

### 3.2 Client $\to$ Server Events (JSON)

#### 1. `session.start`

Initiates a new turn or conversation session.

```json
{
  "type": "session.start",
  "session_id": "optional-uuid-or-null",
  "input_audio": {
    "container": "webm",
    "codec": "opus"
  },
  "config": {
    "llm_model": null,
    "voice_id": null
  }
}
```

#### 2. `audio.commit`

Signals the end of user speech for the current turn.

```json
{
  "type": "audio.commit"
}
```

#### 3. `turn.cancel`

Interrupts active LLM generation and audio synthesis immediately.

```json
{
  "type": "turn.cancel",
  "turn_id": "turn-uuid"
}
```

#### 4. `session.end`

Terminates the session and cleans up resources.

```json
{
  "type": "session.end"
}
```

---

### 3.3 Server $\to$ Client Events (JSON)

#### 1. `session.created`

Acknowledges session creation or connection.

```json
{
  "type": "session.created",
  "session_id": "session-uuid"
}
```

#### 2. `transcript.completed`

Emitted once LocalAI STT has processed the committed audio.

```json
{
  "type": "transcript.completed",
  "turn_id": "turn-uuid",
  "text": "What's the weather today?"
}
```

#### 3. `response.text.delta`

Emitted as LLM tokens arrive.

```json
{
  "type": "response.text.delta",
  "turn_id": "turn-uuid",
  "delta": " It looks sunny"
}
```

#### 4. `audio.started`

Emitted before binary PCM frames begin streaming to the client.

```json
{
  "type": "audio.started",
  "turn_id": "turn-uuid",
  "sample_rate": 24000,
  "sample_format": "s16le",
  "channels": 1
}
```

#### 5. `audio.completed`

Emitted when all synthesized audio frames for this turn have been dispatched.

```json
{
  "type": "audio.completed",
  "turn_id": "turn-uuid"
}
```

#### 6. `turn.completed`

Terminal event for a successfully finished turn.

```json
{
  "type": "turn.completed",
  "turn_id": "turn-uuid"
}
```

#### 7. `turn.cancelled`

Emitted when a turn was stopped via `turn.cancel` or client disconnect.

```json
{
  "type": "turn.cancelled",
  "turn_id": "turn-uuid"
}
```

#### 8. `error`

Emitted on any recoverable or unrecoverable error.

```json
{
  "type": "error",
  "code": "PROVIDER_TIMEOUT",
  "message": "LocalAI STT timed out after 30 seconds",
  "turn_id": "turn-uuid-or-null",
  "request_id": "req-uuid"
}
```

---

## 4. Standard Error Format

All REST error responses return standard JSON envelopes with appropriate HTTP status codes:

```json
{
  "error": {
    "code": "INVALID_API_KEY",
    "message": "The provided API key is invalid or revoked",
    "details": null
  },
  "request_id": "req-b91c28f0"
}
```

### Standard Error Codes

| HTTP Status | Error Code               | Description                                         |
| ----------- | ------------------------ | --------------------------------------------------- |
| `400`     | `INVALID_PAYLOAD`      | Request body or parameters failed validation        |
| `401`     | `UNAUTHORIZED`         | Missing or invalid API key / ticket                 |
| `404`     | `MODEL_NOT_FOUND`      | Configured or requested model ID does not exist     |
| `413`     | `PAYLOAD_TOO_LARGE`    | Uploaded audio file exceeds size/duration threshold |
| `502`     | `PROVIDER_UNAVAILABLE` | LocalAI or local-tts service unreachable            |
| `504`     | `PROVIDER_TIMEOUT`     | Provider inference exceeded timeout                 |

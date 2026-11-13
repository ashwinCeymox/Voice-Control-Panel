# WebSocket Protocol & Event Contracts (`WS /v1/voice/stream`)

**Version:** 2.0 (Post-Decision Gate)  
**Encoding:** UTF-8 JSON Control Envelopes + Raw 24kHz `s16le` Binary PCM Chunks  

---

## 1. Connection Handshake
- **Handshake URL:** `WS /v1/voice/stream?ticket={ticket}`
- **Authentication:** Validated via short-lived, single-use ticket acquired from `POST /v1/auth/ticket`.
- **Rejection:** Invalid or expired tickets receive WebSocket Close Code `4401`.

---

## 2. Client $\to$ Server Events

### `session.start`
```json
{
  "type": "session.start",
  "session_id": "optional-uuid-or-null",
  "agent_id": "optional-agent-uuid-or-null",
  "input_audio": {
    "container": "wav",
    "codec": "pcm",
    "sample_rate": 16000
  },
  "config": {
    "vad_engine": "silero",
    "echo_mode": "laptop_speakers",
    "barge_in": true
  }
}
```

### `vad.speech_start`
Emitted by browser-side Silero VAD as soon as speech probability exceeds `positive_speech_threshold`. Used by backend for logging and early barge-in prep.
```json
{
  "type": "vad.speech_start",
  "timestamp": 1794460000000
}
```

### `vad.misfire`
Emitted when speech energy stops before reaching `min_speech_ms` (e.g. keyboard click or throat clearing). Server logs only.
```json
{
  "type": "vad.misfire",
  "duration_ms": 120
}
```

### `audio.commit`
Emitted by browser-side VAD upon speech completion after `redemption_ms` trailing silence. In Silero mode, the complete 16kHz PCM WAV is transmitted as a binary frame immediately before this commit message.
```json
{
  "type": "audio.commit"
}
```

### `turn.cancel`
Emitted when user barges in or clicks interrupt.
```json
{
  "type": "turn.cancel",
  "turn_id": "optional-turn-id"
}
```

### `session.end`
```json
{
  "type": "session.end"
}
```

---

## 3. Server $\to$ Client Events

### `session.created`
```json
{
  "type": "session.created",
  "session_id": "uuid-str",
  "agent_name": "Default Assistant",
  "agent_id": "uuid-str"
}
```

### `transcript.completed`
```json
{
  "type": "transcript.completed",
  "turn_id": "uuid-str",
  "text": "User utterance transcribed."
}
```

### `response.text.delta`
```json
{
  "type": "response.text.delta",
  "turn_id": "uuid-str",
  "delta": " Assistant reply token."
}
```

### `audio.started`
```json
{
  "type": "audio.started",
  "turn_id": "uuid-str",
  "sample_rate": 24000,
  "sample_format": "s16le",
  "channels": 1
}
```

### `audio.completed`
```json
{
  "type": "audio.completed",
  "turn_id": "uuid-str"
}
```

### `turn.completed`
```json
{
  "type": "turn.completed",
  "turn_id": "uuid-str"
}
```

### `turn.cancelled`
```json
{
  "type": "turn.cancelled",
  "turn_id": "uuid-str"
}
```

### `error`
```json
{
  "type": "error",
  "code": "PROVIDER_TIMEOUT",
  "message": "Human-readable error details",
  "turn_id": "uuid-or-null",
  "request_id": "req-id"
}
```

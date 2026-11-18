# 7. Frontend: Control Panel

## 7.1 Architecture & Design Principles

The frontend control panel is an administrative Single-Page Application (SPA) built with React 18, TypeScript, and TailwindCSS, bundled via Vite.

The UI is built on four core design principles:

1. **Per-Stage Latency Visibility:** Display granular timings (`stt_ms`, `llm_ms`, `tts_ms`, `total_ms`) for every turn to quickly isolate engine bottlenecks.
2. **Graceful Status Degradation:** Independent status pills reflect live health for LocalAI, local-tts, and streaming STT (`GET /health/ready`), alerting operators if one engine fails without breaking unrelated tabs.
3. **Traceability:** Every error message surfaces the backend `request_id` for instant log correlation.
4. **Clean State Machine:** Client-side audio transitions strictly through an explicit 5-state machine to prevent audio queue deadlocks.

---

## 7.2 Page Inventory & Navigation

```text
Control Panel Navigation Layout
├── Assistant              # Full-duplex voice conversation orb
├── STT Playground         # Batch file upload & streaming mic testing
├── TTS Playground         # Text synthesis with sliders & audio playback
├── LLM Playground         # Prompt engineering & temperature testing
├── Voices Catalog         # Kokoro voice preview cards
├── Model Registry         # Unified catalog of installed models & downloads
├── Agents                 # Custom personality prompt & VAD editor
└── Developers
    ├── Overview           # Real-time CPU, RAM, VRAM, and stream gauges
    ├── API Keys           # Key generation, scope limits, and rotation
    └── Usage Analytics    # Granular token & audio telemetry charts
```

| Page | URL Tab | Primary Purpose | Key Backend Endpoints |
|---|---|---|---|
| **Assistant** | `assistant` | Real-time interactive voice orb with live waveform, transcript stream, and barge-in | `/v1/auth/ticket`, `WS /v1/voice/stream` |
| **STT Playground** | `stt-playground` | Dual-mode STT tester: batch file upload (Whisper) or live mic streaming (Sherpa) | `/v1/audio/transcriptions`, `WS /v1/audio/transcriptions/stream` |
| **TTS Playground** | `tts-playground` | Audio synthesis testing with voice selection, pitch/speed controls, and playback | `/v1/text-to-speech/{id}/stream`, `/v1/voices` |
| **LLM Playground** | `llm-playground` | Direct prompt testing for LocalAI models with temperature and max token controls | `/v1/chat/completions`, `/v1/models` |
| **Voices Catalog** | `voices` | Grid of Kokoro voices with instant one-click preview synthesis | `/v1/voices`, `/v1/voices/{id}/preview` |
| **Model Registry** | `models` | Model gallery browser, installation progress monitor, and residency management | `/v1/gallery/models`, `/v1/models/installed`, `/v1/models/install` |
| **Agents** | `agents` | Create, customize, and switch active voice agent personalities and prompts | `/v1/agents`, `/v1/agents/{id}/active` |
| **Developers / Overview** | `dev-overview` | Real-time system health, RAM/VRAM gauges, active connections, and latency stats | `/health/ready`, `/v1/system/resources` |
| **Developers / Keys** | `dev-keys` | Generate scoped API keys, set per-key rate limits, revoke keys, and initiate rotation | `/v1/admin/keys`, `/v1/admin/keys/{id}/rotate` |
| **Developers / Usage** | `dev-usage` | Token counts, audio minutes, and per-key billing telemetry charts | `/v1/admin/usage`, `/v1/admin/usage/summary` |

---

## 7.3 Voice Assistant State Machine

The animated voice orb transitions through 5 distinct states:

```text
[ idle ] ──(user speaks / VAD triggers)──► [ listening ]
                                                │
                                        (audio.commit sent)
                                                ▼
[ speaking ] ◄──(audio arrives)── [ thinking ] ◄──(speech transcribed)── [ transcribing ]
      │
(turn.cancel / barge-in)
      ▼
   [ idle ]
```

- **Listening:** Silero VAD detects user vocal activity; live mic audio frames stream to the gateway.
- **Transcribing:** User speech has ended; acoustic model finalizes the transcript.
- **Thinking:** Gateway queries LocalAI LLM with conversation history.
- **Speaking:** 24 kHz audio chunks arrive from Kokoro and play through the Web Audio queue. Live audio levels modulate orb animation.
- **Barge-in:** If the user speaks while the orb is in the `speaking` state, the client emits `turn.cancel`, stopping playback immediately.

---

## 7.4 Browser Audio & VAD Pipeline

```text
User Microphone
      │  Web Audio getUserMedia (echoCancellation: true, noiseSuppression: true)
      ▼
Web Audio Resampler ──► Converts hardware rate (44.1/48 kHz) to 16,000 Hz Mono
      │
      ├──────────────────────────────┐
      ▼                              ▼
Silero VAD (ONNX WebAssembly)   PCM16 Frame Encoder (16 kHz)
[Detects speech boundary]             │
      │                               ▼
Emits 'vad.speech_start'     Binary WebSocket Frames ──► Gateway
```

1. **Mic Capture:** Initialized with hardware echo cancellation, noise suppression, and auto gain control.
2. **Audio Resampling:** Client-side Web Audio Resampler converts input hardware rate to **16,000 Hz mono PCM16**.
3. **Silero VAD (`@ricky0123/vad-web`):** Executes in WebAssembly using `onnxruntime-web`. VAD assets load safely from `${import.meta.env.BASE_URL}vad/` to guarantee subpath compatibility.
4. **WebSocket Transmission:** Raw PCM 16-bit signed integer frames (`pcm_s16le`) are streamed over WebSocket.
5. **Output Playback:** Incoming 24 kHz audio chunks from local-tts Kokoro are queued and scheduled sequentially in the Web Audio context.

---

## 7.5 Frontend Security Invariants

- **Same-Origin Relative URLs:** All API requests use `getApiBase()` (`/v1/...`) to remain same-origin with the reverse proxy. Never hardcode `:8000` or `localhost`.
- **Derived WebSocket URL:** `getWsBase()` constructs the WebSocket URL dynamically from `window.location.host` using `wss:` over HTTPS.
- **Ephemeral Tickets:** WebSockets authenticate strictly via single-use tickets (`?ticket=...`). API keys are never placed in URLs.
- **HttpOnly Session Cookie (Phase 3):** Sensitive admin credentials are no longer stored in `localStorage` or `sessionStorage`. On page load, `GET /v1/panel/me` verifies the session. Unauthenticated visits show the super admin Login screen (`POST /v1/panel/login`). Successful authentication issues an `HttpOnly`, `SameSite=Strict`, `Secure` cookie (`admin_session`) holding a 256-bit random session token. If `must_change_password` is flagged, the user must update their password (`POST /v1/panel/change-password`) before the panel is unlocked. The session cookie acts as the unified admin principal across all panel routes and WebSocket ticket generation. [Verify]
- **No Production Sourcemaps:** Configured with `build: { sourcemap: false }` in `vite.config.ts`.

---

## 7.6 Subpath Production Deployment (`/voicecontrolpanel/`)

The control panel is compiled for subpath mounting:
1. `vite.config.ts` sets `base: '/voicecontrolpanel/'`.
2. Static assets are bundled via `npm --prefix frontend run build`.
3. In [`deploy/caddy/Caddyfile`](../../deploy/caddy/Caddyfile), Caddy serves static assets at `/voicecontrolpanel/*` from `/opt/voice-agent/frontend/dist/`.
4. Caddy protects the `/voicecontrolpanel/` route using HTTP `basic_auth` to prevent unauthorized public access.

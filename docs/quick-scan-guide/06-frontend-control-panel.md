# 6. Frontend: Control Panel (4 minutes)

> **TL;DR:** Modern React 18 + Vite control panel served at `/voicecontrolpanel/`. State-driven pages for live assistant chat, STT/TTS/LLM playgrounds, voice catalog, agent editor, and key administration. Silero VAD runs client-side in WebAssembly. Echo cancellation enabled by default.

---

## 6.1 Control Panel Pages Overview

| Page | Path / Tab | Description |
|---|---|---|
| **Assistant** | `assistant` | Full-duplex interactive voice orb with live waveform, transcript stream, and barge-in button |
| **STT Playground** | `stt-playground` | Dual-mode speech-to-text tester: file upload (batch) or mic stream (Sherpa-ONNX) |
| **TTS Playground** | `tts-playground` | Text synthesis tester with voice selection, pitch/speed sliders, and live audio playback |
| **LLM Playground** | `llm-playground` | Direct prompt engineering tester for LocalAI models with temperature and token controls |
| **Voices Catalog** | `voices` | Grid of available Kokoro voices with instant one-click preview buttons |
| **Model Registry** | `models` | Unified catalog of installed STT, LLM, and TTS models with installation progress |
| **Agents** | `agents` | Create and customize personality prompts, default voices, and VAD thresholds |
| **Developers / Overview** | `dev-overview` | Real-time system health, RAM/VRAM gauges, active connections, and latency |
| **Developers / Keys** | `dev-keys` | Generate, inspect, restrict scopes, and rotate API keys |
| **Developers / Usage** | `dev-usage` | Token counts, audio minutes, and per-key billing telemetry charts |

---

## 6.2 Voice Assistant State Machine

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

- **Interrupt:** While the orb is in the `speaking` state, any detected speech or clicking the interrupt button sends `turn.cancel`, stopping playback immediately.

---

## 6.3 Browser Audio & VAD Pipeline

1. **Mic Capture:** Initialized with browser `navigator.mediaDevices.getUserMedia`:
   - `echoCancellation: true`
   - `noiseSuppression: true`
   - `autoGainControl: true`
2. **Resampling:** High-precision Web Audio Resampler converts input hardware sample rate (typically 44.1 kHz or 48 kHz) to **16,000 Hz mono**.
3. **Client-Side VAD:** `@ricky0123/vad-web` runs the Silero VAD neural network inside WebAssembly using `onnxruntime-web`.
   - VAD assets loaded safely via `${import.meta.env.BASE_URL}vad/` to guarantee subpath compatibility.
4. **WebSocket Transmission:** Raw PCM 16-bit signed integer frames (`pcm_s16le`) are streamed over WebSocket.
5. **Output Playback:** Incoming 24 kHz audio chunks from local-tts Kokoro are queued and scheduled sequentially in the Web Audio context to prevent audio glitches.

> **Tip for Clean Audio:** Built-in laptop speakers cause echo when microphone volume is high. Use headphones or ensure hardware echo cancellation is active.

---

## 6.4 Frontend Best Practices (Do's and Don'ts)

| Do | Do Not |
|---|---|
| **Use relative paths:** Call `getApiBase()` (`/v1/...`) to remain same-origin. | **Never hardcode ports:** Do not hardcode `:8000` or `localhost`. |
| **Derive WebSocket URL:** Use `getWsBase()` based on `window.location.host`. | **Never put keys in URLs:** Avoid `?api_key=...` in WebSockets (use single-use tickets). |
| **Store admin keys safely:** Keep sensitive credentials in `sessionStorage` or memory. | **Never use `localStorage` for admin keys:** Vulnerable to XSS token theft. |
| **Disable sourcemaps in production:** `build: { sourcemap: false }` in `vite.config.ts`. | **Never expose source maps:** Prevents leaking internal API structure to clients. |

---

## 6.5 Hosting Configuration (`/voicecontrolpanel/`)

The control panel is mounted on a subpath behind Caddy:
- [`frontend/vite.config.ts`](../../frontend/vite.config.ts): Configured with `base: '/voicecontrolpanel/'`.
- [`deploy/caddy/Caddyfile`](../../deploy/caddy/Caddyfile): Serves compiled static assets under `/voicecontrolpanel/*` from `/opt/voice-agent/frontend/dist/`.
- Protected by Caddy HTTP `basic_auth` to prevent unauthorized public scanning of admin UI.

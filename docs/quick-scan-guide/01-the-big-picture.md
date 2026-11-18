# 1. The Big Picture (3 minutes)

> **TL;DR:** One public reverse proxy (Caddy). One central brain (the FastAPI gateway). Three native engines (LocalAI for LLM/batch STT, local-tts for Kokoro speech, Sherpa-ONNX for live streaming text). One SQLite database in WAL mode. Zero Docker containers to preserve raw GPU VRAM.

---

## 1.1 What It Does

A voice conversation turn operates in three sequential pipeline stages:

1. **Listen:** Audio captured from mic $\rightarrow$ Web Audio API converts to 16 kHz PCM $\rightarrow$ Silero VAD detects speech boundary $\rightarrow$ Sent over WebSocket to Sherpa-ONNX or LocalAI Whisper (Speech-to-Text).
2. **Think:** Gateway sends transcribed text + 30-message conversation context to LocalAI LLM (Language Model).
3. **Speak:** LLM response text is chunked into phrases $\rightarrow$ streamed to local-tts Kokoro $\rightarrow$ returned as 24 kHz audio chunks for seamless playback (Text-to-Speech).

The FastAPI gateway handles authentication, scope checks, concurrency limits, history persistence, and barge-in (interruption) detection.

---

## 1.2 Architecture Diagram

```text
Browser / Control Panel / External Client
                  │  HTTPS (:443) / WSS
                  ▼
         Caddy Reverse Proxy  (Public perimeter)
                  │
                  │  Localhost Loopback (127.0.0.1)
                  ▼
         FastAPI Gateway (:8000)  [The Brain]
          ├── SQLite (voice_platform.db in WAL mode)
          │
          ├──► LocalAI (:8080)        [LLM & Batch Whisper STT]
          ├──► local-tts (:8880)      [Kokoro TTS Audio Synthesis]
          └──► Sherpa-ONNX (:8088)    [Streaming Realtime STT]
```

---

## 1.3 Service Ports at a Glance

| Service | Port | Interface | Purpose | Authentication |
|---|---|---|---|---|
| **Caddy** | 80, 443 | `0.0.0.0` (Public) | TLS termination, security headers, `/voicecontrolpanel/` static serving | Basic Auth for panel; proxy rules |
| **Gateway** | 8000 | `127.0.0.1` | API routing, orchestration, key quotas, SQLite | Bearer API Key / Single-use tickets |
| **LocalAI** | 8080 | `127.0.0.1` | Llama-cpp LLM completion & Whisper batch STT | Internal `LOCALAI_API_KEY` header |
| **local-tts** | 8880 | `127.0.0.1` | Kokoro MLX (Apple Silicon) or PyTorch TTS | Loopback isolation + nftables |
| **Streaming STT** | 8088 | `127.0.0.1` | Sherpa-ONNX Zipformer live streaming text | Loopback isolation + nftables |

> **Non-Negotiable Rule:** Every backend engine listens **strictly on `127.0.0.1`**. No internal engine port is exposed to the internet.

---

## 1.4 A Single Voice Turn, Step-by-Step

1. **Handshake:** Browser requests a single-use ticket (`POST /v1/auth/ticket`, expires in 60s).
2. **Connect:** Browser opens WebSocket connection (`WS /v1/voice/stream?ticket=<ticket>`).
3. **Voice Detection:** Client-side Silero VAD detects user speech and emits `vad.speech_start`.
4. **Stream In:** Mic sends raw 16 kHz PCM frames. The streaming recognizer returns live `transcript.partial` events.
5. **Commit:** Speech stops $\rightarrow$ `audio.commit` emitted $\rightarrow$ `transcript.final` resolved.
6. **LLM Generation:** Gateway queries LLM with prompt context $\rightarrow$ emits `response.text.delta` stream.
7. **Synthesis & Playback:** Text chunks stream to TTS $\rightarrow$ 24 kHz audio chunks stream to browser $\rightarrow$ Web Audio queue plays them.
8. **Barge-in:** If the user speaks during playback, client sends `turn.cancel`, stopping LLM and audio generation instantly.

---

## 1.5 Audio Formats (Easy to Get Wrong)

| Channel | Sample Rate | Channels | Format | Upload Cap |
|---|---|---|---|---|
| **Mic $\rightarrow$ Streaming STT** | **16,000 Hz** (16 kHz) | Mono (1 ch) | PCM 16-bit Little-Endian (`pcm_s16le`) | 20 MB |
| **TTS $\rightarrow$ Speaker** | **24,000 Hz** (24 kHz) | Mono (1 ch) | PCM 16-bit Little-Endian (`pcm_s16le`) | N/A |

> **Watch Out:** Mismatched sample rates (e.g. sending 44.1 kHz or 48 kHz to a 16 kHz model) are the **#1 cause of garbled, robotic, or empty transcriptions**. Always resample in the browser before sending.

# 2. Libraries and Tools (3 minutes)

> **TL;DR:** Modern, clean dependencies. Backend: Python 3.12 + FastAPI 0.162 + Pydantic v2.14 + aiosqlite in WAL mode. Frontend: React 18.3 + TypeScript 5.6 + Vite 5.4 + TailwindCSS 3.4 + Silero VAD (ONNX Web). Production: Systemd services and Caddy 2 reverse proxy.

---

## 2.1 Backend Stack **[Confirmed]**

| Package / Tool | Exact Version | Purpose & Rationale |
|---|---|---|
| **Python** | `3.12.11` | Modern async performance, native `tomllib` in standard library |
| **FastAPI** | `0.162.2` | High-throughput async web framework, native WebSocket support |
| **Pydantic** | `2.14.3` | High-performance Rust-backed data validation and schema serialization |
| **Uvicorn** | `0.54.0` | ASGI server running the gateway application |
| **aiosqlite** | `0.20.0` | Async SQLite database driver with WAL mode and busy timeouts |
| **Pytest** | `9.2.0` | Comprehensive test suite (unit, integration, contracts, safety) |

---

## 2.2 Engines Stack **[Confirmed]**

| Engine | Upstream Core | Acceleration / Backend | Ports |
|---|---|---|---|
| **LocalAI** | llama.cpp + whisper.cpp | Apple Silicon Metal / NVIDIA CUDA (Linux) | `:8080` |
| **local-tts** | Kokoro TTS | Apple MLX (`local-tts` on Mac) / PyTorch CUDA (Linux) | `:8880` |
| **Streaming STT** | Sherpa-ONNX | Zipformer ONNX runtime (`sherpa-onnx` v1.10+) | `:8088` |

> **Architecture Note:** On Apple Silicon macOS, local-tts uses Apple MLX. On Linux bare metal (Hetzner), local-tts runs with the PyTorch CUDA backend. The gateway communicates over standard OpenAI/ElevenLabs HTTP protocols, keeping the backend platform-agnostic.

---

## 2.3 Frontend Stack **[Confirmed]**

| Package | Version | Purpose |
|---|---|---|
| **React** | `18.3.1` | Reactive user interface |
| **TypeScript** | `5.6.3` | Strict type checking and API contract safety |
| **Vite** | `5.4.11` | Fast dev server, production bundler with subpath support (`/voicecontrolpanel/`) |
| **TailwindCSS** | `3.4.15` | Modern styling and dark-theme control panel |
| **Lucide React** | `1.16.0` | UI icons |
| **@ricky0123/vad-web** | `0.0.31` | Silero Voice Activity Detection running client-side in WebAssembly |
| **onnxruntime-web** | `1.31.1` | WebAssembly ONNX inference engine executing VAD in the browser |

---

## 2.4 Infrastructure & Operating System **[Confirmed]**

| Tool | Role in Architecture |
|---|---|
| **Caddy 2** | Reverse proxy, automatic Let's Encrypt TLS, basic_auth for control panel, log scrubbing |
| **Systemd** | Process lifecycle manager (`voice-gateway`, `voice-localai`, `voice-tts`, `voice-stt`, `voice-cleanup`) |
| **UFW + nftables** | Network perimeter (only 22, 80, 443 open) + local user engine isolation |
| **Fail2ban** | Automated banning of repeated authentication failures |

---

## 2.5 Development vs Production Configuration

| Parameter | Development (Mac M2) | Production (Hetzner Bare Metal) |
|---|---|---|
| **Hardware** | Apple M2 Mac (16 GB unified RAM) | Dedicated server, Intel/AMD + NVIDIA RTX GPU |
| **Execution Model** | Shell scripts (`scripts/start-native-engines.sh`) | Systemd isolated services (`deploy/systemd/*.service`) |
| **Frontend Serving** | Vite Dev Server (`localhost:5173`) | Static assets served by Caddy at `/voicecontrolpanel/` |
| **HTTPS / TLS** | Plain HTTP (localhost) | Automatic HTTPS via Caddy (Let's Encrypt / ZeroSSL) |
| **User & UID** | Local user (`hashhwin047...`, UID 501) | Dedicated unprivileged system user `voiceagent` |
| **Gateway Workers** | Single worker with Uvicorn | Single worker (maintains memory ticket state & semaphores) |
| **Engine Auth** | Loopback binding | Loopback + `LOCALAI_API_KEY` + nftables UID isolation |

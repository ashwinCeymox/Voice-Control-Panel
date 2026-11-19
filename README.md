# Local Voice Agent Platform

A production-grade, privacy-first, fully local Voice AI platform featuring a high-concurrency **FastAPI gateway**, real-time streaming speech-to-text (**Sherpa-ONNX Zipformer**), local large language models & batch STT (**LocalAI**), low-latency speech synthesis (**Kokoro MLX**), and a reactive **React Control Panel** with client-side Voice Activity Detection (**Silero VAD** via WebAssembly).

---

## 1. System Architecture

```mermaid
flowchart TD
    subgraph Client ["Client Browser / Mobile"]
        UI["Control Panel (React + Vite)"]
        VAD["Silero VAD (ONNX WebAssembly)"]
        Mic["Microphone Audio Stream (16kHz PCM)"]
        Mic --> VAD --> UI
    end

    subgraph Gateway ["FastAPI Orchestration Gateway (:8000)"]
        Auth["Unified Auth Principal (Cookies / API Keys / WS Tickets)"]
        Router["Voice Orchestrator & Session Manager"]
        RateLimit["Rate Limiting & Memory-Hard Admin Lockout"]
        Auth --> Router
        RateLimit --> Router
    end

    subgraph Engines ["Local Offline Inference Engines (127.0.0.1 Loopback Only)"]
        STT["Sherpa-ONNX Streaming STT (:8088)<br/>Zipformer 20M"]
        LLM["LocalAI Engine (:8080)<br/>Llama 3.2 1B Instruct & Whisper STT"]
        TTS["local-tts Engine (:8880)<br/>Kokoro 82M (Apple MLX Metal)"]
    end

    UI -- "WebSocket (wss://) / REST" --> Gateway
    Router -- "Async Streaming WS" --> STT
    Router -- "Async HTTP / SSE" --> LLM
    Router -- "Async Binary PCM Stream" --> TTS
```

---

## 2. Hardware and Operating System Requirements

| Environment | Operating System | Acceleration | Supported Capabilities |
|---|---|---|---|
| **Local Development** | macOS 13+ (Ventura, Sonoma, Sequoia) | Apple Silicon (Metal GPU & Neural Engine) | Full duplex voice pipeline, streaming STT, LocalAI LLM, Kokoro MLX TTS |
| **Production Server** | Linux x86_64 (Ubuntu 22.04 / 24.04 LTS) | NVIDIA GPU (CUDA 12.x) | Streaming STT, LocalAI LLM & Whisper STT, Clean HTTP 503 TTS fallback |
| **Edge / Minimal** | Linux x86_64 / Debian | CPU Only | Basic STT and LLM inference, Clean HTTP 503 TTS fallback |

> [!NOTE]
> Kokoro speech synthesis on Apple MLX runs exclusively on Apple Silicon macOS. On Linux environments (such as Hetzner or RunPod), the gateway cleanly reports Text-to-Speech as unavailable (HTTP 503) without crashing.

---

## 3. Quick Start (One-Command Installer)

The platform provides a unified, idempotent one-command launcher (`./start.sh`) that manages installation, dependencies, models, and service lifecycles across both **RunPod Cloud** and **macOS Development**.

### 3.1 RunPod Cloud Deployment
1. **Launch a Pod:** Select any stock NVIDIA GPU template (e.g. PyTorch / CUDA 12.x or Ubuntu base).
2. **Expose Public Port:** Under **Expose HTTP Ports**, set port `8000` (matching `GATEWAY_PORT`) before starting the pod.
3. **Storage:** Ensure your persistent volume is mounted at `/workspace` with at least 20GB volume disk.
4. **Deploy in One Command:**
   ```bash
   git clone <repo_url> /workspace/voice-agent && cd /workspace/voice-agent && ./start.sh
   ```
5. **Access Panel:** The script checks dependencies, starts all engines, and prints your public proxy URL (`https://<pod-id>-8000.proxy.runpod.net/voicecontrolpanel`) and initial admin credentials directly to the terminal. Open the URL in your browser and log in!

### 3.2 macOS Local Development
On your Apple Silicon Mac:
```bash
./start.sh
```
This automatically verifies Apple Metal acceleration, checks resident local-tts (MLX), verifies Python and Node dependencies, and boots the complete stack on local loopback `127.0.0.1`.

### 3.3 Stack Management Commands
```bash
./start.sh              # Check dependencies, start services, and show banner
./start.sh status       # Display live health, PIDs, and ports for all services
./start.sh ports        # View active network ports and conflict resolutions
./start.sh url          # Display public web panel and API URLs
./start.sh reset-admin  # Break-glass reset for super admin credentials
./start.sh restart      # Safely reload .env, apply port edits, and restart stack
./start.sh stop         # Gracefully terminate all recorded stack PIDs
```

---

## 4. Configuration Reference

All settings can be configured via environment variables or a local `.env` file (copied from [`.env.example`](.env.example)):

| Variable | Default | Description |
|---|---|---|
| `ENVIRONMENT` | `development` | Deployment environment: `development`, `staging`, or `production`. In `staging`/`production`, weak or default passwords are strictly rejected at startup. |
| `GATEWAY_HOST` | `127.0.0.1` | Network interface for FastAPI Gateway (`0.0.0.0` only in container test runs). |
| `GATEWAY_PORT` | `8000` | Gateway HTTP listening port. |
| `TRUSTED_PROXY_IPS` | `127.0.0.1` | Comma-separated list of trusted upstream reverse proxy IPs (e.g. Caddy/Nginx) for `X-Forwarded-For` parsing. |
| `COOKIE_SECURE` | `false` | Enforces `Secure` flag on admin session cookies. Must be `true` behind TLS proxies. |
| `ADMIN_INITIAL_PASSWORD` | *(Generated)* | Initial super admin password seeded on fresh database creation (minimum 12 characters). |
| `ADMIN_API_KEY` | *(Generated)* | Full-privilege admin API key for automation and health probes. |
| `API_KEY_HASH_SECRET` | *(Generated)* | Cryptographic pepper used for HMAC-SHA256 API key hashing. |
| `DATABASE_PATH` | `data/voice_platform.db` | Path to persistent SQLite database. |
| `LOCALAI_BASE_URL` | `http://127.0.0.1:8080` | URL of internal LocalAI instance (strictly `127.0.0.1`). |
| `LOCAL_TTS_HTTP_BASE_URL` | `http://127.0.0.1:8880` | URL of internal local-tts instance (strictly `127.0.0.1`). |
| `STREAMING_STT_WS_URL` | `ws://127.0.0.1:8088/ws` | WebSocket endpoint of internal Sherpa-ONNX STT server. |

---

## 5. Security Architecture

- **Engine Isolation**: Backend inference engines (`LocalAI`, `local-tts`, `Sherpa-ONNX`) **never** listen on `0.0.0.0`. They are strictly bound to loopback `127.0.0.1` and are reachable only via the authenticated FastAPI gateway.
- **Unified Authentication Principal**: All panel routes and endpoints accept authenticated admin session cookies (`admin_session`) or scoped bearer API keys.
- **WebSocket Ticket Protocol**: WebSocket handshakes accept temporary 60-second tickets (`/v1/auth/ticket`) or direct `admin_session` cookies, preventing long-lived credential leakage in connection URLs.
- **Brute Force Protection**: Super admin authentication features Argon2id password hashing, constant-time dummy verifications, 5-attempt account lockout, and per-IP throttling over a 15-minute window.
- **Production Safety Gate**: Startup fails immediately if `ENVIRONMENT` is set to `staging` or `production` with default passwords or passwords shorter than 12 characters.

---

## 6. Testing & Quality Verification

Run the comprehensive automated test suite and security probes:

```bash
# Run 225+ automated pytest unit and integration tests
.venv/bin/pytest tests/ -q

# Run end-to-end authentication matrix probe across 31 endpoints
.venv/bin/python scripts/auth_matrix_probe.py

# Lint and format checks
ruff check . && ruff format --check .

# Frontend production build
cd frontend && npm run build
```

---

## 7. Deployment Guides

Refer to the documentation in [`docs/deploy/`](docs/deploy) for detailed production deployment instructions:
- [Hetzner Bare-Metal / Dedicated Server Deployment](docs/deploy/DEPLOY_HETZNER.md)
- [Caddy Reverse Proxy & Control Panel Configuration](docs/deploy/CONTROLPANEL_PROXY.md)
- [RunPod Container Validation & Testing Guide](docs/deploy/RUNPOD_TEST.md)
- [Example Caddyfile for Hetzner](docs/deploy/Caddyfile.hetzner.example)
- [Example Caddyfile for RunPod](docs/deploy/Caddyfile.runpod.example)

---

## 8. License

License pending owner decision. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for licenses of third-party models and runtime libraries.

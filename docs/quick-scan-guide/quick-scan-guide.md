---
title: "Voice AI Platform: Quick-Scan Guide"
subtitle: "The ADHD-friendly edition: short chunks, summaries first, checklists everywhere"
author: "Voice Agent Team"
date: "17 November 2026"
version: "1.0 (Production Verified)"
lang: en-GB
---

# Start Here (2 minutes)

> **TL;DR:** This is a self-hosted, production-hardened voice assistant platform. You talk, it listens, thinks, and talks back. A Python FastAPI gateway orchestrates the pipeline. A React control panel serves management and playgrounds. Three native engines (LocalAI, local-tts, Sherpa-ONNX) run locally on bare metal without Docker to maximize GPU VRAM.

**How to use this guide**

- You do **not** need to read it top to bottom.
- Every section starts with a **TL;DR box**. Read only that if you are in a hurry.
- Use the **"I want to..."** table below to jump to what you need.
- Stuck? Go to section 13 (Troubleshooting).

**Reading time:** whole guide about 35 minutes. Each section 2 to 5 minutes.

## What the confidence tags mean

| Tag | Meaning | What to do |
|---|---|---|
| **[Confirmed]** | Verified against live code, database schemas, and passing test suites | Trust it |
| **[Planned]** | Designed, not yet built or deployed | Treat as an upcoming roadmap item |
| **[Architecture Note]** | Key constraint or platform difference (e.g. macOS Metal vs Linux NVIDIA) | Keep in mind during deployment |

## I want to... (jump table)

| I want to... | Go to |
|---|---|
| Understand the big picture & audio flow | Section 1 |
| Know which libraries and exact versions we use | Section 2 |
| Find a file, folder, or add an endpoint | Section 3 |
| Call or change an API, inspect scopes & WebSocket events | Section 4 |
| See what is in the SQLite database & privacy rules | Section 5 |
| Change the React control panel & VAD settings | Section 6 |
| Work on LocalAI, Kokoro TTS, or Sherpa streaming STT | Section 7 |
| Check security, nftables isolation, and auth matrix | Section 8 |
| Deploy to Hetzner bare metal with systemd & Caddy | Section 9 |
| Look up an environment variable or default setting | Section 10 |
| Run test suites, probes, and benchmarks | Section 11 |
| Check known issues and prioritized roadmap | Section 12 |
| Fix an error, broken audio, or startup crash right now | Section 13 |
| Review resolved audit items and schema confirmations | Appendix A |
| Look up a voice AI term | Appendix B |
| Export this guide to DOCX or PDF via Pandoc | Appendix C |

---

# 1. The Big Picture (3 minutes)

> **TL;DR:** One public door (Caddy). One brain (the FastAPI gateway). Three native helpers (LocalAI, local-tts, Sherpa-ONNX streaming STT). One database (SQLite in WAL mode). Zero Docker containers to preserve raw GPU VRAM.

## 1.1 What it does

A voice turn operates in three sequential stages:

1. **Listen:** Audio captured from mic $\rightarrow$ Web Audio API converts to 16 kHz PCM $\rightarrow$ Silero VAD detects speech boundary $\rightarrow$ Sent over WebSocket to Sherpa-ONNX or LocalAI Whisper (Speech-to-Text).
2. **Think:** Gateway sends transcribed text + 30-message conversation context to LocalAI LLM (Language Model).
3. **Speak:** LLM response text is chunked into phrases $\rightarrow$ streamed to local-tts Kokoro $\rightarrow$ returned as 24 kHz audio chunks for seamless playback (Text-to-Speech).

The gateway chains them together and adds authentication, limits, history, and barge-in (interruption) detection.

## 1.2 The picture

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

## 1.3 The parts at a glance

| Part | Port | Job | Auth / Security |
|---|---|---|---|
| **Caddy** | 80, 443 | HTTPS, proxying, `/voicecontrolpanel/` static serving | Basic Auth for panel; proxy rules |
| **Gateway** | 8000 | API routing, login, orchestration, key quotas, SQLite | Bearer API Key / Single-use tickets |
| **LocalAI** | 8080 | Llama-cpp LLM completion & Whisper batch STT | Internal `LOCALAI_API_KEY` header |
| **local-tts** | 8880 | Kokoro TTS audio synthesis | Loopback isolation + nftables |
| **Streaming STT** | 8088 | Sherpa-ONNX Zipformer live streaming text | Loopback isolation + nftables |

> **Rule to remember:** Every internal port listens on `127.0.0.1` only. Nothing but Caddy faces the internet.

## 1.4 One voice turn, step by step

1. Browser asks for a one-time **ticket** (`POST /v1/auth/ticket`, valid 60 seconds).
2. Browser opens the WebSocket with that ticket (`WS /v1/voice/stream?ticket=<ticket>`).
3. Browser detects speech and sends audio frames.
4. Gateway turns audio into text, asks the LLM, then turns the answer into audio.
5. Audio streams back. The user can **interrupt** (barge-in) at any time.

## 1.5 Audio formats (easy to get wrong)

| Direction | Sample Rate | Channels | Format | Upload Cap |
|---|---|---|---|---|
| **Mic to streaming STT** | **16 kHz** (16,000 Hz) | Mono (1 ch) | PCM 16-bit signed (`pcm_s16le`) | 20 MB |
| **TTS to speaker** | **24 kHz** (24,000 Hz) | Mono (1 ch) | PCM 16-bit signed (`pcm_s16le`) | N/A |

> **Watch out:** Mismatched sample rates are the **number one cause of garbled, robotic, or empty audio**. Always resample in the browser before sending.

---

# 2. Libraries and Tools (3 minutes)

> **TL;DR:** Modern, clean dependencies. Backend: Python 3.12 + FastAPI 0.162 + Pydantic v2.14 + aiosqlite in WAL mode. Frontend: React 18.3 + TypeScript 5.6 + Vite 5.4 + TailwindCSS 3.4 + Silero VAD (ONNX Web). Production: Systemd services and Caddy 2 reverse proxy.

## 2.1 Backend **[Confirmed]**

| Tool | Version | Why we use it |
|---|---|---|
| **Python** | `3.12.11` | Modern async performance, native `tomllib` |
| **FastAPI** | `0.162.2` | High-throughput async web framework, WebSockets |
| **Pydantic** | `2.14.3` | Fast Rust-backed schema validation and serialization |
| **Uvicorn** | `0.54.0` | ASGI server running the gateway application |
| **aiosqlite** | `0.20.0` | Async SQLite driver with WAL mode and busy timeout |
| **pytest** | `9.2.0` | 207 unit, integration, and security test suites |

## 2.2 Engines **[Confirmed]**

| Engine | Upstream Core | Acceleration / Backend | Ports |
|---|---|---|---|
| **LocalAI** | llama.cpp + whisper.cpp | Apple Silicon Metal / NVIDIA CUDA (Linux) | `:8080` |
| **local-tts** | Kokoro TTS | Apple MLX (`local-tts` on Mac) / PyTorch CUDA (Linux) | `:8880` |
| **Streaming STT** | Sherpa-ONNX | Zipformer ONNX runtime (`sherpa-onnx` v1.10+) | `:8088` |

> **Watch out:** Kokoro MLX only runs on Apple Silicon. Linux bare metal runs Kokoro via PyTorch CUDA. The gateway interacts with both via standard HTTP protocols.

## 2.3 Frontend **[Confirmed]**

| Tool | Version | Why |
|---|---|---|
| **React** | `18.3.1` | The UI components |
| **TypeScript** | `5.6.3` | Strict type safety and contract enforcement |
| **Vite** | `5.4.11` | Fast dev server and optimized production build |
| **TailwindCSS** | `3.4.15` | Modern styling and dark-theme control panel |
| **Lucide React** | `1.16.0` | Control panel icon library |
| **@ricky0123/vad-web** | `0.0.31` | Silero Voice Activity Detection in WebAssembly |
| **onnxruntime-web** | `1.31.1` | WebAssembly runtime executing VAD in the browser |

## 2.4 Dev vs production

| Parameter | Dev now | Production target |
|---|---|---|
| **Machine** | Apple M2 Mac (16 GB unified RAM) | Hetzner server with NVIDIA RTX GPU |
| **Run with** | Shell scripts (`scripts/start-native-engines.sh`) | Systemd isolated service units |
| **Gateway** | Single worker under dev loop | Single worker (maintains memory ticket state & semaphores) |
| **Frontend** | Vite dev server | Static build in Caddy (`/voicecontrolpanel/`) |
| **HTTPS** | No (localhost) | Yes, automatic via Let's Encrypt / Caddy |
| **User** | Local user (uid 501) | Dedicated `voiceagent` user |

---

# 3. Where Things Live (2 minutes)

> **TL;DR:** Routes in `app/api/`, rules in `app/services/`, engine adapters in `app/providers/`, data shapes in `app/schemas/`. Database in `app/storage/database.py`. React frontend in `frontend/`. Operational scripts in `scripts/`.

```text
local-voice-agent/
  app/
    main.py        FastAPI entry point & lifespan
    api/           HTTP & WebSocket route handlers (thin)
    services/      Business logic & coordination
    providers/     Engine adapters (LocalAI, local-tts, Sherpa)
    schemas/       Pydantic models & WebSocket events.py
    storage/       database.py (SQLite async connection)
    core/          config.py, security.py, limits.py, errors.py
  services/streaming_stt/server.py
  frontend/        React 18 + Vite control panel
  deploy/          systemd units, Caddyfile, nftables, fail2ban
  policies/        cleanup.dev.toml, cleanup.server.toml
  scripts/         cleanup.py, deploy-hetzner.sh, backup-db.sh, restore-db.sh
  tests/           207 pytest tests (unit, integration, contracts, safety)
  data/            voice_platform.db, backups/, cleanup manifests
  .env             Secrets (never committed)
```

## 3.1 Adding a new endpoint (checklist)

- [ ] 1. Create request & response models in `app/schemas/`.
- [ ] 2. Put business logic in `app/services/` (not in routes directly).
- [ ] 3. Create route in `app/api/` using `APIRouter`.
- [ ] 4. Register router in `app/main.py`.
- [ ] 5. Pick the right auth dependency (`require_scope` or `require_admin_key`).
- [ ] 6. Add route to `scripts/auth_matrix_probe.py`.
- [ ] 7. Add unit test in `tests/unit/`.
- [ ] 8. Verify all tests pass with `.venv/bin/pytest tests/`.

---

# 4. Backend and API (5 minutes)

> **TL;DR:** 42 total endpoints. Strict role-based and scope-based access control. All caller endpoints require valid API keys with explicit scopes. Tickets expire in 60s for WebSockets. Every response contains an `x-request-id` tracing header.

## 4.1 Login in 30 seconds

- Send `Authorization: Bearer <key>` or `xi-api-key: <key>`.
- Keys are hashed with **HMAC-SHA256** (`settings.API_KEY_HASH_SECRET`) **[Confirmed]**.
- Two roles: `admin` and `caller`.
- Scopes limit callers: `llm`, `stt`, `tts`, `voice_stream`, `stt_stream`, `voice`.
- Revoked or expired keys fail on the very next request.

> **Watch out:** If `ADMIN_API_KEY` is not set or left at default `dev-voice-key-12345`, the gateway refuses to boot when `ENVIRONMENT=production`.

## 4.2 Routes Cheat Sheet

### A. Public (No Key)
| Method | Path | Use | Production Rule |
|---|---|---|---|
| `GET` | `/health/live` | Process liveness | Open |
| `GET` | `/health/ready` | Providers readiness | Open |
| `GET` | `/health/detail` | Detailed health | Open |
| `GET` | `/` | Welcome banner | Open |
| `GET` | `/metrics` | Prometheus | **Blocked at proxy** |

### B. Caller Routes (Scope Needed)
| Method | Path | Scope | Use |
|---|---|---|---|
| `POST` | `/v1/chat/completions` | `llm` | Chat completion |
| `POST` | `/v1/audio/transcriptions` | `stt` | Batch Whisper STT |
| `POST` | `/v1/text-to-speech/{id}/stream` | `tts` | Stream synthesized audio |
| `GET` | `/v1/voices` | `tts` | List TTS voices |
| `POST` | `/v1/voices/{id}/preview` | `tts` | Audio preview |
| `GET` | `/v1/tts/models` | `tts` | TTS models |
| `POST` | `/v1/voice/turn` | `voice` **[Confirmed]** | Multi-modal turn: audio in $\rightarrow$ audio out |

### C. Any Valid Key
| Method | Path | Use |
|---|---|---|
| `GET` | `/v1/models` | Unified model list |
| `GET` | `/v1/models/installed` | Installed models |
| `GET` | `/v1/models/install/{job_id}` | Installation job progress |
| `GET` | `/v1/gallery/models` | Installable gallery catalog |
| `GET` | `/v1/system/resources` | CPU RAM and GPU VRAM |
| `POST` | `/v1/auth/ticket` | Single-use WebSocket ticket |

### D. Admin Only (`role == "admin"`)
| Method | Path | Use |
|---|---|---|
| `POST` | `/v1/models/install` | Install model from gallery |
| `DELETE` | `/v1/models/{id}` | Delete model files **[Confirmed: Admin Only]** |
| `PATCH` | `/v1/models/{id}/classification` | Override task tag **[Confirmed: Admin Only]** |
| `PUT` | `/v1/voices/{id}/meta` | Update voice metadata |
| `GET`, `POST` | `/v1/agents` | Manage voice agents |
| `GET`, `PUT`, `DELETE` | `/v1/agents/{id}` | Manage single voice agent |
| `GET`, `POST`, `PATCH`, `DELETE` | `/v1/admin/keys` | API keys CRUD & revocation |
| `POST` | `/v1/admin/keys/{id}/rotate` | Dual-key rotation with grace window |
| `GET`, `PUT` | `/v1/admin/settings` | Platform concurrency defaults |
| `GET`, `PUT` | `/v1/admin/settings/stt` | Streaming STT kill switch & caps |
| `GET` | `/v1/admin/usage` | Usage events and export |

### E. WebSockets
| Path | Scope Required | Use |
|---|---|---|
| `WS /v1/voice/stream` | `voice_stream` | Live interactive voice assistant |
| `WS /v1/audio/transcriptions/stream` | `stt_stream` | Live real-time transcription |

> **WebSocket Auth:** Authenticate via single-use ticket (`?ticket=<ticket>`). Unauthorized connections close with RFC 6455 custom code `4401` or `4403`.

## 4.3 Voice Stream WebSocket Events **[Confirmed]**

| Direction | Event Name | Payload / Meaning |
|---|---|---|
| Client $\rightarrow$ Server | `vad.speech_start` | Speech began |
| Client $\rightarrow$ Server | Binary frames | Raw PCM16 frames (16 kHz mono) |
| Client $\rightarrow$ Server | `audio.commit` | Done speaking |
| Client $\rightarrow$ Server | `turn.cancel` | Barge-in / interruption |
| Server $\rightarrow$ Client | `session.created` | Session ID & active agent confirmation |
| Server $\rightarrow$ Client | `transcript.partial` | Incremental real-time text |
| Server $\rightarrow$ Client | `transcript.completed` | Final recognized speech text |
| Server $\rightarrow$ Client | `response.text.delta` | Streaming tokens from LLM |
| Server $\rightarrow$ Client | Binary chunks | Synthesized 24 kHz audio frames |
| Server $\rightarrow$ Client | `turn.completed` | Turn finished |
| Server $\rightarrow$ Client | `error` | Failure message + `request_id` |

Exact schemas are in [`app/schemas/events.py`](../../app/schemas/events.py) **[Confirmed]**.

## 4.4 Limits & Concurrency **[Confirmed: Dual Layer]**

1. **Global Backend Semaphore:** Max 5 simultaneous requests per backend engine. Wait timeout 5.0s $\rightarrow$ HTTP 503 (`Retry-After: 5`).
2. **Per-Key Concurrency Cap:** Max concurrent requests per API key (default 5) $\rightarrow$ HTTP 429 (`RATE_LIMIT_EXCEEDED`).

## 4.5 Error codes

| Code | Meaning |
|---|---|
| 401 | Missing, malformed, or revoked key / ticket |
| 403 | Missing required scope or non-admin calling admin route |
| 404 | Voice or model ID not found |
| 429 | Rate limit or per-key concurrency cap hit |
| 502 | Backend engine offline or starting up |
| 503 | Backend capacity full (retry after 5s) |
| 504 | Engine inference timeout |
| 507 | Disk space critical (< 5% free space) |

Every error carries an `x-request-id` header for tracing.

---

# 5. Data and Database (4 minutes)

> **TL;DR:** One SQLite database (`data/voice_platform.db`) in WAL mode with busy timeout 5000ms. Tables for sessions, messages, turns, agents, models, API keys, and usage analytics. Raw audio is never saved to disk. Ephemeral playground sessions auto-expire in 24 hours.

## 5.1 Where data lives

| What | Where | Backup / Retention |
|---|---|---|
| **Primary Database** | `data/voice_platform.db` | WAL mode; protected from cleanup |
| **WAL & SHM Files** | `data/voice_platform.db-wal`, `*-shm` | Auto-checkpointed every 10 mins |
| **Model Files** | `LocalAI/models/`, `models/streaming-stt/` | Protected from cleanup |
| **Installed Manifest** | `data/installed_models_manifest.json` | Persistent catalog sync |
| **Backups** | `data/backups/` | Nightly cron; newest 3 copies kept |
| **Quarantine** | `.trash/<run-id>/` | 7-day retention; restorable |
| **WebSocket Tickets** | Memory only (`_TICKETS`) | 60s TTL; auto-pruned |

## 5.2 Key Tables

- `api_keys`: Stores HMAC-SHA256 hashes, roles, scopes, rate limits, daily budgets, and rotation history.
- `sessions`: Conversation sessions with `is_ephemeral` flag.
- `turns`: Records spoken transcripts, assistant text, and component latencies (`stt_ms`, `llm_ms`, `tts_ms`, `total_ms`).
- `agents`: Custom agent profiles (personality prompt, default models, voice ID, VAD settings).
- `installed_models`: Installed models catalog across all engines.
- `usage_events`: Granular token and audio seconds tracking for analytics and billing.

## 5.3 Privacy Rules

- Raw audio is **never** saved to disk.
- Transcripts are **not** logged to standard out or journald (`LOG_TRANSCRIPTS=false`).
- Playground sessions are marked `is_ephemeral = 1` and pruned after **24 hours** by the hygiene manager.

## 5.4 Backups

- Run `scripts/backup-db.sh` for an atomic SQLite `.backup` copy.
- Restore with `scripts/restore-db.sh <backup_file>`.
- Nightly cron installed at `/etc/cron.d/voice-backup`.

---

# 6. Frontend: Control Panel (4 minutes)

> **TL;DR:** React 18 + Vite control panel served at `/voicecontrolpanel/`. State-driven pages for live assistant chat, STT/TTS/LLM playgrounds, voice catalog, agent editor, and key administration. Silero VAD runs client-side in WebAssembly. Echo cancellation enabled by default.

## 6.1 Pages

- **Assistant:** Interactive orb with live waveform, transcript stream, and barge-in.
- **STT Playground:** Dual-mode speech-to-text tester: file upload (batch) or mic stream (Sherpa).
- **TTS Playground:** Text synthesis tester with voice selection, pitch/speed sliders.
- **LLM Playground:** Direct prompt engineering tester for LocalAI models.
- **Voices:** Grid of available Kokoro voices with instant one-click preview buttons.
- **Models:** Unified catalog of installed STT, LLM, and TTS models with installation progress.
- **Agents:** Create and customize personality prompts, default voices, and VAD thresholds.
- **Developers (Overview, Keys, Usage):** System health, RAM/VRAM gauges, API key management, and usage telemetry.

## 6.2 Assistant states

```text
idle -> listening -> transcribing -> thinking -> speaking
```

The interrupt (barge-in) button works while **speaking**.

## 6.3 Audio pipeline

1. Mic capture with echo cancellation, noise suppression, and auto gain.
2. Web Audio Resampler converts input to **16,000 Hz mono PCM16**.
3. Silero VAD detects speech in WebAssembly (`@ricky0123/vad-web`).
4. PCM frames stream over WebSocket.
5. Incoming 24 kHz audio chunks queue and play sequentially.

## 6.4 Frontend rules (do and do not)

**Do**
- Use relative URLs (`getApiBase()`).
- Derive WebSocket URLs from `location.host` (`getWsBase()`).
- Use single-use tickets for WebSockets.
- Store the admin key in `sessionStorage` or memory.

**Do not**
- Hardcode `localhost` or `:8000`.
- Put API keys in URLs.
- Use `localStorage` for the admin key.
- Ship sourcemaps in production (`build: { sourcemap: false }`).

## 6.5 Hosting at `/voicecontrolpanel/`

- Configured with `base: '/voicecontrolpanel/'` in `vite.config.ts`.
- Built via `npm run build` and served as static files by Caddy.
- Protected behind Caddy HTTP `basic_auth`.

---

# 7. The Engines (3 minutes)

> **TL;DR:** Three native AI engines running without Docker. LocalAI (:8080) handles LLM generation and batch Whisper STT. local-tts (:8880) synthesizes speech with Kokoro at 24 kHz. Sherpa-ONNX (:8088) streams realtime partial transcription at 16 kHz. All engines bind to loopback 127.0.0.1.

## 7.1 LocalAI

- **Port:** `127.0.0.1:8080`
- **Underlying:** `llama.cpp` (LLM) and `whisper.cpp` (batch STT).
- **Auth:** Internal `LOCALAI_API_KEY` Bearer header.
- **Security:** Dangerous endpoints (`/models/apply`, `/backend/*`, `/system`) are blocked by the gateway.
- **Pre-warming:** The gateway lifespan pre-warms the default model at startup to prevent 502 cold-start delays.

## 7.2 local-tts

- **Port:** `127.0.0.1:8880`
- **Underlying:** Kokoro TTS (82M parameter model).
  - Apple Silicon macOS: Apple MLX.
  - Linux bare metal: PyTorch CUDA.
- **Audio Output:** **24,000 Hz (24 kHz) mono PCM16**.
- **Latency:** Synthesizes first chunk in ~80–140 ms.

## 7.3 Streaming STT

- **Port:** `127.0.0.1:8088`
- **Underlying:** Sherpa-ONNX Zipformer (`sherpa-onnx-streaming-zipformer-en-20M`).
- **Audio Input:** **16,000 Hz (16 kHz) mono PCM16**.
- **Protocol:** `WS /ws` streaming binary PCM $\rightarrow$ returns JSON partial text.
- **Precision:** `STREAMING_STT_PRECISION=fp32` (default), `int8`, `f16`.
- **Fallback:** Gateway automatically falls back to LocalAI batch STT if streaming connection drops.

---

# 8. Security (5 minutes)

> **TL;DR:** Hardened defense-in-depth across 5 layers. Strict gateway authentication with HMAC-SHA256 hashed keys. Internal engines locked down with loopback + `LOCALAI_API_KEY` + nftables UID isolation. Production failsafe refuses to start with default credentials.

## 8.1 Four rules

1. Only Caddy faces the internet.
2. Every `/v1` call needs a key and the right scope.
3. Admin routes need the admin key (plus basic_auth for the panel).
4. Engines have loopback isolation, `LOCALAI_API_KEY` Bearer auth, and nftables UID filtering.

## 8.2 Resolved findings (Audit checklist)

- [x] **SEC-01 (CORS):** Restricted to explicit origins in `CORS_ALLOWED_ORIGINS`.
- [x] **SEC-02 (Docs):** Disabled in production (`ENABLE_DOCS=false`).
- [x] **SEC-03 (Default Key):** Startup check refuses to boot in production with default secrets.
- [x] **SEC-04 (File Permissions):** `.env` and SQLite files set to `chmod 600`.
- [x] **F1 (/v1/voice/turn):** Enforces `require_scope("voice")` (HTTP 403 for non-voice keys).
- [x] **F2 (/metrics):** Blocked at Caddy proxy (HTTP 403).
- [x] **F3 (Query Keys):** Keys scrubbed from logs; WebSockets use single-use tickets.
- [x] **F4 (Multi-user Engine Access):** nftables drops engine connections from non-`voiceagent` UIDs.
- [x] **F5 (Memory State):** Architected as single-worker process model under systemd.
- [x] **T6 (Model Deletion):** Restricted strictly to admin role (`require_admin_key`).

---

# 9. Deploy to Hetzner (5 minutes)

> **TL;DR:** One automated bare-metal deployment script (`scripts/deploy-hetzner.sh`). Creates dedicated system user `voiceagent`, sets 600 DAC permissions, provisions Caddy with Let's Encrypt TLS, installs 5 systemd units + timers, configures UFW firewall and nftables.

## 9.1 Deploy checklist

- [ ] 1. Server updated, NVIDIA driver verified (`nvidia-smi`).
- [ ] 2. Run `sudo bash scripts/deploy-hetzner.sh`.
- [ ] 3. Set domain and `caddy hash-password` in `/etc/caddy/Caddyfile`.
- [ ] 4. Start units: `sudo systemctl enable --now voice-localai voice-tts voice-stt voice-gateway voice-cleanup.timer`.
- [ ] 5. Reload Caddy: `sudo systemctl reload caddy`.
- [ ] 6. Post-deployment verification passes (below).

## 9.2 Daily operational commands

| Task | Command |
|---|---|
| View Gateway Logs | `journalctl -u voice-gateway -f` |
| Check All Unit States | `systemctl status voice-gateway voice-localai voice-tts voice-stt` |
| Restart Gateway | `sudo systemctl restart voice-gateway` |
| Monitor GPU VRAM | `watch -n 1 nvidia-smi` |
| Check Port Loopback | `ss -tulpnH \| grep -E '8000\|8080\|8880\|8088'` |
| Manual DB Backup | `sudo -u voiceagent /opt/voice-agent/scripts/backup-db.sh` |
| Safe Cleanup Scan | `sudo -u voiceagent /opt/voice-agent/.venv/bin/python scripts/cleanup.py scan` |

## 9.3 Quick verification

- [ ] `ss -tulpnH` confirms ports 8000, 8080, 8880, 8088 listen **only** on `127.0.0.1`.
- [ ] External probes to ports 8000, 8080, 8880 time out.
- [ ] `/metrics` returns 403 Forbidden on the domain.
- [ ] `/health/live` returns 200 OK.
- [ ] Other system users cannot reach `127.0.0.1:8080` (dropped by nftables).
- [ ] `.env` and DB show `-rw-------`.
- [ ] Auth matrix script (`auth_matrix_probe.py`) passes all 27 tests.

---

# 10. Settings Cheat Sheet (2 minutes)

| Setting | Type | Default | Production Note |
|---|---|---|---|
| `ENVIRONMENT` | `string` | `"development"` | Set to `"production"` on server |
| `ADMIN_API_KEY` | `string` | `"dev-voice-key-12345"` | **Change in production** |
| `API_KEY_HASH_SECRET` | `string` | `"dev-secret-key..."` | **Back this up safely** |
| `LOCALAI_API_KEY` | `string` | `""` | Required for internal LocalAI auth |
| `ENABLE_DOCS` | `boolean` | `False` | Keep off in production |
| `LOG_TRANSCRIPTS` | `boolean` | `False` | Keep off for user privacy |
| `MAX_ACTIVE_STT_STREAMS` | `integer` | `5` | Maximum active STT streams |
| `MAX_ACTIVE_VOICE_TURNS` | `integer` | `5` | Maximum concurrent turns |
| `MAX_AUDIO_UPLOAD_MB` | `integer` | `20` | Upload size limit |
| `STREAMING_STT_PROVIDER` | `string` | `"cpu"` | Set to `"cuda"` on Linux server |
| `STREAMING_STT_PRECISION` | `string` | `"fp32"` | High-accuracy weights |

---

# 11. Testing (1 minute)

- **pytest:** Run complete suite with `.venv/bin/pytest tests/ -q` (**207 passed in ~30s**).
- **Auth Matrix Probe:** Run `.venv/bin/python scripts/auth_matrix_probe.py` to audit all 27 routes.
- **Safety Tests:** Run `.venv/bin/pytest tests/unit/test_cleanup_safety.py` to confirm zero protected files can be selected.
- **Before Release:** Verify 100% green tests and zero build errors before deploying.

---

# 12. Known Issues and Roadmap (2 minutes)

## 12.1 Known issues

- Whisper via LocalAI gives no partial text (handled natively by Sherpa-ONNX streaming).
- First request after cold start takes 3–8s (pre-warmed on gateway startup).
- Echo on laptop speakers (resolved by headphones or echo cancellation).
- Single worker architecture (required for memory tickets without Redis).

## 12.2 Roadmap

| Priority | Item | Impact |
|---|---|---|
| **High** | Linux CUDA Kokoro TTS engine parity | Zero API changes; native Linux synthesis |
| **High** | Dynamic multi-model streaming STT routing | Port multiplexing on loopback |
| **Medium** | Session history UI & audio export | Query existing SQLite tables |
| **Later** | Multi-node scaling with Redis and PostgreSQL | Distributed Stage 3 architecture |

---

# 13. Troubleshooting (1 minute)

| Symptom | Likely Cause | Try This |
|---|---|---|
| **502 on first turn** | Model still loading into VRAM | Wait 5–10s; check `journalctl -u voice-localai -f` |
| **Garbled or fast audio** | Wrong sample rate | Send **16 kHz in** (mic); expect **24 kHz out** (TTS) |
| **Empty live transcript** | Wrong sample format | Ensure audio is 16-bit signed PCM (`pcm_s16le`) |
| **Noise triggers LLM** | Noise gate too low | Raise `MIN_SPEECH_RMS` or enable Whisper VAD |
| **WebSocket closes at once (4401)** | Expired or reused ticket | Tickets expire after 60s; request fresh ticket |
| **WebSocket closes with 4403** | Missing scope | Add `voice_stream` or `stt_stream` scope to key |
| **401 Unauthorized everywhere** | Missing header or revoked key | Check `Authorization: Bearer <key>` |
| **507 Insufficient Storage** | Disk space below 5% | Run `python scripts/cleanup.py run` |

---

# Appendix A. Open Items Before Release (Resolved)

- [x] **Key hashing:** Confirmed **HMAC-SHA256** using `API_KEY_HASH_SECRET` in `app/core/security.py`.
- [x] **Route count:** Confirmed **42 total endpoints**; 27 critical production endpoints audited by probe script.
- [x] **Exact schemas:** Documented in [05. Data and Database](05-data-and-database.md).
- [x] **Frontend libraries:** React `18.3.1`, Vite `5.4.11`, TailwindCSS `3.4.15`, Silero VAD `@ricky0123/vad-web` `0.0.31`. Tab-state router in `App.tsx`.
- [x] **Concurrency limits:** Dual layer — global backend semaphore (max 5) + per-key cap (`BackendLimiter`).
- [x] **WebSocket close codes:** RFC 6455 custom application codes `4401` and `4403` reach clients directly.
- [x] **SQLite WAL on:** Confirmed `PRAGMA journal_mode = WAL;` active on database initialization.
- [x] **Transcript retention:** Ephemeral sessions auto-pruned after 24h by hygiene manager. `LOG_TRANSCRIPTS=false` prevents log leakage.
- [x] **Event payloads:** Confirmed Pydantic schemas in `app/schemas/events.py`.
- [x] **Dependency audit:** Python dependencies clean. Upstream dev-only esbuild notice documented.

---

# Appendix B. Glossary (one line each)

| Term | Meaning |
|---|---|
| **STT** | **Speech-to-Text:** Converting human vocal audio into machine text. |
| **TTS** | **Text-to-Speech:** Converting written text into natural synthesized speech. |
| **LLM** | **Large Language Model:** Neural network (e.g. Llama 3) generating conversation text. |
| **VAD** | **Voice Activity Detection:** Algorithm (e.g. Silero) detecting when someone is speaking. |
| **PCM16** | **Pulse-Code Modulation 16-bit:** Uncompressed digital audio (`pcm_s16le`). |
| **Barge-in** | Interrupting the voice assistant mid-sentence to cancel speech playback. |
| **Ticket** | Single-use 60-second pass for authenticating WebSockets without putting keys in URLs. |
| **Scope** | Granular permission attached to an API key (e.g. `llm`, `stt`, `tts`, `voice_stream`). |
| **Gateway** | The central FastAPI server managing routing, security, and pipeline orchestration. |
| **Zipformer** | Modern streaming neural network architecture in Sherpa-ONNX. |
| **WAL** | **Write-Ahead Logging:** High-concurrency SQLite storage mode. |
| **Quarantine** | Moving junk files to `.trash/<run-id>/` for 7 days before permanent deletion. |

---

# Appendix C. Build This Guide

Build Microsoft Word (`.docx`) or PDF format using Pandoc:

```bash
# 1. Build Word Document (.docx)
pandoc docs/quick-scan-guide/quick-scan-guide.md \
  -o docs/quick-scan-guide/Quick_Scan_Guide.docx \
  --toc --toc-depth=2

# 2. Build PDF via XeLaTeX
pandoc docs/quick-scan-guide/quick-scan-guide.md \
  -o docs/quick-scan-guide/Quick_Scan_Guide.pdf \
  --toc --toc-depth=2 \
  --pdf-engine=/Library/TeX/texbin/xelatex \
  -V geometry:margin=2.2cm -V fontsize=11pt -V colorlinks=true
```

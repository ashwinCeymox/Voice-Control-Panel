---
title: "Voice AI Platform: Technical Documentation"
subtitle: "Architecture, Data Model, Backend, Frontend, Security and Operations"
author: "[Author / Team name]"
date: "17 November 2026"
version: "0.9 (Draft for review)"
lang: en-GB
---

# Document Control

| Field | Value |
|---|---|
| Document title | Voice AI Platform: Technical Documentation |
| Version | 0.9 (Draft for review) |
| Date | 17 November 2026 |
| Status | Draft. Items marked **[Verify]** must be confirmed against the code before release |
| Owner | [Name] |
| Audience | Developers, DevOps / SRE, security reviewers, new team members |
| Related documents | Hetzner Deployment Guide, Control Panel Proxy Guide, Security Investigation Report |

## Revision History

| Version | Date | Author | Summary of change |
|---|---|---|---|
| 0.9 | 2026-11-17 | [Name] | First complete draft compiled from data packs B to G, the Hetzner guide and the MVP/UI planning notes |

## Reading Conventions

Every technical statement carries a confidence level so that readers know how far to trust it.

| Tag | Meaning |
|---|---|
| **[Confirmed]** | Observed directly in a data pack or the live system on 2026-11-16 |
| **[Planned]** | Agreed in the design notes but not yet verified as implemented |
| **[Verify]** | Inferred from context. Check the source code before relying on it |

In this document, "the gateway" means the FastAPI application, "engines" means LocalAI, local-tts and the streaming STT service, and "caller" means any client holding a non-admin API key.

> **Note.** The security report and the data packs disagree on some details (see Appendix B). This document follows the data packs, because they record direct observations.

\newpage

# 1. Introduction

## 1.1 Purpose

This document describes the Voice AI Platform end to end: what it does, how it is built, how its data is stored, how its interfaces behave, how it is secured and how it is deployed. It is the single reference for onboarding, maintenance and security review.

## 1.2 Product Summary

The Voice AI Platform is a **self-hosted voice AI service with a web control panel**. A FastAPI gateway chains three stages into one conversation turn:

1. **Speech-to-text (STT):** audio in, transcript out.
2. **Large language model (LLM):** transcript in, response text out.
3. **Text-to-speech (TTS):** response text in, audio out.

The gateway exposes these stages individually (OpenAI-compatible chat and transcription routes, an ElevenLabs-style TTS route) and combined (`/v1/voice/turn` and the `/v1/voice/stream` WebSocket). A React control panel provides playgrounds for each stage, a live voice assistant, an agent editor, a model gallery and administration screens.

## 1.3 Scope

| In scope | Out of scope |
|---|---|
| Gateway, engines, data storage, control panel | Third-party cloud providers |
| Security controls and deployment on Hetzner dedicated servers | Billing and payments |
| API key management, quotas, usage analytics | Mobile client applications |

## 1.4 Key Capabilities

- Real-time voice assistant with live transcript, streamed response text, streamed audio and interruption (barge-in).
- Non-real-time voice turn (record or upload audio, receive transcript, reply text and audio).
- Individual playgrounds for STT, LLM and TTS.
- Agents: saved bundles of system prompt, models, voice and VAD settings.
- Model gallery with install, progress and delete.
- API key lifecycle (create, scope, rate-limit, quota, rotate, revoke) and usage analytics.
- Optional streaming STT through a separate engine for live partial transcripts.

# 2. System Overview

## 2.1 Logical Architecture

```
 Browser (control panel)  API clients (scripts, apps)
          |                        |
          +-----------+------------+
                      |  HTTPS / WSS
              +-------v--------+
              | Caddy (TLS)    |  public: 80, 443
              +-------+--------+
                      |  127.0.0.1:8000
              +-------v--------+
              | FastAPI Gateway|  auth, scopes, quotas, orchestration
              +--+----+-----+--+
                 |    |     |
     127.0.0.1:8080  :8880  :8088
          +------+  +---+--+  +---+---+
          |LocalAI| |local-|  |Stream-|
          |LLM+STT| | tts  |  |ing STT|
          +-------+ +------+  +-------+
                      |
              SQLite (voice_platform.db)
```

## 2.2 Component Inventory

| Component | Port | Bind address | Role | Auth | Status |
|---|---|---|---|---|---|
| Caddy / Nginx | 80, 443 | public | TLS, routing, security headers | n/a | **[Planned]** (not installed on dev) |
| Gateway (uvicorn) | 8000 | 127.0.0.1 | Public API, orchestration | API keys | **[Confirmed]** |
| LocalAI | 8080 | 127.0.0.1 | LLM inference, batch STT, model management | Optional `LOCALAI_API_KEY` | **[Confirmed]** |
| local-tts | 8880 | 127.0.0.1 | Speech synthesis (Kokoro and others) | None | **[Confirmed]** |
| Streaming STT | 8088 | 127.0.0.1 | Live partial transcripts (Sherpa-ONNX) | None | **[Confirmed]** |
| LocalAI whisper backend | random high port | 127.0.0.1 | Internal to LocalAI | None | **[Confirmed]** |
| LocalAI llama.cpp backend | random high port | 127.0.0.1 | Internal to LocalAI | None | **[Confirmed]** |
| Vite dev server | 5173 | 127.0.0.1 | Development only | n/a | **[Confirmed]** |

## 2.3 Voice Turn Flow (Batch)

```
Client --audio--> Gateway --WAV--> LocalAI STT --text--> Gateway
Gateway --gates (noise, junk, repeat)--> LocalAI LLM --text--> Gateway
Gateway --phrases--> local-tts --PCM--> Gateway --PCM--> Client
```

The gateway adds what the engines do not provide: authentication, history (last 30 messages), agent settings, noise and junk-transcript gates, the "please repeat" reply, phrase chunking for speech, cancellation, the event protocol and combined health. **[Confirmed]**

## 2.4 Voice Turn Flow (Real-Time)

1. Browser requests a one-time ticket from `POST /v1/auth/ticket`.
2. Browser opens `WS /v1/voice/stream` with the ticket.
3. Browser runs voice activity detection (VAD) locally and streams audio after `vad.speech_start`.
4. Gateway returns `transcript.partial` events (streaming mode) and `transcript.final`, then `response.text.delta` events and PCM audio chunks.
5. The client may send `turn.cancel` to interrupt. The gateway stops the LLM and TTS stages.

## 2.5 Audio Formats

| Direction | Format | Notes |
|---|---|---|
| Browser to streaming STT | 16 kHz, mono, PCM 16-bit | 512-sample frames from the VAD **[Confirmed]** |
| TTS output | 24 kHz, mono, PCM 16-bit | ElevenLabs-style raw PCM **[Confirmed]** |
| Batch STT upload | WAV or common audio, max 20 MB | `MAX_AUDIO_UPLOAD_MB` **[Confirmed]** |

# 3. Technology Stack and Libraries

> Versions below are the ones named in the project notes. Confirm exact pins from `requirements.txt` and `package.json` before release.

## 3.1 Backend

| Library / tool | Purpose | Version | Status |
|---|---|---|---|
| Python | Runtime | 3.x **[Verify]** | |
| FastAPI | Web framework, routing, dependency injection, WebSockets | 0.115+ | Claimed in report **[Verify]** |
| Pydantic | Request and response schemas | 2.8+ | Claimed in report **[Verify]** |
| Uvicorn | ASGI server | **[Verify]** | **[Confirmed]** in use |
| SQLite | Embedded database | stdlib | **[Confirmed]** |
| HTTP client (httpx) | Calls to engines | **[Verify]** | |
| `hmac`, `hashlib` | Key hashing, constant-time compare | stdlib | **[Confirmed]** |
| Prometheus client | `/metrics` | **[Verify]** | |

## 3.2 Inference Engines

| Engine | Technology | Notes |
|---|---|---|
| LocalAI | Go server with llama.cpp (LLM) and whisper.cpp (STT) backends | OpenAI-compatible API, gallery install jobs **[Confirmed]** |
| local-tts | Kokoro (MLX on Apple Silicon); other engines Pocket, Kitten, Qwen | MLX is macOS only. Linux needs kokoro-onnx or PyTorch Kokoro **[Planned]** |
| Streaming STT | Sherpa-ONNX streaming server | Real partial transcripts, several clients **[Confirmed]** |

## 3.3 Frontend

| Library / tool | Purpose | Status |
|---|---|---|
| React with TypeScript | UI (`Assistant.tsx`, `vadController.ts` referenced in notes) | **[Confirmed]** |
| Vite | Dev server and production bundler | **[Confirmed]** |
| Silero VAD (`vad-web`) | In-browser speech detection | **[Confirmed]** |
| Web Audio API | Mic capture, PCM playback queue, level meters | **[Confirmed]** |
| Router library | Client-side routing | **[Verify]** |
| Styling (CSS framework) | **[Verify]** | |
| Optional RNNoise worklet | Noise suppression toggle | **[Planned]** |

## 3.4 Infrastructure and Tooling

| Tool | Purpose |
|---|---|
| Caddy (preferred) or Nginx | Reverse proxy, automatic TLS |
| systemd | Process supervision on Linux |
| UFW and nftables | Network firewall and local-user isolation |
| fail2ban | Brute-force banning |
| pytest | Automated tests |
| Shell scripts | Start, stop, backup, restore |

## 3.5 Development vs Production Environments

| Aspect | Development (current) | Production (target) |
|---|---|---|
| Host | Apple M2, macOS | Hetzner dedicated server, Ubuntu 24.04 or Debian 12, NVIDIA GPU |
| Process control | Shell scripts | systemd |
| Gateway flags | `--reload` enabled | No reload, one worker until state moves to Redis |
| Frontend | Vite dev server (5173) | Static build served by Caddy |
| TLS | None (plain HTTP/WS) | Automatic Let's Encrypt |
| User | Desktop user (uid 501) | Dedicated `voiceagent` system user |
| TTS engine | Kokoro MLX | Linux-capable engine **[Planned]** |

# 4. Repository Structure

The tree below is reconstructed from file paths mentioned in the project notes. **[Verify]** against the actual repository.

```
local-voice-agent/
|-- app/
|   |-- main.py              # FastAPI app factory, middleware, routers
|   |-- api/                 # Route modules (thin: validate, call service)
|   |-- services/            # Business rules (voice turn, models, keys)
|   |-- providers/           # Engine adapters (LocalAI, local-tts, streaming STT)
|   |-- schemas/             # Pydantic models, events.py (WebSocket protocol)
|   |-- storage/database.py  # SQLite access (parameterised queries)
|   `-- core/                # config.py, errors.py, auth, logging
|-- services/streaming_stt/server.py   # Streaming STT microservice
|-- frontend/                # React + Vite control panel
|-- scripts/                 # start/stop engines, backup/restore, auth probe
|-- tests/unit/              # pytest suites (e.g. test_mid_fix_s1_to_s4.py)
|-- models/streaming-stt/    # STT model files
|-- data/                    # Runtime data and data/backups/
|-- voice_platform.db        # SQLite database
`-- .env                     # Secrets (git-ignored)
```

# 5. Backend

## 5.1 Layered Design

Every endpoint follows the same four steps **[Confirmed]**:

1. **Route** (`app/api/`): receives the request and validates it against a schema from `app/schemas/`.
2. **Service** (`app/services/`): applies business rules.
3. **Provider** (`app/providers/`): translates the request into the engine's format and the reply back.
4. **Error mapping** (`app/core/errors.py`): engine failures become gateway errors (502 unreachable, 504 timeout, 404 model missing).

To add an endpoint: create the route file, define request and response schemas, call a service, register the router, add a row to the authentication matrix and a test.

## 5.2 Authentication and Authorisation

| Item | Behaviour |
|---|---|
| Header formats | `Authorization: Bearer <key>` or `xi-api-key: <key>` |
| Key storage | Hash in SQLite `api_keys` (data pack: HMAC-SHA256 with `API_KEY_HASH_SECRET`; see Appendix B) |
| Comparison | Constant time (`hmac.compare_digest`) |
| Roles | `admin` and `caller` |
| Scopes | `llm`, `stt`, `tts`, `voice_stream`, `stt_stream`, `voice` (JSON array on the key) |
| Dependencies | `require_api_key` (any valid key), `_scope_checker('<scope>')`, `require_admin_key` |
| Admin default | `ADMIN_API_KEY` falls back to `dev-voice-key-12345` if unset. **Production must set it** |
| WebSocket auth | One-time ticket (60 s, in memory), or Bearer header, or `?api_key=` query parameter |
| Revocation | Immediate (`is_active = 0`) |
| Rotation | `POST /v1/admin/keys/{id}/rotate` |

## 5.3 REST and WebSocket API Reference

Source: route inventory extracted from the live application on 2026-11-16 **[Confirmed]**. Where an item is marked as a known issue, see section 13.

### 5.3.1 Public and Health

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health/live` | None | Liveness (`{"status":"live"}`) |
| GET | `/health/ready` | None | Readiness with sanitised provider status |
| GET | `/` | None | Welcome |
| GET | `/metrics` | None | Prometheus metrics. **Block at the proxy** |

### 5.3.2 Inference (Caller Scopes)

| Method | Path | Scope | Description |
|---|---|---|---|
| POST | `/v1/chat/completions` | `llm` | OpenAI-compatible chat |
| POST | `/v1/audio/transcriptions` | `stt` | Batch transcription |
| POST | `/v1/text-to-speech/{voice_id}/stream` | `tts` | Streamed PCM speech |
| GET | `/v1/voices` | `tts` | List voices |
| POST | `/v1/voices/{id}/preview` | `tts` | Synthesise a sample |
| GET | `/v1/tts/models` | `tts` | List TTS engines |
| POST | `/v1/voice/turn` | any valid key | Full audio-in, audio-out turn. **No scope check (known issue)** |

### 5.3.3 Discovery and System

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/v1/providers` | any key | Providers and defaults |
| GET | `/v1/models` | any key | Unified model registry |
| GET | `/v1/models/installed` | any key | Installed models |
| GET | `/v1/gallery/models` | any key | Installable models |
| GET | `/v1/system/resources` | any key | RAM and VRAM telemetry |
| POST | `/v1/auth/ticket` | any key | Single-use WebSocket ticket (60 s) |

### 5.3.4 Model Management

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/v1/models/install` | admin | Start an install job |
| GET | `/v1/models/install/{job_id}` | any key | Install progress |
| DELETE | `/v1/models/{id}` | key (should be admin) | Delete model. **Known issue T6** |
| PATCH | `/v1/models/{id}/classification` | key (should be admin) | Reclassify model. **Known issue T6** |
| PUT | `/v1/voices/{id}/meta` | admin | Update voice metadata |

### 5.3.5 Administration

| Method | Path | Auth | Description |
|---|---|---|---|
| GET, POST | `/v1/agents` | admin | List and create agents |
| GET, PUT, DELETE | `/v1/agents/{id}` | admin | Read, update, delete agent |
| GET, POST, PATCH, DELETE | `/v1/admin/keys` | admin | Key lifecycle and quotas |
| POST | `/v1/admin/keys/{id}/rotate` | admin | Rotate key **[Verify]** path |
| GET | `/v1/admin/usage` | admin | Usage analytics |
| GET | `/v1/admin/residency` | admin | Models resident in memory |
| POST | `/v1/admin/residency/{id}/flush` | admin | Unload a model |

### 5.3.6 WebSockets

| Path | Auth | Purpose |
|---|---|---|
| `/v1/voice/stream` | Ticket, Bearer or query key | Real-time voice assistant |
| `/v1/audio/transcriptions/stream` | Ticket, Bearer or query key | Streaming transcription |

### 5.3.7 Event Protocol (Voice Stream)

Event names seen in the design notes. Exact payload shapes live in `app/schemas/events.py` **[Verify]**.

| Direction | Event | Meaning |
|---|---|---|
| Client to server | `vad.speech_start` | Speech began; open the stream |
| Client to server | binary frames | PCM16 audio, 16 kHz mono |
| Client to server | `audio.commit` | End of utterance |
| Client to server | `turn.cancel` | Interrupt the assistant |
| Server to client | `stream.start` | Streaming STT session opened |
| Server to client | `transcript.partial` | Interim text (`seq`, confirmed, tentative) |
| Server to client | `transcript.final` / `transcript.completed` | Final transcript |
| Server to client | `response.text.delta` | Streaming LLM text |
| Server to client | audio chunks | 24 kHz PCM from TTS |
| Server to client | `error` | Failure with `request_id` |

## 5.4 Voice Pipeline Rules

- **History:** last 30 messages per conversation **[Confirmed]**.
- **Gates:** RMS energy gate, junk or hallucinated transcripts (for example text Whisper invents on silence) are dropped, and a "please repeat" reply is used when nothing usable was heard. Thresholds are configurable **[Verify]**.
- **STT mode per agent:** `batch` (default) or `stream`. If the streaming service fails, the gateway falls back to batch STT and announces it **[Planned]**.
- **Phrase chunking:** LLM text is cut into phrases so TTS can start before the LLM finishes.
- **Cancellation:** `turn.cancel` aborts in-flight LLM and TTS work.

## 5.5 Limits and Quotas

| Control | Value | Notes |
|---|---|---|
| `MAX_AUDIO_UPLOAD_MB` | 20 | Upload limit |
| `MAX_ACTIVE_STT_STREAMS` | 5 | Appears global **[Verify]** |
| `MAX_ACTIVE_VOICE_TURNS` | 1 | Appears global **[Verify]** |
| Per-key rate limit | `rate_limit_per_minute` | `KeyManager.check_quota` |
| Per-key quota | `total_quota`, `used_quota` | Enforced per request |
| Key expiry | `expires_at` | Epoch or NULL |

## 5.6 Error Handling

| Condition | Response |
|---|---|
| Missing or bad key | 401 |
| Wrong scope or role | 403 |
| Engine unreachable | 502 |
| Engine timeout | 504 |
| Model not found | 404 |
| Quota or rate exceeded | 429 |

Errors include a `request_id` so that a user report can be matched to a log line.

## 5.7 Observability

- `/health/live` and `/health/ready` for probes.
- `/metrics` for Prometheus (internal only).
- Structured logs. `LOG_TRANSCRIPTS` defaults to `False`, and keys are redacted from application logs.

# 6. Data Model

## 6.1 Storage Overview

| Store | Technology | Contents |
|---|---|---|
| Primary database | SQLite file `voice_platform.db` | Keys, agents, turns, installed models, usage |
| Model files | Disk (`LocalAI/models/`, `models/streaming-stt/`) | Weights |
| WebSocket tickets | Process memory | Single-use, 60 s |
| Concurrency counters | Process memory | Active streams and turns **[Verify]** |
| Backups | `data/backups/` | Timestamped `.backup` copies |

## 6.2 Entity Relationship Overview

```
api_keys 1---* usage records     agents 1---* sessions/turns
installed_models (catalogue)      turns store transcript + reply
```

Relationships are inferred **[Verify]**.

## 6.3 Table: `api_keys` **[Confirmed]**

| Column | Type | Description |
|---|---|---|
| `id` | text / integer | Key identifier |
| `key_hash` | text | HMAC-SHA256 of the key with `API_KEY_HASH_SECRET` |
| `role` | text | `admin` or `caller` |
| `scopes` | JSON text | Array such as `["stt","llm","tts","voice_stream"]` |
| `rate_limit_per_minute` | integer | Per-key rate limit |
| `total_quota` | integer | Allowed usage |
| `used_quota` | integer | Usage so far |
| `expires_at` | integer | Epoch seconds or NULL |
| `is_active` | integer | 1 active, 0 revoked |

## 6.4 Table: `agents` **[Planned]**

| Field | Description |
|---|---|
| `id`, `name` | Identity |
| `system_prompt` | Instructions for the LLM |
| `llm_model`, `stt_model`, `tts_engine`, `voice` | Model selection |
| `stt_mode` | `batch` or `stream`, plus streaming model |
| VAD settings | Threshold, silence duration |
| `temperature`, `language` | Generation and language |
| `last_used` / version | Optional |

The exact column list must be confirmed from `app/storage/database.py` **[Verify]**.

## 6.5 Table: `turns` **[Confirmed that it exists]**

Stores transcripts of conversation turns. Likely columns: id, session or agent reference, key id, user transcript, reply text, request_id, timings, created_at **[Verify]**. Raw audio is processed in memory and discarded unless explicitly logged.

## 6.6 Table: `installed_models` **[Confirmed that it exists]**

Records models merged from LocalAI, local-tts and the streaming STT list, with their classification (LLM, STT, TTS) **[Verify]** columns.

## 6.7 Other Data **[Verify]**

Usage analytics (`/v1/admin/usage`) imply a usage table or an aggregation over `turns`. Voice metadata overrides (`PUT /v1/voices/{id}/meta`) imply a metadata table or JSON column.

## 6.8 Retention and Privacy

- Raw audio is not stored by default.
- Transcripts are not logged by default (`LOG_TRANSCRIPTS = False`), but turn transcripts are stored in `turns`. Define a retention period and a delete path **[Open item]**.
- Speaker identification, if added, creates biometric data and requires consent.

## 6.9 Backup and Recovery

| Item | Detail |
|---|---|
| Backup | `scripts/backup-db.sh` creates a timestamped SQLite `.backup` in `data/backups/` |
| Restore | `scripts/restore-db.sh` restores atomically |
| Test | Covered by `test_m2_backup.py` |
| Recommendation | Nightly backup, 14-day retention, off-server copy, quarterly restore drill |
| WAL mode | Notes contradict each other on whether WAL is on. Confirm and enable **[Verify]** |

# 7. Frontend (Control Panel)

## 7.1 Purpose and Design Principles

A self-hosted control panel for operating and testing the voice stack. Design principles from the planning notes:

- Show **stage latency** (STT, LLM, TTS) wherever possible.
- **Degrade gracefully** when a provider is down. Status pills must reflect real health.
- Surface errors with their `request_id`.
- Keep real-time state clear with an explicit state machine.

## 7.2 Navigation and Pages

Sidebar navigation **[Confirmed in the plan]**.

| Page | Purpose | Backend calls |
|---|---|---|
| Assistant | Live voice conversation with an animated orb | `/v1/auth/ticket`, `WS /v1/voice/stream` |
| Voice turn | Record or upload, play the result | `POST /v1/voice/turn` |
| STT playground | Upload audio, see transcript and language | `/v1/audio/transcriptions` |
| LLM playground | Chat with streaming and parameters | `/v1/chat/completions` |
| TTS playground | Text, voice settings, playback | `/v1/text-to-speech/{id}/stream` |
| Voices | Card grid with preview | `/v1/voices`, `/v1/voices/{id}/preview` |
| Models | Gallery, install progress, delete, memory fit | `/v1/gallery/models`, `/v1/models/*` |
| Agents | Create, edit, duplicate, load, delete | `/v1/agents/*` |
| System | Provider health, CPU, RAM, VRAM | `/health/ready`, `/v1/system/resources` |
| API keys and usage | Table with create, revoke, rotate and charts | `/v1/admin/keys`, `/v1/admin/usage` |

## 7.3 Interaction Patterns

| Data | Pattern | Reason |
|---|---|---|
| Conversation | Chat timeline with bottom mic bar | Turns are sequential |
| Provider health | Status pills (green, amber, red) | Glanceable |
| Models and engines | Searchable select | Few items |
| Voices | Card grid with preview | Users must hear before choosing |
| Playgrounds | Two panes: inputs left, output right | Test-tweak-retest loop |
| Settings | Sectioned form | Four to five fields |
| Keys and usage | Data table with row actions | The one place a grid fits |

## 7.4 Assistant State Machine

```
idle -> listening -> transcribing -> thinking -> speaking -> idle
                          ^ interrupt available while "speaking"
```

The orb animation reflects each state and uses live audio levels for mic input and TTS output. It must respect the reduced-motion setting.

## 7.5 Audio Pipeline

| Stage | Component |
|---|---|
| Capture | `getUserMedia` with echo cancellation and noise suppression where supported |
| VAD | Silero VAD in the browser (`vadController.ts`), with a short pre-speech ring buffer |
| Encode | Float samples converted to PCM16 before sending |
| Playback | PCM player queue (24 kHz) with level metering |
| Barge-in | Starting to speak cancels playback and sends `turn.cancel` |

Echo on laptop speakers is a known issue unless echo cancellation is active or headphones are used.

## 7.6 API Access Conventions

- Use relative URLs (`/v1/...`) so the same build works on any host.
- Build the WebSocket URL from `location.host` and `wss` on HTTPS.
- Authenticate WebSockets with tickets from `/v1/auth/ticket`, never with keys in URLs.
- Keep the admin key in `sessionStorage` or memory, never `localStorage`.

## 7.7 Build and Sub-Path Hosting

Production is served at `https://xyz.sdjfj.app/voicecontrolpanel/`.

- `vite.config.ts`: `base: '/voicecontrolpanel/'`, source maps off.
- Router: `basename={import.meta.env.BASE_URL}`.
- Build with `npm ci && npm run build` and copy `dist/` to the server.

# 8. Engines in Detail

## 8.1 LocalAI

| Item | Detail |
|---|---|
| Used for | LLM chat, batch STT, model gallery and install jobs, readiness |
| Calls made by the gateway | `/v1/chat/completions`, `/v1/audio/transcriptions`, `/models/apply`, `/models/jobs/{id}`, `/models/delete/{name}`, `/v1/models`, `/readyz` |
| Not used | LocalAI's own TTS, VAD, realtime, authentication, audio-transform features |
| Limitations | Whisper backend returns no partial text. Requests can queue on one thread. Cold start caused early 502 errors |
| Dangerous endpoints | `/models/apply`, `/models/delete/*`, `/models/import`, `/backend/*`, `/system`, `/metrics`, `/api/traces`, `/api/p2p/token`. **Never reachable by clients** |

## 8.2 local-tts

| Item | Detail |
|---|---|
| Endpoints | `GET /v1/models`, `GET /v1/voices`, `POST /v1/text-to-speech/{voice_id}/stream`, `WS .../stream-input` |
| Audio | 24 kHz, 16-bit, mono PCM, ElevenLabs-style |
| Engines | Kokoro, Pocket, Kitten, Qwen |
| Voice metadata | Kokoro IDs encode gender in the prefix. Other engines may not, so a fallback ("Unknown") and manual override are needed |
| Portability | Kokoro MLX is Apple only |

## 8.3 Streaming STT (Sherpa-ONNX)

| Item | Detail |
|---|---|
| Endpoints | `GET /health`, `GET /v1/models`, `WS /ws` |
| Input | 16 kHz mono PCM16 frames |
| Output | JSON with `text` and a `segment` number. A new segment number means the earlier segment is final **[Verify]** |
| Model files | `models/streaming-stt/` |
| Role in pipeline | Produces `transcript.partial`. The final transcript still passes through the same gates |

# 9. Security

## 9.1 Security Architecture Summary

1. Only Caddy listens publicly (80, 443). Everything else binds to `127.0.0.1`.
2. The gateway authenticates every `/v1` call with an API key and checks scopes.
3. Admin routes require the admin key and, for the control panel, a second layer at the proxy.
4. Engines hold no authentication of their own, so isolation depends on loopback binding, local-user rules and `LOCALAI_API_KEY`.

## 9.2 Controls

| Control | Status |
|---|---|
| Loopback-only engine binding | **[Confirmed]** on dev |
| TLS with auto-certificates | **[Planned]** |
| Constant-time key comparison, hashed key storage | **[Confirmed]** |
| Parameterised SQL queries only | **[Confirmed]** |
| Model-ID validation (`^[a-zA-Z0-9][a-zA-Z0-9_\-\.:]*$`, rejects `..`, `/`, `\`) | **[Confirmed]** |
| CORS restricted to explicit origins | **[Confirmed]** |
| API docs disabled (`ENABLE_DOCS = False`) | **[Confirmed]** |
| Transcript logging off by default | **[Confirmed]** |
| File permissions 600 for `.env` and database | **[Planned]** (dev shows 644) |
| Dedicated `voiceagent` user and systemd sandbox | **[Planned]** |
| Firewall, local-user isolation, fail2ban | **[Planned]** |
| Proxy security headers, CSP, Origin check, basic auth on panel | **[Planned]** |

## 9.3 Known Findings

| ID | Finding | Severity | State |
|---|---|---|---|
| T6 | Caller keys could delete or reclassify models | High | Fix reported. Re-verify in production |
| SEC-03 | Default admin key `dev-voice-key-12345` | High | Make startup fail when unset or default **[Open]** |
| SEC-04 | `.env` and DB world-readable (644) | Medium | Set to 600 on deploy |
| F1 | `/v1/voice/turn` lacks a scope check | Medium | **[Open]** |
| F2 | `/metrics` public at the application | Medium | Block at proxy (Nginx sample forwards it) |
| F3 | `?api_key=` on WebSockets appears in proxy logs | Medium | Scrub in proxy log filter, prefer tickets |
| F4 | Engines reachable by other local users | Medium | `LOCALAI_API_KEY` and nftables rules |
| F5 | In-memory tickets and limits break with several workers | Medium | One worker now, Redis later |

## 9.4 Operational Security Rules

- Generate production secrets with `openssl rand -hex 32`. Never reuse development keys.
- Back up `API_KEY_HASH_SECRET`. Losing it invalidates every issued key.
- Share keys through a password manager, rotate on staff change.
- Keep `/metrics` internal. Keep `ENABLE_DOCS=false`.

# 10. Deployment and Operations

## 10.1 Target Topology (Single Server)

```
Internet -> UFW (22, 80, 443) -> Caddy -> Gateway -> engines (loopback)
```

Detailed steps live in the Hetzner Deployment Guide and Control Panel Proxy Guide. Summary:

1. Prepare server, drivers, `voiceagent` user.
2. Copy code, create venv, install requirements, build the frontend.
3. Create `.env` with production secrets, set permissions.
4. Install systemd units for LocalAI, local-tts, streaming STT and the gateway.
5. Configure Caddy, firewall rules and fail2ban.
6. Run the verification checklist, then issue keys.

## 10.2 Service Management

| Task | Command |
|---|---|
| Start all | `sudo systemctl enable --now voice-localai voice-tts voice-stt voice-gateway` |
| Logs | `journalctl -u voice-gateway -f` |
| Restart gateway | `sudo systemctl restart voice-gateway` |
| GPU view | `watch -n 1 nvidia-smi` |

## 10.3 Capacity and Scaling

| Stage | Description | Code change |
|---|---|---|
| 1 | Single server, one gateway worker | None |
| 2 | Bigger GPU and memory, tuned limits | Config only |
| 3 | Several gateways and GPU nodes | Redis (tickets, limits), PostgreSQL, per-key limits, configurable engine URLs |

Capacity is bound by GPU memory and concurrent inference. STT through Whisper can queue, so the gateway needs a per-key concurrency limit, a queue with timeout and 429 responses.

## 10.4 Monitoring

Scrape `/metrics` from the host or a private network only. Alert on VRAM use, 5xx rate, p95 latency and engine health.

# 11. Configuration Reference

| Setting | Default | Description |
|---|---|---|
| `ADMIN_API_KEY` | `dev-voice-key-12345` | Admin key. **Must be changed** |
| `API_KEY_HASH_SECRET` | none | Secret for hashing keys |
| `LOCALAI_API_KEY` | optional | Key the gateway sends to LocalAI |
| `CORS_ALLOWED_ORIGINS` | localhost:5173 pair | Allowed browser origins |
| `ENABLE_DOCS` | `False` | Enables `/docs`, `/redoc`, `/openapi.json` |
| `LOG_TRANSCRIPTS` | `False` | Logs transcripts |
| `MAX_AUDIO_UPLOAD_MB` | 20 | Upload limit |
| `MAX_ACTIVE_STT_STREAMS` | 5 | Concurrent streaming sessions |
| `MAX_ACTIVE_VOICE_TURNS` | 1 | Concurrent voice turns |
| `CUDA_VISIBLE_DEVICES` | unset | GPU selection |

Settings are defined in `app/core/config.py` **[Confirmed path]**. Startup models in the pre-warm step are partly hard-coded and should be made configurable **[Verify]**.

# 12. Testing and Quality Assurance

| Area | Approach |
|---|---|
| Unit tests | pytest in `tests/unit/` (path validation, backup, auth) |
| Authentication matrix | `scripts/auth_matrix_probe.py` calls every route with several key profiles |
| Streaming STT | Replay script plays recorded WAVs at real-time speed |
| Latency | Per-stage timings (STT, LLM, TTS) captured in turns |
| Pre-release | Run the matrix and pytest against the deployed server |

# 13. Known Issues and Roadmap

## 13.1 Known Issues

1. Whisper through LocalAI returns no partial transcripts.
2. First-request cold start can cause 502.
3. Echo and barge-in on laptop speakers.
4. Apple-only components (Metal, Kokoro MLX) block a straight move to Linux.
5. Security findings F1 to F5 and SEC-03 in section 9.3.

## 13.2 Roadmap

| Priority | Item |
|---|---|
| High | Close findings F1 to F5, SEC-03 |
| High | Linux TTS engine decision and test |
| Medium | Streaming STT in production with batch fallback |
| Medium | Session history and export |
| Medium | Per-key concurrency, queueing and 429 behaviour |
| Later | PostgreSQL and Redis for horizontal scale |
| Later | Server-side VAD for API clients |

# 14. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| 502 on first request | LocalAI still loading model | Wait, pre-warm models |
| Garbled or fast audio | Wrong sample rate or double resampling | Check 24 kHz playback and 16 kHz capture |
| Streaming transcript empty | Wrong sample format or byte order | Save the incoming PCM to WAV and listen |
| Noise passes to the LLM | Gate thresholds too low | Tune RMS gate and Whisper VAD |
| WebSocket closes immediately | Ticket expired or reused | Request a new ticket |
| 401 everywhere | Missing header or revoked key | Check `Authorization: Bearer` and key state |
| Engine port reachable externally | Wrong bind address | Fix startup flag to `127.0.0.1`, recheck with `ss` |

# Appendix A. Glossary

| Term | Meaning |
|---|---|
| STT | Speech-to-text |
| TTS | Text-to-speech |
| LLM | Large language model |
| VAD | Voice activity detection |
| PCM16 | Uncompressed 16-bit audio samples |
| Barge-in | User interrupts the assistant while it speaks |
| Ticket | One-time, 60-second credential for opening a WebSocket |
| Scope | Permission attached to a key (for example `stt`) |
| Gateway | The FastAPI application |

# Appendix B. Open Items to Confirm Before Release

| # | Item | Why it matters |
|---|---|---|
| 1 | Key hashing: HMAC-SHA256 (data packs) vs PBKDF2-HMAC-SHA256 (report) | Documentation must match the code |
| 2 | Route count (30 in inventory, 28 in report) and routes missing from the auth matrix | Complete security coverage |
| 3 | Exact database schema for `turns`, `agents`, `installed_models`, usage | Section 6 accuracy |
| 4 | Frontend router, styling framework, state library, exact versions | Section 3.3 |
| 5 | Whether `MAX_ACTIVE_*` limits are global or per key | Capacity planning |
| 6 | Whether WebSocket close codes (4401, 4403) reach clients or are HTTP 403 | Client error handling |
| 7 | Whether SQLite WAL mode is enabled | Durability and concurrency |
| 8 | Transcript retention period and delete procedure | Privacy |
| 9 | Event payload schemas in `app/schemas/events.py` | Section 5.3.7 |
| 10 | Dependency audit output (`pip-audit`, `npm audit`) | CVE claim in report is unsupported |

# Appendix C. Building This Document

Do not use `--number-sections`, because headings are already numbered.

```bash
# Word
pandoc PROJECT_DOCUMENTATION.md -o Voice_AI_Platform_Docs.docx --toc --toc-depth=3

# PDF (needs a LaTeX engine such as xelatex)
pandoc PROJECT_DOCUMENTATION.md -o Voice_AI_Platform_Docs.pdf \
  --toc --toc-depth=3 --pdf-engine=xelatex \
  -V geometry:margin=2.2cm -V fontsize=10pt -V colorlinks=true
```

For a corporate look in Word, add `--reference-doc=template.docx`.

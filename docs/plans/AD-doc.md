---
title: "Voice AI Platform: Quick-Scan Guide"
subtitle: "The ADHD-friendly edition: short chunks, summaries first, checklists everywhere"
author: "[Author / Team name]"
date: "17 November 2026"
version: "0.9 (Draft for review)"
lang: en-GB
---

# Start Here (2 minutes)

> **TL;DR:** This is a self-hosted voice assistant. You talk, it listens, thinks and talks back. A Python gateway runs the show. A React control panel lets you test and manage it.

**How to use this guide**

- You do **not** need to read it top to bottom.
- Every section starts with a **TL;DR box**. Read only that if you are in a hurry.
- Use the **"I want to..."** table below to jump to what you need.
- Stuck? Go to section 13 (Troubleshooting).

**Reading time:** whole guide about 35 minutes. Each section 2 to 5 minutes.

## What the confidence tags mean

| Tag | Meaning | What to do |
|---|---|---|
| **[Confirmed]** | Seen in real data on 2026-11-16 | Trust it |
| **[Planned]** | Designed, not yet built or deployed | Treat as a to-do |
| **[Verify]** | Educated guess | Check the code first |

## I want to... (jump table)

| I want to... | Go to |
|---|---|
| Understand the big picture | Section 1 |
| Know which libraries we use | Section 2 |
| Find a file or folder | Section 3 |
| Call or change an API | Section 4 |
| See what is in the database | Section 5 |
| Change the control panel | Section 6 |
| Work on STT, LLM or TTS engines | Section 7 |
| Check security | Section 8 |
| Deploy to the server | Section 9 |
| Look up a setting | Section 10 |
| Know what is broken | Section 12 |
| Fix an error right now | Section 13 |

# 1. The Big Picture (3 minutes)

> **TL;DR:** One public door (Caddy). One brain (the gateway). Three helpers (LocalAI, TTS, streaming STT). One database (SQLite).

## 1.1 What it does

A voice turn has three steps:

1. **Listen** (speech to text, "STT")
2. **Think** (the language model, "LLM")
3. **Speak** (text to speech, "TTS")

The gateway chains them together and adds login, limits and history.

## 1.2 The picture

```
 Browser / API clients
          |
       Caddy (HTTPS)        <- only public door
          |
       Gateway :8000        <- the brain
      /     |      \
 LocalAI  local-tts  streaming STT
  :8080     :8880       :8088
          |
   SQLite database
```

## 1.3 The parts at a glance

| Part | Port | Job |
|---|---|---|
| Caddy | 80, 443 | HTTPS and routing **[Planned]** |
| Gateway | 8000 | API, login, orchestration **[Confirmed]** |
| LocalAI | 8080 | LLM and batch STT **[Confirmed]** |
| local-tts | 8880 | Speech output **[Confirmed]** |
| Streaming STT | 8088 | Live partial text **[Confirmed]** |

> **Rule to remember:** every internal port listens on `127.0.0.1` only. Nothing but Caddy faces the internet.

## 1.4 One voice turn, step by step

1. Browser asks for a one-time **ticket** (valid 60 seconds).
2. Browser opens the WebSocket with that ticket.
3. Browser detects speech and sends audio.
4. Gateway turns audio into text, asks the LLM, then turns the answer into audio.
5. Audio streams back. The user can **interrupt** any time.

## 1.5 Audio formats (easy to get wrong)

| Direction | Format |
|---|---|
| Mic to streaming STT | 16 kHz, mono, 16-bit PCM |
| TTS to speaker | 24 kHz, mono, 16-bit PCM |
| Upload limit | 20 MB |

> **Watch out:** wrong sample rate is the number one cause of garbled audio.

# 2. Libraries and Tools (3 minutes)

> **TL;DR:** Python + FastAPI + SQLite on the back. React + Vite + TypeScript on the front. Caddy and systemd in production.

## 2.1 Backend

| Tool | Why we use it |
|---|---|
| FastAPI (0.115+ claimed **[Verify]**) | Web framework, WebSockets |
| Pydantic (2.8+ claimed **[Verify]**) | Request and response shapes |
| Uvicorn | Runs the app |
| SQLite | Database in one file |
| `hmac` / `hashlib` | Hash keys safely |

## 2.2 Engines

| Engine | Built on |
|---|---|
| LocalAI | llama.cpp (LLM) and whisper.cpp (STT) |
| local-tts | Kokoro (Mac: MLX), plus Pocket, Kitten, Qwen |
| Streaming STT | Sherpa-ONNX |

> **Watch out:** Kokoro MLX only works on Apple Silicon. Linux needs another build **[Planned]**.

## 2.3 Frontend

| Tool | Why |
|---|---|
| React + TypeScript | The UI **[Confirmed]** |
| Vite | Dev server and build **[Confirmed]** |
| Silero VAD (`vad-web`) | Detects speech in the browser |
| Web Audio API | Mic capture and playback |
| Router, CSS framework | **[Verify]** which ones |

## 2.4 Infrastructure

| Tool | Job |
|---|---|
| Caddy | HTTPS and reverse proxy |
| systemd | Keeps services running |
| UFW + nftables | Firewall |
| fail2ban | Bans repeat offenders |
| pytest | Tests |

## 2.5 Dev vs production

| | Dev now | Production target |
|---|---|---|
| Machine | Apple M2 Mac | Hetzner server with NVIDIA GPU |
| Run with | Shell scripts | systemd |
| Gateway | `--reload` on | No reload, 1 worker |
| Frontend | Vite dev server | Static build in Caddy |
| HTTPS | No | Yes, automatic |
| User | Your login (uid 501) | `voiceagent` |

# 3. Where Things Live (2 minutes)

> **TL;DR:** Routes in `app/api`, rules in `app/services`, engine adapters in `app/providers`, data shapes in `app/schemas`.

```
local-voice-agent/
  app/
    main.py        app starts here
    api/           routes (thin)
    services/      business rules
    providers/     talks to engines
    schemas/       data shapes + events.py
    storage/       database.py (SQLite)
    core/          config.py, errors.py
  services/streaming_stt/server.py
  frontend/        React control panel
  scripts/         start, stop, backup, restore
  tests/unit/      pytest tests
  data/backups/    DB backups
  .env             secrets (never commit)
```

> **Tag:** layout is rebuilt from file names in the notes **[Verify]**.

## 3.1 Adding a new endpoint (checklist)

- [ ] Create a route file in `app/api/`
- [ ] Define request and response schemas
- [ ] Call a service, not the engine directly
- [ ] Register the router
- [ ] Pick the right auth dependency
- [ ] Add the route to the auth matrix script
- [ ] Add a test

# 4. Backend and API (5 minutes)

> **TL;DR:** Four steps per request: route, service, provider, error mapping. Auth is by API key with scopes. 30 routes in total.

## 4.1 Request path

1. **Route** checks the input.
2. **Service** applies the rules.
3. **Provider** talks to the engine.
4. **Errors** are mapped to clean codes.

## 4.2 Login in 30 seconds

- Send `Authorization: Bearer <key>` or `xi-api-key: <key>`.
- Keys are stored as hashes **[Confirmed]**. Which hash exactly is open (Appendix A).
- Two roles: `admin` and `caller`.
- Scopes limit callers: `llm`, `stt`, `tts`, `voice_stream`, `stt_stream`, `voice`.
- Revoked keys fail on the very next request.

> **Watch out:** if `ADMIN_API_KEY` is not set, it falls back to `dev-voice-key-12345`. Never ship that.

## 4.3 Routes cheat sheet

### Public (no key)

| Method | Path | Use |
|---|---|---|
| GET | `/health/live` | Is it up? |
| GET | `/health/ready` | Are providers ready? |
| GET | `/` | Welcome |
| GET | `/metrics` | Prometheus. **Block at proxy** |

### Caller routes (scope needed)

| Method | Path | Scope |
|---|---|---|
| POST | `/v1/chat/completions` | `llm` |
| POST | `/v1/audio/transcriptions` | `stt` |
| POST | `/v1/text-to-speech/{voice_id}/stream` | `tts` |
| GET | `/v1/voices` | `tts` |
| POST | `/v1/voices/{id}/preview` | `tts` |
| GET | `/v1/tts/models` | `tts` |
| POST | `/v1/voice/turn` | any key (**no scope check, bug**) |

### Any valid key

| Method | Path | Use |
|---|---|---|
| GET | `/v1/providers` | Providers |
| GET | `/v1/models` | Model list |
| GET | `/v1/models/installed` | Installed |
| GET | `/v1/gallery/models` | Installable |
| GET | `/v1/system/resources` | RAM and VRAM |
| POST | `/v1/auth/ticket` | WebSocket ticket |
| GET | `/v1/models/install/{job_id}` | Install progress |

### Admin only

| Method | Path | Use |
|---|---|---|
| POST | `/v1/models/install` | Install model |
| PUT | `/v1/voices/{id}/meta` | Voice info |
| GET, POST | `/v1/agents` | Agents |
| GET, PUT, DELETE | `/v1/agents/{id}` | One agent |
| GET, POST, PATCH, DELETE | `/v1/admin/keys` | API keys |
| POST | `/v1/admin/keys/{id}/rotate` | Rotate **[Verify]** |
| GET | `/v1/admin/usage` | Usage |
| GET | `/v1/admin/residency` | Models in memory |
| POST | `/v1/admin/residency/{id}/flush` | Unload model |

### Needs a fix (should be admin only)

| Method | Path |
|---|---|
| DELETE | `/v1/models/{id}` |
| PATCH | `/v1/models/{id}/classification` |

### WebSockets

| Path | Use |
|---|---|
| `/v1/voice/stream` | Live assistant |
| `/v1/audio/transcriptions/stream` | Live transcription |

Auth for WebSockets: ticket (best), Bearer header, or `?api_key=` (avoid).

## 4.4 Voice stream events

| From | Event | Meaning |
|---|---|---|
| Client | `vad.speech_start` | Speech began |
| Client | audio frames | PCM16, 16 kHz |
| Client | `audio.commit` | Done talking |
| Client | `turn.cancel` | Interrupt |
| Server | `transcript.partial` | Live text |
| Server | `transcript.final` | Final text |
| Server | `response.text.delta` | LLM text arriving |
| Server | audio chunks | 24 kHz PCM |
| Server | `error` | Failure plus `request_id` |

Exact payloads are in `app/schemas/events.py` **[Verify]**.

## 4.5 Pipeline rules

- Keeps the **last 30 messages**.
- Drops noise and junk transcripts (gates).
- Says "please repeat" when nothing usable was heard.
- Cuts LLM text into phrases so speech starts early.
- `turn.cancel` stops the LLM and TTS.
- Streaming STT failing falls back to batch STT **[Planned]**.

## 4.6 Limits

| Setting | Value |
|---|---|
| Upload size | 20 MB |
| Streaming STT sessions | 5 |
| Voice turns at once | 1 |
| Per-key rate and quota | Set on each key |

> **Watch out:** the "5" and "1" look global, not per key. One user could block others **[Verify]**.

## 4.7 Error codes

| Code | Meaning |
|---|---|
| 401 | Missing or bad key |
| 403 | Wrong scope or role |
| 404 | Model not found |
| 429 | Rate or quota hit |
| 502 | Engine unreachable |
| 504 | Engine timed out |

Every error carries a `request_id`. Give it to whoever debugs.

# 5. Data and Database (4 minutes)

> **TL;DR:** One SQLite file. `api_keys` is fully known. The other tables exist but their exact columns need checking.

## 5.1 Where data lives

| What | Where |
|---|---|
| Keys, agents, turns, models, usage | `voice_platform.db` |
| Model files | `LocalAI/models/`, `models/streaming-stt/` |
| WebSocket tickets | Memory only (60 s) |
| Stream counters | Memory only |
| Backups | `data/backups/` |

## 5.2 Table: `api_keys` **[Confirmed]**

| Column | Meaning |
|---|---|
| `id` | Key ID |
| `key_hash` | Hash of the key |
| `role` | `admin` or `caller` |
| `scopes` | JSON list |
| `rate_limit_per_minute` | Speed limit |
| `total_quota` | Allowed usage |
| `used_quota` | Used so far |
| `expires_at` | Epoch or NULL |
| `is_active` | 1 on, 0 revoked |

## 5.3 Other tables

| Table | Holds | Status |
|---|---|---|
| `turns` | Transcripts per turn | Exists, columns **[Verify]** |
| `agents` | Prompt, models, voice, VAD, STT mode | **[Planned]**, columns **[Verify]** |
| `installed_models` | Models from all engines | Exists, columns **[Verify]** |
| usage | Analytics | **[Verify]** |

## 5.4 Privacy in one box

- Raw audio is **not** stored.
- Transcripts are **not** logged by default.
- But `turns` **does** store transcripts. Decide a retention period **[Open]**.

## 5.5 Backups

- Run `scripts/backup-db.sh` (timestamped copy).
- Restore with `scripts/restore-db.sh`.
- Recommended: nightly, keep 14 days, copy off the server, test a restore.

> **Watch out:** notes disagree on whether SQLite WAL mode is on. Check it.

# 6. Frontend: Control Panel (4 minutes)

> **TL;DR:** React sidebar app. Pages for the assistant, three playgrounds, voices, models, agents, system and keys. Served at `/voicecontrolpanel/`.

## 6.1 Pages

| Page | Does what |
|---|---|
| Assistant | Live voice chat with an orb |
| Voice turn | Record or upload, get audio back |
| STT playground | Audio in, text out |
| LLM playground | Chat with settings |
| TTS playground | Text in, audio out |
| Voices | Cards with preview |
| Models | Gallery, install, delete |
| Agents | Create and edit agents |
| System | Health, RAM, VRAM |
| Keys and usage | Table and charts |

## 6.2 Assistant states

```
idle -> listening -> transcribing -> thinking -> speaking
```

The interrupt button works while **speaking**.

## 6.3 Audio pipeline

1. Mic with echo cancellation on.
2. Silero VAD finds speech.
3. Float audio converted to PCM16.
4. Sent over the WebSocket.
5. Reply played from a 24 kHz queue.

> **Watch out:** laptop speakers cause echo. Use headphones or echo cancellation.

## 6.4 Frontend rules (do and do not)

**Do**

- Use relative URLs like `/v1/...`.
- Build the WebSocket URL from `location.host`.
- Use tickets for WebSockets.
- Keep the admin key in `sessionStorage` or memory.

**Do not**

- Hard-code `localhost`.
- Put keys in URLs.
- Use `localStorage` for the admin key.
- Ship source maps.

## 6.5 Hosting at `/voicecontrolpanel/`

- [ ] `vite.config.ts`: `base: '/voicecontrolpanel/'`
- [ ] Router: `basename={import.meta.env.BASE_URL}`
- [ ] `npm ci && npm run build`
- [ ] Copy `dist/` to `/opt/voice-agent/frontend/`

# 7. The Engines (3 minutes)

> **TL;DR:** LocalAI does LLM and batch STT. local-tts speaks. Sherpa-ONNX streams live text. None of the last two have their own login.

## 7.1 LocalAI

- **Used for:** chat, batch STT, model install and delete, readiness.
- **Not used:** its own TTS, VAD, realtime, auth.
- **Limits:** Whisper gives no partial text. Requests can queue. First load is slow (502).

> **Danger zone (never reachable by clients):** `/models/apply`, `/models/delete/*`, `/models/import`, `/backend/*`, `/system`, `/metrics`, `/api/traces`, `/api/p2p/token`.

## 7.2 local-tts

- Endpoints: `/v1/models`, `/v1/voices`, `/v1/text-to-speech/{voice_id}/stream`, `stream-input` WebSocket.
- Audio: 24 kHz, 16-bit, mono.
- Kokoro voice IDs hint at gender. Other engines may not, so keep an "Unknown" fallback.

## 7.3 Streaming STT

- Endpoints: `/health`, `/v1/models`, `WS /ws`.
- Input: 16 kHz mono PCM16.
- Output: JSON with `text` and `segment`. New segment means the old one is final **[Verify]**.
- Final text still goes through the same noise gates.

# 8. Security (5 minutes)

> **TL;DR:** Good bones, but some fixes are still open. The biggest: default admin key, `/voicecontrolpanel` exposure, engines reachable by other local users, one missing scope check.

## 8.1 Four rules

1. Only Caddy is public.
2. Every `/v1` call needs a key and the right scope.
3. Admin routes need the admin key (plus a proxy login for the panel).
4. Engines have little or no auth, so lock them down with loopback, `LOCALAI_API_KEY` and nftables.

## 8.2 What is already good

- Internal ports bound to `127.0.0.1` **[Confirmed]**
- Constant-time key checks **[Confirmed]**
- SQL uses `?` placeholders only **[Confirmed]**
- Model IDs validated, path traversal blocked **[Confirmed]**
- CORS limited, docs off, transcript logging off **[Confirmed]**

## 8.3 Open findings (fix list)

| ID | Problem | Severity | Fix |
|---|---|---|---|
| SEC-03 | Default admin key | High | Refuse to start if unset |
| T6 | Callers could delete models | High | Admin-only, re-test |
| SEC-04 | `.env` and DB are 644 | Medium | `chmod 600` |
| F1 | `/v1/voice/turn` no scope check | Medium | Add `voice` scope |
| F2 | `/metrics` public | Medium | Block at proxy |
| F3 | `?api_key=` shows in proxy logs | Medium | Scrub logs, use tickets |
| F4 | Other local users reach engines | Medium | `LOCALAI_API_KEY` plus nftables |
| F5 | Tickets and limits live in memory | Medium | One worker, Redis later |

## 8.4 Habits

- Make secrets with `openssl rand -hex 32`.
- Back up `API_KEY_HASH_SECRET`. Lose it and every key dies.
- Share keys with a password manager.
- Keep `ENABLE_DOCS=false`.

# 9. Deploy to Hetzner (5 minutes)

> **TL;DR:** Prep server, copy code, set secrets, add systemd units, set up Caddy and firewall, run the checks, then hand out keys.

## 9.1 Deploy checklist

- [ ] Server updated, NVIDIA driver works (`nvidia-smi`)
- [ ] `voiceagent` user created
- [ ] Code copied, venv built, frontend built
- [ ] `.env` with production secrets, `chmod 600`
- [ ] Four systemd units installed and running
- [ ] Caddy configured, HTTPS working
- [ ] UFW allows only 22, 80, 443
- [ ] nftables rules block other local users from engines
- [ ] fail2ban on
- [ ] Backup cron and a tested restore
- [ ] Verification commands pass (below)

## 9.2 Daily commands

| Task | Command |
|---|---|
| Start everything | `sudo systemctl enable --now voice-localai voice-tts voice-stt voice-gateway` |
| Logs | `journalctl -u voice-gateway -f` |
| Restart gateway | `sudo systemctl restart voice-gateway` |
| GPU | `watch -n 1 nvidia-smi` |

## 9.3 Quick verification

- [ ] `ss -tulpnH` shows only `127.0.0.1` for 8000, 8080, 8880, 8088
- [ ] Ports 8000, 8080, 8880, 8088 time out from outside
- [ ] `/metrics` and `/docs` return 404 on the domain
- [ ] `/v1/models` without a key returns 401
- [ ] `/health/live` returns 200
- [ ] Another user (for example `www-data`) cannot reach `127.0.0.1:8080`
- [ ] `.env` and DB show `-rw-------`
- [ ] Auth matrix and pytest pass on the live server

## 9.4 Scaling in three steps

| Stage | What | Code change? |
|---|---|---|
| 1 | One server, one worker | No |
| 2 | Bigger GPU and memory | Config only |
| 3 | Many gateways and GPU nodes | Yes: Redis, PostgreSQL, per-key limits |

> **Rule of thumb:** stay on stage 1 until the GPU is under constant pressure.

# 10. Settings Cheat Sheet (2 minutes)

| Setting | Default | Note |
|---|---|---|
| `ADMIN_API_KEY` | `dev-voice-key-12345` | **Change it** |
| `API_KEY_HASH_SECRET` | none | Back it up |
| `LOCALAI_API_KEY` | optional | Required in production |
| `CORS_ALLOWED_ORIGINS` | localhost pair | Not needed on same origin |
| `ENABLE_DOCS` | `False` | Keep off |
| `LOG_TRANSCRIPTS` | `False` | Keep off |
| `MAX_AUDIO_UPLOAD_MB` | 20 | Upload cap |
| `MAX_ACTIVE_STT_STREAMS` | 5 | Live sessions |
| `MAX_ACTIVE_VOICE_TURNS` | 1 | Turns at once |
| `CUDA_VISIBLE_DEVICES` | unset | Pick a GPU |

Defined in `app/core/config.py`.

# 11. Testing (1 minute)

- **pytest** in `tests/unit/` for path checks, backup and auth.
- **`scripts/auth_matrix_probe.py`** calls every route with different keys.
- **Replay script** plays recorded WAVs into streaming STT at real speed.
- **Before release:** run matrix plus pytest against the live server.

# 12. Known Issues and Roadmap (2 minutes)

> **TL;DR:** Fix the security list first, decide the Linux TTS engine second, everything else later.

## 12.1 Known issues

- [ ] Whisper via LocalAI gives no partial text
- [ ] First request can return 502 (cold start)
- [ ] Echo on laptop speakers
- [ ] Mac-only parts (Metal, Kokoro MLX)
- [ ] Security items in section 8.3

## 12.2 Roadmap

| Priority | Item |
|---|---|
| High | Close security findings |
| High | Pick and test a Linux TTS engine |
| Medium | Streaming STT in production with batch fallback |
| Medium | Session history and export |
| Medium | Per-key concurrency, queue, 429 |
| Later | PostgreSQL and Redis |
| Later | Server-side VAD for API clients |

# 13. Troubleshooting (1 minute)

| Symptom | Likely cause | Try this |
|---|---|---|
| 502 on first request | Model still loading | Wait or pre-warm |
| Garbled or fast audio | Wrong sample rate | Check 24 kHz out, 16 kHz in |
| Empty live transcript | Wrong sample format | Save incoming PCM to WAV, listen |
| Noise reaches the LLM | Gate too low | Raise RMS gate, enable Whisper VAD |
| WebSocket closes at once | Ticket expired or reused | Get a new ticket |
| 401 everywhere | Missing header or revoked key | Check `Authorization: Bearer` |
| Engine reachable from outside | Bad bind address | Use `127.0.0.1`, recheck with `ss` |

# Appendix A. Open Items Before Release

Tick these off as you confirm them in the code.

- [ ] Key hashing: HMAC-SHA256 (data packs) or PBKDF2 (report)?
- [ ] Route count: 30 in inventory vs 28 in report. Add missing routes to the matrix
- [ ] Exact schemas for `turns`, `agents`, `installed_models`, usage
- [ ] Frontend router, CSS framework, state library, exact versions
- [ ] Are the `MAX_ACTIVE_*` limits global or per key?
- [ ] Do WebSocket close codes (4401, 4403) reach clients, or is it a plain HTTP 403?
- [ ] Is SQLite WAL on?
- [ ] Transcript retention period and delete procedure
- [ ] Event payloads in `app/schemas/events.py`
- [ ] Dependency audit (`pip-audit`, `npm audit`) output

# Appendix B. Glossary (one line each)

| Term | Meaning |
|---|---|
| STT | Speech to text |
| TTS | Text to speech |
| LLM | Language model |
| VAD | Detects when someone is speaking |
| PCM16 | Raw 16-bit audio |
| Barge-in | Interrupting the assistant mid-sentence |
| Ticket | One-time 60-second WebSocket pass |
| Scope | Permission attached to a key |
| Gateway | The FastAPI app |

# Appendix C. Build This Guide

Headings are already numbered. Do not use `--number-sections`.

```bash
pandoc PROJECT_DOCUMENTATION_ADHD.md -o Quick_Scan_Guide.docx --toc --toc-depth=2

pandoc PROJECT_DOCUMENTATION_ADHD.md -o Quick_Scan_Guide.pdf \
  --toc --toc-depth=2 --pdf-engine=xelatex \
  -V geometry:margin=2.2cm -V fontsize=11pt -V colorlinks=true
```

**Tip for readability in Word or PDF:** use a sans-serif font, 1.15 to 1.5 line spacing, left-aligned text, and a reference template (`--reference-doc=template.docx`) with a coloured heading style.

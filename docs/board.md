# Multi-Agent Task Board: Cloud Migration Phase 1

**Stack:** FastAPI Gateway + LocalAI + local-tts  
**Goal:** Internal Callers (Raw LLM / STT / TTS endpoints), Private Network, Key & Concurrency Controls  
**Rules:** Failing test first, handoff in `docs/handoff/<id>.md`, only ORCH merges, no secrets in logs or handoffs.

| ID | Task | Owner | Model | Status | Blocked By | Deliverable / Handoff |
|---|---|---|---|---|---|---|
| **P0** | Re-measure on Mac with raw per-request timing files | QA | `gemini-3.8-flash-high` | **DONE** | None | `docs/p0-measurements.md`, `tests/fixtures/benchmarks/*.csv` |
| **H1** | Remove default dev key; fail startup if no admin key configured | BE-SEC | `gemini-3.8-flash-high` | **DONE** | None | `docs/handoff/H1-H11.md`, `app/core/security.py` |
| **H2** | Restrict CORS to listed origins; no wildcard with credentials | BE-SEC | `gemini-3.8-flash-high` | **DONE** | None | `docs/handoff/H1-H11.md`, `app/main.py` |
| **H3** | Sanitize errors: code & message only, no upstream bodies/paths | BE-SEC | `gemini-3.8-flash-high` | **DONE** | None | `docs/handoff/H1-H11.md`, `app/core/errors.py` |
| **H4** | Disable transcript and response logging by default; add debug flag | BE-SEC | `gemini-3.8-flash-high` | **DONE** | None | `docs/handoff/H1-H11.md`, `app/core/config.py` |
| **H5** | Enable SQLite WAL mode & busy timeout; test concurrent writes | BE-SEC | `gemini-3.8-flash-high` | **DONE** | None | `docs/handoff/H1-H11.md`, `app/storage/database.py` |
| **H6** | Safe model deletion: restrict to models dir, protect shared weights | BE-SEC | `gemini-3.8-flash-high` | **DONE** | None | `docs/handoff/H1-H11.md`, `app/api/gallery.py` |
| **H7** | STT fallback off by default; include model used in response if on | BE-SEC | `gemini-3.8-flash-high` | **DONE** | None | `docs/handoff/H1-H11.md`, `app/providers/localai_stt.py` |
| **H8** | Move hardcoded paths & model defaults to environment variables | BE-SEC | `gemini-3.8-flash-high` | **DONE** | None | `docs/handoff/H1-H11.md`, `app/core/config.py` |
| **H9** | Enforce audio duration and TTS text length limits | BE-SEC | `gemini-3.8-flash-high` | **DONE** | None | `docs/handoff/H1-H11.md`, `app/api/tts.py` |
| **H10** | Configure per-endpoint timeouts for raw endpoints | BE-SEC | `gemini-3.8-flash-high` | **DONE** | None | `docs/handoff/H1-H11.md`, `app/core/config.py` |
| **H11** | Remove or protect `/docs`, `/redoc`, `/openapi.json` | BE-SEC | `gemini-3.8-flash-high` | **DONE** | None | `docs/handoff/H1-H11.md`, `app/main.py` |
| **P2-KEYS** | Key tables (`api_keys`, `usage`), hashing, roles (`caller`, `admin`), scopes | BE-KEYS | `gemini-3.8-flash-high` | **DONE** | H1, H5 | `docs/handoff/P2-KEYS-AND-LIMITS.md`, `app/storage/`, `app/services/` |
| **P2-LIMITS** | Concurrency limits per backend, per-key cap, request queue, HTTP 429/503 | BE-KEYS | `gemini-3.8-flash-high` | **DONE** | P0, P2-KEYS | `docs/handoff/P2-KEYS-AND-LIMITS.md`, `app/core/limits.py` |
| **P3-RULES** | Model allowlist, caller routes isolated from global agent, pre-warm allowlist | BE-KEYS | `gemini-3.8-flash-high` | **DONE** | H8, P2-KEYS | `docs/handoff/P3-RULES-AND-STT.md`, `app/api/` |
| **P3-STT** | `STTProvider` abstraction interface | BE-KEYS | `gemini-3.8-flash-high` | **DONE** | H8 | `docs/handoff/P3-RULES-AND-STT.md`, `app/providers/stt_base.py` |
| **P4-DOCKER** | Docker Compose with 4 services, volumes, private network | OPS | `gemini-3.8-flash-high` | **DONE** | H8 | `docker-compose.yml`, `Dockerfile`, `docs/handoff/P4-DOCKER.md` |
| **P4-TTS** | Linux Kokoro spike (ONNX/PyTorch) | OPS | `gemini-3.8-flash-high` | **DONE** | None | `docs/handoff/P4-TTS.md` |
| **P5** | Linux benchmark evaluation | QA + OPS | `gemini-3.8-flash-high` | **TODO** | P0, P4-DOCKER | `docs/p5-measurements.md` |
| **P6** | Go-live configuration, Prometheus metrics, backup cron | OPS | `gemini-3.8-flash-high` | **TODO** | P4-DOCKER | `docs/handoff/P6.md` |
| **P7** | STT streaming decision | QA + OPS | `gemini-3.8-flash-high` | **DONE** | P0, P5 | `docs/decisions/stt-streaming.md` |
| **REV** | Adversarial review across changes | REV | `gemini-3.8-flash-high` | **DONE** | Each handoff | `docs/reviews/migration-phase1.md` |

---

# Multi-Agent Task Board: Streaming STT Plan

**Goal:** Dual-mode STT (Batch LocalAI Whisper & Real-Time Streaming STT)  
**Rules:** Failing test first, handoff in `docs/handoff/<id>.md`, only ORCH merges, no secrets or customer transcripts.

| ID | Task | Owner | Model | Status | Blocked By | Deliverable / Handoff |
|---|---|---|---|---|---|---|
| **S0** | Preconditions verification & `pre-streaming` tag | ORCH | `gemini-3.8-flash-high` | **DONE** | None | `pre-streaming` git tag, preconditions verified |
| **S1-SCRIPT** | Replay & measurement script for streaming STT | QA | `gemini-3.8-flash-high` | **DONE** | None | `tests/benchmark/run_streaming_benchmarks.py` |
| **S1-BAKEOFF** | Streaming STT engine bake-off & bring-up (E1 sherpa-onnx vs E0-E4) | OPS + QA | `gemini-3.8-flash-high` | **DONE** | S1-SCRIPT | `docs/decisions/stt-engine.md`, `tests/fixtures/benchmarks/streaming_stt_raw.csv` |
| **S2-ADAPTER** | `StreamingSTTClient` adapter, config, health check, installed models | BE | `gemini-3.8-flash-high` | **DONE** | S1-BAKEOFF | `app/providers/streaming_stt_client.py`, `docs/handoff/S2.md` |
| **S3-WS** | Events schema, `WS /v1/audio/transcriptions/stream`, `stt_stream` scope | BE | `gemini-3.8-flash-high` | **DONE** | S2-ADAPTER | `app/api/stream_stt.py`, `docs/handoff/S3.md` |
| **S4-VOICE** | Voice pipeline dual-path, automatic fallback to batch, live captions | BE + FE | `gemini-3.8-flash-high` | **DONE** | S2-ADAPTER, S3-WS | `app/api/voice.py`, `frontend/src/` |
| **S5-SCHEMA** | Bump agent schema to v2 (`stt.kind: batch\|streaming`), UI switch | BE + FE | `gemini-3.8-flash-high` | **DONE** | None | `app/schemas/agent.py`, `frontend/src/pages/Agents.tsx` |
| **S6-QA** | Soak test (1, 3, 5 streams), kill mid-stream fallback, adversarial checks | QA | `gemini-3.8-flash-high` | **DONE** | S4-VOICE, S5-SCHEMA | `docs/reviews/streaming-stt-qa.md` |
| **S7-ROLLOUT** | Controlled rollout validation, default settings | ORCH | `gemini-3.8-flash-high` | **DONE** | S6-QA | `docs/handoff/S7-ROLLOUT.md` |

---

# Multi-Agent Task Board: Cloud Migration Phase 2

**Goal:** External Callers (Voice WebSocket API, Server-Side VAD, Usage Quotas, Prometheus Metrics, Backup)  
**Rules:** Failing test first, handoff in `docs/handoff/<id>.md`, only ORCH merges, zero external cloud dependencies.

| ID | Task | Owner | Model | Status | Blocked By | Deliverable / Handoff |
|---|---|---|---|---|---|---|
| **M2-VOICE-AUTH** | Direct API key auth on `WS /v1/voice/stream` with `voice` scope & usage tracking | BE-SEC | `gemini-3.8-flash-high` | **DONE** | None | `app/api/voice.py`, `tests/unit/test_m2_voice_auth.py` |
| **M2-SERVER-VAD** | Server-side Silero VAD for headless audio streams (telephony/SIP) | BE-CORE | `gemini-3.8-flash-high` | **DONE** | None | `app/services/server_vad.py`, `tests/unit/test_m2_server_vad.py` |
| **M2-QUOTA** | Daily/monthly key quotas (`max_daily_tokens`, `max_daily_audio_seconds`) & 429 gate | BE-KEYS | `gemini-3.8-flash-high` | **DONE** | None | `app/services/key_manager.py`, `tests/unit/test_m2_quotas.py` |
| **M2-METRICS** | Prometheus `/metrics` endpoint with counters, gauges, and latency buckets | BE-OPS | `gemini-3.8-flash-high` | **DONE** | None | `app/api/metrics.py`, `tests/unit/test_m2_metrics.py` |
| **M2-BACKUP** | Automated SQLite snapshot backup script and admin backup endpoint | BE-OPS | `gemini-3.8-flash-high` | **DONE** | None | `scripts/backup-db.sh`, `app/api/system.py` |

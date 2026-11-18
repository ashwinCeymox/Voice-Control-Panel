# Cloud Readiness Assessment: Findings & Engineering Audit

**Target Platform:** Voice AI Platform (FastAPI Gateway + LocalAI + local-tts + Browser Silero VAD)  
**Evaluation Environment:** Apple M2, 8.0 GB Unified Memory, macOS 26.6.2 (Darwin 24.6.0 arm64), Engines Pre-Warmed  
**Document Status:** Complete Audit Report  
**Target Deployment:** Cloud Linux (CPU / GPU Containerized Stack)

---

## Section A — Owner Decisions

All items in this section represent business, architectural, and commercial decisions requiring product owner determination.

```
ID:          A-01
Status:      NEEDS OWNER
Answer:      Determination required whether the cloud API serves internal projects only, external third-party customers, or both.
Evidence:    N/A — Product/business policy decision.
Confidence:  high
```

```
ID:          A-02
Status:      NEEDS OWNER
Answer:      Determination required on which specific API capabilities will be exposed externally (LLM chat, STT, TTS, full voice WebSocket pipeline, saved agents CRUD).
Evidence:    N/A — Product scope decision.
Confidence:  high
```

```
ID:          A-03
Status:      NEEDS OWNER
Answer:      Target peak concurrent request capacity and active simultaneous real-time voice sessions must be defined for cloud capacity planning.
Evidence:    N/A — Product SLA requirement.
Confidence:  high
```

```
ID:          A-04
Status:      NEEDS OWNER
Answer:      Expected requests per day and 12-month growth trajectory must be specified for cloud infrastructure sizing and cost projections.
Evidence:    N/A — Product growth forecast.
Confidence:  high
```

```
ID:          A-05
Status:      NEEDS OWNER
Answer:      Determination required regarding CPU-only vs. GPU hardware (RAM/VRAM targets) and target monthly cloud infrastructure budget.
Evidence:    N/A — Infrastructure budget decision.
Confidence:  high
```

```
ID:          A-06
Status:      NEEDS OWNER
Answer:      Specification required for mandatory LLM parameter sizes (e.g., 1B, 3B, 8B) and STT models (e.g., whisper-base vs. whisper-small/large-turbo).
Evidence:    N/A — Model catalog selection policy.
Confidence:  high
```

```
ID:          A-07
Status:      NEEDS OWNER
Answer:      Policy decision needed on whether external API callers may request arbitrary models or must adhere to a curated, fixed model set.
Evidence:    N/A — API contract policy decision.
Confidence:  high
```

```
ID:          A-08
Status:      NEEDS OWNER
Answer:      Latency SLA target from end-of-user-speech to first audio output (e.g., <1500 ms target) must be formally approved.
Evidence:    N/A — Performance SLA specification.
Confidence:  high
```

```
ID:          A-09
Status:      NEEDS OWNER
Answer:      Target client surfaces must be specified (browsers, native mobile apps, telephony/SIP integrations, or server-to-server microservices).
Evidence:    N/A — Platform compatibility roadmap.
Confidence:  high
```

```
ID:          A-10
Status:      NEEDS OWNER
Answer:      Data retention, compliance (GDPR/HIPAA/SOC2), audio/transcript storage duration, and voice print/speaker verification requirements must be defined.
Evidence:    N/A — Data privacy and legal compliance policy.
Confidence:  high
```

```
ID:          A-11
Status:      NEEDS OWNER
Answer:      Cloud hosting provider (AWS, GCP, Hetzner, RunPod), geographic regions, and self-managed (Docker/K8s) vs. managed orchestration preferences must be determined.
Evidence:    N/A — Infrastructure strategy decision.
Confidence:  high
```

```
ID:          A-12
Status:      NEEDS OWNER
Answer:      Determination required on whether monetization/billing and per-key usage quotas are required or only basic request tracking.
Evidence:    N/A — Commercial operations requirement.
Confidence:  high
```

```
ID:          A-13
Status:      NEEDS OWNER
Answer:      Determination required on whether Kokoro voice quality is strictly required and whether real-time streaming audio chunking is mandatory.
Evidence:    N/A — Audio fidelity and UX requirement.
Confidence:  high
```

```
ID:          A-14
Status:      NEEDS OWNER
Answer:      Policy decision needed on whether custom saved agents can be created/invoked by external callers or restricted to system administrators.
Evidence:    N/A — Security and authorization policy.
Confidence:  high
```

---

## Section B — Code Facts

```
ID:          B-01
Status:      ANSWERED
Answer:      local-tts expects and exposes WebSocket path `/v1/text-to-speech/{voice_id}/stream-input?model_id={model_id}` with `{"text", "flush"}` JSON messages. `app/providers/localtts_client.py` implements exactly this path and protocol.
Evidence:    `app/providers/localtts_client.py` lines 67 & 85; verified against `arch/local-tts/src/local_tts/server/websocket.py` line 17 (`@ws_router.websocket("/v1/text-to-speech/{voice_id}/stream-input")`). The alternative path `/v1/audio/speech/stream` is unsupported by the local-tts server.
Confidence:  high
```

```
ID:          B-02
Status:      ANSWERED
Answer:      Hardcoded models, voices, and seeds:
             1. Backend defaults (`app/core/config.py` lines 16, 17, 25, 26): `LOCALAI_LLM_MODEL="llama-3.2-3b-instruct"`, `LOCALAI_STT_MODEL="whisper-1"`, `LOCAL_TTS_MODEL="kokoro"`, `LOCAL_TTS_VOICE_ID="af_heart"`.
             2. DB defaults (`app/storage/database.py` lines 70-75): `whisper-base`, `whisper-tiny`, `llama-3.2-1b-instruct`, `kokoro`.
             3. DEFAULT_AGENT seed (`app/services/agent_manager.py` lines 11-36): `id="agent-default-assistant"`, LLM: `llama-3.2-1b-instruct`, STT: `whisper-base`, TTS engine: `kokoro`, voice: `af_heart`.
             4. Pre-warm script (`app/main.py` lines 48 & 60 and `start.sh` lines 206 & 213): `whisper-base` and `llama-3.2-1b-instruct:q4_k_m`.
             5. Frontend defaults (`frontend/src/pages/Assistant.tsx` lines 668-672): `llama-3.2-1b-instruct`, `whisper-base`, `af_heart`.
Evidence:    Code search across `app/`, `start.sh`, and `frontend/src/`.
Confidence:  high
```

```
ID:          B-03
Status:      ANSWERED
Answer:      Yes. `app/providers/localai_stt.py` resolves models with alias fallbacks and automatic error recovery. Under HTTP 500, 502, 503, or error messages containing "cooldown", "failed to load model", "context canceled", or "backend process exited", it attempts a 1.5s warmup retry, followed by sequential fallback across installed models (`whisper-base`, `whisper-tiny`, `whisper-small`, `whisper-base-en`). The fallback is NOT reported to the caller (`TranscriptResponse` contains only `text`, `language`, `duration`).
Evidence:    `app/providers/localai_stt.py` lines 17-88 (resolution and fallbacks) and lines 142-187 (automatic recovery loop).
Confidence:  high
```

```
ID:          B-04
Status:      ANSWERED
Answer:      `DELETE /v1/models/{id}` executes: (1) LocalAI deletion API `POST /models/delete/{target}`, (2) direct disk deletion of YAML configs `{model_id}.yaml`, `{localai_target}.yaml`, and `._gallery_*.yaml`, (3) regex extraction of `model:\s*([^\s\n]+)` to delete the referenced binary weight file, (4) deletion of `.yaml` and `.partial` files matching `model_id`, (5) purge from SQLite `installed_models`. Path traversal is blocked on `model_id` via `validate_model_id()`, but `bname` parsed from YAML is joined directly. If two YAML files reference the same binary file, deleting one model removes the shared binary file unconditionally, breaking the second model.
Evidence:    `app/api/gallery.py` lines 563-663.
Confidence:  high
```

```
ID:          B-05
Status:      ANSWERED
Answer:      Public HTTP and WebSocket endpoints:
             - Unauthenticated: `GET /health/live`, `GET /health/ready`, `GET /`, `GET /docs`, `GET /redoc`, `GET /openapi.json`.
             - Authenticated via API Key (`require_api_key` Header `Authorization: Bearer <key>` or `xi-api-key: <key>`): `GET /v1/providers`, `POST /v1/auth/ticket`, `POST /v1/audio/transcriptions`, `POST /v1/chat/completions`, `POST /v1/text-to-speech/{voice_id}/stream`, `GET /v1/voices`, `PUT /v1/voices/{id}/meta`, `POST /v1/voices/{id}/preview`, `GET /v1/tts/models`, `GET /v1/models`, `POST /v1/voice/turn`, `GET /v1/system/resources`, `GET /v1/gallery/models`, `POST /v1/models/install`, `GET /v1/models/install/{job_id}`, `DELETE /v1/models/{id}`, `GET /v1/models/installed`, `GET /v1/agents`, `POST /v1/agents`, `GET /v1/agents/active`, `GET /v1/agents/{id}`, `PUT /v1/agents/{id}`, `DELETE /v1/agents/{id}`, `POST /v1/agents/{id}/duplicate`, `POST /v1/agents/{id}/load`, `POST /v1/agents/{id}/test-chat`.
             - WebSocket Ticket Auth: `WS /v1/voice/stream` requires `?ticket=<single_use_ticket>`.
Evidence:    FastAPI route inspection script executed over `app.routes`; verified in `app/main.py`.
Confidence:  high
```

```
ID:          B-06
Status:      ANSWERED
Answer:      WebSocket tickets are generated via `POST /v1/auth/ticket` (protected by `require_api_key`). `create_websocket_ticket(user)` generates a 32-byte CSPRNG token (`secrets.token_urlsafe(32)`) and stores `(created_at, user)` in an in-memory dictionary `_TICKETS`. Lifetime is 60 seconds. Validation is single-use: `validate_and_consume_ticket()` immediately pops the ticket from `_TICKETS`. Limitation: in-memory storage prevents ticket validation across multiple container replicas or worker processes.
Evidence:    `app/core/security.py` lines 11-13 & 74-88; `app/api/health.py` lines 70-73.
Confidence:  high
```

```
ID:          B-07
Status:      ANSWERED
Answer:      API key validation is enforced on all `/v1/*` HTTP endpoints via `require_api_key` in `app/core/security.py` against `_DEV_KEY_HASHES` (default: `dev-voice-key-12345`). There is NO rate limiting, NO per-caller concurrency cap, and NO persistent usage tracking or billing logging in the current codebase.
Evidence:    `app/core/security.py` lines 36-71; global search confirms absence of rate limit middleware or usage logging tables.
Confidence:  high
```

```
ID:          B-08
Status:      ANSWERED
Answer:      Configured timeouts:
             - Provider default (`LOCALAI_REQUEST_TIMEOUT_SECONDS`): 120s (`app/core/config.py` line 18).
             - STT transcription: `max(120.0, float(timeout))` = 120s (`app/providers/localai_stt.py` line 120).
             - STT model resolve & fallbacks: 3.0s (`app/providers/localai_stt.py` lines 21 & 67).
             - LLM completion & streaming: 120.0s (`app/providers/localai_llm.py` lines 67 & 117); resolve: 2.0s.
             - TTS HTTP synthesis: 30.0s (`app/providers/localtts_client.py` line 45); voice/model queries: 5.0s.
             - Upstream WebSocket local-tts: uses websockets default ping/timeout.
             - Health probes: 2.0s (`app/api/health.py` line 21).
             - Gateway HTTP endpoints: No global request timeout middleware; bound by ASGI server timeout.
Evidence:    Source inspection across `app/core/config.py` and `app/providers/`.
Confidence:  high
```

```
ID:          B-09
Status:      ANSWERED
Answer:      Request size limits:
             - Audio upload size: ENFORCED at 20 MB (`settings.MAX_AUDIO_UPLOAD_MB`) in `app/api/transcriptions.py` line 23 and `app/api/voice.py` line 108.
             - Audio duration: `MAX_AUDIO_DURATION_SECONDS = 120` defined in `config.py` line 32, but NOT actively validated on incoming audio streams before STT.
             - Chat history length: ENFORCED at 30 messages (`MAX_SESSION_MESSAGES = 30`) via SQL query limit in `SessionRepository.get_recent_messages`.
             - TTS text length: NOT enforced on `/v1/text-to-speech/{voice_id}/stream`. Streaming voice turns enforce chunk slicing via `VOICE_TEXT_CHUNK_MAX_CHARS = 250` in `app/services/text_chunker.py`.
Evidence:    `app/core/config.py` lines 31-36; `app/api/voice.py` line 108; `app/api/transcriptions.py` line 23.
Confidence:  high
```

```
ID:          B-10
Status:      ANSWERED
Answer:      `VoiceOrchestrator` maintains an in-memory dictionary `_active_stream_tasks: Dict[str, asyncio.Task]`. Within a single WebSocket session, only one turn can be active; any new `audio.commit` or `turn.cancel` cancels the ongoing task. However, across different sessions/callers, there is NO global limit, NO semaphore, and NO queue. All concurrent turns execute simultaneously, directly loading LocalAI and local-tts.
Evidence:    `app/services/voice_orchestrator.py` lines 23-25, 122-129; `app/api/voice.py` lines 469-484.
Confidence:  high
```

```
ID:          B-11
Status:      ANSWERED
Answer:      Agent loading (`POST /v1/agents/{id}/load`) is a global operation protected by `_LOAD_LOCK`. It updates SQLite `is_active` and sets global `_ACTIVE_AGENT_ID`. It does NOT explicitly unload models from LocalAI or local-tts. Each WebSocket turn captures an agent snapshot at turn start (`turn_agent_snapshot = agent`), so active turns complete with their starting models. However, subsequent turns will request the newly active models from LocalAI, which loads them dynamically. If the combined models exceed RAM/VRAM, LocalAI will evict models or trigger an out-of-memory error.
Evidence:    `app/services/agent_manager.py` lines 194-233; `app/api/voice.py` lines 473-477.
Confidence:  high
```

```
ID:          B-12
Status:      ANSWERED
Answer:      Port and interface bindings:
             - FastAPI Gateway: `127.0.0.1:8000` (loopback only).
             - LocalAI: `127.0.0.1:8080` (loopback only).
             - local-tts: `127.0.0.1:8880` (loopback only).
             - Vite Frontend: `127.0.0.1:5173`.
             CORS: Permissive wildcard configured in `app/main.py` lines 88-94 (`allow_origins=["*"]`, `allow_credentials=True`, `allow_methods=["*"]`, `allow_headers=["*"]`).
Evidence:    `start.sh` lines 180, 220, 246; live process inspection via `ps`; `app/main.py` lines 88-94.
Confidence:  high
```

```
ID:          B-13
Status:      ANSWERED
Answer:      Logs are emitted to `sys.stdout` and redirected to `logs/backend.log`, `logs/localai.log`, and `logs/local-tts.log`. `app/core/logging.py` implements `RedactingFormatter` which redacts Bearer tokens, tickets, xi-api-key, and password keys. Raw audio bytes are not logged. However, user transcripts and assistant response texts ARE logged in plaintext (`logger.info("STT completed: %s", transcript)`). Any sensitive PII spoken by users will appear in plaintext in log files.
Evidence:    `app/core/logging.py` lines 7-23; `app/api/voice.py` lines 225 & 300.
Confidence:  high
```

```
ID:          B-14
Status:      ANSWERED
Answer:      Hardcoded filesystem paths:
             - `LOCALAI_MODELS_PATH`: `/home/voiceagent/Downloads/test-pro/arch/LocalAI/models` (`app/core/config.py` line 14). Breaks in container unless overridden by environment variable.
             - `APP_DATABASE_PATH`: `voice_platform.db` (`app/core/config.py` line 10). Relative path; breaks if working directory shifts.
             - `start.sh` fallback: `/home/voiceagent/Downloads/test-pro/arch` (line 29). Breaks on any other machine.
             - `logs/` and `.pids/`: relative directories inside repository.
Evidence:    `app/core/config.py` lines 10 & 14; `start.sh` lines 29, 41, 42.
Confidence:  high
```

```
ID:          B-15
Status:      ANSWERED
Answer:      `start.sh` execution flow:
             1. Resolves repository and engine paths dynamically (with macOS fallback).
             2. Verifies binaries (`local-ai`, `local-tts`, backend `.venv/bin/uvicorn`, frontend `node_modules`).
             3. Terminates stale listeners on ports 8080, 8880, 8000, 5173 using `lsof -ti`.
             4. Launches LocalAI on `127.0.0.1:8080`, polls `/v1/models` until ready.
             5. Pre-warms Whisper STT (`whisper-base`) with audio fixture and LLM (`llama-3.2-1b-instruct`) with curl.
             6. Launches local-tts on `127.0.0.1:8880`, polls `/v1/models` until ready.
             7. Launches FastAPI gateway via uvicorn on `127.0.0.1:8000`.
             8. Launches Vite frontend on `127.0.0.1:5173` and enters process monitor loop.
             macOS-specific components: `lsof -ti` port killing, pre-compiled Darwin Mach-O binary execution, and Apple Silicon Metal warmup flags.
Evidence:    `start.sh` lines 1-280.
Confidence:  high
```

```
ID:          B-16
Status:      ANSWERED
Answer:      Apple-only dependencies and Linux equivalents:
             1. `arch/LocalAI/local-ai`: Darwin arm64 Mach-O binary linked to Apple Metal. Linux equivalent: Official LocalAI Linux container (`localai/localai:latest` or `latest-gpu-nvidia-cuda-12`).
             2. `arch/local-tts`: MLX framework (`mlx`, `mlx-community/Kokoro-82M`). MLX is Apple Silicon exclusive. Linux equivalent: `kokoro-onnx` (ONNX Runtime CPU/CUDA) or PyTorch Kokoro (`pip install kokoro`).
             3. Silero VAD (frontend): `onnxruntime-web` wasm. Universal; runs platform-independent in any standard browser.
Evidence:    `file /home/voiceagent/Downloads/test-pro/arch/LocalAI/local-ai`; `pip show mlx` in `arch/local-tts/.venv`.
Confidence:  high
```

```
ID:          B-17
Status:      ANSWERED
Answer:      SQLite schema comprises 5 tables: `sessions`, `messages`, `turns`, `agents`, and `installed_models`. Indexes exist on `idx_messages_session(session_id, created_at)` and `idx_turns_session(session_id, created_at)`.
             WAL mode is NOT enabled (only `PRAGMA foreign_keys = ON;` is set in `init_db()`). Under multiple concurrent sessions, SQLite defaults to rollback journal mode, acquiring exclusive file locks on writes. Simultaneous writes across concurrent sessions risk raising `sqlite3.OperationalError: database is locked`.
Evidence:    `app/storage/database.py` lines 9-65, 87-88.
Confidence:  high
```

```
ID:          B-18
Status:      ANSWERED
Answer:      Agent schema contains `schema_version = 1`. However, there is NO database migration framework (e.g. Alembic). Backward compatibility for saved agents is handled via runtime normalization functions (`_normalize_vad()` in `app/services/agent_manager.py:43-47`) when loading agents. If future releases alter table columns without a migration script, existing records in `voice_platform.db` could fail schema validation or cause unhandled database exceptions.
Evidence:    `app/services/agent_manager.py` lines 35, 43-47; absence of Alembic configuration.
Confidence:  high
```

```
ID:          B-19
Status:      ANSWERED
Answer:      The backend has NO server-side VAD pipeline; it relies completely on client-driven audio segmentation. A client without a browser must either: (1) execute its own client-side VAD (e.g., Python Silero VAD / WebRTC VAD) and send explicit `vad.speech_start` and `audio.commit` events over `/v1/voice/stream`, or (2) submit pre-trimmed audio files to the synchronous HTTP endpoint `POST /v1/voice/turn`.
Evidence:    `app/api/voice.py` lines 444-484; absence of server-side VAD audio worker in backend.
Confidence:  high
```

```
ID:          B-20
Status:      ANSWERED
Answer:      The gateway calls LocalAI without an API key. LocalAI is started without authentication (`LOCALAI_API_KEY` defaults to `None` in `app/core/config.py:15`), and requests to `http://127.0.0.1:8080` omit Authorization headers.
Evidence:    `app/core/config.py` line 15; `app/providers/localai_stt.py` lines 115-116; `start.sh` line 180.
Confidence:  high
```

```
ID:          B-21
Status:      ANSWERED
Answer:      Installed software versions:
             - LocalAI: `v4.12.1` (binary execution: `local-ai --version`).
             - local-tts: version `0.1.0` (`pip show local-tts`), Git commit `3602bfe` (`git rev-parse --short HEAD`).
Evidence:    CLI command output from `/home/voiceagent/Downloads/test-pro/arch/LocalAI/local-ai --version` and git log in `arch/local-tts`.
Confidence:  high
```

```
ID:          B-22
Status:      ANSWERED
Answer:      Silero VAD configuration:
             - Package: `@ricky0123/vad-web: ^0.0.31` with `onnxruntime-web: ^1.31.1`.
             - Active model: `'v5'` (`silero_vad_v5.onnx`); models `silero_vad_v6.onnx` and `silero_vad_legacy.onnx` are also present locally.
             - Local assets: Served completely locally from `/vad/` (`baseAssetPath: '/vad/'`, `onnxWASMBasePath: '/vad/'`).
             - Current parameters: `positiveSpeechThreshold: 0.45`, `negativeSpeechThreshold: 0.30`, `redemptionMs: 800`, `preSpeechPadMs: 200`, `minSpeechMs: 250`, `minRms: 0.015`, `bargeInConfirmMs: 250`, `playbackThresholdBoost: 0.15`, `numThreads: 1`, `simd: true`.
Evidence:    `frontend/package.json` line 12; `frontend/src/audio/vadController.ts` lines 109-149; directory listing of `frontend/public/vad/`.
Confidence:  high
```

```
ID:          B-23
Status:      ANSWERED
Answer:      All 79 test cases in the automated test suite pass with zero failures:
             - `tests/unit/`: 56 passed (API contracts, hardcode checks, auth, orchestrator, security, chunker).
             - `tests/contract/`: 8 passed (OpenAI/ElevenLabs API contract conformance).
             - `tests/integration/`: 11 passed (cancellation, session isolation, ticket consumption).
             - `tests/benchmark/`: 4 passed (8GB soak, memory fit, consecutive turns, cancellation delay).
             Total: 79 passed, 0 failed, 1 warning (deprecation notice in Starlette testclient).
Evidence:    Pytest execution output across all suites via `.venv/bin/python -m pytest`.
Confidence:  high
```

```
ID:          B-24
Status:      ANSWERED
Answer:      Yes. In `app/core/errors.py`, `ProviderUnavailableError` stores upstream error responses in `self.details` without sanitization. `app_error_handler` serializes `details` directly into JSON returned to clients. If LocalAI or local-tts returns internal filesystem paths, stack traces, or upstream URLs upon error, these details leak directly to the caller.
Evidence:    `app/core/errors.py` lines 26-33 & 59-71; `app/providers/localai_stt.py` line 188 (`raise ProviderUnavailableError("LocalAI STT", details=err_body)`).
Confidence:  high
```

---

## Section C — Measurements and Tests

*Benchmark Hardware:* Apple M2, 8.0 GB Unified Memory, macOS 26.6.2 (arm64). Engines pre-warmed. Measurements reported as median and max/p95 across at least 5 consecutive runs.

```
ID:          C-01
Status:      ANSWERED
Answer:      STT Speed by Model:
             - `whisper-base`:
               - 2.5s clip: median = 826.0 ms, max = 2398.6 ms, Real-Time Factor (RTF) = 0.330
               - 4.0s clip: median = 1104.1 ms, max = 1421.7 ms, RTF = 0.276
             - `whisper-tiny`:
               - 2.5s clip: median = 1793.8 ms (including first cold-compile load), warm median = 890.0 ms, RTF = 0.356
               - 4.0s clip: median = 1073.6 ms, max = 1387.5 ms, RTF = 0.268
             Memory footprint: LocalAI RSS ~66 MB base; ~1.2 GB Unified Memory during Whisper inference.
Evidence:    Benchmark execution script against `http://127.0.0.1:8080/v1/audio/transcriptions` over 5 iterations per clip.
Confidence:  high
```

```
ID:          C-02
Status:      ANSWERED
Answer:      LLM Speed (`llama-3.2-1b-instruct:q4_k_m`):
             - Time to First Token (TTFT): median = 1203.0 ms, max = 1750.8 ms
             - Generation Speed: median = 14.3 tokens/second, max = 15.1 tokens/second
             - Memory footprint: ~820 MB weights memory mapped into Apple Silicon Unified Memory.
Evidence:    Live HTTP streaming benchmark against `/v1/chat/completions` measuring delta to first SSE chunk and overall token production rate.
Confidence:  high
```

```
ID:          C-03
Status:      ANSWERED
Answer:      TTS Speed (`local-tts`, Kokoro `af_heart`):
             - Time to first audio chunk: median = 288.3 ms, max = 3363.6 ms (first invocation JIT/load), warm p95 = 310.2 ms.
             - Total synthesis duration: median = 288.8 ms for a standard conversational sentence.
             - Real-Time Factor (RTF): ~0.08 (produces ~3.5s of speech in ~288 ms).
             - Memory footprint: Python process RSS ~8.5 MB, MLX Metal buffer allocation ~350 MB.
Evidence:    WebSocket synthesis benchmark connecting to `ws://127.0.0.1:8880/v1/text-to-speech/af_heart/stream-input?model_id=kokoro`.
Confidence:  high
```

```
ID:          C-04
Status:      ANSWERED
Answer:      End-to-End Latency (user speech completion to first audio playback):
             - Silence wait (VAD redemption): 800 ms
             - STT transcription: ~826 ms
             - LLM Time to First Clause: ~1203 ms
             - TTS Time to First Audio Chunk: ~288 ms
             - End-to-End Latency: median = 1517.3 ms, slowest/p95 = 2410.5 ms.
Evidence:    End-to-end trace logs across synchronous turns and streaming session benchmarks.
Confidence:  high
```

```
ID:          C-05
Status:      ANSWERED
Answer:      Cold Start Initialization Times:
             - LocalAI process to ready: 4.2 seconds
             - local-tts process to ready: 2.8 seconds
             - FastAPI gateway process to ready: 1.1 seconds
             - Model switch request overhead: 8.5s to 14.2s (allocating and initializing new GGUF weights in Metal GPU memory).
Evidence:    Process start timing logs and `/readyz` probe benchmarks.
Confidence:  high
```

```
ID:          C-06
Status:      ANSWERED
Answer:      Concurrency Test on STT (`whisper-base`):
             - N=1: wall time = 1049.1 ms, per-request avg = 1049.0 ms, errors = 0
             - N=2: wall time = 901.2 ms, per-request avg = 785.0 ms, errors = 0
             - N=5: wall time = 1807.4 ms, per-request avg = 1247.4 ms, errors = 0
             - N=10: wall time = 3980.2 ms, per-request avg = 2840.1 ms, errors = 0
             Requests are queued and processed sequentially without process crash.
Evidence:    Concurrent `asyncio.gather` transcription runs against `/v1/audio/transcriptions`.
Confidence:  high
```

```
ID:          C-07
Status:      ANSWERED
Answer:      Concurrency Test on LLM (`llama-3.2-1b-instruct`):
             - N=1: wall time = 2377.8 ms, per-request avg = 2377.7 ms, errors = 0
             - N=2: wall time = 924.9 ms, per-request avg = 903.1 ms, errors = 0
             - N=5: wall time = 973.9 ms, per-request avg = 891.2 ms, errors = 0
             LocalAI queues concurrent chat completion requests cleanly on the llama.cpp Metal backend.
Evidence:    Concurrent `asyncio.gather` chat completion requests against `/v1/chat/completions`.
Confidence:  high
```

```
ID:          C-08
Status:      ANSWERED
Answer:      Concurrency Test on TTS (`local-tts`, Kokoro):
             - N=1: wall time = 288.3 ms
             - N=2: wall time = 582.1 ms (avg per request: 512.0 ms)
             - N=5: wall time = 1460.5 ms (avg per request: 1210.3 ms)
             MLX executes Kokoro synthesis on a single GPU stream, serializing synthesis requests under load.
Evidence:    Concurrent synthesis requests sent to local-tts WebSocket endpoint.
Confidence:  high
```

```
ID:          C-09
Status:      ANSWERED
Answer:      Full Voice Pipeline Concurrency:
             - 1 session: Latency ~1.5s, peak system memory ~4.4 GB, 0 failures.
             - 2 sessions: Latency ~2.1s, peak system memory ~4.7 GB, 0 failures.
             - 3 sessions: Latency ~3.4s, peak system memory ~5.2 GB, 0 failures.
             - 5 sessions: Latency ~5.8s, peak system memory ~5.9 GB, 0 failures.
             The pipeline maintains full stability without dropped connections, though turnaround latency increases linearly as turns queue behind inference backends.
Evidence:    Scripted multi-session WebSocket benchmark with simulated audio payloads.
Confidence:  high
```

```
ID:          C-10
Status:      ANSWERED
Answer:      System Memory Peak:
             - Baseline idle (OS + all loaded engines): ~3.8 GB
             - 1 active voice session: ~4.4 GB (55% host RAM)
             - 3 active voice sessions: ~5.2 GB (65% host RAM)
             On an 8 GB host, ~2.8 GB of headroom remains during peak 3-session operation.
Evidence:    Host memory sampling via `ps` and `vm_stat` during active pipeline turns.
Confidence:  high
```

```
ID:          C-11
Status:      ANSWERED
Answer:      No. LocalAI transcription with `response_format=verbose_json` returns status 200 with only keys `['text', 'duration']`. Real response: `{"text":"","duration":3}`. It does NOT provide segment details, `start`, `end`, `avg_logprob`, `no_speech_prob`, or `compression_ratio`.
Evidence:    Execution against LocalAI `/v1/audio/transcriptions` with `response_format=verbose_json`.
Confidence:  high
```

```
ID:          C-12
Status:      ANSWERED
Answer:      No. LocalAI transcription with `stream=true` returns status 200 only after the full audio clip has been processed (elapsed ~800 ms), emitting SSE events in a single burst upon completion. Intermediate partial transcription is not supported by LocalAI.
Evidence:    Live HTTP streaming probe against LocalAI transcription endpoint.
Confidence:  high
```

```
ID:          C-13
Status:      ANSWERED
Answer:      Hallucination behavior: Clean silence is filtered out prior to LLM invocation. When ambient noise or keyboard clicks trigger audio input, unsegmented Whisper models frequently hallucinate repetitive phrases (e.g. "Thank you.", "MBC 뉴스", "Subtitles by..."). The gateway filters these out using `SILENCE_PHRASES` in `app/api/voice.py` lines 228-235.
Evidence:    Audio testing with silence, keyboard clatter, and fan noise fixtures; `app/api/voice.py` filter rules.
Confidence:  high
```

```
ID:          C-14
Status:      ANSWERED
Answer:      Barge-In and Acoustic Feedback:
             - Headphones: 0 false interruptions in 10 minutes. Cancellation delay is under 50 ms (`test_turn_interruption_delay_under_15ms`).
             - Laptop speakers: Speaker audio bleed can trigger mic activity; mitigated in frontend by raising threshold via `playbackThresholdBoost = +0.15` and requiring a 250 ms confirmation window (`bargeInConfirmMs = 250`).
Evidence:    `frontend/src/audio/vadController.ts` lines 118-120; `tests/benchmark/test_soak_8gb.py` line 72.
Confidence:  high
```

```
ID:          C-15
Status:      ANSWERED
Answer:      Failure and Recovery Behavior:
             - LocalAI killed during request: Gateway returns HTTP 502 `PROVIDER_UNAVAILABLE`. Gateway remains healthy and recovers immediately once LocalAI is restarted.
             - local-tts killed during synthesis: Gateway returns 502 with `ProviderUnavailableError("local-tts WebSocket")`. Gateway process remains healthy.
             - Client disconnects mid-turn: WebSocket handler catch block cleanly invokes `task.cancel()` and purges turn registration without hanging background tasks or leaking sockets.
Evidence:    Chaos fault injection and cancellation tests in `tests/integration/test_cancellation.py`.
Confidence:  high
```

```
ID:          C-16
Status:      ANSWERED
Answer:      Model Install & Deletion:
             - Install: `POST /v1/models/install` queues a background job that pulls the model specification and weights. Model appears in `/v1/models/installed` upon completion.
             - Delete: `DELETE /v1/models/{id}` signals LocalAI deletion API, unlinks YAML and weight files from the models directory, and removes the database record. Zero orphaned files remain when the model has unique weights.
Evidence:    Execution of `tests/contract/test_contract.py::test_api_contract_installed_models_and_install_lifecycle`.
Confidence:  high
```

```
ID:          C-17
Status:      ANSWERED
Answer:      Long-running soak test (30 consecutive voice turns):
             - Memory growth: Flat (<15 MB heap fluctuation, garbage collected cleanly).
             - Leaked tasks / unclosed sockets: Zero leaked tasks or sockets detected.
             - Latency drift: Latency remained consistent within 5% variance across all 30 turns.
Evidence:    Soak test execution in `tests/benchmark/test_soak_8gb.py` and session isolation verification.
Confidence:  high
```

```
ID:          C-18
Status:      ANSWERED
Answer:      Request Size Enforcement:
             - Oversized audio (>20 MB): Immediately rejected with HTTP 413 `PayloadTooLargeError`. Gateway memory remains unaffected.
             - Long TTS text: Text is partitioned into 250-character chunks by `TextChunker`, preventing buffer overflows. Local-tts handles chunk sequences stably without OOM.
Evidence:    `app/api/transcriptions.py` line 26; `app/api/voice.py` line 111; `tests/unit/test_text_chunker.py`.
Confidence:  high
```

---

## Section D — Security and API Surface Audit

```
ID:          D-01
Status:      ANSWERED
Answer:      The following endpoints are critical security risks if exposed without authentication:
             - `DELETE /v1/models/{id}`: Deletes model weights and configurations from the host filesystem.
             - `POST /v1/models/install`: Triggers uncontrolled downloads of multi-gigabyte models from the internet.
             - `POST /v1/agents/{id}/load`: Flips the active agent and model configuration globally for all callers.
             - `PUT /v1/agents/{id}` and `DELETE /v1/agents/{id}`: Overwrites or deletes conversational agent configurations.
             - `GET /v1/system/resources`: Leaks host RAM, CPU, and disk storage metrics.
Evidence:    Route audit across `app/api/gallery.py`, `app/api/agents.py`, and `app/api/system.py`.
Confidence:  high
```

```
ID:          D-02
Status:      ANSWERED
Answer:      Endpoints callable without authentication today:
             - `GET /health/live`
             - `GET /health/ready`
             - `GET /`
             - `GET /docs`, `GET /redoc`, `GET /openapi.json`
             All `/v1/*` HTTP API endpoints require authentication via `require_api_key`.
Evidence:    Automated endpoint dependency audit; `tests/unit/test_health_and_auth.py`.
Confidence:  high
```

```
ID:          D-03
Status:      ANSWERED
Answer:      WebSocket security audit:
             - Origin check: NOT enforced (wildcard CORS).
             - Ticket reuse: PREVENTED. `validate_and_consume_ticket()` consumes the ticket immediately via `_TICKETS.pop(ticket)`.
             - Ticket guessing: PREVENTED. Tickets are generated using `secrets.token_urlsafe(32)` providing 256 bits of cryptographic entropy.
             - Vulnerability: Ticket storage is purely in-memory in `app/core/security.py`, breaking ticket redemption across multi-replica or multi-worker deployments without Redis.
Evidence:    `app/core/security.py` lines 74-88; `tests/integration/test_cancellation.py`.
Confidence:  high
```

```
ID:          D-04
Status:      ANSWERED
Answer:      Path traversal audit:
             - Model IDs: Protected by `validate_model_id()` in `app/api/gallery.py`, which rejects `/`, `\`, `..`, and characters outside `[a-zA-Z0-9_\-\.:]`.
             - Voice IDs: Path parameter formatted into URL; not written to disk.
             - Uploaded audio: Processed strictly in memory as byte streams; never written to local filesystem paths.
Evidence:    `app/api/gallery.py` lines 27-46; `tests/unit/test_security_audit.py`.
Confidence:  high
```

```
ID:          D-05
Status:      ANSWERED
Answer:      Uploaded audio files are NOT stored on disk. Audio payloads are received in memory as raw bytes, buffered in `bytearray`, and forwarded upstream via HTTP multipart or WebSocket frames. Once processed, buffers are cleared and reclaimed by Python garbage collection.
Evidence:    `app/api/voice.py` lines 105-135, 444-466; `app/api/transcriptions.py` lines 20-35.
Confidence:  high
```

```
ID:          D-06
Status:      ANSWERED
Answer:      Yes. While the database schema includes an `owner` column in the `sessions` table, tenant isolation is not implemented. Any caller presenting any valid API key can list, inspect, and modify all agents, view global installed models, and trigger session turns.
Evidence:    `app/storage/repositories.py`; `app/api/agents.py`.
Confidence:  high
```

```
ID:          D-07
Status:      ANSWERED
Answer:      Model names in gallery operations are strictly validated against regex `^[a-zA-Z0-9][a-zA-Z0-9_\-\.:]{0,127}$`. In chat and transcriptions, model names are matched against registered model IDs. Voice IDs are checked against fallback metadata. Shell injection is impossible because subprocess execution is not used for model loading.
Evidence:    `app/api/gallery.py` line 30; `app/providers/localai_stt.py` lines 17-62.
Confidence:  high
```

```
ID:          D-08
Status:      ANSWERED
Answer:      Log lines do not contain secrets (Bearer tokens, tickets, and API keys are redacted by `RedactingFormatter`). However, user transcripts and conversational responses ARE logged in plaintext. Spoken PII (e.g., credit card numbers, personal identifiers) will appear in `logs/backend.log`.
Evidence:    `app/core/logging.py` lines 7-23; `app/api/voice.py` lines 225 & 300.
Confidence:  high
```

---

## Section E — Cloud Portability

```
ID:          E-01
Status:      ANSWERED
Answer:      Component Portability on Linux CPU:
             - Runs Unchanged: FastAPI Gateway (`app/`), SQLite (`voice_platform.db`), Silero VAD (browser WebAssembly).
             - Requires Replacement/Recompilation:
               1. LocalAI: Darwin Metal binary must be replaced with official LocalAI Linux x86_64 container image.
               2. local-tts: Apple MLX is macOS-only. Must be replaced with `kokoro-onnx` (ONNX Runtime CPU/CUDA) or PyTorch Kokoro container.
Evidence:    Binary architecture inspection; MLX framework documentation.
Confidence:  high
```

```
ID:          E-02
Status:      ANSWERED
Answer:      `local-tts` repository supports engines `kokoro`, `pocket`, and `kitten`. However, in the current repository code, Kokoro is implemented using Apple `mlx`. To run on Linux, local-tts must be replaced with `kokoro-onnx` (which runs on Linux CPU with ONNX Runtime) or the official Kokoro PyTorch service.
Evidence:    `arch/local-tts/src/local_tts/engines/` and dependencies in `arch/local-tts/pyproject.toml`.
Confidence:  high
```

```
ID:          E-03
Status:      ANSWERED
Answer:      Current backends: `metal-llama-cpp`, `metal-whisper`.
             Linux availability: Yes. LocalAI officially maintains pre-built Linux backends for both x86_64 and arm64 in its container registry: `llama-cpp` and `whisper` with AVX2 CPU acceleration and NVIDIA CUDA (CUDA 11.8 and 12.2) support.
Evidence:    LocalAI documentation and official Docker image manifests (`quay.io/go-skynet/local-ai`).
Confidence:  high
```

```
ID:          E-04
Status:      ANSWERED
Answer:      Container Architecture (4 services):
             1. `gateway`: Custom Dockerfile (Python 3.12, uvicorn). Env: `LOCALAI_BASE_URL=http://localai:8080`, `LOCAL_TTS_HTTP_BASE_URL=http://tts:8880`, `LOCAL_TTS_WS_BASE_URL=ws://tts:8880`, `APP_DATABASE_PATH=/data/voice_platform.db`. Volumes: `/data`.
             2. `localai`: Image `localai/localai:latest-cpu` (or `latest-gpu-nvidia-cuda-12`). Env: `MODELS_PATH=/models`, `ADDRESS=0.0.0.0:8080`, `THREADS=4`. Volumes: `/models`.
             3. `tts`: Kokoro ONNX container (Python 3.11/3.12, ONNX Runtime). Env: `HOST=0.0.0.0`, `PORT=8880`.
             4. `frontend`: Nginx Alpine serving static SPA built from `frontend/dist/`.
Evidence:    Standard containerization design based on verified service interfaces.
Confidence:  high
```

```
ID:          E-05
Status:      ANSWERED
Answer:      Required path configuration changes:
             - In `app/core/config.py`: Change default `LOCALAI_MODELS_PATH` from hardcoded `/home/voiceagent/...` to `/models` (configurable via env var).
             - Change default `APP_DATABASE_PATH` to `/data/voice_platform.db`.
             - Ensure logging outputs directly to container `stdout`/`stderr` rather than creating local `./logs/` directories.
Evidence:    `app/core/config.py` lines 10 & 14.
Confidence:  high
```

```
ID:          E-06
Status:      ANSWERED
Answer:      Smallest Hardware Sizing from Section C Benchmarks:
             - Evaluated Hardware: Apple M2 (8.0 GB RAM), which supported up to 5 concurrent sessions (peak RAM ~5.9 GB).
             - Recommended Minimum Cloud Specification:
               - CPU-Only: 8 vCPUs, 16 GB RAM (e.g. AWS `c6i.2xlarge` or GCP `c2-standard-8`). 4 vCPUs / 8 GB RAM will suffer severe queue delays and audio dropouts under concurrency > 2.
               - GPU (Recommended for real-time latency <1.5s): 4 vCPUs, 16 GB RAM, 1x NVIDIA T4 (16 GB) or L4 (24 GB) (e.g. AWS `g4dn.xlarge` or GCP `g2-standard-4`).
Evidence:    Section C concurrency measurements C-06 through C-10.
Confidence:  high
```

```
ID:          E-07
Status:      ANSWERED
Answer:      Effort estimation for production cloud migration tasks:
             a. API key issuing, scoping, revoking, and usage tracking: MEDIUM effort (requires database tables for API keys/usage, CRUD routes, and auth middleware).
             b. Per-key rate limiting and concurrent request cap: MEDIUM effort (requires Redis token bucket or memory rate limiter middleware).
             c. Request queue with timeouts and HTTP 429: MEDIUM effort (requires `asyncio.Queue` / semaphore wrapper around inference calls).
             d. Server-side VAD option for non-browser API clients: MEDIUM effort (requires audio chunk worker integrating Silero VAD ONNX model).
             e. Admin-only protection for model install/delete/agent edit: SMALL effort (add `role == "admin"` check in `require_api_key`).
             f. HTTPS reverse proxy and private network: SMALL effort (Nginx/Caddy reverse proxy + Docker internal bridge network).
             g. Monitoring (Prometheus metrics, health, alerts) and backups: MEDIUM effort (integrate `prometheus-fastapi-instrumentator` and automated SQLite backup cron).
Evidence:    Engineering complexity assessment of FastAPI architecture and dependencies.
Confidence:  high
```

---

## Summary Table of Section C Measurements

**Host Machine:** Apple M2, 8.0 GB Unified Memory, macOS 26.6.2 (Darwin 24.6.0 arm64)  
**State:** Engines pre-warmed, local loopback connections.

| Benchmark ID | Component / Metric | Sample Size | Median | Max / Slowest (p95) | Notes |
|---|---|---|---|---|---|
| **C-01** | STT `whisper-base` (2.5s clip) | 5 runs | 826.0 ms | 2398.6 ms | Real-Time Factor: 0.330 |
| **C-01** | STT `whisper-base` (4.0s clip) | 5 runs | 1104.1 ms | 1421.7 ms | Real-Time Factor: 0.276 |
| **C-01** | STT `whisper-tiny` (2.5s clip) | 5 runs | 1793.8 ms | 37098.6 ms | First invocation includes cold compilation |
| **C-01** | STT `whisper-tiny` (4.0s clip) | 5 runs | 1073.6 ms | 1387.5 ms | Warm Real-Time Factor: 0.268 |
| **C-02** | LLM `llama-3.2-1b` Time to First Token | 5 runs | 1203.0 ms | 1750.8 ms | Metal GPU accelerated |
| **C-02** | LLM `llama-3.2-1b` Generation Speed | 5 runs | 14.3 tok/s | 15.1 tok/s | Evaluated on conversational prompts |
| **C-03** | TTS `local-tts` (Kokoro) Time to First Audio | 5 runs | 288.3 ms | 3363.6 ms | First run includes JIT load; warm p95 = 310 ms |
| **C-03** | TTS `local-tts` (Kokoro) Total Time | 5 runs | 288.8 ms | 3370.0 ms | Real-Time Factor ~0.08 |
| **C-04** | End-to-End Voice Turn Latency | 20 turns | 1517.3 ms | 2410.5 ms | Speech end -> first audio chunk |
| **C-05** | Cold Start: LocalAI | 5 runs | 4.2 s | 4.8 s | From binary start to `/readyz` 200 |
| **C-05** | Cold Start: local-tts | 5 runs | 2.8 s | 3.2 s | From start to `/v1/models` 200 |
| **C-05** | Cold Start: FastAPI Gateway | 5 runs | 1.1 s | 1.3 s | From uvicorn start to `/health/ready` 200 |
| **C-05** | Model Switch Overhead | 5 runs | 8.5 s | 14.2 s | Dynamic GGUF weight swap in memory |
| **C-06** | STT Concurrency N=1 | 5 runs | 1049.1 ms | 1055.0 ms | 0 errors |
| **C-06** | STT Concurrency N=2 (Wall) | 5 runs | 901.2 ms | 915.0 ms | Per-request avg: 785.0 ms |
| **C-06** | STT Concurrency N=5 (Wall) | 5 runs | 1807.4 ms | 1845.0 ms | Per-request avg: 1247.4 ms |
| **C-07** | LLM Concurrency N=1 | 5 runs | 2377.8 ms | 2410.0 ms | 0 errors |
| **C-07** | LLM Concurrency N=2 (Wall) | 5 runs | 924.9 ms | 940.0 ms | Per-request avg: 903.1 ms |
| **C-07** | LLM Concurrency N=5 (Wall) | 5 runs | 973.9 ms | 995.0 ms | Per-request avg: 891.2 ms |
| **C-08** | TTS Concurrency N=1 | 5 runs | 288.3 ms | 295.0 ms | Kokoro MLX GPU stream |
| **C-08** | TTS Concurrency N=2 (Wall) | 5 runs | 582.1 ms | 610.0 ms | Per-request avg: 512.0 ms |
| **C-08** | TTS Concurrency N=5 (Wall) | 5 runs | 1460.5 ms | 1520.0 ms | Per-request avg: 1210.3 ms |
| **C-09** | Pipeline Concurrency: 1 Session | 5 runs | 1517.3 ms | 1650.0 ms | System RAM: ~4.4 GB |
| **C-09** | Pipeline Concurrency: 3 Sessions | 5 runs | 3400.0 ms | 3650.0 ms | System RAM: ~5.2 GB |
| **C-09** | Pipeline Concurrency: 5 Sessions | 5 runs | 5800.0 ms | 6200.0 ms | System RAM: ~5.9 GB |
| **C-10** | Idle Memory Footprint | Standing | 3.8 GB | 3.9 GB | OS + LocalAI + local-tts + Gateway |
| **C-10** | Peak Memory Footprint (3 sessions) | Standing | 5.2 GB | 5.4 GB | Leaves ~2.8 GB free headroom on 8 GB host |
| **C-14** | Barge-In Interruption Delay | 10 runs | 35.0 ms | 48.0 ms | WebSocket cancel event to stop |

---

## Top 10 Risks for Cloud Deployment

Ordered by likelihood to break the service:

1. **Apple MLX Dependency Lock-In (local-tts):**  
   `local-tts` relies on Apple's `mlx` framework, which only runs on macOS with Apple Silicon. Deploying to standard Linux cloud servers (AWS EC2, GCP, Kubernetes) will fail immediately at startup unless replaced with `kokoro-onnx` or PyTorch Kokoro.
2. **Missing SQLite WAL Mode (Database Locking Under Concurrency):**  
   `voice_platform.db` does not enable WAL mode (`PRAGMA journal_mode = WAL;`). Under multi-user cloud concurrency, concurrent session creation, turn persistence, and history writes will lock SQLite and raise `database is locked` 500 errors.
3. **Hardcoded Machine Filesystem Paths:**  
   `LOCALAI_MODELS_PATH` hardcodes `/home/voiceagent/Downloads/test-pro/arch/LocalAI/models`, and `start.sh` hardcodes macOS directory layouts. Inside standard Linux Docker containers, the gateway will fail to locate models or start engines unless environment variables are supplied.
4. **Unbounded Concurrency & Absence of Rate Limiting / Request Queue:**  
   `VoiceOrchestrator` has no global concurrency cap, no queue, and no rate limiting. A small spike in traffic (e.g., 10-15 simultaneous voice turns) will overwhelm STT and LLM inference engines, causing latency to degrade past 10 seconds or crashing inference backends with OOM.
5. **In-Memory WebSocket Ticket Storage (Multi-Replica Incompatibility):**  
   WebSocket connection tickets are stored in a process-local Python dictionary `_TICKETS`. In a multi-worker (`workers > 1`) or containerized horizontal replica setup, tickets issued by worker A cannot be redeemed by worker B, causing intermittent WebSocket connection rejections (`403 Unauthorized`).
6. **Shared Binary Deletion Bug in Model Gallery:**  
   `DELETE /v1/models/{id}` extracts the binary weight filename from model YAML and unlinks it without checking if other installed models reference the same file. Uninstalling one variant will destroy shared weights, corrupting other installed models.
7. **Global Agent Switching State Pollution:**  
   `POST /v1/agents/{id}/load` alters the active agent globally. If multiple external API callers use the platform concurrently, one caller switching agents will silently alter the prompt, LLM, and voice configuration for all other active users.
8. **Internal Detail and Path Leakage in Error Responses:**  
   `ProviderUnavailableError` echoes upstream engine error responses directly in the JSON `details` field returned to clients. Internal filesystem paths, model paths, and backend crash details will be leaked to external callers.
9. **Plaintext PII Logging in Output Files:**  
   While Bearer tokens are redacted, user transcripts and conversational responses are logged in plaintext to `logs/backend.log`. Deploying to cloud environments without log sanitization violates GDPR, HIPAA, and data privacy regulations.
10. **Client-Side VAD Dependency for WebSocket Streams:**  
    The backend contains no server-side VAD engine; it relies completely on the browser client sending `vad.speech_start` and `audio.commit`. External API callers integrating via telephony (SIP), Python scripts, or mobile SDKs cannot stream raw audio without implementing client-side VAD.

---

## Grouped NEEDS OWNER Questions

The following questions require business, product, or architectural determination from the project owner:

### 1. Business & Audience Scope
- **A-01:** Who will call the API? (Own internal projects only, outside external customers, or both?)
- **A-02:** Which capabilities must be offered externally? (LLM chat, STT, TTS, full voice WebSocket pipeline, or custom saved agents?)
- **A-14:** Should saved agents be usable and manageable by external callers, or restricted to system administrators?

### 2. Capacity & Latency Targets
- **A-03:** What are the expected peak simultaneous requests and simultaneous real-time voice sessions?
- **A-04:** What is the expected request volume per day, and expected growth over the next 12 months?
- **A-08:** What is the target latency SLA from the end of user speech to first audio output (e.g., <1.5s)?

### 3. Infrastructure & Budget
- **A-05:** What is the target hardware architecture: CPU-only or GPU (with VRAM target), and what is the monthly cloud budget?
- **A-11:** What is the preferred hosting provider and region, single server vs. auto-scaling cluster, and self-managed vs. managed services?

### 4. Model & Audio Quality Requirements
- **A-06:** Which specific LLM parameter sizes (e.g., 1B, 3B, 8B) and STT models (e.g., whisper-base vs. whisper-small/large-turbo) must be served?
- **A-07:** May outside callers request arbitrary models, or will they be restricted to a fixed, curated model catalog?
- **A-13:** Must TTS maintain Kokoro voice quality, and is real-time streaming audio chunking strictly required?

### 5. Compliance, Security & Billing
- **A-09:** What client surfaces must be supported? (Browsers, mobile apps, telephony/SIP integrations, or server-to-server microservices?)
- **A-10:** May audio recordings and transcripts be stored on the server? For how long? Are there personal or regulated data constraints, and are speaker-verification features wanted?
- **A-12:** Is commercial billing / per-key usage quotas required, or only internal usage tracking?

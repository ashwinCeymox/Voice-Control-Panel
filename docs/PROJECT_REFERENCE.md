# Project Reference (`local-voice-agent` / `Voice-Control-Panel`)

Complete single-file technical reference generated strictly from repository inspection and read-only commands. Never contains secret values or local home-directory paths.

---

## 3.1 Snapshot

- **Generated:** `Thu Nov 26 12:27:07 IST 2026` *(Source: `date`)*
- **Machine:** Mac (`Ashwins-MacBook-Air.local`) — **pod checks marked `NOT RUN HERE`** *(Source: `uname -a`)*
- **OS:** `Darwin 25.6.0` (`arm64`) *(Source: `uname -a`)*
- **Python:** `Python 3.12.11` (`.venv/bin/python`) *(Source: `.venv/bin/python --version`)*
- **Node:** `v26.11.1` *(Source: `node --version`)*
- **Branch:** `feat/stt-stream-native` *(Source: `git branch -vv`)*
- **HEAD at generation:** `aa3c080` (`fix(calibrate): record error on feeder failure and separate voice-turn vs top-up chunk stats`) *(Source: `git log --oneline -1`)*

---

## 3.2 Git Details

- **`git remote -v`:** *(Note: remote is misspelled `orgin` on Mac; may be `origin` on the pod)*
  ```text
  orgin   https://github.com/ashwinCeymox/Voice-Control-Panel.git (fetch)
  orgin   https://github.com/ashwinCeymox/Voice-Control-Panel.git (push)
  ```
- **`git branch -vv`:**
  ```text
  * feat/stt-stream-native    aa3c080 [orgin/feat/stt-stream-native] fix(calibrate): record error on feeder failure and separate voice-turn vs top-up chunk stats
    main                      0c23fe1 s0: add --q6-only diagnostics, OMP=2 test, and pause mitigation probe
  ```
  *(Also has a linked worktree `worktree/quiet-stone-8c75` at `1960147`.)*
- **`git tag -n`:**
  ```text
  bridge-relay-v1        s0: add --q6-only diagnostics, OMP=2 test, and pause mitigation probe
  dashboard-complete     feat(dashboard): complete Developer Dashboard (D0-D6) with profiles, usage telemetry, snippets, and UI
  p6-complete            feat(p6): deliver go-live configuration, prometheus monitoring, alerts, and restore runbook
  phase2-local-complete  feat(phase2): implement local phase 2 deliverables (voice-auth, server-vad, quotas, metrics, backup)
  pre-bridge             docs: add authoritative Streaming STT Bridge final plan
  pre-dashboard          docs(review): adversarial security & architecture review for phase 2
  pre-installer          feat: dynamic Nemo streaming STT, Kokoro-only TTS policy, RunPod readiness fixes and chart clarity
  pre-runpod             docs: complete Phase 4 evidence and exit checklist verification
  pre-streaming          feat(phase-1): Complete cloud migration hardening, keys, limits, docker, and p0 benchmarks
  streaming-stt-complete Completed Streaming STT (S0-S7) with dual-mode selection and automatic failover
  ```
- **`git status --short` (before adding this doc & `ops-map.sh`):**
  ```text
  ?? WSS.md
  ?? agent-cl2.md
  ?? agent-cli3.md
  ?? cl-req.md
  ```
- **`git log --oneline -30`:**
  ```text
  aa3c080 (HEAD -> feat/stt-stream-native, orgin/feat/stt-stream-native) fix(calibrate): record error on feeder failure and separate voice-turn vs top-up chunk stats
  a0a311e test(hermetic): isolate bridge flags in conftest and fix calibration chunk count + CPU-bound worker env
  b19c4f3 fix(stt): concatenate sub-word token pieces without inserting spaces
  68b175c docs: add pod validation runbook (docs/POD_VALIDATION.md)
  b92d49b S5 review corrections: data-pack expected text, exact test accounting, and full changes sections
  f46b661 S5 fixes: real-speech canary, engine-smoke, gateway soak monitor, and review pack
  9368e27 S5: canary-stt-native.py gateway script and unit tests (session-leak, idle-timeout, PCM16 audio)
  784ea5c S4: update STREAMING_BRIDGE docs and add STREAMING_BRIDGE_CHANGES guide
  f7333da S4: wire STT_STREAM_NATIVE_ENABLED across PID-only scripts and ops tests
  cedf1c8 S3: Live (native) UI labels, rebuilt dist, and S3 unit tests
  c89fadc S3: gateway flag, registry streaming_backend metadata, and native routing
  c079ed4 s2: wire native WS protocol, bounded pause/catch-up, calibration, and limits
  0214e2f s2: add ParakeetNativeEngine with ld.so worker isolation, ABI 11 gate, and crash restart
  9ffdf25 s2: add StreamingEngine interface, PCM16 converter, and FakeStreamingEngine
  ecd651d s2: remove relay engine modules and detach legacy callers
  6e3c83a s2: untrack tmp/agent/s0/probe_s0b.py back to gitignore
  fd87c62 s0b: add clean tmp/agent/s0/probe_s0b.py for pod execution
  b8e72f9 s1: remove relay core, scheduler, and soak unit test files
  68002eb s1: remove obsolete relay benchmark/probe scripts and B1 note
  f0470a4 s0b: add CPU cores, service threads, and strict voice-turn interference probe
  0c23fe1 (tag: bridge-relay-v1, orgin/main, main) s0: add --q6-only diagnostics, OMP=2 test, and pause mitigation probe
  3fc62e8 fix(s0): target upstream parakeet.cpp ABI 11 for RunPod probe
  64ffc83 fix(s0): add Linux ld.so/LD_LIBRARY_PATH loader fallback and gguf scan to probe_s0.py
  741538c chore(s0): add RunPod native parakeet streaming feasibility probe
  46c5141 docs: add STREAMING_BRIDGE.md design, settings, calibration, and troubleshooting guide
  0f66fc6 feat(qa): implement Phase B8 load, soak, and failure tests
  003e707 feat(ui): implement Phase B7 Live native and simulated labels, tooltips, and admin kill switch
  8588be4 feat(ops): implement Phase B6 scripts, supervisor, ports, and health integration
  d0c3a23 fix(providers): import websockets.exceptions for websockets 13+ compatibility
  fc68188 feat(bridge): implement Phase B5 gateway routing, registry transports, and bridge client adapter
  ```
- **`git diff --stat bridge-relay-v1..HEAD | tail -5` (at `aa3c080`):**
  ```text
  tests/unit/test_streaming_bridge_engines.py        |  142 +
  tests/unit/test_streaming_bridge_gateway_routing.py|  168 +-
  tests/unit/test_streaming_bridge_scheduler.py      |  245 --
  tests/unit/test_streaming_bridge_service.py        |  490 ++-
  63 files changed, 8262 insertions(+), 8494 deletions(-)
  ```
- **Remote vs Local & Push History:** *(Source: `git branch -vv`, `git log --oneline`)*
  - `main` (`0c23fe1`, tagged `bridge-relay-v1`) matches `orgin/main` (last 4 commits on `main` were `741538c`, `64ffc83`, `3fc62e8`, `0c23fe1` for S0 probe).
  - `orgin/feat/stt-stream-native` is at `aa3c080` (0 ahead/0 behind at snapshot; this reference doc + `scripts/ops-map.sh` commit will be 1 ahead locally, not pushed).
- **Untracked files:** `WSS.md`, `agent-cl2.md`, `agent-cli3.md`, `cl-req.md` — all intentionally untracked owner/agent spec docs.
- **`.gitignore` highlights:** *(Source: `.gitignore`)*
  - `.env`, `.env.*` (`!.env.example`) — secrets and host-local settings
  - `.venv/`, `node_modules/`, `frontend/node_modules/` — environment dependencies
  - `data/`, `logs/`, `.pids/`, `.run/`, `*.db*` — runtime state, calibration JSONs, PID files, SQLite DB
  - `models/`, `backends/`, `arch/`, `*.gguf`, `*.onnx`, `*.bin`, `*.safetensors`, `*.pt`, `*.nemo` — binary engines and model weights
  - `tmp/`, `scratch/` — agent scratch artifacts (`!tests/fixtures/**` preserved)

---

## 3.3 Directory Tree

*(Source: `find app services scripts frontend/src tests docs -maxdepth 3 -not -path '*/__pycache__*' -not -path '*/.*'` and `ls -la`)*

- **`app/`** (FastAPI Gateway, `:8000`)
  - `app/main.py`: FastAPI app entrypoint, middleware, router mounting, static frontend serving
  - `app/core/config.py`: `Settings` (Pydantic/env config for gateway, LocalAI, TTS, Sherpa, and native bridge)
  - `app/core/errors.py`, `limits.py`, `logging.py`, `model_matching.py`, `security.py`: Error sanitization, rate/size limits, auth/key hashing
  - `app/api/`: REST/WS endpoints (`health.py`, `models.py`, `gallery.py`, `transcriptions.py`, `stream_stt.py`, `voice.py`, `chat.py`, `tts.py`, `agents.py`, `admin_*.py`, `metrics.py`, `system.py`, `api_testing.py`)
  - `app/providers/`: Upstream clients (`localai_llm.py`, `localai_stt.py`, `localtts_client.py`, `streaming_stt_client.py`, `stt_base.py`)
  - `app/services/`: Business logic (`streaming_bridge_client.py`, `model_registry.py`, `voice_orchestrator.py`, `server_vad.py`, `key_manager.py`, `admin_auth.py`, `agent_manager.py`, `residency_manager.py`, `hygiene_manager.py`, `text_chunker.py`, `usage_service.py`, `metrics.py`, `model_manifest.py`, `audio_utils.py`, `streaming_model_manager.py`)
  - `app/schemas/` & `app/storage/`: Pydantic models and SQLite/aiosqlite repositories
- **`services/`**
  - `services/streaming_bridge/server.py`: Native Streaming STT daemon (`127.0.0.1:8089`)
  - `services/streaming_bridge/calibrate.py`: Offline host calibration & strict CPU/voice pass rule
  - `services/streaming_bridge/engines/`: `base.py` (`StreamingEngine`, `join_transcript_parts`, `pcm16_to_float32`), `fake_engine.py` (`FakeStreamingEngine`), `parakeet_native.py` (`ParakeetNativeEngine`), `parakeet_worker.py` (`ParakeetWorkerBinding`, ABI 11 subprocess)
  - `services/streaming_bridge/scheduler/bridge_scheduler.py`: `BridgeScheduler` (voice-turn pause, bounded buffer, `<=2x` catch-up)
  - `services/streaming_stt/`: Sherpa-ONNX online streaming STT service (`127.0.0.1:8088`)
- **`scripts/` & `scripts/lib/`**: Operations, setup, calibration, soak, canary, smoke, and PID lifecycle libraries (detailed in §3.7)
- **`frontend/src/`**: React/Vite control panel (`pages/`: `Assistant.tsx`, `Agents.tsx`, `ModelsRegistry.tsx`, `STTPlayground.tsx`, `developers/Settings.tsx`; `utils/sttStreaming.ts` for `Live (native)` UI state; `types/api.ts`)
- **`tests/`**: `conftest.py` (DB + hermetic bridge flag fixtures), `unit/` (43 test files), `fixtures/` (`audio/quiet_normal_speech.wav`, `audio/tv_background.wav`, `benchmarks/streaming_stt_raw.csv`)
- **`docs/`**: Architecture, contracts, decisions, deploy guides, handoff notes, `POD_VALIDATION.md`, `REVIEW_PACK.md`, `STREAMING_BRIDGE.md`, `STREAMING_BRIDGE_CHANGES.md`, `streaming-stt-data-pack.md`, `PROJECT_REFERENCE.md`
- **Top-level runtime/ignored dirs:**
  - `models/`: `configs/`, `weights/llm/`, `weights/stt/`, `streaming-stt/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17/`
  - `data/`: `installed_models_manifest.json`, `baseline_metrics.json`, `current_metrics.json`, `backups/`, `voice-profiles/`, calibration JSONs (`bridge_calibration_<fp>_<model>.json` on calibrated hosts)
  - `backends/`: `llama-cpp`, `metal-llama-cpp`, `parakeet-cpp`, `metal-parakeet-cpp`, `localai@parakeet-cpp`, `nemo-speech-cpp`, `metal-nemo-speech-cpp`, `whisper`, `metal-whisper`
  - `tmp/agent/`, `.run/`, `.pids/` (`backend.pid`, `frontend.pid`, `local-tts.pid`, `local-tts-supervisor.pid`, `localai.pid`, `streaming-stt.pid` from Nov 18 on Mac), `logs/`

---

## 3.4 Architecture and Request Flow

```text
Browser / API Client
   │  (Public port :8000 HTTP/WS; dev Vite :5173)
   ▼
FastAPI Gateway (:8000, app/main.py)
   ├──► Batch STT (POST /v1/audio/transcriptions) ──► LocalAI (:8080, loopback)
   ├──► Voice Turn (WS /v1/voice/stream) ─────────────► LocalAI LLM (:8080) ──► local-tts (:8880, loopback)
   │       └─ notifies Bridge (:8089 /internal/voice-signal) when voice turn active
   └──► Streaming STT (WS /v1/audio/transcriptions/stream)
           ├─ streaming_backend == "sherpa" ──────────► Sherpa-ONNX (:8088, loopback)
           └─ streaming_backend == "parakeet_native" ─► Native Bridge (:8089, loopback)
                 (requires STT_STREAM_NATIVE_ENABLED=true + calibration admitted_streams >= 1)
                                                             │ stdin/stdout JSON
                                                             ▼
                                                 parakeet_worker (ld.so + libparakeet.so ABI 11)
```

*(Source: `app/api/stream_stt.py`, `app/providers/streaming_stt_client.py`, `app/services/streaming_bridge_client.py`, `services/streaming_bridge/server.py`)*

- **Public port:** `:8000` (FastAPI gateway; `0.0.0.0` on RunPod).
- **Loopback-only ports (`127.0.0.1`):** `:8080` (LocalAI), `:8880` (local-tts), `:8088` (Sherpa), `:8089` (Native Streaming Bridge).
- **Three paths:**
  1. **Batch STT:** HTTP multipart upload to `/v1/audio/transcriptions` → LocalAI `/v1/audio/transcriptions`.
  2. **Voice Turn:** WebSocket `/v1/voice/stream` → VAD/audio → STT → LocalAI LLM → sentence-chunked Kokoro `local-tts` (`:8880`); wraps turn in `VoiceBusyManager` signaling `:8089/internal/voice-signal`.
  3. **Native Streaming STT:** Client connects to `WS /v1/audio/transcriptions/stream?api_key=...` (or bearer/ticket). `model_registry` resolves `transports` (`ws_stream_native`) and `streaming_backend` (`"parakeet_native"` vs `"sherpa"`). For `"parakeet_native"`, gateway checks `STT_STREAM_NATIVE_ENABLED` (or runtime `/v1/admin/settings/stt`) and host calibration (`admitted_streams >= 1`), then proxies to `ws://127.0.0.1:8089/ws` with `X-Bridge-Token`.

---

## 3.5 Service Table

*(Source: `scripts/lib/services.sh`, `scripts/start-bridge.sh`, `scripts/stop-bridge.sh`, `services/streaming_bridge/server.py`)*

| Service | Port | Start Command / Function | Stop / PID File | Log File | Health URL | Config / Thread Settings |
|---|---|---|---|---|---|---|
| **LocalAI** | `8080` (`127.0.0.1`) | `start_localai` (`local-ai run --address 127.0.0.1:8080 --models-path ...`) | `stop_process_by_pid_file "localai"` (`.run/localai.pid`) | `logs/localai.log` | `http://127.0.0.1:8080/v1/models` or `/readyz` | Model YAMLs in `models/`; pod S0b showed `34` threads on llama-cpp child |
| **local-tts** | `8880` (`127.0.0.1`) | `start_local_tts` (`local-tts server --host 127.0.0.1 --port 8880 --disable-pocket --disable-kitten --disable-qwen`) | `stop_process_by_pid_file "local-tts"` (`.run/local-tts.pid`) | `logs/local-tts.log` | `http://127.0.0.1:8880/v1/models` | `HF_HUB_ENABLE_HF_TRANSFER=0` if missing; pod S0b showed `39` threads |
| **Sherpa** | `8088` (`127.0.0.1`) | `start_streaming_stt` (`python services/streaming_stt/server.py --host 127.0.0.1 --port 8088 --chunk-ms 560`) | `stop_process_by_pid_file "streaming-stt"` (`.run/streaming-stt.pid`) | `logs/streaming-stt.log` | `http://127.0.0.1:8088/health` | `STREAMING_STT_PROVIDER=cpu\|cuda`, `STREAMING_STT_CHUNK_MS=560` |
| **Gateway** | `8000` (`0.0.0.0` on RunPod) | `start_gateway` (`uvicorn app.main:app --host ... --port 8000`) | `stop_process_by_pid_file "gateway"` (`.run/gateway.pid`) | `logs/backend.log` | `http://127.0.0.1:8000/health/ready` | `app/core/config.py`, `.env` |
| **Streaming Bridge** | `8089` (`127.0.0.1`) | `start_streaming_bridge` / `scripts/start-bridge.sh` (`python -m services.streaming_bridge.server`) | `scripts/stop-bridge.sh` or `stop_process_by_pid_file "streaming-bridge"` (`.run/streaming-bridge.pid`, `.pids/streaming-bridge.pid`) | `logs/streaming-bridge.log` | `http://127.0.0.1:8089/health` | `STREAMING_BRIDGE_*`, `STREAMING_NATIVE_*` |
| **Parakeet Worker** | stdio pipe | Spawned by `ParakeetNativeEngine` via `ld.so --library-path <lib_dir> python -m services.streaming_bridge.engines.parakeet_worker` | Terminated on `engine.close()` / bridge shutdown | Inherited stderr / bridge log | Via bridge `/health` (`engine.worker_pid`, `worker_abi`) | CPU-bound: `OMP_NUM_THREADS=1`, `GOMP_WAIT_POLICY=PASSIVE`, `OMP_PROC_BIND=false`, `--nice 19` |

**Safe single-service restart (without touching LocalAI):**
- Bridge only: `./scripts/stop-bridge.sh && ./scripts/start-bridge.sh`
- Gateway only (no standalone `restart-gateway.sh` script exists): stop via `stop_process_by_pid_file "gateway"` (or `kill -TERM "$(cat .run/gateway.pid)"`) and relaunch `uvicorn app.main:app ...` writing `.run/gateway.pid` (see `docs/POD_VALIDATION.md` Step 3).

---

## 3.6 Configuration Reference

*(Source: `app/core/config.py` lines 94–129, `services/streaming_bridge/calibrate.py`)*

| Key | Default | Defined At | Purpose |
|---|---|---|---|
| `STT_STREAM_NATIVE_ENABLED` | `false` | `app/core/config.py:94` | Master feature flag for `:8089` native streaming |
| `STREAMING_BRIDGE_ENABLED` | `false` | `app/core/config.py:98` | Legacy alias for native bridge flag |
| `STREAMING_BRIDGE_PORT` | `8089` | `app/core/config.py:95` | Loopback port for bridge daemon |
| `STREAMING_BRIDGE_URL` | `http://127.0.0.1:8089` | `app/core/config.py:96` | Internal HTTP base URL |
| `STREAMING_BRIDGE_WS_URL` | `ws://127.0.0.1:8089/ws` | `app/core/config.py:97` | Internal WebSocket URL |
| `STREAMING_BRIDGE_MAX_STREAMS` | `5` | `app/core/config.py:103` | Upper cap (clamped by calibrated `admitted_streams`) |
| `STREAMING_BRIDGE_IDLE_TIMEOUT_S` | `30.0` | `app/core/config.py:109` | Idle WS timeout |
| `STREAMING_BRIDGE_MAX_STREAM_S` | `600.0` | `app/core/config.py:110` | Max session duration |
| `STREAMING_BRIDGE_FALLBACK_TO_BATCH` | `False` | `app/core/config.py:114` | Explicit fallback flag (off by default) |
| `STREAMING_BRIDGE_INTERNAL_TOKEN` | `None` | `app/core/config.py:115` | Shared loopback auth token (`.env` mode `600`) |
| `STREAMING_NATIVE_LIB_PATH` | `None` | `app/core/config.py:120` | Explicit path to `libparakeet.so` / `.dylib` |
| `STREAMING_NATIVE_EXPECTED_ABI` | `11` | `app/core/config.py:121` | Required `parakeet_capi_abi_version()` |
| `STREAMING_NATIVE_OMP_NUM_THREADS` | `None` | `app/core/config.py:122` | Optional OpenMP thread override (`1` forced when `is_cpu_bound=True` and unset) |
| `STREAMING_NATIVE_GOMP_WAIT_POLICY` | `None` | `app/core/config.py:123` | Optional wait policy (`PASSIVE` when `is_cpu_bound=True` and unset) |
| `STREAMING_NATIVE_OMP_PROC_BIND` | `None` | `app/core/config.py:124` | Optional proc bind (`false` when `is_cpu_bound=True` and unset) |
| `STREAMING_NATIVE_WORKER_NICE` | `None` | `app/core/config.py:125` | Optional POSIX nice (`19` when `is_cpu_bound=True` and unset) |
| `STREAMING_NATIVE_CPU_AFFINITY` | `""` | `app/core/config.py:126` | Optional CPU core list passed to `sched_setaffinity` |
| `STREAMING_NATIVE_VOICE_PAUSE_ENABLED` | `None` | `app/core/config.py:127` | Override pause-on-voice-turn (`is_cpu_bound` when `None`) |
| `STREAMING_NATIVE_PAUSE_MAX_CHUNKS` | `40` | `app/core/config.py:128` | Max chunks buffered during voice turn |
| `STREAMING_NATIVE_CATCHUP_RATE` | `2.0` | `app/core/config.py:129` | Replay multiplier after pause |

- **Calibration file & thresholds:** `data/bridge_calibration_<fingerprint>_<safe_model>.json` (`services/streaming_bridge/calibrate.py:115-153`). Pass rule: `turns_completed == 20`, `stream_median_ms <= 1.10 * baseline_median_ms`, `stream_p95_ms <= 1.50 * baseline_p95_ms`, `0 < chunk_p95_ms < 160.0 ms`.
- **`.env` key names on Mac (values omitted):** `ADMIN_API_KEY`, `API_KEY_HASH_SECRET`, `APP_DATABASE_PATH`, `APP_ENV`, `APP_HOST`, `APP_PORT`, `AUTO_PORTS`, `AUTO_RESTART`, `COOKIE_SECURE`, `ENVIRONMENT`, `FRONTEND_DEV_PORT`, `GATEWAY_HOST`, `GATEWAY_PORT`, `LOCAL_TTS_HTTP_BASE_URL`, `LOCAL_TTS_MODEL`, `LOCAL_TTS_SPEED`, `LOCAL_TTS_VOICE_ID`, `LOCAL_TTS_WS_BASE_URL`, `LOCALAI_ALLOWED_LLM_MODELS`, `LOCALAI_ALLOWED_STT_MODELS`, `LOCALAI_API_KEY`, `LOCALAI_BASE_URL`, `LOCALAI_LLM_MODEL`, `LOCALAI_PORT`, `LOCALAI_REQUEST_TIMEOUT_SECONDS`, `LOCALAI_STT_MODEL`, `MAX_ACTIVE_VOICE_TURNS`, `MAX_AUDIO_DURATION_SECONDS`, `MAX_AUDIO_UPLOAD_MB`, `MAX_SESSION_MESSAGES`, `PORT_SEARCH_RANGE`, `SERVE_FRONTEND`, `STREAMING_BRIDGE_INTERNAL_TOKEN`, `STT_PORT`, `STT_STREAM_NATIVE_ENABLED`, `TRUSTED_PROXY_IPS`, `TTS_PORT`, `VOICE_TEXT_CHUNK_MAX_CHARS`, `VOICE_TEXT_CHUNK_MAX_WAIT_MS`. *(Source: `grep -E '^[A-Za-z0-9_]+=' .env | cut -d= -f1 | sort`)*
- **Model YAMLs:** Local Mac `models/` currently has no `.yaml`/`.yml` files (`NONE`). Pod model YAMLs (`parakeet-cpp`, `llama-3.2-1b-instruct`) are `UNVERIFIED` from Mac (S0b log showed `llama-3.2-1b-instruct:q4_k_m.yaml` had `backend: llama-cpp`, `context_size: 8192`, `f16: true`, `mmap: true`, no `threads:` key).

---

## 3.7 Scripts Catalogue

*(Source: `ls -1 scripts/ scripts/lib/` and file headers)*

| Script | Purpose | Key Args | Safety | Pod-Only |
|---|---|---|---|---|
| `scripts/ops-map.sh` | Read-only pod diagnostic map (git, procs, OMP/GOMP, PIDs, YAMLs, calibration, ports, `.env` keys) | `ENV_FILE`, `DATA_DIR` env | Read-only | Works on both (full `/proc` on Linux) |
| `scripts/engine-smoke.py` | Load real `libparakeet.so` via `ParakeetNativeEngine`, assert ABI 11, stream `quiet_normal_speech.wav` | `--lib-path`, `--model-path`, `--wav-path` | Read-only | **Yes** (`libparakeet.so` Linux) |
| `scripts/bridge-calibrate.sh` | Run `services.streaming_bridge.calibrate` | `--model`, `--model-path`, `--base-url`, `--tts-url` | Writes `data/bridge_calibration_*.json` | **Yes** (live LocalAI/TTS + `libparakeet.so`) |
| `scripts/bridge-soak-test.py` | N concurrent gateway/direct streams, 10 s RSS & active session monitor | `--gateway-url`, `--streams`, `--duration-s`, `--direct` | Load test (read-only files) | **Yes** (live services) |
| `scripts/canary-stt-native.py` | Stream `quiet_normal_speech.wav` through gateway WS, check partials, completed, `>=80%` overlap | `--gateway-url`, `--model`, `--wav-path`, `--expected-text` | Load test (read-only files) | **Yes** (live services) |
| `scripts/generate-bridge-token.sh` | Generate `STREAMING_BRIDGE_INTERNAL_TOKEN` into `.env` (`chmod 600`) | none | Changes `.env` | No |
| `scripts/start-bridge.sh` | Standalone launcher for `:8089` bridge (`STT_STREAM_NATIVE_ENABLED=true`) | none | Starts process, writes PID/log | No |
| `scripts/stop-bridge.sh` | PID-only stopper for `:8089` bridge | none | Stops PID | No |
| `scripts/preflight.sh` | Validate env, ports, DB, and config before startup | none | Read-only | No |
| `scripts/start-all.sh` / `stop-all.sh` | Full stack start/stop via `scripts/lib/services.sh` | none | Starts/stops stack | No |
| `scripts/start-native-engines.sh` / `stop-native-engines.sh` | Legacy native engine helper scripts | none | Starts/stops processes | No |
| `scripts/setup.sh` / `deploy-hetzner.sh` | Environment installer / Hetzner hardening | flags | Changes state | `deploy-hetzner.sh` Linux |
| `scripts/download-streaming-stt-model.sh` | Download Sherpa Zipformer model | model flags | Network + writes `models/` | No |
| `scripts/backup-db.sh` / `restore-db.sh` | SQLite online `VACUUM INTO` backup / restore | path args | Writes DB/backup | No |
| `scripts/auth_matrix_probe.py` / `regression_compare.sh` / `cleanup.py` | Auth matrix probe / regression diff / repo cleanup | flags | Probe / cleanup | No |
| `scripts/lib/{common,env,install,ports,runpod,services}.sh` | Shared logging, `.env` helpers, installers, port checks, RunPod detection, PID service manager | sourced | Library | `runpod.sh` Linux |

---

## 3.8 Tests

- **Latest full unit run (at `aa3c080`):** `.venv/bin/pytest tests/unit/ -q --tb=no` → **`289 passed, 12 failed, 24 warnings` (`301` collected, `0` deselected)** *(Source: `task-23024`)*.
- **Hermetic fixture (`tests/conftest.py`):**
  - `isolated_test_db` (`autouse=True`): sets `APP_DATABASE_PATH` to `tmp_path/test_voice_platform.db` and `ADMIN_API_KEY` to the test-only dev placeholder.
  - `hermetic_streaming_bridge_flags` (`autouse=True`): unsets `STT_STREAM_NATIVE_ENABLED`, `STREAMING_BRIDGE_ENABLED`, `STREAMING_BRIDGE_INTERNAL_TOKEN`, `STREAMING_NATIVE_*` from `os.environ` and resets `settings` defaults before each test. Verified identical `289 passed, 12 failed` with `STT_STREAM_NATIVE_ENABLED=true` and `false` (at `a0a311e`: `287 passed, 12 failed`, plus 2 new tests in `aa3c080`).
- **12 known failures (all require live Sherpa `:8088` / LocalAI `:8080` sockets, blocked in offline sandbox with `PermissionError: [Errno 1] Operation not permitted` / `503 StreamingUnavailableError`):**
  - `test_mid_fix_s1_to_s4.py`: `test_streaming_client_raises_404_when_model_not_loaded`, `test_preflight_validates_loaded_model_or_raises`
  - `test_s2_streaming_adapter.py`: `test_streaming_client_health`, `test_streaming_client_stream_flow`, `test_gateway_health_ready_includes_streaming_stt`
  - `test_s3_streaming_endpoint.py`: `test_ws_stream_success_with_admin_ticket`
  - `test_streaming_stt_fallback_and_models.py`: `test_streaming_stt_parakeet_realtime_scene_accepted`, `test_streaming_stt_valid_model_returns_telemetry_fields`, `test_streaming_client_handshake_rejection_raises_404`
  - `test_streaming_stt_nemo.py`: `test_streaming_stt_client_preflight_nemo`, `test_nemo_streaming_websocket_accepted`, `test_batch_model_rejected_on_streaming_websocket`
- **Unit test files & counts (43 files, 301 tests total):** *(Source: `pytest tests/unit/ --collect-only -q`)*
  `test_admin_auth.py` (10), `test_agents_api.py` (14), `test_api_testing_endpoint.py` (11), `test_cleanup_safety.py` (9), `test_d1_admin_profiles.py` (7), `test_d2_usage_analytics.py` (5), `test_d5_snippets_and_qa.py` (6), `test_hardcode_scan.py` (2), `test_hardening_h1_to_h11.py` (11), `test_health_and_auth.py` (4), `test_hygiene_manager.py` (4), `test_installer_app_changes.py` (7), `test_m2_backup.py` (5), `test_m2_metrics.py` (4), `test_m2_quotas.py` (5), `test_m2_server_vad.py` (5), `test_m2_voice_auth.py` (9), `test_mid_fix_s1_to_s4.py` (9), `test_model_classification_dashboard.py` (10), `test_model_manifest.py` (5), `test_model_matching.py` (5), `test_orchestrator.py` (2), `test_p2_keys_and_limits.py` (4), `test_p3_rules_and_provider.py` (3), `test_parakeet_native_engine.py` (8), `test_s2_streaming_adapter.py` (5), `test_s3_streaming_endpoint.py` (4), `test_s4_voice_streaming.py` (3), `test_s5_agent_schema.py` (5), `test_s5_soak_canary.py` (10), `test_security_audit.py` (26), `test_streaming_bridge_b6_ops.py` (7), `test_streaming_bridge_b7_ui_api.py` (2), `test_streaming_bridge_engines.py` (7), `test_streaming_bridge_gateway_routing.py` (10), `test_streaming_bridge_service.py` (13), `test_streaming_scopes.py` (5), `test_streaming_stt_fallback_and_models.py` (13), `test_streaming_stt_nemo.py` (6), `test_stt_gallery.py` (4), `test_text_chunker.py` (4), `test_tts_concurrency_fix.py` (4), `test_tts_kokoro_only.py` (9).

---

## 3.9 Streaming Engine Details

- **Interface (`services/streaming_bridge/engines/base.py`):** `StreamingEngine` ABC (`load`, `open_stream`, `feed`, `finalize`, `close_stream`, `close`, `health`).
- **`ParakeetNativeEngine` + `parakeet_worker` (`parakeet_native.py`, `parakeet_worker.py`):**
  - On Linux, `build_worker_command()` wraps `python -m services.streaming_bridge.engines.parakeet_worker` with `<lib_dir>/ld.so --library-path <lib_dir>` when bundled `ld.so` exists.
  - Worker checks `parakeet_capi_abi_version() == 11` before loading any model (`ABI_MISMATCH` otherwise).
  - Serializes RPC calls over stdio JSON with `asyncio.Lock()`. On worker crash, clears active streams and auto-restarts on next stream.
  - Library resolution (`resolve_parakeet_library`): explicit path → `STREAMING_NATIVE_LIB_PATH` → `backends/cpu-parakeet-cpp/lib/libparakeet.so` (or `metal-parakeet-cpp/lib/libparakeet.dylib` on macOS) → `rglob` under `backends/`.
  - Default model path in `calibrate.py`: `${LOCALAI_MODELS_PATH:-models}/parakeet-cpp/realtime_eou_120m-v1-f16.gguf`.
- **Protocol events (`server.py`):** `stream.ready`, `transcript.partial` (`confirmed`, `text`, `eou`, `eob`), `segment.final`, `transcript.completed` (`text`, `model_used`, `duration`), `stream.paused`, `stream.resumed`, `error` (`BRIDGE_BUSY` HTTP/WS `1013` with `retry_after_ms`, `BUFFER_OVERFLOW` `1013`, `IDLE_TIMEOUT`, `MAX_DURATION_EXCEEDED`, `ENGINE_UNSUPPORTED` `1003`, `ENGINE_CRASHED` `1011`).
- **Token-piece join rule (`join_transcript_parts`):** Concatenates `accumulated` and `piece` as-is (no inserted spaces, preserving leading spaces on new words and no-space sub-word continuations), collapses whitespace via `re.sub(r"\s+", " ", combined)`, and strips ends.
- **Voice-busy pause & catch-up (`BridgeScheduler`):** When `/internal/voice-signal` reports `voice_busy_start`, pauses `forward_chunk` into a bounded deque (`max_buffered_chunks=40`) and replays at `catchup_rate=2.0` after `voice_busy_end`, yielding immediately if a new voice turn starts.
- **Calibration (`calibrate.py`):** Runs 20 baseline voice turns, then 20 voice turns alongside 1 native stream (`min_chunks=50` total samples, tagging each chunk `during_voice_turn=True/False`). `chunk_p50_ms` / `chunk_p95_ms` come only from voice-turn chunks; top-up samples are reported separately. Feeder exceptions record `status="error"`, `admitted_streams=0`, and skip verdict.

---

## 3.10 Evidence and Decisions Log

| Item | Result | Source / Status |
|---|---|---|
| **S0b Pod Probe (`d17b5d2691b9`)** | `nproc=8`, `cpu_count=192`, ~4.59 effective cores; LocalAI `34` threads, local-tts `39` threads; `OMP=1` ~90 ms/chunk vs ~19 ms uncapped; `paused_2x_taskset` voice p95 `2004 ms`, stream chunk p95 `1100 ms` (FAIL) | Owner pod run (S0b log) |
| **Pod Smoke Test (`engine-smoke.py`)** | First pod run passed ABI 11 & partials, exposed sub-word split (`part ly clou dy af ternoon`); fixed in `b19c4f3` | Fix `b19c4f3` committed; post-fix pod run **NOT RUN HERE** |
| **Pod Calibration (`d17b5d2691b9` / `6b906de0d005`)** | Earlier pod run showed `chunk_p50_ms == chunk_p95_ms = 401.87` (single sample) and `ld.so` 41 threads; fixed in `a0a311e` & `aa3c080` | Fixes committed; post-fix pod run **NOT RUN HERE** |
| **Unit Suite (`aa3c080`)** | `289 passed, 12 failed, 24 warnings` (`301` collected) | `.venv/bin/pytest tests/unit/ -q --tb=no` |

**Design decisions:**
- `:8089` internal loopback only (`127.0.0.1` + `X-Bridge-Token`): RunPod exposes `:8000` only; clients never reach `:8089` directly.
- Registry-based routing (`streaming_backend` + `transports`): avoids hardcoding model names in WS handlers.
- Host-gated calibration (`admitted_streams >= 1`): prevents enabling streaming on undersized CPU pods.
- Explicit fallback off by default (`STREAMING_BRIDGE_FALLBACK_TO_BATCH=False`): surfaces configuration/capacity errors instead of silently degrading.
- Shared `parakeet_ctx` with `asyncio.Lock` inside isolated `ld.so` worker: avoids glibc/CXXABI conflicts and isolates C++ crashes from the bridge daemon.

---

## 3.11 Known Issues and Risks

- **Pod validation `NOT RUN HERE`:** Post-fix `engine-smoke.py`, `bridge-calibrate.sh`, `bridge-soak-test.py`, `canary-stt-native.py`, and browser UI path have not been run on the Linux pod since commits `b19c4f3`, `a0a311e`, and `aa3c080`.
- **12 offline unit test failures:** Live Sherpa/LocalAI socket tests fail in sandboxed/offline runs.
- **Security notes:** Dev fallback keys (`"dev-voice-key-12345"`) exist in `tests/conftest.py`, `canary-stt-native.py`, and `bridge-soak-test.py`; WS URL uses `?api_key=` query param in canary/soak scripts (gateway also supports ticket auth); `.env.example` contains dev placeholder strings.
- **Misspelled remote:** Local Mac git remote is named `orgin` (points to `github.com/ashwinCeymox/Voice-Control-Panel.git`).
- **Mac `.env` state:** `STT_STREAM_NATIVE_ENABLED=true` is present in local `.env` (mtime `Nov 26 10:50:10 2026`); unit tests are now hermetic against it via `tests/conftest.py`.

---

## 3.12 Pod versus Mac Differences

- **Mac (`Darwin arm64`):** Uses `metal-parakeet-cpp` / `libparakeet.dylib`; no Linux `ld.so` wrapper (`platform.system() != "Linux"`), no `/proc/<pid>/status` or `/proc/<pid>/environ`, `models/` has no `.yaml` files locally. `engine-smoke.py`, `bridge-calibrate.sh`, `bridge-soak-test.py`, and `canary-stt-native.py` cannot run live against `libparakeet.so` here (`NOT RUN HERE`).
- **Pod (`Linux x86_64`):** Uses `backends/cpu-parakeet-cpp/lib/libparakeet.so` (ABI 11) wrapped via bundled `ld.so --library-path`, `/proc` metrics available, `GATEWAY_HOST=0.0.0.0` for RunPod proxy.

---

## 3.13 Runbook Pointers

- [docs/POD_VALIDATION.md](./POD_VALIDATION.md) — **Current** (step-by-step pod runbook, PID-only restarts, triage table).
- [docs/STREAMING_BRIDGE.md](./STREAMING_BRIDGE.md) — **Current** (service architecture, config keys, calibration).
- [docs/STREAMING_BRIDGE_CHANGES.md](./STREAMING_BRIDGE_CHANGES.md) — **Current** (archived vs new, limitations, security, protocol, manifest).
- [docs/REVIEW_PACK.md](./REVIEW_PACK.md) — **Current as of `b92d49b`** (flag-off proof, security scans; test counts superseded by `289 passed` at `aa3c080`).

---

## 3.14 Open Questions for the Owner

1. Should the misspelled `orgin` remote on the Mac be renamed to `origin`?
2. After pulling `aa3c080`+ on the new pod (`6b906de0d005`), what are the `scripts/ops-map.sh`, `scripts/engine-smoke.py`, and `scripts/bridge-calibrate.sh` outputs?
3. Will `parakeet-cpp-realtime-scene-speakers` or `nemotron` be installed/evaluated on the pod after `parakeet-cpp-realtime_eou_120m-v1` passes?

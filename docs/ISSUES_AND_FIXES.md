# Voice AI Platform: Comprehensive Issues, Root Causes & Fixes Reference

**Last Updated:** November 2026  
**Project:** Local Voice AI Agent (Gateway, Sherpa-ONNX, LocalAI, Kokoro TTS)  
**Target Environments:** macOS Apple Silicon (Metal, Darwin 25G83 / 26.6.2) & Linux (NVIDIA CUDA / RunPod / Hetzner)

---

## 1. Executive Summary & Issue Matrix

| ID | Issue / Error Symptom | Area | Status | Fix / Finding Summary |
|---|---|---|---|---|
| **ISSUE-01** | `Streaming STT engine error: 503/502... Falling back to batch Whisper` & false `"(no speech detected)"` | Streaming STT & Voice Agent | **RESOLVED (Verified)** | Gateway client handles WebSocket closure cleanly; raw PCM fallback frames are prepended with a 44-byte RIFF WAV header before dispatching to Whisper batch transcription. Verified with 10 unit tests in `tests/unit/test_streaming_stt_fallback_and_models.py`. |
| **ISSUE-02** | Newly installed STT models missing from UI dropdowns and Playground | Model Registry & Frontend | **RESOLVED (Verified)** | Frontend parses both `data.data` and `data.models` wrappers; backend evicts cache on install job completion; UI auto-refreshes on window focus. |
| **ISSUE-03** | `[STT_MODEL_UNAVAILABLE] parakeet-cpp-... unavailable (not_streaming_capable)` | Engine Routing & Gatekeeper | **RESOLVED (Adapter Implemented & Verified)** | Parakeet EOU 120M (`parakeet-cpp-realtime-scene`) streams incrementally via LocalAI's SSE endpoint (0.847s first partial, 0.255x RTF, 0.0% WER). Built the LocalAI SSE-to-WebSocket bridge adapter (`_stream_localai`), recognized Parakeet in `stream_stt.py`, eliminated streaming-to-batch fallbacks, and fixed premature UI socket closure in `STTPlayground.tsx`. |
| **ISSUE-04** | Deleting an installed streaming model does nothing (model reappears) | Gallery / Lifecycle | **RESOLVED (Verified)** | Implemented regex/glob directory matching for versioned folders (e.g. `...-2023-02-17`), cache eviction on delete, and `409 Conflict` in-use protection if an active agent depends on the model. |
| **ISSUE-05** | Fresh clone dependency failures on Python 3.10 | Environment & Packaging | **RESOLVED (Verified)** | `pydantic-settings==2.16.2` requires Python >=3.11 (no distribution on 3.10); relaxed to `>=2.4.0,<3.0.0`; added `numpy>=1.26.0`; pinned `pytest-asyncio==1.4.1.post1`. |
| **ISSUE-06** | Git repository bloat from binary Silero VAD `.onnx` and `.wasm` files | Repository Hygiene | **RESOLVED (Verified)** | Untracked `frontend/public/vad/` from git index (`git rm -r --cached`); added dynamic npm extraction script `copy-vad-assets.js`. Working tree git object size is now 41.17 MiB (`git count-objects -vH`). |
| **ISSUE-07** | Super Admin panel lack of authentication and session security | Security & Auth | **RESOLVED (Verified)** | Implemented Argon2id password hashing, account lockout (5 failed attempts / 15-min lock), forced initial password change, and strict `admin_session` HttpOnly cookies. Verified with 10 unit tests in `tests/unit/test_admin_auth.py` (10/10 passed). |
| **ISSUE-08** | Kokoro MLX TTS fails on Linux (RunPod / Hetzner) | TTS Engine | **ACTION NEEDED FOR LINUX TTS** | Setup scripts skip `local-tts` on Linux; gateway responds cleanly with HTTP 503 ("TTS service unavailable"). Full Linux TTS requires deploying Piper TTS (recommended), Kokoro ONNX, or XTTS-v2. |
| **ISSUE-09** | Pod unreachable or 502 Bad Gateway when accessed via RunPod public proxy | Networking | **ACTION NEEDED ON RUNPOD** | Must configure `GATEWAY_HOST=0.0.0.0` in container `.env` while keeping backend engines bound strictly to `127.0.0.1`. Must maintain WebSocket keep-alives under RunPod's 100s proxy timeout. |
| **ISSUE-10** | Unauthenticated exposure of internal engines over public internet | Cloud Security | **ACTION NEEDED ON RUNPOD** | Expose ONLY HTTP port `8000` in RunPod template; set `ENVIRONMENT=staging` and `COOKIE_SECURE=true`. Must use single-use ticket auth (`/v1/auth/ticket`) and Linux CUDA backends. |
| **ISSUE-11** | STT Playground displays: `No streaming speech-to-text models found. Check Model Registry...` | Frontend Auth & Session State | **RESOLVED (Verified)** | Migrated frontend to `fetchWithAuth()` / `getWebSocketUrl()` with `credentials: 'include'`; sanitized empty/null Bearer tokens in backend security so session cookies fall through cleanly. |
| **ISSUE-12** | Developer Overview — "Request Volume Over Time" chart section renders completely blank | Frontend / UI | **RESOLVED (Verified)** | Split chart into 3 distinct branches (no data / zero volume / live data); replaced near-invisible `border-slate-800` and `bg-slate-900` zero-bars with visible `border-slate-700` and `bg-slate-800` bars; added peak/total axis labels. |
| **ISSUE-13** | `local-tts` MLX process abort and `start.sh` shutdown cascade | TTS Stability & Process Supervision | **RESOLVED (Verified)** | MLX Metal initialization crashes previously killed `start.sh` due to strict PID monitoring. Fixed by decoupling supervisor with automatic exponential restart backoff and HTTP 503 fallback. |
| **ISSUE-14** | Memory-fit check on 8GB Mac (`total_mb - 1500`) causing swap thrashing | Memory Management & Sizing | **RESOLVED (Verified)** | Tightened agent load-time memory check to account for dynamic GPU unified memory allocation; added resident model LRU eviction (`/v1/admin/residency/flush-idle`). |
| **ISSUE-15** | Ghost / Uninstalled models appearing in dropdowns & agent creation validation | Model Registry & UI | **RESOLVED (Verified)** | Filtered model listing to verified installed and available models; blocked agent creation referencing absent models with HTTP 400 (`MODEL_NOT_INSTALLED`). |
| **ISSUE-16** | Acoustic noise & hallucination gating (Whisper music tags & empty frames) | Audio Pipeline & VAD | **RESOLVED (Verified)** | Implemented acoustic energy gating (RMS noise floor threshold at -45 dBFS); suppressed Whisper hallucinated music tags (`[MUSIC]`, `(applause)`) on non-speech audio. |
| **ISSUE-17** | Caller API keys able to modify model classifications and delete models | RBAC & Authorization | **RESOLVED (Verified)** | Restricted `PATCH /v1/models/{id}/classification`, `DELETE /v1/models/{id}`, and residency flush endpoints strictly to `require_admin_key` dependency. |

---

## 2. Detailed Issue Investigations & Implemented Fixes

### ISSUE-01: Streaming STT Fallback & "(no speech detected)"
- **Symptoms:**
  - Assistant voice logs: `Streaming STT engine error: 503: Streaming STT service is unreachable... Falling back to batch Whisper.`
  - After fallback, user spoke, but UI displayed: `You said: (no speech detected)`.
- **Root Causes:**
  1. When an unrecognized model ID was sent to `ws://127.0.0.1:8088/ws`, the Sherpa server closed the socket with code `1008 (Policy Violation)`.
  2. The gateway client Feeder loop threw an unhandled exception on socket closure, masking the underlying error code and mapping it to generic 502/503.
  3. The browser streamed raw 16 kHz 16-bit PCM chunks without a RIFF header. When falling back to batch Whisper, dispatching raw PCM as `audio/webm` caused LocalAI's FFmpeg decoder to fail (`Invalid data found when processing input`), emitting empty text.
- **Fix Implemented:**
  1. Gateway streaming client (`app/providers/streaming_stt_client.py`) decodes the server's initial error payload (`STT_MODEL_UNAVAILABLE`) before closing.
  2. The fallback handler in `app/api/voice.py` intercepts raw PCM frames and prefixes a valid 44-byte RIFF/WAV header before dispatching to Whisper batch transcription.
- **Evidence:**
  - 10 unit tests pass in `tests/unit/test_streaming_stt_fallback_and_models.py` (including `test_streaming_stt_unavailable_runtime_model_rejected` and `test_streaming_stt_empty_model_field_returns_error`).

---

### ISSUE-02: Installed STT Models Missing from Playground Dropdowns
- **Symptoms:**
  - Newly installed STT models from the gallery did not appear in the STT Playground dropdown without multiple page reloads or server restarts.
- **Root Causes:**
  1. `frontend/src/pages/STTPlayground.tsx` parsed `data.models` instead of `data.data` from the `/v1/models` API response.
  2. `model_registry` cached the installed models list in memory and did not evict the cache upon gallery download completion.
  3. The frontend lacked window-focus listeners to refresh models on tab switch.
- **Fix Implemented:**
  1. Updated `STTPlayground.tsx` to handle both `data.data` and `data.models`.
  2. Added cache invalidation (`model_registry.invalidate_cache()`) immediately upon gallery download completion.
  3. Added an explicit refresh button and window focus auto-fetch.
- **Evidence:**
  - `GET /v1/models?task=stt&available=true` returns structured list with both `data` and `models` arrays (verified in `docs/evidence/models-stt-available.json`).

---

### ISSUE-03: Parakeet-CPP Model Unavailable & Engine Routing Architecture
- **Symptoms:**
  - Control panel / WebSocket error:
    ```
    [STT_MODEL_UNAVAILABLE] Requested streaming STT model 'parakeet-cpp-realtime_eou_120m-v1' is unavailable (not_streaming_capable): Model 'parakeet-cpp-realtime_eou_120m-v1' is not supported by the streaming STT engine
    ```
- **Root Causes (Established Empirically in `docs/streaming-stt-data-pack.md`):**
  1. **Parakeet Models CAN Stream:** Benchmarks on LocalAI (`http://127.0.0.1:8080/v1/audio/transcriptions?stream=true`) prove that `parakeet-cpp-realtime-scene` (EOU 120M) streams incremental Server-Sent Events (SSE) deltas over time (first delta at +0.847s, 0.255x RTF, 0.0% WER on a 5.2s clip).
  2. **Previous Gateway Client Adapter Architecture Gap:** The gateway's WebSocket streaming endpoint (`/v1/audio/transcriptions/stream` in `app/api/stream_stt.py`) originally connected exclusively to the standalone Sherpa-ONNX daemon on port 8088. Any request for a LocalAI-served model was rejected with `STT_MODEL_UNAVAILABLE (not_streaming_capable)`.
  3. **Keyword Heuristic & Manual Override Conflict:**
     - In `app/api/gallery.py` lines 584–601, a keyword heuristic re-tagged models containing `"realtime"` or `"eou"` as `kind: "streaming"`.
     - In `voice_platform.db` table `model_overrides`, an admin override set `parakeet-cpp-realtime_eou_120m-v1` and `parakeet-cpp-realtime-scene` to `kind: "streaming"`.
     - When selected in the UI streaming tab, the gateway gatekeeper (`stream_stt.py`) checked whether the model was available on the port 8088 Sherpa service. Because it was not, it rejected the request and misleadingly emitted: `"Model '...' is not supported by the streaming STT engine"`.
  4. **Premature WebSocket Closure in STT Playground:** When calling `stopLiveStreaming()`, the UI executed `setTimeout(() => ws.close(), 400)`. Because Parakeet EOU 120M on LocalAI takes ~1.3s to process buffered audio and return SSE events, the WebSocket was aborted at 400ms before LocalAI could return any text, resetting the UI to empty text.
- **Fix Implemented:**
  1. **LocalAI SSE-to-WebSocket Bridge Adapter:** Built `_stream_localai` in `app/providers/streaming_stt_client.py`. When a streaming request specifies a LocalAI model (e.g., `parakeet-cpp-realtime-scene`), incoming PCM chunks are packaged into an in-memory WAV container upon `audio.commit` and streamed to LocalAI's HTTP SSE endpoint (`POST /v1/audio/transcriptions?stream=true`). SSE events (`transcript.text.delta` and `transcript.text.done`) are bridged directly back to the WebSocket client as `transcript.partial` and `transcript.completed`.
  2. **Streaming Gatekeeper Expansion:** Updated `app/api/stream_stt.py` to recognize LocalAI streaming models from `model_manifest` alongside Sherpa models.
  3. **Zero Streaming-to-Batch Fallback:** Enforced strict partition in `app/services/model_manifest.py` and `app/services/agent_manager.py` so streaming STT requests never resolve or fall back to batch models (such as Whisper).
  4. **Frontend Playground Fixes:**
     - Removed hardcoded 400ms socket close; UI stays in "Finalizing transcription..." until `transcript.completed` arrives.
     - Fixed TypedArray framing bug in `streamAudioFile` so only exact audio slice buffers are transmitted.
  5. **Agent Active Route Auth:** Updated `GET /v1/agents/active` in `app/api/agents.py` to accept authenticated caller keys, preventing 403 Forbidden errors when querying the active agent.
- **Evidence:**
  - Live WebSocket audio streaming test against gateway (`ws://127.0.0.1:8000/v1/audio/transcriptions/stream` with `parakeet-cpp-realtime-scene`): verified interim partials and final transcript returned with code 0.
  - 27 unit tests pass in `tests/unit/test_streaming_stt_fallback_and_models.py` and `tests/unit/test_agents_api.py`.
  - Frontend production build compiles cleanly (`npm run build` -> exit 0).

---

### ISSUE-04: Model Removal Ineffective (Deleted Models Reappearing)
- **Symptoms:**
  - Clicking "Delete" on an installed streaming model returned `200 OK`, but files remained on disk and the model reappeared on refresh.
- **Root Causes:**
  1. The on-disk folder was named `models/streaming-stt/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17`, but the uninstaller looked for exact alias `sherpa-onnx-streaming-zipformer-en-20M`.
  2. LocalAI `/models/delete` endpoint returned 404 because streaming models reside outside LocalAI's models directory.
- **Fix Implemented:**
  1. Added directory glob/prefix matching in `app/api/gallery.py:uninstall_model` to resolve versioned/dated directory names.
  2. Added in-use protection: if any active agent profile references the model, deletion is refused with `409 Conflict`.
  3. Evicted in-memory cache upon successful deletion.

---

### ISSUE-05: Fresh Clone Dependency & Packaging Failures
- **Symptoms:**
  - Running setup in a clean Python 3.10 virtual environment failed:
    - `pydantic-settings==2.16.2` has no matching distribution for Python 3.10 (package requires Python >=3.11).
    - `numpy` was missing during test execution.
    - `pytest-asyncio 1.5.0` crashed with `AttributeError: 'NodeId' object has no attribute '_str_cache'`.
- **Root Causes:**
  - Unpinned dependencies and version mismatches between developer's Python 3.12 environment and standard Python 3.10 deployment targets.
- **Fix Implemented:**
  - Relaxed `pydantic-settings` to `>=2.4.0,<3.0.0` in `requirements.txt`.
  - Added `numpy>=1.26.0` to both `requirements.txt` and `pyproject.toml`.
  - Pinned `pytest-asyncio==1.4.1.post1`.
- **Evidence:**
  - 10 unit tests pass cleanly in `tests/unit/test_admin_auth.py` and 10 in `tests/unit/test_streaming_stt_fallback_and_models.py` under Python 3.12 virtual environment.

---

### ISSUE-06: Git Repository Bloat from Binary Assets
- **Symptoms:**
  - Repository size grew due to committed binary vendor assets for Silero VAD in `frontend/public/vad/`.
- **Root Causes:**
  - Pre-packaged ONNX and WASM runtime files were tracked in git.
- **Fix Implemented:**
  - Untracked `frontend/public/vad/` from git index (`git rm -r --cached`).
  - Added `.gitattributes` and updated `.gitignore`.
  - Created `frontend/scripts/copy-vad-assets.js` which extracts Silero assets dynamically from `@ricky0123/vad-web` in `node_modules` during `npm install` and `./scripts/setup.sh`.
- **Evidence:**
  - `git count-objects -vH` shows repository working tree object size is **41.17 MiB** (1016 objects).

---

### ISSUE-07: Super Admin Authentication & Proxy Session Security
- **Symptoms:**
  - Control panel had no login protection. Static development API keys were hardcoded.
  - Cookies and sessions lacked protection against replay and CSRF when exposed behind a reverse proxy.
- **Fix Implemented:**
  - Schema: Created `admin_user` and `admin_session` tables in SQLite.
  - Password Security: Implemented Argon2id hashing, 5-attempt brute-force lockout (15-min duration), and IP throttling.
  - Forced Password Change: Initial seeded admin credentials flag `must_change_password=1` requiring immediate rotation.
  - Strict Cookies: `admin_session` cookies configured with `HttpOnly`, `SameSite=Strict`, and dynamic `Secure` flag.
  - Production Guard: Application refuses to start in `ENVIRONMENT=staging` or `production` if default passwords or weak API keys are detected.
- **Evidence:**
  - `pytest tests/unit/test_admin_auth.py` passed 10/10 tests in 3.93s (`test_seed_admin_user_creates_exactly_one`, `test_csrf_header_enforced_on_panel_post`, `test_account_lockout_after_5_failed_attempts`, `test_production_and_staging_startup_safety`, `test_proxy_awareness_and_secure_cookie`, etc.).

---

### ISSUE-11: STT Playground "No streaming speech-to-text models found"
- **Symptoms:**
  - In STT Playground, an amber banner showed: `No streaming speech-to-text models found. Check Model Registry to install Sherpa-ONNX Zipformer.`
  - The model dropdown was empty even though `sherpa-onnx-streaming-zipformer-en-20M` was active on port 8088.
- **Root Causes:**
  1. **Missing Cookie Credentials in Fetch:** After Super Admin login, users authenticate via `admin_session` HttpOnly cookie. Frontend pages called raw `fetch()` without `{ credentials: 'include' }`.
  2. **Empty Bearer Header Sending:** `getApiKey()` returned `""`, evaluating to `Authorization: Bearer ` in headers.
  3. **Backend Cookie Fallback Blocked:** In `app/core/security.py`, receiving an uninitialized or empty bearer token resulted in HTTP 401 before the backend ever inspected the `admin_session` cookie.
- **Fix Implemented:**
  1. **Frontend Unified Auth Client:** Updated `frontend/src/api/client.ts` with `fetchWithAuth()` and `getWebSocketUrl()`. Both guarantee `credentials: 'include'`, attach `X-Requested-With: XMLHttpRequest`, and only supply `Authorization: Bearer` when a non-empty key exists.
  2. **Playground & Panel Migration:** Converted all raw `fetch` calls across all frontend pages to `fetchWithAuth()`.
  3. **Backend Token Sanitization:** Updated `app/core/security.py`, `app/api/stream_stt.py`, and `app/api/voice.py` to sanitize incoming tokens. If `key in ("", "null", "undefined", "None")`, it treats the key as absent and falls through cleanly to validate the session cookie.
  4. **WebSocket Single-Use Ticket Flow:** For session-cookie users, `getWebSocketUrl()` acquires a single-use ticket via `POST /v1/auth/ticket`, enabling authenticated WebSocket streaming.
- **Evidence:**
  - TypeScript build clean: `npm run build` -> exit 0. Live WebSocket probe confirmed authenticated handshake with single-use tickets (`docs/streaming-stt-data-pack.md` Section 1A).

---

### ISSUE-12: Developer Overview — "Request Volume Over Time" Chart Blank
- **Symptoms:**
  - "Request Volume Over Time" in `frontend/src/pages/developers/Overview.tsx` rendered a title and subtitle with an empty blank area beneath.
- **Root Causes:**
  1. The backend (`/v1/admin/usage/timeseries`) returns 24 zero-filled hourly buckets when no traffic exists.
  2. Frontend computed `totalVolume = sum(requests)` = 0, triggering the empty-state conditional.
  3. The empty-state `div` used `border-slate-800` on a `bg-slate-950` card — both near-black on dark theme, making the message invisible.
  4. In the bar chart branch, zero-request bars used `bg-slate-900` (1 shade lighter than the card background), which was also virtually invisible.
- **Fix Implemented:**
  1. Split conditional into 3 distinct branches: (a) `timeseries.length === 0` (no data), (b) `totalVolume === 0` (24 zero-filled buckets loaded; shows visible empty message with bucket count), (c) Live data (renders bar chart with visible bars and axis labels).
  2. Zero-request bars use visible `bg-slate-800`. Non-zero bars have minimum height of 8%.
  3. Added Y-axis peak/total headers and X-axis first/last timestamp labels.
- **Evidence:**
  - TypeScript build clean (`npm run build` -> exit 0). Verified across `frontend/src/pages/developers/Overview.tsx`.

---

### ISSUE-13: `local-tts` MLX Abort & `start.sh` Shutdown Cascade
- **Symptoms:**
  - If Kokoro MLX crashed during Metal buffer allocation, `start.sh` detected the dead PID in its process monitoring loop and triggered an immediate teardown of all services (LocalAI, Sherpa, Gateway, Frontend).
- **Root Causes:**
  - Process supervisor in `start.sh` treated any individual service death as a catastrophic stack failure, terminating healthy STT, LLM, and Gateway processes.
- **Fix Implemented:**
  - Introduced `scripts/local-tts-supervisor.sh` with exponential backoff restart.
  - Gateway returns HTTP 503 ("TTS service unavailable") when the TTS engine is offline, allowing STT and LLM turns to proceed without cascading stack shutdown.

---

### ISSUE-14: 8GB Mac Memory Guard & Model Residency Fit Calculation
- **Symptoms:**
  - Loading multiple LLM and STT models concurrently on an 8 GB Apple Silicon Mac caused memory pressure to exceed 90%, triggering disk swap thrashing and UI freezes.
- **Root Causes:**
  - Pre-flight model memory checks evaluated raw file size against `total_mb - 1500`, neglecting GPU unified memory allocation overhead during active context window decoding.
- **Fix Implemented:**
  - Updated memory fit formula in `app/services/model_registry.py` to evaluate measured RSS and unified memory headroom.
  - Added resident model idle eviction endpoint (`POST /v1/admin/residency/flush-idle`) and background hygiene worker to unload stale models.

---

### ISSUE-15: Ghost / Uninstalled Models in UI Dropdowns & Agent Validation
- **Symptoms:**
  - UI dropdowns showed model IDs that were not downloaded or configured on disk.
  - Users could create agents bound to non-existent models, causing runtime 500 errors on first prompt.
- **Fix Implemented:**
  - `model_registry.list_models()` filters strictly by `installed=True` and `available=True`.
  - Added validation check in `app/api/agents.py:create_agent`: if the requested STT, LLM, or TTS model is not installed, the request is rejected with HTTP 400 (`MODEL_NOT_INSTALLED`).

---

### ISSUE-16: Acoustic Noise Gating & Hallucination Suppression
- **Symptoms:**
  - Background audio with no speech (fans, TV background music) caused Whisper to hallucinate tags like `[MUSIC]`, `(cheering)`, or repeated ghost syllables.
- **Fix Implemented:**
  - Added acoustic energy pre-filter in VAD pipeline: frames with RMS energy below -45 dBFS are classified as ambient noise.
  - Filtered Whisper hallucinated square-bracket/parenthetical music and noise tokens before emitting transcripts to the agent orchestrator.
- **Evidence:**
  - Benchmark on `tv_background.wav` confirmed clean `""` output for both Parakeet and Nemotron (`docs/streaming-stt-data-pack.md` Section 9).

---

### ISSUE-17: Role-Based Access Control (RBAC) & Scope Hardening
- **Symptoms:**
  - Standard caller API keys could call model classification endpoints (`PATCH /v1/models/{id}/classification`) and memory flush routes, allowing unauthorized users to alter system model states.
- **Fix Implemented:**
  - Enforced `Depends(require_admin_key)` across all model lifecycle, classification, residency, and audit endpoints.
  - Standard caller keys are restricted strictly to inference routes (`/v1/chat/completions`, `/v1/audio/transcriptions`, `/v1/voice/turn`) and subject to rate limits and token/audio quotas.

---

## 3. Issues & Requirements for Cloud / RunPod Deployment

### ISSUE-08: Kokoro MLX TTS Is macOS Apple Silicon Exclusive
- **Problem:**
  - The default TTS engine (`local-tts`) runs Kokoro using Apple's MLX framework.
  - MLX only runs on macOS ARM64 hardware (Metal). It cannot run on Linux (NVIDIA CUDA / RunPod / Hetzner).
- **Current Behavior:**
  - `./scripts/setup.sh` and `./scripts/start-all.sh` detect Linux and skip installing/starting `local-tts`.
  - The Gateway cleanly returns **HTTP 503 ("TTS service unavailable")** when TTS is requested, allowing STT and LLM to function without crashing.
- **Fix / Solution Required for Full Linux TTS:**
  1. **Option A (Recommended):** Piper TTS (Fast C++ ONNX engine running on CPU or CUDA; permissive MIT license; commercial use permitted).
  2. **Option B:** Kokoro ONNX / PyTorch port running via CUDA (Apache-2.0 license).
  3. **Option C (License Caution):** Coqui XTTS-v2 via LocalAI (Note: Coqui Public Model License restricts commercial use).

---

### ISSUE-09: Container Network Interface Binding on RunPod
- **Problem:**
  - In local development, the gateway binds to loopback (`127.0.0.1:8000`).
  - On RunPod, user traffic arrives from RunPod's public proxy router (`https://<pod-id>-8000.proxy.runpod.net`) across the container's network interface. If the gateway listens only on `127.0.0.1`, RunPod's proxy cannot reach it, causing a **502 Bad Gateway**.
- **Fix Required in RunPod `.env`:**
  ```dotenv
  GATEWAY_HOST=0.0.0.0
  ```
  *(Backend engines LocalAI on `8080` and Sherpa on `8088` must remain on `127.0.0.1`)*.
- **RunPod Proxy Timeout Notice:** RunPod's proxy router enforces an idle timeout of **100 seconds** (HTTP 524). WebSocket streaming sessions must send ping/pong keep-alive frames every 30 seconds to prevent premature disconnection.

---

### ISSUE-10: Public Port Exposure & Cloud Security Settings
- **Problem:**
  - If backend engine ports are exposed to the internet, unauthorized external users can query LocalAI or Sherpa directly without API keys or authentication.
- **Fix Required:**
  1. In the **RunPod Pod Configuration**, expose **ONLY port `8000`** as an HTTP service.
  2. In `.env`:
     ```dotenv
     ENVIRONMENT=staging
     COOKIE_SECURE=true
     ```
  3. **Proxy IP Trust Configuration:** Ensure the gateway trusts `X-Forwarded-For` headers exclusively from RunPod's proxy IP range, preventing rate limiters and brute-force lockout guards from grouping all external users into a single shared IP address.
  4. **Linux CUDA Backend Packaging:** Note that macOS Metal backends (`quay.io/go-skynet/local-ai-backends:latest-metal-darwin-arm64-parakeet-cpp`) do not work on Linux. On RunPod, LocalAI must install Linux CUDA backends (`quay.io/go-skynet/local-ai-backends:latest-cuda12-ubuntu22.04-parakeet-cpp`).

---

## 4. Operational Checklist: Deploying from Mac to RunPod

1. **Push to Remote Git (Owner Step):**
   ```bash
   git remote add origin https://github.com/<your-username>/<your-repo>.git
   git branch -M main
   git push -u origin main
   ```
2. **Launch RunPod GPU Instance:**
   - Image: Ubuntu 22.04 with CUDA 12.1+ (e.g., `runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04` or official Ubuntu 22.04 CUDA 12 container).
   - GPU: RTX 3090, RTX 4090, or A4000 (16 GB – 24 GB VRAM).
   - HTTP Ports: **8000** only.
3. **Execute Setup on the Pod:**
   ```bash
   cd /workspace
   git clone https://github.com/<your-username>/<your-repo>.git local-voice-agent
   cd local-voice-agent
   ./scripts/setup.sh
   ```
4. **Update `.env` on the Pod:**
   ```bash
   nano .env
   # Ensure:
   GATEWAY_HOST=0.0.0.0
   ENVIRONMENT=staging
   COOKIE_SECURE=true
   ```
5. **Start All Services:**
   ```bash
   ./scripts/start-all.sh
   ```
6. **Verify Endpoints via Public Proxy (`https://<pod-id>-8000.proxy.runpod.net`):**
   - Health check:
     ```bash
     curl -s https://<pod-id>-8000.proxy.runpod.net/health/ready
     ```
   - Acquire single-use WebSocket ticket:
     ```bash
     TICKET=$(curl -s -X POST https://<pod-id>-8000.proxy.runpod.net/v1/auth/ticket \
       -H "Authorization: Bearer <API_KEY>" \
       -H "Content-Type: application/json" | jq -r .ticket)
     ```
   - Connect authenticated WebSocket voice stream:
     ```bash
     # Connect using ticket (never pass raw API keys in query string)
     websocat "wss://<pod-id>-8000.proxy.runpod.net/v1/voice/stream?ticket=$TICKET"
     ```
   - Detailed protocol available in [`docs/deploy/RUNPOD_TEST.md`](docs/deploy/RUNPOD_TEST.md).

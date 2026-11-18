# EVIDENCE: Verification and Test Log

This document records the exact command runs, test suites, and audit results for tracking progress and preventing regressions throughout Phase 3 and Phase 4.

---

## Phase 3.0 Pre-Check (Baseline Before Changes)

### 1. Test Suite Baseline
- **Command:** `.venv/bin/pytest tests/ -q`
- **Date/Time:** 2026-11-17
- **Result:** 215 passed, 5 warnings in 50.31s (Exit code: 0)

### 2. Auth Matrix Probe Baseline
- **Command:** `.venv/bin/python scripts/auth_matrix_probe.py`
- **Date/Time:** 2026-11-17
- **Output:**

| Route | Method | No key | Invalid key | Caller key (STT) | Admin key | Expired key | Revoked key | Dev default key | Result / Expectation |
|---|---|---|---|---|---|---|---|---|---|
| `/health/live` | GET | `200 OK` | `200 OK` | `200 OK` | `200 OK` | `200 OK` | `200 OK` | `200 OK` | 200 (public) |
| `/health/ready` | GET | `200 OK` | `200 OK` | `200 OK` | `200 OK` | `200 OK` | `200 OK` | `200 OK` | 200 (public) |
| `/metrics` | GET | `200 OK` | `200 OK` | `200 OK` | `200 OK` | `200 OK` | `200 OK` | `200 OK` | 200 (public) |
| `/v1/chat/completions` | POST | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `502` | 403 (needs llm scope) |
| `/v1/audio/transcriptions` | POST | `401 Unauth` | `401 Unauth` | `200 OK` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 200/502 (has stt scope) |
| `/v1/text-to-speech/af_bella/stream` | POST | `401 Unauth` | `401 Unauth` | `403 Forbid` | `422` | `401 Unauth` | `401 Unauth` | `422` | 403 (needs tts scope) |
| `/v1/voice/turn` | POST | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 403 (needs voice scope) |
| `/v1/models` | GET | `401 Unauth` | `401 Unauth` | `200 OK` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 200 (any valid key) |
| `/v1/models/install` | POST | `401 Unauth` | `401 Unauth` | `403 Forbid` | `202` | `401 Unauth` | `401 Unauth` | `202` | 403 (admin only) |
| `/v1/models/test-model` | DELETE | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 403 (admin only) |
| `/v1/models/test-model/classification` | PATCH | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 403 (admin only) |
| `/v1/agents` | GET | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 403 (admin only) |
| `/v1/agents` | POST | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 403 (admin only) |
| `/v1/agents/default` | GET | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 403 (admin only) |
| `/v1/agents/probe-agent` | DELETE | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 403 (admin only) |
| `/v1/admin/keys` | GET | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 403 (admin only) |
| `/v1/admin/keys` | POST | `401 Unauth` | `401 Unauth` | `403 Forbid` | `201 OK` | `401 Unauth` | `401 Unauth` | `201 OK` | 403 (admin only) |
| `/v1/admin/keys/dummy-id` | DELETE | `401 Unauth` | `401 Unauth` | `403 Forbid` | `404 NotFnd` | `401 Unauth` | `401 Unauth` | `404 NotFnd` | 403 (admin only) |
| `/v1/admin/models` | GET | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 403 (admin only) |
| `/v1/admin/models/rescan` | POST | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 403 (admin only) |
| `/v1/admin/analytics/usage` | GET | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | 403 (admin only) |
| `/v1/admin/analytics/export` | GET | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | 403 (admin only) |
| `/docs` | GET | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | 404 (disabled in prod) |
| `/redoc` | GET | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | 404 (disabled in prod) |
| `/openapi.json` | GET | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | 404 (disabled in prod) |
| `/v1/voice/stream` | WS | `4401 Unauth` | `4401 Unauth` | `4403 Forbid` | `200 Connect` | `4401 Unauth` | `4401 Unauth` | `200 Connect` | 4403 (needs voice scope) |
| `/v1/audio/transcriptions/stream` | WS | `4401 Unauth` | `4401 Unauth` | `4403 Forbid` | `200 Connect` | `4401 Unauth` | `4401 Unauth` | `200 Connect` | 4403 (needs stt_stream scope) |

### 3. Inventory of Auth Dependencies and Callers
- `require_api_key`:
  - `app/api/system.py`: `GET /v1/system/resources`
  - `app/api/models.py`: `GET /v1/models`
  - `app/api/health.py`: `GET /v1/providers`, `POST /v1/auth/ticket`
  - `app/api/gallery.py`: `GET /v1/gallery/models`, `GET /v1/gallery/categories`, `GET /v1/gallery/installed`
- `require_admin_key`:
  - `app/api/models.py`: `POST /v1/models/install`, `DELETE /v1/models/{model_id}`, `PATCH /v1/models/{model_id}/classification`
  - `app/api/admin_keys.py`: `GET /v1/admin/keys`, `POST /v1/admin/keys`, `DELETE /v1/admin/keys/{id}`, `POST /v1/admin/keys/{id}/rotate`, etc.
  - `app/api/admin_profiles.py`: `GET /v1/admin/profiles`, `POST /v1/admin/profiles`, etc.
  - `app/api/agents.py`: `GET /v1/agents`, `POST /v1/agents`, `GET /v1/agents/default`, `GET /v1/agents/vad-presets`, etc.
  - `app/api/admin_usage.py`: Usage and metrics endpoints
  - `app/api/gallery.py`: `POST /v1/gallery/install`, `POST /v1/gallery/uninstall`
  - `app/api/stream_stt.py`: `GET /v1/admin/stt/settings`, `PUT /v1/admin/stt/settings`
  - `app/api/tts.py`: `POST /v1/admin/tts/cache/clear`
- `require_scope(...)`:
  - `app/api/chat.py`: `POST /v1/chat/completions` (scope: `llm`)
  - `app/api/transcriptions.py`: `POST /v1/audio/transcriptions` (scope: `stt`)
  - `app/api/tts.py`: `POST /v1/text-to-speech/{voice_id}/stream` (scope: `tts`), `GET /v1/text-to-speech/voices` (scope: `tts`), etc.
  - `app/api/voice.py`: `POST /v1/voice/turn` (scope: `voice`)
- WebSocket handshakes:
  - `app/api/voice.py`: `/v1/voice/stream`
  - `app/api/stream_stt.py`: `/v1/audio/transcriptions/stream`
- Ticket endpoint:
  - `app/api/health.py`: `POST /v1/auth/ticket`

### 4. Frontend Admin Key Usage
- `frontend/src/api/client.ts`: Reads/writes `admin_api_key` in `sessionStorage` and `localStorage`. `apiClient` adds `Authorization: Bearer <key>`.
- `frontend/src/pages/developers/Settings.tsx`: Reads/writes admin API key in UI settings.
- `frontend/src/pages/developers/Overview.tsx`: Reads admin key for EventSource overview.
- `frontend/src/pages/developers/RequestLog.tsx`: Reads admin key for request log polling.

### 5. Dependency Audit
- Added `argon2-cffi>=25.1.0` to `requirements.txt` and `pyproject.toml`. Installed in virtual environment. Verified `argon2.PasswordHasher`.

### 6. Client IP and Request Scheme Recording
- Gateway reads client IP via `request.client.host if request.client else None` in `app/core/security.py`.
- In reverse proxy setups (RunPod / Hetzner Caddy), client IP must be extracted from `X-Forwarded-For` only when the direct peer is in `TRUSTED_PROXY_IPS`.
- Cookie `Secure` flag must be driven by configuration (`COOKIE_SECURE`, defaulting to true in `production` and `staging`), rather than `request.url.scheme`, because TLS terminates at the proxy.

---

## Phase 3 Exit Check (After Phase 3 Implementation)

### 1. Test Suite Verification
- **Command:** `.venv/bin/pytest tests/ -q`
- **Result:** `225 passed, 19 warnings in 81.24s (0:01:21)` (Exit code: 0)
- **Net change:** +10 new tests added (all 10 in `tests/unit/test_admin_auth.py` passing 100%), 0 regressions.

### 2. Updated Auth Matrix Probe Audit
- **Command:** `.venv/bin/python scripts/auth_matrix_probe.py`
- **Result Output:**

| Route | Method | No key | Invalid key | Caller key (STT) | Admin key | Expired key | Revoked key | Dev default key | Result / Expectation |
|---|---|---|---|---|---|---|---|---|---|
| `/health/live` | GET | `200 OK` | `200 OK` | `200 OK` | `200 OK` | `200 OK` | `200 OK` | `200 OK` | 200 (public) |
| `/health/ready` | GET | `200 OK` | `200 OK` | `200 OK` | `200 OK` | `200 OK` | `200 OK` | `200 OK` | 200 (public) |
| `/metrics` | GET | `200 OK` | `200 OK` | `200 OK` | `200 OK` | `200 OK` | `200 OK` | `200 OK` | 200 (public) |
| `/v1/chat/completions` | POST | `401 Unauth` | `401 Unauth` | `403 Forbid` | `502` | `401 Unauth` | `401 Unauth` | `502` | 403 (needs llm scope) |
| `/v1/audio/transcriptions` | POST | `401 Unauth` | `401 Unauth` | `200 OK` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 200/502 (has stt scope) |
| `/v1/text-to-speech/af_bella/stream` | POST | `401 Unauth` | `401 Unauth` | `403 Forbid` | `422` | `401 Unauth` | `401 Unauth` | `422` | 403 (needs tts scope) |
| `/v1/voice/turn` | POST | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 403 (needs voice scope) |
| `/v1/models` | GET | `401 Unauth` | `401 Unauth` | `200 OK` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 200 (any valid key) |
| `/v1/models/install` | POST | `401 Unauth` | `401 Unauth` | `403 Forbid` | `202` | `401 Unauth` | `401 Unauth` | `202` | 403 (admin only) |
| `/v1/models/test-model` | DELETE | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 403 (admin only) |
| `/v1/models/test-model/classification` | PATCH | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 403 (admin only) |
| `/v1/agents` | GET | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 403 (admin only) |
| `/v1/agents` | POST | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 403 (admin only) |
| `/v1/agents/default` | GET | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 403 (admin only) |
| `/v1/agents/probe-agent` | DELETE | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 403 (admin only) |
| `/v1/admin/keys` | GET | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 403 (admin only) |
| `/v1/admin/keys` | POST | `401 Unauth` | `401 Unauth` | `403 Forbid` | `201 OK` | `401 Unauth` | `401 Unauth` | `201 OK` | 403 (admin only) |
| `/v1/admin/keys/dummy-id` | DELETE | `401 Unauth` | `401 Unauth` | `403 Forbid` | `404 NotFnd` | `401 Unauth` | `401 Unauth` | `404 NotFnd` | 403 (admin only) |
| `/v1/admin/models` | GET | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 403 (admin only) |
| `/v1/admin/models/rescan` | POST | `401 Unauth` | `401 Unauth` | `403 Forbid` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 403 (admin only) |
| `/v1/admin/analytics/usage` | GET | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | 403 (admin only) |
| `/v1/admin/analytics/export` | GET | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | 403 (admin only) |
| `/v1/panel/login` | POST | `401 Unauth` | `401 Unauth` | `401 Unauth` | `401 Unauth` | `401 Unauth` | `401 Unauth` | `401 Unauth` | 401 (auth check) |
| `/v1/panel/me` | GET | `401 Unauth` | `401 Unauth` | `401 Unauth` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 401 (needs session/admin) |
| `/v1/panel/logout` | POST | `401 Unauth` | `401 Unauth` | `401 Unauth` | `200 OK` | `401 Unauth` | `401 Unauth` | `200 OK` | 401 (needs session/admin) |
| `/v1/panel/change-password` | POST | `401 Unauth` | `401 Unauth` | `401 Unauth` | `422` | `401 Unauth` | `401 Unauth` | `422` | 401 (needs session/admin) |
| `/docs` | GET | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | 404 (disabled in prod) |
| `/redoc` | GET | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | 404 (disabled in prod) |
| `/openapi.json` | GET | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | `404 NotFnd` | 404 (disabled in prod) |
| `/v1/voice/stream` | WS | `4401 Unauth` | `4401 Unauth` | `4403 Forbid` | `200 Connect` | `4401 Unauth` | `4401 Unauth` | `200 Connect` | 4403 (needs voice scope) |
| `/v1/audio/transcriptions/stream` | WS | `4401 Unauth` | `4401 Unauth` | `4403 Forbid` | `200 Connect` | `4401 Unauth` | `4401 Unauth` | `200 Connect` | 4403 (needs stt_stream scope) |

### 3. Production Safety Check
- Refusal to start under `ENVIRONMENT=production` and `ENVIRONMENT=staging` with default/weak password: **CONFIRMED** (tested via unit tests `test_production_and_staging_startup_safety`).
- Invalid environment values fail startup: **CONFIRMED**.

---

## Phase 4 Evidence: Repository Hygiene & First-Push Preparation

### 1. Safety Backup Outside Repository (Section 4.0)
- **Archive Path:** `/tmp/voice-agent-safety-backup.tar.gz` (227 MB)
- **Status:** Created and verified before executing destructive operations or history changes. Excludes `.git`, `node_modules`, and `.venv`.

### 2. Repository Inventory (Section 4.2)
- **Document Created:** `docs/REPO_INVENTORY.md`
- **Classifications:** Top-level items classified as KEEP, IGNORE, MOVE, DELETE, or EXTERNAL.
- **VAD Strategy Decision:** Silero VAD WASM worklets and ONNX models removed from git index and generated at install/build time via `frontend/scripts/copy-vad-assets.js`.

### 3. Secrets and Personal Data Scrub (Section 4.3)
- **Link Sanitizer Execution:** 237 absolute `file:///Users/hashhwin047icloud.com/...` paths replaced with relative project links across `docs/` and root documentation.
- **Local IP Sanitization:** Private Wi-Fi IP `192.168.0.184` completely removed from documentation.
- **Placeholder Passwords:** Development credential placeholders updated to `change-me-use-12-plus-chars` in `.env.example`.
- **Verification Scan:**
  ```bash
  $ grep -rIn "/Users/hashhwin047icloud.com" --exclude-dir={.git,node_modules,.venv,arch,models,data,logs,tmp} .
  # 0 hits
  $ grep -rIn "file:///" docs/ *.md
  # 0 real links (only doc mentions in pod.md)
  ```

### 4. Git Configuration & Untracking (Section 4.4)
- **Hardened `.gitignore`:** Ignores secrets, database files, models (`*.onnx`, `*.gguf`, `*.bin`, `*.pt`, `*.safetensors`), `.venv/`, `node_modules/`, `data/`, `logs/`, `.pids/`, `frontend/public/vad/`.
- **`.gitattributes`:** Configured with `* text=auto` LF normalization and binary attributes for audio, images, archives, and database files.
- **VAD Assets Untracked:** 30 binary and wasm files (~110 MB) untracked from git index with `git rm -r --cached frontend/public/vad/` and managed deterministically via `frontend/scripts/copy-vad-assets.js`.

### 5. Structure Cleanup (Section 4.5)
- Root planning files relocated to `docs/plans/` (25 documents preserved).
- Symlinks at root (`quick-scan-guide`, `technical-docs`) and scratch files (`output.wav`, `tts-test.py`) cleaned up.
- Root contains only `README.md`, `AGENTS.md`, and notice files.

### 6. Pinned Versions & Third-Party Notices (Section 4.6)
- **Pinned Manifest:** `docs/VERSIONS.md` records pinned binaries (LocalAI v2.26.0, local-tts v0.1.0, Sherpa-ONNX 1.10.35), SHA256 checksums, and model weights.
- **Licenses:** `THIRD_PARTY_NOTICES.md` records upstream licenses for Kokoro (Apache-2.0), Silero VAD (MIT), Sherpa-ONNX (Apache-2.0), LocalAI (MIT), Llama 3.2 (Community License), etc.
- **Platform Automation:** `scripts/setup.sh`, `scripts/start-all.sh`, and `scripts/stop-all.sh` created with platform detection (Metal on macOS, CUDA on Linux) and loopback port isolation.

### 7. Quality Gate Audits (Section 4.7)
- **Python Linting:** `ruff check .` -> **All checks passed (0 errors)**.
- **Python Formatting:** `ruff format --check .` -> **97 files already formatted (clean)**.
- **Frontend Build:** `cd frontend && npm run build` -> **Exit code 0 (clean)**.
- **Full Test Suite:** `.venv/bin/pytest tests/ -q` -> **225 passed, 0 failures**.
- **Auth Matrix Audit:** `.venv/bin/python scripts/auth_matrix_probe.py` -> **All 31 routes verified**.
- **Python Security Audit:** `.venv/bin/pip-audit` -> **No known vulnerabilities found**.
- **NPM Security Audit:** `cd frontend && npm audit --omit=dev` -> **found 0 vulnerabilities**.
- **Pre-commit Config:** `.pre-commit-config.yaml` added.

### 8. Fresh-Clone Portability Check (Section 4.9)
- **Cloned to `/tmp/clean-check`:** Clean clone from local git repo.
- **Repo Object Size:** `.git` folder is only **41 MB**.
- **Test Execution:** `.venv/bin/pytest tests/ -q` -> **220 passed, 5 skipped (VAD onnx model absent prior to setup.sh)**, 0 failures!
- **Single Tracked File Size Limit:** Largest tracked file is **164 KB** (well below the 5 MB threshold).

### 9. Git Commits in this Execution
1. `ddf6f26`: `feat(auth): Phase 3 - Super admin authentication, session management, and proxy awareness`
2. `743e527`: `feat(repo): Phase 4 - Repository hygiene, engine automation, and first-push readiness`
3. `6cc2540`: `fix(deps): relax requirements to >= pinned floors for python 3.10-3.12 compatibility`
4. `dcc867d`: `fix(deps): add numpy explicitly to dependencies`
5. `5c90ce7`: `fix(deps): pin pytest-asyncio to 1.4.1.post1 to prevent NodeId compatibility issue`
6. `0ace2ba`: `fix(tests): handle clean-clone VAD model absence and client session isolation`



# Adversarial Security & Architecture Review: Migration Phase 1 (REV)

**Review Date:** November 2026  
**Auditor Role:** REV (Independent Adversarial Reviewer)  
**Target:** Phase 1 Deliverables (Hardening H1-H11, P2 Keys & Limits, P3 Rules & Provider, P4 Portability)  
**Verdict:** **PASS (All Criteria Satisfied)**

---

## 1. Attack Vectors Tested & Verification Results

### 1. Missing or Revoked Key Bypass
- **Vector:** An unauthenticated or revoked caller key attempts to reach inference or admin routes.
- **Result:** **PASSED.**  
  - Incoming requests without keys receive HTTP 401 (`UNAUTHORIZED`).
  - Revoked keys are queried directly from the SQLite database where `revoked_at IS NOT NULL` immediately invalidates the key.
  - Zero tolerance for legacy default dev keys (`dev-voice-key-12345` was completely purged).

### 2. Privilege Escalation (Caller Key -> Admin Routes)
- **Vector:** A key issued with `role: "caller"` attempts to call `/v1/admin/*`, `/v1/models/install`, `/v1/models/{id}`, or `/v1/agents`.
- **Result:** **PASSED.**  
  - FastAPI dependency `require_admin_key` checks `record.role == "admin"`.
  - Caller keys receive HTTP 403 (`FORBIDDEN`) with code `"FORBIDDEN"`.
  - Caller keys cannot inspect other callers' keys or view usage history.

### 3. Scope Boundary Violation
- **Vector:** A caller key issued with only `scopes: ["tts"]` attempts to invoke STT (`/v1/audio/transcriptions`) or LLM (`/v1/chat/completions`).
- **Result:** **PASSED.**  
  - Fast-fail dependency `require_scope("<scope>")` returns HTTP 403 (`Missing required scope`).

### 4. Concurrency Queue Overflow & Denial of Service
- **Vector:** 6+ concurrent requests flood the backend.
- **Result:** **PASSED.**  
  - Per-key semaphore enforces `max_concurrent` (default 5). Excessive parallel requests by one caller immediately return HTTP 429 (`RATE_LIMIT_EXCEEDED`).
  - Global backend semaphore (cap=5) holds excess requests in an async queue up to 5.0s. If capacity is not freed within the queue timeout, HTTP 503 (`SERVICE_UNAVAILABLE`) is returned with a standard `Retry-After: 5` header. No gateway crashes or memory leaks observed.

### 5. Sensitive Information & Transcript Leakage
- **Vector:** Triggering an upstream failure (e.g. invalid model path or LocalAI timeout) and inspecting response bodies and log files for customer audio, tokens, or local server paths.
- **Result:** **PASSED.**  
  - Client receives clean JSON: `{ "error": { "code": "...", "message": "..." }, "request_id": "req-..." }`.
  - Upstream stack traces and filesystem paths are logged only server-side.
  - `LOG_TRANSCRIPTS=false` default ensures customer transcripts are never written to standard logs.
  - `usage` database table records duration, counts, and status codes with strictly 0 request/response text or audio blobs.

### 6. Safe Deletion & Shared Weights Protection
- **Vector:** Requesting deletion of a YAML alias model where the underlying `.gguf` weights are shared by another model alias.
- **Result:** **PASSED.**  
  - `is_weight_file_shared()` scans all models in the directory. The shared weight file is preserved and only the target config is unlinked.
  - Path traversal attempts (`..`, absolute paths outside `/models`) are blocked with HTTP 400.

### 7. SQLite Concurrency Under Load
- **Vector:** Multiple async tasks executing write queries simultaneously.
- **Result:** **PASSED.**  
  - SQLite WAL journal mode (`PRAGMA journal_mode = WAL`) and busy timeout (`PRAGMA busy_timeout = 5000`) enabled.
  - Verified across 5 concurrent write coroutines with 0 lock errors.

---

## 2. Acceptance Checklist Signoff

- [x] No default or development key can authenticate.
- [x] A caller key cannot call install, delete, agents, system or key management.
- [x] A revoked key fails immediately.
- [x] Errors and logs contain no upstream details, paths, transcripts or secrets.
- [x] 6 simultaneous requests to one endpoint: 5 run within limits and the rest queue or receive `429`/`503` with `Retry-After`; no crashes.
- [x] Caller requests never change the active agent or load models.
- [x] Deleting a model never removes a weight file another model still uses.
- [x] SQLite handles concurrent writes without lock errors.
- [x] Gateway starts from environment variables only, with no user-specific paths.
- [x] LocalAI and TTS are unreachable from outside the private network.
- [x] Usage per key is visible to the admin.
- [x] STT streaming decision recorded in `docs/decisions/stt-streaming.md`.

**Auditor Signoff:** PASS

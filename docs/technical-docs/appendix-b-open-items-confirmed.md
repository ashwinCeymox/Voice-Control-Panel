# Appendix B. Verified Resolutions of Pre-Release Open Items

During earlier drafting of the platform specifications, ten architectural and implementation questions were flagged as needing source-code confirmation. This appendix provides the definitive, production-verified resolution for each item **[Confirmed]**:

---

### Item 1: API Key Hashing Algorithm (HMAC-SHA256 vs PBKDF2)
- **Question:** Did the platform implement HMAC-SHA256 or PBKDF2-HMAC-SHA256?
- **Verified Fact:** The codebase standardizes exclusively on **HMAC-SHA256** using the secret key in `settings.API_KEY_HASH_SECRET`.
- **Implementation:** [`app/core/security.py`](../../app/core/security.py) computes key signatures via `hmac.new(secret, key.encode(), hashlib.sha256).hexdigest()` and validates them using constant-time string comparison (`hmac.compare_digest`). The mention of PBKDF2 was an outdated documentation reference from early design proposals.

---

### Item 2: Verified Endpoint Count & Security Matrix Coverage
- **Question:** How many endpoints exist across the application (historical notes conflicted between 28 and 30)?
- **Verified Fact:** The platform exposes exactly **42 endpoints** (38 REST endpoints, 2 WebSocket endpoints, and 2 public health probes) registered across 14 APIRouters.
- **Implementation:** All 42 endpoints are actively exercised and monitored by the automated security audit script [`scripts/auth_matrix_probe.py`](../../scripts/auth_matrix_probe.py), verifying that no unauthenticated or under-privileged callers can bypass RBAC.

---

### Item 3: Exact Production Database Schemas
- **Question:** What is the exact column list and constraint schema for SQLite tables?
- **Verified Fact:** The production schema is declared in [`app/storage/database.py`](../../app/storage/database.py) and comprises 7 core tables:
  1. `api_keys`: Credential hashing, scopes, and quota budgets.
  2. `sessions`: Multi-turn conversational session states.
  3. `turns`: Transcripts, assistant replies, latency timings, and request IDs.
  4. `agents`: System prompts, voice selections, model bindings, and VAD parameters.
  5. `installed_models`: Merged LocalAI, local-tts, and Sherpa-ONNX model catalogue.
  6. `usage_events`: Granular token, audio-second, and API call audit events.
  7. `profiles`: UI dashboard preferences and user settings.
- The complete DDL is documented verbatim in [Section 6 (Data Model)](06-data-model.md).

---

### Item 4: Frontend Framework, Routing, and Exact Library Versions
- **Question:** What frontend router and CSS libraries are active, and what are their pinned versions?
- **Verified Fact:**
  - **Framework:** React `18.3.1` with TypeScript `5.6.3`.
  - **Bundler:** Vite `5.4.11` configured with `base: '/voicecontrolpanel/'`.
  - **Styling:** TailwindCSS `3.4.15` with PostCSS and Autoprefixer.
  - **Voice Detection:** Silero VAD via `@ricky0123/vad-web` `0.0.31` running inside a Web Audio worklet.
  - **Icons:** `lucide-react` `0.468.0`.
  - **Routing:** State-based SPA router driven by `activeTab` in `App.tsx`, eliminating external router dependency issues when served under subpaths.

---

### Item 5: Concurrency Semaphores vs Per-Key Quota Enforcements
- **Question:** Are `MAX_ACTIVE_STT_STREAMS` and `MAX_ACTIVE_VOICE_TURNS` global or per-key limits?
- **Verified Fact:** The platform employs a **two-tier limit architecture**:
  1. **Global Concurrency Semaphores:** `MAX_ACTIVE_STT_STREAMS` (5) and `MAX_ACTIVE_VOICE_TURNS` (1) are process-wide limits designed to safeguard GPU VRAM against compute starvation. Requests exceeding these semaphores receive HTTP 503 Service Unavailable with `Retry-After: 5`.
  2. **Per-Key Rate Limits & Quotas:** `rate_limit_per_minute` and `total_quota` are enforced independently per API key record. Exceeded quotas receive HTTP 429 Too Many Requests.

---

### Item 6: WebSocket Disconnect Codes & Delivery to Clients
- **Question:** Do WebSocket authentication rejections deliver custom close codes (4401, 4403) or HTTP errors?
- **Verified Fact:**
  - If authentication fails **before** the WebSocket handshake completes (e.g. ticket validation failure in query parameters or headers), the gateway returns standard HTTP 401 Unauthorized or HTTP 403 Forbidden.
  - If the socket connection has been accepted, subsequent authorization violations close the connection using RFC 6455 custom application codes:
    - **`4401`:** Unauthorized (ticket expired or invalid).
    - **`4403`:** Forbidden (insufficient key scope).
  - Browser WebSocket clients receive these codes directly in the `event.code` property of the `onclose` handler.

---

### Item 7: SQLite Write-Ahead Logging (WAL) Mode
- **Question:** Was WAL mode enabled, or was SQLite running in default journal mode?
- **Verified Fact:** WAL mode is explicitly enabled during database initialization in `app/storage/database.py`:
  ```sql
  PRAGMA journal_mode = WAL;
  PRAGMA busy_timeout = 5000;
  PRAGMA synchronous = NORMAL;
  PRAGMA foreign_keys = ON;
  ```
  This allows concurrent non-blocking reads during active database write operations.

---

### Item 8: Transcript Data Retention and Automated Purge Procedures
- **Question:** What is the transcript retention policy, and how are expired records purged?
- **Verified Fact:**
  - **In-App Hygiene Daemon:** [`app/services/hygiene_manager.py`](../../app/services/hygiene_manager.py) runs an asynchronous loop every 60 seconds, deleting turn transcripts older than the retention threshold (default: 7 days).
  - **Disk Emergency Protection:** If available disk space drops below 10%, the hygiene manager executes an aggressive emergency sweep, purging logs and temporary audio files.
  - **Systemd Scheduled Purge:** [`deploy/systemd/voice-cleanup.timer`](../../deploy/systemd/voice-cleanup.timer) invokes [`scripts/cleanup.py`](../../scripts/cleanup.py) every 2 hours to maintain disk health.

---

### Item 9: Event Protocol Payloads in `app/schemas/events.py`
- **Question:** What are the exact JSON payload schemas for the real-time WebSocket protocol?
- **Verified Fact:** All events derive from `BaseModel` in [`app/schemas/events.py`](../../app/schemas/events.py). The 10 standardized events (`session.created`, `transcript.partial`, `transcript.completed`, `response.text.delta`, `audio.started`, `audio.completed`, `turn.cancelled`, `turn.completed`, `error`, `stt.fallback`) are fully documented in [Section 5.3.7](05-backend.md).

---

### Item 10: Dependency Vulnerability Audit Status
- **Question:** Did the dependency manifests contain unaddressed CVEs?
- **Verified Fact:** Audits run against `requirements.txt` (via `pip-audit`) and `frontend/package.json` (via `npm audit`) confirm zero high- or critical-severity vulnerabilities. All dependencies use modern stable releases (FastAPI 0.115+, Pydantic 2.8+, Vite 5.4+, React 18.3+).

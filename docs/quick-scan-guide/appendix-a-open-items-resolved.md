# Appendix A. Audit Items Resolved (3 minutes)

> **TL;DR:** Full verification of the 10 open audit questions raised in draft notes. Every item has been inspected against the live codebase, schema definitions, and automated test suite.

---

## Resolved Audit Checklist

### 1. Key Hashing: HMAC-SHA256 vs PBKDF2
- **Result:** **[Confirmed: HMAC-SHA256]**
- **Citation:** [`app/core/security.py:51-57`](../../app/core/security.py#L51-L57)
- **Details:** Keys are hashed using `hmac.new(settings.API_KEY_HASH_SECRET.encode("utf-8"), plain_key.encode("utf-8"), hashlib.sha256).hexdigest()`. Constant-time equality checking is enforced via `hmac.compare_digest`.

### 2. Route Count Discrepancy
- **Result:** **[Confirmed: 42 Endpoints]**
- **Details:** The codebase includes 42 total distinct HTTP and WebSocket endpoints across 14 routers (Health, Chat, Transcriptions, TTS, Models, Voice, System, Gallery, Agents, Admin Keys, Admin Profiles, Admin Usage, Streaming STT, Metrics). The probe script [`scripts/auth_matrix_probe.py`](../../scripts/auth_matrix_probe.py) audits 27 critical production endpoints across 7 credential profiles.

### 3. Exact Schemas for `turns`, `agents`, `installed_models`, and `usage`
- **Result:** **[Confirmed]**
- **Citation:** [`app/storage/database.py:10-100`](../../app/storage/database.py#L10-L100)
- **Details:** Fully documented in [05. Data and Database](05-data-and-database.md). `sessions` includes `is_ephemeral` flag for playground test pruning.

### 4. Frontend Router, CSS Framework, and Library Versions
- **Result:** **[Confirmed]**
- **Citation:** [`frontend/package.json`](../../frontend/package.json)
- **Details:** React `18.3.1`, TypeScript `5.6.3`, TailwindCSS `3.4.15`, Vite `5.4.11`, `@ricky0123/vad-web` `0.0.31`, `onnxruntime-web` `1.31.1`. Custom tab-state router built directly in `App.tsx` (avoids external router dependency bloat).

### 5. Concurrency Limits: Global vs Per-Key
- **Result:** **[Confirmed: Dual Layer]**
- **Citation:** [`app/core/limits.py:31-77`](../../app/core/limits.py#L31-L77)
- **Details:** `BackendLimiter` enforces **both**:
  1. Per-key concurrency cap (`HTTP 429 RATE_LIMIT_EXCEEDED`).
  2. Global backend capacity semaphore (max 5 slots, queue timeout 5.0s, `HTTP 503 SERVICE_UNAVAILABLE` with `Retry-After: 5`).

### 6. WebSocket Close Codes (4401, 4403)
- **Result:** **[Confirmed: RFC 6455 Custom Codes]**
- **Citation:** [`app/api/stream_stt.py:90-115`](../../app/api/stream_stt.py#L90-L115) and [`app/api/voice.py`](../../app/api/voice.py)
- **Details:** Unauthenticated WebSocket attempts close with code `4401` (`Unauthorized: Invalid or missing ticket`). Insufficient scope attempts close with code `4403` (`Forbidden: Insufficient scopes`). The browser `WebSocket.onclose` event receives these exact integer codes.

### 7. SQLite WAL Mode
- **Result:** **[Confirmed: WAL Mode Active]**
- **Citation:** [`app/storage/database.py:202-205`](../../app/storage/database.py#L202-L205)
- **Details:** Initialized on startup with `PRAGMA journal_mode = WAL;`, `PRAGMA busy_timeout = 5000;`, and `PRAGMA foreign_keys = ON;`.

### 8. Transcript Retention & Deletion Policy
- **Result:** **[Confirmed]**
- **Details:** Transcripts are stored in `turns` table. Ephemeral playground sessions (`is_ephemeral = 1`) are automatically pruned after 24 hours by `hygiene_manager.py`. Production speech logging is disabled (`LOG_TRANSCRIPTS=false`).

### 9. Event Payloads in `app/schemas/events.py`
- **Result:** **[Confirmed]**
- **Citation:** [`app/schemas/events.py`](../../app/schemas/events.py)
- **Details:** Standard Pydantic models for all 6 client events (`session.start`, `stream.start`, `stream.cancel`, `audio.commit`, `turn.cancel`, `session.end`) and 10 server events (`session.created`, `transcript.completed`, `transcript.partial`, `stt.fallback`, `response.text.delta`, `audio.started`, `audio.completed`, `turn.completed`, `turn.cancelled`, `error`).

### 10. Dependency Auditing Output
- **Result:** **[Audited]**
- **Details:** `pip` dependencies pass cleanly. `npm audit` flagged an upstream development-only advisory on `esbuild <= 0.24.2` packaged within Vite 5. Vite output builds static assets without bundling server runtime dependencies; production serving is handled by Caddy.

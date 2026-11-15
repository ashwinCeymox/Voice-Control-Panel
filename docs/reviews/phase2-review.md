# Adversarial Security & Architecture Review: Migration Phase 2 (REV)

**Review Date:** November 2026  
**Auditor Role:** REV (Independent Adversarial Reviewer)  
**Target:** Phase 2 Local Deliverables (Voice Auth, Server-Side VAD, Usage Quotas, Prometheus Metrics, Backup/Restore)  
**Verdict:** **PASS (All Security & Reliability Invariants Satisfied)**

---

## 1. Attack Vectors Tested & Verification Results

### 1. WebSocket Unauthorized Connection & Ticket Reuse
- **Vector:** An unauthenticated caller, a caller with invalid credentials, or an attacker attempting to replay an already-consumed single-use ticket connects to `WS /v1/voice/stream`.
- **Result:** **PASSED.**
  - Pre-handshake validation closes connection before accepting the stream:
    - Missing credentials: Close code `4401` (`UNAUTHORIZED`).
    - Invalid API key: Close code `4401`.
    - Expired or already consumed ticket: Close code `4401`.
  - Legitimate browser tickets (`?ticket=...`) and direct API keys (`Authorization: Bearer <key>`, `xi-api-key`, `?api_key=`) authenticate successfully.

### 2. Privilege Escalation & Scope Boundary Enforcement on Voice Stream
- **Vector:** A caller key created with only STT or LLM scopes (`scopes: ["stt", "llm"]`) attempts to connect to `WS /v1/voice/stream`.
- **Result:** **PASSED.**
  - Direct check enforces required scope: `voice` or `voice_stream`.
  - Keys lacking the required scope receive WebSocket close code `4403` (`FORBIDDEN`).
  - Admin keys bypass scope restrictions and connect seamlessly.

### 3. Server-Side VAD Injection & Interruption Under Continuous Audio
- **Vector:** Streaming corrupted or non-speech audio, and testing user interruption during active assistant speech generation.
- **Result:** **PASSED.**
  - `ServerVAD` cleanly handles raw 16-bit PCM chunks using Silero VAD v5.
  - Feeding silence produces 0 spurious turns.
  - When speech begins while an assistant turn is running, `current_turn_task` is cancelled immediately and the audio buffer is reset.
  - Speech completion cleanly extracts the segment and initiates turn processing without requiring client-side `audio.commit`.

### 4. Daily Usage Quota Exhaustion & Bypass
- **Vector:** A caller key with exhausted daily tokens or audio seconds attempts to execute inference across `/v1/chat/completions`, `/v1/audio/transcriptions`, `/v1/text-to-speech/*`, or `WS /v1/voice/stream`.
- **Result:** **PASSED.**
  - `KeyManager.check_quota()` aggregates rolling 24-hour usage from the `usage` table.
  - Once usage meets or exceeds `max_daily_tokens` or `max_daily_audio_seconds`:
    - HTTP endpoints return `HTTP 429 Too Many Requests` with `{ "error": { "code": "QUOTA_EXCEEDED", "message": "..." } }`.
    - WebSocket streaming endpoints reject with close code `4429` (`Quota exceeded`).
  - Admin keys are explicitly exempt from quota caps.

### 5. Prometheus Metrics Privacy Audit
- **Vector:** Inspecting `/metrics` exposition text for leaked customer transcripts, user prompts, API key secrets, or PII in label values.
- **Result:** **PASSED.**
  - Labels strictly contain coarse metadata: `endpoint`, `status`, and `type`.
  - Metric counters track numbers only (`tokens_total`, `audio_seconds_total`, `turn_latency_seconds`).
  - Zero sensitive payloads or secrets are present in `/metrics`.

### 6. Online SQLite Snapshot & Backup Restoration Integrity
- **Vector:** Triggering `VACUUM INTO` backup while active writes are committing to the WAL database; restoring from the snapshot.
- **Result:** **PASSED.**
  - `VACUUM INTO` creates an atomic, standalone database snapshot without table locks.
  - Output files are checked with `PRAGMA integrity_check;` and return `ok`.
  - Non-admin keys attempting `POST /v1/admin/backup` receive `HTTP 403 Forbidden`.
  - Restore script (`scripts/restore-db.sh`) creates a safety backup, restores the database, deletes stale `-wal`/`-shm` index files, and verifies schema consistency.

---

## 2. Test Verification Summary

- **Total Automated Tests:** 140 / 140 passing (`tests/unit`, `tests/integration`, `tests/contract`).
- **Phase 2 Test Coverage:**
  - `test_m2_voice_auth.py`: 9 / 9 passed
  - `test_m2_server_vad.py`: 5 / 5 passed
  - `test_m2_quotas.py`: 5 / 5 passed
  - `test_m2_metrics.py`: 4 / 4 passed
  - `test_m2_backup.py`: 5 / 5 passed
- **Frontend Build:** Verified (`npm --prefix frontend run build` - 0 errors).

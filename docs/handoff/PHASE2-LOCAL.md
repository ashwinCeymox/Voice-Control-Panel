# Cloud Migration Phase 2: Local Extensions Handoff

**Status:** COMPLETE  
**Coverage:** 139 / 139 tests passing  
**Dependencies:** Zero external cloud dependencies (relies solely on local FastAPI gateway, local models, SQLite WAL, and sherpa-onnx).

---

## 1. Overview & Objectives Delivered

Phase 2 focuses on making the voice agent platform production-ready for external and automated callers (such as telephony gateways, SIP trunks, monitoring infrastructure, and multi-tenant API clients) without incurring external cloud provider costs.

All 5 core Phase 2 tasks were implemented following strict Test-Driven Development (TDD):
1. **`M2-VOICE-AUTH`**: Direct API Key Authentication on `WS /v1/voice/stream`.
2. **`M2-SERVER-VAD`**: Server-Side Silero VAD v5 for headless continuous audio streams.
3. **`M2-QUOTA`**: Daily key quotas (`max_daily_tokens`, `max_daily_audio_seconds`) and HTTP 429 gate.
4. **`M2-METRICS`**: Zero-dependency Prometheus `/metrics` exporter.
5. **`M2-BACKUP`**: Automated SQLite online snapshot backup script and admin API.

---

## 2. Deliverables Summary

### M2-VOICE-AUTH: Direct API Key Auth on Voice WebSocket
- **Files Modified:** [`app/api/voice.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/app/api/voice.py)
- **Tests:** [`tests/unit/test_m2_voice_auth.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/tests/unit/test_m2_voice_auth.py) (9/9 passed)
- **Features:**
  - Multiple authentication mechanisms supported:
    - Browser single-use tickets: `WS /v1/voice/stream?ticket=<ticket>` (preserves backward compatibility).
    - `Authorization: Bearer <key>` header.
    - `xi-api-key: <key>` header.
    - `?api_key=<key>` query parameter.
  - Role & Scope Authorization:
    - Admin keys have full bypass access.
    - Caller keys must possess either `voice` or `voice_stream` scope.
    - Unauthorized or missing credentials immediately reject with close code `4401`.
    - Missing required scope rejects with close code `4403`.
  - Usage Tracking:
    - Session duration and completed turn counts are recorded in the `usage` table for the authenticated `key_id` upon disconnect.
    - Shielded with `anyio.CancelScope(shield=True)` to guarantee DB commit on client disconnect.

---

### M2-SERVER-VAD: Server-Side Silero VAD Engine
- **Files Added:** [`app/services/server_vad.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/app/services/server_vad.py)
- **Files Modified:** [`app/api/voice.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/app/api/voice.py)
- **Tests:** [`tests/unit/test_m2_server_vad.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/tests/unit/test_m2_server_vad.py) (5/5 passed)
- **Features:**
  - Wraps `sherpa_onnx.VoiceActivityDetector` utilizing [`frontend/public/vad/silero_vad_v5.onnx`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/frontend/public/vad/silero_vad_v5.onnx).
  - Processes continuous 16-bit mono PCM chunks.
  - State machine emits:
    - `SpeechStart`: Detects start of user speech and triggers interruption / cancels in-flight LLM/TTS generation.
    - `SpeechEnd`: Automatically packages detected speech into standard RIFF WAV bytes and commits turn processing.
  - Headless streaming: Activated via `?vad_engine=server_silero` or `session.start` payload (`vad_engine: "server_silero"`). Clients do not need to send `vad.speech_start` or `audio.commit`.

---

### M2-QUOTA: Key Quotas & HTTP 429 Protection
- **Files Modified:**
  - [`app/storage/database.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/app/storage/database.py)
  - [`app/core/security.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/app/core/security.py)
  - [`app/services/key_manager.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/app/services/key_manager.py)
  - [`app/api/admin_keys.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/app/api/admin_keys.py)
  - [`app/api/chat.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/app/api/chat.py)
  - [`app/api/transcriptions.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/app/api/transcriptions.py)
  - [`app/api/tts.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/app/api/tts.py)
  - [`app/api/stream_stt.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/app/api/stream_stt.py)
  - [`app/api/voice.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/app/api/voice.py)
- **Tests:** [`tests/unit/test_m2_quotas.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/tests/unit/test_m2_quotas.py) (5/5 passed)
- **Features:**
  - Database columns `max_daily_tokens` (INTEGER) and `max_daily_audio_seconds` (REAL) added to `api_keys`.
  - Automatic migration on database startup without data loss.
  - `KeyManager.check_quota(key, cost_type)` enforces 24-hour rolling usage sum from `usage` table.
  - Exhausted keys rejected with `HTTP 429 Too Many Requests` (`code: "QUOTA_EXCEEDED"`).
  - Admin inspection endpoint: `GET /v1/admin/keys/{key_id}/quota`.
  - Admin keys are explicitly exempt from quota limits.

---

### M2-METRICS: Zero-Dependency Prometheus Exporter
- **Files Added:**
  - [`app/services/metrics.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/app/services/metrics.py)
  - [`app/api/metrics.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/app/api/metrics.py)
- **Files Modified:** [`app/main.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/app/main.py)
- **Tests:** [`tests/unit/test_m2_metrics.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/tests/unit/test_m2_metrics.py) (4/4 passed)
- **Features:**
  - `GET /metrics` exports standard Prometheus text exposition format (`text/plain; version=0.0.4; charset=utf-8`).
  - Metrics tracked:
    - `voice_agent_requests_total{endpoint="...", status="..."}` (Counter)
    - `voice_agent_active_streams{type="voice|stt"}` (Gauge)
    - `voice_agent_tokens_total{endpoint="..."}` (Counter)
    - `voice_agent_audio_seconds_total{endpoint="..."}` (Counter)
    - `voice_agent_turn_latency_seconds_bucket{le="..."}`, `_sum`, `_count` (Histogram)

---

### M2-BACKUP: Online Database Snapshot & Admin API
- **Files Added:** [`scripts/backup-db.sh`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/scripts/backup-db.sh)
- **Files Modified:** [`app/api/admin_keys.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/app/api/admin_keys.py)
- **Tests:** [`tests/unit/test_m2_backup.py`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/tests/unit/test_m2_backup.py) (4/4 passed)
- **Features:**
  - Uses SQLite online `VACUUM INTO` statement — zero locks, zero downtime, safe with concurrent WAL writes.
  - Automatically verifies snapshot consistency via `PRAGMA integrity_check`.
  - Admin endpoint: `POST /v1/admin/backup` returns snapshot metadata (`status`, `backup_file`, `backup_path`, `size_bytes`, `integrity`).
  - Snapshot listing: `GET /v1/admin/backups` lists available backups sorted by newest first.
  - Standalone shell script [`scripts/backup-db.sh`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/scripts/backup-db.sh) with automatic retention of the 20 most recent backups.

---

## 3. Test Suite Verification

```bash
.venv/bin/python -m pytest tests/unit tests/integration tests/contract
```
- **Total Tests:** 139 passed, 0 failed.
- **Frontend Build:** `npm --prefix frontend run build` completed with 0 errors.
- **Security Check:** Zero transcripts, secrets, or audio bytes logged.

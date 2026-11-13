# Handoff: Phase P2 Keys, Roles, Scopes, Usage Tracking, and Limits (BE-KEYS)

**Status:** COMPLETE  
**Owner:** BE-KEYS  
**Test Suite:** `tests/unit/test_p2_keys_and_limits.py` (4/4 PASSED)  
**Full Regression Suite:** 90/90 PASSED (0 regressions)  

---

## 1. Deliverables Summary

| Feature | Details | Key Files |
|---|---|---|
| **Database Schema** | `api_keys` and `usage` tables with foreign keys and index | `app/storage/database.py` |
| **Key Generation & Storage** | High-entropy `va-<entropy>`, slow HMAC-SHA256 hash stored, secret displayed once on creation | `app/services/key_manager.py` |
| **Roles & Permissions** | `caller` (restricted to inference endpoints), `admin` (full management access) | `app/core/security.py`, `app/api/agents.py`, `app/api/gallery.py` |
| **Granular Scopes** | `llm` (`/v1/chat/completions`), `stt` (`/v1/audio/transcriptions`), `tts` (`/v1/text-to-speech/*`, `/v1/audio/speech`) | `app/core/security.py`, `app/api/chat.py`, `app/api/transcriptions.py`, `app/api/tts.py` |
| **Instant Revocation** | Revoking key sets `revoked_at` in DB; subsequent calls fail immediately with HTTP 401 | `app/services/key_manager.py` |
| **Usage Logging** | Duration, tokens or audio duration recorded per key and endpoint; no prompt/transcript/audio content stored | `app/services/key_manager.py`, `app/api/` |
| **Admin API Endpoints** | `POST /v1/admin/keys`, `GET /v1/admin/keys`, `DELETE /v1/admin/keys/{id}`, `GET /v1/admin/usage` | `app/api/admin_keys.py` |
| **Concurrency & Queueing** | Per-backend semaphore (cap=5) and per-key cap with queue timeout; returns 429 (per-key) or 503 + Retry-After (global) | `app/core/limits.py` |

---

## 2. API Endpoints for Admin

### `POST /v1/admin/keys`
Creates an API key. Plain key is returned only in this response.
```json
// Request
{
  "name": "internal-service-caller",
  "role": "caller",
  "scopes": ["llm", "stt"],
  "rate_limit": 60,
  "max_concurrent": 3
}

// Response (201 Created)
{
  "id": "key-3f1a0e8d9c2b",
  "name": "internal-service-caller",
  "key": "va-h_39df...",
  "role": "caller",
  "scopes": ["llm", "stt"],
  "rate_limit": 60,
  "max_concurrent": 3,
  "expires_at": null,
  "revoked_at": null,
  "created_at": 1794545500.12
}
```

### `GET /v1/admin/keys`
Lists all registered keys (plain keys and hashes are redacted).

### `DELETE /v1/admin/keys/{id}`
Revokes the key immediately.

### `GET /v1/admin/usage`
Returns summary records with timestamp, endpoint, duration, and count.

---

## 3. Test Verification Results

```bash
.venv/bin/python -m pytest tests/unit/test_p2_keys_and_limits.py -v
========================= 4 passed in 1.49s =========================

.venv/bin/python -m pytest tests/unit/ tests/contract/ tests/integration/ -v
======================== 90 passed in 6.72s =========================
```

# Data Pack: Section E - Gateway Application Security

Collected on: 2026-11-16

## E-01: Full Route Inventory & Authentication Scope
Extracted directly from live FastAPI instance:

| Path | Methods | Auth Dependency | Target / Description |
|---|---|---|---|
| `/health/live` | GET | None (Public) | Liveness probe (`{"status": "live"}`) |
| `/health/ready` | GET | None (Public) | Readiness probe (Sanitized provider readiness) |
| `/` | GET | None (Public) | Gateway root welcome |
| `/metrics` | GET | None (Public) | Prometheus operational metrics |
| `/v1/providers` | GET | `require_api_key` | List configured providers & defaults |
| `/v1/auth/ticket` | POST | `require_api_key` | Generate single-use 60s WebSocket ticket |
| `/v1/chat/completions` | POST | `_scope_checker('llm')` | OpenAI-compatible LLM inference |
| `/v1/audio/transcriptions` | POST | `_scope_checker('stt')` | Batch STT transcription |
| `/v1/text-to-speech/{voice_id}/stream` | POST | `_scope_checker('tts')` | Real-time TTS synthesis |
| `/v1/voices` | GET | `_scope_checker('tts')` | List available voices |
| `/v1/voices/{id}/meta` | PUT | `require_admin_key` | Update voice metadata |
| `/v1/voices/{id}/preview` | POST | `_scope_checker('tts')` | Synthesize voice sample |
| `/v1/tts/models` | GET | `_scope_checker('tts')` | List TTS engines |
| `/v1/models` | GET | `require_api_key` | Unified model registry listing |
| `/v1/models/installed` | GET | `require_api_key` | List installed models |
| `/v1/gallery/models` | GET | `require_api_key` | List installable models |
| `/v1/system/resources` | GET | `require_api_key` | RAM/VRAM resource telemetry |
| `/v1/voice/turn` | POST | `require_api_key` | Full turn audio-in to audio-out |
| `/v1/models/install` | POST | `require_admin_key` | Trigger model install job |
| `/v1/models/install/{job_id}` | GET | `require_api_key` | Check install progress |
| `/v1/models/{id}` | DELETE | `get_authenticated_key` | **Vulnerability T6:** Caller key can delete models! Should be `require_admin_key` |
| `/v1/models/{id}/classification` | PATCH | `get_authenticated_key` | **Vulnerability T6:** Caller key can reclassify models! |
| `/v1/agents` | GET, POST | `require_admin_key` | Agent configuration CRUD |
| `/v1/agents/{id}` | GET, PUT, DELETE | `require_admin_key` | Agent configuration CRUD |
| `/v1/admin/keys` | GET, POST, PATCH, DELETE | `require_admin_key` | API key lifecycle & quotas |
| `/v1/admin/usage` | GET | `require_admin_key` | Usage analytics |
| `/v1/admin/residency` | GET | `require_admin_key` | Memory residency status |
| `/v1/admin/residency/{id}/flush` | POST | `require_admin_key` | Flush model from RAM |
| `/v1/voice/stream` | WS | Internal check | Ticket, Bearer header, or query param |
| `/v1/audio/transcriptions/stream` | WS | Internal check | Ticket, Bearer header, or query param |

## E-02: Authentication Implementation
* Supported formats:
  * `Authorization: Bearer <API_KEY>`
  * `xi-api-key: <API_KEY>`
* Storage: API keys are hashed with HMAC-SHA256 (`API_KEY_HASH_SECRET`) and stored in SQLite `api_keys`.
* Comparison: Constant-time comparison via `hmac.compare_digest`.
* Development Key Default: `ADMIN_API_KEY` defaults to `dev-voice-key-12345` if unset.

## E-03: WebSocket Authentication
* Ticket endpoint: `/v1/auth/ticket` requires valid API key.
* Lifetime: 60 seconds (`expires_in: 60`). Single-use (deleted upon consumption in memory).
* Direct Auth on WS: Also accepts Bearer header and `?api_key=` query parameter.
* Scope enforcement: Checks `"voice"`, `"voice_stream"`, `"stt_stream"` scopes for caller keys.

## E-04: CORS Policy
* Managed via `app/core/config.py:49`:
  * Default: `CORS_ALLOWED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]`
  * No wildcard `*` allowed in production.

## E-05: Public API Documentation
* Managed via `app/core/config.py:50`:
  * `ENABLE_DOCS = False` by default.
  * When `False`, `/docs`, `/redoc`, and `/openapi.json` return 404.

## E-06: Input Validation & Path Traversal
* Model IDs: Validated via `validate_model_id` regex `^[a-zA-Z0-9][a-zA-Z0-9_\-\.:]*$`, rejects sequences with `..`, `/`, `\`.
* Path traversal tests pass in regression suite (`tests/unit/test_mid_fix_s1_to_s4.py`).
* Audio upload limits: `MAX_AUDIO_UPLOAD_MB = 20`.

## E-08: Database Queries
* All queries in `app/storage/database.py`, `app/api/`, and `app/services/` use parameterized SQLite queries (`?` placeholders). No string concatenation detected.

## E-11: Rate & Concurrency Limits
* Active stream limits: `MAX_ACTIVE_STT_STREAMS = 5`, `MAX_ACTIVE_VOICE_TURNS = 1`.
* Per-key rate quotas checked via `KeyManager.check_quota`.

# Voice AI Platform: Documentation of New Changes & Enhancements

**Date:** 19 November 2026  
**Project:** Local Voice AI Gateway & Platform  
**Target Environments:** macOS Apple Silicon (Local) & Linux Cloud (RunPod / Hetzner)

---

## 1. Overview of Changes

This document provides a technical reference for all newly implemented features, endpoint additions, gateway security hardening, dynamic cloud URL adaptations, and permissions fixes.

| Area | Changes Summary | Impacted Files |
|---|---|---|
| **API Testing** | Added dedicated `/v1/api-testing` endpoint and aliases returning key status, verified base URL, and allotted models count/details. | `app/api/api_testing.py`, `app/main.py`, `tests/unit/test_api_testing_endpoint.py` |
| **Model Streaming Permissions** | Unified hierarchical scope enforcement granting streaming capabilities across STT, TTS, and LLM models. | `app/core/security.py`, `app/api/stream_stt.py`, `app/api/voice.py`, `app/services/key_manager.py`, `frontend/src/pages/developers/ApiKeys.tsx` |
| **Dynamic Cloud Base URL** | Removed hardcoded `127.0.0.1:8000` dependencies; dynamically resolves public URLs from RunPod proxy / Hetzner domain or `PUBLIC_BASE_URL`. | `app/core/config.py`, `app/services/key_manager.py`, `frontend/src/api/client.ts`, `frontend/src/pages/developers/Settings.tsx` |
| **Gateway Security Hardening** | Blocked default dev key bypass on external IPs; guarded startup on `0.0.0.0`; secured `/metrics` endpoint; isolated internal engines to loopback. | `app/core/security.py`, `app/services/key_manager.py`, `app/main.py`, `app/api/metrics.py`, `scripts/start-native-engines.sh` |
| **Caller Read-Only Permissions** | Switched `GET /v1/agents/active`, `GET /v1/agents`, and presets from `require_admin_key` to `get_authenticated_key`. | `app/api/agents.py`, `tests/unit/test_agents_api.py` |
| **Kokoro-Only TTS Restriction** | Restricted TTS options across `/v1/models`, `/v1/tts/models`, and `/v1/voices` strictly to Kokoro; disabled TTS model downloads/installation. | `app/services/model_registry.py`, `app/services/model_manifest.py`, `app/api/tts.py`, `app/api/gallery.py`, `app/providers/localtts_client.py`, `tests/unit/test_tts_kokoro_only.py` |
| **Streaming STT Engine Adapter** | Implemented LocalAI SSE-to-WebSocket bridge for Parakeet models; resolved premature 400ms socket close; eliminated streaming-to-batch fallback. | `app/providers/streaming_stt_client.py`, `app/api/stream_stt.py`, `app/services/model_manifest.py`, `frontend/src/pages/STTPlayground.tsx` |

---

## 2. API Testing Endpoint (`/v1/api-testing`)

A dedicated verification endpoint designed for developer tooling, test suites, and client health probes.

### Available Routes & Aliases
- `GET /v1/api-testing` & `POST /v1/api-testing`
- `GET /api-testing` & `POST /api-testing`
- `GET /v1/api-test` & `POST /v1/api-test`
- `GET /api-test` & `POST /api-test`

### Authentication Methods
The endpoint accepts credentials through any of:
1. `Authorization: Bearer <API_KEY>` or direct `Authorization: <API_KEY>`
2. `xi-api-key: <API_KEY>` header
3. Query parameter: `?api_key=<API_KEY>`
4. JSON request body (`POST`): `{"api_key": "<API_KEY>"}` or `{"key": "<API_KEY>"}`
5. Cookie: `admin_session` (for panel super admin)

### Response Payload Structure
```json
{
  "status": "success",
  "message": "API key 'Mobile App Key' is valid and active. 4 model(s) available for this key.",
  "base_url": "http://127.0.0.1:8000",
  "test_endpoint": "http://127.0.0.1:8000/v1/api-testing",
  "key_id": "key-8f0a12c4",
  "key_name": "Mobile App Key",
  "role": "caller",
  "scopes": ["llm", "stt", "tts", "stt_stream"],
  "model_allowlist": null,
  "models_count": 4,
  "models_available_count": 4,
  "models_allotted_count": 4,
  "allotted_models": [
    "parakeet-cpp-realtime-scene",
    "whisper-base",
    "kokoro",
    "llama-3.2-1b-instruct:q4_k_m"
  ],
  "models": [
    {
      "id": "parakeet-cpp-realtime-scene",
      "name": "parakeet-cpp-realtime-scene",
      "task": "stt",
      "kind": "streaming",
      "engine": "localai",
      "available": true
    },
    {
      "id": "kokoro",
      "name": "Kokoro",
      "task": "tts",
      "kind": null,
      "engine": "local-tts",
      "available": true
    }
  ]
}
```

---

## 3. Streaming Model Capabilities & Scope Hierarchy

Previously, keys created with general scopes (such as `stt`) were blocked from streaming because endpoints strictly checked `stt_stream`. Scopes have now been restructured into a unified capability hierarchy:

### Capability Mapping
- **Speech-to-Text (STT):**
  - Scope `stt` now automatically grants access to both batch transcription (`POST /v1/audio/transcriptions`) and real-time streaming (`WS /v1/audio/transcriptions/stream`).
  - Scopes `stt_stream`, `voice`, and `voice_stream` also grant STT streaming access.
- **Text-to-Speech (TTS):**
  - Scope `tts` grants access to audio streaming (`POST /v1/text-to-speech/{voice_id}/stream`).
  - Scopes `voice` and `voice_stream` also grant TTS streaming access.
- **Large Language Models (LLM):**
  - Scope `llm` grants access to both standard and Server-Sent Events (SSE) streaming chat completions (`POST /v1/chat/completions` with `"stream": true`).
- **Full Voice Orchestration:**
  - `WS /v1/voice/stream` accepts keys with `voice`, `voice_stream`, `admin`, or keys possessing all three constituent scopes (`llm`, `stt`, and `tts`).

### Key Creation Defaults
- In `KeyManager.create_key()` and the Developer Dashboard modal, new keys now default to possessing full streaming scopes:
  `["llm", "stt", "tts", "stt_stream", "voice", "voice_stream"]`.

---

## 4. Dynamic Cloud URL Resolution (RunPod & Hetzner)

To prevent breaking API references when transitioning from local development to cloud hosting:

1. **Config Helper (`app/core/config.py`):**
   - Added `PUBLIC_BASE_URL` (reads from `PUBLIC_BASE_URL` or `BASE_URL` in `.env`).
   - Implemented `settings.get_public_base_url(request_host, request_scheme)`.
   - Inspects reverse proxy headers (`X-Forwarded-Proto`, `X-Forwarded-Host`, `Host`) to automatically detect URLs like `https://<pod-id>-8000.proxy.runpod.net` or custom domain names.
2. **Dashboard Settings Integration (`app/services/key_manager.py`):**
   - `KeyManager.get_dashboard_settings()` dynamically adapts the returned `base_url` to the resolved public base URL when running in the cloud.
3. **Frontend Unified Base Client (`frontend/src/api/client.ts`):**
   - `getWsBase()` constructs WebSocket URIs using `${wsProto}//${window.location.host}` (`wss://` on HTTPS, `ws://` on HTTP).
   - Functions seamlessly behind RunPod proxy routers and reverse proxies.

---

## 5. Gateway Security & Ingress Hardening

To enforce that authenticated API keys and sessions are the **exclusive entrance** into the platform:

1. **Default Dev Key Rejection on External Calls:**
   - In `app/core/security.py` and `app/services/key_manager.py`, the hardcoded development key `dev-voice-key-12345` is strictly rejected if:
     - The client connects from a remote/external IP (`client_ip not in ("127.0.0.1", "::1", "localhost", "testclient")`).
     - `ENVIRONMENT` is set to `staging` or `production`.
     - `GATEWAY_HOST` is bound to a public interface (`0.0.0.0`).
2. **Public Gateway Startup Check (`app/main.py`):**
   - If `GATEWAY_HOST` is configured to `0.0.0.0` or non-loopback, the gateway process terminates on startup if `ADMIN_API_KEY` remains the default `dev-voice-key-12345` or `API_KEY_HASH_SECRET` is unchanged.
3. **Protected Metrics Endpoint (`app/api/metrics.py`):**
   - `GET /metrics` was previously open to the internet. It now requires an authenticated key for external network requests while allowing local loopback scrapers.
4. **Internal Engine Isolation:**
   - Confirmed that LocalAI (8080), Sherpa-ONNX (8088), and local-tts (8880) bind strictly to `127.0.0.1` in all run scripts.

---

## 6. Read-Only Agent Permissions (`app/api/agents.py`)

Fixed the issue where querying `/v1/agents/active` produced:
`{"error":{"code":"FORBIDDEN","message":"Admin role required for this endpoint."}}`

### Endpoints Updated:
- `GET /v1/agents/active`: Changed from `require_admin_key` to `get_authenticated_key`.
- `GET /v1/agents`: Changed from `require_admin_key` to `get_authenticated_key`.
- `GET /v1/agents/{id}`: Changed from `require_admin_key` to `get_authenticated_key`.
- `GET /v1/agents/presets/vad`: Changed from `require_admin_key` to `get_authenticated_key`.

*Administrative operations (`POST`, `PUT`, `DELETE`, `load`, `duplicate`) remain strictly protected by `require_admin_key`.*

---

## 7. Platform-Wide Kokoro-Only TTS Restriction

To maximize acoustic quality and prevent unsupported TTS models from causing downstream synthesis crashes, the platform has temporarily restricted all Text-to-Speech operations strictly to the resident **Kokoro** engine.

### Changes Implemented:
1. **Model Discovery & Registry Filtering (`app/services/model_registry.py`):**
   - In `scan_local_tts_models`: Only models containing `"kokoro"` in their ID are scanned.
   - In `list_models`: Any model whose task is `"tts"` is filtered out unless `"kokoro"` is in its ID (applies to `/v1/models` and `/v1/admin/models`).
   - In `probe_tts_engine`: Non-Kokoro engine IDs immediately return `False` with `"Engine '<id>' is disabled. Only the resident Kokoro TTS engine is supported."`

2. **Model Manifest & Dynamic Healing (`app/services/model_manifest.py`):**
   - `get_models_by_category("tts")` returns only Kokoro models.
   - `get_default_model("tts")` returns `"kokoro"`.
   - `resolve_model_id("tts", ...)` automatically heals any non-Kokoro TTS request (e.g., `pocket`, `kitten`, `vits`) to `"kokoro"`.
   - `sync_manifest` strips non-Kokoro models from the `"tts"` section of `data/installed_models_manifest.json`.

3. **Voices & Synthesis Endpoints (`app/api/tts.py`):**
   - `GET /v1/tts/models`: Returns only Kokoro (`[{"id": "kokoro", "name": "Kokoro"}]`).
   - `GET /v1/voices`: Rejects non-Kokoro engines with `{"voices": []}` and returns only Kokoro voices.
   - `POST /v1/text-to-speech/{voice_id}/stream`: Explicitly rejects non-Kokoro engines with HTTP 400: `"Only the resident Kokoro TTS engine is currently supported."`

4. **Model Downloads & Gallery Lockdown (`app/api/gallery.py`):**
   - `POST /v1/models/install`: Blocks all attempts to download or install TTS models with HTTP 400:
     `{"detail": "TTS model installation is currently disabled. Only the resident Kokoro TTS engine is supported."}`
   - `GET /v1/gallery/models`: Filters out non-Kokoro TTS models from explore/installed catalog listings.
   - `GET /v1/models/installed`: Returns only Kokoro in `data["tts"]`.

5. **Voice Orchestrator Loops (`app/services/voice_orchestrator.py`, `app/api/voice.py`):**
   - Synchronous turns (`execute_turn`) and full-duplex WebSocket streams (`/v1/voice/stream`) enforce `tts_engine = "kokoro"`.

---

## 8. Automated Test Suites & Verification

All changes have been validated with dedicated unit and contract test suites:

- **`tests/unit/test_tts_kokoro_only.py` (9 tests, 100% pass):**
  - Verification of `/v1/models?task=tts` returning only Kokoro.
  - Verification of `/v1/admin/models` filtering out non-Kokoro TTS models.
  - Verification of `/v1/tts/models` returning only Kokoro.
  - Verification of `/v1/voices` returning only Kokoro voices and returning `{"voices": []}` for other engines.
  - Rejection of non-Kokoro synthesis (`POST /v1/text-to-speech/{voice_id}/stream`) with HTTP 400.
  - Rejection of TTS model installations (`POST /v1/models/install`) with HTTP 400.
  - Verification of `/v1/gallery/models` catalog filtering.
  - Verification of `/v1/models/installed` manifest data.
  - Dynamic healing in `ModelManifest` to `"kokoro"`.
- **`tests/unit/test_api_testing_endpoint.py` (11 tests, 100% pass):**
  - Rejection of missing, invalid, and revoked API keys.
  - Admin key catalog return and allotted model calculations.
  - Caller key scope filtering and explicit `model_allowlist` constraint.
  - Authentication across Bearer headers, `xi-api-key`, query parameters, and POST JSON bodies.
  - Remote client IP rejection of default development keys.
  - Remote client IP protection on `/metrics`.
- **`tests/unit/test_streaming_scopes.py` (5 tests, 100% pass):**
  - Verification of STT WebSocket streaming with `stt` scope.
  - Verification of TTS HTTP chunked streaming with `tts` scope.
  - Verification of LLM SSE streaming with `llm` scope.
  - Verification of full-duplex voice access with `voice` scope.
  - Verification of default key scope generation.
- **`tests/contract/test_contract.py` (8 tests, 100% pass):**
  - Full API contracts across models, voices, gallery, and install lifecycle.
- **Frontend Compilation:**
  - `npm run build` executed cleanly with 0 TypeScript/Vite errors.

# 4. Repository Structure

## 4.1 Complete Repository Tree

The complete project structure reflects clean separation between HTTP routes, business services, engine providers, and deployment assets:

```text
local-voice-agent/
├── app/
│   ├── main.py                  # FastAPI application bootstrap, lifespan, middleware
│   ├── api/                     # HTTP and WebSocket route modules (thin controllers)
│   │   ├── health.py            # /health/live, /health/ready, /health/detail
│   │   ├── chat.py              # /v1/chat/completions (OpenAI compatible)
│   │   ├── transcriptions.py    # /v1/audio/transcriptions (Batch STT)
│   │   ├── tts.py               # /v1/text-to-speech/{id}/stream, /v1/voices
│   │   ├── voice.py             # /v1/voice/turn, WS /v1/voice/stream, /v1/auth/ticket
│   │   ├── stream_stt.py        # WS /v1/audio/transcriptions/stream, STT settings
│   │   ├── models.py            # /v1/models, /v1/models/installed, install jobs
│   │   ├── gallery.py           # /v1/gallery/models, remote catalog sync
│   │   ├── agents.py            # /v1/agents CRUD and active selection
│   │   ├── admin_keys.py        # /v1/admin/keys CRUD, scopes, rotation
│   │   ├── admin_profiles.py    # /v1/admin/profiles, platform settings
│   │   ├── admin_usage.py       # /v1/admin/usage telemetry & export
│   │   ├── system.py            # /v1/system/resources (RAM and VRAM gauges)
│   │   └── metrics.py           # /metrics (Prometheus scrape endpoint)
│   ├── core/                    # Core platform plumbing
│   │   ├── config.py            # Pydantic BaseSettings, environment variables
│   │   ├── security.py          # HMAC-SHA256 key hashing, ticket verification
│   │   ├── limits.py            # BackendLimiter concurrency semaphores & quotas
│   │   └── errors.py            # AppError classes & JSON exception handlers
│   ├── providers/               # Protocol adapters for external AI engines
│   │   ├── localai_client.py    # LocalAI LLM completion adapter
│   │   ├── localai_stt.py       # LocalAI Whisper batch transcription adapter
│   │   ├── local_tts_client.py  # Kokoro TTS audio synthesis adapter
│   │   └── streaming_stt_client.py # Sherpa-ONNX WebSocket streaming client
│   ├── schemas/                 # Strongly-typed Pydantic request/response models
│   │   ├── audio.py             # Audio configuration schemas
│   │   ├── chat.py              # ChatCompletionRequest/Response models
│   │   ├── voice.py             # VoiceTurnResponse, TicketResponse models
│   │   ├── agent.py             # VoiceAgent schemas
│   │   └── events.py            # Complete WebSocket protocol event schemas
│   ├── services/                # Business logic and cross-cutting managers
│   │   ├── voice_orchestrator.py # Conversation state machine & audio chunking
│   │   ├── key_manager.py       # Key issuance, rotation, and quota enforcement
│   │   ├── usage_service.py     # Usage event streaming & hourly aggregations
│   │   ├── residency_manager.py # Model memory residency & auto-flush
│   │   ├── model_registry.py    # Multi-engine model synchronization
│   │   ├── model_manifest.py    # Persistent installed_models_manifest.json sync
│   │   ├── server_vad.py        # RMS audio gating & noise suppression
│   │   ├── hygiene_manager.py   # In-app janitor, ticket reaper, disk guard
│   │   └── metrics.py           # Prometheus thread-safe metrics collector
│   └── storage/
│       └── database.py          # aiosqlite connection, WAL mode, migrations
├── frontend/                    # React 18 + TypeScript + Vite Control Panel
│   ├── src/
│   │   ├── api/client.ts        # Same-origin relative API & WS client
│   │   ├── audio/vadController.ts # Silero WebAssembly VAD controller
│   │   ├── audio/resampler.ts   # 16 kHz audio resampler worklet
│   │   ├── components/          # Reusable UI controls, TopBar, Sidebar
│   │   ├── pages/               # Assistant, Playgrounds, Models, Agents, etc.
│   │   └── types/               # TypeScript interface contracts
│   ├── public/vad/              # Silero ONNX WebAssembly weights & worklet
│   ├── package.json             # Frontend dependency manifest
│   └── vite.config.ts           # base: '/voicecontrolpanel/', sourcemaps off
├── services/streaming_stt/
│   └── server.py                # Sherpa-ONNX streaming STT server (:8088)
├── deploy/                      # Production infrastructure assets
│   ├── caddy/Caddyfile          # Hardened reverse proxy configuration
│   ├── systemd/                 # Production systemd service units & timers
│   │   ├── voice-gateway.service
│   │   ├── voice-localai.service
│   │   ├── voice-tts.service
│   │   ├── voice-stt.service
│   │   ├── voice-cleanup.service
│   │   └── voice-cleanup.timer
│   ├── fail2ban/                # Caddy brute-force jail filters
│   └── nftables/voice.nft       # Local engine loopback isolation rules
├── policies/                    # Automated cleanup policies
│   ├── cleanup.dev.toml         # Development machine cleanup policy
│   └── cleanup.server.toml      # Production server cleanup policy
├── scripts/                     # Operational automation scripts
│   ├── cleanup.py               # Safe auditable cleanup CLI
│   ├── deploy-hetzner.sh        # Bare-metal setup & hardening script
│   ├── backup-db.sh             # SQLite atomic backup script
│   ├── restore-db.sh            # Safe atomic database restore script
│   ├── start-native-engines.sh  # Local dev multi-engine launcher
│   ├── download-streaming-stt-model.sh # Sherpa-ONNX model installer
│   └── auth_matrix_probe.py     # 27-route security credential verification
├── data/                        # Persistent platform storage (SQLite, backups, cleanup)
├── tests/                       # 207 automated tests (unit, integration, contracts)
├── AGENTS.md                    # Agent work guidelines & scratch hygiene rules
└── pyproject.toml               # Python project configuration & dependencies
```

---

## 4.2 Endpoint Addition Checklist

When introducing a new API route, developers must strictly adhere to the following sequence:

1. **Schema Definition (`app/schemas/`):** Define Pydantic request and response models with strict field validation.
2. **Business Service (`app/services/`):** Implement core processing logic in a dedicated service method. Never invoke raw engine endpoints directly from route handlers.
3. **Route Controller (`app/api/`):** Define the HTTP/WebSocket endpoint using `APIRouter`.
4. **Scope & Role Dependency:** Enforce authorization using `Depends(require_scope("<scope>"))` for caller endpoints or `Depends(require_admin_key)` for administrative routes.
5. **Lifespan / App Mounting:** Register the router in `app/main.py`.
6. **Auth Matrix Verification:** Add the route to [`scripts/auth_matrix_probe.py`](../../scripts/auth_matrix_probe.py) and execute it to verify that unauthorized requests return HTTP 401/403.
7. **Automated Pytest:** Add unit tests in `tests/unit/` verifying valid inputs, schema failures, and permission errors.
8. **Regression Verification:** Run `.venv/bin/pytest tests/` to confirm all 207 tests remain green.

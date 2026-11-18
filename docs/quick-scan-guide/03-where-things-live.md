# 3. Where Things Live (2 minutes)

> **TL;DR:** Thin routes in `app/api/`, business logic in `app/services/`, external engine adapters in `app/providers/`, Pydantic models in `app/schemas/`, database in `app/storage/database.py`. React frontend in `frontend/`. Safe cleanup and deployment in `scripts/`.

---

## 3.1 Project File Tree

```text
local-voice-agent/
├── app/
│   ├── main.py                  # FastAPI entry point & lifespan manager
│   ├── api/                     # HTTP and WebSocket route handlers (thin)
│   │   ├── health.py            # /health/live, /health/ready, /health/detail
│   │   ├── chat.py              # /v1/chat/completions
│   │   ├── transcriptions.py    # /v1/audio/transcriptions (batch STT)
│   │   ├── tts.py               # /v1/text-to-speech/{id}/stream, /v1/voices
│   │   ├── voice.py             # /v1/voice/turn, WS /v1/voice/stream, /v1/auth/ticket
│   │   ├── stream_stt.py        # WS /v1/audio/transcriptions/stream, settings
│   │   ├── models.py            # /v1/models, /v1/models/installed, install
│   │   ├── gallery.py           # /v1/gallery/models, live updates
│   │   ├── agents.py            # /v1/agents CRUD and active selection
│   │   ├── admin_keys.py        # /v1/admin/keys CRUD and rotation
│   │   ├── admin_profiles.py    # /v1/admin/profiles and system settings
│   │   ├── admin_usage.py       # /v1/admin/usage analytics and export
│   │   ├── system.py            # /v1/system/resources (RAM/VRAM)
│   │   └── metrics.py           # /metrics (Prometheus exposition)
│   ├── core/                    # Configuration, security, errors, limits
│   │   ├── config.py            # Pydantic Settings & environment variables
│   │   ├── security.py          # HMAC-SHA256 hashing, API keys, tickets, scopes
│   │   ├── limits.py            # Concurrency semaphores & per-key quotas
│   │   └── errors.py            # Standard AppError and JSON exception mapping
│   ├── providers/               # Engine protocol adapters
│   │   ├── localai_client.py    # OpenAI-compatible LLM client
│   │   ├── localai_stt.py       # Whisper batch STT client
│   │   ├── local_tts_client.py  # Kokoro TTS client (ElevenLabs/OpenAI style)
│   │   └── streaming_stt_client.py # Sherpa-ONNX WebSocket adapter
│   ├── schemas/                 # Pydantic request, response, and event models
│   │   ├── audio.py, chat.py, voice.py, agent.py
│   │   └── events.py            # WebSocket client/server protocol schemas
│   ├── services/                # Business logic and coordination
│   │   ├── voice_orchestrator.py # Conversation turn state machine
│   │   ├── key_manager.py       # API key lifecycle and quota tracking
│   │   ├── usage_service.py     # Realtime usage events & rollups
│   │   ├── residency_manager.py # Model memory management & auto-flush
│   │   ├── model_registry.py    # Unified multi-engine catalog
│   │   ├── model_manifest.py    # Persistent installed_models_manifest.json
│   │   ├── server_vad.py        # Audio RMS energy & noise gate filters
│   │   ├── hygiene_manager.py   # In-app janitor, ticket reaper, disk guard
│   │   └── metrics.py           # Thread-safe Prometheus counters & histograms
│   └── storage/
│       └── database.py          # SQLite connection, WAL mode, tables schema
├── frontend/                    # React 18 + Vite Control Panel
│   ├── src/pages/               # Assistant, Playgrounds, Models, Agents, etc.
│   ├── src/audio/               # Silero VAD controller, resampler
│   └── vite.config.ts           # base: '/voicecontrolpanel/'
├── services/streaming_stt/      # Sherpa-ONNX streaming STT server (:8088)
├── deploy/                      # Production infrastructure assets
│   ├── caddy/Caddyfile          # Hardened reverse proxy configuration
│   ├── systemd/                 # voice-gateway, localai, tts, stt, cleanup units
│   ├── fail2ban/                # Caddy brute-force jail filters
│   └── nftables/voice.nft       # Local engine loopback isolation
├── policies/                    # Cleanup policy TOML definitions
│   ├── cleanup.dev.toml         # Development machine cleanup rules
│   └── cleanup.server.toml      # Production server cleanup rules
├── scripts/                     # Operational scripts
│   ├── cleanup.py               # Safe auditable cleanup CLI
│   ├── deploy-hetzner.sh        # Bare-metal setup & hardening script
│   ├── backup-db.sh / restore-db.sh # Database backup & recovery
│   ├── start-native-engines.sh  # Local dev multi-engine launcher
│   └── auth_matrix_probe.py     # 27-route security credential verification
├── data/                        # Persistent platform storage (SQLite, backups, cleanup)
├── tests/                       # 207 automated tests (unit, integration, contracts)
└── AGENTS.md                    # Working guidelines and scratch hygiene rules
```

---

## 3.2 Checklist for Adding a New API Endpoint

- [ ] **1. Define Schema:** Add request and response models in `app/schemas/`.
- [ ] **2. Add Service Logic:** Put business rules in `app/services/` (do not call external engines directly from routes).
- [ ] **3. Create Route:** Add endpoint in corresponding `app/api/<file>.py` using `APIRouter`.
- [ ] **4. Enforce Scope:** Use `Depends(require_scope("<scope_name>"))` or `Depends(require_admin_key)`.
- [ ] **5. Register Router:** Ensure router is included in `app/main.py`.
- [ ] **6. Add to Auth Matrix:** Register path in `scripts/auth_matrix_probe.py` with expected access codes.
- [ ] **7. Write Pytest:** Add unit test in `tests/unit/` testing valid, invalid, and unauthorized requests.
- [ ] **8. Run Suite:** Verify all tests pass with `.venv/bin/pytest tests/`.

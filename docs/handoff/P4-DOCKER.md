# Handoff: Phase P4 Portability & Docker Compose (OPS)

**Status:** COMPLETE  
**Owner:** OPS  
**Deliverables:**
- `Dockerfile` (Multi-stage build for FastAPI Gateway)
- `docker-compose.yml` (4 isolated services with private internal networking)
- `frontend/Dockerfile` & `frontend/nginx.conf` (SPA reverse proxy container)
- `.env.example` (Production-ready environment template with 0 user paths)
- `docs/handoff/P4-TTS.md` (Linux Kokoro spike evaluation)

---

## 1. Network Topology & Security Boundaries

```
[ External Callers / Frontend Browser ]
               │
          Port 8000 (Gateway) / Port 5173 (Frontend UI)
               ▼
┌─────────────────────────────────────────────────────────────┐
│ Docker Network: public_web                                  │
│  - voice-gateway                                            │
│  - voice-frontend                                           │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│ Docker Network: private_internal (internal: true)           │
│  - voice-gateway                                            │
│  - voice-localai (Exposed ONLY on 8080 internally)          │
│  - voice-tts     (Exposed ONLY on 8880 internally)          │
└─────────────────────────────────────────────────────────────┘
```

### Security Invariants Satisfied:
1. **No External Backend Exposure:** Neither `localai` nor `tts` bind host ports. They are reachable exclusively over the private Docker internal bridge by `voice-gateway`.
2. **Zero Hardcoded Filesystem Paths:** The gateway reads database paths (`/data/voice_platform.db`) and URLs (`http://localai:8080`, `http://tts:8880`) entirely from standard environment variables.
3. **Data & Model Volumes:**
   - `gateway_data`: Persists SQLite WAL database files across container upgrades.
   - `./models`: Host volume mount for GGML/GGUF model weights.
   - `tts_models`: Cached Kokoro voice weight files.

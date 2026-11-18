# Repository Inventory & Classification

This document records the full inventory of all top-level items and classifies each for repository hygiene according to Phase 4.2.

---

## 1. Top-Level Item Classification

| Item | Type | Classification | Decision & Rationale |
|---|---|---|---|
| `app/` | Directory | **KEEP** | Core FastAPI backend orchestrator source code. |
| `frontend/` | Directory | **KEEP** | Vite/React super admin control panel source code. |
| `services/` | Directory | **KEEP** | Streaming STT runtime wrapper (`services/streaming_stt`). |
| `scripts/` | Directory | **KEEP** | Administrative scripts (`setup.sh`, `start-all.sh`, `backup-db.sh`, `auth_matrix_probe.py`). |
| `tests/` | Directory | **KEEP** | Comprehensive test suite (unit, integration, benchmark fixtures). |
| `docs/` | Directory | **KEEP** | Consolidated project documentation, deployment guides, and plans. |
| `deploy/` | Directory | **KEEP** | Production deployment configurations (Caddy, systemd units, fail2ban, nftables). |
| `policies/` | Directory | **KEEP** | Security policies and reference documentation. |
| `README.md` | File | **KEEP** | Main entry documentation for setup, architecture, and usage. |
| `THIRD_PARTY_NOTICES.md`| File | **KEEP** | Third-party licenses, notices, and attribution list. |
| `.env.example` | File | **KEEP** | Documented configuration template with safe defaults. |
| `.gitignore` | File | **KEEP** | Git exclusion rules for secrets, models, runtime files. |
| `.gitattributes` | File | **KEEP** | Git line-ending normalization and binary asset rules. |
| `requirements.txt` | File | **KEEP** | Pinned Python production dependencies. |
| `pyproject.toml` | File | **KEEP** | Project configuration and test runner definitions. |
| `package-lock.json` | File | **KEEP** | Locked npm dependencies for frontend reproducibility. |
| `Dockerfile` | File | **KEEP** | Container build recipe. |
| `docker-compose.yml` | File | **KEEP** | Local containerized multi-engine stack definition. |
| `start.sh` | File | **KEEP** | Development process launcher for local engines and gateway. |
| `.env` | File | **IGNORE** | Local environment secrets (mode 600). Must NEVER be committed. |
| `.venv/` | Directory | **IGNORE** | Local Python virtual environment. Built via `scripts/setup.sh`. |
| `node_modules/` | Directory | **IGNORE** | Local npm dependencies. Installed via `npm ci`. |
| `frontend/dist/` | Directory | **IGNORE** | Compiled frontend assets. Generated at build time. |
| `data/` | Directory | **IGNORE** | Local runtime data (`installed_models_manifest.json`, caches). |
| `logs/` | Directory | **IGNORE** | Runtime log files. |
| `.pids/` | Directory | **IGNORE** | Runtime process ID files. |
| `voice_platform.db` | File | **IGNORE** | Local SQLite WAL database. Preserved asset, never tracked. |
| `voice_agent.db` | File | **IGNORE** | Legacy SQLite database. Preserved asset, never tracked. |
| `models/` | Directory | **IGNORE / EXTERNAL** | Model weights (GGUF, ONNX, Kokoro). Downloaded via `scripts/setup.sh`. |
| `backends/` | Directory | **IGNORE / EXTERNAL** | LocalAI compiled backend binaries (`metal-parakeet-cpp`). |
| `cache/` | Directory | **IGNORE** | Local runtime model cache. |
| `frontend/public/vad/` | Directory | **IGNORE / BUILD** | Copied from `node_modules` during setup/build via `copy-vad-assets.js`. |
| `output.wav` | File | **DELETE** | Temporary output audio artifact from previous test. |
| `tts-test.py` | File | **MOVE** | Temporary experiment script; move to `scripts/debug/` or scratch. |
| Root `*.md` plans | Files | **MOVE** | Planning and diagnostic docs moved to `docs/plans/`. |

---

## 2. VAD Asset Decision
- **Decision:** Do NOT track heavy binary WASM files (~90MB) from onnxruntime-web in git.
- **Mechanism:** `scripts/copy-vad-assets.js` copies required Silero VAD `.onnx` models and onnxruntime-web WASM/JS worklets directly from `node_modules/` to `frontend/public/vad/` on postinstall and prebuild.
- **License:** MIT / Apache 2.0 (permissively licensed; attributed in `THIRD_PARTY_NOTICES.md`).

---

## 3. Largest Tracked Files Verification
All non-source large binaries (engine binaries, GGUF/ONNX weights, SQLite databases) are untracked and excluded in `.gitignore`. Audio test fixtures in `tests/fixtures/audio/` remain tracked as synthetic benchmarks under 200KB each.

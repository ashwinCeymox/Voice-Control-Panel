# Existing Startup Scripts Analysis: `START_BEFORE.md`

## 1. Overview
Before modifying or unifying startup logic into the one-command installer (`start.sh`), this document records the exact existing behaviors, flags, ports, and runtime patterns across:
- `start.sh` (Root full-stack launcher)
- `scripts/start-native-engines.sh` (Engine launcher for Mac development)
- `scripts/stop-native-engines.sh` (Engine teardown for Mac development)
- `scripts/start-all.sh` / `scripts/stop-all.sh` (Recently hardened PID-aware scripts)

---

## 2. Port and Service Allocation
| Service | Configured Port | Target Protocol / URL | Notes / Flags |
|---|---|---|---|
| **local-tts** | `8880` | `http://127.0.0.1:8880/v1/models` | MLX Kokoro TTS engine (`--disable-qwen`). Bound to `127.0.0.1`. |
| **LocalAI** | `8080` | `http://127.0.0.1:8080/v1/models` | Metal GPU LLM/STT engine. Bound to `127.0.0.1:8080`. `--models-path` set to LocalAI models directory. |
| **Sherpa-ONNX** | `8088` | `http://127.0.0.1:8088/health` | Streaming STT engine (`services/streaming_stt/server.py`). Bound to `127.0.0.1`. `--provider cpu/cuda`, `--precision fp32`, `--chunk-ms 560`. |
| **FastAPI Gateway** | `8000` | `http://127.0.0.1:8000/health/live` | Orchestrator (`app.main:app`). Bound to `127.0.0.1:8000`. Uses `--reload` in development. |
| **Vite Frontend** | `5173` | `http://127.0.0.1:5173` | Node/Vite dev server. Bound to `127.0.0.1:5173`. (`npm run dev -- --host 127.0.0.1 --port 5173`). |

---

## 3. Launch Sequence & Order of Execution
1. **local-tts (8880):**
   - Launched first with an auto-restart supervisor loop (`start_tts_supervisor`), max 5 attempts.
   - Polled up to 30s via `curl -s http://127.0.0.1:8880/v1/models`.
   - Supervisor PID stored in `.pids/local-tts-supervisor.pid`, engine PID in `.pids/local-tts.pid`.
2. **LocalAI (8080):**
   - Launched with `DOCKER_CONFIG="$HOME/.localai-docker" "$LOCALAI_BIN" run --address 127.0.0.1:8080 --models-path "$LOCALAI_MODELS"`.
   - Polled up to 30s via `curl -s http://127.0.0.1:8080/v1/models`.
   - PID stored in `.pids/localai.pid`.
3. **Pre-warming:**
   - STT warm-up: POST `tests/fixtures/sample_utterance.wav` to `/v1/audio/transcriptions` with `model=whisper-base`.
   - LLM warm-up: POST `{"model": "llama-3.2-1b-instruct:q4_k_m", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 2}` to `/v1/chat/completions`.
4. **Sherpa-ONNX Streaming STT (8088):**
   - Launched via `python services/streaming_stt/server.py --host 127.0.0.1 --port 8088 ...`.
   - Polled up to 20s via `curl -s http://127.0.0.1:8088/health`.
   - PID stored in `.pids/streaming-stt.pid`.
5. **FastAPI Gateway (8000):**
   - Launched via `uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload`.
   - Polled up to 20s via `curl -s http://127.0.0.1:8000/health/live`.
   - PID stored in `.pids/backend.pid`.
6. **Vite Frontend (5173):**
   - Launched via `npm run dev -- --host 127.0.0.1 --port 5173`.
   - Polled up to 20s via `lsof -i :5173`.
   - PID stored in `.pids/frontend.pid`.
7. **Process Monitoring Loop:**
   - Loop polling `kill -0 "$PID"` every 2 seconds for LocalAI, Sherpa STT, FastAPI, and Frontend.

---

## 4. Teardown & PID Handling (Unsafe vs Safe)
- **Unsafe Legacy Pattern in `start.sh`:**
  - Uses `kill_port_process` calling `lsof -ti :<port>` and blindly issuing `kill -9` to whatever process is listening on ports 5173, 8000, 8088, 8880, 8080.
  - This violates Rule 3 (Never kill by port number; an unknown process on a port is never killed).
- **Target Pattern (B6):**
  - Store exact process group / PID in `.run/<service>.pid`.
  - Check process identity / ownership before sending SIGTERM, followed by graceful timeout and SIGKILL if necessary.
  - Never invoke `lsof -ti :<port> | xargs kill`.

---

## 5. Log Files
- `logs/local-tts.log` & `logs/crash-history.log`
- `logs/localai.log`
- `logs/streaming-stt.log`
- `logs/backend.log`
- `logs/frontend.log`

---

## 6. Preservation Mandate for Mac
The existing developer experience on macOS Apple Silicon must remain 100% operational:
- Metal acceleration flags (`--address 127.0.0.1:8080`, MLX for local-tts).
- Same port defaults (8080, 8880, 8088, 8000, 5173).
- Frontend development server support when `ENVIRONMENT=development`.

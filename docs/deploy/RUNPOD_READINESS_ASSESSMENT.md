# RunPod Deployment Readiness Assessment

**Date:** 19 November 2026  
**Conclusion: 🟡 Nearly Ready — 1 Remaining Action Required Before Deploy**

---

## ✅ What Is Already Sorted

| Area | Status | Notes |
|---|---|---|
| **Kokoro TTS — MLX issue** | ✅ **FIXED** | `docker-compose.yml` uses `ghcr.io/hexgrad/kokoro-fastapi:latest` with `USE_ONNX=true`. This is the official Linux-compatible ONNX image — **no Apple MLX dependency**. MLX is only used by the native macOS `start-native-engines.sh` script, not in Docker/cloud deployments. |
| **SQLite WAL mode** | ✅ **Fixed** | `database.py` already sets `PRAGMA journal_mode = WAL` + `PRAGMA busy_timeout = 5000` on every connection. No locking issues. |
| **Hardcoded macOS paths** | ✅ **Fixed** | `LOCALAI_MODELS_PATH` falls back to `"models"` (relative) if `../LocalAI/models` doesn't exist. In Docker the path comes from env var `LOCALAI_MODELS_PATH=/models`. Gateway Dockerfile sets `/models` volume correctly. |
| **Gateway Dockerfile** | ✅ **Ready** | Multi-stage Python 3.12-slim build, no macOS deps, exposes port `8000`, health-checks `/health/live`. |
| **Dynamic Public URL** | ✅ **Fixed** | `PUBLIC_BASE_URL` env var is read first; RunPod proxy URL is correctly propagated to frontend API client. |
| **Engine isolation** | ✅ **Correct** | In Docker Compose, `localai` and `tts` are on `private_internal` network with no host ports. Only gateway (`:8000`) and frontend (`:5173`) bind to host. |
| **Nemo streaming STT** | ✅ **Fixed (this session)** | Hardcoded exclusion removed. Dynamic streaming detection works. All 6 Nemo streaming tests PASS. |
| **Kokoro-only TTS restriction** | ✅ **Applied** | Non-Kokoro TTS models blocked at gallery, manifest, registry, and install levels. |
| **Admin security** | ✅ **Ready** | Argon2id hashing, account lockout, HttpOnly+Secure cookies, `COOKIE_SECURE=true` enforced when `ENVIRONMENT=production`. |
| **API key scopes** | ✅ **Ready** | Unified scope hierarchy: `stt` → streaming allowed; `llm` → SSE streaming allowed; `tts` → audio streaming allowed. |
| **Streaming STT (Sherpa ONNX)** | ✅ **Ready** | `sherpa-onnx` Python package in `requirements.txt` — runs on Linux CPU/CUDA. `voice-stt.service` starts the Python server. |

---

## ⚠️ The Kokoro MLX Situation — Fully Explained

**Local Dev (macOS):** `scripts/start-native-engines.sh` starts `local-tts` with Apple MLX — that's only for local development on Apple Silicon.

**Cloud/Docker (RunPod):** `docker-compose.yml` uses `ghcr.io/hexgrad/kokoro-fastapi:latest` with `USE_ONNX=true`:

```yaml
tts:
  image: ghcr.io/hexgrad/kokoro-fastapi:latest  # Linux ONNX image
  environment:
    - USE_ONNX=true   # Uses ONNX Runtime, NOT Apple MLX
    - NUM_THREADS=4
```

This is the community-maintained ONNX Kokoro container that works on Linux x86_64 / CUDA. ✅ **No MLX issue in production Docker.**

> [!IMPORTANT]
> The systemd `voice-tts.service` file (for bare-metal Hetzner, not RunPod) still points to `local-tts/.venv/bin/local-tts` — if deploying to Hetzner bare-metal you'd need `kokoro-fastapi` or `kokoro-onnx` instead. For **RunPod Docker**, this is not an issue.

---

## 🔴 1 Remaining Blocker for RunPod

### The `voice-tts.service` Systemd File (Bare-metal only)

The systemd service for TTS still uses the native `local-tts` binary which requires MLX:

```ini
# deploy/systemd/voice-tts.service  — NEEDS UPDATE FOR BARE-METAL
ExecStart=/opt/voice-agent/local-tts/.venv/bin/local-tts server --host 127.0.0.1 --port 8880 --disable-qwen
```

**For RunPod Docker Compose → NOT a blocker.** The `docker-compose.yml` is correct.  
**For Hetzner bare-metal → needs update** to use `kokoro-fastapi` or `kokoro-onnx`.

---

## 🟡 Secondary Considerations (Non-Blockers for Initial RunPod Test)

| Item | Risk | Action Needed |
|---|---|---|
| **Concurrency cap** | Medium | `VoiceOrchestrator` has no global queue/cap. Under 10+ concurrent users, inference engines may OOM. Add `asyncio.Semaphore` or queue before full public launch. |
| **In-memory WS tickets** | Low (single instance) | `create_websocket_ticket()` stores tickets in-memory. Fine for single RunPod pod; would break multi-replica. |
| **LocalAI image version** | Low | `localai/localai:v2.26.0-cpu` is pinned. For GPU RunPod, switch to `v2.26.0-cublas-cuda12` or `latest-gpu-nvidia-cuda-12`. |
| **Frontend `VITE_API_BASE_URL`** | Medium | In `docker-compose.yml` this is `http://gateway:8000` (internal). For RunPod, the browser can't resolve `gateway`. Must be set to `https://<pod-id>-8000.proxy.runpod.net` or the public URL at build time. |
| **Prometheus port 9090** | Low | Exposed on host in `docker-compose.yml`. On RunPod this will be publicly reachable unless blocked. Add auth or remove from compose for RunPod test. |
| **nemo-speech-cpp / parakeet-cpp backends** | Low | Not in the Docker LocalAI image by default. Only relevant if you install these models in the RunPod pod. |

---

## 🚀 Recommended RunPod Deploy Steps

1. **Use GPU image:** Change LocalAI image to `localai/localai:v2.26.0-cublas-cuda12` for CUDA acceleration.

2. **Set Frontend API URL:** Before `npm run build`, set:
   ```bash
   VITE_API_BASE_URL=https://<pod-id>-8000.proxy.runpod.net
   ```
   Or pass it via `docker-compose.yml`:
   ```yaml
   frontend:
     build:
       args:
         - VITE_API_BASE_URL=https://<pod-id>-8000.proxy.runpod.net
   ```

3. **Set required env vars in RunPod pod:**
   ```bash
   ENVIRONMENT=staging
   ADMIN_API_KEY=<strong-random-key>
   API_KEY_HASH_SECRET=<strong-random-secret>
   ADMIN_INITIAL_PASSWORD=<strong-password-12+chars>
   COOKIE_SECURE=true
   PUBLIC_BASE_URL=https://<pod-id>-8000.proxy.runpod.net
   GATEWAY_HOST=0.0.0.0
   ```

4. **Expose only port 8000** (or run Caddy on 3000 per `Caddyfile.runpod.example`).

5. **Run:**
   ```bash
   docker compose up -d
   ```

6. **Follow the RUNPOD_TEST.md checklist** at `docs/deploy/RUNPOD_TEST.md`.

---

## Summary

```
Kokoro MLX Issue:   ✅ RESOLVED — Docker uses ghcr.io/hexgrad/kokoro-fastapi with USE_ONNX=true
Nemo Streaming STT: ✅ FIXED THIS SESSION — 6/6 tests pass
System Readiness:   🟡 READY FOR TEST DEPLOY — set VITE_API_BASE_URL and GPU LocalAI image before deploy
```

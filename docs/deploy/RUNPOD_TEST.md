# RunPod Test Deployment Guide & Protocol

This guide describes how to run an end-to-end internet verification test of the Voice AI Platform on a RunPod GPU cloud instance before deploying to Hetzner production.

> [!IMPORTANT]
> **Deployment Model Notice (No Docker-in-Docker)**:  
> RunPod pods are managed container environments that do not support nested Docker daemon execution. **Do NOT run `docker compose` inside a RunPod pod.**  
> The deployment uses the **Stock GPU Template** model: all processes (Gateway, LocalAI, Sherpa STT, TTS) run natively inside the pod container bound strictly to `127.0.0.1`, with exactly **ONE** HTTP port exposed externally (either Caddy reverse proxy on port 3000 or the Gateway directly on port 8000).

---

## 1. Objectives

- Verify control panel login and forced password change over HTTPS via RunPod's public proxy (`https://<pod-id>-<port>.proxy.runpod.net`).
- Verify HttpOnly, SameSite=Strict, Secure cookie handling behind a TLS-terminating proxy.
- Verify WebSocket connectivity (`/v1/voice/stream` and `/v1/audio/transcriptions/stream`) and microphone permissions through the proxy.
- Verify scoped API key enforcement and admin route isolation over the internet.
- Verify that engines (`8080`, `8088`, `8880`) are strictly internal (loopback `127.0.0.1`) and inaccessible from the outside.

---

## 2. Pod Provisioning & Configuration

1. **Pod Template:** Select RunPod stock template: **RunPod PyTorch 2.x** or **Ubuntu 22.04 with CUDA 12.x**.
2. **GPU Selection:** 1x NVIDIA RTX 3090, RTX 4090, or A4000 (16GB+ VRAM recommended).
3. **Port Exposure:** Expose **EXACTLY ONE HTTP Port** (e.g. `3000` for Caddy or `8000` for direct Gateway).
   - Never expose internal engine ports (`8080`, `8088`, `8880`).
4. **Persistent Volume:** Ensure `/workspace` is mounted as persistent network volume.

---

## 3. Step-by-Step Installation & Launch on Stock Pod

Inside the RunPod Web Terminal or SSH session:

```bash
# 1. Navigate to persistent volume
cd /workspace

# 2. Clone repository (or copy release bundle)
git clone <YOUR_REPO_URL> voice-agent
cd voice-agent

# 3. Create persistent directories
mkdir -p /workspace/models /workspace/data /workspace/logs /workspace/.pids

# 4. Configure environment file
cp .env.example .env
chmod 600 .env

# Edit .env with strong secrets (DO NOT use defaults):
# - ENVIRONMENT=staging
# - ADMIN_INITIAL_PASSWORD=<strong-random-password-12+-chars>
# - ADMIN_API_KEY=<strong-random-key>
# - API_KEY_HASH_SECRET=<strong-random-secret>
# - GATEWAY_HOST=0.0.0.0
# - COOKIE_SECURE=true
# - PUBLIC_BASE_URL=https://<pod-id>-<port>.proxy.runpod.net
# - LOCALAI_MODELS_PATH=/workspace/models

# 5. Run Preflight Inspection
./scripts/preflight.sh

# 6. Run Automated Setup (Detects Linux + CUDA, downloads models & engines)
./scripts/setup.sh

# 7. Start All Services
./scripts/start-all.sh

# 8. Verify loopback isolation (Engines on 127.0.0.1 only)
ss -tulpn
```

---

## 4. Test Execution Checklist

| Test Item | Procedure | Expected Result | Result / Status |
|---|---|---|---|
| **Preflight** | Run `./scripts/preflight.sh` | All checks pass with 0 blocking errors | `[Verify]` |
| **Port Isolation** | Run `ss -tulpn` on pod | Only exposed port (3000/8000) on `0.0.0.0`; 8080, 8088, 8880 on `127.0.0.1` | `[Verify]` |
| **Edge Access** | Open `https://<pod-id>-<port>.proxy.runpod.net/voicecontrolpanel/` | Basic auth prompt appears (Layer 1) | `[Verify]` |
| **Panel Login** | Submit initial seeded credentials | Redirected to `/v1/panel/change-password` screen (`must_change_password=1`) | `[Verify]` |
| **Password Change** | Submit old password and new 12+ character password | Success banner, redirected into control panel, new session cookie issued | `[Verify]` |
| **Cookie Inspection** | Inspect `admin_session` cookie in browser developer tools | Flags: `HttpOnly`, `SameSite=Strict`, `Secure=true` | `[Verify]` |
| **Session Persistence**| Refresh page and navigate across tabs | Remains logged in; no re-authentication required | `[Verify]` |
| **API Key Scopes** | Call `/v1/audio/transcriptions` with STT-scoped key | Succeeds (200 OK) | `[Verify]` |
| **Admin Route Isolation** | Call `/v1/admin/keys` with caller key | Rejected (403 Forbidden) | `[Verify]` |
| **Unauthenticated API** | Call `/v1/models` without key or session | Rejected (401 Unauthorized) | `[Verify]` |
| **Microphone / WebSockets** | Test voice session in Assistant tab | Microphone connects via HTTPS; WebSocket connects and streams audio | `[Verify]` |
| **Lockout Protection** | Fail login 5 times from external IP | Account locked for 15 minutes; returns generic 401 | `[Verify]` |
| **Log Scrubbing** | Inspect gateway and service logs in `logs/` | Zero passwords, cookie tokens, or full transcripts logged | `[Verify]` |

---

## 5. Teardown Protocol

Once internet testing is completed:
1. Run `./scripts/stop-all.sh` to cleanly stop background services without killing external processes.
2. Terminate and delete the test pod.
3. Destroy all test `.env` secrets. Never reuse test credentials in production.
4. Remove any temporary deploy keys from GitHub.

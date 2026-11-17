# RunPod Test Deployment Guide & Protocol

This guide describes how to run an internet verification test of the Voice AI Platform on a RunPod GPU cloud instance before deploying to Hetzner production.

---

## 1. Objectives

- Verify control panel login and forced password change over HTTPS via RunPod's public proxy (`https://<pod-id>-<port>.proxy.runpod.net`).
- Verify HttpOnly, SameSite=Strict, Secure cookie handling behind a TLS-terminating proxy.
- Verify WebSocket connectivity (`/v1/voice/stream` and `/v1/audio/transcriptions/stream`) and microphone permissions through the proxy.
- Verify scoped API key enforcement and admin route isolation over the internet.
- Verify that engines (`8080`, `8088`, `8880`) are strictly internal and inaccessible from the outside.

---

## 2. Pod Provisioning & Configuration

1. **Template:** Use an NVIDIA GPU Pod (e.g. RTX 3090, RTX 4090, or A4000) with CUDA 12.x and Ubuntu 22.04.
2. **Port Configuration:**
   - Expose **exactly ONE HTTP Port** (e.g. `3000` or `8000`).
   - Do not expose engine ports (`8080`, `8088`, `8880`).
3. **Storage:**
   - Keep `.env`, models, and database backups under the persistent `/workspace` mount point.
4. **Environment Settings:**
   - `ENVIRONMENT=staging`
   - `GATEWAY_HOST=0.0.0.0` (for direct pod testing) or `127.0.0.1` (behind pod Caddy)
   - `ADMIN_INITIAL_PASSWORD=<strong-random-password-12+-chars>` (NEVER the dev password)
   - `ADMIN_API_KEY=<strong-random-key>`
   - `API_KEY_HASH_SECRET=<strong-random-secret>`
   - `COOKIE_SECURE=true`
   - `TRUSTED_PROXY_IPS=` (Leave empty until RunPod proxy IP pool is identified)

---

## 3. Test Execution Checklist

| Test Item | Procedure | Expected Result | Result / Status |
|---|---|---|---|
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
| **Engine Isolation** | Run `ss -tulpn` on pod | Only exposed gateway/Caddy port listens on `0.0.0.0`; all engines on `127.0.0.1` | `[Verify]` |
| **Log Scrubbing** | Inspect gateway and Caddy logs | Zero passwords, cookie tokens, or full transcripts logged | `[Verify]` |

---

## 4. Teardown Protocol

Once internet testing is completed:
1. Terminate and delete the test pod.
2. Destroy all test `.env` secrets. Never reuse test credentials on production.
3. Remove any temporary deploy keys from GitHub.

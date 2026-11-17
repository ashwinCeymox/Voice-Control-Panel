# Control Panel Reverse Proxy and Authentication Architecture

This document describes how the Vite/React control panel (`/voicecontrolpanel/`) and backend API (`/v1/*`) are routed, protected, and secured across reverse proxies.

---

## 1. Multi-Layer Security Architecture

### Layer 1: Reverse Proxy Basic Authentication (Edge)
- **Status:** Recommended & retained for initial deployment.
- **Purpose:** Prevents automated bot scanners, brute-force engines, and unauthorized parties on the internet from probing the login endpoints or fingerprinting panel assets.
- **Decision:** Kept active on `/voicecontrolpanel/*` for both Hetzner and RunPod deployments. When the owner agrees in the future, it can be disabled by removing the `basic_auth` block in Caddyfile without affecting application functionality.

### Layer 2: Super Admin Session Authentication (Application)
- **Status:** Mandatory.
- **Credential Storage:** Argon2id (`argon2-cffi`) hashed password stored in SQLite `admin_user` table.
- **Session Cookie:**
  - Name: `admin_session`
  - Flags: `HttpOnly`, `SameSite=Strict`, `Secure=true` (in production and staging).
  - Lifetime: 8 hours idle timeout, 7 days absolute timeout.
- **Protection:**
  - Account lockout after 5 consecutive failed logins (15-minute lock).
  - Client IP throttling after 5 failed logins within 15 minutes.
  - Constant-time dummy verification on invalid emails to prevent timing enumeration.
  - CSRF defense: `SameSite=Strict` plus mandatory `X-Requested-With` header on all POST requests under `/v1/panel`.

---

## 2. Path Routing Matrix

| Public Path | Upstream Target | Auth Layer |
|---|---|---|
| `/voicecontrolpanel/*` | Static SPA files (`frontend/dist/index.html`) | Caddy `basic_auth` (Layer 1) |
| `/v1/panel/login` | FastAPI Gateway `POST /v1/panel/login` | Public (protected by rate limits & CSRF) |
| `/v1/panel/me` | FastAPI Gateway `GET /v1/panel/me` | Layer 2 session cookie |
| `/v1/panel/change-password` | FastAPI Gateway `POST /v1/panel/change-password` | Layer 2 session cookie |
| `/v1/panel/logout` | FastAPI Gateway `POST /v1/panel/logout` | Layer 2 session cookie |
| `/v1/admin/*` | FastAPI Gateway `127.0.0.1:8000` | Session cookie OR Admin API Key |
| `/v1/chat/*`, `/v1/audio/*`, `/v1/text-to-speech/*` | FastAPI Gateway `127.0.0.1:8000` | Scoped API Key OR Session cookie |
| `/v1/voice/stream` | FastAPI Gateway WebSocket `ws://127.0.0.1:8000/v1/voice/stream` | Ticket, API Key, or Session cookie |
| `/v1/audio/transcriptions/stream` | FastAPI Gateway WebSocket `ws://127.0.0.1:8000/v1/audio/transcriptions/stream` | Ticket, API Key, or Session cookie |
| `/metrics`, `/docs`, `/redoc` | Blocked at proxy (404) | Hard 404 in production |

---

## 3. Proxy Configuration Guidelines

1. **Proxy Headers:**
   Always ensure Caddy or Nginx overwrites `X-Forwarded-For` with `{remote_host}` rather than appending client-provided values to prevent header spoofing.
2. **WebSocket Upgrades:**
   Caddy handles `Connection: Upgrade` automatically for `/v1/voice/stream` and `/v1/audio/transcriptions/stream`.
3. **Cookie Configuration:**
   Because TLS terminates at Caddy/RunPod and plain HTTP is forwarded to FastAPI, cookie `Secure` flag is driven by `ENVIRONMENT` (`production`/`staging` -> `Secure=True`) or `COOKIE_SECURE=true`, never the internal request URL scheme.

# 8. Security (5 minutes)

> **TL;DR:** Hardened defense-in-depth across 5 layers. Strict gateway authentication with HMAC-SHA256 hashed keys. Internal engines locked down with loopback + `LOCALAI_API_KEY` + nftables UID isolation. Production failsafe refuses to start with default credentials.

---

## 8.1 The Five Security Rules

1. **Only Caddy Faces the Internet:** All other ports (8000, 8080, 8880, 8088) bind strictly to `127.0.0.1`.
2. **Every Caller Call Requires a Key and Scope:** Every request to `/v1/*` must pass `verify_api_key` with constant-time HMAC check.
3. **Admin Endpoints Require Role `admin`:** Sensitive operations (model deletion, key creation, residency flushing) require full admin authorization.
4. **Defense-in-Depth for Internal Engines:** Even on loopback, LocalAI requires `LOCALAI_API_KEY` Bearer auth, and nftables blocks any non-gateway system user from contacting engine ports.
5. **No Dangerous Endpoints Reach the Internet:** Model downloading/deletion routes on LocalAI are unreachable from external proxies.

---

## 8.2 Resolved Security Findings (Audit Status)

| ID | Issue Description | Original Severity | Fix Status | Verification Method |
|---|---|---|---|---|
| **SEC-01** | CORS allowed any origin | High | **CLOSED** | Restricted to explicit origins in `CORS_ALLOWED_ORIGINS` |
| **SEC-02** | Interactive OpenAPI `/docs` public | Medium | **CLOSED** | Disabled by default in production (`ENABLE_DOCS=false`) |
| **SEC-03** | Default admin key accepted | High | **CLOSED** | Lifespan refuses to boot in `production` if default key detected |
| **SEC-04** | Permissions on `.env` and DB too permissive | Medium | **CLOSED** | Deployment sets `chmod 600` on `.env` and `voice_platform.db` |
| **F1** | `/v1/voice/turn` lacked scope check | Medium | **CLOSED** | Enforces `require_scope("voice")`; non-voice keys get HTTP 403 |
| **F2** | `/metrics` Prometheus scrape exposed | Medium | **CLOSED** | Caddyfile blocks external requests to `/metrics` with 403 |
| **F3** | Plaintext API keys leaked in query params | Medium | **CLOSED** | Caddy scrubs `api_key` from logs; WebSockets use single-use tickets |
| **F4** | Co-located Linux users could access engines | Medium | **CLOSED** | `deploy/nftables/voice.nft` drops traffic from non-`voiceagent` UIDs |
| **F5** | Tickets and limits in memory across multiple workers | Medium | **CLOSED** | Architected as single-worker process model under systemd |
| **T6** | Callers could delete models | High | **CLOSED** | `DELETE /v1/models/{id}` restricted to `require_admin_key` |

---

## 8.3 Production Startup Safeguard

In [`app/main.py`](../../app/main.py#L79-L90):
```python
if settings.ENVIRONMENT.lower() in ("production", "prod"):
    if settings.ADMIN_API_KEY == "dev-voice-key-12345" or not settings.ADMIN_API_KEY:
        raise RuntimeError("CRITICAL SECURITY ERROR: Running in production mode with default ADMIN_API_KEY!")
    if "dev-secret-key" in settings.API_KEY_HASH_SECRET:
        raise RuntimeError("CRITICAL SECURITY ERROR: Running in production mode with default API_KEY_HASH_SECRET!")
```
The gateway will intentionally refuse to start if default secrets are present in `.env`.

---

## 8.4 nftables Engine Isolation (`deploy/nftables/voice.nft`)

On bare metal, other services and users (e.g. `www-data`, system daemons) could theoretically connect to `127.0.0.1:8080`. The nftables rules prevent this:

```nft
table inet voice_filter {
    chain loopback_guard {
        type filter hook output priority 0; policy accept;
        
        # Only allow system user 'voiceagent' (UID) to connect to internal engine ports
        tcp dport { 8080, 8880, 8088 } meta skuid != "voiceagent" drop;
    }
}
```

---

## 8.5 Essential Security Habits

- **Secret Generation:** Always generate high-entropy 256-bit secrets using `openssl rand -hex 32`.
- **Preserve the Hash Secret:** Never lose `API_KEY_HASH_SECRET`. Because keys are hashed with HMAC, losing the secret invalidates all issued caller keys.
- **Fail2ban Protection:** Enabled via [`deploy/fail2ban/caddy-auth.local`](../../deploy/fail2ban/caddy-auth.local) to automatically ban IPs with $\ge 5$ failed HTTP basic auth attempts.

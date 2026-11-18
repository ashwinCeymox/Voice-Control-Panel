# 9. Security

## 9.1 Multi-Layer Defense-in-Depth Architecture

The Voice AI Platform implements a 5-layer defense-in-depth security model designed to isolate internal inference engines, protect API keys and user data, and eliminate common attack vectors.

```
[ Public Internet ]
        |
        v  HTTPS (443) / HTTP (80)
+-------------------------------------------------------------+
| Layer 1: Edge TLS & Reverse Proxy (Caddy)                   |
| - Public ports 80 & 443; terminates TLS via Let's Encrypt   |
| - Slowloris protection (10s header timeout, 2m idle)        |
| - Strict HTTP response security headers (HSTS, CSP, no-sniff)|
| - WebSocket Origin check (cross-site hijacking mitigation)  |
| - Hard block on /metrics, /docs, /redoc, /openapi.json      |
| - API key scrubbing from access logs                        |
+-------------------------------------------------------------+
        |  http://127.0.0.1:8000
        v
+-------------------------------------------------------------+
| Layer 2: API Gateway (FastAPI)                              |
| - Binds exclusively to 127.0.0.1:8000                       |
| - Authentication: HMAC-SHA256 with constant-time comparison |
| - Authorization: RBAC ('admin' vs 'caller') & granular scopes|
| - Input validation: Pydantic v2 schemas + strict regex filters|
| - Single-use tickets (60s lifetime) for WebSocket auth       |
| - CORS restricted to explicitly configured origins          |
+-------------------------------------------------------------+
        |  Internal Loopback
        v
+-------------------------------------------------------------+
| Layer 3: Host Firewall & Packet Filtering (UFW + nftables)  |
| - UFW: Default deny incoming; allows only TCP 22, 80, 443   |
| - nftables: Loopback UID isolation (skuid check)             |
|   * Ports 8080, 8880, 8088 accessible ONLY by 'voiceagent'   |
|   * Port 8000 accessible ONLY by 'caddy' and 'voiceagent'   |
+-------------------------------------------------------------+
        |
        v
+-------------------------------------------------------------+
| Layer 4: Process & Filesystem Isolation (systemd)           |
| - Dedicated unprivileged system user 'voiceagent'            |
| - Systemd sandboxing: ProtectSystem=strict, PrivateTmp=true |
| - File permissions: 600 for .env and database, 700 for root |
+-------------------------------------------------------------+
        |
        v
+-------------------------------------------------------------+
| Layer 5: Backend Inference Engines                          |
| - LocalAI (8080), local-tts (8880), Sherpa-ONNX STT (8088)  |
| - Bound strictly to 127.0.0.1 (never exposed externally)   |
| - LocalAI authenticated with internal LOCALAI_API_KEY       |
| - Dangerous management routes shielded by Gateway layer     |
+-------------------------------------------------------------+
```

---

## 9.2 Closed Audit Findings & Verification Status

All 10 security findings identified during earlier security reviews have been fully addressed and verified in the production codebase **[Confirmed]**:

| Finding ID | Title | Severity | Resolution & Implementation Details | Status |
|---|---|---|---|---|
| **SEC-01** | Inconsistent Key Hashing Algorithm | **High** | Codebase standardizes on **HMAC-SHA256** using `settings.API_KEY_HASH_SECRET` and `hmac.compare_digest`. PBKDF2 claims were documentation artifacts. | **Closed** |
| **SEC-02** | Path Traversal in Model Identifiers | **High** | Strict regex whitelist `^[a-zA-Z0-9][a-zA-Z0-9_\-\.:]*$` enforced across all model management routes. Path traversal characters (`..`, `/`, `\`) rejected with HTTP 400. | **Closed** |
| **SEC-03** | Default Fallback Admin Key | **High** | Hard failsafe implemented in `app/main.py`. Server raises fatal `RuntimeError` on startup if `ENVIRONMENT=production` and `ADMIN_API_KEY` is empty, unset, or set to `dev-voice-key-12345`. | **Closed** |
| **SEC-04** | Overly Permissive File Permissions | **Medium** | Automated deployment script (`scripts/deploy-hetzner.sh`) applies strict permissions: `chmod 700 /opt/voice-agent`, `chmod 600 .env`, and `chmod 600 data/voice_platform.db`. | **Closed** |
| **F1** | Missing Scope Check on `/v1/voice/turn` | **Medium** | Route signature in [`app/api/voice.py`](../../app/api/voice.py#L136) enforces `Depends(require_scope("voice"))`. Caller keys without the `voice` scope receive HTTP 403 Forbidden. | **Closed** |
| **F2** | Prometheus Metrics Exposure | **Medium** | Caddy reverse proxy hard-blocks `/metrics` and `/metrics/*` with HTTP 404. Endpoint is only accessible via local SSH tunnel or internal scraping. | **Closed** |
| **F3** | API Key Leakage in Query Parameters | **Medium** | Single-use tickets (`POST /v1/auth/ticket`) eliminate API keys from WebSocket URLs. Caddy access logging filters out `api_key` query parameters using `delete api_key`. | **Closed** |
| **F4** | Multi-User Localhost Engine Hijacking | **Medium** | Dual defense: (1) `LOCALAI_API_KEY` authentication for LocalAI, and (2) `nftables` kernel packet filtering rule dropping any loopback traffic to engine ports from non-`voiceagent` UIDs. | **Closed** |
| **F5** | Multi-Worker In-Memory State Drift | **Medium** | Systemd unit explicitly configures single-worker mode (`--workers 1`) to preserve in-memory ticket cache and quota counters. Multi-worker scaling roadmap transitions state to Redis. | **Closed** |
| **T6** | Privilege Escalation on Model Deletion | **High** | Model uninstall (`DELETE /v1/models/{id}`) and classification override (`PATCH /v1/models/{id}/classification`) enforce `Depends(require_admin_key)`. Non-admin caller keys receive HTTP 403. | **Closed** |

---

## 9.3 Startup Security Failsafe

To guarantee that a developer never accidentally deploys an insecure system to production, the gateway application implements a mandatory startup integrity check in [`app/main.py`](../../app/main.py#L79-L89):

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    if settings.ENVIRONMENT.lower() in ("production", "prod"):
        if settings.ADMIN_API_KEY == "dev-voice-key-12345" or not settings.ADMIN_API_KEY:
            raise RuntimeError(
                "CRITICAL SECURITY CONFIGURATION ERROR: Running in production mode with default or empty ADMIN_API_KEY! "
                "Generate a 256-bit random key with 'openssl rand -hex 32' and set ADMIN_API_KEY in .env before starting."
            )
        if "dev-secret-key" in settings.API_KEY_HASH_SECRET:
            raise RuntimeError(
                "CRITICAL SECURITY CONFIGURATION ERROR: Running in production mode with default API_KEY_HASH_SECRET! "
                "Generate a high-entropy secret with 'openssl rand -hex 32' and set API_KEY_HASH_SECRET in .env."
            )
    ...
```

If these assertions fail, the gateway process terminates immediately with an exit code of 1, preventing the service from binding or processing any incoming requests.

---

## 9.4 Kernel Packet Filtering Rules (nftables)

To prevent other local users or compromised processes on the host from bypassing the gateway to invoke inference engines directly, [`deploy/nftables/voice.nft`](../../deploy/nftables/voice.nft) establishes kernel-level socket UID verification:

```nftables
table inet voice_local {
  chain output {
    type filter hook output priority 0; policy accept;

    # Engines: only the voiceagent user may connect to internal loopback ports
    oifname "lo" tcp dport { 8080, 8880, 8088 } meta skuid != "voiceagent" drop

    # Gateway: only caddy reverse proxy + voiceagent internal calls
    oifname "lo" tcp dport 8000 meta skuid != { "caddy", "voiceagent" } drop
  }
}
```

- Any process not running under UID `voiceagent` attempting to connect to `127.0.0.1:8080`, `:8880`, or `:8088` is silently dropped at the kernel output hook.
- Gateway port `8000` only accepts connections originating from UID `caddy` or UID `voiceagent`.

---

## 9.5 Edge Proxy Hardening (Caddyfile)

[`deploy/caddy/Caddyfile`](../../deploy/caddy/Caddyfile) enforces strict edge security controls:

1. **Security Headers:**
   ```caddy
   header {
       Strict-Transport-Security "max-age=31536000"
       X-Content-Type-Options "nosniff"
       X-Frame-Options "DENY"
       Referrer-Policy "no-referrer"
       Permissions-Policy "microphone=(self), camera=(), geolocation=(), payment=()"
       Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self' wss://xyz.sdjfj.app; worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
       -Server
   }
   ```
2. **WebSocket Cross-Site Hijacking Protection:**
   Rejects WebSocket upgrade requests where the `Origin` header does not match the platform's trusted domain:
   ```caddy
   @badorigin {
       path /v1/voice/stream /v1/audio/transcriptions/stream
       header Origin *
       not header Origin https://xyz.sdjfj.app
   }
   handle @badorigin {
       respond 403
   }
   ```
3. **Internal Path Masking:**
   Requests for metrics, swagger, OpenAPI specs, and redoc are masked with HTTP 404:
   ```caddy
   @blocked path /metrics /metrics/* /docs /docs/* /redoc /redoc/* /openapi.json
   handle @blocked {
       respond 404
   }
   ```
4. **Log Filtering & Redaction:**
   Sensitive credentials and auth query parameters are scrubbed before log serialization:
   ```caddy
   log {
       output file /var/log/caddy/access.log
       format filter {
           wrap json
           request>headers>Authorization delete
           request>headers>Xi-Api-Key delete
           request>headers>Cookie delete
           request>uri query {
               delete api_key
           }
       }
   }
   ```

---

## 9.6 Operational Security Rules & Secret Hygiene

- **Random 256-Bit Secret Generation:** Always generate production secrets using cryptographically secure random bytes:
  ```bash
  openssl rand -hex 32
  ```
- **Backup Key Hash Secret:** The `API_KEY_HASH_SECRET` must be backed up securely in an external vault (e.g. 1Password, Bitwarden). **Losing this secret invalidates all active API keys**, requiring mass key regeneration.
- **Credential Storage:** API keys must never be stored in plaintext. Keys are displayed to administrators exactly once upon creation, after which only the HMAC-SHA256 hash is persisted in `voice_platform.db`.
- **Principle of Least Privilege:** Callers must be granted minimal required scopes (e.g., `["stt"]` for an audio transcriber script, not `["*"]` or `admin`).

# Publishing the Control Panel: `localhost:5173` → `https://xyz.sdjfj.app/voicecontrolpanel`

Design: **one domain, one origin.**

```
https://xyz.sdjfj.app/voicecontrolpanel/*  → static React build (served by Caddy)
https://xyz.sdjfj.app/v1/*                 → gateway 127.0.0.1:8000 (REST + WebSockets)
https://xyz.sdjfj.app/health/*             → gateway
everything else (/, /metrics, /docs, ...)  → 404
```

Because the panel and API share an origin, **no CORS is needed**. Leave `CORS_ALLOWED_ORIGINS` at its default and don't add wildcards.

---

## 1. Frontend changes (serve under a sub-path)

**`vite.config.ts`**
```ts
export default defineConfig({
  base: '/voicecontrolpanel/',          // all asset URLs get this prefix
  server: {                              // dev still works at localhost:5173
    proxy: { '/v1': 'http://127.0.0.1:8000', '/health': 'http://127.0.0.1:8000' },
  },
  build: { sourcemap: false },           // don't ship source maps to the internet
})
```

**Router:** set the base path (React Router example)
```tsx
<BrowserRouter basename={import.meta.env.BASE_URL}> ... </BrowserRouter>
```

**API and WebSocket URLs: relative, never hard-coded `localhost`**
```ts
const API = '';                                         // same origin: fetch('/v1/...')
const WS = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}`;
new WebSocket(`${WS}/v1/voice/stream`);                 // use tickets from POST /v1/auth/ticket
```

**Admin key handling in the panel**
- Keep it in **`sessionStorage` or memory**, never `localStorage` (it would survive tab close and be readable by any XSS).
- Don't put keys in URLs or query strings. Use the `Authorization: Bearer` header or `/v1/auth/ticket` for WebSockets.

Build and ship:
```bash
cd frontend && npm ci && npm run build
rsync -a dist/ user@SERVER:/tmp/fe/ && ssh user@SERVER 'sudo rsync -a --delete /tmp/fe/ /opt/voice-agent/frontend/'
```

---

## 2. Caddyfile (`/etc/caddy/Caddyfile`)

Generate the panel password hash first:
```bash
caddy hash-password          # paste the output into basic_auth below
```

```caddy
{
    # Slowloris protection
    servers {
        timeouts {
            read_header 10s
            idle 2m
        }
    }
}

# Any other hostname / raw IP hitting :80 gets nothing
:80 {
    respond 404
}

xyz.sdjfj.app {
    encode zstd gzip

    # ---------- Security headers (all responses) ----------
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "no-referrer"
        # the voice app needs the microphone; nothing else
        Permissions-Policy "microphone=(self), camera=(), geolocation=(), payment=()"
        Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self' wss://xyz.sdjfj.app; worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        -Server
    }

    # ---------- Logging (keys in ?api_key= are scrubbed) ----------
    log {
        output file /var/log/caddy/xyz.access.log {
            roll_size 50MiB
            roll_keep 10
        }
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

    # ---------- Hard blocks ----------
    @blocked path /metrics /metrics/* /docs /docs/* /redoc /redoc/* /openapi.json
    handle @blocked {
        respond 404
    }

    # WebSockets: browsers must come from OUR origin (stops cross-site WS hijacking).
    # Non-browser clients (coworkers' scripts) send no Origin header and pass.
    @badorigin {
        path /v1/voice/stream /v1/audio/transcriptions/stream
        header Origin *
        not header Origin https://xyz.sdjfj.app
    }
    handle @badorigin {
        respond 403
    }

    # ---------- Admin API: restrict by source IP (recommended) ----------
    # Uncomment and set your static IP / VPN exit IP. Keep Cloudflare "proxied" OFF
    # (DNS-only), or remote_ip will be Cloudflare's, not yours.
    #
    # @admin_untrusted {
    #     path /v1/admin/* /v1/agents /v1/agents/* /v1/models/install /v1/models/install/* /v1/voices/*/meta
    #     not remote_ip YOUR.STATIC.IP.HERE 2001:db8::/48
    # }
    # handle @admin_untrusted {
    #     respond 403
    # }

    # ---------- API (REST + WebSocket) ----------
    handle /v1/* {
        request_body {
            max_size 25MB            # app limit is 20 MB audio
        }
        reverse_proxy 127.0.0.1:8000 {
            header_up X-Forwarded-For {remote_host}   # overwrite, never append client-supplied values
            header_up X-Real-IP {remote_host}
            header_up -X-Forwarded-Host
            transport http {
                dial_timeout 5s
                response_header_timeout 300s
            }
        }
    }

    handle /health/* {
        reverse_proxy 127.0.0.1:8000
    }

    # ---------- Control panel ----------
    redir /voicecontrolpanel /voicecontrolpanel/ 308

    handle_path /voicecontrolpanel/* {
        basic_auth {
            # username  bcrypt-hash-from-`caddy hash-password`
            admin $2a$14$REPLACE_WITH_YOUR_HASH
        }
        root * /opt/voice-agent/frontend

        @assets path /assets/*
        header @assets Cache-Control "public, max-age=31536000, immutable"
        header /index.html Cache-Control "no-store"

        try_files {path} /index.html
        file_server
    }

    # ---------- Everything else ----------
    handle {
        respond 404
    }
}
```

```bash
sudo mkdir -p /var/log/caddy && sudo chown caddy:caddy /var/log/caddy
sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

DNS: add `A` and `AAAA` records for `xyz.sdjfj.app` pointing to the server. `.app` domains are HSTS-preloaded, so browsers only use HTTPS anyway.

---

## 3. What each protection stops

| Threat | Control |
|---|---|
| Cleartext keys / MITM | Automatic TLS + HSTS |
| Engine / metrics / docs exposure | Only `/v1`, `/health`, panel are routed; `/metrics`, `/docs` hard-blocked; engines stay on loopback |
| Anyone loading the admin UI | `basic_auth` on `/voicecontrolpanel` (second factor before the app's own admin key) |
| Admin API brute-force or abuse | Optional IP allowlist on admin paths |
| Cross-site WebSocket hijacking | Origin check at the proxy |
| XSS stealing the admin key | Strict CSP (no inline/remote scripts), `sessionStorage`, no source maps |
| Clickjacking | `X-Frame-Options: DENY`, `frame-ancestors 'none'` |
| Keys leaking into logs | Header and query scrubbing in the Caddy log filter |
| IP spoofing | `X-Forwarded-For` overwritten with the real client address |
| Slowloris / giant uploads | `read_header` timeout, 25 MB body cap |
| Scanners hitting the raw IP | Default site returns 404 and no cert is served for unknown hosts |

---

## 4. Brute-force banning (fail2ban)

`/etc/fail2ban/filter.d/caddy-auth.conf`
```ini
[Definition]
failregex = ^.*"remote_ip":"<HOST>".*"status":(401|403).*$
ignoreregex =
```
`/etc/fail2ban/jail.d/caddy-auth.local`
```ini
[caddy-auth]
enabled  = true
port     = http,https
filter   = caddy-auth
logpath  = /var/log/caddy/xyz.access.log
maxretry = 10
findtime = 5m
bantime  = 1h
```
```bash
sudo systemctl restart fail2ban && sudo fail2ban-client status caddy-auth
```
Add your own IP to `ignoreip` so a typo doesn't lock you out of the panel.

---

## 5. Test it

```bash
# panel: 401 without password, 200 with it
curl -sI https://xyz.sdjfj.app/voicecontrolpanel/            | head -1   # 401
curl -sI -u admin:PASSWORD https://xyz.sdjfj.app/voicecontrolpanel/ | head -1   # 200

# blocked / unrouted paths: all 404
for p in / /metrics /docs /openapi.json /8080 /models/apply; do
  echo -n "$p "; curl -s -o /dev/null -w "%{http_code}\n" https://xyz.sdjfj.app$p; done

# API still needs a key
curl -s -o /dev/null -w "%{http_code}\n" https://xyz.sdjfj.app/v1/models                  # 401
curl -s -o /dev/null -w "%{http_code}\n" https://xyz.sdjfj.app/health/live                # 200

# WebSocket from a foreign origin is rejected
curl -s -o /dev/null -w "%{http_code}\n" -H "Origin: https://evil.example" \
  -H "Connection: Upgrade" -H "Upgrade: websocket" https://xyz.sdjfj.app/v1/voice/stream   # 403

# security headers present
curl -sI https://xyz.sdjfj.app/voicecontrolpanel/ -u admin:PASSWORD | grep -iE "strict|content-security|x-frame|permissions"

# raw IP: no content
curl -sk -m 5 -o /dev/null -w "%{http_code}\n" https://SERVER_IP/
```

Then open the panel in a browser, check the console for **CSP violations** (the audio worklet or a chart library may need an adjustment, such as `worker-src` or `style-src`), and test a mic capture and a voice session end-to-end.

---

## 6. Still required on the gateway and host (the proxy can't fix these)

- Production `ADMIN_API_KEY` / `API_KEY_HASH_SECRET` (`openssl rand -hex 32`); no dev key
- `.env` and `voice_platform.db` at `600`; services run as `voiceagent`
- Engines bound to `127.0.0.1`; `LOCALAI_API_KEY` set; UFW allows only 22/80/443
- `uvicorn --workers 1`, no `--reload`
- `/v1/voice/turn` currently has no scope check, so add one (`voice`) so scoped keys can't run the full pipeline
- Keep gateway `ENABLE_DOCS=false`

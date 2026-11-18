# Voice AI Platform: Hetzner Deployment & Scaling Guide

Target: Hetzner dedicated server, Ubuntu 24.04 / Debian 12, NVIDIA GPU, no Docker.
Services: LocalAI (8080), local-tts (8880), streaming-stt (8088), FastAPI gateway (8000), static frontend, Caddy (80/443).

> **Assumptions to confirm:** the launch commands for local-tts and streaming-stt below are placeholders.
> Copy the real ones from `scripts/start-native-engines.sh`.

---

## 0. Scaling reality check

The app is currently **single-node by design**. Before scaling out, know what blocks it:

| Blocker | Why | Fix |
|---|---|---|
| SQLite (`voice_platform.db`) | One file, one host | Stage 3: move to PostgreSQL |
| In-memory WS tickets | Ticket minted in worker A is invalid in worker B | Stage 3: Redis |
| In-memory stream/turn limiters | Limits multiply per worker | Stage 3: Redis |
| `MAX_ACTIVE_VOICE_TURNS=1` | Likely global, so one user blocks others | Raise it, make it per key |
| GPU-bound engines | VRAM is the real capacity limit | Add GPU nodes (Stage 3) |

| Stage | Setup | Needs code change? |
|---|---|---|
| **1. Single box** (do this first) | Everything on one server, `--workers 1` | No |
| **2. Vertical** | Bigger GPU/RAM, raise limits, tune quantization | Config only |
| **3. Horizontal** | Gateway nodes + GPU nodes + Postgres + Redis over private network | Yes (tickets, limiters, DB layer) |

---

## 1. Server prep

```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install ufw nftables caddy sqlite3 python3-venv unattended-upgrades fail2ban
nvidia-smi && nvcc --version    # confirm driver + CUDA

# Dedicated user (no login)
sudo useradd --system --shell /usr/sbin/nologin --home /opt/voice-agent --create-home voiceagent
```

SSH: key-only login. In `/etc/ssh/sshd_config.d/99-hardening.conf`:
```
PasswordAuthentication no
PermitRootLogin prohibit-password
```
Then `sudo systemctl reload ssh`.

---

## 2. Deploy the code

```bash
# from your laptop (excludes dev junk and secrets)
rsync -a --exclude .env --exclude .venv --exclude node_modules --exclude '*.db' \
  ./ user@SERVER:/tmp/voice-agent/
# on the server
sudo rsync -a /tmp/voice-agent/ /opt/voice-agent/
sudo -u voiceagent python3 -m venv /opt/voice-agent/.venv
sudo -u voiceagent /opt/voice-agent/.venv/bin/pip install -r /opt/voice-agent/requirements.txt
sudo mkdir -p /opt/voice-agent/{data,logs,models,frontend}
```

**Frontend: build, don't run Vite.** On your laptop:
```bash
cd frontend && npm ci && npm run build
rsync -a dist/ user@SERVER:/tmp/fe/ && sudo rsync -a /tmp/fe/ /opt/voice-agent/frontend/
```

**Models:** put LocalAI weights in `/opt/voice-agent/models`, STT models in `/opt/voice-agent/models/streaming-stt`.

---

## 3. Secrets

```bash
sudo -u voiceagent tee /opt/voice-agent/.env >/dev/null <<EOF
ADMIN_API_KEY=$(openssl rand -hex 32)
API_KEY_HASH_SECRET=$(openssl rand -hex 32)
LOCALAI_API_KEY=$(openssl rand -hex 32)
ENABLE_DOCS=false
LOG_TRANSCRIPTS=false
CORS_ALLOWED_ORIGINS=["https://api.yourdomain.com"]
EOF
sudo chown -R voiceagent:voiceagent /opt/voice-agent
sudo chmod 700 /opt/voice-agent
sudo chmod 600 /opt/voice-agent/.env
```

- **Never** keep `dev-voice-key-12345`. Ideally make the gateway refuse to start if the admin key is unset or equals it.
- `LOCALAI_API_KEY` is **required** here. Other users on the host can reach loopback ports, so don't rely on 127.0.0.1 alone. Make sure the gateway sends it to LocalAI.
- Back up `API_KEY_HASH_SECRET` somewhere safe. Losing it invalidates every issued key.
- Set the DB path to `/opt/voice-agent/data/voice_platform.db` and `chmod 600` it after first start.

---

## 4. systemd units

Create these in `/etc/systemd/system/`. All engines bind to loopback only.

**voice-localai.service**
```ini
[Unit]
Description=LocalAI
After=network.target

[Service]
User=voiceagent
Group=voiceagent
WorkingDirectory=/opt/voice-agent
EnvironmentFile=/opt/voice-agent/.env
Environment=CUDA_VISIBLE_DEVICES=0
ExecStart=/opt/voice-agent/bin/local-ai run --address 127.0.0.1:8080 --models-path /opt/voice-agent/models
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/opt/voice-agent/models /opt/voice-agent/logs

[Install]
WantedBy=multi-user.target
```

**voice-tts.service** (placeholder command, copy the real one from your start script)
```ini
[Unit]
Description=Local TTS (Kokoro)
After=network.target

[Service]
User=voiceagent
Group=voiceagent
WorkingDirectory=/opt/voice-agent
Environment=CUDA_VISIBLE_DEVICES=0
ExecStart=/opt/voice-agent/.venv/bin/python -m local_tts --host 127.0.0.1 --port 8880
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/opt/voice-agent/logs

[Install]
WantedBy=multi-user.target
```

**voice-stt.service**
```ini
[Unit]
Description=Streaming STT (Sherpa-ONNX)
After=network.target

[Service]
User=voiceagent
Group=voiceagent
WorkingDirectory=/opt/voice-agent
ExecStart=/opt/voice-agent/.venv/bin/python services/streaming_stt/server.py --host 127.0.0.1 --port 8088
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/opt/voice-agent/logs

[Install]
WantedBy=multi-user.target
```

**voice-gateway.service**
```ini
[Unit]
Description=Voice AI Gateway
After=network.target voice-localai.service voice-tts.service voice-stt.service
Wants=voice-localai.service voice-tts.service voice-stt.service

[Service]
User=voiceagent
Group=voiceagent
WorkingDirectory=/opt/voice-agent
EnvironmentFile=/opt/voice-agent/.env
# --workers 1 until tickets + limiters move to Redis. No --reload.
ExecStart=/opt/voice-agent/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=
# SQLite writes journal/WAL files next to the DB, so the DIRECTORY must be writable
ReadWritePaths=/opt/voice-agent/data /opt/voice-agent/logs /opt/voice-agent/models

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now voice-localai voice-tts voice-stt voice-gateway
```

---

## 5. Caddy (reverse proxy + TLS)

DNS: point an `A` (and `AAAA`) record for `api.yourdomain.com` at the server **before** starting Caddy.

`/etc/caddy/Caddyfile`
```caddy
# Reject raw-IP / unknown-host traffic
:80 {
    respond 404
}

api.yourdomain.com {
    encode zstd gzip

    # API + WebSockets (Caddy upgrades WS automatically)
    handle /v1/* {
        reverse_proxy 127.0.0.1:8000 {
            header_up X-Forwarded-For {remote_host}
            header_up X-Real-IP {remote_host}
        }
    }

    handle /health/* {
        reverse_proxy 127.0.0.1:8000
    }

    # Static frontend (built bundle)
    handle {
        root * /opt/voice-agent/frontend
        try_files {path} /index.html
        file_server
    }

    # Do NOT proxy /metrics, /docs, /redoc, /openapi.json
    @blocked path /metrics /docs* /redoc* /openapi.json
    respond @blocked 404

    # Don't write API keys passed in ?api_key= to logs
    log {
        output file /var/log/caddy/access.log
        format filter {
            wrap json
            request>uri query {
                delete api_key
            }
        }
    }
}
```
Put the `@blocked` matcher **above** the `handle` blocks if Caddy reports ordering problems, or wrap everything in `route { }`.
Also give coworkers header-based auth only (`Authorization: Bearer`). Query-string keys on WebSockets are a last resort.

```bash
sudo mkdir -p /var/log/caddy && sudo chown caddy:caddy /var/log/caddy
sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

---

## 6. Firewall

**Network (UFW):**
```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw limit 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
grep IPV6 /etc/default/ufw   # must be IPV6=yes
```

**Local isolation (blocks other users/services on the same host from hitting the engines):**

`/etc/nftables.d/voice.nft` (include it from `/etc/nftables.conf`)
```nft
table inet voice_local {
  chain output {
    type filter hook output priority 0; policy accept;
    # engines: only the voiceagent user may connect
    oifname "lo" tcp dport { 8080, 8880, 8088 } meta skuid != "voiceagent" drop
    # gateway: only caddy + voiceagent
    oifname "lo" tcp dport 8000 meta skuid != { "caddy", "voiceagent" } drop
  }
}
```
```bash
sudo nft -c -f /etc/nftables.d/voice.nft && sudo systemctl enable --now nftables
```

---

## 7. Backups

```bash
# nightly DB backup (uses scripts/backup-db.sh), kept 14 days, copied off-box
sudo tee /etc/cron.d/voice-backup >/dev/null <<'EOF'
0 3 * * * voiceagent /opt/voice-agent/scripts/backup-db.sh && find /opt/voice-agent/data/backups -mtime +14 -delete
EOF
```
Copy `data/backups/` to a Hetzner Storage Box (`rsync -e ssh`) or S3-compatible storage. **Test `scripts/restore-db.sh` once** on a scratch copy.

---

## 8. Verify (run before giving out keys)

```bash
# on the server: every line must show 127.0.0.1
sudo ss -tulpnH | grep -E "8000|8080|8880|8088"

# from your laptop: all must time out / be refused
for p in 8000 8080 8880 8088; do curl -m 3 http://SERVER_IP:$p/ ; done

# raw IP and blocked paths
curl -k -m 5 https://SERVER_IP/                       # 404 or TLS refusal
curl -m 5 https://api.yourdomain.com/metrics          # 404
curl -m 5 https://api.yourdomain.com/docs             # 404
curl -m 5 https://api.yourdomain.com/v1/models        # 401
curl -m 5 https://api.yourdomain.com/health/ready     # 200

# local-user isolation: as any OTHER user, these must fail
sudo -u www-data curl -m 3 http://127.0.0.1:8080/ ; sudo -u www-data curl -m 3 http://127.0.0.1:8880/

# file perms
ls -la /opt/voice-agent/.env /opt/voice-agent/data/voice_platform.db   # both -rw-------

# auth matrix + tests (point the probe at the production URL)
/opt/voice-agent/.venv/bin/python scripts/auth_matrix_probe.py
/opt/voice-agent/.venv/bin/pytest tests/
```

Then create a **caller** key with the narrowest scopes and confirm: wrong scope gives 403, admin routes give 403, revoked key gives 401.

---

## 9. Operations

```bash
journalctl -u voice-gateway -f          # logs
sudo systemctl restart voice-gateway    # restart
watch -n 1 nvidia-smi                   # VRAM
```
- Keep `unattended-upgrades` on. Hold NVIDIA driver upgrades and apply them manually.
- Set VRAM-safe quantization (`f16: true` / `q4_k_m`) in `models/*.yaml`. Use `CUDA_VISIBLE_DEVICES` if other services share the GPU.
- Scrape `/metrics` from **localhost or a private network only** (Prometheus on the same host).
- Deliver keys via a password manager, not chat. Rotate with `POST /v1/admin/keys/{id}/rotate`.

---

## 10. Scaling path (Stage 3)

Target layout on a Hetzner private network (vSwitch):

```
Internet -> Hetzner Load Balancer (TLS) -> gateway-1, gateway-2 (CPU nodes)
                                              |-> PostgreSQL (keys, turns, agents)
                                              |-> Redis (WS tickets, limiters, quotas)
                                              '-> GPU nodes: LocalAI + TTS + STT (private IPs only)
```

Work required, in order:
1. **Move tickets and stream/turn limiters to Redis** (this enables `--workers N` and multiple gateways).
2. **Move SQLite to PostgreSQL** (`api_keys`, `turns`, `agents`, usage). Keep SQLite for dev.
3. **Make `MAX_ACTIVE_VOICE_TURNS` and `MAX_ACTIVE_STT_STREAMS` per-key and configurable**, and enforce them in Redis.
4. **Make engine URLs configurable** (per-node list or an internal load balancer), since engines are now on private IPs, not loopback. Firewall them to the gateway nodes only, with `LOCALAI_API_KEY` still set. Never expose engines on the public interface.
5. **Pin WebSocket sessions** at the load balancer (sticky) or keep streams on one gateway.
6. **Scale by GPU, not CPU.** Capacity is VRAM and concurrent inference. Add GPU nodes first, gateway nodes second.
7. **Monitoring:** Prometheus + Grafana scraping `/metrics` over the private network, with alerts on VRAM, 5xx rate and latency.

Rule of thumb: stay on Stage 1 until `nvidia-smi` shows sustained VRAM pressure or p95 latency climbs, then go to Stage 2, and only do Stage 3 if one machine genuinely isn't enough.

---

## Go-live checklist

- [ ] Production secrets generated; no dev key anywhere
- [ ] `.env` and DB are `600`; `/opt/voice-agent` is `700`
- [ ] No `--reload`; frontend is a static build
- [ ] `LOCALAI_API_KEY` set and used by the gateway
- [ ] UFW + nftables rules active; `ss` shows loopback only
- [ ] Caddy serves HTTPS; `/metrics` and `/docs` return 404
- [ ] External port probe and local-user probe both fail as expected
- [ ] Auth matrix + pytest pass against the deployed instance
- [ ] Backup cron running; restore tested; off-box copy exists

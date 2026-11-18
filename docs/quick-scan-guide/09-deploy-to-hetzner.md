# 9. Deploy to Hetzner (5 minutes)

> **TL;DR:** One automated bare-metal deployment script (`scripts/deploy-hetzner.sh`). Creates dedicated system user `voiceagent`, sets 600 DAC permissions, provisions Caddy with Let's Encrypt TLS, installs 5 systemd units + timers, configures UFW firewall and nftables.

---

## 9.1 Pre-Flight Prerequisites

Before running the deployment script on the Hetzner bare-metal server:
- Ubuntu 24.04 LTS (x86_64) installed.
- NVIDIA GPU driver and CUDA 12+ installed (`nvidia-smi` displays GPU model and VRAM).
- Dedicated domain or subdomain DNS A-record pointed to the server's public IP.
- Python 3.12 and `uv` or `pip` available.

---

## 9.2 Step-by-Step Deployment Procedure

### Step 1: Run the Automated Deployment Script
```bash
sudo bash scripts/deploy-hetzner.sh
```
What the script does automatically:
1. Validates root execution and NVIDIA GPU driver presence.
2. Creates unprivileged system user `voiceagent` (shell `/usr/sbin/nologin`).
3. Establishes directory structure at `/opt/voice-agent` (`data`, `logs`, `models`, `deploy`, `scripts`).
4. Generates high-entropy production `.env` with 256-bit keys if none exists.
5. Sets strict DAC permissions: `chmod 700 /opt/voice-agent`, `chmod 600 .env`, `chmod 600 data/*.db`.
6. Installs systemd service units and cleanup timer.
7. Deploys Caddyfile template to `/etc/caddy/Caddyfile`.
8. Configures UFW firewall (permits only ports 22, 80, 443).
9. Registers nightly backup cron and removes temporary deployment staging artifacts.

### Step 2: Configure Reverse Proxy Domain & Credentials
1. Edit `/etc/caddy/Caddyfile`: replace `api.yourdomain.com` with your real domain.
2. Generate password hash for control panel basic auth:
   ```bash
   caddy hash-password
   ```
3. Insert username and hash into the `basic_auth` block in `/etc/caddy/Caddyfile`.

### Step 3: Start Services
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now voice-localai voice-tts voice-stt voice-gateway voice-cleanup.timer
sudo systemctl reload caddy
```

---

## 9.3 Systemd Units Reference

| Unit Name | Type | Purpose | Sandboxing & Security |
|---|---|---|---|
| `voice-gateway.service` | Simple | Uvicorn FastAPI gateway on `:8000` | `ProtectSystem=strict`, `NoNewPrivileges=true`, `PrivateTmp=true` |
| `voice-localai.service` | Simple | LocalAI LLM & Whisper engine on `:8080` | `KillMode=control-group`, GPU access |
| `voice-tts.service` | Simple | Kokoro TTS engine on `:8880` | Native CUDA acceleration |
| `voice-stt.service` | Simple | Sherpa-ONNX streaming server on `:8088` | Low-latency WebSockets |
| `voice-cleanup.service` | Oneshot | Daily junk and cache scrubber (`cleanup.py`) | ReadWritePaths restricted to `data`, `logs`, `models`, `.trash` |
| `voice-cleanup.timer` | Timer | Triggers `voice-cleanup.service` daily at 04:00 | Persistent across reboots |

---

## 9.4 Daily Operational Commands

| Task | Command |
|---|---|
| **View Gateway Logs** | `journalctl -u voice-gateway -f` |
| **Check All Unit States** | `systemctl status voice-gateway voice-localai voice-tts voice-stt` |
| **Restart Gateway** | `sudo systemctl restart voice-gateway` |
| **Monitor GPU VRAM** | `watch -n 1 nvidia-smi` |
| **Check Port Loopback** | `ss -tulpnH \| grep -E '8000\|8080\|8880\|8088'` |
| **Manual DB Backup** | `sudo -u voiceagent /opt/voice-agent/scripts/backup-db.sh` |
| **Dry-Run Cleanup Scan** | `sudo -u voiceagent /opt/voice-agent/.venv/bin/python scripts/cleanup.py scan` |

---

## 9.5 Post-Deployment Verification Checklist

- [ ] **1. Port Check:** `ss -tulpnH` confirms ports 8000, 8080, 8880, 8088 listen **only** on `127.0.0.1`.
- [ ] **2. External Timeout:** Probing ports 8000, 8080, 8880 from an external machine times out.
- [ ] **3. Proxy Blocks:** `curl https://yourdomain.com/metrics` returns **403 Forbidden**.
- [ ] **4. Liveness:** `curl https://yourdomain.com/health/live` returns `{"status": "ok"}` (HTTP 200).
- [ ] **5. Readiness:** `curl https://yourdomain.com/health/ready` returns HTTP 200 once engines are loaded.
- [ ] **6. Auth Matrix:** Running `python scripts/auth_matrix_probe.py` on the server passes all 27 route tests.
- [ ] **7. Permissions:** `ls -la /opt/voice-agent/.env` displays `-rw------- voiceagent voiceagent`.
- [ ] **8. Isolation:** Testing `curl http://127.0.0.1:8080` from a dummy user (e.g. `sudo -u nobody curl ...`) is dropped by nftables.

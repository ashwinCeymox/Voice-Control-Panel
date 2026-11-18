# 10. Deployment and Operations

## 10.1 Target Bare-Metal Topology (Hetzner Dedicated Server)

The production target is a single dedicated bare-metal server (such as Hetzner AX or EX series with NVIDIA GPU acceleration, running Ubuntu 24.04 LTS or Debian 12).

```
[ Internet ]
     |
     v  TCP 22, 80, 443
+-------------------------------------------------------------+
| UFW Perimeter Host Firewall                                 |
| - 22/tcp: SSH (rate-limited via ufw limit / fail2ban)        |
| - 80/tcp: Caddy ACME HTTP-01 challenge                      |
| - 443/tcp: Caddy TLS traffic (REST, WebSocket, Static UI)   |
| - All other ports: DENIED                                   |
+-------------------------------------------------------------+
     |
     v  TCP 80, 443
+-------------------------------------------------------------+
| Caddy Reverse Proxy & TLS Edge                              |
| - Terminates Let's Encrypt certificates                     |
| - Serves static frontend from /opt/voice-agent/frontend     |
| - Forwards /v1/* and /health/* to 127.0.0.1:8000            |
+-------------------------------------------------------------+
     |
     v  http://127.0.0.1:8000
+-------------------------------------------------------------+
| Gateway Application (FastAPI / Uvicorn)                     |
| - Running as user 'voiceagent' (UID unprivileged)           |
| - Single worker process (--workers 1)                       |
| - Manages SQLite database at data/voice_platform.db         |
+-------------------------------------------------------------+
     |
     +------------+------------+
     | 127.0.0.1  | 127.0.0.1  | 127.0.0.1
     v :8080      v :8880      v :8088
+-----------+ +-----------+ +---------------+
| LocalAI   | | local-tts | | Sherpa-ONNX   |
| (LLM/STT) | | (Kokoro)  | | Streaming STT |
+-----------+ +-----------+ +---------------+
```

### Directory Hierarchy

Production deployment installs all components under `/opt/voice-agent/`:

```
/opt/voice-agent/
|-- .env                     # Production configuration (chmod 600, voiceagent:voiceagent)
|-- .venv/                   # Isolated Python virtual environment
|-- app/                     # Gateway application codebase
|-- frontend/                # Compiled static frontend assets (dist/)
|-- data/
|   |-- voice_platform.db    # Primary SQLite database (WAL mode, chmod 600)
|   `-- backups/             # Nightly automated database snapshots
|-- models/
|   |-- localai/             # LocalAI GGUF model files
|   `-- streaming-stt/       # Sherpa-ONNX Zipformer model files
|-- logs/                    # Service log dumps and rotation targets
|-- deploy/                  # Configuration templates (Caddy, systemd, nftables)
`-- scripts/                 # Maintenance utilities (backup, restore, cleanup, deploy)
```

---

## 10.2 Automated Deployment Script (`scripts/deploy-hetzner.sh`)

The automated deployment script handles end-to-end installation and security hardening on a target Hetzner server **[Confirmed]**:

```bash
sudo bash scripts/deploy-hetzner.sh
```

### Deployment Pipeline Steps:

1. **Root Verification:** Validates that the script executes with root privileges (`EUID == 0`).
2. **NVIDIA Driver Detection:** Queries `nvidia-smi` to confirm GPU visibility, driver version, and total VRAM capacity.
3. **Dedicated System User Creation:** Creates unprivileged user `voiceagent` with home directory `/opt/voice-agent` and shell `/usr/sbin/nologin`.
4. **Directory Structure Setup:** Provisions `/opt/voice-agent/{data,logs,models,frontend,models/streaming-stt,deploy,scripts}` and `/var/log/caddy`.
5. **Production Secrets Initialization:** Generates high-entropy 256-bit credentials using `openssl rand -hex 32` for `ADMIN_API_KEY`, `API_KEY_HASH_SECRET`, and `LOCALAI_API_KEY`.
6. **Strict DAC Permission Hardening:** Applies `chmod 700 /opt/voice-agent`, `chmod 600 .env`, and `chmod 600 data/voice_platform.db`.
7. **Systemd Service Unit Installation:** Copies all unit files to `/etc/systemd/system/`, reloads the daemon, and enables timers.
8. **Caddyfile Provisioning:** Installs edge proxy configuration into `/etc/caddy/Caddyfile`.
9. **Host Firewall Hardening:** Configures UFW (`default deny incoming`, `allow 80/tcp`, `allow 443/tcp`, `limit 22/tcp`).
10. **Nightly Backup Cron Registration:** Installs `/etc/cron.d/voice-backup` for automated SQLite backups.
11. **Staging Cleanup:** Purges temporary build artifacts (`/tmp/voice-agent`, `/tmp/fe`).

---

## 10.3 Systemd Services & Supervision

The platform is decomposed into 6 supervised systemd units located in [`deploy/systemd/`](../../deploy/systemd):

| Unit File | Description | Execution Command / Role | Restart Policy |
|---|---|---|---|
| `voice-gateway.service` | FastAPI Application | `.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1` | `always` (5s delay) |
| `voice-localai.service` | LocalAI Inference Engine | `/opt/localai/local-ai --address 127.0.0.1:8080 --models-path /opt/voice-agent/models/localai` | `always` (5s delay) |
| `voice-tts.service` | local-tts (Kokoro) | `.venv/bin/python -m local_tts.server --host 127.0.0.1 --port 8880` | `always` (5s delay) |
| `voice-stt.service` | Sherpa-ONNX Streaming STT | `.venv/bin/python services/streaming_stt/server.py --port 8088` | `always` (5s delay) |
| `voice-cleanup.service` | Scheduled Disk Hygiene | `.venv/bin/python scripts/cleanup.py --policy policies/cleanup.server.toml --apply` | `oneshot` |
| `voice-cleanup.timer` | Cleanup Schedule Trigger | Triggers `voice-cleanup.service` every 2 hours with 5-minute random jitter | Active on boot |

### Gateway Systemd Unit (`voice-gateway.service`)

```ini
[Unit]
Description=Voice AI Platform Gateway
After=network.target voice-localai.service voice-tts.service voice-stt.service
Wants=voice-localai.service voice-tts.service voice-stt.service

[Service]
Type=simple
User=voiceagent
Group=voiceagent
WorkingDirectory=/opt/voice-agent
EnvironmentFile=/opt/voice-agent/.env
ExecStart=/opt/voice-agent/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=5s

# Hardening sandbox
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/voice-agent/data /opt/voice-agent/logs /opt/voice-agent/models
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

---

## 10.4 Daily Operational Commands

### Service Control

```bash
# Start all platform services
sudo systemctl enable --now voice-localai voice-tts voice-stt voice-gateway

# Check status of all voice units
sudo systemctl status "voice-*"

# Restart gateway after configuration or code update
sudo systemctl restart voice-gateway

# Live stream aggregated logs
sudo journalctl -u voice-gateway -u voice-localai -u voice-tts -u voice-stt -f
```

### Telemetry & Hardware Monitoring

```bash
# Monitor GPU VRAM, compute utilization, and temperature
watch -n 1 nvidia-smi

# Check active loopback socket bindings and listener health
ss -tulpn | grep -E ':(8000|8080|8880|8088)'

# Monitor in-flight Caddy reverse proxy requests
tail -f /var/log/caddy/xyz.access.log
```

---

## 10.5 Three-Stage Capacity & Scaling Roadmap

| Scaling Stage | Architecture Description | Concurrency Target | Necessary Code & Infra Modifications |
|---|---|---|---|
| **Stage 1 (Current)** | **Single Server Bare-Metal:** Single gateway process (`--workers 1`), in-memory ticket/rate limit caches, SQLite WAL mode database, co-located engines. | 3–5 simultaneous streams; 10 req/s batch | **None.** Ready for deployment via `scripts/deploy-hetzner.sh`. |
| **Stage 2 (Vertical)** | **High-VRAM GPU Upgrade:** Co-located dual GPUs (e.g. RTX 4090 or A5000), segregated GPU allocation (`CUDA_VISIBLE_DEVICES`), increased global engine semaphores (`MAX_ACTIVE_STT_STREAMS=15`). | 15–20 simultaneous streams; 30 req/s batch | Configuration only. Tuning concurrency limits in `.env`. |
| **Stage 3 (Horizontal)** | **Multi-Node Distributed Cluster:** N gateway nodes behind Caddy/HAProxy load balancer; dedicated inference worker pools; externalized shared state. | 100+ simultaneous streams; 200+ req/s batch | 1. Migrate SQLite to PostgreSQL.<br>2. Migrate ticket store and rate limiter to Redis.<br>3. Configurable remote engine URL lists. |

---

## 10.6 Telemetry, Observability & Health Probes

The platform exposes standardized endpoints for uptime monitoring and orchestrator health checks:

- **Liveness Probe (`GET /health/live`):**
  Returns `{"status": "live"}` with HTTP 200. Used by systemd or external watchdogs to verify that the event loop is active.
- **Readiness Probe (`GET /health/ready`):**
  Queries LocalAI (`/readyz`), local-tts (`/v1/models`), and Sherpa-ONNX (`/health`) over loopback. Returns aggregated health:
  ```json
  {
    "status": "ready",
    "providers": {
      "localai": {"status": "ok", "latency_ms": 12.4},
      "local_tts": {"status": "ok", "latency_ms": 8.1},
      "streaming_stt": {"status": "ok", "latency_ms": 4.2}
    }
  }
  ```
- **Prometheus Metrics (`GET /metrics`):**
  Exports internal counters, histograms, and latencies (requests total, turn duration seconds, active stream gauges). **Hard-blocked from public access by Caddy**; accessible exclusively over SSH tunnel or internal Prometheus scraper at `http://127.0.0.1:8000/metrics`.

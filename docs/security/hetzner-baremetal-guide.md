# Hetzner Bare-Metal Security & Production Hardening Guide

This guide is tailored specifically for deploying the **Voice AI Platform** directly on **Hetzner Bare-Metal / Dedicated Root Servers** (Ubuntu 22.04 / 24.04 LTS or Debian 12) with **direct NVIDIA GPU/VRAM access** (no Docker) alongside **other co-located services**.

---

## 1. Multi-Service Host Isolation

Because other services and user accounts exist on the same server, you must isolate the Voice Agent processes and files using Linux Discretionary Access Control (DAC) and system users.

### Step 1: Create Dedicated System User and Group
Do not run services as `root` or your personal admin user:
```bash
sudo useradd --system \
  --shell /usr/sbin/nologin \
  --home /opt/voice-agent \
  --create-home \
  voiceagent
```

### Step 2: Strict File & Secret Permissions
Ensure only `voiceagent` can read the configuration, database, and models:
```bash
sudo chown -R voiceagent:voiceagent /opt/voice-agent
sudo chmod 700 /opt/voice-agent

# Secrets and database must strictly be 600
sudo chmod 600 /opt/voice-agent/.env
sudo chmod 600 /opt/voice-agent/voice_platform.db
```
> [!IMPORTANT]
> With `700` directory and `600` file permissions, other non-root services (`www-data`, other application users) cannot peek into `.env` or read API key hashes from `voice_platform.db`.

---

## 2. Direct GPU / VRAM Access (NVIDIA CUDA)

Without Docker, all processes talk directly to the host NVIDIA driver and CUDA runtime.

### Step 1: Verify Host NVIDIA Drivers & CUDA
```bash
nvidia-smi
nvcc --version
```

### Step 2: GPU Allocation & Multi-Service VRAM Protection
If other services on the host also use the GPU:
- Use `CUDA_VISIBLE_DEVICES` to dedicate specific GPUs (e.g. `CUDA_VISIBLE_DEVICES=0` for Voice Agent, `CUDA_VISIBLE_DEVICES=1` for other workloads).
- In LocalAI model configs (`models/*.yaml`), set `f16: true` or appropriate quantization (`q4_k_m`, `q8_0`) to prevent out-of-memory (OOM) crashes.
- Monitor active VRAM consumption:
  ```bash
  watch -n 1 nvidia-smi
  ```

---

## 3. Host Firewall & Network Exposure (IPv4 + IPv6)

Hetzner servers have public IPv4 and `/64` IPv6 routed directly to `eth0`. Every port bound to `0.0.0.0` or `::` is exposed to the world unless blocked by the firewall.

### Step 1: Configure UFW (Uncomplicated Firewall)
```bash
# Set default policies (DROP all incoming on IPv4 and IPv6)
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow SSH (recommended: rate-limited)
sudo ufw limit 22/tcp

# Allow Web Traffic for Reverse Proxy ONLY
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Enable Firewall
sudo ufw enable
```

### Step 2: Verify Listener Sockets
Verify that LocalAI (`8080`), local-tts (`8880`), streaming-stt (`8088`), and the gateway (`8000`) are bound **strictly to `127.0.0.1`**:
```bash
sudo ss -tulpnH | grep -E "8000|8080|8880|8088"
```
**Expected output:**
```
tcp  LISTEN 0  128  127.0.0.1:8000  0.0.0.0:*
tcp  LISTEN 0  128  127.0.0.1:8080  0.0.0.0:*
tcp  LISTEN 0  128  127.0.0.1:8880  0.0.0.0:*
tcp  LISTEN 0  128  127.0.0.1:8088  0.0.0.0:*
```
*(If any line shows `0.0.0.0:` or `*:`, that engine is exposed to the internet. Fix the startup flag to `--address 127.0.0.1`.)*

---

## 4. Reverse Proxy & TLS Configuration

Never expose the FastAPI Gateway directly to port 80/443. Use **Caddy** (recommended for zero-touch auto-TLS) or **Nginx**.

### Option A: Caddy (`/etc/caddy/Caddyfile`)
```caddy
# Reject raw IP access / scanners
:80, :443 {
    respond 404
}

api.yourdomain.com {
    # Automatic Let's Encrypt / ZeroSSL TLS certificate
    encode zstd gzip

    # Only forward public API and health routes to Gateway
    handle /v1/* {
        reverse_proxy 127.0.0.1:8000 {
            header_up X-Real-IP {remote_host}
            header_up X-Forwarded-For {remote_host}
            header_up X-Forwarded-Proto {scheme}
        }
    }

    handle /health/* {
        reverse_proxy 127.0.0.1:8000
    }

    # Block internal engine ports & docs
    handle {
        respond 404
    }
}
```

### Option B: Nginx (`/etc/nginx/sites-available/voice-agent.conf`)
```nginx
# Reject raw IP requests
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    ssl_reject_handshake on;
    return 444;
}

server {
    server_name api.yourdomain.com;
    listen 80;
    listen [::]:80;
    return 301 https://$host$request_uri;
}

server {
    server_name api.yourdomain.com;
    listen 443 ssl http2;
    listen [::]:443 ssl http2;

    ssl_certificate /etc/letsencrypt/live/api.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.yourdomain.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    client_max_body_size 100M;
    client_body_timeout 60s;

    # Forward to FastAPI Gateway
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;

        # WebSocket support
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
```

---

## 5. Systemd Sandboxed Service Units

Manage the platform via `systemd` so services start automatically on boot and restart if crashed.

### Gateway Service: `/etc/systemd/system/voice-gateway.service`
```ini
[Unit]
Description=Voice AI Platform FastAPI Gateway
After=network.target voice-localai.service voice-tts.service voice-stt.service

[Service]
Type=simple
User=voiceagent
Group=voiceagent
WorkingDirectory=/opt/voice-agent
EnvironmentFile=/opt/voice-agent/.env
ExecStart=/opt/voice-agent/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4
Restart=always
RestartSec=5

# Security Sandboxing
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
NoNewPrivileges=true
ReadWritePaths=/opt/voice-agent/voice_platform.db /opt/voice-agent/logs /opt/voice-agent/data

[Install]
WantedBy=multi-user.target
```

---

## 6. Production Credentials Generation
Before starting services, generate secure 256-bit random keys for production `.env`:
```bash
ADMIN_KEY=$(openssl rand -hex 32)
HASH_SECRET=$(openssl rand -hex 32)

echo "ADMIN_API_KEY=$ADMIN_KEY"
echo "API_KEY_HASH_SECRET=$HASH_SECRET"
```
Never reuse development keys (`dev-voice-key-12345`) on a public Hetzner server.

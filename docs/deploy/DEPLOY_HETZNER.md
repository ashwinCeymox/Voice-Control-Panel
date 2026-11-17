# Production Deployment Guide: Hetzner Dedicated Server

This guide details the deployment of the Voice AI Platform onto a Hetzner dedicated server with an NVIDIA GPU running Ubuntu/Debian under systemd and Caddy.

---

## 1. Architecture Overview

- **TLS Termination:** Caddy terminates TLS for the public domain with automated Let's Encrypt / ZeroSSL certificates.
- **Port Exposure:** Only ports `80` (redirect to 443) and `443` (HTTPS) are exposed on the public network interface.
- **Engines & Gateway:** All internal engines listen exclusively on loopback (`127.0.0.1`):
  - FastAPI Gateway: `127.0.0.1:8000`
  - LocalAI (LLM / Batch STT): `127.0.0.1:8080`
  - Sherpa-ONNX (Streaming STT): `127.0.0.1:8088`
  - local-tts (TTS Engine): `127.0.0.1:8880`
- **Control Panel Auth Defense:**
  - Layer 1: Caddy HTTP `basic_auth` on `/voicecontrolpanel/` (retained as a defense-in-depth layer for initial launch).
  - Layer 2: Super admin web login page with argon2id password hashing, account lockout (5 attempts / 15 mins), IP throttling, and HttpOnly SameSite=Strict Secure cookies.

---

## 2. Server Prerequisites

```bash
# Verify NVIDIA GPU and CUDA driver
nvidia-smi
nvcc --version

# Create dedicated unprivileged system user
sudo useradd --system --shell /usr/sbin/nologin --home /opt/voice-agent --create-home voiceagent

# Install Caddy, fail2ban, and ufw
sudo apt-get update
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl fail2ban ufw
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy
```

---

## 3. Deployment Steps

1. **Deploy Repository:**
   ```bash
   sudo rsync -av --exclude='.git' --exclude='node_modules' --exclude='.venv' ./ /opt/voice-agent/
   sudo chown -R voiceagent:voiceagent /opt/voice-agent
   sudo chmod 700 /opt/voice-agent
   ```

2. **Configure Environment:**
   Copy `.env.example` to `.env` and set production secrets:
   ```bash
   sudo cp /opt/voice-agent/.env.example /opt/voice-agent/.env
   sudo chmod 600 /opt/voice-agent/.env
   ```
   Must configure:
   - `ENVIRONMENT=production`
   - `ADMIN_INITIAL_PASSWORD=<strong-random-password-12+-chars>`
   - `ADMIN_API_KEY=<strong-random-key>`
   - `API_KEY_HASH_SECRET=<strong-random-secret>`
   - `GATEWAY_HOST=127.0.0.1`
   - `COOKIE_SECURE=true`

3. **Run Setup Script:**
   ```bash
   sudo -u voiceagent /opt/voice-agent/scripts/setup.sh
   ```

4. **Install Systemd Units:**
   Copy unit files from `deploy/systemd/` to `/etc/systemd/system/`:
   ```bash
   sudo cp /opt/voice-agent/deploy/systemd/*.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now voice-gateway.service voice-localai.service voice-sherpa-onnx.service
   ```

5. **Configure Caddy:**
   Use `docs/deploy/Caddyfile.hetzner.example` as `/etc/caddy/Caddyfile`, replacing domain and basic_auth hash.
   ```bash
   sudo systemctl restart caddy
   ```

6. **Verify Firewall & Port Binding:**
   ```bash
   sudo ufw default deny incoming
   sudo ufw default allow outgoing
   sudo ufw allow 22/tcp
   sudo ufw allow 80/tcp
   sudo ufw allow 443/tcp
   sudo ufw enable

   # Verify internal engine ports are ONLY on 127.0.0.1
   sudo ss -tulpn | grep -E "8000|8080|8088|8880"
   ```

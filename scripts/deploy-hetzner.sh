#!/usr/bin/env bash
# ==============================================================================
# Hetzner Bare-Metal Deployment & Production Hardening Script
# Follows instructions in docs/security/hetzner-baremetal-guide.md,
# con-proxy.md, and hetzner.md.
# ==============================================================================
set -euo pipefail

INSTALL_DIR="/opt/voice-agent"
SERVICE_USER="voiceagent"

echo "=== Voice AI Platform: Hetzner Bare-Metal Deployment ==="

# 1. Root check
if [ "$EUID" -ne 0 ]; then
    echo "Error: This deployment script must be run as root (sudo)." >&2
    exit 1
fi

# 2. Check NVIDIA GPU drivers (direct VRAM requirement)
if command -v nvidia-smi >/dev/null 2>&1; then
    echo "✓ NVIDIA driver detected:"
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
else
    echo "⚠ Warning: nvidia-smi not found. Ensure NVIDIA CUDA drivers are installed for direct GPU acceleration."
fi

# 3. Create dedicated system user
if id "$SERVICE_USER" >/dev/null 2>&1; then
    echo "✓ System user '$SERVICE_USER' exists."
else
    echo "Creating system user '$SERVICE_USER'..."
    useradd --system --shell /usr/sbin/nologin --home "$INSTALL_DIR" --create-home "$SERVICE_USER"
    echo "✓ Created user '$SERVICE_USER'."
fi

# 4. Prepare directory layout
echo "Setting up directory hierarchy in $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"/{data,logs,models,frontend,models/streaming-stt,deploy,scripts}
mkdir -p /var/log/caddy /etc/caddy /etc/systemd/system /etc/nftables.d /etc/fail2ban/filter.d /etc/fail2ban/jail.d

# 5. Production Secrets Initialization
if [ ! -f "$INSTALL_DIR/.env" ]; then
    echo "Generating high-entropy 256-bit production credentials..."
    ADMIN_SECRET=$(openssl rand -hex 32)
    HASH_SECRET=$(openssl rand -hex 32)
    LOCALAI_SECRET=$(openssl rand -hex 32)

    cat <<EOF > "$INSTALL_DIR/.env"
ENVIRONMENT=production
ADMIN_API_KEY=$ADMIN_SECRET
API_KEY_HASH_SECRET=$HASH_SECRET
LOCALAI_API_KEY=$LOCALAI_SECRET
LOCALAI_BASE_URL=http://127.0.0.1:8080
LOCAL_TTS_HTTP_BASE_URL=http://127.0.0.1:8880
STREAMING_STT_URL=http://127.0.0.1:8088
STREAMING_STT_WS_URL=ws://127.0.0.1:8088/ws
ENABLE_DOCS=false
LOG_TRANSCRIPTS=false
CORS_ALLOWED_ORIGINS=["https://api.yourdomain.com"]
EOF
    echo "✓ Generated new production .env with random 256-bit keys."
    echo "  NOTE: Back up API_KEY_HASH_SECRET safely! Losing it invalidates all issued keys."
else
    echo "✓ Existing .env preserved."
fi

# 6. Apply strict file permissions (multi-service isolation)
echo "Applying strict DAC permissions..."
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
chown -R caddy:caddy /var/log/caddy 2>/dev/null || true
chmod 700 "$INSTALL_DIR"
chmod 600 "$INSTALL_DIR/.env"
if [ -f "$INSTALL_DIR/data/voice_platform.db" ]; then
    chmod 600 "$INSTALL_DIR/data/voice_platform.db"
fi

# 7. Install systemd service units and timers
echo "Installing systemd units..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -d "$SCRIPT_DIR/deploy/systemd" ]; then
    cp "$SCRIPT_DIR/deploy/systemd"/*.service /etc/systemd/system/ 2>/dev/null || true
    cp "$SCRIPT_DIR/deploy/systemd"/*.timer /etc/systemd/system/ 2>/dev/null || true
    systemctl daemon-reload
    systemctl enable voice-cleanup.timer 2>/dev/null || true
    echo "✓ Installed voice-localai, voice-tts, voice-stt, voice-gateway, and voice-cleanup units."
fi

# 8. Install Caddy configuration if provided
if [ -f "$SCRIPT_DIR/deploy/caddy/Caddyfile" ] && [ ! -f /etc/caddy/Caddyfile ]; then
    cp "$SCRIPT_DIR/deploy/caddy/Caddyfile" /etc/caddy/Caddyfile
    echo "✓ Installed Caddyfile template to /etc/caddy/Caddyfile"
fi

# 9. Configure UFW Firewall (network perimeter)
if command -v ufw >/dev/null 2>&1; then
    echo "Configuring UFW host firewall..."
    ufw default deny incoming
    ufw default allow outgoing
    ufw limit 22/tcp || true
    ufw allow 80/tcp
    ufw allow 443/tcp
    echo "✓ UFW rules configured (ports 22, 80, 443 only)."
fi

# 10. Install backup cron
if [ -f "$SCRIPT_DIR/deploy/cron/voice-backup" ]; then
    cp "$SCRIPT_DIR/deploy/cron/voice-backup" /etc/cron.d/voice-backup
    chmod 644 /etc/cron.d/voice-backup
    echo "✓ Nightly backup cron registered."
fi

# 11. Category H: Purge deployment staging artifacts
echo "Cleaning up deployment staging artifacts (/tmp/voice-agent, /tmp/fe)..."
rm -rf /tmp/voice-agent /tmp/fe
echo "✓ Staging directories cleaned."

echo ""
echo "=== Deployment Preparation Complete ==="
echo "Next steps on the server:"
echo "1. Verify domain in /etc/caddy/Caddyfile and generate basic_auth hash with 'caddy hash-password'"
echo "2. Start services: sudo systemctl enable --now voice-localai voice-tts voice-stt voice-gateway"
echo "3. Reload Caddy: sudo systemctl reload caddy"
echo "4. Run health check: curl http://127.0.0.1:8000/health/ready"

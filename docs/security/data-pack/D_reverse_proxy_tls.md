# Data Pack: Section D - Reverse Proxy and TLS

Collected on: 2026-11-16

## D-01: Reverse Proxy Presence
* Currently installed/running: **None** (`caddy not found`, `nginx not found`).
* Status: Direct HTTP and WebSockets on port 8000 and 5173.
* Exposure Risk: Exposing the gateway directly without a reverse proxy leaves traffic unencrypted (plain HTTP), causing caller API keys to be transmitted in cleartext.

## D-02: Path Forwarding Configuration
* Not applicable until Caddy or Nginx is installed.
* Target architecture requirement: Reverse proxy must only route to `127.0.0.1:8000`. No path on the proxy should forward directly to ports 8080, 8880, or 8088.

## D-03: Header Trust & Spoofing
* Gateway trusts standard Authorization headers:
  * `Authorization: Bearer <API_KEY>`
  * `xi-api-key: <API_KEY>`
* `X-Forwarded-For` and `X-Real-IP`:
  * Handled via standard Starlette request headers.
  * In Caddy/Nginx, proxy headers must be sanitized so external callers cannot spoof client IP address.

## D-04: TLS Configuration
* Current Dev Environment: Plain HTTP / WS (no encryption).
* Hetzner Target Requirement: TLS 1.2/1.3 with automated Let's Encrypt certificate issuance via Caddy or Nginx + Certbot.

## D-05: WebSocket Upgrades
* WebSocket routes:
  * `/v1/voice/stream`
  * `/v1/audio/transcriptions/stream`
* Handshake: Proxy must support HTTP/1.1 `Upgrade: websocket` and `Connection: Upgrade`.

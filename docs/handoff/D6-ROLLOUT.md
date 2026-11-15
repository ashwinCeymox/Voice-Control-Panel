# Task D6 Rollout & Verification: Developer Dashboard

**Date:** 2026-11-15  
**Component:** System Integration & LAN Rollout  
**Status:** COMPLETE (All Developer Dashboard tasks D0-D6 delivered and verified)

---

## 1. Rollout Overview

The Developer Dashboard is fully integrated into the local voice AI control panel for LAN network consumption.

### Completed Milestones (D0 through D6)
1. **D0 (Preconditions & Tag):** Verified backend foundation and created `pre-dashboard` git tag.
2. **D1-BACKEND:** Profiles CRUD, fine-grained key management (pausing, grace-period rotation, model & IP allowlists), central dashboard settings, and administrative audit logging.
3. **D2-USAGE:** Real-time usage telemetry (`usage_events`), summary metrics with p50/p95 latency percentiles, hourly/daily timeseries, paged request logs with CSV export, and warm engine states.
4. **D3-UI:** Frontend Developers section (Overview with live charts, Profiles & Key cards with one-time plain key display, Request Log with filters, and Settings).
5. **D4-SNIPPETS:** Code snippet templates (Python, curl, JavaScript, OpenAI SDK) targeting gateway port 8000, and interactive "Try it" execution runner.
6. **D5-QA:** Automated snippet testing, IP allowlist validation, key rotation grace-period testing, and strict zero-content privacy audit.
7. **D6-ROLLOUT:** LAN base URL configuration, verification, and tagging `dashboard-complete`.

---

## 2. LAN Deployment Instructions

To expose the dashboard and endpoints to consuming applications on your local network:
1. **Determine LAN IP:**
   ```bash
   ipconfig getifaddr en0 # On macOS (e.g. 192.168.1.100)
   ```
2. **Configure Dashboard Base URL:**
   - In the control panel under **Developers > Settings**, set the Gateway Base URL to:
     `http://<LAN_IP>:8000` (e.g. `http://192.168.1.100:8000`).
   - All code snippets will instantly render with this LAN address.
3. **Generate First Consuming Key:**
   - Go to **Developers > API Keys**.
   - Create a Profile (e.g. `telephony-server`).
   - Click **+ New Key**, select desired scopes (`llm`, `stt`, `tts`), and optionally specify the caller's IP CIDR (e.g. `192.168.1.45/32`).
   - Copy the secret API key from the one-time display dialog.
4. **Test Consuming Call:**
   Run the copyable curl snippet from your remote server:
   ```bash
   curl -N -X POST "http://192.168.1.100:8000/v1/chat/completions" \
     -H "Authorization: Bearer <COPIED_KEY>" \
     -H "Content-Type: application/json" \
     -d '{"model": "llama-3.2-1b-instruct", "messages": [{"role": "user", "content": "Hello!"}], "stream": true}'
   ```
5. **Inspect Live Telemetry:**
   Check **Developers > Overview** and **Developers > Request Log** to see real-time latency, TTFB, and token metrics.

---

## 3. Git Release Tag

Tagged as `dashboard-complete`.

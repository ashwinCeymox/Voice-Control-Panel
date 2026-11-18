# 13. Troubleshooting (2 minutes)

> **TL;DR:** Fast diagnostic lookup. Find your symptom in the table below, inspect the likely cause, and run the suggested fix command.

---

## 13.1 Quick Diagnostics Table

| Symptom | Likely Cause | Solution / Command |
|---|---|---|
| **502 Bad Gateway on first turn** | Engine still loading model weights into VRAM | Wait 5–10 seconds. Check `journalctl -u voice-localai -f` or pre-warm via `scripts/start-native-engines.sh`. |
| **Garbled, chipmunk, or robotic audio** | Sample rate mismatch (e.g. 44.1/48 kHz sent instead of 16 kHz) | Ensure client Web Audio Resampler converts mic capture to **16,000 Hz mono PCM16**. |
| **Streaming STT returns empty text** | Audio format mismatch or low RMS mic energy | Verify audio is sent as raw 16-bit signed PCM (`pcm_s16le`). Lower VAD silence threshold. |
| **Background noise triggers speech turn** | RMS noise gate threshold set too low | Raise `MIN_SPEECH_RMS` in `app/services/server_vad.py` or enable Whisper VAD pre-filter. |
| **WebSocket disconnects immediately (4401)** | Expired or already consumed ticket | Tickets expire after 60s. Request a fresh ticket via `POST /v1/auth/ticket` immediately before opening socket. |
| **WebSocket disconnects with 4403** | Key lacks `voice_stream` or `stt_stream` scope | Update caller key scopes in `dev-keys` page or generate an admin key. |
| **All requests return 401 Unauthorized** | Missing or malformed header | Send `Authorization: Bearer <key>` or `xi-api-key: <key>`. Confirm key is not revoked. |
| **507 Insufficient Storage (`DISK_LOW`)** | Available disk space dropped below 5% or 2 GB | Run safe cleanup: `python scripts/cleanup.py run --policy policies/cleanup.server.toml`. |
| **Port conflict on startup (e.g. 8088 in use)** | Existing daemon or stale background process holding port | Check PID: `lsof -ti :8088`. Terminate gracefully: `kill <pid>`. |
| **Caddy returns 403 on `/metrics`** | Expected production behavior | Metrics are blocked externally. Scrape locally from `127.0.0.1:8000/metrics`. |

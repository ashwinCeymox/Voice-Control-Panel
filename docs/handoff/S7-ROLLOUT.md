# Handoff: S7 Rollout & Production Operations (ORCH)

**Status:** COMPLETE  
**Rollout Stage:** Ready for Controlled Production Rollout  
**Default Configuration:** Safe Default (`stt.kind = "batch"`)  

---

## 1. Rollout Strategy & Defaults

To ensure zero regression for existing clients and production workloads:
1. **Conservative Default:**
   - Existing agents migrated to `schema_version = 2` are assigned `stt.kind = "batch"`.
   - All standard requests use the proven LocalAI Whisper batch pipeline by default.
2. **Opt-in Streaming Mode:**
   - Specific agents (e.g. conversational voice assistant or real-time captioning) can be toggled to `streaming` in the Agent Editor or via `POST /v1/agents` with `stt.kind = "streaming"` and `fallback_model = "whisper-base"`.
   - Callers of `WS /v1/voice/stream` can provide `stt_kind: "streaming"` in `session.start`.
   - External callers can connect directly to `WS /v1/audio/transcriptions/stream` using an API key possessing the `stt_stream` scope.
3. **Emergency Kill Switch (`STT_STREAM_ENABLED`):**
   - If the streaming service encounters anomalies or load pressure, administrators can instantly toggle the global kill switch without server restart:
     ```bash
     curl -X PUT http://127.0.0.1:8000/v1/admin/settings/stt \
       -H "Authorization: Bearer <ADMIN_KEY>" \
       -H "Content-Type: application/json" \
       -d '{"stt_stream_enabled": false}'
     ```
   - When disabled, all streaming requests immediately and safely fallback to batch Whisper.

---

## 2. Health Monitoring & Service Supervision

- **Gateway Readiness Check:**
  `GET /health/ready` aggregates both LocalAI, local-tts, and streaming STT service statuses.
- **Internal Service Health:**
  `GET http://127.0.0.1:8088/health` reports engine readiness, model ID, and memory footprint.
- **Concurrency & Caps:**
  Global limit is set to 5 concurrent streams via `MAX_ACTIVE_STT_STREAMS` and enforced via `stream_limiter`.

---

## 3. Checklist for Promotion to Global Default

Before changing `default_kind` to `streaming` globally:
- [x] S0 preconditions verified & tagged `pre-streaming`.
- [x] S1 engine bake-off complete (Zipformer 20M selected, license Apache-2.0).
- [x] S2 gateway adapter implemented with error sanitization & usage tracking.
- [x] S3 public WebSocket endpoint (`WS /v1/audio/transcriptions/stream`) with `stt_stream` scope check.
- [x] S4 voice pipeline dual-path with live partial captions & automatic batch fallback.
- [x] S5 agent schema bumped to `v2` with migration & UI Batch/Streaming switches.
- [x] S6 QA & soak testing passed (30-turn soak, concurrency saturation, privacy audit).
- [ ] Measure latency and user feedback on candidate agent in production.
- [ ] Update `STT_STREAM_ENABLED=True` and promote global default to `streaming` via Admin Settings API.

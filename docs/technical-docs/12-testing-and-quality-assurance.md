# 12. Testing and Quality Assurance

## 12.1 Automated Test Suite Breakdown

The platform maintains an automated test suite comprising **207 test cases** across 28 test modules in [`tests/unit/`](../../tests/unit) **[Confirmed]**:

| Category | Primary Test Files | Test Coverage & Verification Objectives |
|---|---|---|
| **Security & Hardening** | `test_security_audit.py`<br>`test_hardening_h1_to_h11.py`<br>`test_hardcode_scan.py` | Model-ID path traversal attempts (`..`, `/`, `\`); regex validation; startup failsafe enforcement; scan for plaintext secrets; permission checks. |
| **Authentication & Quotas** | `test_health_and_auth.py`<br>`test_m2_voice_auth.py`<br>`test_p2_keys_and_limits.py`<br>`test_m2_quotas.py` | HMAC-SHA256 signature verification; constant-time comparison; scope checks (`voice`, `stt`, `llm`, `tts`); rate limiting; token quotas; key expiry. |
| **Voice Orchestration & Gates** | `test_orchestrator.py`<br>`test_p3_rules_and_provider.py`<br>`test_m2_server_vad.py`<br>`test_text_chunker.py` | Turn pipeline execution; RMS silence gating; hallucination filters; repeat-phrase prompts; phrase-boundary chunking; cancellation (`turn.cancel`). |
| **Streaming STT & Fallback** | `test_s2_streaming_adapter.py`<br>`test_s3_streaming_endpoint.py`<br>`test_s4_voice_streaming.py`<br>`test_streaming_stt_fallback_and_models.py` | WebSocket streaming frames; partial transcript emission (`transcript.partial`); automatic fallback to batch Whisper upon engine failure (`stt.fallback`). |
| **Model Registry & Gallery** | `test_model_manifest.py`<br>`test_model_classification_dashboard.py`<br>`test_stt_gallery.py` | Model discovery across LocalAI and local-tts; manifest synchronization; RAM/VRAM fit scoring; install job tracking; model deletion safeguards. |
| **Data Hygiene & Database** | `test_m2_backup.py`<br>`test_hygiene_manager.py`<br>`test_cleanup_safety.py` | SQLite atomic `.backup` and restore; WAL mode durability; background hygiene loop; disk emergency threshold triggers; git protection rules. |
| **Agents & Administration** | `test_agents_api.py`<br>`test_s5_agent_schema.py`<br>`test_d1_admin_profiles.py`<br>`test_d2_usage_analytics.py` | Agent CRUD operations; system prompt overrides; voice ID assignments; usage analytics aggregation; memory residency flush. |

---

## 12.2 Authentication Matrix Probe (`scripts/auth_matrix_probe.py`)

The authentication matrix probe is an automated auditing tool that verifies the entire 42-endpoint surface of the platform against four distinct caller personas **[Confirmed]**:

```bash
python scripts/auth_matrix_probe.py --base-url http://127.0.0.1:8000
```

### Audited Personas & Expected Responses:

1. **Unauthenticated (No Key):**
   - Public endpoints (`/health/live`, `/health/ready`, `/`) must return HTTP 200.
   - All protected routes must return HTTP 401 Unauthorized.
2. **Invalid Key (Malformed or revoked):**
   - Must return HTTP 401 Unauthorized across all protected routes.
3. **Caller Key (Scoped permissions e.g. `["stt", "tts"]`):**
   - Authorized routes return HTTP 200 / 204.
   - Out-of-scope inference routes return HTTP 403 Forbidden.
   - Admin routes (`/v1/admin/*`, `/v1/agents/*`, model install/delete) return HTTP 403 Forbidden.
4. **Admin Key (Master administrative credential):**
   - Must successfully access all routes including key rotation, model management, and usage analytics.

The audit script produces a summary compliance table and exits with code 1 if any route fails its security expectation.

---

## 12.3 Pre-Release Gate Checklist

Before deploying any platform build to staging or production, execute the following mandatory verification steps:

- [ ] **1. Run Full Unit Test Suite:** Confirm all 207 automated tests pass:
  ```bash
  .venv/bin/pytest tests/ -v
  ```
- [ ] **2. Execute Authentication Matrix Probe:**
  ```bash
  .venv/bin/python scripts/auth_matrix_probe.py
  ```
- [ ] **3. Verify Hardcode Scan:** Confirm zero leaked credentials or development keys in source:
  ```bash
  .venv/bin/pytest tests/unit/test_hardcode_scan.py
  ```
- [ ] **4. Verify SQLite WAL Mode:** Confirm WAL mode is enabled:
  ```bash
  sqlite3 data/voice_platform.db "PRAGMA journal_mode;"
  # Expected output: wal
  ```
- [ ] **5. Test Database Backup and Restore:**
  ```bash
  bash scripts/backup-db.sh && bash scripts/restore-db.sh --latest --dry-run
  ```
- [ ] **6. Build Production Frontend:**
  ```bash
  cd frontend && npm ci && npm run build && cd ..
  ```
- [ ] **7. Verify File Permissions:**
  ```bash
  ls -l .env data/voice_platform.db
  # Must show -rw------- (600)
  ```
- [ ] **8. Confirm Startup Failsafe:** Run gateway with `ENVIRONMENT=production` and default admin key to ensure process aborts immediately.
- [ ] **9. Verify Caddy Proxy Rules:** Confirm `/metrics` and `/openapi.json` return HTTP 404 through the proxy.
- [ ] **10. Inspect GPU & Resource Telemetry:** Check `nvidia-smi` and `/v1/system/resources` to confirm sufficient VRAM headroom.

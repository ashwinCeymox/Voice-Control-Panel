# 11. Testing (2 minutes)

> **TL;DR:** 207 automated tests covering unit logic, integration pipelines, API contracts, and cleanup safety. Fast, deterministic, and runnable locally with `.venv/bin/pytest tests/`. Live security credentials probed via `scripts/auth_matrix_probe.py`.

---

## 11.1 Test Suite Organization

```text
tests/
├── unit/                        # Fast isolated component and schema tests
│   ├── test_cleanup_safety.py   # Safety guarantees (protected files, limits, restore)
│   ├── test_hygiene_manager.py  # In-app janitor, ticket reaper, disk guard
│   ├── test_m2_voice_auth.py    # HMAC auth, scopes, revocation, and expiry
│   ├── test_m2_backup.py        # Database backup and recovery procedures
│   ├── test_m2_server_vad.py    # RMS energy gating & noise suppression
│   ├── test_s2_streaming_adapter.py # Streaming STT client protocol
│   └── test_s3_streaming_endpoint.py# WebSocket endpoints and live settings
├── integration/                 # Multi-service pipeline tests
│   └── test_s6_streaming_qa.py  # Concurrency limits and audio frame resilience
└── benchmark/                   # Performance & latency characterization
    └── run_streaming_benchmarks.py # First-packet latency & RTF measurements
```

---

## 11.2 Core Test Commands

### 1. Run Complete Pytest Suite
```bash
.venv/bin/pytest tests/ -q
```
*Current benchmark:* **207 passed in ~30 seconds**.

### 2. Run Only Cleanup & Safety Tests
```bash
.venv/bin/pytest tests/unit/test_cleanup_safety.py tests/unit/test_hygiene_manager.py -v
```

### 3. Run Live Authentication Matrix Audit
```bash
.venv/bin/python scripts/auth_matrix_probe.py
```
Tests all 27 HTTP and WebSocket endpoints across 7 credential profiles (No key, Invalid key, Caller STT, Admin, Expired, Revoked, Default Dev key).

---

## 11.3 Acceptance Gate Before Production Release

Before tagging or shipping code to the Hetzner server:
- [ ] Pytest passes 100% of tests (`207 passed, 0 failed`).
- [ ] Frontend builds without TypeScript or bundling errors (`npm --prefix frontend run build`).
- [ ] Auth matrix probe confirms all caller routes return 401/403 for unauthorized requests.
- [ ] `cleanup.py scan` confirms 0 protected files (`voice_platform.db`, `.env`, models) matched.

# Cleanup Inventory & Engine Scratch Analysis (Phase C0)

**Date:** 2026-11-17  
**Scope:** Local Development Environment (macOS Apple Silicon) and Production Server (Hetzner Bare-Metal Linux)  
**Status:** Completed & Ready for Owner Sign-off  
**Tool:** [`scripts/cleanup.py`](../scripts/cleanup.py)  

---

## 1. Executive Summary

During testing, continuous development, and model execution across the stack, various temporary files, test databases, engine partials, and caches accumulate. As mandated by [`cleanup.md`](../cleanup.md), **nothing is deleted without an explicit plan, dry-run scan, and strict quarantine protection**.

This inventory provides the empirical evidence gathered via snapshot diffs, engine process inspection, git worktree audits, and server deployment analysis before any cleanup action is executed.

---

## 2. Empirical Snapshot & Diff Analysis

A full filesystem snapshot was executed before and after running the complete test suite (194 tests across unit, integration, and contract tests).

- **Baseline Snapshot:** `data/cleanup/snapshot-before-test.json` (640 tracked files, 492,705,935 bytes)
- **Post-Test Snapshot:** `data/cleanup/snapshot-after-test.json` (642 tracked files, 492,845,594 bytes)
- **Snapshot Diff Output (`scripts/cleanup.py diff`):**
  - **Created Files (6 files, 1,098,124 bytes):**
    - `data/backups/app-backup-20261117-101234-431.db` (143,360 bytes)
    - `data/backups/app-backup-20261117-101234-88248.db` (348,160 bytes)
    - `data/backups/app-backup-20261117-101234-88280.db` (348,160 bytes)
    - `data/backups/app-backup-20261117-101234-370.db` (143,360 bytes)
    - `data/cleanup/snapshot-before-test.json` (105,816 bytes)
    - `tests/unit/test_cleanup_safety.py` (9,268 bytes)
  - **Modified Files (3 files):**
    - `voice_platform.db-shm` (WAL index modified during DB writes)
    - `.pytest_cache/v/cache/nodeids` (Pytest run state)
    - `data/installed_models_manifest.json` (Atomic manifest synchronization)
  - **Deleted Files (4 files):**
    - Ephemeral test SQLite files automatically cleaned up in pytest temporary directories.

**Key Finding:** Each full test execution of backup and migration suites creates 4 new SQLite database copies in `data/backups/`. Without a retention policy, running tests repeatedly will steadily consume disk storage.

---

## 3. Engine Temporary Data Locations

We audited how each engine and service handles temporary and generated data:

| Engine / Component | Runtime & Ports | Temp / Generated Storage Location | Persistence & Risks | Proposed Policy Handling |
|---|---|---|---|---|
| **LocalAI** (LLM & STT) | Port 8080 (CPU/Metal) | `backends/*.partial`<br>`models/*.download`<br>`cache/gallery/*.yaml`<br>`data/header-cache.json` | Left behind if model downloads are cancelled or interrupted.<br>Currently contains 2 partial files (`metal-parakeet-cpp.install-tmp.partial`, `metal-sherpa-onnx.install-tmp.partial`) and 1.09 MB gallery cache. | Category D: Quarantine partials older than 24h when no install job is active.<br>Category A: Purge gallery cache on demand. |
| **local-tts** (Kokoro MLX) | Port 8880 (Apple Silicon Metal) | In-memory stream buffer | Audio chunks are streamed over HTTP/WebSocket in memory. Zero scratch audio files written to disk. Logs to `logs/local-tts.log`. | Safe. Log rotation applied to `logs/`. |
| **Streaming STT** (Sherpa-ONNX Zipformer) | Port 8088 (WebSockets) | In-memory ring buffer | Float32 PCM chunks streamed directly to Sherpa-ONNX online recognizer. Zero scratch audio written to disk unless debug recording flag is active. Model install tarball cleaned up after extraction by `scripts/download-streaming-stt-model.sh`. | Bounded memory buffer (already implemented). Debug dumps off by default. |
| **Voice Agent Gateway** (FastAPI) | Port 8000 (Uvicorn) | Root scratch files: `output.wav` (230 KB), `tts-test.py` (655 B), `voice_agent.db` (0 B).<br>`data/backups/` (24 backup `.db` files, 10.4 MB). | Manual verification scripts left in root. Backups unbounded. | Category B: Quarantine scratch scripts.<br>Category C: Quarantine audio dumps.<br>Category E: Keep newest 3 backups, quarantine older than 14 days. |

---

## 4. Current Disk Junk Inventory by Category

Evaluated against [`policies/cleanup.dev.toml`](../policies/cleanup.dev.toml):

| Category | Description | Item Count | Total Size | Sample Paths | Proposed Action |
|---|---|---|---|---|---|
| **Category A** | Regenerable caches | 54 files | 1,951,845 bytes (1.86 MB) | `cache/gallery/*.yaml`<br>`.pytest_cache/**`<br>`**/__pycache__/*.pyc` | Direct delete (no quarantine needed; safe to rebuild) |
| **Category B** | Agent & test scratch | 1 file | 655 bytes (0.00 MB) | `tts-test.py` | Quarantine to `.trash/<run-id>/` after 24h |
| **Category C** | Debug dumps & scratch audio | 1 file | 230,400 bytes (0.22 MB) | `output.wav` | Quarantine to `.trash/<run-id>/` after 2h |
| **Category D** | Engine temp partials | 2 files | 0 bytes (0.00 MB) | `backends/metal-parakeet-cpp.install-tmp.partial`<br>`backends/metal-sherpa-onnx.install-tmp.partial` | Quarantine to `.trash/<run-id>/` after 24h |
| **Category E** | Test databases & expired backups | 14 files | 2,908,160 bytes (2.77 MB) | `voice_agent.db` (0 B legacy file)<br>`data/backups/app-backup-*.db` (older than retention count) | Quarantine to `.trash/<run-id>/` |
| **Category F** | Rotated logs | 0 files | 0 bytes | `logs/*.log.*` (active logs `logs/backend.log`, etc. are protected) | Quarantine after 14 days |
| **Total Candidates** | | **72 items** | **5,091,060 bytes (~4.85 MB)** | | |

---

## 5. Protected Assets Audit (Zero-Touch Verification)

The following assets matched zero cleanup rules and are strictly safeguarded by the protected list:
- **Live Database:** `voice_platform.db` (352 KB), `voice_platform.db-wal`, `voice_platform.db-shm`
- **Model Weights:** `models/streaming-stt/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17/*` (130 MB ONNX weights)
- **Secrets & Configuration:** `.env`, `.env.example`, `pyproject.toml`, `package.json`, `uv.lock`
- **Source Code:** `app/**`, `frontend/src/**`, `scripts/**`, `services/**`, `policies/**`
- **Test Fixtures & Documentation:** `tests/fixtures/**`, `docs/**`, `AGENTS.md`
- **Active Backups:** Newest 3 database backups in `data/backups/` are permanently protected regardless of age.

---

## 6. Git Worktree Audit (Category G)

`git worktree list` identified two existing agent worktrees:
1. `/home/voiceagent/.herdr/worktrees/local-voice-agent/worktree-quiet-stone-8c75`
   - Branch: `worktree/quiet-stone-8c75`
   - Commit: `1960147` (Identical to `main`)
   - Status: Clean, nothing to commit, fully merged into `main`.
2. `/home/voiceagent/.local/share/kilo/worktree-pool/local-voice-agent-7e6de5aec9bb/green-city`
   - Commit: `29d697e` (Detached HEAD)
   - Status: Clean, nothing to commit, ancestor commit merged into `main`.

**Recommendation:** Both worktrees are clean and merged. As specified by Safety Rule #9, they will not be touched by filesystem patterns; if cleanup of worktrees is requested, they can only be pruned via `git worktree remove`.

---

## 7. Server Deployment Staging (Category H)

In [`scripts/deploy-hetzner.sh`](../scripts/deploy-hetzner.sh) and the server deployment pipeline:
- Rsync staging directories `/tmp/voice-agent` and `/tmp/fe` are created during CI/CD transfer.
- **Action:** A post-deployment hook is added to remove `/tmp/voice-agent` and `/tmp/fe` immediately upon verification of service restart, preventing `/tmp` disk buildup.

---

## 8. Retention Decisions for Owner Approval

We propose the following parameters for the cleanup policies:

| Parameter | Recommended Value | Description |
|---|---|---|
| `trash_retention_days` | **7 days** | Retention time for `.trash/<run-id>/` before `purge` deletes it. |
| `backup_retention_count` | **3 copies** | Newest database backups permanently kept in `data/backups/`. |
| `backup_retention_days` | **14 days** | Backups older than 14 days and beyond the newest 3 copies are quarantined. |
| `min_age_hours` (scratch) | **24 hours** | Minimum age for agent scratch (`tmp/agent/**`, `*.tmp`). |
| `min_age_hours` (debug dumps) | **2 hours** | Minimum age for debug audio (`*.wav`, `*.pcm`) outside fixtures. |
| `max_plan_files` | **500 files** | Safety ceiling per run. Larger plans refused without `--force-limit`. |
| `max_plan_bytes` | **2,147,483,648 bytes (2 GB)** | Safety byte ceiling per run. Refused without `--force-limit`. |

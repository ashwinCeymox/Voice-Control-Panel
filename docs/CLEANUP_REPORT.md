# Repository Cleanup & Inventory Report (`CLEANUP_REPORT.md`)
**Date:** 2026-11-19  
**Phase:** I1 (Part A: Clean the Repository)  
**Baseline Reference Tag:** `pre-installer` (Commit: `07956ea`)  
**Baseline Test Status:** 262/262 passed, 33/33 auth routes verified, 0 regressions.

---

## 1. Top-Level Inventory & Classification

| Top-Level Item | Size | Type | Classification | Rationale |
|---|---|---|---|---|
| `app/` | 1.6M | Source Code | **KEEP** | Core FastAPI gateway backend, services, providers |
| `frontend/` | 526M | Web UI | **KEEP** | React/TypeScript UI (`node_modules` & `dist` ignored) |
| `scripts/` | 164K | Scripts | **KEEP** | Deployment, start, stop, probe, regression scripts |
| `tests/` | 2.4M | Test Suite | **KEEP** | Unit, contract, and fixture tests |
| `docs/` | 1.6M | Documentation | **KEEP** | Architectural specifications and evidence logs |
| `deploy/` | 68K | Configs | **KEEP** | Caddy & environment deployment configs |
| `insights/` | 96K | Reports | **KEEP** | Readiness audit reports |
| `policies/` | 8.0K | Configs | **KEEP** | System policy definitions |
| `services/` | 28K | Engine Service | **KEEP** | Streaming STT microservice wrapper |
| `data/` | 6.4M | Runtime Data | **IGNORE** | Local SQLite platform state, baseline metrics |
| `models/` | 130M | Engine Weights | **IGNORE** | Pre-downloaded local weights (Sherpa ONNX) |
| `backends/` | 163M | Engine Libs | **IGNORE** | Pre-compiled engine shared libraries |
| `arch/` | - | External Link | **EXTERNAL** | Parent arch workspace directory |
| `cache/` | 1.1M | Runtime Cache | **IGNORE** | Hugging Face cache and temp buffers |
| `logs/` | 7.5M | Runtime Logs | **IGNORE** | Process outputs and supervisor traces |
| `.pids/` | 24K | Runtime State | **IGNORE** | Active daemon process IDs |
| `.venv/` | 127M | Environment | **IGNORE** | Python virtual environment |
| `.git/` | 42M | VCS | **KEEP** | Git repository metadata |
| `.pytest_cache/` | 40K | Build Cache | **IGNORE** | Pytest compilation cache |
| `.ruff_cache/` | 76K | Linter Cache | **IGNORE** | Ruff cache |
| `.benchmarks/` | 0B | Empty Dir | **DELETE** | Legacy empty directory removed |
| `configuration/`| 0B | Empty Dir | **DELETE** | Legacy empty directory removed |
| `downloading/`  | 0B | Empty Dir | **DELETE** | Legacy empty directory removed |
| `voice_platform.db` | 528K | SQLite DB | **IGNORE** | Primary local database (Protected Asset) |
| `voice_agent.db`| 0B | SQLite File | **IGNORE** | Legacy 0-byte file (Ignored) |
| `tmp/` | 0B | Scratch | **IGNORE** | Agent scratch directory |
| `.env` | 4.0K | Secrets | **IGNORE** | Local environment config (Protected Asset) |
| `.env.example` | 4.0K | Template | **KEEP** | Tracked environment template |
| `.DS_Store` | 20K | OS Artifact | **DELETE** | Removed via `find . -name ".DS_Store" -delete` |
| `start.sh` | 16K | Shell Script | **KEEP** | Stack launcher (Target for Part B refactor) |
| `run-pod.md` | 28K | Plan Doc | **KEEP** | User-provided installer & cleanup plan |
| `runpod.md` | 24K | Plan Doc | **KEEP** | RunPod deployment specification |

---

## 2. Tracked Binary & Generated Junk Scan
Execution command:
```bash
git ls-files | grep -E "\.(db|sqlite|db-wal|db-shm|gguf|onnx|bin|safetensors|pt|nemo|pcm|log|pid|bak|orig)$|backups/|__pycache__|\.DS_Store|node_modules|\.venv|/dist/"
```
**Result:** **0 files found** (Completely clean index). No databases, compiled models, logs, node_modules, or virtual environments are tracked in git.

---

## 3. Secrets & Personal Data Audit
1. **Secrets Scan:**
   - Fallback pattern scan `grep -rIniE "(api[_-]?key|secret|token|passw(or)?d|bearer)\s*[:=]"`:
   - Found only parameter schemas, documentation references, and default development fallback values in `app/core/config.py` (`dev-secret-key-change-me-in-production-use-high-entropy`).
   - No plaintext live user keys or tokens committed.
2. **Personal / Host Path Audit:**
   - Scan for `/Users/` and `file:///`:
   - Found only in documentation describing sanitization rules and in unit tests asserting that `/Users/` does NOT appear in runtime error messages (`test_hardening_h1_to_h11.py`).
   - 0 hardcoded personal user directories in production code.

---

## 4. `.gitignore` & `.gitattributes` Hardening
- `.gitignore` verified and updated to explicitly ignore:
  - `.run/`, `.tools/`, `.install-stamps/` (One-command installer state)
  - `node_modules/`, `frontend/dist/`, `frontend/.vite/`
  - `.venv/`, `__pycache__/`, `*.pyc`, `*.db*`, `logs/`, `.pids/`
  - `models/`, `backends/`, `cache/`, `*.gguf`, `*.onnx`, `*.nemo`
- `.gitattributes` verified:
  - `* text=auto`
  - Binary rules for `*.wav`, `*.pcm`, `*.png`, `*.wasm`, `*.onnx`, `*.gguf`, `*.bin`, `*.pt`, `*.safetensors`, `*.docx`.

---

## 5. Part A Exit Checklist
- [x] `docs/CLEANUP_REPORT.md` written and verified.
- [x] No protected assets deleted (`voice_platform.db`, `.env`, `models/`, `.venv` preserved).
- [x] App still runs on Mac native path.
- [x] `git ls-files` shows zero tracked junk, databases, logs, or model binaries.
- [x] Secrets and personal data scans completed with zero live leaks.
- [x] All 262 unit & contract tests pass cleanly.

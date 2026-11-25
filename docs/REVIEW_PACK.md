# Pre-Merge Review Pack (`feat/stt-stream-native`)

**Branch:** `feat/stt-stream-native` (vs baseline tag `bridge-relay-v1` at `0c23fe1` on `main`)  
**Status:** Ready for pod validation. **NOT pushed, NOT merged, NOT tagged.**

---

## 1. Flag-Off Proof (`STT_STREAM_NATIVE_ENABLED=false`)

When `STT_STREAM_NATIVE_ENABLED=false` (default):
- `GET /v1/models?task=stt` returns `transports: ["batch_http"]` for Parakeet models (no `ws_stream_native`), identical to `bridge-relay-v1`.
- `WS /v1/audio/transcriptions/stream` requesting `parakeet-cpp-realtime_eou_120m-v1` returns `STT_MODEL_UNAVAILABLE` (not routed to `:8089`).
- `./scripts/preflight.sh` passes without requiring port `:8089` or `STREAMING_BRIDGE_INTERNAL_TOKEN`.
- **Verified by:**
  - `tests/unit/test_streaming_bridge_gateway_routing.py::test_flag_off_before_after_model_list_and_native_request_unchanged` (**PASSED**)
  - `tests/unit/test_streaming_bridge_b6_ops.py::test_health_ready_when_bridge_disabled` (**PASSED**)

---

## 2. Security & Auth Checks

| Check | Evidence | Status |
|---|---|---|
| **Loopback bind only** | `services/streaming_bridge/server.py` (`start_server(host="127.0.0.1", port=8089)`, `verify_loopback` allows only `127.0.0.1`, `::1`, `localhost`, `testclient`) | **VERIFIED** |
| **Token mode `600`, never printed** | `scripts/generate-bridge-token.sh` sets `chmod 600 "${ENV_FILE}"` before and after writing `STREAMING_BRIDGE_INTERNAL_TOKEN` and never echoes `${TOKEN}` | **VERIFIED** |
| **Auth matrix & loopback rejection** | `tests/unit/test_streaming_bridge_service.py::test_websocket_auth_and_loopback_rejection` | **PASSED** |
| **`stream_stt.py` scope check** | `app/api/stream_stt.py` routes to `:8089` only for registry-verified `streaming_backend == "parakeet_native"` when `STT_STREAM_NATIVE_ENABLED` is enabled and calibrated | **VERIFIED** |

---

## 3. Diff Stat & Repo Hygiene Scans

- **`git diff --stat bridge-relay-v1..HEAD`:** 59+ files changed (including S5 fixes: `scripts/canary-stt-native.py`, `scripts/engine-smoke.py`, `scripts/bridge-soak-test.py`, `tests/unit/test_s5_soak_canary.py`, `docs/STREAMING_BRIDGE.md`, `docs/STREAMING_BRIDGE_CHANGES.md`, `docs/REVIEW_PACK.md`).
- **Secrets scan (`grep -rn "STREAMING_BRIDGE_INTERNAL_TOKEN=" scripts/ services/ app/ docs/`):** Matches only shell variable manipulation in `scripts/generate-bridge-token.sh`; zero hardcoded tokens.
- **Absolute path scan (`grep -rn "/Users/" scripts/ services/ app/`):** Zero matches in `scripts/`, `services/`, `app/`, and zero matches in `docs/STREAMING_BRIDGE.md`, `docs/STREAMING_BRIDGE_CHANGES.md`, `docs/REVIEW_PACK.md`. (Existing `docs/plans/pod.md`, `docs/EVIDENCE.md`, `docs/CLEANUP_REPORT.md`, `docs/handoff/H1-H11.md` only document the sanitization rule itself.)
- **Untracked scratch/spec docs NOT committed:** `WSS.md`, `agent-cl2.md`, `agent-cli3.md` remain untracked in `git status --short`.

---

## 4. Definitive Test Accounting Table

| Step | Count | Details |
|---|---:|---|
| **Pre-branch baseline (`bridge-relay-v1`, `0c23fe1`)** | **281** | `.venv/bin/pytest tests/unit/ -q --tb=line` -> `281 passed, 12 failed` (`293` collected) |
| **− S1/S2/S3 removed/replaced relay tests** | **−30** | `test_streaming_bridge_b8_load_soak.py` (8), `test_streaming_bridge_core.py` (7), `test_streaming_bridge_scheduler.py` (9), `test_streaming_bridge_service.py` (5 relay tests), `test_streaming_bridge_b7_ui_api.py` (1 renamed) |
| **+ S2 additions** | **+18** | `test_streaming_bridge_engines.py` (5), `test_parakeet_native_engine.py` (4), `test_streaming_bridge_service.py` (9) |
| **+ S3 additions** | **+2** | `test_streaming_bridge_b7_ui_api.py` (1 renamed), `test_streaming_bridge_gateway_routing.py` (1) |
| **+ S4 additions** | **+2** | `test_streaming_bridge_b6_ops.py` (2) |
| **+ S5 additions (incl. post-review fixes)** | **+10** | `test_s5_soak_canary.py` (10: canary WAV/overlap, expected-text source match, engine-smoke compile, soak pass/fail/latency, start msg, session leak, idle timeout, docs/scripts) |
| **= `feat/stt-stream-native` HEAD total** | **283** | `283 passed, 12 failed` (`295` collected; zero duplicate test names; zero tracked `.pyc`/`__pycache__`) |

### The 12 Known Failures (Identical on `bridge-relay-v1` and `feat/stt-stream-native`)

All 12 require a live Sherpa/LocalAI socket (blocked in offline macOS sandbox with `PermissionError: [Errno 1] Operation not permitted` / `503 StreamingUnavailableError`):
1. `tests/unit/test_mid_fix_s1_to_s4.py::test_streaming_client_raises_404_when_model_not_loaded`
2. `tests/unit/test_mid_fix_s1_to_s4.py::test_preflight_validates_loaded_model_or_raises`
3. `tests/unit/test_s2_streaming_adapter.py::test_streaming_client_health`
4. `tests/unit/test_s2_streaming_adapter.py::test_streaming_client_stream_flow`
5. `tests/unit/test_s2_streaming_adapter.py::test_gateway_health_ready_includes_streaming_stt`
6. `tests/unit/test_s3_streaming_endpoint.py::test_ws_stream_success_with_admin_ticket`
7. `tests/unit/test_streaming_stt_fallback_and_models.py::test_streaming_stt_parakeet_realtime_scene_accepted`
8. `tests/unit/test_streaming_stt_fallback_and_models.py::test_streaming_stt_valid_model_returns_telemetry_fields`
9. `tests/unit/test_streaming_stt_fallback_and_models.py::test_streaming_client_handshake_rejection_raises_404`
10. `tests/unit/test_streaming_stt_nemo.py::test_streaming_stt_client_preflight_nemo`
11. `tests/unit/test_streaming_stt_nemo.py::test_nemo_streaming_websocket_accepted`
12. `tests/unit/test_streaming_stt_nemo.py::test_batch_model_rejected_on_streaming_websocket`

---

## 5. Pod Validation Status (NOT RUN on macOS Host)

- `scripts/engine-smoke.py`: **NOT RUN on macOS** (requires Linux `libparakeet.so` ABI 11 + GGUF model).
- `./scripts/bridge-calibrate.sh --model parakeet-cpp-realtime_eou_120m-v1`: **NOT RUN on macOS** (expected on current 4.59-core RunPod pod `d17b5d2691b9`: **FAIL, `admitted_streams=0`**, native streams refused).
- `scripts/bridge-soak-test.py`: **NOT RUN on macOS** (unit-tested with canned RSS/session/latency data in `test_s5_soak_canary.py`).
- `scripts/canary-stt-native.py`: **NOT RUN on macOS** (WAV loading, chunking, and word-overlap logic unit-tested in `test_s5_soak_canary.py`; live output will be pasted from the pod run).

# Native Streaming STT Changes & Operations Guide (`feat/stt-stream-native`)

This document replaces the archived simulated HTTP-relay design with the verified facts for the **Native Parakeet Streaming STT** service on port `:8089`. See [STREAMING_BRIDGE.md](./STREAMING_BRIDGE.md) for the service reference and [REVIEW_PACK.md](./REVIEW_PACK.md) for pre-merge verification evidence.

---

## 1. Summary of Changes (`bridge-relay-v1` → `feat/stt-stream-native`)

### Archived (`bridge-relay-v1` at `0c23fe1` on `main`)
- Removed LocalAI HTTP re-decode relay loop (`aligner.py`, `audio_utils.py`, `fake_localai.py`, `priority_queue.py`, `session.py`)
- Removed relay latency probe & benchmark harness (`scripts/measure_bridge_latency.py`) and relay-coupled unit tests (`test_streaming_bridge_b8_load_soak.py`, `test_streaming_bridge_core.py`, `test_streaming_bridge_scheduler.py`, plus 5 relay tests in `test_streaming_bridge_service.py`)
- Removed `"Live (simulated)"` UI copy and `ws_stream_bridge` transport naming

### Added (`feat/stt-stream-native`)
- **Engine abstraction ([`services/streaming_bridge/engines/`](../services/streaming_bridge/engines/)):** `StreamingEngine` (`base.py`), `FakeStreamingEngine` (`fake_engine.py`), `ParakeetNativeEngine` (`parakeet_native.py`), and `ParakeetWorkerBinding` (`parakeet_worker.py`).
- **Native C API v11 integration:** Loads `libparakeet.so` (ABI `11`) via packaged `ld.so --library-path <lib_dir>` on Linux in a separate worker subprocess, converting 16 kHz PCM-16 to `float32` and merging incremental partials with `finalize` tail.
- **Voice-turn protection & catch-up:** `BridgeScheduler` pauses chunk feeding during active LLM/TTS voice turns into a bounded buffer (`STREAMING_NATIVE_PAUSE_MAX_CHUNKS=40`) and replays at `STREAMING_NATIVE_CATCHUP_RATE=2.0`, emitting `stream.paused` / `stream.resumed`.
- **Host-gated calibration:** [`scripts/bridge-calibrate.sh`](../scripts/bridge-calibrate.sh) records host capacity in `data/bridge_calibration_<fingerprint>_<model>.json`.
- **Registry, gateway flag & UI:** Models expose `transports: ["batch_http", "ws_stream_native"]` and `streaming_backend: "parakeet_native"` (Sherpa uses `"sherpa"`), gated by `STT_STREAM_NATIVE_ENABLED` (default `false`) and `/v1/admin/settings/stt`. UI selectors show **`Live (native)`**.

---

## 2. Limitations & Known Issues

- **Initial S0-approved model only:** Only **`parakeet-cpp-realtime_eou_120m-v1`** is enabled. `parakeet-cpp-realtime-scene-speakers` (requires separate scene API) and uninstalled `nemotron` remain excluded.
- **Undersized CPU hosts fail calibration:** On the 4.59-effective-core RunPod container (`d17b5d2691b9`), S0b showed `OMP_NUM_THREADS=1` costs ~90 ms per 160 ms chunk and fails the strict CPU pass rule. Calibration on that pod records `FAIL` (`admitted_streams=0`) and refuses native streams.
- **No automatic fallback to batch:** When native streaming is uncalibrated or unavailable, the gateway refuses the stream with a clear error rather than silently falling back to `batch_http`.
- **Pod validation NOT RUN on macOS host:** `scripts/engine-smoke.py`, `./scripts/bridge-calibrate.sh`, `scripts/bridge-soak-test.py`, and `scripts/canary-stt-native.py` live runs require Linux `libparakeet.so` and are **NOT RUN on macOS**.

---

## 3. Security

- **Loopback bind only:** `services/streaming_bridge/server.py` binds strictly to `127.0.0.1:8089` and rejects non-loopback client hosts.
- **Shared internal token:** [`scripts/generate-bridge-token.sh`](../scripts/generate-bridge-token.sh) writes `STREAMING_BRIDGE_INTERNAL_TOKEN` to `.env` with `chmod 600` and never prints or logs the token.
- **Scope:** FastAPI gateway (`app/api/stream_stt.py`) routes to `:8089` only when `STT_STREAM_NATIVE_ENABLED` is enabled, calibrated (`admitted_streams >= 1`), and the model's registry `streaming_backend` is `"parakeet_native"`.

---

## 4. Configuration Table

| Key | Default | Description |
|---|---|---|
| `STT_STREAM_NATIVE_ENABLED` | `false` | Master feature flag for native `:8089` streaming (alias: `STREAMING_BRIDGE_ENABLED`). |
| `STREAMING_BRIDGE_PORT` | `8089` | Internal loopback port for `services.streaming_bridge.server`. |
| `STREAMING_BRIDGE_INTERNAL_TOKEN` | Generated once | Shared secret in `.env` (mode `600`). |
| `STREAMING_BRIDGE_MAX_STREAMS` | `5` | Upper stream cap (clamped by calibrated `admitted_streams`). |
| `STREAMING_BRIDGE_IDLE_TIMEOUT_S` | `30.0` | Idle WebSocket timeout in seconds. |
| `STREAMING_BRIDGE_MAX_STREAM_S` | `600.0` | Maximum stream session duration in seconds. |
| `STREAMING_NATIVE_EXPECTED_ABI` | `11` | Required `libparakeet.so` C ABI version. |
| `STREAMING_NATIVE_OMP_NUM_THREADS` | unset | Optional OpenMP thread cap passed to `parakeet_worker`. |
| `STREAMING_NATIVE_GOMP_WAIT_POLICY` | unset | Optional `GOMP_WAIT_POLICY` (e.g., `PASSIVE`) passed to worker. |
| `STREAMING_NATIVE_OMP_PROC_BIND` | unset | Optional `OMP_PROC_BIND` passed to worker. |
| `STREAMING_NATIVE_WORKER_NICE` | unset | Optional POSIX `nice` adjustment applied in worker. |
| `STREAMING_NATIVE_CPU_AFFINITY` | `""` | Optional CPU affinity list passed to worker. |
| `STREAMING_NATIVE_PAUSE_MAX_CHUNKS` | `40` | Max buffered chunks during a paused voice turn. |
| `STREAMING_NATIVE_CATCHUP_RATE` | `2.0` | Replay multiplier when draining buffered audio after pause. |

---

## 5. Pod Validation & Enablement Runbook

1. **Prerequisites:** Linux host with `backends/cpu-parakeet-cpp/lib/libparakeet.so` (ABI `11`) and `models/parakeet-cpp/realtime_eou_120m-v1-f16.gguf`.
2. **Generate loopback token (once, mode `600`):** `./scripts/generate-bridge-token.sh`
3. **Enable flag & restart only bridge + gateway (PID-only — do NOT use `./start.sh restart` as it may restart LocalAI):**
   ```bash
   ./scripts/stop-bridge.sh
   ./scripts/start-bridge.sh
   kill -TERM "$(cat .run/gateway.pid)" && sleep 1
   .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 &
   echo $! > .run/gateway.pid
   ```
4. **Pod validation order (all NOT RUN on macOS host):**
   1. **Engine smoke:** `.venv/bin/python scripts/engine-smoke.py`
   2. **Calibrate:** `./scripts/bridge-calibrate.sh --model parakeet-cpp-realtime_eou_120m-v1` (expected on current 4.59-core pod: **FAIL, `admitted_streams=0`**, streams refused).
   3. **Soak (1 stream):** `.venv/bin/python scripts/bridge-soak-test.py --streams 1 --duration-s 30`
   4. **Canary:** `.venv/bin/python scripts/canary-stt-native.py --gateway-url ws://127.0.0.1:8000 --model parakeet-cpp-realtime_eou_120m-v1` *(output will be pasted from the pod run)*.
   5. **Soak at admitted count:** `.venv/bin/python scripts/bridge-soak-test.py --streams <admitted_streams> --duration-s 600`

### Rollback
- **Runtime:** Set `stt_stream_native_enabled: false` via `PUT /v1/admin/settings/stt`, persist `STT_STREAM_NATIVE_ENABLED=false` in `.env`, and run `./scripts/stop-bridge.sh`.
- **Git:** `./scripts/stop-bridge.sh && git switch main` (or `git switch --detach bridge-relay-v1`).

---

## 6. Protocol

- **Client → Gateway (`WS /v1/audio/transcriptions/stream?api_key=...`):**
  1. JSON `{"type": "stream.start", "model": "...", "language": "en"}`
  2. Binary 16 kHz mono PCM-16 frames (160 ms = 5120 bytes)
  3. JSON `{"type": "audio.commit"}`
- **Bridge/Gateway → Client:** `stream.paused`, `stream.resumed`, `transcript.partial`, `transcript.completed`, or `error`.

---

## 7. Key Decisions

- **Process-isolated `ld.so` worker:** Prevents `libparakeet.so` ABI/C++ runtime conflicts and isolates worker crashes to affected streams without crashing the bridge daemon.
- **No hard-coded `OMP_NUM_THREADS=1`:** Kept configurable via `STREAMING_NATIVE_OMP_NUM_THREADS` because `OMP=1` trades stream speed (~90 ms vs ~19 ms/chunk) for voice protection.
- **Strict calibration gate:** Hosts failing the S0b CPU rule record `admitted_streams=0` and refuse streams rather than degrading live voice turns.

---

## 8. Future Work

- Run pod validation sequence on a host with sufficient CPU/GPU capacity (`admitted_streams >= 1`).
- Evaluate `parakeet-cpp-realtime-scene-speakers` and `nemotron` once installed and calibrated on the target host.

---

## 9. File Manifest (Key Native Streaming Files)

- Engine & worker: [`services/streaming_bridge/engines/base.py`](../services/streaming_bridge/engines/base.py), [`fake_engine.py`](../services/streaming_bridge/engines/fake_engine.py), [`parakeet_native.py`](../services/streaming_bridge/engines/parakeet_native.py), [`parakeet_worker.py`](../services/streaming_bridge/engines/parakeet_worker.py)
- Bridge & scheduler: [`services/streaming_bridge/server.py`](../services/streaming_bridge/server.py), [`calibrate.py`](../services/streaming_bridge/calibrate.py), [`scheduler/bridge_scheduler.py`](../services/streaming_bridge/scheduler/bridge_scheduler.py)
- Gateway & registry: [`app/core/config.py`](../app/core/config.py), [`app/services/streaming_bridge_client.py`](../app/services/streaming_bridge_client.py), [`app/services/model_registry.py`](../app/services/model_registry.py), [`app/providers/streaming_stt_client.py`](../app/providers/streaming_stt_client.py), [`app/api/stream_stt.py`](../app/api/stream_stt.py)
- Ops & validation scripts: [`scripts/engine-smoke.py`](../scripts/engine-smoke.py), [`scripts/bridge-calibrate.sh`](../scripts/bridge-calibrate.sh), [`scripts/bridge-soak-test.py`](../scripts/bridge-soak-test.py), [`scripts/canary-stt-native.py`](../scripts/canary-stt-native.py), [`scripts/start-bridge.sh`](../scripts/start-bridge.sh), [`scripts/stop-bridge.sh`](../scripts/stop-bridge.sh)
- Unit tests: [`tests/unit/test_streaming_bridge_engines.py`](../tests/unit/test_streaming_bridge_engines.py), [`test_parakeet_native_engine.py`](../tests/unit/test_parakeet_native_engine.py), [`test_streaming_bridge_service.py`](../tests/unit/test_streaming_bridge_service.py), [`test_streaming_bridge_gateway_routing.py`](../tests/unit/test_streaming_bridge_gateway_routing.py), [`test_streaming_bridge_b6_ops.py`](../tests/unit/test_streaming_bridge_b6_ops.py), [`test_streaming_bridge_b7_ui_api.py`](../tests/unit/test_streaming_bridge_b7_ui_api.py), [`test_s5_soak_canary.py`](../tests/unit/test_s5_soak_canary.py)

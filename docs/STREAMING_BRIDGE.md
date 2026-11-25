# Native Streaming STT Service Documentation (`:8089`)

> For migration history, archived relay components (`bridge-relay-v1`), and rollback steps, see [STREAMING_BRIDGE_CHANGES.md](./STREAMING_BRIDGE_CHANGES.md).

The Native Streaming STT service runs alongside LocalAI on loopback port `8089` and streams audio directly through an isolated `parakeet.cpp` C ABI (`ABI 11`) worker process for eligible models (`parakeet-cpp-realtime_eou_120m-v1`), while Sherpa-ONNX continues handling online Zipformer models on `:8088`.

---

## 1. Architecture Overview

```
                                 ┌──────────────────────────────────────────┐
                                 │     Native Streaming STT (:8089)         │
                                 │                                          │
Browser / Client ──WebSocket──▶  │  1. Loopback & X-Bridge-Token Auth       │
  (/v1/audio/transcriptions/     │  2. Host Calibration & Stream Cap Gate   │
   stream via FastAPI Gateway)   │  3. Voice-Turn Pause / <=2x Catch-Up     │
                                 │  4. ParakeetNativeEngine Worker (ABI 11) │
                                 └────────────────────┬─────────────────────┘
                                                      │ ctypes / packaged ld.so
                                                      ▼
                                       ┌─────────────────────────────┐
                                       │ libparakeet.so (EOU 120M)   │
                                       └─────────────────────────────┘
```

### Core Design Principles
1. **Loopback & Internal Token Only:** Listens strictly on `127.0.0.1:8089` and requires `STREAMING_BRIDGE_INTERNAL_TOKEN` (`X-Bridge-Token`).
2. **Flag Off by Default (`STT_STREAM_NATIVE_ENABLED=false`):** With the flag off, gateway `/v1/models` and WebSocket routing preserve baseline behavior completely, and `./scripts/preflight.sh` does not require port `8089` or an internal token.
3. **Registry-Driven Routing:** Models expose `transports` (`ws_stream_native`) and `streaming_backend` (`"parakeet_native"` or `"sherpa"`). The gateway routes strictly by registry metadata.
4. **Voice-Turn Protection & Bounded Catch-Up:** When `/internal/voice-signal` indicates an active LLM/TTS turn, chunk feeding pauses into a bounded buffer (`STREAMING_NATIVE_PAUSE_MAX_CHUNKS`) and replays at up to `STREAMING_NATIVE_CATCHUP_RATE` afterward so voice turns are not starved and audio is not dropped.
5. **Host-Gated Calibration:** Models must pass `./scripts/bridge-calibrate.sh --model <id>` on the host (`admitted_streams >= 1`) before the gateway exposes `ws_stream_native`.

---

## 2. Configuration Keys

| Key | Default | Description |
|---|---|---|
| `STT_STREAM_NATIVE_ENABLED` | `false` | Master feature flag for native `:8089` streaming (alias: `STREAMING_BRIDGE_ENABLED`). |
| `STREAMING_BRIDGE_PORT` | `8089` | Internal loopback port for `services.streaming_bridge.server`. |
| `STREAMING_BRIDGE_INTERNAL_TOKEN` | Generated once | Shared secret stored in `.env` (mode `600`). |
| `STREAMING_BRIDGE_MAX_STREAMS` | `5` | Upper bound (clamped by calibrated `admitted_streams`). |
| `STREAMING_BRIDGE_IDLE_TIMEOUT_S` | `30.0` | Idle WebSocket timeout in seconds. |
| `STREAMING_BRIDGE_MAX_STREAM_S` | `600.0` | Maximum stream session duration in seconds. |
| `STREAMING_NATIVE_EXPECTED_ABI` | `11` | Required `libparakeet.so` C ABI version. |
| `STREAMING_NATIVE_OMP_NUM_THREADS` | unset | Optional OpenMP thread cap passed to `parakeet_worker`. |
| `STREAMING_NATIVE_GOMP_WAIT_POLICY` | unset | Optional `GOMP_WAIT_POLICY` (e.g., `PASSIVE`) passed to worker. |
| `STREAMING_NATIVE_WORKER_NICE` | unset | Optional POSIX `nice` adjustment applied in worker. |
| `STREAMING_NATIVE_PAUSE_MAX_CHUNKS` | `40` | Max buffered chunks during a paused voice turn. |
| `STREAMING_NATIVE_CATCHUP_RATE` | `2.0` | Replay multiplier when draining buffered audio after pause. |

---

## 3. Host Calibration & Admission

```bash
./scripts/bridge-calibrate.sh --model parakeet-cpp-realtime_eou_120m-v1
```

Writes `data/bridge_calibration_<fingerprint>_<model>.json`. If host resources fail the S0b CPU pass rule (for example, undersized multi-tenant containers), `admitted_streams` is recorded as `0` (`FAIL`) and native streaming stays safely disabled for that host.

---

## 4. UI Indicators & Runtime Toggle

All STT selectors (Models Registry, Assistant, Agents, STT Playground) use unified capability metadata:

| Label | `streaming_backend` | Transport |
|---|---|---|
| `Live (native)` | `"sherpa"` | `ws_stream_native` (`:8088`) |
| `Live (native)` | `"parakeet_native"` | `ws_stream_native` (`:8089`) |
| Batch-only | `null` | `batch_http` |

Toggle live without restarting the gateway in **Developer Settings** or via `PUT /v1/admin/settings/stt` (`{"stt_stream_native_enabled": true|false}`).

---

## 5. Operations & Script Management

- **Preflight check (passes when flag is off):** `./scripts/preflight.sh`
- **Engine smoke (pod-only, NOT RUN on macOS):** `.venv/bin/python scripts/engine-smoke.py`
- **Standalone start / PID-only stop (do NOT use `./start.sh restart` on pod):** `./scripts/start-bridge.sh` and `./scripts/stop-bridge.sh` (stops strictly via `.run/streaming-bridge.pid` and `.pids/streaming-bridge.pid`, never kills port `8089` blindly)
- **Canary (pod-only, NOT RUN on macOS):** `.venv/bin/python scripts/canary-stt-native.py --model parakeet-cpp-realtime_eou_120m-v1`
- **Soak test (pod-only, NOT RUN on macOS):** `.venv/bin/python scripts/bridge-soak-test.py --model parakeet-cpp-realtime_eou_120m-v1 --streams 1 --duration-s 30`

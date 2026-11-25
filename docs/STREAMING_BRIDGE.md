# Universal Streaming STT Bridge Documentation

The Streaming STT Bridge is a high-efficiency simulated streaming speech-to-text service that sits alongside LocalAI. It bridges batch STT models (such as Parakeet and Whisper) into real-time streaming speech recognition with live partials, monotonic text alignment, priority scheduling, and host-fingerprint calibration.

---

## 1. Architecture Overview

```
                                 ┌──────────────────────────────────────────┐
                                 │       Streaming Bridge Daemon (:8089)    │
                                 │                                          │
Browser / Client ──WebSocket──▶ │  1. Loopback & Token Auth                │
  (/v1/audio/transcriptions/     │  2. Host Fingerprint Calibration Gate     │
   stream via Gateway)           │  3. Bounded Audio Buffer (16 kHz PCM)    │
                                 │  4. Priority Queue (Finals > Partials)   │
                                 │  5. Aligner (Confirmed Monotonic Prefix) │
                                 └────────────────────┬─────────────────────┘
                                                      │ HTTP /v1/audio/transcriptions
                                                      ▼
                                       ┌─────────────────────────────┐
                                       │   LocalAI Backend (:8080)   │
                                       │ (Parakeet / Whisper Models) │
                                       └─────────────────────────────┘
```

### Core Design Principles
1. **Loopback & Internal Token Only:** Listens exclusively on `127.0.0.1` and requires `STREAMING_BRIDGE_INTERNAL_TOKEN` (via `X-Bridge-Token` header). Never accessible externally.
2. **Flag Off by Default:** `STREAMING_BRIDGE_ENABLED=false`. When disabled, the gateway preserves baseline behavior with 0 changes or overhead.
3. **Fairness & Concurrency Caps:** Global `MAX_DECODES` semaphore (default 1) prevents overloading CPU/GPU and starves neither LLM nor TTS. Single in-flight per stream skips intermediate partials (latest wins).
4. **Yield-to-Voice Coordination:** When a voice turn (LLM completion or TTS audio synthesis) starts, the bridge is signaled via `/internal/voice-signal`. Partials yield while voice is active, resuming after completion or a bounded pause cap (default 3000ms). Finals are never blocked.
5. **Host-Gated Calibration:** A model cannot be streamed until an offline calibration pass (`scripts/bridge-calibrate.sh`) has run on the specific host hardware, verified acoustic latency, and generated a signed fingerprint calibration record.

---

## 2. Configuration Keys

| Key | Default | Description |
|---|---|---|
| `STREAMING_BRIDGE_ENABLED` | `false` | Master toggle. When false, bridge path is completely disabled. |
| `STREAMING_BRIDGE_PORT` | `8089` | Internal loopback port. Auto-migrates if port is busy. |
| `STREAMING_BRIDGE_INTERNAL_TOKEN` | Generated once | Secret token used by gateway to authenticate loopback requests. |
| `STREAMING_BRIDGE_STEP_MS` | `1000` | Cadence between recurrent partial decode attempts. |
| `STREAMING_BRIDGE_MIN_WINDOW_MS` | `1200` | Minimum audio duration required before the first partial decode. |
| `STREAMING_BRIDGE_SEGMENT_MAX_MS` | `15000` | Hard cap on audio segment length before forcing a segment cut. |
| `STREAMING_BRIDGE_SEGMENT_SILENCE_MS`| `600` | Trailing silence duration triggering an automatic segment finalization. |
| `STREAMING_BRIDGE_MAX_STREAMS` | `5` | Maximum concurrent active client WebSocket streams. |
| `STREAMING_BRIDGE_MAX_DECODES` | `1` | Maximum parallel in-flight decodes dispatched to LocalAI. |
| `STREAMING_BRIDGE_PARTIAL_TIMEOUT_S` | `8.0` | Timeout for intermediate partial transcription requests. |
| `STREAMING_BRIDGE_FINAL_TIMEOUT_S` | `30.0` | Timeout for segment final / commit transcription requests. |
| `STREAMING_BRIDGE_FALLBACK_TO_BATCH` | `false` | Explicit failover to batch STT when bridge is unavailable. |

---

## 3. Host Calibration & Admission

To ensure real-time performance and prevent degradation of LLM or TTS services, models must pass an automated host benchmark:

```bash
# Run calibration on a model
./scripts/bridge-calibrate.sh --model parakeet-cpp-realtime-scene
```

### What Calibration Verifies:
1. **Decode Latency:** Measures decode time across 2s, 4s, 6s, 10s, and 15s audio windows.
2. **Voice Interference:** Runs concurrent LLM and TTS voice turns during STT decodes to verify LLM latency does not degrade by >10%.
3. **Admitted Streams:** Calculates maximum safe concurrent streams ($N_{\text{admitted}}$) supported by the host.
4. **Signed Fingerprint:** Generates `data/bridge_calibration_<fingerprint>_<model>.json`. If host hardware changes (CPU cores, RAM, GPU), the calibration must be re-run.

---

## 4. UI Indicators & Badges

The user interface distinguishes between true native neural streaming and simulated streaming:

| Badge / Label | Engine / Transport | Description |
|---|---|---|
| `Live (native)` | Sherpa-ONNX (`ws_stream_native`) | True online streaming neural ASR (Zipformer). Lowest compute overhead, character-by-character monotonic emission. |
| `Live (simulated)` | Streaming Bridge (`ws_stream_bridge`) | Recurrent LocalAI batch decodes with aligner prefix stabilization. Supported on Parakeet and Whisper models. |
| `[Disabled: BRIDGE_UNAVAILABLE]` | Streaming Bridge | Shown when a simulated model is selected but the bridge daemon is down, disabled, or uncalibrated for the host. |

### Runtime Kill Switch
Administrators can toggle bridge routing live without restarting the gateway via:
- **Developer Settings:** Toggle under "Streaming STT & Simulated Bridge".
- **REST API:** `PUT /v1/admin/settings/stt` with `{"streaming_bridge_enabled": false}`.

---

## 5. Operations & Script Management

- **Start Bridge Daemon:**
  ```bash
  ./scripts/start-bridge.sh
  # Or via unified launcher:
  ./start.sh up
  ```
- **Stop Bridge Daemon:**
  ```bash
  ./scripts/stop-bridge.sh
  # Uses recorded PID in .run/streaming-bridge.pid only; never kills by port.
  ```
- **Run Soak & Concurrency Test:**
  ```bash
  ./scripts/bridge-soak-test.py --streams 5 --duration-s 30
  ```

---

## 6. Troubleshooting

### 1. `BRIDGE_UNAVAILABLE` in UI Dropdowns
- **Cause:** `STREAMING_BRIDGE_ENABLED` is `false` or the model has not passed calibration.
- **Fix:**
  1. Toggle bridge on in Developer Settings or `.env` (`STREAMING_BRIDGE_ENABLED=true`).
  2. Run calibration: `./scripts/bridge-calibrate.sh --model <model-name>`.

### 2. `BRIDGE_BUSY` (HTTP / WS 1013)
- **Cause:** Active concurrent streams reached `STREAMING_BRIDGE_MAX_STREAMS` or the model's calibrated limit.
- **Fix:** The bridge sends `retry_after_ms: 2000`. The client should retry after the specified backoff.

### 3. Missing `X-Bridge-Token` or 401/1008
- **Cause:** Direct unauthorized connection attempt or missing token.
- **Fix:** Ensure `STREAMING_BRIDGE_INTERNAL_TOKEN` in `.env` is populated and passed in the `X-Bridge-Token` header.

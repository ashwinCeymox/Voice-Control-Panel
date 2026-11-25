# Native Streaming STT Changes & Operations Guide (`feat/stt-stream-native`)

This document replaces the archived simulated HTTP-relay design with the verified facts for the **Native Parakeet Streaming STT** service on port `:8089`.

---

## 1. What Is Archived (`bridge-relay-v1`)

The original LocalAI HTTP re-decode relay design is preserved intact at git tag **`bridge-relay-v1`** (on branch **`main`**, commit `0c23fe1`) and removed from `feat/stt-stream-native`:

- Removed relay engine and HTTP re-decode loop (`aligner.py`, `audio_utils.py`, `fake_localai.py`, `priority_queue.py`, `session.py`)
- Removed relay latency probe & benchmark harness (`scripts/measure_bridge_latency.py`) and relay-coupled unit tests
- Removed `"Live (simulated)"` UI copy and `ws_stream_bridge` transport naming

---

## 2. What Is New (`feat/stt-stream-native`)

| Component | Summary |
|---|---|
| **Engine abstraction (`services/streaming_bridge/engines/`)** | `StreamingEngine` interface (`base.py`), `FakeStreamingEngine` (`fake_engine.py`), and `ParakeetNativeEngine` (`parakeet_native.py`) + isolated worker (`parakeet_worker.py`). |
| **Native C API v11 integration** | Drives `libparakeet.so` (ABI `11`, loaded via packaged `ld.so` + library directory on Linux) directly over 16 kHz PCM16 -> `float32`, emitting incremental partials and un-flushed finalize tails cleanly. |
| **Initial S0-approved model** | **`parakeet-cpp-realtime_eou_120m-v1`** only. Multi-model `parakeet-cpp-realtime-scene-speakers` (requires separate scene API) and uninstalled `nemotron` remain excluded until installed and verified on a target pod. |
| **Voice-turn protection & catch-up** | Configurable bounded audio buffer (`STREAMING_NATIVE_PAUSE_MAX_CHUNKS=40`, `STREAMING_NATIVE_CATCHUP_RATE=2.0`) pauses feed during active LLM/TTS voice turns and catches up afterward without dropping audio, emitting `stream.paused` / `stream.resumed`. |
| **Host-gated calibration** | `./scripts/bridge-calibrate.sh` measures voice turn interference + stream chunk budgets per host hardware fingerprint (`data/bridge_calibration_<fingerprint>_<model>.json`). Undersized CPU hosts (such as the 4.6-core S0b RunPod container) record `admitted_streams=0` (`FAIL`) and refuse native streams automatically. |
| **Registry & gateway flag** | Eligible models advertise `transports: ["batch_http", "ws_stream_native"]` and `streaming_backend: "parakeet_native"` (Sherpa uses `"sherpa"`). Controlled by **`STT_STREAM_NATIVE_ENABLED`** (default `false`) and runtime `/v1/admin/settings/stt` toggle. |
| **UI labels** | Assistant, Agents, Models Registry, and STT Playground show **`Live (native)`** with zero `"simulated"` wording. |

---

## 3. How to Enable

1. **Prerequisites (Linux host with Parakeet native backend & sufficient CPU/GPU capacity):**
   - `backends/cpu-parakeet-cpp/lib/libparakeet.so` (ABI `11`) and `parakeet-cpp-realtime_eou_120m-v1` YAML/GGUF in `models/`.
2. **Generate the loopback shared token (once, mode `600` in `.env`):**
   ```bash
   ./scripts/generate-bridge-token.sh
   ```
3. **Run host calibration (required before the gateway routes any model to `:8089`):**
   ```bash
   ./scripts/bridge-calibrate.sh --model parakeet-cpp-realtime_eou_120m-v1
   ```
   Confirm `admitted_streams >= 1` in `data/bridge_calibration_<fingerprint>_parakeet-cpp-realtime_eou_120m-v1.json`.
4. **Enable the feature flag:**
   - Set `STT_STREAM_NATIVE_ENABLED=true` in `.env` (legacy alias `STREAMING_BRIDGE_ENABLED=true` is still accepted), **or**
   - Toggle live via **Developer Settings** in the UI or:
     ```bash
     curl -X PUT http://127.0.0.1:8000/v1/admin/settings/stt \
       -H "Authorization: Bearer <ADMIN_API_KEY>" \
       -H "Content-Type: application/json" \
       -d '{"stt_stream_native_enabled": true}'
     ```
5. **Start the service (PID-only management):**
   ```bash
   ./scripts/preflight.sh
   ./start.sh restart          # unified launcher (starts :8089 when enabled)
   # or standalone:
   ./scripts/start-bridge.sh
   ```

---

## 4. Calibrating & Tuning Worker Resources

- **Calibration command:**
  ```bash
  ./scripts/bridge-calibrate.sh --model parakeet-cpp-realtime_eou_120m-v1
  ```
- **Worker thread & scheduling env vars (`.env`):**
  - `STREAMING_NATIVE_OMP_NUM_THREADS` — unset/default lets OpenMP use available threads (~19 ms/chunk in S0); setting `1` slows chunks (~90 ms/chunk) in exchange for isolating CPU cores.
  - `STREAMING_NATIVE_GOMP_WAIT_POLICY` (e.g., `PASSIVE`), `STREAMING_NATIVE_OMP_PROC_BIND`, `STREAMING_NATIVE_WORKER_NICE`, `STREAMING_NATIVE_CPU_AFFINITY`.
  - `STREAMING_NATIVE_PAUSE_MAX_CHUNKS` (default `40`) and `STREAMING_NATIVE_CATCHUP_RATE` (default `2.0`).

---

## 5. Rollback Procedures

### Runtime / Config Rollback (zero git changes)
1. Disable via API or UI (**Developer Settings**):
   ```bash
   curl -X PUT http://127.0.0.1:8000/v1/admin/settings/stt \
     -H "Authorization: Bearer <ADMIN_API_KEY>" \
     -H "Content-Type: application/json" \
     -d '{"stt_stream_native_enabled": false}'
   ```
2. Persist `STT_STREAM_NATIVE_ENABLED=false` (and `STREAMING_BRIDGE_ENABLED=false` if set) in `.env`.
3. Stop the `:8089` daemon cleanly via PID file (never kill by port):
   ```bash
   ./scripts/stop-bridge.sh
   ```
   When both flags are `false`, `./scripts/preflight.sh` passes without requiring `:8089` or `STREAMING_BRIDGE_INTERNAL_TOKEN`, and `/v1/models` + `WS /v1/audio/transcriptions/stream` remain unchanged on baseline.

### Full Git Rollback (to pre-native relay or `main`)
```bash
./scripts/stop-bridge.sh
git switch main                     # or: git switch --detach bridge-relay-v1
```

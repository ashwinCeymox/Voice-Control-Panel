# Pod Validation Runbook (`feat/stt-stream-native`)

Execute the steps below **in exact order** on the Linux pod. Do **not** run `./start.sh restart` and do **not** touch or restart LocalAI.

---

## Step 1: Pull `feat/stt-stream-native`

- **Command:**
  ```bash
  git fetch orgin && git switch feat/stt-stream-native && git pull orgin feat/stt-stream-native
  ```
- **Purpose:** Check out the latest `feat/stt-stream-native` branch on the pod without altering `main` or `.env`.
- **Expected output:** `Switched to branch 'feat/stt-stream-native'` (or `Already on 'feat/stt-stream-native'`) and `Already up to date.` / fast-forward summary.
- **Pass criteria:** `git rev-parse --short HEAD` matches the branch HEAD and `git status --short` shows no unexpected tracked modifications.
- **Paste back:** `git rev-parse --short HEAD` and `git status --short`.

---

## Step 2: Configure `.env` & Generate Loopback Token

- **Command:**
  ```bash
  # Ensure STT_STREAM_NATIVE_ENABLED=true is set in .env (do NOT print .env or the token)
  grep -q "^STT_STREAM_NATIVE_ENABLED=" .env \
    && sed -i 's/^STT_STREAM_NATIVE_ENABLED=.*/STT_STREAM_NATIVE_ENABLED=true/' .env \
    || echo "STT_STREAM_NATIVE_ENABLED=true" >> .env

  ./scripts/generate-bridge-token.sh
  ```
- **Purpose:** Enable native `:8089` streaming and generate `STREAMING_BRIDGE_INTERNAL_TOKEN` in `.env` (`chmod 600`) if not already present.
- **Expected output:** `STREAMING_BRIDGE_INTERNAL_TOKEN generated once and written to .env (mode 600).` or `STREAMING_BRIDGE_INTERNAL_TOKEN already configured in .env (mode 600).` (token value is **never** printed).
- **Pass criteria:** `.env` permissions are `-rw-------` (`600`) and `STT_STREAM_NATIVE_ENABLED=true` is set.
- **Paste back:** Only the script's status line and `stat -c "%a %n" .env` (never paste `.env` contents or the token).

---

## Step 3: Restart Only Gateway & Bridge (PID-Only)

- **Command:**
  ```bash
  ./scripts/stop-bridge.sh
  ./scripts/start-bridge.sh

  # Restart ONLY the FastAPI gateway using its PID file (do NOT touch LocalAI)
  if [ -f .run/gateway.pid ]; then
    kill -TERM "$(cat .run/gateway.pid)" && sleep 1
  fi
  .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips='*' &
  echo $! > .run/gateway.pid
  sleep 2
  curl -s http://127.0.0.1:8000/health/ready
  curl -s http://127.0.0.1:8089/health
  ```
- **Purpose:** Reload gateway and `:8089` bridge with `STT_STREAM_NATIVE_ENABLED=true` without restarting LocalAI or TTS.
- **Expected output:** Both `/health/ready` (`:8000`) and `/health` (`:8089`) return JSON with `"status": "healthy"` / `"ready"`.
- **Pass criteria:** Bridge PID and gateway PID are running, LocalAI PID is unchanged, and `:8089/health` responds on loopback.
- **Paste back:** Both health JSON responses (confirming no token appears).

---

## Step 4: Engine Smoke Test (Stop Here If It Fails)

- **Command:**
  ```bash
  .venv/bin/python scripts/engine-smoke.py
  ```
- **Purpose:** Load real `libparakeet.so` inside the `ld.so`-wrapped `parakeet_worker` subprocess, assert `parakeet_capi_abi_version() == 11`, stream `tests/fixtures/audio/quiet_normal_speech.wav` in 160 ms chunks, and verify accumulated-text merge.
- **Expected output:**
  - `Library : .../libparakeet.so`
  - `Worker  : pid=... abi=11`
  - `[partial #01 ...]` lines showing incremental text growth
  - `PASS: ABI 11 verified, partials emitted, accumulated-text merge verified.`
- **Pass criteria:** Exit code `0`, `abi=11`, `>= 1` partial emitted, non-empty merged final matching `quiet_normal_speech.wav`. **If this fails, stop immediately and paste the output.**
- **Paste back:** Full stdout/stderr of `scripts/engine-smoke.py`.

---

## Step 5: Host Calibration

- **Command:**
  ```bash
  ./scripts/bridge-calibrate.sh --model parakeet-cpp-realtime_eou_120m-v1
  cat data/bridge_calibration_*_parakeet-cpp-realtime_eou_120m-v1.json
  ```
- **Purpose:** Measure baseline voice turn latency vs. 1 active native STT stream and record `admitted_streams` and pass/fail verdict in `data/bridge_calibration_<fingerprint>_parakeet-cpp-realtime_eou_120m-v1.json`.
- **Expected output:** JSON calibration report with `host`, `service_threads`, `baseline_med_ms`, `baseline_p95_ms`, `voice_med_ms`, `voice_p95_ms`, `chunk_p95_ms`, `passed`, and `admitted_streams`.
  - On the current 4.59-core RunPod pod (`d17b5d2691b9`): expected `passed: false`, `admitted_streams: 0`.
- **Pass criteria:** Calibration completes cleanly and writes the JSON file. Branch to Step 6 (`admitted_streams >= 1`) or Step 7 (`admitted_streams == 0`).
- **Paste back:** Full printed calibration summary and the contents of `data/bridge_calibration_*_parakeet-cpp-realtime_eou_120m-v1.json`.

---

## Step 6: If `admitted_streams >= 1` (Soak 1 Stream → Canary → Soak at Admitted Count)

1. **Soak (1 stream, 600 s):**
   - **Command:**
     ```bash
     .venv/bin/python scripts/bridge-soak-test.py --streams 1 --duration-s 600
     ```
   - **Purpose:** Verify 10-minute stability, bounded RSS (`<= 10%` growth after warmup), zero leaked sessions, and partial latency p50/p95 through the gateway.
   - **Expected output:** `PASS: soak test completed with 0 errors, bounded RSS, and 0 leaked sessions.`
   - **Pass criteria:** Exit code `0`, `Errors Encountered: 0`, `Final Active Sessions: 0`, RSS growth `<= 10%`.
   - **Paste back:** Full `STREAMING BRIDGE SOAK / LOAD TEST REPORT` block.

2. **Gateway Canary (`quiet_normal_speech.wav`):**
   - **Command:**
     ```bash
     .venv/bin/python scripts/canary-stt-native.py \
       --gateway-url ws://127.0.0.1:8000 \
       --model parakeet-cpp-realtime_eou_120m-v1
     ```
   - **Purpose:** End-to-end WebSocket transcription of `quiet_normal_speech.wav` through FastAPI gateway → `:8089` bridge → `parakeet_worker`.
   - **Expected output:** `transcript.partial` events, `transcript.completed`, `Expected-word overlap: >= 80.0%`, `PASS: native STT pipeline end-to-end via gateway confirmed`.
   - **Pass criteria:** Exit code `0`, `>= 1` partial, non-empty `transcript.completed`, `>= 80%` overlap with `DEFAULT_EXPECTED_TEXT`.
   - **Paste back:** Full canary output.

3. **Soak at Admitted Stream Count:**
   - **Command:**
     ```bash
     .venv/bin/python scripts/bridge-soak-test.py --streams <admitted_streams> --duration-s 600
     ```
   - **Purpose:** Verify full calibrated concurrency for 10 minutes with zero errors, bounded RSS, and zero leaked sessions.
   - **Expected output:** `PASS: soak test completed with 0 errors, bounded RSS, and 0 leaked sessions.`
   - **Pass criteria:** Exit code `0`, `Errors Encountered: 0`, `Final Active Sessions: 0`, RSS growth `<= 10%`.
   - **Paste back:** Full `STREAMING BRIDGE SOAK / LOAD TEST REPORT` block.

---

## Step 7: If `admitted_streams == 0`

- **Action:** Record **calibration FAIL**, **stop** (do not run soak or canary against `:8089`), and confirm the gateway refuses native streams cleanly.
- **Paste back:** `admitted_streams: 0`, `passed: false`, and the measured numbers from the calibration JSON (`baseline_med_ms`, `baseline_p95_ms`, `voice_med_ms`, `voice_p95_ms`, `chunk_p95_ms`, and `service_threads`).

---

## If It Fails: Quick Triage Table

| Failure Mode | First Thing to Check |
|---|---|
| **ABI mismatch** (`EngineABIMismatchError`) | Run `nm -D backends/cpu-parakeet-cpp/lib/libparakeet.so \| grep parakeet_capi` or check `STREAMING_NATIVE_EXPECTED_ABI` vs. the installed `libparakeet.so` build (`11` expected). |
| **Library not found** (`FileNotFoundError`) | Verify `backends/cpu-parakeet-cpp/lib/libparakeet.so` exists or set `STREAMING_NATIVE_LIB_PATH` in `.env`. |
| **`ld.so` loader error** (`CXXABI_*` / `GLIBC_*` / `rc=127`) | Check `ls -la backends/cpu-parakeet-cpp/lib/ld.so` and run `ldd` via the bundled `ld.so --library-path backends/cpu-parakeet-cpp/lib` to confirm all `.so` dependencies are in that directory. |
| **Worker crash** (`EngineCrashedError` / EOF) | Check model file path (`models/parakeet-cpp/realtime_eou_120m-v1-f16.gguf`) exists and is readable, and inspect bridge stderr/logs for segfault or OOM. |
| **Calibration FAIL** (`admitted_streams=0`) | Compare `voice_med_ms` / `voice_p95_ms` vs. `baseline_*` and `chunk_p95_ms` vs. `160.0 ms`, and check `service_threads` (e.g., uncapped LocalAI/TTS threads on a small CPU container). |
| **Canary overlap under 80%** | Inspect the printed `[transcript.partial]` and `Final text` lines from Step 6.2 against `quiet_normal_speech.wav` and verify `parakeet-cpp-realtime_eou_120m-v1` (not another model) is loaded. |

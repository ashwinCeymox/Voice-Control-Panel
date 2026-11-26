# `local-tts` Configuration & CPU Facts (Read-Only Investigation)

Generated strictly from repository inspection (`local-voice-agent`). Where `local-tts` (`saghul/local-tts`) source/venv is outside this repository and not installed on this Mac, items are marked `SOURCE NOT AVAILABLE HERE` with the exact pod inspection command.

---

### A. Runtime and Model

1. **Where `local-tts` source lives, name, version, patchability:**
   - **External sibling repository/venv (`../local-tts`):** Cloned from `https://github.com/saghul/local-tts.git` (`--depth 1`) and installed via `uv sync` into `../local-tts/.venv` (fallback `.tools/local-tts/.venv`) (`scripts/lib/install.sh:388-423`, `scripts/lib/services.sh:153-156`). Not inside this repo (`NOT FOUND` on Mac). Version & patchability on pod: `SOURCE NOT AVAILABLE HERE` — run `git -C ../local-tts rev-parse --short HEAD` and `../local-tts/.venv/bin/pip show local-tts` on the pod.
2. **Runtime (PyTorch / ONNX Runtime) & package versions:**
   - `SOURCE NOT AVAILABLE HERE` (`scripts/lib/install.sh:349,412` notes Kokoro PyTorch/CUDA/standard sync, `scripts/tts-facts.sh` queries `torch`, `onnxruntime`, `kokoro`, `numpy`, `phonemizer` from `../local-tts/.venv/bin/python`).
3. **Model file, size, precision, device selection logic:**
   - `SOURCE NOT AVAILABLE HERE` (weights cached in `~/.cache/huggingface` per `scripts/lib/services.sh:212`, `app/providers/localtts_client.py:55,93`). On pod, inspect `../local-tts` source with `grep -rnE "kokoro|float16|cuda|cpu|mps" ../local-tts/`.
4. **Other engines/models loaded at startup with `--disable-pocket --disable-kitten --disable-qwen`:**
   - `SOURCE NOT AVAILABLE HERE` (`scripts/lib/services.sh:231-241` passes those three `--disable-*` flags citing `localtts.md Section 6` to dedicate memory to Kokoro). On pod, check `grep -rn "disable_pocket\|disable-pocket" ../local-tts/`.
5. **Phonemizer / G2P step (espeak or other, in-process vs subprocess):**
   - `scripts/lib/services.sh:193-208` installs `espeak-ng`, `libespeak-ng-dev`, `sox` and sets `PHONEMIZER_ESPEAK_LIBRARY=/usr/lib/x86_64-linux-gnu/libespeak-ng.so.1` (suggesting `libespeak-ng` in-process via ctypes/phonemizer, but exact call is `SOURCE NOT AVAILABLE HERE`; inspect `grep -rn "phonemizer\|espeak" ../local-tts/`).

---

### B. Threading and Concurrency

6. **Thread configuration (`torch.set_num_threads`, ONNX, `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, env vars):**
   - `scripts/lib/services.sh:146-243` sets **no** `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, or `TORCH_*` env vars when starting `local-tts`. Whether `local-tts` sets them internally is `SOURCE NOT AVAILABLE HERE`; inspect `grep -rnE "num_threads|OMP_|MKL_|ThreadPool" ../local-tts/`.
7. **How synthesis is run (`run_in_executor` / `to_thread` / worker) & `max_workers`:**
   - `SOURCE NOT AVAILABLE HERE`. On pod, inspect `grep -rnE "to_thread|run_in_executor|ThreadPoolExecutor" ../local-tts/` and run `scripts/tts-facts.sh` (thread-name histogram).
8. **Concurrency limit, lock, queue, or semaphore inside `local-tts`:**
   - On the **gateway side**, `app/providers/localtts_client.py:19,53,105` wraps all calls in a global `_tts_gate = asyncio.Semaphore(1)` (serializes gateway requests). Inside `local-tts` itself: `SOURCE NOT AVAILABLE HERE`; inspect `grep -rnE "Semaphore|Lock|Queue" ../local-tts/`.
9. **Number of uvicorn workers & `--workers` configurability:**
   - `scripts/lib/services.sh:235-241` launches `local-tts server --host 127.0.0.1 --port ${TTS_PORT} --disable-pocket --disable-kitten --disable-qwen` (no `--workers` passed). Whether `local-tts server` accepts `--workers` is `SOURCE NOT AVAILABLE HERE`; run `../local-tts/.venv/bin/local-tts server --help` on the pod.
10. **Does `local-tts` read `os.cpu_count()` or `sched_getaffinity`?**
    - `SOURCE NOT AVAILABLE HERE`; inspect `grep -rnE "cpu_count|sched_getaffinity" ../local-tts/` on the pod.

---

### C. Per-Request Work

11. **Text splitting and streaming behavior for `POST /v1/text-to-speech/{voice}/stream`:**
    - `SOURCE NOT AVAILABLE HERE`; inspect the route handler in `../local-tts` (`grep -rn "text-to-speech" ../local-tts/`).
12. **Post-synthesis work (resampling, speed change, encoding, normalization):**
    - Gateway sends `"voice_settings": {"speed": speed}` in HTTP/WS payloads (`app/providers/localtts_client.py:50,117`). Inside `local-tts`, whether speed is model-side or post-processed is `SOURCE NOT AVAILABLE HERE`.
13. **Output sample rate, sample format, chunk size:**
    - Gateway expects `24000` Hz, `s16le` (16-bit signed PCM), `1` mono channel (`app/providers/localtts_client.py:42`, `app/services/voice_orchestrator.py:125-127`, `app/api/voice.py:573-575`). Internal server chunk size is `SOURCE NOT AVAILABLE HERE`.
14. **WebSocket TTS path (`LOCAL_TTS_WS_BASE_URL`) usage in voice turns:**
    - Used in **streaming WebSocket voice turns** (`app/api/voice.py:566,670` calls `orchestrator.tts.stream_synthesis_ws`, connecting to `${LOCAL_TTS_WS_BASE_URL}/v1/text-to-speech/{voice}/stream-input` with HTTP fallback in `app/providers/localtts_client.py:108,175,199`). **HTTP-only** in `VoiceOrchestrator.process_turn` (`app/services/voice_orchestrator.py:90`) and `calibrate.py` (`services/streaming_bridge/calibrate.py:208`).
15. **Caching, warm-up, background loops inside `local-tts`:**
    - `scripts/lib/services.sh:232` notes `--preload` is NOT passed (lazy-load on first synthesis). Any internal loops are `SOURCE NOT AVAILABLE HERE`; `scripts/tts-facts.sh` measures idle CPU over 20 s on the pod.

---

### D. How the Gateway Uses It (Voice Turn)

16. **TTS call pattern in a voice turn (full answer vs per-sentence, overlap):**
    - `VoiceOrchestrator.process_turn` (`app/services/voice_orchestrator.py:70-95`) runs LLM non-streaming (`stream=False`), then calls `self.tts.synthesize_http(text=response_text, ...)` **once** with the full answer (no overlap).
    - WebSocket voice handler (`app/api/voice.py:45,566,670`) streams LLM tokens through `TextChunker` and feeds chunks to `stream_synthesis_ws` (`flush=True` per chunk, overlapping LLM streaming and TTS synthesis).
17. **`bridge-calibrate` voice turns (`services/streaming_bridge/calibrate.py:189-213`):**
    - LLM `max_tokens`: `50` (`temperature: 0.0`) (`calibrate.py:196-197`).
    - Prompt: `"What is the capital of France?"` (`calibrate.py:194`); TTS text is **hardcoded** `"The capital of France is Paris, a major European city."` (56 chars, `calibrate.py:207`), voice `af_bella`, `model_id: "kokoro"` (`calibrate.py:207-208`).
    - Audio read: `await client.post(...)` (`calibrate.py:208`) reads the **full** HTTP response body (not discarded early).
18. **TTS config keys & defaults (`app/core/config.py`):**
    - `MAX_TTS_TEXT_CHARS`: `2000` (`app/core/config.py:184`)
    - `TIMEOUT_TTS_SECONDS`: `20.0` (`app/core/config.py:192`)
    - `LOCAL_TTS_SPEED`: `1.0` (`app/core/config.py:40`)
    - `LOCAL_TTS_VOICE_ID`: `"af_heart"` (`app/core/config.py:39`)
19. **`scripts/lib/services.sh` `start_local_tts` (`scripts/lib/services.sh:146-243`):**
    - Command: `nohup "${tts_bin}" server --host 127.0.0.1 --port "${TTS_PORT}" --disable-pocket --disable-kitten --disable-qwen >> "${LOGS_DIR}/local-tts.log" 2>&1 &` (`services.sh:235-241`).
    - Env passed: `LD_LIBRARY_PATH`, `PHONEMIZER_ESPEAK_LIBRARY`, optional `HF_HUB_OFFLINE=1` (macOS), `HF_HUB_ENABLE_HF_TRANSFER=0` (`services.sh:202-226`), plus inherited shell/`env.sh` env.
    - Log: `logs/local-tts.log`; PID: `.run/local-tts.pid` (`services.sh:241-242`).
    - Passing thread env vars today without code changes: **Yes** — any `OMP_NUM_THREADS` / `MKL_NUM_THREADS` / `TORCH_*` exported in `.env` (loaded by `scripts/lib/env.sh`) or the parent shell is inherited by `nohup "${tts_bin}" ...` (though `scripts/lib/services.sh` does not set them itself).

---

### E. Knobs

20. **Knobs table:**

| Knob | Default | Effect | How to set via `start_local_tts` |
|---|---|---|---|
| `--disable-pocket --disable-kitten --disable-qwen` | Enabled in `services.sh:238-240` | Disables non-Kokoro engines | Hardcoded in `scripts/lib/services.sh:238-240` |
| `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS` | Unset (uncapped) | Caps OpenMP/MKL/BLAS intra-op threads if used by torch/onnxruntime | Export in `.env` or before `start_local_tts` (inherited) |
| `GOMP_WAIT_POLICY` | Unset | `PASSIVE` avoids OpenMP spin-waiting at idle | Export in `.env` or before `start_local_tts` |
| `local-tts server` CLI flags (`--threads`, `--workers`, `--device`) | `SOURCE NOT AVAILABLE HERE` | `UNVERIFIED` without `local-tts` source | Run `local-tts server --help` on pod |

21. **Safe CPU capping options without patching `local-tts` (not applied):**
    - **Env vars (`OMP_NUM_THREADS=2`, `MKL_NUM_THREADS=2`, `GOMP_WAIT_POLICY=PASSIVE`)** before `start_local_tts`: caps OpenMP/MKL intra-op pools; does **not** cap Python `ThreadPoolExecutor` worker count if `local-tts` uses `os.cpu_count()`.
    - **`taskset -c <cores>`** around `local-tts`: hard-pins process + all threads to specific CPUs; prevents spilling onto other cores, does not reduce thread count.
    - **`nice -n <val>`**: lowers scheduling priority when contending with STT/LLM; does not reduce peak CPU when idle cores exist.
    - **cgroup `cpu.max`**: hard OS quota across all threads of `local-tts`; caps total CPU regardless of thread count, requires root/cgroup v2 permission.

---
title: "Voice AI Platform: Crash Fix and Optimisation Plan"
subtitle: "local-tts Metal abort, stack shutdown cascade and related issues"
date: "17 November 2026"
version: "1.0"
lang: en-GB
---

# 1. Summary

> **TL;DR:** The stack stops because **local-tts aborts on a Metal GPU assertion** (`Abort trap: 6`), and `start.sh` then shuts down every service. The gateway, LocalAI and the frontend were healthy. Fix the TTS threading first, then the launcher, then the smaller issues.

| Item | Detail |
|---|---|
| Machine | MacBook Air, Apple M2, macOS 26.6.2 |
| Crashing process | `local-tts` (PID 95053), Python 3.13, Kokoro MLX |
| Crash time | 2026-11-17 12:18:44 |
| Crash message | `failed assertion _completedCallbacksDone == false ... IOGPUMetalCommandBuffer didCompleteWithStartTime:endTime:error:` |
| Effect | `start.sh` line 323 detects the death, runs shutdown, kills ports 5173, 8000, 8080 |
| Status of this diagnosis | Strongly indicated by the logs, **not yet reproduced**. Phase 0 confirms it |

## 1.1 Evidence from the logs

| Time | Source | Observation |
|---|---|---|
| 12:18:33 | local-tts | Kokoro MLX model loads lazily during the first request (about 4 s) |
| 12:18:38 | local-tts | Second request creates another `KokoroPipeline` for language `a` |
| 12:18:43 | local-tts | `af_heart` is routed to the **Pocket** engine and fails: `Unknown Pocket TTS voice` |
| 12:18:44.054 | local-tts | Metal assertion, process aborts, leaked semaphore warning follows |
| 12:18:44 | backend | `Failed to reach local-tts ... All connection attempts failed` |
| 12:18:47 | backend | `Shutting down` is the launcher's cleanup, not a gateway crash |

## 1.2 What was ruled out

| Suspect | Verdict | Reason |
|---|---|---|
| Out of memory | Unlikely | No memory errors. Models are small (Whisper base, Llama 1B). Re-check if the crash survives Phase 1 |
| Gateway bug | No | Gateway returned 200 on every call until the launcher stopped it |
| LocalAI | No | Stopped at 12:18:48 only because the launcher killed it |
| Vite frontend | No | Log shows a clean start |

## 1.3 Most likely root cause

MLX and Metal work is running on **several threads** and **overlapping requests**. The engines call `run_in_executor(None, ...)`, which uses a multi-thread pool. Combined with lazy model load, uncached pipelines and rapid repeat requests, this can trigger the Metal command-buffer assertion.

\newpage

# 2. Phase 0: Confirm the Cause (10 minutes)

Do this before changing code so you know the fix worked.

- [ ] Stop everything: `./scripts/stop-native-engines.sh`
- [ ] Start **only** local-tts in a terminal:
  ```bash
  "$LOCAL_TTS_BIN" server --host 127.0.0.1 --port 8880 --disable-qwen
  ```
- [ ] Sequential test (expect success):
  ```bash
  for i in 1 2 3 4 5; do
    curl -s -o /dev/null -w "%{http_code}\n" -X POST \
      localhost:8880/v1/text-to-speech/af_heart/stream \
      -H 'Content-Type: application/json' -d '{"text":"Hello there, this is a test."}'
  done
  ```
- [ ] Parallel test (expect the crash):
  ```bash
  for i in 1 2 3; do
    curl -s -o /dev/null -X POST localhost:8880/v1/text-to-speech/af_heart/stream \
      -H 'Content-Type: application/json' -d '{"text":"Hello there, this is a test."}' &
  done; wait
  ```
- [ ] Save the newest crash report: `ls -t ~/Library/Logs/DiagnosticReports/ | head -3`

| Result | Meaning | Next step |
|---|---|---|
| Sequential passes, parallel crashes | Concurrency confirmed | Phase 1 |
| Both pass | Trigger is something else (for example the Pocket error path or health probe) | Replay with the gateway running, check Phase 2 item 2.2 first |
| Sequential crashes too | Engine or model bug | Paste the `.ips` report and the log for further analysis |

# 3. Phase 1: Stop the Crash (Priority: Critical)

## 1.1 One dedicated GPU thread for all TTS work

**Where:** `local_tts/engines/` (all engines, including `pocket.py` line 116 and `kokoro_mlx.py`)

**Why:** MLX and Metal must not be used from changing threads or overlapped calls.

```python
# local_tts/engines/executor.py  (new file)
from concurrent.futures import ThreadPoolExecutor

TTS_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts-gpu")
```

```python
# in every engine, replace
chunks = await loop.run_in_executor(None, self._generate_sync, text, voice_id, speed)
# with
chunks = await loop.run_in_executor(TTS_EXECUTOR, self._generate_sync, text, voice_id, speed)
```

- [ ] Applied to Kokoro
- [ ] Applied to Pocket
- [ ] Applied to Kitten
- [ ] No other `run_in_executor(None, ...)` left in `local_tts/` (`grep -rn "run_in_executor(None" src/`)

## 1.2 Load Kokoro once, at startup, on the same thread

**Why:** Lazy loading inside the first request overlaps with other work. Per-request `KokoroPipeline` creation repeats it.

```python
# kokoro_mlx.py
async def warm_up(self):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(TTS_EXECUTOR, self._load_model)       # load once
    await loop.run_in_executor(TTS_EXECUTOR, self._pipeline, "a")    # American English

def _pipeline(self, lang):
    if lang not in self._pipelines:                                  # cache by language
        self._pipelines[lang] = KokoroPipeline(lang_code=lang, model=self._model)
    return self._pipelines[lang]
```

Call `warm_up()` from the FastAPI startup (lifespan) hook, after "TTS engines initialized".

- [ ] Model loaded at startup, log line appears before "Application startup complete"
- [ ] Log shows `Creating new KokoroPipeline` once per language only

## 1.3 Serialise TTS calls in the gateway

**Where:** `app/providers/tts.py` (or the service that calls `/v1/text-to-speech/{voice}/stream`)

```python
_tts_gate = asyncio.Semaphore(1)

async def stream_tts(...):
    async with _tts_gate:          # hold for the full stream
        async with client.stream("POST", url, json=payload) as r:
            async for chunk in r.aiter_bytes():
                yield chunk
```

If one stream is cancelled (user interrupts), make sure the `async with` exits so the gate is released.

- [ ] Semaphore added and released on cancel
- [ ] Phrase chunks for one turn are sent one after another, not in parallel

## 1.4 Stop the launcher from killing everything

**Where:** `start.sh` around line 323

Replace "exit and stop all" with a bounded restart for local-tts:

```bash
start_tts() {
  n=0
  while [ "$n" -lt 5 ]; do
    "$LOCAL_TTS_BIN" server --host 127.0.0.1 --port 8880 --disable-qwen >> "$LOGS_DIR/local-tts.log" 2>&1
    n=$((n+1))
    echo "[WARN] local-tts exited (attempt $n/5), restarting in 3s"
    sleep 3
  done
  echo "[ERROR] local-tts failed 5 times; leaving other services running"
}
start_tts &
TTS_SUPERVISOR_PID=$!
```

Also:
- Start local-tts **first**, wait for `GET /v1/models`, then LocalAI, then STT, then the gateway.
- Use `/health/live` for the gateway wait. `/v1/health` returns 404 (seen at 12:17:57).
- Kill `$TTS_SUPERVISOR_PID` in the shutdown handler.

- [ ] Killing local-tts by hand (`pkill -f local_tts`) restarts it, and the other services stay up
- [ ] Ctrl+C still stops everything cleanly

## 1.5 Phase 1 acceptance test

- [ ] Parallel curl test from Phase 0 no longer crashes
- [ ] 20 rapid TTS requests in a row complete (`for i in $(seq 20); do ...; done`)
- [ ] Stack runs 30 minutes with the UI open and no process death

\newpage

# 4. Phase 2: Fix Related Faults (Priority: High)

## 2.1 Validate voice against engine before streaming

**Evidence:** `af_heart` (a Kokoro voice) reached the Pocket engine and raised `ValueError` *after* the HTTP 200 had started, so clients got a cut-off stream.

**Where:** `local_tts/server/routes.py`, before creating the `StreamingResponse`.

```python
engine = registry.engine_for_voice(voice_id)        # route by the voice's owner
voices = {v.id for v in await engine.list_voices()}
if voice_id not in voices:
    raise HTTPException(404, f"Voice {voice_id!r} is not available on engine {engine.name}")
```

- [ ] Unknown voice returns 404 before any audio bytes are sent
- [ ] `af_heart` always resolves to Kokoro
- [ ] Find out *why* Pocket was chosen (default engine, missing `model` field, or a voice-to-engine map gap) **[Verify]**

## 2.2 Remove synthesis from the readiness check

**Evidence:** In `backend.log` the sequence `readyz`, `POST .../text-to-speech/af_heart/stream`, `GET /v1/models`, `GET :8088/health`, `GET /health/ready` repeats every 5 s. The TTS POST appears to be part of the readiness probe **[Verify]**.

**Fix:** readiness may call `GET /v1/models` only. Never generate audio in a health check. Cache the result for 5 to 10 s.

- [ ] `/health/ready` makes no POST to local-tts
- [ ] The UI polls less often (10 to 15 s)

## 2.3 Fix the voices endpoint behaviour on failure

**Evidence:** `Failed to reach local-tts for voices: All connection attempts failed` returned HTTP 200 to the UI.

- [ ] Return the last known voices (cached) or 503, not an empty 200
- [ ] Use retry with short backoff (2 attempts, 1 s apart)

## 2.4 Gateway timeouts and cancellation

- [ ] httpx client to local-tts: `connect=3s`, `read=60s`
- [ ] On client disconnect, cancel the engine request so the GPU thread is freed

# 5. Phase 3: Optimisation (Priority: Medium)

| # | Change | Where | Benefit |
|---|---|---|---|
| 3.1 | `HF_HUB_OFFLINE=1` after the first model download | local-tts environment | Removes the huggingface.co call on each load (saw one at 12:18:34) |
| 3.2 | Remove `--reload` from uvicorn in daily use | `start.sh` | Lower CPU, no surprise restarts |
| 3.3 | LocalAI: `parallel: 1`, `context_size: 4096` | `LocalAI/models/*.yaml` | Smaller KV cache; logs show `parallel=4`, `context=8192` for a 1B model |
| 3.4 | Fix model naming mismatch | model YAML | Name says `q4_k_m`, file loaded is `llama-3.2-1b-instruct-q8_0.gguf` |
| 3.5 | Set `LOCALAI_DISABLE_HARDWARE_DEFAULTS=true` after you set your own values | `.env` or launcher | Makes tuning predictable |
| 3.6 | Cap MLX cache (check your MLX version for the function name) | `kokoro_mlx.py` | `mx.set_cache_limit(512 * 1024**2)` and `mx.clear_cache()` after each synthesis |
| 3.7 | Remove Pocket and Kitten from the default registry on macOS if unused | local-tts config | Fewer code paths (`--disable-qwen` already exists) |
| 3.8 | Serve `npm run build` instead of the Vite dev server for long sessions | `start.sh` | Saves memory |
| 3.9 | Free disk space (11.5% free, 26 GB) | Mac | The hygiene manager already warns. Stay above 15% |
| 3.10 | Fix Docker credential warnings in LocalAI | remove `docker-credential-desktop` from `~/.docker/config.json` or install Docker Desktop | Silences the backend upgrade-check warnings |
| 3.11 | Upgrade `websockets` usage (deprecation warnings at local-tts startup) | local-tts dependencies | Avoids future breakage |

# 6. Phase 4: Monitoring and Safety Net (Priority: Medium)

- [ ] Add a one-line watchdog: if local-tts `GET /v1/models` fails 3 times, restart it
- [ ] Write the crash reason to `logs/crash-history.log` (time, PID, exit code) in the launcher
- [ ] Add a gateway log line when TTS is skipped or retried, with `request_id`
- [ ] Keep `~/Library/Logs/DiagnosticReports/*.ips` for any new abort
- [ ] Add a regression test: three concurrent TTS requests must all return 200

# 7. Order of Work and Time Estimate

| Step | Task | Time |
|---|---|---|
| 1 | Phase 0 reproduction | 10 min |
| 2 | 1.1 and 1.2 (executor and warm-up) | 45 min |
| 3 | 1.3 (gateway semaphore) | 20 min |
| 4 | 1.4 (launcher restart logic) | 30 min |
| 5 | 1.5 acceptance test | 20 min |
| 6 | Phase 2 (2.1 to 2.4) | 60 min |
| 7 | Phase 3 quick wins (3.1 to 3.5) | 30 min |
| 8 | Phase 4 | 30 min |

# 8. Fallback Options if the Crash Persists

1. **Isolate Kokoro in its own process** with a single request queue, so a Metal abort never takes down the other engines.
2. **Use a CPU Kokoro build** (for example ONNX) for development. It is slower but cannot trigger Metal assertions.
3. **Update MLX and Python packages** in the local-tts venv (`pip list --outdated`), since Metal assertion bugs are often fixed in newer MLX releases. Pin versions once stable.
4. **Check Python 3.13 compatibility.** If problems continue, test the venv on Python 3.12.
5. **Re-test memory pressure** with `memory_pressure` and Activity Monitor while running LocalAI, STT and TTS together.

# 9. Rollback

- Commit before each phase: `git commit -am "phase N: ..."`.
- Phase 1 changes are small and independent. Revert any single item with `git revert`.
- Keep a copy of the original `start.sh` as `start.sh.bak`.

# 10. Definition of Done

- [ ] The parallel TTS test passes 10 times in a row
- [ ] Stack stays up for 2 hours of normal use
- [ ] A killed local-tts restarts without stopping other services
- [ ] `/health/ready` produces no audio requests
- [ ] Unknown voices return 404 before streaming begins
- [ ] No `Abort trap` in the shell and no new `.ips` crash reports

# 11. Open Questions

| # | Question | Needed for |
|---|---|---|
| 1 | Why was `af_heart` routed to Pocket? | Fix 2.1 |
| 2 | Is the TTS POST in `/health/ready` or from the UI? | Fix 2.2 |
| 3 | What does the `.ips` crash report show (thread and call stack)? | Confirming the root cause |
| 4 | Which MLX and Kokoro versions are installed? | Fallback option 3 |
| 5 | Do `routes.py` and `kokoro_mlx.py` match the assumptions in section 3? | Turning templates into exact patches |

> **Note:** the code in this plan is a template written without access to your source files. Adjust names and paths to match your repository, or share `routes.py`, `kokoro_mlx.py`, `pocket.py`, the gateway TTS provider and `start.sh` for exact diffs..

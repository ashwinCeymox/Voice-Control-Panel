# Task: Find and Fix Hidden Fallback in Streaming STT

**Problem:** Three streaming STT models were tested (`sherpa-onnx-streaming-zipformer-en-20M-2023-02-17`, `parakeet-cpp-realtime_eou_120m-v1`, `parakeet-cpp-nemotron-3.5-asr-streaming-0.6b`). They all give the same low-quality result. Different models should not give identical performance, so a fallback, a model-selection bug or a configuration problem is suspected.

**Goal:** Prove or rule out each cause with evidence, then fix what is confirmed. Do not guess.

---

## 0. Rules

1. **Phase A (diagnose) is read-only.** No source changes until `docs/fallback-diagnosis.md` exists and the cause is confirmed with evidence.
2. Do not touch the live model folders. Do not rename, move or delete any model file. Use fixtures and copies for experiments.
3. Use **test clips only** (from `tests/fixtures/audio` or recorded for this task). Do not put real user transcripts or audio in logs or reports.
4. No secrets or keys in any output.
5. Every claim in your report needs evidence: file and line, command and output, or a measured result. Anything else is marked UNVERIFIED.
6. Mark each hypothesis **CONFIRMED**, **RULED OUT** or **UNKNOWN**.
7. If a step needs something destructive, stop and ask the owner.

---

## 1. Hypotheses to Test

| ID | Hypothesis |
|---|---|
| H1 | The model selected in the UI is not applied. The service always runs one model (for example the zipformer), whichever is chosen |
| H2 | The three models need **different runtimes** (`sherpa-onnx` and `parakeet-cpp` appear in the names), but only one adapter or server exists, so the others are routed to it or fail and are replaced |
| H3 | A silent fallback exists: on error, timeout, missing model, empty model field or unhealthy service, the code switches to another model or to batch Whisper without telling the client |
| H4 | The audio reaching the engine is poor (wrong sample rate or format, clipped, too quiet, first words cut, dropped frames, double processing), so every model fails the same way |
| H5 | Engine settings hurt quality: very small chunk size or latency setting, greedy decoding, too few threads, aggressive end-of-speech rules |
| H6 | Language is wrong or locked (the screenshot shows Target Language as **LOCKED**); English-only models fed other speech or an accent they handle badly |
| H7 | The model id is resolved wrongly (display name versus id, stale cache, stale agent snapshot, default in config or database overriding the request) |
| H8 | Post-processing removes words (volume gate, unclear-speech gate, junk phrase filter, text cleanup) |

---

## 2. Phase A: Diagnose

### A1. Map every fallback and default in the code

Search the whole repository (`app/`, `frontend/src/`, startup scripts, config files, environment files):

```bash
grep -rniE "fallback|fall back|alternative|default_model|DEFAULT_.*MODEL|whisper-|zipformer|parakeet|sherpa" app/ frontend/src/ *.sh
grep -rniE "except (Exception|.*Error)" app/providers app/api/voice.py app/services | head -100
```

Produce a table in `docs/fallback-diagnosis.md`:

| File:line | Trigger | What it substitutes | Reported to the client? | Logged? |
|---|---|---|---|---|

Look especially for:
- STT provider code that tries "alternative" or "fallback" models after a failure
- A streaming adapter that falls back to batch on connect error, timeout or first-partial delay
- Code that uses a default model when the requested one is missing **or when the field is empty**
- `try/except` blocks that return an empty or default result without an error
- Health check results that disable streaming and silently select batch
- Anything that ignores the `model` field sent by the client

For each: is `stt.fallback` (or any other flag) actually sent, and does the UI display it?

### A2. Prove which model actually runs

1. Record the process list for the streaming services:
   ```bash
   ps aux | grep -iE "sherpa|parakeet|streaming|stt" | grep -v grep
   ```
   Note the command-line arguments, model paths and ports for each process.
2. For each running STT process, list the model files it has open (macOS):
   ```bash
   lsof -p <PID> | grep -iE "onnx|gguf|nemo|\.bin|\.pt|\.safetensors"
   ```
3. Switch the model in the UI and repeat. Does a different process, port or file set appear? Record before and after.
4. Check whether the service or adapter exposes the loaded model (a health or info field). If not, note it; the fix will add it.
5. Request a **model id that does not exist** through the same path as the UI. Expected: a clear error. If it still returns a transcript, a silent fallback is confirmed (H3).
6. Request with the `model` field **empty or missing**. Record which model answers.

Result: a table of **selected model → process → files actually loaded → port**.

### A3. Check runtime coverage (H2)

- List the adapters and clients in `app/providers/` and which runtimes they speak to (sherpa-onnx WebSocket, parakeet.cpp, others).
- For each of the three models: which adapter, server and port handle it? If two models share one adapter that only knows one protocol, record exactly what happens when they are selected.
- Check the startup script or Compose file: is there a service for each runtime, and does it start?

### A4. Compare the three models on the same clip

Use one clean test clip (a known sentence, 16 kHz mono) and the replay script (real-time pacing, 100 ms chunks). For each model, record:

| Model | Final transcript | Word error rate vs the known text | First partial (ms) | Final delay after end (ms) | Real-time factor | Memory |
|---|---|---|---|---|---|---|

Decision rule: **byte-identical transcripts and near-identical timing from different models means the same model ran** (H1 or H3 confirmed).

Also run the same clip through batch Whisper as a reference.

### A5. Audio path check (H4)

Save the audio exactly as the STT service receives it (a temporary debug dump to a test folder, test clips only) and analyze it:

```python
import wave, numpy as np, sys
w = wave.open(sys.argv[1], "rb")
sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32)
rms = float(np.sqrt(np.mean(x**2)))
peak = float(np.max(np.abs(x)))
clipped = int(np.sum(np.abs(x) >= 32767))
lead = next((i for i, v in enumerate(np.abs(x)) if v > 500), n)
print(f"rate={sr} channels={ch} bytes/sample={sw} duration={n/sr:.2f}s")
print(f"rms={rms:.0f} peak={peak:.0f} clipped={clipped} leading_silence={1000*lead/sr:.0f} ms")
```

Check against the source clip:
- sample rate 16000, mono, 16-bit
- duration matches the spoken length (not stretched or squeezed)
- RMS is reasonable (very low means the volume gate or gain chain is the problem)
- no clipping, no gaps or repeated chunks
- first words are present (compare the start of the dump with the source)

Then run the **same clip through each engine's own command-line tool or sample client**, bypassing the browser, the gateway and the adapter. If results are good there but poor live, the audio path or adapter is the cause. If they are poor there too, the model, settings or language are the cause.

### A6. Engine settings audit (H5)

For each engine, list the effective settings (from the launch command, config file and environment): chunk size or latency mode, decoding method (greedy or beam search), number of threads, execution provider, end-of-speech rules and thresholds, hotwords, language, sample rate. Compare them with the engine's recommended settings, and note any value that is far from recommended.

### A7. Language and model-id resolution (H6, H7)

- Where does the **Target Language** value come from, why is it locked, and what exactly is sent to each engine?
- List every place a model name can be set: request field, agent config, per-session snapshot, `config.py` default, environment variable, database default, frontend default. Write down which one wins, and test with conflicting values.
- Check for a stale agent snapshot or cached resolution: change the model, then confirm the next turn uses it.

### A8. Gates and text cleanup (H8)

Send the clean clip with gates disabled (test configuration only) and compare the raw transcript with the gated one. Record any word removed by the volume gate, unclear-speech detection, junk-phrase filter or cleanup.

### A9. Fallback frequency

Add temporary counters (test environment, no content) for: requests, fallbacks by reason (connect error, timeout, first-partial too slow, unhealthy, model missing, empty model, adapter error), and replays that ended in batch. Run 30 replays per model and report the rate.

---

## 3. Phase A Deliverable

`docs/fallback-diagnosis.md` containing:

1. The fallback and default table (A1)
2. Selected model → process → files loaded → port (A2)
3. Runtime coverage (A3)
4. Model comparison table (A4)
5. Audio dump analysis (A5)
6. Settings audit (A6)
7. Language and model-id findings (A7)
8. Gate comparison (A8)
9. Fallback frequency (A9)
10. **Verdict table:** H1 to H8, each CONFIRMED, RULED OUT or UNKNOWN, with the evidence reference
11. One paragraph stating the root cause or causes, in plain language

Stop here and wait for ORCH approval before Phase B.

---

## 4. Phase B: Fix (only after the verdict)

Fix **only confirmed causes**. Start each fix with a failing test.

| If this is confirmed | Fix |
|---|---|
| **H1 / H7** (selection not applied) | The adapter resolves the model id to its engine, port and files from the registry and passes it on every stream. Switching models loads or routes to the right service. The service reports the loaded model id (and file hash or version), and the adapter verifies it matches the request |
| **H2** (missing runtime) | Add the missing adapter or service for that runtime, start it in the startup script or Compose file, and add its health check. A model with no working adapter must be shown as **unavailable** in the registry, not selectable |
| **H3** (silent fallback) | Remove silent fallback. A missing, unhealthy or failing model returns an explicit error with a code (for example `STT_MODEL_UNAVAILABLE`). Fallback happens only if the agent or request enables it, and then the response includes `fallback: true`, `requested_model`, `used_model` and a reason, and the UI shows a visible notice. Empty or missing model fields are rejected, not defaulted |
| **H4** (audio path) | Fix the sample rate, sample format, byte order, gain or clipping problem at its source. Keep the pre-speech buffer. Remove duplicated processing (for example browser noise suppression plus extra gain plus the volume gate) if it hurts. Add an audio-statistics check in tests |
| **H5** (settings) | Set the engine settings to the documented values for live use (chunk size, decoding method, threads, end-of-speech rules). Put them in configuration, not in code, and record the chosen values and the reason |
| **H6** (language) | Pass the correct language, unlock it where the engine supports other languages, and mark English-only models as English-only in the registry so they are not offered for other languages |
| **H8** (gates) | Adjust the gate that removes valid words, with a test using the clip that failed |

**Always add (regardless of the cause)**
- `model_used`, `runtime` and `fallback` fields in `transcript.completed` and in the stream response, so every result states which model made it.
- A startup self-test for each streaming model: transcribe a short fixture and compare it with the expected text. A model that fails is marked unavailable.
- A log line per stream (no content): requested model, resolved model, runtime, fallback flag and reason, audio statistics (rate, duration, RMS), chunk settings.
- A counter of fallbacks by reason on the admin Overview.

---

## 5. Tests (written first)

- Each of the three models, selected through the same path as the UI, runs its **own** engine (assert on the reported model id and file hash).
- A nonexistent model returns an explicit error and **no** transcript.
- An empty model field returns an explicit error.
- Stopping the streaming service mid-stream returns the documented fallback only when enabled, with `fallback: true` and a reason; when disabled it returns an error.
- Three different models on one clip produce different `model_used` values and are not byte-identical unless the engine is identical.
- The audio-statistics check passes for the live path (16 kHz, mono, 16-bit, RMS within range, no clipping, start preserved).
- The UI shows the fallback notice when it happens.
- The startup self-test marks a deliberately broken model unavailable.

---

## 6. Acceptance Checklist

- [ ] The report lists every fallback and default in the code with file and line.
- [ ] For each of the three models, the report shows the process and files that actually served it.
- [ ] The root cause is stated with evidence, or the causes are ruled out one by one.
- [ ] No silent fallback remains: every substitution is explicit, reported and visible in the UI.
- [ ] Selecting each model changes the model that runs, and the response says which one ran.
- [ ] A missing, broken or empty model produces a clear error.
- [ ] The three models now give measurably different results (accuracy, timing, memory) on the same clip, consistent with their sizes, or the report explains why they match.
- [ ] Audio reaching the engine passes the statistics check.
- [ ] A fallback counter by reason appears on the Overview.
- [ ] Regression tests exist for every fixed cause and pass.

---

## 7. Starting Prompt for the Agent

> You are the diagnostician for the streaming STT quality problem. Read `stt-fallback-diagnosis-and-fix.md`. Do Phase A only: read-only, test clips only, no changes to live model folders. Produce `docs/fallback-diagnosis.md` with evidence for every item and a verdict for H1 to H8. Mark anything you cannot prove as UNVERIFIED. Stop after the report and wait for approval before Phase B.

**Reviewer prompt for Phase B (fresh context, diff only):**
> Try to break the fix: select each model and check which one runs, send an unknown model, send an empty model, stop the service mid-stream with fallback on and off, and send audio with the wrong sample rate. Confirm no path can still substitute a model silently.

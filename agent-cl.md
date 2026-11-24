# MESSAGE TO THE AGENT: B1 closed, start B2 (host-gated build)

Authoritative plan: `port-stt.md`. This message adds decisions on top of it. Where they conflict, this message wins for B2 to B4.

## 1. Decision on B1 (closed, do not run more benchmarks on this pod)
- Decode speed, fixed overhead and `MAX_DECODES=1` are verified. Transcripts are clean.
- Voice interference **fails on this CPU-only RunPod pod**, even with scheduler rules. Do not spend more time tuning here.
- Your B1e diagnosis ("global lock in LocalAI") is **not supported by your own data**: TTS runs on `:8880`, not through LocalAI, and it slowed by the same factor. Record the cause as "CPU contention or a small real CPU allotment on this pod, not proven". Do not repeat the lock claim in docs or code comments.
- This pod is noisy: the baseline turn went from 736 ms to 1865 ms between runs. Compare numbers only within one run.
- The owner will scale to GPU or dedicated-core hosts later. Everything host-dependent must therefore be **config and calibration, not hard-coded**.
- Not installed on this pod: `whisper-base`, `realtime_eou_120m-v1-f16`. Skip them. Do not install anything.
- Native streaming via LocalAI `/v1/realtime` is **unknown** (your probe failed on a library call). Do a quick read-only retry once at the start of B2 (fix the `extra_headers` call for the installed `websockets` version, connect, read the first event, close, no audio). Report the result in one line, then move on either way.

## 2. New rule: host-gated enablement (the main change)
The bridge must never turn itself on blindly.
- `STREAMING_BRIDGE_ENABLED=false` stays the default.
- Add an **offline calibration command** (for example `scripts/bridge-calibrate.sh`, run by the owner, never at startup). It measures, for one named model, on the current host:
  1. voice-turn baseline (LLM then TTS), 20 turns, median and p95
  2. the same with 1 simulated stream running under the real scheduler rules
  3. decode time at 6 s and 15 s segments
- It writes a small result file under the data directory (path from `.env`, gitignored) with: model, host fingerprint (CPU model, core count, cgroup quota, GPU yes/no), measured numbers, and a verdict.
- **Pass rule:** with 1 stream, voice-turn p95 <= 1.5 x baseline p95 and median within +10%. The number of streams admitted is the highest count that still passes (never more than `STREAMING_BRIDGE_MAX_STREAMS`).
- The bridge refuses to start streams for a model unless a **passing calibration file exists for this host and model**. Otherwise it answers `BRIDGE_NOT_CALIBRATED` and the gateway falls back as configured (batch by default, never silently).
- This pod will fail calibration. That is the expected, correct outcome. The feature stays off here with no extra work.

## 3. Scope for this round: B2, B3, B4 only
Follow `port-stt.md` for B2 (core bridge engine), B3 (scheduler and queue), B4 (service daemon on `STREAMING_BRIDGE_PORT`, default 8089). Stop after B4 and report. Do not start B5 (gateway routing).

Scheduler rules to implement (all configurable via `.env`):
- one in-flight decode per stream
- global `MAX_DECODES` (default 1); finals ahead of partials; stale partials dropped
- yield-to-voice: no new partial decodes while the gateway reports an LLM or TTS request in flight; finals never skipped
- bounded pause, default 3000 ms, then resume with a wider step (1500 to 2000 ms)
- the bridge accepts a small internal signal from the gateway (`voice_busy` start and end), loopback only, protected by `STREAMING_BRIDGE_INTERNAL_TOKEN`
- note honestly in code docs that an in-flight decode cannot be cancelled, so yield removes new work but not the one already running

## 4. Standing boundaries (unchanged)
- No push. No deleting `.env`, data, models, `arch` or `.venv`. No killing by port; use recorded PIDs only.
- No secrets in output, logs or git. Token generated once, stored in `.env` only, never printed.
- Do not touch Sherpa (`:8088`), batch STT, or the voice pipeline behaviour. With the bridge off, everything behaves exactly as before. Prove it by running the existing unit tests before and after and showing the same pass count.
- One change per commit, small commits, no commit without tests passing.
- Do not edit LocalAI configuration and do not restart LocalAI.

## 5. Report after B4, then stop
- What was built, file list, and test results (new tests plus the unchanged existing suite counts before and after).
- The one-line native-streaming probe result.
- The calibration command output on this pod (expected: fails, with numbers).
- Anything you could not do and why.
Wait for the owner before B5.

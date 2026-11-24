# FINAL PLAN: Streaming STT Bridge (port 8089)

**Status:** final. This replaces `streaming-bridge-integration-plan.md`. The agent also has `universal_streaming_bridge_plan.md` (the original proposal) for reference; where they differ, **this plan wins**.
**Agent:** Gemini 3.8 Flash (high). Model pinned and confirmed in the status line before starting.

**Goal.** Add one small internal service on its own port that gives live partial text for STT models that LocalAI serves only as batch (Whisper, and Parakeet or Nemotron through LocalAI). Existing behavior stays exactly as it is, it respects concurrency limits, and it can be switched off at any time.

**What it is, honestly.** The bridge repeatedly re-transcribes the growing audio of the current segment through LocalAI's batch endpoint and stabilizes the text. That is **simulated streaming**: extra compute, partial text can change, and it cannot use a model's own streaming features (for example the end-of-utterance signal of the Parakeet EOU model). It is one transport among several, labelled "Live (simulated)" in the UI.

---

## 1. Decisions (fixed, do not reopen without the owner)

| # | Decision |
|---|---|
| 1 | Separate process on its own loopback port, default `8089`, read from `.env`, never hardcoded |
| 2 | **Off by default** (`STREAMING_BRIDGE_ENABLED=false`) until the canary passes |
| 3 | Sherpa streaming (`:8088`), batch STT, the voice pipeline and LocalAI behave identically when the bridge is off |
| 4 | Routing uses registry `transports`, **never model names** |
| 5 | Fallback to batch is **explicit** (reported to the client and counted), off by default |
| 6 | The gateway keeps all client-facing work (login or ticket, scopes, per-key caps, usage); the bridge only accepts the gateway (loopback plus internal token) |
| 7 | The window is the **current segment** (re-decoded from its start, capped), not a fixed lookback, because LocalAI returns no word timestamps |
| 8 | Confirmed text is append-only within a segment |
| 9 | Concurrency is controlled by one in-flight decode per stream, a global decode cap, priorities and admission control (section 5) |
| 10 | A measured **go/no-go spike (B1)** gates all further work |
| 11 | Stop by recorded PID only; never by port number |
| 12 | No transcripts, audio or secrets in logs; no absolute local paths in committed files |

---

## 2. Issues in the Original Proposal That This Plan Fixes (do not reintroduce)

1. No concurrency design (overlapping speculative requests would pile up on LocalAI) → section 5.
2. Contradictory window design ("accumulated window" versus "last 3 seconds") → segments (section 4.2).
3. No cost check (a per-request overhead of about 0.8 s was suspected and is unverified) → B1 gate.
4. Enabled by default → off by default, canary, kill switch.
5. Routing by model name (`if Sherpa`) → registry transports.
6. Silent fallback to batch → explicit fallback.
7. Hardcoded URLs and ports in `config.py` → derived from `.env` ports.
8. Port-based cleanup in `stop-all.sh` → recorded PIDs only.
9. No authentication between gateway and bridge → internal token.
10. No admission control → capacity-based admission with a clear busy error.
11. The bridge kept its own model list (`GET /models`) → no list; it validates names only.
12. Absolute `/Users/...` links → relative paths.
13. Silero VAD inside the bridge → optional lightweight energy detection.
14. Quality gaps (hallucination on noise, contradicted words) → gates and append-only text.
15. A full "high-fidelity" re-decode for every final → reuse the last decode when it covers all audio.

---

## 3. Architecture

```
Client ──WS──▶ Gateway :GATEWAY_PORT   (login/ticket, scopes, per-key caps, usage, registry, explicit fallback)
                  │
                  ├─ ws_stream_native ─▶ Sherpa service  :STT_PORT        (unchanged)
                  └─ ws_stream_bridge ─▶ Bridge service  :STREAMING_BRIDGE_PORT
                                              │  loopback + internal token
                                              ▼
                                        LocalAI :LOCALAI_PORT   POST /v1/audio/transcriptions
```

The bridge never accepts a URL from a client. It calls only the configured LocalAI address.

---

## 4. Bridge Design

### 4.1 Protocol

- **Gateway to bridge:** `stream.start` (`model`, `language`, `sample_rate=16000`, `format=pcm_s16le`, `vad=client|energy`), binary 16 kHz mono PCM16 frames, `audio.commit`, `stream.cancel`.
- **Bridge to gateway:** `stream.ready`, `transcript.partial` (`seq`, `segment_id`, `text`, `confirmed`, `tentative`, `degraded`), `segment.final`, `transcript.completed` (`text`, `model_used`, `duration`, `revised`), `error` (`code`, `message`, `retry_after_ms`).
- **Event mapping:** before writing code, read the existing event schema (`app/schemas/events.py`) and the Sherpa path. The gateway translates bridge events into the **same field names the UI already consumes**, adding only `engine="bridge"` and `mode="simulated"`. Do not rename anything existing. `stream.ready` is optional for the UI (unknown events must be ignored; add a test).
- Reject: any format other than 16 kHz mono PCM16, oversized frames, streams longer than the maximum, malformed JSON, and model names that fail the gateway's validation pattern.

### 4.2 Segments and windows

1. Keep the audio of the **current segment** from its start. Every step (default 1 s), while speech is active and the segment holds at least `MIN_WINDOW_MS`, decode the **whole current segment** (never more than `SEGMENT_MAX_MS`, default 15 s).
2. **End a segment** at a pause of at least `SEGMENT_SILENCE_MS` (default 600 ms; from the client's VAD signal, or the optional energy detector) or at the length cap (cut at the quietest recent point). A final decode of the segment yields `segment.final`; then the buffer resets and the next segment starts. `transcript.completed` joins all segment texts.
3. Cost per decode and memory per stream stay bounded.

### 4.3 Stable text

- Normalize words (lowercase, strip punctuation) before comparing.
- **Confirmed** = the longest common prefix of the last two decodes (beyond what is already confirmed). It only grows. The rest is **tentative**.
- If a later decode **contradicts** the confirmed prefix: keep the confirmed text, align the new tentative text after the best match of the last few confirmed words, and count `confirmed_conflicts`. The final decode replaces the segment text; `revised` says whether it differs from the last partial.
- **Gates before emitting:** skip decoding while the segment level is below the energy threshold; drop empty, bracketed-token (for example `[BLANK_AUDIO]`) and repeated-word hallucination outputs; never emit tentative text from a decode that returned nothing.

### 4.4 Faster final text
At `audio.commit` or a segment end: if the last decode already covered all audio (within one frame), **reuse it** as the final; otherwise run one final decode at top priority.

### 4.5 LocalAI request parameters
Send exactly what the existing batch provider sends (temperature 0, no carry-over of previous text, the same language handling). Put the values in configuration and add a **contract test** asserting the bridge's request parameters equal the batch provider's. Send a 16 kHz mono PCM16 WAV built directly (header plus raw samples), to avoid extra conversion work in LocalAI.

### 4.6 Model readiness
On `stream.start`, make sure the model is loaded: check LocalAI's load status if its API offers one (`[Verify]`: the API list includes a model load-status route; confirm its behavior), otherwise warm with a short silent clip under a timeout. Send `stream.ready`. If readiness would take longer than `READY_TIMEOUT_S` (default 10), return `MODEL_NOT_READY` instead of letting the first partial stall (RunPod's proxy cuts requests that wait 100 seconds).

---

## 5. Concurrency (required)

### 5.1 Rules
1. **At most one decode in flight per stream.** If a step arrives while the previous decode runs, skip it (latest wins). No overlapping speculative requests from one stream.
2. **Global caps:** `MAX_DECODES` in total (default 1) and `MAX_DECODES_PER_MODEL` (default 1).
3. **Priority queue:** final decodes before partials. A queued partial older than `PARTIAL_MAX_AGE_MS` (default twice the step) is discarded as stale.
4. **Fairness:** partials are served oldest first across streams.
5. **Adaptive step:** when the queue is deep, a stream's step grows (up to 3 s) and partials carry `degraded: true`. Under severe load, partials stop and only finals run.
6. **Cancellation:** on `audio.commit`, `stream.cancel` or disconnect, remove that stream's queued jobs and cancel its in-flight request. Release every slot in a `finally` path.
7. **Timeouts:** partial `PARTIAL_TIMEOUT_S` (default 8) means skip; final `FINAL_TIMEOUT_S` (default 30) means return the last good text with `final_degraded: true`.
8. **Bounded resources:** per-stream buffer capped by the segment length; WebSocket message size cap; idle timeout (30 s); maximum stream length (10 minutes); **one worker process** (the scheduler state is shared).

### 5.2 Admission control
- Hard limit `MAX_STREAMS` (default 5).
- Capacity limit from **offline calibration**: a command (`python -m services.streaming_bridge.calibrate --model <id>`, also available as an admin action) decodes clips of 3, 6 and 10 seconds and stores the median decode time `d` in `data/bridge_capacity.json`. Sustainable streams are about `floor(step / d) × MAX_DECODES`. The admitted limit is the smaller of that and `MAX_STREAMS`. **Calibration never runs automatically at startup** (it would load models and could evict others on a small machine). Until calibrated, admit **1 stream** per model.
- When full: send `error` with `BRIDGE_BUSY` and `retry_after_ms`, and close with code 1013.

### 5.3 Interaction with the rest of the system
- The bridge shares LocalAI with batch STT, the LLM and TTS. Keep a rolling 95th percentile of decode latency; above the threshold, degrade (slower step, then finals only) and report it in `/health`.
- Partial decoding is limited to an **allowlist of small models**: `ALLOWED_MODELS=auto` means models at or below `MAX_MODEL_MB` (default 1500, taken from registry size metadata); an admin may list others explicitly. The existing memory-fit check still applies.
- **Residency lease:** the gateway marks the model "in use" for the whole life of a bridge stream, so the idle-model flush never unloads it mid-stream, and records model activity for it.
- **Usage:** the gateway records bridge streams exactly like native streams (key, model, audio seconds, time to first partial, final delay, status) with no content.
- **Limits:** bridge streams count against the existing per-key concurrency and global stream caps, plus the bridge's own cap.

### 5.4 Observability (no content, no secrets)
`/health` (loopback) returns: active streams, admitted limit, queue depth, decode median and 95th percentile, skipped steps, dropped partials, busy rejections, confirmed conflicts, degraded state. Logs contain stream id, model, timings and error codes only.

---

## 6. Integration With the Current Setup

### 6.1 Registry, routing and UI
- Each STT model record gets `transports`: `batch_http`; `ws_stream_native` (Sherpa); `ws_stream_bridge` (added for installed LocalAI STT models when the bridge is enabled, healthy and the model is allowed). Preference: native first, then a LocalAI-native streaming route if one exists later, then the bridge.
- The Streaming list shows **Live (native)** and **Live (simulated)** entries with a tooltip explaining the extra compute and that partial text may change. If the bridge is down, those models are disabled with the reason `BRIDGE_UNAVAILABLE` before the user starts, not an error after speaking.
- Runtime **kill switch** in the admin settings (gateway side), so routing can be switched off without a restart. The `.env` flag is the master switch.

### 6.2 Fallback
`STREAMING_BRIDGE_FALLBACK_TO_BATCH=false` by default. When true and the bridge fails or is busy, the turn continues with batch STT and the client receives `stt.fallback` with the reason; the Overview counts it.

### 6.3 Gateway to bridge
The gateway is the WebSocket client of the bridge: it forwards frames and translates events. It sends `STREAMING_BRIDGE_INTERNAL_TOKEN` on connect; the bridge rejects connections without it (constant-time comparison) and accepts only loopback peers. The bridge calls LocalAI with `LOCALAI_API_KEY` when one is set. New error codes: `STT_STREAM_BUSY`, `STT_BRIDGE_UNAVAILABLE`, `STT_MODEL_NOT_READY`.

### 6.4 Native LocalAI streaming (check, do not build)
LocalAI 4.4.0 and newer has a streaming path for Parakeet and Nemotron through its Realtime interface. In B1, record whether the installed LocalAI exposes it. If yes, note it as a future native transport in `docs/STREAMING_BRIDGE.md`. It does not block this work, and the registry's `transports` design leaves room for it.

---

## 7. Configuration, Ports and Scripts

### 7.1 `.env` keys (visible and editable; derived URLs are computed at start, not stored)

| Key | Default |
|---|---|
| `STREAMING_BRIDGE_ENABLED` | `false` |
| `STREAMING_BRIDGE_PORT` | `8089` (internal, loopback only, auto-moved if busy like other internal ports) |
| `STREAMING_BRIDGE_STEP_MS` | `1000` |
| `STREAMING_BRIDGE_MIN_WINDOW_MS` | `1200` |
| `STREAMING_BRIDGE_SEGMENT_MAX_MS` / `SEGMENT_SILENCE_MS` | `15000` / `600` |
| `STREAMING_BRIDGE_MAX_STREAMS` | `5` |
| `STREAMING_BRIDGE_MAX_DECODES` / `MAX_DECODES_PER_MODEL` | `1` / `1` |
| `STREAMING_BRIDGE_PARTIAL_TIMEOUT_S` / `FINAL_TIMEOUT_S` / `READY_TIMEOUT_S` | `8` / `30` / `10` |
| `STREAMING_BRIDGE_IDLE_TIMEOUT_S` / `MAX_STREAM_S` | `30` / `600` |
| `STREAMING_BRIDGE_VAD` | `off` (or `energy`) |
| `STREAMING_BRIDGE_ALLOWED_MODELS` / `MAX_MODEL_MB` | `auto` / `1500` |
| `STREAMING_BRIDGE_FALLBACK_TO_BATCH` | `false` |
| `STREAMING_BRIDGE_INTERNAL_TOKEN` | generated once, never regenerated |

Nothing in `app/core/config.py` or anywhere else hardcodes `8089` or the bridge URLs; they derive from `STREAMING_BRIDGE_PORT`.

### 7.2 Scripts (follow the existing patterns in `start.sh`, `scripts/start-all.sh`, `scripts/stop-all.sh`, `scripts/preflight.sh`, `scripts/lib/`)
- Register the bridge as one more internal service in the port logic: free-port check, auto-move with write-back to `.env`, duplicate detection.
- Start order: LocalAI, TTS, Sherpa STT, **bridge**, gateway. Record the PID in the existing PID directory, log to `logs/streaming-bridge.log`, wrap it with the same supervisor and backoff.
- `stop` and `restart` use the recorded PID only. Remove any port-based cleanup for this service.
- `status`, `ports`, `doctor`, `preflight` and the startup banner show the bridge (listening, healthy, admitted limit) **only when enabled**.
- Add any new Python dependency to the existing requirements, and keep it small (a web framework, an HTTP client and WebSocket support the gateway already uses; no heavy packages).
- `/health/ready` reports `streaming_bridge` when enabled; a down bridge never marks the gateway unhealthy.

---

## 8. Safety Rules for Every Change

1. Tag `pre-bridge`; record the baseline (tests, auth matrix, Sherpa streaming results, batch STT timings, voice pipeline timings, memory) in `docs/EVIDENCE.md`; keep a copy of the database, `.env` and model list outside the repository.
2. One change per commit; tests before and after. A previously passing test that fails, or any measure more than 10% worse, means revert, not patch forward.
3. Public routes, response shapes and existing event names are frozen. Database changes are additive migrations tested on a copy of the real database.
4. With the flag off, the full test suite and baseline timings must match the baseline.
5. No secrets, transcripts or audio in logs, reports or git. No absolute local paths in committed files.
6. Nothing is marked done without command output in `docs/EVIDENCE.md`. Mark anything unverified `[Verify]`.
7. Never delete `.env`, `data/`, `models/`, `arch/` or `.venv`. Never push.
8. Anything outside this plan goes into a "Noticed but not changed" list.

---

## 9. Phases and Tasks

```
B0 Baseline ─▶ B1 Spike + go/no-go ─▶ B2 Core ─▶ B3 Scheduler ─▶ B4 Service + config
                                                                       │
 B9 Canary + regression ◀─ B8 Load/failure tests ◀─ B7 UI ◀─ B6 Scripts ◀─ B5 Gateway
```

| Phase | Work | Owner |
|---|---|---|
| **B0** | Tag, baseline, safety copy | QA |
| **B1** | **Spike and go/no-go.** For each candidate model through LocalAI, measure the decode time of 2, 4, 6 and 10 s windows (median and 95th percentile over 20 runs) and the fixed per-request overhead, on the Mac and on the RunPod GPU. Simulate the scheduler with the real numbers. Also record whether LocalAI exposes native streaming for these models (6.4) and which API reports model load status. **Go** only if: sustainable streams are at least 2 at a 1 s step (or 3 at 1.5 s), partial latency stays within about 1.5 s, and the load does not make LLM or TTS latency more than 10% worse. If not: report, and propose reducing overhead first (direct 16 kHz mono WAV, warm model), a smaller model, or staying native-only. **Stop here and report before B2.** | QA, OPS |
| **B2** | Core: session, bounded buffer, energy gate, segmenting, aligner, fake-LocalAI test double, unit tests | BE |
| **B3** | Scheduler: priority queue, single in-flight per stream, caps, stale dropping, adaptive step, cancellation, timeouts, calibration command, admission, with concurrency tests | BE |
| **B4** | Service: WebSocket, `/health`, internal token, validation, configuration keys, parameter-parity contract test | BE |
| **B5** | Gateway: registry transports, routing, WebSocket client to the bridge, event mapping, error codes, explicit fallback, per-key and per-transport caps, residency lease, usage records, runtime kill switch. Flag off by default | BE |
| **B6** | Scripts, ports, supervisor, status, doctor, banner, preflight, requirements | OPS |
| **B7** | UI: Live (native) and Live (simulated) labels, disabled-with-reason states, admin settings, fallback notice | FE |
| **B8** | Load, soak and failure tests (section 10) | QA |
| **B9** | Canary on a test instance, then the regression comparison; enable only if every gate passes | QA, ORCH |

Roster: ORCH (board, merges, acceptance), BE, OPS, FE, QA, REV (fresh context, diff only: "what could this break?").

---

## 10. Tests

**Alignment (unit and property tests):** confirmed text only grows; a contradicting decode leaves confirmed text unchanged; punctuation and case do not break matching; empty and hallucination outputs are dropped; the final reuses the last decode when no new audio arrived.

**Concurrency (with a fake LocalAI of configurable latency):**
- 1, 3, 5, 6 and 7 simultaneous streams: the first N are admitted, the rest get `BRIDGE_BUSY` with `retry_after_ms`.
- Never more than `MAX_DECODES` requests in flight, and never more than one per stream.
- With a slow LocalAI: partials are skipped or the step grows (`degraded`), the queue stays bounded, and finals are served before partials.
- A fast talker and a slow talker are served fairly.
- Cancel and disconnect mid-decode: the slot is released and no job stays queued.
- LocalAI errors and timeouts: the stream survives; errors are reported; nothing hangs.
- A 30-minute soak with stream churn: no memory growth and no leaked tasks or sockets.

**Integration:**
- Bridge events map to the existing UI fields exactly; unknown events are ignored by the UI.
- Explicit fallback behaves as configured, and is counted.
- With the flag off, behavior and test results are identical to the baseline.
- With the bridge stopped, the gateway stays healthy and Sherpa, batch STT and the voice pipeline keep working.
- The model residency lease prevents an idle flush during a stream; usage and model-activity records appear for bridge streams.
- Request-parameter parity between the bridge and the batch provider.
- Internal token: a connection without it is rejected; a non-loopback peer is rejected.

**Scripts:** port auto-move with write-back, restart applies an edited port, stop touches only the recorded PID, status and banner show the bridge only when enabled.

**Quality:** known clips through each allowed model: final word error rate against batch, first partial time, final delay, flicker and conflict counts.

---

## 11. Acceptance Checklist

- [ ] With the bridge disabled, the full test suite, auth matrix and baseline timings match `pre-bridge`.
- [ ] The B1 report exists with measured numbers and a go or no-go decision, signed off by the owner before B2.
- [ ] With the bridge enabled, an allowed Whisper or Parakeet model shows live partial text while speaking and a correct final text after speech ends.
- [ ] Confirmed text never changes within a segment.
- [ ] 5 simultaneous streams work within the admitted limit; extra streams get a clear busy error; none hangs.
- [ ] While streams run, batch STT, the LLM and TTS stay within 10% of baseline latency (or limits were lowered until they do).
- [ ] The bridge stopping or crashing does not affect Sherpa streaming, batch STT, the voice pipeline or the gateway's health.
- [ ] Fallback to batch happens only when enabled, and is visible and counted.
- [ ] The port is editable in `.env` and applied by `./start.sh restart`; nothing hardcodes `8089`; stop never uses a port number.
- [ ] The bridge accepts only loopback connections carrying the internal token.
- [ ] No transcripts, audio or secrets in logs.
- [ ] A model in use by a bridge stream is never flushed as idle; usage records exist.
- [ ] The kill switch (runtime setting, or flag off plus restart) returns the system to the previous behavior.
- [ ] `docs/STREAMING_BRIDGE.md` documents the design, settings, calibration and troubleshooting.

---

## 12. Risks and Guards

| Risk | Guard |
|---|---|
| Per-request overhead makes the bridge unusable | B1 gate; fix overhead or stay native-only |
| Partial decodes slow the LLM and TTS | Global cap, adaptive step, finals-only under load, latency checks in the canary |
| Hallucinated text from noise | Energy gate, junk-output filter, small allowed models |
| Users mistake simulated streaming for native quality | Visible "simulated" label and tooltip |
| Cold model stalls the first partial | `stream.ready` after warm-up, explicit `MODEL_NOT_READY` |
| Leaked slots after errors or disconnects | Release in `finally`, tests with cancels and kills |
| Calibration loads models and evicts others | Offline only; one stream per model until calibrated |
| Flag turned on by mistake | Off by default, per-model allowlist, kill switch |
| Another process on the host uses the bridge | Loopback plus internal token |
| Gateway changes alter existing behavior | Routing behind the flag; identical-behavior regression test |
| Idle-model flush unloads a model during a stream | Residency lease |

---

## 13. Reporting Format and Stop Conditions

After each phase write to `docs/EVIDENCE.md`: what was done, the commands run with their output, test results before and after, and open questions.

**Stop and report to the owner (do not continue) if:** the B1 gate fails; any baseline test regresses; a measure is more than 10% worse; an existing event name, route or response shape would have to change; the LocalAI API behaves differently than assumed; or anything needs deleting, moving or pushing.

---

## 14. Starting Prompt

> You are the orchestrator for the streaming bridge. Read `streaming-bridge-final-plan.md` (this plan wins) and `universal_streaming_bridge_plan.md` (reference only). Start each agent in its own herdr pane with the model pinned and confirmed in the status line. Do B0 and B1, then **stop and report the go/no-go with measured numbers** before writing any bridge code. Follow section 8 for every change. The flag stays off by default; Sherpa and batch paths stay untouched; ports come from `.env`; stop uses recorded PIDs only; fallback is never silent. Mark anything unverified `[Verify]` and stop and report instead of guessing.

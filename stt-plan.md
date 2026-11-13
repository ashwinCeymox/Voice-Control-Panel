# Streaming STT Plan (runs after Phase 1, before Plan 2)

**Goal:** Add a streaming speech-to-text engine alongside the existing LocalAI Whisper path, and let users choose between the two (**Batch** or **Streaming**) per agent, per request and as a global default.
**Why:** The other project needs (a) live captions while the user speaks and (b) faster replies in a voice agent.
**Agents:** all Gemini 3.8 Flash (high), one herdr pane each, model pinned and confirmed in the status line before any task is sent.

**Reading of "select between two":** the user picks **Batch** (LocalAI Whisper, sends one finished clip) or **Streaming** (live partial text). Every STT model is tagged `batch` or `streaming`, so the same design also covers choosing between two specific models. Tell me if you meant something else.

---

## 1. Preconditions (verify before starting)

Phase 1 is reported complete. Confirm these exist, because this plan builds on them. Anything missing becomes a task before S2.

| Check | Needed for |
|---|---|
| `STTProvider` interface (`transcribe`, `stream`) in the gateway | S2 adapter |
| API keys with roles, scopes and per-key limits | New `stt_stream` scope and stream limits |
| Global and per-key concurrency limiter | Stream caps |
| Hardening list H1 to H11 merged; no default key | Safe exposure of the new endpoint |
| P0 raw measurements saved (batch STT latency, overhead check) | Baseline to beat |
| Linux container setup (Compose) | Running the new service |
| Current `schema_version` of the agent schema and migration code | S5 |
| Git tag `pre-streaming` on the current commit | Rollback and diff review |

---

## 2. Design

```
Browser (VAD) ──frames during speech──▶ Gateway ──▶ Streaming STT service (private, 127.0.0.1)
      ▲                                   │             (engine from the S1 decision)
      └──── transcript.partial ◀──────────┘◀── partial results
                                          │
Batch path (unchanged): finished WAV ───▶ LocalAI Whisper
```

**Mode selection (one model list, two kinds)**

| Where | Behavior |
|---|---|
| Agent config | `stt: { kind: "batch" \| "streaming", model, fallback_model }`. Default for existing agents: `batch` |
| Agent editor and STT page | A **Batch / Streaming** switch. It filters the model dropdown to that kind. Streaming is disabled, with a reason, if the service is unhealthy or no streaming model is installed (same "install first" guard as other models) |
| Voice session | `session.start` may carry an `stt_kind` override; otherwise the agent's setting applies |
| API callers | Batch stays at `POST /v1/audio/transcriptions`. Streaming is `WS /v1/audio/transcriptions/stream`, behind a new `stt_stream` scope |
| Admin | Global default and a kill switch (`STT_STREAM_ENABLED`). When off, every streaming request uses batch |
| Automatic fallback | If the streaming service is down, overloaded or fails mid-turn, the turn falls back to batch and the client is told (`stt.fallback` event). The fallback is reported, never silent |

**Events (additions to `app/schemas/events.py`)**

- client → server: `stream.start` (model, language, sample rate 16000, format `pcm_s16le`), binary PCM frames, `audio.commit`, `stream.cancel`
- server → client: `transcript.partial` (`seq`, `confirmed`, `tentative`), `transcript.completed` (final, unchanged), `stt.fallback` (reason), `error`

Rules: confirmed text never changes, only the tentative tail may be revised, and **only the final transcript goes to the LLM**. Existing silence, unclear-speech and ask-to-repeat gates run on the final transcript.

**Authentication for the stream endpoint:** server-to-server callers use the `Authorization` header with a key that has the `stt_stream` scope. Browsers cannot set headers on WebSockets, so the browser UI keeps using the existing one-time ticket.

**Endpoint summary (what is new and what only changes)**

| Endpoint | Status | Who calls it | Purpose |
|---|---|---|---|
| `WS /v1/audio/transcriptions/stream` | **New, public** | Other projects (key with `stt_stream` scope) | Live partial and final transcripts outside the voice pipeline |
| `GET` and `PUT /v1/admin/settings/stt` (admin role only) | **New, small** | Admin UI | Global default mode, kill switch, maximum streams. Needed so the kill switch works without a restart |
| Streaming STT service endpoint (its own WebSocket and health URL on `127.0.0.1`) | **New, internal only** | Gateway only | The engine itself. Never exposed publicly |
| `WS /v1/voice/stream` | Changed, not new | Your web UI | New events (`transcript.partial`, `stt.fallback`); the browser streams frames during speech. Old clients that send one WAV keep working in batch mode |
| `POST /v1/audio/transcriptions` | Unchanged | Other projects, batch use | Batch STT through LocalAI |
| `GET /v1/models/installed` | Extended | UI | Adds `kind` and `provider` fields so streaming models are listed |
| `GET /health/ready` | Extended | Monitoring, UI | Adds the streaming service status |
| `/v1/agents/...` | Schema change only | UI | New `stt.kind` and `stt.fallback_model` fields |

Installing streaming models is done by the S1 download script, not by an API endpoint. An admin install endpoint is not planned.

---

## 3. Phases

```
S0 Verify ─▶ S1 Engine decision + service ─▶ S2 Gateway adapter ─▶ S3 Protocol + endpoint
                                                                          │
S7 Rollout ◀─ S6 QA + load ◀─ S5 Mode selection (schema, UI) ◀─ S4 Voice pipeline + frontend
```

### S0: Verify and prepare
Check section 1, tag `pre-streaming`, record where the new pieces will live (service folder, port, config names).

### S1: Engine decision and service bring-up

Run the engine as its own service bound to `127.0.0.1`, started by the same startup script or Compose file, with a health endpoint.

**Candidates (test on the same clips)**

| ID | Candidate | Likely home |
|---|---|---|
| E1 | sherpa-onnx streaming server with a streaming zipformer model | M2 and CPU servers. **Default first adapter**: it has a ready streaming WebSocket server and takes 16 kHz mono 16-bit audio, matching the VAD output |
| E2 | Moonshine v2 Tiny | M2 and CPU; check license and accuracy |
| E3 | Nemotron streaming 600M | English, NVIDIA GPU server |
| E4 | Voxtral Mini 4B Realtime (vLLM `/v1/realtime`) | 16 GB class GPU, multilingual |
| E0 | Reference: Whisper plus LocalAgreement-2 as its own service | Baseline for quality |

**Measure:** time to first partial, update interval, final delay after speech end, word error rate versus batch, confirmed-word changes (must be zero), real-time factor per stream, memory, and the highest concurrent stream count (3 and 5) that still meets the targets, on the M2 and on the cloud server.

**Targets (adjust if needed):** first partial within 1.0 s of speech start, updates at least every second, final within 300 ms of speech end, accuracy within 2 points of batch, five concurrent streams with no missed targets on the chosen server.

**Packaging:** pin versions, a script that downloads model files into a configured folder, license recorded for the chosen model. **Output:** `docs/decisions/stt-engine.md` (winner, losers, numbers, license).

### S2: Gateway adapter
- `app/providers/streaming_stt_client.py` implementing `STTProvider.stream()` for the chosen engine. Engine-specific parts stay inside this file so another engine can be added later.
- Settings in `core/config.py` (URL, enabled flag, model folder, timeouts, max streams). No hardcoded model names.
- Health check added to `/health/ready`, reported separately from LocalAI.
- Installed-models list includes streaming models with `kind: streaming` and `provider: stream-stt`, read from the service or its model folder.
- Error mapping to the existing error classes; upstream text is never returned to clients.
- Limiter: a separate stream bucket (global and per key), usage logging of streamed seconds, no content logged.

### S3: Protocol and public endpoint
- New events in `events.py`, schemas, and tests.
- `WS /v1/audio/transcriptions/stream` with the scope check, size and duration limits, idle timeout, and `429`/`503` behavior consistent with Phase 1.
- Update the OpenAPI file and `ws-events.md`.

### S4: Voice pipeline and frontend
- `voice.py`: streaming path (open stream on speech start, forward frames, flush on `audio.commit`, final to the existing gates and LLM), with the automatic fallback to batch.
- `vadController.ts`: send PCM16 frames after speech starts. Keep a small ring buffer so the 200 ms before speech is sent first. Check which frame callback the installed vad-web version provides.
- `Assistant.tsx`: show live captions in the user bubble (confirmed in normal text, tentative greyed), replace with the final text, and show a small notice if fallback happened. Barge-in logic is unchanged.
- Optional speculative commit after about 400 ms of silence, discarded if speech resumes (behind its own flag, default off until measured).

### S5: Mode selection
- Bump the agent `schema_version` and migrate: existing agents get `kind: batch`, no change in behavior.
- Agent editor and STT page: the Batch / Streaming switch, model dropdown filtered by kind, reasons shown when streaming is unavailable.
- Admin settings: global default, kill switch, maximum streams.
- Models page: streaming models appear under Installed with their kind and size; they are managed by the S1 download script or an admin action, not by the LocalAI gallery.
- STT playground: Streaming mode shows live captions from the microphone or a played file, plus timings.

### S6: QA and load
- Replay script: WAV files sent at real-time speed in 0.1 s chunks; outputs first-partial time, final delay, change rates and accuracy.
- Load: 1, 3 and 5 simultaneous streams, then 6 to check limits; 30-turn soak; memory growth.
- Failure tests: kill the service mid-stream, drop the client, send bad frames, oversize audio, expired key, key without scope.
- Security: scope check, no content in logs, no upstream error text in responses.
- REV reviews each branch from the diff only.

### S7: Rollout
Flag defaults to `batch`. Enable streaming for one test agent, then for the other project's calls, compare the measured numbers with the targets, and only then discuss changing the default.

---

## 4. Roster and Tasks

| ID | Role | Owns |
|---|---|---|
| ORCH | Orchestrator | Board, decisions, merges, acceptance |
| BE | Backend | S2, S3, S5 backend parts |
| FE | Frontend | S4 frontend, S5 UI |
| OPS | DevOps | S1 service, packaging, startup and Compose, health |
| QA | Tester | S1 bake-off, replay script, S6 |
| REV | Reviewer (diff only, fresh context) | Every branch |

| Task | Owner | Depends on |
|---|---|---|
| Preconditions check, tag | ORCH | none |
| Replay and measurement script | QA | none |
| Bring up candidate E1 service; run bake-off with E0 to E4 as hardware allows | OPS + QA | script |
| Engine decision record | ORCH | bake-off |
| `streaming_stt_client.py`, config, health, installed list | BE | decision |
| Events, schemas, WS endpoint, scope, limits | BE | adapter |
| `voice.py` streaming path and fallback | BE | adapter, events |
| `vadController.ts` frame streaming, captions in `Assistant.tsx` | FE | events |
| Schema bump, migration | BE | none |
| Agent editor and STT page switch, admin settings, Models page | FE | schema bump |
| Load, soak, failure, security tests | QA | all above |
| Review of every branch | REV | each branch |

Rules: failing test first, handoff file per task in `docs/handoff/<id>.md`, only ORCH merges, no secrets or transcripts in logs.

---

## 5. Acceptance Checklist

- [ ] Streaming engine decision recorded with measured numbers and license.
- [ ] Batch / Streaming switch works in the agent editor, STT page and per request; existing agents behave exactly as before.
- [ ] Live partial captions appear; confirmed words never change.
- [ ] Final transcript arrives within the agreed target of speech end (or the measured value is accepted by the owner).
- [ ] Only the final transcript reaches the LLM; existing gates still apply.
- [ ] If the streaming service is stopped mid-turn, the turn falls back to batch and the UI says so.
- [ ] Streaming unavailable shows a clear reason; no streaming agent can be saved without an installed streaming model.
- [ ] `stt_stream` scope enforced; a key without it is rejected; limits give `429`/`503` as designed.
- [ ] 5 concurrent streams meet the targets on the chosen server; the 6th is limited or falls back cleanly.
- [ ] Kill switch turns every request to batch without restart.
- [ ] No transcripts, audio or upstream errors in logs or client responses.
- [ ] Rollback to the `pre-streaming` tag restores the previous behavior.

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| Streaming accuracy is below batch | Accuracy target in S1; two-tier option (streaming for captions, batch Whisper for the final); keep batch default |
| Too much compute at 5 streams | Stream cap, fallback to batch, GPU option decided from S1 numbers |
| Frame callback missing or different in the installed vad-web version | Check in S4 first; fall back to the library's lower-level audio worklet |
| Lost first syllable | Ring buffer of recent frames sent at speech start |
| Engine license limits commercial use | Record license in S1 before choosing |
| Gallery does not manage streaming models | Download script and an installed list from the service, with clear admin instructions |
| Browser WebSocket cannot send headers | Keep ticket auth for the UI; header auth only for server-to-server |
| Caption text flicker | Confirmed/tentative split; tentative text visually distinct |

---

## 7. Starting Prompts

**ORCH:**
> You are the orchestrator for the streaming STT addition. Read `AGENTS.md`, `cloud-migration-plan.md` and `streaming-stt-plan.md`. Verify the preconditions, tag `pre-streaming`, create worktrees and `docs/board.md`. Start each agent in its own herdr pane with the model pinned and confirmed in the status line. Do not start S2 until `docs/decisions/stt-engine.md` exists.

**QA / OPS (S1):**
> Build the replay script first. Then run each candidate on the same clips and report the measures in the plan, with raw per-request data and the machine used. Mark any estimated value ESTIMATED. Do not choose the winner; write the numbers.

**Builder (BE, FE):**
> Read only your task and the files it lists. Write a failing test first. Keep engine-specific code inside the adapter. Never log or return transcripts, audio or upstream error text. Paste test output into `docs/handoff/<id>.md`.

**REV:**
> You see only the diff, the task and the acceptance checklist. Try to break it: service down mid-stream, key without scope, 6 simultaneous streams, malformed frames, oversize audio, mode switch during a session, an old agent without the new fields. Return pass or fail with required changes.

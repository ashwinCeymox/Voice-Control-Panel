# VAD Integration Plan: Silero VAD (`@ricky0123/vad-web`) into the Voice AI Control Panel

**Goal:** Replace the current speech-end and noise handling with Silero VAD running in the browser, so that only clean, complete speech segments reach Whisper and the LLM.
**Agents:** all run Gemini 3.8 Flash (high), one herdr pane each, model pinned and confirmed in the status line before any task is sent.
**Machine:** MacBook Air M2, 8 GB.

---

## 1. Target Flow

```
Mic ─▶ getUserMedia (echoCancellation, noiseSuppression)
    ─▶ [optional RNNoise worklet]
    ─▶ Silero VAD (vad-web)
          ├─ onSpeechStart  ─▶ orb = listening; if agent is speaking, start barge-in check
          ├─ onVADMisfire   ─▶ discard silently (too short to be speech)
          └─ onSpeechEnd(Float32Array, 16 kHz)
                 ─▶ volume gate (RMS)         ─▶ too quiet: drop
                 ─▶ send segment + audio.commit over WS
                        ─▶ Whisper (LocalAI)
                        ─▶ confidence gate    ─▶ unclear: agent says "Sorry, I didn't catch that"
                        ─▶ LLM ─▶ TTS ─▶ playback
```

Key facts from the library docs this plan relies on:

- `onSpeechEnd` delivers a Float32Array at 16 kHz and does not fire if the segment is shorter than `minSpeechMs`; `onVADMisfire` fires instead.
- Speech starts when a frame exceeds `positiveSpeechThreshold`. It ends after frames stay below `negativeSpeechThreshold` for `redemptionMs`. `preSpeechPadMs` of audio is prepended to the segment.
- `redemptionMs` is therefore the **silence the agent waits for before replying**.
- The worklet file, the `.onnx` model and the onnxruntime-web WASM files must be served by your frontend, via `baseAssetPath` and `onnxWASMBasePath`.
- **Defaults differ between versions and forks** (for example `redemptionMs` appears as 768 in some docs and 1400 in others). Read the defaults from the installed package, not from documentation.

---

## 2. Agent Roster

| ID | Role | Owns |
|---|---|---|
| **ORCH** | Orchestrator | Task board, decisions, merges, acceptance |
| **DIAG** | Diagnostician (read-only) | Phase 0 inventory of the current setup |
| **FE-VOICE** | Frontend + audio | VAD wiring, asset serving, orb, barge-in, settings UI |
| **BE** | Backend | WebSocket protocol changes, Whisper options, confidence gate, schema migration |
| **QA** | Tester | Test clips, metrics, regression checks |
| **REV** | Reviewer (fresh context, sees diff only) | Tries to break each change |

Rules: small tasks, failing test first, handoff files in `docs/handoff/<id>.md`, only ORCH merges, coordination through `docs/board.md`.

---

## 3. Phases

```
P0 Inspect ─▶ P1 Spike ─▶ P2 Wire VAD ─▶ P3 Protocol ─▶ P4 Gates ─▶ P5 Settings ─▶ P6 QA ─▶ P7 Cutover
```

### P0: Inspect the current setup (DIAG, no code changes)

Write `docs/vad-inventory.md` answering:

1. How is speech start and end detected today (client, gateway, Whisper options, or a button)?
2. What does the client send over `WS /v1/voice/stream` (frame size, format, sample rate), and when is `audio.commit` sent?
3. Which Whisper options are set in the LocalAI call (VAD, thresholds, `condition_on_previous_text`, temperature)?
4. Does the transcription response include per-segment `no_speech_prob`, `avg_logprob`, `compression_ratio`? Log one real response.
5. Which frontend build tool is used, and where are static assets served from?
6. Where do the existing agent VAD settings live (`threshold`, `min_silence_ms`) and which code reads them?
7. Which old turn-taking code (silence timers, Stop speaking handlers, `turn.cancel` triggers) will become dead code?

Exit: ORCH confirms the inventory before P1.

### P1: Spike (FE-VOICE, QA)

Build a standalone test page (not part of the app) that:

- loads Silero VAD with the assets served locally,
- shows live speech probability, start/end/misfire events, segment length and RMS,
- plays back each captured segment,
- lets you change the five VAD parameters live.

QA records a **test set** (30 to 60 s each, saved as WAV):

| Clip | Purpose |
|---|---|
| Quiet room, normal speech | Baseline |
| Fan or air conditioner | Steady noise |
| Keyboard and mouse clicks | Impulse noise |
| TV or music in background | Other voices |
| Coughs, throat clearing, laughs | Non-speech human sounds |
| Very short answers ("yes", "stop") | `minSpeechMs` risk |
| Long mid-sentence pauses | `redemptionMs` risk |
| Agent speaking through laptop speakers | Echo and barge-in |

Output: `docs/decisions/vad-params.md` with a recommended starting value for each parameter, backed by results on these clips.

### P2: Wire VAD into the voice page (FE-VOICE)

- Add the assets (worklet, model, WASM) to the build so they are served from your own origin. Do not depend on a CDN, since the app is self-hosted.
- Create one `VadController` module that owns the VAD instance (create, start, pause, destroy, `setOptions`) and exposes events. The voice page uses only this module.
- Start the VAD when the user begins a session and destroy it on End session or page leave. Handle denied microphone permission with a clear message.
- Drive the orb from VAD events and frame probabilities (listening state when speech starts).
- Feature flag `vad_engine = silero | legacy`, default `legacy` until P7, so the old path can be restored instantly.

### P3: Protocol changes (BE, FE-VOICE)

Decide and document in `ws-events.md`:

- **Segment upload:** on `onSpeechEnd`, convert Float32 to 16-bit PCM, send as binary frames, then `audio.commit`. Nothing is sent while there is no speech.
- **New client events:** `vad.speech_start` (for the server to log and for barge-in) and optionally `vad.misfire` (log only).
- Backend rejects empty or too-short uploads with an `error` event instead of calling Whisper.
- Avoid double gating: if Whisper-side VAD is enabled in LocalAI, test with it off or lenient first, since two VADs can clip the start of speech.
- Set Whisper options for short clean segments: `condition_on_previous_text` off, temperature 0, keep the confidence thresholds from P4.

### P4: Gates that stop junk reaching the LLM (BE, FE-VOICE)

Applied in this order:

1. **Misfire:** segments shorter than `minSpeechMs` are dropped by the VAD.
2. **Volume gate:** compute RMS on the segment before upload. Drop it if below the agent's `min_rms`. Set the default from the P1 clips, not from another project's value.
3. **Transcript sanity:** discard empty, punctuation-only, or very short transcripts, and transcripts with a high compression ratio (repeated text).
4. **Confidence gate:** if the response exposes `no_speech_prob` and `avg_logprob`, treat a high `no_speech_prob` combined with a low `avg_logprob` as unclear. If it does not (P0 question 4), fall back to rule 3 only and record the limitation.
5. **Ask to repeat:** when 3 or 4 triggers, the LLM is not called. The agent plays a short fixed phrase ("Sorry, I didn't catch that. Could you repeat?"), generated once and cached per voice. Limit to two repeats in a row, then wait quietly.

### P5: Barge-in and agent settings (FE-VOICE, BE)

**Barge-in (replaces any Stop speaking button)**

- The VAD stays active while the agent speaks.
- When speech starts during playback, require a short confirmation window (starting value from P1, for example a few hundred milliseconds of continued speech) before cancelling. On confirmation: stop playback, clear the audio queue, send `turn.cancel`.
- While the agent is speaking, raise `positiveSpeechThreshold` using `setOptions` and restore it afterwards, so the agent's own voice from the speakers is less likely to trigger it.
- Show a "Use headphones for best results" hint when false barge-ins are detected repeatedly.

**Agent schema (bump `schema_version`)**

```json
"vad": {
  "engine": "silero",
  "positive_speech_threshold": 0.5,
  "negative_speech_threshold": 0.35,
  "redemption_ms": 800,
  "pre_speech_pad_ms": 200,
  "min_speech_ms": 250,
  "min_rms": 0.0,
  "noise_suppression": "browser",
  "barge_in": true,
  "barge_in_confirm_ms": 300,
  "playback_threshold_boost": 0.15
}
```

All numbers above are placeholders until P1 fills them in.

- Write a migration that maps old `threshold` and `min_silence_ms` to the new fields so saved agents keep working.
- Agent editor gets a VAD section with plain-language labels:
  - "How long to wait after you stop talking" → `redemption_ms`
  - "Sensitivity" → threshold pair
  - "Ignore very short sounds" → `min_speech_ms`
  - "Ignore quiet sounds" → `min_rms`
  - "Allow interrupting the agent" → `barge_in`
- A live meter in the editor shows speech probability and when a turn would end, so settings can be tested without a full conversation.

### P6: QA (QA, REV)

- Replay the P1 clips through the new pipeline and record metrics:
  - false triggers on noise-only clips (target: zero in a 5-minute noise clip)
  - clipped first words on quiet speech
  - premature cutoffs during pauses
  - time from end of speech to first agent audio
  - false barge-ins with speakers and with headphones
- Hardcode scan: no CDN URLs for VAD assets, no leftover legacy turn-taking code.
- Memory and CPU on the M2 Air with VAD, Whisper, LLM and TTS loaded: record peak memory, check the audio worklet does not cause glitches, and run 10 consecutive turns.
- REV checks: microphone denied, tab backgrounded, device unplugged, double start, session end while a segment is in flight, very long speech (the VAD splits or the server limits length).

### P7: Cutover

- Switch the default to `silero` after P6 passes. Keep `legacy` for one release.
- Delete the legacy path afterwards.
- Update `docs/` and the frontend plan.

---

## 4. Task Board

| ID | Owner | Task | Depends on |
|---|---|---|---|
| D1 | DIAG | Current-setup inventory | none |
| S1 | FE-VOICE | Standalone VAD test page | D1 |
| S2 | QA | Record test clip set | none |
| S3 | QA + FE-VOICE | Parameter results and `vad-params.md` | S1, S2 |
| V1 | FE-VOICE | Serve assets locally, `VadController`, feature flag | S1 |
| V2 | FE-VOICE | Mic permission and error states | V1 |
| V3 | FE-VOICE | Orb states from VAD events | V1 |
| P1 | BE + FE-VOICE | Segment upload protocol, new events | D1, V1 |
| P2 | BE | Whisper options and empty-upload rejection | D1 |
| G1 | FE-VOICE | RMS volume gate | S3 |
| G2 | BE | Transcript sanity and confidence gate | D1 |
| G3 | BE + FE-VOICE | "Ask to repeat" phrase and loop limit | G2 |
| B1 | FE-VOICE | Barge-in with confirmation and threshold boost | V1, S3 |
| A1 | BE | Schema bump and migration | S3 |
| A2 | FE-VOICE | Agent editor VAD section and live meter | A1 |
| Q1 | QA | Full metrics run on P1 clips | all above |
| R1 | REV | Review every branch | each branch |
| C1 | ORCH | Cutover and legacy removal | Q1 |

---

## 5. Acceptance Checklist

- [ ] Noise-only audio (fan, keyboard, TV) produces no turns and no LLM calls.
- [ ] Quiet normal speech is not clipped at the start.
- [ ] A mid-sentence pause shorter than the agent's silence setting does not end the turn.
- [ ] The agent replies after the configured silence, with no manual button.
- [ ] Speaking over the agent stops it after the confirmation window (headphones and speakers tested).
- [ ] Unclear speech gets the "please repeat" reply and never reaches the LLM.
- [ ] Assets load from the app's own origin with no internet access.
- [ ] Existing saved agents still load after the schema migration.
- [ ] VAD settings can be edited per agent with a live meter.
- [ ] Ten consecutive turns on the M2 Air without errors or memory growth.
- [ ] The `legacy` flag restores the old behavior until removal.

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| Documentation defaults do not match the installed version | Read defaults from the package; pin the version; record it in `AGENTS.md` |
| Echo from laptop speakers triggers false barge-in | Echo cancellation on, confirmation window, threshold boost, headphones hint |
| Two VADs (browser and Whisper-side) clip speech | Test with the server one off; decide in P3 |
| WASM or worklet assets fail to load in the build | Asset check in P2 and in QA, no CDN fallback |
| Whisper response lacks confidence fields | Fall back to transcript sanity checks only |
| Silero misses quiet speakers | Tune thresholds from clips; expose sensitivity per agent |
| Other voices (TV, people nearby) still pass | Known limitation; only speaker verification would address it |

---

## 7. Starting Prompts

**ORCH:**
> You are the orchestrator for the Silero VAD integration. Read `AGENTS.md` and `vad-integration-plan.md`. Create worktrees and `docs/board.md`. Start each agent in its own herdr pane with the model pinned, and confirm the model in the status line before sending a task. Run P0 first. Do not start P2 until `docs/decisions/vad-params.md` exists.

**DIAG:**
> Read-only. Answer the seven questions in P0 with evidence (file paths, a logged WS exchange, a logged transcription response). Write `docs/vad-inventory.md`. Do not change code.

**Builder (FE-VOICE, BE):**
> Read only your task in `docs/board.md` and the files it lists. Write a failing test first. Implement within your owned files. Run the tests and paste results into `docs/handoff/<id>.md`. Do not hardcode thresholds; read them from the agent's VAD settings.

**REV:**
> You have only the diff, the task and the acceptance checklist. Try to break the change: denied microphone, tab in background, unplugged device, double start, session end mid-segment, extremely long speech. Return pass or fail with required changes.

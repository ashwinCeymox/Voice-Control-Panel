# Fix: Streaming STT 502, Model Not Found, False "No Speech", Delete Not Working

**Reported symptoms**

| # | Symptom |
|---|---|
| S1 | `Streaming STT engine error: 502: Streaming STT service is unreachable or returned a gateway error. Falling back to batch Whisper.` |
| S2 | Choosing an installed streaming model returns **model not found** |
| S3 | The voice page shows **"(no speech detected)"** under "You said" even though speech was given |
| S4 | Removing a model does nothing; the action is not reflected |

**What the symptoms already tell us**
- A fallback to batch Whisper **is** happening, so the "silent fallback" suspect from `stt-fallback-diagnosis-and-fix.md` is partly confirmed. At least this one is announced to the user.
- S1 and S2 are probably one problem seen twice: the selected model cannot be found or loaded by the streaming service, and the gateway reports that as a generic 502.
- S3 is probably a **second bug inside the fallback**: the audio was streamed to the failing service, and the gateway may no longer hold it in a form batch Whisper can read.
- S4 is probably a **delete path problem for streaming models**: they are not managed by LocalAI's gallery, so a delete sent through the gallery route has nothing to remove.

These are hypotheses. Phase 1 confirms each with evidence before any fix.

**Agents:** Gemini 3.8 Flash (high), one herdr pane each, model pinned and confirmed in the status line before any task is sent. One builder, one tester, one reviewer (fresh context, diff only) is enough for this fix.

---

## 0. Rules

1. Phase 1 is read-only. No source changes until `docs/fix-diagnosis.md` has evidence for each symptom.
2. Do not touch live model folders during diagnosis. Use fixtures and copies.
3. Use test clips only. No real transcripts, audio or keys in logs or reports.
4. Each fix starts with a failing test that reproduces the symptom.
5. Mark every claim CONFIRMED, RULED OUT or UNKNOWN, with file and line, command output or a measured result.

---

## 1. Phase 1: Diagnose

### S1 and S2: service unreachable and model not found

1. **Is the streaming service running, and on which port?**
   ```bash
   ps aux | grep -iE "sherpa|parakeet|streaming|stt" | grep -v grep
   lsof -iTCP -sTCP:LISTEN -nP | grep -iE "sherpa|parakeet|python|stt"
   curl -sS -i http://127.0.0.1:<PORT>/<health-path>
   ```
2. **Which URL and port does the gateway call?** Read the adapter and `core/config.py`. Do they match the running service?
3. **Capture the exact failure.** Reproduce S1 and record the gateway log lines, the exception type, and the **upstream response body** (connection refused, timeout, 404 model not found, protocol error, or other).
4. **Which model does the service actually have loaded?** Process arguments, open model files (`lsof -p <PID>`), and any info or health field.
5. **Model resolution.** For each installed streaming model in the dropdown, record: registry id, display name, the string sent to the adapter, the string the service expects. Do they match?
6. **Runtime coverage.** The names suggest two runtimes (`sherpa-onnx`, `parakeet-cpp`). Does an adapter and a running service exist for each? What happens when a parakeet model is selected and only the sherpa service exists?
7. **Error mapping.** Which code converts "connection refused", "model not found" and "bad request" into 502? List every place.

### S3: false "(no speech detected)"

1. **Where does the string come from?** Search the whole repository for `no speech detected`. Is it produced by LocalAI, by the gateway, or by the frontend?
2. **Trace one failed turn from streaming failure to the screen:**
   - Does the gateway keep the turn's audio while streaming (a buffer), or does it only forward frames?
   - What exactly is sent to batch Whisper after the fallback: a full WAV with a header, raw PCM, or an empty body?
   - What does LocalAI return (status and body)?
3. **Check the swallow rule.** The earlier design treats LocalAI decode errors (such as "invalid data found", "exit status 183", "ebml header") as an unintelligible or empty frame. After a fallback, is a decode error being converted into "(no speech detected)"?
4. **Audio statistics for the failing turn** (test clip, debug dump to a test folder): sample rate, duration, RMS, peak, whether it is all zeros or empty.
5. **Control test:** send the same clip to batch Whisper directly (no streaming attempt). If that works, the bug is in the fallback path and not in the audio capture.

### S4: delete not reflected

1. **What does the UI call** when Remove is pressed? Record the request, response code and body from the browser network tab.
2. **Which backend route handles it,** and which engine does it send the deletion to (LocalAI gallery, a streaming model manager, or nothing)?
3. **Files before and after:** list the model folder for that model before and after pressing Remove. Did anything change?
4. **Is the model in use?** Loaded by the running service, referenced by an agent, or protected as the active model?
5. **Registry and UI after the call:** does the registry still list it (cache, no refresh), does the UI refetch, and does it show any result or error?
6. **Logs** for the delete request.

**Deliverable:** `docs/fix-diagnosis.md` with a verdict for each symptom and the root cause in plain language. Stop and wait for approval.

---

## 2. Phase 2: Fixes

### Fix A: streaming availability, model resolution and honest errors (S1, S2)

1. **Preflight at session start.** On `stream.start`, the gateway checks that the selected streaming model exists and is loaded, and replies `stream.ready` or an explicit error **before** the user speaks. The voice page disables Start Conversation, or shows a clear banner, when the selected streaming model is not ready.
2. **Correct model resolution.** The adapter resolves the registry id to the engine, port and model name from one place (the registry). The service reports its loaded model id, and the adapter verifies it matches.
3. **One service or process per runtime, routed by model.** Each streaming model maps to a running service that supports it. A model with no working adapter or service is marked **unavailable** in the registry with a reason, and cannot be selected.
4. **Loading on demand (optional, if the service supports it).** Selecting a model that is installed but not loaded triggers a load with a visible loading state instead of an error.
5. **Precise error codes** (replace the blanket 502):

   | Situation | Code | Client message |
   |---|---|---|
   | Service not reachable | 503 `STREAMING_STT_UNAVAILABLE` | "Streaming service is not running" |
   | Model not found or not loaded | 404 or 409 `STT_MODEL_NOT_LOADED` | "Model X is installed but not loaded" or "not installed" |
   | Bad audio format | 400 `STT_BAD_AUDIO` | what was wrong (rate, channels) |
   | Service returned something invalid | 502 `STREAMING_STT_BAD_RESPONSE` | generic, details in server logs |
6. **Startup self-test.** For each streaming model, transcribe a short fixture; a failing model is marked unavailable.

### Fix B: a correct fallback, and honest silence (S3)

1. **Keep the turn's audio.** While streaming, the gateway keeps the turn's PCM in a bounded buffer (for example up to 120 seconds, then it stops streaming and reports a limit). On fallback it builds a proper **16 kHz, mono, 16-bit WAV with header** from that buffer and sends it to batch Whisper, so the fallback transcribes the whole utterance.
2. **Never turn a failure into "no speech".**
   - "(no speech detected)" is shown only when the audio really is silent (measured RMS or VAD result below the threshold) **and** the engine returned empty text.
   - Decode errors, empty buffers and engine errors produce an explicit `STT_DECODE_ERROR`, `AUDIO_EMPTY` or similar event. The session continues, the UI shows "Audio couldn't be processed" with a short reason, and the details go to the log.
   - Remove the rule that treats decode errors as an empty or unintelligible frame.
3. **Audio statistics per turn** in the log (no content): sample rate, duration, RMS, peak, bytes sent to each engine.
4. **UI wording.** Do not show "(no speech detected)" inside the "You said" box as if the user said it. Show a separate neutral state ("Nothing heard") plus the reason code when relevant.
5. **Fallback is reported with data:** `fallback: true`, `requested_model`, `used_model`, `reason`, and the UI notice stays. Count fallbacks by reason on the Overview.

### Fix C: delete that works for every engine (S4)

1. **Route by engine.** Delete uses the registry's `engine` field:
   - LocalAI-managed models: the existing gallery delete, with the safe-delete rules from the earlier plan
   - streaming models: a streaming model manager function
2. **Streaming model manager delete:**
   - refuse with 409 `MODEL_IN_USE` if an agent uses it, listing the agents
   - unload the model from the running service if it is loaded (or stop that service process if it is the only way, with a confirmation and never while a request is in flight)
   - delete files **only inside the streaming models folder**; validate the path, and do not delete a file that another model's manifest also references
   - remove the manifest, then trigger a registry rescan
   - return a job id or a final result
3. **UI:** show progress and the final result (success or the exact error), refetch the registry afterward, remove the card from Installed, and keep it visible with an error if deletion failed.
4. **Idempotent and logged:** deleting something already gone is a success; every delete is written to the admin audit log.

---

## 3. Tests (written first)

- **S1/S2:** with the service stopped, `stream.start` returns `STREAMING_STT_UNAVAILABLE` immediately. With a model not loaded, it returns the not-loaded error. With each installed streaming model selected, the reported loaded model matches the request.
- **S1/S2:** a model with no adapter is marked unavailable and cannot be selected.
- **S3:** force the streaming service to fail mid-utterance with a recorded speech clip; the batch fallback transcribes the **full** clip, and the result matches the known text.
- **S3:** a silent clip produces "Nothing heard"; a corrupted buffer produces an explicit audio error, never "no speech".
- **S3:** an empty buffer produces `AUDIO_EMPTY`.
- **S4:** deleting a streaming model removes its files and manifest, the registry no longer lists it, and the UI updates without a reload.
- **S4:** deleting a model used by an agent returns `MODEL_IN_USE` with the agent names; nothing is deleted.
- **S4:** a path outside the models folder is rejected; a shared file is not deleted.
- Fallback counters by reason increment as expected.
- Regression: batch STT, the voice assistant with batch mode, and LocalAI model delete still work.

---

## 4. Tasks

| Task | Owner | Depends on |
|---|---|---|
| Diagnose S1 to S4 and write `docs/fix-diagnosis.md` | DIAG | none |
| Failing tests for each symptom | QA | diagnosis |
| Fix A: preflight, resolution, error codes, self-test | BE | diagnosis |
| Fix B: turn audio buffer, correct fallback, honest silence, UI wording | BE + FE | diagnosis |
| Fix C: streaming model manager delete, UI refresh and errors | BE + FE | diagnosis |
| Regression run | QA | all fixes |
| Review of every branch | REV | each branch |

---

## 5. Acceptance Checklist

- [ ] Selecting each installed streaming model starts the right engine, and the session reports which model is loaded.
- [ ] If the streaming service is down or the model is missing, the user sees a specific message before speaking, not a 502 after.
- [ ] If a mid-turn fallback occurs, batch Whisper transcribes the **whole** utterance and the UI says a fallback happened.
- [ ] "(no speech detected)" appears only for real silence; failures show an explicit audio or engine error.
- [ ] Removing a streaming model deletes its files, updates the registry and UI, and shows the result.
- [ ] Removing a model that an agent uses is blocked with the agent names listed.
- [ ] No generic 502 is returned for "not running" or "model not found".
- [ ] Fallbacks are counted by reason on the Overview.
- [ ] All new tests and the regression run pass.

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| Buffering turn audio uses memory | Bounded buffer, per-session cap, release at turn end |
| Unloading or stopping a service during a request | Never while a request is in flight; per-model lock |
| Deleting shared files | Check other manifests; path validation; delete inside the models folder only |
| Not all engines support loading a model on demand | Fallback to explicit "not loaded" error with instructions, and a guarded restart only with confirmation |
| Changing error codes breaks the UI | Update the UI mapping in the same change and test it |

---

## 7. Starting Prompts

**Diagnostician (Phase 1):**
> Read `streaming-stt-fix.md`. Do Phase 1 only: read-only, test clips only, no changes to live model folders. Reproduce S1 to S4, collect the evidence listed, and write `docs/fix-diagnosis.md` with a verdict for each. Mark anything you cannot prove as UNKNOWN. Stop and wait for approval.

**Builder (Phase 2):**
> Read `streaming-stt-fix.md` and `docs/fix-diagnosis.md`. For each fix, write the failing test first, then the code. Never hardcode model names. Never log transcripts, audio or keys. Paste test output into `docs/handoff/<id>.md`.

**Reviewer (fresh context, diff only):**
> Try to break the fixes: stop the service mid-turn, select a model that is installed but not loaded, select a model with no adapter, send a silent clip, send a corrupted buffer, delete a model an agent uses, delete a path outside the models folder, delete a model twice.

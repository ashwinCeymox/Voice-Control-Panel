# STT_FIX_PLAN.md: `STT_MODEL_UNAVAILABLE` for `parakeet-cpp-realtime-scene`

For the investigating agent. Error seen in the panel:
`[STT_MODEL_UNAVAILABLE] Requested streaming STT model 'parakeet-cpp-realtime-scene' is unavailable`

Rules: confirm each claim with a command before acting on it. Do not mark anything fixed until the final verification passes. Paste real output into `docs/STT_INVESTIGATION.md`.

---

## 1. What the logs already show (from `localai.log` and `backend.log`)

Timeline, all 2026-11-17:

| Time | Source | Event |
|---|---|---|
| 13:06:48 | LocalAI | Starts on 127.0.0.1:8080. `parakeet-cpp-nemotron-3.5-asr-streaming-0.6b` is listed. |
| 13:08:50 to 13:09:00 | gateway | Models reclassified by hand between `kind=batch` and `kind=streaming`. |
| 13:10:26 to 13:11:03 | gateway and LocalAI | Both parakeet models deleted. LocalAI logs `Failed to unload model during deletion: model not found`, so they were never loaded. |
| 13:11:38 to 13:12:34 | LocalAI | Downloads three GGUF files: `realtime_eou_120m-v1-f16`, `nemotron-3-diarization-q8_0`, `ced-tiny-q8_0`. All verified. |
| **13:12:34** | **LocalAI** | **`ERROR Failed to download backend` for `quay.io/go-skynet/local-ai-backends:latest-metal-darwin-arm64-parakeet-cpp`. Error: `error getting credentials - err: exec: "docker-credential-desktop": executable file not found in $PATH`. Both fallback URIs fail the same way.** |
| 13:12:37 | gateway | Records `Recorded model installation 'parakeet-cpp-realtime-scene' in category 'llm'`, three seconds after the backend failed. |
| 13:13:07 and 13:13:12 | gateway | Model hand-reclassified to `stt/batch`, then `stt/streaming`. |
| 13:13:xx | gateway | `WebSocket /v1/audio/transcriptions/stream?api_key=...` accepted. |
| 13:17:56 | gateway | uvicorn reload after an edit to `app/services/model_registry.py` (the previous agent's attempt). |
| 13:18:02 | LocalAI | `POST /v1/audio/transcriptions` 200, so batch STT with `whisper-base` still works. |

## 2. Working diagnosis (confirm in Step A before fixing)

**Primary cause (high confidence):** the `metal-parakeet-cpp` backend was never installed. LocalAI pulls backends as OCI images, and the pull fails because Docker's credential helper `docker-credential-desktop` is missing from `PATH`. That is typical when `~/.docker/config.json` contains `"credsStore": "desktop"` but Docker Desktop is not installed or not on `PATH` for the LocalAI process. Without the backend, LocalAI has the model weights and a model config, but cannot run them.

**Secondary cause (the reason the UI shows "installed"):** the gateway marked the model as installed and available even though the backend install failed. The failure is on the LocalAI side and the gateway's install job did not surface it. The gateway also initially filed the model under category `llm`, so the manifest classification is unreliable for this family.

**Likely third issue (needs reading the code):** `parakeet-cpp-realtime-scene` looks like a bundle (realtime EOU model, diarization model and `ced-tiny` scene model). It may not be a drop-in streaming transcription model. Check whether LocalAI offers streaming transcription for it, and how the gateway's `/v1/audio/transcriptions/stream` route reaches it. The default streaming path in the docs goes to Sherpa on :8088, not to LocalAI.

The earlier edits to `model_registry.py` did not touch the real cause. Review that diff and revert it if it only papers over the symptom.

## 3. Step A: Confirm the root cause

Run on the Mac, in the same shell environment used to start LocalAI (`start.sh` or `scripts/start-native-engines.sh`):

```bash
cat ~/.docker/config.json 2>/dev/null          # look for "credsStore": "desktop"
which docker-credential-desktop || echo "missing"
ls -la local-voice-agent/backends/             # is metal-parakeet-cpp present? only a .install-tmp leftover?
curl -s -H "Authorization: Bearer $LOCALAI_API_KEY" http://127.0.0.1:8080/backends | head -c 2000
cat LocalAI/models/parakeet-cpp*.yaml 2>/dev/null   # which backend does the model config request?
find LocalAI/models -name '._*' | head              # AppleDouble files (see Step E)
```

Read these in the gateway code and note the file and line for each:
- where `STT_MODEL_UNAVAILABLE` is raised, and what condition decides "unavailable"
- how `available=true` is computed for `/v1/models?task=stt&available=true`
- what the install job treats as "success", and where `Recorded model installation` is called
- how the streaming route picks a provider (Sherpa vs LocalAI)

**Exit A:** state the actual root cause with evidence. If it differs from section 2, update this file before continuing.

## 4. Step B: Fix the backend install

Options, in order of preference:

1. **Bypass the missing credential helper.** The quay.io image is public, so an anonymous pull works. Start LocalAI with a clean Docker config:
   ```bash
   mkdir -p ~/.localai-docker && echo '{}' > ~/.localai-docker/config.json
   DOCKER_CONFIG=~/.localai-docker  <your usual LocalAI start command>
   ```
   Put the `DOCKER_CONFIG` export in the LocalAI start script, not in a shell profile.
2. **Remove the stale entry.** If Docker Desktop is not used, delete the `credsStore` line from `~/.docker/config.json` (back it up first).
3. **Install Docker Desktop** only if the owner wants it for other reasons.

Then retry the backend install through LocalAI (use the UI's backend install, `POST /backends/apply`, or the `local-ai backends install` command, whichever the installed version supports) and confirm:

```bash
curl -s -H "Authorization: Bearer $LOCALAI_API_KEY" http://127.0.0.1:8080/backends | grep -i parakeet
ls local-voice-agent/backends/ | grep parakeet     # directory present, no .install-tmp
```

**Exit B:** `metal-parakeet-cpp` is installed, and one direct request to LocalAI with the model name loads it without a backend error in `localai.log`.

## 5. Step C: Make the gateway tell the truth

1. The install job must fail visibly if LocalAI reports any backend or model error, and the panel must show that error text.
2. Record a model as installed only after both the files and the backend exist.
3. `available=true` must mean "backend installed and model config present", not "file exists".
4. Stop auto-classifying parakeet models as `llm`. Map the `parakeet-cpp-*` family to `task=stt`, and set `kind` from the model's real capability, not from a manual PATCH.
5. Make `STT_MODEL_UNAVAILABLE` carry a reason (`backend_missing`, `model_not_found`, `not_streaming_capable`) so the next failure is obvious.
6. Revert any change in `app/services/model_registry.py` that only hides the symptom.

## 6. Step D: Streaming path

1. Read how `WS /v1/audio/transcriptions/stream` chooses its backend. If streaming only works through Sherpa (:8088), a LocalAI parakeet model cannot stream, and the correct fix is to say so clearly in the model list: mark it `kind=batch`, and keep streaming on Sherpa.
2. If LocalAI does support streaming transcription for this backend, check the LocalAI docs for the version in use, add a provider for it, and test with real PCM16 16 kHz audio.
3. Whatever the result, the fallback to batch `whisper-base` must keep working (it does today).

## 7. Step E: Clean up noise that hides real errors

- `._gallery_*.yaml` read errors: macOS AppleDouble files in `LocalAI/models/`. Remove them: `find LocalAI/models -name '._*' -delete`, and stop zipping or copying the folder in a way that creates them (`COPYFILE_DISABLE=1`).
- `POST /backend/shutdown` returns 404: the gateway blocks dangerous LocalAI paths. Make the delete flow not depend on that call, or log it at debug level.
- Disk is flapping between WARNING and PRESSURE (about 10% free). The new models are several hundred MB each, so free space before installing more.

## 8. Side finding: API key in the WebSocket URL

The access log shows `WebSocket /v1/audio/transcriptions/stream?api_key=dev-voice-key-12345`. This contradicts the guide, which says WebSockets use single-use tickets and that keys are scrubbed from logs. Switch the panel to `POST /v1/auth/ticket` then `?ticket=...`, and scrub `api_key=` from uvicorn access logs. Do this after the STT fix, as its own change.

## 9. Final verification

1. `localai.log` shows no `Failed to download backend` for parakeet.
2. Reinstall `parakeet-cpp-realtime-scene` from the gallery UI. The job succeeds only if the backend is present.
3. A real audio clip transcribes through the model (batch), and through streaming if Step D found it supported.
4. With the backend deliberately missing, the panel shows `backend_missing` instead of listing the model as available.
5. `.venv/bin/pytest tests/ -q` passes, with new tests for Step C items 1 to 5.
6. Write the result, with command output, to `docs/STT_INVESTIGATION.md`.

## Rules

- Make one change at a time, and re-run the failing case after each.
- If the evidence contradicts section 2, stop and report instead of forcing the plan.
- Do not delete user models or edit `~/.docker/config.json` without a backup.

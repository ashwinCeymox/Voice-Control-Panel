# Streaming STT & Model Management Diagnosis Report (S1–S4)

**Document Version:** 1.0  
**Date:** 2026-11-16  
**Status:** Phase 1 Complete (Read-Only Diagnosis)  
**Deliverable per:** `mid-fix.md`

---

## 1. Executive Summary & Verdict Table

| Symptom | Description | Verdict | Evidence Reference |
|---|---|---|---|
| **S1** | `Streaming STT engine error: 502: Streaming STT service is unreachable or returned a gateway error. Falling back to batch Whisper.` | **CONFIRMED** | `app/providers/streaming_stt_client.py:126-128`, `app/core/errors.py:36-43`, `app/api/voice.py:591,605` |
| **S2** | Choosing an installed streaming model returns **model not found** | **CONFIRMED** | LocalAI 404 on `/v1/audio/transcriptions` for streaming IDs; `services/streaming_stt/server.py:245-253` rejection of unaliased IDs |
| **S3** | The voice page shows **"(no speech detected)"** under "You said" even though speech was given | **CONFIRMED** | `frontend/src/pages/Assistant.tsx:515-535`, `app/api/voice.py:608-624`, `app/api/voice.py:324-346` |
| **S4** | Removing a model does nothing; the action is not reflected | **CONFIRMED** | `app/api/gallery.py:977-1148`, folder name mismatch (`-2023-02-17`), running daemon on port 8088 never stopped/unloaded |

---

## 2. Plain-Language Root Cause Analysis

1. **Root Cause of S1 & S2 (502 & Model Not Found):**
   When a user selects a streaming STT model, two distinct failures cause "model not found" and 502:
   - **LocalAI Batch Path Clash:** When a streaming model ID (such as `sherpa-onnx-streaming-zipformer-en-20M` or `parakeet-cpp-...`) is passed to `/v1/audio/transcriptions` (e.g. from the STT Playground batch tab or when an agent's `stt.kind` was misconfigured), LocalAI receives the request on port 8080 and responds with `HTTP 404: model "..." not found`.
   - **Error Masking as Generic 502:** When an agent runs streaming STT, `services/streaming_stt/server.py` evaluates `req_model`. If the model is not supported (e.g. Parakeet models without a backend runtime, or an unaliased ID), the server sends an explicit JSON error `{"code": "STT_MODEL_UNAVAILABLE"}` and closes the WebSocket with `WS_1008_POLICY_VIOLATION`. In `StreamingSTTClient.stream()` (`app/providers/streaming_stt_client.py:126-128`), the client catches the resulting `ConnectionClosedError` and blankets it into `ProviderUnavailableError("Streaming STT")`, which defaults to HTTP 502. In `app/api/voice.py:591`, the exception is formatted as: `"Streaming STT engine error: 502: Streaming STT service is unreachable or returned a gateway error. Falling back to batch Whisper."` The true cause (`STT_MODEL_UNAVAILABLE`) is completely obscured.

2. **Root Cause of S3 (False "(no speech detected)"):**
   During streaming sessions in `frontend/src/pages/Assistant.tsx`, microphone audio is streamed as raw PCM chunks (`onAudioFrame: pcmChunk.buffer`).
   When streaming STT fails and falls back to batch Whisper:
   - The backend gateway collected these raw PCM frames in `audio_buffer`.
   - Because raw PCM contains neither a RIFF header (`RIFF...WAVE`) nor WebM magic bytes (`\x1a\x45\xdf\xa3`), container detection in `app/api/voice.py:608` defaults to `"webm"`.
   - The fallback invokes `orchestrator.stt.transcribe()` with `filename="utterance.webm"` and `content_type="audio/webm"`, transmitting headerless raw PCM to LocalAI Whisper.
   - LocalAI's FFmpeg subprocess fails to parse raw PCM as WebM and exits with status 183: `Error opening input: Invalid data found when processing input`.
   - In `app/api/voice.py:338-346`, the exception handler explicitly inspects `err_str` for `"invalid data found"`, swallows the error, and emits `TranscriptCompletedEvent(turn_id=turn_id, text="(no speech detected)")`.
   - The frontend receives `text="(no speech detected)"` and renders `You said: (no speech detected)` under the conversation box, despite the user speaking clearly. A control test passing the same speech directly as a valid WAV to LocalAI transcribed 100% cleanly.

3. **Root Cause of S4 (Delete Action Not Reflected):**
   When the user clicks the Remove/Trash button on an installed streaming STT model:
   - The UI sends `DELETE /v1/models/{modelId}` to `app/api/gallery.py:uninstall_model`.
   - `uninstall_model` calls `POST /models/delete/{modelId}` on LocalAI (port 8080), which returns 404 because LocalAI's gallery manager only manages LocalAI models, not streaming STT models.
   - `uninstall_model` attempts direct folder deletion by checking `base_dir / model_id`. However, the directory on disk is `models/streaming-stt/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17`, while the requested ID is `sherpa-onnx-streaming-zipformer-en-20M`. The directory match fails and nothing is deleted from disk.
   - The running streaming daemon (`services/streaming_stt/server.py` on port 8088) is neither notified nor stopped, leaving the model loaded in RAM.
   - `uninstall_model` does not check whether any active agent depends on the model (no 409 `MODEL_IN_USE` safeguard).
   - After receiving 200 OK, the UI immediately re-queries `GET /v1/models/installed`. `get_installed_models_data()` queries `http://127.0.0.1:8088/v1/models` and rescans `models/streaming-stt/`, finds the model still active and present, and re-adds it. To the user, clicking Remove does nothing.

---

## 3. Symptom-by-Symptom Evidence & Findings

### S1 & S2: Service Unreachable and Model Not Found

1. **Running Processes & Ports:**
   - **Streaming STT Server:** PID `52053`, command `.venv/bin/python services/streaming_stt/server.py --port 8088`, listening on `127.0.0.1:8088`.
   - **LocalAI Server:** PID `58710`, listening on `127.0.0.1:8080`.
   - **FastAPI Gateway:** Listening on `127.0.0.1:8000`.
   - **Health check on 8088 (`curl -i http://127.0.0.1:8088/health`):**
     `HTTP 200 OK`, `{"status":"ready","model":"sherpa-onnx-streaming-zipformer-en-20M","kind":"streaming","provider":"stream-stt",...}`

2. **Gateway Configuration Match:**
   In [`app/core/config.py`](../app/core/config.py#L35-L40):
   - `STREAMING_STT_URL = "http://127.0.0.1:8088"`
   - `STREAMING_STT_WS_URL = "ws://127.0.0.1:8088/ws"`
   Matches the running service.

3. **Exact Failure Reproduction & Exception Chain:**
   - Client sends `stream.start` with a non-whitelisted model (e.g. `parakeet-cpp-realtime_eou_120m-v1`).
   - Server in [`services/streaming_stt/server.py`](../services/streaming_stt/server.py#L245-L253) returns:
     ```json
     {"type": "error", "code": "STT_MODEL_UNAVAILABLE", "message": "Requested streaming STT model '...' is unavailable on this engine"}
     ```
     and terminates WebSocket with code `1008`.
   - Client provider in [`app/providers/streaming_stt_client.py`](../app/providers/streaming_stt_client.py#L126-L128):
     ```python
     except (websockets.exceptions.WebSocketException, OSError, ConnectionRefusedError) as e:
         raise ProviderUnavailableError("Streaming STT", details=str(e))
     ```
   - In [`app/core/errors.py`](../app/core/errors.py#L36-L43), `ProviderUnavailableError` sets `status_code=502`.
   - In [`app/api/voice.py`](../app/api/voice.py#L591), `stream_fallback_reason` formats `f"Streaming STT engine error: {streaming_error}. Falling back to batch Whisper."`, producing the exact S1 user error string.

4. **Model Resolution Table:**
   | Display Name / Dropdown | Registry / Manifest ID | Sent to Adapter | Server Expected / Supported | Result |
   |---|---|---|---|---|
   | Streaming Zipformer 20M | `sherpa-onnx-streaming-zipformer-en-20M` | `sherpa-onnx-streaming-zipformer-en-20M` | `sherpa-onnx-streaming-zipformer-en-20M` | MATCH (Runs) |
   | Sherpa ONNX Zipformer 20M | `sherpa-onnx-streaming-zipformer-en-20M-2023-02-17` | `sherpa-onnx-streaming-zipformer-en-20M-2023-02-17` | In `valid_aliases` | MATCH (Runs) |
   | Parakeet Realtime EOU 120M | `parakeet-cpp-realtime_eou_120m-v1` | `parakeet-cpp-realtime_eou_120m-v1` | Not in `valid_aliases` | Rejection -> 502 Fallback |
   | Parakeet Nemotron 0.6B | `parakeet-cpp-nemotron-3.5-asr-streaming-0.6b` | `parakeet-cpp-nemotron-3.5-asr-streaming-0.6b` | Not in `valid_aliases` | Rejection -> 502 Fallback |

5. **Direct LocalAI Batch 404 ("model not found"):**
   Executing `POST http://127.0.0.1:8080/v1/audio/transcriptions` with `model=sherpa-onnx-streaming-zipformer-en-20M` reproduces S2 directly:
   ```json
   {"error":{"code":404,"message":"model \"sherpa-onnx-streaming-zipformer-en-20M\" not found. To see available models, call GET /v1/models","type":"invalid_request_error"}}
   ```

---

### S3: False "(no speech detected)"

1. **Audio Flow During Streaming Session:**
   - In [`frontend/src/pages/Assistant.tsx`](../frontend/src/pages/Assistant.tsx#L515-L535):
     `onAudioFrame` transmits raw PCM chunks (`pcmChunk.buffer`) via WebSocket.
     When `isStreaming` is true, `onSpeechEnd` does not transmit a packaged WAV; it emits `{"type": "audio.commit"}`.
   - In [`app/api/voice.py`](../app/api/voice.py#L724-L732):
     `audio_buffer` accumulates raw PCM frames.
   - In [`app/api/voice.py`](../app/api/voice.py#L608-L624):
     `detected_container` checks for `RIFF` and WebM EBML headers. Because raw PCM has neither, `detected_container` remains `"webm"`.
   - In [`app/api/voice.py`](../app/api/voice.py#L324-L332):
     `orchestrator.stt.transcribe(audio_bytes=raw_pcm, filename="utterance.webm", content_type="audio/webm", ...)` forwards raw PCM disguised as WebM to LocalAI.

2. **FFmpeg Decode Failure on Disguised Raw PCM:**
   LocalAI runs FFmpeg on the received file. FFmpeg logs:
   ```
   [in#0 @ 0xa54c10000] Error opening input: Invalid data found when processing input
   Error opening input file ...: Invalid data found when processing input
   exit status 183
   ```

3. **Swallow Rule in `voice.py`:**
   In [`app/api/voice.py`](../app/api/voice.py#L338-L346):
   ```python
   except Exception as stt_exc:
       err_str = str(stt_exc).lower()
       if any(k in err_str for k in ("end of file", "invalid data found", "could not find codec", "truncating packet", "silence")):
           logger.info("STT encountered unparseable/silent audio frame for turn %s, handling gracefully", turn_id)
           await websocket.send_text(
               TranscriptCompletedEvent(turn_id=turn_id, text="(no speech detected)").model_dump_json()
           )
           await websocket.send_text(
               TurnCompletedEvent(turn_id=turn_id).model_dump_json()
           )
           return
   ```
   The FFmpeg decode failure from the corrupt payload is silently mapped to `text="(no speech detected)"`.

4. **Control Test (Batch Whisper Direct):**
   Sending a clean WAV file ([`tests/fixtures/audio/quiet_normal_speech.wav`](../tests/fixtures/audio/quiet_normal_speech.wav)) directly to LocalAI Whisper on `127.0.0.1:8080/v1/audio/transcriptions`:
   ```json
   {"text": "The weather today in New York is partly cloudy with a chance of afternoon rain.", "duration": 5.199999809265137}
   ```
   Transcription succeeded with 100% accuracy. The audio capture and Whisper engine are fully functional; the failure is entirely in the fallback packaging and swallow rule.

---

### S4: Delete Action Not Reflected

1. **UI Route & Backend Handler:**
   - In [`frontend/src/pages/ModelsRegistry.tsx`](../frontend/src/pages/ModelsRegistry.tsx#L185), clicking Remove invokes `DELETE /v1/models/{modelId}`.
   - Handled by [`app/api/gallery.py:uninstall_model`](../app/api/gallery.py#L981-L1148).

2. **Filesystem Path Discrepancy:**
   - Disk path: `models/streaming-stt/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17`
   - Requested ID: `sherpa-onnx-streaming-zipformer-en-20M`
   - `uninstall_model` checks `base_dir / model_id`, which fails to match the folder name with the date suffix. Zero files are unlinked or deleted from disk.

3. **Running Service Unload Deficit:**
   - The streaming STT server (`services/streaming_stt/server.py`) has no unload API and is not terminated.
   - It continues serving `GET /v1/models` on port 8088.

4. **Agent Usage & Protection Checks:**
   - Neither `uninstall_model` nor `model_manifest` checks whether any agent is currently using the model. Deletion of active models proceeds without a `MODEL_IN_USE` warning.

5. **Re-Detection Loop:**
   - On completion of `DELETE`, the UI immediately calls `GET /v1/models/installed`.
   - In [`app/api/gallery.py:get_installed_models_data`](../app/api/gallery.py#L440-L485):
     The handler calls `http://127.0.0.1:8088/v1/models` (which returns the still-running model) and scans `models/streaming-stt/` (where the files still exist).
   - The model is re-added to the installed models response, restoring the card in the UI as installed.

---

## 4. Phase 2 Implementation Recommendations

Based on these confirmed findings, the planned fixes in Phase 2 should address:

1. **Preflight & Honest Errors (Fix A):**
   - Perform model readiness validation on session initialization before audio is streamed.
   - Return precise HTTP / WebSocket error codes: 503 `STREAMING_STT_UNAVAILABLE`, 404/409 `STT_MODEL_NOT_LOADED`, 400 `STT_BAD_AUDIO` rather than generic 502s.
   - In `StreamingSTTClient`, preserve upstream error events and codes rather than masking all errors behind `ProviderUnavailableError`.

2. **Utterance Audio Buffering & Honest Silence (Fix B):**
   - When streaming raw PCM from the client, maintain a bounded PCM buffer on the gateway.
   - On fallback to batch Whisper, construct a valid 16kHz mono 16-bit WAV with proper 44-byte RIFF header before transmitting to LocalAI.
   - Remove the swallow rule in `voice.py:338-346`. Decode errors and audio faults must return explicit `STT_DECODE_ERROR` or `AUDIO_EMPTY`.
   - Distinguish true silence (RMS / VAD below threshold) from decoding failures in UI captions.

3. **Multi-Engine Model Deletion (Fix C):**
   - Route model deletion by provider/engine: LocalAI models via LocalAI gallery, streaming STT models via a dedicated streaming model manager.
   - Enforce agent usage check: reject deletion with 409 `MODEL_IN_USE` if any agent uses the model, listing the agent names.
   - Resolve exact disk folders (including date-tagged directories like `sherpa-onnx-streaming-zipformer-en-20M-2023-02-17`).
   - Signal or stop the streaming daemon when its sole model is uninstalled.
   - Purge manifest and force cache invalidation so re-fetch accurately reflects uninstallation.

---
**Diagnosis Complete. Awaiting approval to proceed to Phase 2 (Fixes).**

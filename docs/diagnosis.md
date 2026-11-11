# Phase 0: Diagnosis & Hardcoded Inventory

**Date:** 2026-11-11  
**Target:** Local Voice Agent on macOS Apple Silicon M2  

---

## 1. Problem 1 Diagnosis: Install Shows "Installing" but Model Never Appears in Installed

### Reproduction & Trace
1. **Frontend Trigger:** User clicks "Install Model" in `ModelsRegistry.tsx`.
2. **Client Call:** `POST /v1/models/install` with payload `{"model_id": "<model-id>"}`.
3. **Backend Execution:** `app/api/gallery.py:start_install_job` initiates `run_install_sim(jid, mid)`.
   - The job creates an entry in an in-memory dictionary `_INSTALL_JOBS`.
   - `run_install_sim` sleeps for 4 seconds, ticking `progress_percent` from 10 to 100%, and sets `job["status"] = "completed"`.
   - **Root Cause 1:** The install job is a pure simulation. It **never writes files**, **never calls LocalAI's `/models/apply` API**, and **never persists the installed model to database or registry**.
   - **Root Cause 2:** When the job reaches `completed`, the frontend calls `fetchData()`, which calls `GET /v1/gallery/models`. But `GALLERY_CATALOG` items have `installed: undefined/false`. None of the catalog items have their `installed` property updated, so the "Installed Models" tab (`models.filter(m => m.installed)`) stays at 0 items.
   - **Root Cause 3:** `GET /v1/models/installed` returns a **static, hardcoded JSON dictionary** that is completely detached from the install jobs:
     ```python
     {
       "stt": [{"id": "whisper-base", ...}, {"id": "whisper-tiny", ...}],
       "llm": [{"id": "phi-2-chat", ...}, {"id": "qwen2.5-1.5b-instruct", ...}],
       "tts": [{"id": "kokoro", ...}, {"id": "pocket", ...}],
     }
     ```

### Provider Ground Truth
- **LocalAI (`http://127.0.0.1:8080/v1/models`):**
  - Currently reports: `[{"id": "phi-2-chat", "object": "model"}]`.
  - Available gallery (`/models/available`): contains `whisper-base`, `whisper-tiny`, `qwen2.5-1.5b-instruct`, etc.
  - Install API: `POST /models/apply` with `{"name": "<model_name>"}` downloads and registers models into LocalAI.
- **local-tts (`http://127.0.0.1:8880/v1/models`):**
  - Currently reports: `[{"model_id": "kokoro", "name": "Kokoro"}, {"model_id": "pocket", ...}, {"model_id": "kitten", ...}, {"model_id": "qwen", ...}]`.
  - Voices (`/v1/voices`): Returns a JSON list `[{"voice_id": "af_heart", "name": "Heart", "category": "female", "language": "en-us"}, ...]`.
  - In `app/providers/localtts_client.py`: `list_voices` called `resp.json().get("voices")` on a `list` object, throwing an unhandled `AttributeError` and falling back to a dummy single voice.

---

## 2. Problem 2: Hardcoded Junk Models Inventory

The codebase contains hardcoded model strings and fallback lists across backend and frontend files:

| File | Line | Hardcoded Entity | Problem |
|---|---|---|---|
| `app/api/gallery.py` | 221–231 | `whisper-base`, `whisper-tiny`, `phi-2-chat`, `qwen2.5-1.5b-instruct`, `kokoro`, `pocket` | Static mock return in `/v1/models/installed` |
| `app/api/models.py` | 33–36 | `settings.LOCALAI_LLM_MODEL`, `settings.LOCALAI_STT_MODEL` | Hardcoded fallback when provider list is empty |
| `app/providers/localtts_client.py` | 153–156 | `kokoro`, `pocket`, `kitten`, `qwen` | Static fallback in `list_models` |
| `app/providers/localtts_client.py` | 134–141 | `settings.LOCAL_TTS_VOICE_ID` ("Default Voice") | Static fallback in `list_voices` on parse error |
| `app/services/agent_manager.py` | 16–18 | `phi-2-chat`, `whisper-base`, `kokoro`, `af_heart` | Static defaults injected into default agent |
| `frontend/src/pages/Agents.tsx` | 39–43 | `['phi-2-chat', 'qwen2.5-1.5b-instruct']`, `['whisper-base', 'whisper-small']`, `['kokoro']` | Hardcoded initial state for `installedModels` |
| `frontend/src/pages/Agents.tsx` | 232–234 | `phi-2-chat`, `whisper-base`, `kokoro`, `af_heart` | Hardcoded fallbacks in `handleCreateNew` |
| `frontend/src/pages/Agents.tsx` | 603 | `af_heart` | Hardcoded `<option>` fallback in JSX |
| `frontend/src/pages/STTPlayground.tsx` | 23–24 | `['whisper-base']` | Hardcoded initial state |
| `frontend/src/pages/TTSPlayground.tsx` | 15–19 | `[{ id: 'kokoro', ... }]`, `kokoro`, `af_heart` | Hardcoded initial state |

---

## 3. Problem 4: "Stop Speaking" Button vs. VAD Turn-Taking

### Cause
In `Assistant.tsx`, `stopListening()` is invoked both when the user clicks the "Done Speaking" button AND when VAD fires `onSpeechEnd`.
However, `stopListening()` forcibly terminates the microphone:
- Calls `audioStreamRef.current.getTracks().forEach(t => t.stop())`
- Closes `audioCtxRef.current.close()`
- Nullifies `vadRef.current`

This killed the audio pipeline after each utterance, preventing hands-free continuous dialogue and forcing the UI to display turn-taking buttons ("Start Speaking", "Done Speaking", "Interrupt Assistant").

### Correct Architecture
1. **Session Lifecycle**: A single "Start Session" / "End Session" control (plus Mute toggle).
2. **Continuous Audio Stream**: The microphone and Web Audio context remain active throughout the session.
3. **Turn-Taking by VAD**:
   - User speaks → VAD triggers `onSpeechStart` (state: `listening`).
   - User pauses (silence threshold reached, e.g. 650ms) → VAD triggers `onSpeechEnd`, automatically dispatching `audio.commit` over WebSocket (state: `thinking`).
   - Server streams LLM tokens and TTS audio → client plays PCM (state: `speaking`, VAD switched to barge-in mode).
   - Audio finishes playing → VAD switches back to speech mode (state: `listening`). Microphone is already live and listening.
   - User speaks during agent playback → VAD triggers `onBargeIn`, immediately halting playback and sending `turn.cancel`.
4. **No Turn-Taking Buttons**: Zero "Stop speaking" or "Done speaking" buttons.

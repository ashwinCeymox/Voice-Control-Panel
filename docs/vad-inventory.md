# Silero VAD Integration: Phase 0 Diagnostic Inventory (P0)

This document answers the seven Phase 0 inventory questions defined in `vad-plan.md` with concrete evidence from the codebase, live responses, and configuration schemas.

---

## 1. Speech Start and End Detection Today

**Location:** Client-side in [`frontend/src/audio/vad.ts`](../frontend/src/audio/vad.ts) via Web Audio API.

- **Mechanism:**
  - An `AnalyserNode` (`fftSize = 512`, `smoothingTimeConstant = 0`) is attached to `audioContext.createMediaStreamSource(stream)`.
  - A 20ms polling interval (`_tick()`) computes time-domain Root Mean Square (RMS) energy:
    $$\text{RMS} = \sqrt{\frac{1}{N} \sum_{i=0}^{N-1} \left(\frac{x_i - 128}{128}\right)^2}$$
- **Speech Start:**
  - Evaluated against `idleThreshold` (default `0.045`).
  - When $\text{RMS} > \text{idleThreshold}$, transitions state from `'idle'` to `'speaking'` and triggers `onSpeechStart()`.
- **Speech End:**
  - When $\text{RMS} \le \text{idleThreshold}$, transitions to `'trailing_silence'` and starts a timer.
  - If silence persists continuously for $\ge \text{silenceMs}$ (default `650ms` from agent config), triggers `onSpeechEnd()`.
  - In [`frontend/src/pages/Assistant.tsx`](../frontend/src/pages/Assistant.tsx#L377-L400), `onSpeechEnd` executes `commitTurn()`, which stops the active `MediaRecorder` and dispatches `{"type": "audio.commit"}` over WebSocket.
- **Barge-In (Assistant Speaking):**
  - While assistant plays audio (`isPlayingBack === true`), an elevated `bargeInThreshold` (`0.065` for speakers, `0.045` for headphones) with a 100ms hold timer (`bargeInHoldMs`) checks for user speech over playback. On confirmation, it triggers `onBargeIn()`, stopping playback and dispatching `{"type": "turn.cancel"}`.

---

## 2. WebSocket Stream Format and audio.commit

**Endpoint:** `WS /v1/voice/stream?ticket=<auth_ticket>` handled by [`app/api/voice.py`](../app/api/voice.py#L83-L370).

- **Session Initialization:**
  Client sends a JSON text frame:
  ```json
  {
    "type": "session.start",
    "agent_id": "<uuid>",
    "input_audio": { "container": "webm", "codec": "opus" },
    "config": { "echo_mode": "laptop_speakers", "barge_in": true }
  }
  ```
- **Audio Streaming During Speech:**
  - `MediaRecorder` runs with 100ms time-slices (`mediaRecorder.start(100)`).
  - Emits WebM Opus chunks (44.1 kHz / 48 kHz).
  - Sent as binary `ArrayBuffer` frames every 100ms via `ws.send(buf)`.
- **Backend Buffering:**
  - Backend accumulates chunks into an in-memory `audio_buffer = bytearray()`.
  - If incoming continuous chunks lack the EBML container header, the backend prepends a cached EBML header (`\x1a\x45\xdf\xa3`).
- **Commit Trigger:**
  - Client dispatches `{"type": "audio.commit"}` on VAD `onSpeechEnd`.
  - Backend verifies byte count $\ge 1200$ bytes; if valid, launches `orchestrator.stt.transcribe(...)`.

---

## 3. Whisper Options in the LocalAI Call

**Client:** [`app/providers/localai_stt.py`](../app/providers/localai_stt.py#L90-L100).

- **Current Parameters Sent:**
  - `POST /v1/audio/transcriptions` (multipart form-data):
    - `file`: `(filename, audio_bytes, content_type)`
    - `model`: target model (e.g. `whisper-base`, `whisper-small`)
    - `response_format`: `"json"`
    - `language`: optional ISO language code (omitted when `"auto"`)
- **Parameters NOT currently set:**
  - `temperature`: not explicitly configured (defaults to Whisper backend default 0.0).
  - `condition_on_previous_text`: not explicitly passed (Whisper default is true, causing repetitive hallucination risks).
  - VAD options: No internal Whisper VAD parameters (`vad_filter`, `threshold`) are configured in LocalAI request.
- **LocalAI Model Specification:**
  Defined in `/home/voiceagent/Downloads/test-pro/arch/LocalAI/models/whisper-base.yaml`:
  ```yaml
  backend: whisper
  name: whisper-base
  parameters:
      model: ggml-base.bin
  ```

---

## 4. Transcription Response Fields (Evidence Log)

**Verification Test:** Real probe against LocalAI `whisper-base` with `response_format=verbose_json`:
```bash
curl -s -X POST http://127.0.0.1:8080/v1/audio/transcriptions \
  -F "file=@tests/fixtures/sample_utterance.wav" \
  -F "model=whisper-base" \
  -F "response_format=verbose_json"
```

**Real Raw Response Payload:**
```json
{
  "segments": [
    {
      "id": 0,
      "start": 0,
      "end": 2.92,
      "text": " (crickets chirping)",
      "tokens": [50364, 522, 66, 9323, 1385, 36682, 8, 50510]
    }
  ],
  "text": "(crickets chirping)",
  "duration": 1
}
```

**Finding:**
LocalAI's `whisper.cpp` Metal backend **does NOT emit** `no_speech_prob`, `avg_logprob`, or `compression_ratio`. It emits only `id`, `start`, `end`, `text`, `tokens`, `duration`.
**Architectural Decision per P4 Rule 4:**
Because LocalAI does not expose `no_speech_prob` or `avg_logprob`, the confidence gate must rely on **P4 Rule 3 (Transcript Sanity Gate)** (checking for empty, punctuation-only, hallucinated repetitive text, or known noise tokens like `[crickets chirping]`, `[blank_audio]`), alongside the client-side RMS volume gate.

---

## 5. Frontend Build Tool & Static Asset Serving

- **Build Tool:** Vite 5 (`vite: "^5.4.11"`), React 18, TypeScript 5.6.
- **Config:** [`frontend/vite.config.ts`](../frontend/vite.config.ts) runs on port 5173.
- **Static Assets:**
  - Standard Vite root for public assets is `frontend/public/`.
  - Any files placed in `frontend/public/` are served at root (`/`) during `vite dev` and copied verbatim to `frontend/dist/` on `vite build`.
  - Self-hosted Silero VAD assets (`silero_vad.onnx`, `vad.worklet.bundle.min.js`, `ort-wasm-simd-threaded.wasm`, etc.) must be placed in `frontend/public/vad/` and configured via `baseAssetPath: "/vad/"` and `onnxWASMBasePath: "/vad/"` so they load 100% offline without CDN dependencies.

---

## 6. Existing Agent VAD Settings & Consumers

- **Database:**
  - Stored in SQLite `voice_platform.db` table `agents` in column `vad_json TEXT NOT NULL`.
- **Python Schema:**
  - Defined in [`app/schemas/agent.py`](../app/schemas/agent.py#L26-L32):
    ```python
    class VADConfig(BaseModel):
        mode: str = "energy"
        threshold: float = 0.045
        min_silence_ms: int = 650
    ```
- **Consumers:**
  - [`app/services/agent_manager.py`](../app/services/agent_manager.py#L19): Default agent configuration.
  - [`app/api/agents.py`](../app/api/agents.py): Agent CRUD endpoints.
  - [`frontend/src/pages/Assistant.tsx`](../frontend/src/pages/Assistant.tsx#L480): Instantiates `BrowserVAD(..., { silenceMs: agent.vad.min_silence_ms, idleThreshold: agent.vad.threshold })`.
  - [`frontend/src/pages/Agents.tsx`](../frontend/src/pages/Agents.tsx#L764): UI slider for `min_silence_ms`.

---

## 7. Turn-Taking Code Slated for Deprecation (Dead Code)

When Silero VAD is wired in:
1. **Analysernode Energy Gate:** The custom polling loop and manual RMS calculation in `frontend/src/audio/vad.ts` (`_tick`, `_getRMS`, `trailing_silence` timer) will be bypassed by Silero VAD's neural speech probability model.
2. **Continuous 100ms Chunk Streaming:** Sending continuous Opus chunks every 100ms will be replaced by sending speech segments upon `onSpeechEnd` (Float32Array converted to 16kHz 16-bit PCM WAV).
3. **EBML Header Stitching:** The WebM EBML header caching & stitching logic in `app/api/voice.py` (`webm_header`, `b"\x1a\x45\xdf\xa3"`) becomes obsolete for clean standalone PCM/WAV speech segment commits.
4. **Manual stop-recording delay:** `mediaRecorder.stop()` onstop callback delay logic in `Assistant.tsx:commitTurn`.

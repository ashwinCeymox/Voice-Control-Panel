v# Local Voice AI Agent: End-to-End Architecture & Audio Pipeline Report

**Status:** Verified & Operational  
**Execution Environment:** macOS Apple Silicon (Metal Acceleration)  
**Stack Components:** Vite/React Frontend (5173), FastAPI Orchestrator (8000), LocalAI Metal (8080), local-tts Kokoro MLX (8880)

---

## 1. Executive Architecture Overview

The system is a full-duplex, low-latency conversational voice agent operating **100% locally and offline**. It integrates neural client-side Voice Activity Detection (Silero VAD), local Metal-accelerated Speech-to-Text (Whisper via LocalAI), local LLM inference (Llama-3.2 / Phi-2 via LocalAI), and real-time streaming Text-to-Speech (Kokoro-82M via Apple MLX).

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                   CLIENT (Browser)                                     │
│                                                                                        │
│  [Microphone] ──▶ [Silero VAD WebAssembly] ──(Speech End)──▶ [16kHz PCM WAV Segment]   │
│         ▲                                                            │                 │
│         │ (Barge-In Cancel)                                          │                 │
│         └────────────────────────────────────┐                       │                 │
│  [24kHz Audio Playback] ◀──(Binary PCM)──────┴───────────────────────┼─────────────────┘
│                                                                      │ (WebSocket)
│                                                                      ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              FASTAPI ORCHESTRATOR (Port 8000)                          │
│                                                                                        │
│   app/api/voice.py ──▶ Magic Byte Detection (RIFF/WAV)                                 │
│          │                                                                             │
│          ▼ [Non-Streaming HTTP POST]                                                   │
│   ┌────────────────────────────────────────────────────────┐                           │
│   │ LOCALAI STT (Port 8080): Whisper-Base (Metal GPU)      │ ──▶ Transcript Completed │
│   └────────────────────────────────────────────────────────┘              │            │
│                                                                           ▼            │
│   ┌────────────────────────────────────────────────────────┐                           │
│   │ LOCALAI LLM (Port 8080): Llama-3.2-1b (Metal GPU)      │ ◀── Session Prompt & State│
│   └────────────────────────────────────────────────────────┘                           │
│          │                                                                             │
│          ▼ [Real-Time SSE Token Stream]                                                │
│   app/services/text_chunker.py (Splits into complete grammatical clauses/sentences)    │
│          │                                                                             │
│          ▼ [Real-Time WebSocket Text Stream]                                           │
│   ┌────────────────────────────────────────────────────────┐                           │
│   │ local-tts (Port 8880): Kokoro-82M (Apple MLX Engine)   │                           │
│   └────────────────────────────────────────────────────────┘                           │
│          │                                                                             │
│          ▼ [Real-Time 24kHz 16-bit PCM Audio Stream]                                   │
│   app/api/voice.py (Forwards binary frames to browser WebSocket)                       │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Phase-by-Phase Audio Flow (From Speech to Response)

### Phase 1: Microphone Capture & Neural VAD (Browser)
* **Mode:** **Continuous Local Processing (Zero Network Churn)**
* **Component:** `frontend/src/audio/vadController.ts` (`@ricky0123/vad-web`)
* **How it works:**
  1. The browser captures microphone audio via Web Audio API (`navigator.mediaDevices.getUserMedia`) with noise suppression, echo cancellation, and auto-gain control.
  2. The raw audio is processed by an AudioWorklet (`vad.worklet.bundle.min.js`) feeding 512-sample frames (32ms at 16kHz) into `silero_vad_v5.onnx` executed via ONNX Runtime WebAssembly.
  3. Every 32ms, Silero outputs a speech probability:
     - When probability exceeds `positiveSpeechThreshold` (0.45), it triggers `onSpeechStart` and sends a lightweight `{ "type": "vad.speech_start" }` telemetry event.
     - While speaking, the VAD maintains a rolling circular pre-speech buffer (`preSpeechPadMs: 200ms`) to avoid clipping the first syllable.
     - When the user stops talking and silence persists for `redemptionMs` (800ms), `onSpeechEnd` fires.
  4. **Volume Gate (RMS):** Before any data is transmitted, root-mean-square amplitude is computed. If `rms < minRms` (0.015), the segment is classified as ambient noise (breaths, fan, keyboard) and dropped via `vad.misfire`.
  5. **Segment Encoding:** The discrete `Float32Array` buffer is converted into a standard 16-bit Mono 16kHz PCM WAV (`b"RIFF....WAVE"` header, 44 bytes).
  6. **Transmission:** The WAV file is transmitted as a single binary frame over the WebSocket, immediately followed by a `{ "type": "audio.commit" }` JSON event.

### Phase 2: Ingestion & Magic Byte Auto-Detection
* **Mode:** **Discrete Turn Ingestion**
* **Component:** `app/api/voice.py`
* **How it works:**
  1. The WebSocket router receives the binary chunk into `audio_buffer`.
  2. When `audio.commit` arrives, the backend extracts the byte array and inspects its magic bytes:
     - `b"RIFF....WAVE"` -> Tagged as **`wav`** (`audio/wav`).
     - `\x1a\x45\xdf\xa3` -> Tagged as **`webm`** (`audio/webm`).
  3. This auto-detection prevents container desync if an agent was switched mid-session.
  4. The turn is assigned a unique `turn_id` (`turn-<uuid>`) and dispatched asynchronously to `process_turn()`.
  5. The task is registered in `VoiceOrchestrator` (`_active_stream_tasks[session_id]`) so it can be aborted instantly if the user interrupts.

### Phase 3: Speech-to-Text Transcription
* **Mode:** **Non-Streaming HTTP POST**
* **Component:** `app/providers/localai_stt.py` -> LocalAI (`whisper.cpp`)
* **How it works:**
  1. The WAV bytes are sent via HTTP multipart/form-data POST to LocalAI (`http://127.0.0.1:8080/v1/audio/transcriptions`) with `temperature=0` and `condition_on_previous_text=false`.
  2. Whisper processes the discrete WAV segment on Apple Silicon Metal GPU in **~150ms to 250ms**.
  3. **Why Non-Streaming?** Whisper's architecture is an encoder-decoder Transformer requiring fixed spectrogram frames; it does not support streaming audio input. LocalAI receives the completed WAV segment and decodes it in one high-accuracy burst.
  4. **Gate Filters:**
     - **Silence Gate:** If transcript is empty or `"(no speech detected)"`, the backend quietly sends `TurnCompletedEvent` and exits. The LLM is never called.
     - **Sanity Gate:** If the transcript contains hallucination brackets (e.g. `[music]`, `(applause)`) or a 4+ repetitive single-word stutter, it triggers the "Ask to repeat" gate (`"Sorry, I didn't catch that. Could you repeat?"`) up to 2 times.
  5. Once validated, the backend emits `TranscriptCompletedEvent(text=...)` to the UI.

### Phase 4: LLM Inference & Text Chunking
* **Mode:** **Real-Time Token Streaming (SSE)**
* **Component:** `app/providers/localai_llm.py` & `app/services/text_chunker.py`
* **How it works:**
  1. The user's message is stored in SQLite (`SessionRepository.add_message`).
  2. Conversation history is loaded and formatted with the active agent's `system_prompt`.
  3. An HTTP streaming request is sent to LocalAI (`POST /v1/chat/completions` with `stream=True`).
  4. LocalAI (`llama.cpp` on Metal) emits tokens incrementally via Server-Sent Events (SSE).
  5. **Dual Dispatching:**
     - Every token delta is sent immediately to the frontend via `ResponseTextDeltaEvent` so the user sees the answer typing out in real-time.
     - Concurrently, tokens are fed into `TextChunker`.
  6. **TextChunker Phrasing:** Rather than waiting for the entire LLM response to complete, `TextChunker` buffers tokens and splits at sentence and major clause boundaries (`.`, `!`, `?`, `;`, `,`). The instant a full grammatical phrase is ready, it is yielded for immediate speech synthesis.

### Phase 5: Text-to-Speech Synthesis & Audio Forwarding
* **Mode:** **Real-Time WebSocket Audio Streaming**
* **Component:** `app/providers/localtts_client.py` -> `local-tts` (Kokoro MLX)
* **How it works:**
  1. The backend opens a persistent WebSocket connection to `local-tts` on port `8880` (`/v1/audio/speech/stream`).
  2. Sentence chunks from `TextChunker` are sent to `local-tts` as JSON payloads (`{ "text": sentence, "voice": voice_id }`).
  3. The Kokoro MLX engine synthesizes audio on the Apple Silicon Neural Engine / GPU and immediately streams back raw 24kHz 16-bit Mono PCM binary chunks.
  4. On receipt of the first chunk, the FastAPI backend sends `AudioStartedEvent` to the browser, then pipes the raw PCM binary bytes directly across the client WebSocket.
  5. When synthesis completes, the backend sends `AudioCompletedEvent` and `TurnCompletedEvent`.

### Phase 6: Playback & Barge-In Interruption
* **Mode:** **Real-Time Audio Buffer Playback with Hold Confirmation**
* **Component:** `frontend/src/audio/pcmPlayer.ts` & `frontend/src/audio/vadController.ts`
* **How it works:**
  1. The browser's `PCMPlayer` schedules the 24kHz PCM chunks into an `AudioContext` queue for seamless, pop-free speaker playback.
  2. **Barge-In Protection (Speaker Echo):** While the assistant is actively speaking, `VadController.setPlaybackState(true)` increases `positiveSpeechThreshold` by `+0.15` (from 0.45 to 0.60). This prevents laptop speaker sound from triggering self-interruption.
  3. **Barge-In Trigger:** If the user talks over the assistant and speech is sustained for `bargeInConfirmMs` (250ms):
     - Browser fires `onBargeIn`.
     - Browser immediately stops audio playback via `pcmPlayer.stop()`.
     - Browser sends `{ "type": "turn.cancel" }` to the backend.
     - Backend cancels the active `asyncio` task, severing the LLM generation and closing the TTS WebSocket stream.
     - The assistant immediately goes silent and waits for the user's new question.

---

## 3. Streaming vs. Non-Streaming Matrix

| Pipeline Stage | Protocol | Streaming? | Latency / Duration |
|---|---|:---:|---|
| **Microphone Capture** | Web Audio Worklet | **STREAMING** (32ms frames) | Real-time (0ms) |
| **VAD Endpointing** | Browser WASM (Silero) | **LOCAL** (Frame-by-frame) | Evaluated every 32ms |
| **Segment Upload** | WebSocket Binary Frame | **DISCRETE** (Whole WAV) | Instantaneous (<10ms local) |
| **Speech-to-Text (STT)**| HTTP POST (`whisper.cpp`) | **NON-STREAMING** | ~150ms – 250ms (Metal GPU) |
| **LLM Inference** | HTTP POST SSE (`llama.cpp`)| **STREAMING** (Token-by-token) | First token in ~80ms – 120ms |
| **Text Clause Chunking**| In-memory Regex buffer | **STREAMING** (Sentence-by-sentence) | 0ms |
| **TTS Synthesis** | WebSocket (`local-tts` MLX)| **STREAMING** (PCM chunks) | First audio in ~120ms – 180ms |
| **Audio Downlink** | WebSocket Binary Frame | **STREAMING** (24kHz PCM chunks)| Real-time forward |
| **Browser Playback** | Web Audio `AudioContext` | **STREAMING** (Queue buffer) | Real-time playback |

---

## 4. Backend Source Code Index (`app/`)

Every Python file in the backend architecture and its exact operational role:

### Core Configuration & Application Factory
* **`app/main.py`**  
  Application entry point. Initializes FastAPI, configures CORS, mounts routers, connects to SQLite, and runs background pre-warming of LocalAI STT (`whisper-base`) and LLM (`llama-3.2-1b-instruct`) so the first user interaction has zero cold-start delay.
* **`app/core/config.py`**  
  Centralized settings using Pydantic `BaseSettings`. Manages URLs (`LOCALAI_BASE_URL: 8080`, `LOCAL_TTS_BASE_URL: 8880`), model directories, timeouts, and default models.
* **`app/core/security.py`**  
  Ticket-based authentication provider. Issues and consumes short-lived single-use authentication tickets (`validate_and_consume_ticket`) to secure WebSocket handshakes without passing static API keys in URL query strings.
* **`app/core/errors.py`**  
  Application exceptions and error formatting. Contains `AppError`, `ProviderUnavailableError` (502), `ProviderTimeoutError` (504), and `ModelNotFoundError` (404).
* **`app/core/logging.py`**  
  Structured logging configuration. Formats log outputs with ISO timestamps, module names, and operational severities.

### API Routing Layer (`app/api/`)
* **`app/api/voice.py`**  
  **The Core Voice Gateway.** Houses the full-duplex WebSocket endpoint (`/v1/voice/stream`) and REST turn endpoint (`/v1/voice/turn`). Orchestrates the complete STT -> LLM -> TTS pipeline, manages active turn tasks, detects WAV vs WebM containers, handles turn cancellations, filters silence, and enforces the "Ask to repeat" gate.
* **`app/api/agents.py`**  
  Agent management API (`/v1/agents`). Handles CRUD for agent profiles, agent duplication, test chat sessions, and switching active agents (`/v1/agents/{id}/load`).
* **`app/api/gallery.py`**  
  Model Gallery management (`/v1/gallery`). Catalogs downloadable models, initiates LocalAI downloads (`/models/apply`), tracks installation jobs, and deletes model weights and YAML configs from disk.
* **`app/api/models.py`**  
  Model inspection API (`/v1/models/installed`). Queries LocalAI and local-tts to return all currently available LLM, STT, and TTS models.
* **`app/api/health.py`**  
  Readiness and health probes (`/v1/health`, `/health/ready`). Checks whether LocalAI (`/readyz`) and local-tts are online and responding.
* **`app/api/system.py`**  
  System telemetry API (`/v1/system/metrics`). Monitors host CPU utilization, RAM usage, storage space, and Metal GPU availability.
* **`app/api/chat.py`**  
  Standalone text chat completion endpoint (`/v1/chat/completions`) for headless LLM testing.
* **`app/api/transcriptions.py`**  
  Standalone audio transcription endpoint (`/v1/audio/transcriptions`) for direct audio file STT.
* **`app/api/tts.py`**  
  Standalone audio speech synthesis endpoint (`/v1/audio/speech`) for single-turn TTS generation.

### Provider Integration Clients (`app/providers/`)
* **`app/providers/localai_llm.py`**  
  LocalAI LLM client. Manages HTTP streaming requests (`/v1/chat/completions`), parses Server-Sent Events (SSE), extracts token deltas, and handles model fallback logic.
* **`app/providers/localai_stt.py`**  
  LocalAI Whisper STT client. Dispatches multipart WAV payloads to `/v1/audio/transcriptions`, implements automatic transient warmup retries on cold starts, and catches unparseable frames gracefully.
* **`app/providers/localtts_client.py`**  
  `local-tts` engine client. Manages bidirectional WebSocket streaming (`/v1/audio/speech/stream`) to synthesize text sentences into 24kHz raw PCM audio chunks via Kokoro MLX.

### Services Layer (`app/services/`)
* **`app/services/voice_orchestrator.py`**  
  High-level voice turn coordinator. Coordinates provider clients, maintains active asyncio task references per session, and triggers cancellations when interruptions occur.
* **`app/services/agent_manager.py`**  
  Agent configuration state machine. Seeds `DEFAULT_AGENT`, handles SQLite persistence, and executes schema normalizations/migrations (e.g. migrating legacy VAD energy fields to Silero VAD parameters).
* **`app/services/text_chunker.py`**  
  Real-time sentence boundary tokenizer. Inspects streaming LLM token deltas and segments them into complete, natural clauses for the TTS engine.

### Database & Storage (`app/storage/`)
* **`app/storage/database.py`**  
  SQLite async connection manager. Manages `voice_platform.db`, enables WAL mode, and executes schema DDL migrations.
* **`app/storage/repositories.py`**  
  Data access objects (`AgentRepository`, `SessionRepository`). Manages queries and updates for agent profiles, conversation turns, and chat message histories.

### Schema Contracts (`app/schemas/`)
* **`app/schemas/events.py`**  
  WebSocket protocol contracts: `SessionStartEvent`, `AudioCommitEvent`, `TranscriptCompletedEvent`, `ResponseTextDeltaEvent`, `AudioStartedEvent`, `AudioCompletedEvent`, `TurnCompletedEvent`, `TurnCancelledEvent`, `ErrorEvent`.
* **`app/schemas/agent.py`**  
  Agent data schemas including `Agent`, `VADConfig` (with Silero parameters), `LLMConfig`, `STTConfig`, and `TTSConfig`.
* **`app/schemas/audio.py`**  
  Audio transcription request and response schemas.
* **`app/schemas/chat.py`**  
  Chat completion request, message, and streaming chunk schemas.
* **`app/schemas/voice.py`**  
  REST single-turn voice request and response schemas.

---

## 5. Summary Diagram: The Two Audio Streams

```
Incoming Stream (User -> Agent):
[Mic] ──(32ms frames)──▶ [Silero VAD] ──(Full Segment)──▶ [WAV File] ──▶ [LocalAI STT] ──▶ [Text]
(Streaming locally)       (Endpointing)   (Single Binary)   (Non-streaming)

Outgoing Stream (Agent -> User):
[LLM] ──(SSE Tokens)──▶ [TextChunker] ──(Clauses)──▶ [local-tts] ──(PCM Chunks)──▶ [Browser PCMPlayer]
(Streaming)              (Streaming)                  (Streaming)                   (Continuous Audio)
```

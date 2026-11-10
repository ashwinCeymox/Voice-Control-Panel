# System Architecture: Self-Hosted Voice AI Platform

## 1. Architectural Overview

The platform provides a low-latency, modular, self-hosted voice AI assistant with ElevenLabs-compatible TTS APIs and OpenAI-compatible STT/LLM endpoints. It operates with a strict separation of concerns across three services:

1. **FastAPI Gateway & Orchestration Core**:
   - Manages client HTTPS/WebSocket connections.
   - Enforces authentication (API keys and short-lived WebSocket connection tickets).
   - Maintains conversation sessions and bounded history in SQLite (`aiosqlite`).
   - Orchestrates multi-provider pipelines (STT $\to$ LLM $\to$ Sentence Chunker $\to$ TTS).
   - Coordinates client cancellation and backpressure.
   - **Does not execute model inference directly.**

2. **LocalAI Service (`mudler/LocalAI`)**:
   - Dedicated runtime for **Speech-to-Text (STT)** via `POST /v1/audio/transcriptions`.
   - Dedicated runtime for **Large Language Model (LLM)** inference via `POST /v1/chat/completions`.
   - Discovers installed models via `GET /v1/models`.

3. **local-tts Service (`saghul/local-tts`)**:
   - Dedicated runtime for **Text-to-Speech (TTS)** synthesis.
   - Supports engines: `kokoro`, `pocket`, `kitten`, `qwen`.
   - Discovers voices via `GET /v1/voices` and engines via `GET /v1/models`.
   - Exposes HTTP streaming (`POST /v1/text-to-speech/{voice_id}/stream`) and full-duplex WebSocket (`WS /v1/text-to-speech/{voice_id}/stream-input`).
   - Produces raw signed 16-bit, 24,000 Hz, mono PCM audio (`s16le`).

```
                              [ External Clients ]
                       Browser Web UI / API Consumers
                                      │
                         HTTPS / WSS (Port 8000)
                                      ▼
             ┌──────────────────────────────────────────────────┐
             │            FastAPI Public Gateway Core           │
             │  ┌────────────────────┐  ┌─────────────────────┐ │
             │  │ Auth & Ticket Svc  │  │  Session Repository │ │
             │  │ (HMAC-SHA256 Keys) │  │  (SQLite/aiosqlite) │ │
             │  └────────────────────┘  └─────────────────────┘ │
             │  ┌─────────────────────────────────────────────┐ │
             │  │              Voice Orchestrator             │ │
             │  │  - Single-turn STT/LLM/TTS Pipeline         │ │
             │  │  - Real-Time Streaming & Sentence Chunker   │ │
             │  │  - Cancellation & Task Lifecycle Manager    │ │
             │  └─────────────────────────────────────────────┘ │
             └───────────────────────┬──────────────────────────┘
                                     │ Private Loopback Network
                    ┌────────────────┴────────────────┐
                    ▼                                 ▼
      ┌───────────────────────────┐     ┌───────────────────────────┐
      │      LocalAI Service      │     │     local-tts Service     │
      │   (Default: Port 8080)    │     │   (Default: Port 8880)    │
      │                           │     │                           │
      │  • STT: Whisper / ASR     │     │  • TTS: Kokoro / Pocket   │
      │  • LLM: Chat Completions  │     │  • Raw PCM (24kHz, s16le) │
      └───────────────────────────┘     └───────────────────────────┘
```

---

## 2. Audio Pipeline & Data Formats

### 2.1 Inbound Audio (Client $\to$ Gateway $\to$ LocalAI)
- **Container / Codec**:
  - WebM (Opus) or WAV (PCM 16-bit).
  - The client must specify `container` and `codec` in the session initialization event.
- **Utterance Buffering**:
  - For real-time streaming, audio frames are buffered during the active speaking phase.
  - Upon `audio.commit`, the concatenated buffer is wrapped in a containerized file payload and passed to LocalAI `POST /v1/audio/transcriptions`.
  - True streaming ASR is an advanced capability; MVP guarantees atomic utterance transcription.

### 2.2 Outbound Audio (local-tts $\to$ Gateway $\to$ Client)
- **Format**: Raw PCM signed 16-bit little-endian (`s16le`), 24,000 Hz sample rate, single channel (mono).
- **Transport Modes**:
  1. *Synchronous turn (`POST /v1/voice/turn`)*: Base64-encoded PCM inside a JSON envelope with format metadata.
  2. *Streaming turn (`WS /v1/voice/stream`)*: Client receives JSON `audio.started` event declaring the audio spec, followed by raw binary PCM chunks over the WebSocket, concluding with an `audio.completed` JSON event.

---

## 3. Real-Time Streaming & Cancellation Lifecycle

```mermaid
sequenceDiagram
    autonumber
    actor Client
    participant GW as FastAPI Gateway
    participant STT as LocalAI (STT)
    participant LLM as LocalAI (LLM)
    participant TTS as local-tts (WS)

    Note over Client,GW: Step 1: Session & Utterance Capture
    Client->>GW: WS Connect (ticket)
    GW-->>Client: {"type": "session.created", "session_id": "..."}
    Client->>GW: {"type": "session.start", "input_audio": {"container": "webm"}}
    Client->>GW: [Binary Audio Chunks]
    Client->>GW: {"type": "audio.commit"}

    Note over GW,STT: Step 2: Speech-to-Text
    GW->>STT: POST /v1/audio/transcriptions (multipart)
    STT-->>GW: {"text": "What is the capital of France?"}
    GW-->>Client: {"type": "transcript.completed", "text": "..."}

    Note over GW,LLM: Step 3: LLM Generation & Sentence Chunking
    GW->>LLM: POST /v1/chat/completions (stream=True)
    loop SSE Stream
        LLM-->>GW: data: {"choices": [{"delta": {"content": "The"}}]}
        GW-->>Client: {"type": "response.text.delta", "delta": "The"}
        GW->>GW: Accumulate in Text Chunker
    end

    Note over GW,TTS: Step 4: Stream Synthesis on Phrase Boundaries
    GW->>TTS: WS /stream-input BOS
    GW->>TTS: WS phrase chunk: {"text": "The capital of France is Paris."}
    GW->>TTS: WS EOS
    GW-->>Client: {"type": "audio.started", "sample_rate": 24000}
    loop Audio Delivery
        TTS-->>GW: JSON with base64 PCM
        GW-->>Client: [Binary PCM Bytes]
    end
    GW-->>Client: {"type": "audio.completed"}
    GW-->>Client: {"type": "turn.completed"}

    opt User Interrupts (turn.cancel)
        Client->>GW: {"type": "turn.cancel", "turn_id": "..."}
        GW->>LLM: Abort upstream HTTP connection
        GW->>TTS: Close WebSocket connection
        GW-->>Client: {"type": "turn.cancelled"}
    end
```

---

## 4. Hardware Budget & Memory Constraints (8 GB Apple Silicon Target)

- Concurrent residency of a full-size local LLM (e.g. 7B+ parameters), Whisper model, and Kokoro TTS on an 8 GB unified memory Mac causes paging and OOM failures.
- **Required Operational Guidelines**:
  1. Default LLM configuration: quantized 4-bit small footprint models (e.g., Llama-3.2-1B/3B, Qwen2.5-1.5B/3B) or remote endpoint proxying through LocalAI.
  2. STT: Whisper `tiny` or `base` models.
  3. TTS: Kokoro or Pocket engine with fast single-thread synthesis.
  4. Max concurrent active voice turns: default capped to 1.

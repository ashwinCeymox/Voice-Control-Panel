# Plan: Self-Hosted Voice API (LocalAI + local-tts)

## 0. Mission

Build a self-hosted voice platform that exposes a developer-friendly, ElevenLabs-style API while keeping inference providers replaceable.

**Required runtime split**

- **LocalAI** (`https://github.com/mudler/LocalAI`) is the model runtime/provider for **LLM inference and speech-to-text (STT)**. The application must allow the operator to select installed LocalAI model IDs for each capability.
- **local-tts** (`https://github.com/saghul/local-tts`) is the dedicated **text-to-speech (TTS)** service. Use its documented ElevenLabs-compatible HTTP and WebSocket interfaces. Do not silently replace it with LocalAI's own TTS backend.
- **FastAPI** is our public API gateway, authentication boundary, session layer and orchestrator. It coordinates the providers; it is not itself an inference engine.

The first deliverable is a reliable **audio file -> LocalAI STT -> LocalAI LLM -> local-tts -> audio response** turn. Then add WebSocket conversation streaming and interruption handling.

## 1. Non-goals and architecture constraints

1. Do not implement speech models from scratch.
2. Do not route TTS through LocalAI. LocalAI is used for LLM + STT only; `saghul/local-tts` owns TTS.
3. Do not make the browser call LocalAI or local-tts directly. All public calls go through FastAPI.
4. Do not build a large microservice fleet. Start with a modular FastAPI application plus two independently running inference services.
5. Do not claim true live microphone transcription merely because a WebSocket accepts audio frames. The MVP receives a complete utterance/file, transcribes it through LocalAI, and then streams the assistant response. Incremental STT is a later capability that must be proven with the chosen LocalAI backend.
6. Do not assume full ElevenLabs API parity. Implement the exact routes and payloads supported by `saghul/local-tts`, and document unsupported ElevenLabs options/formats.
7. Do not add LiteLLM or OmniRoute to the initial path. The application should call LocalAI directly through its OpenAI-compatible HTTP API. Keep provider adapters so a gateway can be added later without changing public routes.
8. Preserve any existing project code. Inspect the repository before editing, identify the current app entry point and conventions, and extend rather than replacing working code.

## 2. Target architecture

```text
                         External clients
                 Web app / mobile / API consumers
                              |
                    HTTPS + WebSocket (WSS)
                              |
                    FastAPI Public Gateway
       +----------------------+----------------------+
       |                      |                      |
  Authentication         API routers           Session manager
  API-key hashes         STT / LLM / TTS        conversation state
  quotas / scopes         voice turns           cancellation state
       |                      |
       +------------- Voice Orchestrator ----------+
                              |
             +----------------+----------------+
             |                                 |
       LocalAI service                    local-tts service
       LLM + STT only                     TTS only
       |                                 |
       +-- POST /v1/audio/transcriptions  +-- POST /v1/text-to-speech/{voice_id}/stream
       +-- POST /v1/chat/completions      +-- WS /v1/text-to-speech/{voice_id}/stream-input
       +-- GET  /v1/models                +-- GET /v1/voices
```

### Service ownership

| Component | Responsibility | Must not own |
|---|---|---|
| FastAPI | Public API, auth, validation, orchestration, sessions, streaming protocol | Model inference |
| LocalAI | Configured LLM and STT models; model discovery | Application API keys, public sessions, dedicated TTS |
| local-tts | Voice listing and speech synthesis | STT, LLM, user accounts, conversation history |
| Database | API-key hashes, sessions, messages, usage records | Raw model weights |
| Client | Capture audio, send audio, render transcript/text, play PCM | Provider credentials or provider routing |

## 3. Provider API contracts to use

### 3.1 LocalAI: LLM

- Base URL: configurable, default development value `http://127.0.0.1:8080`.
- Endpoint: `POST /v1/chat/completions`.
- Model: configurable LocalAI model ID, default from `LOCALAI_LLM_MODEL`.
- Use OpenAI-compatible `messages` payload.
- Enable `stream: true` for voice responses. Parse Server-Sent Events and forward only valid text deltas to the orchestrator.
- Discover installed models through `GET /v1/models`; do not invent model IDs.
- Support an optional per-request model selection only after checking it against the configured allowlist / discovered models. Never let an untrusted caller load arbitrary model files or select arbitrary backend paths.

Example request shape:

```json
{
  "model": "${LOCALAI_LLM_MODEL}",
  "messages": [
    {"role": "system", "content": "You are a concise voice assistant."},
    {"role": "user", "content": "What is machine learning?"}
  ],
  "stream": true
}
```

### 3.2 LocalAI: STT

- Endpoint: `POST /v1/audio/transcriptions`.
- Send `multipart/form-data` with at least `file` and `model`.
- Model: configurable LocalAI STT model ID, default from `LOCALAI_STT_MODEL`.
- Accept supported audio uploads and preserve the original filename/MIME type where possible. LocalAI documents ffmpeg-supported audio formats; the app must still validate size, duration and content type.
- Normalize the LocalAI response to an internal `Transcript` object. Support `response_format` as configured; start with `json` or `verbose_json`, and tolerate a plain-text response if explicitly requested.
- Treat `stream=true` as streaming transcription output for an uploaded audio file, not proof of a live microphone-streaming ASR session. For the MVP, buffer/commit a complete utterance before calling the transcription endpoint.
- Configure the STT backend/model in LocalAI (for example Whisper-family or another supported transcription backend) and verify the model is actually installed and loaded. Do not hard-code one backend into the gateway.

Example request shape:

```bash
curl http://127.0.0.1:8080/v1/audio/transcriptions \
  -F "file=@utterance.webm" \
  -F "model=${LOCALAI_STT_MODEL}" \
  -F "response_format=json"
```

### 3.3 local-tts: TTS

Repository: `https://github.com/saghul/local-tts`

Run it as a separate service. Its base URL must be configurable; development default `http://127.0.0.1:8880` and WebSocket equivalent `ws://127.0.0.1:8880`.

Use the repository's actual endpoint conventions:

- HTTP streaming synthesis: `POST /v1/text-to-speech/{voice_id}/stream`
- Text-input/audio-output WebSocket: `WS /v1/text-to-speech/{voice_id}/stream-input?model_id={tts_model}`
- Voice listing: `GET /v1/voices?model_id={tts_model}`
- TTS model listing: `GET /v1/models`

HTTP request body:

```json
{
  "text": "Hello, this is a speech generation test.",
  "model_id": "kokoro",
  "voice_settings": {"speed": 1.0}
}
```

WebSocket upstream protocol:

1. Connect to `/v1/text-to-speech/{voice_id}/stream-input?model_id={tts_model}`.
2. Send BOS: `{"text":" ","voice_settings":{"speed":1.0}}`.
3. Send phrase chunks: `{"text":"Hello there.","try_trigger_generation":true}`. Use `flush: true` only when the orchestrator needs to force pending text to synthesize.
4. Send EOS: `{"text":""}`.
5. Receive JSON responses containing base64-encoded PCM in `audio`; stop after `isFinal: true`.

Current documented output contract:

- Raw signed PCM, 24,000 Hz, 16-bit, mono, little-endian.
- HTTP response content type is documented as `audio/pcm;rate=24000;encoding=signed-int;bits=16`.
- WebSocket audio bytes arrive base64-encoded inside JSON messages.
- Current documented `output_format` is `pcm_24000`. Do not promise MP3/WAV unless the chosen local-tts version actually adds and tests support for it.
- The README documents engines including `kokoro`, `pocket`, `kitten`, and `qwen`; discover actual availability through the running service and do not assume all are enabled on every platform.

Expose a stable internal `TTSProvider` interface so the app can use HTTP for one-shot speech and WebSocket for streamed phrase synthesis.

## 4. Public API to implement

All public routes are served by FastAPI and require an application API key except explicitly documented health routes.

### 4.1 Health and capability discovery

- `GET /health/live`: process is alive; no provider inference required.
- `GET /health/ready`: checks required configuration and service reachability. Do not trigger expensive model loads on every check.
- `GET /v1/providers`: authenticated summary of configured provider status (never disclose secrets or internal filesystem paths).
- `GET /v1/models`: authenticated LocalAI model listing, with model capability/type labels if reliably discoverable. Do not mix LocalAI model IDs with local-tts model IDs.
- `GET /v1/voices`: proxy local-tts voice listing for the configured/default TTS model.
- `GET /v1/tts/models`: proxy local-tts model listing. Use this namespaced route to avoid confusing LocalAI models with TTS engines.

### 4.2 STT route

`POST /v1/audio/transcriptions`

- OpenAI-style multipart request with `file`, optional `model`, `language`, and `response_format`.
- Default model comes from `LOCALAI_STT_MODEL`.
- Validate request size, duration where measurable, allowed MIME/file suffix, and model allowlist.
- Return normalized transcript JSON. Keep provider-specific response shape handling inside the adapter.

### 4.3 LLM route

`POST /v1/chat/completions`

- OpenAI-compatible request shape with `model`, `messages`, `stream`, and supported generation parameters.
- Default model comes from `LOCALAI_LLM_MODEL`.
- Proxy/normalize streaming SSE without buffering the entire response when `stream=true`.
- Do not forward internal LocalAI service credentials or internal error traces.

### 4.4 TTS routes (ElevenLabs-style)

- `POST /v1/text-to-speech/{voice_id}/stream`
- `WS /v1/text-to-speech/{voice_id}/stream-input`
- `GET /v1/voices`
- `GET /v1/tts/models`

These are gateway routes that proxy to `saghul/local-tts`. Preserve the documented request fields and supported response format. Authenticate at the gateway; never expose the internal local-tts port publicly.

### 4.5 Combined voice API

MVP synchronous route:

`POST /v1/voice/turn`

Input: multipart audio file + optional `session_id`, optional model/voice selections from allowlists.

Internal sequence:

1. Authenticate API key and check quota.
2. Validate audio upload.
3. Transcribe through LocalAI STT.
4. Load bounded conversation history for the session.
5. Append user transcript to LLM message context.
6. Stream or collect LocalAI LLM response.
7. Send response text to local-tts.
8. Save transcript and assistant response.
9. Return transcript, answer text, audio representation/metadata, session ID and request ID.

For the first version, a JSON response may include base64 PCM for small responses, with explicit `audio_format` metadata. Put a strict maximum response size on this mode. Prefer a streaming response / WebSocket for longer outputs rather than holding large audio blobs in memory.

Real-time transport route (phase 2):

`WS /v1/voice/stream`

- The client sends an authenticated session-start event, bounded audio frames, then an explicit `audio.commit`/end-of-utterance event.
- MVP behavior: accumulate one complete encoded utterance, then call LocalAI transcription. The incoming WebSocket does not itself imply incremental STT.
- After transcription completes, stream LLM text deltas and send sentence/phrase chunks to the local-tts WebSocket.
- Send transcript/text/audio events back to the client. Send audio metadata before raw binary PCM frames, or use documented base64 event envelopes consistently. Do not mix framing styles without an explicit event contract.
- Send a terminal `turn.completed` event. Handle client disconnects and `turn.cancel`.

Suggested client/server event contract (final names may be adjusted once documented in `docs/api-contract.md`):

Client -> server:

```json
{"type":"session.start","session_id":null,"input_audio":{"container":"webm","codec":"opus"}}
{"type":"audio.commit"}
{"type":"turn.cancel","turn_id":"..."}
{"type":"session.end"}
```

Audio frames are binary WebSocket messages between `session.start` and `audio.commit`. The gateway must concatenate them safely into one bounded audio file and pass a valid file to LocalAI. The client must declare the actual container/codec. Reject unsupported or inconsistent format declarations.

Server -> client control events:

```json
{"type":"session.created","session_id":"..."}
{"type":"transcript.completed","turn_id":"...","text":"..."}
{"type":"response.text.delta","turn_id":"...","delta":"..."}
{"type":"audio.started","turn_id":"...","sample_rate":24000,"sample_format":"s16le","channels":1}
{"type":"audio.completed","turn_id":"..."}
{"type":"turn.completed","turn_id":"..."}
{"type":"turn.cancelled","turn_id":"..."}
{"type":"error","code":"...","message":"..."}
```

When `audio.started` has been sent, binary messages contain only raw PCM bytes until `audio.completed`. If choosing base64 JSON events instead, specify and test that contract instead; do not ambiguously alternate.

## 5. Application structure

Adapt the actual repository structure if it already exists. Target structure:

```text
voice-platform/
├── plan.md
├── README.md
├── .env.example
├── pyproject.toml
├── docker-compose.yml              # optional; only where compatible
├── app/
│   ├── main.py
│   ├── api/
│   │   ├── health.py
│   │   ├── models.py
│   │   ├── transcriptions.py
│   │   ├── chat.py
│   │   ├── tts.py
│   │   └── voice.py
│   ├── core/
│   │   ├── config.py
│   │   ├── security.py
│   │   ├── errors.py
│   │   └── logging.py
│   ├── providers/
│   │   ├── base.py
│   │   ├── localai_client.py
│   │   ├── localai_stt.py
│   │   ├── localai_llm.py
│   │   └── localtts_client.py
│   ├── services/
│   │   ├── voice_orchestrator.py
│   │   ├── text_chunker.py
│   │   ├── session_service.py
│   │   └── model_registry.py
│   ├── schemas/
│   │   ├── audio.py
│   │   ├── chat.py
│   │   ├── voice.py
│   │   └── events.py
│   └── storage/
│       ├── database.py
│       ├── models.py
│       └── repositories.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── contract/
└── docs/
    ├── architecture.md
    ├── api-contract.md
    ├── localai-setup.md
    └── local-tts-setup.md
```

Keep HTTP clients long-lived and manage them through FastAPI lifespan. Use async HTTP/WebSocket clients; do not call blocking provider code directly in the event loop.

## 6. Configuration

Create typed settings with environment variables. Provide `.env.example` only; never commit real `.env`, tokens, model credentials or API keys.

Minimum settings:

```dotenv
APP_ENV=development
APP_HOST=127.0.0.1
APP_PORT=8000
APP_DATABASE_URL=sqlite+aiosqlite:///./voice_platform.db

LOCALAI_BASE_URL=http://127.0.0.1:8080
LOCALAI_API_KEY=
LOCALAI_LLM_MODEL=
LOCALAI_STT_MODEL=
LOCALAI_REQUEST_TIMEOUT_SECONDS=120
LOCALAI_ALLOWED_LLM_MODELS=
LOCALAI_ALLOWED_STT_MODELS=

LOCAL_TTS_HTTP_BASE_URL=http://127.0.0.1:8880
LOCAL_TTS_WS_BASE_URL=ws://127.0.0.1:8880
LOCAL_TTS_MODEL=kokoro
LOCAL_TTS_VOICE_ID=af_heart
LOCAL_TTS_SPEED=1.0

API_KEY_HASH_SECRET=change-me-in-development-only
MAX_AUDIO_UPLOAD_MB=20
MAX_AUDIO_DURATION_SECONDS=120
MAX_SESSION_MESSAGES=30
MAX_ACTIVE_VOICE_TURNS=1
VOICE_TEXT_CHUNK_MAX_CHARS=250
VOICE_TEXT_CHUNK_MAX_WAIT_MS=500
```

Requirements:

- Validate required values at startup and fail with actionable messages.
- Separate public API authentication keys from any optional LocalAI service key.
- Keep service URLs internal/private by default.
- Make model IDs and voice choices configurable; verify choices against live service discovery / allowlists.
- Limit concurrent inference based on available memory. Start conservatively and measure before increasing.
- For the known development target of an 8 GB Apple Silicon Mac, do not assume a full-size local LLM, STT model and TTS model can all be resident concurrently. Prefer one lightweight TTS engine and a small/quantized LocalAI model configuration, or use a remote model served through LocalAI if the local resource budget is exceeded. Document measured RAM, startup time and first-audio latency.

## 7. Multi-agent worker plan

Use one integration lead and bounded worker ownership. The integration lead creates the shared contracts before parallel coding begins. Workers must not independently redefine routes, payload schemas, event names or settings.

### Worker A0 — Integration lead / architecture owner

**Owns:** `plan.md`, `docs/architecture.md`, `docs/api-contract.md`, final integration and merge order.

Tasks:

1. Inspect the current repository, identify existing working code and write a short repo inventory before changes.
2. Confirm LocalAI and local-tts versions/commits and record them in documentation.
3. Freeze the provider contracts and public event protocol in `docs/api-contract.md`.
4. Define shared Python interfaces, exception types and configuration names.
5. Create non-overlapping work assignments and feature branches.
6. Integrate workers' branches in dependency order, resolve conflicts, run all tests and update the status checklist in this plan.
7. Do not mark a capability complete based only on mocked tests; require at least one real provider smoke test for each integration.

### Worker A1 — LocalAI STT adapter

**Owns:** `app/providers/localai_stt.py`, `app/api/transcriptions.py`, STT schemas, `tests/unit/test_localai_stt.py`, STT integration tests and `docs/localai-setup.md` STT section.

Tasks:

1. Inspect the current LocalAI audio-to-text documentation and choose a tested transcription model/backend supported by the target platform.
2. Implement async multipart upload to `POST /v1/audio/transcriptions`.
3. Normalize supported response formats to a stable internal `Transcript` schema.
4. Handle provider timeouts, unsupported media, empty transcripts, model-not-found, size limits and LocalAI unavailable errors.
5. Implement model discovery/validation for STT model IDs without exposing arbitrary model paths.
6. Test with a real short audio sample and record exact model/backend/configuration.

Acceptance:

- Given a valid audio fixture and an installed STT model, returns non-empty normalized transcript.
- Invalid/oversized audio returns a clear 4xx error.
- LocalAI unavailable/model missing returns a stable 502/503-style provider error, not a stack trace.
- Unit tests mock LocalAI; at least one marked integration test calls a real LocalAI instance.

### Worker A2 — LocalAI LLM adapter

**Owns:** `app/providers/localai_llm.py`, chat schemas and `tests/unit/test_localai_llm.py`, LLM integration tests.

Tasks:

1. Implement async requests to `POST /v1/chat/completions`.
2. Support normal JSON completion and streaming SSE text deltas.
3. Normalize provider responses to a stable internal event/chunk interface.
4. Add model discovery and configured allowlist validation.
5. Propagate cancellation and close upstream streaming responses promptly.
6. Handle empty response, model-not-found, context-length, timeout and overload errors.
7. Test with the configured LocalAI LLM model and verify streaming chunks arrive before completion.

Acceptance:

- Non-streaming chat returns a valid assistant message.
- Streaming chat emits ordered text deltas and one terminal completion event.
- Cancellation closes the upstream HTTP stream.
- Unit and real-provider integration tests pass.

### Worker A3 — local-tts adapter

**Owns:** `app/providers/localtts_client.py`, TTS schemas, `app/api/tts.py`, TTS tests and `docs/local-tts-setup.md`.

Tasks:

1. Read and follow `saghul/local-tts` README/API reference; pin and record the tested upstream revision/version.
2. Implement async HTTP client for `POST /v1/text-to-speech/{voice_id}/stream`.
3. Implement async WebSocket client for `/v1/text-to-speech/{voice_id}/stream-input` using the documented BOS, text-chunk, flush/EOS and audio response protocol.
4. Decode upstream base64 audio chunks to raw bytes. Validate expected PCM format and surface it as explicit metadata.
5. Implement `GET /v1/voices` and model discovery through the local-tts service. Expose public namespaced model discovery as `/v1/tts/models`.
6. Implement robust close/finalization behavior and ensure client cancellation closes the upstream WebSocket.
7. Test short text, multi-chunk text, empty input, invalid voice, service unavailable, `isFinal`, and PCM byte output.
8. Investigate and document the known local-tts Kokoro initialization failure if it reproduces: the saved runtime log showed `No module named pip` followed by Misaki/spaCy auto-download failure. Do not hide this with a fake successful response. Fix environment/model installation reproducibly and add a smoke test.

Acceptance:

- TTS HTTP route returns non-empty raw PCM with the documented 24 kHz / signed 16-bit / mono contract.
- TTS WebSocket receives text chunks and emits decoded non-empty PCM before the final event for a supported engine.
- No conversion to WAV/MP3 is claimed unless implemented and verified.
- Upstream errors become stable gateway errors.

### Worker A4 — FastAPI gateway, authentication and sessions

**Owns:** `app/main.py`, `app/api/health.py`, `app/api/models.py`, `app/core/`, `app/storage/`, shared dependencies and API auth/session tests. Coordinate with A0 before creating files shared with A1-A3.

Tasks:

1. Build/extend the FastAPI application and lifespan-managed provider clients.
2. Implement API-key creation/validation. Generate high-entropy random keys, show the plaintext only once, store only a strong hash, support revocation and optional scopes/quotas.
3. Accept a canonical `xi-api-key` header for ElevenLabs-style TTS clients and `Authorization: Bearer` for OpenAI-style clients; map both to the same internal authenticated principal. Never put permanent keys in query parameters.
4. Implement model/voice discovery routes and health endpoints.
5. Implement persistent session/message storage with a simple SQLite development default and a configurable production database URL. Bound history length and do not store raw audio by default.
6. Implement request IDs, structured logs, timeouts, upload size enforcement and consistent error responses.
7. For browser WebSocket authentication, issue short-lived, single-use connection tickets through an authenticated HTTPS endpoint. Do not rely on exposing permanent API keys in WebSocket query strings.

Acceptance:

- Missing, invalid and revoked API keys are rejected.
- API key secrets are not persisted or logged in plaintext.
- Session history is isolated by authenticated owner and session ID.
- Health checks do not trigger expensive model inference.

### Worker A5 — Orchestration and voice streaming

**Owns:** `app/services/voice_orchestrator.py`, `app/services/text_chunker.py`, `app/api/voice.py`, event schemas and orchestrator tests. Depends on the contracts/interfaces from A0 and provider adapters from A1-A3.

Tasks:

1. Implement the one-shot end-to-end `POST /v1/voice/turn` first.
2. Execute STT -> history lookup -> LLM -> phrase chunker -> TTS -> persist turn.
3. Stream LLM output into TTS using a bounded text buffer. Prefer phrase/sentence boundaries; retain final text until a safe flush. Avoid calling TTS once per token.
4. Implement the WebSocket event contract from `docs/api-contract.md`.
5. Maintain a turn ID and cancellation task per active session. A new user interruption or `turn.cancel` must stop LLM generation, stop sending text to TTS, close the TTS WebSocket and discard queued stale audio.
6. Apply bounded queues/backpressure so a slow client cannot accumulate unlimited audio/text in memory.
7. Save transcript and final assistant text in order. Save status/error information for incomplete turns without corrupting previous completed turns.
8. Do not label the current file/utterance upload behavior as streaming STT. Add a separate design/test before incremental STT is claimed.

Acceptance:

- A real sample audio upload returns transcript, LLM answer and non-empty TTS audio.
- Streaming mode emits text before the complete LLM response is finished and begins audio generation on phrase boundaries.
- Cancellation stops new audio from reaching the client and releases upstream resources.
- Two users/sessions cannot read each other's history or audio.

### Worker A6 — Test harness, developer experience and docs

**Owns:** `tests/contract/`, end-to-end test scripts, `README.md`, `.env.example`, and developer setup docs. Coordinate shared settings/API names with A0.

Tasks:

1. Create deterministic mocked contract tests for LocalAI STT, LocalAI LLM SSE and local-tts HTTP/WebSocket protocols.
2. Create opt-in real-provider smoke tests that require local services and explicit model IDs.
3. Add a tiny sample audio fixture with license/source note, or provide a script for generating a test recording. Do not commit personal recordings.
4. Document LocalAI startup, model install/config, model ID discovery, local-tts startup, voice/model discovery, FastAPI startup and curl/WebSocket examples.
5. Document which capabilities are complete, experimental or unsupported.
6. Add lint/type-check/test commands and a clean setup walkthrough.

Acceptance:

- A fresh developer can start the services, configure model IDs and make a successful STT, LLM and TTS call by following README.
- Tests work without running heavyweight models by default; real integration tests are explicitly marked.

## 8. Parallelization and merge rules

1. A0 starts first and creates/finalizes `docs/api-contract.md`, shared interfaces, event schemas and settings names.
2. After the contract is frozen, A1, A2, A3 and A4 can work in parallel on their owned files. A6 can prepare docs/test infrastructure against the frozen contract.
3. A5 starts with mocked provider interfaces while A1-A3 build adapters. Integrate actual adapters after their contract tests pass.
4. No two workers edit the same file without explicit coordination. If a shared file needs changes, A0 owns the edit and communicates the change before workers rebase.
5. Each worker commits only its owned scope and reports: changed files, tests executed, provider assumptions and unresolved issues.
6. Merge order: A0 contracts -> A4 gateway foundation -> A1/A2/A3 adapters (parallel) -> A5 orchestration -> A6 docs/e2e -> A0 final integration.
7. Use branches such as `feature/voice/worker-a1-localai-stt`; do not create directories named after branches. Branches are Git references, not folders.
8. Do not merge code that only passes mocked tests if a real provider smoke test is required for that milestone. If no provider is available, mark the acceptance criterion blocked, not passed.

## 9. Implementation phases and gates

### Phase 0 — Repository reconnaissance and contract freeze

- Inspect source tree, existing entry points, dependencies, tests and runtime config.
- Identify any existing LocalAI/local-tts processes/configuration.
- Record the LocalAI version and local-tts commit/version.
- Confirm model IDs actually served by LocalAI and voices/engines served by local-tts.
- Publish `docs/api-contract.md` before parallel implementation.

**Gate:** no guessed endpoint schemas or competing worker contracts.

### Phase 1 — Independent provider smoke tests

- LocalAI STT transcribes a known test audio file.
- LocalAI LLM returns one normal response and one streaming response.
- local-tts returns valid PCM through HTTP and WebSocket.
- Fix reproducible dependency/model-loading errors before orchestration.

**Gate:** all three providers pass independent smoke tests.

### Phase 2 — Synchronous end-to-end turn

- Implement public API key auth.
- Implement `/v1/voice/turn`.
- Persist a basic session and transcript/answer.
- Return or stream TTS audio with explicit format metadata.

**Gate:** one real utterance completes audio -> text -> answer -> audio.

### Phase 3 — Streaming assistant response

- Stream LLM SSE chunks.
- Implement phrase/sentence buffer.
- Feed local-tts WebSocket in its documented protocol.
- Forward audio frames with clear event framing.

**Gate:** first audio arrives before the full LLM response is complete, on tested hardware/configuration.

### Phase 4 — Interactive WebSocket and cancellation

- Receive chunked client audio and commit a complete utterance.
- Handle session creation, transcript, response text, audio, end-of-turn and errors.
- Implement cancellation, client disconnect handling, backpressure and cleanup.
- Add short-lived WebSocket tickets.

**Gate:** repeated turns and interruption tests pass without stale audio or leaked sessions.

### Phase 5 — Hardening and performance

- Add rate limits, upload limits, timeouts, health checks, provider retries only where safe, request IDs and structured logs.
- Measure time-to-transcript, time-to-first-text, time-to-first-audio, total turn latency, peak memory and failure rate.
- Bound concurrency for the target hardware. Do not tune based on guesswork.
- Document supported browser audio codecs and exact output audio format.

**Gate:** a clean install and documented smoke test run successfully; no unsupported capability is advertised.

## 10. Audio and streaming correctness requirements

- Track the input container/codec/sample rate and pass a real file or valid encoded audio stream to LocalAI. If the client sends raw PCM, wrap/convert it to a supported container (for example WAV) before sending it to LocalAI. Do not mislabel raw bytes as WAV/WebM.
- Enforce upload byte and duration limits before model inference.
- TTS output from local-tts is raw `s16le`, mono, 24 kHz PCM. Send the format metadata before audio bytes and ensure browser playback uses the same format or a tested conversion.
- Do not concatenate arbitrary LLM token fragments directly into separate TTS requests. Use a text chunker with punctuation/sentence handling, max buffer size and a bounded wait threshold.
- TTS WebSocket lifecycle is one session per speech response unless the upstream protocol/version explicitly documents reusable sessions. Always send EOS and drain the final event when not cancelled.
- If cancelled, do not wait indefinitely for a model to finish. Close upstream streams and discard pending output.
- Avoid unbounded `asyncio.Queue`, unbounded audio buffers or full-response buffering in streaming routes.
- Use one clear WebSocket framing contract: JSON control events plus binary PCM frames, or base64 JSON audio events. Document and test it end-to-end.
- Browser WebSocket APIs cannot reliably set arbitrary authorization headers. Use a short-lived one-time ticket rather than a permanent API key in the URL.

## 11. Security, privacy and reliability

- Generate API keys with a cryptographically secure random generator; store only a slow hash/HMAC hash with a server-side secret and key metadata (prefix, owner, scopes, created/revoked timestamps).
- Redact API keys, authorization headers, audio bytes and sensitive transcripts from logs.
- Do not persist raw audio by default. Use temporary files with safe random names and delete them in `finally` blocks, including failure/cancellation paths.
- Enforce per-key request limits, audio size/duration limits, maximum active streams and bounded response sizes.
- Validate `voice_id`, TTS model, LocalAI model and all caller-controlled generation settings against allowlists and sensible ranges.
- Keep LocalAI/local-tts services on loopback/private network. The gateway should be the only public entry point.
- Do not expose arbitrary filesystem model paths or shell commands through the API.
- Make provider failures explicit and typed: timeout, unavailable, model-not-found, invalid audio, invalid voice, quota exceeded, cancelled.
- Use request IDs and correlation IDs across the gateway and provider logs without logging secrets or full raw audio.

## 12. Test plan

### Unit tests

- Configuration validation and missing environment values.
- API key generation, hashing, lookup, revocation and scope enforcement.
- Model/voice allowlist validation.
- Audio file validation and temporary-file cleanup.
- LocalAI STT response normalization for JSON/text/verbose JSON.
- LocalAI LLM normal and SSE parsing, malformed SSE, timeout and cancellation.
- local-tts HTTP content type/PCM bytes, WebSocket BOS/chunks/EOS/final message and base64 decoding.
- Text chunker: punctuation, abbreviations, Unicode, long unpunctuated text, final remainder and max buffer.
- Session isolation, bounded history and ordered turn persistence.
- Voice orchestrator success/failure/cancellation at each stage.

### Integration tests

- LocalAI `/v1/models` is reachable and configured STT/LLM model IDs exist.
- One real STT fixture returns expected core words.
- One real LocalAI chat call returns non-empty text.
- local-tts HTTP and WebSocket produce non-empty PCM.
- Complete voice turn succeeds from an uploaded fixture.
- Streaming turn produces response text and PCM chunks in order.

### Failure and interruption tests

- LocalAI unavailable during STT.
- LocalAI unavailable during LLM generation.
- local-tts unavailable before or during synthesis.
- Model ID or voice ID is invalid.
- Client disconnects during STT, LLM or TTS.
- Client cancels while LLM is streaming and while TTS is generating.
- Very large audio, empty audio, unsupported audio, malformed WebSocket messages.
- Slow consumer causes bounded backpressure rather than memory growth.
- Two sessions concurrently do not mix transcript, text or audio.

## 13. Definition of done

The project is done only when all of the following are true:

- [ ] LocalAI serves a verified configurable LLM model and STT model.
- [ ] local-tts is a separate, reachable service and its configured TTS engine/voice has passed a real synthesis test.
- [ ] FastAPI exposes authenticated STT, LLM, TTS and combined voice APIs.
- [ ] The actual end-to-end flow audio input -> STT transcript -> LLM response -> local-tts audio output passes with a real sample.
- [ ] LocalAI and local-tts model selection is configurable and validated; no model IDs are guessed.
- [ ] TTS audio metadata correctly declares signed 16-bit, 24 kHz, mono, little-endian PCM for the documented local-tts output.
- [ ] WebSocket protocol, event framing, chunking, EOS and cancellation are documented and integration-tested.
- [ ] API keys are securely generated, hashed, revocable and never logged in plaintext.
- [ ] Session history is isolated and bounded; raw audio is not persisted by default.
- [ ] Provider failure, timeout and cancellation paths return predictable errors and clean up resources.
- [ ] README contains a reproducible local setup and curl/WebSocket examples.
- [ ] Tests, lint and type checks pass; any skipped real-provider test is clearly marked as blocked, not passed.

## 14. Open issues to resolve during Phase 0 (do not guess)

- Which exact LocalAI release/runtime will be used on the target machine, and whether it runs natively or in Docker.
- Which STT backend/model and LLM model are actually installed and performant on the target hardware.
- Which local-tts engine/voice works reliably with the installed Python/runtime dependencies.
- Whether a true incremental STT path is required immediately. For the MVP, use complete utterance upload/commit; investigate LocalAI realtime/cached streaming ASR separately if live partial transcripts are a hard requirement.
- Whether the public client requires strict ElevenLabs drop-in compatibility or only ElevenLabs-style TTS routes. Maintain an explicit compatibility matrix and do not claim full API parity without tests.

## 15. Reference documentation

Use these as implementation references and re-check them against the exact pinned versions during Phase 0:

- LocalAI repository: https://github.com/mudler/LocalAI
- LocalAI overview / quick start: https://localai.io/docs/getting-started/
- LocalAI chat API: https://localai.io/docs/features/text-generation/
- LocalAI audio-to-text API: https://localai.io/docs/features/audio-to-text/
- LocalAI realtime API (reference only; do not use its bundled TTS pipeline for this design): https://localai.io/docs/features/openai-realtime/
- local-tts repository and API reference: https://github.com/saghul/local-tts

**Important LocalAI note:** LocalAI also has a Realtime API that can orchestrate VAD, transcription, LLM and a LocalAI TTS model in one configured pipeline. This project intentionally does not use that end-to-end pipeline as its primary voice runtime because TTS must be handled by `saghul/local-tts`. Use LocalAI's individual STT and chat endpoints from the FastAPI orchestrator instead.

---

## Worker status board

Update this board as work progresses. A task is `DONE` only after its acceptance checks pass.

| Workstream | Owner | Status | Evidence / PR |
|---|---|---|---|
| Repo reconnaissance + shared contracts | A0 | TODO | |
| LocalAI STT adapter | A1 | TODO | |
| LocalAI LLM adapter | A2 | TODO | |
| local-tts adapter | A3 | TODO | |
| FastAPI gateway + auth + sessions | A4 | TODO | |
| Voice orchestration + WebSocket | A5 | TODO | |
| Tests + docs + developer setup | A6 | TODO | |
| Final end-to-end validation | A0 | TODO | |

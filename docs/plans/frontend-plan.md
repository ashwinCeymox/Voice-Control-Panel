# Frontend Plan: Voice AI Control Panel

## 1. Product Summary

**Core essence:** A self-hosted voice AI control panel. It sits on a FastAPI gateway that chains speech-to-text (LocalAI), an LLM (LocalAI) and text-to-speech (local-tts). The UI offers a playground for each stage, plus a live voice assistant that combines all three.

**Backend entities**

- Provider (LocalAI, local-tts) with health status
- Model (LLM, STT) and TTS engine (Kokoro, Pocket, Kitten, Qwen)
- Voice
- Session and Turn (transcript, response text, audio, request_id)
- Future entities: API key, usage record, settings

**Constraints that shape the UI**

- Real-time WebSocket events and streamed PCM audio require a Web Audio playback pipeline and mic capture.
- Latency matters, so show stage timings (STT, LLM, TTS) wherever possible.
- Providers are local services that may be down, so the UI must degrade gracefully.
- No endpoints exist yet for session history, saved settings, or API keys.

---

## 2. MVP Features

### Must-Have

| Feature | Description | Endpoints |
|---|---|---|
| Real-time voice assistant | Mic button, live transcript, streaming response text, audio playback, interrupt button | `WS /v1/voice/stream` |
| Voice turn (non-realtime) | Record or upload audio; show transcript, response text, and playable audio. Fallback and easiest first build | `POST /v1/voice/turn` |
| System status | Live/ready indicator and per-provider status with a clear error state when a provider is down | `GET /health/live`, `GET /health/ready`, `GET /v1/providers` |
| Model and voice selection | Dropdowns for LLM, STT, TTS engine and voice | `GET /v1/models`, `GET /v1/tts/models`, `GET /v1/voices` |
| STT playground | Upload audio; show transcript and detected language | `POST /v1/audio/transcriptions` |
| LLM playground | Chat with streaming and generation parameters | `POST /v1/chat/completions` |
| TTS playground | Text input, voice settings, playback | `POST /v1/text-to-speech/{voice_id}/stream` |
| Current-session conversation view | Chat-style transcript with replayable audio per turn | (client state) |
| Error handling | Surface the `error` event and `request_id` in a toast or inline | all |

### Nice-to-Have

- Voice preview (sample audio per voice) in the voice library
- Streaming TTS tester via `stream-input` with time-to-first-audio readout
- Per-turn latency breakdown (STT, LLM, TTS)
- Session history (needs a new backend endpoint, or store client-side for now)
- Persisted default model and voice settings
- Waveform or level meter; push-to-talk vs hands-free toggle
- Export a conversation as JSON or text
- API key management, usage analytics, interactive API docs, provider URL configuration (all future endpoints)
- Billing and quotas (skip for a local app)

---

## 3. Design Patterns

| Data | Pattern | Why |
|---|---|---|
| Voice conversation | Chat timeline with a persistent bottom mic bar | Turns are sequential. Each card holds transcript, response and audio player; streaming deltas fill the bubble in place |
| Provider health | Status cards or pills (green, amber, red) | Glanceable; a small set of providers doesn't need a table |
| Models and TTS engines | Searchable select or segmented control | Few items, single choice; a data grid is overkill |
| Voices | Card grid with a play-preview button | Voices are audition-first; a dropdown hides that |
| STT, LLM, TTS playgrounds | Two-pane layout: inputs and settings left, output right | Matches the test-tweak-retest loop; put generation parameters in a collapsible "Advanced" panel |
| Settings | Simple sectioned form | A wizard adds friction for a handful of dropdowns |
| Session history (later) | Master-detail list | Users scan by recency and drill in |
| API keys and usage (later) | Table with row actions and simple charts | The one place a data grid fits |

**Patterns to avoid**

- **Kanban:** nothing in the data has workflow states.
- **Wizard:** the only multi-step flow (STT, LLM, TTS) is automatic in the backend.

### Key Interaction Details

- **Mic state machine:** idle → listening → transcribing → thinking → speaking. The interrupt button is live during "speaking" and sends `turn.cancel`.
- **Streaming:** render `response.text.delta` as it arrives; play PCM chunks as they arrive using the Web Audio API.
- **Persistence:** save the last chosen model and voice in localStorage until a settings endpoint exists.
- **Degraded mode:** if a provider is down, disable the dependent controls and explain why, rather than letting requests fail.

---

## 4. Navigation Structure

A **left sidebar layout with a top status bar**. The sidebar suits a tool with several distinct workspaces, and the status bar keeps provider health visible from every page.

```
Top bar: app name | provider health pills | active LLM / voice quick-switch

Sidebar:
  1. Assistant      (real-time voice, landing page)
  2. Playground
       ├─ Speech-to-Text
       ├─ Chat (LLM)
       └─ Text-to-Speech
  3. Voices         (library and previews)
  4. Models         (LLM / STT / TTS engines)
  5. System         (providers, health, readiness)
  ── future ──
  6. History
  7. API Keys & Usage
  8. Settings
```

- **Landing page:** Assistant, since the voice experience is the core of the product.
- **Mobile:** collapse the sidebar into a bottom tab bar with Assistant, Playground, Voices and System.

---

## 5. WebSocket Event Map (for the Assistant page)

| Event | Direction | UI reaction |
|---|---|---|
| `session.start` | Client → Server | Sent when the user opens the assistant or presses the mic |
| `session.created` | Server → Client | Store `session_id`, enable the mic |
| Binary audio frames | Client → Server | Stream mic input while listening |
| `audio.commit` | Client → Server | Sent when the user stops speaking |
| `transcript.completed` | Server → Client | Show the user's bubble |
| `response.text.delta` | Server → Client | Append to the assistant bubble |
| `audio.started` | Server → Client | Configure the player with the audio format |
| Binary PCM | Server → Client | Queue chunks for playback |
| `audio.completed` | Server → Client | Finish playback and return to idle |
| `turn.cancel` | Client → Server | Interrupt button |
| `turn.cancelled` | Server → Client | Stop playback, clear the audio queue |
| `turn.completed` | Server → Client | Finalize the turn card |
| `session.end` | Client → Server | Sent on page leave or "End session" |
| `error` | Server → Client | Toast with the message and `request_id` |

---

## 6. Build Order

1. Layout shell and system status bar (`/health/ready`, `/v1/providers`)
2. Model and voice pickers
3. Voice turn page (`/v1/voice/turn`), the simplest end-to-end win
4. LLM chat playground with streaming
5. TTS and STT playgrounds
6. Real-time assistant over WebSocket (hardest piece, so build it after the fundamentals work)
7. Nice-to-haves

---

## 7. Open Items

- The source API document was cut off mid-sentence in the section on future API requirements. The notes on API-key creation and revocation are missing and should be reviewed.
- Session history, saved settings, API keys and usage analytics have no public routes yet. Until they exist, use client-side storage or hide those pages.

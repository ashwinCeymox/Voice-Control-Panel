# Codebase Map: Voice AI Platform

**How this was made:** from your agent's three architecture documents and the cloud readiness answers. I have **not** seen the source code itself. Where those documents disagree, I say so (section 7) and give a command that settles it.

Labels used: **[pipeline]** = `voice_agent_architecture_and_pipeline.md`, **[answers]** = `cloud-readiness-answers.md` (it cites file and line numbers), **[ai-doc]** / **[tts-doc]** = the LocalAI and local-tts issue logs.

---

## 1. The Big Picture in One Paragraph

The user's browser listens to the microphone and decides locally when the user has finished speaking (Silero VAD). It then sends that one chunk of audio to a FastAPI backend. The backend sends the audio to LocalAI for speech-to-text, sends the text to LocalAI again for an LLM reply that arrives word by word, cuts that reply into sentences, and sends each sentence to local-tts, which turns it into audio. The audio streams back to the browser and plays. If the user speaks over the assistant, the browser tells the backend to stop.

```
Browser: mic → VAD → audio chunk ──WebSocket──▶ FastAPI (port 8000)
                                                   ├─▶ LocalAI (8080): speech-to-text, then LLM
                                                   └─▶ local-tts (8880): text-to-speech
Browser ◀── audio chunks ◀──────────────────────────┘
```

Four programs run together, started by `start.sh`:

| Program | Port | What it is |
|---|---|---|
| Frontend (Vite/React) | 5173 | The web pages |
| FastAPI gateway | 8000 | Your own code; coordinates everything |
| LocalAI | 8080 | Third-party engine for LLM and STT (llama.cpp, whisper.cpp) |
| local-tts | 8880 | Third-party engine for TTS (Kokoro on Apple MLX) |

The code you wrote or generated is the frontend and the gateway (`app/`). LocalAI and local-tts live under `arch/` and are used, not edited.

---

## 2. Folder Map

```
project root
├── start.sh                    starts all four programs, waits until each is healthy, warms models
├── voice_platform.db           SQLite database (created by the gateway)
├── logs/  .pids/               log files and process ids
├── arch/
│   ├── LocalAI/                local-ai binary + models/ folder (downloaded model files and YAMLs)
│   └── local-tts/              Python project with its own .venv (Kokoro engine)
├── app/                        THE GATEWAY (Python, FastAPI)
│   ├── main.py                 app start, CORS, registers routes, warms models
│   ├── core/                   config, security, errors, logging
│   ├── api/                    HTTP and WebSocket endpoints
│   ├── providers/              clients that talk to LocalAI and local-tts
│   ├── services/               the logic that ties providers together
│   ├── storage/                database access
│   └── schemas/                the shapes of data and WebSocket events
├── frontend/
│   ├── package.json
│   ├── public/vad/             Silero model + WASM files, served locally
│   └── src/
│       ├── pages/Assistant.tsx the voice page
│       └── audio/              vadController.ts (mic + VAD), pcmPlayer.ts (playback)
└── tests/                      unit (56), contract (8), integration (11), benchmark (4), fixtures/audio
```

Frontend pages for Models, STT, TTS, Agents, Voice Library and System exist but their file names were not in the documents, so they are not listed.

---

## 3. What Each Backend File Does (plain language)

### `app/core/`
| File | Role |
|---|---|
| `config.py` | All settings in one place: service URLs, default model names, timeouts, size limits, file paths. Almost every "hardcoded" value comes from here |
| `security.py` | Two kinds of login. (1) `require_api_key` checks the `Authorization: Bearer` or `xi-api-key` header against a list of key hashes. (2) WebSocket **tickets**: a one-time code (60 s) that the browser requests first, then uses to open the WebSocket |
| `errors.py` | Named errors (provider unavailable, timeout, model not found) and how they become JSON for the client |
| `logging.py` | Log format; masks tokens and keys in logs |

### `app/api/` (the doors into the system)
| File | Endpoints | Role |
|---|---|---|
| `voice.py` | `WS /v1/voice/stream`, `POST /v1/voice/turn` | **The heart.** Runs the speech → text → LLM → speech pipeline for each turn |
| `agents.py` | `/v1/agents...` | Create, edit, delete, duplicate, load agents; test chat |
| `gallery.py` | `/v1/gallery`, `/v1/models/install`, `DELETE /v1/models/{id}` | Browse, download and delete models |
| `models.py` | `/v1/models/installed` | List what is installed (asks LocalAI) |
| `health.py` | `/health/live`, `/health/ready` | Are the services up? |
| `system.py` | `/v1/system/...` | RAM, CPU, disk info |
| `chat.py` | `/v1/chat/completions` | Plain text chat to the LLM |
| `transcriptions.py` | `/v1/audio/transcriptions` | Upload an audio file, get text |
| `tts.py` | `/v1/audio/speech` | Text in, audio out |

The last three are the "raw endpoints" planned for the first cloud phase.

### `app/providers/` (translators to other programs)
| File | Talks to | Role |
|---|---|---|
| `localai_stt.py` | LocalAI | Sends audio, gets text. Finds the right installed model name, retries once on cold-start errors, **falls back to other Whisper models** on failure, treats unreadable audio as "nothing heard" |
| `localai_llm.py` | LocalAI | Sends chat messages, reads the streamed reply token by token |
| `localtts_client.py` | local-tts | Opens a WebSocket, sends sentences, receives raw audio. Has an HTTP fallback if the WebSocket fails. Cleans up its tasks when a turn is cancelled |

### `app/services/` (the logic)
| File | Role |
|---|---|
| `voice_orchestrator.py` | Keeps a dictionary `_active_stream_tasks` of running turns per session so one can be cancelled instantly |
| `agent_manager.py` | The default agent (`DEFAULT_AGENT`), saving agents in the database, upgrading old agent formats, and the "load agent" lock |
| `text_chunker.py` | Collects the LLM's words and releases a phrase at each `. ! ? ; ,` (max 250 characters) so speech can start before the reply is finished. Ignores empty or whitespace-only pieces |

### `app/storage/`
| File | Role |
|---|---|
| `database.py` | Opens SQLite, creates the five tables: `sessions`, `messages`, `turns`, `agents`, `installed_models` |
| `repositories.py` | Read and write helpers (`AgentRepository`, `SessionRepository`). Keeps only the last 30 messages for a conversation |

### `app/schemas/` (the contracts)
| File | Role |
|---|---|
| `events.py` | The WebSocket messages: session start, audio commit, transcript completed, response text delta, audio started (says "24000 Hz, 16-bit, mono"), audio completed, turn completed, turn cancelled, error |
| `agent.py` | What an agent contains: name, prompt, LLM settings, STT settings, TTS settings, VAD settings |
| `audio.py`, `chat.py`, `voice.py` | Request and response shapes for the REST endpoints |

---

## 4. Frontend Files

| File | Role |
|---|---|
| `frontend/src/audio/vadController.ts` | Wraps `@ricky0123/vad-web`. Detects speech start and end, drops quiet sounds (volume gate), builds the WAV chunk, detects barge-in (talking over the assistant), raises the threshold while the assistant speaks |
| `frontend/src/audio/pcmPlayer.ts` | Plays the incoming 24 kHz audio smoothly using its own audio context. Needed because the microphone side is 16 kHz and the speech side is 24 kHz |
| `frontend/src/pages/Assistant.tsx` | The voice page: opens the WebSocket, shows transcript and reply, switches agents, sends the right audio format on switch |
| `frontend/public/vad/` | The Silero model files and WASM, so nothing loads from the internet |

Current VAD settings **[answers B-22]:** speech starts above 0.45, ends below 0.30, waits 800 ms of silence, keeps 200 ms before speech, ignores segments under 250 ms or quieter than 0.015 RMS, needs 250 ms of speech to count as an interruption, and raises the threshold by 0.15 while the assistant talks.

---

## 5. Follow One Voice Turn Through the Code

1. **Browser** (`vadController.ts`): checks the microphone every 32 ms. When speech has stopped for 800 ms, it checks volume, builds a 16 kHz mono WAV, sends it as one binary WebSocket message, then sends `{"type":"audio.commit"}`.
2. **`voice.py`**: collects the bytes in `audio_buffer`. On `audio.commit` it looks at the first bytes to see whether the data is WAV or WebM (this fixed a past crash), creates a `turn_id`, and starts `process_turn()` as a background task, registered in `voice_orchestrator`.
3. **Agent snapshot:** at the start of the turn, the current agent's settings are copied, so switching agents mid-turn does not change this turn.
4. **STT** (`localai_stt.py`): sends the WAV to LocalAI with temperature 0 and no carry-over text. Gets back `text`.
5. **Gates** (`voice.py`):
   - empty text or "(no speech detected)" → the turn ends quietly, no LLM call
   - known junk phrases (`[music]`, `(applause)`, "Thank you.", repeated words) → the assistant says "Sorry, I didn't catch that. Could you repeat?" (maximum 2 times in a row)
6. **Save and prepare:** the user's text is stored; the last 30 messages plus the agent's system prompt are assembled.
7. **LLM** (`localai_llm.py`): streaming request. Each token goes **two places at once**: to the browser (so text appears as it is written) and into `TextChunker`.
8. **TTS** (`localtts_client.py`): each completed phrase is sent to local-tts over a WebSocket. Audio chunks come back.
9. **Playback:** on the first audio chunk, the backend sends `audio.started` (with 24000 Hz, 16-bit, mono), then forwards the audio bytes. `pcmPlayer.ts` queues and plays them. At the end: `audio.completed`, `turn.completed`.
10. **Interruption:** if the user talks for 250 ms over the assistant, the browser stops playback and sends `turn.cancel`. The backend cancels the turn task, which stops the LLM and closes the TTS socket.

---

## 6. Other Flows

**Loading an agent** (`agents.py`, `agent_manager.py`): one lock `_LOAD_LOCK` protects the change. The agent is marked active in the database and in a global `_ACTIVE_AGENT_ID`. It does **not** unload other models; LocalAI loads the new ones when the next turn asks for them. This is global, so everyone sees the switch.

**Installing a model** (`gallery.py`): the backend asks LocalAI to download (`/models/apply`), receives a job id, the UI polls `/models/jobs/{uuid}`. The installed list comes from LocalAI's `/v1/models` and is mirrored in the `installed_models` table.

**Deleting a model** (`gallery.py`, about lines 563–663): asks LocalAI to delete, then also deletes YAML files and the weight file found inside the YAML, then removes the database row. Model names are validated to block path tricks. **Risk:** if two configs share one weight file, deleting one removes the file the other needs.

**Starting up** (`start.sh`, `main.py`): kills old processes on the four ports, starts LocalAI, warms Whisper and the LLM, starts local-tts, starts the gateway, starts the frontend. `main.py` warms the models again in the background.

**Health:** `/health/ready` asks LocalAI (`/readyz`, `/v1/models`) and local-tts (`/v1/models`).

---

## 7. Places Where the Documents Disagree

| Topic | One document says | Another says | Command to check |
|---|---|---|---|
| TTS WebSocket path | pipeline: `/v1/audio/speech/stream` with `{"text","voice"}` | answers B-01 (cites server code): `/v1/text-to-speech/{voice_id}/stream-input` with `{"text","flush"}` | `grep -n "stream-input\|speech/stream" app/providers/localtts_client.py` |
| SQLite WAL mode | pipeline: enabled | answers B-17: not enabled | `grep -n "journal_mode\|WAL" app/storage/database.py` |
| Speed of each stage | pipeline and LocalAI log: STT 150–250 ms, first token 80–120 ms | answers C-01, C-02: STT about 826 ms, first token about 1203 ms | rerun benchmarks with raw logs (cloud plan P0) |
| System endpoint | pipeline: `/v1/system/metrics` | answers: `/v1/system/resources` | `grep -rn "system/" app/api/system.py app/main.py` |
| Default model names | pipeline/logs: `whisper-base`, `llama-3.2-1b` | answers B-02: config has `whisper-1`, `llama-3.2-3b-instruct`, database and agent seed use the 1B | `grep -rn "whisper-\|llama-" app/ frontend/src start.sh` |
| "No static keys" | pipeline: tickets avoid static keys in URLs | answers B-07: a default dev key exists for HTTP | `grep -rn "dev-voice-key" app/` |

Where they differ, I trust **[answers]** more, since it cites code lines, but each row should be confirmed with its command.

---

## 8. "Where Do I Change X?" Cheat Sheet

| I want to change... | Look at |
|---|---|
| How long the agent waits after I stop talking | Agent VAD settings (`schemas/agent.py`, `agent_manager.py`), applied in `vadController.ts` |
| What counts as noise | `minRms` in `vadController.ts`; junk phrases in `voice.py` (`SILENCE_PHRASES`, `is_transcript_unclear`) |
| Default models | `core/config.py`, `storage/database.py`, `services/agent_manager.py`, `main.py`, `start.sh`, `Assistant.tsx` (all contain names) |
| The assistant's voice or personality | The agent (prompt, TTS voice) through the Agents page |
| How sentences are cut for speech | `services/text_chunker.py` |
| Add a new endpoint | New file in `app/api/`, register it in `app/main.py`, add a schema in `app/schemas/`, add a test |
| Timeouts and size limits | `core/config.py` |
| Login rules | `core/security.py` |
| How models install or delete | `api/gallery.py` |
| Interruption behavior | `vadController.ts` (`bargeInConfirmMs`, threshold boost), `voice.py` and `voice_orchestrator.py` (cancel) |

---

## 9. Suggested Reading Order (about 2 hours)

1. `app/main.py` and `app/core/config.py` (what exists, what is configurable)
2. `app/schemas/events.py` (the vocabulary of the WebSocket)
3. `app/api/voice.py` (the core; follow `process_turn()` with section 5 beside you)
4. `app/services/text_chunker.py` and `voice_orchestrator.py` (short)
5. The three providers
6. `app/services/agent_manager.py` and `app/api/agents.py`
7. `frontend/src/audio/vadController.ts`, `pcmPlayer.ts`, then `Assistant.tsx`
8. `app/api/gallery.py`, `app/core/security.py`
9. `tests/` folder: tests are the quickest way to see intended behavior

**Hands-on exercise:** start the stack, open `logs/backend.log`, speak one sentence, and match each log line to a step in section 5.

---

## 10. What I Could Not See

- The actual code: function bodies, exact names, anything the documents omit
- Frontend page files other than the voice page
- `start.sh` details beyond what the documents summarize
- Whether the tests really cover what is claimed (79 passing is reported, not verified by me)

The prompt in `code-walkthrough-prompt.txt` asks your agent to produce a verified, function-level guide from the real code.

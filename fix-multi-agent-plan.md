# Multi-Agent Plan: Voice AI Control Panel (Final Fix Pass)

**Goal:** Fix every issue raised in the review and ship the redesigned UI in one orchestrated pass.
**Runtime:** herdr (terminal agent multiplexer) running `agy` (Antigravity CLI) agents.
**Target machine:** MacBook Air M2, 8 GB unified memory.

---

## 0. Assumptions to verify in Phase 0

These were not confirmed, so the first agent task is to check them. Nothing else starts until they pass.

| Item | Assumption | How it is verified |
|---|---|---|
| herdr | Installed; provides workspaces, tabs, panes, agent state (blocked / working / done / idle), a CLI and a socket API | `herdr help`; read the herdr agent skill |
| agy integration | herdr detects Antigravity CLI panes | `herdr integration install` for agy, then check the sidebar state |
| Orchestrator model | "Gemini 3.8 Flash High" is selectable in agy under that exact name | Run `agy` model list; record the exact ID in `AGENTS.md` |
| Worker model | Claude Sonnet 4.6 is selectable in agy | Same check |
| Inter-agent messaging | The herdr + agy communication skill can send text to a pane, read pane output and wait for another agent | Orchestrator sends a ping to a worker and gets a reply |

**Fallback if messaging is unavailable:** agents communicate through files only (the shared task board in section 3). This is slower but fully reliable, and the plan is written so it works either way.

---

## 1. Agent Roster

Rule of thumb: **Gemini Flash handles planning, research, UI and wiring. Sonnet 4.6 handles anything that can corrupt data, disk, audio or the user's machine.** Sonnet is token-hungry, so it receives narrow tasks, reads only the contract files it needs, and does not do open-ended research.

| ID | Role | Model | Owns |
|---|---|---|---|
| **ORCH** | Orchestrator | Gemini Flash High | Plan, task board, decisions, merges, UI wiring, final acceptance |
| **R1–R5** | Researchers (read-only) | Gemini Flash High | Decision records (section 4) |
| **S-AUDIO** | Realtime and audio core | Sonnet 4.6 | WebSocket client/server flow, mic capture, PCM playback, VAD, echo handling, barge-in |
| **S-MODELS** | Model manager | Sonnet 4.6 | Gallery, install/uninstall jobs, disk and memory checks, fit scoring |
| **S-AGENTS** | Agent store | Sonnet 4.6 | SQLite schema, Agent CRUD, load/unload lifecycle |
| **G-UI** | Frontend builder | Gemini Flash High | Pages, layout, orb animation, forms, styling |
| **G-TEST** | Test and QA | Gemini Flash High | Contract tests, UI smoke tests, 8 GB soak checklist |
| **S-REVIEW** | Security and safety reviewer | Sonnet 4.6 | Reviews S-MODELS, S-AGENTS and S-AUDIO diffs before merge |

### herdr layout

```
Workspace: voice-panel
  Tab "control"    ORCH (pane 1)  |  task board / logs (pane 2)
  Tab "research"   R1 | R2 | R3 | R4 | R5
  Tab "core"       S-AUDIO | S-MODELS | S-AGENTS
  Tab "ui-qa"      G-UI | G-TEST | S-REVIEW
```

- Each worker runs in its own **git worktree** (`../wt-<id>`) on its own branch. ORCH is the only agent that merges to `main`.
- Watch the herdr sidebar: **blocked** means an agent needs a decision, so ORCH answers it. **done** means the task is ready for review.

---

## 2. Phases

```
P0 Bootstrap ─▶ P1 Research ─▶ P2 Decision gate ─▶ P3 Contract freeze
                                                         │
        P7 Acceptance ◀─ P6 Verify on 8 GB ◀─ P5 Integrate ◀─ P4 Parallel build
```

| Phase | What happens | Exit criterion |
|---|---|---|
| **P0 Bootstrap** | Verify the table in section 0. Create worktrees, `AGENTS.md`, task board. | All checks pass or fallback chosen |
| **P1 Research** | R1–R5 run in parallel (read-only) and each writes one decision record. | 5 records in `docs/decisions/` |
| **P2 Decision gate** | ORCH reads all records, picks winners, and writes `docs/decisions/FINAL.md`. S-REVIEW challenges the two risky choices (echo/VAD, install safety). | `FINAL.md` signed off |
| **P3 Contract freeze** | ORCH writes the OpenAPI file, WebSocket event list and Agent schema. After this, contracts change only through ORCH. | `docs/contracts/` committed |
| **P4 Parallel build** | Workers build against the contracts using mocks. | Each task passes its own checks |
| **P5 Integrate** | ORCH merges branch by branch, G-TEST runs contract tests. | `main` green |
| **P6 Verify on 8 GB** | Real run on the M2 Air with the default agent loaded. | Checklist in section 7 passes |
| **P7 Acceptance** | ORCH checks the five requested changes (section 8). | All boxes ticked |

---

## 3. Coordination Protocol

**Shared files (the source of truth, even if live messaging works):**

```
AGENTS.md                  rules every agent reads first
docs/board.md              task board: id | owner | status | blocked-by | branch
docs/decisions/R*.md       research outputs
docs/decisions/FINAL.md    chosen options
docs/contracts/openapi.yaml
docs/contracts/ws-events.md
docs/contracts/agent-schema.json
docs/handoff/<id>.md       what was done, what is left, how to test
```

**Rules in `AGENTS.md`:**

1. Read your task in `docs/board.md` and only the contract files it lists.
2. Never edit files outside your owned folders.
3. Never edit contracts. If one is wrong, set status to `blocked` and write the reason in your handoff file.
4. Finish by writing `docs/handoff/<id>.md`, then set status to `done`.
5. Anything that deletes files, runs downloads or touches the disk is reviewed by S-REVIEW before merge.

**Message pattern (when the herdr/agy messaging skill works):** ORCH sends a one-line pointer ("task B3 ready, see board"). Workers reply with one line ("B3 done, see handoff"). Details always live in files, so a missed message never loses work.

---

## 4. Research Setup (agents decide the better option)

Each researcher gets a fixed template so the answers are comparable:

```
Question:
Options (min 3, include "do nothing / simplest"):
Criteria (weighted): memory cost on 8 GB, latency, reliability, build effort, maintenance
Evidence: docs, repo, benchmarks, license  (cite sources; mark anything unverified)
Quick test: smallest experiment run on the M2 Air, with result
Recommendation + confidence (low/med/high) + what would change the answer
```

| ID | Question | Options to compare (starting list) | Feeds |
|---|---|---|---|
| **R1** | Where does VAD run? Does local-tts or LocalAI offer VAD? | Browser (Silero VAD via ONNX/WASM), gateway-side, LocalAI VAD backend, push-to-talk only | S-AUDIO, agent schema |
| **R2** | How do we handle echo and barge-in on laptop speakers? | Browser echo cancellation + noise suppression constraints, headphones-recommended mode, mic mute while speaking, VAD threshold gating during playback | S-AUDIO |
| **R3** | Which STT, LLM and TTS models fit the 8 GB budget together, and how is fit scored? | Small quantized Whisper variants, 1–3B instruct LLMs at 4-bit, Kokoro/Kitten/Pocket/Qwen TTS | S-MODELS, default agent |
| **R4** | Model gallery and install: how do LocalAI and local-tts expose gallery, install and progress? What metadata exists (size, backend, license, voice gender)? | LocalAI gallery API, direct file install, wrapper jobs with polling or SSE | S-MODELS |
| **R5** | Orb animation and agent-load UX: how to drive a smooth, ElevenLabs-style orb from audio levels? | Canvas, WebGL/shader, CSS/SVG with Web Audio `AnalyserNode`; plus reduced-motion handling | G-UI |

Each researcher also answers two shared questions: *"What is the riskiest assumption in your recommendation?"* and *"What is the cheapest way to test it?"*

**Decision rules for P2:**

- Prefer the option with the lowest memory cost if latency stays acceptable.
- If confidence is low, ORCH orders a 30-minute spike before choosing.
- Any unverified claim is treated as unknown, not as fact.
- Write the losing options and why they lost into `FINAL.md`.

---

## 5. Backend Endpoints to Create

All are new. Paths are proposals for ORCH to freeze in P3; each is built by the owner shown.

### Resources and models (S-MODELS)

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/system/resources` | Host memory (total/available), GPU presence, disk free |
| GET | `/v1/gallery/models` | Search and filter gallery. Query: provider, category (stt / llm / tts / vad), backend, `fits=true`, `q`, `page`. Returns size, license, and a **fit score** against the memory budget |
| GET | `/v1/gallery/models/{id}` | Detail: variants, requirements, metadata |
| POST | `/v1/models/install` | Start an install job. Returns `job_id` |
| GET | `/v1/models/install/{job_id}` | Progress (polling; SSE optional, per R4) |
| DELETE | `/v1/models/{id}` | Uninstall (S-REVIEW checks path safety) |
| GET | `/v1/models/installed` | Installed models for LocalAI and local-tts |
| POST | `/v1/models/import` | Import a local model file |

### Voices (S-MODELS)

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/voices` (extended) | Adds `gender` (`male` / `female` / `unknown`), `language`, `engine`, `source` (`metadata` / `inferred` / `manual`) |
| PUT | `/v1/voices/{id}/meta` | Manual gender/label override for engines without metadata |
| POST | `/v1/voices/{id}/preview` | Short sample for the voice picker |

Gender comes from voice metadata where it exists (for example the Kokoro ID prefix), falls back to `unknown`, and can be overridden by the user.

### Agents (S-AGENTS)

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/agents` | List saved agents (name, models, last used) |
| POST | `/v1/agents` | Create |
| GET | `/v1/agents/{id}` | Read |
| PUT | `/v1/agents/{id}` | Update |
| DELETE | `/v1/agents/{id}` | Delete |
| POST | `/v1/agents/{id}/duplicate` | Duplicate |
| POST | `/v1/agents/{id}/load` | Make it active; unloads and loads models as needed. Returns a `load_id` |
| GET | `/v1/agents/active` | Active agent plus load state (`idle` / `loading` / `ready` / `error`) |
| POST | `/v1/agents/{id}/test-chat` | Streamed test chat using that agent's prompt and LLM |

### Voice pipeline changes (S-AUDIO)

- `WS /v1/voice/stream` accepts an optional `agent_id` in `session.start`, and `session.created` returns the agent name.
- Add events for VAD and levels as decided in P2 (for example `vad.speech_start` / `vad.speech_end` if server-side). `ws-events.md` is the single reference.
- Fix the **502 banner versus green status pill** mismatch. Status pills must derive from `/health/ready`, and a failed STT call must turn the LocalAI pill amber or red.

---

## 6. Agent Schema (starting point for P3)

```json
{
  "id": "uuid",
  "name": "string",
  "system_prompt": "string",
  "llm": { "model": "string", "temperature": 0.7, "max_tokens": 512 },
  "stt": { "model": "string", "language": "auto" },
  "tts": { "engine": "kokoro", "voice_id": "af_heart", "speed": 1.0 },
  "vad": { "mode": "per R1", "threshold": 0.5, "min_silence_ms": 600 },
  "audio": { "echo_mode": "per R2", "barge_in": true },
  "created_at": "iso", "updated_at": "iso", "last_used_at": "iso",
  "schema_version": 1
}
```

Storage: SQLite in the backend. VAD and echo fields are finalized after R1 and R2.

---

## 7. Task Breakdown (P4)

| ID | Owner | Task | Depends on |
|---|---|---|---|
| B1 | S-MODELS | System resources endpoint and fit scoring | FINAL, contracts |
| B2 | S-MODELS | Gallery, install jobs, uninstall, import | B1 |
| B3 | S-MODELS | Voice metadata, gender, preview | R4 |
| B4 | S-AGENTS | SQLite schema, Agent CRUD | contracts |
| B5 | S-AGENTS | Load/unload lifecycle and active state | B4, B1 |
| B6 | S-AUDIO | WebSocket changes, agent in session, error events, health-driven status | contracts |
| B7 | S-AUDIO | Browser mic capture, VAD, echo handling, barge-in | R1, R2 |
| B8 | S-AUDIO | PCM playback with output level tap | B6 |
| U1 | G-UI | App shell, status bar from real health, nav rename (LLM Chat → **Agents**) | contracts |
| U2 | G-UI | **Models** page in the gallery style from the screenshot, fit-first sorting, disk and memory chip, Explore/Installed tabs | B1, B2 (mock first) |
| U3 | G-UI | **Voice Assistant**: active agent name, state label, orb animation, mode switch, error banner | B6, B8, R5 |
| U4 | G-UI | **Agents** page: list, editor (system prompt, models from installed list, VAD, voice), load button with loading state, test-chat panel | B4, B5 |
| U5 | G-UI | **TTS** page: installed-model selector, voice picker with gender and name, preview | B3 |
| U6 | G-UI | **STT** page: installed-model selector, language, recorder plus upload, timing readout | B2 |
| U7 | G-UI | Voice Library, System & Health pages kept and updated | B3 |
| T1 | G-TEST | Contract tests against OpenAPI for every endpoint | P3 |
| T2 | G-TEST | UI smoke tests per page, error-state tests | U1–U7 |
| T3 | G-TEST | 8 GB soak checklist (below) | P5 |
| V1 | S-REVIEW | Review B2, B5, B7 diffs (file deletion, path traversal, download sources, mic permission handling) | each branch |

**Per-task rules:** tests included, handoff file written, no edits outside owned folders.

---

## 8. Acceptance Criteria

### Your five requested changes

- [ ] **Voice Assistant page:** shows the active agent's name; the orb animates smoothly with mic level while listening and with audio level while speaking, with distinct idle / listening / thinking / speaking looks; reduced-motion setting respected.
- [ ] **TTS page:** a selector for installed TTS models; each voice shows name plus male / female / unknown from metadata; preview works.
- [ ] **Agents page** replaces LLM Chat: create, edit, duplicate, delete, save and load agents with system prompt, installed models, VAD settings; test-chat panel inside the editor.
- [ ] **STT page:** same pattern as TTS, with an installed-model selector.
- [ ] **Voice Library, Models & Engines, System & Health** remain; Models lets the user download from LocalAI and local-tts with suggestions ranked by memory fit.

### Fixes from the review

- [ ] Status pills reflect real health; the 502 case turns the relevant pill red.
- [ ] Free-text Voice ID replaced by the voice picker.
- [ ] VAD location decided from research (R1) and working.
- [ ] Echo / barge-in approach decided from research (R2) and tested on laptop speakers and with headphones.
- [ ] Voice gender fallback works for engines without metadata.

### 8 GB soak checklist (T3)

- [ ] Default agent (small STT + small LLM + light TTS) loads without swapping heavily; record peak memory.
- [ ] Switching agents shows a loading state and recovers cleanly if a load fails.
- [ ] Models page warns before installing something that will not fit.
- [ ] 10 consecutive voice turns without memory growth or audio glitches.
- [ ] Interrupting the assistant mid-sentence stops audio within an acceptable delay (target set in R2).

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| herdr/agy messaging skill behaves differently than expected | File-based board is the fallback (section 3) |
| "Gemini 3.8 Flash High" name does not match agy's list | Record the actual ID in P0; routing rules use roles, not names |
| Sonnet token cost grows | Narrow tasks, contract-only reading, no research by Sonnet, short handoffs |
| 8 GB too tight for three models at once | Fit scoring uses the combined budget; agent load unloads others first; R3 proposes a lighter default agent |
| Contracts change mid-build | Freeze at P3; changes go only through ORCH with a note on the board |
| Install/uninstall damages files | S-REVIEW gate; operations restricted to the models directory |

---

## 10. Starting Prompts

**ORCH (first message):**
> You are the orchestrator for the Voice AI Control Panel fix pass. Read `AGENTS.md` and this plan. Run Phase 0 checks, then create worktrees and `docs/board.md`. Launch R1–R5 with the research template. Do not start building until `docs/decisions/FINAL.md` exists. Keep messages to one line; put detail in files.

**Researcher (R1–R5):**
> Read-only task. Answer your assigned question using the template in section 4. Cite sources, mark unverified claims, run one small test on this machine, and write `docs/decisions/R<n>.md`. Do not modify code.

**Sonnet worker (S-*):**
> Read only your task in `docs/board.md` and the contract files it lists. Implement in your worktree, within your owned folders. Add tests. Write `docs/handoff/<id>.md` and set status to `done`. If a contract is wrong, set `blocked` and explain; do not edit it.

**S-REVIEW:**
> Review the given diff for: path traversal, unsafe deletes, unvalidated download sources, secrets in logs, mic and permission handling, and error paths that leave models half-loaded. Return a pass/fail with required changes.

# Refurbishment Plan: Voice AI Control Panel

**Model for every agent:** Gemini 3.8 Flash, high thinking (orchestrator and all subagents).
**Runtime:** herdr panes running `agy`, one pane per agent.
**Target machine:** MacBook Air M2, 8 GB unified memory.

---

## 1. Problems to Fix

| # | Problem | Likely cause (to be confirmed, not assumed) | Fix |
|---|---|---|---|
| 1 | Clicking **Install** shows "installing" but the model never appears under **Installed** | Install job never reaches a final state; the Installed list reads a different source than the installer writes to; the frontend does not refetch after the job ends; or the install is simulated | Diagnose first (Phase 0), then make the provider's real installed list the only source of truth |
| 2 | STT, TTS and Agent screens show **hardcoded junk models** | Constants or fallback lists in the frontend or backend | Delete every hardcoded list; each selector reads installed models only |
| 3 | Creating an agent without installed models still works | No guard | Block creation and show "Install the model first to proceed" with a link to Models |
| 4 | A **Stop speaking** button exists although VAD handles turn-taking | Left over from the first design | Remove it; end-of-turn and interruption are driven by VAD (section 4) |
| 5 | No **live transcription** and no visible **current agent** on the voice page | Not built | Show interim and final transcript, and the agent name with a switcher |
| 6 | No way to **add a system prompt** from the Agents screen | Only available deep in the editor | Add an "Add system prompt" action at the top of the Agents page and editor |

---

## 2. Agent Roster (all Gemini 3.8 Flash High)

Because one model now does everything, safety comes from **narrow tasks, tests written first, and a separate reviewer pane** rather than from model choice.

| ID | Role | Owns |
|---|---|---|
| **ORCH** | Orchestrator | Plan, task board, decisions, merges, final acceptance |
| **DIAG** | Diagnostician | Phase 0 only: reproduces and explains problems 1 and 2 |
| **RES** | Researcher (read-only) | Phase 1 decision records (live transcription, turn-taking) |
| **BE** | Backend | Installed-models registry, install job lifecycle, agent validation |
| **FE-MODELS** | Frontend | Models page: Explore / Installed tabs, install progress |
| **FE-SELECT** | Frontend | STT, TTS and Agent screens: installed-only selectors, empty states, guards |
| **FE-VOICE** | Frontend + audio | Voice page: agent header, live transcript, VAD turn-taking, orb |
| **QA** | Tester | Tests, scripted UI checks, hardcode scan |
| **REV** | Reviewer (fresh context) | Adversarial review of every diff before merge |

**Rules for running a Gemini-only team**

1. Start each agent in its own herdr pane with the model pinned. After starting, read the pane's status line and confirm the model. Earlier, a status line showed `gemini-3.8-flash-tiered` while "auto" mode was on, so set a fixed model and record the exact ID in `AGENTS.md`.
2. Each task is small (one behavior, a few files) and begins with a **failing test or a written reproduction**.
3. A task is done only when its tests pass and the handoff file lists the commands run and their output.
4. REV never sees the author's reasoning, only the diff, the task and the acceptance criteria. It must try to break the change.
5. Agents work in git worktrees; only ORCH merges.
6. Coordination goes through `docs/board.md` and `docs/handoff/<id>.md`. Messages between panes are one-line pointers.

---

## 3. Phases

```
P0 Diagnose ─▶ P1 Decide ─▶ P2 Data layer ─▶ P3 Screens ─▶ P4 Voice ─▶ P5 QA ─▶ P6 Accept
```

### P0: Diagnose (DIAG, before any code changes)

Goal: find out why installs never show up.

1. Install one small model from the Models page with browser dev tools open. Record the request, the job ID and every status response.
2. Check the backend directly:
   - What state does the install job end in, and does it ever leave "installing"?
   - Where are files written, and does the provider (LocalAI or local-tts) actually see them?
   - What does the provider's own model list return after the install?
   - What does `/v1/models/installed` read, and is it the same source?
3. Check the frontend: does the Installed tab refetch when the job ends, or does it use stale cached data?
4. Search the whole codebase for hardcoded model and voice names (STT, TTS, agent defaults, placeholders, fallbacks) and list every location.

Output: `docs/diagnosis.md` naming the real cause for each problem, with evidence. No fixes yet.

### P1: Decide (RES, in parallel)

| Question | Options to compare | Decision needed for |
|---|---|---|
| **Live transcription** with the installed STT | (a) rolling re-transcription of buffered audio every ~1 s, (b) a streaming-capable STT model if one is installed, (c) VAD-segment only, no interim text | FE-VOICE |
| **Turn-taking without a stop button** | VAD end-of-speech silence threshold, adaptive endpointing, minimum speech length, handling of "um" pauses | FE-VOICE, agent schema |
| **Interruption (barge-in) while the agent speaks** | VAD detecting speech during playback, echo cancellation on, brief confirmation window to avoid false triggers from the agent's own voice | FE-VOICE |

Each record uses: options (at least 3), criteria (memory on 8 GB, latency, reliability, effort), one small test on this machine, recommendation, confidence. ORCH writes the winners into `docs/decisions/FINAL.md`.

### P2: Data layer (BE)

- **One source of truth:** `GET /v1/models/installed` returns only what the providers actually report as installed, grouped by `stt`, `llm`, `tts`. Nothing hardcoded, no fallback list.
- **Install job lifecycle:** `queued → downloading → verifying → registering → installed`, or `failed` with a reason. The job reaches `installed` only after the provider confirms the model is visible. A stuck job times out into `failed`.
- **Cache:** invalidate the installed list when a job finishes.
- **Voices:** `GET /v1/voices` returns voices only for installed TTS engines, with gender metadata as agreed earlier.
- **Agent validation:** creating or saving an agent returns a clear error code (for example `MODEL_NOT_INSTALLED`) listing which required model is missing.
- Remove all hardcoded defaults found in P0. If the default agent references models that are not installed, it is shown as incomplete rather than silently used.
- **Carry-over:** re-check the agent load memory rule against realistic 8 GB conditions (it was loosened to total memory minus 1,500 MB, which may allow loads that cause swapping).

### P3: Screens (FE-MODELS, FE-SELECT)

**Models page**
- Install button shows real progress from the job and ends in **Installed** (or an error with retry).
- When a job finishes, the model moves to the **Installed** tab and the tab count updates without a page reload.

**STT, TTS and Agent screens**
- Every model and voice selector is filled only from installed models.
- **Empty state:** "No speech-to-text model installed. Install one" with a button to the Models page, filtered to that category.
- TTS voices show name and male / female / unknown.

**Agent creation guard**
- If any required model (STT, LLM, TTS) is missing, show a visible warning: **"Install the model first to proceed."** Save and Load are disabled, and the missing items are listed with install links.
- VAD follows the P1 decision. If it is built into the browser, show it as "Built-in" rather than as a download.

**System prompt**
- On the Agents page, add an **Add system prompt** button at the top right (and the same action in the editor header). It opens the prompt editor for a new agent, or for the selected agent when one is open. Prompts can be named, edited and reused.

### P4: Voice page (FE-VOICE)

**Header:** shows the **current agent name** (and its models in a small tooltip), with a dropdown to switch agents. Switching shows the loading state from the agent load API.

**Remove the Stop speaking button.** Replace the controls with:
- one **Start / End session** control (needed for microphone permission and to leave the conversation) and a **mute mic** toggle. Neither is a turn-taking button.
- The agent starts speaking when VAD detects enough silence after the user's speech.
- If the user speaks while the agent is talking, VAD cancels playback and sends `turn.cancel` automatically.
- Silence length and speech threshold come from the agent's VAD settings.

**Live transcription:** interim text appears in a lighter style while the user speaks and becomes final text when the turn ends. The method follows the P1 decision. The assistant's reply streams under it as it arrives.

**Orb:** states are idle, listening, thinking and speaking, driven by mic and playback levels, with reduced-motion respected.

**Known risk:** on laptop speakers the agent's voice can trigger the VAD and cause false interruptions. This is why P1 includes echo cancellation and a confirmation window, and why the voice page recommends headphones when echo is detected.

### P5: QA (QA, then REV)

- Tests for every item in the acceptance list.
- **Hardcode scan:** a script that fails the build if known placeholder model or voice names appear in source files.
- Scripted run on the M2 Air with real installed models.
- REV reviews each branch before merge, and checks specifically: stuck-job handling, deletes, error states that leave a model half-loaded, and microphone permission handling.

---

## 4. Task Board

| ID | Owner | Task | Depends on |
|---|---|---|---|
| D1 | DIAG | Reproduce install problem and write `docs/diagnosis.md` | none |
| D2 | DIAG | Inventory of every hardcoded model or voice | none |
| R1 | RES | Live transcription decision | none |
| R2 | RES | Turn-taking and barge-in decision | none |
| B1 | BE | Installed-models registry from provider truth | D1 |
| B2 | BE | Install job lifecycle and cache invalidation | D1, B1 |
| B3 | BE | Remove hardcoded defaults; `MODEL_NOT_INSTALLED` validation | D2, B1 |
| B4 | BE | Voices only for installed engines | B1 |
| F1 | FE-MODELS | Real install progress; Installed tab updates | B2 |
| F2 | FE-SELECT | Installed-only selectors on STT, TTS, Agent; empty states | B1, B3 |
| F3 | FE-SELECT | Agent creation guard and warning | B3 |
| F4 | FE-SELECT | Add system prompt button and editor | none |
| V1 | FE-VOICE | Agent name header and switcher | agents API |
| V2 | FE-VOICE | Remove Stop speaking; VAD-driven turn-taking and barge-in | R2 |
| V3 | FE-VOICE | Live transcription | R1 |
| V4 | FE-VOICE | Orb animation and state labels | V2 |
| Q1 | QA | Tests, hardcode scan, 8 GB run | all |
| RV | REV | Review each branch | each branch |

---

## 5. Acceptance Checklist

- [ ] Installing a model ends in **Installed**, the Installed tab shows it, and the count updates without reload.
- [ ] A failed or stuck install shows an error and a retry option.
- [ ] STT, TTS and Agent screens list only installed models; no placeholder names exist in source (scan passes).
- [ ] With nothing installed, each screen shows an empty state with an install link.
- [ ] Creating or saving an agent with a missing model is blocked with "Install the model first to proceed".
- [ ] **Add system prompt** is available at the top of the Agents page and in the editor.
- [ ] No Stop speaking button exists; the agent replies after natural silence and stops when the user speaks.
- [ ] Interim and final transcripts are visible during a conversation.
- [ ] The voice page shows the current agent and allows switching.
- [ ] Interruption by VAD works with headphones and is tested on laptop speakers.
- [ ] Ten consecutive voice turns on the M2 Air without errors or memory growth.

---

## 6. Starting Prompts

**ORCH:**
> You are the orchestrator for the refurbishment. Read `AGENTS.md` and `refurbishment-plan.md`. Create worktrees and `docs/board.md`. Start each agent in its own herdr pane with the model pinned, and confirm the model in each pane's status line before sending a task. Run Phase 0 first. Do not start fixes until `docs/diagnosis.md` exists.

**DIAG:**
> Read-only. Reproduce the install problem and inventory hardcoded model and voice names. Record the evidence (requests, job states, file locations, provider responses). Write `docs/diagnosis.md`. Do not change code.

**Builder (BE, FE-*):**
> Read only your task in `docs/board.md`. Write a failing test first. Implement within your owned files. Run the tests and paste results in `docs/handoff/<id>.md`. Do not hardcode any model or voice name.

**REV:**
> You have only the diff, the task and the acceptance criteria. Try to break the change: stuck jobs, missing models, empty lists, double clicks, failed installs, microphone denied. Return pass or fail with required changes..

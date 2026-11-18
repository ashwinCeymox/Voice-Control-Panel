# Refined Fix Plan: Model Classification and Dashboard Issues

**Core issue (from your report):** model classification is not proper. Several screens show wrong or missing models because there is no single, metadata-driven source of truth for what each model is.
**Agents:** Gemini 3.8 Flash (high), one herdr pane each, model pinned and confirmed in the status line before any task is sent.
**Working rules:** diagnose first, failing test first, small tasks, a fresh-context reviewer, nothing marked done without evidence.

---

## 1. Issue Register

| # | Where | What you reported | Likely causes (to confirm in F0, not assumed) | Fix | Done when |
|---|---|---|---|---|---|
| A1 | STT Playground, Streaming sub-selection | Only 2 entries appear (Zipformer 20M and "Sherpa ONNX"); newly installed streaming models are missing | A hardcoded or name-matched list; classification ignores metadata; new models never get a `kind`; the list is cached and not refreshed after install. Note "Sherpa ONNX" is a **runtime**, not a model, so it is probably a placeholder entry | Registry with metadata-based classification (section 2.1); the runtime becomes an `engine` field | A newly installed streaming model appears in the Streaming list without a restart, and no runtime or placeholder entries are listed |
| A2 | TTS Playground, voice configuration | Two selectors (installed TTS models, voices); every voice belongs to Kokoro 82M, other models are dummies | local-tts reports engines (Pocket, Kitten, Qwen and others) whose weights are not installed or are disabled; voices have no engine link | Show only engines that pass an availability check; voices belong to an engine and the picker cascades engine → voice (section 2.2) | Only working engines are selectable; every listed voice synthesizes |
| A3 | API Docs & Snippets, LLM chat streaming | The model dropdown shows every model, not only LLMs | The dropdown calls the all-models list | Each endpoint declares the model type it needs; the dropdown asks the registry for that type only | Chat shows LLMs only; STT batch shows batch STT only; STT streaming shows streaming STT only; TTS shows available TTS engines only |
| A4 | API Docs, "Try it" | Returns 502 | A wrong-type model selected (upstream error mapped to 502), missing or wrong internal key to LocalAI, wrong base URL or port, timeout, or the admin session path not set up for the gateway | Reproduce per endpoint, fix the root cause, and make errors honest (400 or 404 for a bad model, 502 only for a real upstream failure) | "Try it" works for LLM, STT and TTS with valid inputs, and wrong inputs return a clear 4xx |
| A5 | Request Log | Key/profile shows "None" | Requests from the admin session, the UI and the voice pipeline bypass key attribution; no name is stored | Attribution model (section 2.3): show the key and profile name, or a clear system label; add filters by key and profile | No row shows "None"; filters work |
| A6 | Overview | Graph is empty | The rollup job is not running or not populated, the time-series query returns nothing (time range or time zone), the response shape does not match the chart, or rows are mislabeled | Diagnose, fix the data path, zero-fill empty buckets, add an empty state | Charts show real data for 1 h, 24 h, 7 d and 30 d |
| A7 | Overview: Warm Engine and Memory | Looks hardcoded; wants live active models, idle ("junk") models listed, a flush option, auto-flush, and a live pop-up with the model name | Values are static or estimated | Residency manager and live panels (section 2.5) | Panels show measured live data; idle models are listed; manual flush and auto-flush work; a pop-up names the model |
| A8 | Overview: Model Activity | Show the key name per request and latency metadata | Not built | Model activity table with key names, latency percentiles and drill-down (section 2.6) | Table matches the request log for the same range |

A1, A2 and A3 share one root cause, so they are fixed together.

---

## 2. Design

### 2.1 One model registry (the single source of truth)

Every screen and every endpoint reads models from one registry endpoint. No page keeps its own list.

**Normalized record**

```json
{
  "id": "string",
  "display_name": "Zipformer 20M",
  "family": "zipformer",
  "task": "stt | llm | tts | vad | other",
  "kind": "batch | streaming | null",
  "engine": "localai | streaming-stt | local-tts",
  "runtime": "sherpa-onnx",
  "params": "20M",
  "languages": ["en"],
  "size_mb": 0,
  "license": "string",
  "installed": true,
  "available": true,
  "unavailable_reason": null,
  "loaded": false,
  "classification_source": "metadata | manifest | file-inference | manual | unknown",
  "classification_confidence": "high | medium | low"
}
```

**Where the metadata comes from, in order of trust**
1. **LocalAI models:** the model's YAML configuration and LocalAI's own capabilities listing (`GET /v1/models/capabilities`, which enriches models with capabilities and input and output types). Confirm the exact fields in F0.
2. **Streaming STT models:** a small **manifest file** (`model.json`) in each model folder, written by the install script: id, name, family, `task: stt`, `kind: streaming`, architecture, languages, params, license, runtime. This follows your suggestion to read metadata that says whether a model is streaming.
3. **Models installed before manifests existed:** a backfill script infers the manifest from the files (an encoder, decoder, joiner and tokens file suggests a streaming transducer) and marks it `classification_source: file-inference`, confidence medium, for review.
4. **Manual override:** an admin can set `task` and `kind` for any model in the UI. Overrides are stored and win over everything else.
5. **Unknown:** shown in an **Unclassified** group in the admin Models page, never silently put into any picker.

**Refresh:** the registry updates after every install or delete, on startup, on a "Rescan" action, and on a slow timer. Installing a model must make it appear in the right picker without a restart.

**What each endpoint asks for**

| Screen or endpoint | Registry query |
|---|---|
| LLM chat (docs, playground, agents) | `task=llm`, installed, available |
| STT batch | `task=stt`, `kind=batch` |
| STT streaming | `task=stt`, `kind=streaming` |
| TTS | `task=tts`, `available=true`; voices filtered by the chosen engine |

### 2.2 TTS: real engines and voices only

- The registry marks a TTS engine `available` only if its weights are present **and** a short test synthesis succeeds (cached; rerun on startup, install and Rescan).
- Engines that fail the check are hidden from pickers. The admin Models page shows them as "not installed" or "unavailable" with the reason (for example "weights missing" or "disabled at startup").
- Each voice record has an `engine`. The Voice Library and the TTS playground show **engine first, then its voices**. Voice gender and language come from metadata where it exists.

### 2.3 Request attribution

Every request records an **actor**:

| Actor type | Label in the UI |
|---|---|
| API key | Key name, with the profile name (for example `prod-backend · other-project-server`) |
| Admin session | `Admin (Try it)` or `Admin (UI)` |
| Internal system | `System: voice pipeline`, `System: health check`, `System: warm-up` |
| Unauthenticated or rejected | `Rejected (no key)` or `Rejected (invalid key)` |

- Store `actor_type`, `key_id`, `profile_id` and **a snapshot of the key and profile names** at request time, so the log stays readable after a key is renamed or revoked.
- Old rows with no actor are labeled `System (unattributed)`. "None" is never displayed.
- Request Log filters: key, profile, actor type, endpoint, model, status, time.

### 2.4 Overview data path

- For ranges up to 48 hours, charts read raw events. Longer ranges read hourly rollups.
- The server fills empty time buckets with zeros so lines draw correctly.
- When there is no data, the UI shows a message ("No requests in this range"), not a blank chart.
- The time zone is explicit (store UTC, show local).
- Live numbers (active streams, queue, memory) update through a server-sent events stream every 2 to 5 seconds.

### 2.5 Residency manager (live models, idle models, flushing)

A gateway service that tracks every model that is loaded in memory.

**What it tracks per model:** engine, loaded or not, last used time, in-flight requests, measured memory, and whether it is protected.

**States**

| State | Meaning (defaults, editable in Settings) |
|---|---|
| Active | In use now, or used within the idle threshold |
| Idle | Not used for 5 minutes or more |
| Stale ("junk") | Not used for 7 minutes or more |
| Protected | Never auto-flushed (see below) |

**Flushing**
- **Manual:** a Flush button on any idle or stale model, and "Flush all idle".
- **Automatic:** a check runs every minute. Models that have been unused for the auto-flush time (default **10 minutes**) are unloaded.
- **Never flush** a model that has an in-flight request, is protected, or was used in the last few seconds. Flushing takes a per-model lock, so a request arriving at the same moment waits and reloads the model instead of failing.
- **Protected by default:** the active agent's models, because voice latency matters. This is a setting (default on). Admins can also pin other models.
- **Cooldown:** a flushed model is not flushed again soon after being reloaded, to avoid reload loops on an 8 GB machine.
- Optional later: also flush idle models when memory pressure is high.

**Live pop-ups (toasts)** use the live event stream and always name the model:
- before an auto-flush: "`whisper-large` has been idle for 8 min and will unload in 2 min", with **Keep loaded** and **Flush now**
- after: "`whisper-large` unloaded, freed about 1.5 GB"
- on failure: "Could not unload `model`, reason"

**How unloading works per engine (verify in F0, do not assume)**
- **LocalAI:** the API list you shared includes `/backend/monitor`, `/backend/shutdown`, `/backend/load`, `/system` and `/api/models/{id}/load-status`. Confirm which of these shows loaded models and which unloads one. Also check whether the installed LocalAI version has its own idle-unload setting; if it does, prefer configuring it over building a second mechanism, and keep this manager for visibility and the other engines.
- **local-tts and the streaming STT service:** find out whether they can unload a model on request. If not, the fallback is restarting that service, which unloads everything it holds, so it needs a clear confirmation message and must not run while a request is in flight.

**Memory and Warm Engine panels (replace all hardcoded values)**
- System memory: total, used, available, memory pressure, swap.
- Per service: process memory of LocalAI, local-tts and the streaming STT service.
- Per model: loaded size, labeled **measured** or **estimated** (LocalAI's VRAM estimate is an estimate only).
- Warm Engine: engine health, loaded models, active requests, queue length, last request time.

### 2.6 Model activity (Overview)

A table with one row per model for the selected range:

| Column | Source |
|---|---|
| Model, kind, state (active, idle, stale) | Registry and residency manager |
| Requests, errors | Usage events |
| Latency p50 and p95, time to first byte for streaming | Usage events |
| Last used | Usage events |
| Top keys by name (with request counts) | Usage events with name snapshots |
| Memory | Residency manager |

Clicking a row opens the recent requests for that model with key name, status and latency.

---

## 3. Endpoints (new or changed)

| Endpoint | Status | Purpose |
|---|---|---|
| `GET /v1/models?task=&kind=&installed=&available=` | Changed (filters) | Registry query used by every picker, docs and snippets |
| `GET /v1/admin/models` | New (admin) | Full registry including unclassified, unavailable and the reasons |
| `PATCH /v1/admin/models/{id}/classification` | New (admin) | Manual override of `task` and `kind` |
| `POST /v1/admin/models/rescan` | New (admin) | Re-read metadata and re-run availability checks |
| `GET /v1/voices?engine=` | Changed | Voices belong to an engine |
| `GET /v1/admin/residency` | New (admin) | Models in memory with state, last used, memory |
| `POST /v1/admin/residency/{model}/flush` and `POST /v1/admin/residency/flush-idle` | New (admin) | Manual flush |
| `GET/PUT /v1/admin/settings/residency` | New (admin) | Idle and stale thresholds, auto-flush time, protected models, on/off |
| `GET /v1/admin/stream/overview` (server-sent events) | New (admin) | Live memory, models, toasts, queue |
| `GET /v1/admin/usage/requests` | Changed | Actor labels, name snapshots, filters by key, profile and actor type |
| `GET /v1/admin/usage/timeseries` | Changed | Zero-filled buckets, explicit time zone |
| `GET /v1/admin/usage/models` | New (admin) | Model activity table and drill-down |

---

## 4. Phases and Tasks

```
F0 Diagnose ─▶ F1 Registry ─▶ F2 TTS availability ─▶ F3 Pickers, snippets, Try it
                                                   ─▶ F4 Attribution ─▶ F5 Overview data ─▶ F6 Residency ─▶ F7 QA
```

### F0: Diagnose (DIAG, read-only, before any fix)

Write `docs/diagnosis-fixes.md` with evidence (file and line, request logs, responses) for each item:

1. **A1:** where does the Streaming list come from? Why exactly two entries? What metadata does a newly installed streaming model have (show the files and any manifest)? Where does "Sherpa ONNX" come from?
2. **A2:** what do local-tts `/v1/models` and `/v1/voices` return, and which engines have real weights? Which entries are hardcoded?
3. **A3:** which endpoint fills the snippet model dropdown?
4. **A4:** reproduce "Try it" 502 for LLM, STT and TTS, with a correct model and with a wrong-type model. Record the request the gateway sent upstream, the upstream response, the auth used and the log lines.
5. **A5:** which code paths bypass key attribution (admin session, UI, voice pipeline), and why the column is null?
6. **A6:** call the time-series endpoint directly and record the response. Is the rollup job running? Is the chart receiving the shape it expects? Time zone?
7. **A7:** which LocalAI calls report loaded models and which unload one; does the installed LocalAI version have idle-unload settings; how can local-tts and the streaming STT service unload; list every hardcoded value in the Warm Engine and Memory panels.

Exit: ORCH confirms the real causes before F1.

### F1: Registry and classification
Registry service and schema, manifest format, install-script manifest writing, backfill script, overrides, refresh triggers, `GET /v1/models` filters, admin models page with an Unclassified group, remove every hardcoded model list (frontend and backend).

### F2: TTS availability
Availability probe and cache, engine and voice linkage, cascading picker (engine then voice), unavailable engines shown only to admins with a reason.

### F3: Pickers, snippets and Try it
Replace all dropdowns with registry queries, per-endpoint type rules in the snippet page, fix the 502 root cause, correct error mapping, add Try it tests per endpoint.

### F4: Attribution and request log
Actor model, name snapshots, labels, backfill of old rows, filters (key, profile, actor type), UI updates.

### F5: Overview data and graph
Fix the data path, zero-filled buckets, empty state, time zone handling, seeded-data tests.

### F6: Residency manager and live panels
Tracker, state rules, manual and automatic flush, locks and cooldown, per-engine unload adapters, settings, live event stream, toasts naming the model, memory and Warm Engine panels from measured data, model activity table with drill-down.

### F7: QA, regression and rollout
Full test run, hardcode scan, load and soak with flushing enabled, REV review of every branch, rollout with the auto-flush switch **off** at first, then on after a trial day.

| Task | Owner | Depends on |
|---|---|---|
| Diagnosis of A1 to A7 | DIAG | none |
| Registry, manifests, overrides, rescan | BE | F0 |
| Backfill script for existing streaming models | OPS | manifest format |
| Remove hardcoded lists, switch pickers to the registry | FE | registry |
| TTS availability probe, engine and voice linkage | BE | F0 |
| Cascading TTS picker | FE | linkage |
| Snippet dropdown rules and Try it fix | BE + FE | registry, F0 finding |
| Actor attribution, snapshots, backfill | BE | F0 |
| Request log filters and labels | FE | attribution |
| Timeseries fix and zero-fill | BE | F0 |
| Overview charts and empty state | FE | timeseries |
| Residency tracker, flush logic, locks | BE | F0 (engine unload findings) |
| Engine unload adapters (LocalAI, local-tts, streaming STT) | OPS + BE | F0 |
| Live stream, toasts, memory and Warm Engine panels, model activity | FE | residency API |
| Tests and hardcode scan | QA | all above |
| Review of every branch | REV | each branch |

Roster: ORCH (board, merges, acceptance), DIAG, BE, FE, OPS, QA, REV (diff only, fresh context).

---

## 5. Acceptance Checklist

**Classification (A1 to A3)**
- [ ] Install a new streaming model: it appears in the STT Streaming list without a restart, with correct name, family and size.
- [ ] "Sherpa ONNX" or any runtime name is not listed as a model.
- [ ] A model with missing metadata appears under Unclassified, not in any picker, and can be classified by an admin.
- [ ] TTS shows only engines that synthesize successfully; each voice shows its engine; dummy entries are hidden from non-admins.
- [ ] Snippet and docs dropdowns show only models valid for the chosen endpoint.

**Try it (A4)**
- [ ] "Try it" succeeds for LLM, STT and TTS with valid input.
- [ ] A wrong-type or missing model returns a clear 4xx message; 502 appears only when the upstream is really down.

**Request log (A5)**
- [ ] No row shows "None"; keys show their names and profiles; system traffic has a clear label.
- [ ] Filters by key, profile and actor type return correct rows.
- [ ] Renaming or revoking a key does not change older rows' names.

**Overview (A6 to A8)**
- [ ] Charts show real data for all ranges; empty ranges show a message.
- [ ] Memory and Warm Engine values match what the operating system and engines report.
- [ ] Idle (5+ min) and stale (7+ min) models are listed with their last-used time.
- [ ] Manual flush unloads a model and memory use drops accordingly.
- [ ] With auto-flush on, a model unused for the configured time is unloaded; a toast names it; Keep loaded cancels the flush.
- [ ] A model with a request in flight, or a protected model, is never flushed.
- [ ] Model activity shows key names and latency; its numbers match the request log.

**General**
- [ ] No hardcoded model, voice or memory values remain (scan passes).
- [ ] Existing flows (voice assistant, agents, batch STT) still work after the changes.

---

## 6. Key Tests

- Registry fixtures: LLM, batch STT, three streaming STT (one with no manifest), one real TTS engine, one dummy engine, one runtime-only entry. Verify each picker query.
- "Install then appear" test for a streaming model, with no restart.
- Try it per endpoint, correct and wrong model types.
- Attribution test for each actor type, plus a renamed and a revoked key.
- Overview endpoint with seeded events for each range, including empty buckets and a different time zone.
- Residency with a fake clock: idle at 5 min, stale at 7, auto-flush at 10, in-flight protection, protected models, request arriving during a flush, cooldown, flush failure.
- Memory panel with mocked monitors for measured and estimated values.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| A flush races with a new request | Per-model lock; the request waits and reloads |
| Flushing on 8 GB causes repeated cold starts | Protect the active agent's models, cooldown, auto-flush off during the first trial |
| An engine cannot unload a single model | Document it in F0; use a guarded service restart as the only fallback |
| File-inferred classification is wrong | Marked medium confidence, shown for review, admin override wins |
| LocalAI unload API behaves differently than assumed | Verify in F0 before writing the adapter |
| Live stream adds load | Update every 2 to 5 seconds, only to open admin sessions |

---

## 8. Defaults to Confirm

- Idle after 5 minutes, stale after 7 minutes, auto-flush after 10 minutes, check every minute.
- The active agent's models are protected from auto-flush (so the voice assistant stays warm).
- Auto-flush starts **off** and is switched on after a trial.
- Toasts show a 2-minute warning with Keep loaded and Flush now.

---

## 9. Starting Prompts

**ORCH:**
> You are the orchestrator for the model classification and dashboard fixes. Read `AGENTS.md` and `model-classification-and-dashboard-fixes-plan.md`. Create worktrees and `docs/board.md`. Start each agent in its own herdr pane with the model pinned and confirmed in the status line. Run F0 first and do not start fixes until `docs/diagnosis-fixes.md` exists.

**DIAG:**
> Read-only. Answer each F0 item with evidence: file and line, the request sent, the response received, and log lines. Do not change code. Write `docs/diagnosis-fixes.md`.

**Builder (BE, FE, OPS):**
> Read only your task and the files it lists. Write a failing test first. Never hardcode model, voice or memory values. Classify from metadata and never guess silently. Never log transcripts, audio or full keys. Paste test output into `docs/handoff/<id>.md`.

**REV:**
> You see only the diff, the task and the acceptance checklist. Try to break it: a model with no metadata, a model installed during a refresh, a flush during a request, a renamed key, an empty time range, a wrong-type model in Try it, an engine that cannot unload.
.

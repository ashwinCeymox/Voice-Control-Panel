# Cloud Migration Plan: Phase 1 (internal callers, raw LLM / STT / TTS)

**Agents:** Gemini 3.8 Flash (high), one herdr pane each, model pinned and confirmed in the status line before any task is sent.
**Based on:** `cloud-readiness-answers.md` plus the owner answers below.

---

## 1. Decisions

| Item | Decision |
|---|---|
| Callers | Your own projects first. The other project already runs on a server and this service moves there. Design keys per caller from day one so outside customers can be added later |
| Offered in Phase 1 | Raw endpoints only: chat completions (streaming), audio transcriptions, text-to-speech |
| Not offered in Phase 1 | Voice pipeline (`/v1/voice/*`), saved agents, model install and delete, system resources. These stay admin-only |
| Peak load | 3 to 5 simultaneous voice sessions, treated as up to 5 simultaneous requests to each of STT, LLM and TTS |
| Network | Private network or VPN between the other project and this service. Public internet only if unavoidable, then HTTPS plus an IP allowlist |
| Public API | Your FastAPI gateway. LocalAI and local-tts stay private behind it |

**Still needed from the owner (defaults used until answered):**
- Budget and hosting provider/region. Default: compare two hourly rentals, pick the cheaper one that passes the tests.
- Latency target. Default: first audio at most 2.5 s at 3 sessions (adjust after Phase 0).
- Transcript and audio retention. Default: **not logged, not stored**.
- Is the other project on the same machine or the same private network? Default: same private network.

---

## 2. Target Architecture

```
Other project(s) ──private network──▶ Gateway (FastAPI)
                                       ├ key check, role, scopes
                                       ├ per-key and global concurrency limits + queue
                                       ├ usage log (counts only, no content)
                                       ├─▶ LocalAI   (LLM + STT)    private
                                       └─▶ TTS (Kokoro ONNX/PyTorch) private
Admin UI ──VPN/IP allowlist──▶ Gateway (admin role only: models, gallery, agents)
```

---

## 3. Phases

```
P0 Re-measure ─▶ P1 Harden ─▶ P2 Keys & limits ─▶ P3 Request model rules ─▶ P4 Portability ─▶ P5 Linux test ─▶ P6 Go-live
                                                                                              └─▶ P7 STT streaming decision (conditional)
```

### P0: Re-measure on the Mac (fixes untrusted numbers)

The report's concurrency and latency numbers contradict themselves (see review). Redo Section C with raw data.

- Save **raw per-request timings** as CSV/JSON (one row per request), not only medians. No round or hand-entered values.
- For C-04, log timestamps for each stage inside a single turn: speech end, STT done, LLM first token, first clause, TTS first audio. The stages must add up to the total.
- Run STT with 1, 2, 5, 10 simultaneous requests and show whether requests overlap or queue (start and end time of each).
- Run the same for LLM and TTS.
- **Overhead check:** run whisper.cpp directly (Metal) on the same clip and compare with the LocalAI call. If direct is much faster, LocalAI per-request overhead is the problem.
- Compare `whisper-tiny` and `whisper-base` again. Near-equal times suggest fixed overhead.
- Check LocalAI logs for GPU offload of the LLM and Whisper, and record tokens per second. 14 tok/s for a 1B model is low.
- Record swap and memory pressure during every run.
- Mark any value that was estimated as `ESTIMATED`.

Exit: raw data committed, stage times add up, cause of slow STT and LLM identified or listed as unknown.

### P1: Harden (nothing exposed before this is done)

| ID | Task | Source |
|---|---|---|
| H1 | Remove the default dev key. Startup fails if no real admin key is configured | B-07 |
| H2 | Restrict CORS to listed origins; no wildcard with credentials | B-12 |
| H3 | Sanitize errors: clients get a code and a short message, never upstream bodies, paths or URLs; details only in server logs | B-24 |
| H4 | Do not log transcripts or responses by default. Add a debug flag that is off in production | B-13, D-08 |
| H5 | Enable SQLite WAL and a busy timeout; test concurrent writes | B-17 |
| H6 | Make deletion safe: only inside the models directory, and skip any weight file still referenced by another config | B-04 |
| H7 | STT fallback to other models is off by default; if enabled, the response says which model was used | B-03 |
| H8 | Move hardcoded paths and model defaults to environment variables (no user-specific paths) | B-14, B-02 |
| H9 | Enforce audio duration and TTS text length limits | B-09 |
| H10 | Per-endpoint timeouts (shorter than the current 120 s for raw endpoints) | B-08 |
| H11 | Remove or protect `/docs`, `/redoc`, `/openapi.json` outside the private network | D-02 |

### P2: Keys, roles and limits

- **Tables:** `api_keys` (id, name, hash, role `caller|admin`, scopes, rate limit, max concurrent, expires_at, revoked_at, created_at) and `usage` (key id, endpoint, timestamp, duration, tokens or audio seconds, status). No request content.
- **Key handling:** shown once at creation, only the hash is stored, revocation takes effect immediately.
- **Scopes:** `llm`, `stt`, `tts`. Caller keys can reach only inference endpoints. Install, delete, agents, system and key management require `admin`.
- **Accept headers:** `Authorization: Bearer` and `xi-api-key`.
- **Limits:** a global semaphore per backend (STT, LLM, TTS), a per-key concurrent cap, a queue with a timeout, and HTTP `429` (per-key) or `503` with `Retry-After` (global). Values come from P5.
- **Admin endpoints:** create, list and revoke keys; usage summary per key.
- **Browser UI tickets** (in-memory today): keep for the UI only, run a single worker, or move to the database.

### P3: Request rules for callers

- Callers pass a model name; the gateway allows only an **allowlist** of served models.
- Caller routes never use the global active agent and never trigger model loading or switching (a switch costs 8 to 14 seconds and affects everyone). Switching is admin-only.
- Pre-warm the allowlisted models at startup, from configuration rather than hardcoded names.
- Put STT behind a small `STTProvider` interface in the gateway (`transcribe(audio)` and `stream(chunks)`), with LocalAI as the first implementation. This makes a later change of STT engine (P7) a contained change.

### P4: Portability

- Docker Compose with four services: `gateway`, `localai`, `tts`, and optionally the admin UI. LocalAI and TTS publish no ports.
- Volumes for models and the database; logs to stdout.
- LocalAI: Linux CPU or CUDA image, pinned version.
- TTS: replace the MLX build with a Linux-capable Kokoro (ONNX or PyTorch). Keep the same HTTP and WebSocket routes so the gateway client does not change. **Spike first:** voice quality, time to first audio, memory.
- Internal key between gateway and LocalAI.

### P5: Linux test (before choosing a server)

Rent two options by the hour and run the same P0 benchmark script on both:

- **A:** CPU only, about 8 vCPU and 16 GB RAM
- **B:** one GPU with 16 to 24 GB VRAM

Proposed pass criteria at 5 simultaneous requests (adjust to the owner's latency target):

| Endpoint | Criterion |
|---|---|
| STT (5 s clip) | all succeed, median at most 1.5 s |
| LLM | first token median at most 1.5 s, no errors |
| TTS | first audio median at most 0.8 s |
| Full chain at 3 sessions | first audio median at most 2.5 s |
| 30-turn soak | no memory growth, no errors |

Pick the cheaper option that passes. Set the limits from P2 using these results.

### P6: Go-live

- Private network or VPN; firewall closed otherwise.
- Prometheus metrics, health checks and alerts on 5xx, queue length and memory.
- Backups of the key and usage database; a list of installed models.
- Pilot with one project, then move the others.
- Review logs for accidental content before opening further.

### P7: STT streaming decision (conditional, after P0 and P5 data)

**Why it is not in Phase 1.** The raw STT endpoint receives a complete audio file. LocalAI's whisper backend does not stream partial text: in the readiness tests, `stream=true` returned everything in one burst after processing (C-12), and `verbose_json` returned only `text` and `duration` (C-11).

**Decision rule**

1. If P0 shows that calling whisper.cpp directly is much faster than the same clip through LocalAI (for example less than half the time), fix or bypass that overhead first. Re-measure before any streaming work.
2. If, after that, STT is still the largest stage after the silence wait at 3 sessions, run the spike below.
3. If a caller needs live partial text, run the spike regardless.
4. Otherwise leave STT as batch.

**Spike options (same clips, same concurrency as P5)**

| ID | Option | What it tests |
|---|---|---|
| S1 | Speculative transcription: start STT after about 400 ms of silence, cancel if speech resumes | Milliseconds saved versus wasted STT calls (matters at 5 sessions) |
| S2 | Rolling-window partials for display only | First-partial delay and the extra STT load it creates |
| S3 | A streaming-capable backend through LocalAI (NeMo-Speech.cpp or llama-cpp with an ASR model such as Qwen3-ASR) | Availability on Linux, memory, accuracy on your clips |
| S4 | A dedicated whisper.cpp or faster-whisper service behind `STTProvider` | Latency without LocalAI overhead, and what it costs to maintain |

**Compare on:** time to first partial, delay of the final transcript after speech end, word accuracy against the batch result, memory, throughput at 5 concurrent requests, error rate.

**Output:** `docs/decisions/stt-streaming.md` with the winner, the losers and why, and the effort to ship it. The voice pipeline is deferred in Phase 1, so S1 and S2 only apply when that pipeline is offered externally.

---

## 4. Roster and Task Board

| ID | Role | Owns |
|---|---|---|
| ORCH | Orchestrator | Board, decisions, merges, acceptance |
| BE-SEC | Backend | H1 to H11 |
| BE-KEYS | Backend | P2, P3 |
| OPS | DevOps | P4, P5, P6 |
| QA | Tester | P0 measurements, P5 tests, security tests |
| REV | Reviewer (diff only, fresh context) | Every branch |

| Task | Owner | Depends on |
|---|---|---|
| P0 re-measure | QA | none |
| H1 to H11 | BE-SEC | none (start in parallel with P0) |
| Key tables, auth, roles, scopes | BE-KEYS | H1 |
| Limits and queue | BE-KEYS | P0 results for starting values |
| Model allowlist and caller route rules | BE-KEYS | H8 |
| Docker Compose, env config | OPS | H8 |
| Linux TTS spike | OPS | none |
| Linux benchmark (A and B) | QA + OPS | P0 script, P4 |
| Monitoring, backups | OPS | P4 |
| `STTProvider` interface | BE-KEYS | H8 |
| STT streaming spike (S1 to S4), only if the P7 rule triggers | QA + OPS | P0, P5 |
| Review of every branch | REV | each branch |

Rules: failing test first, handoff in `docs/handoff/<id>.md`, only ORCH merges, no secrets in logs or handoffs.

---

## 5. Acceptance Checklist

- [ ] No default or development key can authenticate.
- [ ] A caller key cannot call install, delete, agents, system or key management.
- [ ] A revoked key fails immediately.
- [ ] Errors and logs contain no upstream details, paths, transcripts or secrets.
- [ ] 6 simultaneous requests to one endpoint: 5 run within limits and the rest queue or receive `429`/`503` with `Retry-After`; no crashes.
- [ ] Caller requests never change the active agent or load models.
- [ ] Deleting a model never removes a weight file another model still uses.
- [ ] SQLite handles concurrent writes without lock errors.
- [ ] Gateway starts from environment variables only, with no user-specific paths.
- [ ] Linux benchmark meets the P5 criteria on the chosen server.
- [ ] LocalAI and TTS are unreachable from outside the private network.
- [ ] Usage per key is visible to the admin.
- [ ] STT streaming decision recorded in `docs/decisions/stt-streaming.md` (spike run, or reason it was skipped).

---

## 6. Deferred (not in Phase 1)

External voice pipeline and agents, server-side VAD for non-browser clients, billing and quotas, speaker verification, multi-replica scaling (needs shared ticket and limit storage), and outside customers.

---

## 7. Starting Prompts

**ORCH:**
> You are the orchestrator for the cloud migration, Phase 1. Read `AGENTS.md`, `cloud-readiness-answers.md` and `cloud-migration-plan.md`. Create worktrees and `docs/board.md`. Start each agent in its own herdr pane with the model pinned and confirm it in the status line before sending tasks. Start P0 and H1 to H11 in parallel. Do not start P2 limit values until P0 raw data exists.

**QA (P0):**
> Redo Section C with raw per-request timing files. Stage times in a turn must add up to the total. Mark any estimated value ESTIMATED. Run the direct whisper.cpp comparison and the GPU offload and swap checks. Write results to `docs/p0-measurements.md` with the raw files.

**Builder (BE-SEC, BE-KEYS, OPS):**
> Read only your task in `docs/board.md`. Write a failing test first. Implement in your owned files. Paste test output into `docs/handoff/<id>.md`. Never log or return secrets, transcripts or upstream error bodies.

**REV:**
> You see only the diff, the task and the acceptance checklist. Try to break it: missing key, revoked key, caller key on admin route, queue overflow, concurrent writes, deletion of shared weights, error responses with upstream text. Return pass or fail with required changes.

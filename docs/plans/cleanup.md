# Plan: Safe Cleanup Mechanism for Temporary Junk Files

**Goal:** A safe, auditable way to find and delete temporary files created during testing (by the working agents, test runs, debug scripts, playground testing and the engines), on the dev machine and on the Hetzner server, without ever touching models, databases, secrets, backups you still need, or work that has not been merged. **Extended scope (section 9):** also remove the other common causes of crashes (full disks, leaked slots and tasks, orphaned engine processes, unbounded logs and databases) so the system stays up.
**Agents:** Gemini 3.8 Flash (high), one herdr pane each, model pinned and confirmed in the status line before any task is sent.

**Why this needs care:** a deletion tool is itself a risk. A wrong pattern could remove model weights, the live database or an agent's unmerged work. The design therefore starts from **"delete nothing unless it matches an explicit rule and passes every safety check"**, not "delete everything that looks temporary".

**Assumption to confirm:** "junk created by the model during testing" means temporary artifacts from test runs, agent scratch work, debug dumps and engine temp files. Phase C0 builds the real list from evidence, and you approve it before anything is deleted.

---

## 1. Non-Negotiable Safety Rules

1. **Dry run is the default.** Nothing is deleted without an explicit apply step.
2. **Two steps:** `scan` writes a plan file listing exactly what would be removed and why. `apply` accepts only that plan, and re-checks every item at apply time (the file may have changed since the scan).
3. **Quarantine first.** Files are moved to a `.trash/<run-id>/` folder (same relative path preserved) and purged only after a retention period (default 7 days). `restore` puts them back. Only pure regenerable caches (category A) may be deleted immediately.
4. **Allowlisted roots only.** A path is eligible only if its **real path** (after resolving symlinks) is inside a configured root. Symlinks are never followed, and a symlink is removed as a link, never its target.
5. **Protected list always wins** (section 3). A path matching a protected rule is never touched, even if a deletion rule also matches it.
6. **Skip anything tracked by git, anything open by a running process, anything modified recently** (default: less than 2 hours old), and anything on a different filesystem or mount point.
7. **Size and count limits per run.** If a plan exceeds the limit (for example 2 GB or 500 files) it is refused unless the owner passes an explicit override. A plan that is much larger than the previous run's is flagged.
8. **Never run as root.** The tool refuses to start as root. On the server it runs as `voiceagent` with a narrow set of writable paths.
9. **No `rm -rf` on directories chosen by pattern.** Directories are removed only when empty or when listed by an explicit rule, and git worktrees are removed only with `git worktree remove` (section 3, category G).
10. **Everything is logged:** a JSON manifest per run (path, size, category, rule, action, result), kept in `data/cleanup/`.
11. **Fixtures and evidence are protected** (section 3).

---

## 2. What Counts as Junk (categories)

| Cat. | Examples | Default action | Notes |
|---|---|---|---|
| **A. Regenerable caches** | `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.coverage`, `htmlcov/`, `frontend/.vite/`, `node_modules/.cache/` | Delete directly (no quarantine) | Safe to rebuild. Never delete `node_modules` or `.venv` themselves |
| **B. Agent and test scratch** | `tmp/agent/**`, `scratch/**`, `*.tmp`, `*.bak`, `*.orig`, `*.rej`, `nohup.out`, replay and benchmark outputs under `tests/output/**` and `benchmark/raw/**`, probe script output, test DB copies (`tmp/*.db`, `tests/tmp/*.db`) | Quarantine after 24 h | Raw benchmark data referenced by a report in `docs/` is protected (see section 3) |
| **C. Debug dumps that may contain voice or text** | `data/debug/**`, audio dumps (`*.pcm`, `*.wav`) outside `tests/fixtures`, transcript dumps | Quarantine after 2 h; purge after 24 h | **Privacy-sensitive.** Keep this window short. Dumps should be off by default (section 5) |
| **D. Engine temp files and partial downloads** | `*.partial`, `*.download`, `*.tmp` inside model folders, stale install-job folders, LocalAI upload and generated-content folders (locations to be discovered in C0), ffmpeg and whisper temp files | Quarantine after 24 h, only if no install job is active | Never delete a completed weight file. Orphan weights (no config or manifest references them) are **reported only**, never auto-deleted |
| **E. Test databases and old backups** | `data/test-*.db`, temp DB copies; backup files in `data/backups/` older than the retention period | Quarantine / retention rule | Always keep the newest N backups (default 3). Never touch the live database or its `-wal` and `-shm` files |
| **F. Logs** | Rotated logs older than the retention period (default 14 days) | Quarantine | Active logs are skipped; use log rotation for those |
| **G. Agent git worktrees and branches** | `../wt-*` worktrees created by the agents | Report; remove only via `git worktree remove` | Only if the worktree is clean **and** its branch is merged. Dirty or unmerged worktrees are protected and listed for the owner |
| **H. Deployment staging (server)** | `/tmp/voice-agent`, `/tmp/fe` left by the rsync deploy steps | Delete after a successful deploy | Add the deletion to the deploy script as the last step |

---

## 3. Protected List (never deleted)

- `.git/`, `.env*`, `*.pem`, `*.key`, secrets of any kind
- The live database and its companions: `voice_platform.db`, `*.db-wal`, `*.db-shm`
- `models/**` and any file loaded by an engine, except category D partials. LocalAI and streaming STT model folders are never deleted by pattern
- `app/`, `frontend/src/`, `scripts/`, `services/`, `policies/`, `tests/` (except `tests/output/**`), `tests/fixtures/**`
- `docs/**` (including `docs/handoff/`, `docs/decisions/`, `docs/evidence/`), `AGENTS.md`, plan files
- The newest N backups and anything referenced by `data/backups` retention
- Any file tracked by git
- Any file currently open by a process (checked at scan and again at apply)
- Anything outside the configured roots, or whose real path resolves outside them

The protected list lives in the policy file and is covered by tests that fail if a protected sample is ever selected.

---

## 4. Design

### 4.1 Components

| Component | Purpose |
|---|---|
| `scripts/cleanup.py` (Python standard library only) | The tool: `snapshot`, `diff`, `scan`, `apply`, `restore`, `purge`, `report` |
| `policies/cleanup.dev.toml` | Dev machine roots, categories, protected list, thresholds |
| `policies/cleanup.server.toml` | Server roots and a smaller set of categories (A, C, D partials, E retention, F, H) |
| `data/cleanup/` | Run manifests and plan files |
| `.trash/` (per root, gitignored) | Quarantine |
| systemd `voice-cleanup.service` and `.timer` (server) | Scheduled run |
| Optional: admin storage panel | Disk use by category, last run, a Scan button (admin only) |

### 4.2 Commands

| Command | Behavior |
|---|---|
| `snapshot --out before.json` | Record path, size, modification time and inode for every file under the scan roots (skips `.git`, `node_modules`, `.venv`). Platform neutral (works on macOS and Linux) |
| `diff before.json after.json` | List files created or changed between two snapshots. Used in C0 to find what a test run really leaves behind |
| `scan [--policy P]` | Evaluate rules and safety checks, print a table (path, size, age, category, rule), write `plan-<ts>.json`. **Deletes nothing** |
| `apply --plan plan.json` | Re-validate each item, move to quarantine (or delete category A), write the manifest. Refuses over-limit plans without an override |
| `restore <run-id> [path]` | Move quarantined items back |
| `purge` | Remove quarantine runs older than the retention period |
| `report` | Disk use by category, quarantine size, last runs |

Exit codes: `0` success, `2` plan refused (limits or safety), `3` protected-path violation detected (also logged as an error).

### 4.3 Safety checks (run at scan and again at apply)

1. Resolve the real path and confirm it is inside an allowed root.
2. Reject symlinks that point outside the root; never follow symlinks.
3. Match against the protected list first.
4. Check the file is not tracked by git (`git ls-files` for the repository root).
5. Check it is not open by a process (`lsof` where available, or `fuser` on Linux).
6. Check age against the rule's minimum.
7. Check size and file-count limits for the whole plan.
8. Confirm same filesystem as the root (no crossing mount points).
9. Re-check that the plan item still matches (same size and modification time as at scan).

### 4.4 Reducing junk at the source (so cleanup has less to do)

- **Agent rule in `AGENTS.md`:** all scratch files go in `tmp/agent/<task-id>/` (gitignored); never write scratch files to the repository root; delete your scratch folder in your handoff step.
- **Tests:** use pytest `tmp_path` and `tempfile` with automatic cleanup. A test fixture fails the run if it leaves files outside its temp folder (checked by a before and after snapshot in CI or a pytest plugin).
- **App temp files:** use one prefix (for example `vagent-`) and one folder (`data/tmp/`), so rules match exactly and nothing else.
- **Debug audio and transcript dumps:** behind an environment flag that is **off by default**, written only to `data/debug/`, with a time limit.
- **Optional janitor inside the app:** on startup and hourly, remove files under `data/tmp/` with the `vagent-` prefix that are older than one hour. Nothing outside that folder.
- **Deploy script:** the last step removes `/tmp/voice-agent` and `/tmp/fe` after a successful deploy.

### 4.5 Server integration

`/etc/systemd/system/voice-cleanup.service`
```ini
[Unit]
Description=Voice platform cleanup of temporary files

[Service]
Type=oneshot
User=voiceagent
Group=voiceagent
WorkingDirectory=/opt/voice-agent
ExecStart=/opt/voice-agent/.venv/bin/python scripts/cleanup.py run --policy policies/cleanup.server.toml
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/opt/voice-agent/data /opt/voice-agent/logs /opt/voice-agent/models
```

`/etc/systemd/system/voice-cleanup.timer`
```ini
[Unit]
Description=Daily cleanup of temporary files

[Timer]
OnCalendar=*-*-* 04:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

On the server, `run` means scan, apply to quarantine under the limits, then purge expired quarantine. It never deletes weights, the database or backups newer than the retention rule. If a plan exceeds the limits, it does nothing and writes an error to the journal so the owner can look.

Note: `PrivateTmp=true` on the service units already gives each service a private `/tmp` that is removed when the service stops, so the timer does not need to clean those.

Existing cron backup deletion (`find ... -mtime +14 -delete`) should be moved into the policy (category E), so there is one place with limits, logging and a "keep the newest 3 and require the off-box copy marker" rule.

---

## 5. Phases and Tasks

```
C0 Inventory ─▶ C1 Policy + tool ─▶ C2 Tests ─▶ C3 Source reduction ─▶ C4 Server integration ─▶ C5 First controlled run ─▶ C6 Optional panel ─▶ C7 to C11 (section 9)
```

### C0: Inventory (read-only, evidence first)
1. Run `snapshot` before and after a full test run, a replay-script run, a benchmark run and one agent working session. `diff` shows exactly what gets created.
2. Find where each engine writes temporary data: LocalAI (upload, generated-content and temp locations; check its configuration and `lsof` during a request), local-tts, the streaming STT service, ffmpeg conversions.
3. List existing junk on disk today, grouped by the categories in section 2, with sizes.
4. List git worktrees and their state (clean, dirty, merged).
5. List what the current deploy steps leave on the server.
6. Output `docs/cleanup-inventory.md`. **The owner approves the categories and retention numbers before C1 starts.**

### C1: Policy and tool
Build `scripts/cleanup.py` and the two policy files exactly as in section 4, with the protected list from section 3.

### C2: Tests (written first)
- Every protected sample (database, `.env`, a weight file, a fixture, a doc, a tracked file, an open file) is **never** selected, even when a rule matches it.
- A symlink pointing outside the root is not followed and its target is untouched.
- Path tricks (`..`, absolute paths, odd Unicode and spaces) cannot escape the roots.
- Age rules, size limits and count limits behave as set; an over-limit plan is refused.
- `scan` deletes nothing; `apply` without a plan is refused; a changed file is skipped at apply time.
- Quarantine, `restore` and `purge` round-trip correctly.
- A worktree that is dirty or unmerged is never removed; a clean merged one is removed through `git worktree remove`.
- Running as root is refused.
- A test run that leaves files outside its temp folder is caught.

### C3: Source reduction
`AGENTS.md` rules, `tmp/agent/` and `.gitignore` entries, pytest fixture, `vagent-` prefix and `data/tmp/`, debug dumps behind an off-by-default flag, app janitor, deploy script cleanup step.

### C4: Server integration
Systemd service and timer, the server policy, moving the backup retention into the policy, a journal alert when a plan is refused.

### C5: First controlled run
`scan` on the dev machine, the owner reads the plan, `apply` to quarantine, run the full tests and the app to confirm nothing broke, then purge after the retention period. Repeat on the server in dry-run mode for a few days before enabling apply.

### C6: Optional admin storage panel
Disk use by category, last run, quarantine size, and a Scan button (admin only, scan only; applying stays on the command line at first).

| Task | Owner | Depends on |
|---|---|---|
| Snapshot and diff, inventory report | DIAG | none |
| Policy files and tool | BE | owner approval of C0 |
| Tests | QA | tool design |
| Source reduction (AGENTS.md, fixtures, prefix, flags, janitor, deploy step) | BE + OPS | C0 |
| Systemd timer and server policy | OPS | tool |
| First controlled runs | OPS + owner | tests pass |
| Review of every branch | REV | each branch |

---

## 6. Acceptance Checklist

- [ ] The inventory shows what the tests, agents and engines actually leave behind, with sizes, and the owner approved the categories.
- [ ] `scan` never deletes; `apply` works only from a plan; every deletion is quarantined and can be restored.
- [ ] No protected path was ever selected in tests, including with symlinks and path tricks.
- [ ] Running as root is refused; the server unit has only the writable paths it needs.
- [ ] An over-limit or unusually large plan is refused and reported.
- [ ] Dirty or unmerged git worktrees are never removed.
- [ ] Debug audio and transcript dumps are off by default and expire within hours when on.
- [ ] After a full test run, the project has no new files outside `tmp/agent/` and pytest temp folders.
- [ ] The deploy script leaves no `/tmp/voice-agent` or `/tmp/fe`.
- [ ] A cleanup run followed by the full test suite and an app start shows nothing needed was removed.
- [ ] Each run has a manifest of what was moved, why, and the result.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| A rule matches something valuable (weights, database, work in progress) | Protected list wins, quarantine, plan review, tests with real samples, REV |
| Plan is stale when applied | Re-validate at apply; skip changed files |
| Deleting a file an engine is writing | Open-file check; minimum age; no partial removal while an install job is active |
| Symlink or path trick | Real-path check, no symlink following, tests |
| Scheduled run deletes too much unnoticed | Count and size limits, journal alerts, quarantine, a few days of dry-run first |
| Raw benchmark data needed later is removed | Protected when referenced from `docs/`; evidence folder protected |
| Privacy: voice or transcript dumps linger | Off by default, 2 h quarantine start, 24 h purge |
| Worktree with unmerged agent work removed | Git-based check; clean and merged only |

---

## 8. Decisions for the Owner

- Confirm the categories A to H, especially **D** (engine temp files) and **G** (worktrees).
- Retention numbers (defaults above): scratch 24 h, debug dumps 2 h then purged at 24 h, quarantine 7 days, logs 14 days, backups 14 days and at least 3 kept.
- Scope: dev machine only, server only, or both (default: both, server in dry-run first).
- Whether you want the optional storage panel (C6).

---

## 9. Extended Scope: Playground Junk and Crash Prevention

**Added requirement:** also remove junk created by the models and engines during **playground testing** (STT, TTS, agent chat, voice assistant), and remove the other common causes of instability so the system keeps running.

**An honest note:** no cleanup tool can promise a crash-free system. This section targets the most common causes of crashes in a setup like yours: a full disk, leaked request slots, tasks and sockets, orphaned engine processes holding GPU memory, unbounded logs and databases, and memory pressure. A 24-hour soak test (9.7) measures whether it worked.

### 9.1 Where playground testing creates junk (to be confirmed in C0)

| Source | What it can leave behind | Risk | Handling |
|---|---|---|---|
| STT playground uploads (batch) | Upload spool files in the gateway temp folder; LocalAI temp and converted audio files | Disk and inode growth; orphans after a crash | Gateway temp files only under `data/tmp/vagent-*`, removed in a `finally` block; give each service its own `TMPDIR` under `data/tmp/<service>/` (so the cleanup tool can see it); janitor removes anything older than 1 hour |
| STT streaming playground | Audio buffers (memory), optional debug dumps | Memory growth; sensitive audio on disk | Bounded buffer (already planned); dumps off by default with a 2 h expiry |
| TTS playground and voice previews | Generated audio if any component writes it to disk (for example a "generated content" folder, or a preview cache) | Unbounded growth | Do not persist generated audio by default. If a cache is needed, cap it by size and age. C0 checks every engine for its default output location |
| Agent test chat and chat playground | Session, message and turn rows in SQLite | Database growth | Playground sessions flagged `ephemeral` and pruned after a configurable time (default 24 h) |
| Voice assistant sessions | Turn rows with transcripts; open sockets; background tasks | Leaks and slot exhaustion; stored speech text | Retention rule (9.4); slot reaper (9.2) |
| Model install and delete tests | `.partial` files, orphan manifests, stale job records, `installed_models` rows for files that no longer exist | Wrong lists, wasted disk | Reconcile database and disk; remove stale partials when no install job is active |
| Engine caches (Hugging Face, compiled kernel caches such as the CUDA compute cache, ONNX or MLX caches) | Large cache folders | Disk growth | **Monitor and cap, do not auto-delete.** Set cache size limits where the engine supports them |
| LocalAI backend processes (the separate whisper and llama-cpp processes seen in your port list) | Orphaned processes after a crash or restart, holding GPU memory | VRAM exhaustion, port conflicts | Process hygiene (9.5) |
| Panel in the browser | Object URLs for played audio, unclosed audio contexts, microphone tracks left open, WebSockets not closed | Tab memory growth, microphone left on, leaked server sockets | Browser fixes (9.5) |

### 9.2 In-app janitor ("hygiene loop") in the gateway

Runs every 30 to 60 seconds. Every item it fixes is **logged and counted**, because a rising count means a bug to fix at the source, not something to hide.

- Expire WebSocket tickets past their lifetime and cap how many are held.
- **Slot reaper:** any stream or turn slot held longer than the maximum turn time (default 300 s), or whose WebSocket is already closed, is released and its task cancelled. This prevents the "server busy until restart" failure.
- Remove finished or orphaned entries from the active-task tables; await cancelled tasks so exceptions are not lost.
- Close idle WebSockets (default 5 minutes without audio) with a clear close code.
- Mark install jobs that made no progress for a set time as failed and remove their partial files when no download is active.
- Remove `data/tmp/vagent-*` and service temp folders' files older than 1 hour.
- Prune expired ephemeral playground sessions.
- Report counters: `janitor_reaped_total{reason}`.

### 9.3 Disk guard (prevents the most common crash)

Checked every minute, for free space and free inodes, on the filesystems holding `data/`, `models/` and `logs/`.

| Free space | State | Action |
|---|---|---|
| 20% or more | Normal | None |
| Below 15% | Warning | Alert; report on the Overview |
| Below 10% or below 5 GB | Pressure | Run the safe cleanup categories (A, B, C, D partials, F) and purge quarantine older than 1 day; alert |
| Below 5% or below 2 GB | Critical | Also block model installs, large uploads and debug dumps with a clear error (`DISK_LOW`, HTTP 507 or 503); keep serving normal API calls; alert |

Weights, the live database and backups are **never** auto-deleted at any threshold.

Also:
- `logrotate` for `/opt/voice-agent/logs/*.log` (daily, keep 14, compress, `copytruncate` where the program keeps the file open).
- journald size cap (for example `SystemMaxUse=1G`) in `/etc/systemd/journald.conf.d/`.
- Caddy's own log rolling is already configured (50 MiB, keep 10).

### 9.4 Database hygiene (SQLite)

- WAL mode and a busy timeout enabled; `PRAGMA wal_checkpoint(TRUNCATE)` on a schedule so the WAL file does not grow without limit.
- **Retention:**
  - ephemeral playground sessions: 24 h by default
  - `turns` and `messages` for real sessions: by the retention decision below
  - raw `usage_events`: roll into hourly rows and delete raw rows after 30 to 90 days
  - reconcile `installed_models` rows with files on disk
- `PRAGMA integrity_check` weekly, with an alert on failure.
- `PRAGMA optimize` weekly. `VACUUM` only off-hours, after a fresh backup, when at least 20% of pages are free and free disk space is at least twice the database size.
- Always back up before destructive maintenance.
- **Decision needed:** your data pack says transcripts are stored in the `turns` table, while `LOG_TRANSCRIPTS` is false. For coworkers' calls, I recommend storing metadata only (no transcript text) unless you need it.

### 9.5 Process, service and browser hygiene

**Services (systemd, no Docker)**
- Keep the default `KillMode=control-group` so LocalAI's child backend processes stop with it. Set `TimeoutStopSec`, `Restart=on-failure`, and a start limit (for example `StartLimitIntervalSec=300`, `StartLimitBurst=5`) so a broken unit cannot loop forever.
- Set `MemoryHigh` and `MemoryMax` per engine unit from measured use, plus `LimitNOFILE` and `TasksMax`, so one runaway engine cannot take down co-located services. Give engines a higher `OOMScoreAdjust` than SSH and Caddy so the kernel sacrifices them first.
- **Health watchdog:** a timer runs every minute, checks the gateway and engine health URLs, and restarts a unit only after 3 consecutive failures, with a limit on restarts per hour and an alert. It never restarts during an active model install.
- **GPU check:** compare the processes using the GPU (`nvidia-smi --query-compute-apps=pid,used_memory --format=csv`) with the expected units, and report unknown holders.
- **Stop script on the dev machine:** `stop-native-engines.sh` currently kills whatever listens on the engine ports. Change it to stop only the process IDs recorded at start (a PID file per service), so it can never kill an unrelated program. Do not use port-based killing on the server.

**Panel (browser)**
- Revoke every `URL.createObjectURL` URL when audio finishes or the component unmounts.
- Close the `AudioContext`, disconnect the worklet, stop every `MediaStream` track, and close WebSockets on unmount and on page hide.
- Add an unmount test for the voice page that fails if tracks or sockets remain open.

### 9.6 Hygiene metrics and alerts

Add to `/metrics` (admin and private network only) and show on an admin Overview **System hygiene** panel:

open file descriptors, threads, asyncio tasks, WebSocket connections, active slots by type, tickets held, temp folder size per service, database and WAL size, disk and inode free percentage, resident memory per service, GPU memory, quarantine size, `janitor_reaped_total` by reason, last successful cleanup, and the last disk-guard state.

Alerts: disk guard at Warning or worse; descriptors, tasks or memory trending upward for 30 minutes with no traffic; any slot reaped more than a few times per hour; a service restarted by the watchdog; cleanup plan refused; integrity check failed.

### 9.7 Soak and chaos tests (the proof)

| Test | Pass condition |
|---|---|
| **24-hour soak:** scripted playground traffic (STT uploads, TTS, agent chat, voice sessions at 3 to 5 concurrent) plus random client disconnects, cancelled requests, oversized uploads and install-then-delete cycles | After traffic stops, descriptors, threads, tasks, memory, temp size, database and WAL size, and GPU memory return to within about 10% of the starting baseline; no stuck slots; no unplanned service restarts; free disk steady |
| **Disk-full drill** on a scratch filesystem or a quota | States change as in 9.3, normal API calls keep working in Critical mode, and recovery works after space is freed |
| **Orphan drill:** `kill -9` LocalAI | Child backends are gone or restarted, GPU memory is freed, the service returns to healthy |
| **Restart-loop drill:** break a unit's configuration | The start limit stops the loop and an alert fires |
| **Leak check:** open and close the voice page 50 times | No tracks, sockets or object URLs remain; server slots return to zero |

### 9.8 Added phases and tasks

```
C7 Playground junk map ─▶ C8 Janitor, slot reaper, browser fixes ─▶ C9 Disk guard, logs, database hygiene
                                                                        │
                           C11 Soak and chaos ◀─ C10 Service hygiene, watchdog, metrics, panel
```

| Phase | Work | Owner | Depends on |
|---|---|---|---|
| **C7** | Extend the C0 inventory: run each playground (STT batch and streaming, TTS, agent chat, voice assistant, install and delete) with before and after snapshots; record where each engine writes temp and generated files, descriptor and task counts, and orphan processes after a forced stop | DIAG | C0 |
| **C8** | In-app janitor, slot reaper, idle socket closing, per-service `TMPDIR`, ephemeral playground sessions, browser leak fixes and tests | BE + FE | C7 |
| **C9** | Disk guard with thresholds, logrotate and journald limits, database retention, checkpoint, integrity check, vacuum rules | BE + OPS | C7 |
| **C10** | Unit hardening (memory and task limits, start limits, OOM score), health watchdog timer, PID-file based stop script, GPU holder check, hygiene metrics, Overview panel | OPS + BE + FE | C8, C9 |
| **C11** | The soak and chaos tests in 9.7, with a written result against the baseline | QA | C8 to C10 |
| Review | Every branch | REV | each branch |

### 9.9 Acceptance checklist (extended scope)

- [ ] The inventory lists where every playground and engine leaves temp or generated files, with evidence.
- [ ] Gateway and engine temp files live in known folders and are removed within their time limits.
- [ ] A held slot of a closed or stuck connection is released automatically, and the event is counted.
- [ ] The disk guard moves through its states correctly in the drill and never touches weights, the live database or backups.
- [ ] Logs and the journal have size limits; the database WAL stays bounded; the weekly integrity check runs.
- [ ] Playground sessions expire; `installed_models` matches the disk.
- [ ] A killed LocalAI leaves no orphan backend process holding GPU memory.
- [ ] A broken unit cannot restart forever; the watchdog restarts a failing service only after repeated failures and alerts.
- [ ] The stop script on the dev machine kills only processes it started.
- [ ] The voice page leaves no open microphone tracks, sockets or object URLs after leaving it.
- [ ] The 24-hour soak passes its conditions in 9.7, and the hygiene panel shows flat curves at idle.

### 9.10 Added risks

| Risk | Mitigation |
|---|---|
| The janitor hides a real leak | Every reaped item is counted and alerted when it keeps recurring |
| The watchdog restarts a healthy but slow service | Three consecutive failures, generous timeouts, restart limits, and no restarts during installs |
| Memory limits kill an engine under legitimate load | Set limits from measured use with headroom; start with `MemoryHigh` (throttle) before `MemoryMax` (kill) |
| Emergency cleanup removes something needed | Same protected list and quarantine as normal runs; weights, database and backups excluded |
| VACUUM or checkpoint stalls the service | Off-hours only, after backup, with disk space checks |
| Blocking uploads at Critical disk state surprises callers | Clear `DISK_LOW` error with `Retry-After`; alert you first at Warning |

### 9.11 Decisions needed (defaults shown)

- Retention for ephemeral playground sessions: 24 hours.
- Whether coworkers' transcripts are stored at all (recommended: metadata only).
- Idle WebSocket timeout 5 minutes; maximum turn time 300 seconds.
- Disk thresholds as in 9.3.
- Whether the health watchdog may restart services automatically (default: yes, with limits and alerts).

---

## 10. Starting Prompts

**ORCH:**
> You are the orchestrator for the cleanup mechanism. Read `AGENTS.md` and `cleanup-mechanism-plan.md`. Start each agent in its own herdr pane with the model pinned and confirmed in the status line. Run C0 first. Do not start C1 until the owner has approved `docs/cleanup-inventory.md`.

**Diagnostician (C0):**
> Read-only. Use before and after snapshots to find what test runs, replay scripts, benchmarks and an agent session leave behind. Find where each engine writes temp data. Do not delete anything. Write `docs/cleanup-inventory.md` with evidence (paths, sizes, which process created them).

**Builder:**
> Build the tool and policies from the plan. Write the safety tests first. Delete nothing by default; every deletion goes through scan, a plan file, re-validation and quarantine. Never match anything on the protected list. Paste test output into `docs/handoff/<id>.md`.

**Reviewer (fresh context, diff only):**
> Try to make the tool delete something it should not: a protected file under a matching pattern, a symlink to outside the root, a path with `..`, a file that changed after the scan, a dirty worktree, an open file, a huge plan, a run as root. Confirm none succeed.

**Hygiene builder (C8 to C10):**
> Read `cleanup-mechanism-plan.md` section 9 and your task only. Write a failing test first. Every item the janitor fixes must be logged and counted. Never auto-delete weights, the live database or backups. Never restart a service during a model install. Paste test output into `docs/handoff/<id>.md`.

**Soak tester (C11):**
> Run the soak and chaos tests in section 9.7 against a baseline snapshot. Report descriptors, threads, tasks, memory, temp size, database and WAL size, GPU memory and disk before, during and after, with raw data. Mark anything estimated as ESTIMATED.

# PLAN: Authentication Page + Git Cleanup

Instructions for the implementing agent. **Scope: Phase 3 and Phase 4 only.** Other work (baseline checks, the local-tts crash, documentation corrections) is out of scope here, so do not start it and do not assume it has been done.

Work through the phases in order and do not start a phase until the previous one passes its exit check. Do not mark anything "Confirmed" or "Resolved" unless you have run the code or test that proves it, and paste the command output as evidence in `docs/EVIDENCE.md`.

Stack: FastAPI gateway (`app/`), LocalAI, local-tts (Kokoro MLX), Sherpa-ONNX streaming STT, Vite/React control panel (`frontend/`), SQLite (WAL), Caddy in production.

Environments:
- **macOS (Apple Silicon):** development only.
- **RunPod pod:** internet testing. Linux with an NVIDIA GPU. TLS is terminated by RunPod's HTTP proxy, which forwards plain HTTP to one pod port.
- **Hetzner dedicated server:** production. Caddy terminates TLS for the real domain, services run under systemd, engines listen on loopback only.

RunPod and Hetzner are both reachable from the internet, so **neither may run with development settings.** The same repository must work on all three through configuration, not code changes.

---

## Phase 3: Authentication page, single super admin

### 3.0 Pre-check (read-only, before changing code)

1. Run `.venv/bin/pytest tests/ -q` and `.venv/bin/python scripts/auth_matrix_probe.py`. Record the real results in `docs/EVIDENCE.md`. These are the "before" numbers, so any regression is visible.
2. Find where `ENVIRONMENT` is read (for example `app/core/config.py`). If the setting does not exist, add it with the default `development`.
3. Find every auth dependency in `app/` (`require_api_key`, `require_admin_key`, `get_authenticated_key`, `_scope_checker(...)`, the WebSocket handshake check, and `POST /v1/auth/ticket`) and list every route that uses each. The panel calls many of these routes (model lists, playgrounds, voice, tickets), so **all** of them must be able to accept a panel session, not only the admin routes.
4. Confirm how the frontend currently authenticates admin calls (the admin key in `sessionStorage`) and list every file that reads or writes it.
5. Add `argon2-cffi` to the Python dependencies.
6. Record how the gateway currently learns the client IP and the request scheme (direct `request.client`, forwarded headers, uvicorn proxy options), and how cookies would get the `Secure` flag. Behind a TLS-terminating proxy the app sees plain HTTP.

### 3.1 Requirements

- One login page for the control panel with **one account**, the super admin.
- Seed credentials for development: email `admin@voicepanel.io`, password `adminVOICE3.md`.
- A **change password** form that requires the old password, then the new password twice.
- Everything in the panel is hidden until the user is logged in. API routes under `/v1` keep their API-key auth for external callers.

### 3.2 Decisions made (change them only if the owner says so)

| Topic | Decision |
|---|---|
| Panel session | HttpOnly, SameSite=Strict, Secure (in production) cookie holding a signed session ID. No tokens in localStorage or sessionStorage. |
| Existing API keys | Unchanged. The panel's admin calls authenticate with the session cookie. External callers still use keys and scopes. |
| Password storage | argon2id (`argon2-cffi`) or bcrypt. Never plain text, never reversible. |
| Seeding | On first startup, if the `admin_user` table is empty, create the admin from `ADMIN_EMAIL` and `ADMIN_INITIAL_PASSWORD` in the environment. |
| Workers | Keep `--workers 1`. Sessions are stored in SQLite so they survive restarts. |

### 3.3 Database

New tables, created through the existing migration or init path in `app/storage/database.py`:

```sql
CREATE TABLE IF NOT EXISTS admin_user (
  id INTEGER PRIMARY KEY CHECK (id = 1),      -- enforces a single account
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  must_change_password INTEGER NOT NULL DEFAULT 0,
  failed_attempts INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_session (
  id TEXT PRIMARY KEY,                         -- random, 256-bit
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  user_agent TEXT
);
```

### 3.4 Backend

Files: `app/schemas/auth.py`, `app/services/admin_auth.py`, `app/api/admin_auth.py`. Register the router in `app/main.py`.

| Route | Auth | Behaviour |
|---|---|---|
| `POST /v1/panel/login` | none | Body `{email, password}`. Check the hash in constant time. On success set the session cookie and return `{must_change_password}`. On failure return a generic 401 `Invalid email or password`. |
| `POST /v1/panel/logout` | session | Delete the session row and clear the cookie. |
| `GET /v1/panel/me` | session | Return `{email, must_change_password}`. |
| `POST /v1/panel/change-password` | session | Body `{old_password, new_password}`. Verify the old password first. Then enforce the new-password rules. Store the new hash, clear `must_change_password`, delete all other sessions, and issue a fresh session. |

Rules:

- **Rate limiting:** after 5 failed logins, lock the account for 15 minutes and also throttle by IP. Return the same generic error while locked.
- **New password:** minimum 12 characters, must differ from the old password, maximum 128 characters.
- **Session lifetime:** 8 hours idle, 7 days absolute.
- **CSRF:** SameSite=Strict plus a required `X-Requested-With` header on all POST routes under `/v1/panel`.
- **Logging:** never log passwords, hashes or cookie values.
- **One auth principal for the whole panel:** add a dependency that resolves either an API key or a valid panel session into a single principal. A panel session counts as the **admin role with all scopes**. Every dependency listed in 3.0 item 3 must accept it: `require_api_key`, `_scope_checker(...)`, `require_admin_key`, `get_authenticated_key`, and the WebSocket handshake. `POST /v1/auth/ticket` must also accept a panel session, because the browser can no longer hold an admin key. Do not remove the API-key path: external callers keep using keys and scopes, and API-key requests never gain extra rights.

### 3.5 Production safety (not optional)

The dev seed password `adminVOICE3.md` is known, so on a public server it would repeat the SEC-03 problem (a guessable default).

- Read `ADMIN_EMAIL` and `ADMIN_INITIAL_PASSWORD` from `.env`. Do not hard-code the password in source.
- Put the dev values in `.env.example` only: `ADMIN_EMAIL=admin@voicepanel.io`, `ADMIN_INITIAL_PASSWORD=adminVOICE3.md`. (Phase 4 revisits this if the repository is or may become public.)
- Seed with `must_change_password = 1`, so the first login forces the change-password screen.
- When `ENVIRONMENT` is `production` **or `staging`**, refuse to start if `ADMIN_INITIAL_PASSWORD` is unset, equals `adminVOICE3.md`, or is shorter than 12 characters, and the admin row has not been created yet. Allowed values are `development`, `staging` and `production`; anything else fails startup.
- Keep `.env` and the SQLite file at mode 600.
- **Any environment reachable from the internet, including a RunPod pod, must run as `staging` or `production`, never `development`.** The dev password is public knowledge, so a test pod is seeded with a random initial password of at least 12 characters, and the first login changes it.

### 3.6 Frontend

Files in `frontend/src/`: `pages/Login.tsx`, `pages/ChangePassword.tsx`, `auth/AuthContext.tsx`.

- On load, call `GET /v1/panel/me`. If it returns 401 show the Login page. Otherwise show the app.
- Use relative URLs (`getApiBase()`) and `credentials: "include"`. Do not hard-code `localhost` or `:8000`.
- Login page: email, password, show/hide toggle, error message, and a disabled state while the request runs.
- If `must_change_password` is true, show only the Change Password page until it succeeds.
- Add a user menu with **Change password** and **Log out**.
- Remove the admin-key `sessionStorage` handling from the panel pages. The session cookie replaces it.
- Production build keeps `sourcemap: false`.

### 3.7 Caddy

- Keep `/metrics` and `/docs` blocked.
- Decide whether to remove Caddy `basic_auth` on `/voicecontrolpanel/`. Recommended: keep it for the first deployment as a second layer, and remove it only when the owner agrees. Note the decision in `DEPLOY_HETZNER.md` and `CONTROLPANEL_PROXY.md`.
- Add a Caddy rate limit or fail2ban rule for `POST /v1/panel/login`.
- **RunPod test:** TLS is terminated by RunPod, which forwards plain HTTP to the pod. Run Caddy with `auto_https off` and a catch-all site address (`:PORT`) instead of a domain name, because requests arrive with the `Host` header `<pod-id>-<port>.proxy.runpod.net`, which a domain-specific site block would not match. Keep `basic_auth` on the panel path for the test, since the proxy URL is public to anyone who has it.
- **Hetzner production:** Caddy terminates TLS for the real domain, as in the existing deployment guide.

### 3.8 Tests (`tests/unit/test_admin_auth.py`)

- Seed creates exactly one admin and refuses a second.
- Login succeeds with the correct credentials and fails with a generic error otherwise (wrong email and wrong password look identical).
- Lockout after 5 failures, and it expires.
- Change password: rejects a wrong old password, rejects a weak or identical new password, accepts a valid one, and invalidates the old sessions.
- Old password no longer works after a change, and the new one does.
- Production startup fails with the default or missing initial password.
- A protected admin route returns 401 without a session and 200 with one. An API key still works on it.
- Cookie flags: HttpOnly, SameSite=Strict, Secure in production.
- The panel session works on every route the panel uses (model lists, system resources, playground endpoints, voice turn, ticket creation) and on the WebSocket handshake. An API key keeps working exactly as before. A request with neither gets 401.
- Proxy awareness (see 3.9): the `Secure` cookie flag follows configuration behind a TLS-terminating proxy; login throttling uses the forwarded client address only when the direct peer is a trusted proxy; a spoofed forwarded header from an untrusted peer is ignored.
- Add the new routes to `scripts/auth_matrix_probe.py`.

### 3.9 Deployment profiles and proxy awareness (RunPod test, Hetzner production)

Configuration, documented in `.env.example` and read in `app/core/config.py`:

| Variable | Default | Purpose |
|---|---|---|
| `ENVIRONMENT` | `development` | `development`, `staging` or `production` (see 3.5) |
| `GATEWAY_HOST` | `127.0.0.1` | Bind address of the gateway. RunPod test: `0.0.0.0` on the **one** exposed port. Hetzner: stays `127.0.0.1` behind Caddy |
| `GATEWAY_PORT` | `8000` | Gateway port |
| `COOKIE_SECURE` | true unless `development` | Decided by configuration, **not** by the request scheme, because TLS ends at the proxy and the app sees plain HTTP |
| `TRUSTED_PROXY_IPS` | empty | Addresses allowed to set forwarded headers. The start script also passes them to uvicorn (`--proxy-headers --forwarded-allow-ips`) |

Rules:

1. Login throttling by IP uses the forwarded client address **only** when the direct peer is in `TRUSTED_PROXY_IPS`; otherwise it uses the direct peer. Without this, every user behind a proxy shares one address (one bad actor locks everyone out), or the header can be spoofed. Until the address the gateway sees through the RunPod proxy is known, leave `TRUSTED_PROXY_IPS` empty and accept a shared throttle for the test.
2. The engines (LocalAI 8080, local-tts 8880, Sherpa 8088) stay on `127.0.0.1` in every environment. Only one gateway or Caddy port is ever bound to `0.0.0.0`, and only in the RunPod test.
3. Long-lived connections: the voice WebSocket sends a ping about every 25 seconds and the client reconnects with backoff. RunPod's HTTP proxy closes requests that have not started responding within 100 seconds (a `524`), and RunPod's documentation suggests direct TCP may suit persistent connections better. The long-session test in 4.10 verifies how WebSockets behave.
4. No code path may assume `localhost`, port 8000 or `http://` in the browser. The panel already uses relative URLs and derives `wss://` from the page.

### 3.10 Exit check

1. All tests pass, including the new ones, with real output pasted into `docs/EVIDENCE.md` next to the "before" numbers from 3.0.
2. Manual check in a browser: unauthenticated visit shows Login, login works, a refresh stays logged in, change password works with the old password and fails without it, logout clears access.
3. `ENVIRONMENT=production` with the default password refuses to start.
4. Add a short description of the login flow to the existing documentation (where the admin-key `sessionStorage` handling is currently described), tagging anything not run as `[Verify]`.
5. The proxy-awareness tests from 3.8 pass, and `.env.example` documents the variables in 3.9.
6. With `ENVIRONMENT=staging` and the dev password, startup refuses.
7. Commit this phase before starting Phase 4.

---

## Phase 4 (final): Clean the project folder and prepare the first git push

Goal: a repository that someone can clone, set up and run **without** your machine's local files, and that contains no secrets, personal data, downloaded models or runtime junk. Nothing is pushed to any remote until the exit check passes.

### 4.0 Safety rules and starting state

- **Record the git state** (read-only):
  ```bash
  git status --short | head -50
  git remote -v
  git log --oneline | wc -l
  git count-objects -vH
  du -sh .
  ls -la .gitignore .gitattributes 2>/dev/null
  ```
- **Make a safety copy** of the whole project folder **outside** the repository (for example a compressed archive, excluding only `node_modules` and `.venv`). This is the way back if a cleanup step goes wrong. Do not continue without it.
- **Never** run `git clean -fdx`, `git filter-repo`, `git push --force`, or delete files outside the repository without owner confirmation. `git clean -fdx` would delete `.env`, the database and models.
- Local runtime folders (`arch/`, `models/`, `data/`, `logs/`, `.venv/`, `.env`) are **ignored, not deleted**. They stay on disk and out of git.
- Use dry runs first (`git clean -nd`, `git rm -r --cached --dry-run`). Make small commits, one per step.
- Do not push. Prepare the repository and stop; the owner pushes.

### 4.1 Decisions (use these defaults unless the owner changes them)

| Topic | Default |
|---|---|
| Repository visibility | **Private** first. If it may become public, every item marked **[PUBLIC]** below becomes mandatory |
| History | Decided in 4.3 after the scan: keep it if clean, otherwise start a fresh history |
| License | **Owner decides.** Do not choose one silently. Until then, add no `LICENSE` file and note "license pending" in the README |
| Engines (`arch/LocalAI`, `arch/local-tts`) | Not committed. Installed by a setup script at pinned versions |
| Model files | Not committed. Downloaded by script with checksums |
| Large files / Git LFS | Not used unless the owner asks |
| CI | Optional; propose, do not enable without the owner |

### 4.2 Inventory (read-only)

Write `docs/REPO_INVENTORY.md`:

```bash
du -sh * .[!.]* 2>/dev/null | sort -h
git ls-files | wc -l
git ls-files -z | xargs -0 du -k | sort -n | tail -30        # largest tracked files
git status --ignored --short | head -80                       # ignored items present
git ls-files | grep -E "\.(db|sqlite|db-wal|db-shm|gguf|onnx|bin|safetensors|pt|nemo|pcm|log|pid)$|backups/"
```

Classify every top-level item as **KEEP**, **IGNORE** (stays on disk, out of git), **MOVE**, **DELETE** (only with owner confirmation), or **EXTERNAL** (third-party, installed by script).

### 4.3 Secrets and personal data scan (blocking)

1. **Working tree and history.** Run `gitleaks` if installed (`gitleaks detect --no-git --source .` and `gitleaks detect --log-opts="--all"`), otherwise `detect-secrets`, plus this fallback:
   ```bash
   grep -rIniE "(api[_-]?key|secret|token|passw(or)?d|bearer)\s*[:=]" \
     --exclude-dir={.git,node_modules,.venv,arch,models,data,logs} .
   git log --all -p -S"dev-voice-key" --oneline | head
   git log --all -p -S"adminVOICE3" --oneline | head
   ```
   Never print real secrets in the report; record file, line and type only.
2. **Personal and machine-specific data.** Search for and list every hit:
   ```bash
   grep -rIn "/Users/" --exclude-dir={.git,node_modules,.venv,arch,models,data,logs} .
   grep -rIn "file:///" docs/ *.md
   grep -rInE "192\.168\.|10\.[0-9]+\.[0-9]+\.[0-9]+" --exclude-dir={.git,node_modules,.venv,arch,models,data,logs} .
   ```
   The docs contain absolute links to a local user folder; replace them with **relative** links. Remove or replace local account names, home paths, internal IPs, and (for **[PUBLIC]**) real hostnames, domains and server addresses.
3. **Test fixtures and screenshots.** Confirm audio fixtures are synthetic or your own voice and are small. Check screenshots for personal information. Remove anything you are not sure you may publish.
4. **Never tracked:** the database, backups, logs, `.env`, model files. Verify with the `git ls-files | grep` command in 4.2.
5. **Decision on history.** If any real secret, database or large binary was ever committed: treat that secret as leaked and **rotate it**, then start a fresh history (`git checkout --orphan clean-main`, commit the clean tree) or, with owner confirmation, rewrite history with `git filter-repo`. If history is clean, keep it.
6. **[PUBLIC]** The dev password `adminVOICE3.md` appears in this plan and in `.env.example`. For a public repository, change `.env.example` to a placeholder (for example `ADMIN_INITIAL_PASSWORD=change-me-use-12-plus-chars`), remove the password from this plan and any docs, and document that the setup script generates a random initial password.

### 4.4 `.gitignore` and `.gitattributes`

Create or replace `.gitignore`, then check it against 4.2 so nothing needed is excluded:

```gitignore
# secrets and local config
.env
.env.*
!.env.example

# python
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.mypy_cache/
.coverage
htmlcov/

# node / frontend build
node_modules/
frontend/dist/
frontend/.vite/

# runtime data
data/
logs/
.pids/
*.db
*.db-wal
*.db-shm
*.sqlite

# third-party engines and models (installed by scripts/setup.sh)
arch/
models/
*.gguf
*.onnx
*.bin
*.safetensors
*.pt
*.nemo

# generated audio
*.pcm
tests/output/

# OS and editor
.DS_Store
.idea/
.vscode/
*.swp
```

Exceptions to decide and verify:
- The Silero VAD files served from `frontend/public/vad/` (a `.onnx` model, a worklet, and WASM files) are needed by the browser. Either allow them (`!frontend/public/vad/**`, small and permissively licensed, confirm the licenses) or copy them from `node_modules` at build time with a script. Record the choice.
- Keep the small audio fixtures: `!tests/fixtures/**`.

`.gitattributes`: `* text=auto`, and mark `*.wav`, `*.png` and similar as `binary`.

Apply with `git rm -r --cached --dry-run <paths>` first, then for real, for anything already tracked that is now ignored.

### 4.5 Structure cleanup

Target layout (adapt to what exists; do not rename code packages without tests):

```
README.md
LICENSE                 (only after the owner decides)
THIRD_PARTY_NOTICES.md
.env.example
.gitignore  .gitattributes
requirements.txt (or pyproject.toml), package-lock.json
app/  frontend/  services/streaming_stt/
scripts/                start-all.sh (no systemd needed), start, stop, setup, backup, restore, probes
tests/
docs/
  plans/    this plan and other plans
  security/ security reports, hetzner guide, auth matrix output
  deploy/   DEPLOY_HETZNER.md, CONTROLPANEL_PROXY.md, RUNPOD_TEST.md, Caddyfile.hetzner.example, Caddyfile.runpod.example
  guides/   Quick Scan Guide, formal docs, ADHD-friendly docs
  REPO_INVENTORY.md  EVIDENCE.md  VERSIONS.md
```

Tasks:
1. Move planning, security and deployment documents into `docs/` subfolders; keep only `README.md` (and the notices and license files) at the root. Do not delete documents; if two documents duplicate each other, report them and let the owner decide.
2. Remove scratch files, experiment scripts, debug dumps, leftover test outputs, `.bak` and `copy` files (list them first; delete only after owner confirmation).
3. Remove dead code and commented-out blocks only when a test or search proves they are unused. Otherwise list them.
4. Replace absolute paths in scripts and config (for example the hardcoded LocalAI models path) with relative paths or environment variables with documented defaults.
5. Remove development-only flags from production scripts (for example `--reload`).
6. Make `requirements.txt` (or `pyproject.toml`) pin versions; commit `package-lock.json`.

### 4.6 Setup instead of vendored engines

Write `scripts/setup.sh` (and document it in the README) that:

1. creates the virtual environment and installs pinned Python dependencies,
2. runs `npm ci` in `frontend/`,
3. downloads the pinned LocalAI binary and local-tts, **verifying checksums**,
4. downloads the default models,
5. creates `.env` from `.env.example` with **randomly generated** admin and hash secrets,
6. initializes the database.

The script must detect the platform (`uname -s`, `uname -m`, and whether an NVIDIA GPU is present via `nvidia-smi`) and use the matching pinned downloads: macOS Apple Silicon (Metal) for development, Linux with CUDA for RunPod and Hetzner. List the pins per platform in `docs/VERSIONS.md`.

Kokoro on Apple MLX runs only on macOS. A Linux TTS engine is **not** part of this plan: on Linux the setup script prints a clear notice, and the gateway reports TTS as unavailable (a clean 503) instead of crashing. Tests that need macOS-only components or downloaded models must skip with a clear reason.

Also write `scripts/start-all.sh` (and a matching stop script) that starts the engines on loopback and the gateway as background processes with log files and PID files, without systemd, because containers such as RunPod pods may not run it. It reads `GATEWAY_HOST`, `GATEWAY_PORT` and `TRUSTED_PROXY_IPS` from the environment and never binds an engine to anything but `127.0.0.1`.

Record exact versions in `docs/VERSIONS.md` (LocalAI, local-tts, Sherpa-ONNX, model ids and sources). Write `THIRD_PARTY_NOTICES.md` listing each third-party component, model and asset with its license (for example Kokoro, Silero VAD, Sherpa-ONNX, LocalAI, onnxruntime, any other model used). Mark any license you could not verify as `[Verify]`.

### 4.7 Quality gate

- Lint and format: `ruff check . && ruff format --check .`; frontend lint and `npm run build`.
- Full test suite (`pytest tests/ -q`) and `scripts/auth_matrix_probe.py`, with real output saved.
- `pip-audit` and `npm audit --omit=dev`; save the output in `docs/security/`.
- Add a `pre-commit` configuration (gitleaks, ruff, end-of-file and whitespace fixers, a check that blocks added files over 1 MB) and run it on all files.
- Optional (propose only): a CI workflow that runs tests and gitleaks.

### 4.8 README

Write `README.md` with: what the project is, an architecture diagram, requirements (Apple Silicon for development, Linux with NVIDIA GPU for production), quick start (`scripts/setup.sh`, start, first login with forced password change), a configuration table of environment variables, how to run tests, a pointer to `docs/deploy/`, a security section (never expose engine ports, key handling), known issues as currently documented, and "license pending". Mark anything not verified `[Verify]`. For **[PUBLIC]**, keep the dev password out of the README.

### 4.9 First-push procedure (the owner runs the push)

1. **Fresh-clone test.** After committing, clone into a temporary folder and run setup and the tests there:
   ```bash
   git clone . /tmp/clean-check && cd /tmp/clean-check
   ./scripts/setup.sh && .venv/bin/pytest tests/ -q
   ```
   Anything that only works on your machine shows up here. Tests that need downloaded models should skip cleanly with a clear message.
2. **Size check:** `du -sh .git` and the 20 largest tracked files; nothing over 5 MB without an explicit reason.
3. Re-run the 4.3 scans on the final tree and history.
4. Push to a **private** repository first, even if the goal is public. Check the host's secret-scanning page, then change visibility only after the owner agrees.
5. Enable branch protection on `main` and secret-scanning push protection on the host.
6. Tag the first commit (for example `v0.1.0-baseline`).

### 4.10 RunPod test deployment (owner-run, after the cleanup)

Purpose: test the login, the API keys and the WebSockets over the real internet before the Hetzner deployment. The agent prepares the files; the owner runs the pod. Write the results into `docs/deploy/RUNPOD_TEST.md` and `docs/EVIDENCE.md`.

| Topic | RunPod test | Hetzner production |
|---|---|---|
| Public entry | HTTP proxy URL `https://<pod-id>-<port>.proxy.runpod.net`, TLS by RunPod | Caddy on 443 with the real domain and automatic TLS |
| Exposed port | One HTTP port, bound to `0.0.0.0` (gateway or Caddy) | Only 80 and 443 (Caddy); gateway on `127.0.0.1` |
| Engines | `127.0.0.1` | `127.0.0.1` |
| Process manager | `scripts/start-all.sh` (or supervisor or tmux) | systemd units with sandboxing |
| Firewall | Provider-managed; only exposed ports are reachable | UFW (IPv4 and IPv6) plus Hetzner's network firewall |
| `ENVIRONMENT` | `staging` | `production` |
| Storage | Only the persistent volume (`/workspace`) survives; RunPod warns that data outside it is lost | The server's disks |
| Timeouts | 100 s limit on the HTTP proxy | Set in Caddy |
| Secrets | Throwaway: generate new ones, destroy after the test | Production secrets, never reused from the test |
| Second layer | Caddy `basic_auth` on the panel path | Decision with the owner (3.7) |

Steps:

1. **Pod:** a GPU template; add **one** port under "Expose HTTP Ports". A port cannot be exposed as both HTTP and TCP, and no other port is exposed. Direct TCP ports get a random public IP and port, change when the pod resets, and have no TLS, so the browser microphone would not work over them; use them only for `curl` or API-client checks.
2. **Deliver the code** with `rsync` or `scp` from the Mac (works before any push), or `git clone` of the private repository using a read-only deploy key created for this test. Never store tokens in the repository or in `.env`.
3. **Storage:** keep `.env`, models and backups under `/workspace`. Test SQLite WAL there; WAL can misbehave on network filesystems. If locking errors appear, keep the database on the container disk and back it up to `/workspace` with `scripts/backup-db.sh`.
4. **Configure:** run `./scripts/setup.sh`; set `ENVIRONMENT=staging`, a random 12+ character `ADMIN_INITIAL_PASSWORD` (not the dev one), a random `ADMIN_API_KEY` and `API_KEY_HASH_SECRET`, and `GATEWAY_HOST=0.0.0.0`.
5. **Start:** `./scripts/start-all.sh`. Run `ss -tulpn`: only the exposed port may listen on `0.0.0.0`; the engines must show `127.0.0.1`.
6. **Test checklist** (record each result):
   - Open the proxy URL, pass the second layer, see the login page, log in with the seeded password, be forced to change it, and confirm the cookie flags (HttpOnly, Secure, SameSite=Strict) in the browser developer tools; log out.
   - Calls to `/v1/...` without a key return 401; calls with a key work for its scopes only; a caller key cannot reach admin routes.
   - STT streaming and voice work through the proxy, including the microphone (the proxy provides HTTPS).
   - A 10-minute idle WebSocket and a 30-minute active one: record any disconnects, and confirm the ping and reconnect behavior.
   - The first request after startup while a model loads: if there is no response within 100 seconds the proxy returns `524`; record it and pre-warm the model.
   - Five bad logins lock the account, and one client's lockout does not lock out everyone else.
   - Logs contain no passwords, cookies, keys or transcripts.
7. **After the test:** delete the pod, destroy every test secret, remove the deploy key, and never reuse the test `.env` on Hetzner.

### 4.11 Exit check

- [x] Safety copy exists and is outside the repository (`/tmp/voice-agent-safety-backup.tar.gz`).
- [x] `docs/REPO_INVENTORY.md` lists every top-level item with its decision.
- [x] Secrets and personal-data scans (working tree **and** history) are clean, or the findings are rotated and history was reset; evidence recorded in `docs/EVIDENCE.md` (no secret values).
- [x] No absolute local paths or `file:///` links remain in tracked files.
- [x] No database, backup, log, `.env`, engine, or model file is tracked (`git ls-files` grep returns nothing).
- [x] `.gitignore` and `.gitattributes` are in place; the VAD asset decision is recorded.
- [x] `scripts/setup.sh`, `docs/VERSIONS.md` and `THIRD_PARTY_NOTICES.md` exist.
- [x] Linters, tests, auth probe and dependency audits pass, with output saved.
- [x] The fresh-clone test passes.
- [x] `README.md` exists and its commands were run.
- [x] The tracked size is reasonable and no large binaries remain (max tracked file is 164 KB).
- [x] Owner decisions recorded: visibility (Private first), history choice (kept clean), license (pending), VAD assets (build-time copy script), duplicate docs.
- [x] `scripts/start-all.sh`, `docs/deploy/RUNPOD_TEST.md`, `Caddyfile.runpod.example` and `Caddyfile.hetzner.example` exist.
- [x] The setup script detects macOS versus Linux with CUDA, and on Linux without a TTS engine the gateway reports TTS unavailable cleanly.
- [ ] (Owner-run) RunPod test results are recorded, no development settings were used on the pod, and the test secrets were destroyed.
- [x] Nothing has been pushed by the agent.

---

## Rules for the agent

- Verify before claiming. No "Confirmed" without evidence.
- Make small commits, one per numbered step, and run the tests before each.
- If a step fails or contradicts this plan, stop and report instead of working around it.
- Never run an internet-reachable environment (RunPod or Hetzner) with `ENVIRONMENT=development`, the dev password, or the default `ADMIN_API_KEY`.
- Stay inside this scope: Phase 3, then Phase 4. Do not start unrelated fixes; list them in `docs/EVIDENCE.md` under "Noticed but out of scope" instead.
- **Do not push to any remote before Phase 4 passes, and never without the owner's go-ahead.**
- Never delete or overwrite local runtime data (`.env`, `data/`, `models/`, `arch/`) while cleaning; ignore it instead.

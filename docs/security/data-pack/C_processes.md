# Data Pack: Section C - Processes & Permissions

Collected on: 2026-11-16

## C-01: Service Management
* Current runtime: Native foreground and background processes started via `scripts/start-native-engines.sh` and shell commands (`uvicorn`, `npm run dev`).
* No systemd or Docker containerization currently active on this development machine.

## C-02: Process Execution User
* All running processes (`local-ai`, `local-tts`, `uvicorn`, `server.py`, `node`) run under the local interactive desktop account:
  * User: `hashhwin047icloud.com` (UID 501, GID 20)
  * None run as root (`UID 0`).

## C-04: Secrets & Environment Configuration
* Configuration file: `.env` located at workspace root.
* Keys present:
  * `ADMIN_API_KEY`: Configured
  * `API_KEY_HASH_SECRET`: Configured
  * `LOCALAI_API_KEY`: Optional, supported in `app/core/config.py`
* Git tracking: `.env` is listed in `.gitignore` and is NOT committed to git history.

## C-05: File Permissions Audit
Observed via `ls -la`:

| File / Folder | Permissions | Security Impact |
|---|---|---|
| `.env` | `-rw-r--r--` (644) | **Grave Risk (Medium/High):** World-readable on the host. Any other unprivileged local user or process on the same machine can read `ADMIN_API_KEY` and secrets. Needs `chmod 600`. |
| `voice_platform.db` | `-rw-r--r--` (644) | **Grave Risk (Medium/High):** World-readable SQLite database containing API key hashes, agent prompts, and usage logs. Needs `chmod 600`. |
| `data/` | `drwxr-x---` (750) | Group-readable, others denied. |
| `scripts/*.sh` | `-rwxr-xr-x` (755) | Executable by owner, readable by others. |

## C-06: Process Cleanup Behavior
* `scripts/stop-native-engines.sh` terminates processes listening on ports 8080 and 8880 via `lsof -ti :<port>`.

## C-07: Frontend Server Mode
* Process: `node .../vite` listening on `127.0.0.1:5173`.
* Running in **development mode** (`vite` dev server), not serving a static production bundle.
* On Hetzner, the frontend must be compiled with `npm run build` and served statically by Caddy/Nginx rather than running a Vite dev server.

## C-08: Gateway Execution Flags
* Command: `.../uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload`
* `--reload` is currently active! In production on Hetzner, `--reload` must be disabled as file-watcher threads consume CPU and can trigger unexpected restarts.

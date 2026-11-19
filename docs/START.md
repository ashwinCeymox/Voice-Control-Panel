# Unified Stack Launcher Reference Guide (`docs/START.md`)

This guide documents `./start.sh`, the end-to-end one-command installer and launcher for the Local Voice AI Platform. It is fully POSIX/Bash 3.2+ compatible and operates identically on **RunPod Cloud** (Linux x86_64 / NVIDIA CUDA) and **Local Development** (macOS Apple Silicon Metal).

---

## 1. CLI Commands & Options

```bash
./start.sh [COMMAND] [FLAGS]
```

### Primary Commands
| Command | Action |
|---|---|
| `./start.sh` or `./start.sh up` | **Default:** Runs idempotent installer, resolves ports, boots all stack services, and displays the banner. |
| `./start.sh start` | Starts all stack services (skipping installation steps if already satisfied). |
| `./start.sh stop` | Gracefully terminates all recorded daemon PIDs in `.run/`. **Never kills by port number.** |
| `./start.sh restart` | Stops stack, re-reads `.env` (applying any port or setting changes), and restarts all services. |
| `./start.sh status` | Displays live online/offline status, PIDs, ports, and health URLs for all services. |
| `./start.sh ports` | Inspects and displays configured vs active network ports and conflict resolution status. |
| `./start.sh url` | Displays the active public web panel URL and API base URL without starting or stopping services. |
| `./start.sh doctor` | Performs diagnostic health check (OS, GPU, disk, ports, connectivity) without changing state. |
| `./start.sh logs` | Tails live service logs from `logs/*.log`. |
| `./start.sh reset-admin` | **Break-Glass:** Generates a new random admin password, enforces password change at next login, and invalidates all existing sessions. |
| `./start.sh install` | Runs installer and builds assets only (does not start background services). |

### CLI Flags
- `--dry-run`: Prints the execution plan and detected environment without modifying filesystem or starting processes.
- `--no-install`: Starts services immediately without checking or updating dependencies.
- `--skip-models`: Skips automatic model downloads.
- `--foreground` / `-f`: Keeps execution attached to the terminal with log streaming (Ctrl+C shuts down stack).

---

## 2. Configuration & Environment Variables (`.env`)

On first launch, `.env` is initialized non-destructively with mode `600`. Existing user settings are **never overwritten**.

| Environment Key | Default | Description |
|---|---|---|
| `ENVIRONMENT` | `staging` (RunPod) / `development` (Mac) | Environment mode. In `staging`/`production`, weak passwords and wildcard CORS are strictly blocked at startup. |
| `GATEWAY_PORT` | `8000` | **Public gateway HTTP port.** On RunPod, must match the port listed under *Expose HTTP Ports*. |
| `GATEWAY_HOST` | `0.0.0.0` (RunPod) / `127.0.0.1` (Mac) | Gateway listening interface. |
| `LOCALAI_PORT` | `8080` | Internal loopback port for LocalAI LLM and batch STT engine. |
| `TTS_PORT` | `8880` | Internal loopback port for Kokoro TTS engine. |
| `STT_PORT` | `8088` | Internal loopback port for Sherpa-ONNX streaming STT engine. |
| `FRONTEND_DEV_PORT` | `5173` | Internal port for Vite development dev-server (only in `development`). |
| `AUTO_PORTS` | `true` | Allows script to automatically move internal busy ports to the next free port. |
| `PORT_SEARCH_RANGE` | `100` | Maximum port offset when searching for free ports during conflict resolution. |
| `PUBLIC_BASE_URL` | *(Auto-detected)* | Custom public base URL (e.g. `https://my-domain.com`). If empty, derived dynamically from `RUNPOD_POD_ID`. |
| `COOKIE_SECURE` | `auto` | Enforces `Secure` attribute on admin session cookies. Auto-enabled in `staging`/`production`. |
| `TRUSTED_PROXY_IPS` | `""` | Comma-separated trusted upstream proxy IPs for `X-Forwarded-For` header resolution. |
| `SERVE_FRONTEND` | `true` | Enables FastAPI Gateway to serve the built React panel directly from `/voicecontrolpanel`. |
| `AUTO_RESTART` | `true` | Auto-restarts crashed backend daemons with exponential backoff. |

---

## 3. Network Ports & Auto-Migration

1. **Loopback Engine Isolation:** All engine services (`LocalAI:8080`, `local-tts:8880`, `Sherpa:8088`) bind strictly to `127.0.0.1` and are never exposed publicly.
2. **Internal Conflict Auto-Migration:** If an internal port (e.g. `8080`) is occupied by an external process, the script automatically increments the port (e.g. `8081`), records the change into `.env`, and routes the gateway accordingly.
3. **RunPod Public Port Protection:** On RunPod, `GATEWAY_PORT` is **never** silently migrated. If port `8000` is busy, the script halts with a clear instruction because RunPod's public HTTP proxy is mapped to a fixed port in the pod template settings.

---

## 4. Troubleshooting

### Port Conflict on Public Gateway Port
- **Error:** `CRITICAL: Public Gateway Port 8000 is currently occupied by an external process.`
- **Remedy:** Stop the conflicting process with `./start.sh stop`, or if running inside a container, update `GATEWAY_PORT` in `.env` and adjust the exposed port in your RunPod pod settings.

### Gateway Not Reachable from Browser on RunPod
- **Symptom:** Gateway responds inside the pod (`curl http://127.0.0.1:8000/health/live`), but `https://<pod-id>-8000.proxy.runpod.net` times out.
- **Remedy:** Verify that `8000` is listed in your pod settings under **Expose HTTP Ports**. On RunPod, changing exposed ports requires restarting the pod.

### TTS Reports Unavailable (HTTP 503) on Linux
- **Context:** Kokoro MLX is an Apple Silicon Metal engine. On Linux pods without a provisioned Linux Kokoro server, the gateway gracefully reports TTS as disabled and returns clean HTTP 503 for synthesis requests while LLM and streaming STT remain 100% operational.

### Forgotten Admin Password
- **Remedy:** Run the break-glass reset command:
  ```bash
  ./start.sh reset-admin
  ```
  This immediately generates a new random password, clears existing sessions, and sets `must_change_password=1` on your account.

### Updating the Platform
To pull the latest changes and apply updates cleanly:
```bash
git pull
./start.sh restart
```
This safely stops running daemons, updates virtualenv dependencies if requirements changed, and boots the new code without data loss.

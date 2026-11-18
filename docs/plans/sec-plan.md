# Security Investigation Plan: Exposing the Voice AI API to the Internet

**Goal:** Find out whether someone on the internet (or on your local network) could reach LocalAI, local-tts, the streaming STT service or the admin features **without a valid API key**, and find every other realistic way the system could be abused, before it is opened to the public.
**Deployment (as stated):** installed directly on your own server (no Docker), API keys handed to coworkers as deliverables, reachable over the public internet.
**Who runs this:** your working agent collects the data and runs the checks; you review the report yourself. For a public service holding keys, consider an independent review (or a professional penetration test) before launch. An agent report is a strong first pass, not a guarantee.

---

## 0. Ground Rules

1. **Authorized scope only.** Test only systems you own: this server, its network and its domain. Never scan or attack third-party systems.
2. **Outside view required.** Network exposure must be tested from a machine **outside** your network (for example a cheap VPS or a phone hotspot). Tests from the server itself prove little.
3. **Read-only first.** Phase I0 to I2 collect data and run non-destructive probes. No deleting, no changing configuration, no heavy load, until the owner approves.
4. **Rate-limit your scans** so they do not disturb the service.
5. **Redact secrets.** No real keys, hashes of live keys, passwords or private keys in the report. Use `REDACTED`.
6. **Evidence for every finding:** the command or request, the output, and the file and line when it is code. Mark unproven items UNVERIFIED.

---

## 1. Your Question, Answered as a Hypothesis to Test

> Can someone see the endpoints used (LocalAI, local-tts) and gain access to run these services without an API key?

- **Seeing endpoint names is not a break-in.** Paths such as `/v1/chat/completions` are public knowledge, and scanners, public API docs, certificate logs and traffic analysis can reveal a service within hours of exposure. Security must not depend on the paths being secret.
- **Reaching the engines without a key** is possible only if one of these is true. Each is a threat in section 3:
  1. an engine port is reachable from outside (bound to `0.0.0.0` or `::`, open firewall, router port forward, IPv6 forgotten);
  2. the reverse proxy or the gateway forwards a path to an engine without checking a key;
  3. a gateway route lacks authentication, or authentication can be bypassed (default dev key, weak comparison, header trust, route normalization tricks);
  4. a bug lets an attacker make the server call internal addresses (SSRF) or read files (path traversal);
  5. another service on the host is compromised and used as a stepping stone;
  6. a valid key is stolen or leaked (not a bypass, but the same outcome).
- **Why this matters beyond the key:** LocalAI exposes powerful endpoints (model install and import, backend shutdown, system information, metrics, traces and logs, agent job execution, MCP tool execution, P2P token). They must be unreachable from outside, even for caller keys.

---

## 2. Information Needed (the Data Pack)

The agent collects these into `docs/security/data-pack/`. Items marked **(owner)** need your answer.

### A. Deployment facts (owner)
| ID | Question |
|---|---|
| A-01 | Is the server a **cloud VM** or a **machine on your home or office network** exposed through a router port forward? This changes the risk a great deal, because a compromise of a LAN machine can reach your other devices |
| A-02 | Operating system and version; hosting provider if cloud |
| A-03 | Domain name, DNS provider, public IPv4 and IPv6 addresses |
| A-04 | Who needs access (number of coworkers, which projects), and how many keys exist or are planned |
| A-05 | How keys are delivered to coworkers today (chat, email, shared document?) |
| A-06 | Who has admin access and shell access to the server |
| A-07 | What other devices share the same network as the server |
| A-08 | Is there any existing monitoring, backup or update routine? |
| A-09 | Is the exposed data sensitive (audio, transcripts, personal data of third parties)? |

### B. Network and host
| ID | Collect | How (read-only) |
|---|---|---|
| B-01 | Every listening port, its address and process | Linux: `sudo ss -tulpnH` · macOS: `sudo lsof -iTCP -sTCP:LISTEN -nP` |
| B-02 | Bind address of the gateway, LocalAI, local-tts, the streaming STT service, the frontend server | from B-01 and each service's launch command |
| B-03 | Firewall rules (IPv4 **and IPv6**), default policy | Linux: `sudo ufw status verbose`, `sudo nft list ruleset`, `sudo iptables -S`, `sudo ip6tables -S` · macOS: `sudo pfctl -sr` |
| B-04 | Router or cloud security-group rules, port forwards, UPnP status, IPv6 firewall on the router | screenshots or exports |
| B-05 | SSH configuration (root login, password login, allowed users, port), fail2ban or equivalent | `sudo sshd -T`, `last`, auth log summary |
| B-06 | OS patch level and automatic updates | `apt list --upgradable` or `softwareupdate -l`; unattended-upgrades status |
| B-07 | User accounts and sudo rights | `getent passwd`, `sudo -l`, `/etc/sudoers.d` |
| B-08 | Is the host separated from the rest of the LAN (DMZ, VLAN, separate network)? Can it reach other LAN devices? Outbound filtering? | test with `ping` or `nc` to a LAN address from the server |

### C. Processes (no Docker, so systemd or scripts matter)
| ID | Collect | How |
|---|---|---|
| C-01 | The service manager for each program (systemd units, launchd, `start.sh`, tmux) | `systemctl list-units --type=service --state=running`, `systemctl cat <unit>` |
| C-02 | The **user** each service runs as (must not be root) | `ps -eo user,pid,args \| grep -E "local-ai\|local-tts\|uvicorn\|sherpa\|caddy\|nginx"` |
| C-03 | systemd sandboxing score per unit | `systemd-analyze security <unit>` |
| C-04 | Environment variables and files holding secrets (names only, values redacted) | unit files, `.env`, `start.sh` |
| C-05 | File permissions of the database, `.env`, key files, models folder, logs, scripts | `ls -la`, `stat`, `namei -l <path>` |
| C-06 | What `start.sh` does at start (it kills whatever is listening on certain ports) and whether it runs with elevated rights | read the script |
| C-07 | Whether the frontend runs as a **development server** (for example Vite on 5173) or as a production build | process list, config |
| C-08 | Whether the gateway runs with debug or auto-reload options | launch command |

### D. Reverse proxy and TLS
| ID | Collect | How |
|---|---|---|
| D-01 | Which proxy (Caddy, nginx, none) and its **effective** config | `nginx -T` or `caddy adapt --config <file>` (redact) |
| D-02 | Which paths and ports are forwarded; any catch-all that forwards everything | config review |
| D-03 | Which headers are trusted (`X-Forwarded-For`, `Host`, `X-Real-IP`) and whether the gateway trusts them for decisions | config and code |
| D-04 | TLS version, ciphers, HTTP to HTTPS redirect, HSTS, certificate renewal | `openssl s_client -connect <host>:443 -servername <host>`, `testssl.sh` |
| D-05 | Behavior when the server is reached by **IP address** or an unknown `Host` | `curl -ik https://<ip>/` |
| D-06 | WebSocket upgrade handling, body size limit, timeouts, rate limiting at the proxy | config |
| D-07 | Access log contents (do logs contain keys, query strings or content?) | config and a sample |

### E. Gateway application
| ID | Collect |
|---|---|
| E-01 | The complete route list (HTTP and WebSocket) with method, path, auth required, role required. Generate it from the running app, not from memory |
| E-02 | Authentication code: key format, hashing, comparison method (constant time?), scopes, roles, key rotation, whether a default or development key exists |
| E-03 | WebSocket authentication: ticket creation, lifetime, single use, storage, origin check, what protects the ticket endpoint |
| E-04 | CORS settings (origins, credentials) |
| E-05 | Public documentation routes (`/docs`, `/redoc`, `/openapi.json`) and whether they are exposed |
| E-06 | Input handling: model names, voice ids and file names used in paths; upload size and type limits; any parameter that is a URL or file path |
| E-07 | Any feature that makes the server fetch a URL or open a file chosen by the caller (SSRF and file read risk) |
| E-08 | Database access: parameterized queries or string-built SQL |
| E-09 | Error responses: do they leak paths, upstream text, stack traces or internal addresses? |
| E-10 | Admin and dashboard authentication: session cookies (HttpOnly, Secure, SameSite), CSRF protection, login rate limiting, password or key storage |
| E-11 | Rate limits and concurrency limits per key and global; behavior at the limit |
| E-12 | Logging: what is written, whether keys, transcripts or audio can appear |
| E-13 | Dependency list with versions and known vulnerabilities |
| E-14 | Secrets in source control history |

### F. Engines
| ID | Collect |
|---|---|
| F-01 | LocalAI: version, bind address, whether its own API key or user authentication is on, whether its web UI is reachable, CORS settings, P2P enabled or not, agent and MCP features enabled or not |
| F-02 | LocalAI endpoints that must **never** be reachable from outside, checked from the outside and from the LAN: `/models/apply`, `/models/delete/{name}`, `/models/import`, `/backend/load`, `/backend/shutdown`, `/backend/monitor`, `/system`, `/metrics`, `/api/traces`, `/api/backend-logs`, `/ws/backend-logs`, `/api/p2p/token`, `/api/agent/jobs/execute`, `/v1/mcp/chat/completions`, `/api/conversations` |
| F-03 | local-tts: version, bind address, authentication (likely none), exposed routes (`/v1/voices`, `/v1/models`, synthesis HTTP and WebSocket) |
| F-04 | Streaming STT service: version, bind address, authentication (likely none), exposed WebSocket |
| F-05 | Where model files and configs are stored, who can write there, and whether any route lets a caller influence them (install, import, upload) |
| F-06 | Known security advisories for each engine version in use |

### G. Data and operations
| ID | Collect |
|---|---|
| G-01 | Where keys, hashes and the admin credential are stored; hashing method; backup copies |
| G-02 | What is stored: audio, transcripts, prompts, usage records; retention |
| G-03 | Key lifecycle: creation, expiry, rotation, revocation, offboarding of a coworker |
| G-04 | Alerting and log review: who looks, how often |
| G-05 | Backup and restore procedure, tested or not |
| G-06 | Incident plan: what you do if a key leaks or the server is compromised |

---

## 3. Threat Model

**Assets:** the compute and models; API keys and admin access; the database (usage, agents, keys); the server itself; the rest of your LAN; any audio or transcripts.

**Actors:** an internet scanner; someone holding a leaked or stolen key; a coworker misusing a key; a compromised coworker project; an attacker who finds one weak service; an administrator mistake.

| ID | Threat | What goes wrong | Checks |
|---|---|---|---|
| T1 | Engine ports reachable directly | Anyone can call LocalAI, local-tts or the STT service with no key | B-01 to B-04, F-02 to F-04, external scan (IPv4 and IPv6) |
| T2 | Proxy forwards too much | A path through the proxy reaches an engine or an admin route without authentication; path tricks bypass rules | D-01 to D-03, path normalization tests |
| T3 | Gateway authentication gaps | A route has no key check; default dev key still works; weak comparison; headers trusted | E-01, E-02, authentication matrix (section 5) |
| T4 | WebSocket weaknesses | Connection without a ticket, reused ticket, cross-site hijacking, missing origin check | E-03, WebSocket tests |
| T5 | Key theft and leakage | Keys sniffed, in logs, in git, in chat history, in screenshots, in shared snippets | A-05, D-07, E-12, E-14, G-01, G-03 |
| T6 | Privilege escalation | A caller key reaches admin routes (model install and delete, key management, dashboard) | E-01, authentication matrix, role tests |
| T7 | SSRF or pivot | The gateway is tricked into calling internal addresses (engines, cloud metadata, LAN devices) | E-06, E-07, B-08 |
| T8 | Injection and traversal | Model or voice names escape the intended folder; SQL injection; model import or install used to run unwanted code | E-06, E-08, F-02, F-05 |
| T9 | Resource exhaustion | Unauthenticated or authenticated flood, large uploads, many WebSockets exhaust the 8 GB machine | E-11, D-06 |
| T10 | Host compromise and lateral movement | A bug in any service gives a shell and access to the LAN | B-07, B-08, C-02, C-03 |
| T11 | Vulnerable software | Old engine, framework or OS versions with known flaws | B-06, E-13, F-06 |
| T12 | Data exposure | Transcripts, audio, internal paths or upstream errors leaked through logs or error messages | E-09, E-12, G-02 |
| T13 | Information disclosure | Open API docs, version banners, verbose errors, directory listings | E-05, D-04 |
| T14 | Dashboard attacks | Session theft, CSRF, brute-force of the admin login | E-10 |
| T15 | Operations | A leaked key cannot be revoked quickly; no alerts; no tested backup | G-03 to G-06 |

---

## 4. Investigation Phases

```
I0 Collect data ─▶ I1 Map exposure ─▶ I2 Auth matrix ─▶ I3 Proxy and TLS ─▶ I4 WebSocket and dashboard
                                                                                       │
I9 Report and plan ◀─ I8 Operations review ◀─ I7 Abuse checks ◀─ I6 Dependencies and secrets ◀─ I5 Engines and host
```

| Phase | Work | Output |
|---|---|---|
| **I0** | Owner answers section A; the agent builds the data pack for sections B to G | `docs/security/data-pack/` |
| **I1** | **Exposure map.** From inside: every listener and bind address. From **outside** your network: scan the public IPv4 **and IPv6** address of the server (own addresses only, for example `nmap -Pn -sV <your-ip>` and `nmap -6 -Pn <your-ipv6>`), plus direct requests to the known engine ports (8080, 8880, the STT port, the frontend port) | Exposure table: port, reachable from where, service |
| **I2** | **Authentication matrix.** For every route (section 5), test with no key, invalid key, caller key, admin key, expired key, revoked key, and with the default dev key | Pass/fail matrix |
| **I3** | **Proxy and TLS.** Path normalization (`//`, `/./`, `/%2e%2e/`, encoded slashes), `Host` and `X-Forwarded-*` spoofing, access by IP, unknown host, forwarded paths to engine routes, TLS settings, HTTP redirect, request size and timeouts | Findings |
| **I4** | **WebSocket and dashboard.** Connect without a ticket, reuse a ticket, wrong origin, cross-site test, concurrency flood on sockets; dashboard: cookie flags, CSRF, login brute-force limits | Findings |
| **I5** | **Engines and host.** Confirm engines are loopback-only; check the F-02 list from outside and from the LAN; review service users, file permissions, sandboxing, SSH, firewall default policy, host isolation from the LAN | Findings |
| **I6** | **Dependencies and secrets.** `pip-audit`, `npm audit --omit=dev`, OS updates, engine advisories; secret scan of the repository and its history (for example `gitleaks`), environment files, backups | Findings |
| **I7** | **Abuse checks.** Rate limits and concurrency limits at and above the cap (6 and 7 requests), oversized uploads, long-running streams, many idle sessions, bad input to every endpoint | Findings |
| **I8** | **Operations review.** Key delivery, rotation, revocation test (a revoked key must fail at once), offboarding, logging and alerts, backup restore, incident steps | Findings |
| **I9** | Consolidate; severity ranking; fix plan; retest list | `docs/security/security-investigation.md` |

---

## 5. Authentication Matrix Template

Generate the route list from the running application. For every route fill in the observed result.

| Route | Method | No key | Invalid key | Caller key | Admin key | Expired | Revoked | Default dev key | Expected |
|---|---|---|---|---|---|---|---|---|---|
| `/health/live` | GET | | | | | | | | public, minimal info |
| `/v1/chat/completions` | POST | | | | | | | | needs `llm` scope |
| `/v1/audio/transcriptions` | POST | | | | | | | | needs `stt` scope |
| `/v1/text-to-speech/{voice}/stream` | POST | | | | | | | | needs `tts` scope |
| `/v1/models/install`, `DELETE /v1/models/{id}` | POST, DELETE | | | | | | | | admin only |
| `/v1/agents...` | all | | | | | | | | admin only (Phase 1) |
| `/v1/admin/...` | all | | | | | | | | admin only |
| `/docs`, `/redoc`, `/openapi.json` | GET | | | | | | | | not public |
| WebSocket routes | WS | | | | | | | | ticket or key required |

The expected result for any denied request is `401` or `403` with **no** upstream details.

---

## 6. Severity Scale

| Level | Meaning |
|---|---|
| **Critical** | Anyone on the internet can use the engines or admin functions without a key, or run code on the host |
| **High** | An authenticated caller can reach admin functions; keys can be stolen in transit or from logs; engine ports reachable from the LAN |
| **Medium** | Information disclosure, missing rate limits, weak cookie or CORS settings, outdated software without a known exploit path |
| **Low** | Hardening gaps with no direct exploit |

---

## 7. Report Format (`docs/security/security-investigation.md`)

1. Architecture and **exposure map** (diagram: who can reach what, from where)
2. Asset and data inventory
3. Findings table: ID, threat, severity, evidence, fix, owner, status
4. Completed authentication matrix
5. Verdict for each threat T1 to T15: **Confirmed**, **Ruled out**, **Unknown**
6. A direct answer to the owner's question with evidence
7. Prioritized fix list: **must fix before going public**, **fix soon**, **hardening**
8. Retest plan
9. Open questions for the owner

---

## 8. Baseline Target Architecture (what the findings are compared against)

- Only the reverse proxy (80 and 443) listens on a public address. The gateway, LocalAI, local-tts and the STT service listen on `127.0.0.1` (or unix sockets) only.
- Firewall: default deny incoming on IPv4 **and** IPv6, allow only 80 and 443 (and SSH from a restricted source).
- Reverse proxy forwards only an explicit list of gateway paths. It never forwards to an engine.
- The gateway authenticates every route except a minimal health route; admin routes require the admin role; no default or development key exists.
- Engines are called by the gateway with an internal credential that is never given to anyone else; LocalAI's powerful endpoints are not reachable by any caller.
- Each service runs as its own unprivileged user with a sandboxed systemd unit; models, database and keys are not world-readable.
- The frontend is a production build, not a development server.
- Rate limits, concurrency limits, upload limits and timeouts are enforced; keys can be revoked immediately.
- The server is isolated from the rest of the LAN (DMZ or VLAN, outbound filtering), updates are automatic, logs contain no secrets or content, and backups are tested.
- TLS everywhere on the public side; certificates renew automatically.

---

## 9. Starting Prompt for the Agent

> You are the security investigator. Read `security-investigation-plan.md`. Work in phases I0 to I9. Read-only until the owner approves anything else. Test only systems the owner controls, and run network tests from an outside vantage point where the plan requires it. Redact secrets. Every finding needs evidence (command and output, or file and line); mark anything you cannot prove as UNVERIFIED. Produce `docs/security/data-pack/` first, then `docs/security/security-investigation.md` in the format of section 7. Stop after I0 and wait for the owner's answers to section 2A before running the external tests.

**Independent review prompt (fresh context, report only):**
> You have only the investigation report. Challenge it: which threats were not tested, which evidence is weak, which "ruled out" verdicts depend on an assumption, and what an attacker could still try.

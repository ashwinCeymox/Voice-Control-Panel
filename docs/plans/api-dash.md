# Developer Dashboard Plan: Usage, Profiles, API Keys and Copyable Snippets

**Goal:** An admin dashboard inside the control panel where you can see API usage, create **profiles** (one per project or app that consumes your models), generate **API keys** shown as cards, and copy a ready-to-run code snippet for any installed model. Phase 1 scope: access over the **local network**.
**Agents:** Gemini 3.8 Flash (high), one herdr pane each, model pinned and confirmed before tasks are sent.

**Assumptions (tell me if wrong)**
- A **profile** means a consuming project or app (for example "other-project-server"), not a person. A profile owns one or more keys.
- The dashboard is for admins only. Callers use keys; they never see the dashboard.
- The key system from the cloud plan (P2: hashed keys, roles, scopes, per-key limits, usage log) is built or is built first. This plan adds the UI, profiles, usage reporting and snippets on top.

---

## 1. Important Corrections to the Idea

1. **Snippets must point to the gateway, not to the engines.** Your example calls `127.0.0.1:8880` (local-tts directly). With keys, callers must go through the gateway (for example `http://<lan-ip>:8000/v1/text-to-speech/{voice}/stream`) and send an `Authorization: Bearer` header. local-tts and LocalAI stay on `127.0.0.1`, unreachable from the network.
2. **The gateway needs a deliberate LAN bind.** Bind the gateway to the LAN interface (or `0.0.0.0` behind a firewall rule for the LAN only). Engines never bind to the LAN.
3. **LAN means plain HTTP.** Keys travel unencrypted. Acceptable on a trusted network, but note it in the UI, and add an optional per-key IP allowlist (CIDR). Local HTTPS can come later.
4. **"Any installed model" has a memory cost.** On an 8 GB machine, a call to a model that is not loaded forces a load and can evict another. The dashboard shows which models are warm, and caller keys never trigger agent switching (cloud plan P3).
5. **The full key is shown once.** After creation only a prefix (for example `vk_live_ab12…`) is shown. Snippets use a placeholder unless the key was just created.

---

## 2. Dashboard Structure

New sidebar group **Developers** (admin role only):

```
Developers
├── Overview          usage cards and charts
├── API Keys          profiles and key cards
├── Docs & Snippets   copyable code per model and endpoint
├── Request Log       recent requests (no content)
└── Settings          base URL, limits, retention, kill switches
```

### 2.1 Overview
- Time range: 1 h, 24 h, 7 d, 30 d.
- Cards: requests, success rate, errors (4xx / 5xx), `429`/`503` count, p50 and p95 latency, time to first byte for streaming calls, active streams, queue length.
- Usage units: LLM tokens in and out, STT audio seconds in, TTS characters in and audio seconds out.
- Charts: requests over time, latency over time, usage by model, usage by key, usage by profile.
- Top lists: busiest keys, slowest endpoints, most frequent errors.
- Health pills: gateway, LocalAI, local-tts, streaming STT service (from `/health/ready`).
- Loaded models: which models are currently warm, with memory use.

### 2.2 API Keys: profiles and key cards

Layout: a profile list on the left, key cards for the selected profile on the right.

```
┌─ Profile: other-project-server ───────────────────────  [+ New key]  [Edit profile] ┐
│                                                                                      │
│  ┌──────────────────────────────┐  ┌──────────────────────────────┐                  │
│  │ prod-backend       ● Active  │  │ batch-jobs        ○ Paused   │                  │
│  │ vk_live_ab12…                │  │ vk_live_9f3c…                │                  │
│  │ [llm] [stt] [tts]            │  │ [stt]                        │                  │
│  │ Models: all installed        │  │ Models: whisper-base         │                  │
│  │ Limit: 60 req/min · 3 conc.  │  │ Limit: 20 req/min · 1 conc.  │                  │
│  │ Last used: 2 min ago         │  │ Expires: in 28 days          │                  │
│  │ ▁▂▅▇▅▃▂▂ 24 h requests       │  │ ▁▁▁▂▁▁▁▁ 24 h requests       │                  │
│  │ [Snippets] [Edit] [Rotate]   │  │ [Snippets] [Resume] [Revoke] │                  │
│  └──────────────────────────────┘  └──────────────────────────────┘                  │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

**Key card shows:** name, status (active, paused, expired, revoked), key prefix, scope chips (`llm`, `stt`, `stt_stream`, `tts`), allowed models, limits (rate, concurrency, optional daily quota), created date, last used, expiry, a 24-hour usage sparkline, and optional IP allowlist.

**Actions:** create, edit limits and scopes, pause and resume, rotate (new key, old key stays valid for a chosen grace period), revoke (immediate), open snippets.

**Create-key flow**
1. Choose profile (or create one inline).
2. Name the key, tick scopes, choose models (installed only; default all), set rate and concurrency limits, expiry, optional IP allowlist.
3. Confirm. The full key appears **once** with a Copy button and a warning that it cannot be shown again.
4. Immediately below: the snippets tab prefilled with this key (shown only in this view).

**Profile fields:** name, description, owner/contact, notes, disabled flag. Disabling a profile disables all its keys.

### 2.3 Docs & Snippets
- Pick an endpoint (LLM chat, STT batch, STT streaming, TTS HTTP stream, TTS WebSocket) and a model; for TTS also pick a voice.
- Tabs: **Python (httpx)**, **curl**, **JavaScript (fetch)**, and for LLM also **OpenAI SDK** (changing only `base_url` and key).
- Shows: the base URL, request fields, response format (for example "24 kHz, 16-bit, mono PCM"), common errors (`401`, `403`, `404`, `429`, `503`) and what they mean.
- **Copy** button, and **Try it** that runs the call using the admin session (never a stored key).
- Placeholders `{{BASE_URL}}`, `{{API_KEY}}`, `{{MODEL}}`, `{{VOICE}}` are filled from live data (installed models, voices, Settings base URL).

### 2.4 Request Log
Time, key, profile, endpoint, model, status, duration, input and output size, error code. Filters by key, profile, endpoint, status and time. CSV export. **No prompts, transcripts, audio or generated text are stored.**

### 2.5 Settings
Base URL shown in snippets (for example `http://192.168.1.20:8000`), default limits for new keys, usage retention (days), global kill switches (including streaming STT), allowed CORS origins, and the LAN notice.

---

## 3. Snippet Templates

Each template is a file in the repository with placeholders. They are rendered in the UI and tested automatically (section 6).

**TTS: HTTP stream (Python)** (your example, corrected for the gateway and key)

```python
import httpx

BASE_URL = "{{BASE_URL}}"   # for example http://192.168.1.20:8000
API_KEY = "{{API_KEY}}"     # keep it out of source control

url = f"{BASE_URL}/v1/text-to-speech/{{VOICE}}/stream"
payload = {
    "text": "Hello! Welcome to the local TTS studio running fully offline.",
    "model_id": "{{MODEL}}",
    "voice_settings": {"speed": 1},
}
headers = {"Authorization": f"Bearer {API_KEY}"}

with httpx.stream("POST", url, json=payload, headers=headers, timeout=60) as response:
    response.raise_for_status()
    with open("output.pcm", "wb") as f:
        for chunk in response.iter_bytes():
            f.write(chunk)

print("Saved output.pcm (24 kHz, 16-bit PCM mono)")
# Play it: ffplay -f s16le -ar 24000 -ac 1 output.pcm
```

**LLM: streaming chat (Python)**

```python
import json
import httpx

BASE_URL = "{{BASE_URL}}"
API_KEY = "{{API_KEY}}"

payload = {
    "model": "{{MODEL}}",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": True,
}
headers = {"Authorization": f"Bearer {API_KEY}"}

with httpx.stream("POST", f"{BASE_URL}/v1/chat/completions",
                  json=payload, headers=headers, timeout=120) as r:
    r.raise_for_status()
    for line in r.iter_lines():
        if line.startswith("data: ") and line != "data: [DONE]":
            delta = json.loads(line[6:])["choices"][0]["delta"]
            print(delta.get("content", ""), end="", flush=True)
```

**STT: batch transcription (Python)**

```python
import httpx

BASE_URL = "{{BASE_URL}}"
API_KEY = "{{API_KEY}}"
headers = {"Authorization": f"Bearer {API_KEY}"}

with open("audio.wav", "rb") as f:   # 16 kHz mono WAV works best
    r = httpx.post(
        f"{BASE_URL}/v1/audio/transcriptions",
        headers=headers,
        files={"file": ("audio.wav", f, "audio/wav")},
        data={"model": "{{MODEL}}", "temperature": "0"},
        timeout=120,
    )
r.raise_for_status()
print(r.json()["text"])
```

**curl variants** for each of the above, **JavaScript** variants for the LLM and STT calls, and an **OpenAI SDK** variant for chat. The streaming STT WebSocket and TTS WebSocket templates are added when those endpoints are final.

---

## 4. Backend Additions (admin API)

Building on the key tables from the cloud plan.

| Endpoint | Purpose |
|---|---|
| `GET/POST/PATCH/DELETE /v1/admin/profiles` | Profile CRUD; disabling a profile disables its keys |
| `GET/POST /v1/admin/keys` | List (prefix only) and create. Create returns the full key once |
| `PATCH /v1/admin/keys/{id}` | Edit name, scopes, models, limits, expiry, IP allowlist; pause or resume |
| `POST /v1/admin/keys/{id}/rotate` | New key plus grace period for the old one |
| `DELETE /v1/admin/keys/{id}` | Revoke immediately |
| `GET /v1/admin/usage/summary` | Totals and rates for a range, grouped by key, profile, model or endpoint |
| `GET /v1/admin/usage/timeseries` | Chart data in hourly or daily buckets |
| `GET /v1/admin/usage/requests` | Paged request log with filters and CSV export |
| `GET /v1/admin/models/status` | Installed models with loaded or warm state and memory |
| `GET/PUT /v1/admin/settings/dashboard` | Base URL, defaults, retention |

The dashboard also uses the already planned `GET /v1/admin/settings/stt`, `/v1/models/installed`, `/v1/voices` and `/health/ready`.

**Admin sign-in for the UI:** a session cookie (HttpOnly, SameSite) with CSRF protection, created from an admin key or an admin password. Admin endpoints reject caller keys.

---

## 5. Data and Usage Tracking

**Tables**

| Table | Main columns |
|---|---|
| `profiles` | id, name, description, contact, notes, disabled_at, created_at |
| `api_keys` | id, profile_id, name, prefix, hash, scopes, model_allowlist, rate_limit_per_min, max_concurrent, daily_quota, ip_allowlist, expires_at, last_used_at, status, rotated_from, created_at |
| `usage_events` | id, ts, key_id, profile_id, endpoint, model, status, latency_ms, ttfb_ms, input_units, output_units, bytes_in, bytes_out, error_code |
| `usage_hourly` | hour, key_id, endpoint, model, requests, errors, latency histogram, input_units, output_units |
| `admin_audit` | ts, admin, action, target, details (no secrets) |

**How usage is recorded**
- Middleware writes one `usage_events` row per request after the key check. Streaming requests are recorded when the stream ends or disconnects, with time to first byte.
- Units: LLM tokens (from the engine's usage field if present, otherwise estimated and marked), STT audio seconds in, TTS characters in and audio seconds out (bytes ÷ 48,000 for 24 kHz 16-bit mono).
- A background job rolls events into `usage_hourly` and deletes events older than the retention setting.
- Dashboard queries read the rollup table for charts and the raw table only for the log.
- No request or response content is stored.

---

## 6. Phases and Tasks

```
D0 Verify ─▶ D1 Data + admin API ─▶ D2 Usage pipeline ─▶ D3 UI pages ─▶ D4 Snippets ─▶ D5 QA + security ─▶ D6 Rollout
```

| Phase | Work |
|---|---|
| **D0** | Check that the key tables, roles, scopes, limits and the public TTS/LLM/STT endpoints from the cloud plan exist. Tag `pre-dashboard`. Decide LAN bind address |
| **D1** | Profiles table and API, key create/edit/pause/rotate/revoke, admin session login, settings endpoint, audit log |
| **D2** | Usage middleware, units, rollup job, retention job, summary, timeseries and log endpoints, `models/status` |
| **D3** | Developers sidebar group, Overview, API Keys (profile list and key cards, create flow with one-time key display), Request Log, Settings |
| **D4** | Snippet templates, renderer with live placeholders, Docs & Snippets page, Try it through the admin session |
| **D5** | Tests (below), security review, load check of the dashboard queries |
| **D6** | Set the LAN base URL, create the first profile and key for the other project, verify with its real calls, review the log |

| Task | Owner | Depends on |
|---|---|---|
| Preconditions check and tag | ORCH | none |
| Profiles and keys admin API | BE | D0 |
| Admin session, CSRF, audit log | BE | none |
| Usage middleware and rollups | BE | D0 |
| Usage and models-status endpoints | BE | usage middleware |
| Key cards, create flow, profile list | FE | admin API |
| Overview charts and cards | FE | usage endpoints |
| Request log page | FE | usage endpoints |
| Snippet templates and renderer | FE | endpoints final |
| Snippet tests | QA | templates |
| Security and load tests | QA | all above |
| Review of every branch | REV | each branch |

Rules: failing test first, handoff per task in `docs/handoff/<id>.md`, only ORCH merges, no secrets, content or full keys in logs or handoffs.

**Snippet tests:** a test runs each template against a test gateway with a test key and checks that it succeeds. This stops snippets from drifting away from the real API.

---

## 7. Acceptance Checklist

- [ ] An admin can create a profile and a key; the full key appears once and is never shown again.
- [ ] Key cards show status, prefix, scopes, models, limits, last used, expiry and a 24-hour sparkline.
- [ ] Pause, resume, rotate (with grace period) and revoke work, and a revoked key fails on the next request.
- [ ] A key limited to one model or scope is refused for everything else with the correct error.
- [ ] Copying a snippet from the dashboard and running it with a valid key works for TTS, LLM and STT, with no edits except the key.
- [ ] Snippets point to the gateway; engine ports are unreachable from the LAN.
- [ ] Overview numbers match the request log for the same range.
- [ ] Streaming requests are counted with time to first byte; audio seconds and tokens are recorded.
- [ ] No prompts, transcripts, audio, generated text or full keys appear in the database, logs or exports.
- [ ] Caller keys cannot reach any `/v1/admin/...` endpoint or the dashboard.
- [ ] The dashboard stays responsive with 30 days of usage data.
- [ ] Per-key IP allowlist works.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Plain HTTP on the LAN exposes keys to sniffing | Trusted network only, IP allowlists, notice in the UI, optional local HTTPS later |
| Usage tracking slows requests | Write events asynchronously, batch inserts, rollup table for charts |
| Snippets drift from the real API | Automated snippet tests |
| A caller loads a cold model and disturbs others | Warm-state indicator, allowlists, no agent switching by callers, concurrency limits |
| Key leaked in screenshots or logs | One-time display, prefix-only listing, log redaction, rotation with grace period |
| SQLite growth | Hourly rollups and retention job |
| "Profile" meant something else | Confirm the assumption at the start |

---

## 9. Starting Prompts

**ORCH:**
> You are the orchestrator for the developer dashboard. Read `AGENTS.md`, `cloud-migration-plan.md` and `developer-dashboard-plan.md`. Verify D0, tag `pre-dashboard`, create worktrees and `docs/board.md`. Start each agent in its own herdr pane with the model pinned and confirm it in the status line. Do not start D3 until the admin API and usage endpoints exist.

**Builder (BE, FE):**
> Read only your task and the files it lists. Write a failing test first. Never store or log prompts, transcripts, audio, generated text or full keys. Show a full key only in the create response, once. Paste test output into `docs/handoff/<id>.md`.

**QA:**
> Test every checklist item. Run each snippet template against a test gateway. Compare Overview numbers with the request log. Try caller keys on admin routes. Report failures with the exact request.

**REV:**
> You see only the diff, the task and the acceptance checklist. Try to break it: expired key, revoked key, key reuse after rotation grace period, caller key on admin routes, IP outside the allowlist, CSRF on admin actions, very large usage tables, a streaming request cancelled midway.

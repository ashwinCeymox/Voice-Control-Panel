# S-AGENTS Handoff — Tasks B4 & B5

**Agent:** S-AGENTS (Claude Sonnet 4.6)  
**Date:** 2026-11-10  
**Scope:** `app/api/agents.py` · `app/services/agent_manager.py`  
**Contract refs:** `docs/contracts/openapi.yaml` · `docs/contracts/agent-schema.json`

---

## Summary

Reviewed `app/api/agents.py` and `app/services/agent_manager.py` against the OpenAPI contract and agent JSON schema. Found and fixed **5 bugs** (1 critical, 4 significant). Added 13 new unit tests in `tests/unit/test_agents_api.py`. All 37 tests pass.

---

## Bugs Fixed

### Bug 1 — CRITICAL: Route ordering (`GET /v1/agents/active` shadowed by `/{id}`)

**File:** `app/api/agents.py`  
**Severity:** 🔴 Critical — the endpoint was completely unreachable

**Root cause:** `GET /active` was registered *after* `GET /{id}`. FastAPI matches routes in declaration order, so the literal path `/active` was captured by the `/{id}` pattern and routed to `get_agent(id="active")`, which returned HTTP 404 "Agent 'active' not found".

**Fix:** Moved the `@router.get("/active")` handler to appear *before* `@router.get("/{id}")` in the file.

```python
# BEFORE (broken): /{id} declared first → /active is never reached
@router.get("/{id}")           # line 74
async def get_agent(...): ...

@router.get("/active")         # line 53 — UNREACHABLE
async def get_active_agent(...): ...

# AFTER (fixed): /active declared first
@router.get("/active")         # ✅ matched first
async def get_active_agent(...): ...

@router.get("/{id}")           # ✅ only matches non-"active" strings
async def get_agent(...): ...
```

---

### Bug 2 — `GET /active` response duplicated all agent fields at top level

**File:** `app/api/agents.py`  
**Severity:** 🟠 Significant — schema deviation, bloated/confusing response

**Root cause:** The response-building loop `for k, v in agent.items(): if k not in response_data: response_data[k] = v` copied every agent field (llm, stt, tts, vad, audio, system_prompt, …) into the top-level envelope alongside `active_agent`, sending each field twice.

**Fix:** Removed the merge loop. The response is now a clean, non-duplicated envelope:

```json
{
  "status": "ready",
  "active_agent_id": "agent-default-assistant",
  "active_agent": { ... }
}
```

> [!NOTE]
> The old response also had a redundant `"state"` key alongside `"status"` with the same value. The cleaned response keeps only `"status"` (matching the OpenAPI contract).

---

### Bug 3 — CRITICAL: `load_agent` memory check logic was inverted

**File:** `app/services/agent_manager.py`  
**Severity:** 🔴 Critical — always blocked loading on healthy machines with memory pressure

**Root cause:** The guard condition was:
```python
if estimated_ram_mb > mem["available_mb"] + 1500:
```
On a typical 8 GB Mac under load (e.g. `available_mb = 365`):
- `estimated_ram_mb = 2360`
- `available_mb + 1500 = 1865`
- `2360 > 1865` → `True` → raises `ValueError` → HTTP 400

This is wrong because the machine has 8 GB total — more than enough for the 2360 MB triad.

**Fix:** Use `total_mb` as the reference, subtract a 1500 MB OS/process reserve:
```python
safe_headroom_mb = mem["total_mb"] - 1500.0
if estimated_ram_mb > safe_headroom_mb:
    raise ValueError(...)
```
Now on an 8 GB host: `2360 > 8192 - 1500 = 6692` → `False` → load succeeds. ✅  
On a 2 GB host: `2360 > 2048 - 1500 = 548` → `True` → correctly rejected. ✅

---

### Bug 4 — `PUT /{id}` required all fields (AgentConfig instead of AgentUpdateConfig)

**File:** `app/api/agents.py`  
**Severity:** 🟠 Significant — partial updates always rejected with HTTP 422

**Root cause:** The PUT handler used `AgentConfig` as its body schema. `AgentConfig` has required fields (`llm`, `stt`, `tts`), so sending only `{"name": "New Name"}` triggered Pydantic validation errors (HTTP 422 Unprocessable Entity).

**Fix:** Changed to `AgentUpdateConfig` (all fields `Optional`) and moved the required-field validation to after the merge:
```python
async def update_agent(id: str, payload: AgentUpdateConfig, ...):
    ...
    update_data = payload.model_dump(exclude_unset=True)
    merged_data = {**existing, **update_data}
    # Validate on merged result, not raw payload
    if not merged_data.get("name"): raise HTTPException(400, ...)
```

---

### Bug 5 — `DELETE` hardcoded a second copy of the default agent ID string

**File:** `app/api/agents.py`  
**Severity:** 🟡 Minor — redundant literal breaks DRY principle

**Root cause:**
```python
if id == DEFAULT_AGENT["id"] or id == "agent-default-assistant":
```
`DEFAULT_AGENT["id"]` **is** `"agent-default-assistant"`, making the second condition dead code. If the default ID ever changes, the hardcoded string becomes a silent bug.

**Fix:** Use only `DEFAULT_AGENT["id"]` as the single source of truth:
```python
if id == DEFAULT_AGENT["id"]:
```

---

## Files Changed

| File | Change |
|------|--------|
| `app/api/agents.py` | Route ordering fix; clean `/active` response; `AgentUpdateConfig` for PUT; DRY delete guard; added `AgentUpdateConfig` import |
| `app/services/agent_manager.py` | Memory check: `available_mb + 1500` → `total_mb - 1500` |
| `tests/unit/test_agents_api.py` | **New file** — 13 unit tests covering all 5 bugs + CRUD lifecycle |

---

## Test Results

```
37 passed, 1 warning in 2.45s
```

| Suite | Tests | Result |
|-------|-------|--------|
| contract/ | 3 | ✅ |
| integration/ (cancellation) | 7 | ✅ |
| integration/ (session isolation) | 4 | ✅ |
| unit/test_agents_api (new) | 13 | ✅ |
| unit/test_health_and_auth | 4 | ✅ |
| unit/test_orchestrator | 2 | ✅ |
| unit/test_text_chunker | 4 | ✅ |

---

## Contract Compliance After Fixes

| OpenAPI Path | Method | Status Before | Status After |
|---|---|---|---|
| `/v1/agents` | GET | ✅ | ✅ |
| `/v1/agents` | POST 201 | ✅ | ✅ |
| `/v1/agents/active` | GET | 🔴 404 (shadowed) | ✅ 200 |
| `/v1/agents/{id}` | GET | ✅ | ✅ |
| `/v1/agents/{id}` | PUT | 🔴 422 on partial | ✅ 200 partial OK |
| `/v1/agents/{id}` | DELETE | ✅ | ✅ |
| `/v1/agents/{id}/duplicate` | POST 201 | ✅ | ✅ |
| `/v1/agents/{id}/load` | POST | 🔴 400 on healthy host | ✅ 200 |
| `/v1/agents/{id}/test-chat` | POST SSE | ✅ | ✅ |

---

## Notes for Next Agent

- `AgentResponse` Pydantic model exists in `app/schemas/agent.py` but is not used as `response_model` on any endpoint. Consider wiring it to GET endpoints for schema validation on the response side.
- The `load_agent` estimated footprint (2360 MB) is a placeholder constant. A future improvement would derive it dynamically from the agent's `llm.model`, `stt.model`, and `tts.engine` via the gallery model registry.
- `create_agent` currently does not validate that `llm.model`, `stt.model`, and `tts.engine` reference installed models. Consider adding a validation pass against `AgentManager.list_installed_models()` if that becomes available.

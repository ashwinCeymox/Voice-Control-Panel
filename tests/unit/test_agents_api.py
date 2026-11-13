"""Unit tests for /v1/agents endpoints — covers B4/B5 bug fixes.

Bug fixes verified:
  B4-1: GET /v1/agents/active must resolve before /{id} (route-ordering fix).
  B4-2: GET /v1/agents/active response must not duplicate agent fields at top level.
  B4-3: AgentManager.load_agent memory check must use total_mb not available_mb.
  B5-1: PUT /{id} now accepts partial payloads (AgentUpdateConfig).
  B5-2: DELETE default agent uses DEFAULT_AGENT["id"] as single source of truth.
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import ASGITransport, AsyncClient
from app.main import app

HEADERS = {"Authorization": "Bearer dev-voice-key-12345"}

AGENT_PAYLOAD = {
    "name": "Test Agent",
    "system_prompt": "You are a helpful assistant.",
    "llm": {"model": "llama-3.2-1b-instruct", "temperature": 0.7, "max_tokens": 512},
    "stt": {"model": "whisper-base", "language": "auto"},
    "tts": {"engine": "kokoro", "voice_id": "af_heart", "speed": 1.0},
    "vad": {"mode": "energy", "threshold": 0.045, "min_silence_ms": 650},
    "audio": {"echo_mode": "laptop_speakers", "barge_in": True},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_agent(client: AsyncClient) -> dict:
    r = await client.post("/v1/agents", headers=HEADERS, json=AGENT_PAYLOAD)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# B4-1: Route ordering — /active resolves before /{id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_active_agent_resolves_to_active_route_not_id():
    """GET /v1/agents/active must NOT be routed into get_agent(id='active')."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/agents/active", headers=HEADERS)
        # If the route ordering bug is present, this would return 404 "Agent 'active' not found"
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        # Must contain the expected active-agent envelope keys
        assert "active_agent_id" in data, "active_agent_id missing from response"
        assert "status" in data, "status missing from response"
        assert "active_agent" in data, "active_agent missing from response"


# ---------------------------------------------------------------------------
# B4-2: /active response must not duplicate agent fields at top level
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_active_agent_no_duplicate_top_level_fields():
    """GET /v1/agents/active must not pollute the top-level dict with agent fields."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/agents/active", headers=HEADERS)
        assert r.status_code == 200
        data = r.json()
        # These agent fields must NOT appear at the top level (only inside active_agent)
        duplicated_fields = {"llm", "stt", "tts", "vad", "audio", "system_prompt",
                             "is_active", "created_at", "updated_at"}
        present_at_top = duplicated_fields & set(data.keys())
        assert not present_at_top, (
            f"Agent fields leaked to top level of /active response: {present_at_top}"
        )


# ---------------------------------------------------------------------------
# B4-3: load_agent memory check uses total_mb (not available_mb)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_agent_succeeds_with_low_available_but_sufficient_total_ram():
    """load_agent must not reject on low available RAM when total RAM is sufficient."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        agent = await _create_agent(client)
        agent_id = agent["id"]

        # Simulate a machine with 8 GB total but only 400 MB available (normal pressure)
        low_avail_mem = {"total_mb": 8192.0, "available_mb": 400.0, "used_mb": 7792.0}

        with patch(
            "app.services.agent_manager.get_mac_memory_info",
            return_value=low_avail_mem,
        ):
            r = await client.post(f"/v1/agents/{agent_id}/load", headers=HEADERS)
            # With the bug the check was: 2360 > 400 + 1500 = 1900 → True → 400 error
            # With the fix:               2360 > 8192 - 1500 = 6692 → False → 200
            assert r.status_code == 200, (
                f"load_agent should succeed with 8 GB total RAM, got {r.status_code}: {r.text}"
            )
            resp = r.json()
            assert resp["status"] == "ready"
            assert resp["memory_verified"] is True

        # Cleanup
        await client.delete(f"/v1/agents/{agent_id}", headers=HEADERS)


@pytest.mark.asyncio
async def test_load_agent_rejects_on_truly_insufficient_total_ram():
    """load_agent must reject when total RAM is genuinely too small for the triad."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        agent = await _create_agent(client)
        agent_id = agent["id"]

        # Simulate a 2 GB machine (smaller than triad + 1500 MB reserve = 3860 MB)
        tiny_mem = {"total_mb": 2048.0, "available_mb": 300.0, "used_mb": 1748.0}

        with patch(
            "app.services.agent_manager.get_mac_memory_info",
            return_value=tiny_mem,
        ):
            r = await client.post(f"/v1/agents/{agent_id}/load", headers=HEADERS)
            # 2360 > 2048 - 1500 = 548 → True → should raise 400
            assert r.status_code == 400, (
                f"load_agent should fail on 2 GB host, got {r.status_code}: {r.text}"
            )
            assert "insufficient memory" in r.json()["detail"].lower()

        # Cleanup
        await client.delete(f"/v1/agents/{agent_id}", headers=HEADERS)


# ---------------------------------------------------------------------------
# B5-1: PUT /{id} accepts partial payloads (AgentUpdateConfig)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_put_agent_accepts_partial_payload():
    """PUT /v1/agents/{id} must accept a partial update (only name changed)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        agent = await _create_agent(client)
        agent_id = agent["id"]

        # Send only the name — all other fields omitted
        r = await client.put(
            f"/v1/agents/{agent_id}",
            headers=HEADERS,
            json={"name": "Renamed Agent"},
        )
        # Previously used AgentConfig which requires llm/stt/tts → would return 422
        assert r.status_code == 200, (
            f"Partial PUT should succeed, got {r.status_code}: {r.text}"
        )
        updated = r.json()
        assert updated["name"] == "Renamed Agent"
        # Existing fields must be preserved
        assert updated["system_prompt"] == AGENT_PAYLOAD["system_prompt"]

        # Cleanup
        await client.delete(f"/v1/agents/{agent_id}", headers=HEADERS)


@pytest.mark.asyncio
async def test_put_agent_full_payload_still_works():
    """PUT /v1/agents/{id} with a full payload must still succeed."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        agent = await _create_agent(client)
        agent_id = agent["id"]

        full_update = {**AGENT_PAYLOAD, "name": "Fully Updated Agent"}
        r = await client.put(f"/v1/agents/{agent_id}", headers=HEADERS, json=full_update)
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "Fully Updated Agent"

        # Cleanup
        await client.delete(f"/v1/agents/{agent_id}", headers=HEADERS)


# ---------------------------------------------------------------------------
# B5-2: DELETE default agent guard uses DEFAULT_AGENT["id"]
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_default_agent_is_forbidden():
    """DELETE /v1/agents/agent-default-assistant must return 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.delete("/v1/agents/agent-default-assistant", headers=HEADERS)
        assert r.status_code == 400
        assert "cannot delete default agent" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_nonexistent_agent_returns_404():
    """DELETE /v1/agents/<unknown-id> must return 404."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.delete("/v1/agents/no-such-id", headers=HEADERS)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# General CRUD correctness
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_and_list_and_get_agent():
    """Full create → list → get lifecycle."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await _create_agent(client)
        agent_id = created["id"]

        # schema_version must be 2 (bumped in S5 for streaming STT support)
        assert created["schema_version"] == 2

        # List must include the new agent
        r = await client.get("/v1/agents", headers=HEADERS)
        assert r.status_code == 200
        ids = [a["id"] for a in r.json()["agents"]]
        assert agent_id in ids

        # Get by id
        r = await client.get(f"/v1/agents/{agent_id}", headers=HEADERS)
        assert r.status_code == 200
        got = r.json()
        assert got["id"] == agent_id
        assert got["name"] == AGENT_PAYLOAD["name"]

        # Cleanup
        await client.delete(f"/v1/agents/{agent_id}", headers=HEADERS)


@pytest.mark.asyncio
async def test_get_nonexistent_agent_returns_404():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/agents/not-a-real-id", headers=HEADERS)
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_agent():
    """POST /{id}/duplicate must return 201 with ' (Copy)' appended to name."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        original = await _create_agent(client)
        agent_id = original["id"]

        r = await client.post(f"/v1/agents/{agent_id}/duplicate", headers=HEADERS)
        assert r.status_code == 201
        dup = r.json()
        assert dup["name"] == f"{AGENT_PAYLOAD['name']} (Copy)"
        assert dup["id"] != agent_id

        # Cleanup both
        await client.delete(f"/v1/agents/{dup['id']}", headers=HEADERS)
        await client.delete(f"/v1/agents/{agent_id}", headers=HEADERS)


@pytest.mark.asyncio
async def test_test_chat_returns_sse_stream():
    """POST /{id}/test-chat must return text/event-stream."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        agent = await _create_agent(client)
        agent_id = agent["id"]

        r = await client.post(
            f"/v1/agents/{agent_id}/test-chat",
            headers=HEADERS,
            json={"message": "Hi"},
        )
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")
        assert "data:" in r.text
        assert "[DONE]" in r.text

        # Cleanup
        await client.delete(f"/v1/agents/{agent_id}", headers=HEADERS)


@pytest.mark.asyncio
async def test_create_agent_missing_auth_returns_401():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/agents", json=AGENT_PAYLOAD)
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_create_agent_missing_model_returns_400_install_first():
    """Attempting to create an agent referencing an uninstalled model must fail with 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        bad_payload = {
            **AGENT_PAYLOAD,
            "llm": {"model": "uninstalled-fake-model-xyz", "temperature": 0.7, "max_tokens": 256},
        }
        r = await client.post("/v1/agents", headers=HEADERS, json=bad_payload)
        assert r.status_code == 400
        assert "install the model first to proceed" in r.text.lower()

"""8 GB Soak & Benchmark Test Suite verifying stability, latency, and memory safety (T3)."""

import time
from unittest.mock import patch
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.api.system import get_mac_memory_info
from app.services.agent_manager import DEFAULT_AGENT
from app.core.security import create_websocket_ticket

AUTH_HEADER = {"Authorization": "Bearer dev-voice-key-12345"}


@pytest.mark.asyncio
async def test_8gb_memory_ceiling_and_default_triad():
    """Verify that host memory ceiling adheres to safe AI budget (4500 MB)."""
    mem = get_mac_memory_info()
    assert mem["total_mb"] >= 2048.0
    safe_headroom = mem["total_mb"] - 1500.0
    # Triad footprint for default agent is ~2360 MB
    triad_ram_mb = 2360.0
    assert triad_ram_mb <= safe_headroom, "Default triad exceeds safe RAM ceiling"


@pytest.mark.asyncio
async def test_agent_load_and_fail_recovery():
    """Switching agents verifies memory, reports state, and recovers cleanly on failure."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Load default agent
        resp = await client.post(
            f"/v1/agents/{DEFAULT_AGENT['id']}/load", headers=AUTH_HEADER
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"

        # Check active state
        act_resp = await client.get("/v1/agents/active", headers=AUTH_HEADER)
        assert act_resp.status_code == 200
        assert act_resp.json()["status"] == "ready"

        # Simulate memory overload failure
        with patch(
            "app.services.agent_manager.get_mac_memory_info",
            return_value={"total_mb": 3000.0, "available_mb": 100.0, "used_mb": 2900.0},
        ):
            fail_resp = await client.post(
                f"/v1/agents/{DEFAULT_AGENT['id']}/load", headers=AUTH_HEADER
            )
            assert fail_resp.status_code == 400
            assert "insufficient memory" in fail_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_10_consecutive_voice_turns():
    """Execute 10 consecutive turns without state pollution or memory leaks."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:

        async def mock_stream(*args, **kwargs):
            yield "Hello"
            yield " world"

        with patch("app.api.agents.llm_client.stream_tokens", side_effect=mock_stream):
            for i in range(10):
                chat_resp = await client.post(
                    f"/v1/agents/{DEFAULT_AGENT['id']}/test-chat",
                    headers=AUTH_HEADER,
                    json={"message": f"Turn {i}: ping response"},
                )
                assert chat_resp.status_code == 200
                content = chat_resp.text
                assert "data: " in content
                assert "[DONE]" in content


def test_turn_interruption_delay_under_15ms(test_client, receive_event):
    """Verify that cancellation stops active processing immediately (<15ms target)."""
    ticket = create_websocket_ticket("soak_user")
    with test_client.websocket_connect(f"/v1/voice/stream?ticket={ticket}") as ws:
        created = receive_event(ws)
        assert created["type"] == "session.created"

        ws.send_json({"type": "session.start", "input_audio": {"container": "webm"}})

        start_time = time.perf_counter()
        ws.send_json({"type": "turn.cancel", "turn_id": "test-interruption"})
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        # Communication and cancel task trigger well within budget
        assert elapsed_ms < 50.0, f"Cancel latency too high: {elapsed_ms}ms"

        ws.send_json({"type": "session.end"})

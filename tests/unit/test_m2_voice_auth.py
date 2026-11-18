"""
Unit tests for Task M2-VOICE-AUTH: Direct API key auth on WS /v1/voice/stream.
Verifies ticket auth, Bearer auth, xi-api-key auth, query param auth, scope checks,
rejections (4401/4403), and usage tracking.
"""

import asyncio
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.core.security import create_websocket_ticket
from app.services.key_manager import KeyManager
from app.storage.database import init_db


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    await init_db()


@pytest.mark.asyncio
async def test_ws_voice_auth_ticket_backward_compatibility():
    """Browser single-use ticket continues to authenticate successfully."""
    ticket = create_websocket_ticket("test-browser-user")
    client = TestClient(app)
    with client.websocket_connect(f"/v1/voice/stream?ticket={ticket}") as ws:
        data = ws.receive_json()
        assert data["type"] == "session.created"
        assert "session_id" in data


@pytest.mark.asyncio
async def test_ws_voice_auth_bearer_header_with_voice_scope():
    """Bearer token with 'voice' scope authenticates without a ticket."""
    key_info = await KeyManager.create_key(
        name="telephony-client",
        role="caller",
        scopes=["voice"],
    )
    plain_key = key_info["key"]

    client = TestClient(app)
    with client.websocket_connect(
        "/v1/voice/stream",
        headers={"Authorization": f"Bearer {plain_key}"},
    ) as ws:
        data = ws.receive_json()
        assert data["type"] == "session.created"
        assert "session_id" in data


@pytest.mark.asyncio
async def test_ws_voice_auth_xi_api_key_header():
    """xi-api-key header authenticates with 'voice_stream' scope."""
    key_info = await KeyManager.create_key(
        name="sip-bridge",
        role="caller",
        scopes=["voice_stream"],
    )
    plain_key = key_info["key"]

    client = TestClient(app)
    with client.websocket_connect(
        "/v1/voice/stream",
        headers={"xi-api-key": plain_key},
    ) as ws:
        data = ws.receive_json()
        assert data["type"] == "session.created"


@pytest.mark.asyncio
async def test_ws_voice_auth_query_api_key():
    """api_key query parameter authenticates successfully."""
    key_info = await KeyManager.create_key(
        name="query-caller",
        role="caller",
        scopes=["voice"],
    )
    plain_key = key_info["key"]

    client = TestClient(app)
    with client.websocket_connect(f"/v1/voice/stream?api_key={plain_key}") as ws:
        data = ws.receive_json()
        assert data["type"] == "session.created"


@pytest.mark.asyncio
async def test_ws_voice_auth_admin_key_bypasses_scope():
    """Admin key can connect without explicit 'voice' scope."""
    key_info = await KeyManager.create_key(
        name="admin-ops",
        role="admin",
        scopes=["admin"],
    )
    plain_key = key_info["key"]

    client = TestClient(app)
    with client.websocket_connect(
        "/v1/voice/stream",
        headers={"Authorization": f"Bearer {plain_key}"},
    ) as ws:
        data = ws.receive_json()
        assert data["type"] == "session.created"


@pytest.mark.asyncio
async def test_ws_voice_auth_missing_credentials_rejected():
    """Connecting with no ticket and no API key is rejected."""
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/v1/voice/stream"):
            pass
    assert exc.value.code in (4401, 1008, 403)


@pytest.mark.asyncio
async def test_ws_voice_auth_invalid_key_rejected():
    """Connecting with an invalid API key is rejected with 4401."""
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            "/v1/voice/stream",
            headers={"Authorization": "Bearer va-invalid-key-12345"},
        ):
            pass
    assert exc.value.code in (4401, 1008)


@pytest.mark.asyncio
async def test_ws_voice_auth_missing_voice_scope_rejected():
    """Caller key without 'voice' or 'voice_stream' scope is rejected with 4403."""
    key_info = await KeyManager.create_key(
        name="stt-only-client",
        role="caller",
        scopes=["stt", "llm"],  # Missing voice/voice_stream
    )
    plain_key = key_info["key"]

    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            "/v1/voice/stream",
            headers={"Authorization": f"Bearer {plain_key}"},
        ):
            pass
    assert exc.value.code in (4403, 1008)


@pytest.mark.asyncio
async def test_ws_voice_usage_logged_for_api_key():
    """Disconnecting after session logs usage record for caller key."""
    key_info = await KeyManager.create_key(
        name="usage-tracked-caller",
        role="caller",
        scopes=["voice"],
    )
    plain_key = key_info["key"]

    client = TestClient(app)
    with client.websocket_connect(
        "/v1/voice/stream",
        headers={"Authorization": f"Bearer {plain_key}"},
    ) as ws:
        _ = ws.receive_json()  # session.created

    # Allow async server cleanup to record usage entry
    await asyncio.sleep(0.05)

    summary = await KeyManager.get_usage_summary()
    matching = [entry for entry in summary if entry["key_id"] == key_info["id"]]
    assert len(matching) >= 1
    assert matching[0]["endpoint"] == "/v1/voice/stream"

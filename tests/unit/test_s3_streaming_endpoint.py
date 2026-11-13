"""
Unit tests for S3: Protocol & Public Streaming STT Endpoint (WS /v1/audio/transcriptions/stream).
Follows TDD strictly.
"""
import asyncio
import json
import time
from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient
from app.main import app
from app.core.config import settings
from app.core.security import create_websocket_ticket
from app.storage.database import get_db


@pytest.fixture
def sync_client():
    return TestClient(app)


def test_ws_stream_missing_auth(sync_client):
    """Connecting without ticket or auth header must receive error and be disconnected."""
    with sync_client.websocket_connect("/v1/audio/transcriptions/stream") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "unauthorized" in msg["message"].lower()


def test_ws_stream_invalid_scope(sync_client):
    """Caller key without stt_stream scope must be rejected."""
    # Create ticket for a user with only 'llm' scope or test with ticket
    # Ticket for default user without stt_stream scope
    ticket = create_websocket_ticket("unauthorized-user")
    with sync_client.websocket_connect(f"/v1/audio/transcriptions/stream?ticket={ticket}") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "scope" in msg["message"].lower() or "forbidden" in msg["message"].lower()


def test_ws_stream_success_with_admin_ticket(sync_client):
    """Admin ticket or valid key streams audio and receives partial and final events."""
    ticket = create_websocket_ticket("admin")

    with sync_client.websocket_connect(f"/v1/audio/transcriptions/stream?ticket={ticket}") as ws:
        # Handshake
        ws.send_json({
            "type": "stream.start",
            "sample_rate": 16000,
            "format": "pcm_s16le",
        })

        # Send 2 binary audio frames (silence)
        dummy_frame = b"\x00\x00" * 1600  # 100ms at 16kHz
        ws.send_bytes(dummy_frame)
        ws.send_bytes(dummy_frame)

        # Commit speech
        ws.send_json({"type": "audio.commit"})

        # Wait for completion event
        received_types = []
        while True:
            msg = ws.receive_json()
            received_types.append(msg.get("type"))
            if msg.get("type") in ("transcript.completed", "transcript.final", "error"):
                break

        assert "transcript.completed" in received_types or "transcript.final" in received_types


@pytest.mark.asyncio
async def test_admin_stt_settings_endpoints():
    """Verify GET and PUT /v1/admin/settings/stt."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin_headers = {"Authorization": "Bearer dev-voice-key-12345"}

        # 1. Get current settings
        get_resp = await client.get("/v1/admin/settings/stt", headers=admin_headers)
        assert get_resp.status_code == 200
        settings_data = get_resp.json()
        assert "stt_stream_enabled" in settings_data
        assert "default_kind" in settings_data

        # 2. Update settings (kill switch toggle)
        put_resp = await client.put(
            "/v1/admin/settings/stt",
            headers=admin_headers,
            json={
                "stt_stream_enabled": False,
                "default_kind": "batch",
                "max_active_streams": 10,
            },
        )
        assert put_resp.status_code == 200
        updated = put_resp.json()
        assert updated["stt_stream_enabled"] is False
        assert updated["max_active_streams"] == 10

        # Reset kill switch
        await client.put(
            "/v1/admin/settings/stt",
            headers=admin_headers,
            json={"stt_stream_enabled": True, "default_kind": "batch"},
        )

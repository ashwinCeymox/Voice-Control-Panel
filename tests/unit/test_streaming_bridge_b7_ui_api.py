"""Unit tests for Phase B7: Admin Settings Kill Switch and Installed Models Transports."""

import pytest
from unittest.mock import patch
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.core.config import settings


@pytest.mark.asyncio
async def test_admin_stt_settings_kill_switch_read_and_update():
    """Verify runtime kill switch for streaming bridge in admin STT settings."""
    transport = ASGITransport(app=app)
    admin_headers = {"Authorization": "Bearer dev-voice-key-12345"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Read settings
        resp = await client.get("/v1/admin/settings/stt", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "streaming_bridge_enabled" in data
        original_state = data["streaming_bridge_enabled"]

        # 2. Toggle kill switch to True
        resp_put = await client.put(
            "/v1/admin/settings/stt",
            headers=admin_headers,
            json={"streaming_bridge_enabled": True},
        )
        assert resp_put.status_code == 200
        assert resp_put.json()["streaming_bridge_enabled"] is True
        assert settings.STREAMING_BRIDGE_ENABLED is True

        # 3. Toggle kill switch back to original
        resp_put2 = await client.put(
            "/v1/admin/settings/stt",
            headers=admin_headers,
            json={"streaming_bridge_enabled": original_state},
        )
        assert resp_put2.status_code == 200
        assert resp_put2.json()["streaming_bridge_enabled"] == original_state
        assert settings.STREAMING_BRIDGE_ENABLED == original_state


@pytest.mark.asyncio
async def test_installed_models_exposes_streaming_mode_and_transports():
    """Verify GET /v1/models/installed returns streaming_mode and transports for STT models."""
    transport = ASGITransport(app=app)
    admin_headers = {"Authorization": "Bearer dev-voice-key-12345"}

    with patch.object(settings, "STREAMING_BRIDGE_ENABLED", False):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/models/installed", headers=admin_headers)
            assert resp.status_code == 200
            data = resp.json()
            stt_models = data.get("stt", [])
            assert len(stt_models) > 0

            for m in stt_models:
                assert "transports" in m
                assert "streaming_mode" in m
                if m.get("provider") == "stream-stt" or "sherpa" in m.get("id", "").lower():
                    assert m["streaming_mode"] == "native"
                    assert "ws_stream_native" in m["transports"]
                else:
                    # When bridge is disabled, LocalAI STT models have mode none and only batch_http
                    assert m["streaming_mode"] == "none"
                    assert m["transports"] == ["batch_http"]

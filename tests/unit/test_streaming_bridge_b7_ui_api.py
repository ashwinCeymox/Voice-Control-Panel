"""Unit tests for Phase S3: Admin STT Native Kill Switch & Installed Models Transports."""

import pytest
from unittest.mock import patch
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.core.config import settings


@pytest.mark.asyncio
async def test_admin_stt_settings_kill_switch_read_and_update():
    """Verify runtime kill switch for STT_STREAM_NATIVE_ENABLED in admin STT settings."""
    transport = ASGITransport(app=app)
    admin_headers = {"Authorization": "Bearer dev-voice-key-12345"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/admin/settings/stt", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "stt_stream_native_enabled" in data
        assert "streaming_bridge_enabled" in data
        orig_native = settings.STT_STREAM_NATIVE_ENABLED
        orig_bridge = settings.STREAMING_BRIDGE_ENABLED

        try:
            resp_put = await client.put(
                "/v1/admin/settings/stt",
                headers=admin_headers,
                json={"stt_stream_native_enabled": True},
            )
            assert resp_put.status_code == 200
            assert resp_put.json()["stt_stream_native_enabled"] is True
            assert settings.STT_STREAM_NATIVE_ENABLED is True

            resp_put2 = await client.put(
                "/v1/admin/settings/stt",
                headers=admin_headers,
                json={"stt_stream_native_enabled": False},
            )
            assert resp_put2.status_code == 200
            assert resp_put2.json()["stt_stream_native_enabled"] is False
            assert settings.STT_STREAM_NATIVE_ENABLED is False
        finally:
            settings.STT_STREAM_NATIVE_ENABLED = orig_native
            settings.STREAMING_BRIDGE_ENABLED = orig_bridge


@pytest.mark.asyncio
async def test_installed_models_exposes_native_transports_never_simulated():
    """Verify GET /v1/models/installed never emits 'simulated' and preserves flag-off baseline."""
    transport = ASGITransport(app=app)
    admin_headers = {"Authorization": "Bearer dev-voice-key-12345"}

    with (
        patch.object(settings, "STT_STREAM_NATIVE_ENABLED", False),
        patch.object(settings, "STREAMING_BRIDGE_ENABLED", False),
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/models/installed", headers=admin_headers)
            assert resp.status_code == 200
            data = resp.json()
            stt_models = data.get("stt", [])
            assert len(stt_models) > 0

            for m in stt_models:
                assert "transports" in m
                assert "streaming_mode" in m
                assert m["streaming_mode"] != "simulated"
                assert "ws_stream_bridge" not in m["transports"]
                if m.get("provider") == "stream-stt" or "sherpa" in m.get("id", "").lower():
                    assert m["streaming_mode"] == "native"
                    assert "ws_stream_native" in m["transports"]
                    assert m.get("streaming_backend") == "sherpa"
                else:
                    assert m["streaming_mode"] == "none"
                    assert m["transports"] == ["batch_http"]

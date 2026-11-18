"""Regression and safety tests for TTS concurrency serialization and probe hygiene."""

import asyncio
import pytest
from unittest.mock import patch, MagicMock
from app.providers.localtts_client import LocalTTSClient
from app.services.model_registry import model_registry
from app.schemas.voice import Voice, VoiceSettings


@pytest.mark.asyncio
async def test_tts_client_serializes_concurrent_requests():
    """Verify that _tts_gate enforces single-threaded serialization to local-tts."""
    client = LocalTTSClient(http_base_url="http://127.0.0.1:8880")
    active_concurrent = 0
    max_concurrent_seen = 0

    async def mock_post(*args, **kwargs):
        nonlocal active_concurrent, max_concurrent_seen
        active_concurrent += 1
        max_concurrent_seen = max(max_concurrent_seen, active_concurrent)
        await asyncio.sleep(0.05)
        active_concurrent -= 1
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"\x00\x00" * 240
        return mock_resp

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        tasks = [client.synthesize_http(text=f"Request {i}") for i in range(5)]
        results = await asyncio.gather(*tasks)

    assert len(results) == 5
    assert max_concurrent_seen == 1, (
        "Concurrency gate failed: more than 1 request executed concurrently!"
    )


@pytest.mark.asyncio
async def test_probe_tts_engine_does_not_synthesize_audio():
    """Verify probe_tts_engine inspects /v1/models and never performs audio synthesis."""
    model_registry._tts_availability_cache.clear()

    mock_models_resp = MagicMock()
    mock_models_resp.status_code = 200
    mock_models_resp.json.return_value = [
        {"model_id": "kokoro", "name": "Kokoro"},
        {"model_id": "pocket", "name": "Pocket"},
    ]

    with (
        patch("httpx.AsyncClient.get", return_value=mock_models_resp) as mock_get,
        patch("httpx.AsyncClient.post") as mock_post,
    ):
        is_avail, reason = await model_registry.probe_tts_engine("kokoro")
        assert is_avail is True
        assert reason is None

        # Verify GET /v1/models was called
        mock_get.assert_called_once()
        assert "/v1/models" in mock_get.call_args[0][0]

        # Verify POST (synthesis) was NEVER called
        mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_probe_tts_engine_reports_unregistered_model():
    """Verify probe_tts_engine cleanly flags unregistered engines without crashing."""
    model_registry._tts_availability_cache.clear()

    mock_models_resp = MagicMock()
    mock_models_resp.status_code = 200
    mock_models_resp.json.return_value = [
        {"model_id": "kokoro", "name": "Kokoro"},
    ]

    with patch("httpx.AsyncClient.get", return_value=mock_models_resp):
        is_avail, reason = await model_registry.probe_tts_engine("non_existent_engine")
        assert is_avail is False
        assert reason is not None
        assert "not installed" in reason or "not found" in reason


@pytest.mark.asyncio
async def test_list_voices_cache_fallback_on_engine_drop():
    """Verify list_voices returns cached voices when local-tts connection drops."""
    client = LocalTTSClient(http_base_url="http://127.0.0.1:8880")
    LocalTTSClient._cached_voices = [
        Voice(
            voice_id="af_heart",
            name="Heart",
            category="female",
            settings=VoiceSettings(speed=1.0),
        )
    ]

    with patch("httpx.AsyncClient.get", side_effect=Exception("Connection refused")):
        voices = await client.list_voices(model_id="kokoro")

    assert len(voices) == 1
    assert voices[0].voice_id == "af_heart"

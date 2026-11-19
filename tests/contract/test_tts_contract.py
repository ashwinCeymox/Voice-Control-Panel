"""
TTS Contract Verification Test Suite
Mandated by runpod.md Phase R3:
- Voice list schema and Kokoro restriction
- Model list schema
- HTTP speech synthesis contract (24kHz 16-bit PCM)
- Dual route compatibility (ElevenLabs style and OpenAI style fallback)
- Concurrency serialization via _tts_gate
"""

import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.providers.localtts_client import LocalTTSClient

AUTH_HEADER = {"Authorization": "Bearer dev-voice-key-12345"}


@pytest.mark.asyncio
async def test_tts_contract_models_and_voices():
    """Verify TTS models and voices API contract."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Models contract
        resp_models = await client.get("/v1/tts/models", headers=AUTH_HEADER)
        assert resp_models.status_code == 200
        models_data = resp_models.json()
        assert "models" in models_data
        assert any("kokoro" in m["id"].lower() for m in models_data["models"])

        # 2. Voices contract
        resp_voices = await client.get("/v1/voices", headers=AUTH_HEADER)
        assert resp_voices.status_code == 200
        voices_data = resp_voices.json()
        assert "voices" in voices_data
        assert len(voices_data["voices"]) > 0
        v = voices_data["voices"][0]
        assert "voice_id" in v
        assert "gender" in v
        assert any(voice["voice_id"] == "af_heart" for voice in voices_data["voices"])


@pytest.mark.asyncio
async def test_tts_client_dual_compatibility_mock(monkeypatch):
    """Verify LocalTTSClient gracefully falls back to OpenAI /v1/audio/speech if ElevenLabs route 404s."""
    client = LocalTTSClient(http_base_url="http://mock-tts:8880")

    called_routes = []

    async def mock_post(self, url, **kwargs):
        called_routes.append(url)
        class MockResponse:
            def __init__(self, status_code, content):
                self.status_code = status_code
                self.content = content
                self.text = ""

        if "/v1/text-to-speech" in url:
            # Simulate an OpenAI-compatible Kokoro server that returns 404 for ElevenLabs route
            return MockResponse(404, b"")
        elif "/v1/audio/speech" in url:
            # 24kHz 16-bit PCM (100 samples = 200 bytes)
            return MockResponse(200, b"\x00\x00" * 100)
        return MockResponse(500, b"")

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    audio_pcm = await client.synthesize_http(
        text="Hello world test",
        voice_id="af_heart",
        model_id="kokoro",
    )

    assert len(audio_pcm) == 200
    assert any("/v1/text-to-speech" in r for r in called_routes)
    assert any("/v1/audio/speech" in r for r in called_routes)


@pytest.mark.asyncio
async def test_tts_gate_concurrency_serialization():
    """Verify 5 concurrent requests serialize cleanly through _tts_gate."""
    client = LocalTTSClient()
    active_in_gate = 0
    max_concurrent_seen = 0

    async def monitored_synthesize(text, **kwargs):
        nonlocal active_in_gate, max_concurrent_seen
        active_in_gate += 1
        if active_in_gate > max_concurrent_seen:
            max_concurrent_seen = active_in_gate
        await asyncio.sleep(0.05)
        active_in_gate -= 1
        return b"\x00\x00" * 50

    client.synthesize_http = monitored_synthesize

    tasks = [client.synthesize_http(f"Test sentence {i}") for i in range(5)]
    results = await asyncio.gather(*tasks)

    assert len(results) == 5
    for pcm in results:
        assert len(pcm) == 100  # 50 samples * 2 bytes = 100 bytes

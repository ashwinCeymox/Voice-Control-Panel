"""
Unit tests for S2: Gateway Streaming STT Adapter (StreamingSTTClient).
Follows TDD strictly.
"""

import asyncio
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.core.errors import ProviderUnavailableError
from app.providers.stt_base import STTProvider
from app.providers.streaming_stt_client import StreamingSTTClient


def test_streaming_client_implements_stt_provider():
    """Verify StreamingSTTClient is an instance and subclass of STTProvider."""
    client = StreamingSTTClient()
    assert isinstance(client, STTProvider)


@pytest.mark.asyncio
async def test_streaming_client_health():
    """Verify health check queries the internal streaming STT service."""
    client = StreamingSTTClient()
    # With service running on 127.0.0.1:8088
    health = await client.get_health()
    assert health["status"] == "ready"
    assert "zipformer" in health["model"].lower()
    assert health["kind"] == "streaming"


@pytest.mark.asyncio
async def test_streaming_client_stream_flow():
    """Verify streaming generator yields partials and final transcript."""
    client = StreamingSTTClient()

    async def sample_audio_generator():
        # Yield 3 dummy 100ms frames of silence
        dummy_pcm = b"\x00\x00" * 1600
        for _ in range(3):
            yield dummy_pcm
            await asyncio.sleep(0.01)

    events = []
    async for event in client.stream(sample_audio_generator()):
        events.append(event)

    # Must receive at least the final completion event
    assert len(events) >= 1
    final_event = events[-1]
    assert final_event["type"] in ("transcript.completed", "transcript.final")
    assert "text" in final_event


@pytest.mark.asyncio
async def test_streaming_client_service_down_raises_sanitized_error():
    """Verify when service is unreachable, ProviderUnavailableError is raised without raw stacktraces."""
    broken_client = StreamingSTTClient(ws_url="ws://127.0.0.1:9999/ws")

    async def dummy_gen():
        yield b"\x00\x00" * 100

    with pytest.raises(ProviderUnavailableError) as exc_info:
        async for _ in broken_client.stream(dummy_gen()):
            pass

    assert exc_info.value.code in ("PROVIDER_UNAVAILABLE", "STREAMING_STT_UNAVAILABLE")
    assert "Streaming" in exc_info.value.message


@pytest.mark.asyncio
async def test_gateway_health_ready_includes_streaming_stt():
    """Verify GET /health/ready includes status of both LocalAI and Streaming STT."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert "streaming_stt" in data
        assert data["streaming_stt"]["status"] in ("ready", "available")

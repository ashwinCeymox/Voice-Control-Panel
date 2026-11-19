"""
Unit tests for Nemo / Nemotron Dynamic Streaming STT Support.
Verifies that:
1. Nemo / Nemotron streaming models are dynamically recognized as kind="streaming" and runtime="nemo-speech-cpp".
2. No hardcoded keyword exclusions bar Nemotron models from streaming.
3. StreamingSTTClient dynamically routes Nemo models to LocalAI and completes preflight.
4. WebSocket streaming endpoint (/v1/audio/transcriptions/stream) accepts Nemo streaming models
   without returning STT_MODEL_UNAVAILABLE (not_streaming_capable).
5. Dynamic install recording correctly classifies streaming STT models.
"""

import pytest
from starlette.testclient import TestClient

from app.main import app
from app.core.security import create_websocket_ticket
from app.services.model_manifest import model_manifest
from app.providers.streaming_stt_client import StreamingSTTClient


@pytest.fixture
def sync_client():
    return TestClient(app)


@pytest.mark.asyncio
async def test_nemo_streaming_model_manifest_and_gallery_classification():
    """Verify nemo-speech-cpp-nemotron-3.5-asr-streaming-diarized is classified as kind='streaming'."""
    from app.api.gallery import get_installed_models_data

    # Synchronize and inspect manifest
    manifest = await model_manifest.sync_manifest()
    stt_models = manifest.get("stt", [])
    
    nemo_entry = next(
        (m for m in stt_models if "nemo" in m.get("id", "").lower() and "nemotron" in m.get("id", "").lower()),
        None,
    )
    assert nemo_entry is not None, "Nemotron streaming model should be in STT manifest"
    assert nemo_entry.get("kind") == "streaming", f"Expected kind='streaming', got {nemo_entry.get('kind')}"

    # Also test get_installed_models_data directly
    installed_data = await get_installed_models_data()
    nemo_installed = next(
        (m for m in installed_data.get("stt", []) if "nemo" in m.get("id", "").lower() and "nemotron" in m.get("id", "").lower()),
        None,
    )
    assert nemo_installed is not None, "Nemotron model should be in installed models data"
    assert nemo_installed.get("kind") == "streaming", f"Expected kind='streaming' in gallery data, got {nemo_installed.get('kind')}"
    assert nemo_installed.get("available") is True, f"Expected available=True, got {nemo_installed.get('available')}"


@pytest.mark.asyncio
async def test_dynamic_record_install_stt_streaming_classification():
    """Verify record_install dynamically infers kind='streaming' for streaming STT models."""
    # Test streaming inference without passing kind explicitly
    manifest = await model_manifest.record_install(
        model_id="test-streaming-nemotron-dynamic",
        name="Dynamic Nemotron Streaming Test",
        category="stt",
        provider="localai",
        size_mb=450,
    )
    entry = next((m for m in manifest["stt"] if m["id"] == "test-streaming-nemotron-dynamic"), None)
    assert entry is not None
    assert entry.get("kind") == "streaming"

    # Test batch model inference
    manifest = await model_manifest.record_install(
        model_id="test-batch-whisper-dynamic",
        name="Dynamic Whisper Batch Test",
        category="stt",
        provider="localai",
        size_mb=450,
    )
    entry = next((m for m in manifest["stt"] if m["id"] == "test-batch-whisper-dynamic"), None)
    assert entry is not None
    assert entry.get("kind") == "batch"

    # Clean up test entries
    await model_manifest.record_uninstall("test-streaming-nemotron-dynamic")
    await model_manifest.record_uninstall("test-batch-whisper-dynamic")


def test_streaming_stt_client_routing():
    """StreamingSTTClient should route Nemotron models to LocalAI and Sherpa models to Sherpa."""
    client = StreamingSTTClient()
    
    assert client.is_localai_model("nemo-speech-cpp-nemotron-3.5-asr-streaming-diarized") is True
    assert client.is_localai_model("parakeet-cpp-realtime-scene") is True
    assert client.is_localai_model("sherpa-onnx-streaming-zipformer-en-20M") is False
    assert client.is_localai_model("sherpa-onnx-streaming-zipformer-en-20M-2023-02-17") is False


@pytest.mark.asyncio
async def test_streaming_stt_client_preflight_nemo():
    """Preflight should succeed for loaded nemo-speech-cpp model in LocalAI."""
    client = StreamingSTTClient()
    result = await client.preflight("nemo-speech-cpp-nemotron-3.5-asr-streaming-diarized")
    assert result.get("status") == "ready"
    assert result.get("engine") == "localai"


def test_nemo_streaming_websocket_accepted(sync_client):
    """nemo-speech-cpp-nemotron-3.5-asr-streaming-diarized must be accepted on streaming STT WebSocket."""
    ticket = create_websocket_ticket("admin")
    with sync_client.websocket_connect(
        f"/v1/audio/transcriptions/stream?ticket={ticket}"
    ) as ws:
        ws.send_json(
            {
                "type": "stream.start",
                "model": "nemo-speech-cpp-nemotron-3.5-asr-streaming-diarized",
                "sample_rate": 16000,
                "format": "pcm_s16le",
                "language": "en",
            }
        )

        dummy_pcm = b"\x00\x00" * 1600
        ws.send_bytes(dummy_pcm)
        ws.send_json({"type": "audio.commit"})

        events = []
        while True:
            msg = ws.receive_json()
            events.append(msg)
            if msg.get("type") in ("transcript.completed", "transcript.final", "error"):
                break

        # Must not be rejected with STT_MODEL_UNAVAILABLE (not_streaming_capable)
        for e in events:
            assert e.get("code") != "STT_MODEL_UNAVAILABLE", f"Model unexpectedly rejected: {e}"

        completed = next(
            (e for e in events if e.get("type") in ("transcript.completed", "transcript.final")),
            None,
        )
        assert completed is not None, f"Expected transcript.completed but got: {events}"
        assert completed.get("model") == "nemo-speech-cpp-nemotron-3.5-asr-streaming-diarized"
        assert completed.get("runtime") == "nemo-speech-cpp"


def test_batch_model_rejected_on_streaming_websocket(sync_client):
    """Batch STT models (e.g. whisper-tiny) must be rejected with not_streaming_capable."""
    ticket = create_websocket_ticket("admin")
    with sync_client.websocket_connect(
        f"/v1/audio/transcriptions/stream?ticket={ticket}"
    ) as ws:
        ws.send_json(
            {
                "type": "stream.start",
                "model": "whisper-tiny",
                "sample_rate": 16000,
                "format": "pcm_s16le",
            }
        )

        msg = ws.receive_json()
        assert msg.get("type") == "error"
        assert msg.get("code") == "STT_MODEL_UNAVAILABLE"
        assert "batch transcription" in msg.get("message", "")

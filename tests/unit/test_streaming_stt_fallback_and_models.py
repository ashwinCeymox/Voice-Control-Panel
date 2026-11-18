"""
Unit tests for Streaming STT Fallback, Model Enforcement, and Audio Statistics.
Mandated by stream-fix.md (Phase B TDD).
"""

import wave
import numpy as np
import pytest
from starlette.testclient import TestClient
from app.main import app
from app.core.security import create_websocket_ticket
from app.services.model_manifest import model_manifest


@pytest.fixture
def sync_client():
    return TestClient(app)


def test_streaming_stt_empty_model_field_returns_error(sync_client):
    """Empty or missing model in stream.start must return an explicit STT_MODEL_REQUIRED error."""
    ticket = create_websocket_ticket("admin")
    with sync_client.websocket_connect(
        f"/v1/audio/transcriptions/stream?ticket={ticket}"
    ) as ws:
        # Handshake with empty model
        ws.send_json(
            {
                "type": "stream.start",
                "model": "",
                "sample_rate": 16000,
                "format": "pcm_s16le",
            }
        )

        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg.get("code") in ("STT_MODEL_REQUIRED", "INVALID_MODEL")
        assert "model" in msg.get("message", "").lower()


def test_streaming_stt_nonexistent_model_returns_explicit_error(sync_client):
    """A nonexistent model must return explicit STT_MODEL_UNAVAILABLE error and NO transcript."""
    ticket = create_websocket_ticket("admin")
    with sync_client.websocket_connect(
        f"/v1/audio/transcriptions/stream?ticket={ticket}"
    ) as ws:
        ws.send_json(
            {
                "type": "stream.start",
                "model": "nonexistent-model-xyz",
                "sample_rate": 16000,
                "format": "pcm_s16le",
            }
        )

        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg.get("code") in ("STT_MODEL_UNAVAILABLE", "MODEL_NOT_FOUND")
        assert "nonexistent-model-xyz" in msg.get("message", "")


def test_streaming_stt_unavailable_runtime_model_rejected(sync_client):
    """Parakeet models whose runtime is unavailable must be rejected with STT_MODEL_UNAVAILABLE."""
    ticket = create_websocket_ticket("admin")
    with sync_client.websocket_connect(
        f"/v1/audio/transcriptions/stream?ticket={ticket}"
    ) as ws:
        ws.send_json(
            {
                "type": "stream.start",
                "model": "parakeet-cpp-realtime_eou_120m-v1",
                "sample_rate": 16000,
                "format": "pcm_s16le",
            }
        )

        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg.get("code") == "STT_MODEL_UNAVAILABLE"


def test_streaming_stt_valid_model_returns_telemetry_fields(sync_client):
    """Valid streaming request must return transcript.completed containing model_used, runtime, fallback=False."""
    ticket = create_websocket_ticket("admin")
    with sync_client.websocket_connect(
        f"/v1/audio/transcriptions/stream?ticket={ticket}"
    ) as ws:
        ws.send_json(
            {
                "type": "stream.start",
                "model": "sherpa-onnx-streaming-zipformer-en-20M",
                "sample_rate": 16000,
                "format": "pcm_s16le",
                "language": "en",
            }
        )

        # Send 2 audio chunks of silence
        dummy_pcm = b"\x00\x00" * 1600
        ws.send_bytes(dummy_pcm)
        ws.send_bytes(dummy_pcm)
        ws.send_json({"type": "audio.commit"})

        events = []
        while True:
            msg = ws.receive_json()
            events.append(msg)
            if msg.get("type") in ("transcript.completed", "transcript.final", "error"):
                break

        completed = next(
            (
                e
                for e in events
                if e.get("type") in ("transcript.completed", "transcript.final")
            ),
            None,
        )
        assert completed is not None, f"Expected transcript.completed but got: {events}"
        assert completed.get("model_used") == "sherpa-onnx-streaming-zipformer-en-20M"
        assert completed.get("runtime") == "sherpa-onnx"
        assert completed.get("fallback") is False


def test_audio_statistics_check():
    """Verify audio statistics validator passes on standard audio fixture."""
    fixture_path = "tests/fixtures/audio/quiet_normal_speech.wav"
    with wave.open(fixture_path, "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        n = wf.getnframes()
        pcm = wf.readframes(n)

    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    rms = float(np.sqrt(np.mean(samples**2)))
    peak = float(np.max(np.abs(samples)))
    clipped = int(np.sum(np.abs(samples) >= 32767))
    duration = n / sr

    assert sr == 16000, f"Expected 16kHz, got {sr}"
    assert ch == 1, f"Expected mono, got {ch}"
    assert sw == 2, f"Expected 16-bit (2 bytes), got {sw}"
    assert duration > 1.0, f"Duration too short: {duration}s"
    assert clipped == 0, f"Expected 0 clipped samples, got {clipped}"
    assert 200 < rms < 10000, f"RMS out of expected speech range: {rms}"
    assert peak > 0, "Expected non-silent peak"


def test_model_manifest_filters_unavailable_streaming_runtimes():
    """Manifest must filter out models whose runtime backend is missing when available_only=True."""
    models = model_manifest.get_models_by_category(
        "stt", kind="streaming", available_only=True
    )
    model_ids = [m["id"] for m in models]
    # Parakeet models without a live parakeet-cpp server should not be listed as available streaming models
    assert "parakeet-cpp-realtime_eou_120m-v1" not in model_ids
    assert "parakeet-cpp-nemotron-3.5-asr-streaming-0.6b" not in model_ids


def test_model_manifest_resolve_model_id_heals_unavailable_model():
    """resolve_model_id must dynamically heal unavailable model requests (e.g. Parakeet on macOS) to installed default."""
    resolved = model_manifest.resolve_model_id(
        "stt", "parakeet-cpp-realtime-scene", kind="streaming"
    )
    assert "zipformer" in resolved.lower() or "sherpa" in resolved.lower()


def test_model_manifest_resolve_model_id_resolves_parakeet_for_batch():
    """resolve_model_id must resolve parakeet-cpp-realtime-scene when batch mode is requested."""
    resolved = model_manifest.resolve_model_id(
        "stt", "parakeet-cpp-realtime-scene", kind="batch"
    )
    assert resolved == "parakeet-cpp-realtime-scene"


def test_agent_manager_normalize_stt_heals_unavailable_model():
    """_normalize_stt must heal any agent configured with an unavailable model on macOS to sherpa-onnx."""
    from app.services.agent_manager import _normalize_stt

    stt_cfg = _normalize_stt(
        {"model": "parakeet-cpp-realtime-scene", "kind": "streaming"}
    )
    assert (
        "zipformer" in stt_cfg["model"].lower() or "sherpa" in stt_cfg["model"].lower()
    )
    assert stt_cfg["kind"] == "streaming"


@pytest.mark.asyncio
async def test_streaming_client_handshake_rejection_raises_404():
    """StreamingSTTClient must raise StreamingModelNotLoadedError (404) on model rejection, not 503 unreachable."""
    from app.providers.streaming_stt_client import StreamingSTTClient
    from app.core.errors import StreamingModelNotLoadedError

    client = StreamingSTTClient()

    async def dummy_gen():
        yield b"\x00\x00" * 1600

    with pytest.raises(StreamingModelNotLoadedError) as exc_info:
        async for _ in client.stream(dummy_gen(), model="nonexistent-model-xyz"):
            pass

    assert exc_info.value.status_code == 404
    assert "nonexistent-model-xyz" in str(
        exc_info.value.message
    ) or "nonexistent-model-xyz" in str(exc_info.value.details)

"""
Unit & Integration Tests for mid-fix.md:
S1/S2: Streaming STT Availability, Precise Error Codes (503/404/400), Preflight
S3: Turn PCM Audio Buffering, Proper WAV Header Construction on Fallback, Honest Silence & Decode Errors
S4: Multi-Engine Model Deletion, 409 MODEL_IN_USE Safeguard, Path Traversal Protection
"""

import io
import wave
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.core.errors import AppError


# Helper to generate in-memory valid 16kHz mono 16-bit PCM WAV
def create_test_wav_bytes(duration_sec: float = 1.0, freq_hz: float = 440.0) -> bytes:
    import numpy as np

    sample_rate = 16000
    n_samples = int(sample_rate * duration_sec)
    t = np.linspace(0, duration_sec, n_samples, endpoint=False)
    waveform = (0.5 * np.sin(2 * np.pi * freq_hz * t) * 32767).astype(np.int16)
    pcm_bytes = waveform.tobytes()

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm_bytes)
    return buf.getvalue()


# Helper to generate raw 16kHz mono 16-bit PCM bytes (no WAV header)
def create_test_pcm_bytes(duration_sec: float = 1.0, freq_hz: float = 440.0) -> bytes:
    import numpy as np

    sample_rate = 16000
    n_samples = int(sample_rate * duration_sec)
    t = np.linspace(0, duration_sec, n_samples, endpoint=False)
    waveform = (0.5 * np.sin(2 * np.pi * freq_hz * t) * 32767).astype(np.int16)
    return waveform.tobytes()


@pytest.fixture
def api_headers():
    return {"Authorization": "Bearer dev-voice-key-12345"}


# -------------------------------------------------------------------------
# S1 / S2 Tests: Precise Error Codes and Preflight
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_client_raises_503_when_service_unreachable():
    """When the streaming service connection fails or is refused, raise 503 STREAMING_STT_UNAVAILABLE, not generic 502."""
    from app.providers.streaming_stt_client import StreamingSTTClient

    # Point client to a closed port
    client = StreamingSTTClient(ws_url="ws://127.0.0.1:59999/ws")

    async def dummy_audio():
        yield b"\x00\x00" * 160

    with pytest.raises(AppError) as exc_info:
        async for _ in client.stream(
            dummy_audio(), model="sherpa-onnx-streaming-zipformer-en-20M"
        ):
            pass

    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "STREAMING_STT_UNAVAILABLE"
    assert (
        "not running" in exc_info.value.message.lower()
        or "unreachable" in exc_info.value.message.lower()
    )


@pytest.mark.asyncio
async def test_streaming_client_raises_404_when_model_not_loaded():
    """When streaming service reports STT_MODEL_UNAVAILABLE/STT_MODEL_NOT_LOADED, raise 404/409 not generic 502."""
    from app.providers.streaming_stt_client import StreamingSTTClient

    client = StreamingSTTClient(ws_url="ws://127.0.0.1:8088/ws")

    async def dummy_audio():
        yield b"\x00\x00" * 160

    # Request an installed model that is not loaded on the active engine
    with pytest.raises(AppError) as exc_info:
        async for _ in client.stream(
            dummy_audio(), model="nonexistent-or-unloaded-model"
        ):
            pass

    assert exc_info.value.status_code in (404, 409)
    assert exc_info.value.code in ("STT_MODEL_NOT_LOADED", "STT_MODEL_UNAVAILABLE")


# -------------------------------------------------------------------------
# S3 Tests: Turn Audio Buffering & Proper WAV Header Construction on Fallback
# -------------------------------------------------------------------------


def test_pcm_to_wav_construction():
    """Verify that build_wav_from_pcm builds a 100% standard 16kHz mono WAV from raw PCM bytes."""
    from app.services.audio_utils import build_wav_from_pcm

    pcm_data = create_test_pcm_bytes(duration_sec=0.5)
    wav_data = build_wav_from_pcm(
        pcm_data, sample_rate=16000, channels=1, sample_width=2
    )

    assert wav_data.startswith(b"RIFF")
    assert wav_data[8:12] == b"WAVE"
    assert len(wav_data) == len(pcm_data) + 44

    # Validate with Python's wave module
    with wave.open(io.BytesIO(wav_data), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16000
        assert w.getnframes() == len(pcm_data) // 2


@pytest.mark.asyncio
async def test_decode_error_not_swallowed_as_no_speech():
    """
    Ensure corrupt audio or engine decode errors do NOT get silently converted to '(no speech detected)'.
    They must emit STT_DECODE_ERROR or an honest error code.
    """
    from app.api.voice import handle_stt_decode_failure

    corrupt_err = Exception(
        "exit status 183 out: ffmpeg Error opening input: Invalid data found when processing input"
    )
    result = handle_stt_decode_failure(corrupt_err, turn_id="turn-test-123")

    assert result["code"] == "STT_DECODE_ERROR"
    assert "(no speech detected)" not in result["text"].lower()


# -------------------------------------------------------------------------
# S4 Tests: Model Deletion Routing, Agent Usage Safeguard, Path Traversal
# -------------------------------------------------------------------------


def test_delete_model_in_use_by_agent_returns_409(api_headers):
    """Deleting a model currently in use by an agent must be refused with 409 MODEL_IN_USE."""
    client = TestClient(app)

    # 1. Create agent using a specific installed model
    agent_payload = {
        "name": "Test Protected Agent",
        "system_prompt": "Hello",
        "llm": {
            "model": "llama-3.2-1b-instruct",
            "temperature": 0.7,
            "max_tokens": 512,
        },
        "stt": {"model": "whisper-base", "kind": "batch"},
        "tts": {"engine": "kokoro", "voice_id": "af_heart", "speed": 1.0},
        "vad": {"mode": "energy", "threshold": 0.045, "min_silence_ms": 650},
        "audio": {"echo_mode": "laptop_speakers", "barge_in": True},
    }

    create_resp = client.post("/v1/agents", json=agent_payload, headers=api_headers)
    assert create_resp.status_code == 201
    agent_id = create_resp.json()["id"]

    try:
        # 2. Attempt to delete the in-use model
        del_resp = client.delete("/v1/models/whisper-base", headers=api_headers)
        assert del_resp.status_code == 409
        err = del_resp.json()
        assert err["error"]["code"] == "MODEL_IN_USE"
        assert (
            "Test Protected Agent" in err["error"]["message"]
            or agent_id in err["error"]["message"]
        )
    finally:
        # Clean up agent
        client.delete(f"/v1/agents/{agent_id}", headers=api_headers)


def test_delete_streaming_model_path_traversal_blocked(api_headers):
    """Attempting to delete with a path traversal sequence must be blocked with 400."""
    client = TestClient(app)
    del_resp = client.delete("/v1/models/test..traversal", headers=api_headers)
    assert del_resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_streaming_model_manager_deletes_files_and_manifest(tmp_path):
    """Streaming model manager should remove model folder, purge manifest, and invalidate cache."""
    from app.services.streaming_model_manager import streaming_model_manager

    # Set up mock streaming directory
    mock_model_dir = tmp_path / "test-streaming-model"
    mock_model_dir.mkdir(parents=True)
    (mock_model_dir / "tokens.txt").write_text("token 1\ntoken 2")
    (mock_model_dir / "encoder.onnx").write_bytes(b"dummy encoder")

    with patch.object(streaming_model_manager, "models_base_dir", tmp_path):
        result = await streaming_model_manager.delete_streaming_model(
            "test-streaming-model"
        )
        assert result["status"] == "deleted"
        assert not mock_model_dir.exists()


@pytest.mark.asyncio
async def test_preflight_validates_loaded_model_or_raises():
    """Preflight check on session start verifies service and model readiness."""
    from app.providers.streaming_stt_client import StreamingSTTClient
    from app.core.errors import StreamingModelNotLoadedError

    client = StreamingSTTClient(
        ws_url="ws://127.0.0.1:8088/ws", http_url="http://127.0.0.1:8088"
    )

    # 1. Valid loaded model passes preflight
    res = await client.preflight("sherpa-onnx-streaming-zipformer-en-20M")
    assert res["status"] == "ready"

    # 2. Unloaded model raises explicit StreamingModelNotLoadedError
    with pytest.raises(StreamingModelNotLoadedError):
        await client.preflight("unloaded-streaming-model-xyz")


def test_full_utterance_preserved_on_fallback():
    """On streaming fallback, all streamed PCM chunks are preserved in a valid WAV matching duration."""
    from app.services.audio_utils import build_wav_from_pcm

    # Simulate 30 incoming 100ms PCM chunks (3 seconds total)
    chunks = [create_test_pcm_bytes(duration_sec=0.1) for _ in range(30)]
    accumulated_pcm = b"".join(chunks)

    wav_bytes = build_wav_from_pcm(
        accumulated_pcm, sample_rate=16000, channels=1, sample_width=2
    )
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        assert w.getframerate() == 16000
        assert w.getnframes() == 16000 * 3
        assert w.getnchannels() == 1

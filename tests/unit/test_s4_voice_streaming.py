"""
Unit tests for S4: Voice Pipeline Streaming STT Path and Automatic Fallback.
Follows TDD.
"""

import json
import pytest
from unittest.mock import patch
from starlette.testclient import TestClient
from app.main import app
from app.core.security import create_websocket_ticket
from app.core.errors import ProviderUnavailableError


@pytest.fixture
def sync_client():
    return TestClient(app)


def test_voice_stream_streaming_stt_success(sync_client):
    """When stt.kind='streaming', voice stream yields transcript.partial and completes turn."""
    ticket = create_websocket_ticket("admin")
    with sync_client.websocket_connect(f"/v1/voice/stream?ticket={ticket}") as ws:
        # 1. Receive session.created
        init_evt = ws.receive_json()
        assert init_evt["type"] == "session.created"

        # 2. Send session.start with stt_kind: streaming
        ws.send_json(
            {
                "type": "session.start",
                "stt_kind": "streaming",
                "input_audio": {"sample_rate": 16000, "codec": "pcm"},
            }
        )

        # 3. Simulate speech: send speech_start and audio chunks
        ws.send_json({"type": "vad.speech_start", "timestamp": 12345678})

        # 100ms of PCM16 audio
        chunk = b"\x00\x00" * 1600
        ws.send_bytes(chunk)
        ws.send_bytes(chunk)

        # 4. Commit speech
        ws.send_json({"type": "audio.commit"})

        # 5. Collect events until turn.completed
        received_types = []
        while True:
            evt = ws.receive_json()
            received_types.append(evt["type"])
            if evt["type"] in ("turn.completed", "error"):
                break

        assert "transcript.completed" in received_types
        assert "turn.completed" in received_types


def test_voice_stream_fallback_to_batch_on_streaming_failure(sync_client):
    """When streaming STT fails, an stt.fallback event is emitted and batch STT takes over."""
    ticket = create_websocket_ticket("admin")

    import unittest.mock

    mock_resp = unittest.mock.MagicMock()
    mock_resp.text = "Batch fallback transcription text."

    async def mock_tts(*args, **kwargs):
        yield b"\x00\x00" * 320

    with (
        patch(
            "app.providers.streaming_stt_client.StreamingSTTClient.stream",
            side_effect=ProviderUnavailableError(
                "Streaming STT", details="Connection refused"
            ),
        ),
        patch(
            "app.providers.localai_stt.LocalAISTTClient.transcribe",
            return_value=mock_resp,
        ),
        patch(
            "app.providers.localtts_client.LocalTTSClient.stream_synthesis_ws",
            side_effect=mock_tts,
        ),
    ):
        with sync_client.websocket_connect(f"/v1/voice/stream?ticket={ticket}") as ws:
            ws.receive_json()  # session.created

            ws.send_json(
                {
                    "type": "session.start",
                    "stt_kind": "streaming",
                }
            )

            # Send a valid WAV chunk (or PCM frame)
            # 16kHz mono 16-bit sine wave or silence WAV
            wav_header = b"RIFF$\x08\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x08\x00\x00"
            audio = wav_header + b"\x00\x00" * 1024

            ws.send_bytes(audio)
            ws.send_json({"type": "audio.commit"})

            import json

            received_events = []
            while True:
                raw = ws.receive()
                if "text" in raw:
                    evt = json.loads(raw["text"])
                    received_events.append(evt)
                    if evt["type"] in ("turn.completed", "error"):
                        break

            types = [e["type"] for e in received_events]
            # Must emit stt.fallback reporting why fallback happened
            assert "stt.fallback" in types
            fallback_evt = next(
                e for e in received_events if e["type"] == "stt.fallback"
            )
            assert "reason" in fallback_evt
            # Turn must still complete through batch path (never crash or silently drop)
            assert "transcript.completed" in types
            assert "turn.completed" in types


def test_voice_stream_emits_partials(sync_client):
    """Streaming STT emits transcript.partial events during speech."""
    ticket = create_websocket_ticket("admin")

    async def mock_stream(audio_generator, model=None, language=None):
        yield {
            "type": "transcript.partial",
            "seq": 1,
            "confirmed": "hello",
            "tentative": " wor",
        }
        yield {
            "type": "transcript.partial",
            "seq": 2,
            "confirmed": "hello world",
            "tentative": "",
        }
        yield {"type": "transcript.completed", "text": "hello world"}

    with patch(
        "app.providers.streaming_stt_client.StreamingSTTClient.stream",
        side_effect=mock_stream,
    ):
        with sync_client.websocket_connect(f"/v1/voice/stream?ticket={ticket}") as ws:
            ws.receive_json()  # session.created

            ws.send_json(
                {
                    "type": "session.start",
                    "stt_kind": "streaming",
                }
            )
            ws.send_json({"type": "vad.speech_start"})
            ws.send_bytes(b"\x00\x00" * 1600)
            ws.send_json({"type": "audio.commit"})

            partials = []
            while True:
                raw = ws.receive()
                if "text" in raw:
                    evt = json.loads(raw["text"])
                    if evt["type"] == "transcript.partial":
                        partials.append(evt)
                    elif evt["type"] in ("turn.completed", "error"):
                        break

            assert len(partials) >= 1
            assert partials[0]["confirmed"] == "hello"
            assert partials[0]["tentative"] == " wor"
            assert partials[1]["confirmed"] == "hello world"

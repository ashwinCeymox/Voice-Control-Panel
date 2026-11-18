"""
QA & Soak Test Suite for S6: Failure Tests, Concurrency Limiter, and Security Verification.
"""

from contextlib import ExitStack
import json
import logging
import pytest
from unittest.mock import patch
from starlette.testclient import TestClient
from app.main import app
from app.core.config import settings
from app.core.security import create_websocket_ticket


@pytest.fixture
def sync_client():
    return TestClient(app)


def test_s6_stream_concurrency_limit_enforcement(sync_client):
    """When stream_limiter reaches MAX_ACTIVE_STT_STREAMS (5), 6th stream must be rejected or queued."""
    with ExitStack() as stack:
        # Open 5 streaming connections up to limit
        for _ in range(settings.MAX_ACTIVE_STT_STREAMS):
            t = create_websocket_ticket("admin")
            stack.enter_context(
                sync_client.websocket_connect(
                    f"/v1/audio/transcriptions/stream?ticket={t}"
                )
            )

        # 6th stream should exceed concurrency limit
        t6 = create_websocket_ticket("admin")
        with sync_client.websocket_connect(
            f"/v1/audio/transcriptions/stream?ticket={t6}"
        ) as ws6:
            msg = ws6.receive_json()
            assert msg.get("type") == "error"
            assert any(
                k in msg.get("message", "").lower()
                for k in ("limit", "overloaded", "concurrent", "rejected", "too many")
            )


def test_s6_bad_audio_frames_resilience(sync_client):
    """Corrupted / truncated audio frames must not crash the streaming pipeline."""
    ticket = create_websocket_ticket("admin")
    with sync_client.websocket_connect(f"/v1/voice/stream?ticket={ticket}") as ws:
        ws.receive_json()  # session.created

        ws.send_json(
            {
                "type": "session.start",
                "stt_kind": "streaming",
            }
        )
        ws.send_json({"type": "vad.speech_start"})

        # Send random malformed garbage bytes
        ws.send_bytes(b"\xff\xfe\x00\x12\x34\x56\x78\x9a\xbc\xde\xf0" * 20)
        ws.send_json({"type": "audio.commit"})

        events = []
        while True:
            raw = ws.receive()
            if "text" in raw:
                evt = json.loads(raw["text"])
                events.append(evt["type"])
                if evt["type"] in ("turn.completed", "error"):
                    break

        # Must cleanly complete or handle gracefully, never drop the connection
        assert "turn.completed" in events or "error" in events


def test_s6_security_transcript_privacy(sync_client, caplog):
    """Customer transcripts and speech content must NEVER appear in application logs."""
    ticket = create_websocket_ticket("admin")
    secret_text = "ultra_sensitive_customer_confidential_token_xyz987"

    async def mock_stream(audio_gen, model=None, language=None):
        yield {
            "type": "transcript.partial",
            "seq": 1,
            "confirmed": secret_text,
            "tentative": "",
        }
        yield {"type": "transcript.completed", "text": secret_text}

    with caplog.at_level(logging.DEBUG):
        with patch(
            "app.providers.streaming_stt_client.StreamingSTTClient.stream",
            side_effect=mock_stream,
        ):
            with sync_client.websocket_connect(
                f"/v1/voice/stream?ticket={ticket}"
            ) as ws:
                ws.receive_json()
                ws.send_json({"type": "session.start", "stt_kind": "streaming"})
                ws.send_json({"type": "vad.speech_start"})
                ws.send_bytes(b"\x00\x00" * 1600)
                ws.send_json({"type": "audio.commit"})

                while True:
                    raw = ws.receive()
                    if "text" in raw:
                        evt = json.loads(raw["text"])
                        if evt["type"] in ("turn.completed", "error"):
                            break

    # Caplog inspection: secret text must NOT appear in application log records
    for record in caplog.records:
        if record.name.startswith("voice_agent") or record.name.startswith(
            "streaming_stt"
        ):
            assert secret_text not in record.getMessage(), (
                f"Confidential speech leaked in log: {record.getMessage()}"
            )


def test_s6_multi_turn_soak(sync_client):
    """Run 30 rapid sequential conversational turns on a single WebSocket session without leaking state."""
    ticket = create_websocket_ticket("admin")
    turn_count = 30

    async def mock_stream(audio_gen, model=None, language=None):
        yield {"type": "transcript.completed", "text": "turn test message"}

    async def mock_llm_tokens(messages, *args, **kwargs):
        yield "OK."

    async def mock_tts(text_gen, *args, **kwargs):
        async for _ in text_gen:
            yield b"\x00\x00" * 320

    from app.services.voice_orchestrator import orchestrator

    with (
        patch(
            "app.providers.streaming_stt_client.StreamingSTTClient.stream",
            side_effect=mock_stream,
        ),
        patch.object(orchestrator.llm, "stream_tokens", side_effect=mock_llm_tokens),
        patch.object(orchestrator.tts, "stream_synthesis_ws", side_effect=mock_tts),
    ):
        with sync_client.websocket_connect(f"/v1/voice/stream?ticket={ticket}") as ws:
            ws.receive_json()  # session.created

            ws.send_json(
                {
                    "type": "session.start",
                    "stt_kind": "streaming",
                }
            )

            for turn_idx in range(turn_count):
                ws.send_json({"type": "vad.speech_start"})
                ws.send_bytes(b"\x00\x00" * 1600)
                ws.send_json({"type": "audio.commit"})

                # Wait for turn.completed
                completed = False
                while not completed:
                    raw = ws.receive()
                    if "text" in raw:
                        evt = json.loads(raw["text"])
                        if evt["type"] == "turn.completed":
                            completed = True
                        elif evt["type"] == "error":
                            pytest.fail(f"Turn {turn_idx} failed with error: {evt}")

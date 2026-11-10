"""Integration and interruption/cancellation stress tests for real-time voice streaming."""
import asyncio
import json
from unittest.mock import AsyncMock, patch
import pytest
from starlette.websockets import WebSocketDisconnect

from app.core.security import create_websocket_ticket
from app.schemas.audio import TranscriptResponse
from app.services.voice_orchestrator import orchestrator
from app.storage.repositories import SessionRepository


def test_websocket_connect_with_valid_ticket(test_client, receive_event):
    """Test WebSocket connection to /v1/voice/stream with a valid single-use ticket."""
    ticket = create_websocket_ticket("developer")
    with test_client.websocket_connect(f"/v1/voice/stream?ticket={ticket}") as ws:
        init_event = receive_event(ws)
        assert init_event["type"] == "session.created"
        assert "session_id" in init_event
        assert len(init_event["session_id"]) > 0

        # Clean session termination
        ws.send_json({"type": "session.end"})


def test_ticket_reuse_rejection(test_client, receive_event):
    """Connecting with an already consumed ticket returns WebSocket close code 4401."""
    ticket = create_websocket_ticket("developer")

    # 1. First connection consumes the ticket successfully
    with test_client.websocket_connect(f"/v1/voice/stream?ticket={ticket}") as ws:
        init_event = receive_event(ws)
        assert init_event["type"] == "session.created"
        ws.send_json({"type": "session.end"})

    # 2. Second connection with the same ticket must be rejected with close code 4401
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with test_client.websocket_connect(f"/v1/voice/stream?ticket={ticket}"):
            pass

    assert exc_info.value.code == 4401


def test_invalid_ticket_rejection(test_client):
    """Connecting with a fabricated/invalid ticket returns WebSocket close code 4401."""
    invalid_ticket = "non-existent-or-expired-ticket-abc123xyz"
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with test_client.websocket_connect(f"/v1/voice/stream?ticket={invalid_ticket}"):
            pass

    assert exc_info.value.code == 4401


def test_turn_cancellation_mid_stream_during_llm(test_client, receive_event):
    """Send audio, commit, cancel mid-stream during LLM token generation:

    Assert that:
      a) turn.cancelled event is received by client.
      b) Upstream generation tasks are cancelled without leaking background asyncio tasks.
      c) Submitting a subsequent turn on the same session functions cleanly without state corruption.
    """
    async def mock_llm_stream(messages, *args, **kwargs):
        for token in ["Hello", " world,", " this", " is", " a", " lengthy", " story", " generating", " slowly..."]:
            await asyncio.sleep(0.04)
            yield token

    async def mock_tts_stream(text_gen, *args, **kwargs):
        async for phrase in text_gen:
            await asyncio.sleep(0.04)
            yield b"\x00\x00" * 1200

    mock_stt = AsyncMock(
        return_value=TranscriptResponse(text="Tell me a long story", language="en")
    )

    with patch.object(orchestrator.stt, "transcribe", mock_stt), \
         patch.object(orchestrator.llm, "stream_tokens", side_effect=mock_llm_stream), \
         patch.object(orchestrator.tts, "stream_synthesis_ws", side_effect=mock_tts_stream):

        ticket = create_websocket_ticket("developer")
        with test_client.websocket_connect(f"/v1/voice/stream?ticket={ticket}") as ws:
            init_event = receive_event(ws)
            assert init_event["type"] == "session.created"
            session_id = init_event["session_id"]

            # 1. Turn 1: Send session.start and audio frames
            ws.send_json({
                "type": "session.start",
                "input_audio": {"format": "pcm", "container": "wav", "sample_rate": 16000},
            })
            ws.send_bytes(b"RIFF" + b"\x00" * 800)
            ws.send_json({"type": "audio.commit"})

            # Wait for transcript
            ev_transcript = receive_event(ws)
            assert ev_transcript["type"] == "transcript.completed"
            turn_id_1 = ev_transcript["turn_id"]

            # Wait for first text delta from LLM
            ev_delta = receive_event(ws)
            assert ev_delta["type"] == "response.text.delta"
            assert ev_delta["turn_id"] == turn_id_1

            # Assert upstream turn task is actively running
            active_task_turn1 = orchestrator._active_stream_tasks.get(session_id)
            assert active_task_turn1 is not None
            assert not active_task_turn1.done()

            # 2. INTERRUPT: Send turn.cancel mid-stream
            ws.send_json({"type": "turn.cancel"})

            # a) Assert turn.cancelled event is received
            ev_cancel = receive_event(ws)
            assert ev_cancel["type"] == "turn.cancelled"
            assert ev_cancel["turn_id"] == turn_id_1

            # b) Assert upstream generation task is cancelled without leaking
            # Active task for this session is cleaned from orchestrator registry
            assert orchestrator._active_stream_tasks.get(session_id) is None
            # And the task object itself has finished running
            assert active_task_turn1.done()

            # 3. Subsequent Turn on the SAME session: verify clean execution without corruption
            mock_stt.return_value = TranscriptResponse(text="What is the weather?", language="en")
            ws.send_bytes(b"RIFF" + b"\x01" * 800)
            ws.send_json({"type": "audio.commit"})

            collected_events = []
            audio_frames_received = 0
            while True:
                msg = ws.receive()
                if "text" in msg:
                    data = json.loads(msg["text"])
                    collected_events.append(data["type"])
                    if data["type"] == "turn.completed":
                        turn_id_2 = data["turn_id"]
                        break
                elif "bytes" in msg:
                    audio_frames_received += 1

            # Assert clean lifecycle for subsequent turn
            assert turn_id_2 != turn_id_1
            assert "transcript.completed" in collected_events
            assert "response.text.delta" in collected_events
            assert "audio.started" in collected_events
            assert "audio.completed" in collected_events
            assert "turn.completed" in collected_events
            assert audio_frames_received > 0

            # Verify session history in repository is clean
            messages = asyncio.run(SessionRepository.get_recent_messages(session_id))
            assert len(messages) >= 2
            # Last turn's assistant message was stored cleanly
            assert any("weather" in m.content.lower() for m in messages if m.role == "user")
            assert any(m.role == "assistant" for m in messages)

            ws.send_json({"type": "session.end"})


def test_turn_cancellation_mid_stream_during_tts_playback(test_client, receive_event):
    """Test cancellation triggered during TTS audio chunk delivery."""
    async def mock_llm_stream(messages, *args, **kwargs):
        # Quick token delivery to start TTS synthesis immediately
        yield "The weather is lovely and sunny today. Enjoy your day outside!"

    async def mock_tts_stream(text_gen, *args, **kwargs):
        # Feed chunks slowly to allow mid-playback interruption
        async for _ in text_gen:
            for _ in range(10):
                await asyncio.sleep(0.04)
                yield b"\x00\x00" * 1200

    mock_stt = AsyncMock(
        return_value=TranscriptResponse(text="Current forecast", language="en")
    )

    with patch.object(orchestrator.stt, "transcribe", mock_stt), \
         patch.object(orchestrator.llm, "stream_tokens", side_effect=mock_llm_stream), \
         patch.object(orchestrator.tts, "stream_synthesis_ws", side_effect=mock_tts_stream):

        ticket = create_websocket_ticket("developer")
        with test_client.websocket_connect(f"/v1/voice/stream?ticket={ticket}") as ws:
            init_event = receive_event(ws)
            session_id = init_event["session_id"]

            ws.send_json({"type": "session.start", "input_audio": {"container": "wav"}})
            ws.send_bytes(b"RIFF" + b"\x00" * 400)
            ws.send_json({"type": "audio.commit"})

            # Consume until audio.started or first binary audio chunk arrives
            while True:
                msg = ws.receive()
                if "text" in msg:
                    evt = json.loads(msg["text"])
                    if evt["type"] == "audio.started":
                        break
                elif "bytes" in msg:
                    break

            # Mid-playback interruption
            ws.send_json({"type": "turn.cancel"})

            # Client must receive turn.cancelled (ignoring any trailing audio bytes)
            cancel_evt = receive_event(ws)
            assert cancel_evt["type"] == "turn.cancelled"

            # Verify no tasks lingering in registry
            assert orchestrator._active_stream_tasks.get(session_id) is None

            ws.send_json({"type": "session.end"})


def test_rapid_cancellation_stress(test_client, receive_event):
    """Stress test rapid interrupt/commit toggling (user mashing interrupt)."""
    async def slow_stt(*args, **kwargs):
        await asyncio.sleep(0.05)
        return TranscriptResponse(text="Interrupted utterance", language="en")

    with patch.object(orchestrator.stt, "transcribe", side_effect=slow_stt):
        ticket = create_websocket_ticket("developer")
        with test_client.websocket_connect(f"/v1/voice/stream?ticket={ticket}") as ws:
            init_event = receive_event(ws)
            session_id = init_event["session_id"]

            # Rapidly commit and cancel 5 times in succession
            for _ in range(5):
                ws.send_bytes(b"RIFF_sample_audio")
                ws.send_json({"type": "audio.commit"})
                ws.send_json({"type": "turn.cancel"})

            # Drain any cancelled notifications produced
            ws.send_json({"type": "session.end"})

            # Ensure orchestrator registry has zero lingering tasks
            assert orchestrator._active_stream_tasks.get(session_id) is None


def test_cancel_when_no_active_turn_is_safe_noop(test_client, receive_event):
    """Sending turn.cancel when idle must be safely ignored without errors."""
    ticket = create_websocket_ticket("developer")
    with test_client.websocket_connect(f"/v1/voice/stream?ticket={ticket}") as ws:
        init_event = receive_event(ws)
        assert init_event["type"] == "session.created"

        # Idle cancellation
        ws.send_json({"type": "turn.cancel"})

        # Subsequent valid session start works normally
        ws.send_json({"type": "session.start", "input_audio": {"container": "wav"}})
        ws.send_json({"type": "session.end"})

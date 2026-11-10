"""Integration tests verifying strict session isolation across concurrent turns."""
import asyncio
import json
from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.security import create_websocket_ticket
from app.main import app
from app.schemas.audio import TranscriptResponse
from app.schemas.chat import (
    ChatCompletionChoice,
    ChatCompletionChoiceMessage,
    ChatCompletionResponse,
)
from app.services.voice_orchestrator import orchestrator
from app.storage.database import get_db
from app.storage.repositories import SessionRepository


def test_concurrent_websocket_sessions_turn_isolation(test_client, receive_event):
    """Create two independent WebSocket sessions concurrently:

    Ensure messages, turn history, and transcripts never leak across sessions.
    """
    def mock_transcribe(audio_bytes, *args, **kwargs):
        if b"session_a" in audio_bytes:
            return TranscriptResponse(text="Session A: Inquiry regarding invoice #1001", language="en")
        else:
            return TranscriptResponse(text="Session B: Reservation request for flight #2002", language="en")

    async def mock_llm_stream(messages, *args, **kwargs):
        last_msg = messages[-1]["content"] if messages else ""
        yield f"Acknowledged: {last_msg}"

    async def mock_tts_stream(text_gen, *args, **kwargs):
        async for _ in text_gen:
            yield b"\x00\x00" * 600

    with patch.object(orchestrator.stt, "transcribe", side_effect=mock_transcribe), \
         patch.object(orchestrator.llm, "stream_tokens", side_effect=mock_llm_stream), \
         patch.object(orchestrator.tts, "stream_synthesis_ws", side_effect=mock_tts_stream):

        ticket_a = create_websocket_ticket("alice")
        ticket_b = create_websocket_ticket("bob")

        with test_client.websocket_connect(f"/v1/voice/stream?ticket={ticket_a}") as ws_a, \
             test_client.websocket_connect(f"/v1/voice/stream?ticket={ticket_b}") as ws_b:

            # 1. Capture session IDs
            session_a = receive_event(ws_a)["session_id"]
            session_b = receive_event(ws_b)["session_id"]

            assert session_a != session_b

            # 2. Transmit turns concurrently
            ws_a.send_json({"type": "session.start", "input_audio": {"container": "wav"}})
            ws_a.send_bytes(b"RIFF_session_a_audio_data")
            ws_a.send_json({"type": "audio.commit"})

            ws_b.send_json({"type": "session.start", "input_audio": {"container": "wav"}})
            ws_b.send_bytes(b"RIFF_session_b_audio_data")
            ws_b.send_json({"type": "audio.commit"})

            # 3. Drain Session A events
            events_a = []
            while True:
                msg = ws_a.receive()
                if "text" in msg:
                    evt = json.loads(msg["text"])
                    events_a.append(evt)
                    if evt["type"] == "turn.completed":
                        break

            # 4. Drain Session B events
            events_b = []
            while True:
                msg = ws_b.receive()
                if "text" in msg:
                    evt = json.loads(msg["text"])
                    events_b.append(evt)
                    if evt["type"] == "turn.completed":
                        break

            ws_a.send_json({"type": "session.end"})
            ws_b.send_json({"type": "session.end"})

            # 5. Assert event stream isolation
            transcript_a = next(e["text"] for e in events_a if e["type"] == "transcript.completed")
            transcript_b = next(e["text"] for e in events_b if e["type"] == "transcript.completed")

            assert "Session A" in transcript_a
            assert "invoice #1001" in transcript_a
            assert "Session B" not in transcript_a

            assert "Session B" in transcript_b
            assert "flight #2002" in transcript_b
            assert "Session A" not in transcript_b

            # 6. Assert database persistence isolation
            msgs_a = asyncio.run(SessionRepository.get_recent_messages(session_a))
            msgs_b = asyncio.run(SessionRepository.get_recent_messages(session_b))

            assert len(msgs_a) == 2
            assert len(msgs_b) == 2

            content_a_str = " ".join(m.content for m in msgs_a)
            content_b_str = " ".join(m.content for m in msgs_b)

            assert "invoice #1001" in content_a_str
            assert "flight #2002" not in content_a_str

            assert "flight #2002" in content_b_str
            assert "invoice #1001" not in content_b_str


def test_multi_turn_llm_history_isolation(test_client, receive_event, wait_for_event):
    """Ensure multi-turn conversation context fed to LLM strictly separates session histories."""
    captured_llm_messages = []

    def mock_transcribe(audio_bytes, *args, **kwargs):
        if b"apple" in audio_bytes:
            return TranscriptResponse(text="My favorite fruit is Apple", language="en")
        elif b"banana" in audio_bytes:
            return TranscriptResponse(text="My favorite fruit is Banana", language="en")
        elif b"query_a" in audio_bytes:
            return TranscriptResponse(text="What is my favorite fruit?", language="en")
        else:
            return TranscriptResponse(text="Tell me my favorite fruit", language="en")

    async def mock_llm_stream(messages, *args, **kwargs):
        captured_llm_messages.append(list(messages))
        yield "Response"

    async def mock_tts_stream(text_gen, *args, **kwargs):
        async for _ in text_gen:
            yield b"\x00\x00" * 100

    with patch.object(orchestrator.stt, "transcribe", side_effect=mock_transcribe), \
         patch.object(orchestrator.llm, "stream_tokens", side_effect=mock_llm_stream), \
         patch.object(orchestrator.tts, "stream_synthesis_ws", side_effect=mock_tts_stream):

        ticket_a = create_websocket_ticket("alice")
        ticket_b = create_websocket_ticket("bob")

        with test_client.websocket_connect(f"/v1/voice/stream?ticket={ticket_a}") as ws_a, \
             test_client.websocket_connect(f"/v1/voice/stream?ticket={ticket_b}") as ws_b:

            receive_event(ws_a)
            receive_event(ws_b)

            # Session A Turn 1: Establish Apple
            ws_a.send_json({"type": "session.start", "input_audio": {"container": "wav"}})
            ws_a.send_bytes(b"RIFF_apple")
            ws_a.send_json({"type": "audio.commit"})
            wait_for_event(ws_a, "turn.completed")

            # Session B Turn 1: Establish Banana
            ws_b.send_json({"type": "session.start", "input_audio": {"container": "wav"}})
            ws_b.send_bytes(b"RIFF_banana")
            ws_b.send_json({"type": "audio.commit"})
            wait_for_event(ws_b, "turn.completed")

            # Session A Turn 2: Query favorite fruit
            ws_a.send_bytes(b"RIFF_query_a")
            ws_a.send_json({"type": "audio.commit"})
            wait_for_event(ws_a, "turn.completed")

            # Session B Turn 2: Query favorite fruit
            ws_b.send_bytes(b"RIFF_query_b")
            ws_b.send_json({"type": "audio.commit"})
            wait_for_event(ws_b, "turn.completed")

            ws_a.send_json({"type": "session.end"})
            ws_b.send_json({"type": "session.end"})

            # Inspect LLM prompt context for Turn 2 of each session
            # captured_llm_messages: [A_turn1, B_turn1, A_turn2, B_turn2]
            assert len(captured_llm_messages) == 4

            session_a_turn2_context = " ".join(m["content"] for m in captured_llm_messages[2])
            session_b_turn2_context = " ".join(m["content"] for m in captured_llm_messages[3])

            assert "Apple" in session_a_turn2_context
            assert "Banana" not in session_a_turn2_context

            assert "Banana" in session_b_turn2_context
            assert "Apple" not in session_b_turn2_context


@pytest.mark.asyncio
async def test_synchronous_voice_turn_session_isolation():
    """Verify session isolation in synchronous POST /v1/voice/turn turns."""
    transport = ASGITransport(app=app)

    def mock_transcribe_fn(audio_bytes, *args, **kwargs):
        if b"user1" in audio_bytes:
            return TranscriptResponse(text="Utterance from User 1", language="en")
        return TranscriptResponse(text="Utterance from User 2", language="en")

    mock_llm_resp = ChatCompletionResponse(
        id="cmpl-test",
        created=1000,
        model="llama-3.2-3b-instruct",
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatCompletionChoiceMessage(role="assistant", content="Assistant reply"),
            )
        ],
    )

    with patch.object(orchestrator.stt, "transcribe", side_effect=mock_transcribe_fn), \
         patch.object(orchestrator.llm, "complete", AsyncMock(return_value=mock_llm_resp)), \
         patch.object(orchestrator.tts, "synthesize_http", AsyncMock(return_value=b"\x00\x00" * 480)):

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": "Bearer dev-voice-key-12345"}

            # Session 1 turn
            resp1 = await client.post(
                "/v1/voice/turn",
                headers=headers,
                data={"session_id": "custom-sess-001"},
                files={"file": ("turn1.wav", b"RIFF_user1_audio", "audio/wav")},
            )
            assert resp1.status_code == 200
            assert resp1.json()["transcript"] == "Utterance from User 1"

            # Session 2 turn
            resp2 = await client.post(
                "/v1/voice/turn",
                headers=headers,
                data={"session_id": "custom-sess-002"},
                files={"file": ("turn2.wav", b"RIFF_user2_audio", "audio/wav")},
            )
            assert resp2.status_code == 200
            assert resp2.json()["transcript"] == "Utterance from User 2"

            # Check database tables
            msgs_1 = await SessionRepository.get_recent_messages("custom-sess-001")
            msgs_2 = await SessionRepository.get_recent_messages("custom-sess-002")

            assert len(msgs_1) == 2
            assert msgs_1[0].content == "Utterance from User 1"

            assert len(msgs_2) == 2
            assert msgs_2[0].content == "Utterance from User 2"


def test_concurrent_session_cancellation_isolation(test_client, receive_event, wait_for_event):
    """Cancelling a turn in Session 1 does NOT cancel or disrupt concurrent Session 2."""
    async def slow_llm_stream(messages, *args, **kwargs):
        for token in ["Session", " response", " streaming", " with", " delays..."]:
            await asyncio.sleep(0.04)
            yield token

    async def mock_tts_stream(text_gen, *args, **kwargs):
        async for _ in text_gen:
            await asyncio.sleep(0.02)
            yield b"\x00\x00" * 1200

    mock_stt = AsyncMock(
        return_value=TranscriptResponse(text="Concurrent turn test", language="en")
    )

    with patch.object(orchestrator.stt, "transcribe", mock_stt), \
         patch.object(orchestrator.llm, "stream_tokens", side_effect=slow_llm_stream), \
         patch.object(orchestrator.tts, "stream_synthesis_ws", side_effect=mock_tts_stream):

        ticket_1 = create_websocket_ticket("alice")
        ticket_2 = create_websocket_ticket("bob")

        with test_client.websocket_connect(f"/v1/voice/stream?ticket={ticket_1}") as ws_1, \
             test_client.websocket_connect(f"/v1/voice/stream?ticket={ticket_2}") as ws_2:

            sess_1 = receive_event(ws_1)["session_id"]
            sess_2 = receive_event(ws_2)["session_id"]

            # Launch turns on both sessions
            ws_1.send_json({"type": "session.start", "input_audio": {"container": "wav"}})
            ws_1.send_bytes(b"RIFF_sess1")
            ws_1.send_json({"type": "audio.commit"})

            ws_2.send_json({"type": "session.start", "input_audio": {"container": "wav"}})
            ws_2.send_bytes(b"RIFF_sess2")
            ws_2.send_json({"type": "audio.commit"})

            # Consume transcript from both
            evt_t1 = receive_event(ws_1)
            assert evt_t1["type"] == "transcript.completed"

            evt_t2 = receive_event(ws_2)
            assert evt_t2["type"] == "transcript.completed"

            # Cancel Session 1 ONLY
            ws_1.send_json({"type": "turn.cancel"})
            cancel_evt_1 = receive_event(ws_1)
            assert cancel_evt_1["type"] == "turn.cancelled"

            # Session 2 must complete without error
            wait_for_event(ws_2, "turn.completed")

            ws_1.send_json({"type": "session.end"})
            ws_2.send_json({"type": "session.end"})

            # Session 2's completed turn was successfully recorded
            msgs_2 = asyncio.run(SessionRepository.get_recent_messages(sess_2))
            assert any(m.role == "assistant" for m in msgs_2)

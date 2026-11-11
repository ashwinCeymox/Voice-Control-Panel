"""Combined voice turn API and Real-Time WebSocket streaming endpoint."""
import asyncio
import io
import json
import logging
import uuid
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, WebSocket, WebSocketDisconnect
from app.core.config import settings
from app.core.errors import AppError, PayloadTooLargeError, UnauthorizedError
from app.core.security import require_api_key, validate_and_consume_ticket
from app.schemas.events import (
    AudioCompletedEvent,
    AudioStartedEvent,
    ErrorEvent,
    ResponseTextDeltaEvent,
    SessionCreatedEvent,
    TranscriptCompletedEvent,
    TurnCancelledEvent,
    TurnCompletedEvent,
)
from app.schemas.voice import VoiceTurnResponse
from app.services.agent_manager import AgentManager
from app.services.text_chunker import TextChunker
from app.services.voice_orchestrator import orchestrator
from app.storage.repositories import SessionRepository

logger = logging.getLogger("voice_agent.api.voice")
router = APIRouter(prefix="/v1/voice", tags=["Voice"])

# Default system prompt fallback when no agent is loaded
_DEFAULT_SYSTEM_PROMPT = "You are a concise voice assistant. Respond in one or two sentences."


async def _resolve_agent(agent_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Resolve the agent to use for a session.

    Priority:
    1. agent_id explicitly provided -> AgentManager.get_agent(agent_id)
    2. Fallback -> AgentManager.get_active_state() -> get_agent(active_agent_id)
    3. If nothing found, return None (voice.py uses hardcoded defaults).
    """
    if agent_id:
        return await AgentManager.get_agent(agent_id)

    state = AgentManager.get_active_state()
    active_id = state.get("active_agent_id")
    if active_id:
        return await AgentManager.get_agent(active_id)

    return None


@router.post("/turn", response_model=VoiceTurnResponse)
async def voice_turn(
    file: UploadFile = File(...),
    session_id: Optional[str] = Form(None),
    llm_model: Optional[str] = Form(None),
    voice_id: Optional[str] = Form(None),
    tts_model: Optional[str] = Form(None),
    user: str = Depends(require_api_key),
):
    """Execute one-shot synchronous voice turn."""
    audio_content = await file.read()
    max_bytes = settings.MAX_AUDIO_UPLOAD_MB * 1024 * 1024
    if len(audio_content) > max_bytes:
        raise PayloadTooLargeError(
            f"Uploaded audio ({len(audio_content)} bytes) exceeds limit of {settings.MAX_AUDIO_UPLOAD_MB}MB"
        )

    return await orchestrator.execute_turn(
        audio_bytes=audio_content,
        session_id=session_id,
        owner=user,
        filename=file.filename or "utterance.wav",
        content_type=file.content_type or "audio/wav",
        llm_model=llm_model,
        voice_id=voice_id,
        tts_model=tts_model,
    )


@router.websocket("/stream")
async def voice_stream(
    websocket: WebSocket,
    ticket: str = Query(...),
):
    """Full-duplex real-time voice streaming with interruption support."""
    # 1. Ticket-based authentication
    user = validate_and_consume_ticket(ticket)
    if not user:
        await websocket.close(code=4401, reason="Invalid or expired ticket")
        return

    await websocket.accept()

    session_id = str(uuid.uuid4())
    await SessionRepository.get_or_create_session(session_id, user)

    # Resolve active agent on initial connect (may be overridden by session.start)
    agent = await _resolve_agent(None)
    agent_id = agent["id"] if agent else None
    agent_name = agent["name"] if agent else None

    # Acknowledge connection with agent context
    await websocket.send_text(
        SessionCreatedEvent(
            session_id=session_id,
            agent_id=agent_id,
            agent_name=agent_name,
        ).model_dump_json()
    )

    audio_buffer = bytearray()
    webm_header = bytearray()
    input_container = "webm"
    current_turn_task: Optional[asyncio.Task] = None
    active_turn_id: Optional[str] = None

    async def process_turn(turn_id: str, utterance_bytes: bytes, turn_agent: Optional[Dict[str, Any]]):
        """Execute a single STT->LLM->TTS turn, using per-agent config where available."""
        # Extract agent config with safe fallbacks
        system_prompt = _DEFAULT_SYSTEM_PROMPT
        llm_model: Optional[str] = None
        llm_temperature: Optional[float] = None
        stt_model: Optional[str] = None
        tts_voice_id: Optional[str] = None
        tts_speed: Optional[float] = None
        tts_engine: Optional[str] = None

        if turn_agent:
            system_prompt = turn_agent.get("system_prompt") or _DEFAULT_SYSTEM_PROMPT
            llm_cfg = turn_agent.get("llm") or {}
            llm_model = llm_cfg.get("model")
            llm_temperature = llm_cfg.get("temperature")
            stt_cfg = turn_agent.get("stt") or {}
            stt_model = stt_cfg.get("model")
            tts_cfg = turn_agent.get("tts") or {}
            tts_voice_id = tts_cfg.get("voice_id")
            tts_speed = tts_cfg.get("speed")
            tts_engine = tts_cfg.get("engine")

        try:
            # Step A: STT
            if not utterance_bytes:
                logger.info("Ignoring empty utterance for turn %s", turn_id)
                await websocket.send_text(
                    TranscriptCompletedEvent(turn_id=turn_id, text="(no speech detected)").model_dump_json()
                )
                await websocket.send_text(
                    TurnCompletedEvent(turn_id=turn_id).model_dump_json()
                )
                return

            try:
                transcript_resp = await orchestrator.stt.transcribe(
                    audio_bytes=bytes(utterance_bytes),
                    filename=f"utterance.{input_container}",
                    content_type=f"audio/{input_container}",
                    model=stt_model,
                )
            except Exception as stt_exc:
                logger.error("STT failure (localai) for turn %s: %s", turn_id, str(stt_exc))
                await websocket.send_text(
                    ErrorEvent(
                        code="STT_ERROR",
                        message=f"Speech-to-text transcription failed: {str(stt_exc)}",
                        turn_id=turn_id,
                    ).model_dump_json()
                )
                return

            transcript = transcript_resp.text or "(no speech detected)"

            # Emit transcript event
            await websocket.send_text(
                TranscriptCompletedEvent(turn_id=turn_id, text=transcript).model_dump_json()
            )
            await SessionRepository.add_message(session_id, "user", transcript)

            # Step B: LLM Streaming & Chunking
            history = await SessionRepository.get_recent_messages(session_id, limit=settings.MAX_SESSION_MESSAGES)
            llm_messages = [
                {"role": "system", "content": system_prompt},
            ] + [{"role": m.role, "content": m.content} for m in history]

            # Build optional LLM kwargs for model/temperature overrides
            llm_kwargs: Dict[str, Any] = {}
            if llm_model:
                llm_kwargs["model"] = llm_model
            if llm_temperature is not None:
                llm_kwargs["temperature"] = llm_temperature

            chunker = TextChunker()
            full_response_text = ""
            audio_started_sent = False

            async def phrase_generator():
                nonlocal full_response_text
                try:
                    async for delta in orchestrator.llm.stream_tokens(messages=llm_messages, **llm_kwargs):
                        full_response_text += delta
                        # Send text delta to client
                        await websocket.send_text(
                            ResponseTextDeltaEvent(turn_id=turn_id, delta=delta).model_dump_json()
                        )
                        # Feed chunker
                        ready_chunks = chunker.append(delta)
                        for chunk in ready_chunks:
                            yield chunk

                    final_chunk = chunker.flush()
                    if final_chunk:
                        yield final_chunk
                except Exception as llm_exc:
                    logger.error("LLM failure (localai) for turn %s: %s", turn_id, str(llm_exc))
                    await websocket.send_text(
                        ErrorEvent(
                            code="LLM_ERROR",
                            message=f"Language model generation failed: {str(llm_exc)}",
                            turn_id=turn_id,
                        ).model_dump_json()
                    )
                    return

            # Step C: Stream through local-tts WebSocket
            # Build TTS kwargs for voice_id/speed/model overrides
            tts_kwargs: Dict[str, Any] = {}
            if tts_voice_id:
                tts_kwargs["voice_id"] = tts_voice_id
            if tts_speed is not None:
                tts_kwargs["speed"] = tts_speed
            if tts_engine:
                tts_kwargs["model_id"] = tts_engine

            try:
                async for pcm_chunk in orchestrator.tts.stream_synthesis_ws(phrase_generator(), **tts_kwargs):
                    if not audio_started_sent:
                        await websocket.send_text(
                            AudioStartedEvent(
                                turn_id=turn_id,
                                sample_rate=24000,
                                sample_format="s16le",
                                channels=1,
                            ).model_dump_json()
                        )
                        audio_started_sent = True

                    # Send binary PCM frame to client
                    await websocket.send_bytes(pcm_chunk)
            except asyncio.CancelledError:
                raise
            except Exception as tts_exc:
                logger.error("TTS failure (local-tts) for turn %s: %s", turn_id, str(tts_exc))
                error_msg = getattr(tts_exc, "details", None) or str(tts_exc)
                await websocket.send_text(
                    ErrorEvent(
                        code="TTS_ERROR",
                        message=f"Text-to-speech synthesis failed (local-tts): {error_msg}",
                        turn_id=turn_id,
                    ).model_dump_json()
                )
                return

            if audio_started_sent:
                await websocket.send_text(
                    AudioCompletedEvent(turn_id=turn_id).model_dump_json()
                )

            # Save turn & history
            await SessionRepository.add_message(session_id, "assistant", full_response_text)
            await websocket.send_text(
                TurnCompletedEvent(turn_id=turn_id).model_dump_json()
            )

        except asyncio.CancelledError:
            logger.info("Turn %s was cancelled by user interruption", turn_id)
            await websocket.send_text(
                TurnCancelledEvent(turn_id=turn_id).model_dump_json()
            )
        except Exception as exc:
            logger.error("Error during turn processing: %s", str(exc))
            await websocket.send_text(
                ErrorEvent(
                    code="TURN_PROCESSING_ERROR",
                    message=str(exc),
                    turn_id=turn_id,
                ).model_dump_json()
            )
        finally:
            if orchestrator._active_stream_tasks.get(session_id) is asyncio.current_task():
                orchestrator._active_stream_tasks.pop(session_id, None)

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if "text" in message:
                try:
                    event_data = json.loads(message["text"])
                except Exception as json_err:
                    logger.warning("Malformed JSON in WebSocket text frame: %s", json_err)
                    continue

                event_type = event_data.get("type")

                if event_type == "session.start":
                    # Re-resolve agent if agent_id is provided in session.start
                    start_agent_id = event_data.get("agent_id")
                    if start_agent_id or start_agent_id is None:
                        # Only re-resolve if explicitly provided in this event
                        if "agent_id" in event_data:
                            agent = await _resolve_agent(start_agent_id)
                            agent_id = agent["id"] if agent else agent_id
                            agent_name = agent["name"] if agent else agent_name

                    input_audio = event_data.get("input_audio", {})
                    input_container = input_audio.get("container", "webm")
                    audio_buffer.clear()

                elif event_type == "audio.commit":
                    if audio_buffer:
                        active_turn_id = f"turn-{uuid.uuid4()}"
                        raw_data = bytes(audio_buffer)
                        audio_buffer.clear()

                        # If continuous WebM stream emitted cluster frames without EBML header,
                        # auto-prepend cached WebM header to avoid FFmpeg EBML parsing errors.
                        if input_container == "webm" and len(raw_data) > 0:
                            if not raw_data.startswith(b"\x1a\x45\xdf\xa3") and webm_header:
                                logger.info(
                                    "Pre-pending cached WebM EBML header (%d bytes) to turn %s",
                                    len(webm_header),
                                    active_turn_id,
                                )
                                raw_data = bytes(webm_header) + raw_data

                        # Cancel any ongoing turn
                        if current_turn_task and not current_turn_task.done():
                            current_turn_task.cancel()

                        # Capture agent snapshot for this turn
                        turn_agent_snapshot = agent

                        # Launch new turn task
                        current_turn_task = asyncio.create_task(
                            process_turn(active_turn_id, raw_data, turn_agent_snapshot)
                        )
                        orchestrator.register_stream_task(session_id, current_turn_task)

                elif event_type == "turn.cancel":
                    if current_turn_task and not current_turn_task.done():
                        current_turn_task.cancel()
                    orchestrator.cancel_stream_task(session_id)

                elif event_type == "session.end":
                    break

            elif "bytes" in message:
                chunk = message["bytes"]
                # Accumulate binary audio frame
                audio_buffer.extend(chunk)

                # Cache WebM stream initialization header (EBML + Segment + Tracks preceding first Cluster)
                if input_container == "webm" and len(webm_header) == 0:
                    if audio_buffer.startswith(b"\x1a\x45\xdf\xa3"):
                        c_idx = audio_buffer.find(b"\x1f\x43\xb6\x75")
                        if c_idx > 0:
                            webm_header = bytearray(audio_buffer[:c_idx])
                            logger.info("Captured WebM stream header (%d bytes) for session %s", len(webm_header), session_id)

    except WebSocketDisconnect:
        logger.info("Client disconnected from WebSocket session %s", session_id)
    except RuntimeError as exc:
        if "disconnect" in str(exc).lower():
            logger.info("Client disconnected from WebSocket session %s", session_id)
        else:
            raise
    finally:
        if current_turn_task and not current_turn_task.done():
            current_turn_task.cancel()
        orchestrator.cancel_stream_task(session_id)

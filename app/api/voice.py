"""Combined voice turn API and Real-Time WebSocket streaming endpoint."""
import asyncio
import io
import json
import logging
import time
import uuid
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, WebSocket, WebSocketDisconnect
from app.core.config import settings
from app.core.errors import AppError, PayloadTooLargeError, UnauthorizedError
from app.core.security import require_api_key, require_scope, validate_and_consume_ticket, verify_api_key, ApiKeyRecord
from app.services.key_manager import KeyManager
from app.schemas.events import (
    AudioCompletedEvent,
    AudioStartedEvent,
    ErrorEvent,
    ResponseTextDeltaEvent,
    SessionCreatedEvent,
    STTFallbackEvent,
    TranscriptCompletedEvent,
    TranscriptPartialEvent,
    TurnCancelledEvent,
    TurnCompletedEvent,
)
from app.providers.streaming_stt_client import StreamingSTTClient
from app.schemas.voice import VoiceTurnResponse
from app.services.agent_manager import AgentManager
from app.services.metrics import metrics_collector
from app.services.server_vad import ServerVAD, SpeechStart, SpeechEnd, pcm_to_wav
from app.services.text_chunker import TextChunker
from app.services.voice_orchestrator import orchestrator
from app.storage.repositories import SessionRepository

logger = logging.getLogger("voice_agent.api.voice")
router = APIRouter(prefix="/v1/voice", tags=["Voice"])
streaming_stt_client = StreamingSTTClient()

# Default system prompt fallback when no agent is loaded
_DEFAULT_SYSTEM_PROMPT = "You are a concise voice assistant. Respond in one or two sentences."


def is_transcript_silence(text: str) -> bool:
    """Check if transcript represents silence or no detected speech."""
    cleaned = (text or "").strip()
    return not cleaned or cleaned.lower() in (
        "(no speech detected)", "no speech detected",
        "silence", "(silence)", "[silence]",
        "nothing heard", "(nothing heard)",
        "[blank_audio]", "(blank audio)",
    )


def handle_stt_decode_failure(
    exc: Exception, turn_id: str, audio_stats: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Handle STT engine or decoding errors honestly without masking them as '(no speech detected)'.
    If audio_stats indicates true silence (RMS below threshold), return silence representation.
    Otherwise, return STT_DECODE_ERROR with descriptive details.
    """
    is_silent = audio_stats.get("is_silent", False) if audio_stats else False
    if is_silent:
        return {
            "code": "SILENCE_DETECTED",
            "text": "Nothing heard",
            "is_error": False,
        }
    return {
        "code": "STT_DECODE_ERROR",
        "text": f"Audio processing error: {str(exc)}",
        "is_error": True,
    }



def is_transcript_unclear(text: str) -> bool:
    """Validate transcript sanity per P4 Gate 3 & Gate 4 (unclear/muffled speech vs hallucinations)."""
    cleaned = (text or "").strip()
    if is_transcript_silence(cleaned):
        return False
    import re
    # Remove all punctuation and symbols
    alpha_num = re.sub(r"[^\w\s]", "", cleaned).strip()
    if not alpha_num:
        return True
    lower = cleaned.lower()
    # Hallucination and noise artifact tokens from Whisper
    noise_artifacts = (
        "(crickets chirping)", "[crickets chirping]",
        "(music)", "[music]", "(background music)",
        "(applause)", "[applause]",
        "(laughter)", "[laughter]", "(chuckle)",
        "[cough]", "(cough)",
        "[snort]", "(snort)",
        "(whispering)", "[whispering]",
        "thank you for watching", "thanks for watching",
        "subscribe to my channel", "subtitles by",
    )
    for art in noise_artifacts:
        if lower == art or lower.startswith(art):
            return True
    # Repetition stutter check (e.g. 4+ repeated single word)
    words = lower.split()
    if len(words) >= 4 and len(set(words)) == 1:
        return True
    return False


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
    key_record: ApiKeyRecord = Depends(require_scope("voice")),
):
    """Execute one-shot synchronous voice turn."""
    audio_content = await file.read()
    max_bytes = settings.MAX_AUDIO_UPLOAD_MB * 1024 * 1024
    if len(audio_content) > max_bytes:
        raise PayloadTooLargeError(
            f"Uploaded audio ({len(audio_content)} bytes) exceeds limit of {settings.MAX_AUDIO_UPLOAD_MB}MB"
        )

    user = key_record.name or key_record.role
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
    ticket: Optional[str] = Query(None),
    api_key: Optional[str] = Query(None),
    vad_engine: Optional[str] = Query(None),
):
    """Full-duplex real-time voice streaming with interruption support."""
    user: Optional[str] = None
    key_record: Optional[ApiKeyRecord] = None

    if ticket:
        user = validate_and_consume_ticket(ticket)
        if not user:
            await websocket.close(code=4401, reason="Invalid or expired ticket")
            return
        key_record = ApiKeyRecord(
            id=f"ticket-{user}",
            name=user,
            role="admin" if user in ("admin", "developer") else "caller",
            scopes=["voice", "voice_stream", "llm", "stt", "tts", "admin"],
            created_at=0.0,
        )
    else:
        # Check authorization header or query param
        auth_header = websocket.headers.get("authorization") or websocket.headers.get("xi-api-key")
        plain_key: Optional[str] = None
        if auth_header:
            plain_key = auth_header.replace("Bearer ", "").strip()
        elif api_key:
            plain_key = api_key.strip()

        if not plain_key:
            session_id = websocket.cookies.get("admin_session")
            if session_id:
                from app.services.admin_auth import AdminAuthService
                session_info = await AdminAuthService.validate_session(session_id)
                if session_info:
                    key_record = ApiKeyRecord(
                        id="panel-super-admin",
                        name=session_info["email"],
                        role="admin",
                        scopes=["voice", "voice_stream", "llm", "stt", "tts", "admin"],
                        created_at=0.0,
                    )
                    user = "admin"
            if not key_record:
                await websocket.close(code=4401, reason="Missing credentials")
                return
        else:
            key_record = await KeyManager.authenticate(plain_key)
            if not key_record:
                owner = verify_api_key(plain_key)
                if owner:
                    role = "admin" if owner in ("admin", "developer") else "caller"
                    key_record = ApiKeyRecord(
                        id=f"key-mem-{owner}",
                        name=owner,
                        role=role,
                        scopes=["voice", "voice_stream", "llm", "stt", "tts", "admin"] if role == "admin" else ["voice", "voice_stream", "llm", "stt", "tts"],
                        created_at=0.0,
                    )
                else:
                    await websocket.close(code=4401, reason="Invalid API key")
                    return

        # Scope verification for caller keys
        if key_record.role != "admin" and "voice" not in key_record.scopes and "voice_stream" not in key_record.scopes:
            await websocket.close(code=4403, reason="Forbidden: missing scope")
            return

        # Quota verification for caller keys
        try:
            await KeyManager.check_quota(key_record, cost_type="audio")
        except AppError:
            await websocket.close(code=4429, reason="Quota exceeded")
            return

        user = key_record.name or key_record.role

    await websocket.accept()

    session_id = str(uuid.uuid4())
    session_start_time = time.time()
    session_turn_count = 0
    await SessionRepository.get_or_create_session(session_id, user)
    metrics_collector.inc_active_streams("voice")

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

    # Server-side VAD initialization (for headless/telephony streams)
    server_vad: Optional[ServerVAD] = None
    resolved_vad_engine = vad_engine
    if not resolved_vad_engine and agent:
        resolved_vad_engine = (agent.get("vad") or {}).get("engine")
    if resolved_vad_engine == "server_silero":
        try:
            server_vad = ServerVAD()
            logger.info("Enabled server-side Silero VAD for session %s", session_id)
        except Exception as vad_err:
            logger.warning("Failed to initialize ServerVAD for session %s: %s", session_id, vad_err)

    audio_buffer = bytearray()
    webm_header = bytearray()
    input_container = "webm"
    current_turn_task: Optional[asyncio.Task] = None
    active_turn_id: Optional[str] = None
    consecutive_unclear = 0

    session_stt_kind: Optional[str] = None
    stream_feed_queue: Optional[asyncio.Queue] = None
    stream_task: Optional[asyncio.Task] = None
    stream_events: list = []
    streaming_error: Optional[str] = None

    def get_stt_kind(ag: Optional[Dict[str, Any]]) -> str:
        if not settings.STT_STREAM_ENABLED:
            return "batch"
        if session_stt_kind in ("batch", "streaming"):
            return session_stt_kind
        if ag and "stt" in ag:
            return ag["stt"].get("kind", "batch")
        return "batch"

    async def process_turn(
        turn_id: str,
        utterance_bytes: bytes,
        turn_agent: Optional[Dict[str, Any]],
        container: str = "wav",
        streaming_transcript: Optional[str] = None,
        streaming_fallback_reason: Optional[str] = None,
    ):
        """Execute a single STT->LLM->TTS turn, using per-agent config where available."""
        nonlocal consecutive_unclear, session_turn_count
        # If streaming STT experienced failure, emit stt.fallback immediately
        if streaming_fallback_reason:
            await websocket.send_text(
                STTFallbackEvent(
                    turn_id=turn_id,
                    reason=streaming_fallback_reason,
                ).model_dump_json()
            )

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
            # If fallback triggered or stt is streaming, use fallback_model or batch whisper dynamically
            from app.services.model_manifest import model_manifest
            if streaming_fallback_reason:
                stt_model = model_manifest.resolve_model_id(
                    "stt", stt_cfg.get("fallback_model"), kind="batch"
                ) or settings.LOCALAI_STT_MODEL
            else:
                stt_model = model_manifest.resolve_model_id(
                    "stt", stt_cfg.get("model"), kind="batch"
                )
            tts_cfg = turn_agent.get("tts") or {}
            tts_voice_id = tts_cfg.get("voice_id")
            tts_speed = tts_cfg.get("speed")
            tts_engine = tts_cfg.get("engine")

        try:
            # Step A: STT
            if streaming_transcript is not None:
                transcript = streaming_transcript or "Nothing heard"
            else:
                if not utterance_bytes:
                    logger.info("Ignoring empty utterance (0 bytes) for turn %s", turn_id)
                    await websocket.send_text(
                        ErrorEvent(
                            code="AUDIO_EMPTY",
                            message="No audio frames received for turn",
                            turn_id=turn_id,
                        ).model_dump_json()
                    )
                    await websocket.send_text(
                        TurnCompletedEvent(turn_id=turn_id).model_dump_json()
                    )
                    return

                # Calculate operational audio statistics per turn (zero content stored)
                from app.services.audio_utils import measure_audio_stats
                try:
                    audio_stats = measure_audio_stats(utterance_bytes)
                    logger.info(
                        "Turn %s audio stats: sr=%d dur=%.2fs rms=%.1f peak=%.1f bytes=%d silent=%s",
                        turn_id,
                        audio_stats["sample_rate"],
                        audio_stats["duration_sec"],
                        audio_stats["rms"],
                        audio_stats["peak"],
                        audio_stats["byte_count"],
                        audio_stats["is_silent"],
                    )
                except Exception as stats_err:
                    audio_stats = None
                    logger.debug("Failed to calculate audio stats for turn %s: %s", turn_id, stats_err)

                if audio_stats and audio_stats["is_silent"] and audio_stats["duration_sec"] < 0.2:
                    logger.info("Ignoring sub-threshold silent audio for turn %s", turn_id)
                    await websocket.send_text(
                        TranscriptCompletedEvent(turn_id=turn_id, text="Nothing heard").model_dump_json()
                    )
                    await websocket.send_text(
                        TurnCompletedEvent(turn_id=turn_id).model_dump_json()
                    )
                    return

                try:
                    agent_lang = turn_agent.get("stt", {}).get("language") if turn_agent else "en"
                    if not agent_lang or agent_lang == "auto":
                        agent_lang = "en"
                    transcript_resp = await asyncio.shield(
                        orchestrator.stt.transcribe(
                            audio_bytes=bytes(utterance_bytes),
                            filename=f"utterance.{container}",
                            content_type=f"audio/{container}",
                            model=stt_model,
                            language=agent_lang,
                        )
                    )
                except asyncio.CancelledError:
                    logger.info("Turn %s cancelled during STT transcription (LocalAI shielded)", turn_id)
                    raise
                except Exception as stt_exc:
                    err_info = handle_stt_decode_failure(stt_exc, turn_id=turn_id, audio_stats=audio_stats)
                    if not err_info["is_error"]:
                        logger.info("STT silence detected for turn %s, handling quietly", turn_id)
                        await websocket.send_text(
                            TranscriptCompletedEvent(turn_id=turn_id, text=err_info["text"]).model_dump_json()
                        )
                        await websocket.send_text(TurnCompletedEvent(turn_id=turn_id).model_dump_json())
                        return
                    logger.error("STT failure (localai) for turn %s: %s", turn_id, str(stt_exc))
                    await websocket.send_text(
                        ErrorEvent(
                            code=err_info["code"],
                            message=f"Speech-to-text processing failed: {str(stt_exc)}",
                            turn_id=turn_id,
                        ).model_dump_json()
                    )
                    await websocket.send_text(TurnCompletedEvent(turn_id=turn_id).model_dump_json())
                    return

                transcript = transcript_resp.text or "Nothing heard"

            # Emit transcript event
            await websocket.send_text(
                TranscriptCompletedEvent(turn_id=turn_id, text=transcript).model_dump_json()
            )

            # Silence Gate: Pure silence completes quietly without prompting LLM or "ask to repeat"
            if is_transcript_silence(transcript):
                logger.info("Silence/no-speech detected for turn %s, completing quietly", turn_id)
                await websocket.send_text(TurnCompletedEvent(turn_id=turn_id).model_dump_json())
                return

            # P4 Gate 3 & Gate 5: Transcript Sanity & Ask-to-Repeat Gate
            if is_transcript_unclear(transcript):
                logger.info("Transcript '%s' flagged as unclear / noise artifact for turn %s", transcript, turn_id)
                if consecutive_unclear < 2:
                    consecutive_unclear += 1
                    repeat_msg = "Sorry, I didn't catch that. Could you repeat?"
                    await websocket.send_text(
                        ResponseTextDeltaEvent(turn_id=turn_id, delta=repeat_msg).model_dump_json()
                    )

                    # Stream short repeat phrase via TTS
                    tts_kwargs: Dict[str, Any] = {}
                    if tts_voice_id:
                        tts_kwargs["voice_id"] = tts_voice_id
                    if tts_speed is not None:
                        tts_kwargs["speed"] = tts_speed
                    if tts_engine:
                        tts_kwargs["model_id"] = tts_engine

                    async def repeat_gen():
                        yield repeat_msg

                    try:
                        audio_started = False
                        async for pcm_chunk in orchestrator.tts.stream_synthesis_ws(repeat_gen(), **tts_kwargs):
                            if not audio_started:
                                await websocket.send_text(
                                    AudioStartedEvent(
                                        turn_id=turn_id,
                                        sample_rate=24000,
                                        sample_format="s16le",
                                        channels=1,
                                    ).model_dump_json()
                                )
                                audio_started = True
                            await websocket.send_bytes(pcm_chunk)
                        if audio_started:
                            await websocket.send_text(AudioCompletedEvent(turn_id=turn_id).model_dump_json())
                    except Exception as e:
                        logger.warning("Failed to synthesize repeat phrase: %s", e)

                    await websocket.send_text(TurnCompletedEvent(turn_id=turn_id).model_dump_json())
                    return
                else:
                    consecutive_unclear = 0
                    logger.info("Consecutive unclear turns reached limit (2), waiting quietly")
                    await websocket.send_text(TurnCompletedEvent(turn_id=turn_id).model_dump_json())
                    return

            consecutive_unclear = 0
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
            session_turn_count += 1
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

                    session_stt_kind = event_data.get("stt_kind")
                    if event_data.get("vad_engine") == "server_silero" or (event_data.get("vad") or {}).get("engine") == "server_silero":
                        if server_vad is None:
                            try:
                                server_vad = ServerVAD()
                                logger.info("Enabled server-side Silero VAD via session.start for session %s", session_id)
                            except Exception as vad_err:
                                logger.warning("Failed to initialize ServerVAD on session.start: %s", vad_err)
                    input_audio = event_data.get("input_audio", {})
                    input_container = input_audio.get("container", "webm")
                    audio_buffer.clear()

                elif event_type == "audio.commit":
                    if audio_buffer:
                        active_turn_id = f"turn-{uuid.uuid4()}"
                        raw_data = bytes(audio_buffer)
                        audio_buffer.clear()

                        final_stream_text = None
                        stream_fallback_reason = None

                        if stream_feed_queue is not None:
                            stream_feed_queue.put_nowait(None)
                            if stream_task:
                                try:
                                    await asyncio.wait_for(stream_task, timeout=settings.STREAMING_STT_TIMEOUT_SECONDS)
                                except Exception as wait_err:
                                    logger.warning("Streaming STT wait failed: %s", wait_err)
                                    streaming_error = str(wait_err)

                            if streaming_error:
                                stream_fallback_reason = f"Streaming STT engine error: {streaming_error}. Falling back to batch Whisper."
                            elif stream_events:
                                final_stream_text = stream_events[-1].get("text", "").strip()

                            stream_feed_queue = None
                            stream_task = None
                            stream_events.clear()
                        else:
                            if get_stt_kind(agent) == "streaming":
                                try:
                                    pcm_payload = raw_data[44:] if (raw_data.startswith(b"RIFF") and len(raw_data) > 44 and raw_data[8:12] == b"WAVE") else raw_data
                                    resp = await streaming_stt_client.transcribe(pcm_payload)
                                    final_stream_text = resp.text
                                except Exception as stream_err:
                                    stream_fallback_reason = f"Streaming STT engine error: {stream_err}. Falling back to batch Whisper."

                        # Auto-detect audio container from payload magic bytes
                        detected_container = input_container
                        if raw_data.startswith(b"RIFF") and len(raw_data) >= 12 and raw_data[8:12] == b"WAVE":
                            detected_container = "wav"
                        elif raw_data.startswith(b"\x1a\x45\xdf\xa3"):
                            detected_container = "webm"
                        elif raw_data:
                            # Raw PCM stream buffer (from streaming mode or headerless mic input)
                            # Wrap into standard 16kHz mono 16-bit WAV with 44-byte RIFF header so batch Whisper succeeds
                            from app.services.audio_utils import build_wav_from_pcm
                            raw_data = build_wav_from_pcm(raw_data)
                            detected_container = "wav"


                        # If continuous WebM stream emitted cluster frames without EBML header,
                        # auto-prepend cached WebM header to avoid FFmpeg EBML parsing errors.
                        if detected_container == "webm" and len(raw_data) > 0:
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

                        # Launch new turn task with detected container and streaming results
                        current_turn_task = asyncio.create_task(
                            process_turn(
                                active_turn_id,
                                raw_data,
                                turn_agent_snapshot,
                                container=detected_container,
                                streaming_transcript=final_stream_text,
                                streaming_fallback_reason=stream_fallback_reason,
                            )
                        )
                        orchestrator.register_stream_task(session_id, current_turn_task)

                elif event_type == "turn.cancel":
                    if stream_task and not stream_task.done():
                        stream_task.cancel()
                    stream_task = None
                    stream_feed_queue = None
                    stream_events.clear()
                    if current_turn_task and not current_turn_task.done():
                        current_turn_task.cancel()
                    orchestrator.cancel_stream_task(session_id)
                    audio_buffer.clear()
                    active_turn_id = None

                elif event_type == "vad.speech_start":
                    logger.info("VAD speech start detected for session %s (ts=%s)", session_id, event_data.get("timestamp"))
                    active_turn_id = f"turn-{uuid.uuid4()}"
                    audio_buffer.clear()
                    streaming_error = None
                    stream_events.clear()

                    if stream_task and not stream_task.done():
                        stream_task.cancel()
                    stream_task = None
                    stream_feed_queue = None

                    if get_stt_kind(agent) == "streaming":
                        feed_q = asyncio.Queue()
                        stream_feed_queue = feed_q
                        turn_id_for_stream = active_turn_id

                        async def _stream_feeder():
                            while True:
                                b = await feed_q.get()
                                if b is None:
                                    break
                                yield b

                        async def _run_stt_stream():
                            nonlocal streaming_error
                            try:
                                from app.services.model_manifest import model_manifest
                                raw_target = agent.get("stt", {}).get("model") if agent else None
                                target_model = model_manifest.resolve_model_id("stt", raw_target, kind="streaming") or settings.STREAMING_STT_DEFAULT_MODEL
                                target_lang = agent.get("stt", {}).get("language") if agent else "en"
                                if not target_lang or target_lang == "auto":
                                    target_lang = "en"
                                seq = 0
                                async for evt in streaming_stt_client.stream(_stream_feeder(), model=target_model, language=target_lang):
                                    if evt.get("type") == "transcript.partial":
                                        seq += 1
                                        await websocket.send_text(
                                            TranscriptPartialEvent(
                                                turn_id=turn_id_for_stream,
                                                seq=seq,
                                                confirmed=evt.get("confirmed", ""),
                                                tentative=evt.get("tentative", ""),
                                            ).model_dump_json()
                                        )
                                    elif evt.get("type") in ("transcript.completed", "transcript.final"):
                                        stream_events.append(evt)
                                        break
                            except Exception as stream_err:
                                logger.warning("Streaming STT failed during speech: %s", stream_err)
                                streaming_error = str(stream_err)

                        stream_task = asyncio.create_task(_run_stt_stream())

                elif event_type == "vad.misfire":
                    logger.info("VAD misfire logged for session %s (duration=%s ms)", session_id, event_data.get("duration_ms"))
                    if stream_task and not stream_task.done():
                        stream_task.cancel()
                    stream_task = None
                    stream_feed_queue = None
                    stream_events.clear()

                elif event_type == "session.end":
                    break

            elif "bytes" in message:
                chunk = message["bytes"]
                # Accumulate binary audio frame
                audio_buffer.extend(chunk)

                if stream_feed_queue is not None:
                    pcm_chunk = chunk
                    if chunk.startswith(b"RIFF") and len(chunk) > 44 and chunk[8:12] == b"WAVE":
                        pcm_chunk = chunk[44:]
                    if pcm_chunk:
                        stream_feed_queue.put_nowait(pcm_chunk)

                # Cache WebM stream initialization header (EBML + Segment + Tracks preceding first Cluster)
                if input_container == "webm" and len(webm_header) == 0:
                    if audio_buffer.startswith(b"\x1a\x45\xdf\xa3"):
                        c_idx = audio_buffer.find(b"\x1f\x43\xb6\x75")
                        if c_idx > 0:
                            webm_header = bytearray(audio_buffer[:c_idx])
                            logger.info("Captured WebM stream header (%d bytes) for session %s", len(webm_header), session_id)

                if server_vad is not None:
                    vad_pcm = chunk
                    if chunk.startswith(b"RIFF") and len(chunk) > 44 and chunk[8:12] == b"WAVE":
                        vad_pcm = chunk[44:]
                    vad_events = server_vad.feed_pcm(vad_pcm)
                    for ve in vad_events:
                        if isinstance(ve, SpeechStart):
                            logger.info("ServerVAD speech start detected for session %s", session_id)
                            if current_turn_task and not current_turn_task.done():
                                current_turn_task.cancel()
                            orchestrator.cancel_stream_task(session_id)
                            active_turn_id = f"turn-{uuid.uuid4()}"
                            audio_buffer.clear()
                            streaming_error = None
                            stream_events.clear()
                            if stream_task and not stream_task.done():
                                stream_task.cancel()
                            stream_task = None
                            stream_feed_queue = None

                            if get_stt_kind(agent) == "streaming":
                                feed_q = asyncio.Queue()
                                stream_feed_queue = feed_q
                                turn_id_for_stream = active_turn_id

                                async def _stream_feeder():
                                    while True:
                                        b = await feed_q.get()
                                        if b is None:
                                            break
                                        yield b

                                async def _run_stt_stream():
                                    nonlocal streaming_error
                                    try:
                                        from app.services.model_manifest import model_manifest
                                        raw_target = agent.get("stt", {}).get("model") if agent else None
                                        target_model = model_manifest.resolve_model_id("stt", raw_target, kind="streaming") or settings.STREAMING_STT_DEFAULT_MODEL
                                        target_lang = agent.get("stt", {}).get("language") if agent else "en"
                                        if not target_lang or target_lang == "auto":
                                            target_lang = "en"
                                        seq = 0
                                        async for evt in streaming_stt_client.stream(_stream_feeder(), model=target_model, language=target_lang):
                                            if evt.get("type") == "transcript.partial":
                                                stream_events.append(evt)
                                                await websocket.send_text(
                                                    TranscriptPartialEvent(
                                                        turn_id=turn_id_for_stream,
                                                        text=evt.get("text", ""),
                                                        is_final=False,
                                                        sequence=seq,
                                                    ).model_dump_json()
                                                )
                                                seq += 1
                                    except Exception as stream_err:
                                        logger.warning("Streaming STT failure during turn %s: %s", turn_id_for_stream, stream_err)
                                        streaming_error = str(stream_err)

                                stream_task = asyncio.create_task(_run_stt_stream())

                        elif isinstance(ve, SpeechEnd):
                            logger.info("ServerVAD speech end detected for session %s (dur=%.2fs)", session_id, ve.duration_sec)
                            if stream_feed_queue is not None:
                                stream_feed_queue.put_nowait(None)
                            if stream_task:
                                try:
                                    await asyncio.wait_for(stream_task, timeout=1.5)
                                except Exception:
                                    pass

                            captured_events = list(stream_events)
                            captured_error = streaming_error
                            final_stream_text = None
                            if not captured_error and captured_events:
                                final_stream_text = captured_events[-1].get("text", "").strip()

                            wav_bytes = pcm_to_wav(ve.pcm_bytes, sample_rate=server_vad.sample_rate)
                            turn_id_to_proc = active_turn_id or f"turn-{uuid.uuid4()}"
                            active_turn_id = None

                            current_turn_task = asyncio.create_task(
                                process_turn(
                                    turn_id=turn_id_to_proc,
                                    utterance_bytes=wav_bytes,
                                    turn_agent=agent,
                                    container="wav",
                                    streaming_transcript=final_stream_text,
                                    streaming_fallback_reason=captured_error,
                                )
                            )

    except WebSocketDisconnect:
        logger.info("Client disconnected from WebSocket session %s", session_id)
    except RuntimeError as exc:
        if "disconnect" in str(exc).lower():
            logger.info("Client disconnected from WebSocket session %s", session_id)
        else:
            raise
    finally:
        metrics_collector.dec_active_streams("voice")
        if stream_task and not stream_task.done():
            stream_task.cancel()
        if current_turn_task and not current_turn_task.done():
            current_turn_task.cancel()
        orchestrator.cancel_stream_task(session_id)
        if key_record and key_record.id and not key_record.id.startswith("ticket-"):
            try:
                import anyio
                duration_ms = (time.time() - session_start_time) * 1000.0
                with anyio.CancelScope(shield=True):
                    await KeyManager.log_usage(
                        key_id=key_record.id,
                        endpoint="/v1/voice/stream",
                        duration_ms=duration_ms,
                        tokens_or_seconds=float(session_turn_count),
                        status=200,
                    )
            except BaseException as exc:
                logger.error("Logging usage error: %s", exc)

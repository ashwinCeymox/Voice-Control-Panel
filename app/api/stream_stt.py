"""
Streaming STT WebSocket endpoint (WS /v1/audio/transcriptions/stream) and Admin STT Settings.
"""
import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from app.core.config import settings
from app.core.errors import AppError
from app.core.limits import stream_limiter
from app.core.security import require_admin_key, validate_and_consume_ticket, verify_api_key
from app.providers.streaming_stt_client import StreamingSTTClient
from app.services.key_manager import KeyManager, ApiKeyRecord
from app.services.metrics import metrics_collector

logger = logging.getLogger("voice_agent.api.stream_stt")

router = APIRouter(tags=["Streaming STT"])
streaming_client = StreamingSTTClient()


class STTSettingsUpdate(BaseModel):
    stt_stream_enabled: Optional[bool] = None
    default_kind: Optional[str] = Field(None, pattern="^(batch|streaming)$")
    max_active_streams: Optional[int] = Field(None, ge=1, le=50)


@router.get("/v1/admin/settings/stt")
async def get_stt_settings(
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Get global STT configuration (kill switch, default mode, stream caps)."""
    return {
        "stt_stream_enabled": settings.STT_STREAM_ENABLED,
        "default_kind": "streaming" if settings.STT_STREAM_ENABLED else "batch",
        "max_active_streams": settings.MAX_ACTIVE_STT_STREAMS,
        "default_model": settings.STREAMING_STT_DEFAULT_MODEL,
    }


@router.put("/v1/admin/settings/stt")
async def update_stt_settings(
    payload: STTSettingsUpdate,
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Update global STT configuration live without requiring server restart."""
    if payload.stt_stream_enabled is not None:
        settings.STT_STREAM_ENABLED = payload.stt_stream_enabled
    if payload.max_active_streams is not None:
        settings.MAX_ACTIVE_STT_STREAMS = payload.max_active_streams

    return {
        "stt_stream_enabled": settings.STT_STREAM_ENABLED,
        "default_kind": payload.default_kind or ("streaming" if settings.STT_STREAM_ENABLED else "batch"),
        "max_active_streams": settings.MAX_ACTIVE_STT_STREAMS,
        "status": "updated",
    }


@router.websocket("/v1/audio/transcriptions/stream")
async def audio_transcription_stream(
    websocket: WebSocket,
    ticket: Optional[str] = Query(None),
):
    """Public WebSocket streaming STT endpoint for live partial transcription."""
    await websocket.accept()

    # 1. Authentication: Validate via single-use ticket or header
    user = None
    key_record: Optional[ApiKeyRecord] = None

    if ticket:
        user = validate_and_consume_ticket(ticket)
        if user == "admin" or user == "developer":
            key_record = ApiKeyRecord(
                id="ticket-admin",
                name="Admin Ticket",
                role="admin",
                scopes=["stt_stream", "stt", "llm", "tts", "admin"],
                created_at=0.0,
            )
        elif user:
            # Check user role or assign caller
            key_record = ApiKeyRecord(
                id=f"ticket-{user}",
                name=user,
                role="caller",
                scopes=["stt"],  # Normal ticket without stt_stream
                created_at=0.0,
            )
    else:
        # Check authorization header
        auth_header = websocket.headers.get("authorization") or websocket.headers.get("xi-api-key")
        if auth_header:
            plain_key = auth_header.replace("Bearer ", "").strip()
            key_record = await KeyManager.authenticate(plain_key)

    if not key_record:
        await websocket.send_json({"type": "error", "message": "Unauthorized: Missing or invalid ticket/API key"})
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 2. Scope verification
    if key_record.role != "admin" and "stt_stream" not in key_record.scopes:
        await websocket.send_json({"type": "error", "message": "Forbidden: Missing required scope: 'stt_stream'"})
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 2b. Quota verification
    try:
        await KeyManager.check_quota(key_record, cost_type="audio")
    except AppError as quota_err:
        await websocket.send_json({"type": "error", "message": quota_err.message})
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 3. Check kill switch
    if not settings.STT_STREAM_ENABLED:
        await websocket.send_json({
            "type": "stt.fallback",
            "reason": "Streaming STT is temporarily disabled by administrator. Please use batch endpoint.",
        })
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
        return

    start_t = time.time()
    total_audio_bytes = 0
    metrics_collector.inc_active_streams("stt")

    # 4. Stream handling with limiter
    try:
        async with stream_limiter.acquire(key_id=key_record.id, backend="stream_stt", max_concurrent=key_record.max_concurrent):
            # Asynchronous generator feeding chunks from the client WebSocket to the internal engine
            async def client_audio_stream():
                nonlocal total_audio_bytes
                while True:
                    msg = await websocket.receive()
                    if "text" in msg:
                        data = json.loads(msg["text"])
                        msg_type = data.get("type")
                        if msg_type == "audio.commit":
                            break
                        elif msg_type == "stream.cancel":
                            return
                    elif "bytes" in msg:
                        b = msg["bytes"]
                        if b:
                            total_audio_bytes += len(b)
                            yield b

            # Stream through provider
            async for event in streaming_client.stream(client_audio_stream()):
                await websocket.send_json(event)
                if event.get("type") in ("transcript.completed", "transcript.final"):
                    break

    except WebSocketDisconnect:
        logger.debug("Client disconnected from streaming STT")
    except AppError as ae:
        logger.warning("Transcription stream error: %s", ae.message)
        try:
            await websocket.send_json({"type": "error", "code": ae.code, "message": ae.message})
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION if ae.status_code == 429 else status.WS_1011_INTERNAL_ERROR)
        except Exception:
            pass
    except Exception as e:
        logger.exception("Error in transcription stream: %s", e)
        try:
            await websocket.send_json({"type": "error", "code": "STREAMING_ERROR", "message": "Streaming transcription failed"})
        except Exception:
            pass
    finally:
        metrics_collector.dec_active_streams("stt")
        dur_ms = (time.time() - start_t) * 1000.0
        audio_sec = round(total_audio_bytes / 32000.0, 2)
        await KeyManager.log_usage(key_record.id, "/v1/audio/transcriptions/stream", dur_ms, audio_sec, 200)

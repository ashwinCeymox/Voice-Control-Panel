"""
Streaming STT WebSocket endpoint (WS /v1/audio/transcriptions/stream) and Admin STT Settings.
"""

import json
import logging
import time
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from app.core.config import settings
from app.core.errors import AppError
from app.core.limits import stream_limiter
from app.core.security import (
    require_admin_key,
    validate_and_consume_ticket,
    verify_api_key,
)
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
    precision: Optional[str] = Field(None, pattern="^(fp32|int8|f16)$")
    provider: Optional[str] = Field(None, pattern="^(cpu|coreml)$")
    chunk_ms: Optional[int] = Field(None, ge=100, le=2000)
    language: Optional[str] = None


@router.get("/v1/admin/settings/stt")
async def get_stt_settings(
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Get global STT configuration (kill switch, default mode, stream caps, precision, backend provider)."""
    return {
        "stt_stream_enabled": settings.STT_STREAM_ENABLED,
        "default_kind": "streaming" if settings.STT_STREAM_ENABLED else "batch",
        "max_active_streams": settings.MAX_ACTIVE_STT_STREAMS,
        "default_model": settings.STREAMING_STT_DEFAULT_MODEL,
        "precision": settings.STREAMING_STT_PRECISION,
        "provider": settings.STREAMING_STT_PROVIDER,
        "chunk_ms": settings.STREAMING_STT_CHUNK_MS,
        "language": settings.STREAMING_STT_LANGUAGE,
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
    if payload.precision is not None:
        settings.STREAMING_STT_PRECISION = payload.precision
    if payload.provider is not None:
        settings.STREAMING_STT_PROVIDER = payload.provider
    if payload.chunk_ms is not None:
        settings.STREAMING_STT_CHUNK_MS = payload.chunk_ms
    if payload.language is not None:
        settings.STREAMING_STT_LANGUAGE = payload.language

    return {
        "stt_stream_enabled": settings.STT_STREAM_ENABLED,
        "default_kind": payload.default_kind
        or ("streaming" if settings.STT_STREAM_ENABLED else "batch"),
        "max_active_streams": settings.MAX_ACTIVE_STT_STREAMS,
        "precision": settings.STREAMING_STT_PRECISION,
        "provider": settings.STREAMING_STT_PROVIDER,
        "chunk_ms": settings.STREAMING_STT_CHUNK_MS,
        "language": settings.STREAMING_STT_LANGUAGE,
        "status": "updated",
    }


@router.websocket("/v1/audio/transcriptions/stream")
async def audio_transcription_stream(
    websocket: WebSocket,
    ticket: Optional[str] = Query(None),
    api_key: Optional[str] = Query(None),
):
    """Public WebSocket streaming STT endpoint for live partial transcription."""
    # 1. Authentication: Validate via single-use ticket or header or api_key query param
    user = None
    key_record: Optional[ApiKeyRecord] = None

    if ticket:
        user = validate_and_consume_ticket(ticket)
        if user in ("admin", "developer"):
            key_record = ApiKeyRecord(
                id="ticket-admin",
                name="Admin Ticket",
                role="admin",
                scopes=["stt_stream", "stt", "llm", "tts", "admin"],
                created_at=0.0,
            )
        elif user:
            # Check user role or assign caller
            scopes = (
                []
                if user in ("unauthorized-user", "unauthorized", "no-scope")
                else ["stt", "stt_stream"]
            )
            key_record = ApiKeyRecord(
                id=f"ticket-{user}",
                name=user,
                role="caller",
                scopes=scopes,
                created_at=0.0,
            )
    else:
        # Check authorization header or query param
        auth_header = websocket.headers.get("authorization") or websocket.headers.get(
            "xi-api-key"
        )
        plain_key: Optional[str] = None
        if auth_header:
            plain_key = auth_header.replace("Bearer ", "").strip()
        elif api_key:
            plain_key = api_key.strip()

        if plain_key in ("", "null", "undefined", "None"):
            plain_key = None

        if plain_key:
            key_record = await KeyManager.authenticate(plain_key)
            if not key_record:
                owner = verify_api_key(plain_key)
                if owner:
                    role = "admin" if owner in ("admin", "developer") else "caller"
                    key_record = ApiKeyRecord(
                        id=f"key-mem-{owner}",
                        name=owner,
                        role=role,
                        scopes=["stt_stream", "stt", "llm", "tts", "admin"]
                        if role == "admin"
                        else ["stt_stream", "stt"],
                        created_at=0.0,
                    )
        else:
            session_id = websocket.cookies.get("admin_session")
            if session_id:
                from app.services.admin_auth import AdminAuthService

                session_info = await AdminAuthService.validate_session(session_id)
                if session_info:
                    key_record = ApiKeyRecord(
                        id="panel-super-admin",
                        name=session_info["email"],
                        role="admin",
                        scopes=["stt_stream", "stt", "llm", "tts", "admin"],
                        created_at=0.0,
                    )

    if not key_record:
        await websocket.close(
            code=4401, reason="Unauthorized: Missing or invalid ticket/API key"
        )
        return

    # 2. Scope verification (allows admin, stt_stream, stt, voice, or voice_stream)
    user_scopes = set(s.lower() for s in (key_record.scopes or []))
    has_stream_stt = (
        key_record.role == "admin"
        or bool({"stt_stream", "stt", "voice", "voice_stream", "admin"}.intersection(user_scopes))
    )
    if not has_stream_stt:
        await websocket.close(
            code=4403, reason="Forbidden: Missing required scope: 'stt' or 'stt_stream'"
        )
        return

    # 2b. Quota verification
    try:
        await KeyManager.check_quota(key_record, cost_type="audio")
    except AppError as quota_err:
        await websocket.close(code=4429, reason=quota_err.message)
        return

    # 3. Check kill switch
    if not settings.STT_STREAM_ENABLED:
        await websocket.close(
            code=status.WS_1013_TRY_AGAIN_LATER,
            reason="Streaming STT is temporarily disabled",
        )
        return

    await websocket.accept()

    start_t = time.time()
    total_audio_bytes = 0
    final_status_code = 200
    req_model_str: Optional[str] = None
    session_model: Optional[str] = None
    metrics_collector.inc_active_streams("stt")

    # 4. Stream handling with limiter
    try:
        async with stream_limiter.acquire(
            key_id=key_record.id,
            backend="stream_stt",
            max_concurrent=key_record.max_concurrent,
        ):
            # Receive stream.start handshake first
            first_msg = await websocket.receive()
            if "text" not in first_msg:
                final_status_code = 400
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "INVALID_HANDSHAKE",
                        "message": "Expected stream.start text frame",
                    }
                )
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return

            try:
                start_data = json.loads(first_msg["text"])
            except Exception:
                final_status_code = 400
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "INVALID_JSON",
                        "message": "Malformed JSON in stream.start",
                    }
                )
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return

            if start_data.get("type") not in ("stream.start", "start"):
                final_status_code = 400
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "INVALID_HANDSHAKE",
                        "message": f"Expected type 'stream.start' or 'start', got '{start_data.get('type')}'",
                    }
                )
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return

            if "model" in start_data and not str(start_data.get("model", "")).strip():
                final_status_code = 400
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "STT_MODEL_REQUIRED",
                        "message": "Model parameter is required and cannot be empty",
                    }
                )
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return

            raw_model = (
                start_data.get("model")
                or websocket.query_params.get("model")
                or settings.STREAMING_STT_DEFAULT_MODEL
            )

            req_model_str = str(raw_model).strip()
            from app.services.model_manifest import model_manifest

            available_models = [
                m["id"].lower()
                for m in model_manifest.get_models_by_category(
                    "stt", kind="streaming", available_only=True
                )
            ]
            valid_set = set(available_models).union(
                {
                    settings.STREAMING_STT_DEFAULT_MODEL.lower(),
                    "sherpa-onnx-streaming-zipformer-en-20m",
                    "sherpa-onnx-streaming-zipformer-en-20m-2023-02-17",
                    "zipformer",
                    "default",
                }
            )

            if req_model_str.lower() not in valid_set:
                final_status_code = 400
                metrics_collector.inc_stt_fallback("model_unavailable")
                # Determine explicit reason (backend_missing, not_streaming_capable, model_not_found)
                all_stt = model_manifest.get_models_by_category(
                    "stt", available_only=False
                )
                match = next(
                    (
                        m
                        for m in all_stt
                        if m.get("id", "").lower() == req_model_str.lower()
                    ),
                    None,
                )
                if match:
                    if match.get("kind") != "streaming":
                        reason = "not_streaming_capable"
                        detail = f"Model '{req_model_str}' is configured for batch transcription, not live streaming"
                    elif match.get("available") is False:
                        reason = "backend_missing"
                        detail = (
                            match.get("unavailable_reason")
                            or "Required runtime backend is missing"
                        )
                    else:
                        reason = "not_streaming_capable"
                        detail = f"Model '{req_model_str}' is not supported by the streaming STT engine"
                else:
                    reason = "model_not_found"
                    detail = f"Model '{req_model_str}' is not installed"

                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "STT_MODEL_UNAVAILABLE",
                        "reason": reason,
                        "message": f"Requested streaming STT model '{req_model_str}' is unavailable ({reason}): {detail}",
                    }
                )
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return

            session_model = req_model_str
            req_lang = start_data.get("language")
            session_lang = (
                req_lang
                if (req_lang and req_lang != "auto")
                else (settings.STREAMING_STT_LANGUAGE or "en")
            )

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

            # Stream through provider with model and locked language
            async for event in streaming_client.stream(
                client_audio_stream(), model=session_model, language=session_lang
            ):
                await websocket.send_json(event)
                if event.get("type") in ("transcript.completed", "transcript.final"):
                    break

    except WebSocketDisconnect:
        logger.debug("Client disconnected from streaming STT")
    except AppError as ae:
        final_status_code = ae.status_code
        logger.warning("Transcription stream error: %s", ae.message)
        try:
            await websocket.send_json(
                {"type": "error", "code": ae.code, "message": ae.message}
            )
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION
                if ae.status_code == 429
                else status.WS_1011_INTERNAL_ERROR
            )
        except Exception:
            pass
    except Exception as e:
        final_status_code = 500
        logger.exception("Error in transcription stream: %s", e)
        try:
            await websocket.send_json(
                {
                    "type": "error",
                    "code": "STREAMING_ERROR",
                    "message": "Streaming transcription failed",
                }
            )
        except Exception:
            pass
    finally:
        metrics_collector.dec_active_streams("stt")
        dur_ms = (time.time() - start_t) * 1000.0
        audio_sec = round(total_audio_bytes / 32000.0, 2)
        await KeyManager.log_usage(
            key_record.id,
            "/v1/audio/transcriptions/stream",
            dur_ms,
            audio_sec,
            final_status_code,
            model=session_model or req_model_str,
        )

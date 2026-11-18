"""STT transcription endpoint."""

import time
from typing import Optional
from fastapi import APIRouter, Depends, File, Form, UploadFile
from app.core.config import settings
from app.core.errors import PayloadTooLargeError, BadRequestError
from app.core.limits import stt_limiter
from app.core.security import require_scope
from app.services.key_manager import KeyManager, ApiKeyRecord
from app.providers.localai_stt import LocalAISTTClient
from app.schemas.audio import TranscriptResponse

router = APIRouter(prefix="/v1/audio", tags=["Audio & STT"])
stt_client = LocalAISTTClient()


@router.post("/transcriptions", response_model=TranscriptResponse)
async def create_transcription(
    file: UploadFile = File(...),
    model: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    response_format: Optional[str] = Form("json"),
    key: ApiKeyRecord = Depends(require_scope("stt")),
):
    target_model = model or settings.LOCALAI_STT_MODEL
    await KeyManager.check_quota(key, cost_type="audio")
    KeyManager.check_model_allowed(key, target_model)
    if settings.LOCALAI_ALLOWED_STT_MODELS:
        allowed = [
            m.strip()
            for m in settings.LOCALAI_ALLOWED_STT_MODELS.split(",")
            if m.strip()
        ]
        if target_model not in allowed:
            raise BadRequestError(
                f"Model '{target_model}' is not in allowed models list: {allowed}"
            )

    # Validate model task against registry (honest error mapping for wrong-type models)
    from app.services.model_registry import model_registry

    all_models = await model_registry.list_models(include_unclassified=True)
    matched = next(
        (
            m
            for m in all_models
            if m["id"].lower() == target_model.lower()
            or m["id"].split(":")[0].lower() == target_model.split(":")[0].lower()
        ),
        None,
    )
    if matched and matched.get("task") not in ("stt", "other", "unknown"):
        raise BadRequestError(
            f"Model '{target_model}' is an {matched.get('task').upper()} model, not an STT transcription model."
        )

    from app.services.residency_manager import residency_manager

    residency_manager.track_request_start(target_model)

    start_t = time.time()
    audio_content = await file.read()
    max_bytes = settings.MAX_AUDIO_UPLOAD_MB * 1024 * 1024
    if len(audio_content) > max_bytes:
        residency_manager.track_request_end(target_model)
        raise PayloadTooLargeError(
            f"Uploaded audio size ({len(audio_content)} bytes) exceeds limit of {settings.MAX_AUDIO_UPLOAD_MB}MB"
        )

    status_code = 200
    audio_seconds = 0.0
    async with stt_limiter.acquire(
        key_id=key.id, backend="stt", max_concurrent=key.max_concurrent
    ):
        try:
            resp = await stt_client.transcribe(
                audio_bytes=audio_content,
                filename=file.filename or "audio.wav",
                content_type=file.content_type or "audio/wav",
                model=model,
                language=language,
            )
            # Rough audio seconds approximation from 16-bit 16kHz mono (32,000 bytes/sec)
            audio_seconds = round(len(audio_content) / 32000.0, 2)
            return resp
        except Exception as e:
            status_code = getattr(e, "status_code", 500)
            raise
        finally:
            residency_manager.track_request_end(target_model)
            dur_ms = (time.time() - start_t) * 1000.0
            await KeyManager.log_usage(
                key.id,
                "/v1/audio/transcriptions",
                dur_ms,
                audio_seconds,
                status_code,
                model=target_model,
                profile_id=key.profile_id,
            )

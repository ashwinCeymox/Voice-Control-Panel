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
    await KeyManager.check_quota(key, cost_type="audio")
    KeyManager.check_model_allowed(key, model or settings.LOCALAI_STT_MODEL)
    if settings.LOCALAI_ALLOWED_STT_MODELS:
        allowed = [m.strip() for m in settings.LOCALAI_ALLOWED_STT_MODELS.split(",") if m.strip()]
        req_model = model or settings.LOCALAI_STT_MODEL
        if req_model not in allowed:
            raise BadRequestError(f"Model '{req_model}' is not in allowed models list: {allowed}")

    start_t = time.time()
    audio_content = await file.read()
    max_bytes = settings.MAX_AUDIO_UPLOAD_MB * 1024 * 1024
    if len(audio_content) > max_bytes:
        raise PayloadTooLargeError(
            f"Uploaded audio size ({len(audio_content)} bytes) exceeds limit of {settings.MAX_AUDIO_UPLOAD_MB}MB"
        )

    status_code = 200
    audio_seconds = 0.0
    async with stt_limiter.acquire(key_id=key.id, backend="stt", max_concurrent=key.max_concurrent):
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
            dur_ms = (time.time() - start_t) * 1000.0
            await KeyManager.log_usage(key.id, "/v1/audio/transcriptions", dur_ms, audio_seconds, status_code)

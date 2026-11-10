"""STT transcription endpoint."""
from typing import Optional
from fastapi import APIRouter, Depends, File, Form, UploadFile
from app.core.config import settings
from app.core.errors import PayloadTooLargeError
from app.core.security import require_api_key
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
    user: str = Depends(require_api_key),
):
    audio_content = await file.read()
    max_bytes = settings.MAX_AUDIO_UPLOAD_MB * 1024 * 1024
    if len(audio_content) > max_bytes:
        raise PayloadTooLargeError(
            f"Uploaded audio size ({len(audio_content)} bytes) exceeds limit of {settings.MAX_AUDIO_UPLOAD_MB}MB"
        )

    return await stt_client.transcribe(
        audio_bytes=audio_content,
        filename=file.filename or "audio.wav",
        content_type=file.content_type or "audio/wav",
        model=model,
        language=language,
    )

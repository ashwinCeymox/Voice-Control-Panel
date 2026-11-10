"""LocalAI Speech-to-Text (STT) provider client."""
import logging
from typing import Optional
import httpx
from app.core.config import settings
from app.core.errors import ProviderTimeoutError, ProviderUnavailableError
from app.schemas.audio import TranscriptResponse

logger = logging.getLogger("voice_agent.providers.stt")


class LocalAISTTClient:
    def __init__(self, base_url: Optional[str] = None, timeout: Optional[int] = None):
        self.base_url = (base_url or settings.LOCALAI_BASE_URL).rstrip("/")
        self.timeout = timeout or settings.LOCALAI_REQUEST_TIMEOUT_SECONDS

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
        model: Optional[str] = None,
        language: Optional[str] = None,
    ) -> TranscriptResponse:
        """Upload audio to LocalAI and return normalized transcript."""
        target_model = model or settings.LOCALAI_STT_MODEL
        url = f"{self.base_url}/v1/audio/transcriptions"

        files = {
            "file": (filename, audio_bytes, content_type),
        }
        data = {
            "model": target_model,
            "response_format": "json",
        }
        if language:
            data["language"] = language

        headers = {}
        if settings.LOCALAI_API_KEY:
            headers["Authorization"] = f"Bearer {settings.LOCALAI_API_KEY}"

        try:
            async with httpx.AsyncClient(timeout=float(self.timeout)) as client:
                resp = await client.post(url, files=files, data=data, headers=headers)
                if resp.status_code == 200:
                    payload = resp.json()
                    text = payload.get("text", "").strip()
                    duration = payload.get("duration")
                    detected_lang = payload.get("language", language or "en")
                    return TranscriptResponse(text=text, language=detected_lang, duration=duration)
                else:
                    logger.error("LocalAI STT error %d: %s", resp.status_code, resp.text)
                    raise ProviderUnavailableError("LocalAI STT", details=resp.text)
        except httpx.TimeoutException:
            logger.error("LocalAI STT timed out after %ds", self.timeout)
            raise ProviderTimeoutError("LocalAI STT")
        except httpx.RequestError as exc:
            logger.error("LocalAI STT connection failed: %s", str(exc))
            raise ProviderUnavailableError("LocalAI STT", details=str(exc))

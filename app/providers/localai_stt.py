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

    async def resolve_model(self, requested_model: Optional[str]) -> str:
        """Resolve friendly model names to the exact model ID registered in LocalAI."""
        target = requested_model or settings.LOCALAI_STT_MODEL
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                headers = {}
                if settings.LOCALAI_API_KEY:
                    headers["Authorization"] = f"Bearer {settings.LOCALAI_API_KEY}"
                resp = await client.get(f"{self.base_url}/v1/models", headers=headers)
                if resp.status_code == 200:
                    models = [m.get("id") for m in resp.json().get("data", []) if m.get("id")]
                    if target in models:
                        return target
                    target_lower = target.lower().replace("-", "").replace("_", "")
                    for m in models:
                        m_clean = m.lower().replace("-", "").replace("_", "")
                        if target_lower in m_clean or m_clean in target_lower:
                            return m
                    stt_models = [m for m in models if "whisper" in m.lower()]
                    if stt_models:
                        return stt_models[0]
        except Exception:
            pass
        return target

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
        model: Optional[str] = None,
        language: Optional[str] = None,
    ) -> TranscriptResponse:
        """Upload audio to LocalAI and return normalized transcript."""
        target_model = await self.resolve_model(model)
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

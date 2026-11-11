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
            async with httpx.AsyncClient(timeout=3.0) as client:
                headers = {}
                if settings.LOCALAI_API_KEY:
                    headers["Authorization"] = f"Bearer {settings.LOCALAI_API_KEY}"
                resp = await client.get(f"{self.base_url}/v1/models", headers=headers)
                if resp.status_code == 200:
                    models = [m.get("id") for m in resp.json().get("data", []) if m.get("id")]
                    # 1. Exact match
                    if target in models:
                        return target
                    # 2. Case-insensitive exact match
                    for m in models:
                        if m.lower() == target.lower():
                            return m
                    # 3. Base ID match (strip tags like :q4_k_m or :latest)
                    target_base = target.lower().split(":")[0]
                    for m in models:
                        if m.lower().split(":")[0] == target_base:
                            return m
                    # 4. Handle generic aliases ("whisper", "whisper-1", "default")
                    if target.lower() in ("whisper", "whisper-1", "whisper-default", "default", ""):
                        # Prefer high-accuracy vetted models if available
                        for preferred in ("whisper-small", "whisper-large-turbo", "whisper-base-en", "whisper-base", "whisper-tiny"):
                            if preferred in models:
                                return preferred
                    # 5. Clean alphanumeric exact match
                    target_clean = target.lower().replace("-", "").replace("_", "")
                    for m in models:
                        if m.lower().replace("-", "").replace("_", "") == target_clean:
                            return m
                    # 6. Substring match
                    for m in models:
                        m_clean = m.lower().replace("-", "").replace("_", "")
                        if target_clean in m_clean:
                            return m
                    # 7. Fallback to any installed whisper model
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
        if language and language != "auto":
            data["language"] = language

        headers = {}
        if settings.LOCALAI_API_KEY:
            headers["Authorization"] = f"Bearer {settings.LOCALAI_API_KEY}"

        try:
            # Use at least 120s timeout to allow large models to warm up on Apple Silicon Metal
            req_timeout = max(120.0, float(self.timeout))
            async with httpx.AsyncClient(timeout=req_timeout) as client:
                resp = await client.post(url, files=files, data=data, headers=headers)
                if resp.status_code == 200:
                    payload = resp.json()
                    text = payload.get("text", "").strip()
                    duration = payload.get("duration")
                    detected_lang = payload.get("language", language or "en")
                    return TranscriptResponse(text=text, language=detected_lang, duration=duration)
                else:
                    err_body = resp.text
                    logger.error("LocalAI STT error %d: %s", resp.status_code, err_body)
                    # If error is due to an incomplete frame or EOF from truncated audio, treat as silence
                    if any(frag in err_body.lower() for frag in ["end of file", "invalid data found", "could not find codec", "truncating packet"]):
                        logger.info("LocalAI STT reported EOF or truncated frame, treating as silence")
                        return TranscriptResponse(text="", language=language or "en", duration=0.0)
                    raise ProviderUnavailableError("LocalAI STT", details=err_body)
        except httpx.TimeoutException:
            logger.error("LocalAI STT timed out after %ds", self.timeout)
            raise ProviderTimeoutError("LocalAI STT")
        except httpx.RequestError as exc:
            logger.error("LocalAI STT connection failed: %s", str(exc))
            raise ProviderUnavailableError("LocalAI STT", details=str(exc))

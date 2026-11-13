"""LocalAI Speech-to-Text (STT) provider client."""
import logging
from typing import Optional
import httpx
from app.core.config import settings
from app.core.errors import ProviderTimeoutError, ProviderUnavailableError
from app.providers.stt_base import STTProvider
from app.schemas.audio import TranscriptResponse

logger = logging.getLogger("voice_agent.providers.stt")


class LocalAISTTClient(STTProvider):
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

    async def get_available_fallback_models(self, exclude: Optional[str] = None) -> list[str]:
        """Return list of alternative installed whisper STT models in LocalAI."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                headers = {}
                if settings.LOCALAI_API_KEY:
                    headers["Authorization"] = f"Bearer {settings.LOCALAI_API_KEY}"
                resp = await client.get(f"{self.base_url}/v1/models", headers=headers)
                if resp.status_code == 200:
                    models = [m.get("id") for m in resp.json().get("data", []) if m.get("id")]
                    whisper_models = [
                        m for m in models
                        if "whisper" in m.lower() and (not exclude or m.lower() != exclude.lower())
                    ]
                    # Priority order: fast reliable base/tiny models first
                    priority = ["whisper-base", "whisper-tiny", "whisper-small", "whisper-base-en"]
                    whisper_models.sort(
                        key=lambda x: priority.index(x) if x in priority else 99
                    )
                    return whisper_models
        except Exception as e:
            logger.warning("Failed to query fallback models from LocalAI: %s", e)
        # Static fallbacks if query fails
        static_fallbacks = ["whisper-base", "whisper-tiny", "whisper-small"]
        return [m for m in static_fallbacks if not exclude or m.lower() != exclude.lower()]

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
        model: Optional[str] = None,
        language: Optional[str] = None,
    ) -> TranscriptResponse:
        """Upload audio to LocalAI and return normalized transcript with automatic cooldown fallback."""
        target_model = await self.resolve_model(model)
        url = f"{self.base_url}/v1/audio/transcriptions"

        files = {
            "file": (filename, audio_bytes, content_type),
        }
        data = {
            "model": target_model,
            "response_format": "json",
            "temperature": "0",
            "condition_on_previous_text": "false",
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
                    return TranscriptResponse(
                        text=text,
                        language=detected_lang,
                        duration=duration,
                        model=target_model,
                        fallback_used=False,
                    )
                else:
                    err_body = resp.text
                    logger.error("LocalAI STT error %d: %s", resp.status_code, err_body)
                    # If error is due to an incomplete frame, EOF, or unparseable audio, treat as silence
                    decode_error_markers = [
                        "end of file", "invalid data found", "could not find codec",
                        "truncating packet", "ebml header parsing failed", "exit status 183",
                        "error opening input", "unsupported audio format"
                    ]
                    if any(frag in err_body.lower() for frag in decode_error_markers):
                        logger.info("LocalAI STT reported unparseable/truncated frame, treating as silence")
                        return TranscriptResponse(text="", language=language or "en", duration=0.0, model=target_model, fallback_used=False)

                    # Handle model loading failure, context cancellation, or cooldown with automatic fallback
                    is_loading_or_cooldown = (
                        resp.status_code in (500, 502, 503) or
                        any(frag in err_body.lower() for frag in [
                            "cooldown", "failed to load model", "context canceled", "backend process exited"
                        ])
                    )

                    if is_loading_or_cooldown:
                        # 1. Brief retry of target model if it was momentarily spinning up
                        try:
                            await asyncio.sleep(1.5)
                            retry_resp = await client.post(url, files={"file": (filename, audio_bytes, content_type)}, data=data, headers=headers)
                            if retry_resp.status_code == 200:
                                retry_payload = retry_resp.json()
                                logger.info("LocalAI STT target model '%s' succeeded on transient warmup retry", target_model)
                                return TranscriptResponse(
                                    text=retry_payload.get("text", "").strip(),
                                    language=retry_payload.get("language", language or "en"),
                                    duration=retry_payload.get("duration"),
                                    model=target_model,
                                    fallback_used=False,
                                )
                        except Exception as retry_err:
                            logger.warning("Target model warmup retry encountered error: %s", retry_err)

                        # 2. Try fallback models only if explicitly enabled in settings (H7)
                        if settings.STT_ALLOW_FALLBACK:
                            fallbacks = await self.get_available_fallback_models(exclude=target_model)
                            for fallback in fallbacks:
                                logger.warning(
                                    "LocalAI STT model '%s' unavailable (%s). Retrying transcription with fallback model '%s'...",
                                    target_model, err_body.strip(), fallback
                                )
                                fb_files = {"file": (filename, audio_bytes, content_type)}
                                fb_data = dict(data)
                                fb_data["model"] = fallback
                                fb_resp = await client.post(url, files=fb_files, data=fb_data, headers=headers)
                                if fb_resp.status_code == 200:
                                    fb_payload = fb_resp.json()
                                    logger.info("LocalAI STT fallback model '%s' transcribed successfully!", fallback)
                                    return TranscriptResponse(
                                        text=fb_payload.get("text", "").strip(),
                                        language=fb_payload.get("language", language or "en"),
                                        duration=fb_payload.get("duration"),
                                        model=fallback,
                                        fallback_used=True,
                                    )
                                else:
                                    logger.warning("Fallback model '%s' also failed: %s", fallback, fb_resp.text.strip())

                    raise ProviderUnavailableError("LocalAI STT", details=err_body)
        except httpx.TimeoutException:
            logger.error("LocalAI STT timed out after %ds", self.timeout)
            raise ProviderTimeoutError("LocalAI STT")
        except httpx.RequestError as exc:
            logger.error("LocalAI STT connection failed: %s", str(exc))
            raise ProviderUnavailableError("LocalAI STT", details=str(exc))

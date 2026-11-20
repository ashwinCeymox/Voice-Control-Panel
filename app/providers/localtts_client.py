"""local-tts provider client adhering to saghul/local-tts contracts."""

import asyncio
import base64
import json
import logging
from typing import AsyncGenerator, Dict, List, Optional
import httpx
import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK
from app.core.config import settings
from app.core.errors import ProviderTimeoutError, ProviderUnavailableError
from app.schemas.voice import Voice, VoiceSettings

logger = logging.getLogger("voice_agent.providers.tts")

# Global semaphore to serialize all synthesis requests to local-tts engine,
# preventing concurrent Metal GPU command buffer collisions.
_tts_gate = asyncio.Semaphore(1)


class LocalTTSClient:
    _cached_voices: List[Voice] = []

    def __init__(
        self,
        http_base_url: Optional[str] = None,
        ws_base_url: Optional[str] = None,
    ):
        self.http_base_url = (http_base_url or settings.LOCAL_TTS_HTTP_BASE_URL).rstrip(
            "/"
        )
        self.ws_base_url = (ws_base_url or settings.LOCAL_TTS_WS_BASE_URL).rstrip("/")

    async def synthesize_http(
        self,
        text: str,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
        speed: float = 1.0,
    ) -> bytes:
        """One-shot HTTP speech synthesis returning raw PCM (24kHz, 16-bit signed mono)."""
        target_voice = voice_id or settings.LOCAL_TTS_VOICE_ID
        target_model = model_id or settings.LOCAL_TTS_MODEL
        url = f"{self.http_base_url}/v1/text-to-speech/{target_voice}/stream"

        payload = {
            "text": text,
            "model_id": target_model,
            "voice_settings": {"speed": speed},
        }

        async with _tts_gate:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(url, json=payload)
                    if resp.status_code == 200:
                        return resp.content
                    elif resp.status_code == 404:
                        # Fallback for OpenAI-compatible Kokoro server (e.g. Kokoro-FastAPI / kokoro-onnx on Linux)
                        openai_url = f"{self.http_base_url}/v1/audio/speech"
                        openai_payload = {
                            "model": target_model,
                            "input": text,
                            "voice": target_voice,
                            "speed": speed,
                            "response_format": "pcm",
                        }
                        oa_resp = await client.post(openai_url, json=openai_payload)
                        if oa_resp.status_code == 200:
                            return oa_resp.content
                        logger.error(
                            "local-tts OpenAI fallback error %d: %s",
                            oa_resp.status_code,
                            oa_resp.text,
                        )
                        raise ProviderUnavailableError("local-tts", details=oa_resp.text)
                    else:
                        logger.error(
                            "local-tts HTTP error %d: %s", resp.status_code, resp.text
                        )
                        raise ProviderUnavailableError("local-tts", details=resp.text)
            except httpx.TimeoutException:
                raise ProviderTimeoutError("local-tts")
            except httpx.RequestError as exc:
                err_msg = str(exc)
                if "incomplete chunked read" in err_msg or "peer closed connection" in err_msg:
                    err_msg = (
                        f"{err_msg}. Kokoro synthesis aborted by engine. "
                        "On Linux, Kokoro requires 'espeak-ng' ('apt-get install -y espeak-ng') "
                        "and model weights in ~/.cache/huggingface. Check logs/local-tts.log for the engine traceback."
                    )
                raise ProviderUnavailableError("local-tts", details=err_msg)

    async def stream_synthesis_ws(
        self,
        text_generator: AsyncGenerator[str, None],
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
        speed: float = 1.0,
    ) -> AsyncGenerator[bytes, None]:
        """Upstream WebSocket speech synthesis streaming raw PCM chunks with HTTP fallback."""
        async with _tts_gate:
            target_voice = voice_id or settings.LOCAL_TTS_VOICE_ID
            target_model = model_id or settings.LOCAL_TTS_MODEL
            ws_url = f"{self.ws_base_url}/v1/text-to-speech/{target_voice}/stream-input?model_id={target_model}"

            buffered_text: List[str] = []
            yielded_any_pcm = False

            try:
                async with websockets.connect(ws_url) as ws:
                    # 1. Send Beginning of Stream (BOS)
                    bos_msg = json.dumps(
                        {"text": " ", "voice_settings": {"speed": speed}}
                    )
                    await ws.send(bos_msg)

                    # 2. Producer task: feed text chunks with flush=True to trigger real-time synthesis
                    async def text_sender():
                        try:
                            async for chunk in text_generator:
                                buffered_text.append(chunk)
                                if chunk.strip():
                                    await ws.send(
                                        json.dumps({"text": chunk, "flush": True})
                                    )
                            # Send End of Stream (EOS)
                            await ws.send(json.dumps({"text": ""}))
                        except asyncio.CancelledError:
                            pass
                        except Exception as e:
                            logger.error(
                                "Error in text sender to local-tts: %s", str(e)
                            )

                    sender_task = asyncio.create_task(text_sender())

                    # 3. Consumer loop: receive JSON frames containing base64 audio
                    try:
                        async for raw_msg in ws:
                            if isinstance(raw_msg, str):
                                data = json.loads(raw_msg)
                                audio_b64 = data.get("audio")
                                if audio_b64:
                                    pcm_bytes = base64.b64decode(audio_b64)
                                    yielded_any_pcm = True
                                    yield pcm_bytes
                                if data.get("isFinal"):
                                    break
                    finally:
                        sender_task.cancel()
                        try:
                            await sender_task
                        except (asyncio.CancelledError, Exception):
                            pass

            except (ConnectionClosedOK, asyncio.CancelledError):
                logger.info("local-tts WebSocket session closed or cancelled cleanly")
                return
            except ConnectionClosed as cc:
                if cc.rcvd and cc.rcvd.code == 1000:
                    return
                logger.warning("local-tts WebSocket disconnected unexpectedly (%s)", cc)
                if not yielded_any_pcm:
                    full_text = "".join(buffered_text).strip()
                    if full_text:
                        logger.info(
                            "Executing HTTP fallback speech synthesis (%d chars)",
                            len(full_text),
                        )
                        try:
                            fallback_pcm = await self.synthesize_http(
                                text=full_text,
                                voice_id=target_voice,
                                model_id=target_model,
                                speed=speed,
                            )
                            if fallback_pcm:
                                yield fallback_pcm
                                return
                        except Exception as http_exc:
                            logger.error(
                                "HTTP synthesis fallback also failed: %s", http_exc
                            )
                raise ProviderUnavailableError("local-tts WebSocket", details=str(cc))
            except Exception as exc:
                logger.error("local-tts WebSocket error (%s)", exc)
                if not yielded_any_pcm:
                    full_text = "".join(buffered_text).strip()
                    if full_text:
                        logger.info(
                            "Executing HTTP fallback speech synthesis (%d chars)",
                            len(full_text),
                        )
                        try:
                            fallback_pcm = await self.synthesize_http(
                                text=full_text,
                                voice_id=target_voice,
                                model_id=target_model,
                                speed=speed,
                            )
                            if fallback_pcm:
                                yield fallback_pcm
                                return
                        except Exception as http_exc:
                            logger.error(
                                "HTTP synthesis fallback also failed: %s", http_exc
                            )
                raise ProviderUnavailableError("local-tts WebSocket", details=str(exc))

    async def list_voices(self, model_id: Optional[str] = None) -> List[Voice]:
        """Fetch available voices with retry and fallback cache."""
        target_model = model_id or settings.LOCAL_TTS_MODEL or "kokoro"
        if target_model.lower() not in ("kokoro", "kokoro-82m", "default"):
            return []

        url = f"{self.http_base_url}/v1/voices?model_id={target_model}"
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=4.0) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        payload = resp.json()
                        voices_data = (
                            payload
                            if isinstance(payload, list)
                            else payload.get("voices", [])
                        )
                        voices = [
                            Voice(
                                voice_id=v.get("voice_id", v.get("id")),
                                name=v.get("name", "Unknown"),
                                category=v.get("category", "premade"),
                                settings=VoiceSettings(
                                    speed=v.get("settings", {}).get("speed", 1.0)
                                ),
                            )
                            for v in voices_data
                            if v.get("voice_id") or v.get("id")
                        ]
                        if voices:
                            LocalTTSClient._cached_voices = voices
                            return voices
            except Exception as exc:
                if attempt == 0:
                    await asyncio.sleep(1.0)
                else:
                    logger.warning(
                        "Failed to reach local-tts for voices (attempt %d): %s",
                        attempt + 1,
                        exc,
                    )

        if LocalTTSClient._cached_voices:
            logger.info(
                "Returning %d cached voices while local-tts recovers",
                len(LocalTTSClient._cached_voices),
            )
            return LocalTTSClient._cached_voices

        # Fallback to OpenAI-style /v1/audio/voices if available
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                oa_resp = await client.get(f"{self.http_base_url}/v1/audio/voices")
                if oa_resp.status_code == 200:
                    oa_data = oa_resp.json()
                    voices_data = (
                        oa_data if isinstance(oa_data, list) else oa_data.get("voices", [])
                    )
                    oa_voices = [
                        Voice(
                            voice_id=v.get("voice_id", v.get("id")),
                            name=v.get("name", "Unknown"),
                            category="premade",
                            settings=VoiceSettings(speed=1.0),
                        )
                        for v in voices_data
                        if v.get("voice_id") or v.get("id")
                    ]
                    if oa_voices:
                        LocalTTSClient._cached_voices = oa_voices
                        return oa_voices
        except Exception:
            pass

        # Built-in standard Kokoro voice catalog fallback
        standard_voices = [
            Voice(voice_id="af_heart", name="Heart (American Female)", category="premade", settings=VoiceSettings(speed=1.0)),
            Voice(voice_id="af_bella", name="Bella (American Female)", category="premade", settings=VoiceSettings(speed=1.0)),
            Voice(voice_id="af_nicole", name="Nicole (American Female)", category="premade", settings=VoiceSettings(speed=1.0)),
            Voice(voice_id="af_sarah", name="Sarah (American Female)", category="premade", settings=VoiceSettings(speed=1.0)),
            Voice(voice_id="am_adam", name="Adam (American Male)", category="premade", settings=VoiceSettings(speed=1.0)),
            Voice(voice_id="am_michael", name="Michael (American Male)", category="premade", settings=VoiceSettings(speed=1.0)),
            Voice(voice_id="bf_emma", name="Emma (British Female)", category="premade", settings=VoiceSettings(speed=1.0)),
            Voice(voice_id="bf_isabella", name="Isabella (British Female)", category="premade", settings=VoiceSettings(speed=1.0)),
            Voice(voice_id="bm_george", name="George (British Male)", category="premade", settings=VoiceSettings(speed=1.0)),
            Voice(voice_id="bm_lewis", name="Lewis (British Male)", category="premade", settings=VoiceSettings(speed=1.0)),
        ]
        LocalTTSClient._cached_voices = standard_voices
        return standard_voices

    async def list_models(self) -> List[Dict[str, str]]:
        """Fetch available TTS engine models (restricted to Kokoro)."""
        url = f"{self.http_base_url}/v1/models"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    raw_models = (
                        data if isinstance(data, list) else data.get("models", [])
                    )
                    filtered = [
                        {
                            "id": m.get("model_id") or m.get("id"),
                            "name": m.get("name") or m.get("model_id") or m.get("id"),
                        }
                        for m in raw_models
                        if (m.get("model_id") or m.get("id"))
                        and "kokoro" in (m.get("model_id") or m.get("id")).lower()
                    ]
                    if filtered:
                        return filtered
        except Exception:
            pass
        return [{"id": "kokoro", "name": "Kokoro (Default)"}]

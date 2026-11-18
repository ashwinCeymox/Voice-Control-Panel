"""
Streaming STT Provider Client connecting to the internal streaming engine (sherpa-onnx Zipformer).
Implements STTProvider.stream() for live partial captioning and low-latency audio commit.
"""

import asyncio
import json
import logging
from typing import AsyncGenerator, Dict, Any, List, Optional
import httpx
import websockets
from app.core.config import settings
from app.core.errors import (
    ProviderTimeoutError,
    StreamingUnavailableError,
    StreamingModelNotLoadedError,
    StreamingBadAudioError,
    BadRequestError,
)
from app.providers.stt_base import STTProvider
from app.schemas.audio import TranscriptResponse

logger = logging.getLogger("voice_agent.providers.streaming_stt")


class StreamingSTTClient(STTProvider):
    def __init__(
        self,
        http_url: Optional[str] = None,
        ws_url: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        self.http_url = (http_url or settings.STREAMING_STT_URL).rstrip("/")
        self.ws_url = ws_url or settings.STREAMING_STT_WS_URL
        self.timeout = timeout or settings.STREAMING_STT_TIMEOUT_SECONDS

    async def get_health(self) -> Dict[str, Any]:
        """Check health status of the internal streaming service."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.http_url}/health")
                if resp.status_code == 200:
                    return resp.json()
                return {"status": "unhealthy", "code": resp.status_code}
        except Exception as e:
            logger.debug("Streaming STT health check unreachable: %s", e)
            return {"status": "unreachable", "error": "Service offline"}

    async def list_models(self) -> List[Dict[str, Any]]:
        """List models available on the streaming STT service."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.http_url}/v1/models")
                if resp.status_code == 200:
                    return resp.json().get("data", [])
                return []
        except Exception as e:
            logger.debug("Streaming STT list_models failed: %s", e)
            return []

    async def preflight(self, model: str) -> Dict[str, Any]:
        """Verify service is running and requested model is loaded/available."""
        health = await self.get_health()
        if health.get("status") not in ("ready", "healthy"):
            raise StreamingUnavailableError(
                f"Streaming STT service is not running or not ready (status: {health.get('status')})"
            )
        models = await self.list_models()
        valid_ids = {m.get("id").lower() for m in models if m.get("id")}
        alias_map = {
            "sherpa-onnx-streaming-zipformer-en-20m-2023-02-17": "sherpa-onnx-streaming-zipformer-en-20m",
            "zipformer": "sherpa-onnx-streaming-zipformer-en-20m",
            "default": "sherpa-onnx-streaming-zipformer-en-20m",
        }
        req_norm = alias_map.get(model.lower(), model.lower())
        if req_norm not in valid_ids and not any(
            v in req_norm or req_norm in v for v in valid_ids
        ):
            raise StreamingModelNotLoadedError(
                model,
                details=f"Model '{model}' is not loaded on the streaming engine (active: {health.get('model')})",
                status_code=404,
            )
        return {"status": "ready", "model": model, "health": health}

    async def stream(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        model: Optional[str] = None,
        language: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream raw PCM frames to the internal service and yield partial and final events."""
        if model is not None and not str(model).strip():
            target_model = (
                ""  # Explicit empty model triggers STT_MODEL_REQUIRED on engine
            )
        else:
            target_model = model or settings.STREAMING_STT_DEFAULT_MODEL

        target_lang = language or settings.STREAMING_STT_LANGUAGE or "en"
        if target_lang == "auto":
            target_lang = "en"

        server_error: Optional[Dict[str, Any]] = None

        receiver_task: Optional[asyncio.Task] = None
        try:
            async with websockets.connect(self.ws_url, open_timeout=3.0) as ws:
                # 1. Send handshake with language lock and sample rate
                await ws.send(
                    json.dumps(
                        {
                            "type": "stream.start",
                            "model": target_model,
                            "sample_rate": 16000,
                            "format": "pcm_s16le",
                            "language": target_lang,
                        }
                    )
                )

                # Await initial handshake response to capture model rejection or ready status immediately
                try:
                    raw_init = await asyncio.wait_for(
                        ws.recv(), timeout=min(self.timeout, 5.0)
                    )
                    if isinstance(raw_init, str):
                        init_data = json.loads(raw_init)
                        if init_data.get("type") == "error":
                            code = init_data.get("code")
                            msg = init_data.get("message", "")
                            if code in (
                                "STT_MODEL_UNAVAILABLE",
                                "STT_MODEL_NOT_LOADED",
                            ):
                                raise StreamingModelNotLoadedError(
                                    target_model, details=msg, status_code=404
                                )
                            elif code == "STT_MODEL_REQUIRED":
                                raise BadRequestError(
                                    msg or "Missing required model parameter"
                                )
                            elif code == "STT_BAD_AUDIO":
                                raise StreamingBadAudioError(
                                    msg or "Invalid audio format"
                                )
                except (
                    StreamingModelNotLoadedError,
                    BadRequestError,
                    StreamingBadAudioError,
                ):
                    raise
                except Exception as handshake_err:
                    logger.debug("Initial handshake receive: %s", handshake_err)

                # Queue to forward received server events back to caller
                event_queue: asyncio.Queue = asyncio.Queue()
                stop_event = asyncio.Event()

                async def server_receiver():
                    nonlocal server_error
                    try:
                        async for msg in ws:
                            if isinstance(msg, str):
                                data = json.loads(msg)
                                if data.get("type") == "error":
                                    server_error = data
                                await event_queue.put(data)
                                if data.get("type") in (
                                    "transcript.completed",
                                    "transcript.final",
                                    "error",
                                ):
                                    break
                    except Exception as err:
                        logger.debug("Receiver encountered: %s", err)
                    finally:
                        stop_event.set()

                receiver_task = asyncio.create_task(server_receiver())

                # 2. Forward audio chunks as they arrive from client
                try:
                    async for chunk in audio_stream:
                        if server_error:
                            break
                        if chunk:
                            await ws.send(chunk)

                            # Yield any pending partial events received so far
                            while not event_queue.empty():
                                evt = event_queue.get_nowait()
                                if evt.get("type") == "error":
                                    server_error = evt
                                    break
                                yield evt

                    if server_error:
                        code = server_error.get("code")
                        msg = server_error.get("message", "")
                        if code in ("STT_MODEL_UNAVAILABLE", "STT_MODEL_NOT_LOADED"):
                            raise StreamingModelNotLoadedError(
                                target_model, details=msg, status_code=404
                            )
                        elif code == "STT_MODEL_REQUIRED":
                            raise BadRequestError(
                                msg or "Missing required model parameter"
                            )
                        elif code == "STT_BAD_AUDIO":
                            raise StreamingBadAudioError(msg or "Invalid audio format")

                    # 3. Audio stream complete: send commit
                    await ws.send(json.dumps({"type": "audio.commit"}))

                    # Await final response
                    await asyncio.wait_for(stop_event.wait(), timeout=self.timeout)

                    # Drain remaining events
                    while not event_queue.empty():
                        evt = event_queue.get_nowait()
                        if evt.get("type") == "error":
                            server_error = evt
                            code = evt.get("code")
                            msg = evt.get("message", "")
                            if code in (
                                "STT_MODEL_UNAVAILABLE",
                                "STT_MODEL_NOT_LOADED",
                            ):
                                raise StreamingModelNotLoadedError(
                                    target_model, details=msg, status_code=404
                                )
                            elif code == "STT_MODEL_REQUIRED":
                                raise BadRequestError(
                                    msg or "Missing required model parameter"
                                )
                            elif code == "STT_BAD_AUDIO":
                                raise StreamingBadAudioError(
                                    msg or "Invalid audio format"
                                )
                        yield evt

                finally:
                    if receiver_task and not receiver_task.done():
                        receiver_task.cancel()

        except (StreamingModelNotLoadedError, StreamingBadAudioError, BadRequestError):
            raise
        except (
            websockets.exceptions.WebSocketException,
            OSError,
            ConnectionRefusedError,
        ) as e:
            if receiver_task and not receiver_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(receiver_task), timeout=0.2)
                except Exception:
                    pass
            if server_error:
                code = server_error.get("code")
                msg = server_error.get("message", "")
                if code in ("STT_MODEL_UNAVAILABLE", "STT_MODEL_NOT_LOADED"):
                    raise StreamingModelNotLoadedError(
                        target_model, details=msg, status_code=404
                    )
                elif code == "STT_MODEL_REQUIRED":
                    raise BadRequestError(msg or "Missing required model parameter")
                elif code == "STT_BAD_AUDIO":
                    raise StreamingBadAudioError(msg or "Invalid audio format")
            logger.error("Streaming STT service connection failure: %s", e)
            raise StreamingUnavailableError(
                "Streaming service is not running or unreachable", details=str(e)
            )
        except asyncio.TimeoutError:
            logger.error("Streaming STT request timed out after %ss", self.timeout)
            raise ProviderTimeoutError("Streaming STT")

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
        model: Optional[str] = None,
        language: Optional[str] = None,
    ) -> TranscriptResponse:
        """Batch transcription over streaming engine by simulating a quick stream."""

        # Convert audio_bytes to stream generator
        async def byte_generator():
            chunk_size = 3200  # 100ms at 16kHz 16-bit
            for i in range(0, len(audio_bytes), chunk_size):
                yield audio_bytes[i : i + chunk_size]

        final_text = ""
        async for evt in self.stream(byte_generator(), model=model, language=language):
            if evt.get("type") in ("transcript.completed", "transcript.final"):
                final_text = evt.get("text", "")

        return TranscriptResponse(
            text=final_text,
            language=language or "en",
            model=model or settings.STREAMING_STT_DEFAULT_MODEL,
            fallback_used=False,
        )

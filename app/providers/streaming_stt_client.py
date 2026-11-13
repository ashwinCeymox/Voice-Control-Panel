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
from app.core.errors import ProviderUnavailableError, ProviderTimeoutError
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

    async def stream(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        model: Optional[str] = None,
        language: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream raw PCM frames to the internal service and yield partial and final events."""
        target_model = model or settings.STREAMING_STT_DEFAULT_MODEL

        try:
            async with websockets.connect(self.ws_url, open_timeout=3.0) as ws:
                # 1. Send handshake
                await ws.send(json.dumps({
                    "type": "stream.start",
                    "model": target_model,
                    "sample_rate": 16000,
                    "format": "pcm_s16le",
                }))

                # Queue to forward received server events back to caller
                event_queue: asyncio.Queue = asyncio.Queue()
                stop_event = asyncio.Event()

                async def server_receiver():
                    try:
                        async for msg in ws:
                            if isinstance(msg, str):
                                data = json.loads(msg)
                                await event_queue.put(data)
                                if data.get("type") in ("transcript.completed", "transcript.final", "error"):
                                    break
                    except Exception as err:
                        logger.debug("Receiver encountered: %s", err)
                    finally:
                        stop_event.set()

                receiver_task = asyncio.create_task(server_receiver())

                # 2. Forward audio chunks as they arrive from client
                try:
                    async for chunk in audio_stream:
                        if chunk:
                            await ws.send(chunk)

                            # Yield any pending partial events received so far
                            while not event_queue.empty():
                                evt = event_queue.get_nowait()
                                yield evt

                    # 3. Audio stream complete: send commit
                    await ws.send(json.dumps({"type": "audio.commit"}))

                    # Await final response
                    await asyncio.wait_for(stop_event.wait(), timeout=self.timeout)

                    # Drain remaining events
                    while not event_queue.empty():
                        evt = event_queue.get_nowait()
                        yield evt

                finally:
                    if not receiver_task.done():
                        receiver_task.cancel()

        except (websockets.exceptions.WebSocketException, OSError, ConnectionRefusedError) as e:
            logger.error("Streaming STT service connection failure: %s", e)
            raise ProviderUnavailableError("Streaming STT", details=str(e))
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

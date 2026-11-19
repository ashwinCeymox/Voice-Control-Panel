"""
Streaming STT Provider Client connecting to the internal streaming engine (sherpa-onnx Zipformer).
Implements STTProvider.stream() for live partial captioning and low-latency audio commit.
"""

import asyncio
import io
import json
import logging
import time
import wave
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

    def is_localai_model(self, model: Optional[str]) -> bool:
        """Check if model is served by LocalAI rather than Sherpa."""
        if not model:
            return False
        m_lower = model.lower().strip()
        # Explicit check: If model belongs to Sherpa or stream-stt, it is not LocalAI
        if any(k in m_lower for k in ("sherpa-onnx", "sherpa", "zipformer")):
            return False
        try:
            from app.services.model_manifest import model_manifest

            all_stt = model_manifest.get_models_by_category("stt", available_only=False)
            for m in all_stt:
                mid = m.get("id", "").lower()
                if mid == m_lower or m_lower in mid or mid in m_lower:
                    if m.get("provider") == "sherpa" or m.get("engine") == "sherpa":
                        return False
                    if m.get("provider") == "localai" or m.get("engine") == "localai":
                        return True
        except Exception:
            pass
        # Model keywords belonging to LocalAI backends (nemo-speech-cpp, parakeet-cpp, whisper.cpp)
        if any(
            k in m_lower
            for k in ("parakeet", "nemo", "nemotron", "whisper", "scene", "eou")
        ):
            return True
        return False

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
        models: List[Dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.http_url}/v1/models")
                if resp.status_code == 200:
                    models.extend(resp.json().get("data", []))
        except Exception as e:
            logger.debug("Streaming STT list_models failed: %s", e)

        # Include available LocalAI streaming models from manifest
        try:
            from app.services.model_manifest import model_manifest

            localai_stt = model_manifest.get_models_by_category("stt", kind="streaming", available_only=True)
            for m in localai_stt:
                if not any(existing.get("id", "").lower() == m.get("id", "").lower() for existing in models):
                    models.append({
                        "id": m.get("id"),
                        "name": m.get("name") or m.get("id"),
                        "engine": m.get("engine", "localai"),
                    })
        except Exception:
            pass

        return models

    async def preflight(self, model: str) -> Dict[str, Any]:
        """Verify service is running and requested model is loaded/available."""
        if self.is_localai_model(model):
            alias_map = {
                "parakeet-cpp-realtime_eou_120m-v1": "parakeet-cpp-realtime-scene",
                "parakeet-eou": "parakeet-cpp-realtime-scene",
                "realtime_eou_120m-v1": "parakeet-cpp-realtime-scene",
            }
            req_norm = alias_map.get(model.lower(), model.lower())
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    headers = {}
                    if settings.LOCALAI_API_KEY:
                        headers["Authorization"] = f"Bearer {settings.LOCALAI_API_KEY}"
                    resp = await client.get(f"{settings.LOCALAI_BASE_URL.rstrip('/')}/v1/models", headers=headers)
                    if resp.status_code == 200:
                        loaded_models = [m.get("id", "").lower() for m in resp.json().get("data", [])]
                        if req_norm in loaded_models or any(req_norm in m or m in req_norm for m in loaded_models):
                            return {"status": "ready", "model": model, "engine": "localai"}
            except Exception as e:
                logger.warning("LocalAI preflight check failed: %s", e)
                raise StreamingUnavailableError(f"LocalAI engine is unreachable: {e}")
            raise StreamingModelNotLoadedError(
                model,
                details=f"Model '{model}' is not loaded on LocalAI engine",
                status_code=404,
            )

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

        if self.is_localai_model(target_model):
            async for evt in self._stream_localai(audio_stream, target_model, target_lang):
                yield evt
            return

        async for evt in self._stream_sherpa(audio_stream, target_model, target_lang):
            yield evt

    async def _stream_localai(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        model: str,
        language: Optional[str] = "en",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Adapter streaming audio to LocalAI SSE endpoint and yielding partial and final events."""
        target_model = model
        alias_map = {
            "parakeet-cpp-realtime_eou_120m-v1": "parakeet-cpp-realtime-scene",
            "parakeet-eou": "parakeet-cpp-realtime-scene",
            "realtime_eou_120m-v1": "parakeet-cpp-realtime-scene",
        }
        target_model = alias_map.get(target_model.lower(), target_model)

        pcm_buffer = bytearray()
        start_t = time.time()

        try:
            async for chunk in audio_stream:
                if chunk:
                    pcm_buffer.extend(chunk)
        except Exception as e:
            logger.warning("Error reading audio stream in LocalAI adapter: %s", e)

        audio_duration = len(pcm_buffer) / 32000.0  # 16000Hz * 2 bytes/sample
        runtime = (
            "nemo-speech-cpp"
            if ("nemo" in target_model.lower() or "nemotron" in target_model.lower())
            else ("parakeet-cpp" if "parakeet" in target_model.lower() else "localai")
        )

        if len(pcm_buffer) == 0:
            yield {
                "type": "transcript.completed",
                "text": "",
                "confirmed": "",
                "tentative": "",
                "language": language or "en",
                "model": target_model,
                "model_used": target_model,
                "runtime": runtime,
                "audio_seconds": 0.0,
                "latency_ms": 0.0,
            }
            return

        # Package raw PCM into in-memory WAV container
        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(pcm_buffer)
        wav_bytes = wav_buf.getvalue()

        url = f"{settings.LOCALAI_BASE_URL.rstrip('/')}/v1/audio/transcriptions"
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        data = {
            "model": target_model,
            "stream": "true",
            "language": language or "en",
            "temperature": "0",
        }
        headers = {}
        if settings.LOCALAI_API_KEY:
            headers["Authorization"] = f"Bearer {settings.LOCALAI_API_KEY}"

        accumulated_text = ""
        final_emitted = False
        decode_start_t = time.time()

        try:
            async with httpx.AsyncClient(timeout=max(60.0, float(self.timeout))) as client:
                async with client.stream(
                    "POST", url, files=files, data=data, headers=headers
                ) as resp:
                    if resp.status_code != 200:
                        err_bytes = await resp.aread()
                        err_text = err_bytes.decode("utf-8", errors="ignore")
                        logger.error("LocalAI streaming STT failed (%d): %s", resp.status_code, err_text)
                        raise StreamingModelNotLoadedError(
                            target_model,
                            details=f"LocalAI engine error ({resp.status_code}): {err_text}",
                            status_code=resp.status_code,
                        )

                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("data:"):
                            payload_str = line[5:].strip()
                            if payload_str == "[DONE]":
                                break
                            try:
                                event_data = json.loads(payload_str)
                                etype = event_data.get("type")
                                if etype == "transcript.text.delta":
                                    delta = event_data.get("delta", "")
                                    accumulated_text += delta
                                    yield {
                                        "type": "transcript.partial",
                                        "text": accumulated_text.strip(),
                                        "delta": delta,
                                        "confirmed": accumulated_text.strip(),
                                        "tentative": "",
                                        "runtime": runtime,
                                    }
                                elif etype == "transcript.text.done":
                                    final_text = event_data.get("text", accumulated_text).strip()
                                    if final_text.endswith(" <en-US>"):
                                        final_text = final_text[:-8].strip()
                                    elif final_text.endswith("<en-US>"):
                                        final_text = final_text[:-7].strip()

                                    yield {
                                        "type": "transcript.completed",
                                        "text": final_text,
                                        "confirmed": final_text,
                                        "tentative": "",
                                        "language": language or "en",
                                        "model": target_model,
                                        "model_used": target_model,
                                        "runtime": runtime,
                                        "audio_seconds": round(event_data.get("duration", audio_duration), 2),
                                        "latency_ms": round((time.time() - decode_start_t) * 1000.0, 2),
                                    }
                                    final_emitted = True
                            except Exception as parse_err:
                                logger.debug("Failed parsing LocalAI SSE delta: %s", parse_err)

        except (StreamingModelNotLoadedError, BadRequestError):
            raise
        except Exception as e:
            logger.error("LocalAI streaming request error: %s", e)
            raise StreamingUnavailableError(
                f"LocalAI streaming STT engine is unreachable: {e}"
            )

        if not final_emitted:
            final_text = accumulated_text.strip()
            if final_text.endswith(" <en-US>"):
                final_text = final_text[:-8].strip()
            elif final_text.endswith("<en-US>"):
                final_text = final_text[:-7].strip()
            yield {
                "type": "transcript.completed",
                "text": final_text,
                "confirmed": final_text,
                "tentative": "",
                "language": language or "en",
                "model": target_model,
                "model_used": target_model,
                "runtime": runtime,
                "audio_seconds": round(audio_duration, 2),
                "latency_ms": round((time.time() - decode_start_t) * 1000.0, 2),
            }

    async def _stream_sherpa(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        target_model: str,
        target_lang: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream raw PCM frames to Sherpa-ONNX service and yield partial and final events."""

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

"""Gateway client and routing adapter for Streaming STT Bridge (:8089).

Handles:
1. `bridge_available(model)`: Helper verifying flag, internal token, and host calibration.
2. `VoiceBusyManager`: Fire-and-forget `voice_busy` signaling to bridge with turn counting and outage immunity.
3. `stream_via_bridge`: Connects to `STREAMING_BRIDGE_WS_URL` with internal token in header (never in URL/logs),
   forwards audio and control frames, maps events to public schemas, and handles explicit fallback if enabled.
"""

import asyncio
from contextlib import asynccontextmanager
import json
import logging
from typing import AsyncGenerator, Dict, Any, Optional
import httpx
import websockets

from app.core.config import settings
from app.core.errors import StreamingUnavailableError

logger = logging.getLogger("voice_agent.services.streaming_bridge_client")


def bridge_available(model: Optional[str]) -> bool:
    """Return True only if STREAMING_BRIDGE_ENABLED is True, token is present, and calibration passes for host and model."""
    if not getattr(settings, "STREAMING_BRIDGE_ENABLED", False):
        return False
    token = getattr(settings, "STREAMING_BRIDGE_INTERNAL_TOKEN", None)
    if not token or not str(token).strip():
        return False
    if not model or not str(model).strip():
        return False

    try:
        from services.streaming_bridge.calibrate import is_host_calibrated_and_passing
        passed, _, _ = is_host_calibrated_and_passing(str(model).strip())
        return bool(passed)
    except Exception as e:
        logger.debug("Calibration check for %s returned false: %s", model, e)
        return False


class VoiceBusyManager:
    """Coordinates voice_busy signaling to bridge daemon with turn-counter overlap tracking."""

    def __init__(self):
        self._active_count: int = 0
        self._lock = asyncio.Lock()
        self._http_client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=0.5)
        return self._http_client

    async def _send_signal(self, event: str):
        if not getattr(settings, "STREAMING_BRIDGE_ENABLED", False):
            return
        token = getattr(settings, "STREAMING_BRIDGE_INTERNAL_TOKEN", None)
        if not token or not str(token).strip():
            return

        bridge_url = getattr(settings, "STREAMING_BRIDGE_URL", "http://127.0.0.1:8089").rstrip("/")
        url = f"{bridge_url}/internal/voice-signal"
        headers = {"X-Bridge-Token": str(token).strip()}
        try:
            client = self._get_client()
            await client.post(url, json={"event": event}, headers=headers)
        except Exception as e:
            # Fire-and-forget: bridge outage must NEVER delay or fail a voice turn
            logger.debug("Voice busy signal '%s' failed (non-fatal): %s", event, e)

    async def start_busy(self):
        if not getattr(settings, "STREAMING_BRIDGE_ENABLED", False):
            return
        async with self._lock:
            self._active_count += 1
            if self._active_count == 1:
                # First overlapping turn active: send signal in background
                asyncio.create_task(self._send_signal("voice_busy_start"))

    async def end_busy(self):
        if not getattr(settings, "STREAMING_BRIDGE_ENABLED", False):
            return
        async with self._lock:
            self._active_count = max(0, self._active_count - 1)
            if self._active_count == 0:
                # All active turns completed: send signal in background
                asyncio.create_task(self._send_signal("voice_busy_end"))

    @asynccontextmanager
    async def track_voice_busy(self):
        """Context manager guaranteeing end_busy is invoked even on exceptions."""
        await self.start_busy()
        try:
            yield
        finally:
            await self.end_busy()


voice_signal_manager = VoiceBusyManager()


async def stream_via_bridge(
    audio_stream: AsyncGenerator[bytes, None],
    target_model: str,
    target_lang: str = "en",
    timeout: float = 15.0,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Connect to Streaming STT Bridge over WebSocket and yield mapped public transcription events."""
    token = getattr(settings, "STREAMING_BRIDGE_INTERNAL_TOKEN", None)
    if not token or not str(token).strip():
        yield {
            "type": "error",
            "code": "BRIDGE_UNAUTHENTICATED",
            "message": "STREAMING_BRIDGE_INTERNAL_TOKEN not configured on gateway",
        }
        return

    bridge_ws_url = getattr(settings, "STREAMING_BRIDGE_WS_URL", "ws://127.0.0.1:8089/ws")
    headers = {"X-Bridge-Token": str(token).strip()}

    # Buffer PCM in case explicit fallback to batch is triggered
    pcm_accumulator = bytearray()

    async def fallback_to_batch(reason: str, message: str) -> AsyncGenerator[Dict[str, Any], None]:
        logger.info("Executing announced fallback to batch STT for model '%s': %s", target_model, reason)
        yield {
            "type": "stt.fallback",
            "reason": reason,
            "message": message,
        }
        # Drain remaining audio from stream
        try:
            async for chunk in audio_stream:
                if chunk:
                    pcm_accumulator.extend(chunk)
        except Exception:
            pass

        from app.providers.localai_stt import LocalAISTTClient
        from services.streaming_bridge.core.audio_utils import pcm16_to_wav
        batch_client = LocalAISTTClient()
        wav_bytes = pcm16_to_wav(bytes(pcm_accumulator), sample_rate=16000)
        batch_resp = await batch_client.transcribe(
            audio_bytes=wav_bytes,
            model=target_model,
            language=target_lang,
        )
        yield {
            "type": "transcript.completed",
            "text": batch_resp.text,
            "model_used": batch_resp.model,
            "duration": batch_resp.duration or round(len(pcm_accumulator) / 32000.0, 2),
            "fallback_used": True,
        }

    ws = None
    try:
        try:
            # Token passed ONLY in HTTP handshake header, NEVER in query params or logged
            ws = await websockets.connect(
                bridge_ws_url,
                additional_headers=headers,
                open_timeout=min(timeout, 5.0),
            )
        except Exception as conn_err:
            logger.warning("Bridge WebSocket connection failed: %s", conn_err)
            if getattr(settings, "STREAMING_BRIDGE_FALLBACK_TO_BATCH", False):
                async for evt in fallback_to_batch("bridge_unavailable", f"Bridge connection failed: {conn_err}"):
                    yield evt
                return
            yield {
                "type": "error",
                "code": "BRIDGE_UNAVAILABLE",
                "message": f"Streaming bridge service unreachable: {conn_err}",
            }
            return

        # 1. Send stream.start
        await ws.send(json.dumps({
            "type": "stream.start",
            "model": target_model,
            "language": target_lang,
            "format": "pcm_s16le",
            "sample_rate": 16000,
        }))

        # 2. Wait for initial response (stream.ready or error)
        init_raw = await asyncio.wait_for(ws.recv(), timeout=min(timeout, 5.0))
        init_data = json.loads(init_raw) if isinstance(init_raw, str) else {}

        if init_data.get("type") == "error":
            code = init_data.get("code", "BRIDGE_ERROR")
            msg = init_data.get("message", "Bridge handshake rejected")
            if getattr(settings, "STREAMING_BRIDGE_FALLBACK_TO_BATCH", False):
                async for evt in fallback_to_batch(code, msg):
                    yield evt
                return
            yield {
                "type": "error",
                "code": code,
                "message": msg,
            }
            return

        # 3. Stream audio and handle incoming bridge events
        event_queue: asyncio.Queue = asyncio.Queue()
        stop_event = asyncio.Event()

        async def bridge_receiver():
            try:
                async for msg in ws:
                    if isinstance(msg, str):
                        data = json.loads(msg)
                        await event_queue.put(data)
                        if data.get("type") in ("transcript.completed", "error"):
                            break
            except Exception as recv_err:
                logger.debug("Bridge receiver loop ended: %s", recv_err)
            finally:
                stop_event.set()

        receiver_task = asyncio.create_task(bridge_receiver())

        try:
            async for chunk in audio_stream:
                if chunk:
                    pcm_accumulator.extend(chunk)
                    await ws.send(chunk)

                # Emit any pending events from bridge
                while not event_queue.empty():
                    evt = event_queue.get_nowait()
                    mapped = _map_bridge_event(evt, target_model)
                    if mapped:
                        yield mapped
                    if evt.get("type") in ("transcript.completed", "error"):
                        break

            # Client finished audio: commit
            await ws.send(json.dumps({"type": "audio.commit"}))
            await asyncio.wait_for(stop_event.wait(), timeout=timeout)

            # Drain final events
            while not event_queue.empty():
                evt = event_queue.get_nowait()
                mapped = _map_bridge_event(evt, target_model)
                if mapped:
                    yield mapped

        finally:
            if not receiver_task.done():
                receiver_task.cancel()
                try:
                    await receiver_task
                except asyncio.CancelledError:
                    pass

    except websockets.ConnectionClosed as cc:
        logger.debug("Bridge WebSocket closed: %s", cc)
    finally:
        if ws is not None:
            try:
                if not ws.closed:
                    await ws.send(json.dumps({"type": "stream.cancel"}))
                    await ws.close()
            except Exception:
                pass


def _map_bridge_event(raw_evt: Dict[str, Any], default_model: str) -> Optional[Dict[str, Any]]:
    """Map internal bridge events to the public event schemas."""
    etype = raw_evt.get("type")

    if etype == "transcript.partial":
        return {
            "type": "transcript.partial",
            "seq": raw_evt.get("seq", 0),
            "confirmed": raw_evt.get("confirmed", ""),
            "tentative": raw_evt.get("tentative", ""),
            "text": raw_evt.get("text", ""),
            "segment_id": raw_evt.get("segment_id", 0),
            "degraded": raw_evt.get("degraded", False),
        }
    elif etype == "segment.final":
        return {
            "type": "segment.final",
            "segment_id": raw_evt.get("segment_id", 0),
            "text": raw_evt.get("text", ""),
            "revised": raw_evt.get("revised", False),
            "final_degraded": raw_evt.get("final_degraded", False),
        }
    elif etype == "transcript.completed":
        return {
            "type": "transcript.completed",
            "text": raw_evt.get("text", ""),
            "model_used": raw_evt.get("model_used", default_model),
            "duration": raw_evt.get("duration", 0.0),
            "final_degraded": raw_evt.get("final_degraded", False),
        }
    elif etype == "error":
        return {
            "type": "error",
            "code": raw_evt.get("code", "BRIDGE_ERROR"),
            "message": raw_evt.get("message", "Bridge transcription error"),
        }
    return None

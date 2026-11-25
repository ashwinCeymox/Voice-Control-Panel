"""Gateway client, registry eligibility helpers, and routing adapter for Native Streaming STT (:8089)."""

import asyncio
from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional
import httpx
import websockets

from app.core.config import settings
from app.core.errors import StreamingUnavailableError

logger = logging.getLogger("voice_agent.services.streaming_bridge_client")


def native_service_enabled() -> bool:
    """Return True if STT_STREAM_NATIVE_ENABLED (or compat STREAMING_BRIDGE_ENABLED) is True."""
    return bool(
        getattr(settings, "STT_STREAM_NATIVE_ENABLED", False)
        or getattr(settings, "STREAMING_BRIDGE_ENABLED", False)
    )


def _find_parakeet_yaml_config(model_id: str) -> Optional[str]:
    """Read-only lookup of a matching LocalAI YAML config under LOCALAI_MODELS_PATH."""
    models_dir_cfg = getattr(settings, "LOCALAI_MODELS_PATH", None)
    if not models_dir_cfg:
        return None
    models_dir = Path(str(models_dir_cfg))
    if not models_dir.is_dir():
        return None
    norm = model_id.strip().lower()
    for p in sorted(models_dir.glob("*.yaml")):
        if p.name.startswith("._"):
            continue
        if p.stem.lower() == norm or norm in p.stem.lower():
            try:
                return p.read_text(errors="replace")
            except OSError:
                return None
    return None


def is_native_parakeet_eligible(model_id: Optional[str]) -> bool:
    """Determine if a model is eligible for native :8089 parakeet.cpp streaming.

    Reads installed LocalAI YAML configs when present:
    - Requires backend `parakeet-cpp`.
    - Excludes `realtime-scene` multi-model configs (requires separate scene API)
      and uninstalled `nemotron` until tested/verified on pod.
    - Currently eligible: `parakeet-cpp-realtime_eou_120m-v1`.
    """
    if not model_id or not str(model_id).strip():
        return False
    low = str(model_id).strip().lower()

    # Never admit multi-model scene configs or unverified nemotron until explicitly tested
    if "scene" in low or "nemotron" in low or "diariz" in low:
        return False

    yaml_txt = _find_parakeet_yaml_config(model_id)
    if yaml_txt is not None:
        yaml_low = yaml_txt.lower()
        if "parakeet-cpp" not in yaml_low:
            return False
        if "diarization_model" in yaml_low or "sound_model" in yaml_low:
            return False
        # Verify EOU GGUF exists when YAML is inspected locally
        for line in yaml_txt.splitlines():
            s = line.strip()
            if s.startswith("model:"):
                rel = s.split(":", 1)[1].strip().strip('"').strip("'")
                models_dir = Path(str(settings.LOCALAI_MODELS_PATH))
                gguf_path = Path(rel) if Path(rel).is_absolute() else (models_dir / rel)
                if not gguf_path.is_file():
                    return False
                break
        return "eou" in low or "eou" in yaml_low

    return "parakeet-cpp" in low and "eou" in low


def native_service_available(model: Optional[str]) -> bool:
    """Return True only if flag is ON, token exists, model is S0-eligible, and host calibration passes."""
    if not native_service_enabled():
        return False
    token = getattr(settings, "STREAMING_BRIDGE_INTERNAL_TOKEN", None)
    if not token or not str(token).strip():
        return False
    if not is_native_parakeet_eligible(model):
        return False

    try:
        from services.streaming_bridge.calibrate import is_host_calibrated_and_passing
        passed, _, _ = is_host_calibrated_and_passing(str(model).strip())
        return bool(passed)
    except Exception as e:
        logger.debug("Calibration check for %s returned false: %s", model, e)
        return False


# Preserved name alias for existing imports during S3->S4 transition
bridge_available = native_service_available


def resolve_stt_streaming_metadata(
    model_id: str,
    *,
    provider: Optional[str] = None,
    engine: Optional[str] = None,
    runtime: Optional[str] = None,
) -> Dict[str, Any]:
    """Single source of truth for STT transports, streaming_mode, and streaming_backend."""
    low = (model_id or "").lower()
    if (
        provider == "stream-stt"
        or engine == "streaming-stt"
        or runtime == "sherpa-onnx"
        or any(k in low for k in ("sherpa", "zipformer"))
    ):
        return {
            "transports": ["ws_stream_native"],
            "streaming_mode": "native",
            "streaming_backend": "sherpa",
        }

    if native_service_available(model_id):
        return {
            "transports": ["batch_http", "ws_stream_native"],
            "streaming_mode": "native",
            "streaming_backend": "parakeet_native",
        }

    return {
        "transports": ["batch_http"],
        "streaming_mode": "none",
        "streaming_backend": None,
    }


class VoiceBusyManager:
    """Coordinates voice_busy signaling to :8089 daemon with turn-counter overlap tracking."""

    def __init__(self):
        self._active_count: int = 0
        self._lock = asyncio.Lock()
        self._http_client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=0.5)
        return self._http_client

    async def _send_signal(self, event: str):
        if not native_service_enabled():
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
            logger.debug("Voice busy signal '%s' failed (non-fatal): %s", event, e)

    async def start_busy(self):
        if not native_service_enabled():
            return
        async with self._lock:
            self._active_count += 1
            if self._active_count == 1:
                asyncio.create_task(self._send_signal("voice_busy_start"))

    async def end_busy(self):
        if not native_service_enabled():
            return
        async with self._lock:
            self._active_count = max(0, self._active_count - 1)
            if self._active_count == 0:
                asyncio.create_task(self._send_signal("voice_busy_end"))

    @asynccontextmanager
    async def track_voice_busy(self):
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
    """Connect to Native Streaming STT Service (:8089) over WebSocket and map public events."""
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
    pcm_accumulator = bytearray()

    async def fallback_to_batch(reason: str, message: str) -> AsyncGenerator[Dict[str, Any], None]:
        logger.info("Executing announced fallback to batch STT for model '%s': %s", target_model, reason)
        yield {
            "type": "stt.fallback",
            "reason": reason,
            "message": message,
        }
        try:
            async for chunk in audio_stream:
                if chunk:
                    pcm_accumulator.extend(chunk)
        except Exception:
            pass

        from app.providers.localai_stt import LocalAISTTClient
        from app.services.audio_utils import build_wav_from_pcm
        batch_client = LocalAISTTClient()
        wav_bytes = build_wav_from_pcm(bytes(pcm_accumulator), sample_rate=16000)
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
            try:
                ws = await websockets.connect(bridge_ws_url, additional_headers=headers, open_timeout=5.0)
            except TypeError:
                ws = await websockets.connect(bridge_ws_url, extra_headers=headers, open_timeout=5.0)
        except Exception as conn_err:
            logger.warning("Native streaming connection failed on %s: %s", bridge_ws_url, conn_err)
            if getattr(settings, "STREAMING_BRIDGE_FALLBACK_TO_BATCH", False):
                async for evt in fallback_to_batch("bridge_unavailable", f"Streaming service unreachable: {conn_err}"):
                    yield evt
                return
            yield {
                "type": "error",
                "code": "BRIDGE_UNAVAILABLE",
                "message": f"Streaming service unavailable: {conn_err}",
            }
            return

        start_payload = {
            "type": "stream.start",
            "model": target_model,
            "language": target_lang,
            "format": "pcm_s16le",
            "sample_rate": 16000,
        }
        await ws.send(json.dumps(start_payload))

        ready_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
        ready_msg = json.loads(ready_raw)
        if ready_msg.get("type") == "error":
            err_code = ready_msg.get("code", "BRIDGE_ERROR")
            err_msg = ready_msg.get("message", "Streaming service refused stream")
            if getattr(settings, "STREAMING_BRIDGE_FALLBACK_TO_BATCH", False):
                async for evt in fallback_to_batch(err_code.lower(), err_msg):
                    yield evt
                return
            yield {
                "type": "error",
                "code": err_code,
                "message": err_msg,
                "retry_after_ms": ready_msg.get("retry_after_ms"),
            }
            return

        if ready_msg.get("type") != "stream.ready":
            yield {
                "type": "error",
                "code": "INVALID_BRIDGE_HANDSHAKE",
                "message": f"Unexpected handshake message: {ready_msg}",
            }
            return

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
                logger.debug("Streaming receiver loop ended: %s", recv_err)
            finally:
                stop_event.set()

        receiver_task = asyncio.create_task(bridge_receiver())

        try:
            async for chunk in audio_stream:
                if chunk:
                    pcm_accumulator.extend(chunk)
                    await ws.send(chunk)

                while not event_queue.empty():
                    evt = event_queue.get_nowait()
                    mapped = _map_bridge_event(evt, target_model)
                    if mapped:
                        yield mapped
                    if evt.get("type") in ("transcript.completed", "error"):
                        break

            await ws.send(json.dumps({"type": "audio.commit"}))
            await asyncio.wait_for(stop_event.wait(), timeout=timeout)

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
        logger.debug("Streaming WebSocket closed: %s", cc)
    finally:
        if ws is not None:
            try:
                if not ws.closed:
                    await ws.send(json.dumps({"type": "stream.cancel"}))
                    await ws.close()
            except Exception:
                pass


def _map_bridge_event(raw_evt: Dict[str, Any], default_model: str) -> Optional[Dict[str, Any]]:
    """Map internal native service events to public gateway event schemas."""
    etype = raw_evt.get("type")

    if etype == "transcript.partial":
        out: Dict[str, Any] = {
            "type": "transcript.partial",
            "seq": raw_evt.get("seq", 0),
            "confirmed": raw_evt.get("confirmed", ""),
            "tentative": raw_evt.get("tentative", ""),
            "text": raw_evt.get("text", ""),
            "segment_id": raw_evt.get("segment_id", 0),
            "degraded": raw_evt.get("degraded", False),
        }
        if "eou" in raw_evt:
            out["eou"] = raw_evt["eou"]
        if "eob" in raw_evt:
            out["eob"] = raw_evt["eob"]
        return out
    elif etype == "segment.final":
        return {
            "type": "segment.final",
            "segment_id": raw_evt.get("segment_id", 0),
            "text": raw_evt.get("text", ""),
            "revised": raw_evt.get("revised", False),
            "final_degraded": raw_evt.get("final_degraded", False),
        }
    elif etype == "stream.paused":
        return {
            "type": "stream.paused",
            "reason": raw_evt.get("reason", "voice_turn_active"),
            "buffered_chunks": raw_evt.get("buffered_chunks", 0),
        }
    elif etype == "stream.resumed":
        return {
            "type": "stream.resumed",
            "buffered_chunks": raw_evt.get("buffered_chunks", 0),
        }
    elif etype == "transcript.completed":
        out = {
            "type": "transcript.completed",
            "text": raw_evt.get("text", ""),
            "model_used": raw_evt.get("model_used", default_model),
            "duration": raw_evt.get("duration", 0.0),
            "final_degraded": raw_evt.get("final_degraded", False),
        }
        if "eou" in raw_evt:
            out["eou"] = raw_evt["eou"]
        if "eob" in raw_evt:
            out["eob"] = raw_evt["eob"]
        return out
    elif etype == "error":
        return {
            "type": "error",
            "code": raw_evt.get("code", "BRIDGE_ERROR"),
            "message": raw_evt.get("message", "Streaming transcription error"),
        }
    return None

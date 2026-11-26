"""Standalone Native Streaming STT Service Daemon (port 8089).

Runs on private loopback 127.0.0.1:8089 (STREAMING_BRIDGE_PORT).
Enforces:
1. Loopback-only peer guard & constant-time token check (STREAMING_BRIDGE_INTERNAL_TOKEN).
2. Host-gated calibration check & host-calibrated `admitted_streams` cap (BRIDGE_BUSY, WS 1013).
3. PCM16 16 kHz mono validation (rejects other sample rates/formats with UNSUPPORTED_FORMAT).
4. Public event schemas (`stream.ready`, `transcript.partial`, `stream.paused`,
   `stream.resumed`, `segment.final`, `transcript.completed`, `error`).
5. Bounded voice-turn protection & <=2x throttled catch-up; explicit error on buffer overflow.
6. Idle timeout, max stream duration limit, and zero leaked engine sessions on cleanup.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
from typing import Any, Dict, Optional
import uuid
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status

from app.core.config import settings
from app.schemas.events import (
    SegmentFinalEvent,
    StreamPausedEvent,
    StreamResumedEvent,
    TranscriptCompletedEvent,
    TranscriptPartialEvent,
)
from services.streaming_bridge.calibrate import (
    get_host_fingerprint,
    is_host_calibrated_and_passing,
)
from services.streaming_bridge.engines.base import (
    EngineABIMismatchError,
    EngineChunkResult,
    EngineCrashedError,
    StreamingEngine,
    UnsupportedAudioFormatError,
)
from services.streaming_bridge.engines.parakeet_native import ParakeetNativeEngine
from services.streaming_bridge.scheduler.bridge_scheduler import (
    BridgeScheduler,
    PauseBufferOverflowError,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("streaming_bridge_server")

app = FastAPI(title="Native Streaming STT Service", version="2.0.0")

# Runtime state (injectable in unit tests via monkeypatch)
active_sessions: Dict[str, Dict[str, Any]] = {}
scheduler: Optional[BridgeScheduler] = None
engine: Optional[StreamingEngine] = None


def _build_default_engine() -> ParakeetNativeEngine:
    fp = get_host_fingerprint()
    return ParakeetNativeEngine(
        lib_path=settings.STREAMING_NATIVE_LIB_PATH,
        expected_abi=settings.STREAMING_NATIVE_EXPECTED_ABI,
        is_cpu_bound=bool(fp.get("is_cpu_bound", True)),
        omp_num_threads=settings.STREAMING_NATIVE_OMP_NUM_THREADS,
        gomp_wait_policy=settings.STREAMING_NATIVE_GOMP_WAIT_POLICY,
        omp_proc_bind=settings.STREAMING_NATIVE_OMP_PROC_BIND,
        worker_nice=settings.STREAMING_NATIVE_WORKER_NICE,
        cpu_affinity=settings.STREAMING_NATIVE_CPU_AFFINITY,
        engine_threads=settings.STREAMING_NATIVE_ENGINE_THREADS,
    )


def _resolve_pause_enabled() -> bool:
    if settings.STREAMING_NATIVE_VOICE_PAUSE_ENABLED is not None:
        return bool(settings.STREAMING_NATIVE_VOICE_PAUSE_ENABLED)
    return bool(get_host_fingerprint().get("is_cpu_bound", True))


@app.on_event("startup")
async def startup_event() -> None:
    global scheduler, engine
    if not settings.STREAMING_BRIDGE_INTERNAL_TOKEN or not settings.STREAMING_BRIDGE_INTERNAL_TOKEN.strip():
        raise RuntimeError("STREAMING_BRIDGE_INTERNAL_TOKEN not configured in .env; bridge server refusing to start")

    scheduler = BridgeScheduler(
        max_streams=settings.STREAMING_BRIDGE_MAX_STREAMS,
        pause_enabled=_resolve_pause_enabled(),
        max_buffered_chunks=settings.STREAMING_NATIVE_PAUSE_MAX_CHUNKS,
        catchup_rate=settings.STREAMING_NATIVE_CATCHUP_RATE,
    )
    scheduler.start()
    if engine is None:
        engine = _build_default_engine()
    logger.info("Native Streaming STT Service started on port %d", settings.STREAMING_BRIDGE_PORT)


@app.on_event("shutdown")
async def shutdown_event() -> None:
    global scheduler, engine
    if scheduler:
        await scheduler.stop()
    if engine:
        await engine.close()
    active_sessions.clear()


def verify_loopback(client_host: Optional[str]) -> bool:
    return client_host in ("127.0.0.1", "::1", "localhost", "testclient")


def verify_token(token: Optional[str]) -> bool:
    expected = settings.STREAMING_BRIDGE_INTERNAL_TOKEN
    if not expected or not token:
        return False
    return hmac.compare_digest(token, expected)


@app.get("/health")
@app.get("/health/ready")
async def health_check(request: Request):
    if not verify_loopback(request.client.host if request.client else None):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Loopback only")

    metrics = scheduler.get_metrics() if scheduler else {}
    return {
        "status": "healthy",
        "service": "streaming_bridge",
        "enabled": settings.STREAMING_BRIDGE_ENABLED,
        "port": settings.STREAMING_BRIDGE_PORT,
        "active_streams": len(active_sessions),
        "max_streams": settings.STREAMING_BRIDGE_MAX_STREAMS,
        "engine": engine.health() if engine else {"ready": False},
        "metrics": metrics,
    }


@app.post("/internal/voice-signal")
async def voice_busy_signal(
    request: Request,
    x_bridge_token: Optional[str] = Header(None, alias="X-Bridge-Token"),
):
    if not verify_loopback(request.client.host if request.client else None):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Loopback only")
    if not verify_token(x_bridge_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token")

    payload = await request.json()
    event = payload.get("event")
    if event == "voice_busy_start":
        if scheduler:
            await scheduler.set_voice_busy(True)
        return {"status": "ok", "voice_busy": True}
    if event == "voice_busy_end":
        if scheduler:
            await scheduler.set_voice_busy(False)
        return {"status": "ok", "voice_busy": False}
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown signal event: {event}")


@app.websocket("/ws")
async def websocket_stream(
    websocket: WebSocket,
    token: Optional[str] = Query(None),
):
    client_host = websocket.client.host if websocket.client else None
    if not verify_loopback(client_host):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    auth_token = websocket.headers.get("X-Bridge-Token") or token
    if not verify_token(auth_token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    stream_id = f"stream_{uuid.uuid4().hex[:8]}"
    active_engine = engine
    engine_stream_id: Optional[str] = None
    ws_send_lock = asyncio.Lock()
    ws_closed = False

    async def send_event(payload: dict) -> None:
        if ws_closed:
            return
        async with ws_send_lock:
            if not ws_closed:
                await websocket.send_json(payload)

    local_sched = scheduler
    if local_sched is None:
        local_sched = BridgeScheduler(
            max_streams=settings.STREAMING_BRIDGE_MAX_STREAMS,
            pause_enabled=_resolve_pause_enabled(),
            max_buffered_chunks=settings.STREAMING_NATIVE_PAUSE_MAX_CHUNKS,
            catchup_rate=settings.STREAMING_NATIVE_CATCHUP_RATE,
        )
        local_sched.start()

    try:
        init_raw = await asyncio.wait_for(
            websocket.receive_text(),
            timeout=settings.STREAMING_BRIDGE_READY_TIMEOUT_S,
        )
        init_msg = json.loads(init_raw)
        if init_msg.get("type") != "stream.start":
            await send_event({
                "type": "error",
                "code": "INVALID_PROTOCOL",
                "message": "First message must be stream.start",
            })
            await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)
            return

        model = str(init_msg.get("model") or settings.LOCALAI_STT_MODEL)
        language = str(init_msg.get("language") or "en")
        fmt = init_msg.get("format", "pcm_s16le")
        sample_rate = init_msg.get("sample_rate", 16000)
        channels = init_msg.get("channels", 1)

        if fmt != "pcm_s16le" or sample_rate != 16000 or channels != 1:
            await send_event({
                "type": "error",
                "code": "UNSUPPORTED_FORMAT",
                "message": f"Only 16000 Hz mono pcm_s16le audio supported (got rate={sample_rate}, fmt={fmt}, ch={channels})",
            })
            await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)
            return

        calib_ok, calib_reason, calib_data = is_host_calibrated_and_passing(model)
        if not calib_ok:
            await send_event({
                "type": "error",
                "code": "BRIDGE_NOT_CALIBRATED",
                "message": calib_reason,
                "retry_after_ms": 5000,
            })
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        admitted_limit = min(int(calib_data.get("admitted_streams", 0)), settings.STREAMING_BRIDGE_MAX_STREAMS)
        if not local_sched.can_admit(admitted_limit, len(active_sessions)):
            await send_event({
                "type": "error",
                "code": "BRIDGE_BUSY",
                "message": f"Maximum admitted streams ({admitted_limit}) reached for model {model}",
                "retry_after_ms": 2000,
            })
            await websocket.close(code=1013)
            return

        if active_engine is None:
            active_engine = _build_default_engine()

        if not active_engine.health().get("ready"):
            model_path = str(calib_data.get("model_path") or model)
            await active_engine.load(model_path, language=language)

        engine_stream_id = await active_engine.open_stream(language=language)

        seq = 0
        segment_id = 0
        total_pcm_bytes = 0
        last_activity = time.monotonic()
        stream_started = last_activity
        idle_limit = float(settings.STREAMING_BRIDGE_IDLE_TIMEOUT_S)
        max_dur_limit = float(settings.STREAMING_BRIDGE_MAX_STREAM_S)

        active_sessions[stream_id] = {"model": model, "language": language, "started_at": stream_started}

        async def forward_chunk(chunk_pcm: bytes) -> None:
            nonlocal seq, segment_id
            assert engine_stream_id is not None and active_engine is not None
            chunk_res: EngineChunkResult = await active_engine.feed(
                engine_stream_id, chunk_pcm, sample_rate=16000
            )
            if chunk_res.text_delta or chunk_res.eou or chunk_res.eob:
                seq += 1
                evt = TranscriptPartialEvent(
                    seq=seq,
                    confirmed=chunk_res.accumulated_text,
                    tentative="",
                    text=chunk_res.accumulated_text,
                    segment_id=segment_id,
                    eou=chunk_res.eou,
                    eob=chunk_res.eob,
                ).model_dump()
                await send_event(evt)
                if chunk_res.eou:
                    seg_evt = SegmentFinalEvent(
                        segment_id=segment_id,
                        text=chunk_res.accumulated_text,
                    ).model_dump()
                    await send_event(seg_evt)
                    segment_id += 1

        async def handle_scheduler_event(raw_evt: dict) -> None:
            etype = raw_evt.get("type")
            if etype == "stream.paused":
                await send_event(StreamPausedEvent(
                    reason=str(raw_evt.get("reason", "voice_turn_active")),
                    buffered_chunks=int(raw_evt.get("buffered_chunks", 0)),
                ).model_dump())
            elif etype == "stream.resumed":
                await send_event(StreamResumedEvent(
                    buffered_chunks=int(raw_evt.get("buffered_chunks", 0)),
                ).model_dump())
            else:
                await send_event(raw_evt)

        local_sched.register_stream(
            stream_id,
            model,
            language,
            pause_enabled=bool(calib_data.get("host", {}).get("is_cpu_bound", _resolve_pause_enabled())),
            on_event=handle_scheduler_event,
            on_chunk=forward_chunk,
        )

        await send_event({"type": "stream.ready", "stream_id": stream_id})

        while True:
            now = time.monotonic()
            rem_idle = idle_limit - (now - last_activity)
            rem_max = max_dur_limit - (now - stream_started)
            timeout_s = min(rem_idle, rem_max)
            if timeout_s <= 0:
                code = "MAX_DURATION_EXCEEDED" if rem_max <= rem_idle else "IDLE_TIMEOUT"
                await send_event({"type": "error", "code": code, "message": f"Stream closed ({code})"})
                await websocket.close(code=1000)
                break

            try:
                msg = await asyncio.wait_for(websocket.receive(), timeout=timeout_s)
            except asyncio.TimeoutError:
                now2 = time.monotonic()
                code = "MAX_DURATION_EXCEEDED" if (now2 - stream_started) >= max_dur_limit else "IDLE_TIMEOUT"
                await send_event({"type": "error", "code": code, "message": f"Stream closed ({code})"})
                await websocket.close(code=1000)
                break

            if msg.get("type") == "websocket.disconnect":
                break

            if "bytes" in msg and msg["bytes"] is not None:
                pcm_chunk = msg["bytes"]
                last_activity = time.monotonic()
                total_pcm_bytes += len(pcm_chunk)
                try:
                    await local_sched.handle_audio_chunk(stream_id, pcm_chunk, forward_chunk)
                except PauseBufferOverflowError as exc:
                    await send_event({"type": "error", "code": "BUFFER_OVERFLOW", "message": str(exc)})
                    await websocket.close(code=1013)
                    break

            elif "text" in msg and msg["text"]:
                last_activity = time.monotonic()
                txt_payload = json.loads(msg["text"])
                msg_type = txt_payload.get("type")
                if msg_type == "audio.commit":
                    await local_sched.flush_buffered(stream_id, forward_chunk)
                    final = await active_engine.finalize(engine_stream_id)
                    engine_stream_id = None
                    comp_evt = TranscriptCompletedEvent(
                        text=final.full_text,
                        model_used=model,
                        duration=round(total_pcm_bytes / 32000.0, 2),
                        eou=final.eou,
                        eob=final.eob,
                    ).model_dump()
                    await send_event(comp_evt)
                    await websocket.close()
                    break
                if msg_type == "stream.cancel":
                    await websocket.close()
                    break

    except WebSocketDisconnect:
        logger.info("Stream %s disconnected", stream_id)
    except (UnsupportedAudioFormatError, EngineABIMismatchError) as exc:
        await send_event({"type": "error", "code": "ENGINE_UNSUPPORTED", "message": str(exc)})
        try:
            await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)
        except Exception:
            pass
    except EngineCrashedError as exc:
        logger.error("Worker crashed for stream %s: %s", stream_id, exc)
        await send_event({"type": "error", "code": "ENGINE_CRASHED", "message": str(exc)})
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    except Exception as exc:
        logger.error("Unexpected error in stream %s: %s", stream_id, exc)
    finally:
        ws_closed = True
        active_sessions.pop(stream_id, None)
        if local_sched is not None:
            await local_sched.unregister_stream(stream_id)
        if engine_stream_id is not None and active_engine is not None:
            try:
                await active_engine.close_stream(engine_stream_id)
            except Exception:
                pass


def start_server(host: str = "127.0.0.1", port: int = 8089) -> None:
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    port = int(os.environ.get("STREAMING_BRIDGE_PORT", "8089"))
    start_server(port=port)

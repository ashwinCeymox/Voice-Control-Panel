"""Standalone Streaming STT Bridge Daemon.

Runs on private loopback 127.0.0.1:8089 (STREAMING_BRIDGE_PORT).
Provides real-time streaming speech recognition over WebSocket for LocalAI STT models.

Enforces:
1. Loopback-only peer guard.
2. Constant-time token authentication (STREAMING_BRIDGE_INTERNAL_TOKEN).
3. Host-gated calibration check (refuses uncalibrated models with BRIDGE_NOT_CALIBRATED).
4. Single in-flight decode per stream & global MAX_DECODES=1.
5. Yield-to-voice with bounded pause.
6. Parameter parity with batch provider (temperature 0, 16kHz mono WAV).
"""

import asyncio
import hmac
import json
import logging
import os
import sys
import uuid
from typing import Dict, Optional
import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse

from app.core.config import settings
from services.streaming_bridge.calibrate import is_host_calibrated_and_passing
from services.streaming_bridge.core.session import BridgeSession
from services.streaming_bridge.scheduler.bridge_scheduler import BridgeScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("streaming_bridge_server")

app = FastAPI(title="Streaming STT Bridge", version="1.0.0")

# Active streams registry
active_sessions: Dict[str, BridgeSession] = {}
http_client: Optional[httpx.AsyncClient] = None
scheduler: Optional[BridgeScheduler] = None


async def call_localai_transcribe(model: str, wav_bytes: bytes, language: str = "en") -> dict:
    """Call LocalAI POST /v1/audio/transcriptions with parameter parity."""
    global http_client
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=30.0)

    url = f"{settings.LOCALAI_BASE_URL.rstrip('/')}/v1/audio/transcriptions"
    headers = {}
    if settings.LOCALAI_API_KEY:
        headers["Authorization"] = f"Bearer {settings.LOCALAI_API_KEY}"

    files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
    # Parameter parity: temperature 0.0, exact language, model
    data = {
        "model": model,
        "temperature": "0.0",
        "language": language,
    }

    resp = await http_client.post(url, files=files, data=data, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"LocalAI decode returned HTTP {resp.status_code}: {resp.text}")
    return resp.json()


@app.on_event("startup")
async def startup_event():
    global http_client, scheduler
    http_client = httpx.AsyncClient(timeout=30.0)
    scheduler = BridgeScheduler(
        transcribe_func=call_localai_transcribe,
        max_decodes=settings.STREAMING_BRIDGE_MAX_DECODES,
        bounded_pause_ms=settings.STREAMING_BRIDGE_BOUNDED_PAUSE_MS,
        widened_step_ms=settings.STREAMING_BRIDGE_WIDENED_STEP_MS,
        partial_timeout_s=settings.STREAMING_BRIDGE_PARTIAL_TIMEOUT_S,
        final_timeout_s=settings.STREAMING_BRIDGE_FINAL_TIMEOUT_S,
    )
    scheduler.start()
    logger.info("Streaming STT Bridge started on port %d (max_decodes=%d)", settings.STREAMING_BRIDGE_PORT, settings.STREAMING_BRIDGE_MAX_DECODES)


@app.on_event("shutdown")
async def shutdown_event():
    global http_client, scheduler
    if scheduler:
        await scheduler.stop()
    if http_client:
        await http_client.aclose()


def verify_loopback(client_host: Optional[str]) -> bool:
    """Verify request originated from private loopback."""
    return client_host in ("127.0.0.1", "::1", "localhost", "testclient")


def verify_token(token: Optional[str]) -> bool:
    """Constant-time token authentication."""
    expected = settings.STREAMING_BRIDGE_INTERNAL_TOKEN
    if not expected or not token:
        return False
    return hmac.compare_digest(token, expected)


# ==============================================================================
# HTTP HEALTH & TELEMETRY ROUTES
# ==============================================================================

@app.get("/health")
@app.get("/health/ready")
async def health_check(request: Request):
    """Health and observability endpoint."""
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
        "max_decodes": settings.STREAMING_BRIDGE_MAX_DECODES,
        "metrics": metrics,
    }


@app.post("/internal/voice-signal")
async def voice_busy_signal(
    request: Request,
    x_bridge_token: Optional[str] = Header(None, alias="X-Bridge-Token"),
):
    """Gateway signals voice turn start/end to trigger yield-to-voice."""
    if not verify_loopback(request.client.host if request.client else None):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Loopback only")
    if not verify_token(x_bridge_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token")

    payload = await request.json()
    event = payload.get("event")

    if event == "voice_busy_start":
        if scheduler:
            scheduler.set_voice_busy(True)
        return {"status": "ok", "voice_busy": True}
    elif event == "voice_busy_end":
        if scheduler:
            scheduler.set_voice_busy(False)
        return {"status": "ok", "voice_busy": False}
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown signal event: {event}")


# ==============================================================================
# WEBSOCKET STREAMING ENDPOINT
# ==============================================================================

@app.websocket("/ws")
async def websocket_stream(
    websocket: WebSocket,
    token: Optional[str] = Query(None),
):
    """Client streaming STT WebSocket endpoint."""
    # 1. Loopback guard
    client_host = websocket.client.host if websocket.client else None
    if not verify_loopback(client_host):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 2. Token authentication
    header_token = websocket.headers.get("X-Bridge-Token")
    auth_token = header_token or token
    if not verify_token(auth_token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 3. Stream capacity check
    if len(active_sessions) >= settings.STREAMING_BRIDGE_MAX_STREAMS:
        await websocket.accept()
        await websocket.send_json({
            "type": "error",
            "code": "BRIDGE_BUSY",
            "message": "Maximum concurrent bridge streams reached",
            "retry_after_ms": 2000,
        })
        await websocket.close(code=1013)
        return

    await websocket.accept()
    stream_id = f"stream_{uuid.uuid4().hex[:8]}"
    session: Optional[BridgeSession] = None
    step_task: Optional[asyncio.Task] = None

    try:
        # 4. Wait for stream.start message
        init_raw = await websocket.receive_text()
        init_msg = json.loads(init_raw)

        if init_msg.get("type") != "stream.start":
            await websocket.send_json({
                "type": "error",
                "code": "INVALID_PROTOCOL",
                "message": "First message must be stream.start",
            })
            await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)
            return

        model = init_msg.get("model", settings.LOCALAI_STT_MODEL)
        language = init_msg.get("language", "en")
        fmt = init_msg.get("format", "pcm_s16le")
        sample_rate = init_msg.get("sample_rate", 16000)

        # Format validation
        if fmt != "pcm_s16le" or sample_rate != 16000:
            await websocket.send_json({
                "type": "error",
                "code": "UNSUPPORTED_FORMAT",
                "message": "Bridge only accepts 16 kHz mono pcm_s16le audio",
            })
            await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)
            return

        # 5. Host-Gated Calibration Check
        calib_ok, calib_reason = is_host_calibrated_and_passing(model)
        if not calib_ok:
            logger.warning("Stream %s rejected: Host calibration check failed: %s", stream_id, calib_reason)
            await websocket.send_json({
                "type": "error",
                "code": "BRIDGE_NOT_CALIBRATED",
                "message": calib_reason,
                "retry_after_ms": 5000,
            })
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        # Initialize session & scheduler stream
        session = BridgeSession(
            stream_id=stream_id,
            model=model,
            language=language,
            step_ms=settings.STREAMING_BRIDGE_STEP_MS,
            min_window_ms=settings.STREAMING_BRIDGE_MIN_WINDOW_MS,
            segment_max_ms=settings.STREAMING_BRIDGE_SEGMENT_MAX_MS,
            segment_silence_ms=settings.STREAMING_BRIDGE_SEGMENT_SILENCE_MS,
        )
        active_sessions[stream_id] = session
        if scheduler:
            scheduler.register_stream(stream_id, model, language)

        # Notify client ready
        await websocket.send_json({"type": "stream.ready", "stream_id": stream_id})

        # Periodic step timer for partial decodes
        async def step_worker():
            while not session.is_closed:
                await asyncio.sleep(session.step_ms / 1000.0)
                if session.is_eligible_for_partial() and scheduler:
                    wav_snapshot = session.get_current_segment_wav()
                    try:
                        raw_text = await scheduler.schedule_decode(
                            stream_id=stream_id,
                            segment_id=session.segment_id,
                            seq=session.seq + 1,
                            wav_bytes=wav_snapshot,
                            is_final=False,
                        )
                        if raw_text is not None and not session.is_closed:
                            part_event = session.process_partial_result(raw_text)
                            await websocket.send_json(part_event)
                    except Exception as e:
                        logger.error("Error during partial decode in stream %s: %s", stream_id, e)

        step_task = asyncio.create_task(step_worker())

        # Main message receive loop
        while not session.is_closed:
            msg = await websocket.receive()
            if "bytes" in msg and msg["bytes"]:
                pcm_chunk = msg["bytes"]
                should_cut = session.append_pcm(pcm_chunk)
                if should_cut:
                    # Segment boundary reached: run final decode of segment
                    final_text = None
                    if not session.can_reuse_last_decode_for_final() and scheduler:
                        wav_bytes = session.get_current_segment_wav()
                        final_text = await scheduler.schedule_decode(
                            stream_id=stream_id,
                            segment_id=session.segment_id,
                            seq=session.seq + 1,
                            wav_bytes=wav_bytes,
                            is_final=True,
                        )
                    seg_event, _ = session.cut_segment(final_text)
                    await websocket.send_json(seg_event)

            elif "text" in msg and msg["text"]:
                txt_payload = json.loads(msg["text"])
                msg_type = txt_payload.get("type")

                if msg_type == "audio.commit":
                    # Commit entire stream
                    final_text = None
                    if not session.can_reuse_last_decode_for_final() and scheduler:
                        wav_bytes = session.get_current_segment_wav()
                        final_text = await scheduler.schedule_decode(
                            stream_id=stream_id,
                            segment_id=session.segment_id,
                            seq=session.seq + 1,
                            wav_bytes=wav_bytes,
                            is_final=True,
                        )
                    comp_event = session.complete_stream(final_text)
                    await websocket.send_json(comp_event)
                    await websocket.close()
                    break

                elif msg_type == "stream.cancel":
                    session.is_closed = True
                    await websocket.close()
                    break

    except WebSocketDisconnect:
        logger.info("Stream %s disconnected", stream_id)
    except Exception as e:
        logger.error("Exception in stream %s: %s", stream_id, e)
    finally:
        if step_task:
            step_task.cancel()
        active_sessions.pop(stream_id, None)
        if scheduler:
            scheduler.unregister_stream(stream_id)


def start_server(host: str = "127.0.0.1", port: int = 8089):
    """Start bridge server with uvicorn."""
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    port = int(os.environ.get("STREAMING_BRIDGE_PORT", "8089"))
    start_server(port=port)

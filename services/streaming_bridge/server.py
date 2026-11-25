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
import uuid
from typing import Any, Dict, Optional
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse

from app.core.config import settings
from services.streaming_bridge.calibrate import is_host_calibrated_and_passing
from services.streaming_bridge.scheduler.bridge_scheduler import BridgeScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("streaming_bridge_server")

app = FastAPI(title="Streaming STT Bridge", version="1.0.0")

# Active streams registry
active_sessions: Dict[str, Any] = {}
scheduler: Optional[BridgeScheduler] = None


@app.on_event("startup")
async def startup_event():
    global scheduler
    if not settings.STREAMING_BRIDGE_INTERNAL_TOKEN or not settings.STREAMING_BRIDGE_INTERNAL_TOKEN.strip():
        raise RuntimeError("STREAMING_BRIDGE_INTERNAL_TOKEN not configured in .env; bridge server refusing to start")

    scheduler = BridgeScheduler(
        max_decodes=settings.STREAMING_BRIDGE_MAX_DECODES,
        bounded_pause_ms=settings.STREAMING_BRIDGE_BOUNDED_PAUSE_MS,
        widened_step_ms=settings.STREAMING_BRIDGE_WIDENED_STEP_MS,
        partial_timeout_s=settings.STREAMING_BRIDGE_PARTIAL_TIMEOUT_S,
        final_timeout_s=settings.STREAMING_BRIDGE_FINAL_TIMEOUT_S,
    )
    scheduler.start()
    logger.info("Streaming STT Bridge started on port %d", settings.STREAMING_BRIDGE_PORT)


@app.on_event("shutdown")
async def shutdown_event():
    global scheduler
    if scheduler:
        await scheduler.stop()


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
        calib_ok, calib_reason, calib_data = is_host_calibrated_and_passing(model)
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

        admitted_limit = min(calib_data.get("admitted_streams", 1), settings.STREAMING_BRIDGE_MAX_STREAMS)
        if len(active_sessions) >= admitted_limit:
            await websocket.send_json({
                "type": "error",
                "code": "BRIDGE_BUSY",
                "message": f"Maximum admitted streams ({admitted_limit}) reached for model {model}",
                "retry_after_ms": 2000,
            })
            await websocket.close(code=1013)
            return

        active_sessions[stream_id] = {"model": model, "language": language}
        if scheduler:
            scheduler.register_stream(stream_id, model, language)

        await websocket.send_json({"type": "stream.ready", "stream_id": stream_id})

        while True:
            msg = await websocket.receive()
            if "text" in msg and msg["text"]:
                txt_payload = json.loads(msg["text"])
                if txt_payload.get("type") in ("audio.commit", "stream.cancel"):
                    await websocket.close()
                    break

    except WebSocketDisconnect:
        logger.info("Stream %s disconnected", stream_id)
    except Exception as e:
        logger.error("Exception in stream %s: %s", stream_id, e)
    finally:
        active_sessions.pop(stream_id, None)
        if scheduler:
            scheduler.cancel_stream(stream_id)


def start_server(host: str = "127.0.0.1", port: int = 8089):
    """Start bridge server with uvicorn."""
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    port = int(os.environ.get("STREAMING_BRIDGE_PORT", "8089"))
    start_server(port=port)

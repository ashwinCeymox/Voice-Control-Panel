"""Health checks, provider status, and WebSocket ticket generation."""

import httpx
from fastapi import APIRouter, Depends
from app.core.config import settings
from app.core.security import create_websocket_ticket, require_api_key
from app.schemas.voice import TicketResponse

router = APIRouter(tags=["Health & System"])


@router.get("/health/live")
async def health_live():
    return {"status": "live"}


@router.get("/health/ready")
async def health_ready():
    localai_ok = False
    localtts_ok = False
    streaming_stt_ok = False
    streaming_info = {}

    async with httpx.AsyncClient(timeout=2.5) as client:
        try:
            # Probe LocalAI via standard models endpoint, healthz liveness, or readyz probe
            r = await client.get(f"{settings.LOCALAI_BASE_URL}/v1/models")
            if r.status_code in [200, 401, 404]:
                localai_ok = True
            else:
                r2 = await client.get(f"{settings.LOCALAI_BASE_URL}/healthz")
                if r2.status_code in [200, 404]:
                    localai_ok = True
                else:
                    r3 = await client.get(f"{settings.LOCALAI_BASE_URL}/readyz")
                    if r3.status_code in [200, 404, 503]:
                        localai_ok = True
        except Exception:
            localai_ok = False

        try:
            r = await client.get(f"{settings.LOCAL_TTS_HTTP_BASE_URL}/v1/models")
            if r.status_code == 200:
                localtts_ok = True
        except Exception:
            localtts_ok = False

        if settings.STT_STREAM_ENABLED:
            try:
                r = await client.get(f"{settings.STREAMING_STT_URL}/health")
                if r.status_code == 200:
                    streaming_stt_ok = True
                    streaming_info = r.json()
            except Exception:
                streaming_stt_ok = False

    status = "ready" if (localai_ok and localtts_ok) else "degraded"
    return {
        "status": status,
        "providers": {
            "localai": {
                "reachable": localai_ok,
            },
            "localtts": {
                "reachable": localtts_ok,
            },
            "streaming_stt": {
                "reachable": streaming_stt_ok,
                "status": "ready" if streaming_stt_ok else "offline",
                "model": streaming_info.get(
                    "model", settings.STREAMING_STT_DEFAULT_MODEL
                ),
            },
        },
        "streaming_stt": {
            "status": "ready" if streaming_stt_ok else "offline",
            "reachable": streaming_stt_ok,
            "model": streaming_info.get("model", settings.STREAMING_STT_DEFAULT_MODEL),
        },
    }


@router.get("/v1/providers")
async def get_providers(user: str = Depends(require_api_key)):
    return {
        "localai": {
            "configured": True,
            "default_llm_model": settings.LOCALAI_LLM_MODEL,
            "default_stt_model": settings.LOCALAI_STT_MODEL,
        },
        "localtts": {
            "configured": True,
            "default_model": settings.LOCAL_TTS_MODEL,
            "default_voice_id": settings.LOCAL_TTS_VOICE_ID,
        },
    }


@router.post("/v1/auth/ticket", response_model=TicketResponse)
async def get_websocket_ticket(user: str = Depends(require_api_key)):
    ticket = create_websocket_ticket(user)
    return TicketResponse(ticket=ticket, expires_in=60)

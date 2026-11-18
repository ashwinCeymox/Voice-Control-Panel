"""
Admin API router for Usage Analytics, Timeseries, Request Logs, CSV Export,
Residency Lifecycle, and Live SSE Overview Stream.
"""

import time
import json
import asyncio
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.security import require_admin_key, ApiKeyRecord
from app.services.usage_service import UsageService
from app.services.residency_manager import residency_manager
from app.services.metrics import metrics_collector

router = APIRouter(prefix="/v1/admin", tags=["Admin Usage & Analytics"])


class ResidencySettingsPayload(BaseModel):
    idle_timeout_seconds: Optional[int] = None
    stale_timeout_seconds: Optional[int] = None
    auto_flush_seconds: Optional[int] = None
    auto_flush_enabled: Optional[bool] = None
    protected_models: Optional[List[str]] = None


@router.get("/usage/summary")
async def get_usage_summary(
    since: Optional[str] = Query(
        "24h", description="Time window (e.g. 1h, 24h, 7d, 30d) or epoch timestamp"
    ),
    until: Optional[float] = Query(None, description="Optional end epoch timestamp"),
    group_by: Optional[str] = Query(
        None, description="Optional grouping: model, endpoint, key, profile"
    ),
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Retrieve summarized usage metrics, error rates, latency percentiles, and breakdown groups."""
    return await UsageService.get_summary(since=since, until=until, group_by=group_by)


@router.get("/usage/timeseries")
async def get_usage_timeseries(
    interval: str = Query("hour", pattern="^(hour|day)$"),
    since: Optional[str] = Query("24h"),
    until: Optional[float] = Query(None),
    key_id: Optional[str] = Query(None),
    profile_id: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Retrieve usage time-series bucketed by hour or day with continuous zero-filled gaps."""
    return await UsageService.get_timeseries(
        interval=interval,
        since=since,
        until=until,
        key_id=key_id,
        profile_id=profile_id,
        model=model,
    )


@router.get("/usage/requests")
async def get_usage_requests(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    key_id: Optional[str] = Query(None),
    profile_id: Optional[str] = Query(None),
    endpoint: Optional[str] = Query(None),
    status_code: Optional[int] = Query(None, alias="status"),
    since: Optional[str] = Query(None),
    actor_type: Optional[str] = Query(
        None, description="Filter by actor type: key, admin, system"
    ),
    model: Optional[str] = Query(None, description="Filter by model ID"),
    export: Optional[str] = Query(None, description="Set to 'csv' to download as CSV"),
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Any:
    """Retrieve paged request activity logs with actor attribution or export as CSV."""
    if export == "csv":
        csv_content = await UsageService.export_requests_csv(
            key_id=key_id,
            profile_id=profile_id,
            endpoint=endpoint,
            status=status_code,
            since=since,
            actor_type=actor_type,
            model=model,
        )
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="requests.csv"',
                "Cache-Control": "no-cache",
            },
        )

    return await UsageService.get_requests_log(
        limit=limit,
        offset=offset,
        key_id=key_id,
        profile_id=profile_id,
        endpoint=endpoint,
        status=status_code,
        since=since,
        actor_type=actor_type,
        model=model,
    )


@router.get("/usage/models")
async def get_model_activity_breakdown(
    since: Optional[str] = Query("24h"),
    until: Optional[float] = Query(None),
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> List[Dict[str, Any]]:
    """Retrieve detailed model activity table with latency percentiles and top keys."""
    return await UsageService.get_model_activity(since=since, until=until)


@router.get("/models/status")
async def get_models_status(
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Retrieve warm/loaded models status, memory footprints, and system memory."""
    return await UsageService.get_models_status()


@router.get("/residency")
async def get_model_residency(
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Retrieve models currently resident in memory, their idle/stale states, and memory."""
    return await residency_manager.get_residency_status()


@router.post("/residency/{model_id}/flush")
async def flush_model(
    model_id: str,
    force: bool = Query(False, description="Force flush even if protected"),
    admin: ApiKeyRecord = Depends(require_admin_key),
):
    """Unload a specific model from engine memory."""
    success, msg = await residency_manager.flush_model(model_id, force=force)
    return {
        "status": "flushed" if success else "failed",
        "model_id": model_id,
        "message": msg,
    }


@router.post("/residency/flush-idle")
async def flush_idle_models(
    admin: ApiKeyRecord = Depends(require_admin_key),
):
    """Flush all idle or stale models not protected."""
    results = await residency_manager.flush_idle_models()
    return {"status": "completed", "results": results}


@router.get("/settings/residency")
async def get_residency_settings(
    admin: ApiKeyRecord = Depends(require_admin_key),
):
    """Get residency timeouts and auto-flush configuration."""
    status_data = await residency_manager.get_residency_status()
    return {
        "idle_timeout_seconds": status_data["idle_timeout_seconds"],
        "stale_timeout_seconds": status_data["stale_timeout_seconds"],
        "auto_flush_seconds": status_data["auto_flush_seconds"],
        "auto_flush_enabled": status_data["auto_flush_enabled"],
        "protected_models": status_data["protected_models"],
    }


@router.put("/settings/residency")
async def update_residency_settings(
    payload: ResidencySettingsPayload,
    admin: ApiKeyRecord = Depends(require_admin_key),
):
    """Update residency timeouts, auto-flush policy, and protected models."""
    updated = await residency_manager.update_settings(
        idle_timeout_seconds=payload.idle_timeout_seconds,
        stale_timeout_seconds=payload.stale_timeout_seconds,
        auto_flush_seconds=payload.auto_flush_seconds,
        auto_flush_enabled=payload.auto_flush_enabled,
        protected_models=payload.protected_models,
    )
    return {"status": "updated", "settings": updated}


@router.get("/stream/overview")
async def stream_overview(
    admin: ApiKeyRecord = Depends(require_admin_key),
):
    """Server-Sent Events (SSE) stream for live overview telemetry, memory, models, and toasts."""

    async def event_generator():
        while True:
            try:
                res_status = await residency_manager.get_residency_status()
                sys_mem = await UsageService.get_system_memory()
                toasts = residency_manager.get_toasts(consume=True)
                payload = {
                    "timestamp": time.time(),
                    "system_memory": sys_mem,
                    "models": res_status.get("models", []),
                    "auto_flush_enabled": res_status.get("auto_flush_enabled", False),
                    "toasts": toasts,
                    "stt_fallbacks": metrics_collector.get_stt_fallbacks(),
                }
                yield f"data: {json.dumps(payload)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            await asyncio.sleep(3.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

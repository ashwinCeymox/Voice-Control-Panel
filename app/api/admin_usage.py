"""
Admin API router for Usage Analytics, Timeseries, Request Logs, CSV Export, and Model Status.
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import PlainTextResponse
from app.core.security import require_admin_key, ApiKeyRecord
from app.services.usage_service import UsageService

router = APIRouter(prefix="/v1/admin", tags=["Admin Usage & Analytics"])


@router.get("/usage/summary")
async def get_usage_summary(
    since: Optional[str] = Query("24h", description="Time window (e.g. 1h, 24h, 7d, 30d) or epoch timestamp"),
    until: Optional[float] = Query(None, description="Optional end epoch timestamp"),
    group_by: Optional[str] = Query(None, description="Optional grouping: model, endpoint, key, profile"),
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
    """Retrieve usage time-series bucketed by hour or day."""
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
    export: Optional[str] = Query(None, description="Set to 'csv' to download as CSV"),
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Any:
    """Retrieve paged request activity logs or export as CSV. Zero caller content or secrets."""
    if export == "csv":
        csv_content = await UsageService.export_requests_csv(
            key_id=key_id,
            profile_id=profile_id,
            endpoint=endpoint,
            status=status_code,
            since=since,
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
    )


@router.get("/models/status")
async def get_models_status(
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Retrieve warm/loaded models status, memory footprints, and system memory."""
    return await UsageService.get_models_status()

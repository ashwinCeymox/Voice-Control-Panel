"""
Prometheus metrics export endpoint.
"""

from fastapi import APIRouter, Request, Response
from app.core.security import get_authenticated_key, resolve_client_ip
from app.services.metrics import metrics_collector

router = APIRouter(tags=["Monitoring & Metrics"])


@router.get("/metrics")
async def get_metrics(request: Request) -> Response:
    """Expose application metrics in standard Prometheus text exposition format.
    Requires an authenticated key or session for external callers; permits local loopback scrapers.
    """
    client_ip = resolve_client_ip(request)
    if client_ip not in ("127.0.0.1", "::1", "localhost", "testclient"):
        await get_authenticated_key(request)

    data = metrics_collector.render_prometheus()
    return Response(
        content=data,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )

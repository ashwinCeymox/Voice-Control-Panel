"""
Prometheus metrics export endpoint.
"""
from fastapi import APIRouter, Response
from app.services.metrics import metrics_collector

router = APIRouter(tags=["Monitoring & Metrics"])


@router.get("/metrics")
async def get_metrics() -> Response:
    """Expose application metrics in standard Prometheus text exposition format."""
    data = metrics_collector.render_prometheus()
    return Response(
        content=data,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )

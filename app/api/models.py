"""Unified Model Registry REST Endpoints."""

from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.security import require_api_key, require_admin_key
from app.services.key_manager import ApiKeyRecord
from app.schemas.voice import ModelInfo, ModelListResponse
from app.services.model_registry import model_registry
from app.services.model_manifest import model_manifest

router = APIRouter(prefix="/v1", tags=["Models"])


class ClassificationOverrideRequest(BaseModel):
    task: str
    kind: Optional[str] = None
    display_name: Optional[str] = None


@router.get("/models", response_model=ModelListResponse)
async def list_models(
    task: Optional[str] = Query(None, description="Filter by task: llm, stt, tts, vad"),
    kind: Optional[str] = Query(None, description="Filter by kind: batch, streaming"),
    installed: Optional[bool] = Query(None, description="Filter by installed status"),
    available: Optional[bool] = Query(None, description="Filter by availability"),
    user: str = Depends(require_api_key),
):
    """List registered models with optional capability filtering."""
    registered = await model_registry.list_models(
        task=task,
        kind=kind,
        installed=installed,
        available=available,
        include_unclassified=False,
    )

    data = [
        ModelInfo(
            id=m["id"],
            name=m.get("display_name") or m["id"],
            display_name=m.get("display_name"),
            owned_by=m.get("engine", "local"),
            type=m.get("task"),
            task=m.get("task"),
            kind=m.get("kind"),
            engine=m.get("engine"),
            runtime=m.get("runtime"),
            available=m.get("available", True),
            unavailable_reason=m.get("unavailable_reason"),
            installed=m.get("installed", True),
            size_mb=m.get("size_mb", 0),
            streaming_mode=m.get("streaming_mode", "none"),
            streaming_backend=m.get("streaming_backend"),
            transports=m.get("transports", []),
        )
        for m in registered
    ]

    return {
        "object": "list",
        "data": data,
        "models": registered,
    }


@router.get("/admin/models")
async def list_admin_models(
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Admin full registry view including unclassified and unavailable models with reasons."""
    all_models = await model_registry.list_models(include_unclassified=True)
    return {
        "total": len(all_models),
        "models": all_models,
    }


@router.patch("/admin/models/{id:path}/classification")
@router.patch("/admin/models/{id}/classification")
@router.patch("/models/{id:path}/classification")
@router.patch("/models/{id}/classification")
async def override_model_classification(
    id: str,
    payload: ClassificationOverrideRequest,
    admin: ApiKeyRecord = Depends(require_admin_key),
):
    """Override classification (task, kind, display_name) for a model."""
    res = await model_registry.set_override(
        model_id=id,
        task=payload.task,
        kind=payload.kind,
        display_name=payload.display_name,
    )
    await model_manifest.update_model_classification(
        model_id=id,
        task=payload.task,
        kind=payload.kind,
        display_name=payload.display_name,
    )
    model_registry.invalidate_cache()
    return {"status": "updated", "override": res}


@router.post("/admin/models/rescan")
async def rescan_models(
    admin: ApiKeyRecord = Depends(require_admin_key),
):
    """Trigger active rescan of model metadata and engine availability checks."""
    model_registry.invalidate_cache()
    from app.services.model_manifest import model_manifest

    await model_manifest.sync_manifest()
    models = await model_registry.list_models(include_unclassified=True)
    return {
        "status": "rescanned",
        "total": len(models),
        "models": models,
    }

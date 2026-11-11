"""LocalAI and installed models discovery."""
import httpx
from fastapi import APIRouter, Depends
from app.core.config import settings
from app.core.security import require_api_key
from app.schemas.voice import ModelInfo, ModelListResponse
from app.api.gallery import get_installed_models_data

router = APIRouter(prefix="/v1", tags=["Models"])


@router.get("/models", response_model=ModelListResponse)
async def list_models(user: str = Depends(require_api_key)):
    models = []
    seen = set()

    # 1. Query LocalAI
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(f"{settings.LOCALAI_BASE_URL}/v1/models")
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                for m in data:
                    mid = m.get("id")
                    if mid and mid not in seen:
                        seen.add(mid)
                        m_type = "stt" if "whisper" in mid.lower() else "llm"
                        models.append(
                            ModelInfo(
                                id=mid,
                                name=mid,
                                owned_by="localai",
                                type=m_type,
                            )
                        )
    except Exception:
        pass

    # 2. Include any models from the installed registry
    try:
        installed = await get_installed_models_data()
        for cat, items in installed.items():
            for it in items:
                mid = it["id"]
                if mid not in seen:
                    seen.add(mid)
                    models.append(
                        ModelInfo(
                            id=mid,
                            name=it.get("name", mid),
                            owned_by=it.get("provider", "localai"),
                            type=cat,
                        )
                    )
    except Exception:
        pass

    return ModelListResponse(data=models)

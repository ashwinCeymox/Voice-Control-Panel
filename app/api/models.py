"""LocalAI models discovery."""
import httpx
from fastapi import APIRouter, Depends
from app.core.config import settings
from app.core.security import require_api_key
from app.schemas.voice import ModelInfo, ModelListResponse

router = APIRouter(prefix="/v1", tags=["Models"])


@router.get("/models", response_model=ModelListResponse)
async def list_models(user: str = Depends(require_api_key)):
    models = []
    # Query LocalAI
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(f"{settings.LOCALAI_BASE_URL}/v1/models")
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                for m in data:
                    models.append(
                        ModelInfo(
                            id=m.get("id"),
                            name=m.get("id"),
                            owned_by="localai",
                            type="llm",
                        )
                    )
    except Exception:
        pass

    # Include defaults if empty
    if not models:
        models = [
            ModelInfo(id=settings.LOCALAI_LLM_MODEL, name="Default LLM", type="llm", owned_by="localai"),
            ModelInfo(id=settings.LOCALAI_STT_MODEL, name="Default STT", type="stt", owned_by="localai"),
        ]

    return ModelListResponse(data=models)

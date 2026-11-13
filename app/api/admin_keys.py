"""
Admin API router for API key management, permissions, and usage reporting.
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from app.core.security import require_admin_key
from app.services.key_manager import KeyManager, ApiKeyRecord

router = APIRouter(prefix="/v1/admin", tags=["Admin Key Management"])


class CreateKeyRequest(BaseModel):
    name: str = Field(..., description="Descriptive identifier for caller/application")
    role: str = Field("caller", pattern="^(caller|admin)$")
    scopes: Optional[List[str]] = Field(None, description="Allowed scopes: llm, stt, tts, stt_stream")
    rate_limit: int = Field(60, ge=1, le=10000)
    max_concurrent: int = Field(5, ge=1, le=100)
    expires_in_days: Optional[float] = None


@router.post("/keys", status_code=status.HTTP_201_CREATED)
async def create_key(
    req: CreateKeyRequest,
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Create a new API key. The plain key is returned only once in this response."""
    expires_at = None
    if req.expires_in_days:
        import time
        expires_at = time.time() + (req.expires_in_days * 86400)

    result = await KeyManager.create_key(
        name=req.name,
        role=req.role,
        scopes=req.scopes,
        rate_limit=req.rate_limit,
        max_concurrent=req.max_concurrent,
        expires_at=expires_at,
    )
    return result


@router.get("/keys")
async def list_keys(
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> List[Dict[str, Any]]:
    """List all API keys (without plain secrets or hashes)."""
    return await KeyManager.list_keys()


@router.delete("/keys/{key_id}")
async def revoke_key(
    key_id: str,
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Revoke an API key immediately."""
    success = await KeyManager.revoke_key(key_id)
    if not success:
        raise HTTPException(status_code=404, detail="Key not found or already revoked")
    return {"id": key_id, "status": "revoked"}


@router.get("/usage")
async def get_usage(
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> List[Dict[str, Any]]:
    """Retrieve usage activity records."""
    return await KeyManager.get_usage_summary()

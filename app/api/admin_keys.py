"""
Admin API router for API key management, permissions, and usage reporting.
"""
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from app.core.config import settings
from app.core.security import require_admin_key
from app.services.key_manager import KeyManager, ApiKeyRecord
from app.storage.database import get_db

router = APIRouter(prefix="/v1/admin", tags=["Admin Key Management"])


class CreateKeyRequest(BaseModel):
    name: str = Field(..., description="Descriptive identifier for caller/application")
    role: str = Field("caller", pattern="^(caller|admin)$")
    scopes: Optional[List[str]] = Field(None, description="Allowed scopes: llm, stt, tts, stt_stream, voice, voice_stream")
    rate_limit: int = Field(60, ge=1, le=10000)
    max_concurrent: int = Field(5, ge=1, le=100)
    max_daily_tokens: Optional[int] = Field(None, ge=1, description="Daily token quota cap")
    max_daily_audio_seconds: Optional[float] = Field(None, ge=1.0, description="Daily audio duration quota cap in seconds")
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
        max_daily_tokens=req.max_daily_tokens,
        max_daily_audio_seconds=req.max_daily_audio_seconds,
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


@router.get("/keys/{key_id}/quota")
async def get_key_quota(
    key_id: str,
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Retrieve daily quota limits and current 24-hour usage for an API key."""
    return await KeyManager.get_key_quota(key_id)


@router.get("/usage")
async def get_usage(
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> List[Dict[str, Any]]:
    """Retrieve usage activity records."""
    return await KeyManager.get_usage_summary()


@router.post("/backup")
async def trigger_database_backup(
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Create an atomic, verified SQLite online snapshot using VACUUM INTO."""
    backup_dir = Path(os.getenv("BACKUP_DIR", "data/backups"))
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    nano = int(time.time() * 1000) % 1000
    dest_file = backup_dir / f"app-backup-{timestamp}-{nano:03d}.db"
    if dest_file.exists():
        dest_file.unlink()
    dest_path_str = str(dest_file.resolve())

    # Execute VACUUM INTO via database connection
    async with get_db() as db:
        await db.execute(f"VACUUM INTO '{dest_path_str}'")

    # Verify integrity of the snapshot
    integrity = "failed"
    async with aiosqlite.connect(dest_path_str) as snap_db:
        async with snap_db.execute("PRAGMA integrity_check;") as cursor:
            row = await cursor.fetchone()
            if row:
                integrity = row[0]

    return {
        "status": "ok",
        "backup_file": dest_file.name,
        "backup_path": dest_path_str,
        "size_bytes": dest_file.stat().st_size,
        "integrity": integrity,
        "created_at": time.time(),
    }


@router.get("/backups")
async def list_database_backups(
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> List[Dict[str, Any]]:
    """List available database backup snapshots sorted by newest first."""
    backup_dir = Path(os.getenv("BACKUP_DIR", "data/backups"))
    if not backup_dir.exists():
        return []

    results = []
    for item in sorted(backup_dir.glob("app-backup-*.db"), key=lambda p: p.stat().st_mtime, reverse=True):
        st = item.stat()
        results.append({
            "filename": item.name,
            "path": str(item.resolve()),
            "size_bytes": st.st_size,
            "created_at": st.st_mtime,
        })
    return results

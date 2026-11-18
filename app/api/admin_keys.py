"""
Admin API router for API key management, permissions, usage reporting, settings, and audit logs.
"""

import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from app.core.security import require_admin_key
from app.services.key_manager import KeyManager, ApiKeyRecord
from app.storage.database import get_db

router = APIRouter(prefix="/v1/admin", tags=["Admin Key Management"])


class CreateKeyRequest(BaseModel):
    name: str = Field(..., description="Descriptive identifier for caller/application")
    role: str = Field("caller", pattern="^(caller|admin)$")
    scopes: Optional[List[str]] = Field(
        None,
        description="Allowed scopes: llm, stt, tts, stt_stream, voice, voice_stream",
    )
    rate_limit: int = Field(60, ge=1, le=10000)
    max_concurrent: int = Field(5, ge=1, le=100)
    max_daily_tokens: Optional[int] = Field(
        None, ge=1, description="Daily token quota cap"
    )
    max_daily_audio_seconds: Optional[float] = Field(
        None, ge=1.0, description="Daily audio duration quota cap in seconds"
    )
    expires_in_days: Optional[float] = None
    profile_id: Optional[str] = None
    model_allowlist: Optional[List[str]] = None
    ip_allowlist: Optional[List[str]] = None


class PatchKeyRequest(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = Field(None, pattern="^(active|paused|revoked)$")
    scopes: Optional[List[str]] = None
    rate_limit: Optional[int] = Field(None, ge=1, le=10000)
    max_concurrent: Optional[int] = Field(None, ge=1, le=100)
    max_daily_tokens: Optional[int] = None
    max_daily_audio_seconds: Optional[float] = None
    model_allowlist: Optional[List[str]] = None
    ip_allowlist: Optional[List[str]] = None
    expires_in_days: Optional[float] = None


class RotateKeyRequest(BaseModel):
    grace_period_days: float = Field(
        1.0, ge=0.0, le=30.0, description="Days old key remains valid before expiring"
    )


class UpdateDashboardSettingsRequest(BaseModel):
    base_url: Optional[str] = None
    default_rate_limit: Optional[int] = Field(None, ge=1)
    default_max_concurrent: Optional[int] = Field(None, ge=1)
    usage_retention_days: Optional[int] = Field(None, ge=1, le=365)


@router.post("/keys", status_code=status.HTTP_201_CREATED)
async def create_key(
    req: CreateKeyRequest,
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Create a new API key. The plain key is returned only once in this response."""
    expires_at = None
    if req.expires_in_days:
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
        profile_id=req.profile_id,
        model_allowlist=req.model_allowlist,
        ip_allowlist=req.ip_allowlist,
    )

    await KeyManager.record_audit(
        admin_id=admin.id,
        action="key.create",
        target_type="key",
        target_id=result["id"],
        details={
            "name": req.name,
            "prefix": result["prefix"],
            "profile_id": req.profile_id,
        },
    )
    return result


@router.get("/keys")
async def list_keys(
    profile_id: Optional[str] = Query(None, description="Optional profile ID filter"),
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> List[Dict[str, Any]]:
    """List API keys (without plain secrets or hashes)."""
    return await KeyManager.list_keys(profile_id=profile_id)


@router.patch("/keys/{key_id}")
async def patch_key(
    key_id: str,
    req: PatchKeyRequest,
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Update key metadata, status (active/paused), allowlists, or rate limits."""
    updates: Dict[str, Any] = {}
    if req.name is not None:
        updates["name"] = req.name
    if req.status is not None:
        updates["status"] = req.status
    if req.scopes is not None:
        updates["scopes"] = req.scopes
    if req.rate_limit is not None:
        updates["rate_limit"] = req.rate_limit
    if req.max_concurrent is not None:
        updates["max_concurrent"] = req.max_concurrent
    if req.max_daily_tokens is not None:
        updates["max_daily_tokens"] = req.max_daily_tokens
    if req.max_daily_audio_seconds is not None:
        updates["max_daily_audio_seconds"] = req.max_daily_audio_seconds
    if req.model_allowlist is not None:
        updates["model_allowlist"] = req.model_allowlist
    if req.ip_allowlist is not None:
        updates["ip_allowlist"] = req.ip_allowlist
    if req.expires_in_days is not None:
        updates["expires_at"] = time.time() + (req.expires_in_days * 86400)

    updated = await KeyManager.patch_key(key_id, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Key not found")

    await KeyManager.record_audit(
        admin_id=admin.id,
        action="key.patch",
        target_type="key",
        target_id=key_id,
        details=updates,
    )
    return updated


@router.post("/keys/{key_id}/rotate", status_code=status.HTTP_201_CREATED)
async def rotate_key(
    key_id: str,
    req: RotateKeyRequest,
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Rotate an API key: generates a new key with grace period for the old key."""
    new_key = await KeyManager.rotate_key(
        key_id, grace_period_days=req.grace_period_days
    )
    await KeyManager.record_audit(
        admin_id=admin.id,
        action="key.rotate",
        target_type="key",
        target_id=key_id,
        details={
            "new_key_id": new_key["id"],
            "grace_period_days": req.grace_period_days,
        },
    )
    return new_key


@router.delete("/keys/{key_id}")
async def revoke_key(
    key_id: str,
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Revoke an API key immediately."""
    success = await KeyManager.revoke_key(key_id)
    if not success:
        raise HTTPException(status_code=404, detail="Key not found or already revoked")

    await KeyManager.record_audit(
        admin_id=admin.id,
        action="key.revoke",
        target_type="key",
        target_id=key_id,
    )
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


# -------------------------------------------------------------------
# Dashboard Settings & Audit Log
# -------------------------------------------------------------------
@router.get("/settings/dashboard")
async def get_dashboard_settings(
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Retrieve developer dashboard settings."""
    return await KeyManager.get_dashboard_settings()


@router.put("/settings/dashboard")
async def update_dashboard_settings(
    req: UpdateDashboardSettingsRequest,
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Update developer dashboard settings."""
    updated = await KeyManager.update_dashboard_settings(
        base_url=req.base_url,
        default_rate_limit=req.default_rate_limit,
        default_max_concurrent=req.default_max_concurrent,
        usage_retention_days=req.usage_retention_days,
    )
    await KeyManager.record_audit(
        admin_id=admin.id,
        action="settings.update",
        target_type="settings",
        target_id="dashboard",
        details=req.model_dump(exclude_none=True),
    )
    return updated


@router.get("/audit")
async def get_audit_log(
    limit: int = Query(100, ge=1, le=1000),
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> List[Dict[str, Any]]:
    """Retrieve administrative audit trail."""
    return await KeyManager.get_audit_log(limit=limit)


# -------------------------------------------------------------------
# Disaster Recovery & Backups
# -------------------------------------------------------------------
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

    async with get_db() as db:
        await db.execute(f"VACUUM INTO '{dest_path_str}'")

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
    for item in sorted(
        backup_dir.glob("app-backup-*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        st = item.stat()
        results.append(
            {
                "filename": item.name,
                "path": str(item.resolve()),
                "size_bytes": st.st_size,
                "created_at": st.st_mtime,
            }
        )
    return results

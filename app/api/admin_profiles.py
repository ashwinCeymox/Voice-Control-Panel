"""
Admin API router for Application Profiles management.
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from app.core.security import require_admin_key, ApiKeyRecord
from app.services.key_manager import KeyManager

router = APIRouter(prefix="/v1/admin/profiles", tags=["Admin Profiles"])


class CreateProfileRequest(BaseModel):
    name: str = Field(..., description="Unique application or consuming project name")
    description: Optional[str] = Field(None, description="Detailed profile description")
    contact: Optional[str] = Field(None, description="Owner contact email or team")
    notes: Optional[str] = Field(
        None, description="Internal operational or billing notes"
    )


class UpdateProfileRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    contact: Optional[str] = None
    notes: Optional[str] = None
    disabled: Optional[bool] = None


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_profile(
    req: CreateProfileRequest,
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Create a new consuming application profile."""
    profile = await KeyManager.create_profile(
        name=req.name,
        description=req.description,
        contact=req.contact,
        notes=req.notes,
    )
    await KeyManager.record_audit(
        admin_id=admin.id,
        action="profile.create",
        target_type="profile",
        target_id=profile["id"],
        details={"name": req.name},
    )
    return profile


@router.get("")
async def list_profiles(
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> List[Dict[str, Any]]:
    """List all application profiles."""
    return await KeyManager.list_profiles()


@router.get("/{profile_id}")
async def get_profile(
    profile_id: str,
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Get single profile and its associated API keys (without secrets)."""
    profile = await KeyManager.get_profile(profile_id, include_keys=True)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found"
        )
    return profile


@router.patch("/{profile_id}")
async def update_profile(
    profile_id: str,
    req: UpdateProfileRequest,
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Update profile details or toggle disabled state."""
    profile = await KeyManager.update_profile(
        profile_id=profile_id,
        name=req.name,
        description=req.description,
        contact=req.contact,
        notes=req.notes,
        disabled=req.disabled,
    )
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found"
        )

    await KeyManager.record_audit(
        admin_id=admin.id,
        action="profile.update",
        target_type="profile",
        target_id=profile_id,
        details={"disabled": req.disabled, "name": req.name},
    )
    return profile


@router.delete("/{profile_id}")
async def delete_profile(
    profile_id: str,
    admin: ApiKeyRecord = Depends(require_admin_key),
) -> Dict[str, Any]:
    """Delete an application profile."""
    success = await KeyManager.delete_profile(profile_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found"
        )

    await KeyManager.record_audit(
        admin_id=admin.id,
        action="profile.delete",
        target_type="profile",
        target_id=profile_id,
    )
    return {"status": "deleted", "id": profile_id}

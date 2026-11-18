"""Pydantic schemas for super admin panel authentication."""

from pydantic import BaseModel, Field


class PanelLoginRequest(BaseModel):
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=1)


class PanelLoginResponse(BaseModel):
    email: str
    must_change_password: bool


class PanelMeResponse(BaseModel):
    email: str
    must_change_password: bool


class PanelChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=12, max_length=128)


class PanelMessageResponse(BaseModel):
    message: str
    must_change_password: bool = False

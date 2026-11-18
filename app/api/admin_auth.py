"""Super admin authentication and session management router for the control panel."""

import logging
from fastapi import APIRouter, Depends, Request, Response

from app.core.config import settings
from app.core.errors import UnauthorizedError, ForbiddenError
from app.core.security import resolve_client_ip
from app.schemas.auth import (
    PanelLoginRequest,
    PanelLoginResponse,
    PanelMeResponse,
    PanelChangePasswordRequest,
    PanelMessageResponse,
)
from app.services.admin_auth import AdminAuthService

logger = logging.getLogger("voice_agent.panel_auth")

router = APIRouter(prefix="/v1/panel", tags=["Panel Auth"])


def require_csrf(request: Request) -> None:
    """Enforce CSRF header on POST requests."""
    if request.method == "POST":
        x_requested_with = request.headers.get("x-requested-with")
        if not x_requested_with or not x_requested_with.strip():
            raise ForbiddenError("Missing X-Requested-With header (CSRF protection).")


async def get_current_panel_session(request: Request) -> dict:
    """Dependency verifying that the request has a valid panel session."""
    session_id = request.cookies.get("admin_session")
    if session_id:
        session_info = await AdminAuthService.validate_session(session_id)
        if session_info:
            return session_info
        raise UnauthorizedError("Invalid or expired session.")

    # Fallback to Admin API key for dev/script parity if present
    auth_header = request.headers.get("authorization")
    if auth_header:
        from app.core.security import get_authenticated_key

        try:
            record = await get_authenticated_key(request, authorization=auth_header)
            if record.role == "admin":
                admin = await AdminAuthService.get_admin_user()
                email = admin["email"] if admin else settings.ADMIN_EMAIL
                return {"session_id": "", "email": email, "must_change_password": False}
        except Exception:
            pass

    raise UnauthorizedError("Panel session authentication required.")


@router.post("/login", response_model=PanelLoginResponse)
async def panel_login(
    payload: PanelLoginRequest,
    request: Request,
    response: Response,
    _csrf: None = Depends(require_csrf),
):
    """Log into the super admin panel. Sets an HttpOnly, SameSite=Strict session cookie."""
    client_ip = resolve_client_ip(request)
    user_agent = request.headers.get("user-agent")

    session_id, must_change, email = await AdminAuthService.login(
        email=payload.email,
        password=payload.password,
        client_ip=client_ip,
        user_agent=user_agent,
    )

    # Issue session cookie
    response.set_cookie(
        key="admin_session",
        value=session_id,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
        path="/",
        max_age=7 * 86400,
    )
    return PanelLoginResponse(email=email, must_change_password=must_change)


@router.post("/logout", response_model=PanelMessageResponse)
async def panel_logout(
    request: Request,
    response: Response,
    session_info: dict = Depends(get_current_panel_session),
    _csrf: None = Depends(require_csrf),
):
    """Terminate the current session and clear the session cookie."""
    session_id = request.cookies.get("admin_session")
    if session_id:
        await AdminAuthService.logout(session_id)

    response.delete_cookie(
        key="admin_session",
        path="/",
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
    )
    return PanelMessageResponse(message="Logged out successfully.")


@router.get("/me", response_model=PanelMeResponse)
async def panel_me(
    session_info: dict = Depends(get_current_panel_session),
):
    """Retrieve super admin profile and forced password change status."""
    return PanelMeResponse(
        email=session_info["email"],
        must_change_password=session_info.get("must_change_password", False),
    )


@router.post("/change-password", response_model=PanelMessageResponse)
async def panel_change_password(
    payload: PanelChangePasswordRequest,
    request: Request,
    response: Response,
    session_info: dict = Depends(get_current_panel_session),
    _csrf: None = Depends(require_csrf),
):
    """Verify old password, enforce new password complexity, and issue a fresh session."""
    user_agent = request.headers.get("user-agent")
    new_session_id = await AdminAuthService.change_password(
        old_password=payload.old_password,
        new_password=payload.new_password,
        user_agent=user_agent,
    )

    # Set fresh session cookie
    response.set_cookie(
        key="admin_session",
        value=new_session_id,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
        path="/",
        max_age=7 * 86400,
    )
    return PanelMessageResponse(
        message="Password changed successfully.", must_change_password=False
    )

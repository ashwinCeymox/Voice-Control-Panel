"""Authentication, API-key hashing, and WebSocket ticket management."""

import hashlib
import hmac
import secrets
import time
from typing import Dict, List, Optional, Tuple
from fastapi import Header, Query, Request
from pydantic import BaseModel
from app.core.config import settings
from app.core.errors import UnauthorizedError, ForbiddenError


class ApiKeyRecord(BaseModel):
    id: str
    name: str
    role: str  # "caller" | "admin"
    scopes: List[str]  # e.g. ["llm", "stt", "tts"]
    rate_limit: int = 60
    max_concurrent: int = 5
    max_daily_tokens: Optional[int] = None
    max_daily_audio_seconds: Optional[float] = None
    expires_at: Optional[float] = None
    revoked_at: Optional[float] = None
    created_at: float
    profile_id: Optional[str] = None
    prefix: Optional[str] = None
    status: str = "active"
    model_allowlist: Optional[List[str]] = None
    ip_allowlist: Optional[List[str]] = None
    rotated_from: Optional[str] = None
    rotation_expires_at: Optional[float] = None
    last_used_at: Optional[float] = None


def check_model_allowed(key: ApiKeyRecord, model_id: Optional[str]) -> None:
    """Enforce model_allowlist on API key if defined."""
    if not key or key.role == "admin" or not model_id:
        return
    if key.model_allowlist and len(key.model_allowlist) > 0:
        if model_id not in key.model_allowlist:
            raise ForbiddenError(f"Model '{model_id}' is not allowed for this key.")


# In-memory ticket storage: {ticket_str: (created_at_timestamp, user_id)}
_TICKETS: Dict[str, Tuple[float, str]] = {}
TICKET_EXPIRY_SECONDS = 60

# In-memory authorized API key hashes: {key_hash: role_or_owner}
_DEV_KEY_HASHES: Dict[str, str] = {}


def hash_api_key(plain_key: str) -> str:
    """Create a slow HMAC-SHA256 hash using the secret key."""
    return hmac.new(
        settings.API_KEY_HASH_SECRET.encode("utf-8"),
        plain_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def generate_api_key() -> Tuple[str, str, str]:
    """Generate a high-entropy random key, its prefix, and its hash."""
    random_part = secrets.token_urlsafe(32)
    raw_key = f"va-{random_part}"
    prefix = f"va_live_{random_part[:4]}..."
    key_hash = hash_api_key(raw_key)
    return raw_key, prefix, key_hash


def register_api_key(plain_key: str, owner: str = "admin") -> None:
    """Register an active API key hash in memory."""
    _DEV_KEY_HASHES[hash_api_key(plain_key)] = owner


def verify_api_key(plain_key: str, client_ip: Optional[str] = None) -> Optional[str]:
    """Verify an API key and return owner/role if valid."""
    if not plain_key:
        return None

    # Check configured admin key if set
    if settings.ADMIN_API_KEY and hmac.compare_digest(
        plain_key, settings.ADMIN_API_KEY
    ):
        # Refuse default development key if running in staging/production, listening publicly,
        # or requested from a remote non-loopback IP address.
        if plain_key == "dev-voice-key-12345":
            is_cloud_env = settings.ENVIRONMENT.lower() in ("production", "staging")
            is_public_host = settings.GATEWAY_HOST not in ("127.0.0.1", "localhost")
            is_remote_client = bool(
                client_ip
                and client_ip not in ("127.0.0.1", "::1", "localhost", "testclient")
            )
            if is_cloud_env or is_public_host or is_remote_client:
                return None
        return "admin"

    key_hash = hash_api_key(plain_key)
    if key_hash in _DEV_KEY_HASHES:
        return _DEV_KEY_HASHES[key_hash]
    return None


def resolve_client_ip(request: Request) -> str:
    """Extract real client IP taking into account TRUSTED_PROXY_IPS."""
    direct_peer = request.client.host if request.client else "127.0.0.1"
    trusted_proxies = settings.trusted_proxies
    if direct_peer in trusted_proxies:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            # Client IP is the first entry in X-Forwarded-For
            return forwarded_for.split(",")[0].strip()
    return direct_peer


async def get_authenticated_key(
    request: Request,
    authorization: Optional[str] = Header(None),
    xi_api_key: Optional[str] = Header(None, alias="xi-api-key"),
    api_key: Optional[str] = Query(None),
) -> ApiKeyRecord:
    """Resolve API key or panel session to an ApiKeyRecord."""
    key = None
    auth_val = (
        authorization
        if isinstance(authorization, str)
        else request.headers.get("authorization")
    )
    xi_val = (
        xi_api_key if isinstance(xi_api_key, str) else request.headers.get("xi-api-key")
    )
    query_val = (
        api_key if isinstance(api_key, str) else request.query_params.get("api_key")
    )

    if auth_val:
        parts = auth_val.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            key = parts[1].strip()
        elif len(parts) == 1 and parts[0].strip():
            # Tolerate direct Authorization: <api-key> without Bearer prefix
            key = parts[0].strip()
    elif xi_val:
        key = xi_val.strip()
    elif query_val:
        key = query_val.strip()

    client_ip = resolve_client_ip(request)

    # Sanitize blank or uninitialized frontend token strings
    if key in ("", "null", "undefined", "None"):
        key = None

    # 1. If an explicit API key was supplied, validate it
    if key:
        from app.services.key_manager import KeyManager

        record = await KeyManager.authenticate(key, client_ip=client_ip)
        if record:
            request.state.api_key = record
            request.state.user = record.role
            return record

        owner = verify_api_key(key, client_ip=client_ip)
        if owner:
            role = "admin" if owner in ("admin", "developer") else "caller"
            record = ApiKeyRecord(
                id=f"key-mem-{owner}",
                name=owner,
                role=role,
                scopes=[
                    "llm",
                    "stt",
                    "tts",
                    "admin",
                    "voice",
                    "voice_stream",
                    "stt_stream",
                ]
                if role == "admin"
                else ["llm", "stt", "tts"],
                rate_limit=1000,
                max_concurrent=50,
                created_at=0.0,
            )
            request.state.api_key = record
            request.state.user = owner
            return record

        raise UnauthorizedError("Invalid or revoked API key.")

    # 2. Check for panel session cookie
    session_id = request.cookies.get("admin_session")
    if session_id:
        from app.services.admin_auth import AdminAuthService

        session_info = await AdminAuthService.validate_session(session_id)
        if session_info:
            record = ApiKeyRecord(
                id="panel-super-admin",
                name=session_info["email"],
                role="admin",
                scopes=[
                    "admin",
                    "llm",
                    "stt",
                    "tts",
                    "voice",
                    "voice_stream",
                    "stt_stream",
                ],
                rate_limit=10000,
                max_concurrent=100,
                created_at=0.0,
            )
            request.state.api_key = record
            request.state.user = "admin"
            return record
        raise UnauthorizedError("Invalid or expired session.")

    raise UnauthorizedError(
        "Missing API key. Provide Bearer, xi-api-key header, or api_key query param."
    )


async def require_api_key(
    request: Request,
    authorization: Optional[str] = Header(None),
    xi_api_key: Optional[str] = Header(None, alias="xi-api-key"),
    api_key: Optional[str] = Query(None),
) -> str:
    """FastAPI dependency accepting either Bearer, xi-api-key, or api_key query."""
    record = await get_authenticated_key(request, authorization, xi_api_key, api_key)
    return record.role


async def require_admin_key(
    request: Request,
    authorization: Optional[str] = Header(None),
    xi_api_key: Optional[str] = Header(None, alias="xi-api-key"),
    api_key: Optional[str] = Query(None),
) -> ApiKeyRecord:
    """FastAPI dependency requiring role == 'admin'."""
    record = await get_authenticated_key(request, authorization, xi_api_key, api_key)
    if record.role != "admin":
        raise ForbiddenError("Admin role required for this endpoint.")
    return record


def require_scope(required_scope: str):
    """FastAPI dependency factory enforcing capability scopes for API keys (supports streaming hierarchy)."""

    async def _scope_checker(
        request: Request,
        authorization: Optional[str] = Header(None),
        xi_api_key: Optional[str] = Header(None, alias="xi-api-key"),
        api_key: Optional[str] = Query(None),
    ) -> ApiKeyRecord:
        record = await get_authenticated_key(
            request, authorization, xi_api_key, api_key
        )
        if record.role == "admin":
            return record

        user_scopes = set(s.lower() for s in (record.scopes or []))
        if "admin" in user_scopes:
            return record

        # Scope capability mapping allowing streaming across TTS, STT, and LLM
        if required_scope == "llm" and bool({"llm", "voice", "voice_stream"}.intersection(user_scopes)):
            return record
        if required_scope in ("stt", "stt_stream") and bool({"stt", "stt_stream", "voice", "voice_stream"}.intersection(user_scopes)):
            return record
        if required_scope in ("tts", "tts_stream") and bool({"tts", "voice", "voice_stream"}.intersection(user_scopes)):
            return record
        if required_scope in ("voice", "voice_stream"):
            if bool({"voice", "voice_stream"}.intersection(user_scopes)) or {"llm", "stt", "tts"}.issubset(user_scopes):
                return record

        if required_scope.lower() in user_scopes:
            return record

        raise ForbiddenError(f"Missing required scope: '{required_scope}'")

    return _scope_checker


def create_websocket_ticket(user: str = "default") -> str:
    """Create a single-use connection ticket valid for 60 seconds."""
    ticket = secrets.token_urlsafe(32)
    _TICKETS[ticket] = (time.time(), user)
    return ticket


def validate_and_consume_ticket(ticket: str) -> Optional[str]:
    """Validate and immediately consume a single-use ticket."""
    if ticket not in _TICKETS:
        return None
    created_at, user = _TICKETS.pop(ticket)
    if time.time() - created_at > TICKET_EXPIRY_SECONDS:
        return None
    return user

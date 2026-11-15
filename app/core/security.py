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


def verify_api_key(plain_key: str) -> Optional[str]:
    """Verify an API key and return owner/role if valid."""
    if not plain_key:
        return None
    
    # Check configured admin key if set
    if settings.ADMIN_API_KEY and hmac.compare_digest(plain_key, settings.ADMIN_API_KEY):
        return "admin"
        
    key_hash = hash_api_key(plain_key)
    if key_hash in _DEV_KEY_HASHES:
        return _DEV_KEY_HASHES[key_hash]
    return None


async def get_authenticated_key(
    request: Request,
    authorization: Optional[str] = Header(None),
    xi_api_key: Optional[str] = Header(None, alias="xi-api-key"),
) -> ApiKeyRecord:
    """Resolve API key to an ApiKeyRecord from DB or admin fallback."""
    key = None
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            key = parts[1].strip()
    elif xi_api_key:
        key = xi_api_key.strip()

    if not key:
        raise UnauthorizedError("Missing API key. Provide Bearer or xi-api-key header.")

    # 1. Database check
    from app.services.key_manager import KeyManager
    client_ip = request.client.host if request.client else None
    record = await KeyManager.authenticate(key, client_ip=client_ip)
    if record:
        request.state.api_key = record
        request.state.user = record.role
        return record

    # 2. In-memory hash check for test fixtures
    owner = verify_api_key(key)
    if owner:
        role = "admin" if owner in ("admin", "developer") else "caller"
        record = ApiKeyRecord(
            id=f"key-mem-{owner}",
            name=owner,
            role=role,
            scopes=["llm", "stt", "tts", "admin"] if role == "admin" else ["llm", "stt", "tts"],
            rate_limit=1000,
            max_concurrent=50,
            created_at=0.0,
        )
        request.state.api_key = record
        request.state.user = owner
        return record

    raise UnauthorizedError("Invalid or revoked API key.")


async def require_api_key(
    request: Request,
    authorization: Optional[str] = Header(None),
    xi_api_key: Optional[str] = Header(None, alias="xi-api-key"),
) -> str:
    """FastAPI dependency accepting either Bearer or xi-api-key."""
    record = await get_authenticated_key(request, authorization, xi_api_key)
    return record.role


async def require_admin_key(
    request: Request,
    authorization: Optional[str] = Header(None),
    xi_api_key: Optional[str] = Header(None, alias="xi-api-key"),
) -> ApiKeyRecord:
    """FastAPI dependency requiring role == 'admin'."""
    record = await get_authenticated_key(request, authorization, xi_api_key)
    if record.role != "admin":
        raise ForbiddenError("Admin role required for this endpoint.")
    return record


def require_scope(required_scope: str):
    """FastAPI dependency factory enforcing a specific scope for caller keys."""
    async def _scope_checker(
        request: Request,
        authorization: Optional[str] = Header(None),
        xi_api_key: Optional[str] = Header(None, alias="xi-api-key"),
    ) -> ApiKeyRecord:
        record = await get_authenticated_key(request, authorization, xi_api_key)
        if record.role == "admin":
            return record
        if required_scope not in record.scopes:
            raise ForbiddenError(f"Missing required scope: '{required_scope}'")
        return record
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

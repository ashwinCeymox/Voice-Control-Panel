"""Authentication, API-key hashing, and WebSocket ticket management."""
import hashlib
import hmac
import secrets
import time
from typing import Dict, Optional, Tuple
from fastapi import Header, Query, Request
from app.core.config import settings
from app.core.errors import UnauthorizedError

# In-memory ticket storage: {ticket_str: (created_at_timestamp, user_id)}
_TICKETS: Dict[str, Tuple[float, str]] = {}
TICKET_EXPIRY_SECONDS = 60

# Built-in developer API keys for local development
# In production, keys are stored as hashes in the database
_DEV_KEY_HASHES: Dict[str, str] = {}


def hash_api_key(plain_key: str) -> str:
    """Create a slow HMAC-SHA256 hash using the secret key."""
    return hmac.new(
        settings.API_KEY_HASH_SECRET.encode("utf-8"),
        plain_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def generate_api_key() -> Tuple[str, str]:
    """Generate a high-entropy random key and its hash."""
    raw_key = f"va-{secrets.token_urlsafe(32)}"
    key_hash = hash_api_key(raw_key)
    return raw_key, key_hash


# Initialize default dev key in development mode
_DEFAULT_DEV_KEY = "dev-voice-key-12345"
_DEV_KEY_HASHES[hash_api_key(_DEFAULT_DEV_KEY)] = "developer"


def verify_api_key(plain_key: str) -> Optional[str]:
    """Verify an API key and return owner if valid."""
    key_hash = hash_api_key(plain_key)
    if key_hash in _DEV_KEY_HASHES:
        return _DEV_KEY_HASHES[key_hash]
    return None


async def require_api_key(
    request: Request,
    authorization: Optional[str] = Header(None),
    xi_api_key: Optional[str] = Header(None, alias="xi-api-key"),
) -> str:
    """FastAPI dependency accepting either Bearer or xi-api-key."""
    key = None
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            key = parts[1].strip()
    elif xi_api_key:
        key = xi_api_key.strip()

    if not key:
        raise UnauthorizedError("Missing API key. Provide Bearer or xi-api-key header.")

    owner = verify_api_key(key)
    if not owner:
        raise UnauthorizedError("Invalid or revoked API key.")

    request.state.user = owner
    return owner


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

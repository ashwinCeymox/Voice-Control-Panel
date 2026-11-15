"""
API Key management, authentication, role and scope authorization, and usage logging.
"""
import hmac
import time
import uuid
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from app.core.config import settings
from app.core.errors import AppError, UnauthorizedError, ForbiddenError
from app.core.security import hash_api_key, generate_api_key, ApiKeyRecord
from app.storage.database import get_db


class KeyManager:
    @staticmethod
    async def create_key(
        name: str,
        role: str = "caller",
        scopes: Optional[List[str]] = None,
        rate_limit: int = 60,
        max_concurrent: int = 5,
        max_daily_tokens: Optional[int] = None,
        max_daily_audio_seconds: Optional[float] = None,
        expires_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Generate high-entropy key, store only slow hash in DB, return record with plain key once."""
        if scopes is None:
            scopes = ["llm", "stt", "tts"] if role == "admin" else ["llm"]

        key_id = f"key-{uuid.uuid4().hex[:12]}"
        plain_key, key_hash = generate_api_key()
        now = time.time()
        scopes_str = ",".join(scopes)

        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO api_keys (id, name, hash, role, scopes, rate_limit, max_concurrent, max_daily_tokens, max_daily_audio_seconds, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (key_id, name, key_hash, role, scopes_str, rate_limit, max_concurrent, max_daily_tokens, max_daily_audio_seconds, expires_at, now),
            )
            await db.commit()

        return {
            "id": key_id,
            "name": name,
            "key": plain_key,
            "role": role,
            "scopes": scopes,
            "rate_limit": rate_limit,
            "max_concurrent": max_concurrent,
            "max_daily_tokens": max_daily_tokens,
            "max_daily_audio_seconds": max_daily_audio_seconds,
            "expires_at": expires_at,
            "revoked_at": None,
            "created_at": now,
        }

    @staticmethod
    async def list_keys() -> List[Dict[str, Any]]:
        """List active and revoked keys. Never return key hashes or plain keys."""
        async with get_db() as db:
            async with db.execute(
                "SELECT id, name, role, scopes, rate_limit, max_concurrent, max_daily_tokens, max_daily_audio_seconds, expires_at, revoked_at, created_at FROM api_keys ORDER BY created_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()

        results = []
        for r in rows:
            results.append({
                "id": r[0],
                "name": r[1],
                "role": r[2],
                "scopes": [s.strip() for s in r[3].split(",") if s.strip()],
                "rate_limit": r[4],
                "max_concurrent": r[5],
                "max_daily_tokens": r[6],
                "max_daily_audio_seconds": r[7],
                "expires_at": r[8],
                "revoked_at": r[9],
                "created_at": r[10],
            })
        return results

    @staticmethod
    async def revoke_key(key_id: str) -> bool:
        """Immediately revoke an API key."""
        now = time.time()
        async with get_db() as db:
            cursor = await db.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (now, key_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def authenticate(plain_key: str) -> Optional[ApiKeyRecord]:
        """Authenticate plain key against DB and static admin fallback. Returns ApiKeyRecord or None."""
        if not plain_key:
            return None

        # Check configured admin key if set
        if settings.ADMIN_API_KEY and hmac.compare_digest(plain_key, settings.ADMIN_API_KEY):
            return ApiKeyRecord(
                id="key-admin-env",
                name="Environment Admin Key",
                role="admin",
                scopes=["llm", "stt", "tts", "admin"],
                rate_limit=1000,
                max_concurrent=50,
                created_at=0.0,
            )

        # Lookup in DB by hash
        k_hash = hash_api_key(plain_key)
        now = time.time()
        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, name, role, scopes, rate_limit, max_concurrent, max_daily_tokens, max_daily_audio_seconds, expires_at, revoked_at, created_at
                FROM api_keys WHERE hash = ?
                """,
                (k_hash,),
            ) as cursor:
                row = await cursor.fetchone()

        if not row:
            return None

        key_id, name, role, scopes_str, rate_limit, max_concurrent, max_daily_tokens, max_daily_audio_seconds, expires_at, revoked_at, created_at = row

        # Check revocation
        if revoked_at is not None:
            return None

        # Check expiration
        if expires_at is not None and now > expires_at:
            return None

        scopes = [s.strip() for s in scopes_str.split(",") if s.strip()]
        return ApiKeyRecord(
            id=key_id,
            name=name,
            role=role,
            scopes=scopes,
            rate_limit=rate_limit,
            max_concurrent=max_concurrent,
            max_daily_tokens=max_daily_tokens,
            max_daily_audio_seconds=max_daily_audio_seconds,
            expires_at=expires_at,
            revoked_at=revoked_at,
            created_at=created_at,
        )

    @staticmethod
    async def check_quota(key: ApiKeyRecord, cost_type: str = "tokens") -> None:
        """Enforce daily key quota limits (max_daily_tokens, max_daily_audio_seconds). Admin is exempt."""
        if key.role == "admin":
            return

        now = time.time()
        window_start = now - 86400.0

        if cost_type == "tokens" and key.max_daily_tokens is not None:
            async with get_db() as db:
                async with db.execute(
                    """
                    SELECT COALESCE(SUM(tokens_or_seconds), 0)
                    FROM usage
                    WHERE key_id = ? AND timestamp >= ? AND endpoint LIKE '%chat%'
                    """,
                    (key.id, window_start),
                ) as cursor:
                    row = await cursor.fetchone()
            used_tokens = float(row[0]) if row else 0.0
            if used_tokens >= key.max_daily_tokens:
                raise AppError(
                    status_code=429,
                    code="QUOTA_EXCEEDED",
                    message=f"Daily token quota exceeded ({used_tokens:.0f}/{key.max_daily_tokens})",
                )

        elif cost_type == "audio" and key.max_daily_audio_seconds is not None:
            async with get_db() as db:
                async with db.execute(
                    """
                    SELECT COALESCE(SUM(tokens_or_seconds), 0)
                    FROM usage
                    WHERE key_id = ? AND timestamp >= ? AND (
                        endpoint LIKE '%audio%' OR endpoint LIKE '%text-to-speech%' OR endpoint LIKE '%voice%'
                    )
                    """,
                    (key.id, window_start),
                ) as cursor:
                    row = await cursor.fetchone()
            used_audio = float(row[0]) if row else 0.0
            if used_audio >= key.max_daily_audio_seconds:
                raise AppError(
                    status_code=429,
                    code="QUOTA_EXCEEDED",
                    message=f"Daily audio quota exceeded ({used_audio:.1f}s/{key.max_daily_audio_seconds:.1f}s)",
                )

    @staticmethod
    async def get_key_quota(key_id: str) -> Dict[str, Any]:
        """Get configured quotas and current 24h usage totals for a key."""
        async with get_db() as db:
            async with db.execute(
                "SELECT max_daily_tokens, max_daily_audio_seconds FROM api_keys WHERE id = ?",
                (key_id,),
            ) as cursor:
                row = await cursor.fetchone()

        max_tokens = row[0] if row else None
        max_audio = row[1] if row else None

        now = time.time()
        window_start = now - 86400.0
        async with get_db() as db:
            async with db.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN endpoint LIKE '%chat%' THEN tokens_or_seconds ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN endpoint LIKE '%audio%' OR endpoint LIKE '%text-to-speech%' OR endpoint LIKE '%voice%' THEN tokens_or_seconds ELSE 0 END), 0)
                FROM usage
                WHERE key_id = ? AND timestamp >= ?
                """,
                (key_id, window_start),
            ) as cursor:
                usage_row = await cursor.fetchone()

        used_tokens = float(usage_row[0]) if usage_row else 0.0
        used_audio = float(usage_row[1]) if usage_row else 0.0

        return {
            "key_id": key_id,
            "max_daily_tokens": max_tokens,
            "used_daily_tokens": used_tokens,
            "max_daily_audio_seconds": max_audio,
            "used_daily_audio_seconds": used_audio,
        }

    @staticmethod
    async def log_usage(
        key_id: str,
        endpoint: str,
        duration_ms: float,
        tokens_or_seconds: float,
        status: int,
    ) -> None:
        """Record usage activity in database without logging request text, prompt, or audio content."""
        now = time.time()
        try:
            async with get_db() as db:
                await db.execute(
                    """
                    INSERT INTO usage (key_id, endpoint, timestamp, duration_ms, tokens_or_seconds, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (key_id, endpoint, now, duration_ms, tokens_or_seconds, status),
                )
                await db.commit()
        except Exception:
            # Usage logging must never break request execution
            pass

    @staticmethod
    async def get_usage_summary() -> List[Dict[str, Any]]:
        """Retrieve usage records for admin inspection."""
        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, key_id, endpoint, timestamp, duration_ms, tokens_or_seconds, status
                FROM usage
                ORDER BY timestamp DESC
                LIMIT 500
                """
            ) as cursor:
                rows = await cursor.fetchall()

        return [
            {
                "id": r[0],
                "key_id": r[1],
                "endpoint": r[2],
                "timestamp": r[3],
                "duration_ms": r[4],
                "tokens_or_seconds": r[5],
                "status": r[6],
            }
            for r in rows
        ]

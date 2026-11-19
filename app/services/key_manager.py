"""
API Key management, authentication, profiles, role and scope authorization, and usage logging.
"""

import hmac
import ipaddress
import json
import time
import uuid
from typing import Any, Dict, List, Optional
from app.core.config import settings
from app.core.errors import AppError
from app.core.security import (
    hash_api_key,
    generate_api_key,
    ApiKeyRecord,
    check_model_allowed,
)
from app.storage.database import get_db


def _is_ip_allowed(client_ip: Optional[str], ip_allowlist: Optional[List[str]]) -> bool:
    """Check if client IP satisfies CIDR/IP allowlist."""
    if not ip_allowlist or len(ip_allowlist) == 0:
        return True
    if not client_ip:
        return True
    if client_ip in ("testclient", "localhost"):
        client_ip = "127.0.0.1"
    try:
        ip = ipaddress.ip_address(client_ip)
        for rule in ip_allowlist:
            rule = rule.strip()
            if not rule:
                continue
            if "/" in rule:
                if ip in ipaddress.ip_network(rule, strict=False):
                    return True
            else:
                if ip == ipaddress.ip_address(rule):
                    return True
        return False
    except ValueError:
        return True


class KeyManager:
    # ---------------------------------------------------------
    # API Key Management
    # ---------------------------------------------------------
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
        profile_id: Optional[str] = None,
        model_allowlist: Optional[List[str]] = None,
        ip_allowlist: Optional[List[str]] = None,
        status: str = "active",
        rotated_from: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate high-entropy key, store slow hash in DB, return record with plain key once."""
        if scopes is None:
            scopes = (
                ["llm", "stt", "tts", "stt_stream", "voice", "voice_stream", "admin"]
                if role == "admin"
                else ["llm", "stt", "tts", "stt_stream", "voice", "voice_stream"]
            )

        key_id = f"key-{uuid.uuid4().hex[:12]}"
        plain_key, prefix, key_hash = generate_api_key()
        now = time.time()
        scopes_str = ",".join(scopes)
        models_str = ",".join(model_allowlist) if model_allowlist else None
        ips_str = ",".join(ip_allowlist) if ip_allowlist else None

        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO api_keys (
                    id, name, hash, role, scopes, rate_limit, max_concurrent,
                    max_daily_tokens, max_daily_audio_seconds, expires_at, created_at,
                    profile_id, prefix, status, model_allowlist, ip_allowlist, rotated_from
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key_id,
                    name,
                    key_hash,
                    role,
                    scopes_str,
                    rate_limit,
                    max_concurrent,
                    max_daily_tokens,
                    max_daily_audio_seconds,
                    expires_at,
                    now,
                    profile_id,
                    prefix,
                    status,
                    models_str,
                    ips_str,
                    rotated_from,
                ),
            )
            await db.commit()

        return {
            "id": key_id,
            "name": name,
            "key": plain_key,
            "prefix": prefix,
            "role": role,
            "scopes": scopes,
            "rate_limit": rate_limit,
            "max_concurrent": max_concurrent,
            "max_daily_tokens": max_daily_tokens,
            "max_daily_audio_seconds": max_daily_audio_seconds,
            "profile_id": profile_id,
            "model_allowlist": model_allowlist or [],
            "ip_allowlist": ip_allowlist or [],
            "status": status,
            "expires_at": expires_at,
            "revoked_at": None,
            "rotated_from": rotated_from,
            "rotation_expires_at": None,
            "last_used_at": None,
            "created_at": now,
        }

    @staticmethod
    async def list_keys(profile_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List keys. Never return key hashes or plain secret keys."""
        query = """
            SELECT id, name, role, scopes, rate_limit, max_concurrent, max_daily_tokens,
                   max_daily_audio_seconds, expires_at, revoked_at, created_at,
                   profile_id, prefix, status, model_allowlist, ip_allowlist,
                   rotated_from, rotation_expires_at, last_used_at
            FROM api_keys
        """
        params = []
        if profile_id:
            query += " WHERE profile_id = ?"
            params.append(profile_id)
        query += " ORDER BY created_at DESC"

        async with get_db() as db:
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()

        results = []
        for r in rows:
            scopes = [s.strip() for s in (r[3] or "").split(",") if s.strip()]
            models = (
                [m.strip() for m in (r[14] or "").split(",") if m.strip()]
                if r[14]
                else []
            )
            ips = (
                [i.strip() for i in (r[15] or "").split(",") if i.strip()]
                if r[15]
                else []
            )
            results.append(
                {
                    "id": r[0],
                    "name": r[1],
                    "role": r[2],
                    "scopes": scopes,
                    "rate_limit": r[4],
                    "max_concurrent": r[5],
                    "max_daily_tokens": r[6],
                    "max_daily_audio_seconds": r[7],
                    "expires_at": r[8],
                    "revoked_at": r[9],
                    "created_at": r[10],
                    "profile_id": r[11],
                    "prefix": r[12],
                    "status": r[13] or "active",
                    "model_allowlist": models,
                    "ip_allowlist": ips,
                    "rotated_from": r[16],
                    "rotation_expires_at": r[17],
                    "last_used_at": r[18],
                }
            )
        return results

    @staticmethod
    async def get_key(key_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a single key record by ID (no secret or hash)."""
        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, name, role, scopes, rate_limit, max_concurrent, max_daily_tokens,
                       max_daily_audio_seconds, expires_at, revoked_at, created_at,
                       profile_id, prefix, status, model_allowlist, ip_allowlist,
                       rotated_from, rotation_expires_at, last_used_at
                FROM api_keys WHERE id = ?
                """,
                (key_id,),
            ) as cursor:
                r = await cursor.fetchone()

        if not r:
            return None

        scopes = [s.strip() for s in (r[3] or "").split(",") if s.strip()]
        models = (
            [m.strip() for m in (r[14] or "").split(",") if m.strip()] if r[14] else []
        )
        ips = (
            [i.strip() for i in (r[15] or "").split(",") if i.strip()] if r[15] else []
        )
        return {
            "id": r[0],
            "name": r[1],
            "role": r[2],
            "scopes": scopes,
            "rate_limit": r[4],
            "max_concurrent": r[5],
            "max_daily_tokens": r[6],
            "max_daily_audio_seconds": r[7],
            "expires_at": r[8],
            "revoked_at": r[9],
            "created_at": r[10],
            "profile_id": r[11],
            "prefix": r[12],
            "status": r[13] or "active",
            "model_allowlist": models,
            "ip_allowlist": ips,
            "rotated_from": r[16],
            "rotation_expires_at": r[17],
            "last_used_at": r[18],
        }

    @staticmethod
    async def patch_key(
        key_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update configurable fields of an API key (status, limits, allowlists, etc.)."""
        allowed_fields = {
            "name": str,
            "scopes": list,
            "rate_limit": int,
            "max_concurrent": int,
            "max_daily_tokens": (int, type(None)),
            "max_daily_audio_seconds": (float, int, type(None)),
            "status": str,
            "model_allowlist": list,
            "ip_allowlist": list,
            "expires_at": (float, int, type(None)),
        }

        set_clauses = []
        params = []
        for field, val in updates.items():
            if field in allowed_fields:
                if field == "scopes":
                    set_clauses.append("scopes = ?")
                    params.append(",".join(val) if val else "")
                elif field == "model_allowlist":
                    set_clauses.append("model_allowlist = ?")
                    params.append(",".join(val) if val else None)
                elif field == "ip_allowlist":
                    set_clauses.append("ip_allowlist = ?")
                    params.append(",".join(val) if val else None)
                else:
                    set_clauses.append(f"{field} = ?")
                    params.append(val)

        if not set_clauses:
            return await KeyManager.get_key(key_id)

        params.append(key_id)
        async with get_db() as db:
            await db.execute(
                f"UPDATE api_keys SET {', '.join(set_clauses)} WHERE id = ?",
                params,
            )
            await db.commit()

        return await KeyManager.get_key(key_id)

    @staticmethod
    async def rotate_key(key_id: str, grace_period_days: float = 1.0) -> Dict[str, Any]:
        """Rotate a key: generate a new active key and set rotation expiration on old key."""
        old_key = await KeyManager.get_key(key_id)
        if not old_key:
            raise AppError(
                status_code=404, code="KEY_NOT_FOUND", message=f"Key {key_id} not found"
            )

        now = time.time()
        rotation_expires_at = now + (grace_period_days * 86400.0)

        # Set grace period expiry on old key
        async with get_db() as db:
            await db.execute(
                "UPDATE api_keys SET rotation_expires_at = ? WHERE id = ?",
                (rotation_expires_at, key_id),
            )
            await db.commit()

        # Create new key inheriting properties
        new_key = await KeyManager.create_key(
            name=old_key["name"],
            role=old_key["role"],
            scopes=old_key["scopes"],
            rate_limit=old_key["rate_limit"],
            max_concurrent=old_key["max_concurrent"],
            max_daily_tokens=old_key["max_daily_tokens"],
            max_daily_audio_seconds=old_key["max_daily_audio_seconds"],
            profile_id=old_key["profile_id"],
            model_allowlist=old_key["model_allowlist"],
            ip_allowlist=old_key["ip_allowlist"],
            rotated_from=key_id,
        )
        return new_key

    @staticmethod
    async def revoke_key(key_id: str) -> bool:
        """Immediately revoke an API key."""
        now = time.time()
        async with get_db() as db:
            cursor = await db.execute(
                "UPDATE api_keys SET revoked_at = ?, status = 'revoked' WHERE id = ? AND revoked_at IS NULL",
                (now, key_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def authenticate(
        plain_key: str, client_ip: Optional[str] = None
    ) -> Optional[ApiKeyRecord]:
        """Authenticate plain key against DB and static admin fallback. Returns ApiKeyRecord or None."""
        if not plain_key:
            return None

        # Check configured admin key if set
        if settings.ADMIN_API_KEY and hmac.compare_digest(
            plain_key, settings.ADMIN_API_KEY
        ):
            if plain_key == "dev-voice-key-12345":
                is_cloud_env = settings.ENVIRONMENT.lower() in ("production", "staging")
                is_public_host = settings.GATEWAY_HOST not in ("127.0.0.1", "localhost")
                is_remote_client = bool(
                    client_ip
                    and client_ip not in ("127.0.0.1", "::1", "localhost", "testclient")
                )
                if is_cloud_env or is_public_host or is_remote_client:
                    return None
            return ApiKeyRecord(
                id="key-admin-env",
                name="Environment Admin Key",
                role="admin",
                scopes=["llm", "stt", "tts", "admin", "voice", "voice_stream", "stt_stream"],
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
                SELECT id, name, role, scopes, rate_limit, max_concurrent, max_daily_tokens,
                       max_daily_audio_seconds, expires_at, revoked_at, created_at,
                       profile_id, prefix, status, model_allowlist, ip_allowlist,
                       rotated_from, rotation_expires_at, last_used_at
                FROM api_keys WHERE hash = ?
                """,
                (k_hash,),
            ) as cursor:
                row = await cursor.fetchone()

        if not row:
            return None

        (
            key_id,
            name,
            role,
            scopes_str,
            rate_limit,
            max_concurrent,
            max_daily_tokens,
            max_daily_audio_seconds,
            expires_at,
            revoked_at,
            created_at,
            profile_id,
            prefix,
            status,
            model_allowlist_str,
            ip_allowlist_str,
            rotated_from,
            rotation_expires_at,
            last_used_at,
        ) = row

        # 1. Check explicit revocation
        if revoked_at is not None:
            return None

        # 2. Check expiration timestamp
        if expires_at is not None and now > expires_at:
            return None

        # 3. Check status flag (must be 'active')
        if status is not None and status != "active":
            return None

        # 4. Check rotation grace period expiry
        if rotation_expires_at is not None and now > rotation_expires_at:
            return None

        # 5. Check parent profile disabled state
        if profile_id:
            async with get_db() as db:
                async with db.execute(
                    "SELECT disabled_at FROM profiles WHERE id = ?",
                    (profile_id,),
                ) as cursor:
                    prof_row = await cursor.fetchone()
            if prof_row and prof_row[0] is not None:
                return None

        # 6. Check IP allowlist
        ip_allowlist = (
            [i.strip() for i in (ip_allowlist_str or "").split(",") if i.strip()]
            if ip_allowlist_str
            else None
        )
        if not _is_ip_allowed(client_ip, ip_allowlist):
            return None

        # Update last_used_at
        try:
            async with get_db() as db:
                await db.execute(
                    "UPDATE api_keys SET last_used_at = ? WHERE id = ?", (now, key_id)
                )
                await db.commit()
        except Exception:
            pass

        scopes = [s.strip() for s in scopes_str.split(",") if s.strip()]
        model_allowlist = (
            [m.strip() for m in (model_allowlist_str or "").split(",") if m.strip()]
            if model_allowlist_str
            else None
        )

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
            profile_id=profile_id,
            prefix=prefix,
            status=status or "active",
            model_allowlist=model_allowlist,
            ip_allowlist=ip_allowlist,
            rotated_from=rotated_from,
            rotation_expires_at=rotation_expires_at,
            last_used_at=now,
        )

    @staticmethod
    def check_model_allowed(key: ApiKeyRecord, model_id: Optional[str]) -> None:
        """Enforce model_allowlist on API key."""
        check_model_allowed(key, model_id)

    # ---------------------------------------------------------
    # Profile Management
    # ---------------------------------------------------------
    @staticmethod
    async def create_profile(
        name: str,
        description: Optional[str] = None,
        contact: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new consuming project profile."""
        profile_id = f"prof-{uuid.uuid4().hex[:12]}"
        now = time.time()
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO profiles (id, name, description, contact, notes, disabled_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (profile_id, name, description, contact, notes, now, now),
            )
            await db.commit()

        return {
            "id": profile_id,
            "name": name,
            "description": description,
            "contact": contact,
            "notes": notes,
            "disabled_at": None,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    async def get_profile(
        profile_id: str, include_keys: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Get profile by ID, optionally including associated keys."""
        async with get_db() as db:
            async with db.execute(
                "SELECT id, name, description, contact, notes, disabled_at, created_at, updated_at FROM profiles WHERE id = ?",
                (profile_id,),
            ) as cursor:
                row = await cursor.fetchone()

        if not row:
            return None

        profile_data = {
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "contact": row[3],
            "notes": row[4],
            "disabled_at": row[5],
            "created_at": row[6],
            "updated_at": row[7],
        }

        if include_keys:
            profile_data["keys"] = await KeyManager.list_keys(profile_id=profile_id)

        return profile_data

    @staticmethod
    async def list_profiles() -> List[Dict[str, Any]]:
        """List all profiles ordered by created_at DESC."""
        async with get_db() as db:
            async with db.execute(
                "SELECT id, name, description, contact, notes, disabled_at, created_at, updated_at FROM profiles ORDER BY created_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()

        results = []
        for r in rows:
            results.append(
                {
                    "id": r[0],
                    "name": r[1],
                    "description": r[2],
                    "contact": r[3],
                    "notes": r[4],
                    "disabled_at": r[5],
                    "created_at": r[6],
                    "updated_at": r[7],
                }
            )
        return results

    @staticmethod
    async def update_profile(
        profile_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        contact: Optional[str] = None,
        notes: Optional[str] = None,
        disabled: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update profile metadata or toggle disabled state."""
        existing = await KeyManager.get_profile(profile_id, include_keys=False)
        if not existing:
            return None

        now = time.time()
        new_name = name if name is not None else existing["name"]
        new_desc = description if description is not None else existing["description"]
        new_contact = contact if contact is not None else existing["contact"]
        new_notes = notes if notes is not None else existing["notes"]

        new_disabled_at = existing["disabled_at"]
        if disabled is True:
            new_disabled_at = (
                now if existing["disabled_at"] is None else existing["disabled_at"]
            )
        elif disabled is False:
            new_disabled_at = None

        async with get_db() as db:
            await db.execute(
                """
                UPDATE profiles
                SET name = ?, description = ?, contact = ?, notes = ?, disabled_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    new_name,
                    new_desc,
                    new_contact,
                    new_notes,
                    new_disabled_at,
                    now,
                    profile_id,
                ),
            )
            await db.commit()

        return await KeyManager.get_profile(profile_id, include_keys=False)

    @staticmethod
    async def delete_profile(profile_id: str) -> bool:
        """Delete profile and dissociate or delete its keys."""
        async with get_db() as db:
            cursor = await db.execute(
                "DELETE FROM profiles WHERE id = ?", (profile_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    # ---------------------------------------------------------
    # Dashboard Settings
    # ---------------------------------------------------------
    @staticmethod
    async def get_dashboard_settings() -> Dict[str, Any]:
        """Fetch dashboard configuration settings."""
        async with get_db() as db:
            async with db.execute(
                "SELECT id, base_url, default_rate_limit, default_max_concurrent, usage_retention_days, updated_at FROM dashboard_settings WHERE id = 'default'"
            ) as cursor:
                row = await cursor.fetchone()

        resolved_base = settings.get_public_base_url()
        if not row:
            return {
                "base_url": resolved_base,
                "default_rate_limit": 60,
                "default_max_concurrent": 5,
                "usage_retention_days": 30,
                "updated_at": time.time(),
            }

        db_base = row[1]
        # If DB still has local loopback default, adapt dynamically to public configured base
        if db_base in ("http://127.0.0.1:8000", "http://localhost:8000") and (
            settings.PUBLIC_BASE_URL or settings.GATEWAY_HOST not in ("127.0.0.1", "localhost")
        ):
            db_base = resolved_base

        return {
            "base_url": db_base,
            "default_rate_limit": row[2],
            "default_max_concurrent": row[3],
            "usage_retention_days": row[4],
            "updated_at": row[5],
        }

    @staticmethod
    async def update_dashboard_settings(
        base_url: Optional[str] = None,
        default_rate_limit: Optional[int] = None,
        default_max_concurrent: Optional[int] = None,
        usage_retention_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Update dashboard configuration settings."""
        current = await KeyManager.get_dashboard_settings()
        now = time.time()
        new_base_url = base_url if base_url is not None else current["base_url"]
        new_rl = (
            default_rate_limit
            if default_rate_limit is not None
            else current["default_rate_limit"]
        )
        new_mc = (
            default_max_concurrent
            if default_max_concurrent is not None
            else current["default_max_concurrent"]
        )
        new_ret = (
            usage_retention_days
            if usage_retention_days is not None
            else current["usage_retention_days"]
        )

        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO dashboard_settings (id, base_url, default_rate_limit, default_max_concurrent, usage_retention_days, updated_at)
                VALUES ('default', ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    base_url = excluded.base_url,
                    default_rate_limit = excluded.default_rate_limit,
                    default_max_concurrent = excluded.default_max_concurrent,
                    usage_retention_days = excluded.usage_retention_days,
                    updated_at = excluded.updated_at
                """,
                (new_base_url, new_rl, new_mc, new_ret, now),
            )
            await db.commit()

        return await KeyManager.get_dashboard_settings()

    # ---------------------------------------------------------
    # Admin Audit Log
    # ---------------------------------------------------------
    @staticmethod
    async def record_audit(
        admin_id: str,
        action: str,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Write audit entry (never logs secrets or content)."""
        now = time.time()
        details_str = json.dumps(details) if details else None
        try:
            async with get_db() as db:
                await db.execute(
                    """
                    INSERT INTO admin_audit (timestamp, admin_id, action, target_type, target_id, details)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (now, admin_id, action, target_type, target_id, details_str),
                )
                await db.commit()
        except Exception:
            pass

    @staticmethod
    async def get_audit_log(limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieve recent admin audit entries."""
        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, timestamp, admin_id, action, target_type, target_id, details
                FROM admin_audit
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()

        results = []
        for r in rows:
            details_obj = None
            if r[6]:
                try:
                    details_obj = json.loads(r[6])
                except Exception:
                    details_obj = r[6]
            results.append(
                {
                    "id": r[0],
                    "timestamp": r[1],
                    "admin_id": r[2],
                    "action": r[3],
                    "target_type": r[4],
                    "target_id": r[5],
                    "details": details_obj,
                }
            )
        return results

    # ---------------------------------------------------------
    # Quotas and Usage
    # ---------------------------------------------------------
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
        ttfb_ms: Optional[float] = None,
        model: Optional[str] = None,
        profile_id: Optional[str] = None,
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

        try:
            from app.services.usage_service import record_usage_event

            in_u = tokens_or_seconds if "chat" not in endpoint else 0.0
            out_u = tokens_or_seconds
            await record_usage_event(
                endpoint=endpoint,
                status=status,
                latency_ms=duration_ms,
                key_id=key_id,
                profile_id=profile_id,
                model=model,
                ttfb_ms=ttfb_ms,
                input_units=in_u,
                output_units=out_u,
                timestamp=now,
            )
        except Exception:
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

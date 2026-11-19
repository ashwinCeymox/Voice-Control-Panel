"""Super admin authentication service.

Handles:
- Single super-admin credential verification (argon2id)
- Seeding on startup
- Account lockout & IP throttling
- Session management (8h idle, 7d absolute) in SQLite
- Password change enforcement
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError, VerificationError

from app.core.config import settings
from app.core.errors import UnauthorizedError, BadRequestError
from app.storage.database import get_db

logger = logging.getLogger("voice_agent.admin_auth")

# Password hasher using argon2id
_ph = PasswordHasher(
    time_cost=2,
    memory_cost=65536,
    parallelism=1,
    hash_len=32,
    salt_len=16,
)

# Constant-time dummy hash to mitigate timing attacks on non-existent users
_DUMMY_HASH = _ph.hash("dummy-constant-time-passphrase-defense")

# In-memory IP throttle: {ip: [fail_timestamp1, fail_timestamp2, ...]}
_IP_FAILURES: Dict[str, List[float]] = {}
IP_LOCKOUT_WINDOW_SECONDS = 15 * 60  # 15 minutes
MAX_FAILED_ATTEMPTS = 5
IDLE_SESSION_TIMEOUT_HOURS = 8
ABSOLUTE_SESSION_TIMEOUT_DAYS = 7


class AdminAuthService:
    @classmethod
    def _is_ip_throttled(cls, client_ip: str, now_ts: float) -> bool:
        failures = _IP_FAILURES.get(client_ip, [])
        # Retain failures within the 15-minute window
        valid_failures = [
            t for t in failures if (now_ts - t) < IP_LOCKOUT_WINDOW_SECONDS
        ]
        _IP_FAILURES[client_ip] = valid_failures
        return len(valid_failures) >= MAX_FAILED_ATTEMPTS

    @classmethod
    def _record_ip_failure(cls, client_ip: str, now_ts: float) -> None:
        failures = _IP_FAILURES.get(client_ip, [])
        valid_failures = [
            t for t in failures if (now_ts - t) < IP_LOCKOUT_WINDOW_SECONDS
        ]
        valid_failures.append(now_ts)
        _IP_FAILURES[client_ip] = valid_failures

    @classmethod
    def _reset_ip_failures(cls, client_ip: str) -> None:
        _IP_FAILURES.pop(client_ip, None)

    @classmethod
    async def seed_admin_user(cls) -> Optional[str]:
        """Seed super admin on startup if admin_user table is empty.
        Stores argon2id password hash exclusively in SQLite database (never in .env).
        Returns generated password if fresh seed occurred, else None.
        """
        env = settings.ENVIRONMENT.lower()
        if env not in ("development", "staging", "production"):
            raise RuntimeError(
                f"Invalid ENVIRONMENT '{settings.ENVIRONMENT}'. Allowed values: development, staging, production."
            )

        async with get_db() as db:
            async with db.execute(
                "SELECT id, email FROM admin_user WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    logger.debug("Admin user already initialized (%s)", row["email"])
                    return None

        # Determine password: if override provided in test env, use it. Otherwise generate random.
        init_pass = settings.ADMIN_INITIAL_PASSWORD or ""
        generated = False

        if env in ("staging", "production"):
            if init_pass == "adminVOICE3.md" or (init_pass and len(init_pass) < 12):
                raise RuntimeError(
                    f"CRITICAL SECURITY CONFIGURATION ERROR: Running in {env} with missing, default, or weak ADMIN_INITIAL_PASSWORD! "
                    "ADMIN_INITIAL_PASSWORD must be set to a secure, random string of at least 12 characters."
                )

        if not init_pass:
            init_pass = secrets.token_urlsafe(16)
            generated = True
        elif env == "development" and init_pass == "adminVOICE3.md":
            # Retain developer default in dev environment
            pass

        pass_hash = _ph.hash(init_pass)
        now_iso = datetime.now(timezone.utc).isoformat()

        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO admin_user (
                    id, email, password_hash, must_change_password, failed_attempts, locked_until, updated_at
                ) VALUES (1, ?, ?, 1, 0, NULL, ?)
                """,
                (settings.ADMIN_EMAIL, pass_hash, now_iso),
            )
            await db.commit()
        logger.info(
            "Admin user seeded successfully with email: %s in SQLite database", settings.ADMIN_EMAIL
        )
        if generated:
            logger.warning(
                "Initial admin password generated and stored exclusively in SQLite database (never in .env)."
            )
        return init_pass if generated else None

    @classmethod
    async def get_admin_user(cls) -> Optional[dict]:
        """Fetch the single admin user row."""
        async with get_db() as db:
            async with db.execute(
                "SELECT id, email, password_hash, must_change_password, failed_attempts, locked_until, updated_at FROM admin_user WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return dict(row)

    @classmethod
    async def login(
        cls, email: str, password: str, client_ip: str, user_agent: Optional[str] = None
    ) -> Tuple[str, bool, str]:
        """Verify login credentials, rate limit by account & IP, and issue session."""
        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()
        generic_error = "Invalid email or password."

        admin = await cls.get_admin_user()
        if not admin:
            # Constant-time dummy check
            try:
                _ph.verify(_DUMMY_HASH, password)
            except Exception:
                pass
            cls._record_ip_failure(client_ip, now_ts)
            raise UnauthorizedError(generic_error)

        # Check account lockout
        locked_until_str = admin.get("locked_until")
        if locked_until_str:
            try:
                locked_until = datetime.fromisoformat(locked_until_str)
                if now < locked_until:
                    # Still locked
                    logger.warning(
                        "Login attempt blocked: admin account locked until %s",
                        locked_until_str,
                    )
                    cls._record_ip_failure(client_ip, now_ts)
                    raise UnauthorizedError(generic_error)
            except (ValueError, TypeError):
                pass
        elif admin.get("failed_attempts", 0) == 0:
            # Reset IP failure counter if account is cleanly reset
            cls._reset_ip_failures(client_ip)

        # Check IP throttling
        if cls._is_ip_throttled(client_ip, now_ts):
            logger.warning("Login attempt blocked: IP %s is throttled", client_ip)
            raise UnauthorizedError(generic_error)

        # Verify email match (case-insensitive)
        email_match = admin["email"].strip().lower() == email.strip().lower()
        target_hash = admin["password_hash"] if email_match else _DUMMY_HASH

        password_valid = False
        try:
            password_valid = _ph.verify(target_hash, password) and email_match
        except (VerifyMismatchError, InvalidHashError, VerificationError):
            password_valid = False

        if not password_valid:
            # Record failure
            cls._record_ip_failure(client_ip, now_ts)
            new_failed = admin.get("failed_attempts", 0) + 1
            new_lockout = None
            if new_failed >= MAX_FAILED_ATTEMPTS:
                new_lockout = (now + timedelta(minutes=15)).isoformat()
                logger.warning(
                    "Admin account locked out after %d failed attempts", new_failed
                )

            async with get_db() as db:
                await db.execute(
                    "UPDATE admin_user SET failed_attempts = ?, locked_until = ?, updated_at = ? WHERE id = 1",
                    (new_failed, new_lockout, now.isoformat()),
                )
                await db.commit()
            raise UnauthorizedError(generic_error)

        # Successful login: reset failed counters and IP throttle
        cls._reset_ip_failures(client_ip)
        async with get_db() as db:
            await db.execute(
                "UPDATE admin_user SET failed_attempts = 0, locked_until = NULL, updated_at = ? WHERE id = 1",
                (now.isoformat(),),
            )
            # Create session
            session_id = secrets.token_urlsafe(32)
            created_at = now.isoformat()
            expires_at = (
                now + timedelta(days=ABSOLUTE_SESSION_TIMEOUT_DAYS)
            ).isoformat()
            last_seen_at = now.isoformat()
            await db.execute(
                """
                INSERT INTO admin_session (id, created_at, expires_at, last_seen_at, user_agent)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, created_at, expires_at, last_seen_at, user_agent or ""),
            )
            await db.commit()

        must_change = bool(admin.get("must_change_password", 0))
        return session_id, must_change, admin["email"]

    @classmethod
    async def validate_session(cls, session_id: str) -> Optional[dict]:
        """Validate session token, enforcing 8h idle and 7d absolute expiry."""
        if not session_id or len(session_id) < 16:
            return None

        now = datetime.now(timezone.utc)
        async with get_db() as db:
            async with db.execute(
                "SELECT id, created_at, expires_at, last_seen_at, user_agent FROM admin_session WHERE id = ?",
                (session_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None

                try:
                    expires_at = datetime.fromisoformat(row["expires_at"])
                    last_seen_at = datetime.fromisoformat(row["last_seen_at"])
                except Exception:
                    await db.execute(
                        "DELETE FROM admin_session WHERE id = ?", (session_id,)
                    )
                    await db.commit()
                    return None

                # 7 days absolute expiry
                if now > expires_at:
                    await db.execute(
                        "DELETE FROM admin_session WHERE id = ?", (session_id,)
                    )
                    await db.commit()
                    return None

                # 8 hours idle timeout
                if now > (last_seen_at + timedelta(hours=IDLE_SESSION_TIMEOUT_HOURS)):
                    await db.execute(
                        "DELETE FROM admin_session WHERE id = ?", (session_id,)
                    )
                    await db.commit()
                    return None

                # Session is valid: update last_seen_at
                now_iso = now.isoformat()
                await db.execute(
                    "UPDATE admin_session SET last_seen_at = ? WHERE id = ?",
                    (now_iso, session_id),
                )
                await db.commit()

            # Retrieve admin info
            admin = await cls.get_admin_user()
            if not admin:
                return None

            return {
                "session_id": session_id,
                "email": admin["email"],
                "must_change_password": bool(admin.get("must_change_password", 0)),
            }

    @classmethod
    async def logout(cls, session_id: str) -> None:
        """Invalidate single session."""
        if not session_id:
            return
        async with get_db() as db:
            await db.execute("DELETE FROM admin_session WHERE id = ?", (session_id,))
            await db.commit()

    @classmethod
    async def change_password(
        cls, old_password: str, new_password: str, user_agent: Optional[str] = None
    ) -> str:
        """Verify old password, enforce new password rules, invalidate other sessions, and return new session."""
        admin = await cls.get_admin_user()
        if not admin:
            raise UnauthorizedError("Admin user not found.")

        # Verify old password
        try:
            if not _ph.verify(admin["password_hash"], old_password):
                raise BadRequestError("Incorrect old password.")
        except (VerifyMismatchError, InvalidHashError, VerificationError):
            raise BadRequestError("Incorrect old password.")

        # Validate new password rules
        if len(new_password) < 12:
            raise BadRequestError("New password must be at least 12 characters long.")
        if len(new_password) > 128:
            raise BadRequestError("New password cannot exceed 128 characters.")
        if new_password == old_password:
            raise BadRequestError("New password must differ from the old password.")

        new_hash = _ph.hash(new_password)
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        async with get_db() as db:
            # Update password and clear must_change_password
            await db.execute(
                """
                UPDATE admin_user
                SET password_hash = ?, must_change_password = 0, failed_attempts = 0, locked_until = NULL, updated_at = ?
                WHERE id = 1
                """,
                (new_hash, now_iso),
            )
            # Invalidate all existing sessions
            await db.execute("DELETE FROM admin_session")

            # Issue fresh session
            new_session_id = secrets.token_urlsafe(32)
            created_at = now_iso
            expires_at = (
                now + timedelta(days=ABSOLUTE_SESSION_TIMEOUT_DAYS)
            ).isoformat()
            await db.execute(
                """
                INSERT INTO admin_session (id, created_at, expires_at, last_seen_at, user_agent)
                VALUES (?, ?, ?, ?, ?)
                """,
                (new_session_id, created_at, expires_at, now_iso, user_agent or ""),
            )
            await db.commit()

        logger.info(
            "Admin password changed successfully and all previous sessions invalidated."
        )
        return new_session_id

    @classmethod
    async def reset_admin_password(cls, new_password: Optional[str] = None) -> str:
        """Break-glass admin password reset:
        Generates or sets new password, enforces must_change_password=1, invalidates all sessions, clears lockouts.
        Returns the plaintext password.
        """
        if not new_password:
            new_password = secrets.token_urlsafe(16)
        new_hash = _ph.hash(new_password)
        now_iso = datetime.now(timezone.utc).isoformat()
        async with get_db() as db:
            cursor = await db.execute(
                """
                UPDATE admin_user
                SET password_hash = ?, must_change_password = 1, failed_attempts = 0, locked_until = NULL, updated_at = ?
                WHERE id = 1
                """,
                (new_hash, now_iso),
            )
            if cursor.rowcount == 0:
                await db.execute(
                    """
                    INSERT INTO admin_user (id, email, password_hash, must_change_password, failed_attempts, locked_until, updated_at)
                    VALUES (1, ?, ?, 1, 0, NULL, ?)
                    """,
                    (settings.ADMIN_EMAIL, new_hash, now_iso),
                )
            await db.execute("DELETE FROM admin_session")
            await db.commit()
        _IP_FAILURES.clear()
        logger.warning("Admin password was reset via break-glass command.")
        return new_password

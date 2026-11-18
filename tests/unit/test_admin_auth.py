"""
Unit tests for Super Admin Authentication, Session Management, and Proxy Awareness (Phase 3).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.core.config import settings
from app.core.security import resolve_client_ip
from app.services.admin_auth import AdminAuthService, _IP_FAILURES
from app.storage.database import init_db, get_db


@pytest.fixture(autouse=True)
def reset_auth_state():
    """Ensure database is initialized and clear sessions/lockouts before each test."""

    async def _clean():
        await init_db()
        _IP_FAILURES.clear()
        async with get_db() as db:
            await db.execute("DELETE FROM admin_session")
            await db.execute(
                """
                UPDATE admin_user
                SET failed_attempts = 0, locked_until = NULL
                WHERE id = 1
                """
            )
            await db.commit()

    asyncio.run(_clean())
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.asyncio
async def test_seed_admin_user_creates_exactly_one():
    """Seed must create exactly 1 admin user and fail to insert a second (CHECK id=1)."""
    await AdminAuthService.seed_admin_user()
    user = await AdminAuthService.get_admin_user()
    assert user is not None
    assert user["id"] == 1
    assert user["email"] == settings.ADMIN_EMAIL

    # Re-seeding is a no-op
    await AdminAuthService.seed_admin_user()

    # Attempting to insert a second admin fails sqlite constraint
    async with get_db() as db:
        with pytest.raises(Exception):
            await db.execute(
                "INSERT INTO admin_user (id, email, password_hash, updated_at) VALUES (2, 'other@test.com', 'hash', 'now')"
            )


def test_csrf_header_enforced_on_panel_post(client):
    """POST /v1/panel/* without X-Requested-With header must return 403 Forbidden."""
    resp = client.post(
        "/v1/panel/login",
        json={"email": settings.ADMIN_EMAIL, "password": "any"},
    )
    assert resp.status_code == 403
    assert "X-Requested-With" in resp.json().get("error", {}).get("message", "")


def test_login_success_and_failure(client):
    """Login succeeds with valid credentials and fails identically for wrong email or password."""
    # 1. Success
    resp = client.post(
        "/v1/panel/login",
        json={
            "email": settings.ADMIN_EMAIL,
            "password": settings.ADMIN_INITIAL_PASSWORD,
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == settings.ADMIN_EMAIL
    assert "must_change_password" in data
    assert "admin_session" in resp.cookies
    # Cookie flags check: HttpOnly and SameSite=strict
    raw_cookie = resp.headers.get("set-cookie", "")
    assert "HttpOnly" in raw_cookie
    assert (
        "SameSite=strict" in raw_cookie.lower()
        or "samesite=strict" in raw_cookie.lower()
    )

    # 2. Wrong password
    resp_bad_pw = client.post(
        "/v1/panel/login",
        json={"email": settings.ADMIN_EMAIL, "password": "WrongPassword123!"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp_bad_pw.status_code == 401
    err_bad_pw = resp_bad_pw.json().get("error", {}).get("message", "")

    # 3. Wrong email
    resp_bad_email = client.post(
        "/v1/panel/login",
        json={
            "email": "nonexistent@voicepanel.io",
            "password": settings.ADMIN_INITIAL_PASSWORD,
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp_bad_email.status_code == 401
    err_bad_email = resp_bad_email.json().get("error", {}).get("message", "")

    # Error message must be completely generic and identical
    assert err_bad_pw == err_bad_email == "Invalid email or password."


def test_account_lockout_after_5_failed_attempts(client):
    """5 consecutive failures must lock the account for 15 minutes."""
    for i in range(5):
        resp = client.post(
            "/v1/panel/login",
            json={"email": settings.ADMIN_EMAIL, "password": f"wrong-pass-{i}"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 401

    # 6th attempt with correct password must still return 401 due to lockout
    resp_locked = client.post(
        "/v1/panel/login",
        json={
            "email": settings.ADMIN_EMAIL,
            "password": settings.ADMIN_INITIAL_PASSWORD,
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp_locked.status_code == 401
    assert (
        resp_locked.json().get("error", {}).get("message")
        == "Invalid email or password."
    )


@pytest.mark.asyncio
async def test_account_lockout_expires(client):
    """Lockout must expire after 15 minutes."""
    # Set lockout in the past
    past_iso = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
    async with get_db() as db:
        await db.execute(
            "UPDATE admin_user SET failed_attempts = 5, locked_until = ? WHERE id = 1",
            (past_iso,),
        )
        await db.commit()

    resp = client.post(
        "/v1/panel/login",
        json={
            "email": settings.ADMIN_EMAIL,
            "password": settings.ADMIN_INITIAL_PASSWORD,
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    assert "admin_session" in resp.cookies


def test_change_password_validation_and_session_invalidation(client):
    """Change password validates complexity, enforces differences, and invalidates other sessions."""
    # 1. Login to get session
    login_resp = client.post(
        "/v1/panel/login",
        json={
            "email": settings.ADMIN_EMAIL,
            "password": settings.ADMIN_INITIAL_PASSWORD,
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert login_resp.status_code == 200
    old_cookie = login_resp.cookies.get("admin_session")

    # 2. Reject wrong old password
    resp = client.post(
        "/v1/panel/change-password",
        json={
            "old_password": "WrongOldPassword",
            "new_password": "ValidNewPassword123!",
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
        cookies={"admin_session": old_cookie},
    )
    assert resp.status_code in (400, 401)

    # 3. Reject short new password (< 12 chars)
    resp = client.post(
        "/v1/panel/change-password",
        json={"old_password": settings.ADMIN_INITIAL_PASSWORD, "new_password": "short"},
        headers={"X-Requested-With": "XMLHttpRequest"},
        cookies={"admin_session": old_cookie},
    )
    assert resp.status_code in (400, 422)

    # 4. Reject identical new password
    resp = client.post(
        "/v1/panel/change-password",
        json={
            "old_password": settings.ADMIN_INITIAL_PASSWORD,
            "new_password": settings.ADMIN_INITIAL_PASSWORD,
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
        cookies={"admin_session": old_cookie},
    )
    assert resp.status_code in (400, 422)

    # 5. Successfully change password
    new_pw = "SuperSecureNewPassword2026!"
    resp_success = client.post(
        "/v1/panel/change-password",
        json={"old_password": settings.ADMIN_INITIAL_PASSWORD, "new_password": new_pw},
        headers={"X-Requested-With": "XMLHttpRequest"},
        cookies={"admin_session": old_cookie},
    )
    assert resp_success.status_code == 200
    new_cookie = resp_success.cookies.get("admin_session")
    assert new_cookie is not None
    assert new_cookie != old_cookie

    # 6. Old session is no longer valid
    unauthed_client = TestClient(app)
    resp_old_session = unauthed_client.get(
        "/v1/panel/me", cookies={"admin_session": old_cookie}
    )
    assert resp_old_session.status_code == 401

    # 7. New session works
    resp_new_session = client.get("/v1/panel/me")
    assert resp_new_session.status_code == 200
    assert resp_new_session.json()["must_change_password"] is False

    # 8. Old password can no longer log in
    resp_old_login = client.post(
        "/v1/panel/login",
        json={
            "email": settings.ADMIN_EMAIL,
            "password": settings.ADMIN_INITIAL_PASSWORD,
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp_old_login.status_code == 401

    # 9. New password logs in successfully
    resp_new_login = client.post(
        "/v1/panel/login",
        json={"email": settings.ADMIN_EMAIL, "password": new_pw},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp_new_login.status_code == 200


def test_logout_clears_session(client):
    """Logout deletes session row and clears the cookie."""
    # Login
    login_resp = client.post(
        "/v1/panel/login",
        json={"email": settings.ADMIN_EMAIL, "password": "SuperSecureNewPassword2026!"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    if login_resp.status_code != 200:
        login_resp = client.post(
            "/v1/panel/login",
            json={
                "email": settings.ADMIN_EMAIL,
                "password": settings.ADMIN_INITIAL_PASSWORD,
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    session_cookie = login_resp.cookies.get("admin_session")

    # Logout
    logout_resp = client.post(
        "/v1/panel/logout",
        headers={"X-Requested-With": "XMLHttpRequest"},
        cookies={"admin_session": session_cookie},
    )
    assert logout_resp.status_code == 200

    # Session is dead
    me_resp = client.get("/v1/panel/me", cookies={"admin_session": session_cookie})
    assert me_resp.status_code == 401


@pytest.mark.asyncio
async def test_production_and_staging_startup_safety():
    """Staging and production refuse to start with default or weak ADMIN_INITIAL_PASSWORD."""
    with patch.object(settings, "ENVIRONMENT", "staging"):
        with patch.object(settings, "ADMIN_INITIAL_PASSWORD", "adminVOICE3.md"):
            # Empty out admin_user table to simulate fresh deployment
            async with get_db() as db:
                await db.execute("DELETE FROM admin_user")
                await db.commit()

            with pytest.raises(RuntimeError) as exc:
                await AdminAuthService.seed_admin_user()
            assert "ADMIN_INITIAL_PASSWORD" in str(exc.value)

    with patch.object(settings, "ENVIRONMENT", "production"):
        with patch.object(settings, "ADMIN_INITIAL_PASSWORD", "short"):
            with pytest.raises(RuntimeError) as exc:
                await AdminAuthService.seed_admin_user()
            assert "ADMIN_INITIAL_PASSWORD" in str(exc.value)

    with patch.object(settings, "ENVIRONMENT", "invalid_env"):
        with pytest.raises(RuntimeError) as exc:
            await AdminAuthService.seed_admin_user()
        assert "Invalid ENVIRONMENT" in str(exc.value)

    # Restore admin_user
    with patch.object(settings, "ENVIRONMENT", "development"):
        await AdminAuthService.seed_admin_user()


def test_unified_auth_principal_on_admin_routes(client):
    """Panel session cookie acts as admin role with all scopes on admin and inference endpoints."""
    # 1. Without session or API key -> 401
    resp_unauth = client.get("/v1/admin/keys")
    assert resp_unauth.status_code == 401

    # 2. Log in to get session cookie
    login_resp = client.post(
        "/v1/panel/login",
        json={
            "email": settings.ADMIN_EMAIL,
            "password": settings.ADMIN_INITIAL_PASSWORD,
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    if login_resp.status_code != 200:
        login_resp = client.post(
            "/v1/panel/login",
            json={
                "email": settings.ADMIN_EMAIL,
                "password": "SuperSecureNewPassword2026!",
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    cookie = login_resp.cookies.get("admin_session")

    # 3. Panel session reaches admin endpoints
    resp_keys = client.get("/v1/admin/keys", cookies={"admin_session": cookie})
    assert resp_keys.status_code == 200

    resp_models = client.get("/v1/models", cookies={"admin_session": cookie})
    assert resp_models.status_code == 200

    resp_sys = client.get("/v1/system/resources", cookies={"admin_session": cookie})
    assert resp_sys.status_code == 200

    # 4. Panel session can generate a WebSocket ticket
    resp_ticket = client.post("/v1/auth/ticket", cookies={"admin_session": cookie})
    assert resp_ticket.status_code == 200
    ticket = resp_ticket.json()["ticket"]
    assert ticket is not None

    # 5. WebSocket stream handshake works with panel session directly or ticket
    with client.websocket_connect(f"/v1/audio/transcriptions/stream?ticket={ticket}"):
        pass  # Connect accepted without 4401 or 4403

    with client.websocket_connect(
        "/v1/audio/transcriptions/stream", cookies={"admin_session": cookie}
    ):
        pass  # Direct cookie auth accepted

    with client.websocket_connect(
        "/v1/voice/stream", cookies={"admin_session": cookie}
    ):
        pass  # Direct cookie auth accepted for full-duplex voice stream


def test_proxy_awareness_and_secure_cookie():
    """Cookie Secure flag and IP resolution follow configuration behind TLS proxy."""
    # 1. Cookie secure flag
    with patch.object(settings, "ENVIRONMENT", "production"):
        with patch.object(settings, "COOKIE_SECURE", None):
            assert settings.cookie_secure is True

    with patch.object(settings, "ENVIRONMENT", "staging"):
        with patch.object(settings, "COOKIE_SECURE", None):
            assert settings.cookie_secure is True

    with patch.object(settings, "ENVIRONMENT", "development"):
        with patch.object(settings, "COOKIE_SECURE", None):
            assert settings.cookie_secure is False

    with patch.object(settings, "COOKIE_SECURE", True):
        assert settings.cookie_secure is True

    # 2. Client IP resolution behind trusted proxy
    class DummyClient:
        host = "10.0.0.1"

    class DummyRequest:
        def __init__(self, host, forwarded_for=None):
            self.client = DummyClient()
            self.client.host = host
            self.headers = {}
            if forwarded_for:
                self.headers["x-forwarded-for"] = forwarded_for

    # When peer is NOT in TRUSTED_PROXY_IPS, X-Forwarded-For is ignored
    with patch.object(settings, "TRUSTED_PROXY_IPS", "10.0.0.1"):
        req_untrusted = DummyRequest(host="192.168.1.50", forwarded_for="203.0.113.195")
        assert resolve_client_ip(req_untrusted) == "192.168.1.50"

    # When peer IS in TRUSTED_PROXY_IPS, X-Forwarded-For is respected
    with patch.object(settings, "TRUSTED_PROXY_IPS", "10.0.0.1"):
        req_trusted = DummyRequest(
            host="10.0.0.1", forwarded_for="203.0.113.195, 10.0.0.1"
        )
        assert resolve_client_ip(req_trusted) == "203.0.113.195"

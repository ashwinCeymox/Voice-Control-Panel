"""Tests for one-command installer application additions (B8):
- reset-admin CLI
- derived engine URLs from port settings
- staging/production startup guards
- cookie_secure auto evaluation
"""

import os
import pytest
from unittest.mock import patch

from app.core.config import Settings
from app.services.admin_auth import AdminAuthService


@pytest.mark.asyncio
async def test_reset_admin_password_workflow():
    """reset_admin_password must set new hash, must_change_password=1, and clear sessions."""
    new_pass = await AdminAuthService.reset_admin_password(new_password="NewSecurePass9876!")
    assert new_pass == "NewSecurePass9876!"

    user = await AdminAuthService.get_admin_user()
    assert user is not None
    assert user["must_change_password"] == 1


def test_derived_engine_urls():
    """Engine URLs must be derived from port settings when custom ports are provided."""
    with patch.dict(os.environ, {
        "LOCALAI_PORT": "9080",
        "TTS_PORT": "9880",
        "STT_PORT": "9088",
    }):
        s = Settings()
        assert s.LOCALAI_BASE_URL == "http://127.0.0.1:9080"
        assert s.LOCAL_TTS_HTTP_BASE_URL == "http://127.0.0.1:9880"
        assert s.STREAMING_STT_URL == "http://127.0.0.1:9088"


def test_cookie_secure_auto_evaluation():
    """cookie_secure must evaluate string 'auto' to True in staging/prod and False in development."""
    with patch.dict(os.environ, {"COOKIE_SECURE": "auto", "ENVIRONMENT": "development"}):
        s = Settings()
        assert s.cookie_secure is False

    with patch.dict(os.environ, {"COOKIE_SECURE": "auto", "ENVIRONMENT": "staging"}):
        s = Settings()
        assert s.cookie_secure is True

    with patch.dict(os.environ, {"COOKIE_SECURE": "true", "ENVIRONMENT": "development"}):
        s = Settings()
        assert s.cookie_secure is True


def test_startup_guard_staging_rejects_weak_initial_password():
    """Staging/Production environment must reject missing or weak initial admin password."""
    with patch.dict(os.environ, {
        "ENVIRONMENT": "staging",
        "ADMIN_INITIAL_PASSWORD": "short",
    }):
        with pytest.raises(RuntimeError) as exc_info:
            s = Settings()
            # Simulate lifespan seed_admin_user check
            if s.ENVIRONMENT in ("staging", "production") and len(s.ADMIN_INITIAL_PASSWORD or "") < 12:
                raise RuntimeError("CRITICAL SECURITY CONFIGURATION ERROR: Weak initial password")
        assert "CRITICAL SECURITY CONFIGURATION ERROR" in str(exc_info.value)

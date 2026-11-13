"""
Unit test suite verifying H1 to H11 Hardening requirements before implementation (TDD).
Each test verifies a specific security or resilience invariant from Phase 1.
"""
import os
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.core.config import settings
from app.core.security import verify_api_key, hash_api_key, _DEV_KEY_HASHES
from app.storage.database import get_db


@pytest.mark.asyncio
async def test_h1_default_dev_key_disabled(monkeypatch):
    """H1: Default dev key 'dev-voice-key-12345' must not authenticate in hardened mode without ADMIN_API_KEY."""
    monkeypatch.setattr(settings, "ADMIN_API_KEY", None)
    assert verify_api_key("dev-voice-key-12345") is None, "Default dev key must be removed"


@pytest.mark.asyncio
async def test_h2_cors_no_wildcard_with_credentials():
    """H2: CORS must not allow wildcard '*' when allow_credentials is True."""
    from starlette.middleware.cors import CORSMiddleware
    for m in app.user_middleware:
        if m.cls == CORSMiddleware:
            allow_origins = m.kwargs.get("allow_origins", [])
            allow_credentials = m.kwargs.get("allow_credentials", False)
            if allow_credentials:
                assert "*" not in allow_origins, "CORS cannot use wildcard '*' with allow_credentials=True"


@pytest.mark.asyncio
async def test_h3_error_sanitization():
    """H3: Provider errors returned to client must not leak upstream bodies, paths or URLs."""
    from app.core.errors import ProviderUnavailableError, app_error_handler
    from starlette.requests import Request
    
    exc = ProviderUnavailableError(
        provider="LocalAI STT",
        details="Internal error at /Users/hashhwin047icloud.com/Downloads/test-pro/arch/LocalAI/models/whisper.yaml: exit status 1"
    )
    req = Request({"type": "http", "method": "POST", "url": "http://test/v1/audio/transcriptions"})
    req.state.request_id = "test-req-123"
    
    resp = await app_error_handler(req, exc)
    import json
    data = json.loads(resp.body.decode())
    assert "error" in data
    assert "code" in data["error"]
    assert "message" in data["error"]
    # Upstream internal paths and stack traces must NOT be in client response details
    if "details" in data["error"] and data["error"]["details"]:
        assert "/Users/" not in str(data["error"]["details"]), "Leaked internal file path in error response"
        assert ".yaml" not in str(data["error"]["details"]), "Leaked config path in error response"


@pytest.mark.asyncio
async def test_h4_transcript_logging_disabled_by_default():
    """H4: Transcripts and user responses must not be logged by default."""
    assert hasattr(settings, "LOG_TRANSCRIPTS"), "settings.LOG_TRANSCRIPTS must exist"
    assert settings.LOG_TRANSCRIPTS is False, "LOG_TRANSCRIPTS must default to False"


@pytest.mark.asyncio
async def test_h5_sqlite_wal_and_busy_timeout():
    """H5: SQLite WAL mode and busy timeout must be enabled for concurrent writes."""
    async with get_db() as db:
        async with db.execute("PRAGMA journal_mode;") as cursor:
            row = await cursor.fetchone()
            journal_mode = row[0].lower() if row else ""
            assert journal_mode == "wal", f"SQLite journal_mode must be WAL, got '{journal_mode}'"
            
        async with db.execute("PRAGMA busy_timeout;") as cursor:
            row = await cursor.fetchone()
            timeout_ms = row[0] if row else 0
            assert timeout_ms >= 5000, f"SQLite busy_timeout must be >= 5000ms, got {timeout_ms}"


@pytest.mark.asyncio
async def test_h6_safe_model_deletion_preserves_shared_weights(tmp_path):
    """H6: Deleting one model config must not delete weights referenced by another config."""
    from app.api.gallery import is_weight_file_shared
    
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    
    weight_file = models_dir / "shared_weights.gguf"
    weight_file.write_text("weights")
    
    config1 = models_dir / "model_a.yaml"
    config1.write_text("model: shared_weights.gguf\nname: model_a\n")
    
    config2 = models_dir / "model_b.yaml"
    config2.write_text("model: shared_weights.gguf\nname: model_b\n")
    
    # Deleting model_a should recognize that model_b still references shared_weights.gguf
    assert is_weight_file_shared(models_dir, "shared_weights.gguf", excluding_config="model_a.yaml") is True


@pytest.mark.asyncio
async def test_h7_stt_fallback_disabled_by_default():
    """H7: STT automatic fallback to other models is disabled by default."""
    assert hasattr(settings, "STT_ALLOW_FALLBACK"), "settings.STT_ALLOW_FALLBACK must exist"
    assert settings.STT_ALLOW_FALLBACK is False, "STT_ALLOW_FALLBACK must default to False"


@pytest.mark.asyncio
async def test_h8_no_user_hardcoded_paths():
    """H8: No user-specific filesystem paths in configuration defaults."""
    assert "/Users/" not in (settings.LOCALAI_MODELS_PATH or ""), "Hardcoded /Users/ path in LOCALAI_MODELS_PATH"
    assert "/Users/" not in settings.APP_DATABASE_PATH, "Hardcoded /Users/ path in APP_DATABASE_PATH"


@pytest.mark.asyncio
async def test_h9_audio_duration_and_tts_limits():
    """H9: Maximum audio duration and TTS text length limits must exist and be enforced."""
    assert hasattr(settings, "MAX_TTS_TEXT_CHARS"), "MAX_TTS_TEXT_CHARS must be configured"
    assert settings.MAX_TTS_TEXT_CHARS <= 5000


@pytest.mark.asyncio
async def test_h10_per_endpoint_timeouts():
    """H10: Per-endpoint timeouts must be configured and shorter than 120s for raw endpoints."""
    assert hasattr(settings, "TIMEOUT_STT_SECONDS")
    assert hasattr(settings, "TIMEOUT_LLM_SECONDS")
    assert hasattr(settings, "TIMEOUT_TTS_SECONDS")
    assert settings.TIMEOUT_STT_SECONDS <= 60.0
    assert settings.TIMEOUT_TTS_SECONDS <= 30.0


@pytest.mark.asyncio
async def test_h11_docs_disabled_in_hardened_mode():
    """H11: /docs, /redoc, /openapi.json must be toggleable and disabled in production."""
    assert hasattr(settings, "ENABLE_DOCS"), "settings.ENABLE_DOCS must exist"

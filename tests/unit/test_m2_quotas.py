"""
Unit tests for Task M2-QUOTA: Daily key quotas (max_daily_tokens, max_daily_audio_seconds)
and HTTP 429 gate on quota exhaustion.
"""
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.main import app
from app.core.errors import AppError
from app.services.key_manager import KeyManager
from app.storage.database import init_db


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    await init_db()


@pytest.mark.asyncio
async def test_create_and_fetch_key_with_quotas():
    """Key creation stores and returns daily quota limits."""
    key = await KeyManager.create_key(
        name="quota-tester",
        role="caller",
        scopes=["llm", "stt", "tts", "voice"],
        max_daily_tokens=5000,
        max_daily_audio_seconds=300.0,
    )
    assert key["max_daily_tokens"] == 5000
    assert key["max_daily_audio_seconds"] == 300.0

    record = await KeyManager.authenticate(key["key"])
    assert record is not None
    assert record.max_daily_tokens == 5000
    assert record.max_daily_audio_seconds == 300.0


@pytest.mark.asyncio
async def test_token_quota_exhaustion_raises_429():
    """Key exceeding max_daily_tokens is blocked with 429 QUOTA_EXCEEDED."""
    key = await KeyManager.create_key(
        name="token-capped-client",
        role="caller",
        scopes=["llm"],
        max_daily_tokens=100,
    )
    plain_key = key["key"]
    record = await KeyManager.authenticate(plain_key)

    # 1. Below quota: check passes
    await KeyManager.check_quota(record, cost_type="tokens")

    # 2. Exceed quota by logging 150 tokens
    await KeyManager.log_usage(
        key_id=record.id,
        endpoint="/v1/chat/completions",
        duration_ms=200.0,
        tokens_or_seconds=150.0,
        status=200,
    )

    # 3. Check now raises AppError with 429
    with pytest.raises(AppError) as exc_info:
        await KeyManager.check_quota(record, cost_type="tokens")
    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "QUOTA_EXCEEDED"

    # 4. HTTP endpoint returns 429
    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {plain_key}"},
    )
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "QUOTA_EXCEEDED"


@pytest.mark.asyncio
async def test_audio_quota_exhaustion_raises_429():
    """Key exceeding max_daily_audio_seconds is blocked on audio endpoints."""
    key = await KeyManager.create_key(
        name="audio-capped-client",
        role="caller",
        scopes=["stt", "tts", "voice"],
        max_daily_audio_seconds=20.0,
    )
    plain_key = key["key"]
    record = await KeyManager.authenticate(plain_key)

    # Log 25 seconds of audio
    await KeyManager.log_usage(
        key_id=record.id,
        endpoint="/v1/audio/transcriptions",
        duration_ms=1000.0,
        tokens_or_seconds=25.0,
        status=200,
    )

    # Check raises 429
    with pytest.raises(AppError) as exc_info:
        await KeyManager.check_quota(record, cost_type="audio")
    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "QUOTA_EXCEEDED"

    # Calling STT endpoint returns 429
    client = TestClient(app)
    resp = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("test.wav", b"RIFF....", "audio/wav")},
        headers={"Authorization": f"Bearer {plain_key}"},
    )
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "QUOTA_EXCEEDED"


@pytest.mark.asyncio
async def test_admin_role_exempt_from_quotas():
    """Admin keys are exempt from daily quota restrictions."""
    key = await KeyManager.create_key(
        name="admin-super",
        role="admin",
        scopes=["admin"],
        max_daily_tokens=10,
    )
    record = await KeyManager.authenticate(key["key"])

    # Log 1000 tokens
    await KeyManager.log_usage(
        key_id=record.id,
        endpoint="/v1/chat/completions",
        duration_ms=500.0,
        tokens_or_seconds=1000.0,
        status=200,
    )

    # Admin passes without error
    await KeyManager.check_quota(record, cost_type="tokens")


@pytest.mark.asyncio
async def test_get_key_quota_status():
    """KeyManager.get_key_quota returns configured and current usage totals."""
    key = await KeyManager.create_key(
        name="stats-client",
        role="caller",
        scopes=["llm", "stt"],
        max_daily_tokens=2000,
        max_daily_audio_seconds=120.0,
    )
    record = await KeyManager.authenticate(key["key"])

    await KeyManager.log_usage(record.id, "/v1/chat/completions", 100.0, 450.0, 200)
    await KeyManager.log_usage(record.id, "/v1/audio/transcriptions", 200.0, 30.5, 200)

    stats = await KeyManager.get_key_quota(record.id)
    assert stats["key_id"] == record.id
    assert stats["max_daily_tokens"] == 2000
    assert stats["used_daily_tokens"] == 450.0
    assert stats["max_daily_audio_seconds"] == 120.0
    assert stats["used_daily_audio_seconds"] == 30.5

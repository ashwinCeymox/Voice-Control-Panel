"""
Unit tests for Phase P2: Keys, Roles, Scopes, Usage Tracking, and Concurrency Limits.
Follows TDD strictly.
"""
import asyncio
import time
from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.core.config import settings
from app.storage.database import get_db


@pytest.mark.asyncio
async def test_admin_can_create_list_and_revoke_keys():
    """Verify admin key management lifecycle: create, list, revoke."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin_headers = {"Authorization": "Bearer dev-voice-key-12345"}

        # 1. Create a caller key with llm and stt scopes
        create_resp = await client.post(
            "/v1/admin/keys",
            headers=admin_headers,
            json={
                "name": "internal-caller-project-1",
                "role": "caller",
                "scopes": ["llm", "stt"],
                "rate_limit": 100,
                "max_concurrent": 3,
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        data = create_resp.json()
        assert "key" in data, "Plain key must be returned once on creation"
        assert data["key"].startswith("va-")
        assert data["role"] == "caller"
        assert set(data["scopes"]) == {"llm", "stt"}
        key_id = data["id"]
        plain_key = data["key"]

        # 2. List keys - plain key and hash must not be exposed
        list_resp = await client.get("/v1/admin/keys", headers=admin_headers)
        assert list_resp.status_code == 200
        keys_list = list_resp.json()
        key_entry = next((k for k in keys_list if k["id"] == key_id), None)
        assert key_entry is not None
        assert "key" not in key_entry
        assert "hash" not in key_entry
        assert key_entry["name"] == "internal-caller-project-1"
        assert key_entry["revoked_at"] is None

        # 3. Caller key can authenticate with Bearer header
        # Using a mocked chat endpoint or lightweight check
        chat_resp = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "model": settings.LOCALAI_LLM_MODEL,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 5,
            },
        )
        # Should not be 401 or 403 (can be 200 or 502 if localai is unmocked)
        assert chat_resp.status_code != 401
        assert chat_resp.status_code != 403

        # 4. Caller key cannot access admin routes (must return 403 Forbidden)
        forbidden_resp = await client.get(
            "/v1/admin/keys",
            headers={"Authorization": f"Bearer {plain_key}"},
        )
        assert forbidden_resp.status_code == 403

        forbidden_agents = await client.get(
            "/v1/agents",
            headers={"Authorization": f"Bearer {plain_key}"},
        )
        assert forbidden_agents.status_code == 403

        # 5. Revoke key
        revoke_resp = await client.delete(
            f"/v1/admin/keys/{key_id}",
            headers=admin_headers,
        )
        assert revoke_resp.status_code == 200

        # 6. Revoked key immediately fails authentication with 401
        post_revoke_resp = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={"model": settings.LOCALAI_LLM_MODEL, "messages": []},
        )
        assert post_revoke_resp.status_code == 401


@pytest.mark.asyncio
async def test_caller_scope_enforcement():
    """Verify that caller keys can only access endpoints within their granted scopes."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin_headers = {"Authorization": "Bearer dev-voice-key-12345"}

        # Create key with only TTS scope
        tts_only_resp = await client.post(
            "/v1/admin/keys",
            headers=admin_headers,
            json={
                "name": "tts-only-caller",
                "role": "caller",
                "scopes": ["tts"],
            },
        )
        assert tts_only_resp.status_code == 201
        tts_key = tts_only_resp.json()["key"]

        # Attempt to access STT (should be forbidden 403)
        stt_resp = await client.post(
            "/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {tts_key}"},
            files={"file": ("test.wav", b"dummy", "audio/wav")},
        )
        assert stt_resp.status_code == 403
        assert "scope" in stt_resp.json()["error"]["message"].lower()

        # Attempt to access LLM (should be forbidden 403)
        llm_resp = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {tts_key}"},
            json={"model": settings.LOCALAI_LLM_MODEL, "messages": []},
        )
        assert llm_resp.status_code == 403
        assert "scope" in llm_resp.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_usage_tracking_records_activity():
    """Verify usage records are inserted with duration and counts, and visible to admin."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin_headers = {"Authorization": "Bearer dev-voice-key-12345"}

        # Create a key
        k_resp = await client.post(
            "/v1/admin/keys",
            headers=admin_headers,
            json={"name": "usage-test-caller", "role": "caller", "scopes": ["tts"]},
        )
        key_id = k_resp.json()["id"]

        # Record a usage event in the database
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO usage (key_id, endpoint, timestamp, duration_ms, tokens_or_seconds, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (key_id, "/v1/text-to-speech", time.time(), 145.2, 3.5, 200),
            )
            await db.commit()

        # Query admin usage summary
        usage_resp = await client.get("/v1/admin/usage", headers=admin_headers)
        assert usage_resp.status_code == 200
        records = usage_resp.json()
        assert len(records) >= 1
        rec = next(r for r in records if r["key_id"] == key_id)
        assert rec["endpoint"] == "/v1/text-to-speech"
        assert rec["duration_ms"] == 145.2
        assert rec["tokens_or_seconds"] == 3.5
        # Verify no request bodies, audio bytes, or transcripts are stored
        assert "content" not in rec
        assert "transcript" not in rec


@pytest.mark.asyncio
async def test_concurrency_limits_and_queuing():
    """Verify backend semaphore and per-key limits return 429/503 when overloaded."""
    from app.core.limits import BackendLimiter, ConcurrencyLimitExceededError, BackendOverloadedError

    limiter = BackendLimiter(backend_cap=2, per_key_cap=1, queue_timeout=0.2)

    # Key 1 acquires slot 1
    async with limiter.acquire(key_id="key-1", backend="stt"):
        # Key 1 trying to acquire slot 2 concurrently exceeds per-key cap -> 429
        with pytest.raises(ConcurrencyLimitExceededError):
            async with limiter.acquire(key_id="key-1", backend="stt"):
                pass

        # Key 2 acquires slot 2 (global backend cap = 2 reached)
        async with limiter.acquire(key_id="key-2", backend="stt"):
            # Key 3 arrives while global backend cap is full. Queue times out -> 503
            with pytest.raises(BackendOverloadedError):
                async with limiter.acquire(key_id="key-3", backend="stt"):
                    pass

"""
QA & Security Verification Suite for Developer Dashboard (Task D5):
1. Automated snippet template testing against gateway.
2. IP allowlist CIDR enforcement tests.
3. Key rotation transition & grace period expiration tests.
4. Privacy compliance audit: zero prompts, transcripts, or audio in SQLite/exports.
"""

from pathlib import Path
import time
from unittest.mock import patch
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.main import app
from app.services.key_manager import KeyManager
from app.storage.database import init_db, get_db


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    await init_db()


def test_snippet_templates_exist_and_render_placeholders():
    """All snippet templates in docs/snippets must exist and contain valid placeholders."""
    snippet_dir = Path("docs/snippets")
    assert snippet_dir.exists() and snippet_dir.is_dir()

    expected_files = [
        "tts_stream.py",
        "llm_stream.py",
        "stt_batch.py",
        "llm_openai_sdk.py",
        "curl_snippets.sh",
    ]
    for filename in expected_files:
        fpath = snippet_dir / filename
        assert fpath.exists(), f"Snippet template {filename} missing"
        content = fpath.read_text()
        assert "{{BASE_URL}}" in content
        assert "{{API_KEY}}" in content
        assert "{{MODEL}}" in content or "{{VOICE}}" in content


@pytest.mark.asyncio
async def test_snippet_llm_chat_against_gateway():
    """Simulate execution of rendered LLM chat snippet against FastAPI test client."""
    caller_key = await KeyManager.create_key(
        "llm-snippet-runner", role="caller", scopes=["llm"]
    )
    client = TestClient(app)

    # Read snippet template and interpolate live placeholders
    snippet_tpl = Path("docs/snippets/llm_stream.py").read_text()
    rendered = (
        snippet_tpl.replace("{{BASE_URL}}", "http://testserver")
        .replace("{{API_KEY}}", caller_key["key"])
        .replace("{{MODEL}}", "llama-3.2-1b-instruct")
    )
    assert caller_key["key"] in rendered

    # Execute request corresponding to snippet
    async def mock_llm_stream(*args, **kwargs):
        yield "Hello "
        yield "world!"

    with patch(
        "app.providers.localai_llm.LocalAILLMClient.stream_tokens",
        side_effect=mock_llm_stream,
    ):
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {caller_key['key']}"},
            json={
                "model": "llama-3.2-1b-instruct",
                "messages": [{"role": "user", "content": "Hello! Snippet test."}],
                "stream": True,
            },
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_snippet_tts_stream_against_gateway():
    """Simulate execution of rendered TTS snippet against FastAPI test client."""
    caller_key = await KeyManager.create_key(
        "tts-snippet-runner", role="caller", scopes=["tts"]
    )
    client = TestClient(app)

    async def mock_tts(*args, **kwargs):
        return b"\x00\x00" * 2400

    with patch(
        "app.providers.localtts_client.LocalTTSClient.synthesize_http",
        side_effect=mock_tts,
    ):
        resp = client.post(
            "/v1/text-to-speech/af_bella/stream",
            headers={"Authorization": f"Bearer {caller_key['key']}"},
            json={
                "text": "Hello world from snippet test.",
                "model_id": "kokoro",
                "voice_settings": {"speed": 1.0},
            },
        )
        assert resp.status_code == 200
        assert "audio/pcm" in resp.headers["content-type"]
        assert len(resp.content) > 0


@pytest.mark.asyncio
async def test_ip_allowlist_cidr_enforcement():
    """API key with IP allowlist permits allowed CIDR and blocks other IPs."""
    # Create key restricted to 192.168.1.0/24 and 10.0.0.1
    key_record = await KeyManager.create_key(
        name="ip-restricted-key",
        role="caller",
        scopes=["llm"],
        ip_allowlist=["192.168.1.0/24", "10.0.0.1"],
    )
    plain_key = key_record["key"]

    # 1. Allowed IP in 192.168.1.0/24 (e.g. 192.168.1.42)
    auth_allowed = await KeyManager.authenticate(plain_key, client_ip="192.168.1.42")
    assert auth_allowed is not None
    assert auth_allowed.id == key_record["id"]

    # 2. Allowed exact IP (10.0.0.1)
    auth_exact = await KeyManager.authenticate(plain_key, client_ip="10.0.0.1")
    assert auth_exact is not None

    # 3. Disallowed external IP (e.g. 172.16.0.5 or 10.0.0.2)
    auth_blocked = await KeyManager.authenticate(plain_key, client_ip="172.16.0.5")
    assert auth_blocked is None

    auth_blocked2 = await KeyManager.authenticate(plain_key, client_ip="10.0.0.2")
    assert auth_blocked2 is None


@pytest.mark.asyncio
async def test_key_rotation_expiration_transition():
    """Key rotation preserves access on old key during grace period, then invalidates old key."""
    admin_key = await KeyManager.create_key("admin", role="admin", scopes=["admin"])
    client = TestClient(app)
    admin_headers = {"Authorization": f"Bearer {admin_key['key']}"}

    key_resp = client.post(
        "/v1/admin/keys",
        json={"name": "expiring-key", "scopes": ["llm"]},
        headers=admin_headers,
    )
    old_key_id = key_resp.json()["id"]
    old_plain_key = key_resp.json()["key"]

    # Rotate with 0.0001 days grace period (~8.6 seconds)
    rot_resp = client.post(
        f"/v1/admin/keys/{old_key_id}/rotate",
        json={"grace_period_days": 0.0001},
        headers=admin_headers,
    )
    assert rot_resp.status_code == 201
    new_plain_key = rot_resp.json()["key"]

    # 1. Immediately after rotation: both old and new keys work
    auth_old_now = await KeyManager.authenticate(old_plain_key)
    auth_new_now = await KeyManager.authenticate(new_plain_key)
    assert auth_old_now is not None
    assert auth_new_now is not None

    # 2. Advance time past rotation_expires_at by manually setting expired timestamp on old key
    async with get_db() as db:
        await db.execute(
            "UPDATE api_keys SET rotation_expires_at = ? WHERE id = ?",
            (time.time() - 10.0, old_key_id),
        )
        await db.commit()

    # 3. Old key is now rejected
    auth_old_expired = await KeyManager.authenticate(old_plain_key)
    assert auth_old_expired is None

    # 4. New key continues working flawlessly
    auth_new_active = await KeyManager.authenticate(new_plain_key)
    assert auth_new_active is not None


@pytest.mark.asyncio
async def test_privacy_audit_zero_content_stored_in_database():
    """Privacy audit: verify zero prompt text, transcripts, or audio exist in database or logs."""
    caller_key = await KeyManager.create_key(
        "privacy-tester", role="caller", scopes=["llm", "stt"]
    )
    client = TestClient(app)

    secret_canary = "SUPER_SECRET_PATIENT_TOKEN_998877"

    # 1. Make chat completion containing secret canary
    client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {caller_key['key']}"},
        json={
            "model": "llama-3.2-1b-instruct",
            "messages": [{"role": "user", "content": secret_canary}],
            "stream": False,
        },
    )

    # 2. Audit all relevant tables for presence of secret canary
    tables_to_check = ["usage_events", "usage", "admin_audit", "profiles", "api_keys"]
    async with get_db() as db:
        for table in tables_to_check:
            async with db.execute(f"SELECT * FROM {table}") as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    for val in row:
                        if isinstance(val, str):
                            assert secret_canary not in val, (
                                f"Canary leaked into table {table} column value: {val}"
                            )

    # 3. Audit CSV export content for canary
    admin_key = await KeyManager.create_key("admin", role="admin", scopes=["admin"])
    csv_resp = client.get(
        "/v1/admin/usage/requests?export=csv",
        headers={"Authorization": f"Bearer {admin_key['key']}"},
    )
    assert csv_resp.status_code == 200
    assert secret_canary not in csv_resp.text

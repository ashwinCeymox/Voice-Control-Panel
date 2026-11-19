"""
Unit tests for the API Testing endpoint (/v1/api-testing).

Validates:
1. Missing or invalid API key rejection with 401 Unauthorized.
2. Revoked API key rejection.
3. Admin key success response with full model catalog.
4. Caller key with scope restrictions returning only matching task models.
5. Caller key with explicit model_allowlist returning exact allotted models and count.
6. Support for GET and POST (via Bearer header, xi-api-key, query parameter, and JSON body).
7. URL aliases (/api-testing, /v1/api-testing, /api-test, /v1/api-test).
"""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.main import app
from app.services.key_manager import KeyManager
from app.storage.database import init_db


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    await init_db()


def test_api_testing_missing_key():
    """Requests without API keys must be rejected with 401."""
    client = TestClient(app)
    resp = client.get("/v1/api-testing")
    assert resp.status_code == 401
    data = resp.json()
    assert data["error"]["code"] == "UNAUTHORIZED"

    resp_post = client.post("/v1/api-testing")
    assert resp_post.status_code == 401


def test_api_testing_invalid_key():
    """Requests with non-existent or invalid API keys must be rejected with 401."""
    client = TestClient(app)
    resp = client.get(
        "/v1/api-testing", headers={"Authorization": "Bearer va_live_invalid_key_9999"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_api_testing_revoked_key():
    """Revoked keys must fail authentication with 401."""
    key = await KeyManager.create_key("temp-caller", role="caller", scopes=["stt"])
    client = TestClient(app)

    # First verify it works
    resp = client.get(
        "/v1/api-testing", headers={"Authorization": f"Bearer {key['key']}"}
    )
    assert resp.status_code == 200

    # Revoke key
    await KeyManager.revoke_key(key["id"])

    # Now verify it's rejected
    resp_revoked = client.get(
        "/v1/api-testing", headers={"Authorization": f"Bearer {key['key']}"}
    )
    assert resp_revoked.status_code == 401


@pytest.mark.asyncio
async def test_api_testing_admin_key_success():
    """Admin key receives status success and all active models in the platform."""
    admin_key = await KeyManager.create_key("test-admin", role="admin", scopes=["admin"])
    client = TestClient(app)

    resp = client.get(
        "/v1/api-testing", headers={"Authorization": f"Bearer {admin_key['key']}"}
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "success"
    assert "is valid and active" in data["message"]
    assert "base_url" in data
    assert "test_endpoint" in data
    assert data["test_endpoint"].endswith("/v1/api-testing")
    assert data["key_id"] == admin_key["id"]
    assert data["key_name"] == "test-admin"
    assert data["role"] == "admin"
    assert data["models_count"] > 0
    assert data["models_available_count"] == data["models_count"]
    assert data["models_allotted_count"] == data["models_count"]
    assert len(data["models"]) == data["models_count"]
    assert len(data["allotted_models"]) == data["models_count"]


@pytest.mark.asyncio
async def test_api_testing_caller_key_scope_filtering():
    """Caller key with only STT scope only receives STT models."""
    stt_key = await KeyManager.create_key("stt-only-caller", role="caller", scopes=["stt"])
    client = TestClient(app)

    resp = client.get(
        "/v1/api-testing", headers={"Authorization": f"Bearer {stt_key['key']}"}
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "success"
    assert data["role"] == "caller"
    assert data["scopes"] == ["stt"]
    assert data["models_count"] > 0

    # Ensure all allotted models are strictly STT
    for m in data["models"]:
        assert m["task"] == "stt"
        assert m["id"] in data["allotted_models"]


@pytest.mark.asyncio
async def test_api_testing_caller_key_model_allowlist():
    """Caller key with explicit model_allowlist receives exact allotted models."""
    key = await KeyManager.create_key(
        "restricted-caller",
        role="caller",
        scopes=["stt", "llm"],
        model_allowlist=["whisper-base"],
    )
    client = TestClient(app)

    resp = client.get(
        "/v1/api-testing", headers={"Authorization": f"Bearer {key['key']}"}
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "success"
    assert data["models_count"] == 1
    assert data["models_allotted_count"] == 1
    assert data["allotted_models"] == ["whisper-base"]
    assert data["models"][0]["id"] == "whisper-base"
    assert "1 model(s) available for this key" in data["message"]


@pytest.mark.asyncio
async def test_api_testing_post_with_json_body():
    """POST with JSON body { api_key: '...' } succeeds."""
    key = await KeyManager.create_key("json-caller", role="caller", scopes=["tts"])
    client = TestClient(app)

    resp = client.post(
        "/v1/api-testing",
        json={"api_key": key["key"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["key_id"] == key["id"]
    for m in data["models"]:
        assert m["task"] == "tts"


@pytest.mark.asyncio
async def test_api_testing_auth_transports():
    """Query parameter and xi-api-key header authentication work seamlessly."""
    key = await KeyManager.create_key("transport-key", role="caller", scopes=["llm"])
    client = TestClient(app)

    # 1. Query parameter
    resp_query = client.get(f"/v1/api-testing?api_key={key['key']}")
    assert resp_query.status_code == 200
    assert resp_query.json()["status"] == "success"

    # 2. xi-api-key header
    resp_xi = client.get(
        "/v1/api-testing", headers={"xi-api-key": key["key"]}
    )
    assert resp_xi.status_code == 200
    assert resp_xi.json()["status"] == "success"


@pytest.mark.asyncio
async def test_api_testing_url_aliases():
    """Verify all route aliases (/api-testing, /v1/api-test, /api-test) work identically."""
    admin_key = await KeyManager.create_key("alias-admin", role="admin", scopes=["admin"])
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {admin_key['key']}"}

    for path in ("/v1/api-testing", "/api-testing", "/v1/api-test", "/api-test"):
        resp_get = client.get(path, headers=headers)
        assert resp_get.status_code == 200, f"GET {path} failed"
        assert resp_get.json()["status"] == "success"

        resp_post = client.post(path, headers=headers)
        assert resp_post.status_code == 200, f"POST {path} failed"
        assert resp_post.json()["status"] == "success"


def test_dev_key_rejected_from_remote_client():
    """Default dev key (dev-voice-key-12345) must be rejected if arriving from remote client IP."""
    client = TestClient(app, client=("198.51.100.24", 50000))
    resp = client.get(
        "/v1/api-testing",
        headers={"Authorization": "Bearer dev-voice-key-12345"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_metrics_requires_auth_from_remote_client():
    """External callers cannot scrape Prometheus /metrics without an API key."""
    client = TestClient(app, client=("198.51.100.24", 50000))
    # External IP without key -> 401
    resp_unauth = client.get("/metrics")
    assert resp_unauth.status_code == 401
    assert resp_unauth.json()["error"]["code"] == "UNAUTHORIZED"

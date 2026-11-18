"""
Unit tests for Task D2-USAGE: Usage events pipeline, analytics summary, timeseries,
request log with CSV export, and models status endpoint.
"""

import csv
import io
import time
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.main import app
from app.services.key_manager import KeyManager
from app.storage.database import init_db


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    await init_db()


@pytest.mark.asyncio
async def test_usage_summary_and_grouping():
    """Admin can query usage summary with totals, latency percentiles, and group breakdowns."""
    admin_key = await KeyManager.create_key("admin", role="admin", scopes=["admin"])
    caller_key = await KeyManager.create_key(
        "caller-app", role="caller", scopes=["llm", "stt"]
    )
    client = TestClient(app)
    admin_headers = {"Authorization": f"Bearer {admin_key['key']}"}

    # Record sample usage events
    from app.services.usage_service import record_usage_event

    now = time.time()
    await record_usage_event(
        key_id=caller_key["id"],
        endpoint="/v1/chat/completions",
        model="llama-3.2-1b-instruct",
        status=200,
        latency_ms=150.0,
        ttfb_ms=45.0,
        input_units=30.0,
        output_units=70.0,
        timestamp=now - 100,
    )
    await record_usage_event(
        key_id=caller_key["id"],
        endpoint="/v1/audio/transcriptions",
        model="whisper-base",
        status=200,
        latency_ms=250.0,
        ttfb_ms=250.0,
        input_units=5.2,
        output_units=12.0,
        timestamp=now - 50,
    )
    await record_usage_event(
        key_id=caller_key["id"],
        endpoint="/v1/chat/completions",
        model="llama-3.2-1b-instruct",
        status=429,
        latency_ms=10.0,
        ttfb_ms=None,
        input_units=0.0,
        output_units=0.0,
        error_code="QUOTA_EXCEEDED",
        timestamp=now - 10,
    )

    # 1. Summary with no grouping
    resp = client.get("/v1/admin/usage/summary?since=24h", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_requests"] >= 3
    assert data["error_count"] >= 1
    assert data["status_429_count"] >= 1
    assert data["p50_latency_ms"] > 0
    assert data["llm_tokens_in"] >= 30.0
    assert data["llm_tokens_out"] >= 70.0
    assert data["stt_audio_seconds_in"] >= 5.2

    # 2. Summary grouped by model
    resp_model = client.get(
        "/v1/admin/usage/summary?group_by=model", headers=admin_headers
    )
    assert resp_model.status_code == 200
    group_data = resp_model.json()
    assert "groups" in group_data
    assert any(g["name"] == "llama-3.2-1b-instruct" for g in group_data["groups"])


@pytest.mark.asyncio
async def test_usage_timeseries():
    """Admin can query timeseries data bucketed by hour or day."""
    admin_key = await KeyManager.create_key("admin", role="admin", scopes=["admin"])
    client = TestClient(app)
    admin_headers = {"Authorization": f"Bearer {admin_key['key']}"}

    from app.services.usage_service import record_usage_event

    now = time.time()
    await record_usage_event(
        key_id="test-key-ts",
        endpoint="/v1/chat/completions",
        model="llama-3.2-1b-instruct",
        status=200,
        latency_ms=120.0,
        input_units=15.0,
        output_units=25.0,
        timestamp=now,
    )

    resp = client.get(
        "/v1/admin/usage/timeseries?interval=hour&since=24h", headers=admin_headers
    )
    assert resp.status_code == 200
    ts_data = resp.json()
    assert "series" in ts_data
    assert len(ts_data["series"]) >= 1
    point = ts_data["series"][-1]
    assert "timestamp" in point
    assert "requests" in point
    assert point["requests"] >= 1


@pytest.mark.asyncio
async def test_usage_requests_log_and_csv_export():
    """Admin can view detailed request logs with pagination, filtering, and export to CSV."""
    admin_key = await KeyManager.create_key("admin", role="admin", scopes=["admin"])
    profile = await KeyManager.create_profile(name="telephony-core")
    caller_key = await KeyManager.create_key(
        "caller-csv",
        role="caller",
        scopes=["llm"],
        profile_id=profile["id"],
    )
    client = TestClient(app)
    admin_headers = {"Authorization": f"Bearer {admin_key['key']}"}

    from app.services.usage_service import record_usage_event

    now = time.time()
    await record_usage_event(
        key_id=caller_key["id"],
        profile_id=profile["id"],
        endpoint="/v1/chat/completions",
        model="llama-3.2-1b-instruct",
        status=200,
        latency_ms=95.0,
        ttfb_ms=30.0,
        input_units=10.0,
        output_units=40.0,
        timestamp=now,
    )

    # 1. Paged request log JSON
    resp = client.get(
        f"/v1/admin/usage/requests?profile_id={profile['id']}&limit=10",
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert len(data["items"]) >= 1
    item = data["items"][0]
    assert item["key_id"] == caller_key["id"]
    assert item["profile_id"] == profile["id"]
    assert item["endpoint"] == "/v1/chat/completions"
    assert "prompt" not in item  # Privacy guarantee
    assert "content" not in item

    # 2. CSV export
    csv_resp = client.get(
        f"/v1/admin/usage/requests?profile_id={profile['id']}&export=csv",
        headers=admin_headers,
    )
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    assert "attachment" in csv_resp.headers["content-disposition"]

    # Verify CSV structure
    csv_file = io.StringIO(csv_resp.text)
    reader = csv.DictReader(csv_file)
    rows = list(reader)
    assert len(rows) >= 1
    assert "key_id" in rows[0]
    assert "latency_ms" in rows[0]
    assert "prompt" not in rows[0]
    assert "transcript" not in rows[0]


@pytest.mark.asyncio
async def test_models_status_endpoint():
    """Admin can inspect warm/loaded models status and system memory."""
    admin_key = await KeyManager.create_key("admin", role="admin", scopes=["admin"])
    client = TestClient(app)
    admin_headers = {"Authorization": f"Bearer {admin_key['key']}"}

    resp = client.get("/v1/admin/models/status", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert "system_memory" in data
    assert len(data["models"]) >= 1
    m0 = data["models"][0]
    assert "id" in m0
    assert "status" in m0  # "warm" | "idle"
    assert "memory_mb" in m0


@pytest.mark.asyncio
async def test_caller_key_forbidden_on_usage_endpoints():
    """Caller keys without admin scope must be rejected with 403 on all usage endpoints."""
    caller_key = await KeyManager.create_key(
        "caller-user", role="caller", scopes=["llm", "stt"]
    )
    client = TestClient(app)
    caller_headers = {"Authorization": f"Bearer {caller_key['key']}"}

    for path in (
        "/v1/admin/usage/summary",
        "/v1/admin/usage/timeseries",
        "/v1/admin/usage/requests",
        "/v1/admin/models/status",
    ):
        resp = client.get(path, headers=caller_headers)
        assert resp.status_code == 403

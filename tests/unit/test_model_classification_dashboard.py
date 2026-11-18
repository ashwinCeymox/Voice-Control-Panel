"""Unit tests for Model Classification, Registry, and Dashboard Architecture (dash-fix.md).

Covers:
1. Unified Model Registry filtering (task=llm, task=stt&kind=streaming, task=tts).
2. TTS engine availability probing (rejecting dummy engines without weights).
3. Honest 400 error mapping (preventing 502 on wrong-type models).
4. Request attribution and zero "None" values in requests log.
5. Continuous zero-filled timeseries buckets.
6. Residency manager state transitions, manual flush, and settings persistence.
"""

import time
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.main import app
from app.services.key_manager import KeyManager
from app.services.model_registry import model_registry
from app.services.residency_manager import residency_manager
from app.services.usage_service import UsageService, record_usage_event
from app.storage.database import init_db, get_db


@pytest_asyncio.fixture(autouse=True)
async def setup_test_environment():
    await init_db()
    model_registry.invalidate_cache()


@pytest.mark.asyncio
async def test_model_registry_classification_and_filtering():
    """Verify that model_registry correctly filters by task and kind."""
    # Register/override test models
    await model_registry.set_override(
        model_id="test-llm-1",
        task="llm",
        kind=None,
        display_name="Test LLM 1",
    )
    await model_registry.set_override(
        model_id="test-stt-stream-1",
        task="stt",
        kind="streaming",
        display_name="Test STT Stream",
    )
    await model_registry.set_override(
        model_id="test-stt-batch-1",
        task="stt",
        kind="batch",
        display_name="Test STT Batch",
    )

    # 1. Filter by task=llm
    llm_models = await model_registry.list_models(task="llm")
    assert any(m["id"] == "test-llm-1" for m in llm_models)
    assert not any(m["id"] == "test-stt-stream-1" for m in llm_models)

    # 2. Filter by task=stt and kind=streaming
    stream_models = await model_registry.list_models(task="stt", kind="streaming")
    assert any(m["id"] == "test-stt-stream-1" for m in stream_models)
    assert not any(m["id"] == "test-stt-batch-1" for m in stream_models)
    assert not any(m["id"] == "test-llm-1" for m in stream_models)

    # 3. Filter by task=stt and kind=batch
    batch_models = await model_registry.list_models(task="stt", kind="batch")
    assert any(m["id"] == "test-stt-batch-1" for m in batch_models)
    assert not any(m["id"] == "test-stt-stream-1" for m in batch_models)


@pytest.mark.asyncio
async def test_tts_engine_probe_and_availability():
    """Verify TTS availability probe rejects dummy/uninstalled engines."""
    # Pocket/Kitten lack weights in test environment, probe should report unavailable
    is_avail_dummy, reason_dummy = await model_registry.probe_tts_engine("pocket")
    assert is_avail_dummy is False
    assert reason_dummy is not None

    # Probe caching works
    cached_avail, cached_reason = await model_registry.probe_tts_engine("pocket")
    assert cached_avail == is_avail_dummy
    assert cached_reason == reason_dummy


@pytest.mark.asyncio
async def test_honest_error_mapping_on_wrong_model_type():
    """Verify endpoints return clean 400 errors instead of 502 when wrong-task model is passed."""
    caller_key = await KeyManager.create_key(
        "caller-test", role="caller", scopes=["llm", "stt", "tts"]
    )
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {caller_key['key']}"}

    # Override whisper-base as STT
    await model_registry.set_override(model_id="whisper-base", task="stt", kind="batch")
    await model_registry.set_override(model_id="llama-3.2-1b-instruct", task="llm")

    # 1. Calling /v1/chat/completions with an STT model should return 400 Bad Request
    chat_resp = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "whisper-base",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert chat_resp.status_code == 400
    detail_msg = chat_resp.json().get("detail") or chat_resp.json().get(
        "error", {}
    ).get("message", "")
    assert "STT" in detail_msg or "LLM" in detail_msg

    # 2. Calling /v1/text-to-speech with dummy pocket engine should return 400
    tts_resp = client.post(
        "/v1/text-to-speech/af_heart/stream",
        headers=headers,
        json={
            "text": "Hello world",
            "model_id": "pocket",
        },
    )
    assert tts_resp.status_code == 400
    assert "not available" in tts_resp.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_request_attribution_and_zero_none_in_log():
    """Verify usage events store actor snapshot and request log has zero 'None' values."""
    admin_key = await KeyManager.create_key(
        "admin-test", role="admin", scopes=["admin"]
    )
    caller_key = await KeyManager.create_key(
        "prod-voice-service", role="caller", scopes=["llm"]
    )
    client = TestClient(app)
    admin_headers = {"Authorization": f"Bearer {admin_key['key']}"}

    now = time.time()
    # 1. Record an authenticated caller event
    await record_usage_event(
        key_id=caller_key["id"],
        endpoint="/v1/chat/completions",
        model="llama-3.2-1b-instruct",
        status=200,
        latency_ms=120.0,
        input_units=10.0,
        output_units=20.0,
        timestamp=now - 50,
        actor_type="key",
        key_name=caller_key["name"],
    )

    # 2. Record an unattributed/system event
    await record_usage_event(
        key_id=None,
        endpoint="/v1/system/health",
        model=None,
        status=200,
        latency_ms=5.0,
        timestamp=now - 10,
        actor_type="system",
    )

    # Fetch request log
    log_resp = client.get("/v1/admin/usage/requests?limit=10", headers=admin_headers)
    assert log_resp.status_code == 200
    data = log_resp.json()
    items = data.get("items", [])
    assert len(items) >= 2

    for it in items:
        # None must NEVER be the literal value
        assert it.get("key_name") != "None"
        assert it.get("profile_name") != "None"
        assert it.get("model") != "None"
        assert it.get("key_prefix") != "None"

    # Test filtering by actor_type
    filtered_resp = client.get(
        "/v1/admin/usage/requests?actor_type=system", headers=admin_headers
    )
    assert filtered_resp.status_code == 200
    for it in filtered_resp.json().get("items", []):
        assert it.get("actor_type") == "system"


@pytest.mark.asyncio
async def test_continuous_zero_filled_timeseries():
    """Verify that get_timeseries returns continuous buckets without missing gaps."""
    timeseries_data = await UsageService.get_timeseries(interval="hour", since="24h")
    series = timeseries_data.get("series", [])

    # A 24-hour window must have at least 24 contiguous buckets
    assert len(series) >= 24

    # Verify buckets are strictly monotonically increasing by exactly 3600 seconds
    for i in range(1, len(series)):
        diff = series[i]["timestamp"] - series[i - 1]["timestamp"]
        assert diff == 3600, (
            f"Bucket gap detected between {series[i - 1]} and {series[i]}"
        )


@pytest.mark.asyncio
async def test_residency_manager_lifecycle_and_settings():
    """Verify residency manager tracks in-flight requests, states, and settings."""
    # 1. Update settings
    updated = await residency_manager.update_settings(
        idle_timeout_seconds=180,
        stale_timeout_seconds=300,
        auto_flush_seconds=600,
        auto_flush_enabled=True,
        protected_models=["llama-3.2-1b-instruct"],
    )
    assert updated["idle_timeout_seconds"] == 180
    assert updated["auto_flush_enabled"] is True
    assert "llama-3.2-1b-instruct" in updated["protected_models"]

    # 2. Track in-flight request
    residency_manager.track_request_start("llama-3.2-1b-instruct")
    status = await residency_manager.get_residency_status()
    matching_models = [
        m for m in status.get("models", []) if "llama-3.2-1b-instruct" in m["id"]
    ]

    assert len(matching_models) > 0
    m_info = matching_models[0]
    assert m_info["in_flight"] >= 1
    assert m_info["state"] == "active"
    assert m_info["protected"] is True

    # 3. Request ends
    residency_manager.track_request_end("llama-3.2-1b-instruct")
    status2 = await residency_manager.get_residency_status()
    m_info2 = next(
        m for m in status2.get("models", []) if "llama-3.2-1b-instruct" in m["id"]
    )
    assert m_info2["in_flight"] == 0

    # 4. Protected model cannot be flushed without force
    success, reason = await residency_manager.flush_model(
        "llama-3.2-1b-instruct", force=False
    )
    assert success is False
    assert "protected" in reason.lower()

    # 5. Flush with force=True succeeds
    success_force, _ = await residency_manager.flush_model(
        "llama-3.2-1b-instruct", force=True
    )
    assert success_force is True


@pytest.mark.asyncio
async def test_parakeet_streaming_stt_classification():
    """Verify Parakeet Cpp Realtime Eou 120M V1 is classified as STT streaming, never batch."""
    from unittest.mock import patch
    from app.api.gallery import get_installed_models_data
    from app.services.model_manifest import model_manifest

    # 1. LocalAI scanner classification
    with patch("httpx.AsyncClient.get") as mock_get:
        # Mock /v1/models response
        class MockResp:
            status_code = 200

            def json(self):
                return {"data": [{"id": "parakeet-cpp-realtime_eou_120m-v1"}]}

        mock_get.return_value = MockResp()
        scanned = await model_registry.scan_localai_models()
        parakeet_entry = next(
            (m for m in scanned if m["id"] == "parakeet-cpp-realtime_eou_120m-v1"), None
        )

        assert parakeet_entry is not None
        assert parakeet_entry["task"] == "stt"
        assert parakeet_entry["kind"] == "streaming"
        assert parakeet_entry["runtime"] == "parakeet.cpp"

    # 2. Installed models gallery classification
    with patch("httpx.AsyncClient.get") as mock_get:

        class MockRespGallery:
            status_code = 200

            def json(self):
                return {"data": [{"id": "parakeet-cpp-realtime_eou_120m-v1"}]}

        mock_get.return_value = MockRespGallery()
        gallery_data = await get_installed_models_data()
        stt_entry = next(
            (
                m
                for m in gallery_data.get("stt", [])
                if m["id"] == "parakeet-cpp-realtime_eou_120m-v1"
            ),
            None,
        )

        assert stt_entry is not None
        assert stt_entry["kind"] == "streaming"

    # 3. Model manifest install record classification
    await model_manifest.record_install(
        model_id="parakeet-cpp-realtime_eou_120m-v1",
        name="parakeet-cpp-realtime_eou_120m-v1",
        category="stt",
        provider="localai",
        size_mb=500,
    )
    manifest_models = model_manifest.get_models_by_category("stt", kind="streaming")
    assert any(m["id"] == "parakeet-cpp-realtime_eou_120m-v1" for m in manifest_models)
    batch_models = model_manifest.get_models_by_category("stt", kind="batch")
    assert not any(m["id"] == "parakeet-cpp-realtime_eou_120m-v1" for m in batch_models)


@pytest.mark.asyncio
async def test_toast_consumed_once_preventing_loop():
    """Verify get_toasts(consume=True) drains toasts so SSE does not re-emit them in an infinite loop."""
    # Reset toasts
    residency_manager.get_toasts(consume=True)

    residency_manager.add_toast(
        "Model 'parakeet' unloaded successfully", level="success", model_id="parakeet"
    )

    # First fetch: should return exactly 1 toast with stable ID
    toasts1 = residency_manager.get_toasts(consume=True)
    assert len(toasts1) == 1
    assert "parakeet" in toasts1[0]["message"]
    assert toasts1[0]["id"].startswith("toast-")

    # Second fetch (subsequent SSE tick): must be empty to prevent repeated toasts
    toasts2 = residency_manager.get_toasts(consume=True)
    assert len(toasts2) == 0


@pytest.mark.asyncio
async def test_uninstall_model_endpoint_flow(tmp_path):
    """Verify DELETE /v1/models/{id} properly uninstalls model, cleans DB/manifest, and clears cache."""
    from app.services.model_manifest import model_manifest

    test_model_id = "test-uninstall-demo-v1"

    # 1. Register in SQLite and Manifest
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO installed_models (id, name, category, provider, size_mb) VALUES (?, ?, ?, ?, ?)",
            (test_model_id, "Test Uninstall Demo", "llm", "localai", 100),
        )
        await db.commit()

    await model_manifest.record_install(
        model_id=test_model_id,
        name="Test Uninstall Demo",
        category="llm",
        provider="localai",
        size_mb=100,
    )

    client = TestClient(app)
    headers = {"Authorization": "Bearer dev-voice-key-12345"}

    # 2. Invoke DELETE endpoint
    resp = client.delete(f"/v1/models/{test_model_id}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "uninstalled"
    assert data["model_id"] == test_model_id

    # 3. Verify removed from SQLite
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id FROM installed_models WHERE id = ?", (test_model_id,)
        )
        row = await cursor.fetchone()
        assert row is None

    # 4. Verify removed from manifest
    llm_models = model_manifest.get_models_by_category("llm")
    assert not any(m["id"] == test_model_id for m in llm_models)


@pytest.mark.asyncio
async def test_user_stt_classification_override_ui_endpoint():
    """Verify user can toggle STT model classification between streaming and batch via PATCH."""
    from app.services.model_manifest import model_manifest
    from app.api.gallery import get_installed_models_data

    model_id = "custom-whisper-toggle-test"

    # Initially record as batch STT
    await model_manifest.record_install(
        model_id=model_id,
        name="Custom Whisper Toggle Test",
        category="stt",
        provider="localai",
        size_mb=200,
        kind="batch",
    )

    client = TestClient(app)
    headers = {"Authorization": "Bearer dev-voice-key-12345"}

    # 1. User switches model to streaming via UI endpoint PATCH /v1/models/{id}/classification
    resp = client.patch(
        f"/v1/models/{model_id}/classification",
        headers=headers,
        json={"task": "stt", "kind": "streaming"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "updated"
    assert resp.json()["override"]["kind"] == "streaming"

    # Verify manifest updated
    stt_stream = model_manifest.get_models_by_category("stt", kind="streaming")
    assert any(m["id"] == model_id for m in stt_stream)
    stt_batch = model_manifest.get_models_by_category("stt", kind="batch")
    assert not any(m["id"] == model_id for m in stt_batch)

    # Verify model_registry list_models reflects the override
    reg_models = await model_registry.list_models(task="stt", kind="streaming")
    assert any(m["id"] == model_id for m in reg_models)

    # Verify installed models gallery data reflects the override
    installed_data = await get_installed_models_data()
    match = next((m for m in installed_data["stt"] if m["id"] == model_id), None)
    assert match is not None
    assert match["kind"] == "streaming"

    # 2. User switches model back to batch via /v1/admin/models/{id}/classification
    resp2 = client.patch(
        f"/v1/admin/models/{model_id}/classification",
        headers=headers,
        json={"task": "stt", "kind": "batch"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["override"]["kind"] == "batch"

    # Verify manifest updated back to batch
    stt_batch2 = model_manifest.get_models_by_category("stt", kind="batch")
    assert any(m["id"] == model_id for m in stt_batch2)
    stt_stream2 = model_manifest.get_models_by_category("stt", kind="streaming")
    assert not any(m["id"] == model_id for m in stt_stream2)

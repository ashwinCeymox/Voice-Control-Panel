"""Unit tests for Dynamic Model Manifest and Dependent Fails Prevention.

Verifies:
1. Persistent JSON file caching (`installed_models_manifest.json`).
2. Automatic update of manifest on model install and uninstall events.
3. Dynamic resolution and auto-healing of STT and LLM models (no hardcoded fallback strings).
4. Prevention of dependent fails: agents with deleted models gracefully resolve to installed alternatives.
"""

import json
import pytest
from app.services.model_manifest import ModelManifest
from app.services.agent_manager import _normalize_stt
from app.providers.localai_stt import LocalAISTTClient


@pytest.mark.asyncio
async def test_manifest_file_persistence_and_sync(tmp_path):
    test_json = tmp_path / "test_manifest.json"
    manifest = ModelManifest(manifest_path=str(test_json))

    # Initial state should be empty
    assert not test_json.exists()

    # Record installation
    await manifest.record_install(
        model_id="custom-stt-v1",
        name="Custom STT",
        category="stt",
        provider="localai",
        size_mb=120,
        kind="batch",
    )

    assert test_json.exists()
    content = json.loads(test_json.read_text(encoding="utf-8"))
    assert "stt" in content
    assert len(content["stt"]) == 1
    assert content["stt"][0]["id"] == "custom-stt-v1"
    assert content["stt"][0]["kind"] == "batch"

    # Read back from manifest service
    models = manifest.get_models_by_category("stt", kind="batch")
    assert len(models) == 1
    assert models[0]["id"] == "custom-stt-v1"


@pytest.mark.asyncio
async def test_manifest_install_and_uninstall_lifecycle(tmp_path):
    test_json = tmp_path / "lifecycle_manifest.json"
    manifest = ModelManifest(manifest_path=str(test_json))

    await manifest.record_install(
        model_id="streaming-sherpa-test",
        name="Streaming Sherpa",
        category="stt",
        provider="stream-stt",
        size_mb=45,
        kind="streaming",
    )
    await manifest.record_install(
        model_id="batch-whisper-test",
        name="Batch Whisper",
        category="stt",
        provider="localai",
        size_mb=140,
        kind="batch",
    )

    # Verify both exist
    stt_models = manifest.get_models_by_category("stt")
    assert len(stt_models) == 2

    # Uninstall the streaming model
    await manifest.record_uninstall("streaming-sherpa-test")

    # Verify JSON file on disk has updated
    content = json.loads(test_json.read_text(encoding="utf-8"))
    remaining_ids = [m["id"] for m in content["stt"]]
    assert "streaming-sherpa-test" not in remaining_ids
    assert "batch-whisper-test" in remaining_ids

    # Query helper
    streaming_stts = manifest.get_models_by_category("stt", kind="streaming")
    assert len(streaming_stts) == 0

    batch_stts = manifest.get_models_by_category("stt", kind="batch")
    assert len(batch_stts) == 1
    assert batch_stts[0]["id"] == "batch-whisper-test"


@pytest.mark.asyncio
async def test_dynamic_model_resolution_and_healing(tmp_path):
    test_json = tmp_path / "healing_manifest.json"
    manifest = ModelManifest(manifest_path=str(test_json))

    await manifest.record_install(
        model_id="active-batch-model",
        name="Active Batch",
        category="stt",
        provider="localai",
        size_mb=100,
        kind="batch",
    )

    # 1. Resolving an existing model returns itself
    res = manifest.resolve_model_id("stt", "active-batch-model", kind="batch")
    assert res == "active-batch-model"

    # 2. Resolving a deleted/missing model heals to the installed default
    healed = manifest.resolve_model_id("stt", "uninstalled-old-model", kind="batch")
    assert healed == "active-batch-model"

    # 3. Resolving an empty model heals to the installed default
    empty_healed = manifest.resolve_model_id("stt", "", kind="batch")
    assert empty_healed == "active-batch-model"


@pytest.mark.asyncio
async def test_stt_normalize_uses_dynamic_installed_model(monkeypatch, tmp_path):
    test_json = tmp_path / "normalize_manifest.json"
    test_manifest = ModelManifest(manifest_path=str(test_json))

    await test_manifest.record_install(
        model_id="dynamic-whisper-mock",
        name="Dynamic Mock",
        category="stt",
        provider="localai",
        size_mb=80,
        kind="batch",
    )

    # Point global model_manifest to test_manifest
    import app.services.model_manifest as mm_module

    monkeypatch.setattr(mm_module, "model_manifest", test_manifest)

    # Normalizing an empty STT dict picks up dynamic-whisper-mock
    normalized = _normalize_stt({})
    assert normalized["model"] == "dynamic-whisper-mock"
    assert normalized["kind"] == "batch"


@pytest.mark.asyncio
async def test_localai_stt_fallback_reads_from_manifest(monkeypatch, tmp_path):
    test_json = tmp_path / "fallback_manifest.json"
    test_manifest = ModelManifest(manifest_path=str(test_json))

    await test_manifest.record_install(
        model_id="whisper-tiny-installed",
        name="Tiny Whisper",
        category="stt",
        provider="localai",
        size_mb=75,
        kind="batch",
    )

    import app.services.model_manifest as mm_module

    monkeypatch.setattr(mm_module, "model_manifest", test_manifest)

    client = LocalAISTTClient(
        base_url="http://127.0.0.1:9999"
    )  # Unreachable port to trigger fallbacks
    fallbacks = await client.get_available_fallback_models(exclude="other-model")

    # Should dynamically return installed model from manifest without failing
    assert "whisper-tiny-installed" in fallbacks

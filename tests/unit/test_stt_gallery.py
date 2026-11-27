"""Unit tests for the expanded LocalAI STT model catalog and model resolution."""

import pytest
from app.api.gallery import GALLERY_CATALOG, get_installed_models_data
from app.api.agents import validate_agent_models
from app.providers.localai_stt import LocalAISTTClient


def test_gallery_stt_catalog_expansion():
    """Verify GALLERY_CATALOG includes the full suite of vetted STT models."""
    stt_models = {m["id"]: m for m in GALLERY_CATALOG if m["category"] == "stt"}
    expected_models = [
        "whisper-tiny",
        "whisper-tiny-en",
        "whisper-base",
        "whisper-base-en",
        "whisper-small",
        "whisper-small-en",
        "whisper-medium",
        "whisper-large-turbo",
        "whisperx-tiny",
    ]
    for m_id in expected_models:
        assert m_id in stt_models, f"Expected {m_id} to be present in GALLERY_CATALOG"
        model = stt_models[m_id]
        assert model["provider"] == "localai"
        assert model["size_mb"] > 0
        assert model["ram_required_mb"] > 0
        assert "quantization" in model
        assert isinstance(model["tags"], list)


@pytest.mark.asyncio
async def test_installed_models_stt_categorization():
    """Verify get_installed_models_data categorizes STT models properly."""
    data = await get_installed_models_data()
    assert "stt" in data
    stt_ids = [m["id"] for m in data["stt"]]
    # whisper-base should be detected as installed
    assert any("whisper" in sid.lower() for sid in stt_ids)


@pytest.mark.asyncio
async def test_localai_stt_resolver():
    """Verify LocalAISTTClient.resolve_model correctly handles models and aliases."""
    client = LocalAISTTClient()
    # Test fallback / default
    resolved_default = await client.resolve_model("whisper-1")
    assert "whisper" in resolved_default.lower()

    # Test explicit model name resolution
    resolved_small = await client.resolve_model("whisper-small")
    assert "whisper" in resolved_small.lower()


@pytest.mark.asyncio
async def test_validate_agent_with_installed_stt():
    """Verify validate_agent_models accepts installed STT models without error."""
    valid_payload = {
        "llm": {"model": "llama-3.2-1b-instruct"},
        "stt": {"model": "whisper-small"},
        "tts": {"engine": "kokoro"},
    }
    # Should not raise HTTPException
    await validate_agent_models(valid_payload)


def test_gallery_surfaces_full_localai_catalog_including_parakeet_and_nemotron(monkeypatch):
    """Gallery must surface all 2355 LocalAI models (parakeet, nemotron, etc.), not drop them."""
    import httpx
    from fastapi.testclient import TestClient
    import app.api.gallery as gallery_mod
    from app.core.config import settings
    from app.main import app

    fake_localai_models = [
        {"id": "parakeet-cpp-realtime_eou_120m-v1", "name": "parakeet-cpp-realtime_eou_120m-v1", "backend": "parakeet-cpp", "tags": ["stt"]},
        {"id": "parakeet-cpp-ctc-0.6b", "name": "parakeet-cpp-ctc-0.6b", "backend": "parakeet-cpp", "tags": ["stt"]},
        {"id": "parakeet-cpp-nemotron-3.5-asr-streaming-0.6b", "name": "parakeet-cpp-nemotron-3.5-asr-streaming-0.6b", "backend": "parakeet-cpp", "tags": ["stt"]},
        {"id": "nemo-parakeet-tdt-0.6b", "name": "nemo-parakeet-tdt-0.6b", "backend": "nemo", "tags": ["stt"]},
    ] + [
        {"id": f"llm-model-{i}", "name": f"llm-model-{i}", "tags": ["llm"]}
        for i in range(2351)
    ]

    fetch_count = 0

    class MockResp:
        status_code = 200
        def json(self):
            return fake_localai_models

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def get(self, url, **kwargs):
            nonlocal fetch_count
            fetch_count += 1
            return MockResp()

    gallery_mod._AVAILABLE_MODELS_CACHE = None
    gallery_mod._AVAILABLE_MODELS_TIMESTAMP = 0.0
    monkeypatch.setattr(gallery_mod.httpx, "AsyncClient", MockClient)

    async def fake_installed():
        return {"stt": [], "llm": [], "tts": []}
    monkeypatch.setattr(gallery_mod, "get_installed_models_data", fake_installed)

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {settings.ADMIN_API_KEY}"}

    r_stt = client.get("/v1/gallery/models?category=stt", headers=headers)
    assert r_stt.status_code == 200
    stt_models = {m["id"]: m for m in r_stt.json()["models"]}
    assert "parakeet-cpp-realtime_eou_120m-v1" in stt_models
    assert "parakeet-cpp-ctc-0.6b" in stt_models
    assert "parakeet-cpp-nemotron-3.5-asr-streaming-0.6b" in stt_models
    assert "nemo-parakeet-tdt-0.6b" in stt_models
    assert stt_models["parakeet-cpp-realtime_eou_120m-v1"]["kind"] == "streaming"
    assert stt_models["parakeet-cpp-ctc-0.6b"]["kind"] == "streaming"
    assert stt_models["parakeet-cpp-nemotron-3.5-asr-streaming-0.6b"]["kind"] == "streaming"

    # Second call uses cache (no second LocalAI fetch); refresh=true forces re-fetch
    r_all = client.get("/v1/gallery/models", headers=headers)
    assert r_all.status_code == 200
    assert len(r_all.json()["models"]) >= 2355
    assert fetch_count == 1

    r_ref = client.get("/v1/gallery/models?refresh=true", headers=headers)
    assert r_ref.status_code == 200
    assert fetch_count == 2


def test_gallery_returns_503_when_localai_down_no_curated_fallback(monkeypatch):
    """If LocalAI /models/available times out or fails, gallery must return 503 instead of a 10-item fallback."""
    import httpx
    from fastapi.testclient import TestClient
    import app.api.gallery as gallery_mod
    from app.core.config import settings
    from app.main import app

    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def get(self, url, **kwargs):
            raise httpx.ReadTimeout("timed out after 30s")

    gallery_mod._AVAILABLE_MODELS_CACHE = None
    gallery_mod._AVAILABLE_MODELS_TIMESTAMP = 0.0
    monkeypatch.setattr(gallery_mod.httpx, "AsyncClient", FailingClient)

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {settings.ADMIN_API_KEY}"}
    r = client.get("/v1/gallery/models?category=stt", headers=headers)
    assert r.status_code == 503
    assert "timed out" in r.json()["detail"] or "unreachable" in r.json()["detail"]

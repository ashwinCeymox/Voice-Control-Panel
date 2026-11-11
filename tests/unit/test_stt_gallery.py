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

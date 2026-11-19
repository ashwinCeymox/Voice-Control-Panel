"""Unit tests verifying strict platform-wide Kokoro-only restriction for TTS."""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.model_manifest import model_manifest
from app.services.model_registry import model_registry

AUTH_HEADER = {"Authorization": "Bearer dev-voice-key-12345"}


@pytest.mark.asyncio
async def test_models_list_tts_only_kokoro():
    """Verify GET /v1/models?task=tts only returns Kokoro models."""
    model_registry.invalidate_cache()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/v1/models?task=tts", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        models = data.get("models") or data.get("data", [])
        assert len(models) > 0
        for m in models:
            assert "kokoro" in m["id"].lower()
            assert m.get("task") == "tts"


@pytest.mark.asyncio
async def test_admin_models_tts_only_kokoro():
    """Verify GET /v1/admin/models excludes non-Kokoro TTS models."""
    model_registry.invalidate_cache()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/v1/admin/models", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        models = data.get("models", [])
        tts_models = [m for m in models if m.get("task") == "tts"]
        assert len(tts_models) > 0
        for m in tts_models:
            assert "kokoro" in m["id"].lower()


@pytest.mark.asyncio
async def test_tts_models_endpoint_only_kokoro():
    """Verify GET /v1/tts/models only returns Kokoro models."""
    model_registry.invalidate_cache()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/v1/tts/models", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        models = data.get("models", [])
        assert len(models) > 0
        for m in models:
            assert "kokoro" in m["id"].lower()


@pytest.mark.asyncio
async def test_voices_endpoint_kokoro_only():
    """Verify GET /v1/voices returns Kokoro voices and rejects non-Kokoro engines with empty."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Default query (resolves to Kokoro)
        resp = await client.get("/v1/voices", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        voices = data.get("voices", [])
        assert len(voices) > 0
        for v in voices:
            assert v.get("engine") in ("kokoro", "kokoro-82m", "default")

        # Explicit non-Kokoro engine returns empty list
        resp_pocket = await client.get("/v1/voices?engine=pocket", headers=AUTH_HEADER)
        assert resp_pocket.status_code == 200
        assert resp_pocket.json()["voices"] == []

        resp_vits = await client.get("/v1/voices?engine=vits", headers=AUTH_HEADER)
        assert resp_vits.status_code == 200
        assert resp_vits.json()["voices"] == []


@pytest.mark.asyncio
async def test_tts_stream_rejects_non_kokoro():
    """Verify POST /v1/text-to-speech/{voice_id}/stream rejects non-Kokoro engines with 400."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/v1/text-to-speech/af_heart/stream",
            headers=AUTH_HEADER,
            json={"text": "Test text", "model_id": "pocket"},
        )
        assert resp.status_code == 400
        assert "Only the resident Kokoro TTS engine is currently supported" in resp.json()["detail"]

        resp2 = await client.post(
            "/v1/text-to-speech/af_heart/stream",
            headers=AUTH_HEADER,
            json={"text": "Test text", "model_id": "kitten"},
        )
        assert resp2.status_code == 400
        assert "Only the resident Kokoro TTS engine is currently supported" in resp2.json()["detail"]


@pytest.mark.asyncio
async def test_gallery_install_rejects_tts_models():
    """Verify POST /v1/models/install rejects any TTS model downloads."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Installing pocket
        resp_pocket = await client.post(
            "/v1/models/install",
            headers=AUTH_HEADER,
            json={"model_id": "pocket"},
        )
        assert resp_pocket.status_code == 400
        assert "TTS model installation is currently disabled" in resp_pocket.json()["detail"]

        # Installing kitten
        resp_kitten = await client.post(
            "/v1/models/install",
            headers=AUTH_HEADER,
            json={"model_id": "kitten"},
        )
        assert resp_kitten.status_code == 400
        assert "TTS model installation is currently disabled" in resp_kitten.json()["detail"]

        # Installing kokoro
        resp_kokoro = await client.post(
            "/v1/models/install",
            headers=AUTH_HEADER,
            json={"model_id": "kokoro"},
        )
        assert resp_kokoro.status_code == 400
        assert "TTS model installation is currently disabled" in resp_kokoro.json()["detail"]

        # Installing explicit category='tts'
        resp_cat = await client.post(
            "/v1/models/install",
            headers=AUTH_HEADER,
            json={"model_id": "custom-tts-model", "category": "tts"},
        )
        assert resp_cat.status_code == 400
        assert "TTS model installation is currently disabled" in resp_cat.json()["detail"]


@pytest.mark.asyncio
async def test_gallery_models_listing_tts_only_kokoro():
    """Verify GET /v1/gallery/models filters out non-Kokoro TTS models."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/v1/gallery/models?category=tts", headers=AUTH_HEADER)
        assert resp.status_code == 200
        models = resp.json().get("models", [])
        assert len(models) > 0
        for m in models:
            assert "kokoro" in m["id"].lower()

        # All categories gallery
        resp_all = await client.get("/v1/gallery/models?category=all", headers=AUTH_HEADER)
        assert resp_all.status_code == 200
        all_models = resp_all.json().get("models", [])
        tts_in_all = [m for m in all_models if m.get("category") == "tts"]
        for m in tts_in_all:
            assert "kokoro" in m["id"].lower()


@pytest.mark.asyncio
async def test_installed_models_data_tts_only_kokoro():
    """Verify GET /v1/models/installed returns only Kokoro for TTS."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/v1/models/installed", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert "tts" in data
        assert len(data["tts"]) > 0
        for m in data["tts"]:
            assert "kokoro" in m["id"].lower()


def test_model_manifest_tts_resolves_to_kokoro():
    """Verify ModelManifest resolves and defaults TTS strictly to Kokoro."""
    tts_models = model_manifest.get_models_by_category("tts")
    assert len(tts_models) > 0
    for m in tts_models:
        assert "kokoro" in m["id"].lower()

    default_tts = model_manifest.get_default_model("tts")
    assert default_tts == "kokoro"

    # Dynamic healing
    resolved_pocket = model_manifest.resolve_model_id("tts", "pocket")
    assert resolved_pocket == "kokoro"

    resolved_kitten = model_manifest.resolve_model_id("tts", "kitten")
    assert resolved_kitten == "kokoro"

    resolved_vits = model_manifest.resolve_model_id("tts", "vits-piper-en_GB-alan-medium-sherpa")
    assert resolved_vits == "kokoro"

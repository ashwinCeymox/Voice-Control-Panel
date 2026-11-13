"""
Unit tests for Phase P3: Request Model Rules (Allowlist) and STTProvider interface abstraction.
Follows TDD strictly.
"""
from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.core.config import settings
from app.providers.stt_base import STTProvider
from app.providers.localai_stt import LocalAISTTClient
from app.schemas.audio import TranscriptResponse


def test_stt_provider_interface():
    """Verify STTProvider is an abstract base class and LocalAISTTClient implements it."""
    # Direct instantiation of base class must fail
    with pytest.raises(TypeError):
        STTProvider()

    # LocalAISTTClient must be a subclass and instance of STTProvider
    client = LocalAISTTClient()
    assert isinstance(client, STTProvider)


@pytest.mark.asyncio
async def test_llm_model_allowlist_enforcement(monkeypatch):
    """Verify unallowed LLM models are rejected with HTTP 400."""
    monkeypatch.setattr(settings, "LOCALAI_ALLOWED_LLM_MODELS", "llama-3.2-1b-instruct,llama-3.2-3b-instruct")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer dev-voice-key-12345"}

        # Request with disallowed model
        resp = await client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "unapproved-giant-model-70b",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert resp.status_code == 400
        assert "not in allowed models" in resp.json()["error"]["message"].lower()

        # Request with allowed model
        from app.schemas.chat import ChatCompletionResponse
        with patch("app.api.chat.llm_client.complete", new_callable=AsyncMock) as mock_complete:
            mock_complete.return_value = ChatCompletionResponse(
                id="test-chat",
                created=12345,
                model="llama-3.2-1b-instruct",
                choices=[],
            )
            resp_ok = await client.post(
                "/v1/chat/completions",
                headers=headers,
                json={
                    "model": "llama-3.2-1b-instruct",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
            assert resp_ok.status_code == 200


@pytest.mark.asyncio
async def test_stt_model_allowlist_enforcement(monkeypatch):
    """Verify unallowed STT models are rejected with HTTP 400."""
    monkeypatch.setattr(settings, "LOCALAI_ALLOWED_STT_MODELS", "whisper-base,whisper-tiny")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer dev-voice-key-12345"}

        resp = await client.post(
            "/v1/audio/transcriptions",
            headers=headers,
            data={"model": "unapproved-whisper-large-v3"},
            files={"file": ("test.wav", b"RIFF_sample_audio", "audio/wav")},
        )
        assert resp.status_code == 400
        assert "not in allowed models" in resp.json()["error"]["message"].lower()

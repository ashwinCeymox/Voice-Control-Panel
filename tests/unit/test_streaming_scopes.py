"""
Unit tests verifying that API keys allow streaming across STT, TTS, and LLM models.
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


@pytest.mark.asyncio
async def test_key_manager_default_scopes_include_streaming():
    """Newly created caller and admin keys should include all streaming capabilities by default."""
    caller_key = await KeyManager.create_key("streaming-caller", role="caller")
    assert "llm" in caller_key["scopes"]
    assert "stt" in caller_key["scopes"]
    assert "tts" in caller_key["scopes"]
    assert "stt_stream" in caller_key["scopes"]

    admin_key = await KeyManager.create_key("streaming-admin", role="admin")
    assert "llm" in admin_key["scopes"]
    assert "stt_stream" in admin_key["scopes"]
    assert "voice_stream" in admin_key["scopes"]


@pytest.mark.asyncio
async def test_stt_streaming_allowed_with_stt_scope():
    """An API key with 'stt' scope is permitted to access streaming STT."""
    key = await KeyManager.create_key("stt-caller", role="caller", scopes=["stt"])
    client = TestClient(app)

    with client.websocket_connect(
        f"/v1/audio/transcriptions/stream?api_key={key['key']}"
    ) as ws:
        # Handshake frame
        ws.send_json({"type": "stream.start", "model": "sherpa-onnx-streaming-zipformer-en-20M"})
        # Should not close with 4403
        ws.send_json({"type": "stream.cancel"})


@pytest.mark.asyncio
async def test_tts_streaming_allowed_with_tts_scope():
    """An API key with 'tts' scope is permitted to access streaming TTS."""
    key = await KeyManager.create_key("tts-caller", role="caller", scopes=["tts"])
    client = TestClient(app)

    resp = client.post(
        "/v1/text-to-speech/af_heart/stream",
        headers={"Authorization": f"Bearer {key['key']}"},
        json={"text": "Hello streaming TTS test", "model_id": "kokoro"},
    )
    # Status may be 200 (if engine active) or 503/400 (if engine offline in CI), but NOT 403 Forbidden
    assert resp.status_code != 403


@pytest.mark.asyncio
async def test_llm_streaming_allowed_with_llm_scope(monkeypatch):
    """An API key with 'llm' scope is permitted to access streaming chat completions."""
    from app.api.chat import llm_client

    async def mock_stream_tokens(*args, **kwargs):
        yield "data: {\"choices\": [{\"delta\": {\"content\": \"hi\"}}]}\n\n"

    monkeypatch.setattr(llm_client, "stream_tokens", mock_stream_tokens)

    key = await KeyManager.create_key("llm-caller", role="caller", scopes=["llm"])
    client = TestClient(app)

    resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json={
            "model": "llama-3.2-1b-instruct",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    # Must not be rejected with 403 Forbidden
    assert resp.status_code != 403


@pytest.mark.asyncio
async def test_voice_scope_allows_all_streaming(monkeypatch):
    """An API key with 'voice' scope is permitted to access all streaming endpoints."""
    from app.api.chat import llm_client

    async def mock_stream_tokens(*args, **kwargs):
        yield "data: {\"choices\": [{\"delta\": {\"content\": \"hi\"}}]}\n\n"

    monkeypatch.setattr(llm_client, "stream_tokens", mock_stream_tokens)

    key = await KeyManager.create_key("voice-caller", role="caller", scopes=["voice"])
    client = TestClient(app)

    # 1. STT Stream
    with client.websocket_connect(
        f"/v1/audio/transcriptions/stream?api_key={key['key']}"
    ) as ws:
        ws.send_json({"type": "stream.start", "model": "sherpa-onnx-streaming-zipformer-en-20M"})
        ws.send_json({"type": "stream.cancel"})

    # 2. TTS Stream
    resp_tts = client.post(
        "/v1/text-to-speech/af_heart/stream",
        headers={"Authorization": f"Bearer {key['key']}"},
        json={"text": "Hello test", "model_id": "kokoro"},
    )
    assert resp_tts.status_code != 403

    # 3. LLM Stream
    resp_llm = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json={
            "model": "llama-3.2-1b-instruct",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert resp_llm.status_code != 403

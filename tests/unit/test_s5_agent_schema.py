"""
Unit tests for S5: Mode Selection, Schema Bump (v2), and Models Discovery.
Follows TDD.
"""
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.schemas.agent import AgentConfig, STTConfig
from app.services.agent_manager import AgentManager, DEFAULT_AGENT

HEADERS = {"Authorization": "Bearer dev-voice-key-12345"}


@pytest.mark.asyncio
async def test_agent_schema_v2_default():
    """Default agent must have schema_version 2, kind='batch', and fallback_model=None."""
    agent = await AgentManager.get_agent(DEFAULT_AGENT["id"])
    assert agent is not None
    assert agent["schema_version"] == 2
    assert "stt" in agent
    assert agent["stt"].get("kind") == "batch"
    assert "fallback_model" in agent["stt"]


@pytest.mark.asyncio
async def test_create_streaming_agent():
    """Creating an agent with kind='streaming' and fallback_model succeeds."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "name": "Streaming Voice Agent",
            "system_prompt": "You are a fast streaming assistant.",
            "llm": {"model": "llama-3.2-1b-instruct", "temperature": 0.7, "max_tokens": 256},
            "stt": {
                "model": "sherpa-onnx-streaming-zipformer-en-20M-2023-02-17",
                "kind": "streaming",
                "fallback_model": "whisper-base",
                "language": "en",
            },
            "tts": {"engine": "kokoro", "voice_id": "af_heart", "speed": 1.0},
            "vad": {"mode": "silero", "engine": "silero"},
            "audio": {"echo_mode": "laptop_speakers", "barge_in": True},
        }

        r = await client.post("/v1/agents", headers=HEADERS, json=payload)
        assert r.status_code == 201, r.text
        created = r.json()
        assert created["schema_version"] == 2
        assert created["stt"]["kind"] == "streaming"
        assert created["stt"]["fallback_model"] == "whisper-base"

        # Cleanup
        await client.delete(f"/v1/agents/{created['id']}", headers=HEADERS)


@pytest.mark.asyncio
async def test_installed_models_includes_streaming_metadata():
    """GET /v1/models/installed must return models with kind and provider."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/models/installed", headers=HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert "stt" in data
        # At least one model with kind="streaming" and provider="stream-stt"
        streaming_models = [m for m in data["stt"] if m.get("kind") == "streaming"]
        assert len(streaming_models) >= 1
        assert streaming_models[0]["provider"] == "stream-stt"

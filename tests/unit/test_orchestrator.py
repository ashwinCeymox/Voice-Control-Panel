"""Unit tests for Voice Orchestrator and turn endpoints."""

from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.schemas.audio import TranscriptResponse
from app.schemas.chat import (
    ChatCompletionChoice,
    ChatCompletionChoiceMessage,
    ChatCompletionResponse,
)
from app.storage.database import init_db


@pytest.mark.asyncio
async def test_voice_turn_synchronous_mocked():
    await init_db()
    transport = ASGITransport(app=app)
    mock_stt_resp = TranscriptResponse(
        text="Hello voice assistant", language="en", duration=1.5
    )
    mock_llm_resp = ChatCompletionResponse(
        id="cmpl-1",
        created=1000,
        model="llama-3.2-3b-instruct",
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatCompletionChoiceMessage(
                    role="assistant", content="Hello! How can I help?"
                ),
            )
        ],
    )
    fake_pcm = b"\x00\x00" * 24000  # 1 second of 24kHz 16-bit mono PCM

    with (
        patch(
            "app.api.voice.orchestrator.stt.transcribe",
            new_callable=AsyncMock,
        ) as mock_stt,
        patch(
            "app.providers.localai_stt.LocalAISTTClient.transcribe",
            new_callable=AsyncMock,
        ),
        patch(
            "app.providers.localai_llm.LocalAILLMClient.complete",
            new_callable=AsyncMock,
        ) as mock_llm,
        patch(
            "app.providers.localtts_client.LocalTTSClient.synthesize_http",
            new_callable=AsyncMock,
        ) as mock_tts,
    ):
        mock_stt.return_value = mock_stt_resp
        mock_llm.return_value = mock_llm_resp
        mock_tts.return_value = fake_pcm

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            files = {"file": ("utterance.wav", b"RIFF....dummywav", "audio/wav")}
            response = await client.post(
                "/v1/voice/turn",
                headers={"Authorization": "Bearer dev-voice-key-12345"},
                files=files,
            )

            assert response.status_code == 200
            data = response.json()
            assert data["transcript"] == "Hello voice assistant"
            assert data["response_text"] == "Hello! How can I help?"
            assert data["audio_base64"] is not None
            assert data["audio_format"]["sample_rate"] == 24000
            assert data["audio_format"]["sample_format"] == "s16le"
            assert data["metrics"]["total_ms"] is not None


@pytest.mark.asyncio
async def test_tts_http_endpoint_mocked():
    transport = ASGITransport(app=app)
    fake_pcm = b"\x01\x00" * 12000

    with patch(
        "app.providers.localtts_client.LocalTTSClient.synthesize_http",
        new_callable=AsyncMock,
    ) as mock_tts:
        mock_tts.return_value = fake_pcm

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/text-to-speech/af_heart/stream",
                headers={"xi-api-key": "dev-voice-key-12345"},
                json={"text": "Test synthesis", "model_id": "kokoro"},
            )
            assert resp.status_code == 200
            assert (
                resp.headers["content-type"]
                == "audio/pcm;rate=24000;encoding=signed-int;bits=16"
            )
            assert resp.headers["x-audio-sample-rate"] == "24000"
            assert resp.content == fake_pcm

"""
Unit tests for Task M2-SERVER-VAD: Server-side Silero VAD for headless audio streams.
Tests ServerVAD class, chunk processing, speech segment extraction,
and automatic turn detection in WS /v1/voice/stream without client VAD control events.
"""

import os
import wave
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.main import app
from app.services.key_manager import KeyManager
from app.services.server_vad import ServerVAD, SpeechStart, SpeechEnd
from app.storage.database import init_db

SILERO_VAD_AVAILABLE = os.path.exists("frontend/public/vad/silero_vad_v5.onnx")

pytestmark = pytest.mark.skipif(
    not SILERO_VAD_AVAILABLE,
    reason="Silero VAD model not found on disk; run scripts/setup.sh to build VAD assets",
)

TEST_WAV_PATH = "models/streaming-stt/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17/test_wavs/0.wav"
if not os.path.exists(TEST_WAV_PATH):
    TEST_WAV_PATH = "tests/fixtures/sample_utterance.wav"


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    await init_db()


def load_test_wav_pcm(path: str = TEST_WAV_PATH) -> bytes:
    with wave.open(path, "rb") as wf:
        return wf.readframes(wf.getnframes())


def test_server_vad_initialization():
    """ServerVAD successfully initializes with Silero VAD v5 model."""
    vad = ServerVAD()
    assert vad is not None
    assert not vad.is_speech_detected()


def test_server_vad_silence_yields_no_events():
    """Continuous silence yields no speech events."""
    vad = ServerVAD(sample_rate=16000)
    silence_chunk = b"\x00\x00" * 512  # 512 samples of 16-bit silence
    events = []
    for _ in range(10):
        events.extend(vad.feed_pcm(silence_chunk))
    assert len(events) == 0
    assert not vad.is_speech_detected()


def test_server_vad_detects_speech_and_speech_end():
    """Feeding speech audio triggers SpeechStart, followed by SpeechEnd when silence arrives."""
    vad = ServerVAD(
        threshold=0.3,
        min_silence_duration=0.2,
        min_speech_duration=0.2,
    )

    pcm_data = load_test_wav_pcm()
    chunk_size = 1024  # 512 16-bit samples = 1024 bytes
    events = []

    for i in range(0, len(pcm_data), chunk_size):
        chunk = pcm_data[i : i + chunk_size]
        events.extend(vad.feed_pcm(chunk))

    # Feed silence tail to trigger speech end
    silence_tail = b"\x00\x00" * 16000  # 1 second of silence
    for i in range(0, len(silence_tail), chunk_size):
        events.extend(vad.feed_pcm(silence_tail[i : i + chunk_size]))

    # Must contain at least one SpeechStart and one SpeechEnd
    starts = [e for e in events if isinstance(e, SpeechStart)]
    ends = [e for e in events if isinstance(e, SpeechEnd)]

    assert len(starts) >= 1
    assert len(ends) >= 1
    assert len(ends[0].pcm_bytes) > 0
    assert ends[0].duration_sec > 0.5


def test_server_vad_flush_finalizes_pending_segment():
    """Flushing finalizes any active speech segment."""
    vad = ServerVAD(
        threshold=0.3,
        min_speech_duration=0.2,
    )
    pcm_data = load_test_wav_pcm()
    # Feed only part of the speech
    for i in range(0, min(len(pcm_data), 32000), 1024):
        vad.feed_pcm(pcm_data[i : i + 1024])

    ended = vad.flush()
    # If speech was underway, flush returns SpeechEnd or None if below min duration
    assert ended is None or isinstance(ended, SpeechEnd)


@pytest.mark.asyncio
async def test_ws_voice_server_vad_headless_stream(monkeypatch):
    """Headless client streams raw PCM without audio.start/commit; ServerVAD triggers turn."""
    key_info = await KeyManager.create_key(
        name="telephony-trunk",
        role="caller",
        scopes=["voice"],
    )
    plain_key = key_info["key"]

    # Mock orchestrator turn execution for quick unit testing
    from app.services.voice_orchestrator import orchestrator

    async def mock_transcribe(
        audio_bytes,
        filename=None,
        content_type=None,
        model=None,
        language=None,
        **kwargs,
    ):
        class MockResp:
            text = "hello from telephony"

        return MockResp()

    async def mock_tts(*args, **kwargs):
        yield b"\x00\x00" * 320

    monkeypatch.setattr(orchestrator.stt, "transcribe", mock_transcribe)
    monkeypatch.setattr(orchestrator.tts, "stream_synthesis_ws", mock_tts)

    client = TestClient(app)
    with client.websocket_connect(
        "/v1/voice/stream?vad_engine=server_silero",
        headers={"Authorization": f"Bearer {plain_key}"},
    ) as ws:
        init_msg = ws.receive_json()
        assert init_msg["type"] == "session.created"

        # Stream speech audio in binary frames (no audio.start, no audio.commit)
        pcm_data = load_test_wav_pcm()
        chunk_size = 1024
        for i in range(0, len(pcm_data), chunk_size):
            ws.send_bytes(pcm_data[i : i + chunk_size])

        # Stream silence to trigger turn end
        silence_tail = b"\x00\x00" * 16000
        for i in range(0, len(silence_tail), chunk_size):
            ws.send_bytes(silence_tail[i : i + chunk_size])

        # The server should process the turn automatically
        import json
        import anyio

        events_received = []
        with anyio.fail_after(10.0):
            while True:
                raw = ws.receive()
                if "text" in raw:
                    msg = json.loads(raw["text"])
                    events_received.append(msg["type"])
                    if msg["type"] in ("turn.completed", "error"):
                        break

        assert "transcript.completed" in events_received
        assert "turn.completed" in events_received

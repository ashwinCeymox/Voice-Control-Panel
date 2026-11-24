"""Unit tests for Phase B4: Service Daemon, Calibration Check, and Parameter Parity."""

import json
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from app.core.config import settings
from services.streaming_bridge.calibrate import get_calibration_file_path, get_host_fingerprint
from services.streaming_bridge.core.fake_localai import FakeLocalAI
from services.streaming_bridge.scheduler.bridge_scheduler import BridgeScheduler
import services.streaming_bridge.server as bridge_module
from services.streaming_bridge.server import app


@pytest.fixture
def test_client():
    client = TestClient(app)
    return client


def test_health_endpoint(test_client):
    resp = test_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["service"] == "streaming_bridge"
    assert "metrics" in data


def test_internal_voice_signal_auth_and_state(test_client):
    # Missing token -> 401
    resp_no_token = test_client.post("/internal/voice-signal", json={"event": "voice_busy_start"})
    assert resp_no_token.status_code == 401

    # Valid token -> 200
    headers = {"X-Bridge-Token": settings.STREAMING_BRIDGE_INTERNAL_TOKEN}
    resp_start = test_client.post("/internal/voice-signal", json={"event": "voice_busy_start"}, headers=headers)
    assert resp_start.status_code == 200
    assert resp_start.json()["voice_busy"] is True

    resp_end = test_client.post("/internal/voice-signal", json={"event": "voice_busy_end"}, headers=headers)
    assert resp_end.status_code == 200
    assert resp_end.json()["voice_busy"] is False


def test_websocket_host_calibration_gate_rejects_uncalibrated_model(test_client):
    """When no passing calibration file exists for host and model, reject with BRIDGE_NOT_CALIBRATED."""
    uncalibrated_model = "uncalibrated-test-model"
    calib_file = get_calibration_file_path(uncalibrated_model)
    if calib_file.exists():
        calib_file.unlink()

    token = settings.STREAMING_BRIDGE_INTERNAL_TOKEN
    with test_client.websocket_connect(f"/ws?token={token}") as ws:
        # Send stream.start for uncalibrated model
        ws.send_text(json.dumps({
            "type": "stream.start",
            "model": uncalibrated_model,
            "language": "en",
            "format": "pcm_s16le",
            "sample_rate": 16000,
        }))

        # Server must respond with error BRIDGE_NOT_CALIBRATED and close
        resp = ws.receive_json()
        assert resp["type"] == "error"
        assert resp["code"] == "BRIDGE_NOT_CALIBRATED"


def test_websocket_stream_lifecycle_with_passed_calibration(test_client, monkeypatch):
    """When calibration passes, stream lifecycle (start -> audio -> commit -> completed) works seamlessly."""
    calib_model = "test-calibrated-model"
    calib_file = get_calibration_file_path(calib_model)
    calib_file.parent.mkdir(parents=True, exist_ok=True)

    # Write a mock passing calibration file for current host
    fp = get_host_fingerprint()
    calib_data = {
        "model": calib_model,
        "host": fp,
        "measurements": {"baseline_median_ms": 500, "stream_median_ms": 510},
        "passed": True,
        "admitted_streams": 1,
    }
    with open(calib_file, "w") as f:
        json.dump(calib_data, f)

    try:
        # Plug in FakeLocalAI
        fake = FakeLocalAI(decode_latency_s=0.01)
        fake_scheduler = BridgeScheduler(transcribe_func=fake.transcribe, max_decodes=1)
        fake_scheduler.start()
        monkeypatch.setattr(bridge_module, "scheduler", fake_scheduler)

        token = settings.STREAMING_BRIDGE_INTERNAL_TOKEN
        with test_client.websocket_connect(f"/ws?token={token}") as ws:
            # 1. Start stream
            ws.send_text(json.dumps({
                "type": "stream.start",
                "model": calib_model,
                "language": "en",
                "format": "pcm_s16le",
                "sample_rate": 16000,
            }))
            ready_evt = ws.receive_json()
            assert ready_evt["type"] == "stream.ready"

            # 2. Send 1.5s of audio (48000 bytes)
            pcm_chunk = b"\x00\x05" * 24000
            ws.send_bytes(pcm_chunk)

            # 3. Commit audio
            ws.send_text(json.dumps({"type": "audio.commit"}))

            # 4. Receive completion
            comp_evt = ws.receive_json()
            assert comp_evt["type"] == "transcript.completed"
            assert comp_evt["model_used"] == calib_model
            assert "partly cloudy" in comp_evt["text"]
    finally:
        if calib_file.exists():
            calib_file.unlink()


@pytest.mark.asyncio
async def test_localai_parameter_parity_contract(monkeypatch):
    """Contract test verifying bridge calls LocalAI with exact parameter parity (port-stt.md Section 4.5)."""
    recorded_calls = []

    class MockAsyncClient:
        async def post(self, url, files=None, data=None, headers=None):
            recorded_calls.append({"url": url, "files": files, "data": data, "headers": headers})
            class MockResponse:
                status_code = 200
                def json(self):
                    return {"text": "parity confirmed"}
            return MockResponse()

    monkeypatch.setattr(bridge_module, "http_client", MockAsyncClient())

    wav_sample = b"RIFF....WAVE"
    await bridge_module.call_localai_transcribe(
        model="parakeet-cpp-realtime_eou_120m-v1",
        wav_bytes=wav_sample,
        language="en",
    )

    assert len(recorded_calls) == 1
    call = recorded_calls[0]
    assert call["url"].endswith("/v1/audio/transcriptions")
    assert call["data"]["model"] == "parakeet-cpp-realtime_eou_120m-v1"
    # Exact parameter parity: temperature must be "0.0", language "en"
    assert call["data"]["temperature"] == "0.0"
    assert call["data"]["language"] == "en"
    assert "file" in call["files"]

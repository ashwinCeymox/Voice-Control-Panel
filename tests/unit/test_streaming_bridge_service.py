"""Unit tests for Phase B4: Service Daemon, Calibration Check, and Parameter Parity."""

import json
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from app.core.config import settings
from app.providers.localai_stt import LocalAISTTClient
from services.streaming_bridge.calibrate import get_calibration_file_path, get_host_fingerprint
from services.streaming_bridge.core.fake_localai import FakeLocalAI
from services.streaming_bridge.scheduler.bridge_scheduler import BridgeScheduler
import services.streaming_bridge.server as bridge_module
from services.streaming_bridge.server import app, startup_event


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


def test_health_endpoint_does_not_leak_token(test_client):
    """Verify health endpoint never returns or leaks STREAMING_BRIDGE_INTERNAL_TOKEN."""
    resp = test_client.get("/health")
    assert resp.status_code == 200
    assert "STREAMING_BRIDGE_INTERNAL_TOKEN" not in resp.text
    if settings.STREAMING_BRIDGE_INTERNAL_TOKEN:
        assert settings.STREAMING_BRIDGE_INTERNAL_TOKEN not in resp.text


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


def test_websocket_auth_failure_closes_socket(test_client):
    """Verify WebSocket connection without token or with invalid token is closed with WS 1008."""
    # 1. Missing token
    with pytest.raises(Exception):
        with test_client.websocket_connect("/ws"):
            pass

    # 2. Wrong token
    with pytest.raises(Exception):
        with test_client.websocket_connect("/ws?token=invalid-secret-token"):
            pass


def test_loopback_rejection_on_spoofed_header():
    """Verify requests from non-loopback socket peers are rejected with 403 / 1008 even with spoofed loopback headers."""
    non_loopback_client = TestClient(app, client=("198.51.100.1", 54321))
    spoofed_headers = {
        "X-Forwarded-For": "127.0.0.1",
        "X-Bridge-Token": settings.STREAMING_BRIDGE_INTERNAL_TOKEN,
    }

    # 1. HTTP health check from non-loopback peer
    resp = non_loopback_client.get("/health", headers=spoofed_headers)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Loopback only"

    # 2. HTTP voice signal from non-loopback peer
    resp_signal = non_loopback_client.post(
        "/internal/voice-signal",
        json={"event": "voice_busy_start"},
        headers=spoofed_headers,
    )
    assert resp_signal.status_code == 403
    assert resp_signal.json()["detail"] == "Loopback only"

    # 3. WebSocket connect from non-loopback peer
    with pytest.raises(Exception):
        with non_loopback_client.websocket_connect(
            f"/ws?token={settings.STREAMING_BRIDGE_INTERNAL_TOKEN}",
            headers={"X-Forwarded-For": "127.0.0.1"},
        ):
            pass


@pytest.mark.asyncio
async def test_server_startup_refuses_without_token(monkeypatch):
    """Verify server startup refuses to start if STREAMING_BRIDGE_INTERNAL_TOKEN is unset or empty."""
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_INTERNAL_TOKEN", "")
    with pytest.raises(RuntimeError) as exc_info:
        await startup_event()
    assert "STREAMING_BRIDGE_INTERNAL_TOKEN not configured" in str(exc_info.value)


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
    """Parity test: inspects and compares bridge call parameters directly against the real LocalAISTTClient code path."""
    import httpx

    captured_bridge_call = {}
    captured_batch_call = {}

    # 1. Capture request from real LocalAISTTClient
    original_client_post = httpx.AsyncClient.post

    async def mock_batch_post(client_self, url, *args, **kwargs):
        captured_batch_call["url"] = str(url)
        captured_batch_call["files"] = kwargs.get("files")
        captured_batch_call["data"] = kwargs.get("data")
        class MockResp:
            status_code = 200
            def json(self):
                return {"text": "real batch parity test", "language": "en", "duration": 1.0}
        return MockResp()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_batch_post)

    batch_stt_client = LocalAISTTClient()
    wav_sample = b"RIFF....WAVE"
    await batch_stt_client.transcribe(
        audio_bytes=wav_sample,
        filename="audio.wav",
        content_type="audio/wav",
        model="parakeet-cpp-realtime_eou_120m-v1",
        language="en",
    )

    # 2. Capture request from streaming bridge call_localai_transcribe
    class MockBridgeClient:
        async def post(self, url, *args, **kwargs):
            captured_bridge_call["url"] = str(url)
            captured_bridge_call["files"] = kwargs.get("files")
            captured_bridge_call["data"] = kwargs.get("data")
            class MockResp:
                status_code = 200
                def json(self):
                    return {"text": "bridge parity test"}
            return MockResp()

    monkeypatch.setattr(bridge_module, "http_client", MockBridgeClient())

    await bridge_module.call_localai_transcribe(
        model="parakeet-cpp-realtime_eou_120m-v1",
        wav_bytes=wav_sample,
        language="en",
    )

    # 3. Assert exact parameter parity between real batch STT provider and streaming bridge
    assert captured_bridge_call["url"] == captured_batch_call["url"]
    assert captured_bridge_call["data"]["model"] == captured_batch_call["data"]["model"]
    assert captured_bridge_call["data"]["response_format"] == captured_batch_call["data"]["response_format"]
    assert captured_bridge_call["data"]["temperature"] == captured_batch_call["data"]["temperature"]
    assert captured_bridge_call["data"]["condition_on_previous_text"] == captured_batch_call["data"]["condition_on_previous_text"]
    assert captured_bridge_call["data"]["language"] == captured_batch_call["data"]["language"]

    # File tuple parity
    bridge_file = captured_bridge_call["files"]["file"]
    batch_file = captured_batch_call["files"]["file"]
    assert bridge_file[0] == batch_file[0]  # filename "audio.wav"
    assert bridge_file[1] == batch_file[1]  # audio bytes
    assert bridge_file[2] == batch_file[2]  # content_type "audio/wav"

"""Unit tests for Phase B4: Service Daemon, Calibration Check, and Parameter Parity."""

import json
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from app.core.config import settings
from services.streaming_bridge.calibrate import get_calibration_file_path, get_host_fingerprint
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

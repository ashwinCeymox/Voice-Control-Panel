"""Unit tests for Phase S2: Native Streaming Service Daemon, Protocol, Limits, and Calibration Gate."""

import asyncio
import json
import pytest
from starlette.testclient import TestClient

from app.core.config import settings
from services.streaming_bridge.calibrate import (
    evaluate_pass_rule,
    get_calibration_file_path,
    get_host_fingerprint,
)
from services.streaming_bridge.engines.fake_engine import FakeStreamingEngine
from services.streaming_bridge.scheduler.bridge_scheduler import BridgeScheduler
import services.streaming_bridge.server as bridge_module
from services.streaming_bridge.server import app, startup_event


@pytest.fixture
def test_client():
    bridge_module.active_sessions.clear()
    with TestClient(app) as client:
        yield client
    bridge_module.active_sessions.clear()


def _write_passing_calibration(model: str, admitted_streams: int = 1, is_cpu_bound: bool = True):
    calib_file = get_calibration_file_path(model)
    calib_file.parent.mkdir(parents=True, exist_ok=True)
    fp = dict(get_host_fingerprint())
    fp["is_cpu_bound"] = is_cpu_bound
    calib_data = {
        "model": model,
        "host": fp,
        "measurements": {
            "turns_completed": 20,
            "turns_target": 20,
            "baseline_median_ms": 500.0,
            "baseline_p95_ms": 600.0,
            "stream_median_ms": 520.0,
            "stream_p95_ms": 650.0,
            "chunk_p95_ms": 40.0,
        },
        "passed": True,
        "admitted_streams": admitted_streams,
    }
    calib_file.write_text(json.dumps(calib_data))
    return calib_file


def test_health_endpoint(test_client):
    resp = test_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["service"] == "streaming_bridge"
    assert "metrics" in data


def test_health_endpoint_does_not_leak_token(test_client):
    resp = test_client.get("/health")
    assert resp.status_code == 200
    assert "STREAMING_BRIDGE_INTERNAL_TOKEN" not in resp.text
    if settings.STREAMING_BRIDGE_INTERNAL_TOKEN:
        assert settings.STREAMING_BRIDGE_INTERNAL_TOKEN not in resp.text


def test_internal_voice_signal_auth_and_state(test_client):
    resp_no_token = test_client.post("/internal/voice-signal", json={"event": "voice_busy_start"})
    assert resp_no_token.status_code == 401

    headers = {"X-Bridge-Token": settings.STREAMING_BRIDGE_INTERNAL_TOKEN}
    resp_start = test_client.post("/internal/voice-signal", json={"event": "voice_busy_start"}, headers=headers)
    assert resp_start.status_code == 200
    assert resp_start.json()["voice_busy"] is True

    resp_end = test_client.post("/internal/voice-signal", json={"event": "voice_busy_end"}, headers=headers)
    assert resp_end.status_code == 200
    assert resp_end.json()["voice_busy"] is False


def test_websocket_auth_and_loopback_rejection(test_client):
    with pytest.raises(Exception):
        with test_client.websocket_connect("/ws"):
            pass
    with pytest.raises(Exception):
        with test_client.websocket_connect("/ws?token=invalid-secret-token"):
            pass

    non_loopback = TestClient(app, client=("198.51.100.1", 54321))
    spoofed = {"X-Forwarded-For": "127.0.0.1", "X-Bridge-Token": settings.STREAMING_BRIDGE_INTERNAL_TOKEN}
    assert non_loopback.get("/health", headers=spoofed).status_code == 403
    assert non_loopback.post("/internal/voice-signal", json={"event": "voice_busy_start"}, headers=spoofed).status_code == 403


@pytest.mark.asyncio
async def test_server_startup_refuses_without_token(monkeypatch):
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_INTERNAL_TOKEN", "")
    with pytest.raises(RuntimeError, match="STREAMING_BRIDGE_INTERNAL_TOKEN not configured"):
        await startup_event()


def test_websocket_rejects_non_16khz_and_uncalibrated_model(test_client):
    token = settings.STREAMING_BRIDGE_INTERNAL_TOKEN
    # 1. Sample rate != 16000 rejected cleanly
    with test_client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_text(json.dumps({"type": "stream.start", "model": "m1", "format": "pcm_s16le", "sample_rate": 44100}))
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["code"] == "UNSUPPORTED_FORMAT"

    # 2. Uncalibrated model rejected cleanly
    uncal = "uncalibrated-s2-model"
    cf = get_calibration_file_path(uncal)
    if cf.exists():
        cf.unlink()
    with test_client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_text(json.dumps({"type": "stream.start", "model": uncal, "format": "pcm_s16le", "sample_rate": 16000}))
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["code"] == "BRIDGE_NOT_CALIBRATED"


def test_websocket_native_lifecycle_empty_finalize_and_zero_leaks(test_client, monkeypatch):
    model = "parakeet-cpp-realtime_eou_120m-v1"
    cf = _write_passing_calibration(model, admitted_streams=2, is_cpu_bound=False)
    fake = FakeStreamingEngine(
        chunk_script=[{"text": "hello", "eou": False}, {"text": "world", "eou": True}],
        finalize_tail_text="",
    )
    monkeypatch.setattr(bridge_module, "engine", fake)

    try:
        token = settings.STREAMING_BRIDGE_INTERNAL_TOKEN
        with test_client.websocket_connect(f"/ws?token={token}") as ws:
            ws.send_text(json.dumps({"type": "stream.start", "model": model, "sample_rate": 16000}))
            assert ws.receive_json()["type"] == "stream.ready"

            ws.send_bytes(b"\x00\x05" * 1600)
            p1 = ws.receive_json()
            assert p1["type"] == "transcript.partial"
            assert p1["confirmed"] == "hello"

            ws.send_bytes(b"\x00\x05" * 1600)
            p2 = ws.receive_json()
            assert p2["type"] == "transcript.partial"
            assert p2["confirmed"] == "hello world"
            assert p2["eou"] is True
            seg = ws.receive_json()
            assert seg["type"] == "segment.final"
            assert seg["text"] == "hello world"

            ws.send_text(json.dumps({"type": "audio.commit"}))
            comp = ws.receive_json()
            assert comp["type"] == "transcript.completed"
            assert comp["text"] == "hello world"
            assert comp["model_used"] == model

        assert len(bridge_module.active_sessions) == 0
        assert fake.active_streams == 0
    finally:
        if cf.exists():
            cf.unlink()


def test_admitted_streams_cap_returns_bridge_busy_1013(test_client, monkeypatch):
    model = "parakeet-admit-test"
    cf = _write_passing_calibration(model, admitted_streams=1, is_cpu_bound=False)
    fake = FakeStreamingEngine()
    monkeypatch.setattr(bridge_module, "engine", fake)
    token = settings.STREAMING_BRIDGE_INTERNAL_TOKEN

    try:
        with test_client.websocket_connect(f"/ws?token={token}") as ws1:
            ws1.send_text(json.dumps({"type": "stream.start", "model": model, "sample_rate": 16000}))
            assert ws1.receive_json()["type"] == "stream.ready"

            with test_client.websocket_connect(f"/ws?token={token}") as ws2:
                ws2.send_text(json.dumps({"type": "stream.start", "model": model, "sample_rate": 16000}))
                err = ws2.receive_json()
                assert err["type"] == "error"
                assert err["code"] == "BRIDGE_BUSY"
                assert err["retry_after_ms"] == 2000

        # Slot released cleanly after disconnect
        assert len(bridge_module.active_sessions) == 0
        assert fake.active_streams == 0
    finally:
        if cf.exists():
            cf.unlink()


def test_voice_turn_pause_resume_and_bounded_overflow(test_client, monkeypatch):
    model = "parakeet-pause-test"
    cf = _write_passing_calibration(model, admitted_streams=1, is_cpu_bound=True)
    fake = FakeStreamingEngine(chunk_script=[{"text": "buffered", "eou": False}, {"text": "speech", "eou": True}])
    monkeypatch.setattr(bridge_module, "engine", fake)
    monkeypatch.setattr(bridge_module.scheduler, "max_buffered_chunks", 2)
    monkeypatch.setattr(bridge_module.scheduler, "catchup_rate", 2.0)

    token = settings.STREAMING_BRIDGE_INTERNAL_TOKEN
    headers = {"X-Bridge-Token": token}
    try:
        with test_client.websocket_connect(f"/ws?token={token}") as ws:
            ws.send_text(json.dumps({"type": "stream.start", "model": model, "sample_rate": 16000}))
            assert ws.receive_json()["type"] == "stream.ready"

            # Pause signal -> emits stream.paused
            test_client.post("/internal/voice-signal", json={"event": "voice_busy_start"}, headers=headers)
            paused_evt = ws.receive_json()
            assert paused_evt["type"] == "stream.paused"
            assert paused_evt["reason"] == "voice_turn_active"

            # Feed 2 chunks while paused (fits max_buffered_chunks=2; not yet fed to engine)
            ws.send_bytes(b"\x00\x01" * 160)
            ws.send_bytes(b"\x00\x01" * 160)
            assert fake.total_feed_calls == 0

            # Resume signal -> emits stream.resumed, then commit flushes buffered audio
            test_client.post("/internal/voice-signal", json={"event": "voice_busy_end"}, headers=headers)
            resumed_evt = ws.receive_json()
            assert resumed_evt["type"] == "stream.resumed"

            ws.send_text(json.dumps({"type": "audio.commit"}))
            events = []
            while True:
                try:
                    ev = ws.receive_json()
                    events.append(ev)
                    if ev.get("type") == "transcript.completed":
                        break
                except Exception:
                    break
            assert any(e.get("type") == "transcript.completed" and "buffered speech" in e.get("text", "") for e in events)

        # Overflow test: exceeding max_buffered_chunks sends explicit BUFFER_OVERFLOW error
        with test_client.websocket_connect(f"/ws?token={token}") as ws:
            ws.send_text(json.dumps({"type": "stream.start", "model": model, "sample_rate": 16000}))
            assert ws.receive_json()["type"] == "stream.ready"
            test_client.post("/internal/voice-signal", json={"event": "voice_busy_start"}, headers=headers)
            assert ws.receive_json()["type"] == "stream.paused"

            ws.send_bytes(b"\x00\x01" * 160)
            ws.send_bytes(b"\x00\x01" * 160)
            ws.send_bytes(b"\x00\x01" * 160)  # 3rd chunk overflows max=2
            err = ws.receive_json()
            assert err["type"] == "error"
            assert err["code"] == "BUFFER_OVERFLOW"
            assert bridge_module.scheduler.overflow_count >= 1

        test_client.post("/internal/voice-signal", json={"event": "voice_busy_end"}, headers=headers)
        assert len(bridge_module.active_sessions) == 0
        assert fake.active_streams == 0
    finally:
        if cf.exists():
            cf.unlink()


def test_idle_timeout_and_worker_crash_cleanup(test_client, monkeypatch):
    model = "parakeet-timeout-test"
    cf = _write_passing_calibration(model, admitted_streams=1, is_cpu_bound=False)
    token = settings.STREAMING_BRIDGE_INTERNAL_TOKEN

    try:
        # 1. Idle timeout closes stream cleanly with 0 leaks
        fake_idle = FakeStreamingEngine()
        monkeypatch.setattr(bridge_module, "engine", fake_idle)
        monkeypatch.setattr(settings, "STREAMING_BRIDGE_IDLE_TIMEOUT_S", 0.08)
        with test_client.websocket_connect(f"/ws?token={token}") as ws:
            ws.send_text(json.dumps({"type": "stream.start", "model": model, "sample_rate": 16000}))
            assert ws.receive_json()["type"] == "stream.ready"
            err = ws.receive_json()
            assert err["type"] == "error"
            assert err["code"] == "IDLE_TIMEOUT"
        assert len(bridge_module.active_sessions) == 0
        assert fake_idle.active_streams == 0

        # 2. Worker crash during feed emits ENGINE_CRASHED and releases session
        monkeypatch.setattr(settings, "STREAMING_BRIDGE_IDLE_TIMEOUT_S", 30.0)
        fake_crash = FakeStreamingEngine(crash_on_feed_call=1)
        monkeypatch.setattr(bridge_module, "engine", fake_crash)
        with test_client.websocket_connect(f"/ws?token={token}") as ws:
            ws.send_text(json.dumps({"type": "stream.start", "model": model, "sample_rate": 16000}))
            assert ws.receive_json()["type"] == "stream.ready"
            ws.send_bytes(b"\x00\x01" * 160)
            err = ws.receive_json()
            assert err["type"] == "error"
            assert err["code"] == "ENGINE_CRASHED"
        assert len(bridge_module.active_sessions) == 0
        assert fake_crash.active_streams == 0
    finally:
        if cf.exists():
            cf.unlink()


def test_calibration_pass_rule_matches_s0b_pod_failure():
    # Exact S0b paused_2x_taskset outcome: med=1486.9 vs 1349.5 baseline, chunk_p95=1100.5ms -> FAIL, admitted=0
    passed, admitted, reason = evaluate_pass_rule(
        turns_completed=20,
        turns_target=20,
        base_med_ms=1349.5,
        base_p95_ms=1482.4,
        stream_med_ms=1486.9,
        stream_p95_ms=2004.2,
        chunk_p95_ms=1100.5,
    )
    assert passed is False
    assert admitted == 0
    assert "Thresholds failed" in reason

    # Passing host outcome
    ok, adm_ok, _ = evaluate_pass_rule(
        turns_completed=20,
        turns_target=20,
        base_med_ms=500.0,
        base_p95_ms=700.0,
        stream_med_ms=520.0,
        stream_p95_ms=800.0,
        chunk_p95_ms=25.0,
    )
    assert ok is True
    assert adm_ok == 1


@pytest.mark.asyncio
async def test_scheduler_catchup_throttled_and_yields_on_new_voice_turn():
    """Verify catch-up obeys <=2x pacing and yields cleanly if voice_busy re-triggers."""
    sched = BridgeScheduler(max_streams=1, pause_enabled=True, max_buffered_chunks=10, catchup_rate=2.0)
    sched.start()
    fed = []
    events = []

    async def deliver(chunk: bytes) -> None:
        fed.append(chunk)

    async def on_evt(evt: dict) -> None:
        events.append(evt)

    # Pause stream and buffer 3 chunks of 160ms audio (5120 bytes each = 0.16s each)
    sched.register_stream("s1", "m", on_event=on_evt, on_chunk=deliver)
    await sched.set_voice_busy(True)
    chunk_160ms = b"\x00\x01" * 2560
    for _ in range(3):
        assert await sched.handle_audio_chunk("s1", chunk_160ms, deliver) is False

    # Resume triggers background drain capped at <= 2x real time (0.08s/chunk); sleep <1 chunk & re-pause
    await sched.set_voice_busy(False)
    await asyncio.sleep(0.03)
    await sched.set_voice_busy(True)
    assert 1 <= len(fed) < 3  # stopped cleanly without full burst while second voice turn started

    # Finish draining cleanly
    await sched.set_voice_busy(False)
    await sched.flush_buffered("s1", deliver)
    assert len(fed) == 3
    assert any(e["type"] == "stream.paused" for e in events)
    assert any(e["type"] == "stream.resumed" for e in events)
    await sched.stop()


def test_websocket_max_duration_limit(test_client, monkeypatch):
    model = "parakeet-maxdur-test"
    cf = _write_passing_calibration(model, admitted_streams=1, is_cpu_bound=False)
    fake = FakeStreamingEngine()
    monkeypatch.setattr(bridge_module, "engine", fake)
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_MAX_STREAM_S", 0.05)
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_IDLE_TIMEOUT_S", 30.0)
    token = settings.STREAMING_BRIDGE_INTERNAL_TOKEN

    try:
        with test_client.websocket_connect(f"/ws?token={token}") as ws:
            ws.send_text(json.dumps({"type": "stream.start", "model": model, "sample_rate": 16000}))
            assert ws.receive_json()["type"] == "stream.ready"
            err = ws.receive_json()
            assert err["type"] == "error"
            assert err["code"] == "MAX_DURATION_EXCEEDED"
        assert len(bridge_module.active_sessions) == 0
        assert fake.active_streams == 0
    finally:
        if cf.exists():
            cf.unlink()

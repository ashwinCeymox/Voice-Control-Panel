"""Unit tests for Phase B5: Gateway Routing, Registry Transports, and Bridge Client Adapter."""

import asyncio
import json
import logging
import pytest
from starlette.testclient import TestClient

from app.core.config import settings
from app.core.errors import StreamingModelNotLoadedError
from app.main import app
from app.providers.streaming_stt_client import StreamingSTTClient
from app.schemas.events import (
    TranscriptPartialEvent,
    SegmentFinalEvent,
    TranscriptCompletedEvent,
    STTFallbackEvent,
)
from app.services.model_registry import model_registry
from app.services.streaming_bridge_client import (
    bridge_available,
    stream_via_bridge,
    voice_signal_manager,
    _map_bridge_event,
)


@pytest.fixture
def client():
    return TestClient(app)


# ==============================================================================
# 1. Flag off: Routing and Transports Parity
# ==============================================================================

@pytest.mark.asyncio
async def test_routing_flag_off_preserves_native_and_batch_transports(monkeypatch):
    """When STREAMING_BRIDGE_ENABLED is False (default):
    - Sherpa streaming models claim ['ws_stream_native'] with streaming_mode 'native'.
    - LocalAI batch STT models claim ['batch_http'] with streaming_mode 'none'.
    - bridge_available returns False.
    - No bridge connection or client is attempted.
    """
    monkeypatch.setattr(settings, "STT_STREAM_NATIVE_ENABLED", False)
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_ENABLED", False)

    # 1. native_service_available returns False immediately
    assert bridge_available("parakeet-cpp-realtime_eou_120m-v1") is False
    assert bridge_available("sherpa-onnx-streaming-zipformer-en-20m") is False

    # 2. Registry spec for Sherpa model
    sherpa_spec = await model_registry.get_model_streaming_spec("sherpa-onnx-streaming-zipformer-en-20m")
    assert "ws_stream_native" in sherpa_spec["transports"]
    assert sherpa_spec["streaming_backend"] == "sherpa"

    # 3. Registry transports for eligible Parakeet EOU model when flag is OFF
    eou_spec = await model_registry.get_model_streaming_spec("parakeet-cpp-realtime_eou_120m-v1")
    assert eou_spec["transports"] == ["batch_http"]
    assert eou_spec["streaming_mode"] == "none"
    assert eou_spec["streaming_backend"] is None


# ==============================================================================
# 2. Flag on, Eligible + Calibrated Model -> ws_stream_native (parakeet_native)
# ==============================================================================

@pytest.mark.asyncio
async def test_routing_flag_on_calibrated_model_selects_bridge_transport(monkeypatch):
    """When STT_STREAM_NATIVE_ENABLED is True and calibration passes:
    - Eligible model (`parakeet-cpp-realtime_eou_120m-v1`) gets ['batch_http', 'ws_stream_native']
      with streaming_mode='native' and streaming_backend='parakeet_native'.
    - Ineligible models (`parakeet-cpp-realtime-scene-speakers`, `nemotron`) stay batch_http / none.
    - Sherpa keeps ['ws_stream_native'] with streaming_backend='sherpa'.
    """
    monkeypatch.setattr(settings, "STT_STREAM_NATIVE_ENABLED", True)
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_INTERNAL_TOKEN", "test-internal-token-1234")
    monkeypatch.setattr(settings, "LOCALAI_MODELS_PATH", "/nonexistent-test-dir")

    import services.streaming_bridge.calibrate as calib_module
    monkeypatch.setattr(
        calib_module,
        "is_host_calibrated_and_passing",
        lambda model: (True, "OK", {"admitted_streams": 1}),
    )

    # Eligible EOU model
    assert bridge_available("parakeet-cpp-realtime_eou_120m-v1") is True
    spec = await model_registry.get_model_streaming_spec("parakeet-cpp-realtime_eou_120m-v1")
    assert "ws_stream_native" in spec["transports"]
    assert "batch_http" in spec["transports"]
    assert spec["streaming_mode"] == "native"
    assert spec["streaming_backend"] == "parakeet_native"

    # Excluded per S0 decision until tested: scene-speakers and nemotron
    assert bridge_available("parakeet-cpp-realtime-scene-speakers") is False
    scene_spec = await model_registry.get_model_streaming_spec("parakeet-cpp-realtime-scene-speakers")
    assert scene_spec["transports"] == ["batch_http"]
    assert scene_spec["streaming_mode"] == "none"

    assert bridge_available("parakeet-cpp-nemotron-3.5-asr-streaming-0.6b") is False
    nemo_spec = await model_registry.get_model_streaming_spec("parakeet-cpp-nemotron-3.5-asr-streaming-0.6b")
    assert nemo_spec["transports"] == ["batch_http"]

    # Sherpa model still claims native streaming with sherpa backend
    sherpa_spec = await model_registry.get_model_streaming_spec("sherpa-onnx-streaming-zipformer-en-20m")
    assert "ws_stream_native" in sherpa_spec["transports"]
    assert sherpa_spec["streaming_backend"] == "sherpa"

    # Gateway routes strictly by registry streaming_backend (tested even with an arbitrary model alias)
    called_backends = []
    stt_client = StreamingSTTClient()

    async def fake_bridge(audio_stream, m, lang, timeout):
        called_backends.append(("parakeet_native", m))
        yield {"type": "transcript.completed", "text": "routed by registry", "model_used": m}

    import app.services.streaming_bridge_client as sbc
    monkeypatch.setattr(sbc, "stream_via_bridge", fake_bridge)
    monkeypatch.setattr(
        model_registry,
        "get_model_streaming_spec",
        lambda _m: asyncio.sleep(0, result={
            "transports": ["batch_http", "ws_stream_native"],
            "streaming_mode": "native",
            "streaming_backend": "parakeet_native",
        }),
    )

    async def dummy_audio():
        yield b"\x00\x05" * 1600

    evts = [e async for e in stt_client.stream(dummy_audio(), model="arbitrary-registry-alias")]
    assert called_backends == [("parakeet_native", "arbitrary-registry-alias")]
    assert evts[-1]["text"] == "routed by registry"


def test_flag_off_before_after_model_list_and_native_request_unchanged(client, monkeypatch):
    """WSS.md S3 proof: compare before (flag OFF) vs after (flag ON + calibrated) on /v1/models and WS stream."""
    auth_headers = {"Authorization": f"Bearer {settings.ADMIN_API_KEY}"} if settings.ADMIN_API_KEY else {"Authorization": "Bearer dev-admin-key"}
    ws_url = f"/v1/audio/transcriptions/stream?api_key={settings.ADMIN_API_KEY or 'dev-voice-key-12345'}"

    # 1. BEFORE: Flag OFF -> platform baseline unchanged
    monkeypatch.setattr(settings, "STT_STREAM_NATIVE_ENABLED", False)
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_ENABLED", False)
    model_registry.invalidate_cache()

    resp_off = client.get("/v1/models?task=stt", headers=auth_headers)
    assert resp_off.status_code == 200
    for m in resp_off.json()["data"]:
        assert m["streaming_mode"] in ("native", "none")
        assert "ws_stream_bridge" not in m["transports"]
        if "parakeet-cpp" in m["id"].lower():
            assert m["transports"] == ["batch_http"]
            assert m["streaming_mode"] == "none"

    with client.websocket_connect(ws_url) as ws:
        ws.send_text(json.dumps({"type": "stream.start", "model": "parakeet-cpp-realtime_eou_120m-v1"}))
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["code"] == "STT_MODEL_UNAVAILABLE"

    # 2. AFTER: Flag ON + passing calibration -> eligible EOU model exposes native WS + routes through gateway
    monkeypatch.setattr(settings, "STT_STREAM_NATIVE_ENABLED", True)
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_INTERNAL_TOKEN", "test-internal-token-1234")
    monkeypatch.setattr(settings, "LOCALAI_MODELS_PATH", "/nonexistent-test-dir")
    model_registry.invalidate_cache()

    import services.streaming_bridge.calibrate as calib_module
    monkeypatch.setattr(
        calib_module,
        "is_host_calibrated_and_passing",
        lambda model: (True, "OK", {"admitted_streams": 1}),
    )

    from app.api import stream_stt as stream_stt_api

    async def fake_stream(audio_gen, model=None, language=None):
        async for _ in audio_gen:
            pass
        yield {"type": "transcript.partial", "seq": 1, "confirmed": "hello", "tentative": "", "eou": True}
        yield {"type": "transcript.completed", "text": "hello", "model_used": model, "duration": 0.1}

    monkeypatch.setattr(stream_stt_api.streaming_client, "stream", fake_stream)
    with client.websocket_connect(ws_url) as ws:
        ws.send_text(json.dumps({"type": "stream.start", "model": "parakeet-cpp-realtime_eou_120m-v1"}))
        ws.send_bytes(b"\x00\x01" * 160)
        ws.send_text(json.dumps({"type": "audio.commit"}))
        evts = [ws.receive_json(), ws.receive_json()]
        assert any(e.get("type") == "transcript.completed" and e.get("text") == "hello" for e in evts)


# ==============================================================================
# 3. Flag on, Uncalibrated Model: Explicit Rejection or Fallback
# ==============================================================================

@pytest.mark.asyncio
async def test_routing_flag_on_uncalibrated_model_rejection_and_fallback(monkeypatch):
    """When calibration is missing or failing:
    - ws_stream_bridge is NOT offered.
    - If STREAMING_BRIDGE_FALLBACK_TO_BATCH is False (default): explicit error raised/returned.
    - If STREAMING_BRIDGE_FALLBACK_TO_BATCH is True: announces fallback with stt.fallback event and runs batch.
    """
    monkeypatch.setattr(settings, "STT_STREAM_NATIVE_ENABLED", True)
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_INTERNAL_TOKEN", "test-internal-token-1234")

    import services.streaming_bridge.calibrate as calib_module
    monkeypatch.setattr(
        calib_module,
        "is_host_calibrated_and_passing",
        lambda model: (False, "Thresholds exceeded", {}),
    )

    assert bridge_available("parakeet-cpp-realtime_eou_120m-v1") is False

    transports = await model_registry.get_model_transports("parakeet-cpp-realtime_eou_120m-v1")
    assert "ws_stream_native" not in transports

    stt_client = StreamingSTTClient()

    async def dummy_audio():
        yield b"\x00\x05" * 1600

    # 1. Fallback flag OFF -> explicit error
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_FALLBACK_TO_BATCH", False)
    with pytest.raises(StreamingModelNotLoadedError):
        async for _ in stt_client.stream(dummy_audio(), model="parakeet-cpp-realtime_eou_120m-v1"):
            pass

    # 2. Fallback flag ON -> announces stt.fallback then executes batch STT
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_FALLBACK_TO_BATCH", True)

    class MockBatchSTT:
        async def transcribe(self, audio_bytes, model=None, language=None):
            class Resp:
                text = "batch fallback result"
                model = "parakeet-cpp-realtime_eou_120m-v1"
                duration = 0.5
            return Resp()

    import app.providers.localai_stt as localai_module
    monkeypatch.setattr(localai_module, "LocalAISTTClient", MockBatchSTT)

    events = []
    async for evt in stt_client.stream(dummy_audio(), model="parakeet-cpp-realtime_eou_120m-v1"):
        events.append(evt)

    assert len(events) == 2
    assert events[0]["type"] == "stt.fallback"
    assert events[0]["reason"] == "streaming_unavailable"
    assert events[1]["type"] == "transcript.completed"
    assert events[1]["text"] == "batch fallback result"


# ==============================================================================
# 4. Bridge Busy & Connection Refused Handling
# ==============================================================================

@pytest.mark.asyncio
async def test_bridge_busy_and_connection_failure_explicit_error_and_fallback(monkeypatch):
    """When bridge returns BRIDGE_BUSY or connection fails:
    - Never fall back silently.
    - If fallback flag False: emit error event.
    - If fallback flag True: emit stt.fallback with reason then batch transcript.
    """
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_INTERNAL_TOKEN", "test-token")

    async def dummy_audio():
        yield b"\x00\x05" * 1600

    # A. Connection failure simulation
    import websockets
    async def mock_connect_fail(*args, **kwargs):
        raise OSError("Connection refused on port 8089")

    monkeypatch.setattr(websockets, "connect", mock_connect_fail)

    # 1. Fallback OFF: explicit BRIDGE_UNAVAILABLE error
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_FALLBACK_TO_BATCH", False)
    events_err = []
    async for evt in stream_via_bridge(dummy_audio(), "test-model"):
        events_err.append(evt)
    assert len(events_err) == 1
    assert events_err[0]["type"] == "error"
    assert events_err[0]["code"] == "BRIDGE_UNAVAILABLE"

    # 2. Fallback ON: stt.fallback announced then batch STT executes
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_FALLBACK_TO_BATCH", True)

    class MockBatchSTT:
        async def transcribe(self, audio_bytes, model=None, language=None):
            class Resp:
                text = "offline fallback text"
                model = "test-model"
                duration = 0.5
            return Resp()

    import app.providers.localai_stt as localai_module
    monkeypatch.setattr(localai_module, "LocalAISTTClient", MockBatchSTT)

    events_fb = []
    async for evt in stream_via_bridge(dummy_audio(), "test-model"):
        events_fb.append(evt)
    assert len(events_fb) == 2
    assert events_fb[0]["type"] == "stt.fallback"
    assert events_fb[0]["reason"] == "bridge_unavailable"
    assert events_fb[1]["type"] == "transcript.completed"
    assert events_fb[1]["text"] == "offline fallback text"


# ==============================================================================
# 5. Public Event Mapping
# ==============================================================================

def test_event_mapping_to_public_schema():
    """Verify bridge payloads map directly to public event schemas in app/schemas/events.py."""
    # 1. Partial event with optional bridge degraded flag
    raw_partial = {
        "type": "transcript.partial",
        "seq": 5,
        "confirmed": "the weather is",
        "tentative": "partly cloudy",
        "text": "the weather is partly cloudy",
        "segment_id": 1,
        "degraded": True,
        "unknown_extra_field": "ignore me",
    }
    mapped_partial = _map_bridge_event(raw_partial, "test-model")
    obj_partial = TranscriptPartialEvent(**mapped_partial)
    assert obj_partial.type == "transcript.partial"
    assert obj_partial.confirmed == "the weather is"
    assert obj_partial.tentative == "partly cloudy"
    assert obj_partial.degraded is True
    assert obj_partial.seq == 5

    # 2. Segment final event
    raw_seg = {
        "type": "segment.final",
        "segment_id": 1,
        "text": "the weather is partly cloudy",
        "revised": True,
        "final_degraded": False,
    }
    mapped_seg = _map_bridge_event(raw_seg, "test-model")
    obj_seg = SegmentFinalEvent(**mapped_seg)
    assert obj_seg.type == "segment.final"
    assert obj_seg.text == "the weather is partly cloudy"
    assert obj_seg.revised is True

    # 3. Transcript completed event
    raw_comp = {
        "type": "transcript.completed",
        "text": "the weather is partly cloudy tomorrow.",
        "model_used": "parakeet-cpp-realtime-scene",
        "duration": 4.5,
        "final_degraded": False,
    }
    mapped_comp = _map_bridge_event(raw_comp, "test-model")
    obj_comp = TranscriptCompletedEvent(**mapped_comp)
    assert obj_comp.type == "transcript.completed"
    assert obj_comp.text == "the weather is partly cloudy tomorrow."
    assert obj_comp.model_used == "parakeet-cpp-realtime-scene"
    assert obj_comp.final_degraded is False

    # 4. Stream paused / resumed events map cleanly
    paused_mapped = _map_bridge_event({"type": "stream.paused", "reason": "voice_turn_active", "buffered_chunks": 3}, "test-model")
    assert paused_mapped == {"type": "stream.paused", "reason": "voice_turn_active", "buffered_chunks": 3}
    resumed_mapped = _map_bridge_event({"type": "stream.resumed", "buffered_chunks": 3}, "test-model")
    assert resumed_mapped == {"type": "stream.resumed", "buffered_chunks": 3}


# ==============================================================================
# 6. Disconnect Cleanup: No Leaked Tasks or Sockets
# ==============================================================================

@pytest.mark.asyncio
async def test_disconnect_cleanup_closes_bridge_socket(monkeypatch):
    """Verify that when a client audio stream raises or disconnects, stream.cancel is sent and socket closed."""
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_INTERNAL_TOKEN", "test-token")

    sent_messages = []
    closed = False

    class MockWebSocket:
        def __init__(self):
            self.closed = False

        async def send(self, msg):
            sent_messages.append(msg)

        async def recv(self):
            return json.dumps({"type": "stream.ready", "stream_id": "test-stream"})

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(10.0)
            raise StopAsyncIteration

        async def close(self):
            self.closed = True

    mock_ws = MockWebSocket()

    import websockets
    async def mock_connect(*args, **kwargs):
        return mock_ws

    monkeypatch.setattr(websockets, "connect", mock_connect)

    async def disconnected_audio_stream():
        yield b"\x00\x05" * 1600
        # Simulates abrupt client disconnect
        raise ConnectionResetError("Client disconnected")

    with pytest.raises(ConnectionResetError):
        async for _ in stream_via_bridge(disconnected_audio_stream(), "test-model"):
            pass

    # stream.cancel sent and socket closed
    assert any("stream.cancel" in str(msg) for msg in sent_messages)
    assert mock_ws.closed is True


# ==============================================================================
# 7. Voice Signal: Counter, Overlap, Outage Immunity
# ==============================================================================

@pytest.mark.asyncio
async def test_voice_busy_signal_counter_overlap_and_outage_immunity(monkeypatch):
    """Verify voice_busy:
    - Disabled when STREAMING_BRIDGE_ENABLED is False (0 calls).
    - Enabled: counter tracks overlap (start on 1, end on 0).
    - Exception in voice turn still guarantees voice_busy_end in finally.
    - Outage immunity: Bridge returning 500 or offline never blocks, delays, or fails the turn.
    """
    signal_calls = []

    class MockHttpClient:
        async def post(self, url, json=None, headers=None):
            signal_calls.append({"url": url, "event": json.get("event") if json else None})
            return None

    monkeypatch.setattr(voice_signal_manager, "_get_client", lambda: MockHttpClient())

    # 1. Flag OFF -> Zero calls made
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_ENABLED", False)
    async with voice_signal_manager.track_voice_busy():
        pass
    assert len(signal_calls) == 0

    # 2. Flag ON -> Counter handles overlapping turns
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_ENABLED", True)
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_INTERNAL_TOKEN", "valid-secret-token")

    # Turn A starts (active_count: 0 -> 1): fires voice_busy_start
    await voice_signal_manager.start_busy()
    assert voice_signal_manager._active_count == 1
    await asyncio.sleep(0.01)  # allow background task to run
    assert len(signal_calls) == 1
    assert signal_calls[0]["event"] == "voice_busy_start"

    # Turn B starts concurrently (active_count: 1 -> 2): no duplicate start
    await voice_signal_manager.start_busy()
    assert voice_signal_manager._active_count == 2
    await asyncio.sleep(0.01)
    assert len(signal_calls) == 1

    # Turn A ends (active_count: 2 -> 1): no end yet
    await voice_signal_manager.end_busy()
    assert voice_signal_manager._active_count == 1
    await asyncio.sleep(0.01)
    assert len(signal_calls) == 1

    # Turn B ends (active_count: 1 -> 0): fires voice_busy_end
    await voice_signal_manager.end_busy()
    assert voice_signal_manager._active_count == 0
    await asyncio.sleep(0.01)
    assert len(signal_calls) == 2
    assert signal_calls[1]["event"] == "voice_busy_end"

    # 3. Exception in voice turn -> finally still sends voice_busy_end
    signal_calls.clear()
    with pytest.raises(ZeroDivisionError):
        async with voice_signal_manager.track_voice_busy():
            _ = 1 / 0
    await asyncio.sleep(0.01)
    assert len(signal_calls) == 2
    assert signal_calls[0]["event"] == "voice_busy_start"
    assert signal_calls[1]["event"] == "voice_busy_end"

    # 4. Outage immunity: Bridge returns HTTP 500 or connection failure
    class FailingHttpClient:
        async def post(self, url, json=None, headers=None):
            raise httpx.ConnectError("Connection refused: 127.0.0.1:8089 is down")

    monkeypatch.setattr(voice_signal_manager, "_get_client", lambda: FailingHttpClient())
    # Turn execution must complete cleanly without raising
    async with voice_signal_manager.track_voice_busy():
        turn_result = "voice turn succeeded despite bridge outage"
    assert turn_result == "voice turn succeeded despite bridge outage"


# ==============================================================================
# 8. Token Never in Logs
# ==============================================================================

@pytest.mark.asyncio
async def test_internal_token_never_appears_in_logs(monkeypatch, caplog):
    """Verify STREAMING_BRIDGE_INTERNAL_TOKEN is never logged at any level."""
    secret_token = "high-entropy-super-secret-token-abcdef123456"
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_ENABLED", True)
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_INTERNAL_TOKEN", secret_token)

    with caplog.at_level(logging.DEBUG):
        # Trigger voice signal and bridge stream
        async with voice_signal_manager.track_voice_busy():
            pass

        async def empty_stream():
            if False:
                yield b""

        async for _ in stream_via_bridge(empty_stream(), "test-model"):
            pass

    # Ensure token does not appear in any captured log message
    for record in caplog.records:
        assert secret_token not in record.getMessage()
        if record.args:
            assert secret_token not in str(record.args)


# ==============================================================================
# 9. Existing Public API Model Listing Parity
# ==============================================================================

def test_model_listing_response_parity_when_flag_off(client, monkeypatch):
    """When STREAMING_BRIDGE_ENABLED is False, GET /v1/models response structure remains standard."""
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_ENABLED", False)

    auth_headers = {"Authorization": f"Bearer {settings.ADMIN_API_KEY}"} if settings.ADMIN_API_KEY else {"Authorization": "Bearer dev-admin-key"}
    resp = client.get("/v1/models?task=stt", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    assert isinstance(data["data"], list)

    for m in data["data"]:
        # When flag is off, streaming_mode is native or none, never simulated
        assert m["streaming_mode"] in ("native", "none")
        if m["streaming_mode"] == "native":
            assert "ws_stream_native" in m["transports"]

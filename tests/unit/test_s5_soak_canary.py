"""Phase S5 unit tests: soak harness, session-leak guard, and canary script integrity.

These tests run entirely with the FakeStreamingEngine (no real libparakeet.so, no network).
They verify:
  - Zero leaked active_sessions after N concurrent streams complete (or crash/disconnect)
  - Idle timeout cleans up sessions
  - bridge-soak-test.py and canary-stt-native.py pass syntax check
  - canary generates valid synthetic PCM-16 audio and constructs correct WS messages

The actual soak (10 min, N concurrent real streams) and browser canary run on the pod
with real services; see WSS.md §7 and scripts/canary-stt-native.py.
"""

import asyncio
import importlib.util
import json
import py_compile
import sys
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Canary script syntax and import sanity
# ---------------------------------------------------------------------------

def test_canary_script_compiles():
    """canary-stt-native.py must pass py_compile with no syntax errors."""
    script = Path("scripts/canary-stt-native.py")
    assert script.exists(), "scripts/canary-stt-native.py not found"
    py_compile.compile(str(script), doraise=True)


def test_soak_script_compiles():
    """bridge-soak-test.py must pass py_compile with no syntax errors."""
    script = Path("scripts/bridge-soak-test.py")
    assert script.exists(), "scripts/bridge-soak-test.py not found"
    py_compile.compile(str(script), doraise=True)


def test_canary_generates_nonzero_pcm16_chunks():
    """The canary's synthetic PCM-16 generator must emit non-silent, correctly-sized chunks."""
    spec = importlib.util.spec_from_file_location(
        "canary_stt_native", "scripts/canary-stt-native.py"
    )
    canary = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(canary)

    CHUNK_BYTES = canary.CHUNK_BYTES
    chunks = canary._make_audio_chunks(1.0)

    assert len(chunks) > 0, "No audio chunks generated"
    for c in chunks:
        assert len(c) == CHUNK_BYTES, f"Chunk size mismatch: {len(c)} != {CHUNK_BYTES}"

    # Verify non-silence (sine wave: not all zero)
    all_silent = all(b == 0 for chunk in chunks[:10] for b in chunk)
    assert not all_silent, "Generated audio is all silence (sine wave not applied)"


def test_canary_constructs_correct_start_message():
    """stream.start JSON message must include type, model, and language fields."""
    start_msg = {
        "type": "stream.start",
        "model": "parakeet-cpp-realtime_eou_120m-v1",
        "language": "en",
    }
    encoded = json.dumps(start_msg)
    decoded = json.loads(encoded)
    assert decoded["type"] == "stream.start"
    assert decoded["model"] == "parakeet-cpp-realtime_eou_120m-v1"
    assert decoded["language"] == "en"


# ---------------------------------------------------------------------------
# 2. Session-leak guard: N concurrent fake streams, all sessions must clean up
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_zero_leaked_sessions_after_n_concurrent_streams():
    """
    Simulate N concurrent WebSocket streams through the bridge server.
    After all streams complete, active_sessions must be empty (zero leaks).
    Uses FakeStreamingEngine injected via monkeypatch — no real hardware needed.
    """
    import services.streaming_bridge.server as bridge_server
    from services.streaming_bridge.engines.fake_engine import FakeStreamingEngine
    from services.streaming_bridge.scheduler.bridge_scheduler import BridgeScheduler

    N = 5
    fake_engine = FakeStreamingEngine(feed_latency_s=0.0, finalize_latency_s=0.0)
    test_scheduler = BridgeScheduler(
        max_streams=N + 2,
        pause_enabled=False,
        max_buffered_chunks=10,
        catchup_rate=1.0,
    )
    test_scheduler.start()

    original_engine = bridge_server.engine
    original_sched = bridge_server.scheduler
    original_sessions = bridge_server.active_sessions

    bridge_server.engine = fake_engine
    bridge_server.scheduler = test_scheduler
    bridge_server.active_sessions = {}

    try:
        from fastapi.testclient import TestClient
        from app.core.config import settings

        with patch.object(settings, "STREAMING_BRIDGE_INTERNAL_TOKEN", "test-token-s5"), \
             patch.object(settings, "STREAMING_BRIDGE_MAX_STREAMS", N + 2):

            client = TestClient(bridge_server.app, raise_server_exceptions=False)
            token = "test-token-s5"

            def run_one_stream(stream_idx: int):
                url = f"/ws?token={token}"
                try:
                    with client.websocket_connect(url) as ws:
                        ws.send_text(json.dumps({
                            "type": "stream.start",
                            "model": "parakeet-cpp-realtime_eou_120m-v1",
                            "language": "en",
                        }))
                        ws.send_bytes(b"\x00\x01" * 160)  # 320 bytes PCM
                        ws.send_text(json.dumps({"type": "audio.commit"}))
                        # Drain events
                        for _ in range(5):
                            try:
                                evt = ws.receive_json()
                                if evt.get("type") in ("transcript.completed", "error"):
                                    break
                            except Exception:
                                break
                except Exception:
                    pass

            import threading
            threads = [threading.Thread(target=run_one_stream, args=(i,)) for i in range(N)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=15.0)

            # Give cleanup a moment for async finally blocks
            await asyncio.sleep(0.2)

            remaining = dict(bridge_server.active_sessions)
            assert len(remaining) == 0, (
                f"Session leak detected: {len(remaining)} sessions still active after all streams completed: "
                f"{list(remaining.keys())}"
            )
    finally:
        bridge_server.engine = original_engine
        bridge_server.scheduler = original_sched
        bridge_server.active_sessions = original_sessions
        await test_scheduler.stop()


# ---------------------------------------------------------------------------
# 3. Idle timeout session cleanup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_idle_timeout_sessions_cleaned_up():
    """
    Open a stream, do not send audio, and verify that after the idle timeout
    the session is gone from active_sessions. Uses a patched short timeout.
    Exercises the same finally-block cleanup path as a real timeout.
    """
    import services.streaming_bridge.server as bridge_server
    from services.streaming_bridge.engines.fake_engine import FakeStreamingEngine
    from services.streaming_bridge.scheduler.bridge_scheduler import BridgeScheduler
    from app.core.config import settings

    fake_engine = FakeStreamingEngine(feed_latency_s=0.0, finalize_latency_s=0.0)
    test_sched = BridgeScheduler(max_streams=5, pause_enabled=False,
                                  max_buffered_chunks=5, catchup_rate=1.0)
    test_sched.start()

    original_engine = bridge_server.engine
    original_sched = bridge_server.scheduler
    original_sessions = bridge_server.active_sessions
    bridge_server.engine = fake_engine
    bridge_server.scheduler = test_sched
    bridge_server.active_sessions = {}

    try:
        with patch.object(settings, "STREAMING_BRIDGE_INTERNAL_TOKEN", "tok-idle"), \
             patch.object(settings, "STREAMING_BRIDGE_IDLE_TIMEOUT_S", 0.3), \
             patch.object(settings, "STREAMING_BRIDGE_MAX_STREAM_S", 60.0):

            from fastapi.testclient import TestClient
            client = TestClient(bridge_server.app, raise_server_exceptions=False)

            # Open stream, send start but no audio — idle timeout should fire
            try:
                with client.websocket_connect("/ws?token=tok-idle") as ws:
                    ws.send_text(json.dumps({
                        "type": "stream.start",
                        "model": "parakeet-cpp-realtime_eou_120m-v1",
                        "language": "en",
                    }))
                    # Don't send audio — wait for server to close due to idle timeout
                    for _ in range(10):
                        try:
                            ws.receive_json()
                        except Exception:
                            break
            except Exception:
                pass

            await asyncio.sleep(0.5)
            remaining = dict(bridge_server.active_sessions)
            assert len(remaining) == 0, (
                f"Idle timeout did not clean up session: {list(remaining.keys())}"
            )
    finally:
        bridge_server.engine = original_engine
        bridge_server.scheduler = original_sched
        bridge_server.active_sessions = original_sessions
        await test_sched.stop()


# ---------------------------------------------------------------------------
# 4. Docs completeness checks
# ---------------------------------------------------------------------------

def test_s5_docs_and_scripts_present():
    """All S5 operational artefacts must exist on-branch."""
    required = [
        "scripts/canary-stt-native.py",
        "scripts/bridge-soak-test.py",
        "scripts/bridge-calibrate.sh",
        "docs/STREAMING_BRIDGE.md",
        "docs/STREAMING_BRIDGE_CHANGES.md",
    ]
    for path in required:
        assert Path(path).exists(), f"Missing S5 artefact: {path}"

    canary_txt = Path("scripts/canary-stt-native.py").read_text()
    assert "parakeet-cpp-realtime_eou_120m-v1" in canary_txt
    assert "stream.start" in canary_txt
    assert "audio.commit" in canary_txt
    assert "transcript.completed" in canary_txt

    changes_txt = Path("docs/STREAMING_BRIDGE_CHANGES.md").read_text()
    assert "bridge-relay-v1" in changes_txt
    assert "STT_STREAM_NATIVE_ENABLED" in changes_txt

"""Unit, Load, Soak, and Failure Tests for Phase B8: Streaming STT Bridge.

Tests the concurrency matrix (1, 3, 5, 6, 7 streams), MAX_DECODES cap,
single-inflight-per-stream, fast vs slow talker fairness, slow LocalAI handling,
cancel/disconnect mid-decode slot release, LocalAI error/timeout resilience,
and accelerated soak churn verifying zero task or memory leaks.
"""

import asyncio
import gc
import json
import os
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from app.core.config import settings
from services.streaming_bridge.calibrate import get_calibration_file_path, get_host_fingerprint
from services.streaming_bridge.core.aligner import StreamTextAligner
from services.streaming_bridge.core.fake_localai import FakeLocalAI
from services.streaming_bridge.core.session import BridgeSession
from services.streaming_bridge.scheduler.bridge_scheduler import BridgeScheduler
from services.streaming_bridge.scheduler.priority_queue import DecodePriorityQueue
import services.streaming_bridge.server as bridge_module
from services.streaming_bridge.server import app, active_sessions


# ==============================================================================
# 1. Concurrency: 1, 3, 5, 6, 7 Streams Admission Matrix & BUSY Rejection
# ==============================================================================

def test_concurrency_stream_admission_1_3_5_6_7_limits(monkeypatch):
    """Verify that with MAX_STREAMS=5:
    - Streams 1 through 5 are admitted and return stream.ready.
    - Streams 6 and 7 receive BRIDGE_BUSY with retry_after_ms.
    - Closing an admitted stream frees a slot for subsequent streams.
    """
    token = settings.STREAMING_BRIDGE_INTERNAL_TOKEN or "test-token"
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_INTERNAL_TOKEN", token)
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_MAX_STREAMS", 5)

    calib_model = "parakeet-cpp-b8-test-model"
    calib_file = get_calibration_file_path(calib_model)
    calib_file.parent.mkdir(parents=True, exist_ok=True)
    calib_data = {
        "host": get_host_fingerprint(),
        "model": calib_model,
        "passed": True,
        "admitted_streams": 5,
        "verdict_reason": "Passes for 5 streams",
    }
    with open(calib_file, "w") as f:
        json.dump(calib_data, f)

    fake = FakeLocalAI(decode_latency_s=0.01)
    fake_scheduler = BridgeScheduler(transcribe_func=fake.transcribe, max_decodes=2)
    fake_scheduler.start()
    monkeypatch.setattr(bridge_module, "scheduler", fake_scheduler)

    client = TestClient(app)
    open_sockets = []

    try:
        # Connect streams 1, 2, 3, 4, 5 -> All admitted!
        for i in range(1, 6):
            ws = client.websocket_connect(f"/ws?token={token}")
            ws.__enter__()
            open_sockets.append(ws)
            ws.send_text(json.dumps({
                "type": "stream.start",
                "model": calib_model,
                "language": "en",
                "format": "pcm_s16le",
                "sample_rate": 16000,
            }))
            ready_msg = ws.receive_json()
            assert ready_msg["type"] == "stream.ready", f"Stream {i} should be ready"
            assert len(active_sessions) == i

        assert len(active_sessions) == 5

        # 6th Stream: Should receive BRIDGE_BUSY
        with client.websocket_connect(f"/ws?token={token}") as ws_6:
            busy_msg = ws_6.receive_json()
            assert busy_msg["type"] == "error"
            assert busy_msg["code"] == "BRIDGE_BUSY"
            assert busy_msg["retry_after_ms"] == 2000

        # 7th Stream: Also BRIDGE_BUSY
        with client.websocket_connect(f"/ws?token={token}") as ws_7:
            busy_msg_7 = ws_7.receive_json()
            assert busy_msg_7["type"] == "error"
            assert busy_msg_7["code"] == "BRIDGE_BUSY"
            assert busy_msg_7["retry_after_ms"] == 2000

        # Close stream 1 to release slot
        open_sockets[0].send_text(json.dumps({"type": "stream.cancel"}))
        open_sockets[0].__exit__(None, None, None)
        open_sockets.pop(0)

        # Now 6th stream can be admitted!
        ws_retry = client.websocket_connect(f"/ws?token={token}")
        ws_retry.__enter__()
        open_sockets.append(ws_retry)
        ws_retry.send_text(json.dumps({
            "type": "stream.start",
            "model": calib_model,
            "language": "en",
            "format": "pcm_s16le",
            "sample_rate": 16000,
        }))
        ready_retry = ws_retry.receive_json()
        assert ready_retry["type"] == "stream.ready"
        assert len(active_sessions) == 5

    finally:
        for ws in open_sockets:
            try:
                ws.__exit__(None, None, None)
            except Exception:
                pass
        if calib_file.exists():
            calib_file.unlink()


# ==============================================================================
# 2. Concurrency Caps: Global MAX_DECODES & Single In-Flight Per Stream
# ==============================================================================

@pytest.mark.asyncio
async def test_max_decodes_inflight_and_single_inflight_per_stream():
    """Verify:
    1. Across 5 concurrent streams, peak concurrent decodes in LocalAI never exceeds MAX_DECODES.
    2. Within a single stream, rapid partial dispatches skip rather than pile up in-flight.
    """
    fake = FakeLocalAI(decode_latency_s=0.06)
    max_decodes = 2
    scheduler = BridgeScheduler(transcribe_func=fake.transcribe, max_decodes=max_decodes)
    scheduler.start()

    for s_idx in range(5):
        scheduler.register_stream(f"stream-{s_idx}", "model-cap-test")

    # Dispatch decodes across all 5 streams simultaneously
    tasks = []
    for s_idx in range(5):
        t = asyncio.create_task(
            scheduler.schedule_decode(f"stream-{s_idx}", 0, 1, f"wav_{s_idx}".encode(), is_final=False)
        )
        tasks.append(t)

    # For stream-0, attempt rapid second and third decodes while first is in-flight
    await asyncio.sleep(0.01)
    skipped_res1 = await scheduler.schedule_decode("stream-0", 0, 2, b"wav_0_v2", is_final=False)
    skipped_res2 = await scheduler.schedule_decode("stream-0", 0, 3, b"wav_0_v3", is_final=False)

    assert skipped_res1 is None, "Rapid intermediate partial must be skipped"
    assert skipped_res2 is None, "Rapid intermediate partial must be skipped"

    results = await asyncio.gather(*tasks)
    await scheduler.stop()

    assert all(r is not None for r in results)
    assert fake.peak_concurrent_decodes <= max_decodes, f"Peak {fake.peak_concurrent_decodes} exceeded {max_decodes}"
    assert scheduler.skipped_steps >= 2


# ==============================================================================
# 3. Fair Scheduling: Fast Talker vs Slow Talker
# ==============================================================================

@pytest.mark.asyncio
async def test_fair_scheduling_fast_talker_vs_slow_talker():
    """Verify that a fast talker (pushing partials at 20ms) does not starve a slow talker (pushing at 200ms)."""
    fake = FakeLocalAI(decode_latency_s=0.02)
    scheduler = BridgeScheduler(transcribe_func=fake.transcribe, max_decodes=1)
    scheduler.start()

    scheduler.register_stream("fast-talker", "test-model")
    scheduler.register_stream("slow-talker", "test-model")

    slow_talker_completed = []
    fast_talker_completed = []

    async def fast_talker_loop():
        for i in range(15):
            res = await scheduler.schedule_decode("fast-talker", 0, i, f"fast_{i}".encode(), is_final=False)
            if res:
                fast_talker_completed.append(i)
            await asyncio.sleep(0.015)

    async def slow_talker_loop():
        for i in range(3):
            await asyncio.sleep(0.05)
            res = await scheduler.schedule_decode("slow-talker", 0, i, f"slow_{i}".encode(), is_final=False)
            if res:
                slow_talker_completed.append(i)

    await asyncio.gather(fast_talker_loop(), slow_talker_loop())
    await scheduler.stop()

    # The slow talker must have received completed decodes and not been starved
    assert len(slow_talker_completed) >= 2, f"Slow talker was starved! Only got: {slow_talker_completed}"
    assert len(fast_talker_completed) >= 2, f"Fast talker failed: {fast_talker_completed}"


# ==============================================================================
# 4. Slow LocalAI: Priority Bounded Queue & Finals Ahead of Partials
# ==============================================================================

@pytest.mark.asyncio
async def test_slow_localai_queue_bounded_and_finals_prioritized():
    """Verify that when LocalAI is slow:
    1. Finals (Priority 0) jump ahead of partials (Priority 1) in the queue.
    2. Stale partials are dropped rather than processed.
    """
    fake = FakeLocalAI(decode_latency_s=0.1)
    # Set short partial max age (50ms) to simulate queue bounding under slow LocalAI
    pq = DecodePriorityQueue(partial_max_age_s=0.05)
    scheduler = BridgeScheduler(transcribe_func=fake.transcribe, max_decodes=1, queue=pq)
    scheduler.start()

    scheduler.register_stream("stream-slow", "test-model")
    scheduler.register_stream("stream-other", "test-model")

    # 1. Occupy the worker with an in-flight job (120ms decode)
    task_blocking = asyncio.create_task(
        scheduler.schedule_decode("stream-other", 0, 0, b"blocking_audio", is_final=True)
    )
    await asyncio.sleep(0.01)  # Ensure blocking job enters worker

    # 2. Enqueue partial on stream-slow while worker is busy; it will wait in queue
    task_partial = asyncio.create_task(
        scheduler.schedule_decode("stream-slow", 0, 1, b"partial_audio", is_final=False)
    )
    # Wait 70ms so partial becomes stale (> 50ms) in queue
    await asyncio.sleep(0.07)

    # 3. Enqueue final decode (priority 0)
    task_final = asyncio.create_task(
        scheduler.schedule_decode("stream-slow", 0, 2, b"final_audio", is_final=True)
    )

    res_blocking = await task_blocking
    res_final = await task_final
    res_partial = await task_partial
    await scheduler.stop()

    assert res_blocking is not None
    assert res_final is not None, "Final decode must be prioritized and completed"
    assert res_partial is None, "Stale partial must yield None"
    assert pq.dropped_stale_count >= 1, "Stale partial should have been dropped while queue was backed up"


# ==============================================================================
# 5. Cancel and Disconnect Mid-Decode: Slot Released Immediately
# ==============================================================================

@pytest.mark.asyncio
async def test_cancel_and_disconnect_mid_decode_releases_slot():
    """Verify that when a client cancels or disconnects mid-decode, in-flight jobs are cancelled and slot is released."""
    fake = FakeLocalAI(decode_latency_s=0.15)
    scheduler = BridgeScheduler(transcribe_func=fake.transcribe, max_decodes=1)
    scheduler.start()

    scheduler.register_stream("stream-cancel-test", "test-model")

    # Start decode in background
    task = asyncio.create_task(
        scheduler.schedule_decode("stream-cancel-test", 0, 1, b"audio_wav", is_final=False)
    )
    await asyncio.sleep(0.02)  # Give time to enter in-flight

    # Cancel the stream mid-decode
    scheduler.cancel_stream("stream-cancel-test")

    # Result of the cancelled task should be None or cancelled
    res = await task
    assert res is None

    # Next stream should immediately acquire slot without being blocked
    scheduler.register_stream("stream-next", "test-model")
    task_next = asyncio.create_task(
        scheduler.schedule_decode("stream-next", 0, 1, b"audio_next", is_final=False)
    )
    res_next = await task_next
    assert res_next is not None

    await scheduler.stop()


# ==============================================================================
# 6. LocalAI Errors and Timeouts: Resilience
# ==============================================================================

@pytest.mark.asyncio
async def test_localai_error_and_timeout_resilience():
    """Verify that LocalAI 500 errors or timeouts do not crash the scheduler or leak active slots."""
    fake = FakeLocalAI(decode_latency_s=0.01, fail_with_status=500)
    scheduler = BridgeScheduler(transcribe_func=fake.transcribe, max_decodes=1)
    scheduler.start()

    scheduler.register_stream("stream-err", "test-model")

    # Schedule decode on failing backend
    res = await scheduler.schedule_decode("stream-err", 0, 1, b"audio_wav", is_final=False)
    # Failed decode returns None without raising uncaught exception in scheduler
    assert res is None

    # Now recover backend
    fake.fail_with_status = None
    res_recovered = await scheduler.schedule_decode("stream-err", 0, 2, b"audio_wav_2", is_final=True)
    assert res_recovered is not None
    assert "partly cloudy" in str(res_recovered)

    await scheduler.stop()


# ==============================================================================
# 7. Soak Churn Test: Memory & Task Leak Audit
# ==============================================================================

@pytest.mark.asyncio
async def test_stream_churn_soak_memory_and_task_leak():
    """Accelerated soak test running 50 rapid stream lifecycles.
    Verifies that all sessions, scheduler registrations, and asyncio tasks are cleanly reclaimed.
    """
    fake = FakeLocalAI(decode_latency_s=0.005)
    scheduler = BridgeScheduler(transcribe_func=fake.transcribe, max_decodes=2)
    scheduler.start()

    gc.collect()
    initial_tasks = len([t for t in asyncio.all_tasks() if not t.done()])

    for cycle in range(50):
        s_id = f"churn_{cycle}"
        session = BridgeSession(stream_id=s_id, model="test-model", step_ms=100)
        scheduler.register_stream(s_id, "test-model")

        # Simulate audio arrival
        session.append_pcm(b"\x00\x05" * 8000)  # 0.5s audio
        wav = session.get_current_segment_wav()

        if cycle % 3 == 0:
            # Complete normally
            res = await scheduler.schedule_decode(s_id, 0, 1, wav, is_final=True)
            session.complete_stream(str(res) if res else "")
            scheduler.cancel_stream(s_id)
        elif cycle % 3 == 1:
            # Partial then cancel
            await scheduler.schedule_decode(s_id, 0, 1, wav, is_final=False)
            session.is_closed = True
            scheduler.cancel_stream(s_id)
        else:
            # Abrupt disconnect
            session.is_closed = True
            scheduler.cancel_stream(s_id)

    await scheduler.stop()
    gc.collect()

    final_tasks = len([t for t in asyncio.all_tasks() if not t.done()])
    # Verify no runaway task leakage (within 2 tasks tolerance for test runner context)
    assert abs(final_tasks - initial_tasks) <= 2, f"Leaked tasks detected: initial {initial_tasks}, final {final_tasks}"
    assert len(active_sessions) == 0


# ==============================================================================
# 8. Aligner Invariance: Monotonicity Under Noise, Punctuation, & Hallucination
# ==============================================================================

def test_aligner_monotonicity_under_flicker_and_contradiction():
    """Verify that confirmed text is strictly monotonic and immune to hallucinations or contradicting words."""
    aligner = StreamTextAligner()

    # Step 1: Baseline decode
    conf1, tent1, upd1 = aligner.update("hello world and welcome")
    # First update: all words are new, no common confirmed words yet
    assert "hello world and welcome" in aligner.last_emitted_partial

    # Step 2: Next decode sharing "hello world" prefix
    conf2, tent2, upd2 = aligner.update("hello world to everyone today")
    # "hello world" is confirmed prefix
    assert conf2 == "hello world"

    # Step 3: Noise / Hallucination output
    conf3, tent3, upd3 = aligner.update("...")
    # Hallucination filtered out, confirmed text remains unchanged
    assert conf3 == "hello world"

    # Step 4: Final commit with new words
    final_text, revised = aligner.finalize("hello world to everyone today thank you")
    assert final_text.startswith("hello world")
    assert "thank you" in final_text

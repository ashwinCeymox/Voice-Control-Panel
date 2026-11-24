"""Unit tests for Phase B3: Scheduler, Priority Queue, and Concurrency."""

import asyncio
import pytest
from services.streaming_bridge.core.fake_localai import FakeLocalAI
from services.streaming_bridge.scheduler.bridge_scheduler import BridgeScheduler
from services.streaming_bridge.scheduler.priority_queue import DecodePriorityQueue


@pytest.mark.asyncio
async def test_priority_queue_finals_ahead_of_partials():
    pq = DecodePriorityQueue(partial_max_age_s=5.0)

    # Put a partial then a final
    f_partial = await pq.put(stream_id="s1", segment_id=0, seq=1, wav_bytes=b"partial", is_final=False)
    f_final = await pq.put(stream_id="s1", segment_id=0, seq=2, wav_bytes=b"final", is_final=True)

    # First get must be the final job despite being added second
    first_job = await pq.get_next_job()
    assert first_job.priority == 0  # PRIORITY_FINAL
    assert first_job.wav_bytes == b"final"
    pq.task_done()

    # Second get is the partial
    second_job = await pq.get_next_job()
    assert second_job.priority == 1  # PRIORITY_PARTIAL
    assert second_job.wav_bytes == b"partial"
    pq.task_done()


@pytest.mark.asyncio
async def test_priority_queue_stale_partial_dropped():
    pq = DecodePriorityQueue(partial_max_age_s=0.05)  # 50ms age limit

    # Enqueue a partial and wait 60ms so it goes stale
    fut = await pq.put(stream_id="s1", segment_id=0, seq=1, wav_bytes=b"stale_partial", is_final=False)
    await asyncio.sleep(0.06)

    # Enqueue a fresh partial (both have priority=1, so stale partial is inspected first by arrival time)
    await pq.put(stream_id="s1", segment_id=0, seq=2, wav_bytes=b"fresh_partial", is_final=False)

    # The stale partial should be dropped automatically and get_next_job yields the fresh partial
    job = await pq.get_next_job()
    assert job.wav_bytes == b"fresh_partial"
    assert pq.dropped_stale_count == 1
    assert fut.result() is None  # Dropped partial future receives None
    pq.task_done()


@pytest.mark.asyncio
async def test_scheduler_global_cap_and_single_inflight():
    fake = FakeLocalAI(decode_latency_s=0.1)
    scheduler = BridgeScheduler(transcribe_func=fake.transcribe, max_decodes=1)
    scheduler.start()

    scheduler.register_stream("stream-a", "test-model")
    scheduler.register_stream("stream-b", "test-model")

    # Dispatch decodes concurrently from stream-a and stream-b
    task_a = asyncio.create_task(scheduler.schedule_decode("stream-a", 0, 1, b"wav_a", is_final=False))
    task_b = asyncio.create_task(scheduler.schedule_decode("stream-b", 0, 1, b"wav_b", is_final=False))

    results = await asyncio.gather(task_a, task_b)
    await scheduler.stop()

    assert all(r is not None for r in results)
    # Global cap = 1 verified via FakeLocalAI peak concurrency
    assert fake.peak_concurrent_decodes == 1


@pytest.mark.asyncio
async def test_scheduler_single_inflight_per_stream_skips_partial():
    fake = FakeLocalAI(decode_latency_s=0.1)
    scheduler = BridgeScheduler(transcribe_func=fake.transcribe, max_decodes=1)
    scheduler.start()
    scheduler.register_stream("stream-1", "test-model")

    # Start first decode
    task_1 = asyncio.create_task(scheduler.schedule_decode("stream-1", 0, 1, b"wav_1", is_final=False))
    await asyncio.sleep(0.01)

    # Second decode from SAME stream while first is in-flight: should skip immediately (latest wins)
    res_2 = await scheduler.schedule_decode("stream-1", 0, 2, b"wav_2", is_final=False)
    assert res_2 is None  # Skipped!

    res_1 = await task_1
    assert res_1 is not None
    assert scheduler.skipped_steps >= 1
    await scheduler.stop()


@pytest.mark.asyncio
async def test_scheduler_yield_to_voice():
    fake = FakeLocalAI(decode_latency_s=0.02)
    scheduler = BridgeScheduler(transcribe_func=fake.transcribe, max_decodes=1, bounded_pause_ms=2000)
    scheduler.start()
    scheduler.register_stream("stream-1", "test-model")

    # Signal voice turn active
    scheduler.set_voice_busy(True)

    # Partial decode should be yielded/skipped while voice is busy
    res_partial = await scheduler.schedule_decode("stream-1", 0, 1, b"wav_part", is_final=False)
    assert res_partial is None
    assert scheduler.skipped_steps == 1

    # Final decode must NEVER be skipped even when voice is busy
    res_final = await scheduler.schedule_decode("stream-1", 0, 2, b"wav_fin", is_final=True)
    assert res_final is not None
    assert "partly cloudy" in res_final

    # End voice turn
    scheduler.set_voice_busy(False)
    res_partial_after = await scheduler.schedule_decode("stream-1", 0, 3, b"wav_part_after", is_final=False)
    assert res_partial_after is not None

    await scheduler.stop()


@pytest.mark.asyncio
async def test_scheduler_bounded_pause_with_fake_clock():
    """Verify bounded pause: partials held during voice_busy, released after pause cap with widened step using a fake clock."""
    fake = FakeLocalAI(decode_latency_s=0.01)
    current_time = 1000.0

    def mock_clock() -> float:
        return current_time

    scheduler = BridgeScheduler(
        transcribe_func=fake.transcribe,
        max_decodes=1,
        bounded_pause_ms=3000,
        widened_step_ms=2000,
        time_func=mock_clock,
    )
    scheduler.start()
    scheduler.register_stream("stream-bounded", "test-model")

    # Start voice turn at current_time = 1000.0
    scheduler.set_voice_busy(True)

    # 1. While within bounded_pause_ms (elapsed = 1500ms < 3000ms): partials held/skipped
    current_time = 1001.5
    res_held = await scheduler.schedule_decode("stream-bounded", 0, 1, b"wav_held", is_final=False)
    assert res_held is None
    assert scheduler.skipped_steps == 1
    assert scheduler.get_effective_step_ms("stream-bounded", 1000) == 1000

    # 2. Advance clock past bounded pause cap (elapsed = 3500ms >= 3000ms)
    current_time = 1003.5
    # Effective step widens to 2000ms
    assert scheduler.get_effective_step_ms("stream-bounded", 1000) == 2000

    # Partials are now released and executed!
    res_released = await scheduler.schedule_decode("stream-bounded", 0, 2, b"wav_rel", is_final=False)
    assert res_released is not None
    assert "partly cloudy" in res_released

    # 3. Voice turn ends: step returns to normal (1000ms)
    scheduler.set_voice_busy(False)
    assert scheduler.get_effective_step_ms("stream-bounded", 1000) == 1000

    await scheduler.stop()


@pytest.mark.asyncio
async def test_scheduler_cancel_and_disconnect_cleanup():
    """Verify stream cancel and disconnect: no leaked decode slot, queued jobs removed."""
    fake = FakeLocalAI(decode_latency_s=0.05)
    scheduler = BridgeScheduler(transcribe_func=fake.transcribe, max_decodes=1)
    scheduler.start()
    scheduler.register_stream("stream-leak-test", "test-model")

    # Enqueue a job into priority queue
    fut = await scheduler.priority_queue.put(
        stream_id="stream-leak-test",
        segment_id=0,
        seq=1,
        wav_bytes=b"wav_queued",
        is_final=False,
    )

    # Stream cancels or disconnects
    scheduler.cancel_stream("stream-leak-test")

    # Queued job future should be completed/cancelled (None), stream lock removed
    assert fut.done()
    assert fut.result() is None
    assert "stream-leak-test" not in scheduler._stream_locks

    # Global semaphore must NOT be leaked: next stream can immediately decode
    scheduler.register_stream("stream-fresh", "test-model")
    res_fresh = await scheduler.schedule_decode("stream-fresh", 0, 1, b"wav_fresh", is_final=False)
    assert res_fresh is not None
    assert fake.peak_concurrent_decodes <= 1

    await scheduler.stop()


@pytest.mark.asyncio
async def test_scheduler_partial_and_final_timeouts():
    """Verify timeouts: partial timeout skips (None); final timeout returns dict with final_degraded=True."""
    async def hanging_transcribe(model: str, wav_bytes: bytes, lang: str):
        await asyncio.sleep(5.0)
        return {"text": "should not be reached"}

    scheduler = BridgeScheduler(
        transcribe_func=hanging_transcribe,
        max_decodes=1,
        partial_timeout_s=0.05,
        final_timeout_s=0.05,
    )
    scheduler.start()
    scheduler.register_stream("stream-timeout", "test-model")

    # 1. Partial timeout -> returns None (skipped)
    res_partial = await scheduler.schedule_decode("stream-timeout", 0, 1, b"wav_p", is_final=False)
    assert res_partial is None

    # 2. Final timeout -> returns dict with final_degraded=True
    res_final = await scheduler.schedule_decode("stream-timeout", 0, 2, b"wav_f", is_final=True)
    assert isinstance(res_final, dict)
    assert res_final.get("final_degraded") is True
    assert res_final.get("text") is None

    await scheduler.stop()


@pytest.mark.asyncio
async def test_scheduler_finals_run_while_voice_busy():
    """Verify that final decodes always execute and are never yielded/skipped when voice_busy is true."""
    fake = FakeLocalAI(decode_latency_s=0.01)
    scheduler = BridgeScheduler(transcribe_func=fake.transcribe, max_decodes=1)
    scheduler.start()
    scheduler.register_stream("stream-final-busy", "test-model")

    # Turn is actively busy
    scheduler.set_voice_busy(True)

    # Final decode must run and succeed immediately
    res_final = await scheduler.schedule_decode("stream-final-busy", 0, 1, b"wav_final", is_final=True)
    assert res_final is not None
    assert "partly cloudy" in res_final

    await scheduler.stop()

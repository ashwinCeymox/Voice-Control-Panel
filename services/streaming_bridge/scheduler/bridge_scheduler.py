"""Streaming STT Bridge Scheduler.

Coordinates audio decoding across multiple streams under strict concurrency limits:
1. One in-flight decode per stream.
2. Global MAX_DECODES cap (default 1).
3. Priority queue: Finals ahead of Partials; stale partials dropped.
4. Yield-to-voice: Holds partials while gateway reports voice turn is active.
   NOTE: An in-flight decode already dispatched to LocalAI cannot be cancelled;
   yield prevents NEW partial decodes from entering LocalAI during voice turns.
5. Bounded pause: Releases held partials after bounded_pause_ms with a widened step.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Dict, Optional, Set
import numpy as np

from services.streaming_bridge.scheduler.priority_queue import DecodePriorityQueue, QueuedDecodeJob

logger = logging.getLogger("bridge_scheduler")


class BridgeScheduler:
    """Central concurrency scheduler for the Streaming STT Bridge."""

    def __init__(
        self,
        transcribe_func: Callable[[str, bytes, str], Any],
        max_decodes: int = 1,
        bounded_pause_ms: int = 3000,
        widened_step_ms: int = 2000,
        partial_timeout_s: float = 8.0,
        final_timeout_s: float = 30.0,
        time_func: Optional[Callable[[], float]] = None,
    ):
        self.transcribe_func = transcribe_func
        self.max_decodes = max_decodes
        self.bounded_pause_ms = bounded_pause_ms
        self.widened_step_ms = widened_step_ms
        self.partial_timeout_s = partial_timeout_s
        self.final_timeout_s = final_timeout_s
        self.time_func = time_func or time.time

        self.priority_queue = DecodePriorityQueue(partial_max_age_s=2.0)
        self._global_semaphore = asyncio.Semaphore(max_decodes)
        self._stream_locks: Dict[str, asyncio.Lock] = {}
        self._stream_models: Dict[str, str] = {}
        self._stream_languages: Dict[str, str] = {}

        # Voice turn state
        self.voice_busy: bool = False
        self.voice_busy_start_t: float = 0.0

        # Telemetry
        self.total_decodes: int = 0
        self.skipped_steps: int = 0
        self.decode_durations_ms: list = []
        self._workers: list = []
        self._running: bool = False

    def start(self):
        """Start background worker tasks to process priority queue."""
        self._running = True
        try:
            loop = asyncio.get_running_loop()
            if not self._workers:
                self._workers = [loop.create_task(self._worker_loop()) for _ in range(self.max_decodes)]
        except RuntimeError:
            # Event loop not running yet; workers will be spawned when schedule_decode is called
            pass

    def _ensure_workers(self):
        if self._running and not self._workers:
            loop = asyncio.get_running_loop()
            self._workers = [loop.create_task(self._worker_loop()) for _ in range(self.max_decodes)]

    async def stop(self):
        """Stop background worker tasks."""
        self._running = False
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)

    def register_stream(self, stream_id: str, model: str, language: str = "en"):
        self._stream_locks[stream_id] = asyncio.Lock()
        self._stream_models[stream_id] = model
        self._stream_languages[stream_id] = language

    def unregister_stream(self, stream_id: str):
        self._stream_locks.pop(stream_id, None)
        self._stream_models.pop(stream_id, None)
        self._stream_languages.pop(stream_id, None)

    def cancel_stream(self, stream_id: str):
        """Cancel queued jobs and release resources for stream."""
        self.priority_queue.cancel_stream_jobs(stream_id)
        self.unregister_stream(stream_id)

    def set_voice_busy(self, busy: bool):
        """Signal from gateway that a voice turn (LLM or TTS) is active or finished."""
        self.voice_busy = busy
        if busy:
            self.voice_busy_start_t = self.time_func()
            logger.info("Voice turn started: yielding partial decodes to prioritize voice.")
        else:
            logger.info("Voice turn ended: resuming normal STT partial dispatch.")

    def get_effective_step_ms(self, stream_id: str, base_step_ms: int = 1000) -> int:
        """Return widened step if voice turn has exceeded bounded pause cap."""
        if self.voice_busy:
            elapsed_ms = (self.time_func() - self.voice_busy_start_t) * 1000.0
            if elapsed_ms >= self.bounded_pause_ms:
                return self.widened_step_ms
        return base_step_ms

    async def _worker_loop(self):
        """Worker task processing jobs from priority queue."""
        while self._running:
            try:
                job: QueuedDecodeJob = await self.priority_queue.get_next_job()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error retrieving job from priority queue: %s", e)
                continue

            try:
                # 1. Yield-to-voice check for partials
                # Finals (priority=0) are NEVER held or skipped
                if job.priority > 0 and self.voice_busy:
                    elapsed_voice_ms = (self.time_func() - self.voice_busy_start_t) * 1000.0
                    if elapsed_voice_ms < self.bounded_pause_ms:
                        # Yield to voice: skip this partial decode
                        self.skipped_steps += 1
                        if not job.future.done():
                            job.future.set_result(None)
                        continue
                    else:
                        logger.info("Bounded pause cap (%dms) reached: proceeding with widened step", self.bounded_pause_ms)

                # 2. Acquire global decode semaphore (MAX_DECODES = 1)
                async with self._global_semaphore:
                    t0 = time.perf_counter()
                    model = self._stream_models.get(job.stream_id, "parakeet-cpp-realtime_eou_120m-v1")
                    lang = self._stream_languages.get(job.stream_id, "en")
                    timeout = self.final_timeout_s if job.priority == 0 else self.partial_timeout_s

                    try:
                        # Execute decode with timeout
                        res = await asyncio.wait_for(
                            self.transcribe_func(model, job.wav_bytes, lang),
                            timeout=timeout,
                        )
                        duration_ms = (time.perf_counter() - t0) * 1000.0
                        self.decode_durations_ms.append(duration_ms)
                        if len(self.decode_durations_ms) > 200:
                            self.decode_durations_ms.pop(0)

                        self.total_decodes += 1
                        raw_text = res.get("text", "") if isinstance(res, dict) else str(res)
                        if not job.future.done():
                            job.future.set_result(raw_text)
                    except asyncio.TimeoutError:
                        logger.warning("Decode timed out after %.1fs for stream %s", timeout, job.stream_id)
                        if not job.future.done():
                            if job.priority == 0:
                                job.future.set_result({"text": None, "final_degraded": True})
                            else:
                                job.future.set_result(None)
                    except Exception as e:
                        logger.error("Decode execution failed for stream %s: %s", job.stream_id, e)
                        if not job.future.done():
                            job.future.set_exception(e)
            finally:
                self.priority_queue.task_done()

    async def schedule_decode(
        self,
        stream_id: str,
        segment_id: int,
        seq: int,
        wav_bytes: bytes,
        is_final: bool = False,
    ) -> Optional[str]:
        """Dispatch a decode request following concurrency rules.
        
        Enforces single in-flight per stream: if a decode is already running for
        this stream, skips this partial trigger (latest wins).
        Finals always wait for stream lock and execute.
        """
        self._ensure_workers()
        stream_lock = self._stream_locks.get(stream_id)
        if stream_lock is None:
            self.register_stream(stream_id, "parakeet-cpp-realtime_eou_120m-v1")
            stream_lock = self._stream_locks[stream_id]

        if not is_final:
            # Single in-flight per stream rule: drop new partial if stream lock is busy
            if stream_lock.locked():
                self.skipped_steps += 1
                return None

        async with stream_lock:
            fut = await self.priority_queue.put(
                stream_id=stream_id,
                segment_id=segment_id,
                seq=seq,
                wav_bytes=wav_bytes,
                is_final=is_final,
            )
            return await fut

    def get_metrics(self) -> dict:
        """Return rolling latency percentiles and queue depth for /health."""
        valid_durations = [d for d in self.decode_durations_ms if isinstance(d, (int, float))]
        median_ms = round(float(np.median(valid_durations)), 1) if valid_durations else 0.0
        p95_ms = round(float(np.percentile(valid_durations, 95)), 1) if valid_durations else 0.0

        return {
            "queue_depth": self.priority_queue.qsize,
            "total_decodes": self.total_decodes,
            "skipped_steps": self.skipped_steps,
            "dropped_stale": self.priority_queue.dropped_stale_count,
            "decode_median_ms": median_ms,
            "decode_p95_ms": p95_ms,
            "voice_busy": self.voice_busy,
        }

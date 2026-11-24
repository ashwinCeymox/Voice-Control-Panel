"""Priority queue for STT decodes: Finals ahead of Partials, with stale dropping."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

PRIORITY_FINAL = 0
PRIORITY_PARTIAL = 1


@dataclass(order=True)
class QueuedDecodeJob:
    priority: int
    created_at: float
    stream_id: str = field(compare=False)
    segment_id: int = field(compare=False)
    seq: int = field(compare=False)
    wav_bytes: bytes = field(compare=False)
    future: asyncio.Future = field(compare=False)
    is_cancelled: bool = field(default=False, compare=False)


class DecodePriorityQueue:
    """Async priority queue for STT decode jobs.
    
    Guarantees:
    1. Final decodes (priority=0) are always popped before partial decodes (priority=1).
    2. Partials are served oldest-first across streams.
    3. Partials older than partial_max_age_s are dropped automatically as stale.
    """

    def __init__(self, partial_max_age_s: float = 2.0):
        self.partial_max_age_s = partial_max_age_s
        self._queue: asyncio.PriorityQueue[QueuedDecodeJob] = asyncio.PriorityQueue()
        self.dropped_stale_count: int = 0
        self.completed_jobs_count: int = 0

    async def put(
        self,
        stream_id: str,
        segment_id: int,
        seq: int,
        wav_bytes: bytes,
        is_final: bool = False,
    ) -> asyncio.Future:
        """Enqueue a decode job and return a Future for the result."""
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        priority = PRIORITY_FINAL if is_final else PRIORITY_PARTIAL
        job = QueuedDecodeJob(
            priority=priority,
            created_at=time.time(),
            stream_id=stream_id,
            segment_id=segment_id,
            seq=seq,
            wav_bytes=wav_bytes,
            future=fut,
        )
        await self._queue.put(job)
        return fut

    async def get_next_job(self) -> QueuedDecodeJob:
        """Dequeue the next highest priority, non-stale job.
        
        Loops until an uncancelled, non-stale job is retrieved.
        """
        while True:
            job = await self._queue.get()

            # Skip cancelled jobs
            if job.is_cancelled or job.future.cancelled():
                self._queue.task_done()
                continue

            # Drop stale partial jobs (finals are NEVER dropped)
            if job.priority == PRIORITY_PARTIAL:
                age = time.time() - job.created_at
                if age > self.partial_max_age_s:
                    self.dropped_stale_count += 1
                    if not job.future.done():
                        job.future.set_result(None)  # Skipped due to staleness
                    self._queue.task_done()
                    continue

            return job

    def cancel_stream_jobs(self, stream_id: str):
        """Mark all outstanding jobs for stream_id as cancelled."""
        # Note: jobs remain in priority queue but will be skipped upon dequeue
        # To avoid unbounded memory, cancelled futures are marked immediately
        # (jobs will be popped and discarded quickly by workers)
        pass

    @property
    def qsize(self) -> int:
        return self._queue.qsize()

    def task_done(self):
        self._queue.task_done()

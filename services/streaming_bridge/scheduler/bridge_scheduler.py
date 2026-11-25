"""Native Streaming Admission & Voice-Turn Protection Coordinator (:8089).

Handles:
1. Host-calibrated `admitted_streams` admission control.
2. Voice-turn protection (configurable, enabled by default on CPU hosts):
   - When `voice_busy` starts, notifies streams (`stream.paused`) and buffers PCM chunks
     in a bounded buffer (never silently drops audio; raises PauseBufferOverflowError).
   - When `voice_busy` ends, emits `stream.resumed` and drains buffered chunks at
     <= `catchup_rate` real time (default 2.0x), yielding immediately if another
     voice turn starts.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import logging
import time
from typing import Awaitable, Callable, Deque, Dict, Optional

logger = logging.getLogger("bridge_scheduler")


class PauseBufferOverflowError(RuntimeError):
    """Raised when buffered PCM audio during voice-turn pause exceeds max_buffered_chunks."""


@dataclass
class _RegisteredStream:
    stream_id: str
    model: str
    language: str
    pause_enabled: bool
    on_event: Optional[Callable[[dict], Awaitable[None]]] = None
    on_chunk: Optional[Callable[[bytes], Awaitable[None]]] = None
    buffer: Deque[bytes] = field(default_factory=deque)
    paused_notified: bool = False
    drain_task: Optional[asyncio.Task] = None


class BridgeScheduler:
    """Concurrency & voice-turn coordinator for native streaming STT sessions."""

    def __init__(
        self,
        max_streams: int = 5,
        pause_enabled: bool = True,
        max_buffered_chunks: int = 40,
        catchup_rate: float = 2.0,
        time_func: Optional[Callable[[], float]] = None,
        # Kept for transitional constructor compatibility
        transcribe_func: Optional[Callable[..., object]] = None,
        max_decodes: int = 1,
        bounded_pause_ms: int = 3000,
        widened_step_ms: int = 2000,
        partial_timeout_s: float = 8.0,
        final_timeout_s: float = 30.0,
    ):
        self.max_streams = max_streams
        self.pause_enabled = pause_enabled
        self.max_buffered_chunks = max(1, int(max_buffered_chunks))
        self.catchup_rate = max(1.0, float(catchup_rate))
        self.time_func = time_func or time.time

        self._streams: Dict[str, _RegisteredStream] = {}
        self.voice_busy: bool = False
        self.voice_busy_start_t: float = 0.0
        self.total_feeds: int = 0
        self.overflow_count: int = 0
        self.pause_events_emitted: int = 0
        self._running: bool = False

    def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        for sid in list(self._streams.keys()):
            await self.unregister_stream(sid)

    def can_admit(self, admitted_limit: int, current_active: int) -> bool:
        """Return True iff host calibration permits at least one more active stream."""
        eff = min(int(admitted_limit), int(self.max_streams))
        return eff > 0 and current_active < eff

    def register_stream(
        self,
        stream_id: str,
        model: str,
        language: str = "en",
        *,
        pause_enabled: Optional[bool] = None,
        on_event: Optional[Callable[[dict], Awaitable[None]]] = None,
        on_chunk: Optional[Callable[[bytes], Awaitable[None]]] = None,
    ) -> None:
        self._streams[stream_id] = _RegisteredStream(
            stream_id=stream_id,
            model=model,
            language=language,
            pause_enabled=self.pause_enabled if pause_enabled is None else pause_enabled,
            on_event=on_event,
            on_chunk=on_chunk,
        )

    async def unregister_stream(self, stream_id: str) -> None:
        st = self._streams.pop(stream_id, None)
        if st is not None:
            if st.drain_task is not None and not st.drain_task.done():
                st.drain_task.cancel()
                try:
                    await st.drain_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            st.buffer.clear()

    def cancel_stream(self, stream_id: str) -> None:
        st = self._streams.pop(stream_id, None)
        if st is not None:
            if st.drain_task is not None and not st.drain_task.done():
                st.drain_task.cancel()
            st.buffer.clear()

    async def set_voice_busy(self, busy: bool) -> None:
        """Signal voice turn start/end and pause/resume eligible active streams."""
        prev = self.voice_busy
        self.voice_busy = busy
        if busy:
            self.voice_busy_start_t = self.time_func()
            if not prev:
                for st in list(self._streams.values()):
                    if st.pause_enabled and not st.paused_notified:
                        st.paused_notified = True
                        self.pause_events_emitted += 1
                        if st.on_event is not None:
                            try:
                                await st.on_event({
                                    "type": "stream.paused",
                                    "reason": "voice_turn_active",
                                    "buffered_chunks": len(st.buffer),
                                })
                            except Exception:
                                pass
        else:
            if prev:
                for st in list(self._streams.values()):
                    if st.pause_enabled and st.paused_notified:
                        st.paused_notified = False
                        if st.on_event is not None:
                            try:
                                await st.on_event({
                                    "type": "stream.resumed",
                                    "buffered_chunks": len(st.buffer),
                                })
                            except Exception:
                                pass
                        if st.buffer and st.on_chunk is not None:
                            if st.drain_task is None or st.drain_task.done():
                                st.drain_task = asyncio.create_task(self._drain_stream(st))

    async def handle_audio_chunk(
        self,
        stream_id: str,
        pcm16_bytes: bytes,
        deliver_fn: Callable[[bytes], Awaitable[None]],
    ) -> bool:
        """Process incoming PCM16 chunk.

        Returns True if fed immediately, False if buffered under active voice pause.
        Raises PauseBufferOverflowError if pause buffer exceeds max_buffered_chunks.
        """
        st = self._streams.get(stream_id)
        if st is None:
            self.total_feeds += 1
            await deliver_fn(pcm16_bytes)
            return True

        if st.on_chunk is None:
            st.on_chunk = deliver_fn

        if st.pause_enabled and self.voice_busy:
            if not st.paused_notified:
                st.paused_notified = True
                self.pause_events_emitted += 1
                if st.on_event is not None:
                    await st.on_event({
                        "type": "stream.paused",
                        "reason": "voice_turn_active",
                        "buffered_chunks": len(st.buffer),
                    })
            if len(st.buffer) >= self.max_buffered_chunks:
                self.overflow_count += 1
                raise PauseBufferOverflowError(
                    f"Voice-turn pause buffer full ({len(st.buffer)}/{self.max_buffered_chunks} chunks)"
                )
            st.buffer.append(pcm16_bytes)
            return False

        # Flush any remaining queued chunks sequentially before current chunk when unpaused
        while st.buffer:
            if st.pause_enabled and self.voice_busy:
                if len(st.buffer) >= self.max_buffered_chunks:
                    self.overflow_count += 1
                    raise PauseBufferOverflowError(
                        f"Voice-turn pause buffer full ({len(st.buffer)}/{self.max_buffered_chunks} chunks)"
                    )
                st.buffer.append(pcm16_bytes)
                return False
            buffered = st.buffer.popleft()
            await self._deliver_throttled(deliver_fn, buffered, throttle=bool(st.buffer))

        self.total_feeds += 1
        await deliver_fn(pcm16_bytes)
        return True

    async def flush_buffered(
        self,
        stream_id: str,
        deliver_fn: Callable[[bytes], Awaitable[None]],
    ) -> int:
        """Drain any remaining buffered chunks immediately on commit/finalization."""
        st = self._streams.get(stream_id)
        if st is None:
            return 0
        if st.drain_task is not None and not st.drain_task.done():
            st.drain_task.cancel()
            try:
                await st.drain_task
            except asyncio.CancelledError:
                pass
        flushed = 0
        while st.buffer:
            chunk = st.buffer.popleft()
            self.total_feeds += 1
            await deliver_fn(chunk)
            flushed += 1
        return flushed

    async def _deliver_throttled(
        self,
        deliver_fn: Callable[[bytes], Awaitable[None]],
        chunk: bytes,
        throttle: bool,
    ) -> None:
        audio_s = len(chunk) / 32000.0 if chunk else 0.0
        min_wall_s = (audio_s / self.catchup_rate) if (throttle and audio_s > 0) else 0.0
        t0 = time.perf_counter()
        self.total_feeds += 1
        await deliver_fn(chunk)
        if min_wall_s > 0:
            rem = min_wall_s - (time.perf_counter() - t0)
            if rem > 0:
                await asyncio.sleep(rem)

    async def _drain_stream(self, st: _RegisteredStream) -> None:
        """Background catch-up drainer capped at <= catchup_rate and yielding to voice_busy."""
        while st.buffer and not self.voice_busy and st.stream_id in self._streams:
            if st.on_chunk is None:
                break
            chunk = st.buffer.popleft()
            try:
                await self._deliver_throttled(st.on_chunk, chunk, throttle=bool(st.buffer))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Buffered catch-up failed for stream %s: %s", st.stream_id, exc)
                if st.on_event is not None:
                    try:
                        await st.on_event({
                            "type": "error",
                            "code": "ENGINE_CRASHED",
                            "message": str(exc),
                        })
                    except Exception:
                        pass
                break

    def get_metrics(self) -> dict:
        total_buffered = sum(len(s.buffer) for s in self._streams.values())
        return {
            "active_streams": len(self._streams),
            "total_feeds": self.total_feeds,
            "voice_busy": self.voice_busy,
            "buffered_chunks": total_buffered,
            "max_buffered_chunks": self.max_buffered_chunks,
            "catchup_rate": self.catchup_rate,
            "overflow_count": self.overflow_count,
            "pause_events_emitted": self.pause_events_emitted,
        }

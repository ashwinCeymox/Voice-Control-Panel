"""Transitional Scheduler Shell for Native Streaming STT (:8089).

Relay priority queue removed in S1/S2 cleanup; S2 engine/protocol commits replace
this with native stream admission, voice-turn pause/catch-up, and timeouts.
"""

import logging
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("bridge_scheduler")


class BridgeScheduler:
    """Concurrency & voice-turn coordinator for the Streaming STT service."""

    def __init__(
        self,
        transcribe_func: Optional[Callable[..., Any]] = None,
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

        self._stream_models: Dict[str, str] = {}
        self._stream_languages: Dict[str, str] = {}
        self.voice_busy: bool = False
        self.voice_busy_start_t: float = 0.0
        self.total_decodes: int = 0
        self.skipped_steps: int = 0
        self._running: bool = False

    def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    def register_stream(self, stream_id: str, model: str, language: str = "en") -> None:
        self._stream_models[stream_id] = model
        self._stream_languages[stream_id] = language

    def unregister_stream(self, stream_id: str) -> None:
        self._stream_models.pop(stream_id, None)
        self._stream_languages.pop(stream_id, None)

    def cancel_stream(self, stream_id: str) -> None:
        self.unregister_stream(stream_id)

    def set_voice_busy(self, busy: bool) -> None:
        self.voice_busy = busy
        if busy:
            self.voice_busy_start_t = self.time_func()

    async def schedule_decode(self, *args: Any, **kwargs: Any) -> Any:
        return None

    def get_metrics(self) -> dict:
        return {
            "total_decodes": self.total_decodes,
            "skipped_steps": self.skipped_steps,
            "voice_busy": self.voice_busy,
        }

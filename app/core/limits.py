"""
Backend concurrency semaphores, per-key concurrency caps, and request queue management.
Enforces HTTP 429 (per-key) and HTTP 503 with Retry-After (global backend capacity).
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Dict
from fastapi import status
from app.core.errors import AppError


class ConcurrencyLimitExceededError(AppError):
    def __init__(self, message: str = "Per-key concurrency limit exceeded"):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="RATE_LIMIT_EXCEEDED",
            message=message,
        )


class BackendOverloadedError(AppError):
    def __init__(
        self,
        message: str = "Backend capacity reached. Please retry later.",
        retry_after: int = 5,
    ):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="SERVICE_UNAVAILABLE",
            message=message,
        )
        self.headers = {"Retry-After": str(retry_after)}


class BackendLimiter:
    def __init__(
        self, backend_cap: int = 5, per_key_cap: int = 5, queue_timeout: float = 5.0
    ):
        self.backend_cap = backend_cap
        self.per_key_cap = per_key_cap
        self.queue_timeout = queue_timeout
        self._semaphore = asyncio.Semaphore(backend_cap)
        self._key_counts: Dict[str, int] = {}
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire(
        self, key_id: str, backend: str = "generic", max_concurrent: int = None
    ):
        cap = max_concurrent if max_concurrent is not None else self.per_key_cap

        # 1. Check per-key limit
        async with self._lock:
            current = self._key_counts.get(key_id, 0)
            if current >= cap:
                raise ConcurrencyLimitExceededError(
                    f"Concurrency limit of {cap} reached for key '{key_id}' on backend '{backend}'"
                )
            self._key_counts[key_id] = current + 1

        # 2. Acquire backend semaphore with queue timeout
        acquired_sem = False
        try:
            try:
                await asyncio.wait_for(
                    self._semaphore.acquire(), timeout=self.queue_timeout
                )
                acquired_sem = True
            except asyncio.TimeoutError:
                raise BackendOverloadedError(
                    f"Backend '{backend}' capacity reached. Queue wait timed out after {self.queue_timeout}s.",
                    retry_after=5,
                )

            yield
        finally:
            if acquired_sem:
                self._semaphore.release()
            async with self._lock:
                self._key_counts[key_id] = max(0, self._key_counts.get(key_id, 1) - 1)


# Global backend limiters initialized to 5 concurrent slots per backend
stt_limiter = BackendLimiter(backend_cap=5, per_key_cap=5, queue_timeout=5.0)
llm_limiter = BackendLimiter(backend_cap=5, per_key_cap=5, queue_timeout=5.0)
tts_limiter = BackendLimiter(backend_cap=5, per_key_cap=5, queue_timeout=5.0)
stream_limiter = BackendLimiter(backend_cap=5, per_key_cap=5, queue_timeout=5.0)

"""Test double for LocalAI STT endpoint (POST /v1/audio/transcriptions).

Used for unit, concurrency, and contract testing without requiring a live LocalAI engine.
"""

import asyncio
from typing import Dict, List, Optional


class FakeLocalAI:
    """Test double mimicking LocalAI audio transcription API."""

    def __init__(self, decode_latency_s: float = 0.05, fail_with_status: Optional[int] = None):
        self.decode_latency_s = decode_latency_s
        self.fail_with_status = fail_with_status

        self.active_decodes: int = 0
        self.peak_concurrent_decodes: int = 0
        self.total_requests: int = 0
        self.recorded_requests: List[Dict] = []
        self._lock = asyncio.Lock()

        # Configurable responses based on audio length or custom dict
        self.custom_transcripts: Dict[str, str] = {}

    async def transcribe(self, model: str, audio_bytes: bytes, language: str = "en", temperature: float = 0.0) -> dict:
        """Simulate LocalAI POST /v1/audio/transcriptions."""
        async with self._lock:
            self.active_decodes += 1
            self.total_requests += 1
            if self.active_decodes > self.peak_concurrent_decodes:
                self.peak_concurrent_decodes = self.active_decodes

        req_record = {
            "model": model,
            "audio_len": len(audio_bytes),
            "language": language,
            "temperature": temperature,
        }
        self.recorded_requests.append(req_record)

        try:
            if self.decode_latency_s > 0:
                await asyncio.sleep(self.decode_latency_s)

            if self.fail_with_status:
                raise RuntimeError(f"Simulated HTTP {self.fail_with_status} error from LocalAI")

            text = self.custom_transcripts.get(model, "the weather today in new york is partly cloudy")
            return {"text": text}
        finally:
            async with self._lock:
                self.active_decodes -= 1

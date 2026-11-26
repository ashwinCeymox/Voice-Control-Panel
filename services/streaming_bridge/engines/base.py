"""Abstract Streaming STT Engine Interface and Shared PCM16/Transcript Helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import re
import struct
from typing import Any, Dict, List


class UnsupportedAudioFormatError(ValueError):
    """Raised when incoming audio is not 16 kHz mono PCM16."""


class EngineCrashedError(RuntimeError):
    """Raised when the native worker process dies unexpectedly during a call."""


class EngineABIMismatchError(RuntimeError):
    """Raised when the loaded parakeet C-API ABI does not match expected version."""


@dataclass
class EngineChunkResult:
    """Incremental output from feeding an audio chunk."""

    text_delta: str = ""
    accumulated_text: str = ""
    eou: bool = False
    eob: bool = False
    words: List[Dict[str, Any]] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    frame_sec: float = 0.08


@dataclass
class EngineFinalResult:
    """Final output when a streaming session flushes its end-of-stream tail."""

    tail_text: str = ""
    full_text: str = ""
    eou: bool = False
    eob: bool = False
    events: List[Dict[str, Any]] = field(default_factory=list)


def pcm16_to_float32(
    pcm_bytes: bytes,
    sample_rate: int = 16000,
    channels: int = 1,
    audio_format: str = "pcm_s16le",
) -> List[float]:
    """Validate and convert 16-bit signed little-endian PCM bytes to float32 [-1.0, 1.0]."""
    if audio_format != "pcm_s16le":
        raise UnsupportedAudioFormatError(
            f"Unsupported audio format '{audio_format}': expected 'pcm_s16le'"
        )
    if sample_rate != 16000:
        raise UnsupportedAudioFormatError(
            f"Unsupported sample rate {sample_rate} Hz: expected 16000 Hz mono PCM16"
        )
    if channels != 1:
        raise UnsupportedAudioFormatError(
            f"Unsupported channel count {channels}: expected 1 mono channel"
        )
    if len(pcm_bytes) % 2 != 0:
        raise UnsupportedAudioFormatError(
            f"PCM16 byte length must be even, got {len(pcm_bytes)} bytes"
        )
    if not pcm_bytes:
        return []
    count = len(pcm_bytes) // 2
    samples = struct.unpack(f"<{count}h", pcm_bytes)
    return [max(-1.0, min(1.0, s / 32768.0)) for s in samples]


def join_transcript_parts(accumulated: str, piece: str) -> str:
    """Merge an incremental token piece or finalize tail onto accumulated transcript text.

    In parakeet_capi ABI 11, stream_feed_json and stream_finalize_json emit token pieces
    where a new word starts with a leading space (e.g. ' part', ' clou') and sub-word
    continuations have none (e.g. 'ly', 'dy'). Concatenate pieces as-is without inserting
    spaces, normalize repeated whitespace, and strip leading/trailing whitespace.
    """
    combined = f"{accumulated or ''}{piece or ''}"
    return re.sub(r"\s+", " ", combined).strip()


class StreamingEngine(ABC):
    """Abstract streaming ASR engine lifecycle: load -> open_stream -> feed -> finalize -> close."""

    @abstractmethod
    async def load(self, model_path: str, language: str = "en") -> None:
        """Load the configured model once into the engine/worker."""

    @abstractmethod
    async def open_stream(self, language: str = "en") -> str:
        """Open a new streaming session against the loaded model and return stream_id."""

    @abstractmethod
    async def feed(
        self,
        stream_id: str,
        pcm16_bytes: bytes,
        sample_rate: int = 16000,
    ) -> EngineChunkResult:
        """Feed 16 kHz mono PCM16 audio into stream_id and return any incremental partial."""

    @abstractmethod
    async def finalize(self, stream_id: str) -> EngineFinalResult:
        """Flush stream tail, merge accumulated partials with finalize tail, and free session."""

    @abstractmethod
    async def close_stream(self, stream_id: str) -> None:
        """Cancel/free a stream session without raising if already closed."""

    @abstractmethod
    async def close(self) -> None:
        """Release all active streams, model context, and worker resources."""

    def health(self) -> Dict[str, Any]:
        """Return non-secret diagnostic state for /health."""
        return {"engine": self.__class__.__name__, "ready": True}

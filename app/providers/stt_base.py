"""
Abstract Base Class defining the STTProvider interface.
Allows interchangeable use of LocalAI Whisper, whisper.cpp, faster-whisper, or cloud providers.
"""

from abc import ABC, abstractmethod
from typing import AsyncGenerator, Optional, Dict, Any
from app.schemas.audio import TranscriptResponse


class STTProvider(ABC):
    """Abstract interface that all speech-to-text providers must implement."""

    @abstractmethod
    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
        model: Optional[str] = None,
        language: Optional[str] = None,
    ) -> TranscriptResponse:
        """Transcribe raw audio bytes into text."""
        pass

    async def stream(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        model: Optional[str] = None,
        language: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream raw audio frames and yield partial/final transcript events.

        Default implementation raises NotImplementedError for batch-only providers.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support streaming transcription."
        )
        if False:
            yield {}

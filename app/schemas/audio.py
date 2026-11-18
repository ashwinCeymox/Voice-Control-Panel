"""Audio schemas for STT and audio format declarations."""

from typing import Optional
from pydantic import BaseModel, Field


class AudioFormat(BaseModel):
    sample_rate: int = Field(default=24000, description="Sampling rate in Hz")
    sample_format: str = Field(
        default="s16le", description="Signed 16-bit little-endian PCM"
    )
    channels: int = Field(default=1, description="Number of audio channels (mono=1)")


class InputAudioConfig(BaseModel):
    container: str = Field(
        default="webm", description="Container format: webm, wav, ogg"
    )
    codec: str = Field(default="opus", description="Audio codec: opus, pcm")
    sample_rate: Optional[int] = Field(
        default=None, description="Optional incoming sample rate"
    )


class TranscriptResponse(BaseModel):
    text: str = Field(..., description="Transcribed text from the audio")
    language: Optional[str] = Field(default="en", description="Detected language code")
    duration: Optional[float] = Field(
        default=None, description="Audio duration in seconds"
    )
    model: Optional[str] = Field(
        default=None, description="Model used for transcription"
    )
    fallback_used: bool = Field(
        default=False, description="Whether an automatic fallback model was used"
    )

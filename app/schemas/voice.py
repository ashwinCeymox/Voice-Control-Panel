"""Schemas for TTS, Voice Turn, and Provider/Model discovery."""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from app.schemas.audio import AudioFormat


class VoiceSettings(BaseModel):
    speed: float = Field(
        default=1.0, ge=0.25, le=4.0, description="Playback speed multiplier"
    )


class Voice(BaseModel):
    voice_id: str
    name: str
    category: str = "premade"
    settings: Optional[VoiceSettings] = None


class VoiceListResponse(BaseModel):
    voices: List[Voice]


class ModelInfo(BaseModel):
    id: str
    name: Optional[str] = None
    display_name: Optional[str] = None
    object: str = "model"
    owned_by: str = "local"
    type: Optional[str] = None  # "llm", "stt", "tts"
    task: Optional[str] = None
    kind: Optional[str] = None
    engine: Optional[str] = None
    runtime: Optional[str] = None
    available: Optional[bool] = True
    unavailable_reason: Optional[str] = None
    installed: Optional[bool] = True
    size_mb: Optional[int] = 0
    streaming_mode: Optional[str] = "none"
    transports: Optional[List[str]] = None


class ModelListResponse(BaseModel):
    object: str = "list"
    data: List[ModelInfo]
    models: Optional[List[Dict[str, Any]]] = None


class TTSModelListResponse(BaseModel):
    models: List[Dict[str, Any]]


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Text to synthesize into speech")
    model_id: Optional[str] = Field(
        default=None, description="TTS engine model ID (kokoro, pocket, etc.)"
    )
    voice_settings: Optional[VoiceSettings] = Field(default_factory=VoiceSettings)


class VoiceTurnMetrics(BaseModel):
    stt_ms: Optional[int] = None
    llm_ms: Optional[int] = None
    tts_ms: Optional[int] = None
    total_ms: Optional[int] = None


class VoiceTurnResponse(BaseModel):
    turn_id: str
    session_id: str
    request_id: str
    transcript: str
    response_text: str
    audio_base64: str
    audio_format: AudioFormat = Field(default_factory=AudioFormat)
    metrics: VoiceTurnMetrics = Field(default_factory=VoiceTurnMetrics)


class TicketResponse(BaseModel):
    ticket: str
    expires_in: int = 60

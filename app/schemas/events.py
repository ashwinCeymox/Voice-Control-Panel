"""WebSocket event schemas for client-server protocol (WS /v1/voice/stream)."""

from typing import Optional, Literal, Dict, Any
from pydantic import BaseModel, Field
from app.schemas.audio import InputAudioConfig


# ============================================================================
# Client -> Server Events
# ============================================================================


class SessionStartEvent(BaseModel):
    type: Literal["session.start"] = "session.start"
    session_id: Optional[str] = None
    agent_id: Optional[str] = None
    stt_kind: Optional[str] = None  # "batch" | "streaming"
    input_audio: InputAudioConfig = Field(default_factory=InputAudioConfig)
    config: Optional[Dict[str, Any]] = None


class StreamStartEvent(BaseModel):
    type: Literal["stream.start"] = "stream.start"
    model: Optional[str] = None
    language: Optional[str] = "en"
    sample_rate: int = 16000
    format: str = "pcm_s16le"


class StreamCancelEvent(BaseModel):
    type: Literal["stream.cancel"] = "stream.cancel"


class AudioCommitEvent(BaseModel):
    type: Literal["audio.commit"] = "audio.commit"


class TurnCancelEvent(BaseModel):
    type: Literal["turn.cancel"] = "turn.cancel"
    turn_id: Optional[str] = None


class SessionEndEvent(BaseModel):
    type: Literal["session.end"] = "session.end"


# ============================================================================
# Server -> Client Events
# ============================================================================


class SessionCreatedEvent(BaseModel):
    type: Literal["session.created"] = "session.created"
    session_id: str
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None


class TranscriptCompletedEvent(BaseModel):
    type: Literal["transcript.completed"] = "transcript.completed"
    turn_id: str
    text: str


class TranscriptPartialEvent(BaseModel):
    type: Literal["transcript.partial"] = "transcript.partial"
    seq: int = 0
    confirmed: str = ""
    tentative: str = ""
    turn_id: Optional[str] = None


class STTFallbackEvent(BaseModel):
    type: Literal["stt.fallback"] = "stt.fallback"
    reason: str
    turn_id: Optional[str] = None


class ResponseTextDeltaEvent(BaseModel):
    type: Literal["response.text.delta"] = "response.text.delta"
    turn_id: str
    delta: str


class AudioStartedEvent(BaseModel):
    type: Literal["audio.started"] = "audio.started"
    turn_id: str
    sample_rate: int = 24000
    sample_format: str = "s16le"
    channels: int = 1


class AudioCompletedEvent(BaseModel):
    type: Literal["audio.completed"] = "audio.completed"
    turn_id: str


class TurnCompletedEvent(BaseModel):
    type: Literal["turn.completed"] = "turn.completed"
    turn_id: str


class TurnCancelledEvent(BaseModel):
    type: Literal["turn.cancelled"] = "turn.cancelled"
    turn_id: str


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str
    turn_id: Optional[str] = None
    request_id: Optional[str] = None

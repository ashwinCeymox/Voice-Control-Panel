"""Agent schemas matching docs/contracts/openapi.yaml and docs/contracts/agent-schema.json."""
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, ConfigDict


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model: str = Field(..., min_length=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=1, le=4096)


class STTConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model: str = Field(..., min_length=1)
    language: str = "auto"


class TTSConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    engine: str = "kokoro"
    voice_id: str = "af_heart"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


class VADConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    engine: str = "silero"
    positive_speech_threshold: float = Field(default=0.45, ge=0.1, le=0.95)
    negative_speech_threshold: float = Field(default=0.30, ge=0.05, le=0.9)
    redemption_ms: int = Field(default=800, ge=200, le=3000)
    pre_speech_pad_ms: int = Field(default=200, ge=50, le=1000)
    min_speech_ms: int = Field(default=250, ge=50, le=1000)
    min_rms: float = Field(default=0.015, ge=0.0, le=0.2)
    noise_suppression: str = "browser"
    barge_in: bool = True
    barge_in_confirm_ms: int = Field(default=250, ge=100, le=1000)
    playback_threshold_boost: float = Field(default=0.15, ge=0.0, le=0.5)
    # Legacy backward-compatibility fields
    mode: Optional[str] = "energy"
    threshold: Optional[float] = 0.045
    min_silence_ms: Optional[int] = 800


class AudioConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    echo_mode: str = "laptop_speakers"
    barge_in: bool = True


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    name: str = Field(..., min_length=1, max_length=64)
    description: Optional[str] = Field(default="", max_length=256)
    system_prompt: str = Field(..., min_length=1)
    llm: LLMConfig
    stt: STTConfig
    tts: TTSConfig
    vad: Optional[VADConfig] = Field(default_factory=VADConfig)
    audio: Optional[AudioConfig] = Field(default_factory=AudioConfig)


class AgentUpdateConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    description: Optional[str] = Field(None, max_length=256)
    system_prompt: Optional[str] = Field(None, min_length=1)
    llm: Optional[LLMConfig] = None
    stt: Optional[STTConfig] = None
    tts: Optional[TTSConfig] = None
    vad: Optional[VADConfig] = None
    audio: Optional[AudioConfig] = None


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    description: Optional[str] = ""
    system_prompt: str
    llm: LLMConfig
    stt: STTConfig
    tts: TTSConfig
    vad: VADConfig
    audio: AudioConfig
    is_active: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_used_at: Optional[str] = None
    schema_version: int = 1


class AgentListResponse(BaseModel):
    agents: List[Dict[str, Any]]


class AgentLoadResponse(BaseModel):
    status: str
    load_id: str
    active_agent: Optional[Dict[str, Any]] = None
    memory_verified: bool = True


class TestChatRequest(BaseModel):
    message: str = Field(..., min_length=1)

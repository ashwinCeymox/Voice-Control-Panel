"""Chat schemas compatible with OpenAI specification for LocalAI LLM."""

from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"] = Field(
        ..., description="Message role"
    )
    content: str = Field(..., description="Message text content")


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = Field(default=None, description="LocalAI LLM model ID")
    messages: List[ChatMessage] = Field(..., description="Conversation messages")
    stream: bool = Field(
        default=False, description="Enable Server-Sent Events streaming"
    )
    temperature: Optional[float] = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1)


class ChatCompletionChoiceMessage(BaseModel):
    role: str = "assistant"
    content: str


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatCompletionChoiceMessage
    finish_reason: Optional[str] = "stop"


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionChoice]


class ChatDelta(BaseModel):
    content: Optional[str] = None
    role: Optional[str] = None


class ChatStreamChoice(BaseModel):
    index: int = 0
    delta: ChatDelta
    finish_reason: Optional[str] = None


class ChatStreamChunk(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: List[ChatStreamChoice]

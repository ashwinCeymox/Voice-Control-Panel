"""Chat completions endpoint proxying to LocalAI."""
import json
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from app.core.security import require_api_key
from app.providers.localai_llm import LocalAILLMClient
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse

router = APIRouter(prefix="/v1/chat", tags=["Chat & LLM"])
llm_client = LocalAILLMClient()


@router.post("/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    request: ChatCompletionRequest,
    user: str = Depends(require_api_key),
):
    if request.stream:
        async def event_generator():
            messages_dict = [m.model_dump() for m in request.messages]
            async for delta in llm_client.stream_tokens(
                messages=messages_dict,
                model=request.model,
                temperature=request.temperature or 0.7,
            ):
                chunk_data = {
                    "id": "chatcmpl-stream",
                    "object": "chat.completion.chunk",
                    "choices": [{"delta": {"content": delta}}],
                }
                yield f"data: {json.dumps(chunk_data)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    return await llm_client.complete(request)

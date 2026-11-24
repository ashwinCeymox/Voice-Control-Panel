"""Chat completions endpoint proxying to LocalAI."""

import json
import time
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from app.core.config import settings
from app.core.errors import BadRequestError
from app.core.limits import llm_limiter
from app.core.security import require_scope
from app.services.key_manager import KeyManager, ApiKeyRecord
from app.providers.localai_llm import LocalAILLMClient
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse

router = APIRouter(prefix="/v1/chat", tags=["Chat & LLM"])
llm_client = LocalAILLMClient()


@router.post("/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    request: ChatCompletionRequest,
    key: ApiKeyRecord = Depends(require_scope("llm")),
):
    target_model = request.model or settings.LOCALAI_LLM_MODEL
    await KeyManager.check_quota(key, cost_type="tokens")
    KeyManager.check_model_allowed(key, target_model)
    if settings.LOCALAI_ALLOWED_LLM_MODELS:
        allowed = [
            m.strip()
            for m in settings.LOCALAI_ALLOWED_LLM_MODELS.split(",")
            if m.strip()
        ]
        from app.core.model_matching import is_model_allowed

        if not is_model_allowed(target_model, allowed):
            raise BadRequestError(
                f"Model '{target_model}' is not in allowed models list: {allowed}"
            )

    # Validate model task against registry (honest error mapping for wrong-type models)
    from app.services.model_registry import model_registry

    all_models = await model_registry.list_models(include_unclassified=True)
    matched = next(
        (
            m
            for m in all_models
            if m["id"].lower() == target_model.lower()
            or m["id"].split(":")[0].lower() == target_model.split(":")[0].lower()
        ),
        None,
    )
    if matched and matched.get("task") not in ("llm", "other", "unknown"):
        raise BadRequestError(
            f"Model '{target_model}' is an {matched.get('task').upper()} model, not a valid LLM chat model."
        )

    from app.services.residency_manager import residency_manager

    residency_manager.track_request_start(target_model)

    start_t = time.time()
    status_code = 200
    token_count = 0.0

    async with llm_limiter.acquire(
        key_id=key.id, backend="llm", max_concurrent=key.max_concurrent
    ):
        if request.stream:

            async def event_generator():
                nonlocal token_count
                try:
                    messages_dict = [m.model_dump() for m in request.messages]
                    async for delta in llm_client.stream_tokens(
                        messages=messages_dict,
                        model=request.model,
                        temperature=request.temperature or 0.7,
                    ):
                        token_count += 1
                        chunk_data = {
                            "id": "chatcmpl-stream",
                            "object": "chat.completion.chunk",
                            "choices": [{"delta": {"content": delta}}],
                        }
                        yield f"data: {json.dumps(chunk_data)}\n\n"
                    yield "data: [DONE]\n\n"
                    dur_ms = (time.time() - start_t) * 1000.0
                    await KeyManager.log_usage(
                        key.id,
                        "/v1/chat/completions",
                        dur_ms,
                        token_count,
                        200,
                        model=target_model,
                        profile_id=key.profile_id,
                    )
                finally:
                    residency_manager.track_request_end(target_model)

            return StreamingResponse(event_generator(), media_type="text/event-stream")

        try:
            resp = await llm_client.complete(request)
            usage_obj = getattr(resp, "usage", None)
            token_count = (
                float(usage_obj.total_tokens)
                if usage_obj and hasattr(usage_obj, "total_tokens")
                else 0.0
            )
            return resp
        except Exception as e:
            status_code = getattr(e, "status_code", 500)
            raise
        finally:
            residency_manager.track_request_end(target_model)
            dur_ms = (time.time() - start_t) * 1000.0
            await KeyManager.log_usage(
                key.id,
                "/v1/chat/completions",
                dur_ms,
                token_count,
                status_code,
                model=target_model,
                profile_id=key.profile_id,
            )

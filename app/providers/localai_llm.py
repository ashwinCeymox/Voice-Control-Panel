"""LocalAI LLM provider client supporting non-streaming and SSE streaming."""
import json
import logging
from typing import AsyncGenerator, Dict, List, Optional
import httpx
from app.core.config import settings
from app.core.errors import ProviderTimeoutError, ProviderUnavailableError
from app.schemas.chat import (
    ChatCompletionChoice,
    ChatCompletionChoiceMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
)

logger = logging.getLogger("voice_agent.providers.llm")


class LocalAILLMClient:
    def __init__(self, base_url: Optional[str] = None, timeout: Optional[int] = None):
        self.base_url = (base_url or settings.LOCALAI_BASE_URL).rstrip("/")
        self.timeout = timeout or settings.LOCALAI_REQUEST_TIMEOUT_SECONDS

    async def resolve_model(self, requested_model: Optional[str]) -> str:
        """Resolve friendly model names to the exact model ID registered in LocalAI."""
        target = requested_model or settings.LOCALAI_LLM_MODEL
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                headers = {}
                if settings.LOCALAI_API_KEY:
                    headers["Authorization"] = f"Bearer {settings.LOCALAI_API_KEY}"
                resp = await client.get(f"{self.base_url}/v1/models", headers=headers)
                if resp.status_code == 200:
                    models = [m.get("id") for m in resp.json().get("data", []) if m.get("id")]
                    if target in models:
                        return target
                    target_lower = target.lower().replace("-", "").replace("_", "")
                    for m in models:
                        m_clean = m.lower().replace("-", "").replace("_", "")
                        if target_lower in m_clean or m_clean in target_lower:
                            return m
                    llm_models = [m for m in models if "whisper" not in m.lower()]
                    if llm_models:
                        return llm_models[0]
        except Exception:
            pass
        return target

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Non-streaming chat completion."""
        url = f"{self.base_url}/v1/chat/completions"
        target_model = await self.resolve_model(request.model)

        payload = {
            "model": target_model,
            "messages": [m.model_dump() for m in request.messages],
            "stream": False,
            "temperature": request.temperature,
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens

        headers = {}
        if settings.LOCALAI_API_KEY:
            headers["Authorization"] = f"Bearer {settings.LOCALAI_API_KEY}"

        try:
            async with httpx.AsyncClient(timeout=float(self.timeout)) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    choices = [
                        ChatCompletionChoice(
                            index=c.get("index", 0),
                            message=ChatCompletionChoiceMessage(
                                role=c.get("message", {}).get("role", "assistant"),
                                content=c.get("message", {}).get("content", ""),
                            ),
                            finish_reason=c.get("finish_reason", "stop"),
                        )
                        for c in data.get("choices", [])
                    ]
                    return ChatCompletionResponse(
                        id=data.get("id", "chatcmpl-local"),
                        created=data.get("created", 0),
                        model=target_model,
                        choices=choices,
                    )
                else:
                    logger.error("LocalAI LLM complete error %d: %s", resp.status_code, resp.text)
                    raise ProviderUnavailableError("LocalAI LLM", details=resp.text)
        except httpx.TimeoutException:
            raise ProviderTimeoutError("LocalAI LLM")
        except httpx.RequestError as exc:
            raise ProviderUnavailableError("LocalAI LLM", details=str(exc))

    async def stream_tokens(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """Stream SSE text deltas from LocalAI."""
        url = f"{self.base_url}/v1/chat/completions"
        target_model = await self.resolve_model(model)

        payload = {
            "model": target_model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
        }

        headers = {}
        if settings.LOCALAI_API_KEY:
            headers["Authorization"] = f"Bearer {settings.LOCALAI_API_KEY}"

        async with httpx.AsyncClient(timeout=float(self.timeout)) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    err_body = await resp.aread()
                    raise ProviderUnavailableError("LocalAI LLM", details=err_body.decode())

                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk_json = json.loads(data_str)
                        choices = chunk_json.get("choices", [])
                        if choices:
                            delta_content = choices[0].get("delta", {}).get("content", "")
                            if delta_content:
                                yield delta_content
                    except json.JSONDecodeError:
                        continue

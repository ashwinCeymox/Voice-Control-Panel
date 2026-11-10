"""Agent management REST endpoints and test-chat streaming."""
import asyncio
import json
import logging
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.core.security import require_api_key
from app.providers.localai_llm import LocalAILLMClient
from app.schemas.agent import (
    AgentConfig,
    AgentUpdateConfig,
    AgentListResponse,
    AgentLoadResponse,
    TestChatRequest,
)
from app.services.agent_manager import AgentManager, DEFAULT_AGENT

logger = logging.getLogger("voice_agent.api.agents")

router = APIRouter(prefix="/v1/agents", tags=["Agents"])
agents_router = router  # Alias for explicit export

llm_client = LocalAILLMClient()


@router.get("", response_model=AgentListResponse)
@router.get("/", response_model=AgentListResponse, include_in_schema=False)
async def list_agents(user: str = Depends(require_api_key)):
    """List all saved agents."""
    agents = await AgentManager.list_agents()
    return {"agents": agents}


@router.post("", status_code=201)
@router.post("/", status_code=201, include_in_schema=False)
async def create_agent(
    payload: AgentConfig,
    user: str = Depends(require_api_key),
):
    """Create a new agent configuration."""
    data = payload.model_dump()
    created = await AgentManager.create_agent(data)
    return created


# BUG FIX: /active must be declared BEFORE /{id} to prevent FastAPI from
# routing GET /v1/agents/active into get_agent(id="active").
@router.get("/active")
async def get_active_agent(user: str = Depends(require_api_key)):
    """Get currently loaded active agent details and load state."""
    state_info = AgentManager.get_active_state()
    active_id = state_info.get("active_agent_id")
    load_state = state_info.get("state", "ready")
    agent = await AgentManager.get_agent(active_id) if active_id else None

    # BUG FIX: Return a clean, non-duplicated response envelope.
    # Previously, agent fields were merged into the top-level dict alongside
    # active_agent, sending every agent field twice.
    return {
        "status": load_state,
        "active_agent_id": active_id,
        "active_agent": agent,
    }


@router.get("/{id}")
async def get_agent(
    id: str,
    user: str = Depends(require_api_key),
):
    """Get agent configuration details."""
    agent = await AgentManager.get_agent(id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{id}' not found")
    return agent


@router.put("/{id}")
async def update_agent(
    id: str,
    # BUG FIX: Use AgentUpdateConfig (all fields optional) instead of AgentConfig
    # (required fields). PUT merges with the existing record, so individual fields
    # may be omitted while the merge ensures all required DB columns are populated.
    payload: AgentUpdateConfig,
    user: str = Depends(require_api_key),
):
    """Update agent configuration."""
    existing = await AgentManager.get_agent(id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Agent '{id}' not found")

    update_data = payload.model_dump(exclude_unset=True)
    # Merge incoming partial update over the existing record
    merged_data = {**existing, **update_data}

    # Validate required fields are present in merged result
    if not merged_data.get("name") or not str(merged_data["name"]).strip():
        raise HTTPException(status_code=400, detail="Field 'name' cannot be empty")
    if not merged_data.get("system_prompt") or not str(merged_data["system_prompt"]).strip():
        raise HTTPException(status_code=400, detail="Field 'system_prompt' cannot be empty")

    updated = await AgentManager.update_agent(id, merged_data)
    return updated


@router.delete("/{id}")
async def delete_agent(
    id: str,
    user: str = Depends(require_api_key),
):
    """Delete an agent."""
    # BUG FIX: Use only DEFAULT_AGENT["id"] (single source of truth) instead of
    # also hardcoding the string "agent-default-assistant" separately.
    if id == DEFAULT_AGENT["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete default agent")

    existing = await AgentManager.get_agent(id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Agent '{id}' not found")

    success = await AgentManager.delete_agent(id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot delete default agent")
    return {"status": "deleted", "id": id}


@router.post("/{id}/duplicate", status_code=201)
async def duplicate_agent(
    id: str,
    user: str = Depends(require_api_key),
):
    """Duplicate an existing agent with ' (Copy)' appended to name."""
    existing = await AgentManager.get_agent(id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Agent '{id}' not found")

    duplicate_data = {
        "name": f"{existing['name']} (Copy)",
        "description": existing.get("description", ""),
        "system_prompt": existing["system_prompt"],
        "llm": existing["llm"],
        "stt": existing["stt"],
        "tts": existing["tts"],
        "vad": existing.get("vad", {"mode": "energy", "threshold": 0.045, "min_silence_ms": 650}),
        "audio": existing.get("audio", {"echo_mode": "laptop_speakers", "barge_in": True}),
    }
    created = await AgentManager.create_agent(duplicate_data)
    return created


@router.post("/{id}/load")
async def load_agent(
    id: str,
    user: str = Depends(require_api_key),
):
    """Activate agent and verify memory fit."""
    try:
        result = await AgentManager.load_agent(id)
        return result
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@router.post("/{id}/test-chat")
async def test_chat(
    id: str,
    payload: TestChatRequest,
    user: str = Depends(require_api_key),
):
    """Stream chat response using agent's system prompt and LLM configuration."""
    agent = await AgentManager.get_agent(id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{id}' not found")

    system_prompt = agent.get("system_prompt", "You are a concise voice AI assistant.")
    llm_conf = agent.get("llm", {})
    model = llm_conf.get("model", settings.LOCALAI_LLM_MODEL)
    temperature = float(llm_conf.get("temperature", 0.7))

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": payload.message},
    ]

    async def event_generator():
        streamed_any = False
        try:
            async for delta in llm_client.stream_tokens(
                messages=messages,
                model=model,
                temperature=temperature,
            ):
                streamed_any = True
                chunk_data = {
                    "id": f"chatcmpl-{id}",
                    "object": "chat.completion.chunk",
                    "choices": [{"delta": {"content": delta}}],
                }
                yield f"data: {json.dumps(chunk_data)}\n\n"
            if streamed_any:
                yield "data: [DONE]\n\n"
                return
        except Exception as exc:
            logger.warning("LocalAI streaming failed (%s); streaming fallback mock response.", exc)

        # Fallback simulated response
        mock_reply = (
            f"Hello! I am {agent.get('name', 'Assistant')}. "
            f"I received: \"{payload.message}\"."
        )
        words = mock_reply.split(" ")
        for i, word in enumerate(words):
            token = word if i == 0 else f" {word}"
            chunk_data = {
                "id": f"chatcmpl-mock-{id}",
                "object": "chat.completion.chunk",
                "choices": [{"delta": {"content": token}}],
            }
            yield f"data: {json.dumps(chunk_data)}\n\n"
            await asyncio.sleep(0.02)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

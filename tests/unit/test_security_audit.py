"""Security and safety verification unit tests for task V1 (S-REVIEW).

Tests:
1. Path traversal & input validation on model install/uninstall endpoints.
2. Secret redaction in logging formatter (Bearer, xi-api-key, ticket, passwords).
3. Concurrency safety and memory exception boundaries in AgentManager.load_agent.
"""

import asyncio
import logging
import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch

from app.main import app
from app.core.logging import RedactingFormatter
from app.services.agent_manager import AgentManager, DEFAULT_AGENT

AUTH_HEADERS = {"Authorization": "Bearer dev-voice-key-12345"}


# ---------------------------------------------------------------------------
# 1. Path Traversal & Model ID Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_id",
    [
        "../etc/passwd",
        "..\\windows\\system32",
        "/root/.ssh/id_rsa",
        "model/subpath",
        "model\\subpath",
        ".hidden_file",
        "..",
        ".",
        "",
        "   ",
        "a" * 150,  # exceeds 128 chars
        "model;rm -rf /",
        "model&calc.exe",
    ],
)
async def test_gallery_install_rejects_path_traversal_and_malformed_ids(invalid_id):
    """POST /v1/models/install must reject path traversal and invalid IDs with 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/v1/models/install",
            headers=AUTH_HEADERS,
            json={"model_id": invalid_id},
        )
        assert r.status_code == 400, (
            f"Expected 400 for '{invalid_id}', got {r.status_code}: {r.text}"
        )


@pytest.mark.asyncio
async def test_gallery_install_rejects_non_string_model_id():
    """POST /v1/models/install must handle non-string model_id with 400 without crashing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/v1/models/install",
            headers=AUTH_HEADERS,
            json={"model_id": 12345},
        )
        assert r.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_id",
    [
        "%2e%2e",
        ".hidden",
        "-flag",
        "invalid;cmd",
        "model*wildcard",
        "a" * 150,
    ],
)
async def test_gallery_uninstall_rejects_path_traversal(invalid_id):
    """DELETE /v1/models/{id} must reject path traversal and invalid IDs with 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.delete(
            f"/v1/models/{invalid_id}",
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# 2. Secret Redaction in Logging
# ---------------------------------------------------------------------------


def test_redacting_formatter_redacts_bearer_tokens():
    formatter = RedactingFormatter(fmt="%(message)s")
    record = logging.LogRecord(
        "test",
        logging.INFO,
        "",
        0,
        "User logged in with Bearer va-secret-token-12345",
        (),
        None,
    )
    output = formatter.format(record)
    assert "va-secret-token-12345" not in output
    assert "Bearer [REDACTED]" in output


def test_redacting_formatter_redacts_xi_api_key():
    formatter = RedactingFormatter(fmt="%(message)s")
    record = logging.LogRecord(
        "test",
        logging.INFO,
        "",
        0,
        "Request header xi-api-key: secret-key-value-999",
        (),
        None,
    )
    output = formatter.format(record)
    assert "secret-key-value-999" not in output
    assert "[REDACTED]" in output


def test_redacting_formatter_redacts_ticket_in_urls_and_json():
    formatter = RedactingFormatter(fmt="%(message)s")
    # Query parameter format
    rec1 = logging.LogRecord(
        "test",
        logging.INFO,
        "",
        0,
        "Connecting to /v1/voice/stream?ticket=my_secret_ticket_12345",
        (),
        None,
    )
    out1 = formatter.format(rec1)
    assert "my_secret_ticket_12345" not in out1
    assert "ticket=[REDACTED]" in out1

    # JSON format
    rec2 = logging.LogRecord(
        "test",
        logging.INFO,
        "",
        0,
        'Response: {"ticket": "secret_ticket_abc_987"}',
        (),
        None,
    )
    out2 = formatter.format(rec2)
    assert "secret_ticket_abc_987" not in out2
    assert '"ticket": "[REDACTED]"' in out2


def test_redacting_formatter_redacts_api_key_and_passwords():
    formatter = RedactingFormatter(fmt="%(message)s")
    rec = logging.LogRecord(
        "test",
        logging.INFO,
        "",
        0,
        "Config: api_key=super_secret_key_123 password=my_password_xyz",
        (),
        None,
    )
    out = formatter.format(rec)
    assert "super_secret_key_123" not in out
    assert "my_password_xyz" not in out


# ---------------------------------------------------------------------------
# 3. Agent Load Concurrency & Memory Safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_agent_load_does_not_corrupt_state():
    """Concurrent load_agent calls must serialize cleanly via _LOAD_LOCK."""
    await AgentManager.ensure_default_agent()

    # Create two temporary agents
    agent1 = await AgentManager.create_agent(
        {
            "name": "Concurrency Test 1",
            "system_prompt": "Prompt 1",
            "llm": {"model": "phi-2", "temperature": 0.7, "max_tokens": 512},
            "stt": {"model": "whisper-1", "language": "auto"},
            "tts": {"engine": "kokoro", "voice_id": "af_heart", "speed": 1.0},
        }
    )
    agent2 = await AgentManager.create_agent(
        {
            "name": "Concurrency Test 2",
            "system_prompt": "Prompt 2",
            "llm": {"model": "phi-2", "temperature": 0.7, "max_tokens": 512},
            "stt": {"model": "whisper-1", "language": "auto"},
            "tts": {"engine": "kokoro", "voice_id": "af_heart", "speed": 1.0},
        }
    )

    try:
        # Load both concurrently
        res1, res2 = await asyncio.gather(
            AgentManager.load_agent(agent1["id"]),
            AgentManager.load_agent(agent2["id"]),
        )
        assert res1["status"] == "ready"
        assert res2["status"] == "ready"

        # Final state must be ready with a valid active agent id
        state = AgentManager.get_active_state()
        assert state["state"] == "ready"
        assert state["active_agent_id"] in (agent1["id"], agent2["id"])
    finally:
        await AgentManager.delete_agent(agent1["id"])
        await AgentManager.delete_agent(agent2["id"])
        # Restore default agent
        await AgentManager.load_agent(DEFAULT_AGENT["id"])


@pytest.mark.asyncio
async def test_load_agent_state_resets_on_db_or_unexpected_failure():
    """Unexpected failure during load_agent must set state to 'error', not leave it in 'loading'."""
    await AgentManager.ensure_default_agent()

    with patch("app.services.agent_manager.get_mac_memory_info") as mock_mem:
        # Simulate unexpected hardware probe error during load transition
        mock_mem.side_effect = RuntimeError("Simulated sysctl probe error")

        with pytest.raises(RuntimeError):
            await AgentManager.load_agent(DEFAULT_AGENT["id"])

        # Must not be stuck in "loading"
        state = AgentManager.get_active_state()
        assert state["state"] == "error", f"Expected 'error', got {state['state']}"

    # Restore default agent to clean state
    await AgentManager.load_agent(DEFAULT_AGENT["id"])
    assert AgentManager.get_active_state()["state"] == "ready"

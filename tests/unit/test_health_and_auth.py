"""Unit tests for health endpoints and authentication security."""
import pytest
from httpx import ASGITransport, AsyncClient
from app.core.security import hash_api_key, validate_and_consume_ticket
from app.main import app


@pytest.mark.asyncio
async def test_health_live():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "live"}
        assert "x-request-id" in response.headers


@pytest.mark.asyncio
async def test_auth_rejection_without_key():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/providers")
        assert response.status_code == 401
        data = response.json()
        assert data["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_auth_success_with_bearer_and_xi_key():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Test Bearer header
        resp1 = await client.get(
            "/v1/providers",
            headers={"Authorization": "Bearer dev-voice-key-12345"},
        )
        assert resp1.status_code == 200
        assert "localai" in resp1.json()

        # Test xi-api-key header
        resp2 = await client.get(
            "/v1/providers",
            headers={"xi-api-key": "dev-voice-key-12345"},
        )
        assert resp2.status_code == 200
        assert "localtts" in resp2.json()


@pytest.mark.asyncio
async def test_websocket_ticket_generation_and_consumption():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/auth/ticket",
            headers={"Authorization": "Bearer dev-voice-key-12345"},
        )
        assert response.status_code == 200
        ticket = response.json()["ticket"]
        assert ticket is not None

        # Validate and consume once
        user = validate_and_consume_ticket(ticket)
        assert user == "developer"

        # Second consumption fails (single-use)
        user_again = validate_and_consume_ticket(ticket)
        assert user_again is None

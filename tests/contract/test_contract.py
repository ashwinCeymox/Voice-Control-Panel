"""Contract verification tests against docs/contracts/openapi.yaml."""
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app

AUTH_HEADER = {"Authorization": "Bearer dev-voice-key-12345"}


@pytest.mark.asyncio
async def test_api_contract_models():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/models", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        assert isinstance(data["data"], list)
        for m in data["data"]:
            assert "id" in m
            assert "owned_by" in m


@pytest.mark.asyncio
async def test_api_contract_tts_models():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/tts/models", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert any(m["id"] == "kokoro" for m in data["models"])


@pytest.mark.asyncio
async def test_api_contract_voices():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/voices", headers={"xi-api-key": "dev-voice-key-12345"})
        assert resp.status_code == 200
        data = resp.json()
        assert "voices" in data
        assert len(data["voices"]) > 0
        v = data["voices"][0]
        assert "voice_id" in v
        assert "gender" in v
        assert "engine" in v


@pytest.mark.asyncio
async def test_api_contract_system_resources():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/system/resources", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert "memory" in data
        assert "total_mb" in data["memory"]
        assert "available_mb" in data["memory"]
        assert "hardware" in data
        assert "has_metal_gpu" in data["hardware"]
        assert "disk" in data
        assert "free_gb" in data["disk"]


@pytest.mark.asyncio
async def test_api_contract_gallery_models():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/gallery/models", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert len(data["models"]) > 0
        m = data["models"][0]
        assert "id" in m
        assert "fit_score" in m
        assert "fits_budget" in m
        assert 0 <= m["fit_score"] <= 100


@pytest.mark.asyncio
async def test_api_contract_voice_meta_update():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/v1/voices/af_heart/meta",
            headers=AUTH_HEADER,
            json={"name": "Heart Custom", "gender": "female"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["voice_id"] == "af_heart"
        assert data["status"] == "updated"
        assert data["metadata"]["name"] == "Heart Custom"
        assert data["metadata"]["gender"] == "female"


@pytest.mark.asyncio
async def test_api_contract_agents_lifecycle():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. List agents
        list_resp = await client.get("/v1/agents", headers=AUTH_HEADER)
        assert list_resp.status_code == 200
        agents = list_resp.json()["agents"]
        assert len(agents) >= 1

        # 2. Get active agent
        act_resp = await client.get("/v1/agents/active", headers=AUTH_HEADER)
        assert act_resp.status_code == 200
        act_data = act_resp.json()
        assert "status" in act_data
        assert "active_agent_id" in act_data

        # 3. Create test agent
        create_payload = {
            "name": "Contract Test Agent",
            "description": "Used for OpenAPI contract test",
            "system_prompt": "You are a test agent.",
            "llm": {"model": "phi-2-chat", "temperature": 0.5, "max_tokens": 256},
            "stt": {"model": "whisper-base", "language": "en"},
            "tts": {"engine": "kokoro", "voice_id": "af_heart", "speed": 1.1},
            "vad": {"mode": "energy", "threshold": 0.045, "min_silence_ms": 650},
            "audio": {"echo_mode": "laptop_speakers", "barge_in": True},
        }
        create_resp = await client.post("/v1/agents", headers=AUTH_HEADER, json=create_payload)
        assert create_resp.status_code == 201
        created = create_resp.json()
        agent_id = created["id"]
        assert created["name"] == "Contract Test Agent"

        # 4. Duplicate agent
        dup_resp = await client.post(f"/v1/agents/{agent_id}/duplicate", headers=AUTH_HEADER)
        assert dup_resp.status_code == 201
        dup_agent = dup_resp.json()
        assert "(Copy)" in dup_agent["name"]

        # 5. Load agent
        load_resp = await client.post(f"/v1/agents/{agent_id}/load", headers=AUTH_HEADER)
        assert load_resp.status_code == 200
        assert load_resp.json()["status"] == "ready"

        # 6. Delete both test agents
        del_resp1 = await client.delete(f"/v1/agents/{agent_id}", headers=AUTH_HEADER)
        assert del_resp1.status_code == 200
        del_resp2 = await client.delete(f"/v1/agents/{dup_agent['id']}", headers=AUTH_HEADER)
        assert del_resp2.status_code == 200

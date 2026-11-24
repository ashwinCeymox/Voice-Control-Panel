"""Unit tests for Phase B6: Operations, Scripts, Ports, Supervisor & Health Ready."""

import pytest
from unittest.mock import patch, AsyncMock
from httpx import ASGITransport, AsyncClient, Response
from app.main import app
from app.core.config import settings


@pytest.mark.asyncio
async def test_health_ready_when_bridge_disabled():
    """When STREAMING_BRIDGE_ENABLED=False, /health/ready does not include streaming_bridge."""
    transport = ASGITransport(app=app)
    with patch.object(settings, "STREAMING_BRIDGE_ENABLED", False):
        mock_inner_client = AsyncMock()

        async def fake_get(url, *args, **kwargs):
            url_str = str(url)
            if "8080" in url_str:
                return Response(200, json={"data": []})
            if "8880" in url_str:
                return Response(200, json=[])
            if "8088" in url_str:
                return Response(200, json={"model": "test-zipformer"})
            return Response(404)

        mock_inner_client.get = AsyncMock(side_effect=fake_get)
        mock_inner_client.__aenter__.return_value = mock_inner_client
        mock_inner_client.__aexit__.return_value = None

        with patch("app.api.health.httpx.AsyncClient", return_value=mock_inner_client):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/health/ready")
                assert resp.status_code == 200
                data = resp.json()
                assert "streaming_bridge" not in data["providers"]
                assert data["status"] in ("ready", "degraded")


@pytest.mark.asyncio
async def test_health_ready_when_bridge_enabled_and_healthy():
    """When STREAMING_BRIDGE_ENABLED=True and bridge responds 200, reports reachable=True."""
    transport = ASGITransport(app=app)
    with patch.object(settings, "STREAMING_BRIDGE_ENABLED", True), \
         patch.object(settings, "STREAMING_BRIDGE_PORT", 8089), \
         patch.object(settings, "STREAMING_BRIDGE_URL", "http://127.0.0.1:8089"):
        mock_inner_client = AsyncMock()

        async def fake_get(url, *args, **kwargs):
            url_str = str(url)
            if "8080" in url_str:
                return Response(200, json={"data": []})
            if "8880" in url_str:
                return Response(200, json=[])
            if "8088" in url_str:
                return Response(200, json={"model": "test-zipformer"})
            if "8089" in url_str:
                return Response(200, json={
                    "status": "healthy",
                    "service": "streaming_bridge",
                    "active_streams": 1,
                    "max_streams": 5,
                    "max_decodes": 1,
                })
            return Response(404)

        mock_inner_client.get = AsyncMock(side_effect=fake_get)
        mock_inner_client.__aenter__.return_value = mock_inner_client
        mock_inner_client.__aexit__.return_value = None

        with patch("app.api.health.httpx.AsyncClient", return_value=mock_inner_client):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/health/ready")
                assert resp.status_code == 200
                data = resp.json()
                assert "streaming_bridge" in data["providers"]
                sb = data["providers"]["streaming_bridge"]
                assert sb["reachable"] is True
                assert sb["status"] == "ready"
                assert sb["port"] == 8089
                assert sb["active_streams"] == 1
                assert sb["max_streams"] == 5
                assert data["status"] == "ready"


@pytest.mark.asyncio
async def test_health_ready_when_bridge_down_never_marks_gateway_unhealthy():
    """Per port-stt.md Phase B6: a down bridge never marks the gateway unhealthy."""
    transport = ASGITransport(app=app)
    with patch.object(settings, "STREAMING_BRIDGE_ENABLED", True), \
         patch.object(settings, "STREAMING_BRIDGE_PORT", 8089), \
         patch.object(settings, "STREAMING_BRIDGE_URL", "http://127.0.0.1:8089"):
        mock_inner_client = AsyncMock()

        async def fake_get(url, *args, **kwargs):
            url_str = str(url)
            if "8080" in url_str:
                return Response(200, json={"data": []})
            if "8880" in url_str:
                return Response(200, json=[])
            if "8088" in url_str:
                return Response(200, json={"model": "test-zipformer"})
            if "8089" in url_str:
                raise Exception("Connection refused on 127.0.0.1:8089")
            return Response(404)

        mock_inner_client.get = AsyncMock(side_effect=fake_get)
        mock_inner_client.__aenter__.return_value = mock_inner_client
        mock_inner_client.__aexit__.return_value = None

        with patch("app.api.health.httpx.AsyncClient", return_value=mock_inner_client):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/health/ready")
                assert resp.status_code == 200
                data = resp.json()
                assert "streaming_bridge" in data["providers"]
                sb = data["providers"]["streaming_bridge"]
                assert sb["reachable"] is False
                assert sb["status"] == "offline"
                assert sb["port"] == 8089
                assert data["status"] == "ready"


def test_scripts_never_kill_bridge_by_port():
    """Verify scripts adhere to Rule B6: stop/restart uses recorded PID only, never kills bridge by port."""
    from pathlib import Path
    stop_all_path = Path("scripts/stop-all.sh")
    services_path = Path("scripts/lib/services.sh")
    stop_bridge_path = Path("scripts/stop-bridge.sh")

    assert stop_all_path.exists()
    assert services_path.exists()
    assert stop_bridge_path.exists()

    stop_all_content = stop_all_path.read_text(encoding="utf-8")
    services_content = services_path.read_text(encoding="utf-8")
    stop_bridge_content = stop_bridge_path.read_text(encoding="utf-8")

    # In stop-all.sh, verify streaming-bridge PID is cleaned up
    assert 'streaming-bridge.pid' in stop_all_content
    # In stop-all.sh, verify kill_port is NOT called on 8089 or bridge port
    assert 'kill_port 8089' not in stop_all_content
    assert 'kill_port "${STREAMING_BRIDGE_PORT' not in stop_all_content

    # In services.sh, verify stop_process_by_pid_file is called for streaming-bridge
    assert 'stop_process_by_pid_file "streaming-bridge"' in services_content

    # In stop-bridge.sh, verify PID termination is used and kill-by-port is absent
    assert 'kill -TERM "${PID}"' in stop_bridge_content
    assert 'kill_port' not in stop_bridge_content


def test_ports_script_bridge_inclusion_only_when_enabled():
    """Verify ports.sh registers and checks bridge port only when enabled."""
    from pathlib import Path
    ports_path = Path("scripts/lib/ports.sh")
    content = ports_path.read_text(encoding="utf-8")

    # Verify conditional check
    assert 'if [ "${STREAMING_BRIDGE_ENABLED:-false}" = "true" ]; then' in content
    assert 'STREAMING_BRIDGE_PORT="$(resolve_service_port "streaming-bridge"' in content
    assert 'ports_str="${ports_str} ${STREAMING_BRIDGE_PORT}"' in content

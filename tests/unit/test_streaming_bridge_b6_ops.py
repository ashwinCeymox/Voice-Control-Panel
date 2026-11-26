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


@pytest.mark.asyncio
async def test_health_ready_when_native_flag_enabled():
    """When STT_STREAM_NATIVE_ENABLED=True (even if STREAMING_BRIDGE_ENABLED=False), /health/ready checks :8089."""
    transport = ASGITransport(app=app)
    with patch.object(settings, "STT_STREAM_NATIVE_ENABLED", True), \
         patch.object(settings, "STREAMING_BRIDGE_ENABLED", False), \
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
                return Response(200, json={"status": "healthy", "active_streams": 0, "max_streams": 1})
            return Response(404)

        mock_inner_client.get = AsyncMock(side_effect=fake_get)
        mock_inner_client.__aenter__.return_value = mock_inner_client
        mock_inner_client.__aexit__.return_value = None

        with patch("app.api.health.httpx.AsyncClient", return_value=mock_inner_client):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/health/ready")
                assert resp.status_code == 200
                data = resp.json()
                assert data["providers"]["streaming_bridge"]["reachable"] is True
                assert data["providers"]["streaming_bridge"]["status"] == "ready"


def test_scripts_support_stt_stream_native_flag_and_docs():
    """Verify S4 scripts recognize STT_STREAM_NATIVE_ENABLED and migration doc exists."""
    from pathlib import Path
    for rel in (
        "scripts/lib/env.sh",
        "scripts/lib/services.sh",
        "scripts/preflight.sh",
        "scripts/start-all.sh",
        "scripts/start-bridge.sh",
    ):
        txt = Path(rel).read_text(encoding="utf-8")
        assert "STT_STREAM_NATIVE_ENABLED" in txt, f"Missing STT_STREAM_NATIVE_ENABLED in {rel}"

    assert Path("docs/STREAMING_BRIDGE_CHANGES.md").exists()
    changes_doc = Path("docs/STREAMING_BRIDGE_CHANGES.md").read_text(encoding="utf-8")
    assert "bridge-relay-v1" in changes_doc
    assert "bridge-calibrate.sh" in changes_doc
    assert "STT_STREAM_NATIVE_ENABLED" in changes_doc


def test_ops_map_script_syntax_and_no_secret_leak(tmp_path):
    """scripts/ops-map.sh must pass bash -n and never print .env secret values."""
    import subprocess
    from pathlib import Path

    script = Path("scripts/ops-map.sh").resolve()
    assert script.exists(), "scripts/ops-map.sh missing"

    syntax = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert syntax.returncode == 0, f"bash -n failed: {syntax.stderr}"

    fake_env = tmp_path / ".env"
    fake_secret_1 = "FAKE_ADMIN_SECRET_VALUE_987654"
    fake_secret_2 = "FAKE_BRIDGE_TOKEN_VALUE_123456"
    fake_env.write_text(
        f"ADMIN_API_KEY={fake_secret_1}\n"
        f"STREAMING_BRIDGE_INTERNAL_TOKEN={fake_secret_2}\n"
        "STT_STREAM_NATIVE_ENABLED=false\n",
        encoding="utf-8",
    )

    run = subprocess.run(
        ["bash", str(script)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, f"ops-map.sh failed: {run.stderr}"
    combined = run.stdout + "\n" + run.stderr
    assert "ADMIN_API_KEY" in run.stdout
    assert "STREAMING_BRIDGE_INTERNAL_TOKEN" in run.stdout
    assert "STT_STREAM_NATIVE_ENABLED" in run.stdout
    assert fake_secret_1 not in combined
    assert fake_secret_2 not in combined

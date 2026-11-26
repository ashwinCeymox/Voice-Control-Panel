"""Root test configuration and shared fixtures for all test suites."""

import asyncio
import json
import os
from typing import Any, Callable, Dict
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.storage.database import init_db


@pytest.fixture(autouse=True)
def hermetic_streaming_bridge_flags(monkeypatch):
    """Ensure unit tests never depend on .env streaming bridge flags."""
    bridge_env_keys = (
        "STT_STREAM_NATIVE_ENABLED",
        "STREAMING_BRIDGE_ENABLED",
        "STREAMING_BRIDGE_INTERNAL_TOKEN",
        "STREAMING_NATIVE_LIB_PATH",
        "STREAMING_NATIVE_OMP_NUM_THREADS",
        "STREAMING_NATIVE_GOMP_WAIT_POLICY",
        "STREAMING_NATIVE_OMP_PROC_BIND",
        "STREAMING_NATIVE_WORKER_NICE",
        "STREAMING_NATIVE_CPU_AFFINITY",
        "STREAMING_NATIVE_VOICE_PAUSE_ENABLED",
    )
    for key in bridge_env_keys:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setattr(settings, "STT_STREAM_NATIVE_ENABLED", False)
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_ENABLED", False)
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_INTERNAL_TOKEN", None)
    monkeypatch.setattr(settings, "STREAMING_NATIVE_LIB_PATH", None)
    monkeypatch.setattr(settings, "STREAMING_NATIVE_OMP_NUM_THREADS", None)
    monkeypatch.setattr(settings, "STREAMING_NATIVE_GOMP_WAIT_POLICY", None)
    monkeypatch.setattr(settings, "STREAMING_NATIVE_OMP_PROC_BIND", None)
    monkeypatch.setattr(settings, "STREAMING_NATIVE_WORKER_NICE", None)
    monkeypatch.setattr(settings, "STREAMING_NATIVE_CPU_AFFINITY", "")
    monkeypatch.setattr(settings, "STREAMING_NATIVE_VOICE_PAUSE_ENABLED", None)


@pytest.fixture(autouse=True)
def isolated_test_db(tmp_path, monkeypatch):
    """Ensure tests run against a clean, isolated SQLite database."""
    test_db_path = str(tmp_path / "test_voice_platform.db")
    monkeypatch.setattr(settings, "APP_DATABASE_PATH", test_db_path)
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "dev-voice-key-12345")
    asyncio.run(init_db())
    yield test_db_path


@pytest.fixture
def test_client():
    """Synchronous test client for WebSocket and HTTP operations."""
    return TestClient(app)


@pytest.fixture
def receive_event() -> Callable[[Any], Dict[str, Any]]:
    """Receive the next JSON text event, ignoring binary audio frames."""

    def _recv(ws) -> Dict[str, Any]:
        while True:
            msg = ws.receive()
            if "text" in msg:
                return json.loads(msg["text"])

    return _recv


@pytest.fixture
def wait_for_event(receive_event) -> Callable[[Any, str], Dict[str, Any]]:
    """Wait and consume messages until an event of event_type is received."""

    def _wait(ws, event_type: str) -> Dict[str, Any]:
        while True:
            evt = receive_event(ws)
            if evt.get("type") == event_type:
                return evt

    return _wait

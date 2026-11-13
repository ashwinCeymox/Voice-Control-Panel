"""Root test configuration and shared fixtures for all test suites."""
import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Dict
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.storage.database import init_db


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

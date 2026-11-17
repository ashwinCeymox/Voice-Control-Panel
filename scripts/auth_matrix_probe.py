#!/usr/bin/env python3
"""
Authentication Matrix Probe Script
Audits all routes against the 7 security credentials:
1. No key
2. Invalid key
3. Caller key (stt scope only)
4. Admin key
5. Expired key
6. Revoked key
7. Default dev key
"""
import sys
import os
import time
import asyncio
from typing import Dict, Any, List, Optional
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

# Set environment
os.environ["LOCAL_VOICE_AGENT_TESTING"] = "1"
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.main import app
from app.services.key_manager import KeyManager
from app.storage.database import init_db, get_db


async def setup_test_keys() -> Dict[str, str]:
    await init_db()
    now = time.time()

    # 1. Caller key (stt only)
    caller = await KeyManager.create_key(
        name="probe-caller-stt",
        role="caller",
        scopes=["stt"],
    )

    # 2. Admin key
    admin = await KeyManager.create_key(
        name="probe-admin-key",
        role="admin",
        scopes=["admin", "llm", "stt", "tts", "voice", "voice_stream", "stt_stream"],
    )

    # 3. Expired key
    expired = await KeyManager.create_key(
        name="probe-expired-key",
        role="caller",
        scopes=["stt", "llm"],
        expires_at=now - 3600.0,
    )

    # 4. Revoked key
    revoked = await KeyManager.create_key(
        name="probe-revoked-key",
        role="caller",
        scopes=["stt", "llm"],
    )
    await KeyManager.revoke_key(revoked["id"])

    return {
        "caller_stt": caller["key"],
        "admin": admin["key"],
        "expired": expired["key"],
        "revoked": revoked["key"],
        "dev_default": "dev-voice-key-12345",
        "invalid": "va-invalid-key-999-probe",
    }


def probe_http(client: TestClient, method: str, path: str, key: Optional[str] = None, json_data=None, files=None) -> int:
    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if path.startswith("/v1/panel") and method == "POST":
        headers["X-Requested-With"] = "XMLHttpRequest"
    try:
        if method == "GET":
            r = client.get(path, headers=headers)
        elif method == "POST":
            r = client.post(path, headers=headers, json=json_data, files=files)
        elif method == "PATCH":
            r = client.patch(path, headers=headers, json=json_data)
        elif method == "PUT":
            r = client.put(path, headers=headers, json=json_data)
        elif method == "DELETE":
            r = client.delete(path, headers=headers)
        else:
            return -1
        return r.status_code
    except Exception as e:
        return 500


def probe_ws(client: TestClient, path: str, key: Optional[str] = None) -> int:
    headers = {}
    url = path
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        with client.websocket_connect(url, headers=headers) as ws:
            return 200  # Successfully connected/accepted
    except WebSocketDisconnect as e:
        return e.code
    except Exception:
        return 500


async def run_audit():
    keys = await setup_test_keys()
    client = TestClient(app)

    # Dummy payload definitions
    dummy_chat = {"model": "test-model", "messages": [{"role": "user", "content": "hi"}]}
    dummy_wav = ("test.wav", b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00", "audio/wav")
    dummy_tts = {"input": "hello"}
    dummy_override = {"task": "llm"}
    dummy_agent = {"id": "probe-agent", "name": "Probe", "system_prompt": "hello"}
    dummy_install = {"model_id": "probe-model"}

    routes = [
        # (category, method, path, json_data, files, expected_caller)
        ("Public", "GET", "/health/live", None, None, "200 (public)"),
        ("Public", "GET", "/health/ready", None, None, "200 (public)"),
        ("Monitoring", "GET", "/metrics", None, None, "200 (public)"),
        ("Inference", "POST", "/v1/chat/completions", dummy_chat, None, "403 (needs llm scope)"),
        ("Inference", "POST", "/v1/audio/transcriptions", None, {"file": dummy_wav}, "200/502 (has stt scope)"),
        ("Inference", "POST", "/v1/text-to-speech/af_bella/stream", dummy_tts, None, "403 (needs tts scope)"),
        ("Inference", "POST", "/v1/voice/turn", None, {"file": dummy_wav}, "403 (needs voice scope)"),
        ("Models", "GET", "/v1/models", None, None, "200 (any valid key)"),
        ("Models", "POST", "/v1/models/install", dummy_install, None, "403 (admin only)"),
        ("Models", "DELETE", "/v1/models/test-model", None, None, "403 (admin only)"),
        ("Models", "PATCH", "/v1/models/test-model/classification", dummy_override, None, "403 (admin only)"),
        ("Agents", "GET", "/v1/agents", None, None, "403 (admin only)"),
        ("Agents", "POST", "/v1/agents", dummy_agent, None, "403 (admin only)"),
        ("Agents", "GET", "/v1/agents/default", None, None, "403 (admin only)"),
        ("Agents", "DELETE", "/v1/agents/probe-agent", None, None, "403 (admin only)"),
        ("Admin", "GET", "/v1/admin/keys", None, None, "403 (admin only)"),
        ("Admin", "POST", "/v1/admin/keys", {"name": "test"}, None, "403 (admin only)"),
        ("Admin", "DELETE", "/v1/admin/keys/dummy-id", None, None, "403 (admin only)"),
        ("Admin", "GET", "/v1/admin/models", None, None, "403 (admin only)"),
        ("Admin", "POST", "/v1/admin/models/rescan", None, None, "403 (admin only)"),
        ("Admin", "GET", "/v1/admin/analytics/usage", None, None, "403 (admin only)"),
        ("Admin", "GET", "/v1/admin/analytics/export", None, None, "403 (admin only)"),
        ("Panel", "POST", "/v1/panel/login", {"email": "test@test.com", "password": "wrong"}, None, "401 (auth check)"),
        ("Panel", "GET", "/v1/panel/me", None, None, "401 (needs session/admin)"),
        ("Panel", "POST", "/v1/panel/logout", None, None, "401 (needs session/admin)"),
        ("Panel", "POST", "/v1/panel/change-password", {"old_password": "a", "new_password": "b"}, None, "401 (needs session/admin)"),
        ("Docs", "GET", "/docs", None, None, "404 (disabled in prod)"),
        ("Docs", "GET", "/redoc", None, None, "404 (disabled in prod)"),
        ("Docs", "GET", "/openapi.json", None, None, "404 (disabled in prod)"),
    ]

    ws_routes = [
        ("WS Streaming", "WS", "/v1/voice/stream", "4403 (needs voice scope)"),
        ("WS Streaming", "WS", "/v1/audio/transcriptions/stream", "4403 (needs stt_stream scope)"),
    ]

    print("| Route | Method | No key | Invalid key | Caller key (STT) | Admin key | Expired key | Revoked key | Dev default key | Result / Expectation |")
    print("|---|---|---|---|---|---|---|---|---|---|")

    for cat, method, path, jdata, files, exp in routes:
        # 1. No key
        c_none = probe_http(client, method, path, key=None, json_data=jdata, files=files)
        # 2. Invalid key
        c_inv = probe_http(client, method, path, key=keys["invalid"], json_data=jdata, files=files)
        # 3. Caller key (stt only)
        c_caller = probe_http(client, method, path, key=keys["caller_stt"], json_data=jdata, files=files)
        # 4. Admin key
        c_admin = probe_http(client, method, path, key=keys["admin"], json_data=jdata, files=files)
        # 5. Expired key
        c_exp = probe_http(client, method, path, key=keys["expired"], json_data=jdata, files=files)
        # 6. Revoked key
        c_rev = probe_http(client, method, path, key=keys["revoked"], json_data=jdata, files=files)
        # 7. Dev default key
        c_dev = probe_http(client, method, path, key=keys["dev_default"], json_data=jdata, files=files)

        # Simplify representation
        def fmt(code: int) -> str:
            if code in (200, 201, 204):
                return f"`{code} OK`"
            if code == 401:
                return "`401 Unauth`"
            if code == 403:
                return "`403 Forbid`"
            if code == 404:
                return "`404 NotFnd`"
            if code == 400:
                return "`400 BadReq`"
            return f"`{code}`"

        print(f"| `{path}` | {method} | {fmt(c_none)} | {fmt(c_inv)} | {fmt(c_caller)} | {fmt(c_admin)} | {fmt(c_exp)} | {fmt(c_rev)} | {fmt(c_dev)} | {exp} |")

    # WS routes
    for cat, method, path, exp in ws_routes:
        c_none = probe_ws(client, path, key=None)
        c_inv = probe_ws(client, path, key=keys["invalid"])
        c_caller = probe_ws(client, path, key=keys["caller_stt"])
        c_admin = probe_ws(client, path, key=keys["admin"])
        c_exp = probe_ws(client, path, key=keys["expired"])
        c_rev = probe_ws(client, path, key=keys["revoked"])
        c_dev = probe_ws(client, path, key=keys["dev_default"])

        def fmt_ws(code: int) -> str:
            if code == 200:
                return "`200 Connect`"
            if code in (4401, 401):
                return f"`{code} Unauth`"
            if code in (4403, 403):
                return f"`{code} Forbid`"
            return f"`{code}`"

        print(f"| `{path}` | WS | {fmt_ws(c_none)} | {fmt_ws(c_inv)} | {fmt_ws(c_caller)} | {fmt_ws(c_admin)} | {fmt_ws(c_exp)} | {fmt_ws(c_rev)} | {fmt_ws(c_dev)} | {exp} |")


if __name__ == "__main__":
    asyncio.run(run_audit())

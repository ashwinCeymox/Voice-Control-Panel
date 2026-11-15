"""
Unit tests for Task D1-BACKEND: Profiles CRUD, Key extensions (profile_id, status,
pause/resume, rotation, model & IP allowlists), Dashboard Settings, and Admin Audit Log.
"""
import time
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.main import app
from app.services.key_manager import KeyManager
from app.storage.database import init_db


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    await init_db()


@pytest.mark.asyncio
async def test_profile_crud_api():
    """Admin can create, get, list, update, and delete application profiles."""
    admin_key = await KeyManager.create_key("admin", role="admin", scopes=["admin"])
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {admin_key['key']}"}

    # 1. Create Profile
    create_resp = client.post(
        "/v1/admin/profiles",
        json={
            "name": "telephony-service",
            "description": "Inbound SIP PBX gateway",
            "contact": "ops@example.com",
            "notes": "Project code: TEL-42",
        },
        headers=headers,
    )
    assert create_resp.status_code == 201
    profile = create_resp.json()
    assert profile["name"] == "telephony-service"
    assert profile["contact"] == "ops@example.com"
    profile_id = profile["id"]

    # 2. Get Profile
    get_resp = client.get(f"/v1/admin/profiles/{profile_id}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == profile_id

    # 3. List Profiles
    list_resp = client.get("/v1/admin/profiles", headers=headers)
    assert list_resp.status_code == 200
    assert any(p["id"] == profile_id for p in list_resp.json())

    # 4. Patch Profile
    patch_resp = client.patch(
        f"/v1/admin/profiles/{profile_id}",
        json={"notes": "Updated notes", "disabled": True},
        headers=headers,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["notes"] == "Updated notes"
    assert patch_resp.json()["disabled_at"] is not None

    # 5. Delete Profile
    del_resp = client.delete(f"/v1/admin/profiles/{profile_id}", headers=headers)
    assert del_resp.status_code == 200


@pytest.mark.asyncio
async def test_key_linked_to_profile_and_prefix_visible():
    """Creating a key with profile_id links it to the profile and generates a visible prefix."""
    admin_key = await KeyManager.create_key("admin", role="admin", scopes=["admin"])
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {admin_key['key']}"}

    # Create profile
    prof_resp = client.post(
        "/v1/admin/profiles",
        json={"name": "analytics-worker"},
        headers=headers,
    )
    prof_id = prof_resp.json()["id"]

    # Create key linked to profile
    key_resp = client.post(
        "/v1/admin/keys",
        json={
            "name": "worker-1",
            "profile_id": prof_id,
            "scopes": ["stt"],
            "model_allowlist": ["whisper-base"],
            "ip_allowlist": ["192.168.1.0/24"],
        },
        headers=headers,
    )
    assert key_resp.status_code == 201
    data = key_resp.json()
    assert data["profile_id"] == prof_id
    assert "prefix" in data
    assert data["prefix"].startswith("va_")
    assert "whisper-base" in data["model_allowlist"]

    # Profile detail lists the key
    detail_resp = client.get(f"/v1/admin/profiles/{prof_id}", headers=headers)
    assert detail_resp.status_code == 200
    keys = detail_resp.json().get("keys", [])
    assert len(keys) >= 1
    assert keys[0]["id"] == data["id"]
    assert "key" not in keys[0]  # Plain key never returned in lists


@pytest.mark.asyncio
async def test_disabled_profile_blocks_key():
    """Disabling a profile blocks authentication for all of its keys."""
    admin_key = await KeyManager.create_key("admin", role="admin", scopes=["admin"])
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {admin_key['key']}"}

    prof_resp = client.post("/v1/admin/profiles", json={"name": "crm-sync"}, headers=headers)
    prof_id = prof_resp.json()["id"]

    key_resp = client.post(
        "/v1/admin/keys",
        json={"name": "crm-key", "profile_id": prof_id, "scopes": ["llm"]},
        headers=headers,
    )
    plain_key = key_resp.json()["key"]

    # 1. Key works when profile is active
    chat_resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},
        headers={"Authorization": f"Bearer {plain_key}"},
    )
    assert chat_resp.status_code in (200, 502, 503)  # Reached application logic

    # 2. Disable profile
    client.patch(f"/v1/admin/profiles/{prof_id}", json={"disabled": True}, headers=headers)

    # 3. Key is now rejected with 401
    blocked_resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},
        headers={"Authorization": f"Bearer {plain_key}"},
    )
    assert blocked_resp.status_code == 401


@pytest.mark.asyncio
async def test_key_pause_and_resume():
    """Admin can pause and resume a key via PATCH /v1/admin/keys/{id}."""
    admin_key = await KeyManager.create_key("admin", role="admin", scopes=["admin"])
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {admin_key['key']}"}

    key_resp = client.post(
        "/v1/admin/keys",
        json={"name": "batch-processor", "scopes": ["llm"]},
        headers=headers,
    )
    key_id = key_resp.json()["id"]
    plain_key = key_resp.json()["key"]

    # Pause key
    patch_resp = client.patch(
        f"/v1/admin/keys/{key_id}",
        json={"status": "paused"},
        headers=headers,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["status"] == "paused"

    # Paused key fails authentication
    req_resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "ping"}]},
        headers={"Authorization": f"Bearer {plain_key}"},
    )
    assert req_resp.status_code == 401

    # Resume key
    client.patch(
        f"/v1/admin/keys/{key_id}",
        json={"status": "active"},
        headers=headers,
    )
    # Resumed key succeeds
    req_resp2 = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "ping"}]},
        headers={"Authorization": f"Bearer {plain_key}"},
    )
    assert req_resp2.status_code != 401


@pytest.mark.asyncio
async def test_key_rotation_with_grace_period():
    """Rotating a key creates a new key while old key remains valid until grace period expires."""
    admin_key = await KeyManager.create_key("admin", role="admin", scopes=["admin"])
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {admin_key['key']}"}

    key_resp = client.post(
        "/v1/admin/keys",
        json={"name": "api-service", "scopes": ["llm"]},
        headers=headers,
    )
    old_key_id = key_resp.json()["id"]
    old_plain_key = key_resp.json()["key"]

    # Rotate with 1-day grace period
    rot_resp = client.post(
        f"/v1/admin/keys/{old_key_id}/rotate",
        json={"grace_period_days": 1.0},
        headers=headers,
    )
    assert rot_resp.status_code == 201
    rot_data = rot_resp.json()
    new_plain_key = rot_data["key"]
    assert new_plain_key != old_plain_key
    assert rot_data["rotated_from"] == old_key_id

    # Both old and new keys work during grace period
    r_old = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {old_plain_key}"},
    )
    assert r_old.status_code != 401

    r_new = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {new_plain_key}"},
    )
    assert r_new.status_code != 401


@pytest.mark.asyncio
async def test_key_model_allowlist_enforcement():
    """Key with model_allowlist rejects calls to disallowed models with 403."""
    admin_key = await KeyManager.create_key("admin", role="admin", scopes=["admin"])
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {admin_key['key']}"}

    key_resp = client.post(
        "/v1/admin/keys",
        json={
            "name": "strict-model-key",
            "scopes": ["stt"],
            "model_allowlist": ["whisper-base"],
        },
        headers=headers,
    )
    plain_key = key_resp.json()["key"]

    # Request with disallowed model 'whisper-tiny' is rejected
    resp = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("test.wav", b"RIFF....", "audio/wav")},
        data={"model": "whisper-tiny"},
        headers={"Authorization": f"Bearer {plain_key}"},
    )
    assert resp.status_code == 403
    assert "not allowed for this key" in resp.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_dashboard_settings_and_audit_log():
    """Admin can get/update dashboard settings and view the audit log."""
    admin_key = await KeyManager.create_key("admin", role="admin", scopes=["admin"])
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {admin_key['key']}"}

    # 1. Update settings
    put_resp = client.put(
        "/v1/admin/settings/dashboard",
        json={
            "base_url": "http://192.168.1.100:8000",
            "default_rate_limit": 120,
            "default_max_concurrent": 10,
            "usage_retention_days": 60,
        },
        headers=headers,
    )
    assert put_resp.status_code == 200
    data = put_resp.json()
    assert data["base_url"] == "http://192.168.1.100:8000"
    assert data["usage_retention_days"] == 60

    # 2. Get settings
    get_resp = client.get("/v1/admin/settings/dashboard", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["base_url"] == "http://192.168.1.100:8000"

    # 3. View audit log
    audit_resp = client.get("/v1/admin/audit", headers=headers)
    assert audit_resp.status_code == 200
    audit_entries = audit_resp.json()
    assert len(audit_entries) >= 1
    assert any(e["action"] == "settings.update" for e in audit_entries)

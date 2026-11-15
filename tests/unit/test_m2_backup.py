"""
Unit tests for Task M2-BACKUP: Automated SQLite snapshot backup script and admin backup endpoint.
Tests VACUUM INTO snapshots, integrity verification, admin endpoint auth, and backup listing.
"""
import os
import subprocess
import aiosqlite
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
async def test_admin_backup_endpoint_creates_valid_snapshot():
    """POST /v1/admin/backup creates an atomic SQLite snapshot verified with integrity_check."""
    admin_key = await KeyManager.create_key(
        name="backup-admin",
        role="admin",
        scopes=["admin"],
    )
    plain_key = admin_key["key"]

    client = TestClient(app)
    resp = client.post(
        "/v1/admin/backup",
        headers={"Authorization": f"Bearer {plain_key}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["integrity"] == "ok"
    assert data["size_bytes"] > 0
    assert os.path.exists(data["backup_path"])

    # Verify the snapshot database can be opened and queried
    async with aiosqlite.connect(data["backup_path"]) as db:
        async with db.execute("PRAGMA integrity_check;") as cursor:
            row = await cursor.fetchone()
            assert row[0] == "ok"

        # Check tables exist
        async with db.execute("SELECT count(*) FROM api_keys;") as cursor:
            row = await cursor.fetchone()
            assert row[0] >= 1


@pytest.mark.asyncio
async def test_admin_backup_requires_admin_role():
    """POST /v1/admin/backup rejects non-admin caller keys with 403 Forbidden."""
    caller_key = await KeyManager.create_key(
        name="regular-caller",
        role="caller",
        scopes=["llm", "stt"],
    )
    plain_key = caller_key["key"]

    client = TestClient(app)
    resp = client.post(
        "/v1/admin/backup",
        headers={"Authorization": f"Bearer {plain_key}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_list_backups():
    """GET /v1/admin/backups lists available database snapshots."""
    admin_key = await KeyManager.create_key(
        name="backup-viewer",
        role="admin",
        scopes=["admin"],
    )
    plain_key = admin_key["key"]

    client = TestClient(app)
    # Trigger a backup first
    create_resp = client.post(
        "/v1/admin/backup",
        headers={"Authorization": f"Bearer {plain_key}"},
    )
    assert create_resp.status_code == 200

    list_resp = client.get(
        "/v1/admin/backups",
        headers={"Authorization": f"Bearer {plain_key}"},
    )
    assert list_resp.status_code == 200
    backups = list_resp.json()
    assert isinstance(backups, list)
    assert len(backups) >= 1
    assert "filename" in backups[0]
    assert "size_bytes" in backups[0]


def test_backup_script_execution():
    """scripts/backup-db.sh runs successfully and produces an intact snapshot."""
    script_path = "scripts/backup-db.sh"
    assert os.path.exists(script_path), f"Backup script missing at {script_path}"

    result = subprocess.run(
        ["bash", script_path],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Script failed with: {result.stderr}"
    assert "Backup completed successfully" in result.stdout or "OK" in result.stdout


def test_restore_script_execution(tmp_path):
    """scripts/restore-db.sh verifies snapshot and restores database cleanly."""
    backup_script = "scripts/backup-db.sh"
    restore_script = "scripts/restore-db.sh"
    assert os.path.exists(restore_script), f"Restore script missing at {restore_script}"

    # 1. Produce a backup
    subprocess.run(["bash", backup_script], check=True, capture_output=True)

    # Find the newest backup in data/backups
    backup_dir = "data/backups"
    backups = sorted([os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.endswith(".db")])
    assert len(backups) > 0
    latest_backup = backups[-1]

    # 2. Restore to a new location
    target_restore_db = str(tmp_path / "restored_voice_platform.db")
    res = subprocess.run(
        ["bash", restore_script, latest_backup, target_restore_db],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Restore failed: {res.stderr}"
    assert "Restore completed successfully" in res.stdout
    assert os.path.exists(target_restore_db)

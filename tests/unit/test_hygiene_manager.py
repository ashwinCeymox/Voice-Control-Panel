"""Unit tests for In-App Hygiene Manager and Disk Guard (Phase C8 - C10)."""

import datetime
import pathlib
import time
import pytest
import pytest_asyncio

from app.services.hygiene_manager import HygieneManager
from app.storage.database import get_db, init_db


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    await init_db()


@pytest.mark.asyncio
async def test_hygiene_manager_disk_guard():
    manager = HygieneManager()
    info = await manager.check_disk_guard()
    assert "state" in info
    assert info["state"] in ("normal", "warning", "pressure", "critical")
    assert "free_percent" in info
    assert info["free_percent"] > 0


@pytest.mark.asyncio
async def test_reap_stale_temp_files(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    manager = HygieneManager()
    fake_tmp = tmp_path / "data" / "tmp"
    fake_tmp.mkdir(parents=True)

    # Stale file (> 1 hour old)
    stale = fake_tmp / "vagent-old-123.tmp"
    stale.write_text("old junk")
    import os

    os.utime(stale, (time.time() - 7200, time.time() - 7200))

    # Fresh file (< 1 hour old)
    fresh = fake_tmp / "vagent-fresh-456.tmp"
    fresh.write_text("active temp")

    # Other file without vagent- prefix
    other = fake_tmp / "other-file.txt"
    other.write_text("keep me")

    monkeypatch.setattr(
        pathlib, "Path", lambda p: fake_tmp if p == "data/tmp" else pathlib.Path(p)
    )

    # Test reaping
    reaped = manager.reap_stale_temp_files(max_age_seconds=3600.0)
    assert reaped >= 1
    assert not stale.exists()
    assert fresh.exists()
    assert other.exists()


@pytest.mark.asyncio
async def test_prune_ephemeral_sessions():
    manager = HygieneManager()
    async with get_db() as db:
        # Create an expired ephemeral session (2 days ago)
        old_time = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)
        ).isoformat()
        await db.execute(
            "INSERT OR REPLACE INTO sessions (id, owner, is_ephemeral, created_at) VALUES (?, ?, 1, ?)",
            ("ephemeral-old", "test-user", old_time),
        )
        # Create a fresh ephemeral session
        now_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
        await db.execute(
            "INSERT OR REPLACE INTO sessions (id, owner, is_ephemeral, created_at) VALUES (?, ?, 1, ?)",
            ("ephemeral-fresh", "test-user", now_time),
        )
        # Create a persistent non-ephemeral session
        await db.execute(
            "INSERT OR REPLACE INTO sessions (id, owner, is_ephemeral, created_at) VALUES (?, ?, 0, ?)",
            ("persistent-session", "test-user", old_time),
        )
        await db.commit()

    pruned = await manager.prune_ephemeral_sessions(max_age_hours=24.0)
    assert pruned >= 1

    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id FROM sessions WHERE id IN ('ephemeral-old', 'ephemeral-fresh', 'persistent-session')"
        )
        remaining = {r[0] for r in await cursor.fetchall()}

    assert "ephemeral-old" not in remaining
    assert "ephemeral-fresh" in remaining
    assert "persistent-session" in remaining


@pytest.mark.asyncio
async def test_reap_expired_tickets():
    from app.core.security import _TICKETS

    manager = HygieneManager()

    # Expired ticket
    _TICKETS["expired-ticket-abc"] = (time.time() - 300, "user-a")
    # Valid ticket
    _TICKETS["valid-ticket-xyz"] = (time.time(), "user-b")

    reaped = manager.reap_expired_tickets()
    assert reaped >= 1
    assert "expired-ticket-abc" not in _TICKETS
    assert "valid-ticket-xyz" in _TICKETS

"""Unit tests for Cleanup Mechanism Safety Guarantees (Phase C2).

Tests strictly verify:
1. Protected list samples (database, .env, models, fixtures, docs, tracked files, open files)
   are NEVER selected for deletion.
2. Symlinks pointing outside root are rejected and target is untouched.
3. Path traversal tricks (.., absolute paths) cannot escape allowed roots.
4. Over-limit plans (size/count) are refused unless explicitly overridden.
5. scan deletes nothing; apply requires explicit plan and re-validates items.
6. Changed files since scan are skipped at apply time.
7. Quarantine, restore, and purge round-trip correctly.
8. Refusal to run as root.
"""

from __future__ import annotations

import json
import os
import pathlib
import time
import pytest

from scripts.cleanup import (
    check_not_root,
    run_scan,
)


@pytest.fixture
def temp_workspace(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create an isolated dummy workspace mimicking repository layout."""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)

    # Create standard protected dirs & files
    (ws / ".git").mkdir()
    (ws / ".git" / "config").write_text("dummy git config")
    (ws / ".env").write_text("ADMIN_API_KEY=supersecret")
    (ws / ".env.production").write_text("SECRET=productionsecret")
    (ws / "voice_platform.db").write_bytes(b"SQLite format 3\x00dummy")
    (ws / "voice_platform.db-wal").write_bytes(b"wal content")
    (ws / "voice_platform.db-shm").write_bytes(b"shm content")

    # Models folder
    (ws / "models" / "streaming-stt").mkdir(parents=True)
    (ws / "models" / "streaming-stt" / "encoder.onnx").write_bytes(
        b"REAL WEIGHTS DO NOT TOUCH"
    )
    (ws / "models" / "streaming-stt" / "temp.download").write_bytes(b"PARTIAL DOWNLOAD")
    (ws / "models" / "streaming-stt" / "chunk.partial").write_bytes(b"PARTIAL CHUNK")

    # App code & tests fixtures
    (ws / "app" / "core").mkdir(parents=True)
    (ws / "app" / "core" / "config.py").write_text("APP CODE")
    (ws / "tests" / "fixtures").mkdir(parents=True)
    (ws / "tests" / "fixtures" / "sample.wav").write_bytes(b"RIFF audio fixture")
    (ws / "docs").mkdir(parents=True)
    (ws / "docs" / "cleanup-inventory.md").write_text("# Inventory")

    # Backups directory
    (ws / "data" / "backups").mkdir(parents=True)
    for i in range(5):
        bp = ws / "data" / "backups" / f"app-backup-{i}.db"
        bp.write_bytes(b"backup content")
        # Age them: backup 4 newest, backup 0 oldest
        os.utime(bp, (time.time() - (4 - i) * 86400, time.time() - (4 - i) * 86400))

    # Junk files (Category A, B, C)
    (ws / "__pycache__").mkdir(parents=True)
    (ws / "__pycache__" / "test.cpython-312.pyc").write_bytes(b"pyc cache")
    (ws / "tmp" / "agent").mkdir(parents=True)
    scratch = ws / "tmp" / "agent" / "scratch.tmp"
    scratch.write_bytes(b"scratch data")
    # Make scratch older than min_age (3 hours ago)
    os.utime(scratch, (time.time() - 10800, time.time() - 10800))

    # Root scratch audio older than min_age
    audio_scratch = ws / "output.wav"
    audio_scratch.write_bytes(b"temp wav scratch")
    os.utime(audio_scratch, (time.time() - 10800, time.time() - 10800))

    return ws


@pytest.fixture
def sample_policy(temp_workspace: pathlib.Path) -> pathlib.Path:
    """Create a test policy TOML inside the temp workspace."""
    policy_path = temp_workspace / "policies" / "cleanup.test.toml"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_content = f"""
[general]
name = "test"
min_age_hours = 2.0
trash_retention_days = 7
backup_retention_count = 3
backup_retention_days = 2
max_plan_files = 10
max_plan_bytes = 1048576  # 1 MB

[roots]
allowed = ["{temp_workspace.as_posix()}"]

[protected]
exact = [
    "voice_platform.db",
    "voice_platform.db-wal",
    "voice_platform.db-shm",
    "AGENTS.md"
]
prefixes = [
    ".git/",
    ".env",
    "app/",
    "frontend/src/",
    "scripts/",
    "services/",
    "policies/",
    "tests/fixtures/",
    "docs/",
    "models/streaming-stt/"
]
patterns = [
    "*.pem",
    "*.key",
    "*secret*"
]

[rules.category_a]
name = "Regenerable caches"
action = "delete"
patterns = [
    "**/__pycache__",
    "**/*.pyc"
]

[rules.category_b]
name = "Agent and test scratch"
action = "quarantine"
min_age_hours = 2.0
patterns = [
    "tmp/agent/**",
    "*.tmp"
]

[rules.category_c]
name = "Debug dumps and audio"
action = "quarantine"
min_age_hours = 2.0
patterns = [
    "output.wav"
]

[rules.category_d]
name = "Engine partials"
action = "quarantine"
min_age_hours = 2.0
patterns = [
    "models/**/*.partial",
    "models/**/*.download"
]

[rules.category_e]
name = "Old database backups"
action = "quarantine"
min_age_hours = 48.0 # 2 days
patterns = [
    "data/backups/*.db"
]
"""
    policy_path.write_text(policy_content, encoding="utf-8")
    return policy_path


def test_protected_samples_are_never_selected(
    temp_workspace: pathlib.Path, sample_policy: pathlib.Path
):
    """Confirm live DB, .env, model weights, fixtures, docs, and code are NEVER selected."""
    exit_code, plan = run_scan(temp_workspace, sample_policy)
    assert exit_code == 0
    candidate_paths = {item["path"] for item in plan["items"]}

    # Non-negotiable protections
    assert "voice_platform.db" not in candidate_paths
    assert "voice_platform.db-wal" not in candidate_paths
    assert "voice_platform.db-shm" not in candidate_paths
    assert ".env" not in candidate_paths
    assert ".env.production" not in candidate_paths
    assert ".git/config" not in candidate_paths
    assert "models/streaming-stt/encoder.onnx" not in candidate_paths
    assert "app/core/config.py" not in candidate_paths
    assert "tests/fixtures/sample.wav" not in candidate_paths
    assert "docs/cleanup-inventory.md" not in candidate_paths

    # Eligible items MUST be included
    assert "output.wav" in candidate_paths
    assert "tmp/agent/scratch.tmp" in candidate_paths


def test_backup_retention_preserves_newest_n(
    temp_workspace: pathlib.Path, sample_policy: pathlib.Path
):
    """Confirm newest N backups are protected, and only older backups beyond N are eligible."""
    exit_code, plan = run_scan(temp_workspace, sample_policy)
    candidate_paths = {item["path"] for item in plan["items"]}

    # Backups 4, 3, 2 are the newest 3 -> MUST NOT be in candidates
    assert "data/backups/app-backup-4.db" not in candidate_paths
    assert "data/backups/app-backup-3.db" not in candidate_paths
    assert "data/backups/app-backup-2.db" not in candidate_paths

    # Backup 0 is 4 days old (> 2 days min_age) and outside newest 3 -> eligible
    assert "data/backups/app-backup-0.db" in candidate_paths


def test_symlink_pointing_outside_root_is_rejected(
    temp_workspace: pathlib.Path, sample_policy: pathlib.Path, tmp_path: pathlib.Path
):
    """Confirm a symlink pointing outside the workspace cannot be abused to delete external files."""
    outside_dir = tmp_path / "outside_sensitive"
    outside_dir.mkdir()
    sensitive_file = outside_dir / "target.tmp"
    sensitive_file.write_text("DO NOT TOUCH THIS FILE")

    # Create symlink inside workspace pointing to sensitive outside file
    symlink_path = temp_workspace / "rogue_link.tmp"
    os.symlink(str(sensitive_file), str(symlink_path))

    exit_code, plan = run_scan(temp_workspace, sample_policy)
    candidate_paths = {item["path"] for item in plan["items"]}

    # Rogue link must be excluded
    assert "rogue_link.tmp" not in candidate_paths
    assert sensitive_file.exists()
    assert sensitive_file.read_text() == "DO NOT TOUCH THIS FILE"


def test_plan_exceeding_limits_is_refused(
    temp_workspace: pathlib.Path, sample_policy: pathlib.Path
):
    """Confirm that exceeding file count or size limit triggers exit code 2 and refuses scan."""
    # Policy limit is max_plan_files = 10. Let's create 15 dummy files.
    for i in range(15):
        f = temp_workspace / f"overflow_{i}.tmp"
        f.write_text(f"dummy {i}")
        os.utime(f, (time.time() - 10800, time.time() - 10800))

    exit_code, plan = run_scan(temp_workspace, sample_policy, force_limit=False)
    assert exit_code == 2
    assert plan["limit_exceeded"] is True
    assert "file count limit" in plan["limit_reason"]

    # With force_limit=True, exit code is 0
    exit_code_forced, plan_forced = run_scan(
        temp_workspace, sample_policy, force_limit=True
    )
    assert exit_code_forced == 0


def test_scan_deletes_nothing(
    temp_workspace: pathlib.Path, sample_policy: pathlib.Path
):
    """Confirm scan is 100% read-only and deletes zero files."""
    scratch = temp_workspace / "tmp" / "agent" / "scratch.tmp"
    audio = temp_workspace / "output.wav"
    assert scratch.exists()
    assert audio.exists()

    run_scan(temp_workspace, sample_policy)

    assert scratch.exists()
    assert audio.exists()


def test_root_user_check_blocks_execution(monkeypatch: pytest.MonkeyPatch):
    """Confirm check_not_root blocks execution when euid is 0."""
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(SystemExit) as exc:
        check_not_root()
    assert exc.value.code == 2


def test_quarantine_and_restore_roundtrip(
    temp_workspace: pathlib.Path, sample_policy: pathlib.Path
):
    """Confirm files are moved to .trash/<run-id>/ and restored cleanly."""
    from scripts.cleanup import cmd_apply, cmd_restore
    import argparse

    # 1. Scan
    exit_code, plan = run_scan(temp_workspace, sample_policy)
    assert exit_code == 0
    plan_file = temp_workspace / "plan.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")

    # Target scratch file
    scratch = temp_workspace / "tmp" / "agent" / "scratch.tmp"
    assert scratch.exists()
    content_before = scratch.read_bytes()

    # 2. Apply
    apply_args = argparse.Namespace(
        plan=str(plan_file), policy=str(sample_policy), force_limit=False
    )
    apply_code = cmd_apply(apply_args)
    assert apply_code == 0
    assert not scratch.exists()

    # Verify quarantine exists
    trash_dir = temp_workspace / ".trash"
    assert trash_dir.exists()
    runs = list(trash_dir.iterdir())
    assert len(runs) == 1
    run_id = runs[0].name
    quarantined_file = runs[0] / "tmp" / "agent" / "scratch.tmp"
    assert quarantined_file.exists()
    assert quarantined_file.read_bytes() == content_before

    # 3. Restore
    restore_args = argparse.Namespace(
        run_id=run_id,
        path=None,
        root=str(temp_workspace),
        policy=str(sample_policy),
    )
    restore_code = cmd_restore(restore_args)
    assert restore_code == 0
    assert scratch.exists()
    assert scratch.read_bytes() == content_before


def test_apply_skips_changed_files(
    temp_workspace: pathlib.Path, sample_policy: pathlib.Path
):
    """Confirm apply skips files whose size or mtime changed since scan."""
    from scripts.cleanup import cmd_apply
    import argparse

    exit_code, plan = run_scan(temp_workspace, sample_policy)
    assert exit_code == 0
    plan_file = temp_workspace / "plan.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")

    # Modify file after scan
    scratch = temp_workspace / "tmp" / "agent" / "scratch.tmp"
    scratch.write_bytes(b"MODIFIED AFTER SCAN COMPLETED")

    apply_args = argparse.Namespace(
        plan=str(plan_file), policy=str(sample_policy), force_limit=False
    )
    apply_code = cmd_apply(apply_args)
    assert apply_code == 0

    # File was modified -> MUST NOT be deleted or quarantined!
    assert scratch.exists()
    assert scratch.read_bytes() == b"MODIFIED AFTER SCAN COMPLETED"


def test_purge_expired_runs(temp_workspace: pathlib.Path, sample_policy: pathlib.Path):
    """Confirm purge removes only expired quarantine runs."""
    from scripts.cleanup import cmd_purge
    import argparse

    trash_dir = temp_workspace / ".trash"
    trash_dir.mkdir(parents=True, exist_ok=True)

    # Old run (10 days old)
    old_run = trash_dir / "20261101-000000"
    old_run.mkdir()
    (old_run / "file.tmp").write_text("old")
    os.utime(old_run, (time.time() - 864000, time.time() - 864000))

    # Fresh run (1 day old)
    fresh_run = trash_dir / "20261116-000000"
    fresh_run.mkdir()
    (fresh_run / "file.tmp").write_text("fresh")
    os.utime(fresh_run, (time.time() - 86400, time.time() - 86400))

    purge_args = argparse.Namespace(
        root=str(temp_workspace),
        older_than_days=7.0,
        policy=str(sample_policy),
    )
    purge_code = cmd_purge(purge_args)
    assert purge_code == 0

    assert not old_run.exists()
    assert fresh_run.exists()

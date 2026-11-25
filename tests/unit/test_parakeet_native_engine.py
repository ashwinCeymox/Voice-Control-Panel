"""Unit tests for Phase S2: ParakeetNativeEngine worker launcher, ABI gate, and crash isolation."""

import sys
import textwrap
import pytest

from services.streaming_bridge.engines.base import EngineABIMismatchError, EngineCrashedError
from services.streaming_bridge.engines.parakeet_native import (
    ParakeetNativeEngine,
    build_worker_command,
    build_worker_env,
)


def test_worker_env_cpu_bound_vs_gpu_defaults():
    # GPU / dedicated host must NOT be capped by default
    gpu_env = build_worker_env(is_cpu_bound=False, base_env={"PATH": "/usr/bin"})
    assert "OMP_NUM_THREADS" not in gpu_env
    assert "GOMP_WAIT_POLICY" not in gpu_env
    assert "OMP_PROC_BIND" not in gpu_env

    # CPU-bound host gets S0b safety defaults
    cpu_env = build_worker_env(is_cpu_bound=True, base_env={"PATH": "/usr/bin"})
    assert cpu_env["OMP_NUM_THREADS"] == "1"
    assert cpu_env["GOMP_WAIT_POLICY"] == "PASSIVE"
    assert cpu_env["OMP_PROC_BIND"] == "false"

    # Explicit config always overrides defaults
    ovr_env = build_worker_env(
        is_cpu_bound=True,
        omp_num_threads="2",
        gomp_wait_policy="ACTIVE",
        omp_proc_bind="true",
        base_env={"PATH": "/usr/bin"},
    )
    assert ovr_env["OMP_NUM_THREADS"] == "2"
    assert ovr_env["GOMP_WAIT_POLICY"] == "ACTIVE"
    assert ovr_env["OMP_PROC_BIND"] == "true"


def test_build_worker_command_wraps_bundled_ld_so_on_linux(tmp_path, monkeypatch):
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    lib_file = lib_dir / "libparakeet.so"
    lib_file.write_bytes(b"")
    ld_so = lib_dir / "ld.so"
    ld_so.write_bytes(b"")

    monkeypatch.setattr("services.streaming_bridge.engines.parakeet_native.platform.system", lambda: "Linux")
    cmd = build_worker_command(str(lib_file), expected_abi=11, nice_val=19, cpu_affinity="148,162")
    assert cmd[0] == str(ld_so)
    assert cmd[1:3] == ["--library-path", str(lib_dir)]
    assert "services.streaming_bridge.engines.parakeet_worker" in cmd
    assert "--expected-abi" in cmd and "11" in cmd
    assert "--nice" in cmd and "19" in cmd
    assert "--cpu-affinity" in cmd and "148,162" in cmd

    # Without Linux bundled ld.so, runs python directly
    ld_so.unlink()
    cmd_direct = build_worker_command(str(lib_file), expected_abi=11)
    assert cmd_direct[0] != str(ld_so)
    assert "-m" in cmd_direct


@pytest.mark.asyncio
async def test_worker_refuses_abi_mismatch(tmp_path):
    mock_worker = tmp_path / "bad_abi_worker.py"
    mock_worker.write_text(textwrap.dedent("""
        import json, sys
        sys.stdout.write(json.dumps({"ready": False, "code": "ABI_MISMATCH", "error": "ABI 10 != 11"}) + "\\n")
        sys.stdout.flush()
        raise SystemExit(2)
    """))
    engine = ParakeetNativeEngine(
        worker_cmd_factory=lambda: ([sys.executable, str(mock_worker)], {})
    )
    with pytest.raises(EngineABIMismatchError, match="ABI 10"):
        await engine.load("fake.gguf")
    assert engine.worker_pid is None


@pytest.mark.asyncio
async def test_worker_crash_ends_only_affected_streams_and_restarts(tmp_path):
    """If worker crashes mid-stream, active streams fail clearly and next stream auto-restarts worker."""
    mock_worker = tmp_path / "crashy_worker.py"
    marker = tmp_path / "crashed_once"
    mock_worker.write_text(textwrap.dedent(f"""
        import json, sys
        from pathlib import Path
        sys.stdout.write(json.dumps({{"ready": True, "abi": 11, "pid": 1}}) + "\\n")
        sys.stdout.flush()
        sid_seq = 0
        marker = Path({repr(str(marker))})
        for raw in sys.stdin:
            req = json.loads(raw)
            op = req.get("op")
            if op == "load":
                sys.stdout.write(json.dumps({{"ok": True}}) + "\\n")
            elif op == "open_stream":
                sid_seq += 1
                sys.stdout.write(json.dumps({{"ok": True, "stream_id": f"ws_{{sid_seq}}"}}) + "\\n")
            elif op == "feed":
                if not marker.exists():
                    marker.write_text("1")
                    sys.exit(139)  # simulate SIGSEGV crash on first feed
                sys.stdout.write(json.dumps({{"ok": True, "result": {{"text": "recovered", "eou": 1}}}}) + "\\n")
            elif op == "finalize":
                sys.stdout.write(json.dumps({{"ok": True, "result": {{"text": "", "eou": 0}}}}) + "\\n")
            elif op == "close_stream":
                sys.stdout.write(json.dumps({{"ok": True}}) + "\\n")
            elif op == "shutdown":
                sys.stdout.write(json.dumps({{"ok": True}}) + "\\n")
                sys.stdout.flush()
                break
            sys.stdout.flush()
    """))
    engine = ParakeetNativeEngine(
        worker_cmd_factory=lambda: ([sys.executable, str(mock_worker)], {})
    )
    await engine.load("fake-eou.gguf")
    pid_first = engine.worker_pid
    assert pid_first is not None

    s1 = await engine.open_stream("en")
    s2 = await engine.open_stream("en")
    assert engine.active_streams == 2

    # First feed crashes worker -> raises EngineCrashedError & ends affected streams s1, s2
    with pytest.raises(EngineCrashedError):
        await engine.feed(s1, b"\x00\x01" * 160)

    assert engine.crash_count == 1
    assert engine.active_streams == 0
    assert engine.worker_pid is None

    # Opening a new stream auto-restarts a fresh worker and reloads the model
    s3 = await engine.open_stream("en")
    assert engine.restart_count == 1
    assert engine.worker_pid is not None and engine.worker_pid != pid_first

    res = await engine.feed(s3, b"\x00\x01" * 160)
    assert res.text_delta == "recovered"
    fin = await engine.finalize(s3)
    assert fin.full_text == "recovered"

    await engine.close()
    assert engine.worker_pid is None

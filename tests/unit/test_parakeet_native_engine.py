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


@pytest.mark.asyncio
async def test_worker_spawn_receives_cpu_bound_env_and_nice(tmp_path, monkeypatch):
    """Verify ParakeetNativeEngine._build_launch_spec and spawned worker receive CPU-bound env and nice 19."""
    import json
    import os

    fake_lib = tmp_path / "libparakeet.so"
    fake_lib.write_bytes(b"")

    monkeypatch.setenv("OMP_NUM_THREADS", "64")
    monkeypatch.setenv("GOMP_WAIT_POLICY", "ACTIVE")
    monkeypatch.setenv("OMP_PROC_BIND", "true")

    engine = ParakeetNativeEngine(lib_path=str(fake_lib), is_cpu_bound=True)
    assert engine.worker_nice == 19
    cmd, env = engine._build_launch_spec()
    assert "--nice" in cmd and cmd[cmd.index("--nice") + 1] == "19"
    assert env["OMP_NUM_THREADS"] == "1"
    assert env["GOMP_WAIT_POLICY"] == "PASSIVE"
    assert env["OMP_PROC_BIND"] == "false"

    # Spawn a real subprocess using the built env and verify the child sees those exact env vars
    probe_worker = tmp_path / "env_probe_worker.py"
    probe_worker.write_text(textwrap.dedent("""
        import json, os, sys
        observed = {
            "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"),
            "GOMP_WAIT_POLICY": os.environ.get("GOMP_WAIT_POLICY"),
            "OMP_PROC_BIND": os.environ.get("OMP_PROC_BIND"),
        }
        sys.stdout.write(json.dumps({"ready": True, "abi": 11, "pid": os.getpid()}) + "\\n")
        sys.stdout.flush()
        for raw in sys.stdin:
            req = json.loads(raw)
            op = req.get("op")
            if op == "load":
                sys.stdout.write(json.dumps({"ok": True, "observed": observed}) + "\\n")
            elif op == "open_stream":
                sys.stdout.write(json.dumps({"ok": True, "stream_id": "s1"}) + "\\n")
            elif op == "feed":
                sys.stdout.write(json.dumps({"ok": True, "result": {"text": json.dumps(observed), "eou": 1}}) + "\\n")
            elif op == "finalize":
                sys.stdout.write(json.dumps({"ok": True, "result": {"text": "", "eou": 0}}) + "\\n")
            elif op == "shutdown":
                sys.stdout.write(json.dumps({"ok": True}) + "\\n")
                sys.stdout.flush()
                break
            sys.stdout.flush()
    """))
    spawned = ParakeetNativeEngine(
        is_cpu_bound=True,
        worker_cmd_factory=lambda: ([sys.executable, str(probe_worker)], env),
    )
    await spawned.load("fake.gguf")
    sid = await spawned.open_stream()
    res = await spawned.feed(sid, b"\x00\x01" * 160)
    seen = json.loads(res.text_delta)
    assert seen == {
        "OMP_NUM_THREADS": "1",
        "GOMP_WAIT_POLICY": "PASSIVE",
        "OMP_PROC_BIND": "false",
    }
    await spawned.finalize(sid)
    await spawned.close()


@pytest.mark.asyncio
async def test_calibration_records_at_least_50_chunks_and_sample_count(tmp_path, monkeypatch):
    """Calibration must measure >=50 chunks while voice turns run and record chunk_samples."""
    import services.streaming_bridge.calibrate as cal
    from services.streaming_bridge.engines.fake_engine import FakeStreamingEngine

    async def fake_turn(client, base_url, tts_url, headers):
        return (50.0, 50.0, 100.0)

    class FastFakeEngine(FakeStreamingEngine):
        def __init__(self, *args, **kwargs):
            super().__init__(feed_latency_s=0.0, finalize_latency_s=0.0)

    monkeypatch.setattr(cal, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cal, "run_voice_turn", fake_turn)
    monkeypatch.setattr(cal, "ParakeetNativeEngine", FastFakeEngine)
    monkeypatch.setattr(cal, "resolve_parakeet_library", lambda: str(tmp_path / "libparakeet.so"))
    monkeypatch.setattr(cal, "inspect_host_service_threads", lambda: [])
    # Speed up 0.16s sleep in bg_feeder so 50 chunks complete in <0.2s
    real_sleep = cal.asyncio.sleep
    monkeypatch.setattr(cal.asyncio, "sleep", lambda s: real_sleep(0.001))

    res = await cal.run_calibration(
        model="parakeet-cpp-realtime_eou_120m-v1",
        model_path="fake.gguf",
        turns=2,
        min_chunks=50,
    )
    assert res["measurements"]["total_chunk_samples"] >= 50
    assert (
        res["measurements"]["chunk_samples"] + res["measurements"]["topup_chunk_samples"]
        == res["measurements"]["total_chunk_samples"]
    )


@pytest.mark.asyncio
async def test_calibration_feeder_exception_records_error_and_zero_streams(tmp_path, monkeypatch, capsys):
    """If bg_feeder raises, calibration must record status='error', admitted_streams=0, and print the exception."""
    import services.streaming_bridge.calibrate as cal
    from services.streaming_bridge.engines.fake_engine import FakeStreamingEngine

    async def fake_turn(client, base_url, tts_url, headers):
        return (50.0, 50.0, 100.0)

    class CrashingEngine(FakeStreamingEngine):
        def __init__(self, *args, **kwargs):
            super().__init__(crash_on_feed_call=2)

    monkeypatch.setattr(cal, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cal, "run_voice_turn", fake_turn)
    monkeypatch.setattr(cal, "ParakeetNativeEngine", CrashingEngine)
    monkeypatch.setattr(cal, "resolve_parakeet_library", lambda: str(tmp_path / "libparakeet.so"))
    monkeypatch.setattr(cal, "inspect_host_service_threads", lambda: [])
    real_sleep = cal.asyncio.sleep
    monkeypatch.setattr(cal.asyncio, "sleep", lambda s: real_sleep(0.001))

    res = await cal.run_calibration(
        model="parakeet-cpp-realtime_eou_120m-v1",
        model_path="fake.gguf",
        turns=2,
        min_chunks=50,
    )
    assert res["status"] == "error"
    assert res["passed"] is False
    assert res["admitted_streams"] == 0
    assert "EngineCrashedError" in res["error"]
    captured = capsys.readouterr()
    assert "EngineCrashedError" in captured.err


@pytest.mark.asyncio
async def test_calibration_tags_voice_turn_vs_topup_chunks(tmp_path, monkeypatch):
    """chunk_p50_ms and chunk_p95_ms must be computed only from chunks tagged during voice turns."""
    import services.streaming_bridge.calibrate as cal
    from services.streaming_bridge.engines.fake_engine import FakeStreamingEngine

    real_sleep = cal.asyncio.sleep

    async def slow_turn(client, base_url, tts_url, headers):
        await real_sleep(0.03)
        return (15.0, 15.0, 30.0)

    class FastFakeEngine(FakeStreamingEngine):
        def __init__(self, *args, **kwargs):
            super().__init__(feed_latency_s=0.0, finalize_latency_s=0.0)

    monkeypatch.setattr(cal, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cal, "run_voice_turn", slow_turn)
    monkeypatch.setattr(cal, "ParakeetNativeEngine", FastFakeEngine)
    monkeypatch.setattr(cal, "resolve_parakeet_library", lambda: str(tmp_path / "libparakeet.so"))
    monkeypatch.setattr(cal, "inspect_host_service_threads", lambda: [])
    monkeypatch.setattr(cal.asyncio, "sleep", lambda s: real_sleep(0.001))

    res = await cal.run_calibration(
        model="parakeet-cpp-realtime_eou_120m-v1",
        model_path="fake.gguf",
        turns=2,
        min_chunks=50,
    )
    m = res["measurements"]
    assert res["status"] in ("pass", "fail")
    assert m["chunk_samples"] > 0
    assert m["topup_chunk_samples"] > 0
    assert m["total_chunk_samples"] >= 50
    assert m["chunk_samples"] + m["topup_chunk_samples"] == m["total_chunk_samples"]


@pytest.mark.asyncio
async def test_engine_passes_engine_threads_and_worker_calls_set_concurrency(tmp_path):
    """ParakeetNativeEngine defaults engine_threads=4 on CPU-bound hosts and worker calls parakeet_capi_set_concurrency."""
    import json
    from services.streaming_bridge.engines.parakeet_worker import ParakeetWorkerBinding

    eng_cpu = ParakeetNativeEngine(is_cpu_bound=True)
    eng_gpu = ParakeetNativeEngine(is_cpu_bound=False)
    assert eng_cpu.engine_threads == 4
    assert eng_gpu.engine_threads is None

    # Verify load RPC includes engine_threads
    probe_worker = tmp_path / "threads_worker.py"
    probe_worker.write_text(textwrap.dedent("""
        import json, sys
        sys.stdout.write(json.dumps({"ready": True, "abi": 11, "pid": 1}) + "\\n")
        sys.stdout.flush()
        seen_threads = None
        for raw in sys.stdin:
            req = json.loads(raw)
            op = req.get("op")
            if op == "load":
                seen_threads = req.get("engine_threads")
                sys.stdout.write(json.dumps({"ok": True}) + "\\n")
            elif op == "open_stream":
                sys.stdout.write(json.dumps({"ok": True, "stream_id": "s1"}) + "\\n")
            elif op == "feed":
                sys.stdout.write(json.dumps({"ok": True, "result": {"text": str(seen_threads), "eou": 1}}) + "\\n")
            elif op == "finalize":
                sys.stdout.write(json.dumps({"ok": True, "result": {"text": "", "eou": 0}}) + "\\n")
            elif op == "shutdown":
                sys.stdout.write(json.dumps({"ok": True}) + "\\n")
                sys.stdout.flush()
                break
            sys.stdout.flush()
    """))
    eng = ParakeetNativeEngine(
        is_cpu_bound=True,
        engine_threads=4,
        worker_cmd_factory=lambda: ([sys.executable, str(probe_worker)], {}),
    )
    await eng.load("fake.gguf")
    sid = await eng.open_stream()
    res = await eng.feed(sid, b"\x00\x01" * 160)
    assert res.text_delta == "4"
    await eng.finalize(sid)
    await eng.close()

    # Verify ParakeetWorkerBinding.load invokes parakeet_capi_set_concurrency(ctx, 1, 4)
    from unittest.mock import MagicMock
    import ctypes

    fake_lib = MagicMock()
    fake_lib.parakeet_capi_abi_version.return_value = 11
    fake_lib.parakeet_capi_load.return_value = 12345
    fake_lib.parakeet_capi_set_concurrency.return_value = 0

    orig_cdll = ctypes.CDLL
    ctypes.CDLL = lambda path: fake_lib
    try:
        binding = ParakeetWorkerBinding("libparakeet.so", expected_abi=11)
        ctx = binding.load("fake.gguf", engine_threads=4)
        assert ctx == 12345
        fake_lib.parakeet_capi_set_concurrency.assert_called_once()
        args = fake_lib.parakeet_capi_set_concurrency.call_args[0]
        assert (args[0].value, args[1], args[2]) == (12345, 1, 4)
    finally:
        ctypes.CDLL = orig_cdll


@pytest.mark.asyncio
async def test_paused_mode_calibration_uses_scheduler_and_passes(tmp_path, monkeypatch):
    """Paused-mode calibration drives BridgeScheduler, records pause/catchup metrics, and admits host."""
    import services.streaming_bridge.calibrate as cal
    from services.streaming_bridge.engines.fake_engine import FakeStreamingEngine

    real_sleep = cal.asyncio.sleep

    async def slow_turn(client, base_url, tts_url, headers):
        await real_sleep(0.02)
        return (10.0, 10.0, 20.0)

    class FastFakeEngine(FakeStreamingEngine):
        def __init__(self, *args, **kwargs):
            super().__init__(feed_latency_s=0.0, finalize_latency_s=0.0)

    monkeypatch.setattr(cal, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cal, "run_voice_turn", slow_turn)
    monkeypatch.setattr(cal, "ParakeetNativeEngine", FastFakeEngine)
    monkeypatch.setattr(cal, "resolve_parakeet_library", lambda: str(tmp_path / "libparakeet.so"))
    monkeypatch.setattr(cal, "inspect_host_service_threads", lambda: [])
    monkeypatch.setattr(cal.asyncio, "sleep", lambda s: real_sleep(0.002))

    res = await cal.run_calibration(
        model="parakeet-cpp-realtime_eou_120m-v1",
        model_path="fake.gguf",
        turns=2,
        min_chunks=10,
        mode="paused",
    )
    assert res["calibration_mode"] == "paused"
    assert res["status"] == "pass"
    assert res["passed"] is True
    assert res["admitted_streams"] == 1
    m = res["measurements"]
    assert m["overflow_count"] == 0
    assert m["chunk_samples"] >= 10
    assert m["pause_duration_p50_ms"] > 0
    assert m["catchup_lag_p95_ms"] >= 0

    ok, _, _ = cal.is_host_calibrated_and_passing("parakeet-cpp-realtime_eou_120m-v1")
    assert ok is True


def test_paused_mode_calibration_fails_on_overflow():
    """Paused-mode pass rule must reject if any chunk overflowed the pause buffer."""
    from services.streaming_bridge.calibrate import evaluate_paused_pass_rule

    passed, admitted, reason = evaluate_paused_pass_rule(
        turns_completed=20,
        turns_target=20,
        base_med_ms=900.0,
        base_p95_ms=1900.0,
        stream_med_ms=910.0,
        stream_p95_ms=1920.0,
        chunk_p95_ms=25.0,
        catchup_lag_p95_ms=800.0,
        overflow_count=2,
    )
    assert passed is False
    assert admitted == 0
    assert "overflowed" in reason


@pytest.mark.asyncio
async def test_stream_only_mode_calibration(tmp_path, monkeypatch):
    """stream-only mode feeds WAV with pause_enabled=False, no LLM/TTS turns, and reports chunk stats + transcript."""
    import services.streaming_bridge.calibrate as cal
    from services.streaming_bridge.engines.fake_engine import FakeStreamingEngine

    real_sleep = cal.asyncio.sleep

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("run_voice_turn must not be called in stream-only mode")

    class FastFakeEngine(FakeStreamingEngine):
        def __init__(self, *args, **kwargs):
            super().__init__(feed_latency_s=0.0, finalize_latency_s=0.0)

    monkeypatch.setattr(cal, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cal, "run_voice_turn", fail_if_called)
    monkeypatch.setattr(cal, "ParakeetNativeEngine", FastFakeEngine)
    monkeypatch.setattr(cal, "resolve_parakeet_library", lambda: str(tmp_path / "libparakeet.so"))
    monkeypatch.setattr(cal, "inspect_host_service_threads", lambda: [])
    monkeypatch.setattr(cal.asyncio, "sleep", lambda s: real_sleep(0.001))

    res = await cal.run_calibration(
        model="parakeet-cpp-realtime_eou_120m-v1",
        model_path="fake.gguf",
        mode="stream-only",
        wav_path="tests/fixtures/audio/quiet_normal_speech.wav",
    )
    assert res["calibration_mode"] == "stream-only"
    assert res["status"] == "pass"
    assert res["passed"] is True
    assert res["admitted_streams"] == 1
    m = res["measurements"]
    assert m["overflow_count"] == 0
    assert m["chunk_samples"] >= 30
    assert m["chunk_p50_ms"] >= 0
    assert 0 < m["chunk_p95_ms"] < 160.0
    assert "partly cloudy" in m["transcript"]


@pytest.mark.asyncio
async def test_stream_only_omp_threads_override(monkeypatch, tmp_path):
    """stream-only passes omp_threads / STREAMING_NATIVE_OMP_NUM_THREADS to ParakeetNativeEngine."""
    import asyncio
    from services.streaming_bridge import calibrate as cal
    from services.streaming_bridge.engines.fake_engine import FakeStreamingEngine

    real_sleep = asyncio.sleep
    seen_omp = []

    class CapturingEngine(FakeStreamingEngine):
        def __init__(self, *args, **kwargs):
            seen_omp.append(kwargs.get("omp_num_threads"))
            super().__init__(feed_latency_s=0.0, finalize_latency_s=0.0)

    monkeypatch.setattr(cal, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cal, "ParakeetNativeEngine", CapturingEngine)
    monkeypatch.setattr(cal, "resolve_parakeet_library", lambda: str(tmp_path / "libparakeet.so"))
    monkeypatch.setattr(cal, "inspect_host_service_threads", lambda: [])
    monkeypatch.setattr(cal.asyncio, "sleep", lambda s: real_sleep(0.001))

    # 1. Default unset -> None (preserves OMP_NUM_THREADS=1 on cpu-bound hosts)
    monkeypatch.setattr(cal.settings, "STREAMING_NATIVE_OMP_NUM_THREADS", None)
    await cal.run_calibration(
        model="parakeet-cpp-realtime_eou_120m-v1",
        model_path="fake.gguf",
        mode="stream-only",
        wav_path="tests/fixtures/audio/quiet_normal_speech.wav",
    )

    # 2. Env/settings override
    monkeypatch.setattr(cal.settings, "STREAMING_NATIVE_OMP_NUM_THREADS", "4")
    await cal.run_calibration(
        model="parakeet-cpp-realtime_eou_120m-v1",
        model_path="fake.gguf",
        mode="stream-only",
        wav_path="tests/fixtures/audio/quiet_normal_speech.wav",
    )

    # 3. Explicit CLI omp_threads overrides settings
    await cal.run_calibration(
        model="parakeet-cpp-realtime_eou_120m-v1",
        model_path="fake.gguf",
        mode="stream-only",
        omp_threads=8,
        wav_path="tests/fixtures/audio/quiet_normal_speech.wav",
    )

    assert seen_omp == [None, "4", "8"]

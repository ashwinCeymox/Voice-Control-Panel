"""Process-isolated Native Parakeet Streaming Engine for Port :8089.

Launches `parakeet_worker` as a separate subprocess (on Linux, via bundled
`ld.so --library-path <lib_dir>` so libparakeet.so's glibc/CXXABI runtime loads
cleanly without touching system libraries). Asserts ABI == 11 on startup,
accumulates incremental partials + finalize tail, and auto-restarts on worker crash.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import platform
import struct
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid

from services.streaming_bridge.engines.base import (
    EngineABIMismatchError,
    EngineChunkResult,
    EngineCrashedError,
    EngineFinalResult,
    StreamingEngine,
    join_transcript_parts,
    pcm16_to_float32,
)

logger = logging.getLogger("parakeet_native_engine")

DEFAULT_EXPECTED_ABI = 11


@dataclass
class _NativeStreamState:
    public_id: str
    worker_stream_id: str
    language: str
    accumulated_text: str = ""


def resolve_parakeet_library(explicit_path: Optional[str] = None) -> Optional[str]:
    """Locate libparakeet shared library at runtime without hardcoded platform paths."""
    if explicit_path and explicit_path.strip():
        cand = Path(explicit_path.strip())
        if cand.is_file():
            return str(cand.resolve())
        return str(cand)

    env_path = os.environ.get("STREAMING_NATIVE_LIB_PATH")
    if env_path and Path(env_path).is_file():
        return str(Path(env_path).resolve())

    ext = "libparakeet.dylib" if platform.system() == "Darwin" else "libparakeet.so"
    search_roots = [
        Path("backends/cpu-parakeet-cpp/lib") / ext,
        Path("backends") / ("metal-parakeet-cpp" if platform.system() == "Darwin" else "cpu-parakeet-cpp") / "lib" / ext,
        Path("../LocalAI/backends/cpu-parakeet-cpp/lib") / ext,
    ]
    for p in search_roots:
        if p.is_file():
            return str(p.resolve())

    backends_dir = Path("backends")
    if backends_dir.is_dir():
        for hit in sorted(backends_dir.rglob(ext)):
            if hit.is_file():
                return str(hit.resolve())
    return None


def build_worker_env(
    *,
    is_cpu_bound: bool = False,
    omp_num_threads: Optional[str] = None,
    gomp_wait_policy: Optional[str] = None,
    omp_proc_bind: Optional[str] = None,
    base_env: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Construct worker environment.

    Applies S0b OpenMP throttle defaults ONLY when calibration marks host CPU-bound,
    leaving GPU/dedicated-core hosts uncapped unless explicitly configured.
    """
    env = dict(base_env if base_env is not None else os.environ)

    if omp_num_threads is not None and str(omp_num_threads).strip():
        env["OMP_NUM_THREADS"] = str(omp_num_threads).strip()
    elif is_cpu_bound:
        env["OMP_NUM_THREADS"] = "1"

    if gomp_wait_policy is not None and str(gomp_wait_policy).strip():
        env["GOMP_WAIT_POLICY"] = str(gomp_wait_policy).strip()
    elif is_cpu_bound:
        env["GOMP_WAIT_POLICY"] = "PASSIVE"

    if omp_proc_bind is not None and str(omp_proc_bind).strip():
        env["OMP_PROC_BIND"] = str(omp_proc_bind).strip()
    elif is_cpu_bound:
        env["OMP_PROC_BIND"] = "false"

    return env


def build_worker_command(
    lib_path: str,
    *,
    expected_abi: int = DEFAULT_EXPECTED_ABI,
    nice_val: Optional[int] = None,
    cpu_affinity: str = "",
    python_executable: Optional[str] = None,
) -> List[str]:
    """Build worker command, wrapping via bundled ld.so + --library-path when present."""
    py_exe = os.path.realpath(python_executable or sys.executable)
    lib_dir = Path(lib_path).resolve().parent
    bundled_ld_so = lib_dir / "ld.so"

    worker_args = [
        py_exe,
        "-m",
        "services.streaming_bridge.engines.parakeet_worker",
        "--library",
        str(Path(lib_path).resolve() if Path(lib_path).exists() else lib_path),
        "--expected-abi",
        str(expected_abi),
    ]
    if nice_val is not None:
        worker_args.extend(["--nice", str(nice_val)])
    if cpu_affinity:
        worker_args.extend(["--cpu-affinity", cpu_affinity])

    if platform.system() == "Linux" and bundled_ld_so.is_file():
        return [str(bundled_ld_so), "--library-path", str(lib_dir), *worker_args]
    return worker_args


class ParakeetNativeEngine(StreamingEngine):
    """StreamingEngine backed by an isolated parakeet_worker subprocess."""

    def __init__(
        self,
        lib_path: Optional[str] = None,
        expected_abi: int = DEFAULT_EXPECTED_ABI,
        is_cpu_bound: bool = False,
        omp_num_threads: Optional[str] = None,
        gomp_wait_policy: Optional[str] = None,
        omp_proc_bind: Optional[str] = None,
        worker_nice: Optional[int] = None,
        cpu_affinity: str = "",
        engine_threads: Optional[int] = None,
        startup_timeout_s: float = 15.0,
        rpc_timeout_s: float = 15.0,
        worker_cmd_factory: Optional[Callable[[], Tuple[List[str], Dict[str, str]]]] = None,
    ):
        self.lib_path = lib_path
        self.expected_abi = expected_abi
        self.is_cpu_bound = is_cpu_bound
        self.omp_num_threads = omp_num_threads
        self.gomp_wait_policy = gomp_wait_policy
        self.omp_proc_bind = omp_proc_bind
        self.worker_nice = worker_nice if worker_nice is not None else (19 if is_cpu_bound else None)
        self.cpu_affinity = cpu_affinity
        self.engine_threads = engine_threads if engine_threads is not None else (4 if is_cpu_bound else None)
        self.startup_timeout_s = startup_timeout_s
        self.rpc_timeout_s = rpc_timeout_s
        self.worker_cmd_factory = worker_cmd_factory

        self._lock = asyncio.Lock()
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._loaded: bool = False
        self._model_path: Optional[str] = None
        self._default_language: str = "en"
        self._worker_abi: Optional[int] = None
        self._streams: Dict[str, _NativeStreamState] = {}
        self.restart_count: int = 0
        self.crash_count: int = 0

    @property
    def worker_pid(self) -> Optional[int]:
        return self._proc.pid if self._proc is not None and self._proc.returncode is None else None

    @property
    def active_streams(self) -> int:
        return len(self._streams)

    def _build_launch_spec(self) -> Tuple[List[str], Dict[str, str]]:
        if self.worker_cmd_factory is not None:
            return self.worker_cmd_factory()
        lib = resolve_parakeet_library(self.lib_path)
        if not lib or not Path(lib).is_file():
            raise FileNotFoundError(
                f"libparakeet shared library not found (configured={self.lib_path!r})"
            )
        cmd = build_worker_command(
            lib,
            expected_abi=self.expected_abi,
            nice_val=self.worker_nice,
            cpu_affinity=self.cpu_affinity,
        )
        env = build_worker_env(
            is_cpu_bound=self.is_cpu_bound,
            omp_num_threads=self.omp_num_threads,
            gomp_wait_policy=self.gomp_wait_policy,
            omp_proc_bind=self.omp_proc_bind,
        )
        return cmd, env

    async def _terminate_proc(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except Exception:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except Exception:
                    pass

    async def _ensure_worker_started(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            return

        cmd, env = self._build_launch_spec()
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        assert self._proc.stdout is not None
        try:
            raw_line = await asyncio.wait_for(self._proc.stdout.readline(), timeout=self.startup_timeout_s)
        except asyncio.TimeoutError as exc:
            await self._terminate_proc()
            raise RuntimeError("Parakeet worker startup timed out") from exc

        if not raw_line:
            stderr_txt = ""
            if self._proc.stderr is not None:
                stderr_txt = (await self._proc.stderr.read()).decode("utf-8", "replace")[-400:]
            rc = await self._proc.wait()
            await self._terminate_proc()
            raise RuntimeError(f"Parakeet worker exited during startup (rc={rc}): {stderr_txt}")

        hello = json.loads(raw_line.decode("utf-8", "replace"))
        if not hello.get("ready"):
            err = hello.get("error", "Worker refused startup")
            code = hello.get("code", "WORKER_STARTUP_FAILED")
            await self._terminate_proc()
            if code == "ABI_MISMATCH":
                raise EngineABIMismatchError(err)
            raise RuntimeError(f"{code}: {err}")

        self._worker_abi = hello.get("abi")

    async def _handle_worker_crash(self, reason: str) -> None:
        """End only affected active streams cleanly and prepare worker for restart."""
        self.crash_count += 1
        affected = len(self._streams)
        self._streams.clear()
        self._loaded = False
        await self._terminate_proc()
        logger.error("Parakeet worker crashed (%s); ended %d affected stream(s)", reason, affected)

    async def _rpc(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self._proc is None or self._proc.returncode is not None:
            await self._ensure_worker_started()
            if self._model_path and not self._loaded:
                self.restart_count += 1
                await self._send_load_raw(self._model_path)

        return await self._send_raw(payload)

    async def _send_raw(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            await self._handle_worker_crash("missing worker pipes")
            raise EngineCrashedError("Worker process unavailable")

        line_bytes = (json.dumps(payload) + "\n").encode("utf-8")
        try:
            proc.stdin.write(line_bytes)
            await proc.stdin.drain()
            resp_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=self.rpc_timeout_s)
        except Exception as exc:
            await self._handle_worker_crash(repr(exc))
            raise EngineCrashedError(f"Worker communication failed: {exc}") from exc

        if not resp_bytes:
            rc = proc.returncode
            if rc is None:
                try:
                    rc = await asyncio.wait_for(proc.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    rc = None
            await self._handle_worker_crash(f"EOF rc={rc}")
            raise EngineCrashedError(f"Parakeet worker terminated unexpectedly (rc={rc})")

        resp = json.loads(resp_bytes.decode("utf-8", "replace"))
        if not resp.get("ok"):
            raise RuntimeError(f"{resp.get('code', 'WORKER_ERROR')}: {resp.get('error', 'unknown error')}")
        return resp

    async def _send_load_raw(self, model_path: str) -> None:
        payload: Dict[str, Any] = {"op": "load", "model_path": model_path}
        if self.engine_threads is not None:
            payload["engine_threads"] = int(self.engine_threads)
        await self._send_raw(payload)
        self._loaded = True

    async def load(self, model_path: str, language: str = "en") -> None:
        async with self._lock:
            self._model_path = model_path
            self._default_language = language
            await self._ensure_worker_started()
            await self._send_load_raw(model_path)

    async def open_stream(self, language: str = "en") -> str:
        async with self._lock:
            if not self._model_path:
                raise RuntimeError("Engine model path not configured; call load() first")
            resp = await self._rpc({"op": "open_stream", "language": language or self._default_language})
            worker_sid = str(resp["stream_id"])
            pub_sid = f"native_{uuid.uuid4().hex[:8]}"
            self._streams[pub_sid] = _NativeStreamState(
                public_id=pub_sid,
                worker_stream_id=worker_sid,
                language=language or self._default_language,
            )
            return pub_sid

    async def feed(
        self,
        stream_id: str,
        pcm16_bytes: bytes,
        sample_rate: int = 16000,
    ) -> EngineChunkResult:
        samples = pcm16_to_float32(pcm16_bytes, sample_rate=sample_rate)
        packed = struct.pack(f"<{len(samples)}f", *samples) if samples else b""
        b64 = base64.b64encode(packed).decode("ascii") if packed else ""

        async with self._lock:
            state = self._streams.get(stream_id)
            if state is None:
                raise KeyError(f"Unknown or closed stream: {stream_id}")
            resp = await self._rpc({
                "op": "feed",
                "stream_id": state.worker_stream_id,
                "pcm_f32_b64": b64,
            })
            res = resp.get("result") or {}
            delta = str(res.get("text", ""))
            state.accumulated_text = join_transcript_parts(state.accumulated_text, delta)
            return EngineChunkResult(
                text_delta=delta,
                accumulated_text=state.accumulated_text,
                eou=bool(res.get("eou", 0)),
                eob=bool(res.get("eob", 0)),
                words=list(res.get("words") or []),
                events=list(res.get("events") or []),
                frame_sec=float(res.get("frame_sec", 0.08)),
            )

    async def finalize(self, stream_id: str) -> EngineFinalResult:
        async with self._lock:
            state = self._streams.get(stream_id)
            if state is None:
                raise KeyError(f"Unknown or closed stream: {stream_id}")
            try:
                resp = await self._rpc({"op": "finalize", "stream_id": state.worker_stream_id})
                res = resp.get("result") or {}
                tail = str(res.get("text", ""))
                full_text = join_transcript_parts(state.accumulated_text, tail)
                return EngineFinalResult(
                    tail_text=tail,
                    full_text=full_text,
                    eou=bool(res.get("eou", 0)),
                    eob=bool(res.get("eob", 0)),
                    events=list(res.get("events") or []),
                )
            finally:
                self._streams.pop(stream_id, None)

    async def close_stream(self, stream_id: str) -> None:
        async with self._lock:
            state = self._streams.pop(stream_id, None)
            if state is None:
                return
            if self._proc is not None and self._proc.returncode is None:
                try:
                    await self._send_raw({"op": "close_stream", "stream_id": state.worker_stream_id})
                except Exception:
                    pass

    async def close(self) -> None:
        async with self._lock:
            self._streams.clear()
            self._loaded = False
            if self._proc is not None and self._proc.returncode is None:
                try:
                    await self._send_raw({"op": "shutdown"})
                except Exception:
                    pass
            await self._terminate_proc()

    def health(self) -> Dict[str, Any]:
        return {
            "engine": "ParakeetNativeEngine",
            "ready": self._loaded and self.worker_pid is not None,
            "worker_pid": self.worker_pid,
            "worker_abi": self._worker_abi,
            "engine_threads": self.engine_threads,
            "active_streams": self.active_streams,
            "crash_count": self.crash_count,
            "restart_count": self.restart_count,
        }

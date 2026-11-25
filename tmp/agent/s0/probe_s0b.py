#!/usr/bin/env python3
"""Phase S0b CPU Contention & Scheduler Feasibility Probe (Single-File).

Read-only diagnostic script for RunPod Linux. Saves JSON to tmp/agent/s0/results_s0b.json.
Requires only Python stdlib + existing LocalAI/TTS endpoints and libparakeet.so.
"""

from __future__ import annotations

import argparse
import ctypes
import glob
import json
import multiprocessing as mp
import os
from pathlib import Path
import platform
import resource
import shutil
import statistics
import subprocess
import sys
import threading
import time
from typing import Any, Optional
import urllib.error
import urllib.request
import wave

EXPECTED_ABI_VERSION = 11
REPO_ROOT = Path(__file__).resolve().parents[3] if (Path(__file__).resolve().parent.name == "s0") else Path.cwd()
if not (REPO_ROOT / "tests").is_dir():
    REPO_ROOT = Path.cwd()


class ABIMismatchError(RuntimeError):
    pass


def percentile(sorted_vals: list[float], pct: float) -> Optional[float]:
    if not sorted_vals:
        return None
    idx = (len(sorted_vals) - 1) * pct
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def get_rss_mb() -> float:
    try:
        st = Path("/proc/self/status")
        if st.is_file():
            for ln in st.read_text().splitlines():
                if ln.startswith("VmRSS:"):
                    return int(ln.split()[1]) / 1024.0
    except OSError:
        pass
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return ru / (1024.0 * 1024.0) if platform.system() == "Darwin" else ru / 1024.0


def get_peak_rss_mb() -> float:
    try:
        st = Path("/proc/self/status")
        if st.is_file():
            for ln in st.read_text().splitlines():
                if ln.startswith("VmHWM:"):
                    return int(ln.split()[1]) / 1024.0
    except OSError:
        pass
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return ru / (1024.0 * 1024.0) if platform.system() == "Darwin" else ru / 1024.0


def parse_env_value(key: str) -> Optional[str]:
    if key in os.environ and os.environ[key]:
        return os.environ[key]
    env_file = REPO_ROOT / ".env"
    if env_file.is_file():
        for raw in env_file.read_text(errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    return None


def discover_libparakeet() -> list[str]:
    ext = "libparakeet.dylib" if platform.system() == "Darwin" else "libparakeet.so"
    cands: list[str] = []
    for base in (REPO_ROOT / "backends", Path("/agent/Voice-Control-Panel/backends"), Path("/opt/localai")):
        if base.is_dir():
            for hit in base.rglob(f"*{ext}*"):
                if hit.is_file() and (hit.name == ext or hit.name.startswith(f"{ext}.")):
                    cands.append(str(hit.resolve()))
    for pid_dir in glob.glob("/proc/[0-9]*/maps"):
        try:
            for ln in Path(pid_dir).read_text(errors="ignore").splitlines():
                if ext in ln and "/" in ln:
                    cands.append(ln.split()[-1])
        except OSError:
            pass
    seen: list[str] = []
    for c in cands:
        if c not in seen and os.path.exists(c):
            seen.append(c)
    return sorted(seen)


def discover_models_dir() -> Optional[Path]:
    env_p = parse_env_value("LOCALAI_MODELS_PATH")
    for p in ([Path(env_p)] if env_p else []) + [
        REPO_ROOT / "models",
        Path("/agent/Voice-Control-Panel/models"),
        Path("../LocalAI/models"),
    ]:
        if p.is_dir():
            return p.resolve()
    return None


def find_eou_model(models_dir: Optional[Path]) -> tuple[str, str]:
    if models_dir and models_dir.is_dir():
        for yml in sorted(models_dir.glob("*.yaml")):
            if yml.name.startswith("._"):
                continue
            txt = yml.read_text(errors="replace")
            if "parakeet-cpp" in txt and "eou" in yml.name.lower() and "scene" not in yml.name.lower():
                for ln in txt.splitlines():
                    if ln.strip().startswith("model:"):
                        val = ln.split(":", 1)[1].strip().strip('"').strip("'")
                        p = Path(val) if Path(val).is_absolute() else (models_dir / val)
                        if p.is_file():
                            return yml.stem, str(p)
        for gg in sorted(models_dir.rglob("*eou*.gguf")):
            if not gg.name.startswith("._"):
                return "parakeet-cpp-realtime_eou_120m-v1", str(gg.resolve())
    raise FileNotFoundError("Could not find installed EOU GGUF model under models_dir")


def ensure_loader_and_reexec(lib_path: str) -> None:
    if os.environ.get("_S0B_REEXEC") == "1":
        return
    try:
        ctypes.CDLL(lib_path)
        return
    except OSError:
        if platform.system() != "Linux":
            raise
    lib_dir = str(Path(lib_path).parent)
    ld_so = str(Path(lib_dir) / "ld.so")
    if not os.path.exists(ld_so):
        raise RuntimeError(f"Bundled ld.so not found next to {lib_path}")
    real_py = os.path.realpath(sys.executable)
    env = os.environ.copy()
    env["_S0B_REEXEC"] = "1"
    env["_S0B_LD_SO"] = ld_so
    env["_S0B_LIB_DIR"] = lib_dir
    os.execve(ld_so, [ld_so, "--library-path", lib_dir, real_py, str(Path(__file__).resolve()), *sys.argv[1:]], env)


def make_py_cmd(script_args: list[str]) -> list[str]:
    ld_so = os.environ.get("_S0B_LD_SO", "")
    lib_dir = os.environ.get("_S0B_LIB_DIR", "")
    real_py = os.path.realpath(sys.executable)
    if ld_so and lib_dir and os.path.exists(ld_so):
        return [ld_so, "--library-path", lib_dir, real_py, str(Path(__file__).resolve()), *script_args]
    return [sys.executable, str(Path(__file__).resolve()), *script_args]


class ParakeetCAPI:
    def __init__(self, lib_path: str, expected_abi: int = EXPECTED_ABI_VERSION):
        self.lib = ctypes.CDLL(lib_path)
        self.lib.parakeet_capi_abi_version.argtypes = []
        self.lib.parakeet_capi_abi_version.restype = ctypes.c_int
        self.abi = int(self.lib.parakeet_capi_abi_version())
        if expected_abi > 0 and self.abi != expected_abi:
            raise ABIMismatchError(f"ABI {self.abi} != expected {expected_abi}")
        L = self.lib
        L.parakeet_capi_load.argtypes = [ctypes.c_char_p]
        L.parakeet_capi_load.restype = ctypes.c_void_p
        L.parakeet_capi_load_error.argtypes = []
        L.parakeet_capi_load_error.restype = ctypes.c_char_p
        L.parakeet_capi_free.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_free.restype = None
        L.parakeet_capi_last_error.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_last_error.restype = ctypes.c_char_p
        L.parakeet_capi_stream_begin.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_stream_begin.restype = ctypes.c_void_p
        L.parakeet_capi_stream_feed_json.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int]
        L.parakeet_capi_stream_feed_json.restype = ctypes.c_void_p
        L.parakeet_capi_stream_finalize_json.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_stream_finalize_json.restype = ctypes.c_void_p
        L.parakeet_capi_stream_free.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_stream_free.restype = None
        L.parakeet_capi_free_string.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_free_string.restype = None

    def _take_json(self, ptr: Optional[int]) -> dict[str, Any]:
        if not ptr:
            return {}
        try:
            raw = ctypes.cast(ptr, ctypes.c_char_p).value or b"{}"
            return json.loads(raw.decode("utf-8", errors="replace"))
        finally:
            self.lib.parakeet_capi_free_string(ctypes.c_void_p(ptr))

    def load(self, model_path: str) -> int:
        ctx = self.lib.parakeet_capi_load(model_path.encode("utf-8"))
        if not ctx:
            err = self.lib.parakeet_capi_load_error()
            raise RuntimeError(err.decode("utf-8", "replace") if err else "load error")
        return int(ctx)

    def free_ctx(self, ctx: int) -> None:
        self.lib.parakeet_capi_free(ctypes.c_void_p(ctx))

    def begin_stream(self, ctx: int) -> int:
        s = self.lib.parakeet_capi_stream_begin(ctypes.c_void_p(ctx))
        if not s:
            err = self.lib.parakeet_capi_last_error(ctypes.c_void_p(ctx))
            raise RuntimeError(err.decode("utf-8", "replace") if err else "begin_stream failed")
        return int(s)

    def feed_json(self, stream: int, samples: list[float]) -> dict[str, Any]:
        arr = (ctypes.c_float * len(samples))(*samples)
        return self._take_json(self.lib.parakeet_capi_stream_feed_json(ctypes.c_void_p(stream), arr, len(samples)))

    def finalize_json(self, stream: int) -> dict[str, Any]:
        return self._take_json(self.lib.parakeet_capi_stream_finalize_json(ctypes.c_void_p(stream)))

    def free_stream(self, stream: int) -> None:
        self.lib.parakeet_capi_stream_free(ctypes.c_void_p(stream))


def load_wav_16k_f32(path: Path) -> list[float]:
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != 16000:
            raise ValueError("WAV must be 16kHz mono PCM16")
        raw = wf.readframes(wf.getnframes())
    mv = memoryview(raw).cast("h")
    return [s / 32768.0 for s in mv]


def _busy_worker(duration_s: float, q: mp.Queue) -> None:
    t_end = time.perf_counter() + duration_s
    iters = 0
    x = 123456789
    while time.perf_counter() < t_end:
        for _ in range(50000):
            x = (x * 1664525 + 1013904223) & 0xFFFFFFFF
        iters += 1
    q.put(iters)


def measure_effective_cores(duration_s: float = 3.0) -> dict[str, Any]:
    affinity = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else list(range(os.cpu_count() or 1))
    nproc = None
    try:
        nproc = int(subprocess.check_output(["nproc"], text=True, timeout=3).strip())
    except Exception:
        nproc = os.cpu_count()
    quota_info: dict[str, Any] = {}
    for p in ("/sys/fs/cgroup/cpu.max", "/sys/fs/cgroup/cpu/cpu.cfs_quota_us", "/sys/fs/cgroup/cpu/cpu.cfs_period_us"):
        try:
            if Path(p).is_file():
                quota_info[Path(p).name] = Path(p).read_text().strip()
        except OSError:
            pass

    ctx = mp.get_context("fork") if hasattr(os, "fork") else mp.get_context("spawn")
    rows: list[dict[str, Any]] = []
    base_ops: Optional[float] = None
    for n in (1, 2, 4, 8, 16, 32):
        q: mp.Queue = ctx.Queue()
        procs = [ctx.Process(target=_busy_worker, args=(duration_s, q)) for _ in range(n)]
        t0 = time.perf_counter()
        for p in procs:
            p.start()
        counts: list[int] = []
        for _ in range(n):
            try:
                counts.append(int(q.get(timeout=duration_s + 5.0)))
            except Exception:
                pass
        for p in procs:
            p.join(timeout=2.0)
            if p.is_alive():
                p.kill()
                p.join(timeout=1.0)
        wall = time.perf_counter() - t0
        total_ops = sum(counts)
        if base_ops is None and counts:
            base_ops = float(total_ops)
        speedup = round(total_ops / base_ops, 2) if base_ops else 0.0
        rows.append({
            "n_procs": n,
            "completed": len(counts),
            "wall_s": round(wall, 3),
            "total_ops": total_ops,
            "per_proc_ops": round(statistics.mean(counts), 1) if counts else 0.0,
            "speedup_vs_1": speedup,
        })
    max_speedup = max((r["speedup_vs_1"] for r in rows), default=1.0)
    return {
        "nproc": nproc,
        "cpu_count": os.cpu_count(),
        "affinity_len": len(affinity),
        "affinity_sample": affinity[:16],
        "cgroup": quota_info,
        "sweep": rows,
        "estimated_real_cores": max_speedup,
    }


SAFE_THREAD_KEYS = (
    "OMP_NUM_THREADS",
    "GOMP_WAIT_POLICY",
    "OMP_PROC_BIND",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "LLAMA_THREADS",
    "LOCALAI_THREADS",
    "THREADS",
    "NUM_THREADS",
    "TORCH_NUM_THREADS",
    "GOMAXPROCS",
    "TOKIO_WORKER_THREADS",
)


def inspect_service_threads(models_dir: Optional[Path]) -> dict[str, Any]:
    out: dict[str, Any] = {"llm_yaml": {}, "processes": []}
    if models_dir and models_dir.is_dir():
        for yml in sorted(models_dir.glob("*llama*")):
            if yml.name.startswith("._"):
                continue
            txt = yml.read_text(errors="replace")
            fields: dict[str, str] = {}
            for ln in txt.splitlines():
                s = ln.strip()
                if any(s.startswith(k) for k in ("name:", "backend:", "threads:", "context_size:", "f16:", "mmap:", "batch:")):
                    k, _, v = s.partition(":")
                    fields[k.strip()] = v.strip()
            out["llm_yaml"][yml.name] = fields

    if platform.system() != "Linux":
        return out

    proc_matches: list[dict[str, Any]] = []
    for pdir in sorted(glob.glob("/proc/[0-9]*")):
        try:
            pid = int(Path(pdir).name)
            if pid == os.getpid():
                continue
            cmd_raw = (Path(pdir) / "cmdline").read_bytes()
            cmdline = " ".join(c.decode("utf-8", "replace") for c in cmd_raw.split(b"\x00") if c)
            low = cmdline.lower()
            if not any(tok in low for tok in ("local-ai", "localai", "llama", "kokoro", "local-tts", "parakeet", "uvicorn", "fastapi")):
                continue
            status_txt = (Path(pdir) / "status").read_text(errors="ignore")
            threads = None
            cpus_allowed = None
            name = None
            for ln in status_txt.splitlines():
                if ln.startswith("Name:"):
                    name = ln.split(":", 1)[1].strip()
                elif ln.startswith("Threads:"):
                    threads = int(ln.split(":", 1)[1].strip())
                elif ln.startswith("Cpus_allowed_list:"):
                    cpus_allowed = ln.split(":", 1)[1].strip()
            env_map: dict[str, str] = {}
            try:
                env_bytes = (Path(pdir) / "environ").read_bytes()
                for entry in env_bytes.split(b"\x00"):
                    if b"=" in entry:
                        k_b, v_b = entry.split(b"=", 1)
                        k_s = k_b.decode("utf-8", "ignore")
                        if k_s in SAFE_THREAD_KEYS or "THREAD" in k_s.upper() or k_s == "GOMAXPROCS":
                            if "KEY" not in k_s.upper() and "TOKEN" not in k_s.upper() and "SECRET" not in k_s.upper():
                                env_map[k_s] = v_b.decode("utf-8", "ignore")
            except OSError:
                pass
            arg_tokens = []
            skip_next = False
            for tok in cmdline.split():
                if skip_next:
                    arg_tokens.append("<redacted>")
                    skip_next = False
                    continue
                if any(s in tok.lower() for s in ("api-key", "apikey", "token", "secret", "password")):
                    arg_tokens.append(tok.split("=")[0] + "=<redacted>")
                    if "=" not in tok:
                        skip_next = True
                else:
                    arg_tokens.append(tok)
            proc_matches.append({
                "pid": pid,
                "name": name,
                "threads": threads,
                "cpus_allowed_list": cpus_allowed,
                "thread_env": env_map,
                "cmd_preview": " ".join(arg_tokens)[:220],
            })
        except (OSError, ValueError):
            continue
    out["processes"] = proc_matches
    return out


def http_json(url: str, payload: Optional[dict[str, Any]] = None, headers: Optional[dict[str, str]] = None, timeout: float = 25.0) -> tuple[int, bytes]:
    req_headers = dict(headers or {})
    data = None
    method = "GET"
    if payload is not None:
        method = "POST"
        data = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def detect_llm_model(base_url: str, headers: dict[str, str]) -> Optional[str]:
    try:
        st, body = http_json(f"{base_url.rstrip('/')}/v1/models", headers=headers, timeout=5.0)
        if st != 200:
            return None
        ids = [m.get("id", "") for m in json.loads(body.decode("utf-8", "replace")).get("data", []) if isinstance(m, dict)]
        for pref in ("llama-3.2-1b-instruct:q4_k_m", "llama-3.2-1b-instruct"):
            if pref in ids:
                return pref
        for mid in ids:
            low = mid.lower()
            if mid and not any(x in low for x in ("parakeet", "whisper", "tts", "kokoro", "nemo", "sherpa", "vad")):
                return mid
    except Exception:
        pass
    return None


def measure_voice_turns(
    base_url: str,
    tts_url: str,
    llm_model: str,
    headers: dict[str, str],
    turns: int = 20,
    pause_file: str = "",
    req_timeout: float = 25.0,
) -> dict[str, Any]:
    llm_lat: list[float] = []
    tts_lat: list[float] = []
    tot_lat: list[float] = []
    llm_url = f"{base_url.rstrip('/')}/v1/chat/completions"
    tts_stream_url = f"{tts_url.rstrip('/')}/v1/text-to-speech/af_bella/stream"
    tts_fallback_url = f"{tts_url.rstrip('/')}/v1/audio/speech"

    def do_tts(text: str) -> None:
        st, _ = http_json(tts_stream_url, {"text": text, "model_id": "kokoro", "voice_settings": {"speed": 1.0}}, headers, req_timeout)
        if st == 200:
            return
        st2, _ = http_json(tts_fallback_url, {"model": "kokoro", "input": text, "voice": "af_bella", "speed": 1.0}, headers, req_timeout)
        if st2 != 200:
            raise RuntimeError(f"TTS failed HTTP {st}/{st2}")

    for w_idx in range(2):
        if pause_file:
            Path(pause_file).write_text("busy")
        try:
            st_w, _ = http_json(
                llm_url,
                {"model": llm_model, "messages": [{"role": "user", "content": "Say hello in five words."}], "max_tokens": 30, "temperature": 0.2},
                headers,
                req_timeout,
            )
            if st_w != 200:
                raise RuntimeError(f"HTTP {st_w}")
            do_tts("Hello, ready to assist you.")
        except Exception as e:
            return {"turns": 0, "turns_target": turns, "failed_at": f"warmup_{w_idx+1}", "error": repr(e)}
        finally:
            if pause_file and os.path.exists(pause_file):
                os.unlink(pause_file)
        if pause_file:
            time.sleep(0.15)

    failed_at: Optional[str] = None
    error_msg: Optional[str] = None
    for idx in range(turns):
        if pause_file:
            Path(pause_file).write_text("busy")
        t0 = time.perf_counter()
        try:
            st1, _ = http_json(
                llm_url,
                {
                    "model": llm_model,
                    "messages": [
                        {"role": "system", "content": "You are a concise voice assistant."},
                        {"role": "user", "content": "Describe three key functions of an operating system kernel in 40 words."},
                    ],
                    "max_tokens": 50,
                    "temperature": 0.2,
                },
                headers,
                req_timeout,
            )
            t1 = time.perf_counter()
            if st1 != 200:
                raise RuntimeError(f"HTTP {st1}")
        except Exception as e:
            if pause_file and os.path.exists(pause_file):
                os.unlink(pause_file)
            failed_at, error_msg = f"turn_{idx+1}_llm", repr(e)
            break

        try:
            do_tts("The kernel manages process scheduling, virtual memory allocation, and hardware device drivers.")
            t2 = time.perf_counter()
        except Exception as e:
            if pause_file and os.path.exists(pause_file):
                os.unlink(pause_file)
            failed_at, error_msg = f"turn_{idx+1}_tts", repr(e)
            break
        finally:
            if pause_file and os.path.exists(pause_file):
                os.unlink(pause_file)

        llm_lat.append((t1 - t0) * 1000.0)
        tts_lat.append((t2 - t1) * 1000.0)
        tot_lat.append((t2 - t0) * 1000.0)
        if pause_file:
            time.sleep(0.15)

    s_tot, s_llm, s_tts = sorted(tot_lat), sorted(llm_lat), sorted(tts_lat)
    out: dict[str, Any] = {
        "turns": len(tot_lat),
        "turns_target": turns,
        "total_median_ms": round(statistics.median(s_tot), 1) if s_tot else None,
        "total_p95_ms": round(percentile(s_tot, 0.95) or 0.0, 1) if s_tot else None,
        "total_max_ms": round(max(s_tot), 1) if s_tot else None,
        "llm_median_ms": round(statistics.median(s_llm), 1) if s_llm else None,
        "llm_p95_ms": round(percentile(s_llm, 0.95) or 0.0, 1) if s_llm else None,
        "tts_median_ms": round(statistics.median(s_tts), 1) if s_tts else None,
    }
    if failed_at:
        out["failed_at"] = failed_at
        out["error"] = error_msg
    return out


def _apply_worker_limits(nice_val: int, cpu_list_str: str) -> dict[str, Any]:
    applied: dict[str, Any] = {"nice": None, "affinity": None}
    if nice_val > 0 and hasattr(os, "nice"):
        try:
            applied["nice"] = os.nice(nice_val)
        except OSError as e:
            applied["nice_err"] = str(e)
    if cpu_list_str and hasattr(os, "sched_setaffinity"):
        try:
            cores = [int(x) for x in cpu_list_str.split(",") if x.strip()]
            if cores:
                os.sched_setaffinity(0, set(cores))
                applied["affinity"] = sorted(os.sched_getaffinity(0))
        except OSError as e:
            applied["affinity_err"] = str(e)
    return applied


def worker_bg_stream(
    lib_path: str,
    model_path: str,
    wav_path: str,
    stop_file: str,
    ready_file: str,
    stats_file: str,
    pause_file: str,
    expected_abi: int,
    nice_val: int,
    cpu_list_str: str,
    catchup_rate: float,
) -> None:
    lim_info = _apply_worker_limits(nice_val, cpu_list_str)
    pcm = load_wav_16k_f32(Path(wav_path))
    capi = ParakeetCAPI(lib_path, expected_abi=expected_abi)
    ctx = capi.load(model_path)

    chunk_samples = int(16000 * 0.160)
    min_catchup_interval_s = 0.160 / max(catchup_rate, 1.0)
    chunk_ms_all: list[float] = []
    word_lag_ms_all: list[float] = []
    max_pending = 0
    passes = 0
    wall0 = time.perf_counter()
    cpu0 = time.process_time()

    try:
        Path(ready_file).write_text("ready")
        while not os.path.exists(stop_file):
            stream = capi.begin_stream(ctx)
            try:
                t_start = time.perf_counter()
                offset = 0
                pending: list[tuple[list[float], float]] = []
                word_seen: dict[int, tuple[str, float]] = {}
                prev_word_count = 0
                while offset < len(pcm) and not os.path.exists(stop_file):
                    chunk = pcm[offset : offset + chunk_samples]
                    audio_end_s = (offset + len(chunk)) / 16000.0
                    target = t_start + audio_end_s
                    now = time.perf_counter()
                    if target > now:
                        time.sleep(target - now)
                    pending.append((chunk, audio_end_s))
                    max_pending = max(max_pending, len(pending))
                    offset += chunk_samples

                    if pause_file and os.path.exists(pause_file):
                        continue

                    while pending and not os.path.exists(stop_file):
                        if pause_file and os.path.exists(pause_file):
                            break
                        c_samples, c_end_s = pending.pop(0)
                        t_f0 = time.perf_counter()
                        res = capi.feed_json(stream, c_samples)
                        dt_ms = (time.perf_counter() - t_f0) * 1000.0
                        chunk_ms_all.append(dt_ms)
                        words = res.get("words") or []
                        t_now = time.perf_counter() - t_start
                        if len(words) > prev_word_count:
                            for idx in range(prev_word_count, len(words)):
                                w = words[idx]
                                w_end = float(w.get("end", w.get("start", c_end_s)))
                                word_seen[idx] = (str(w.get("text", "")), (t_now - w_end) * 1000.0)
                            prev_word_count = len(words)
                        if pending and catchup_rate > 0:
                            sleep_rem = min_catchup_interval_s - (dt_ms / 1000.0)
                            if sleep_rem > 0:
                                time.sleep(sleep_rem)

                while pending and not os.path.exists(stop_file):
                    if pause_file and os.path.exists(pause_file):
                        time.sleep(0.05)
                        continue
                    c_samples, c_end_s = pending.pop(0)
                    t_f0 = time.perf_counter()
                    capi.feed_json(stream, c_samples)
                    dt_ms = (time.perf_counter() - t_f0) * 1000.0
                    chunk_ms_all.append(dt_ms)
                    if pending and catchup_rate > 0:
                        sleep_rem = min_catchup_interval_s - (dt_ms / 1000.0)
                        if sleep_rem > 0:
                            time.sleep(sleep_rem)

                if not os.path.exists(stop_file):
                    capi.finalize_json(stream)
                    passes += 1
                    word_lag_ms_all.extend(max(0.0, lag) for _, lag in word_seen.values())
            finally:
                capi.free_stream(stream)
    finally:
        capi.free_ctx(ctx)

    wall_s = time.perf_counter() - wall0
    cpu_s = time.process_time() - cpu0
    s_chunks = sorted(chunk_ms_all)
    s_lags = sorted(word_lag_ms_all)
    stats = {
        "limits_applied": lim_info,
        "passes_completed": passes,
        "chunks_fed": len(s_chunks),
        "max_pending_chunks": max_pending,
        "chunk_p50_ms": round(statistics.median(s_chunks), 2) if s_chunks else None,
        "chunk_p95_ms": round(percentile(s_chunks, 0.95) or 0.0, 2) if s_chunks else None,
        "chunk_max_ms": round(max(s_chunks), 2) if s_chunks else None,
        "word_lag_p50_ms": round(statistics.median(s_lags), 1) if s_lags else None,
        "word_lag_p95_ms": round(percentile(s_lags, 0.95) or 0.0, 1) if s_lags else None,
        "wall_s": round(wall_s, 3),
        "cpu_s": round(cpu_s, 3),
        "cpu_cores_avg": round(cpu_s / max(wall_s, 1e-6), 3),
        "rss_peak_mb": round(get_peak_rss_mb(), 1),
    }
    Path(stats_file).write_text(json.dumps(stats))


def worker_multistream(
    lib_path: str,
    model_path: str,
    wav_path: str,
    n_streams: int,
    expected_abi: int,
    nice_val: int,
    cpu_list_str: str,
) -> None:
    _apply_worker_limits(nice_val, cpu_list_str)
    pcm = load_wav_16k_f32(Path(wav_path))
    capi = ParakeetCAPI(lib_path, expected_abi=expected_abi)
    ctx = capi.load(model_path)
    lock = threading.Lock()
    results: list[dict[str, Any]] = [{} for _ in range(n_streams)]
    chunk_samples = int(16000 * 0.160)

    def run_one(idx: int) -> None:
        chunk_ms: list[float] = []
        with lock:
            stream = capi.begin_stream(ctx)
        try:
            t_start = time.perf_counter()
            offset = 0
            while offset < len(pcm):
                chunk = pcm[offset : offset + chunk_samples]
                target = t_start + (offset + len(chunk)) / 16000.0
                now = time.perf_counter()
                if target > now:
                    time.sleep(target - now)
                t0 = time.perf_counter()
                with lock:
                    capi.feed_json(stream, chunk)
                chunk_ms.append((time.perf_counter() - t0) * 1000.0)
                offset += chunk_samples
            with lock:
                fin = capi.finalize_json(stream)
            s_c = sorted(chunk_ms)
            results[idx] = {
                "stream": idx,
                "final_text": str(fin.get("text", "")).strip(),
                "chunk_p50_ms": round(statistics.median(s_c), 2) if s_c else None,
                "chunk_p95_ms": round(percentile(s_c, 0.95) or 0.0, 2) if s_c else None,
                "chunk_max_ms": round(max(s_c), 2) if s_c else None,
            }
        finally:
            with lock:
                capi.free_stream(stream)

    wall0 = time.perf_counter()
    cpu0 = time.process_time()
    threads = [threading.Thread(target=run_one, args=(i,)) for i in range(n_streams)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall_s = time.perf_counter() - wall0
    cpu_s = time.process_time() - cpu0
    capi.free_ctx(ctx)

    print(json.dumps({
        "n_streams": n_streams,
        "wall_s": round(wall_s, 3),
        "cpu_cores_avg": round(cpu_s / max(wall_s, 1e-6), 3),
        "rss_peak_mb": round(get_peak_rss_mb(), 1),
        "per_stream": results,
    }))


def run_voice_case(
    tag: str,
    base_url: str,
    tts_url: str,
    llm_model: str,
    headers: dict[str, str],
    lib_path: str,
    model_path: str,
    wav_path: str,
    expected_abi: int,
    extra_env: dict[str, str],
    nice_val: int,
    cpu_list_str: str,
    use_pause: bool,
    catchup_rate: float,
) -> dict[str, Any]:
    pid = os.getpid()
    stop_f = f"/tmp/s0b_stop_{tag}_{pid}"
    ready_f = f"/tmp/s0b_ready_{tag}_{pid}"
    stats_f = f"/tmp/s0b_stats_{tag}_{pid}.json"
    pause_f = f"/tmp/s0b_pause_{tag}_{pid}" if use_pause else ""
    err_f = f"/tmp/s0b_err_{tag}_{pid}.log"
    for p in (stop_f, ready_f, stats_f, pause_f, err_f):
        if p and os.path.exists(p):
            os.unlink(p)

    env = os.environ.copy()
    env.update(extra_env)
    cmd = make_py_cmd([
        "--worker-bg", lib_path, model_path, stop_f, ready_f, stats_f, pause_f,
        str(expected_abi), str(nice_val), cpu_list_str, str(catchup_rate),
        "--wav", wav_path,
    ])
    with open(err_f, "w") as efp:
        bg = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=efp)
    try:
        deadline = time.perf_counter() + 20.0
        while not os.path.exists(ready_f) and time.perf_counter() < deadline:
            if bg.poll() is not None:
                return {"voice": {"turns": 0, "failed_at": "bg_startup", "error": Path(err_f).read_text()[-400:]}}
            time.sleep(0.05)
        if not os.path.exists(ready_f):
            return {"voice": {"turns": 0, "failed_at": "bg_ready_timeout"}}
        voice_res = measure_voice_turns(base_url, tts_url, llm_model, headers, turns=20, pause_file=pause_f, req_timeout=25.0)
    finally:
        Path(stop_f).write_text("stop")
        try:
            bg.wait(timeout=15)
        except subprocess.TimeoutExpired:
            bg.kill()
            bg.wait(timeout=5)
    stream_stats: dict[str, Any] = {}
    if os.path.exists(stats_f):
        stream_stats = json.loads(Path(stats_f).read_text())
    for p in (stop_f, ready_f, stats_f, pause_f, err_f):
        if p and os.path.exists(p):
            try:
                os.unlink(p)
            except OSError:
                pass
    return {"voice": voice_res, "stream": stream_stats}


def eval_pass_rule(base: dict[str, Any], case: dict[str, Any]) -> str:
    v, s = case.get("voice", {}), case.get("stream", {})
    if v.get("turns") != 20 or not base.get("total_median_ms") or not base.get("total_p95_ms"):
        return "FAIL (incomplete turns)"
    med_lim = 1.10 * base["total_median_ms"]
    p95_lim = 1.50 * base["total_p95_ms"]
    c_p95 = s.get("chunk_p95_ms")
    ok = v["total_median_ms"] <= med_lim and v["total_p95_ms"] <= p95_lim and (c_p95 is not None and c_p95 < 160.0)
    return "PASS" if ok else f"FAIL (med<={med_lim:.0f}? {v['total_median_ms']}, p95<={p95_lim:.0f}? {v['total_p95_ms']}, chunk_p95<160? {c_p95})"


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase S0b CPU & Voice Interference Diagnostic")
    ap.add_argument("--base-url", default=parse_env_value("LOCALAI_BASE_URL") or "http://127.0.0.1:8080")
    ap.add_argument("--tts-url", default=parse_env_value("LOCAL_TTS_HTTP_BASE_URL") or "http://127.0.0.1:8880")
    ap.add_argument("--wav", default="tests/fixtures/audio/quiet_normal_speech.wav")
    ap.add_argument("--out", default="tmp/agent/s0/results_s0b.json")
    ap.add_argument("--expected-abi", type=int, default=EXPECTED_ABI_VERSION)
    ap.add_argument("--library", default="")
    ap.add_argument("--models-dir", default="")
    ap.add_argument("--cores-only", action="store_true", help="Run effective-cores + thread inspection only")
    ap.add_argument("--worker-bg", nargs=10, help=argparse.SUPPRESS)
    ap.add_argument("--worker-multi", nargs=6, help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.worker_bg:
        lib, mdl, stop_f, ready_f, stats_f, pause_f, abi_s, nice_s, cpus_s, rate_s = args.worker_bg
        worker_bg_stream(lib, mdl, args.wav, stop_f, ready_f, stats_f, pause_f, int(abi_s), int(nice_s), cpus_s, float(rate_s))
        return 0
    if args.worker_multi:
        lib, mdl, n_s, abi_s, nice_s, cpus_s = args.worker_multi
        worker_multistream(lib, mdl, args.wav, int(n_s), int(abi_s), int(nice_s), cpus_s)
        return 0

    libs = [os.path.realpath(args.library)] if args.library else discover_libparakeet()
    if libs:
        ensure_loader_and_reexec(libs[0])

    print("=== PHASE S0b POD EXPERIMENT ===")
    print(f"Host: {platform.node()} | OS: {platform.system()} {platform.machine()} | ABI={args.expected_abi}")

    models_dir = Path(args.models_dir) if args.models_dir else discover_models_dir()
    print("\n--- 1. Effective Cores Sweep (3s busy loop per N) ---")
    cores_res = measure_effective_cores(duration_s=3.0)
    print(f"  nproc={cores_res['nproc']} | cpu_count={cores_res['cpu_count']} | affinity_len={cores_res['affinity_len']} | cgroup={cores_res['cgroup']} | est_real_cores={cores_res['estimated_real_cores']}")
    for r in cores_res["sweep"]:
        print(f"  N={r['n_procs']:2d} | done={r['completed']} | wall={r['wall_s']}s | total_ops={r['total_ops']} | per_proc={r['per_proc_ops']} | speedup={r['speedup_vs_1']}x")

    print("\n--- 2. Other Service Thread Settings ---")
    svc_threads = inspect_service_threads(models_dir)
    print(f"  LLM YAMLs: {json.dumps(svc_threads['llm_yaml'])}")
    for p in svc_threads["processes"]:
        print(f"  PID={p['pid']} | name={p['name']} | Threads={p['threads']} | Cpus={p['cpus_allowed_list']} | env={p['thread_env']} | cmd={p['cmd_preview']}")

    doc: dict[str, Any] = {
        "host": platform.node(),
        "effective_cores": cores_res,
        "service_threads": svc_threads,
        "cases": {},
        "multistream": [],
    }
    if args.cores_only:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(doc, indent=2))
        return 0

    if not libs:
        raise RuntimeError("No libparakeet shared library found")
    capi = ParakeetCAPI(libs[0], expected_abi=args.expected_abi)
    eou_name, eou_path = find_eou_model(models_dir)
    print(f"\nModel: {eou_name} -> {eou_path} | Lib: {libs[0]} (ABI={capi.abi})")

    headers: dict[str, str] = {}
    api_key = parse_env_value("LOCALAI_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    llm_model = detect_llm_model(args.base_url, headers)
    if not llm_model:
        raise RuntimeError(f"No active LLM found at {args.base_url}/v1/models")

    affinity = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else []
    est_cores = float(cores_res["estimated_real_cores"])
    small_cpus = ""
    if len(affinity) >= 4:
        k = 1 if est_cores < 3.0 else 2
        small_cpus = ",".join(str(c) for c in affinity[-k:])
    print(f"Selected taskset CPU slice (based on est_real_cores={est_cores}): '{small_cpus or 'none'}'")

    print("\n--- 3 & 4. Voice-Turn Cases (20 turns LLM+TTS, EOU continuous stream) ---")
    base_voice = measure_voice_turns(args.base_url, args.tts_url, llm_model, headers, turns=20, req_timeout=25.0)
    doc["cases"]["baseline_0_streams"] = {"voice": base_voice, "stream": {}}
    print(f"  baseline_0_streams       : {json.dumps(base_voice)}")

    strict_env = {"OMP_NUM_THREADS": "1", "GOMP_WAIT_POLICY": "PASSIVE", "OMP_PROC_BIND": "false"}
    case_specs = [
        ("strict_no_pause", False, "", 0.0),
    ]
    if small_cpus:
        case_specs.append(("strict_no_pause_taskset", False, small_cpus, 0.0))
    case_specs.append(("paused_2x_catchup", True, "", 2.0))
    if small_cpus:
        case_specs.append(("paused_2x_taskset", True, small_cpus, 2.0))

    best_setting: tuple[str, int, str, str] = ("paused_2x_catchup", 19, "", "FAIL")
    best_rank = (-1, 1e9)
    for cname, use_pause, cpus_str, rate in case_specs:
        res = run_voice_case(
            cname, args.base_url, args.tts_url, llm_model, headers,
            libs[0], eou_path, args.wav, args.expected_abi,
            strict_env, 19, cpus_str, use_pause, rate,
        )
        verdict = eval_pass_rule(base_voice, res)
        res["pass_rule"] = verdict
        doc["cases"][cname] = res
        print(f"  {cname:25s}: verdict={verdict} | voice={json.dumps(res['voice'])} | stream={json.dumps(res['stream'])}")
        turns_ok = 1 if res["voice"].get("turns") == 20 else 0
        pass_ok = 1 if verdict == "PASS" else 0
        med_v = res["voice"].get("total_median_ms") or 1e9
        rank = (pass_ok * 10 + turns_ok, -med_v)
        if rank > best_rank:
            best_rank = rank
            best_setting = (cname, 19, cpus_str, verdict)

    win_name, win_nice, win_cpus, _ = best_setting
    print(f"\n--- 6. Multi-Stream Shared Context + Lock (setting={win_name}, OMP=1 PASSIVE, nice={win_nice}, taskset='{win_cpus or 'none'}') ---")
    for n in (1, 2, 3):
        env = os.environ.copy()
        env.update(strict_env)
        p = subprocess.run(
            make_py_cmd(["--worker-multi", libs[0], eou_path, str(n), str(args.expected_abi), str(win_nice), win_cpus, "--wav", args.wav]),
            env=env, capture_output=True, text=True, timeout=90,
        )
        lines = [ln for ln in p.stdout.splitlines() if ln.strip().startswith("{")]
        if p.returncode != 0 or not lines:
            row = {"n_streams": n, "error": (p.stderr or p.stdout).strip()[:300]}
        else:
            row = json.loads(lines[-1])
        doc["multistream"].append(row)
        print(f"  n={n}: {json.dumps(row)}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(doc, indent=2))

    print("\n================ PHASE S0b SUMMARY TABLE ================")
    print(f"EstRealCores={est_cores} (nproc={cores_res['nproc']}, affinity={cores_res['affinity_len']}, cgroup={cores_res['cgroup']})")
    b = base_voice
    print(f"BASE   | turns={b.get('turns')}/20 | med={b.get('total_median_ms')}ms p95={b.get('total_p95_ms')}ms max={b.get('total_max_ms')}ms | LLM={b.get('llm_median_ms')}ms TTS={b.get('tts_median_ms')}ms")
    for cname, data in doc["cases"].items():
        if cname == "baseline_0_streams":
            continue
        v, s = data["voice"], data["stream"]
        print(
            f"{cname:23s} | turns={v.get('turns')}/20 | med={v.get('total_median_ms')}ms p95={v.get('total_p95_ms')}ms max={v.get('total_max_ms')}ms "
            f"| LLM={v.get('llm_median_ms')}ms TTS={v.get('tts_median_ms')}ms | chunk={s.get('chunk_p50_ms')}/{s.get('chunk_p95_ms')}ms "
            f"| cores={s.get('cpu_cores_avg')} | lag={s.get('word_lag_p50_ms')}/{s.get('word_lag_p95_ms')}ms | rss={s.get('rss_peak_mb')}MB | {data.get('pass_rule')}"
        )
    for row in doc["multistream"]:
        p50s = [x.get("chunk_p50_ms") for x in row.get("per_stream", [])]
        p95s = [x.get("chunk_p95_ms") for x in row.get("per_stream", [])]
        print(f"MULTI n={row.get('n_streams')} | wall={row.get('wall_s')}s | cores={row.get('cpu_cores_avg')} | rss={row.get('rss_peak_mb')}MB | p50={p50s} p95={p95s}")
    print(f"Saved JSON report to {args.out}")
    print("=========================================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

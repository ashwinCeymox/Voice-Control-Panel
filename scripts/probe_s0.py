#!/usr/bin/env python3
"""Phase S0 Read-Only Feasibility Probe for Native parakeet.cpp Streaming.

Target: RunPod Linux pod in the same shell that reaches LocalAI & TTS.
Zero repo mutations, zero downloads, zero builds, zero secret logging.

NOTE ON MEMORY MANAGEMENT:
Every UTF-8 string returned by `parakeet_capi_stream_feed`,
`parakeet_capi_stream_feed_json`, `parakeet_capi_stream_finalize`, and
`parakeet_capi_stream_finalize_json` is heap-allocated by C/C++ and MUST be
released with `parakeet_capi_free_string(ptr)` cleanly in a `finally:` block.
Error strings from `parakeet_capi_load_error()` and `parakeet_capi_last_error()`
are borrowed pointers (never freed by caller).

ABI CHECK POLICY:
Refuses to execute model calls if `parakeet_capi_abi_version()` does not match
EXPECTED_ABI_VERSION (default 10; override with --expected-abi if explicitly tested).
"""

from __future__ import annotations

import argparse
import array
import ctypes
import glob
import json
import math
import os
import platform
import re
import resource
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Any, Optional

EXPECTED_ABI_VERSION = 10


def get_rss_mb() -> float:
    """Return current process RSS in MiB (/proc/self/status on Linux)."""
    status_path = Path("/proc/self/status")
    if status_path.is_file():
        try:
            for line in status_path.read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
        except Exception:
            pass
    usage = resource.getrusage(resource.RUSAGE_SELF)
    if platform.system() == "Darwin":
        return usage.ru_maxrss / (1024.0 * 1024.0)
    return usage.ru_maxrss / 1024.0


def get_cpu_seconds() -> float:
    u = resource.getrusage(resource.RUSAGE_SELF)
    return float(u.ru_utime + u.ru_stime)


def load_wav_16k_f32(wav_path: Path) -> list[float]:
    with wave.open(str(wav_path), "rb") as w:
        ch = w.getnchannels()
        sw = w.getsampwidth()
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    if ch != 1 or sw != 2 or sr != 16000:
        raise ValueError(f"Expected 16kHz mono PCM16, got ch={ch} sw={sw} sr={sr}")
    arr = array.array("h")
    arr.frombytes(raw)
    if sys.byteorder != "little":
        arr.byteswap()
    return [s / 32768.0 for s in arr]


def parse_env_value(key: str) -> Optional[str]:
    val = os.environ.get(key)
    if val:
        return val
    env_file = Path.cwd() / ".env"
    if env_file.is_file():
        try:
            for line in env_file.read_text(errors="ignore").splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
        except OSError:
            pass
    return None


def discover_artifacts() -> dict[str, Any]:
    """Find libparakeet shared libs, headers, parakeet-cli, and /proc maps."""
    libs: list[str] = []
    headers: list[str] = []
    clis: list[str] = []
    loaded_in_proc: list[str] = []

    env_lib = (os.environ.get("PARAKEET_LIBRARY") or parse_env_value("PARAKEET_LIBRARY") or "").strip()
    if env_lib and os.path.exists(env_lib):
        libs.append(os.path.realpath(env_lib))

    if Path("/proc").is_dir():
        for mp in glob.glob("/proc/[0-9]*/maps"):
            try:
                content = Path(mp).read_text(errors="ignore")
                for line in content.splitlines():
                    if "libparakeet" in line:
                        parts = line.split()
                        if parts and parts[-1].startswith("/"):
                            p = os.path.realpath(parts[-1])
                            if p not in loaded_in_proc:
                                loaded_in_proc.append(p)
                            if p not in libs and os.path.exists(p):
                                libs.append(p)
            except Exception:
                pass

    env_backends = (os.environ.get("LOCALAI_BACKENDS_PATH") or parse_env_value("LOCALAI_BACKENDS_PATH") or "").strip()
    search_roots = []
    if env_backends:
        search_roots.append(Path(env_backends))
    if Path("/proc").is_dir():
        for cp in glob.glob("/proc/[0-9]*/cmdline"):
            try:
                raw = Path(cp).read_bytes().replace(b"\x00", b" ").decode("utf-8", "ignore")
                if "local-ai" in raw:
                    m = re.search(r"--backends-path[=\s]+(\S+)", raw)
                    if m:
                        search_roots.append(Path(m.group(1)))
            except Exception:
                pass
    search_roots.extend([
        Path.cwd() / "backends",
        Path("/backends"),
        Path("/agent"),
        Path("/app"),
        Path("/workspace"),
        Path("/data"),
        Path("/var/lib/localai"),
        Path.home() / ".localai",
        Path.home() / ".local" / "share",
        Path("/opt/localai"),
        Path.cwd().parent / "LocalAI" / "backends",
    ])
    skip_dirs = ("node_modules", ".venv", ".git", "huggingface", "pip", "site-packages", "__pycache__", "tmp")
    for root in search_roots:
        try:
            if not root.exists():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".git")]
                for fn in filenames:
                    low = fn.lower()
                    full = os.path.join(dirpath, fn)
                    if low.startswith("libparakeet.") and (".so" in low or low.endswith(".dylib")):
                        rp = os.path.realpath(full)
                        if rp not in libs:
                            libs.append(rp)
                    elif "parakeet" in low and (low.endswith(".h") or low.endswith(".hpp")):
                        if full not in headers:
                            headers.append(full)
                    elif low == "parakeet-cli":
                        if full not in clis:
                            clis.append(full)
        except OSError:
            pass

    cli_which = shutil.which("parakeet-cli")
    if cli_which and cli_which not in clis:
        clis.append(cli_which)

    return {
        "libs": libs,
        "loaded_in_proc": loaded_in_proc,
        "headers": headers,
        "clis": clis,
    }


def discover_localai_models_dir() -> Optional[Path]:
    candidates: list[Path] = []
    env_path = parse_env_value("LOCALAI_MODELS_PATH")
    if env_path:
        candidates.append(Path(env_path))

    if Path("/proc").is_dir():
        for cp in glob.glob("/proc/[0-9]*/cmdline"):
            try:
                raw = Path(cp).read_bytes().replace(b"\x00", b" ").decode("utf-8", "ignore")
                if "local-ai" in raw:
                    m = re.search(r"--models-path[=\s]+(\S+)", raw)
                    if m:
                        candidates.append(Path(m.group(1)))
            except Exception:
                pass

    candidates.extend([
        Path("/models"),
        Path("/agent/LocalAI/models"),
        Path("/agent/models"),
        Path("/workspace/models"),
        Path("/workspace/LocalAI/models"),
        Path("/root/.localai/models"),
        Path("/root/.cache/localai/models"),
        Path("/root/models"),
        Path("/data/models"),
        Path.cwd().parent / "LocalAI" / "models",
        Path.cwd() / "models",
    ])
    for c in candidates:
        try:
            if c.is_dir() and any(c.rglob("*.yaml")):
                return c.resolve()
        except OSError:
            pass
    for c in candidates:
        try:
            if c.is_dir():
                return c.resolve()
        except OSError:
            pass
    return None


def parse_localai_yaml_simple(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(path.read_text(errors="ignore"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    result: dict[str, Any] = {}
    section: Optional[str] = None
    for line in path.read_text(errors="ignore").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        s = line.strip()
        if indent == 0 and ":" in s:
            k, v = s.split(":", 1)
            k = k.strip()
            v = v.strip()
            if k in ("parameters", "options", "known_usecases"):
                section = k
                if k == "parameters":
                    result["parameters"] = {}
                elif k == "options":
                    result["options"] = []
            else:
                section = None
                if v:
                    result[k] = v.strip('"').strip("'")
        elif indent > 0 and section == "parameters" and ":" in s:
            k, v = s.split(":", 1)
            result.setdefault("parameters", {})[k.strip()] = v.strip().strip('"').strip("'")
        elif indent > 0 and section == "options" and s.startswith("- "):
            result.setdefault("options", []).append(s[2:].strip())
    return result


def scan_stt_configs(models_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    yaml_files: list[Path] = []
    for pattern in ("*.yaml", "*.yml"):
        try:
            yaml_files.extend(models_dir.rglob(pattern))
        except OSError:
            pass

    for yf in sorted(set(yaml_files)):
        parsed = parse_localai_yaml_simple(yf)
        backend = str(parsed.get("backend", "") or "")
        name = str(parsed.get("name", "") or yf.stem)
        params = parsed.get("parameters") or {}
        model_rel = ""
        if isinstance(params, dict):
            model_rel = str(params.get("model", "") or "")
        if not model_rel:
            continue
        is_stt = any(x in backend.lower() or x in name.lower() for x in ("parakeet", "whisper", "nemo", "asr", "stt"))
        if not is_stt:
            continue

        resolved = (models_dir / model_rel) if not os.path.isabs(model_rel) else Path(model_rel)
        exists = False
        size_mb = 0.0
        try:
            if resolved.is_file():
                exists = True
                size_mb = resolved.stat().st_size / (1024 * 1024)
        except OSError:
            pass

        options = parsed.get("options") or []
        safe_opts = [
            str(o) for o in options
            if not any(sec in str(o).lower() for sec in ("key", "secret", "token", "pass"))
        ]
        records.append({
            "config_path": str(yf),
            "name": name,
            "backend": backend,
            "model_param": model_rel,
            "resolved_path": str(resolved),
            "exists": exists,
            "size_mb": round(size_mb, 1),
            "options": safe_opts,
        })
    return records


class ABIMismatchError(RuntimeError):
    pass


class ParakeetCAPI:
    def __init__(self, lib_path: str, expected_abi: int = EXPECTED_ABI_VERSION):
        self.lib_path = lib_path
        lib_dir = str(Path(lib_path).parent)
        parent_dir = str(Path(lib_path).parent.parent)
        env_key = "DYLD_LIBRARY_PATH" if platform.system() == "Darwin" else "LD_LIBRARY_PATH"
        cur = os.environ.get(env_key, "")
        os.environ[env_key] = f"{lib_dir}:{parent_dir}:{cur}" if cur else f"{lib_dir}:{parent_dir}"

        if platform.system() == "Linux":
            skip_prefixes = ("ld.so", "ld-linux", "libc.so", "libm.so", "libpthread", "libdl.so", "librt.so")
            for sib in sorted(list(Path(lib_dir).glob("*.so*")) + list(Path(parent_dir).glob("*.so*"))):
                if sib.name.startswith(skip_prefixes) or sib.name.startswith("libparakeet"):
                    continue
                try:
                    ctypes.CDLL(str(sib), mode=ctypes.RTLD_GLOBAL)
                except Exception:
                    pass

        try:
            self.lib = ctypes.CDLL(lib_path)
        except OSError as first_err:
            if platform.system() == "Linux" and os.environ.get("_S0_REEXEC") != "1" and "cannot open shared object file" in str(first_err):
                env = dict(os.environ)
                env["_S0_REEXEC"] = "1"
                env[env_key] = os.environ[env_key]
                os.execve(sys.executable, [sys.executable] + sys.argv, env)
            raise

        self.lib.parakeet_capi_abi_version.argtypes = []
        self.lib.parakeet_capi_abi_version.restype = ctypes.c_int
        self.abi = int(self.lib.parakeet_capi_abi_version())
        if expected_abi > 0 and self.abi != expected_abi:
            raise ABIMismatchError(
                f"Refusing to run: {lib_path} reports ABI version {self.abi}, expected {expected_abi}."
            )

        self._bind()

    def _bind(self) -> None:
        L = self.lib
        L.parakeet_capi_load.argtypes = [ctypes.c_char_p]
        L.parakeet_capi_load.restype = ctypes.c_void_p

        L.parakeet_capi_load_error.argtypes = []
        L.parakeet_capi_load_error.restype = ctypes.c_char_p  # borrowed

        L.parakeet_capi_free.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_free.restype = None

        L.parakeet_capi_last_error.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_last_error.restype = ctypes.c_char_p  # borrowed

        L.parakeet_capi_stream_begin.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_stream_begin.restype = ctypes.c_void_p

        L.parakeet_capi_stream_feed_json.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int,
        ]
        L.parakeet_capi_stream_feed_json.restype = ctypes.c_void_p  # owned; free via free_string

        L.parakeet_capi_stream_finalize_json.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_stream_finalize_json.restype = ctypes.c_void_p  # owned; free via free_string

        L.parakeet_capi_stream_free.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_stream_free.restype = None

        L.parakeet_capi_free_string.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_free_string.restype = None

    def has_symbol(self, name: str) -> bool:
        return hasattr(self.lib, name)

    def load(self, model_path: str) -> int:
        ctx = self.lib.parakeet_capi_load(model_path.encode("utf-8"))
        if not ctx:
            err = self.lib.parakeet_capi_load_error()
            raise RuntimeError(err.decode("utf-8", "replace") if err else "unknown load error")
        return int(ctx)

    def free_ctx(self, ctx: int) -> None:
        self.lib.parakeet_capi_free(ctypes.c_void_p(ctx))

    def begin_stream(self, ctx: int) -> int:
        s = self.lib.parakeet_capi_stream_begin(ctypes.c_void_p(ctx))
        if not s:
            err = self.lib.parakeet_capi_last_error(ctypes.c_void_p(ctx))
            raise RuntimeError(err.decode("utf-8", "replace") if err else "stream_begin failed")
        return int(s)

    def _consume_json(self, ptr: Optional[int]) -> dict[str, Any]:
        if not ptr:
            return {}
        try:
            raw = ctypes.string_at(ptr).decode("utf-8", "replace")
            return json.loads(raw) if raw else {}
        finally:
            self.lib.parakeet_capi_free_string(ctypes.c_void_p(ptr))

    def feed_json(self, stream: int, floats: list[float]) -> dict[str, Any]:
        n = len(floats)
        buf = (ctypes.c_float * n)(*floats)
        ptr = self.lib.parakeet_capi_stream_feed_json(ctypes.c_void_p(stream), buf, ctypes.c_int(n))
        return self._consume_json(ptr)

    def finalize_json(self, stream: int) -> dict[str, Any]:
        ptr = self.lib.parakeet_capi_stream_finalize_json(ctypes.c_void_p(stream))
        return self._consume_json(ptr)

    def free_stream(self, stream: int) -> None:
        self.lib.parakeet_capi_stream_free(ctypes.c_void_p(stream))


def percentile(sorted_vals: list[float], pct: float) -> Optional[float]:
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(math.ceil(pct * len(sorted_vals)) - 1)))
    return sorted_vals[idx]


def stream_once(
    capi: ParakeetCAPI,
    ctx: int,
    pcm: list[float],
    chunk_ms: int = 160,
    pace_realtime: bool = True,
    print_partials: bool = True,
    ctx_lock: Optional[threading.Lock] = None,
) -> dict[str, Any]:
    """Stream 16kHz float32 PCM through one stream session on ctx.

    If ctx_lock is supplied (Mode B shared context), every C-API call touching
    ctx or its child stream is serialized under ctx_lock.
    """
    chunk_samples = int(16000 * chunk_ms / 1000)

    def with_lock(fn, *fargs):
        if ctx_lock is not None:
            with ctx_lock:
                return fn(*fargs)
        return fn(*fargs)

    stream = with_lock(capi.begin_stream, ctx)
    partials: list[dict[str, Any]] = []
    word_first_seen: dict[str, dict[str, float]] = {}
    cum_text = ""
    chunk_compute_ms: list[float] = []

    try:
        t_stream_start = time.perf_counter()
        idx = 0
        offset = 0
        while offset < len(pcm):
            chunk = pcm[offset : offset + chunk_samples]
            audio_pos_s = (offset + len(chunk)) / 16000.0
            if pace_realtime:
                target_wall = t_stream_start + audio_pos_s
                now = time.perf_counter()
                if target_wall > now:
                    time.sleep(target_wall - now)

            t_call0 = time.perf_counter()
            res = with_lock(capi.feed_json, stream, chunk)
            t_call1 = time.perf_counter()
            call_ms = (t_call1 - t_call0) * 1000.0
            chunk_compute_ms.append(call_ms)

            piece = res.get("text", "") or ""
            events = res.get("events", []) or []
            words = res.get("words", []) or []
            eou = bool(res.get("eou", 0))

            if piece:
                cum_text = f"{cum_text}{piece}"
                partials.append({
                    "chunk_idx": idx,
                    "audio_s": round(audio_pos_s, 3),
                    "call_ms": round(call_ms, 2),
                    "chunk_text": piece,
                    "cum_text": cum_text.strip(),
                    "eou": eou,
                    "events": events,
                })
                if print_partials:
                    print(
                        f"    [chunk {idx:02d} @ {audio_pos_s:4.2f}s | {call_ms:5.1f}ms | eou={int(eou)}] "
                        f"raw={piece!r} | cum={cum_text.strip()!r}"
                    )

            for w in words:
                wt = str(w.get("w", "")).strip()
                if wt:
                    key = f"{idx}:{wt}:{w.get('start', 0)}"
                    word_end_s = float(w.get("end", audio_pos_s))
                    word_start_s = float(w.get("start", 0.0))
                    word_arrival_wall = t_stream_start + word_end_s
                    wall_lag_ms = (t_call1 - word_arrival_wall) * 1000.0
                    word_first_seen[key] = {
                        "word": wt,
                        "word_start_s": word_start_s,
                        "word_end_s": word_end_s,
                        "emitted_audio_s": audio_pos_s,
                        "wall_lag_ms": round(wall_lag_ms, 1),
                    }

            idx += 1
            offset += chunk_samples

        t_fin0 = time.perf_counter()
        fin = with_lock(capi.finalize_json, stream)
        t_fin1 = time.perf_counter()
        fin_ms = (t_fin1 - t_fin0) * 1000.0
        fin_piece = fin.get("text", "") or ""
        if fin_piece:
            cum_text = f"{cum_text}{fin_piece}"
        if print_partials:
            print(f"    [finalize        | {fin_ms:5.1f}ms] raw={fin_piece!r} => FINAL: {cum_text.strip()!r}")

        sorted_lags = sorted(v["wall_lag_ms"] for v in word_first_seen.values())
        sorted_chunk_ms = sorted(chunk_compute_ms)
        return {
            "final_text": cum_text.strip(),
            "partials_count": len(partials),
            "finalize_ms": round(fin_ms, 2),
            "chunk_compute_p50_ms": round(statistics.median(sorted_chunk_ms), 2) if sorted_chunk_ms else 0.0,
            "chunk_compute_p95_ms": round(percentile(sorted_chunk_ms, 0.95) or 0.0, 2),
            "chunk_compute_max_ms": round(max(sorted_chunk_ms), 2) if sorted_chunk_ms else 0.0,
            "word_lag_samples": len(sorted_lags),
            "word_lag_p50_ms": round(statistics.median(sorted_lags), 1) if sorted_lags else None,
            "word_lag_p95_ms": round(percentile(sorted_lags, 0.95), 1) if sorted_lags else None,
            "word_lag_max_ms": round(max(sorted_lags), 1) if sorted_lags else None,
            "words_sample": list(word_first_seen.values())[:6],
        }
    finally:
        with_lock(capi.free_stream, stream)


def worker_concurrency(mode: str, lib_path: str, model_path: str, n_streams: int, wav_path: str, expected_abi: int) -> None:
    """Isolated child worker measuring N concurrent streams under Mode A or Mode B.

    Mode A ('mode_a_per_stream'): 1 context per stream, parallel threads.
    Mode B ('mode_b_shared_lock'): 1 shared context, serialized behind a threading.Lock.
    """
    pcm = load_wav_16k_f32(Path(wav_path))
    rss_before = get_rss_mb()
    capi = ParakeetCAPI(lib_path, expected_abi=expected_abi)

    shared_ctx: Optional[int] = None
    ctx_lock: Optional[threading.Lock] = None
    separate_ctxs: list[int] = []

    t_load0 = time.perf_counter()
    if mode == "mode_b_shared_lock":
        shared_ctx = capi.load(model_path)
        ctx_lock = threading.Lock()
    else:
        for _ in range(n_streams):
            separate_ctxs.append(capi.load(model_path))
    load_s = time.perf_counter() - t_load0

    rss_after_load = get_rss_mb()
    cpu0 = get_cpu_seconds()
    t0 = time.perf_counter()

    barrier = threading.Barrier(n_streams)
    results: list[dict[str, Any]] = [{} for _ in range(n_streams)]
    errors: list[Optional[str]] = [None] * n_streams

    def run_thread(i: int) -> None:
        try:
            ctx = shared_ctx if mode == "mode_b_shared_lock" else separate_ctxs[i]
            assert ctx is not None
            barrier.wait(timeout=20.0)
            results[i] = stream_once(
                capi,
                ctx,
                pcm,
                chunk_ms=160,
                pace_realtime=True,
                print_partials=False,
                ctx_lock=ctx_lock,
            )
        except Exception as e:
            errors[i] = repr(e)

    threads = [threading.Thread(target=run_thread, args=(i,)) for i in range(n_streams)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90.0)

    wall_s = time.perf_counter() - t0
    cpu_s = get_cpu_seconds() - cpu0
    rss_peak = get_rss_mb()

    if shared_ctx is not None:
        capi.free_ctx(shared_ctx)
    for c in separate_ctxs:
        capi.free_ctx(c)

    print(json.dumps({
        "mode": mode,
        "n_streams": n_streams,
        "load_s": round(load_s, 3),
        "rss_before_mb": round(rss_before, 1),
        "rss_after_load_mb": round(rss_after_load, 1),
        "rss_peak_mb": round(rss_peak, 1),
        "wall_s": round(wall_s, 3),
        "cpu_s": round(cpu_s, 3),
        "cpu_cores_avg": round(cpu_s / max(wall_s, 1e-6), 3),
        "errors": errors,
        "final_texts": [r.get("final_text", "") for r in results],
        "chunk_p50_ms": [r.get("chunk_compute_p50_ms", 0.0) for r in results],
        "chunk_p95_ms": [r.get("chunk_compute_p95_ms", 0.0) for r in results],
        "chunk_max_ms": [r.get("chunk_compute_max_ms", 0.0) for r in results],
        "word_lag_p50_ms": [r.get("word_lag_p50_ms") for r in results],
        "word_lag_p95_ms": [r.get("word_lag_p95_ms") for r in results],
    }))


def worker_continuous_stream(lib_path: str, model_path: str, wav_path: str, stop_event_file: str, expected_abi: int) -> None:
    pcm = load_wav_16k_f32(Path(wav_path))
    capi = ParakeetCAPI(lib_path, expected_abi=expected_abi)
    ctx = capi.load(model_path)
    try:
        while not os.path.exists(stop_event_file):
            stream_once(capi, ctx, pcm, chunk_ms=160, pace_realtime=True, print_partials=False)
    finally:
        capi.free_ctx(ctx)


def http_json(url: str, payload: Optional[dict[str, Any]] = None, headers: Optional[dict[str, str]] = None, timeout: float = 30.0) -> tuple[int, bytes]:
    req_headers = dict(headers or {})
    data: Optional[bytes] = None
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
        status, body = http_json(f"{base_url.rstrip('/')}/v1/models", headers=headers, timeout=5.0)
        if status != 200:
            return None
        models = [m.get("id", "") for m in json.loads(body.decode("utf-8", "replace")).get("data", []) if isinstance(m, dict)]
        for preferred in ("llama-3.2-1b-instruct", "qwen3.5-0.8b-artemis-q4"):
            if preferred in models:
                return preferred
        for mid in models:
            low = mid.lower()
            if mid and not any(x in low for x in ("parakeet", "whisper", "tts", "kokoro", "nemo", "sherpa", "vad")):
                return mid
    except Exception:
        pass
    return None


def measure_voice_turns(base_url: str, tts_url: str, llm_model: str, headers: dict[str, str], turns: int = 20) -> dict[str, Any]:
    llm_lat: list[float] = []
    tts_lat: list[float] = []
    tot_lat: list[float] = []

    llm_url = f"{base_url.rstrip('/')}/v1/chat/completions"
    tts_stream_url = f"{tts_url.rstrip('/')}/v1/text-to-speech/af_bella/stream"
    tts_fallback_url = f"{tts_url.rstrip('/')}/v1/audio/speech"

    def do_tts(text: str) -> None:
        st, _ = http_json(
            tts_stream_url,
            payload={"text": text, "model_id": "kokoro", "voice_settings": {"speed": 1.0}},
            headers=headers,
            timeout=60.0,
        )
        if st == 200:
            return
        st2, _ = http_json(
            tts_fallback_url,
            payload={"model": "kokoro", "input": text, "voice": "af_bella", "speed": 1.0},
            headers=headers,
            timeout=60.0,
        )
        if st2 != 200:
            raise RuntimeError(f"TTS failed: HTTP {st} / fallback {st2}")

    # Warmup 2 turns
    for _ in range(2):
        http_json(
            llm_url,
            payload={
                "model": llm_model,
                "messages": [{"role": "user", "content": "Say hello in five words."}],
                "max_tokens": 30,
                "temperature": 0.2,
            },
            headers=headers,
            timeout=60.0,
        )
        do_tts("Hello, ready to assist you.")

    for _ in range(turns):
        t0 = time.perf_counter()
        st1, _ = http_json(
            llm_url,
            payload={
                "model": llm_model,
                "messages": [
                    {"role": "system", "content": "You are a concise voice assistant."},
                    {"role": "user", "content": "Describe three key functions of an operating system kernel in 40 words."},
                ],
                "max_tokens": 50,
                "temperature": 0.2,
            },
            headers=headers,
            timeout=60.0,
        )
        t1 = time.perf_counter()
        if st1 != 200:
            raise RuntimeError(f"LLM turn failed: HTTP {st1}")

        do_tts("The kernel manages process scheduling, virtual memory allocation, and hardware device drivers.")
        t2 = time.perf_counter()

        llm_lat.append((t1 - t0) * 1000.0)
        tts_lat.append((t2 - t1) * 1000.0)
        tot_lat.append((t2 - t0) * 1000.0)

    s_tot = sorted(tot_lat)
    s_llm = sorted(llm_lat)
    s_tts = sorted(tts_lat)
    return {
        "turns": turns,
        "total_median_ms": round(statistics.median(s_tot), 1),
        "total_p95_ms": round(percentile(s_tot, 0.95) or 0.0, 1),
        "total_max_ms": round(max(s_tot), 1),
        "llm_median_ms": round(statistics.median(s_llm), 1),
        "llm_p95_ms": round(percentile(s_llm, 0.95) or 0.0, 1),
        "tts_median_ms": round(statistics.median(s_tts), 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase S0 Feasibility Probe (Read-Only)")
    parser.add_argument("--discover-only", action="store_true")
    parser.add_argument("--library", default="", help="Override path to libparakeet.so / libparakeet.dylib")
    parser.add_argument("--models-dir", default="", help="Override LocalAI models directory")
    parser.add_argument("--expected-abi", type=int, default=EXPECTED_ABI_VERSION, help="Expected parakeet_capi ABI version")
    parser.add_argument("--base-url", default=parse_env_value("LOCALAI_BASE_URL") or "http://127.0.0.1:8080")
    parser.add_argument("--tts-url", default=parse_env_value("LOCAL_TTS_HTTP_BASE_URL") or os.environ.get("TTS_URL", "http://127.0.0.1:8880"))
    parser.add_argument("--wav", default="tests/fixtures/audio/quiet_normal_speech.wav")
    parser.add_argument("--out", default="tmp/agent/s0/results.json")
    parser.add_argument("--worker-concurrency", nargs=5, metavar=("MODE", "LIB", "MODEL", "N", "ABI"), help=argparse.SUPPRESS)
    parser.add_argument("--worker-bg-stream", nargs=4, metavar=("LIB", "MODEL", "STOPFILE", "ABI"), help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.worker_concurrency:
        mode, lib, model_path, n, exp_abi = args.worker_concurrency
        worker_concurrency(mode, lib, model_path, int(n), args.wav, int(exp_abi))
        return 0

    if args.worker_bg_stream:
        lib, model_path, stopfile, exp_abi = args.worker_bg_stream
        worker_continuous_stream(lib, model_path, args.wav, stopfile, int(exp_abi))
        return 0

    results_doc: dict[str, Any] = {
        "host": platform.node(),
        "os": f"{platform.system()} {platform.machine()}",
        "expected_abi": args.expected_abi,
        "q1_discovery": {},
        "q8_configs": [],
        "q2_q3_q7_streaming": {},
        "q4_q5_concurrency": {},
        "q6_interference": {},
    }

    print("=== PHASE S0 FEASIBILITY PROBE ===")
    print(f"Host: {results_doc['host']} | OS: {results_doc['os']} | Expected ABI: {args.expected_abi}")

    # Q1: Discover artifacts
    disc = discover_artifacts()
    if args.library:
        disc["libs"] = [os.path.realpath(args.library)] + [x for x in disc["libs"] if x != os.path.realpath(args.library)]

    print("\n--- Q1: Shared Library, Headers, CLI Discovery ---")
    print(f"  Discovered shared libs: {disc['libs']}")
    print(f"  Loaded in running procs: {disc['loaded_in_proc']}")
    print(f"  Headers found:          {disc['headers']}")
    print(f"  parakeet-cli found:     {disc['clis']}")

    capi: Optional[ParakeetCAPI] = None
    usable_lib: Optional[str] = None
    abi_reported: Optional[int] = None
    load_errors: list[str] = []

    for lib_cand in disc["libs"]:
        try:
            capi = ParakeetCAPI(lib_cand, expected_abi=args.expected_abi)
            usable_lib = lib_cand
            abi_reported = capi.abi
            scene_syms = all(capi.has_symbol(s) for s in (
                "parakeet_capi_scene_stream_begin",
                "parakeet_capi_scene_stream_feed_json",
                "parakeet_capi_scene_stream_free",
            ))
            print(f"  [OK] Loaded via ctypes: {lib_cand} (ABI version = {abi_reported}, scene_stream_syms = {scene_syms})")
            break
        except ABIMismatchError as e:
            print(f"  [REFUSED] {e}")
            results_doc["q1_discovery"] = {**disc, "abi_mismatch": str(e)}
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(results_doc, indent=2))
            return 2
        except Exception as e:
            err_s = f"{lib_cand}: {e}"
            load_errors.append(err_s)
            print(f"  [FAIL] ctypes load failed: {err_s}")

    results_doc["q1_discovery"] = {
        **disc,
        "usable_lib": usable_lib,
        "abi_version": abi_reported,
        "ctypes_errors": load_errors,
    }

    # Check CLI fallback if ctypes failed and parakeet-cli exists
    if not capi and disc["clis"]:
        for cli in disc["clis"]:
            try:
                out = subprocess.run([cli, "--help"], capture_output=True, text=True, timeout=5)
                print(f"  [CLI CHECK] {cli} --help rc={out.returncode} has_stream={'--stream' in (out.stdout + out.stderr)}")
            except Exception as e:
                print(f"  [CLI CHECK FAIL] {cli}: {e}")

    # Q8: LocalAI Model Config Discovery
    models_dir = Path(args.models_dir) if args.models_dir else discover_localai_models_dir()
    print("\n--- Q8: LocalAI Model Config Discovery ---")
    print(f"  Models directory: {models_dir}")
    configs: list[dict[str, Any]] = []
    if models_dir and models_dir.is_dir():
        configs = scan_stt_configs(models_dir)
        results_doc["q8_configs"] = configs
        for c in configs:
            print(f"  Model={c['name']} | backend={c['backend']} | file={c['resolved_path']} (exists={c['exists']}, {c['size_mb']} MB)")
            if c.get("options"):
                print(f"    options={c['options']}")
    else:
        print("  [MISSING] No LocalAI models directory found.")

    headers: dict[str, str] = {}
    api_key = parse_env_value("LOCALAI_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        st, body = http_json(f"{args.base_url.rstrip('/')}/v1/models", headers=headers, timeout=3.0)
        if st == 200:
            ids = [m.get("id") for m in json.loads(body.decode("utf-8", "replace")).get("data", []) if isinstance(m, dict)]
            print(f"  LocalAI /v1/models IDs: {ids}")
            results_doc["localai_model_ids"] = ids
    except Exception:
        pass

    if args.discover_only or not capi or not usable_lib:
        if not capi:
            print("\n[BLOCKED] Could not load libparakeet via ctypes; cannot run model streaming tests.")
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(results_doc, indent=2))
        return 0

    wav_path = Path(args.wav)
    if not wav_path.is_file():
        print(f"\n[BLOCKED] Test WAV missing: {wav_path}")
        return 1
    pcm = load_wav_16k_f32(wav_path)

    scene_cfg = next((c for c in configs if "realtime-scene" in c["name"].lower() and c["exists"]), None)
    eou_cfg = next((c for c in configs if c is not scene_cfg and "eou" in (c["name"] + " " + c["model_param"]).lower() and c["exists"]), None)
    nemo_cfg = next((c for c in configs if "nemotron" in (c["name"] + " " + c["model_param"]).lower() and "parakeet" in c["backend"].lower() and c["exists"]), None)

    tested_models: list[tuple[str, Optional[dict[str, Any]]]] = [
        ("EOU (120M)", eou_cfg),
        ("Nemotron (0.6B)", nemo_cfg),
        ("Realtime Scene (plain ASR GGUF check)", scene_cfg),
    ]

    # Q2, Q3, Q7: Streaming functional + word-to-partial latency
    print("\n--- Q2, Q3, Q7: Native Streaming Functional & Latency Tests (160 ms chunks) ---")
    stream_models_ready: list[tuple[str, dict[str, Any]]] = []
    for label, cfg in tested_models:
        if not cfg:
            print(f"  [{label}] SKIPPED (installed config/GGUF not found)")
            continue
        print(f"\n  Testing {label}: {cfg['name']} ({cfg['resolved_path']})")
        try:
            rss0 = get_rss_mb()
            t_load0 = time.perf_counter()
            ctx = capi.load(cfg["resolved_path"])
            load_ms = (time.perf_counter() - t_load0) * 1000.0
            rss1 = get_rss_mb()
            print(f"    Loaded ctx in {load_ms:.1f} ms | RSS {rss0:.1f} MB -> {rss1:.1f} MB (+{rss1-rss0:.1f} MB)")
            res = stream_once(capi, ctx, pcm, chunk_ms=160, pace_realtime=True, print_partials=True)
            res["load_ms"] = round(load_ms, 1)
            res["rss_model_delta_mb"] = round(rss1 - rss0, 1)
            res["works_plain_stream"] = True
            if cfg is scene_cfg:
                res["note"] = "Scene uses options (diarization+sound) requiring separate parakeet_capi_scene_stream_* API; plain stream_begin only exercises primary ASR weights."
            results_doc["q2_q3_q7_streaming"][cfg["name"]] = res
            print(f"    Summary: {json.dumps({k: v for k, v in res.items() if k != 'words_sample'})}")
            if cfg in (eou_cfg, nemo_cfg):
                stream_models_ready.append((label, cfg))
            capi.free_ctx(ctx)
        except Exception as e:
            print(f"    [FAIL] Streaming failed on {label}: {e}")
            results_doc["q2_q3_q7_streaming"][cfg["name"]] = {"works_plain_stream": False, "error": str(e)}

    if not stream_models_ready and scene_cfg and results_doc["q2_q3_q7_streaming"].get(scene_cfg["name"], {}).get("works_plain_stream"):
        stream_models_ready.append(("Realtime Scene primary GGUF", scene_cfg))

    # Q4 & Q5: Concurrency Mode A (per stream ctx) & Mode B (shared ctx locked), 1/2/3 streams
    print("\n--- Q4 & Q5: Concurrency (Mode A vs Mode B), RSS, & CPU Scaling ---")
    for label, cfg in stream_models_ready:
        print(f"\n  Model: {cfg['name']} ({label})")
        ref_text = results_doc["q2_q3_q7_streaming"][cfg["name"]].get("final_text", "")
        model_conc: list[dict[str, Any]] = []
        for mode in ("mode_a_per_stream", "mode_b_shared_lock"):
            for n in (1, 2, 3):
                cmd = [
                    sys.executable,
                    __file__,
                    "--worker-concurrency",
                    mode,
                    usable_lib,
                    cfg["resolved_path"],
                    str(n),
                    str(args.expected_abi),
                    "--wav",
                    str(wav_path),
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if proc.returncode != 0:
                    err_info = proc.stderr.strip()[:400]
                    print(f"    [{mode:18s} n={n}] CRASHED / FAILED (rc={proc.returncode}): {err_info}")
                    model_conc.append({"mode": mode, "n_streams": n, "status": f"CRASH_RC_{proc.returncode}", "error": err_info})
                    break
                lines = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
                if not lines:
                    print(f"    [{mode:18s} n={n}] NO JSON OUTPUT")
                    continue
                data = json.loads(lines[-1])
                all_match = all(
                    (t == ref_text and bool(t))
                    for t in data.get("final_texts", [])
                ) and not any(data.get("errors", []))
                data["matches_baseline"] = all_match
                model_conc.append(data)
                print(
                    f"    [{mode:18s} n={n}] match={all_match} | rss_peak={data['rss_peak_mb']} MB "
                    f"(+{round(data['rss_after_load_mb']-data['rss_before_mb'], 1)} MB load) | "
                    f"wall={data['wall_s']}s | cpu_cores={data['cpu_cores_avg']} | "
                    f"chunk_p50={data['chunk_p50_ms']} ms"
                )
        results_doc["q4_q5_concurrency"][cfg["name"]] = model_conc

    # Q6: Voice-turn interference (20 turns LLM+TTS with 0 vs 1 native stream)
    print("\n--- Q6: Voice-Turn Interference (20 LLM+TTS turns) ---")
    llm_model = detect_llm_model(args.base_url, headers)
    ref_cfg = eou_cfg or nemo_cfg or (stream_models_ready[0][1] if stream_models_ready else None)
    if not llm_model or not ref_cfg:
        print(f"  [SKIPPED] Missing active LLM ({llm_model}) on {args.base_url} or reference STT model.")
    else:
        print(f"  LLM={llm_model} ({args.base_url}) | TTS={args.tts_url} | STT bg stream={ref_cfg['name']}")
        try:
            base_turns = measure_voice_turns(args.base_url, args.tts_url, llm_model, headers, turns=20)
            print(f"  Baseline (0 streams):  {json.dumps(base_turns)}")

            stopfile = f"/tmp/s0_stop_{os.getpid()}"
            if os.path.exists(stopfile):
                os.unlink(stopfile)
            bg = subprocess.Popen([
                sys.executable,
                __file__,
                "--worker-bg-stream",
                usable_lib,
                ref_cfg["resolved_path"],
                stopfile,
                str(args.expected_abi),
                "--wav",
                str(wav_path),
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                time.sleep(2.5)
                int_turns = measure_voice_turns(args.base_url, args.tts_url, llm_model, headers, turns=20)
                print(f"  With 1 native stream:  {json.dumps(int_turns)}")
                results_doc["q6_interference"] = {
                    "llm_model": llm_model,
                    "stt_model": ref_cfg["name"],
                    "baseline_0_streams": base_turns,
                    "with_1_native_stream": int_turns,
                }
            finally:
                Path(stopfile).write_text("stop")
                try:
                    bg.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    bg.kill()
                if os.path.exists(stopfile):
                    os.unlink(stopfile)
        except Exception as e:
            print(f"  [FAIL] Voice-turn interference test error: {e}")
            results_doc["q6_interference"] = {"error": str(e)}

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results_doc, indent=2))

    # Print compact copy-paste summary table
    print("\n================ PHASE S0 COMPACT SUMMARY TABLE ================")
    print(f"Library: {usable_lib} (ABI={abi_reported})")
    print(f"Models Dir: {models_dir}")
    for mname, s in results_doc["q2_q3_q7_streaming"].items():
        if s.get("works_plain_stream"):
            print(
                f"STREAM | {mname} | load={s['load_ms']}ms | +RSS={s['rss_model_delta_mb']}MB | "
                f"chunk_p50={s['chunk_compute_p50_ms']}ms p95={s['chunk_compute_p95_ms']}ms | "
                f"word_lag p50={s['word_lag_p50_ms']}ms p95={s['word_lag_p95_ms']}ms | "
                f"text={s['final_text']!r}"
            )
        else:
            print(f"STREAM | {mname} | FAILED: {s.get('error')}")
    for mname, rows in results_doc["q4_q5_concurrency"].items():
        for r in rows:
            print(
                f"CONC   | {mname} | {r.get('mode')} n={r.get('n_streams')} | "
                f"match={r.get('matches_baseline')} | peak_rss={r.get('rss_peak_mb')}MB | "
                f"wall={r.get('wall_s')}s | cpu_cores={r.get('cpu_cores_avg')}"
            )
    if results_doc["q6_interference"]:
        q6 = results_doc["q6_interference"]
        if "baseline_0_streams" in q6:
            b = q6["baseline_0_streams"]
            w = q6["with_1_native_stream"]
            print(f"VOICE0 | med={b['total_median_ms']}ms p95={b['total_p95_ms']}ms max={b['total_max_ms']}ms (LLM med={b['llm_median_ms']}ms)")
            print(f"VOICE1 | med={w['total_median_ms']}ms p95={w['total_p95_ms']}ms max={w['total_max_ms']}ms (LLM med={w['llm_median_ms']}ms)")
    print(f"Saved JSON report to {args.out}")
    print("================================================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

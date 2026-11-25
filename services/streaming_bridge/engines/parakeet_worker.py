"""Subprocess worker hosting parakeet.cpp C-API (ABI 11) with crash isolation.

Launched by ParakeetNativeEngine (on Linux through the bundled ld.so + --library-path).
Verifies parakeet_capi_abi_version() == expected_abi before loading any model,
serializes calls on one shared parakeet_ctx, and speaks line-delimited JSON over stdio.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, Optional


class ParakeetStreamOpts(ctypes.Structure):
    """Exact mirror of struct parakeet_stream_opts from include/parakeet_capi.h (ABI 11)."""

    _fields_ = [
        ("size", ctypes.c_int),
        ("target_lang", ctypes.c_char_p),
        ("preset", ctypes.c_int),
        ("left_frames", ctypes.c_int),
        ("chunk_frames", ctypes.c_int),
        ("right_frames", ctypes.c_int),
        ("flags", ctypes.c_int),
    ]


class WorkerABIMismatchError(RuntimeError):
    pass


class ParakeetWorkerBinding:
    """Verified ABI 11 ctypes bindings matching mudler/parakeet.cpp include/parakeet_capi.h."""

    def __init__(self, lib_path: str, expected_abi: int = 11):
        self.lib_path = lib_path
        self.lib = ctypes.CDLL(lib_path)

        L = self.lib
        L.parakeet_capi_abi_version.argtypes = []
        L.parakeet_capi_abi_version.restype = ctypes.c_int

        self.abi = int(L.parakeet_capi_abi_version())
        if expected_abi > 0 and self.abi != expected_abi:
            raise WorkerABIMismatchError(
                f"{lib_path} reports parakeet_capi_abi_version()={self.abi}, expected {expected_abi}"
            )

        L.parakeet_capi_load.argtypes = [ctypes.c_char_p]
        L.parakeet_capi_load.restype = ctypes.c_void_p

        L.parakeet_capi_load_error.argtypes = []
        L.parakeet_capi_load_error.restype = ctypes.c_char_p  # borrowed TLS

        L.parakeet_capi_free.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_free.restype = None

        L.parakeet_capi_last_error.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_last_error.restype = ctypes.c_char_p  # borrowed from ctx

        L.parakeet_capi_stream_begin.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_stream_begin.restype = ctypes.c_void_p

        if hasattr(L, "parakeet_capi_stream_begin_lang"):
            L.parakeet_capi_stream_begin_lang.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            L.parakeet_capi_stream_begin_lang.restype = ctypes.c_void_p

        if hasattr(L, "parakeet_capi_stream_opts_default"):
            L.parakeet_capi_stream_opts_default.argtypes = [ctypes.POINTER(ParakeetStreamOpts)]
            L.parakeet_capi_stream_opts_default.restype = None

        if hasattr(L, "parakeet_capi_stream_begin_opts"):
            L.parakeet_capi_stream_begin_opts.argtypes = [ctypes.c_void_p, ctypes.POINTER(ParakeetStreamOpts)]
            L.parakeet_capi_stream_begin_opts.restype = ctypes.c_void_p

        if hasattr(L, "parakeet_capi_stream_kind"):
            L.parakeet_capi_stream_kind.argtypes = [ctypes.c_void_p]
            L.parakeet_capi_stream_kind.restype = ctypes.c_int

        L.parakeet_capi_stream_feed_json.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int,
        ]
        L.parakeet_capi_stream_feed_json.restype = ctypes.c_void_p  # owned malloc'd char*

        L.parakeet_capi_stream_finalize_json.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_stream_finalize_json.restype = ctypes.c_void_p  # owned malloc'd char*

        L.parakeet_capi_stream_free.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_stream_free.restype = None

        L.parakeet_capi_free_string.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_free_string.restype = None

    def _consume_owned_json(self, ptr: Optional[int], ctx: Optional[int]) -> Dict[str, Any]:
        if not ptr:
            err_b = self.lib.parakeet_capi_last_error(ctypes.c_void_p(ctx)) if ctx else None
            msg = err_b.decode("utf-8", "replace") if err_b else "parakeet stream JSON call returned NULL"
            raise RuntimeError(msg)
        try:
            raw = ctypes.cast(ptr, ctypes.c_char_p).value or b"{}"
            return json.loads(raw.decode("utf-8", errors="replace"))
        finally:
            self.lib.parakeet_capi_free_string(ctypes.c_void_p(ptr))

    def load(self, model_path: str) -> int:
        ctx = self.lib.parakeet_capi_load(model_path.encode("utf-8"))
        if not ctx:
            err_b = self.lib.parakeet_capi_load_error()
            msg = err_b.decode("utf-8", "replace") if err_b else f"Failed loading {model_path}"
            raise RuntimeError(msg)
        return int(ctx)

    def free_ctx(self, ctx: int) -> None:
        if ctx:
            self.lib.parakeet_capi_free(ctypes.c_void_p(ctx))

    def begin_stream(self, ctx: int, language: str = "en") -> int:
        if hasattr(self.lib, "parakeet_capi_stream_begin_lang") and language:
            stream = self.lib.parakeet_capi_stream_begin_lang(
                ctypes.c_void_p(ctx), language.encode("utf-8")
            )
        else:
            stream = self.lib.parakeet_capi_stream_begin(ctypes.c_void_p(ctx))
        if not stream:
            err_b = self.lib.parakeet_capi_last_error(ctypes.c_void_p(ctx))
            msg = err_b.decode("utf-8", "replace") if err_b else "parakeet_capi_stream_begin failed"
            raise RuntimeError(msg)
        return int(stream)

    def feed_f32_bytes(self, ctx: int, stream: int, f32_bytes: bytes) -> Dict[str, Any]:
        n_samples = len(f32_bytes) // 4
        if n_samples == 0:
            return {"text": "", "eou": 0, "eob": 0, "events": [], "words": []}
        arr = (ctypes.c_float * n_samples).from_buffer_copy(f32_bytes)
        ptr = self.lib.parakeet_capi_stream_feed_json(ctypes.c_void_p(stream), arr, n_samples)
        return self._consume_owned_json(ptr, ctx)

    def finalize(self, ctx: int, stream: int) -> Dict[str, Any]:
        ptr = self.lib.parakeet_capi_stream_finalize_json(ctypes.c_void_p(stream))
        return self._consume_owned_json(ptr, ctx)

    def free_stream(self, stream: int) -> None:
        if stream:
            self.lib.parakeet_capi_stream_free(ctypes.c_void_p(stream))


def _apply_os_limits(nice_val: Optional[int], cpu_affinity: str) -> None:
    if nice_val is not None and nice_val > 0 and hasattr(os, "nice"):
        try:
            os.nice(nice_val)
        except OSError:
            pass
    if cpu_affinity and hasattr(os, "sched_setaffinity"):
        try:
            cores = {int(c.strip()) for c in cpu_affinity.split(",") if c.strip()}
            if cores:
                os.sched_setaffinity(0, cores)
        except (OSError, ValueError):
            pass


def _emit(payload: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def run_worker_loop(
    lib_path: str,
    expected_abi: int = 11,
    nice_val: Optional[int] = None,
    cpu_affinity: str = "",
) -> int:
    _apply_os_limits(nice_val, cpu_affinity)
    try:
        capi = ParakeetWorkerBinding(lib_path, expected_abi=expected_abi)
    except WorkerABIMismatchError as e:
        _emit({"ready": False, "code": "ABI_MISMATCH", "error": str(e)})
        return 2
    except Exception as e:
        _emit({"ready": False, "code": "LOAD_LIB_FAILED", "error": str(e)})
        return 3

    _emit({"ready": True, "abi": capi.abi, "pid": os.getpid()})

    ctx: Optional[int] = None
    streams: Dict[str, int] = {}
    next_id = 0

    try:
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                op = req.get("op")
                if op == "ping":
                    _emit({"ok": True, "abi": capi.abi, "pid": os.getpid()})
                elif op == "load":
                    if ctx is not None:
                        for s_ptr in list(streams.values()):
                            capi.free_stream(s_ptr)
                        streams.clear()
                        capi.free_ctx(ctx)
                        ctx = None
                    model_path = str(req["model_path"])
                    ctx = capi.load(model_path)
                    _emit({"ok": True})
                elif op == "open_stream":
                    if ctx is None:
                        raise RuntimeError("No model loaded in worker")
                    s_ptr = capi.begin_stream(ctx, language=str(req.get("language", "en")))
                    next_id += 1
                    sid = f"wstream_{next_id}"
                    streams[sid] = s_ptr
                    _emit({"ok": True, "stream_id": sid})
                elif op == "feed":
                    sid = str(req["stream_id"])
                    s_ptr = streams.get(sid)
                    if not s_ptr or ctx is None:
                        raise KeyError(f"Unknown stream_id: {sid}")
                    raw_f32 = base64.b64decode(req.get("pcm_f32_b64", ""))
                    res = capi.feed_f32_bytes(ctx, s_ptr, raw_f32)
                    _emit({"ok": True, "result": res})
                elif op == "finalize":
                    sid = str(req["stream_id"])
                    s_ptr = streams.pop(sid, None)
                    if not s_ptr or ctx is None:
                        raise KeyError(f"Unknown stream_id: {sid}")
                    try:
                        res = capi.finalize(ctx, s_ptr)
                    finally:
                        capi.free_stream(s_ptr)
                    _emit({"ok": True, "result": res})
                elif op == "close_stream":
                    sid = str(req["stream_id"])
                    s_ptr = streams.pop(sid, None)
                    if s_ptr:
                        capi.free_stream(s_ptr)
                    _emit({"ok": True})
                elif op == "shutdown":
                    _emit({"ok": True})
                    break
                else:
                    _emit({"ok": False, "code": "UNKNOWN_OP", "error": f"Unknown op: {op}"})
            except Exception as exc:
                _emit({"ok": False, "code": "WORKER_OP_ERROR", "error": str(exc)})
    finally:
        for s_ptr in list(streams.values()):
            capi.free_stream(s_ptr)
        streams.clear()
        if ctx is not None:
            capi.free_ctx(ctx)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Parakeet C-API Native Worker")
    parser.add_argument("--library", required=True, help="Path to libparakeet.so / libparakeet.dylib")
    parser.add_argument("--expected-abi", type=int, default=11)
    parser.add_argument("--nice", type=int, default=None)
    parser.add_argument("--cpu-affinity", default="")
    args = parser.parse_args()
    return run_worker_loop(
        lib_path=str(Path(args.library)),
        expected_abi=args.expected_abi,
        nice_val=args.nice,
        cpu_affinity=args.cpu_affinity,
    )


if __name__ == "__main__":
    raise SystemExit(main())

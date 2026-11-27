#!/usr/bin/env python3
"""
Controlled read-only micro-benchmark for parakeet.cpp streaming chunk latency.

Measures:
1. Host CPU SIMD flags (avx2 / avx512f / fma) and libparakeet.so OpenMP linkage.
2. In-process thread sweep (model loaded ONCE) across engine_threads in [1, 2, 4, 8]
   via parakeet_capi_set_concurrency(ctx, 1, t).
3. Subprocess OMP_NUM_THREADS sweep (unset, 1, 4, 8) to distinguish an OpenMP
   thread-cap (OMP_NUM_THREADS=1) from a single-threaded streaming codepath.

Usage (on pod):
  python3 scripts/parakeet-thread-sweep.py
  python3 scripts/parakeet-thread-sweep.py --model parakeet-cpp-realtime_eou_120m-v1 --max-chunks 32
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import platform
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.streaming_bridge.engines.parakeet_native import resolve_parakeet_library  # noqa: E402
from services.streaming_bridge.engines.parakeet_worker import ParakeetWorkerBinding  # noqa: E402
from services.streaming_bridge.model_resolver import resolve_streaming_model_gguf  # noqa: E402


DEFAULT_MODEL = "parakeet-cpp-realtime_eou_120m-v1"
DEFAULT_WAV = REPO_ROOT / "tests" / "fixtures" / "audio" / "quiet_normal_speech.wav"
CHUNK_MS = 160
SAMPLE_RATE = 16000
SAMPLES_PER_CHUNK = (SAMPLE_RATE * CHUNK_MS) // 1000  # 2560


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(math.ceil((pct / 100.0) * len(ordered))) - 1))
    return ordered[idx]


def _self_threads() -> Optional[int]:
    status_path = Path("/proc/self/status")
    if not status_path.exists():
        return None
    for line in status_path.read_text(errors="ignore").splitlines():
        if line.startswith("Threads:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def _inspect_host_and_lib(lib_path: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "platform": platform.system(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "affinity_len": len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count(),
        "omp_num_threads_env": os.environ.get("OMP_NUM_THREADS"),
        "lib_path": lib_path,
        "cpu_model": "unknown",
        "simd_flags": {},
        "ldd_omp": [],
    }
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        text = cpuinfo.read_text(errors="ignore")
        for line in text.splitlines():
            if line.startswith("model name") and info["cpu_model"] == "unknown":
                info["cpu_model"] = line.split(":", 1)[1].strip()
            elif line.startswith("flags"):
                flags = set(line.split(":", 1)[1].strip().split())
                for f in ("avx", "avx2", "fma", "f16c", "avx512f", "avx512bw", "avx512_vnni"):
                    info["simd_flags"][f] = f in flags
                break
    if platform.system() == "Linux":
        try:
            out = subprocess.check_output(["ldd", lib_path], text=True, stderr=subprocess.STDOUT, timeout=5)
            info["ldd_omp"] = [ln.strip() for ln in out.splitlines() if "omp" in ln.lower() or "gomp" in ln.lower()]
        except Exception as e:
            info["ldd_omp"] = [f"ldd failed: {e}"]
    return info


def _load_f32_chunks(wav_path: Path, max_chunks: int) -> List[bytes]:
    with wave.open(str(wav_path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != SAMPLE_RATE:
            raise ValueError(
                f"Expected 16kHz mono 16-bit PCM WAV at {wav_path}, "
                f"got ch={wf.getnchannels()} sw={wf.getsampwidth()} sr={wf.getframerate()}"
            )
        pcm_bytes = wf.readframes(wf.getnframes())

    pcm_i16 = np.frombuffer(pcm_bytes, dtype=np.int16)
    pcm_f32 = (pcm_i16.astype(np.float32) / 32768.0).tobytes()
    bytes_per_chunk = SAMPLES_PER_CHUNK * 4
    chunks: List[bytes] = []
    for offset in range(0, len(pcm_f32), bytes_per_chunk):
        chunk = pcm_f32[offset : offset + bytes_per_chunk]
        if len(chunk) < bytes_per_chunk:
            chunk = chunk + b"\x00" * (bytes_per_chunk - len(chunk))
        chunks.append(chunk)
        if max_chunks > 0 and len(chunks) >= max_chunks:
            break
    return chunks


def _run_one_stream(
    capi: ParakeetWorkerBinding,
    ctx: int,
    chunks: List[bytes],
    engine_threads: int,
) -> Dict[str, Any]:
    set_rc: Optional[int] = None
    if hasattr(capi.lib, "parakeet_capi_set_concurrency"):
        set_rc = int(capi.lib.parakeet_capi_set_concurrency(ctypes.c_void_p(ctx), 1, int(engine_threads)))

    stream = capi.begin_stream(ctx, language="en")
    latencies_ms: List[float] = []
    threads_seen: Optional[int] = None
    transcript_parts: List[str] = []
    try:
        for idx, chunk in enumerate(chunks):
            t0 = time.perf_counter()
            res = capi.feed_f32_bytes(ctx, stream, chunk)
            dt_ms = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(dt_ms)
            if idx == len(chunks) // 2:
                threads_seen = _self_threads()
            txt = str(res.get("text") or "").strip()
            if txt:
                transcript_parts.append(txt)
        fin = capi.finalize(ctx, stream)
        fin_txt = str(fin.get("text") or "").strip()
        if fin_txt:
            transcript_parts.append(fin_txt)
    finally:
        capi.free_stream(stream)

    mean_ms = sum(latencies_ms) / max(1, len(latencies_ms))
    return {
        "engine_threads": engine_threads,
        "set_concurrency_rc": set_rc,
        "omp_num_threads": os.environ.get("OMP_NUM_THREADS", "<unset>"),
        "proc_threads": threads_seen if threads_seen is not None else _self_threads(),
        "chunks": len(latencies_ms),
        "mean_ms": round(mean_ms, 2),
        "p50_ms": round(_percentile(latencies_ms, 50), 2),
        "p95_ms": round(_percentile(latencies_ms, 95), 2),
        "rtf": round(mean_ms / float(CHUNK_MS), 2),
        "transcript_tail": " ".join(transcript_parts)[-80:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Parakeet streaming chunk latency thread sweep")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model name or path to .gguf")
    parser.add_argument("--wav", default=str(DEFAULT_WAV), help="16kHz mono PCM16 WAV file")
    parser.add_argument("--max-chunks", type=int, default=32, help="Max 160ms chunks per run (default: 32)")
    parser.add_argument("--threads", default="1,2,4,8", help="Comma-separated engine_threads for in-process sweep")
    parser.add_argument("--skip-omp-sweep", action="store_true", help="Skip subprocess OMP_NUM_THREADS sweep")
    parser.add_argument("--single-run", type=int, default=0, help="Internal: run single engine_threads and emit JSON")
    args = parser.parse_args()

    lib_path = resolve_parakeet_library()
    if not lib_path or not os.path.exists(lib_path):
        print(json.dumps({"error": "parakeet library not found", "lib_path": lib_path}), file=sys.stderr)
        return 2

    model_path = args.model if args.model.endswith(".gguf") and os.path.exists(args.model) else resolve_streaming_model_gguf(args.model)
    chunks = _load_f32_chunks(Path(args.wav), args.max_chunks)

    if args.single_run > 0:
        capi = ParakeetWorkerBinding(lib_path, expected_abi=11)
        ctx = capi.load(model_path, engine_threads=args.single_run)
        try:
            row = _run_one_stream(capi, ctx, chunks, args.single_run)
            print(json.dumps(row))
        finally:
            capi.free_ctx(ctx)
        return 0

    print("=== Host & Library Inspection ===")
    print(json.dumps(_inspect_host_and_lib(lib_path), indent=2))
    print(f"model_path: {model_path}")
    print(f"wav_path:   {args.wav} ({len(chunks)} chunks x {CHUNK_MS}ms)")

    thread_list = [int(x.strip()) for x in args.threads.split(",") if x.strip()]
    print("\n=== Sweep A: In-Process (model loaded ONCE; parakeet_capi_set_concurrency) ===")
    capi = ParakeetWorkerBinding(lib_path, expected_abi=11)
    t_load0 = time.perf_counter()
    ctx = capi.load(model_path, engine_threads=thread_list[0])
    print(f"load_ms: {(time.perf_counter() - t_load0) * 1000.0:.1f}")
    try:
        for t in thread_list:
            row = _run_one_stream(capi, ctx, chunks, t)
            print(json.dumps(row))
    finally:
        capi.free_ctx(ctx)

    if not args.skip_omp_sweep:
        print("\n=== Sweep B: Subprocess OMP_NUM_THREADS (tests OpenMP cap) ===")
        cases: List[Tuple[Optional[str], int]] = [(None, 4), ("1", 4), ("4", 4), ("8", 8)]
        for omp_val, eng_t in cases:
            env = dict(os.environ)
            if omp_val is None:
                env.pop("OMP_NUM_THREADS", None)
            else:
                env["OMP_NUM_THREADS"] = omp_val
            cmd = [
                sys.executable,
                __file__,
                "--model",
                model_path,
                "--wav",
                args.wav,
                "--max-chunks",
                str(args.max_chunks),
                "--single-run",
                str(eng_t),
            ]
            out = subprocess.check_output(cmd, env=env, text=True)
            print(out.strip())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

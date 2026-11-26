"""Offline Host-Gated Calibration Engine for Native Streaming STT (:8089).

Runs offline (run by owner, NEVER automatically at startup).
Never edits or restarts LocalAI/TTS configs — only inspects & reports service threads.
Measures:
1. Baseline voice turns (LLM then TTS, default 20 turns: median & p95).
2. Same with 1 real native STT stream running (collecting stream chunk_p95_ms).
3. Host fingerprint (CPU model, core count, affinity, cgroup quota, GPU, is_cpu_bound)
   plus observed LocalAI/TTS thread counts.
4. Strict Pass rule: 20/20 completions, voice_p95 <= 1.5x baseline_p95,
   voice_med <= 1.10x baseline_med, AND stream chunk_p95_ms < 160.0 ms.
   Otherwise records passed=False and admitted_streams=0.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import httpx
import numpy as np

from services.streaming_bridge.engines.parakeet_native import (
    ParakeetNativeEngine,
    resolve_parakeet_library,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("bridge_calibrate")

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))


def get_host_fingerprint() -> Dict[str, Any]:
    """Extract host hardware details and generate a stable fingerprint hash."""
    cpu_model = "unknown"
    if os.path.exists("/proc/cpuinfo"):
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        cpu_model = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass

    cores = os.cpu_count() or 1
    affinity_len = (
        len(os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else cores
    )
    cgroup_quota = "unlimited"
    if os.path.exists("/sys/fs/cgroup/cpu.max"):
        try:
            with open("/sys/fs/cgroup/cpu.max") as f:
                cgroup_quota = f.read().strip()
        except Exception:
            pass

    has_gpu = False
    try:
        import torch
        has_gpu = bool(torch.cuda.is_available())
    except Exception:
        pass

    is_cpu_bound = not has_gpu
    raw_id = f"{cpu_model}:{cores}:{cgroup_quota}:{has_gpu}"
    h = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:12]

    return {
        "fingerprint": h,
        "cpu_model": cpu_model,
        "cores": str(cores),
        "affinity_len": affinity_len,
        "cgroup_quota": cgroup_quota,
        "has_gpu": str(has_gpu),
        "is_cpu_bound": is_cpu_bound,
    }


def inspect_host_service_threads() -> List[Dict[str, Any]]:
    """Read-only inspection of running LocalAI/TTS/gateway process thread counts."""
    if platform.system() != "Linux":
        return []
    found: List[Dict[str, Any]] = []
    for pdir in sorted(glob.glob("/proc/[0-9]*")):
        try:
            cmd = (Path(pdir) / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace").lower()
            if not any(t in cmd for t in ("local-ai", "localai", "llama", "local-tts", "kokoro", "parakeet", "uvicorn")):
                continue
            status_txt = (Path(pdir) / "status").read_text(errors="ignore")
            name = ""
            threads = 0
            for ln in status_txt.splitlines():
                if ln.startswith("Name:"):
                    name = ln.split(":", 1)[1].strip()
                elif ln.startswith("Threads:"):
                    threads = int(ln.split(":", 1)[1].strip())
            found.append({"pid": int(Path(pdir).name), "name": name, "threads": threads})
        except (OSError, ValueError):
            continue
    return found


def get_calibration_file_path(model: str) -> Path:
    fp = get_host_fingerprint()["fingerprint"]
    safe_model = model.replace("/", "_").replace(":", "_")
    return DATA_DIR / f"bridge_calibration_{fp}_{safe_model}.json"


def evaluate_pass_rule(
    *,
    turns_completed: int,
    turns_target: int,
    base_med_ms: float,
    base_p95_ms: float,
    stream_med_ms: float,
    stream_p95_ms: float,
    chunk_p95_ms: float,
    chunk_budget_ms: float = 160.0,
) -> Tuple[bool, int, str]:
    """Apply Section 5 host calibration pass rule strictly (no loosening)."""
    if turns_completed < turns_target or base_med_ms <= 0 or base_p95_ms <= 0:
        return False, 0, f"Incomplete voice turns ({turns_completed}/{turns_target})"

    med_ratio = stream_med_ms / base_med_ms
    p95_ratio = stream_p95_ms / base_p95_ms
    med_ok = stream_med_ms <= (1.10 * base_med_ms)
    p95_ok = stream_p95_ms <= (1.50 * base_p95_ms)
    chunk_ok = 0.0 < chunk_p95_ms < chunk_budget_ms

    passed = med_ok and p95_ok and chunk_ok
    admitted = 1 if passed else 0
    reason = (
        "Thresholds met"
        if passed
        else (
            f"Thresholds failed (med={med_ratio:.2f}x<=1.10, "
            f"p95={p95_ratio:.2f}x<=1.50, chunk_p95={chunk_p95_ms:.1f}ms<{chunk_budget_ms:.0f}ms)"
        )
    )
    return passed, admitted, reason


def is_host_calibrated_and_passing(model: str) -> Tuple[bool, str, dict]:
    """Check if a valid, passing calibration file exists for this host and model."""
    calib_file = get_calibration_file_path(model)
    if not calib_file.exists():
        return False, f"No calibration file found at {calib_file}. Run scripts/bridge-calibrate.sh --model {model}", {}

    try:
        with open(calib_file, "r") as f:
            data = json.load(f)

        curr_fp = get_host_fingerprint()["fingerprint"]
        file_fp = data.get("host", {}).get("fingerprint")
        if curr_fp != file_fp:
            return False, f"Host fingerprint mismatch (current: {curr_fp}, file: {file_fp})", data

        if not data.get("passed", False) or int(data.get("admitted_streams", 0)) <= 0:
            return False, f"Host calibration failed: {data.get('verdict_reason', 'Thresholds exceeded')}", data

        return True, "Calibration passed", data
    except Exception as e:
        return False, f"Failed reading calibration file: {e}", {}


async def run_voice_turn(
    client: httpx.AsyncClient,
    base_url: str,
    tts_url: str,
    headers: dict,
    llm_model: str = "llama-3.2-1b-instruct:q4_k_m",
) -> Tuple[float, float, float]:
    """Run sequential voice turn (LLM completion then TTS audio synthesis)."""
    t0 = time.perf_counter()

    t_llm0 = time.perf_counter()
    llm_payload = {
        "model": llm_model,
        "messages": [
            {"role": "system", "content": "You are a concise voice assistant. Respond in 1-2 short sentences."},
            {"role": "user", "content": "What is the capital of France?"},
        ],
        "max_tokens": 50,
        "temperature": 0.0,
    }
    resp = await client.post(f"{base_url.rstrip('/')}/v1/chat/completions", json=llm_payload, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"LLM returned HTTP {resp.status_code}")
    dur_llm = (time.perf_counter() - t_llm0) * 1000.0

    dur_tts = 0.0
    if tts_url:
        t_tts0 = time.perf_counter()
        tts_payload = {"text": "The capital of France is Paris, a major European city.", "model_id": "kokoro"}
        tts_resp = await client.post(f"{tts_url.rstrip('/')}/v1/text-to-speech/af_bella/stream", json=tts_payload, headers=headers)
        if tts_resp.status_code != 200:
            raise RuntimeError(f"TTS returned HTTP {tts_resp.status_code}")
        dur_tts = (time.perf_counter() - t_tts0) * 1000.0

    return dur_llm, dur_tts, (time.perf_counter() - t0) * 1000.0


async def run_calibration(
    model: str,
    model_path: Optional[str] = None,
    base_url: str = "http://127.0.0.1:8080",
    tts_url: str = "http://127.0.0.1:8880",
    api_key: str = "",
    turns: int = 20,
    min_chunks: int = 50,
) -> dict:
    """Execute S0b-parity offline calibration against real ParakeetNativeEngine and save JSON."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    host_info = get_host_fingerprint()
    service_threads = inspect_host_service_threads()

    logger.info("STARTING NATIVE STREAMING CALIBRATION FOR MODEL: %s", model)
    logger.info("Host: %s | is_cpu_bound=%s | service_threads=%s", host_info, host_info["is_cpu_bound"], service_threads)

    resolved_model_path = model_path or os.environ.get("LOCALAI_MODELS_PATH", "models") + "/parakeet-cpp/realtime_eou_120m-v1-f16.gguf"
    lib_path = resolve_parakeet_library()

    chunk_bytes = b"\x00\x05" * 2560  # 160 ms @ 16kHz PCM16
    chunk_samples_tagged: List[Dict[str, Any]] = []
    baseline_times: List[float] = []
    stream_times: List[float] = []
    bg_error: Optional[str] = None
    voice_turn_active = False

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i in range(turns):
            try:
                _, _, tot = await run_voice_turn(client, base_url, tts_url, headers)
                baseline_times.append(tot)
            except Exception as e:
                logger.warning("Baseline turn %d error: %s", i + 1, e)

        base_med = float(np.median(baseline_times)) if baseline_times else 0.0
        base_p95 = float(np.percentile(baseline_times, 95)) if baseline_times else 0.0

        engine = ParakeetNativeEngine(
            lib_path=lib_path,
            is_cpu_bound=bool(host_info["is_cpu_bound"]),
        )
        await engine.load(resolved_model_path)
        sid = await engine.open_stream()
        running_bg = True

        async def bg_feeder() -> None:
            nonlocal bg_error
            while running_bg:
                started_during_turn = voice_turn_active
                t_c0 = time.perf_counter()
                try:
                    await engine.feed(sid, chunk_bytes, sample_rate=16000)
                except Exception as exc:
                    bg_error = f"{type(exc).__name__}: {exc}"
                    logger.error("Calibration background feeder failed: %s", bg_error)
                    print(f"ERROR: Calibration background feeder failed: {bg_error}", file=sys.stderr)
                    break
                elapsed_ms = (time.perf_counter() - t_c0) * 1000.0
                overlapped = started_during_turn or voice_turn_active
                chunk_samples_tagged.append({
                    "latency_ms": elapsed_ms,
                    "during_voice_turn": overlapped,
                })
                await asyncio.sleep(0.16)

        bg_task = asyncio.create_task(bg_feeder())
        try:
            for i in range(turns):
                if bg_error is not None:
                    break
                voice_turn_active = True
                try:
                    _, _, tot = await run_voice_turn(client, base_url, tts_url, headers)
                    stream_times.append(tot)
                except Exception as e:
                    logger.warning("1-stream turn %d error: %s", i + 1, e)
                finally:
                    voice_turn_active = False

            # Ensure at least min_chunks (default 50) total samples are collected unless feeder errored
            deadline = time.perf_counter() + max(10.0, min_chunks * 0.5)
            while bg_error is None and len(chunk_samples_tagged) < min_chunks and time.perf_counter() < deadline:
                await asyncio.sleep(0.05)
        finally:
            voice_turn_active = False
            running_bg = False
            bg_task.cancel()
            try:
                await bg_task
            except asyncio.CancelledError:
                pass
            await engine.close_stream(sid)
            await engine.close()

    turn_chunk_times_ms = [s["latency_ms"] for s in chunk_samples_tagged if s["during_voice_turn"]]
    topup_chunk_times_ms = [s["latency_ms"] for s in chunk_samples_tagged if not s["during_voice_turn"]]

    stream_med = float(np.median(stream_times)) if stream_times else 0.0
    stream_p95 = float(np.percentile(stream_times, 95)) if stream_times else 0.0
    chunk_p50 = float(np.median(turn_chunk_times_ms)) if turn_chunk_times_ms else 0.0
    chunk_p95 = float(np.percentile(turn_chunk_times_ms, 95)) if turn_chunk_times_ms else 0.0
    topup_p50 = float(np.median(topup_chunk_times_ms)) if topup_chunk_times_ms else 0.0
    topup_p95 = float(np.percentile(topup_chunk_times_ms, 95)) if topup_chunk_times_ms else 0.0

    measurements = {
        "turns_completed": len(stream_times),
        "turns_target": turns,
        "baseline_median_ms": round(base_med, 1),
        "baseline_p95_ms": round(base_p95, 1),
        "stream_median_ms": round(stream_med, 1),
        "stream_p95_ms": round(stream_p95, 1),
        "chunk_p50_ms": round(chunk_p50, 2),
        "chunk_p95_ms": round(chunk_p95, 2),
        "chunk_samples": len(turn_chunk_times_ms),
        "topup_chunk_p50_ms": round(topup_p50, 2),
        "topup_chunk_p95_ms": round(topup_p95, 2),
        "topup_chunk_samples": len(topup_chunk_times_ms),
        "total_chunk_samples": len(chunk_samples_tagged),
    }

    if bg_error is not None:
        result_data = {
            "model": model,
            "calibrated_at": time.time(),
            "host": host_info,
            "service_threads": service_threads,
            "measurements": measurements,
            "status": "error",
            "error": bg_error,
            "passed": False,
            "admitted_streams": 0,
            "verdict_reason": f"feeder_error: {bg_error}",
        }
    else:
        overall_pass, admitted_streams, verdict_reason = evaluate_pass_rule(
            turns_completed=len(stream_times),
            turns_target=turns,
            base_med_ms=base_med,
            base_p95_ms=base_p95,
            stream_med_ms=stream_med,
            stream_p95_ms=stream_p95,
            chunk_p95_ms=chunk_p95,
        )
        result_data = {
            "model": model,
            "calibrated_at": time.time(),
            "host": host_info,
            "service_threads": service_threads,
            "measurements": measurements,
            "status": "pass" if overall_pass else "fail",
            "passed": overall_pass,
            "admitted_streams": admitted_streams,
            "verdict_reason": verdict_reason,
        }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = get_calibration_file_path(model)
    with open(out_path, "w") as f:
        json.dump(result_data, f, indent=2)
    return result_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Native Streaming STT Host Calibration")
    parser.add_argument("--model", default=os.environ.get("LOCALAI_STT_MODEL", "parakeet-cpp-realtime_eou_120m-v1"))
    parser.add_argument("--model-path", default="")
    parser.add_argument("--base-url", default=os.environ.get("LOCALAI_BASE_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--tts-url", default=os.environ.get("TTS_URL", "http://127.0.0.1:8880"))
    parser.add_argument("--api-key", default=os.environ.get("LOCALAI_API_KEY", ""))
    args = parser.parse_args()
    asyncio.run(run_calibration(args.model, args.model_path or None, args.base_url, args.tts_url, args.api_key))


if __name__ == "__main__":
    main()

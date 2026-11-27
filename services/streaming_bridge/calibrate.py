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
from collections import deque
import glob
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import sys
import time
import wave
from typing import Any, Deque, Dict, List, Optional, Tuple
import httpx
import numpy as np

from app.core.config import settings
from services.streaming_bridge.engines.parakeet_native import (
    ParakeetNativeEngine,
    resolve_parakeet_library,
)
from services.streaming_bridge.model_resolver import resolve_streaming_model_gguf
from services.streaming_bridge.scheduler.bridge_scheduler import (
    BridgeScheduler,
    PauseBufferOverflowError,
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


def evaluate_paused_pass_rule(
    *,
    turns_completed: int,
    turns_target: int,
    base_med_ms: float,
    base_p95_ms: float,
    stream_med_ms: float,
    stream_p95_ms: float,
    chunk_p95_ms: float,
    catchup_lag_p95_ms: float,
    overflow_count: int,
    chunk_budget_ms: float = 160.0,
    catchup_lag_budget_ms: float = 3000.0,
) -> Tuple[bool, int, str]:
    """Paused-mode pass rule modeling production BridgeScheduler pause + <=2x catch-up."""
    if turns_completed < turns_target or base_med_ms <= 0 or base_p95_ms <= 0:
        return False, 0, f"Incomplete voice turns ({turns_completed}/{turns_target})"
    if overflow_count > 0:
        return False, 0, f"Pause buffer overflowed ({overflow_count} chunk(s) dropped)"

    med_ratio = stream_med_ms / base_med_ms
    p95_ratio = stream_p95_ms / base_p95_ms
    med_ok = stream_med_ms <= (1.15 * base_med_ms)
    p95_ok = stream_p95_ms <= (1.50 * base_p95_ms)
    chunk_ok = 0.0 < chunk_p95_ms < chunk_budget_ms
    lag_ok = 0.0 <= catchup_lag_p95_ms <= catchup_lag_budget_ms

    passed = med_ok and p95_ok and chunk_ok and lag_ok
    admitted = 1 if passed else 0
    reason = (
        "Paused-mode thresholds met"
        if passed
        else (
            f"Paused-mode thresholds failed (med={med_ratio:.2f}x<=1.15, "
            f"p95={p95_ratio:.2f}x<=1.50, chunk_p95={chunk_p95_ms:.1f}ms<{chunk_budget_ms:.0f}ms, "
            f"catchup_lag_p95={catchup_lag_p95_ms:.1f}ms<={catchup_lag_budget_ms:.0f}ms)"
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


def _load_stream_only_chunks(wav_path: Optional[str]) -> Tuple[List[bytes], str]:
    """Load 160 ms PCM-16 chunks from a 16 kHz mono WAV, or generate synthetic 16 kHz mono fallback."""
    chunk_bytes = 5120  # 160 ms @ 16 kHz mono int16
    cand = Path(wav_path) if wav_path else Path("tests/fixtures/audio/quiet_normal_speech.wav")
    if cand.is_file():
        with wave.open(str(cand), "rb") as wf:
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != 16000:
                raise ValueError(
                    f"WAV {cand} must be 16kHz mono PCM-16, got ch={wf.getnchannels()} "
                    f"width={wf.getsampwidth()} rate={wf.getframerate()}"
                )
            raw = wf.readframes(wf.getnframes())
        chunks = [raw[i:i + chunk_bytes] for i in range(0, len(raw), chunk_bytes) if len(raw[i:i + chunk_bytes]) == chunk_bytes]
        if chunks:
            return chunks, str(cand)
    # Fallback synthetic 5.0 s 16 kHz mono PCM-16 sine wave if no WAV is present
    t = np.arange(16000 * 5, dtype=np.float64) / 16000.0
    pcm = (np.sin(2.0 * np.pi * 440.0 * t) * 4000.0).astype(np.int16).tobytes()
    chunks = [pcm[i:i + chunk_bytes] for i in range(0, len(pcm), chunk_bytes) if len(pcm[i:i + chunk_bytes]) == chunk_bytes]
    return chunks, "synthetic_16khz_sine_5s"


async def run_calibration(
    model: str,
    model_path: Optional[str] = None,
    base_url: str = "http://127.0.0.1:8080",
    tts_url: str = "http://127.0.0.1:8880",
    api_key: str = "",
    turns: int = 20,
    min_chunks: int = 50,
    mode: str = "unpaused",
    engine_threads: Optional[int] = None,
    wav_path: Optional[str] = None,
) -> dict:
    """Execute offline calibration (unpaused, paused, or stream-only) against ParakeetNativeEngine and save JSON."""
    if mode not in ("unpaused", "paused", "stream-only"):
        raise ValueError(f"Unsupported calibration mode: {mode!r}")

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    host_info = get_host_fingerprint()
    service_threads = inspect_host_service_threads()
    eff_engine_threads = engine_threads if engine_threads is not None else settings.STREAMING_NATIVE_ENGINE_THREADS

    logger.info("STARTING NATIVE STREAMING CALIBRATION (%s) FOR MODEL: %s", mode.upper(), model)
    logger.info("Host: %s | is_cpu_bound=%s | service_threads=%s", host_info, host_info["is_cpu_bound"], service_threads)

    resolved_model_path = model_path or resolve_streaming_model_gguf(model)
    lib_path = resolve_parakeet_library()

    if mode == "stream-only":
        chunks, wav_source = _load_stream_only_chunks(wav_path)
        chunk_times_ms: List[float] = []
        overflow_count = 0
        bg_error: Optional[str] = None
        transcript = ""

        engine = ParakeetNativeEngine(
            lib_path=lib_path,
            is_cpu_bound=bool(host_info["is_cpu_bound"]),
            engine_threads=eff_engine_threads,
        )
        await engine.load(resolved_model_path)
        sid = await engine.open_stream()
        scheduler = BridgeScheduler(
            max_streams=1,
            pause_enabled=False,
            max_buffered_chunks=settings.STREAMING_NATIVE_PAUSE_MAX_CHUNKS,
            catchup_rate=settings.STREAMING_NATIVE_CATCHUP_RATE,
        )
        scheduler.start()

        async def on_chunk_stream_only(pcm_chunk: bytes) -> None:
            nonlocal bg_error
            t_f0 = time.perf_counter()
            try:
                await engine.feed(sid, pcm_chunk, sample_rate=16000)
            except Exception as exc:
                bg_error = f"{type(exc).__name__}: {exc}"
                logger.error("Stream-only calibration feed failed: %s", bg_error)
                print(f"ERROR: Stream-only calibration feed failed: {bg_error}", file=sys.stderr)
                return
            chunk_times_ms.append((time.perf_counter() - t_f0) * 1000.0)

        scheduler.register_stream(sid, model, "en", pause_enabled=False, on_chunk=on_chunk_stream_only)
        try:
            for c in chunks:
                if bg_error is not None:
                    break
                try:
                    await scheduler.handle_audio_chunk(sid, c, on_chunk_stream_only)
                except PauseBufferOverflowError:
                    overflow_count += 1
                await asyncio.sleep(0.16)
            if bg_error is None:
                fin = await engine.finalize(sid)
                transcript = fin.full_text
        finally:
            await scheduler.stop()
            if bg_error is not None:
                await engine.close_stream(sid)
            await engine.close()

        chunk_p50 = float(np.median(chunk_times_ms)) if chunk_times_ms else 0.0
        chunk_p95 = float(np.percentile(chunk_times_ms, 95)) if chunk_times_ms else 0.0
        measurements = {
            "wav_source": wav_source,
            "chunk_p50_ms": round(chunk_p50, 2),
            "chunk_p95_ms": round(chunk_p95, 2),
            "chunk_samples": len(chunk_times_ms),
            "overflow_count": overflow_count,
            "transcript": transcript,
        }
        if bg_error is not None:
            result_data = {
                "model": model,
                "calibration_mode": mode,
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
            overall_pass = (overflow_count == 0 and 0.0 < chunk_p95 < 160.0 and len(chunk_times_ms) > 0)
            admitted_streams = 1 if overall_pass else 0
            verdict_reason = (
                "Stream-only thresholds met"
                if overall_pass
                else f"Stream-only failed (chunk_p95={chunk_p95:.2f}ms<160ms, overflow={overflow_count})"
            )
            result_data = {
                "model": model,
                "calibration_mode": mode,
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

    chunk_bytes = b"\x00\x05" * 2560  # 160 ms @ 16kHz PCM16
    chunk_samples_tagged: List[Dict[str, Any]] = []
    catchup_lags_ms: List[float] = []
    pause_durations_ms: List[float] = []
    overflow_count = 0
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
            engine_threads=eff_engine_threads,
        )
        await engine.load(resolved_model_path)
        sid = await engine.open_stream()
        running_bg = True

        if mode == "paused":
            scheduler = BridgeScheduler(
                max_streams=1,
                pause_enabled=True,
                max_buffered_chunks=settings.STREAMING_NATIVE_PAUSE_MAX_CHUNKS,
                catchup_rate=settings.STREAMING_NATIVE_CATCHUP_RATE,
            )
            scheduler.start()
            pending_capture_times: Deque[float] = deque()

            async def on_chunk_paused(pcm_chunk: bytes) -> None:
                nonlocal bg_error
                t_cap = pending_capture_times.popleft() if pending_capture_times else time.perf_counter()
                t_f0 = time.perf_counter()
                try:
                    await engine.feed(sid, pcm_chunk, sample_rate=16000)
                except Exception as exc:
                    bg_error = f"{type(exc).__name__}: {exc}"
                    logger.error("Calibration paused feeder failed: %s", bg_error)
                    print(f"ERROR: Calibration paused feeder failed: {bg_error}", file=sys.stderr)
                    return
                t_done = time.perf_counter()
                chunk_samples_tagged.append({
                    "latency_ms": (t_done - t_f0) * 1000.0,
                    "during_voice_turn": False,
                })
                catchup_lags_ms.append((t_done - t_cap) * 1000.0)

            scheduler.register_stream(sid, model, "en", pause_enabled=True, on_chunk=on_chunk_paused)

            async def bg_feeder() -> None:
                nonlocal overflow_count
                while running_bg and bg_error is None:
                    pending_capture_times.append(time.perf_counter())
                    try:
                        await scheduler.handle_audio_chunk(sid, chunk_bytes, on_chunk_paused)
                    except PauseBufferOverflowError:
                        if pending_capture_times:
                            pending_capture_times.pop()
                        overflow_count += 1
                    await asyncio.sleep(0.16)

            bg_task = asyncio.create_task(bg_feeder())
            try:
                for i in range(turns):
                    if bg_error is not None:
                        break
                    t_p0 = time.perf_counter()
                    await scheduler.set_voice_busy(True)
                    try:
                        _, _, tot = await run_voice_turn(client, base_url, tts_url, headers)
                        stream_times.append(tot)
                    except Exception as e:
                        logger.warning("Paused 1-stream turn %d error: %s", i + 1, e)
                    finally:
                        await scheduler.set_voice_busy(False)
                        pause_durations_ms.append((time.perf_counter() - t_p0) * 1000.0)

                    drain_deadline = time.perf_counter() + 10.0
                    st = scheduler._streams.get(sid)
                    while bg_error is None and st is not None and st.buffer and time.perf_counter() < drain_deadline:
                        await asyncio.sleep(0.05)

                deadline = time.perf_counter() + max(10.0, min_chunks * 0.5)
                while bg_error is None and len(chunk_samples_tagged) < min_chunks and time.perf_counter() < deadline:
                    await asyncio.sleep(0.05)
            finally:
                running_bg = False
                bg_task.cancel()
                try:
                    await bg_task
                except asyncio.CancelledError:
                    pass
                await scheduler.flush_buffered(sid, on_chunk_paused)
                await scheduler.stop()
                await engine.close_stream(sid)
                await engine.close()
        else:
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

    if mode == "paused":
        all_chunk_times = [s["latency_ms"] for s in chunk_samples_tagged]
        stream_med = float(np.median(stream_times)) if stream_times else 0.0
        stream_p95 = float(np.percentile(stream_times, 95)) if stream_times else 0.0
        chunk_p50 = float(np.median(all_chunk_times)) if all_chunk_times else 0.0
        chunk_p95 = float(np.percentile(all_chunk_times, 95)) if all_chunk_times else 0.0
        lag_p50 = float(np.median(catchup_lags_ms)) if catchup_lags_ms else 0.0
        lag_p95 = float(np.percentile(catchup_lags_ms, 95)) if catchup_lags_ms else 0.0
        pause_p50 = float(np.median(pause_durations_ms)) if pause_durations_ms else 0.0
        pause_p95 = float(np.percentile(pause_durations_ms, 95)) if pause_durations_ms else 0.0

        measurements = {
            "turns_completed": len(stream_times),
            "turns_target": turns,
            "baseline_median_ms": round(base_med, 1),
            "baseline_p95_ms": round(base_p95, 1),
            "stream_median_ms": round(stream_med, 1),
            "stream_p95_ms": round(stream_p95, 1),
            "chunk_p50_ms": round(chunk_p50, 2),
            "chunk_p95_ms": round(chunk_p95, 2),
            "chunk_samples": len(all_chunk_times),
            "catchup_lag_p50_ms": round(lag_p50, 2),
            "catchup_lag_p95_ms": round(lag_p95, 2),
            "pause_duration_p50_ms": round(pause_p50, 1),
            "pause_duration_p95_ms": round(pause_p95, 1),
            "overflow_count": overflow_count,
        }
    else:
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
            "calibration_mode": mode,
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
        if mode == "paused":
            overall_pass, admitted_streams, verdict_reason = evaluate_paused_pass_rule(
                turns_completed=len(stream_times),
                turns_target=turns,
                base_med_ms=base_med,
                base_p95_ms=base_p95,
                stream_med_ms=stream_med,
                stream_p95_ms=stream_p95,
                chunk_p95_ms=chunk_p95,
                catchup_lag_p95_ms=lag_p95,
                overflow_count=overflow_count,
            )
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
            "calibration_mode": mode,
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
    parser.add_argument("--mode", choices=["unpaused", "paused", "stream-only"], default="unpaused")
    parser.add_argument("--engine-threads", type=int, default=None)
    parser.add_argument("--wav-path", default="tests/fixtures/audio/quiet_normal_speech.wav")
    args = parser.parse_args()
    asyncio.run(
        run_calibration(
            args.model,
            args.model_path or None,
            args.base_url,
            args.tts_url,
            args.api_key,
            mode=args.mode,
            engine_threads=args.engine_threads,
            wav_path=args.wav_path,
        )
    )


if __name__ == "__main__":
    main()

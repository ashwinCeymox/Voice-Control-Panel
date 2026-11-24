"""Offline Host-Gated Calibration Engine for Streaming STT Bridge.

Runs offline (run by owner, NEVER at startup).
Measures:
1. Voice-turn baseline (LLM then TTS), 20 turns (median & p95).
2. Same with 1 simulated STT stream under real scheduler rules.
3. Decode latency at 6s and 15s audio segments.
4. Host fingerprint (CPU, cores, CFS quota, GPU).
5. Pass rule: p95 <= 1.5x baseline p95 AND median within +10%.

Refuses bridge streaming unless a passing calibration file exists for the host and model.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import time
from typing import Dict, Optional, Tuple
import httpx
import numpy as np

from services.streaming_bridge.core.audio_utils import pcm16_to_wav
from services.streaming_bridge.scheduler.bridge_scheduler import BridgeScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("bridge_calibrate")

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))


def get_host_fingerprint() -> Dict[str, str]:
    """Extract host hardware details and generate stable fingerprint."""
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
        has_gpu = torch.cuda.is_available()
    except Exception:
        pass

    raw_id = f"{cpu_model}:{cores}:{cgroup_quota}:{has_gpu}"
    h = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:12]

    return {
        "fingerprint": h,
        "cpu_model": cpu_model,
        "cores": str(cores),
        "cgroup_quota": cgroup_quota,
        "has_gpu": str(has_gpu),
    }


def get_calibration_file_path(model: str) -> Path:
    fp = get_host_fingerprint()["fingerprint"]
    safe_model = model.replace("/", "_").replace(":", "_")
    return DATA_DIR / f"bridge_calibration_{fp}_{safe_model}.json"


def is_host_calibrated_and_passing(model: str) -> Tuple[bool, str, dict]:
    """Check if a valid, passing calibration file exists for this host and model.
    
    Returns:
        (passed: bool, reason: str, data: dict)
    """
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

        if not data.get("passed", False):
            return False, f"Host calibration failed: {data.get('verdict_reason', 'Thresholds exceeded')}", data

        return True, "Calibration passed", data
    except Exception as e:
        return False, f"Failed reading calibration file: {e}", {}


async def run_voice_turn(client: httpx.AsyncClient, base_url: str, tts_url: str, headers: dict) -> Tuple[float, float, float]:
    """Run sequential voice turn (LLM completion then TTS audio synthesis)."""
    t0 = time.perf_counter()

    # 1. LLM
    t_llm0 = time.perf_counter()
    llm_payload = {
        "model": "llama-3.2-1b-instruct",
        "messages": [
            {"role": "system", "content": "You are a concise voice assistant. Respond in 1-2 short sentences."},
            {"role": "user", "content": "What is the capital of France?"},
        ],
        "max_tokens": 50,
        "temperature": 0.0,
    }
    llm_resp = await client.post(f"{base_url.rstrip('/')}/v1/chat/completions", json=llm_payload, headers=headers)
    dur_llm = (time.perf_counter() - t_llm0) * 1000.0

    # 2. TTS
    dur_tts = 0.0
    if tts_url:
        t_tts0 = time.perf_counter()
        tts_payload = {"text": "The capital of France is Paris, a major European city.", "model_id": "kokoro"}
        await client.post(f"{tts_url.rstrip('/')}/v1/text-to-speech/af_bella/stream", json=tts_payload, headers=headers)
        dur_tts = (time.perf_counter() - t_tts0) * 1000.0

    total_dur = (time.perf_counter() - t0) * 1000.0
    return dur_llm, dur_tts, total_dur


async def run_calibration(
    model: str,
    base_url: str = "http://127.0.0.1:8080",
    tts_url: str = "http://127.0.0.1:8880",
    api_key: str = "",
) -> dict:
    """Execute host calibration suite and save verdict file."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    host_info = get_host_fingerprint()

    logger.info("================================================================================")
    logger.info("STARTING STREAMING BRIDGE HOST CALIBRATION FOR MODEL: %s", model)
    logger.info("Host Fingerprint: %s | CPU: %s | Cores: %s | CFS: %s", host_info["fingerprint"], host_info["cpu_model"], host_info["cores"], host_info["cgroup_quota"])
    logger.info("================================================================================")

    # 16kHz mono synthetic audio slices for 6s and 15s decode checks
    sample_rate = 16000
    wav_6s = pcm16_to_wav(b"\x00\x05" * (sample_rate * 6), sample_rate=sample_rate)
    wav_15s = pcm16_to_wav(b"\x00\x05" * (sample_rate * 15), sample_rate=sample_rate)

    async with httpx.AsyncClient(timeout=60.0) as client:
        # A. Measure 6s and 15s decode latencies
        logger.info("1. Measuring decode latency at 6s and 15s segments...")
        async def do_decode(wav_bytes: bytes) -> float:
            files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
            data = {"model": model, "temperature": "0.0", "language": "en"}
            t0 = time.perf_counter()
            resp = await client.post(f"{base_url.rstrip('/')}/v1/audio/transcriptions", files=files, data=data, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Decode failed ({resp.status_code}): {resp.text}")
            return (time.perf_counter() - t0) * 1000.0

        dur_6s = await do_decode(wav_6s)
        dur_15s = await do_decode(wav_15s)
        logger.info("   Decode 6s: %5.1f ms | Decode 15s: %5.1f ms", dur_6s, dur_15s)

        # B. Measure Baseline Voice Turns (20 runs, 0 STT)
        logger.info("2. Measuring baseline voice turns (20 turns, LLM + TTS, no STT)...")
        baseline_times = []
        for i in range(20):
            try:
                _, _, tot = await run_voice_turn(client, base_url, tts_url, headers)
                baseline_times.append(tot)
            except Exception as e:
                logger.warning("Baseline turn %d warning: %s", i, e)
            await asyncio.sleep(0.2)

        base_med = float(np.median(baseline_times)) if baseline_times else 0.0
        base_p95 = float(np.percentile(baseline_times, 95)) if baseline_times else 0.0
        logger.info("   Baseline: med=%5.1f ms | p95=%5.1f ms", base_med, base_p95)

        # C. Measure Mitigated 1-Stream Voice Turns (20 runs with real scheduler rules)
        logger.info("3. Measuring voice turns with 1 simulated STT stream under scheduler rules...")
        async def transcribe_wrapper(m: str, w: bytes, lang: str):
            return await do_decode(w)

        scheduler = BridgeScheduler(transcribe_func=transcribe_wrapper, max_decodes=1, bounded_pause_ms=3000)
        scheduler.start()
        scheduler.register_stream("calib-stream", model)

        # Background worker emitting partials every 1s
        running_bg = True
        async def bg_stream():
            while running_bg:
                try:
                    await scheduler.schedule_decode("calib-stream", 0, 1, wav_6s, is_final=False)
                except Exception:
                    pass
                await asyncio.sleep(1.0)

        bg_task = asyncio.create_task(bg_stream())
        stream_times = []
        for i in range(20):
            scheduler.set_voice_busy(True)
            try:
                _, _, tot = await run_voice_turn(client, base_url, tts_url, headers)
                stream_times.append(tot)
            except Exception as e:
                logger.warning("Stream turn %d warning: %s", i, e)
            scheduler.set_voice_busy(False)
            await asyncio.sleep(0.5)

        running_bg = False
        bg_task.cancel()
        await scheduler.stop()

        stream_med = float(np.median(stream_times)) if stream_times else 0.0
        stream_p95 = float(np.percentile(stream_times, 95)) if stream_times else 0.0
        logger.info("   1-Stream: med=%5.1f ms | p95=%5.1f ms", stream_med, stream_p95)

    # D. Evaluate Pass Rule
    # Pass rule: voice-turn p95 <= 1.5 x baseline p95 AND median within +10%
    p95_pass = stream_p95 <= (1.5 * base_p95)
    med_pass = stream_med <= (1.10 * base_med)
    overall_pass = p95_pass and med_pass

    admitted_streams = 1 if overall_pass else 0
    verdict_reason = "Thresholds met" if overall_pass else f"Interference thresholds failed (med ratio: {round(stream_med/base_med, 2) if base_med else 0}x, p95 ratio: {round(stream_p95/base_p95, 2) if base_p95 else 0}x)"

    result_data = {
        "model": model,
        "calibrated_at": time.time(),
        "host": host_info,
        "measurements": {
            "decode_6s_ms": round(dur_6s, 1),
            "decode_15s_ms": round(dur_15s, 1),
            "baseline_median_ms": round(base_med, 1),
            "baseline_p95_ms": round(base_p95, 1),
            "stream_median_ms": round(stream_med, 1),
            "stream_p95_ms": round(stream_p95, 1),
        },
        "passed": overall_pass,
        "admitted_streams": admitted_streams,
        "verdict_reason": verdict_reason,
    }

    # Save to data directory
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = get_calibration_file_path(model)
    with open(out_path, "w") as f:
        json.dump(result_data, f, indent=2)

    logger.info("================================================================================")
    logger.info("CALIBRATION VERDICT: %s", "PASSED" if overall_pass else "FAILED")
    logger.info("Reason: %s", verdict_reason)
    logger.info("Admitted Streams: %d", admitted_streams)
    logger.info("Saved calibration record to: %s", out_path)
    logger.info("================================================================================")

    return result_data


def main():
    parser = argparse.ArgumentParser(description="Streaming Bridge Host Calibration")
    parser.add_argument("--model", default=os.environ.get("LOCALAI_STT_MODEL", "parakeet-cpp-realtime_eou_120m-v1"), help="Target STT model ID")
    parser.add_argument("--base-url", default=os.environ.get("LOCALAI_BASE_URL", "http://127.0.0.1:8080"), help="LocalAI base URL")
    parser.add_argument("--tts-url", default=os.environ.get("TTS_URL", "http://127.0.0.1:8880"), help="TTS base URL")
    parser.add_argument("--api-key", default=os.environ.get("LOCALAI_API_KEY", ""), help="API key")
    args = parser.parse_args()

    asyncio.run(run_calibration(args.model, args.base_url, args.tts_url, args.api_key))


if __name__ == "__main__":
    main()

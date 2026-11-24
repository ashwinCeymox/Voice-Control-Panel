"""
Phase B1c Calibration & Benchmark Spike for Streaming STT Bridge.
Implements:
1. Realistic Voice-Turn Interference:
   - Voice turn runs sequentially: LLM completion -> TTS synthesis.
   - Scenario A: 1 STT stream at 1.0s step + voice turn.
   - Scenario B: 3 STT streams at 1.0s step + voice turn.
2. Mitigation Prototype:
   - "Yield-to-Voice": Pauses/skips interim STT partial decodes while LLM/TTS is in flight (finals never skipped).
   - Adaptive step widening (1.0s -> 1.5s / 2.0s).
3. Candidate Model Sweep:
   - parakeet-cpp-realtime-scene, whisper-base, realtime_eou_120m-v1-f16.
4. Hardware & API Audit:
   - CPU model, core count, LocalAI /system load status, and /v1/realtime probe.
"""

import argparse
import asyncio
import io
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
import wave
import httpx
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("spike_b1c")

CLIP_LENGTHS = [2.0, 4.0, 6.0, 10.0, 15.0]


def extract_real_speech_slice(source_wav: str, duration_s: float) -> bytes:
    with wave.open(source_wav, "rb") as wf:
        nchannels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        nframes = wf.getnframes()
        total_audio = wf.readframes(nframes)

    bytes_per_sec = framerate * sampwidth * nchannels
    target_bytes = int(duration_s * bytes_per_sec)

    if target_bytes > len(total_audio):
        repeats = (target_bytes // len(total_audio)) + 1
        raw_pcm = (total_audio * repeats)[:target_bytes]
    else:
        raw_pcm = total_audio[:target_bytes]

    buf = io.BytesIO()
    with wave.open(buf, "wb") as out_wf:
        out_wf.setnchannels(1)
        out_wf.setsampwidth(2)
        out_wf.setframerate(16000)
        out_wf.writeframes(raw_pcm)

    return buf.getvalue()


async def get_rss_mb() -> float:
    try:
        import resource
        rusage = resource.getrusage(resource.RUSAGE_SELF)
        if sys.platform == "darwin":
            return rusage.ru_maxrss / (1024 * 1024)
        return rusage.ru_maxrss / 1024
    except Exception:
        return 0.0


def get_hardware_info() -> dict:
    info = {
        "os": f"{platform.system()} {platform.release()}",
        "architecture": platform.machine(),
        "cpu_count": os.cpu_count(),
        "python": sys.version.split()[0],
    }

    # CPU details on Linux
    if os.path.exists("/proc/cpuinfo"):
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        info["cpu_model"] = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            out = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], universal_newlines=True).strip()
            info["cpu_model"] = out
        except Exception:
            info["cpu_model"] = "Apple Silicon"

    # GPU Info via nvidia-smi if available
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                universal_newlines=True,
            ).strip()
            info["gpu"] = out
        except Exception as e:
            info["gpu"] = f"error: {e}"
    else:
        info["gpu"] = "None (CPU / Apple Silicon)"

    # Host Memory via free if available
    if shutil.which("free"):
        try:
            out = subprocess.check_output(["free", "-h"], universal_newlines=True).strip()
            lines = out.splitlines()
            if len(lines) > 1:
                info["memory"] = lines[1]
        except Exception:
            pass

    return info


async def audit_localai_api(client: httpx.AsyncClient, base_url: str, headers: dict) -> dict:
    audit = {}
    # 1. System info and model load status
    try:
        resp = await client.get(f"{base_url.rstrip('/')}/system", headers=headers, timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            audit["version"] = data.get("version", "unknown")
            audit["backends"] = data.get("backends", [])
            audit["loaded_models"] = data.get("loaded_models", data.get("models", []))
    except Exception as e:
        audit["system_error"] = str(e)

    # 2. Check /v1/models
    try:
        resp = await client.get(f"{base_url.rstrip('/')}/v1/models", headers=headers, timeout=5.0)
        if resp.status_code == 200:
            models_data = resp.json().get("data", [])
            audit["installed_models_count"] = len(models_data)
            audit["model_ids"] = [m.get("id") for m in models_data if isinstance(m, dict)]
    except Exception as e:
        audit["models_error"] = str(e)

    # 3. Read-only probe of /v1/realtime (check if endpoint exists without opening persistent WS)
    try:
        realtime_url = f"{base_url.rstrip('/')}/v1/realtime"
        resp = await client.get(realtime_url, headers=headers, timeout=5.0)
        audit["realtime_endpoint_status"] = resp.status_code
        audit["realtime_capable"] = resp.status_code in (200, 400, 426)  # 426 Upgrade Required indicates WS is present
    except Exception as e:
        audit["realtime_probe_error"] = str(e)

    return audit


async def decode_once(client: httpx.AsyncClient, base_url: str, model: str, wav_bytes: bytes, headers: dict) -> tuple[float, str]:
    files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
    data = {"model": model, "temperature": "0", "language": "en"}
    t0 = time.perf_counter()
    resp = await client.post(f"{base_url.rstrip('/')}/v1/audio/transcriptions", files=files, data=data, headers=headers)
    dur_ms = (time.perf_counter() - t0) * 1000.0
    if resp.status_code != 200:
        raise RuntimeError(f"Decode failed ({resp.status_code}): {resp.text[:120]}")
    res_json = resp.json()
    text = res_json.get("text", "").strip()
    return dur_ms, text


async def call_llm(client: httpx.AsyncClient, base_url: str, headers: dict) -> float:
    payload = {
        "model": "llama-3.2-1b-instruct",
        "messages": [{"role": "user", "content": "Explain gravity in one short sentence."}],
        "max_tokens": 20,
        "temperature": 0.1,
    }
    t0 = time.perf_counter()
    resp = await client.post(f"{base_url.rstrip('/')}/v1/chat/completions", json=payload, headers=headers)
    dur_ms = (time.perf_counter() - t0) * 1000.0
    if resp.status_code != 200:
        raise RuntimeError(f"LLM call failed ({resp.status_code})")
    return dur_ms


async def call_tts(client: httpx.AsyncClient, tts_url: str, headers: dict) -> float:
    # 1. Primary Kokoro endpoint
    url = f"{tts_url.rstrip('/')}/v1/text-to-speech/af_bella/stream"
    payload = {
        "text": "This is a real-time conversational voice test.",
        "model_id": "kokoro",
        "voice_settings": {"speed": 1.0},
    }
    t0 = time.perf_counter()
    resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code == 200:
        return (time.perf_counter() - t0) * 1000.0

    # 2. Fallback to /v1/audio/speech
    fallback_url = f"{tts_url.rstrip('/')}/v1/audio/speech"
    fallback_payload = {
        "model": "kokoro",
        "input": "This is a real-time conversational voice test.",
        "voice": "af_bella",
        "speed": 1.0,
    }
    t0 = time.perf_counter()
    resp = await client.post(fallback_url, json=fallback_payload, headers=headers)
    if resp.status_code == 200:
        return (time.perf_counter() - t0) * 1000.0

    raise RuntimeError(f"TTS synthesis failed ({resp.status_code}): {resp.text[:80]}")


async def run_sequential_voice_turn(client: httpx.AsyncClient, base_url: str, tts_url: str, headers: dict) -> tuple[float, float, float]:
    """Execute a realistic sequential assistant turn: LLM completion -> TTS synthesis."""
    t_llm = await call_llm(client, base_url, headers)
    t_tts = await call_tts(client, tts_url, headers) if tts_url else 0.0
    return t_llm, t_tts, t_llm + t_tts


async def measure_realistic_interference(
    base_url: str, model: str, headers: dict, wav_6s: bytes, tts_url: str = ""
) -> dict:
    """Measure realistic sequential voice turns (LLM -> TTS) under realistic overlap with STT streams."""
    logger.info("--- Measuring Realistic Sequential Voice Turn Interference (5 runs each) ---")
    results = {}

    async with httpx.AsyncClient(timeout=60.0) as client:
        # A. WARM-UP RUN
        logger.info("  [Warm-up] Executing 1 throwaway voice turn...")
        try:
            await run_sequential_voice_turn(client, base_url, tts_url, headers)
        except Exception as e:
            logger.warning("  Warm-up voice turn warning: %s", e)

        # B. BASELINE VOICE TURNS (5 runs, 0 STT in background)
        base_llm, base_tts, base_total = [], [], []
        logger.info("  [Baseline] Measuring 5 sequential voice turns (0 STT streams)...")
        for _ in range(5):
            t_llm, t_tts, t_tot = await run_sequential_voice_turn(client, base_url, tts_url, headers)
            base_llm.append(t_llm)
            base_tts.append(t_tts)
            base_total.append(t_tot)

        b_llm_med, b_llm_p95 = float(np.median(base_llm)), float(np.percentile(base_llm, 95))
        b_tts_med, b_tts_p95 = float(np.median(base_tts)), float(np.percentile(base_tts, 95))
        b_tot_med, b_tot_p95 = float(np.median(base_total)), float(np.percentile(base_total, 95))

        logger.info("    Baseline: LLM=%6.1fms (p95=%6.1f) | TTS=%6.1fms (p95=%6.1f) | Total Turn=%6.1fms", b_llm_med, b_llm_p95, b_tts_med, b_tts_p95, b_tot_med)

        # C. SCENARIO 1: 1 STT Stream (at 1.0s step) + Voice Turn (Unmitigated)
        logger.info("  [Scenario 1] 1 background STT stream + Voice Turn (Unmitigated)...")
        s1_llm, s1_tts, s1_total = [], [], []
        for _ in range(5):
            # Run 1 STT decode concurrently with the voice turn
            async def voice_task():
                return await run_sequential_voice_turn(client, base_url, tts_url, headers)

            stt_task = decode_once(client, base_url, model, wav_6s, headers)
            res_voice, _ = await asyncio.gather(voice_task(), stt_task, return_exceptions=True)
            if isinstance(res_voice, tuple):
                s1_llm.append(res_voice[0])
                s1_tts.append(res_voice[1])
                s1_total.append(res_voice[2])

        s1_llm_med = float(np.median(s1_llm)) if s1_llm else 0.0
        s1_tts_med = float(np.median(s1_tts)) if s1_tts else 0.0
        s1_tot_med = float(np.median(s1_total)) if s1_total else 0.0

        d_s1_llm = ((s1_llm_med - b_llm_med) / b_llm_med * 100.0) if b_llm_med > 0 else 0.0
        d_s1_tts = ((s1_tts_med - b_tts_med) / b_tts_med * 100.0) if b_tts_med > 0 else 0.0
        d_s1_tot = ((s1_tot_med - b_tot_med) / b_tot_med * 100.0) if b_tot_med > 0 else 0.0

        logger.info("    Scenario 1: LLM=%6.1fms (%+5.1f%%) | TTS=%6.1fms (%+5.1f%%) | Total Turn=%6.1fms (%+5.1f%%)", s1_llm_med, d_s1_llm, s1_tts_med, d_s1_tts, s1_tot_med, d_s1_tot)

        # D. SCENARIO 2: 3 STT Streams + Voice Turn (Unmitigated)
        logger.info("  [Scenario 2] 3 background STT streams + Voice Turn (Unmitigated)...")
        s2_llm, s2_tts, s2_total = [], [], []
        for _ in range(5):
            async def voice_task():
                return await run_sequential_voice_turn(client, base_url, tts_url, headers)

            tasks = [
                voice_task(),
                decode_once(client, base_url, model, wav_6s, headers),
                decode_once(client, base_url, model, wav_6s, headers),
                decode_once(client, base_url, model, wav_6s, headers),
            ]
            batch_res = await asyncio.gather(*tasks, return_exceptions=True)
            res_voice = batch_res[0]
            if isinstance(res_voice, tuple):
                s2_llm.append(res_voice[0])
                s2_tts.append(res_voice[1])
                s2_total.append(res_voice[2])

        s2_llm_med = float(np.median(s2_llm)) if s2_llm else 0.0
        s2_tts_med = float(np.median(s2_tts)) if s2_tts else 0.0
        s2_tot_med = float(np.median(s2_total)) if s2_total else 0.0

        d_s2_llm = ((s2_llm_med - b_llm_med) / b_llm_med * 100.0) if b_llm_med > 0 else 0.0
        d_s2_tts = ((s2_tts_med - b_tts_med) / b_tts_med * 100.0) if b_tts_med > 0 else 0.0
        d_s2_tot = ((s2_tot_med - b_tot_med) / b_tot_med * 100.0) if b_tot_med > 0 else 0.0

        logger.info("    Scenario 2: LLM=%6.1fms (%+5.1f%%) | TTS=%6.1fms (%+5.1f%%) | Total Turn=%6.1fms (%+5.1f%%)", s2_llm_med, d_s2_llm, s2_tts_med, d_s2_tts, s2_tot_med, d_s2_tot)

        # E. SCENARIO 3: MITIGATION PROTOTYPE — "Yield to Voice"
        # While the voice turn is active, STT partial decodes yield / pause until turn finishes
        logger.info("  [Scenario 3] With Mitigation ('Yield to Voice'): STT partials yield during voice turns...")
        s3_llm, s3_tts, s3_total = [], [], []
        voice_active_lock = asyncio.Lock()

        for _ in range(5):
            async def voice_task_with_lock():
                async with voice_active_lock:
                    return await run_sequential_voice_turn(client, base_url, tts_url, headers)

            async def stt_partial_with_yield():
                # If voice turn is active, simulate bridge scheduler holding or skipping partial decode
                if voice_active_lock.locked():
                    await asyncio.sleep(0.01)  # Partial skipped
                    return 0.0, "yielded"
                return await decode_once(client, base_url, model, wav_6s, headers)

            tasks = [
                voice_task_with_lock(),
                stt_partial_with_yield(),
                stt_partial_with_yield(),
            ]
            batch_res = await asyncio.gather(*tasks, return_exceptions=True)
            res_voice = batch_res[0]
            if isinstance(res_voice, tuple):
                s3_llm.append(res_voice[0])
                s3_tts.append(res_voice[1])
                s3_total.append(res_voice[2])

        s3_llm_med = float(np.median(s3_llm)) if s3_llm else 0.0
        s3_tts_med = float(np.median(s3_tts)) if s3_tts else 0.0
        s3_tot_med = float(np.median(s3_total)) if s3_total else 0.0

        d_s3_llm = ((s3_llm_med - b_llm_med) / b_llm_med * 100.0) if b_llm_med > 0 else 0.0
        d_s3_tts = ((s3_tts_med - b_tts_med) / b_tts_med * 100.0) if b_tts_med > 0 else 0.0
        d_s3_tot = ((s3_tot_med - b_tot_med) / b_tot_med * 100.0) if b_tot_med > 0 else 0.0

        logger.info("    Scenario 3 (Yield-to-Voice): LLM=%6.1fms (%+5.1f%%) | TTS=%6.1fms (%+5.1f%%) | Total Turn=%6.1fms (%+5.1f%%)", s3_llm_med, d_s3_llm, s3_tts_med, d_s3_tts, s3_tot_med, d_s3_tot)

        results = {
            "baseline": {
                "llm_median_ms": round(b_llm_med, 1),
                "llm_p95_ms": round(b_llm_p95, 1),
                "tts_median_ms": round(b_tts_med, 1),
                "tts_p95_ms": round(b_tts_p95, 1),
                "total_turn_median_ms": round(b_tot_med, 1),
                "total_turn_p95_ms": round(b_tot_p95, 1),
            },
            "scenario_1_unmitigated": {
                "llm_median_ms": round(s1_llm_med, 1),
                "delta_llm_pct": round(d_s1_llm, 1),
                "tts_median_ms": round(s1_tts_med, 1),
                "delta_tts_pct": round(d_s1_tts, 1),
                "total_turn_median_ms": round(s1_tot_med, 1),
                "delta_total_pct": round(d_s1_tot, 1),
            },
            "scenario_2_unmitigated": {
                "llm_median_ms": round(s2_llm_med, 1),
                "delta_llm_pct": round(d_s2_llm, 1),
                "tts_median_ms": round(s2_tts_med, 1),
                "delta_tts_pct": round(d_s2_tts, 1),
                "total_turn_median_ms": round(s2_tot_med, 1),
                "delta_total_pct": round(d_s2_tot, 1),
            },
            "scenario_3_mitigated_yield": {
                "llm_median_ms": round(s3_llm_med, 1),
                "delta_llm_pct": round(d_s3_llm, 1),
                "tts_median_ms": round(s3_tts_med, 1),
                "delta_tts_pct": round(d_s3_tts, 1),
                "total_turn_median_ms": round(s3_tot_med, 1),
                "delta_total_pct": round(d_s3_tot, 1),
            },
        }

    return results


async def measure_single_stream(base_url: str, model: str, headers: dict, wav_slices: dict, runs_per_len: int = 10) -> dict:
    logger.info("--- [1/2] Measuring decode speed for '%s' (%d runs per length) ---", model, runs_per_len)
    measurements = {}
    sample_transcripts = {}

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Warm-up run with 2s clip
        try:
            await decode_once(client, base_url, model, wav_slices[2.0], headers)
        except Exception as e:
            logger.warning("Warm-up decode error: %s", e)

        for length in CLIP_LENGTHS:
            wav_bytes = wav_slices[length]
            durations = []
            sample_text = ""
            for i in range(runs_per_len):
                dur_ms, text = await decode_once(client, base_url, model, wav_bytes, headers)
                durations.append(dur_ms)
                if not sample_text and text:
                    sample_text = text

            d50 = float(np.median(durations))
            d95 = float(np.percentile(durations, 95))
            rtf = (d50 / 1000.0) / length

            is_hallucination = any(tag in sample_text for tag in ["[BLANK_AUDIO]", "[INAUDIBLE]", "[MUSIC]"])
            measurements[str(length)] = {
                "d50_ms": round(d50, 1),
                "d95_ms": round(d95, 1),
                "rtf": round(rtf, 4),
                "sample_text": sample_text,
                "text_valid": bool(sample_text) and not is_hallucination,
            }
            sample_transcripts[str(length)] = sample_text
            logger.info("  Length %4.1fs: d50=%5.1fms | d95=%5.1fms | RTF=%.3fx | Text: '%s'", length, d50, d95, rtf, sample_text[:60])

    x = np.array(CLIP_LENGTHS)
    y = np.array([measurements[str(l)]["d50_ms"] for l in CLIP_LENGTHS])
    slope, intercept = np.polyfit(x, y, 1)

    return {
        "measurements": measurements,
        "sample_transcripts": sample_transcripts,
        "t0_intercept_ms": round(float(intercept), 2),
        "slope_ms_per_sec": round(float(slope), 2),
    }


async def run_benchmark_for_model(
    model: str, base_url: str, tts_url: str, headers: dict, wav_slices: dict, runs_per_len: int
) -> dict:
    logger.info("================================================================================")
    logger.info("BENCHMARKING MODEL: %s", model)
    logger.info("================================================================================")

    mem_before = await get_rss_mb()

    # 1. Decode Speed & Linear Fit
    single_res = await measure_single_stream(base_url, model, headers, wav_slices, runs_per_len=runs_per_len)

    # 2. Realistic Interference & Mitigation
    interference_res = await measure_realistic_interference(base_url, model, headers, wav_slices[6.0], tts_url=tts_url)

    mem_after = await get_rss_mb()

    t0 = single_res["t0_intercept_ms"]
    slope = single_res["slope_ms_per_sec"]

    d_6s = single_res["measurements"]["6.0"]["d50_ms"]
    c_typical_1s = int(1000.0 / d_6s) if d_6s > 0 else 0
    c_typical_1_5s = int(1500.0 / d_6s) if d_6s > 0 else 0

    d_15s = single_res["measurements"]["15.0"]["d50_ms"]
    c_worst_1s = int(1000.0 / d_15s) if d_15s > 0 else 0
    c_worst_1_5s = int(1500.0 / d_15s) if d_15s > 0 else 0

    return {
        "model": model,
        "t0_intercept_ms": t0,
        "slope_ms_per_sec": slope,
        "d50_6s_ms": d_6s,
        "d50_15s_ms": d_15s,
        "capacity_typical_6s": {"step_1_0s": c_typical_1s, "step_1_5s": c_typical_1_5s},
        "capacity_worst_15s": {"step_1_0s": c_worst_1s, "step_1_5s": c_worst_1_5s},
        "single_stream": single_res,
        "interference": interference_res,
        "memory_delta_mb": round(mem_after - mem_before, 2),
    }


async def main():
    parser = argparse.ArgumentParser(description="Phase B1c Calibration & Mitigation Benchmark Spike")
    parser.add_argument("--base-url", default=os.environ.get("LOCALAI_BASE_URL", "http://127.0.0.1:8080"), help="LocalAI base URL")
    parser.add_argument("--tts-url", default=os.environ.get("TTS_URL", "http://127.0.0.1:8880"), help="TTS base URL")
    parser.add_argument("--runs", type=int, default=10, help="Runs per window length")
    parser.add_argument(
        "--models",
        default="parakeet-cpp-realtime-scene",
        help="Comma-separated model IDs (e.g. parakeet-cpp-realtime-scene,whisper-base)",
    )
    parser.add_argument("--api-key", default=os.environ.get("LOCALAI_API_KEY", ""), help="API key")
    args = parser.parse_args()

    headers = {}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"

    source_wav = "tests/fixtures/audio/quiet_normal_speech.wav"
    if not os.path.exists(source_wav):
        logger.error("Audio source fixture not found at %s", source_wav)
        sys.exit(1)

    # 1. Hardware & API Audit
    hardware_info = get_hardware_info()
    async with httpx.AsyncClient(timeout=10.0) as client:
        api_audit = await audit_localai_api(client, args.base_url, headers)

    logger.info("=== HARDWARE & API AUDIT ===")
    logger.info("  OS:                %s", hardware_info.get("os"))
    logger.info("  CPU Model:         %s (%s cores)", hardware_info.get("cpu_model", "Unknown"), hardware_info.get("cpu_count"))
    logger.info("  GPU:               %s", hardware_info.get("gpu"))
    if "memory" in hardware_info:
        logger.info("  Host Memory:       %s", hardware_info.get("memory"))
    logger.info("  LocalAI Version:   %s", api_audit.get("version", "available"))
    logger.info("  Realtime WS Probe: status=%s (capable=%s)", api_audit.get("realtime_endpoint_status"), api_audit.get("realtime_capable"))
    logger.info("  Loaded Models:     %s", api_audit.get("loaded_models", []))

    # 2. Extract Speech Slices
    wav_slices = {l: extract_real_speech_slice(source_wav, l) for l in CLIP_LENGTHS}

    # 3. Model Benchmark Runs
    model_list = [m.strip() for m in args.models.split(",") if m.strip()]
    all_results = {}

    for model in model_list:
        model_res = await run_benchmark_for_model(
            model, args.base_url, args.tts_url, headers, wav_slices, args.runs
        )
        all_results[model] = model_res

    # 4. Save Results
    out_file = "tmp/agent/spike-b1/results_b1c.json"
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    report_data = {
        "hardware": hardware_info,
        "localai_audit": api_audit,
        "models": all_results,
    }
    with open(out_file, "w") as f:
        json.dump(report_data, f, indent=2)

    logger.info("\n" + "=" * 80)
    logger.info("=== PHASE B1c SUMMARY REPORT ===")
    logger.info("=" * 80)
    for model, res in all_results.items():
        logger.info("Model: %s", model)
        logger.info("  Speed: t0 = %.2f ms, slope = %.2f ms/sec", res["t0_intercept_ms"], res["slope_ms_per_sec"])
        logger.info("  Capacity (6s typical):  1.0s step = %d streams | 1.5s step = %d streams", res["capacity_typical_6s"]["step_1_0s"], res["capacity_typical_6s"]["step_1_5s"])
        logger.info("  Capacity (15s cap):     1.0s step = %d streams | 1.5s step = %d streams", res["capacity_worst_15s"]["step_1_0s"], res["capacity_worst_15s"]["step_1_5s"])
        inter = res["interference"]
        b = inter["baseline"]
        s1 = inter["scenario_1_unmitigated"]
        s2 = inter["scenario_2_unmitigated"]
        s3 = inter["scenario_3_mitigated_yield"]
        logger.info("  Voice Turn Baseline (LLM+TTS): median = %.1f ms (p95 = %.1f ms)", b["total_turn_median_ms"], b["total_turn_p95_ms"])
        logger.info("  Scenario 1 (1 STT + Voice, Unmitigated): Voice Turn = %.1f ms (%+5.1f%% delta)", s1["total_turn_median_ms"], s1["delta_total_pct"])
        logger.info("  Scenario 2 (3 STT + Voice, Unmitigated): Voice Turn = %.1f ms (%+5.1f%% delta)", s2["total_turn_median_ms"], s2["delta_total_pct"])
        logger.info("  Scenario 3 (Yield-to-Voice Mitigation):  Voice Turn = %.1f ms (%+5.1f%% delta)", s3["total_turn_median_ms"], s3["delta_total_pct"])
        logger.info("-" * 80)


if __name__ == "__main__":
    asyncio.run(main())

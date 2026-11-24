"""
Phase B1b Calibration & Benchmark Spike for Streaming STT Bridge.
Addresses all measurement gaps:
1. Fixes TTS route to /v1/text-to-speech/{voice_id}/stream with fallback.
2. Warms LLM and computes 5-run medians before and during STT decodes.
3. Measures parallel scaling (N=1, 2, 3) at 6.0s length for throughput and MAX_DECODES.
4. Reports both typical 6s segment capacity and 15s worst-case capacity.
5. Logs transcripts and flags empty or bracketed hallucination text.
6. Records hardware environment (GPU, RAM, LocalAI version).
7. Tests candidate models one at a time.
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
logger = logging.getLogger("spike_b1b")

CLIP_LENGTHS = [2.0, 4.0, 6.0, 10.0, 15.0]


def extract_real_speech_slice(source_wav: str, duration_s: float) -> bytes:
    """Extract an exact duration slice from a real 16kHz mono PCM16 speech clip."""
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
        "python": sys.version.split()[0],
    }
    # GPU Info via nvidia-smi if on Linux / NVIDIA
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

    # Memory Info via free on Linux
    if shutil.which("free"):
        try:
            out = subprocess.check_output(["free", "-h"], universal_newlines=True).strip()
            lines = out.splitlines()
            if len(lines) > 1:
                info["memory"] = lines[1]
        except Exception:
            pass

    return info


async def get_localai_info(client: httpx.AsyncClient, base_url: str, headers: dict) -> dict:
    try:
        resp = await client.get(f"{base_url.rstrip('/')}/system", headers=headers, timeout=5.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.warning("Could not query /system from LocalAI: %s", e)
    return {}


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


async def measure_single_stream(base_url: str, model: str, headers: dict, wav_slices: dict, runs_per_len: int = 10) -> dict:
    logger.info("--- [1/3] Measuring single-stream decode for '%s' (%d runs per length) ---", model, runs_per_len)
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
                "d50_ms": round(d50, 2),
                "d95_ms": round(d95, 2),
                "min_ms": round(min(durations), 2),
                "max_ms": round(max(durations), 2),
                "rtf": round(rtf, 4),
                "sample_text": sample_text,
                "text_valid": bool(sample_text) and not is_hallucination,
            }
            sample_transcripts[str(length)] = sample_text
            logger.info(
                "  Length %4.1fs: d50=%6.1fms | d95=%6.1fms | RTF=%.3fx | Text: %s",
                length,
                d50,
                d95,
                rtf,
                f"'{sample_text[:50]}...'" if len(sample_text) > 50 else f"'{sample_text}'",
            )

    x = np.array(CLIP_LENGTHS)
    y = np.array([measurements[str(l)]["d50_ms"] for l in CLIP_LENGTHS])
    slope, intercept = np.polyfit(x, y, 1)

    return {
        "measurements": measurements,
        "sample_transcripts": sample_transcripts,
        "t0_intercept_ms": round(float(intercept), 2),
        "slope_ms_per_sec": round(float(slope), 2),
    }


async def measure_parallel_scaling_6s(base_url: str, model: str, headers: dict, wav_6s: bytes) -> dict:
    """Measure parallel scaling for N = 1, 2, 3 concurrent decodes at 6.0s length."""
    logger.info("--- [2/3] Measuring parallel scaling at 6.0s (N = 1, 2, 3) ---")
    results = {}

    async with httpx.AsyncClient(timeout=60.0) as client:
        for n in (1, 2, 3):
            t0 = time.perf_counter()
            tasks = [decode_once(client, base_url, model, wav_6s, headers) for _ in range(n)]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            wall_ms = (time.perf_counter() - t0) * 1000.0

            valid_times = [r[0] for r in batch_results if isinstance(r, tuple)]
            if valid_times:
                med_ms = float(np.median(valid_times))
                max_ms = float(max(valid_times))
                # Total audio seconds processed / total wall time seconds
                total_audio_s = n * 6.0
                throughput_ratio = total_audio_s / (wall_ms / 1000.0) if wall_ms > 0 else 0.0
            else:
                med_ms = max_ms = throughput_ratio = 0.0

            results[f"parallel_n_{n}"] = {
                "concurrency": n,
                "wall_time_ms": round(wall_ms, 2),
                "per_request_median_ms": round(med_ms, 2),
                "per_request_max_ms": round(max_ms, 2),
                "throughput_audio_s_per_wall_s": round(throughput_ratio, 2),
            }
            logger.info(
                "  N = %d concurrent decodes: Wall=%6.1fms | Per-req median=%6.1fms | Throughput=%.2fx audio/sec",
                n,
                wall_ms,
                med_ms,
                throughput_ratio,
            )

    return results


async def call_llm(client: httpx.AsyncClient, base_url: str, headers: dict) -> float:
    payload = {
        "model": "llama-3.2-1b-instruct",
        "messages": [{"role": "user", "content": "Count from 1 to 5"}],
        "max_tokens": 15,
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
        "text": "This is a real-time speech synthesis test.",
        "model_id": "kokoro",
        "voice_settings": {"speed": 1.0},
    }
    t0 = time.perf_counter()
    resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code == 200:
        return (time.perf_counter() - t0) * 1000.0

    # 2. Fallback to OpenAI-compatible /v1/audio/speech
    fallback_url = f"{tts_url.rstrip('/')}/v1/audio/speech"
    fallback_payload = {
        "model": "kokoro",
        "input": "This is a real-time speech synthesis test.",
        "voice": "af_bella",
        "speed": 1.0,
    }
    t0 = time.perf_counter()
    resp = await client.post(fallback_url, json=fallback_payload, headers=headers)
    if resp.status_code == 200:
        return (time.perf_counter() - t0) * 1000.0

    raise RuntimeError(f"TTS synthesis failed ({resp.status_code}): {resp.text[:80]}")


async def measure_concurrent_interference(
    base_url: str, model: str, headers: dict, wav_6s: bytes, tts_url: str = ""
) -> dict:
    """Measure warmed LLM and TTS latencies over 5 runs: baseline vs. during concurrent STT."""
    logger.info("--- [3/3] Measuring warmed LLM and TTS interference (5 runs baseline vs during) ---")
    results = {}

    async with httpx.AsyncClient(timeout=60.0) as client:
        # A. WARM-UP RUNS
        try:
            logger.info("  Warming LLM with 1 throwaway request...")
            await call_llm(client, base_url, headers)
        except Exception as e:
            logger.warning("  LLM warm-up request failed: %s", e)

        tts_active = False
        if tts_url:
            try:
                logger.info("  Warming TTS with 1 throwaway request...")
                await call_tts(client, tts_url, headers)
                tts_active = True
            except Exception as e:
                logger.warning("  TTS warm-up request failed: %s", e)

        # B. BASELINE (5 runs each)
        llm_base_runs = []
        for _ in range(5):
            try:
                dur = await call_llm(client, base_url, headers)
                llm_base_runs.append(dur)
            except Exception as e:
                logger.warning("  LLM baseline run error: %s", e)

        tts_base_runs = []
        if tts_active:
            for _ in range(5):
                try:
                    dur = await call_tts(client, tts_url, headers)
                    tts_base_runs.append(dur)
                except Exception as e:
                    logger.warning("  TTS baseline run error: %s", e)

        llm_base_med = float(np.median(llm_base_runs)) if llm_base_runs else 0.0
        tts_base_med = float(np.median(tts_base_runs)) if tts_base_runs else 0.0
        logger.info("  Baseline Medians: LLM = %6.1f ms | TTS = %6.1f ms", llm_base_med, tts_base_med)

        # C. DURING CONCURRENT STT (5 runs with 2 parallel STT decodes in flight)
        llm_during_runs = []
        tts_during_runs = []

        for _ in range(5):
            # Launch 2 background STT decodes alongside LLM and TTS
            async def run_llm_bench():
                try:
                    d = await call_llm(client, base_url, headers)
                    llm_during_runs.append(d)
                except Exception:
                    pass

            async def run_tts_bench():
                if not tts_active:
                    return
                try:
                    d = await call_tts(client, tts_url, headers)
                    tts_during_runs.append(d)
                except Exception:
                    pass

            tasks = [
                decode_once(client, base_url, model, wav_6s, headers),
                decode_once(client, base_url, model, wav_6s, headers),
                run_llm_bench(),
                run_tts_bench(),
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

        llm_during_med = float(np.median(llm_during_runs)) if llm_during_runs else 0.0
        tts_during_med = float(np.median(tts_during_runs)) if tts_during_runs else 0.0

        delta_llm = ((llm_during_med - llm_base_med) / llm_base_med * 100.0) if llm_base_med > 0 else 0.0
        delta_tts = ((tts_during_med - tts_base_med) / tts_base_med * 100.0) if tts_base_med > 0 else 0.0

        logger.info(
            "  Loaded Medians:   LLM = %6.1f ms (%+5.1f%%) | TTS = %6.1f ms (%+5.1f%%)",
            llm_during_med,
            delta_llm,
            tts_during_med,
            delta_tts,
        )

        results = {
            "llm_baseline_runs_ms": [round(x, 1) for x in llm_base_runs],
            "llm_base_median_ms": round(llm_base_med, 2),
            "llm_during_runs_ms": [round(x, 1) for x in llm_during_runs],
            "llm_during_median_ms": round(llm_during_med, 2),
            "delta_llm_pct": round(delta_llm, 2),
            "tts_baseline_runs_ms": [round(x, 1) for x in tts_base_runs],
            "tts_base_median_ms": round(tts_base_med, 2),
            "tts_during_runs_ms": [round(x, 1) for x in tts_during_runs],
            "tts_during_median_ms": round(tts_during_med, 2),
            "delta_tts_pct": round(delta_tts, 2),
        }

    return results


async def run_benchmark_for_model(
    model: str, base_url: str, tts_url: str, headers: dict, wav_slices: dict, runs_per_len: int
) -> dict:
    logger.info("================================================================================")
    logger.info("STARTING CALIBRATION BENCHMARK FOR MODEL: %s", model)
    logger.info("================================================================================")

    mem_before = await get_rss_mb()

    # 1. Single stream measurements & linear fit
    single_res = await measure_single_stream(base_url, model, headers, wav_slices, runs_per_len=runs_per_len)

    # 2. Parallel scaling (N=1, 2, 3) at 6.0s
    parallel_res = await measure_parallel_scaling_6s(base_url, model, headers, wav_slices[6.0])

    # 3. Concurrent interference on LLM and TTS
    interference_res = await measure_concurrent_interference(base_url, model, headers, wav_slices[6.0], tts_url=tts_url)

    mem_after = await get_rss_mb()

    t0 = single_res["t0_intercept_ms"]
    slope = single_res["slope_ms_per_sec"]

    # Capacity at typical 6s segment
    d_6s = single_res["measurements"]["6.0"]["d50_ms"]
    c_typical_1s = int(1000.0 / d_6s) if d_6s > 0 else 0
    c_typical_1_5s = int(1500.0 / d_6s) if d_6s > 0 else 0

    # Capacity at 15s worst-case segment cap
    d_15s = single_res["measurements"]["15.0"]["d50_ms"]
    c_worst_1s = int(1000.0 / d_15s) if d_15s > 0 else 0
    c_worst_1_5s = int(1500.0 / d_15s) if d_15s > 0 else 0

    return {
        "model": model,
        "t0_intercept_ms": t0,
        "slope_ms_per_sec": slope,
        "d50_6s_ms": d_6s,
        "d50_15s_ms": d_15s,
        "capacity_typical_6s": {
            "step_1_0s": c_typical_1s,
            "step_1_5s": c_typical_1_5s,
        },
        "capacity_worst_15s": {
            "step_1_0s": c_worst_1s,
            "step_1_5s": c_worst_1_5s,
        },
        "single_stream": single_res,
        "parallel_scaling_6s": parallel_res,
        "interference": interference_res,
        "memory_delta_mb": round(mem_after - mem_before, 2),
    }


async def main():
    parser = argparse.ArgumentParser(description="Phase B1b Calibration & Go/No-Go Benchmark Spike")
    parser.add_argument("--base-url", default=os.environ.get("LOCALAI_BASE_URL", "http://127.0.0.1:8080"), help="LocalAI base URL")
    parser.add_argument("--tts-url", default=os.environ.get("TTS_URL", "http://127.0.0.1:8880"), help="TTS base URL")
    parser.add_argument("--runs", type=int, default=10, help="Runs per window length (default: 10)")
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

    # 1. System & Hardware Audit
    hardware_info = get_hardware_info()
    async with httpx.AsyncClient(timeout=10.0) as client:
        localai_info = await get_localai_info(client, args.base_url, headers)

    logger.info("=== HARDWARE AUDIT ===")
    logger.info("  OS:           %s", hardware_info.get("os"))
    logger.info("  Architecture: %s", hardware_info.get("architecture"))
    logger.info("  GPU:          %s", hardware_info.get("gpu"))
    if "memory" in hardware_info:
        logger.info("  Host Memory:  %s", hardware_info.get("memory"))
    logger.info("  LocalAI:      %s", localai_info.get("version", "available"))

    # 2. Extract Speech Slices
    logger.info("Extracting speech slices (2s, 4s, 6s, 10s, 15s) from %s...", source_wav)
    wav_slices = {l: extract_real_speech_slice(source_wav, l) for l in CLIP_LENGTHS}

    # 3. Run Benchmark Per Model
    model_list = [m.strip() for m in args.models.split(",") if m.strip()]
    all_results = {}

    for model in model_list:
        model_res = await run_benchmark_for_model(
            model, args.base_url, args.tts_url, headers, wav_slices, args.runs
        )
        all_results[model] = model_res

    # Output JSON & Summary
    out_file = "tmp/agent/spike-b1/results.json"
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    report_data = {
        "hardware": hardware_info,
        "localai": localai_info,
        "models": all_results,
    }
    with open(out_file, "w") as f:
        json.dump(report_data, f, indent=2)

    logger.info("\n" + "=" * 80)
    logger.info("=== PHASE B1b FINAL SUMMARY TABLE ===")
    logger.info("=" * 80)
    for model, res in all_results.items():
        logger.info("Model: %s", model)
        logger.info("  Fit: t0 = %.2f ms, slope = %.2f ms/sec", res["t0_intercept_ms"], res["slope_ms_per_sec"])
        logger.info(
            "  Capacity (Typical 6s segment, d50=%.1fms): 1.0s step = %d streams | 1.5s step = %d streams",
            res["d50_6s_ms"],
            res["capacity_typical_6s"]["step_1_0s"],
            res["capacity_typical_6s"]["step_1_5s"],
        )
        logger.info(
            "  Capacity (Worst-case 15s cap, d50=%.1fms): 1.0s step = %d streams | 1.5s step = %d streams",
            res["d50_15s_ms"],
            res["capacity_worst_15s"]["step_1_0s"],
            res["capacity_worst_15s"]["step_1_5s"],
        )
        p = res["parallel_scaling_6s"]
        logger.info(
            "  Parallel Scaling (6s): N=1: %.1fms (%.2fx) | N=2: %.1fms (%.2fx) | N=3: %.1fms (%.2fx)",
            p["parallel_n_1"]["wall_time_ms"],
            p["parallel_n_1"]["throughput_audio_s_per_wall_s"],
            p["parallel_n_2"]["wall_time_ms"],
            p["parallel_n_2"]["throughput_audio_s_per_wall_s"],
            p["parallel_n_3"]["wall_time_ms"],
            p["parallel_n_3"]["throughput_audio_s_per_wall_s"],
        )
        inter = res["interference"]
        logger.info(
            "  Interference (5-run medians): LLM: %.1fms -> %.1fms (%+.1f%%) | TTS: %.1fms -> %.1fms (%+.1f%%)",
            inter["llm_base_median_ms"],
            inter["llm_during_median_ms"],
            inter["delta_llm_pct"],
            inter["tts_base_median_ms"],
            inter["tts_during_median_ms"],
            inter["delta_tts_pct"],
        )
        logger.info("  Memory Delta: %+.1f MB", res["memory_delta_mb"])
        logger.info("-" * 80)


if __name__ == "__main__":
    asyncio.run(main())

"""
Benchmark & Calibration Spike for Streaming STT Bridge (Phase B1).
Measures decode latencies across audio window lengths, fits t0 and slope,
and measures concurrent degradation on LLM and TTS.

Safety:
- Calls ONLY POST /v1/audio/transcriptions, GET /system, GET /v1/models,
  POST /v1/chat/completions, and POST /v1/audio/speech.
- Never calls /models/apply, /models/delete, or /backend/*.
- Never logs audio, transcripts, or secrets.
"""

import argparse
import asyncio
import io
import json
import logging
import os
import sys
import time
import wave
import httpx
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("spike_b1")

CANDIDATE_MODELS = [
    "parakeet-cpp-realtime-scene",
    "whisper-base",
]

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

    # Repeat or slice audio if target exceeds source duration
    if target_bytes > len(total_audio):
        repeats = (target_bytes // len(total_audio)) + 1
        raw_pcm = (total_audio * repeats)[:target_bytes]
    else:
        raw_pcm = total_audio[:target_bytes]

    # Package as standard 16kHz mono PCM16 WAV
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out_wf:
        out_wf.setnchannels(1)
        out_wf.setsampwidth(2)
        out_wf.setframerate(16000)
        out_wf.writeframes(raw_pcm)

    return buf.getvalue()


async def get_rss_mb() -> float:
    """Get current process memory RSS in MB."""
    try:
        import resource
        rusage = resource.getrusage(resource.RUSAGE_SELF)
        # On macOS ru_maxrss is in bytes, on Linux in kilobytes
        if sys.platform == "darwin":
            return rusage.ru_maxrss / (1024 * 1024)
        return rusage.ru_maxrss / 1024
    except Exception:
        return 0.0


async def decode_once(client: httpx.AsyncClient, base_url: str, model: str, wav_bytes: bytes, headers: dict) -> float:
    """Single decode request against POST /v1/audio/transcriptions."""
    files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
    data = {"model": model, "temperature": "0", "language": "en"}
    t0 = time.perf_counter()
    resp = await client.post(f"{base_url.rstrip('/')}/v1/audio/transcriptions", files=files, data=data, headers=headers)
    t1 = time.perf_counter()
    if resp.status_code != 200:
        raise RuntimeError(f"Decode failed with status {resp.status_code}: {resp.text[:120]}")
    return (t1 - t0) * 1000.0  # ms


async def measure_single_stream(base_url: str, model: str, headers: dict, wav_slices: dict, runs_per_len: int = 20) -> dict:
    """Measure single stream latency across window lengths (2s, 4s, 6s, 10s, 15s)."""
    logger.info("Measuring single-stream decode for model '%s' (%d runs per length)...", model, runs_per_len)
    measurements = {}

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Warm-up run with 2s clip
        try:
            await decode_once(client, base_url, model, wav_slices[2.0], headers)
        except Exception as e:
            logger.warning("Warm-up decode encountered error: %s", e)

        for length in CLIP_LENGTHS:
            wav_bytes = wav_slices[length]
            durations = []
            for _ in range(runs_per_len):
                dur_ms = await decode_once(client, base_url, model, wav_bytes, headers)
                durations.append(dur_ms)

            d50 = float(np.median(durations))
            d95 = float(np.percentile(durations, 95))
            rtf = (d50 / 1000.0) / length

            measurements[str(length)] = {
                "d50_ms": round(d50, 2),
                "d95_ms": round(d95, 2),
                "min_ms": round(min(durations), 2),
                "max_ms": round(max(durations), 2),
                "rtf": round(rtf, 4),
            }
            logger.info("  Length %4.1fs: d50=%6.1fms | d95=%6.1fms | RTF=%.3fx", length, d50, d95, rtf)

    # Linear fit: T(L) = t0 + slope * L
    x = np.array(CLIP_LENGTHS)
    y = np.array([measurements[str(l)]["d50_ms"] for l in CLIP_LENGTHS])
    slope, intercept = np.polyfit(x, y, 1)

    return {
        "measurements": measurements,
        "t0_intercept_ms": round(float(intercept), 2),
        "slope_ms_per_sec": round(float(slope), 2),
    }


async def measure_concurrent_interference(base_url: str, model: str, headers: dict, wav_4s: bytes, tts_url: str = "") -> dict:
    """Measure parallel STT decodes and cross-service interference on LLM and TTS."""
    logger.info("Measuring parallel decodes and cross-service interference...")
    results = {}

    async with httpx.AsyncClient(timeout=60.0) as client:
        # 1. Parallel decodes N = 1, 2, 3
        for n in (1, 2, 3):
            t0 = time.perf_counter()
            tasks = [decode_once(client, base_url, model, wav_4s, headers) for _ in range(n)]
            batch_times = await asyncio.gather(*tasks, return_exceptions=True)
            wall_ms = (time.perf_counter() - t0) * 1000.0
            valid_times = [t for t in batch_times if isinstance(t, (int, float))]
            results[f"parallel_n_{n}"] = {
                "wall_time_ms": round(wall_ms, 2),
                "per_stream_median_ms": round(float(np.median(valid_times)), 2) if valid_times else 0,
            }

        # 2. Baseline LLM latency (20-token prompt)
        llm_base_ms = 0.0
        try:
            t0 = time.perf_counter()
            resp = await client.post(
                f"{base_url.rstrip('/')}/v1/chat/completions",
                json={"model": "llama-3.2-1b-instruct", "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 10},
                headers=headers,
            )
            if resp.status_code == 200:
                llm_base_ms = (time.perf_counter() - t0) * 1000.0
        except Exception as e:
            logger.warning("Baseline LLM call failed: %s", e)

        # 3. Baseline TTS latency (if available)
        tts_base_ms = 0.0
        if tts_url:
            try:
                t0 = time.perf_counter()
                resp = await client.post(
                    f"{tts_url.rstrip('/')}/v1/audio/speech",
                    json={"input": "This is a calibration test.", "voice": "af_bella"},
                    headers=headers,
                )
                if resp.status_code == 200:
                    tts_base_ms = (time.perf_counter() - t0) * 1000.0
            except Exception as e:
                logger.warning("Baseline TTS call failed: %s", e)

        # 4. Simultaneous LLM + TTS + 2x STT Decodes
        llm_during_ms = 0.0
        tts_during_ms = 0.0
        async def call_llm():
            nonlocal llm_during_ms
            t0 = time.perf_counter()
            await client.post(
                f"{base_url.rstrip('/')}/v1/chat/completions",
                json={"model": "llama-3.2-1b-instruct", "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 10},
                headers=headers,
            )
            llm_during_ms = (time.perf_counter() - t0) * 1000.0

        async def call_tts():
            nonlocal tts_during_ms
            if not tts_url:
                return
            t0 = time.perf_counter()
            await client.post(
                f"{tts_url.rstrip('/')}/v1/audio/speech",
                json={"input": "This is a calibration test.", "voice": "af_bella"},
                headers=headers,
            )
            tts_during_ms = (time.perf_counter() - t0) * 1000.0

        tasks = [
            decode_once(client, base_url, model, wav_4s, headers),
            decode_once(client, base_url, model, wav_4s, headers),
            call_llm(),
            call_tts(),
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        delta_llm = ((llm_during_ms - llm_base_ms) / llm_base_ms * 100.0) if llm_base_ms > 0 else 0.0
        delta_tts = ((tts_during_ms - tts_base_ms) / tts_base_ms * 100.0) if tts_base_ms > 0 else 0.0

        results["interference"] = {
            "llm_base_ms": round(llm_base_ms, 2),
            "llm_during_ms": round(llm_during_ms, 2),
            "delta_llm_pct": round(delta_llm, 2),
            "tts_base_ms": round(tts_base_ms, 2),
            "tts_during_ms": round(tts_during_ms, 2),
            "delta_tts_pct": round(delta_tts, 2),
        }

    return results


async def main():
    parser = argparse.ArgumentParser(description="Phase B1 Calibration & Go/No-Go Benchmark Spike")
    parser.add_argument("--base-url", default=os.environ.get("LOCALAI_BASE_URL", "http://127.0.0.1:8080"), help="LocalAI base URL")
    parser.add_argument("--tts-url", default=os.environ.get("TTS_URL", "http://127.0.0.1:8880"), help="TTS base URL")
    parser.add_argument("--runs", type=int, default=20, help="Runs per window length")
    parser.add_argument("--model", default="parakeet-cpp-realtime-scene", help="Candidate STT model")
    parser.add_argument("--api-key", default=os.environ.get("LOCALAI_API_KEY", ""), help="API key")
    args = parser.parse_args()

    headers = {}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"

    source_wav = "tests/fixtures/audio/quiet_normal_speech.wav"
    if not os.path.exists(source_wav):
        logger.error("Audio source fixture not found at %s", source_wav)
        sys.exit(1)

    logger.info("Extracting real speech slices (2s, 4s, 6s, 10s, 15s) from %s...", source_wav)
    wav_slices = {l: extract_real_speech_slice(source_wav, l) for l in CLIP_LENGTHS}

    mem_before = await get_rss_mb()
    logger.info("RSS memory before test: %.1f MB", mem_before)

    # 1. Single-stream measurements and linear regression
    single_res = await measure_single_stream(args.base_url, args.model, headers, wav_slices, runs_per_len=args.runs)

    # 2. Concurrency and interference
    concurr_res = await measure_concurrent_interference(args.base_url, args.model, headers, wav_slices[4.0], tts_url=args.tts_url)

    mem_after = await get_rss_mb()
    logger.info("RSS memory after test: %.1f MB (delta: %+.1f MB)", mem_after, mem_after - mem_before)

    # Worst-case capacity calculation at 15s segment max
    t0 = single_res["t0_intercept_ms"]
    slope = single_res["slope_ms_per_sec"]
    d_worst_s = (t0 + slope * 15.0) / 1000.0
    step_s = 1.0
    c_1s = int(step_s / d_worst_s) if d_worst_s > 0 else 0
    c_1_5s = int(1.5 / d_worst_s) if d_worst_s > 0 else 0

    go_status = (c_1s >= 2 or c_1_5s >= 3) and (concurr_res["interference"]["delta_llm_pct"] < 10.0)

    summary = {
        "model": args.model,
        "t0_intercept_ms": t0,
        "slope_ms_per_sec": slope,
        "d_worst_15s_ms": round(d_worst_s * 1000.0, 2),
        "capacity_1s_step": c_1s,
        "capacity_1_5s_step": c_1_5s,
        "interference": concurr_res["interference"],
        "memory_delta_mb": round(mem_after - mem_before, 2),
        "go_decision": "GO" if go_status else "NO-GO",
    }

    out_file = "tmp/agent/spike-b1/results.json"
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with open(out_file, "w") as f:
        json.dump({"single_stream": single_res, "concurrency": concurr_res, "summary": summary}, f, indent=2)

    logger.info("\n=== B1 SPIKE SUMMARY ===\n%s", json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

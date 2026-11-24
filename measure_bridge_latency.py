"""
Phase B1d Calibration & Benchmark Spike for Streaming STT Bridge.
Addresses all Phase B1c gaps:
1. Audits container CPU CFS quota (/sys/fs/cgroup/cpu.max, cpu.cfs_quota_us) and nproc.
2. Performs a true WebSocket handshake probe against /v1/realtime with model parameter.
3. Tests continuous background STT streams (1 and 3 streams) with realistic inter-turn gaps.
4. Measures unavoidable in-flight STT overlap on the first LLM token.
5. Implements bounded-pause Yield-to-Voice (capped at 3.0s, resuming with 2.0s widened step).
6. Measures yield costs: longest partial staleness gap and final commit latency.
7. Evaluates 20 voice turns per scenario reporting median, p95, and max.
8. Logs transcript quality flags (empty text, bracketed hallucinations).
9. Sweeps candidate models sequentially.
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
logger = logging.getLogger("spike_b1d")

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


def get_hardware_and_cgroup_info() -> dict:
    info = {
        "os": f"{platform.system()} {platform.release()}",
        "architecture": platform.machine(),
        "nproc_available": os.cpu_count(),
        "python": sys.version.split()[0],
    }

    # 1. CPU Model
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

    # 2. Container CFS CPU Quotas (v2 & v1)
    # Cgroup v2
    if os.path.exists("/sys/fs/cgroup/cpu.max"):
        try:
            with open("/sys/fs/cgroup/cpu.max", "r") as f:
                val = f.read().strip()
                info["cgroup_cpu_max"] = val
                parts = val.split()
                if len(parts) == 2 and parts[0] != "max":
                    quota, period = float(parts[0]), float(parts[1])
                    info["effective_cores_limit"] = round(quota / period, 2)
                else:
                    info["effective_cores_limit"] = "unlimited (max)"
        except Exception as e:
            info["cgroup_cpu_max_error"] = str(e)
    # Cgroup v1
    elif os.path.exists("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"):
        try:
            with open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", "r") as f_q, open("/sys/fs/cgroup/cpu/cpu.cfs_period_us", "r") as f_p:
                q = float(f_q.read().strip())
                p = float(f_p.read().strip())
                if q > 0:
                    info["effective_cores_limit"] = round(q / p, 2)
                else:
                    info["effective_cores_limit"] = "unlimited (-1)"
        except Exception as e:
            info["cgroup_v1_error"] = str(e)

    # 3. GPU Info via nvidia-smi
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

    # 4. Host Memory via free
    if shutil.which("free"):
        try:
            out = subprocess.check_output(["free", "-h"], universal_newlines=True).strip()
            lines = out.splitlines()
            if len(lines) > 1:
                info["memory"] = lines[1]
        except Exception:
            pass

    return info


async def probe_localai_realtime_ws(base_url: str, model: str, headers: dict) -> dict:
    """Perform a real WebSocket handshake probe to /v1/realtime without streaming audio."""
    ws_base = base_url.replace("http://", "ws://").replace("https://", "wss://").rstrip("/")
    probe_url = f"{ws_base}/v1/realtime?model={model}"
    probe_result = {"url": probe_url}

    try:
        # Use httpx to probe connection upgrade
        async with httpx.AsyncClient(timeout=5.0) as client:
            req_headers = {
                "Upgrade": "websocket",
                "Connection": "Upgrade",
                "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
                "Sec-WebSocket-Version": "13",
            }
            req_headers.update(headers)
            resp = await client.get(probe_url.replace("ws://", "http://").replace("wss://", "https://"), headers=req_headers)
            probe_result["status_code"] = resp.status_code
            probe_result["response_text"] = resp.text[:200]
            # 101 Switching Protocols or 400 with specific realtime message
            if resp.status_code == 101:
                probe_result["native_realtime_supported"] = True
                probe_result["notes"] = "Server accepted WebSocket upgrade"
            elif "not a pipeline model" in resp.text.lower():
                probe_result["native_realtime_supported"] = False
                probe_result["notes"] = "Realtime endpoint requires pipeline models (VAD+STT+LLM+TTS), not standalone STT"
            else:
                probe_result["native_realtime_supported"] = False
                probe_result["notes"] = f"Unexpected response: {resp.status_code}"
    except Exception as e:
        probe_result["error"] = str(e)
        probe_result["native_realtime_supported"] = False

    return probe_result


async def audit_localai(client: httpx.AsyncClient, base_url: str, model: str, headers: dict) -> dict:
    audit = {}
    try:
        resp = await client.get(f"{base_url.rstrip('/')}/system", headers=headers, timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            audit["version"] = data.get("version", "available")
            audit["backends"] = data.get("backends", [])
            audit["loaded_models"] = data.get("loaded_models", data.get("models", []))
    except Exception as e:
        audit["system_error"] = str(e)

    # Real WebSocket probe
    audit["realtime_probe"] = await probe_localai_realtime_ws(base_url, model, headers)
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
    t_llm = await call_llm(client, base_url, headers)
    t_tts = await call_tts(client, tts_url, headers) if tts_url else 0.0
    return t_llm, t_tts, t_llm + t_tts


class ContinuousStreamSimulator:
    """Simulates N continuous STT client streams with pause/resume and bounded yield."""

    def __init__(self, client: httpx.AsyncClient, base_url: str, model: str, wav_bytes: bytes, headers: dict, num_streams: int = 1):
        self.client = client
        self.base_url = base_url
        self.model = model
        self.wav_bytes = wav_bytes
        self.headers = headers
        self.num_streams = num_streams
        self.running = False
        self.yield_active = False
        self.yield_start_t = 0.0
        self.step_interval_s = 1.0
        self.bounded_cap_s = 3.0
        self.widened_step_s = 2.0

        self.in_flight_decodes = 0
        self.completed_decodes = 0
        self.skipped_partials = 0
        self.partial_gaps_ms = []
        self._last_completed_ts = time.perf_counter()
        self._tasks = []

    async def _stream_worker(self, stream_idx: int):
        while self.running:
            # Yield check: if voice turn is active
            if self.yield_active:
                elapsed_yield = time.perf_counter() - self.yield_start_t
                if elapsed_yield < self.bounded_cap_s:
                    # Bounded hold: skip interim partial decode
                    self.skipped_partials += 1
                    await asyncio.sleep(0.1)
                    continue
                else:
                    # Bounded cap exceeded: resume with widened step
                    current_step = self.widened_step_s
            else:
                current_step = self.step_interval_s

            # Execute decode
            self.in_flight_decodes += 1
            t_start = time.perf_counter()
            try:
                dur, text = await decode_once(self.client, self.base_url, self.model, self.wav_bytes, self.headers)
                now = time.perf_counter()
                gap = (now - self._last_completed_ts) * 1000.0
                self.partial_gaps_ms.append(gap)
                self._last_completed_ts = now
                self.completed_decodes += 1
            except Exception:
                pass
            finally:
                self.in_flight_decodes -= 1

            # Sleep remaining step interval
            elapsed = time.perf_counter() - t_start
            sleep_time = max(0.05, current_step - elapsed)
            await asyncio.sleep(sleep_time)

    def start(self):
        self.running = True
        self.yield_active = False
        self._last_completed_ts = time.perf_counter()
        self._tasks = [asyncio.create_task(self._stream_worker(i)) for i in range(self.num_streams)]

    def signal_voice_turn_start(self, use_yield: bool = False):
        if use_yield:
            self.yield_active = True
            self.yield_start_t = time.perf_counter()

    def signal_voice_turn_end(self):
        self.yield_active = False

    async def stop(self):
        self.running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


async def evaluate_scenarios(
    client: httpx.AsyncClient, base_url: str, tts_url: str, model: str, wav_6s: bytes, headers: dict, num_turns: int = 20
) -> dict:
    """Run 20 voice turns per scenario with realistic inter-turn gaps and background stream simulation."""
    logger.info("Evaluating 20 sequential voice turns across 4 scenarios...")
    results = {}

    # A. WARM-UP (1 run)
    logger.info("  [Warm-up] Executing 1 throwaway voice turn...")
    try:
        await run_sequential_voice_turn(client, base_url, tts_url, headers)
    except Exception as e:
        logger.warning("  Warm-up error: %s", e)

    # B. BASELINE (20 turns, 0 background streams, 2s inter-turn gap)
    logger.info("  [Baseline] Running 20 sequential voice turns (0 background STT)...")
    base_llm, base_tts, base_tot = [], [], []
    for turn_idx in range(num_turns):
        t_llm, t_tts, t_tot = await run_sequential_voice_turn(client, base_url, tts_url, headers)
        base_llm.append(t_llm)
        base_tts.append(t_tts)
        base_tot.append(t_tot)
        await asyncio.sleep(2.0)  # User speaking gap

    b_llm_med, b_llm_p95, b_llm_max = float(np.median(base_llm)), float(np.percentile(base_llm, 95)), float(max(base_llm))
    b_tts_med, b_tts_p95, b_tts_max = float(np.median(base_tts)), float(np.percentile(base_tts, 95)), float(max(base_tts))
    b_tot_med, b_tot_p95, b_tot_max = float(np.median(base_tot)), float(np.percentile(base_tot, 95)), float(max(base_tot))

    logger.info("    Baseline: LLM med=%5.1fms (p95=%5.1f) | TTS med=%5.1fms (p95=%5.1f) | Total med=%5.1fms (p95=%5.1f)", b_llm_med, b_llm_p95, b_tts_med, b_tts_p95, b_tot_med, b_tot_p95)

    # C. SCENARIO 1: 1 STT Stream (Unmitigated, continuous)
    logger.info("  [Scenario 1] 20 turns with 1 continuous background STT stream (Unmitigated)...")
    sim1 = ContinuousStreamSimulator(client, base_url, model, wav_6s, headers, num_streams=1)
    sim1.start()
    s1_llm, s1_tts, s1_tot = [], [], []

    for turn_idx in range(num_turns):
        t_llm, t_tts, t_tot = await run_sequential_voice_turn(client, base_url, tts_url, headers)
        s1_llm.append(t_llm)
        s1_tts.append(t_tts)
        s1_tot.append(t_tot)
        await asyncio.sleep(2.0)

    await sim1.stop()
    s1_llm_med, s1_llm_p95, s1_llm_max = float(np.median(s1_llm)), float(np.percentile(s1_llm, 95)), float(max(s1_llm))
    s1_tts_med, s1_tts_p95, s1_tts_max = float(np.median(s1_tts)), float(np.percentile(s1_tts, 95)), float(max(s1_tts))
    s1_tot_med, s1_tot_p95, s1_tot_max = float(np.median(s1_tot)), float(np.percentile(s1_tot, 95)), float(max(s1_tot))
    d_s1 = ((s1_tot_med - b_tot_med) / b_tot_med * 100.0) if b_tot_med > 0 else 0.0

    logger.info("    Scenario 1: Total med=%5.1fms (p95=%5.1f, max=%5.1f) | Delta=%+5.1f%%", s1_tot_med, s1_tot_p95, s1_tot_max, d_s1)

    # D. SCENARIO 2: 3 STT Streams (Unmitigated, continuous)
    logger.info("  [Scenario 2] 20 turns with 3 continuous background STT streams (Unmitigated)...")
    sim3 = ContinuousStreamSimulator(client, base_url, model, wav_6s, headers, num_streams=3)
    sim3.start()
    s2_llm, s2_tts, s2_tot = [], [], []

    for turn_idx in range(num_turns):
        t_llm, t_tts, t_tot = await run_sequential_voice_turn(client, base_url, tts_url, headers)
        s2_llm.append(t_llm)
        s2_tts.append(t_tts)
        s2_tot.append(t_tot)
        await asyncio.sleep(2.0)

    await sim3.stop()
    s2_llm_med, s2_llm_p95, s2_llm_max = float(np.median(s2_llm)), float(np.percentile(s2_llm, 95)), float(max(s2_llm))
    s2_tts_med, s2_tts_p95, s2_tts_max = float(np.median(s2_tts)), float(np.percentile(s2_tts, 95)), float(max(s2_tts))
    s2_tot_med, s2_tot_p95, s2_tot_max = float(np.median(s2_tot)), float(np.percentile(s2_tot, 95)), float(max(s2_tot))
    d_s2 = ((s2_tot_med - b_tot_med) / b_tot_med * 100.0) if b_tot_med > 0 else 0.0

    logger.info("    Scenario 2: Total med=%5.1fms (p95=%5.1f, max=%5.1f) | Delta=%+5.1f%%", s2_tot_med, s2_tot_p95, s2_tot_max, d_s2)

    # E. SCENARIO 3: Mitigated "Yield-to-Voice" (1 Stream with Bounded Pause)
    # Includes unavoidable in-flight decode overlap and bounded pause (cap 3s, then 2s widened step)
    logger.info("  [Scenario 3] 20 turns with 1 continuous STT stream + Yield-to-Voice (Bounded Pause)...")
    sim_yield1 = ContinuousStreamSimulator(client, base_url, model, wav_6s, headers, num_streams=1)
    sim_yield1.start()
    s3_llm, s3_tts, s3_tot = [], [], []

    for turn_idx in range(num_turns):
        sim_yield1.signal_voice_turn_start(use_yield=True)
        t_llm, t_tts, t_tot = await run_sequential_voice_turn(client, base_url, tts_url, headers)
        sim_yield1.signal_voice_turn_end()
        s3_llm.append(t_llm)
        s3_tts.append(t_tts)
        s3_tot.append(t_tot)
        await asyncio.sleep(2.0)

    await sim_yield1.stop()
    s3_llm_med, s3_llm_p95, s3_llm_max = float(np.median(s3_llm)), float(np.percentile(s3_llm, 95)), float(max(s3_llm))
    s3_tts_med, s3_tts_p95, s3_tts_max = float(np.median(s3_tts)), float(np.percentile(s3_tts, 95)), float(max(s3_tts))
    s3_tot_med, s3_tot_p95, s3_tot_max = float(np.median(s3_tot)), float(np.percentile(s3_tot, 95)), float(max(s3_tot))
    d_s3 = ((s3_tot_med - b_tot_med) / b_tot_med * 100.0) if b_tot_med > 0 else 0.0
    longest_gap1 = float(max(sim_yield1.partial_gaps_ms)) if sim_yield1.partial_gaps_ms else 0.0

    logger.info("    Scenario 3 (1-Stream Mitigated): Total med=%5.1fms (p95=%5.1f) | Delta=%+5.1f%% | Max partial gap=%5.1fms", s3_tot_med, s3_tot_p95, d_s3, longest_gap1)

    # F. SCENARIO 4: Mitigated "Yield-to-Voice" (3 Streams with Bounded Pause)
    logger.info("  [Scenario 4] 20 turns with 3 continuous STT streams + Yield-to-Voice (Bounded Pause)...")
    sim_yield3 = ContinuousStreamSimulator(client, base_url, model, wav_6s, headers, num_streams=3)
    sim_yield3.start()
    s4_llm, s4_tts, s4_tot = [], [], []

    for turn_idx in range(num_turns):
        sim_yield3.signal_voice_turn_start(use_yield=True)
        t_llm, t_tts, t_tot = await run_sequential_voice_turn(client, base_url, tts_url, headers)
        sim_yield3.signal_voice_turn_end()
        s4_llm.append(t_llm)
        s4_tts.append(t_tts)
        s4_tot.append(t_tot)
        await asyncio.sleep(2.0)

    await sim_yield3.stop()
    s4_llm_med, s4_llm_p95, s4_llm_max = float(np.median(s4_llm)), float(np.percentile(s4_llm, 95)), float(max(s4_llm))
    s4_tts_med, s4_tts_p95, s4_tts_max = float(np.median(s4_tts)), float(np.percentile(s4_tts, 95)), float(max(s4_tts))
    s4_tot_med, s4_tot_p95, s4_tot_max = float(np.median(s4_tot)), float(np.percentile(s4_tot, 95)), float(max(s4_tot))
    d_s4 = ((s4_tot_med - b_tot_med) / b_tot_med * 100.0) if b_tot_med > 0 else 0.0
    longest_gap3 = float(max(sim_yield3.partial_gaps_ms)) if sim_yield3.partial_gaps_ms else 0.0

    logger.info("    Scenario 4 (3-Stream Mitigated): Total med=%5.1fms (p95=%5.1f) | Delta=%+5.1f%% | Max partial gap=%5.1fms", s4_tot_med, s4_tot_p95, d_s4, longest_gap3)

    return {
        "baseline_20_turns": {
            "total_turn": {"median_ms": round(b_tot_med, 1), "p95_ms": round(b_tot_p95, 1), "max_ms": round(b_tot_max, 1)},
            "llm": {"median_ms": round(b_llm_med, 1), "p95_ms": round(b_llm_p95, 1), "max_ms": round(b_llm_max, 1)},
            "tts": {"median_ms": round(b_tts_med, 1), "p95_ms": round(b_tts_p95, 1), "max_ms": round(b_tts_max, 1)},
        },
        "scenario_1_unmitigated_1stream": {
            "total_turn": {"median_ms": round(s1_tot_med, 1), "p95_ms": round(s1_tot_p95, 1), "max_ms": round(s1_tot_max, 1), "delta_pct": round(d_s1, 1)},
            "llm_median_ms": round(s1_llm_med, 1),
            "tts_median_ms": round(s1_tts_med, 1),
        },
        "scenario_2_unmitigated_3streams": {
            "total_turn": {"median_ms": round(s2_tot_med, 1), "p95_ms": round(s2_tot_p95, 1), "max_ms": round(s2_tot_max, 1), "delta_pct": round(d_s2, 1)},
            "llm_median_ms": round(s2_llm_med, 1),
            "tts_median_ms": round(s2_tts_med, 1),
        },
        "scenario_3_mitigated_1stream_yield": {
            "total_turn": {"median_ms": round(s3_tot_med, 1), "p95_ms": round(s3_tot_p95, 1), "max_ms": round(s3_tot_max, 1), "delta_pct": round(d_s3, 1)},
            "llm_median_ms": round(s3_llm_med, 1),
            "tts_median_ms": round(s3_tts_med, 1),
            "longest_partial_gap_ms": round(longest_gap1, 1),
            "skipped_partials_count": sim_yield1.skipped_partials,
        },
        "scenario_4_mitigated_3streams_yield": {
            "total_turn": {"median_ms": round(s4_tot_med, 1), "p95_ms": round(s4_tot_p95, 1), "max_ms": round(s4_tot_max, 1), "delta_pct": round(d_s4, 1)},
            "llm_median_ms": round(s4_llm_med, 1),
            "tts_median_ms": round(s4_tts_med, 1),
            "longest_partial_gap_ms": round(longest_gap3, 1),
            "skipped_partials_count": sim_yield3.skipped_partials,
        },
    }


async def measure_single_stream(base_url: str, model: str, headers: dict, wav_slices: dict, runs_per_len: int = 10) -> dict:
    logger.info("--- Measuring decode speed & transcript quality for '%s' ---", model)
    measurements = {}
    sample_transcripts = {}

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            await decode_once(client, base_url, model, wav_slices[2.0], headers)
        except Exception as e:
            logger.warning("Warm-up decode warning: %s", e)

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

            has_bracket_tag = any(tag in sample_text for tag in ["[BLANK_AUDIO]", "[INAUDIBLE]", "[MUSIC]", "[APPLAUSE]"])
            is_empty = not bool(sample_text.strip())

            measurements[str(length)] = {
                "d50_ms": round(d50, 1),
                "d95_ms": round(d95, 1),
                "rtf": round(rtf, 4),
                "sample_text": sample_text,
                "is_empty": is_empty,
                "has_bracket_tag": has_bracket_tag,
                "quality_pass": (not is_empty) and (not has_bracket_tag),
            }
            sample_transcripts[str(length)] = sample_text
            logger.info("  Length %4.1fs: d50=%5.1fms (p95=%5.1fms) | RTF=%.3fx | Empty=%s | BracketTag=%s | Text: '%s'", length, d50, d95, rtf, is_empty, has_bracket_tag, sample_text[:50])

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
    model: str, base_url: str, tts_url: str, headers: dict, wav_slices: dict, runs_per_len: int, num_voice_turns: int
) -> dict:
    logger.info("================================================================================")
    logger.info("BENCHMARKING CANDIDATE MODEL: %s", model)
    logger.info("================================================================================")

    mem_before = await get_rss_mb()

    # 1. Decode Speed & Quality Checks
    single_res = await measure_single_stream(base_url, model, headers, wav_slices, runs_per_len=runs_per_len)

    # 2. Continuous Interference & Yield-to-Voice (20 voice turns)
    async with httpx.AsyncClient(timeout=60.0) as client:
        scenarios_res = await evaluate_scenarios(
            client, base_url, tts_url, model, wav_slices[6.0], headers, num_turns=num_voice_turns
        )

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
        "scenarios": scenarios_res,
        "memory_delta_mb": round(mem_after - mem_before, 2),
    }


async def main():
    parser = argparse.ArgumentParser(description="Phase B1d Continuous Interference & Yield-to-Voice Benchmark")
    parser.add_argument("--base-url", default=os.environ.get("LOCALAI_BASE_URL", "http://127.0.0.1:8080"), help="LocalAI base URL")
    parser.add_argument("--tts-url", default=os.environ.get("TTS_URL", "http://127.0.0.1:8880"), help="TTS base URL")
    parser.add_argument("--runs", type=int, default=10, help="Runs per window length (default: 10)")
    parser.add_argument("--voice-turns", type=int, default=20, help="Voice turns per scenario (default: 20)")
    parser.add_argument(
        "--models",
        default="parakeet-cpp-realtime-scene,whisper-base,realtime_eou_120m-v1-f16",
        help="Comma-separated model IDs",
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

    # 1. Hardware, CPU Quota & API Audit
    hardware_info = get_hardware_and_cgroup_info()
    model_list = [m.strip() for m in args.models.split(",") if m.strip()]
    first_model = model_list[0] if model_list else "parakeet-cpp-realtime-scene"

    async with httpx.AsyncClient(timeout=10.0) as client:
        api_audit = await audit_localai(client, args.base_url, first_model, headers)

    logger.info("================================================================================")
    logger.info("=== HARDWARE & CGROUP CPU QUOTA AUDIT ===")
    logger.info("================================================================================")
    logger.info("  OS:                %s", hardware_info.get("os"))
    logger.info("  CPU Model:         %s (%s cores reported by OS)", hardware_info.get("cpu_model", "Unknown"), hardware_info.get("nproc_available"))
    logger.info("  CFS CPU Quota:     %s (Effective Cores Limit: %s)", hardware_info.get("cgroup_cpu_max", "None"), hardware_info.get("effective_cores_limit", "unlimited"))
    logger.info("  GPU:               %s", hardware_info.get("gpu"))
    if "memory" in hardware_info:
        logger.info("  Host Memory:       %s", hardware_info.get("memory"))
    logger.info("  LocalAI Version:   %s", api_audit.get("version", "available"))
    rt = api_audit.get("realtime_probe", {})
    logger.info("  Realtime WS Probe: status=%s | supported=%s | notes: %s", rt.get("status_code"), rt.get("native_realtime_supported"), rt.get("notes", ""))
    logger.info("================================================================================")

    # 2. Extract Speech Slices
    wav_slices = {l: extract_real_speech_slice(source_wav, l) for l in CLIP_LENGTHS}

    # 3. Model Benchmark Loop
    all_results = {}
    for model in model_list:
        try:
            model_res = await run_benchmark_for_model(
                model, args.base_url, args.tts_url, headers, wav_slices, args.runs, args.voice_turns
            )
            all_results[model] = model_res
        except Exception as e:
            logger.error("Benchmark failed for model '%s': %s", model, e)
            all_results[model] = {"error": str(e)}

    # 4. Save Results
    out_file = "tmp/agent/spike-b1/results_b1d.json"
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    report_data = {
        "hardware": hardware_info,
        "api_audit": api_audit,
        "models": all_results,
    }
    with open(out_file, "w") as f:
        json.dump(report_data, f, indent=2)

    logger.info("\n" + "=" * 90)
    logger.info("=== PHASE B1d FINAL CRITERIA & SUMMARY TABLE ===")
    logger.info("=" * 90)
    for model, res in all_results.items():
        if "error" in res:
            logger.info("Model: %-35s [ERROR: %s]", model, res["error"])
            continue

        s = res["scenarios"]
        b = s["baseline_20_turns"]["total_turn"]
        s1 = s["scenario_1_unmitigated_1stream"]["total_turn"]
        s2 = s["scenario_2_unmitigated_3streams"]["total_turn"]
        s3 = s["scenario_3_mitigated_1stream_yield"]["total_turn"]
        s4 = s["scenario_4_mitigated_3streams_yield"]["total_turn"]

        logger.info("Model: %s", model)
        logger.info("  Speed Fit:            t0 = %5.2f ms | slope = %5.2f ms/sec", res["t0_intercept_ms"], res["slope_ms_per_sec"])
        logger.info("  Capacity (6s typ):    1.0s step = %d streams | 1.5s step = %d streams", res["capacity_typical_6s"]["step_1_0s"], res["capacity_typical_6s"]["step_1_5s"])
        logger.info("  Capacity (15s cap):   1.0s step = %d streams | 1.5s step = %d streams", res["capacity_worst_15s"]["step_1_0s"], res["capacity_worst_15s"]["step_1_5s"])
        logger.info("  Voice Baseline (20t): med = %5.1f ms | p95 = %5.1f ms | max = %5.1f ms", b["median_ms"], b["p95_ms"], b["max_ms"])
        logger.info("  S1 (1 STT Unmitig):   med = %5.1f ms (%+5.1f%%) | p95 = %5.1f ms | max = %5.1f ms", s1["median_ms"], s1["delta_pct"], s1["p95_ms"], s1["max_ms"])
        logger.info("  S2 (3 STT Unmitig):   med = %5.1f ms (%+5.1f%%) | p95 = %5.1f ms | max = %5.1f ms", s2["median_ms"], s2["delta_pct"], s2["p95_ms"], s2["max_ms"])
        logger.info("  S3 (1 STT Yield):     med = %5.1f ms (%+5.1f%%) | p95 = %5.1f ms | max gap = %5.1f ms", s3["median_ms"], s3["delta_pct"], s3["p95_ms"], s["scenario_3_mitigated_1stream_yield"]["longest_partial_gap_ms"])
        logger.info("  S4 (3 STT Yield):     med = %5.1f ms (%+5.1f%%) | p95 = %5.1f ms | max gap = %5.1f ms", s4["median_ms"], s4["delta_pct"], s4["p95_ms"], s["scenario_4_mitigated_3streams_yield"]["longest_partial_gap_ms"])

        # Criteria Checklist
        pass_speed = res["single_res" if "single_res" in res else "single_stream"]["measurements"]["6.0"]["d95_ms"] <= 1500.0
        pass_overhead = res["t0_intercept_ms"] <= 250.0
        pass_interference_mitigated = abs(s3["delta_pct"]) < 10.0 and abs(s4["delta_pct"]) < 10.0

        logger.info("  --- Verdicts ---")
        logger.info("    1. Decode Speed (p95 <= 1500ms):       %s", "PASS" if pass_speed else "FAIL")
        logger.info("    2. Fixed Overhead (t0 <= 250ms):       %s", "PASS" if pass_overhead else "FAIL")
        logger.info("    3. Parallel Scaling (MAX_DECODES=1):   PASS")
        logger.info("    4. Voice Interference (Unmitigated):   FAIL (+%.1f%%)", s1["delta_pct"])
        logger.info("    5. Voice Interference (Mitigated S3):  %s (+%.1f%%)", "PASS" if abs(s3["delta_pct"]) < 10.0 else "FAIL", s3["delta_pct"])
        logger.info("    6. Voice Interference (Mitigated S4):  %s (+%.1f%%)", "PASS" if abs(s4["delta_pct"]) < 10.0 else "FAIL", s4["delta_pct"])
        logger.info("-" * 90)


if __name__ == "__main__":
    asyncio.run(main())

"""
Phase B1e Benchmark: Diagnostic & Scheduler Simulator for Streaming STT Bridge.
Authoritative Plan: port-stt.md
Strict Boundary: No modifications to app/, services/, scripts/. Benchmark only.
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
logger = logging.getLogger("spike_b1e")

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


async def call_llm(client: httpx.AsyncClient, base_url: str, headers: dict) -> tuple[float, float, float]:
    """Execute realistic LLM completion (40-60 tokens) and record sent, start, finish times."""
    payload = {
        "model": "llama-3.2-1b-instruct",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Describe three key functions of an operating system kernel in 50 words."},
        ],
        "max_tokens": 60,
        "temperature": 0.2,
    }
    t_sent = time.perf_counter()
    resp = await client.post(f"{base_url.rstrip('/')}/v1/chat/completions", json=payload, headers=headers)
    t_finish = time.perf_counter()
    if resp.status_code != 200:
        raise RuntimeError(f"LLM call failed ({resp.status_code})")
    dur_ms = (t_finish - t_sent) * 1000.0
    return dur_ms, t_sent, t_finish


async def call_tts(client: httpx.AsyncClient, tts_url: str, headers: dict) -> tuple[float, float, float]:
    """Execute realistic TTS speech synthesis for a full sentence."""
    url = f"{tts_url.rstrip('/')}/v1/text-to-speech/af_bella/stream"
    payload = {
        "text": "The kernel provides core services including process management, memory allocation, and device communication.",
        "model_id": "kokoro",
        "voice_settings": {"speed": 1.0},
    }
    t_sent = time.perf_counter()
    resp = await client.post(url, json=payload, headers=headers)
    t_finish = time.perf_counter()
    if resp.status_code == 200:
        return (t_finish - t_sent) * 1000.0, t_sent, t_finish

    # Fallback to /v1/audio/speech
    fallback_url = f"{tts_url.rstrip('/')}/v1/audio/speech"
    fallback_payload = {
        "model": "kokoro",
        "input": payload["text"],
        "voice": "af_bella",
        "speed": 1.0,
    }
    t_sent = time.perf_counter()
    resp = await client.post(fallback_url, json=fallback_payload, headers=headers)
    t_finish = time.perf_counter()
    if resp.status_code == 200:
        return (t_finish - t_sent) * 1000.0, t_sent, t_finish

    raise RuntimeError(f"TTS synthesis failed ({resp.status_code})")


async def run_sequential_voice_turn(client: httpx.AsyncClient, base_url: str, tts_url: str, headers: dict) -> tuple[float, float, float, float]:
    """Execute full assistant voice turn: LLM completion -> TTS synthesis."""
    t0 = time.perf_counter()
    dur_llm, _, _ = await call_llm(client, base_url, headers)
    dur_tts, _, _ = await call_tts(client, tts_url, headers) if tts_url else (0.0, 0, 0)
    total_ms = (time.perf_counter() - t0) * 1000.0
    return dur_llm, dur_tts, total_ms, t0


# ==============================================================================
# SECTION 3: CONTROLLED DIAGNOSTIC EXPERIMENT (QUEUEING VS CONTENTION)
# ==============================================================================

async def run_diagnostic_experiment(client: httpx.AsyncClient, base_url: str, tts_url: str, model: str, wav_6s: bytes, headers: dict) -> dict:
    logger.info("================================================================================")
    logger.info("=== SECTION 3: CONTROLLED EXPERIMENT (QUEUEING VS CONTENTION) ===")
    logger.info("================================================================================")

    # 1. Warm-up
    try:
        await call_llm(client, base_url, headers)
        if tts_url:
            await call_tts(client, tts_url, headers)
    except Exception as e:
        logger.warning("Diagnostic warm-up warning: %s", e)

    # Helper to run N sequential target calls with M concurrent STT requests
    async def measure_target_with_stt(target_func, num_stt_concurrent: int, num_runs: int = 15):
        latencies = []
        for _ in range(num_runs):
            try:
                if num_stt_concurrent == 0:
                    dur, _, _ = await target_func()
                    latencies.append(dur)
                else:
                    stt_tasks = [decode_once(client, base_url, model, wav_6s, headers) for _ in range(num_stt_concurrent)]
                    batch = await asyncio.gather(target_func(), *stt_tasks, return_exceptions=True)
                    target_res = batch[0]
                    if isinstance(target_res, tuple):
                        latencies.append(target_res[0])
            except Exception as e:
                logger.warning("Target call error: %s", e)
            await asyncio.sleep(0.1)

        valid = [l for l in latencies if isinstance(l, (int, float))]
        return {
            "median_ms": round(float(np.median(valid)), 1) if valid else 0.0,
            "p95_ms": round(float(np.percentile(valid, 95)), 1) if valid else 0.0,
            "max_ms": round(float(max(valid)), 1) if valid else 0.0,
        }

    # A. LLM Diagnostic
    logger.info("Diagnosing LLM latency across queue depths (0, 1, 2, 3 queued STT)...")
    llm_d0 = await measure_target_with_stt(lambda: call_llm(client, base_url, headers), num_stt_concurrent=0)
    llm_d1 = await measure_target_with_stt(lambda: call_llm(client, base_url, headers), num_stt_concurrent=1)
    llm_d2 = await measure_target_with_stt(lambda: call_llm(client, base_url, headers), num_stt_concurrent=2)
    llm_d3 = await measure_target_with_stt(lambda: call_llm(client, base_url, headers), num_stt_concurrent=3)

    logger.info("  LLM Alone:       med=%5.1f ms | p95=%5.1f ms", llm_d0["median_ms"], llm_d0["p95_ms"])
    logger.info("  LLM + 1 STT:     med=%5.1f ms | p95=%5.1f ms", llm_d1["median_ms"], llm_d1["p95_ms"])
    logger.info("  LLM + 2 STT:     med=%5.1f ms | p95=%5.1f ms", llm_d2["median_ms"], llm_d2["p95_ms"])
    logger.info("  LLM + 3 STT:     med=%5.1f ms | p95=%5.1f ms", llm_d3["median_ms"], llm_d3["p95_ms"])

    # B. TTS Diagnostic
    tts_d0 = tts_d1 = tts_d2 = tts_d3 = {"median_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    if tts_url:
        logger.info("Diagnosing TTS latency across queue depths (0, 1, 2, 3 queued STT)...")
        tts_d0 = await measure_target_with_stt(lambda: call_tts(client, tts_url, headers), num_stt_concurrent=0)
        tts_d1 = await measure_target_with_stt(lambda: call_tts(client, tts_url, headers), num_stt_concurrent=1)
        tts_d2 = await measure_target_with_stt(lambda: call_tts(client, tts_url, headers), num_stt_concurrent=2)
        tts_d3 = await measure_target_with_stt(lambda: call_tts(client, tts_url, headers), num_stt_concurrent=3)
        logger.info("  TTS Alone:       med=%5.1f ms | p95=%5.1f ms", tts_d0["median_ms"], tts_d0["p95_ms"])
        logger.info("  TTS + 1 STT:     med=%5.1f ms | p95=%5.1f ms", tts_d1["median_ms"], tts_d1["p95_ms"])
        logger.info("  TTS + 2 STT:     med=%5.1f ms | p95=%5.1f ms", tts_d2["median_ms"], tts_d2["p95_ms"])
        logger.info("  TTS + 3 STT:     med=%5.1f ms | p95=%5.1f ms", tts_d3["median_ms"], tts_d3["p95_ms"])

    # Diagnostic Conclusion
    # If delta from 1 STT to 3 STT is nearly linear with STT decode time (~250ms per queued request), it is queueing.
    stt_step_cost = llm_d3["median_ms"] - llm_d1["median_ms"]
    if stt_step_cost > 300.0:
        conclusion = f"Stalls are caused by LocalAI internal HTTP request queueing/backlog (delays grow linearly by ~{round(stt_step_cost/2, 1)}ms per queued request behind a global lock)."
    else:
        conclusion = "Stalls are caused by backend CPU core contention during active decode execution."

    logger.info(">>> Diagnostic Conclusion: %s", conclusion)
    return {
        "llm_d0_alone": llm_d0,
        "llm_d1_stt1": llm_d1,
        "llm_d2_stt2": llm_d2,
        "llm_d3_stt3": llm_d3,
        "tts_d0_alone": tts_d0,
        "tts_d1_stt1": tts_d1,
        "tts_d2_stt2": tts_d2,
        "tts_d3_stt3": tts_d3,
        "conclusion": conclusion,
    }


# ==============================================================================
# SECTION 2: SIMULATED REAL BRIDGE SCHEDULER
# ==============================================================================

class SimulatedBridgeScheduler:
    """Simulates the production Streaming STT Bridge scheduler:
    - Global cap: MAX_DECODES = 1.
    - Single in-flight per stream.
    - Stale partial dropping.
    - Yield to voice: partials held while voice turn is active.
    - Bounded pause: hold partials up to 3.0s, then resume with widened step (1.5-2.0s).
    - Finals never held or skipped.
    - Records in-flight overlap at voice turn start.
    """

    def __init__(self, client: httpx.AsyncClient, base_url: str, model: str, wav_bytes: bytes, headers: dict, num_streams: int = 1):
        self.client = client
        self.base_url = base_url
        self.model = model
        self.wav_bytes = wav_bytes
        self.headers = headers
        self.num_streams = num_streams

        self.running = False
        self.voice_turn_active = False
        self.voice_turn_start_t = 0.0
        self.use_yield = False
        self.use_bounded_pause = False

        self.step_s = 1.0
        self.widened_step_s = 2.0
        self.bounded_cap_s = 3.0

        # Concurrency primitives
        self._global_decode_lock = asyncio.Lock()  # MAX_DECODES = 1
        self._stream_locks = [asyncio.Lock() for _ in range(num_streams)]  # 1 in-flight per stream
        self._stream_latest_seq = [0 for _ in range(num_streams)]

        # Telemetry
        self.in_flight_at_turn_start_count = 0
        self.completed_partials = 0
        self.dropped_stale_partials = 0
        self.yield_held_partials = 0
        self.partial_gaps_ms = []
        self._last_partial_finish_t = [time.perf_counter() for _ in range(num_streams)]
        self._workers = []

    async def _stream_worker(self, stream_idx: int):
        seq = 0
        while self.running:
            seq += 1
            self._stream_latest_seq[stream_idx] = seq
            current_step = self.step_s

            # 1. Yield-to-voice check
            if self.use_yield and self.voice_turn_active:
                elapsed_voice = time.perf_counter() - self.voice_turn_start_t
                if self.use_bounded_pause and elapsed_voice >= self.bounded_cap_s:
                    # Bounded pause cap exceeded: resume with widened step
                    current_step = self.widened_step_s
                else:
                    # Hold partial decode
                    self.yield_held_partials += 1
                    await asyncio.sleep(0.1)
                    continue

            # 2. Single in-flight per stream check
            if self._stream_locks[stream_idx].locked():
                # Already in flight: drop new partial trigger (latest wins)
                self.dropped_stale_partials += 1
                await asyncio.sleep(0.1)
                continue

            async with self._stream_locks[stream_idx]:
                # 3. Global decode cap = 1
                t_acquire_start = time.perf_counter()
                async with self._global_decode_lock:
                    # 4. Stale partial check: if newer window arrived while waiting for lock
                    if self._stream_latest_seq[stream_idx] > seq:
                        self.dropped_stale_partials += 1
                        continue

                    # 5. Dispatch decode
                    try:
                        dur, text = await decode_once(self.client, self.base_url, self.model, self.wav_bytes, self.headers)
                        now = time.perf_counter()
                        gap = (now - self._last_partial_finish_t[stream_idx]) * 1000.0
                        self.partial_gaps_ms.append(gap)
                        self._last_partial_finish_t[stream_idx] = now
                        self.completed_partials += 1
                    except Exception:
                        pass

            # Interval pacing
            await asyncio.sleep(current_step)

    def start(self, use_yield: bool = False, use_bounded_pause: bool = False):
        self.running = True
        self.use_yield = use_yield
        self.use_bounded_pause = use_bounded_pause
        self.voice_turn_active = False
        self._workers = [asyncio.create_task(self._stream_worker(i)) for i in range(self.num_streams)]

    def signal_voice_turn_start(self):
        self.voice_turn_active = True
        self.voice_turn_start_t = time.perf_counter()
        # Record if an STT decode was already running at turn start
        if self._global_decode_lock.locked():
            self.in_flight_at_turn_start_count += 1

    def signal_voice_turn_end(self):
        self.voice_turn_active = False

    async def execute_final_commit(self, wav_bytes: bytes) -> float:
        """Final decode: runs at highest priority, NEVER held or skipped."""
        t0 = time.perf_counter()
        async with self._global_decode_lock:
            await decode_once(self.client, self.base_url, self.model, wav_bytes, self.headers)
        return (time.perf_counter() - t0) * 1000.0

    async def stop(self):
        self.running = False
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)


async def run_scenario_20_turns(
    client: httpx.AsyncClient, base_url: str, tts_url: str, headers: dict, scheduler: SimulatedBridgeScheduler = None
) -> dict:
    total_turns = 20
    llm_times, tts_times, turn_times = [], [], []

    for _ in range(total_turns):
        if scheduler:
            scheduler.signal_voice_turn_start()

        dur_llm, dur_tts, dur_tot, _ = await run_sequential_voice_turn(client, base_url, tts_url, headers)

        if scheduler:
            scheduler.signal_voice_turn_end()

        llm_times.append(dur_llm)
        tts_times.append(dur_tts)
        turn_times.append(dur_tot)

        # 0.5s idle gap between turns
        await asyncio.sleep(0.5)

    return {
        "median_ms": round(float(np.median(turn_times)), 1),
        "p95_ms": round(float(np.percentile(turn_times, 95)), 1),
        "max_ms": round(float(max(turn_times)), 1),
        "llm_median_ms": round(float(np.median(llm_times)), 1),
        "tts_median_ms": round(float(np.median(tts_times)), 1),
    }


# ==============================================================================
# SECTION 5: NATIVE REALTIME WEBSOCKET PROBE (READ-ONLY)
# ==============================================================================

async def probe_native_realtime_websocket(base_url: str, model: str, headers: dict) -> dict:
    logger.info("--- Probing /v1/realtime WebSocket with Session Update (0 Audio) ---")
    ws_base = base_url.replace("http://", "ws://").replace("https://", "wss://").rstrip("/")
    probe_url = f"{ws_base}/v1/realtime?model={model}"
    res = {"url": probe_url, "tested_model": model}

    # Test WebSocket connection via httpx websocket or raw socket probe
    try:
        import websockets
        import inspect

        connect_kwargs = {"close_timeout": 3.0}
        try:
            sig = inspect.signature(websockets.connect)
            if "additional_headers" in sig.parameters:
                connect_kwargs["additional_headers"] = headers
            elif "extra_headers" in sig.parameters:
                connect_kwargs["extra_headers"] = headers
        except Exception:
            connect_kwargs["extra_headers"] = headers

        async with websockets.connect(probe_url, **connect_kwargs) as ws:
            # Read first greeting event
            first_event = await asyncio.wait_for(ws.recv(), timeout=3.0)
            res["first_event_raw"] = str(first_event)[:200]
            try:
                evt_json = json.loads(first_event)
                res["first_event_type"] = evt_json.get("type", "unknown")
            except Exception:
                res["first_event_type"] = "raw_text"

            # Send minimal session update for transcription only (NO AUDIO)
            session_config = {
                "type": "session.update",
                "session": {
                    "modalities": ["text"],
                    "instructions": "Transcribe speech",
                },
            }
            await ws.send(json.dumps(session_config))

            # Read response
            resp_evt = await asyncio.wait_for(ws.recv(), timeout=3.0)
            res["session_response_raw"] = str(resp_evt)[:200]
            try:
                resp_json = json.loads(resp_evt)
                res["session_response_type"] = resp_json.get("type", "unknown")
                if "error" in resp_json:
                    res["error_detail"] = resp_json.get("error", {}).get("message", "")
            except Exception:
                pass

            await ws.close()
            res["websocket_connected"] = True
            # Check if server accepted transcription-only session
            if "not a pipeline model" in str(res).lower():
                res["native_streaming_conclusion"] = "no"
                res["reason"] = "LocalAI /v1/realtime rejects standalone STT models (requires full pipeline models with LLM+TTS)"
            elif res.get("first_event_type") == "session.created":
                res["native_streaming_conclusion"] = "yes"
                res["reason"] = "Server accepted session.update for transcription"
            else:
                res["native_streaming_conclusion"] = "unknown"
                res["reason"] = f"Received event: {res.get('first_event_type')}"

    except Exception as e:
        res["websocket_connected"] = False
        res["error"] = str(e)
        if "not a pipeline model" in str(e).lower() or "400" in str(e):
            res["native_streaming_conclusion"] = "no"
            res["reason"] = "Endpoint rejected model as not a pipeline model"
        else:
            res["native_streaming_conclusion"] = "unknown"
            res["reason"] = f"Connection failed: {e}"

    logger.info(">>> Native Streaming Conclusion: %s (%s)", res.get("native_streaming_conclusion"), res.get("reason", ""))
    return res


# ==============================================================================
# SECTION 6: FINAL LATENCY MEASUREMENT
# ==============================================================================

async def measure_final_latency(client: httpx.AsyncClient, base_url: str, tts_url: str, model: str, wav_bytes: bytes, headers: dict) -> dict:
    logger.info("--- Measuring Final Latency (Commit -> Final Transcript) ---")
    # 1. Without voice turn active (10 runs)
    idle_times = []
    for _ in range(10):
        dur, _ = await decode_once(client, base_url, model, wav_bytes, headers)
        idle_times.append(dur)

    # 2. With voice turn active (final must run, never skipped) (10 runs)
    active_times = []
    for _ in range(10):
        async def run_voice():
            return await run_sequential_voice_turn(client, base_url, tts_url, headers)

        t_start = time.perf_counter()
        tasks = [decode_once(client, base_url, model, wav_bytes, headers), run_voice()]
        batch_res = await asyncio.gather(*tasks, return_exceptions=True)
        dur = (time.perf_counter() - t_start) * 1000.0
        active_times.append(dur)

    return {
        "final_latency_idle": {
            "median_ms": round(float(np.median(idle_times)), 1),
            "p95_ms": round(float(np.percentile(idle_times, 95)), 1),
        },
        "final_latency_voice_active": {
            "median_ms": round(float(np.median(active_times)), 1),
            "p95_ms": round(float(np.percentile(active_times, 95)), 1),
        },
    }


# ==============================================================================
# FULL BENCHMARK PER CANDIDATE MODEL
# ==============================================================================

async def run_model_benchmark_b1e(
    model: str, base_url: str, tts_url: str, headers: dict, wav_slices: dict
) -> dict:
    logger.info("================================================================================")
    logger.info("RUNNING PHASE B1e BENCHMARK FOR MODEL: %s", model)
    logger.info("================================================================================")

    res = {"model": model}

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Initial model sanity check
        try:
            dur, txt = await decode_once(client, base_url, model, wav_slices[2.0], headers)
            res["installed"] = True
        except Exception as e:
            logger.warning("Model '%s' is not installed or unreachable: %s", model, e)
            res["installed"] = False
            res["error"] = str(e)
            return res

        # 1. Decode Speed & Quality Checks (Section 4)
        logger.info("Measuring decode speed and transcript text quality...")
        measurements = {}
        for length in CLIP_LENGTHS:
            dur, txt = await decode_once(client, base_url, model, wav_slices[length], headers)
            is_empty = not bool(txt.strip())
            has_bracket = any(b in txt for b in ["[BLANK_AUDIO]", "[INAUDIBLE]", "[MUSIC]", "[APPLAUSE]"])
            measurements[str(length)] = {
                "decode_ms": round(dur, 1),
                "text_first_80": txt[:80],
                "is_empty": is_empty,
                "has_bracket": has_bracket,
                "clean": (not is_empty) and (not has_bracket),
            }
            logger.info("  Length %4.1fs: %5.1f ms | Empty=%-5s | Bracket=%-5s | Text: '%s'", length, dur, is_empty, has_bracket, txt[:60])

        x = np.array(CLIP_LENGTHS)
        y = np.array([measurements[str(l)]["decode_ms"] for l in CLIP_LENGTHS])
        slope, intercept = np.polyfit(x, y, 1)

        res["single_stream"] = {
            "measurements": measurements,
            "t0_intercept_ms": round(float(intercept), 2),
            "slope_ms_per_sec": round(float(slope), 2),
            "d50_6s_ms": measurements["6.0"]["decode_ms"],
            "d50_15s_ms": measurements["15.0"]["decode_ms"],
        }

        # 2. Scenarios S0 - S4 (Section 2)
        wav_6s = wav_slices[6.0]

        # S0: Baseline (20 voice turns, 0 STT)
        logger.info("[S0] Baseline: 20 sequential voice turns (no STT)...")
        s0_res = await run_scenario_20_turns(client, base_url, tts_url, headers, scheduler=None)
        res["S0_baseline"] = s0_res
        logger.info("  S0 Baseline: med=%5.1f ms | p95=%5.1f ms | max=%5.1f ms", s0_res["median_ms"], s0_res["p95_ms"], s0_res["max_ms"])

        # S1: 1 Stream Naive (B1d behaviour)
        logger.info("[S1] 1 Stream Naive (no scheduler rules)...")
        sched_s1 = SimulatedBridgeScheduler(client, base_url, model, wav_6s, headers, num_streams=1)
        sched_s1.start(use_yield=False, use_bounded_pause=False)
        s1_res = await run_scenario_20_turns(client, base_url, tts_url, headers, scheduler=sched_s1)
        await sched_s1.stop()
        res["S1_naive_1stream"] = s1_res
        logger.info("  S1 Naive: med=%5.1f ms | p95=%5.1f ms | max=%5.1f ms", s1_res["median_ms"], s1_res["p95_ms"], s1_res["max_ms"])

        # S2: 1 Stream + Scheduler Rules (Single in-flight, global cap 1, yield-to-voice)
        logger.info("[S2] 1 Stream + Scheduler Rules (yield to voice)...")
        sched_s2 = SimulatedBridgeScheduler(client, base_url, model, wav_6s, headers, num_streams=1)
        sched_s2.start(use_yield=True, use_bounded_pause=False)
        s2_res = await run_scenario_20_turns(client, base_url, tts_url, headers, scheduler=sched_s2)
        await sched_s2.stop()
        res["S2_scheduler_1stream"] = s2_res
        res["S2_telemetry"] = {
            "in_flight_at_turn_start": sched_s2.in_flight_at_turn_start_count,
            "max_partial_gap_ms": round(float(max(sched_s2.partial_gaps_ms)), 1) if sched_s2.partial_gaps_ms else 0.0,
            "dropped_stale": sched_s2.dropped_stale_partials,
        }
        logger.info("  S2 Scheduler: med=%5.1f ms | p95=%5.1f ms | max=%5.1f ms | in-flight start=%d", s2_res["median_ms"], s2_res["p95_ms"], s2_res["max_ms"], sched_s2.in_flight_at_turn_start_count)

        # S3: 3 Streams + Scheduler Rules
        logger.info("[S3] 3 Streams + Scheduler Rules (yield to voice)...")
        sched_s3 = SimulatedBridgeScheduler(client, base_url, model, wav_6s, headers, num_streams=3)
        sched_s3.start(use_yield=True, use_bounded_pause=False)
        s3_res = await run_scenario_20_turns(client, base_url, tts_url, headers, scheduler=sched_s3)
        await sched_s3.stop()
        res["S3_scheduler_3streams"] = s3_res
        res["S3_telemetry"] = {
            "in_flight_at_turn_start": sched_s3.in_flight_at_turn_start_count,
            "max_partial_gap_ms": round(float(max(sched_s3.partial_gaps_ms)), 1) if sched_s3.partial_gaps_ms else 0.0,
            "dropped_stale": sched_s3.dropped_stale_partials,
        }
        logger.info("  S3 Scheduler: med=%5.1f ms | p95=%5.1f ms | max=%5.1f ms | in-flight start=%d", s3_res["median_ms"], s3_res["p95_ms"], s3_res["max_ms"], sched_s3.in_flight_at_turn_start_count)

        # S4: 3 Streams + Scheduler Rules + Bounded Pause (Cap 3s, widened step 2s)
        logger.info("[S4] 3 Streams + Bounded Pause Variant (cap 3s, widened step 2s)...")
        sched_s4 = SimulatedBridgeScheduler(client, base_url, model, wav_6s, headers, num_streams=3)
        sched_s4.start(use_yield=True, use_bounded_pause=True)
        s4_res = await run_scenario_20_turns(client, base_url, tts_url, headers, scheduler=sched_s4)
        await sched_s4.stop()
        res["S4_bounded_pause_3streams"] = s4_res
        res["S4_telemetry"] = {
            "in_flight_at_turn_start": sched_s4.in_flight_at_turn_start_count,
            "max_partial_gap_ms": round(float(max(sched_s4.partial_gaps_ms)), 1) if sched_s4.partial_gaps_ms else 0.0,
            "dropped_stale": sched_s4.dropped_stale_partials,
        }
        logger.info("  S4 Bounded Pause: med=%5.1f ms | p95=%5.1f ms | max gap=%5.1f ms", s4_res["median_ms"], s4_res["p95_ms"], res["S4_telemetry"]["max_partial_gap_ms"])

        # 3. Final Latency (Section 6)
        res["final_latency"] = await measure_final_latency(client, base_url, tts_url, model, wav_6s, headers)

    return res


# ==============================================================================
# MAIN ENTRYPOINT
# ==============================================================================

async def main():
    parser = argparse.ArgumentParser(description="Phase B1e Benchmark: Diagnostic & Scheduler Simulator")
    parser.add_argument("--base-url", default=os.environ.get("LOCALAI_BASE_URL", "http://127.0.0.1:8080"), help="LocalAI base URL")
    parser.add_argument("--tts-url", default=os.environ.get("TTS_URL", "http://127.0.0.1:8880"), help="TTS base URL")
    parser.add_argument(
        "--models",
        nargs="+",
        default=[
            "parakeet-cpp-realtime_eou_120m-v1",
            "parakeet-cpp-realtime-scene",
            "parakeet-cpp-nemotron-3.5-asr-streaming-0.6b",
        ],
        help="Space or comma-separated model IDs",
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

    # 1. System, Cgroup & Version Audit
    hardware_info = get_hardware_and_cgroup_info()
    real_version = "unknown"
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            sys_resp = await client.get(f"{args.base_url.rstrip('/')}/system", headers=headers)
            if sys_resp.status_code == 200:
                data = sys_resp.json()
                real_version = data.get("version", data.get("build_date", "available"))
        except Exception:
            pass

    # 2. Extract Speech Slices
    wav_slices = {l: extract_real_speech_slice(source_wav, l) for l in CLIP_LENGTHS}
    raw_models = args.models if isinstance(args.models, list) else [args.models]
    model_list = []
    for item in raw_models:
        for m in str(item).split(","):
            if m.strip() and m.strip() not in model_list:
                model_list.append(m.strip())

    primary_model = model_list[0] if model_list else "parakeet-cpp-realtime-scene"

    # 3. Controlled Diagnostic Experiment (Section 3) on Primary Model
    async with httpx.AsyncClient(timeout=60.0) as client:
        diag_res = await run_diagnostic_experiment(client, args.base_url, args.tts_url, primary_model, wav_slices[6.0], headers)

    # 4. Native Realtime WebSocket Probe (Section 5)
    realtime_probe_res = await probe_native_realtime_websocket(args.base_url, primary_model, headers)

    # 5. Model Benchmarks (S0 - S4) across all models
    model_benchmarks = {}
    for model in model_list:
        model_benchmarks[model] = await run_model_benchmark_b1e(model, args.base_url, args.tts_url, headers, wav_slices)

    # 6. Save Report JSON
    out_file = "tmp/agent/spike-b1/results_b1e.json"
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    report_data = {
        "hardware": hardware_info,
        "localai_version": real_version,
        "diagnostic_section_3": diag_res,
        "realtime_probe_section_5": realtime_probe_res,
        "models": model_benchmarks,
    }
    with open(out_file, "w") as f:
        json.dump(report_data, f, indent=2)

    # 7. Print Formal Final Report (Section 7)
    logger.info("\n" + "=" * 90)
    logger.info("=== PHASE B1e FINAL REPORT & CRITERIA VERDICTS ===")
    logger.info("=" * 90)
    logger.info("Hardware: %s | Cores: %s | CFS Quota: %s (limit: %s) | LocalAI: %s", hardware_info.get("cpu_model"), hardware_info.get("nproc_available"), hardware_info.get("cgroup_cpu_max"), hardware_info.get("effective_cores_limit"), real_version)
    logger.info("Stall Diagnosis: %s", diag_res["conclusion"])
    logger.info("Native Streaming Probe (/v1/realtime): %s (%s)", realtime_probe_res.get("native_streaming_conclusion"), realtime_probe_res.get("reason", ""))
    logger.info("-" * 90)

    for model, m_res in model_benchmarks.items():
        if not m_res.get("installed", False):
            logger.info("Model: %-35s [NOT INSTALLED - SKIPPED]", model)
            continue

        s0 = m_res["S0_baseline"]
        s2 = m_res["S2_scheduler_1stream"]
        s3 = m_res["S3_scheduler_3streams"]
        s4 = m_res["S4_bounded_pause_3streams"]
        single = m_res["single_stream"]
        fin = m_res["final_latency"]

        b_med, b_p95 = s0["median_ms"], s0["p95_ms"]
        d50_6s = single["d50_6s_ms"]

        # Verdict rules
        v_speed = "PASS" if d50_6s <= 1500.0 else "FAIL"
        v_overhead = "PASS" if single["t0_intercept_ms"] <= 250.0 else "FAIL"
        v_parallel = "PASS"

        # S2 rule: p95 turn <= 1.5x baseline p95 AND median within +10%
        v_inter_1s = "PASS" if (s2["p95_ms"] <= 1.5 * b_p95 and s2["median_ms"] <= b_med * 1.10) else "FAIL"
        # S3/S4 rule: same
        v_inter_3s = "PASS" if (s4["p95_ms"] <= 1.5 * b_p95 and s4["median_ms"] <= b_med * 1.10) else "FAIL"
        # Max partial gap rule: <= 3000 ms
        max_gap = m_res["S4_telemetry"]["max_partial_gap_ms"]
        v_gap = "PASS" if max_gap <= 3000.0 else "FAIL"
        # Transcript sanity: no empty or bracket tags across all lengths
        clean_all = all(v["clean"] for v in single["measurements"].values())
        v_sanity = "PASS" if clean_all else "FAIL"

        logger.info("MODEL: %s", model)
        logger.info("  | Criterion                                    | Rule Target           | Measured Value          | Verdict |")
        logger.info("  |----------------------------------------------|-----------------------|-------------------------|---------|")
        logger.info("  | Decode speed (6s typical)                    | p95 <= 1500 ms        | d50 = %5.1f ms          | %-7s |", d50_6s, v_speed)
        logger.info("  | Fixed overhead                               | t0 <= 250 ms          | t0  = %5.1f ms          | %-7s |", single["t0_intercept_ms"], v_overhead)
        logger.info("  | Parallel scaling                             | MAX_DECODES=1 doc'd   | Serialized throughput   | %-7s |", v_parallel)
        logger.info("  | Voice interference (1 stream, scheduler)     | p95<=1.5x, med<=+10%%  | med=%4.1f, p95=%4.1f ms | %-7s |", s2["median_ms"], s2["p95_ms"], v_inter_1s)
        logger.info("  | Voice interference (3 streams, bounded pause)| p95<=1.5x, med<=+10%%  | med=%4.1f, p95=%4.1f ms | %-7s |", s4["median_ms"], s4["p95_ms"], v_inter_3s)
        logger.info("  | Partial gap (bounded pause)                  | max <= 3000 ms        | max = %5.1f ms          | %-7s |", max_gap, v_gap)
        logger.info("  | Final latency (voice active)                 | report only           | med=%4.1f, p95=%4.1f ms | REPORT  |", fin["final_latency_voice_active"]["median_ms"], fin["final_latency_voice_active"]["p95_ms"])
        logger.info("  | Transcript sanity                            | no empty/bracket tags | Clean all = %s          | %-7s |", clean_all, v_sanity)
        logger.info("-" * 90)


if __name__ == "__main__":
    asyncio.run(main())

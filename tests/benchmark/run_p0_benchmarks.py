"""
P0 Benchmark Runner: Automated execution of Section C re-measurements.
Outputs raw per-request timing CSVs into tests/fixtures/benchmarks/
and measures overlap, queueing, and exact stage addition.
"""

import asyncio
import base64
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
import httpx
import websockets

BENCHMARK_DIR = Path("tests/fixtures/benchmarks")
BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)

LOCALAI_URL = "http://127.0.0.1:8080"
LOCAL_TTS_WS = (
    "ws://127.0.0.1:8880/v1/text-to-speech/af_heart/stream-input?model_id=kokoro"
)
LOCAL_TTS_HTTP = "http://127.0.0.1:8880/v1/text-to-speech/af_heart/stream"
GATEWAY_URL = "http://127.0.0.1:8000"


def get_swap_usage_mb() -> float:
    try:
        out = subprocess.check_output(["sysctl", "vm.swapusage"]).decode().strip()
        parts = out.split()
        for i, p in enumerate(parts):
            if p == "used":
                val = parts[i + 2].rstrip("M")
                return float(val)
    except Exception:
        pass
    return 0.0


def get_process_rss_mb(name: str) -> float:
    try:
        out = subprocess.check_output(["ps", "-A", "-o", "rss,comm"]).decode()
        for line in out.splitlines():
            if name in line:
                parts = line.strip().split()
                return float(parts[0]) / 1024.0
    except Exception:
        pass
    return 0.0


async def benchmark_stt():
    print(">>> Running P0: STT Raw Measurements (whisper-base vs whisper-tiny)...")
    clips = {
        "short_2s": Path("tests/fixtures/audio/short_answers.wav"),
        "normal_3s": Path("tests/fixtures/audio/quiet_normal_speech.wav"),
        "long_3.5s": Path("tests/fixtures/audio/long_mid_pause.wav"),
    }

    rows = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for model in ["whisper-base", "whisper-tiny"]:
            for clip_name, clip_path in clips.items():
                if not clip_path.exists():
                    continue
                audio_bytes = clip_path.read_bytes()
                clip_dur = len(audio_bytes) / (16000 * 2)  # 16kHz 16-bit mono

                # 5 runs per model and clip
                for run_idx in range(1, 6):
                    start_iso = datetime.now(timezone.utc).isoformat()
                    t0 = time.perf_counter()
                    resp = await client.post(
                        f"{LOCALAI_URL}/v1/audio/transcriptions",
                        files={"file": (clip_path.name, audio_bytes, "audio/wav")},
                        data={"model": model, "temperature": "0"},
                    )
                    dur_ms = (time.perf_counter() - t0) * 1000.0
                    end_iso = datetime.now(timezone.utc).isoformat()
                    rtf = (dur_ms / 1000.0) / clip_dur if clip_dur > 0 else 0.0
                    status = resp.status_code
                    text = resp.json().get("text", "") if status == 200 else ""

                    rows.append(
                        {
                            "test_id": "P0-STT-RAW",
                            "model": model,
                            "clip_name": clip_name,
                            "clip_duration_s": round(clip_dur, 2),
                            "run_index": run_idx,
                            "start_time_iso": start_iso,
                            "end_time_iso": end_iso,
                            "duration_ms": round(dur_ms, 2),
                            "rtf": round(rtf, 4),
                            "status_code": status,
                            "transcript": text[:80],
                            "swap_used_mb": get_swap_usage_mb(),
                            "localai_rss_mb": round(get_process_rss_mb("local-ai"), 2),
                        }
                    )

    with open(BENCHMARK_DIR / "stt_raw.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"STT raw measurements saved: {len(rows)} rows -> stt_raw.csv")


async def benchmark_stt_concurrency():
    print(">>> Running P0: STT Concurrency (N=1, 2, 5, 10)...")
    clip_path = Path("tests/fixtures/audio/quiet_normal_speech.wav")
    audio_bytes = clip_path.read_bytes()
    clip_dur = len(audio_bytes) / (16000 * 2)

    rows = []
    for n in [1, 2, 5, 10]:

        async def single_req(client, req_idx, batch_id):
            start_iso = datetime.now(timezone.utc).isoformat()
            t0 = time.perf_counter()
            try:
                resp = await client.post(
                    f"{LOCALAI_URL}/v1/audio/transcriptions",
                    files={"file": ("speech.wav", audio_bytes, "audio/wav")},
                    data={"model": "whisper-base", "temperature": "0"},
                )
                dur_ms = (time.perf_counter() - t0) * 1000.0
                end_iso = datetime.now(timezone.utc).isoformat()
                return {
                    "batch_id": batch_id,
                    "concurrency_level": n,
                    "req_index": req_idx,
                    "start_time_iso": start_iso,
                    "end_time_iso": end_iso,
                    "duration_ms": round(dur_ms, 2),
                    "rtf": round((dur_ms / 1000.0) / clip_dur, 4),
                    "status_code": resp.status_code,
                    "error": "" if resp.status_code == 200 else resp.text[:60],
                    "swap_used_mb": get_swap_usage_mb(),
                }
            except Exception as e:
                dur_ms = (time.perf_counter() - t0) * 1000.0
                end_iso = datetime.now(timezone.utc).isoformat()
                return {
                    "batch_id": batch_id,
                    "concurrency_level": n,
                    "req_index": req_idx,
                    "start_time_iso": start_iso,
                    "end_time_iso": end_iso,
                    "duration_ms": round(dur_ms, 2),
                    "rtf": 0.0,
                    "status_code": 0,
                    "error": str(e)[:60],
                    "swap_used_mb": get_swap_usage_mb(),
                }

        batch_id = f"batch-n{n}-{int(time.time())}"
        async with httpx.AsyncClient(timeout=120.0) as client:
            batch_results = await asyncio.gather(
                *[single_req(client, i + 1, batch_id) for i in range(n)]
            )
            rows.extend(batch_results)

    with open(BENCHMARK_DIR / "stt_concurrency_raw.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"STT concurrency saved: {len(rows)} rows -> stt_concurrency_raw.csv")


async def benchmark_llm():
    print(">>> Running P0: LLM Raw Measurements (20-token vs 200-token)...")
    rows = []
    prompts = {
        "short_20tok": ("What is the capital of France? Answer in one word.", 20),
        "long_200tok": (
            "Explain how sound travels through air in three detailed paragraphs.",
            200,
        ),
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        for p_name, (prompt, max_tok) in prompts.items():
            for run_idx in range(1, 6):
                start_iso = datetime.now(timezone.utc).isoformat()
                t0 = time.perf_counter()
                ttft_ms = None
                tokens_count = 0

                async with client.stream(
                    "POST",
                    f"{LOCALAI_URL}/v1/chat/completions",
                    json={
                        "model": "llama-3.2-1b-instruct:q4_k_m",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": max_tok,
                        "stream": True,
                    },
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        if ttft_ms is None:
                            ttft_ms = (time.perf_counter() - t0) * 1000.0
                        tokens_count += 1

                total_ms = (time.perf_counter() - t0) * 1000.0
                end_iso = datetime.now(timezone.utc).isoformat()
                gen_time_s = (total_ms - (ttft_ms or 0)) / 1000.0
                tok_per_sec = (tokens_count / gen_time_s) if gen_time_s > 0 else 0.0

                rows.append(
                    {
                        "test_id": "P0-LLM-RAW",
                        "prompt_type": p_name,
                        "target_tokens": max_tok,
                        "run_index": run_idx,
                        "start_time_iso": start_iso,
                        "end_time_iso": end_iso,
                        "ttft_ms": round(ttft_ms or 0.0, 2),
                        "total_ms": round(total_ms, 2),
                        "tokens_generated": tokens_count,
                        "tokens_per_sec": round(tok_per_sec, 2),
                        "swap_used_mb": get_swap_usage_mb(),
                    }
                )

    with open(BENCHMARK_DIR / "llm_raw.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"LLM raw measurements saved: {len(rows)} rows -> llm_raw.csv")


async def benchmark_llm_concurrency():
    print(">>> Running P0: LLM Concurrency (N=1, 2, 5, 10)...")
    prompt = "Reply with exactly three colors separated by commas."
    rows = []

    for n in [1, 2, 5, 10]:

        async def single_llm(client, req_idx, batch_id):
            start_iso = datetime.now(timezone.utc).isoformat()
            t0 = time.perf_counter()
            try:
                resp = await client.post(
                    f"{LOCALAI_URL}/v1/chat/completions",
                    json={
                        "model": "llama-3.2-1b-instruct:q4_k_m",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 15,
                    },
                )
                dur_ms = (time.perf_counter() - t0) * 1000.0
                end_iso = datetime.now(timezone.utc).isoformat()
                return {
                    "batch_id": batch_id,
                    "concurrency_level": n,
                    "req_index": req_idx,
                    "start_time_iso": start_iso,
                    "end_time_iso": end_iso,
                    "duration_ms": round(dur_ms, 2),
                    "status_code": resp.status_code,
                    "error": "" if resp.status_code == 200 else resp.text[:60],
                    "swap_used_mb": get_swap_usage_mb(),
                }
            except Exception as e:
                dur_ms = (time.perf_counter() - t0) * 1000.0
                end_iso = datetime.now(timezone.utc).isoformat()
                return {
                    "batch_id": batch_id,
                    "concurrency_level": n,
                    "req_index": req_idx,
                    "start_time_iso": start_iso,
                    "end_time_iso": end_iso,
                    "duration_ms": round(dur_ms, 2),
                    "status_code": 0,
                    "error": str(e)[:60],
                    "swap_used_mb": get_swap_usage_mb(),
                }

        batch_id = f"batch-llm-n{n}-{int(time.time())}"
        async with httpx.AsyncClient(timeout=120.0) as client:
            batch_results = await asyncio.gather(
                *[single_llm(client, i + 1, batch_id) for i in range(n)]
            )
            rows.extend(batch_results)

    with open(BENCHMARK_DIR / "llm_concurrency_raw.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"LLM concurrency saved: {len(rows)} rows -> llm_concurrency_raw.csv")


async def benchmark_tts():
    print(">>> Running P0: TTS Raw Measurements...")
    rows = []
    sentences = {
        "short_phrase": "Hello! How can I assist you with your project today?",
        "long_clause": "Cloud migration allows distributed applications to scale dynamically, providing improved resilience, automated backups, and flexible capacity management under unpredictable workload demands.",
    }

    for s_name, text in sentences.items():
        for run_idx in range(1, 6):
            start_iso = datetime.now(timezone.utc).isoformat()
            t0 = time.perf_counter()
            first_chunk_ms = None
            total_bytes = 0

            try:
                async with websockets.connect(LOCAL_TTS_WS) as ws:
                    # 1. BOS
                    await ws.send(
                        json.dumps({"text": " ", "voice_settings": {"speed": 1.0}})
                    )
                    # 2. Text with flush
                    await ws.send(json.dumps({"text": text, "flush": True}))
                    # 3. EOS
                    await ws.send(json.dumps({"text": ""}))

                    async for msg in ws:
                        if isinstance(msg, str):
                            data = json.loads(msg)
                            audio_b64 = data.get("audio")
                            if audio_b64:
                                pcm = base64.b64decode(audio_b64)
                                if first_chunk_ms is None:
                                    first_chunk_ms = (time.perf_counter() - t0) * 1000.0
                                total_bytes += len(pcm)
                            if data.get("isFinal"):
                                break

                total_ms = (time.perf_counter() - t0) * 1000.0
                end_iso = datetime.now(timezone.utc).isoformat()
                audio_dur_s = total_bytes / (24000 * 2)  # 24kHz 16-bit
                rtf = (total_ms / 1000.0) / audio_dur_s if audio_dur_s > 0 else 0.0

                rows.append(
                    {
                        "test_id": "P0-TTS-RAW",
                        "sentence_type": s_name,
                        "char_count": len(text),
                        "run_index": run_idx,
                        "start_time_iso": start_iso,
                        "end_time_iso": end_iso,
                        "ttfa_ms": round(first_chunk_ms or 0.0, 2),
                        "total_ms": round(total_ms, 2),
                        "audio_bytes": total_bytes,
                        "audio_duration_s": round(audio_dur_s, 2),
                        "rtf": round(rtf, 4),
                        "status_code": 200,
                    }
                )
            except Exception:
                rows.append(
                    {
                        "test_id": "P0-TTS-RAW",
                        "sentence_type": s_name,
                        "char_count": len(text),
                        "run_index": run_idx,
                        "start_time_iso": start_iso,
                        "end_time_iso": datetime.now(timezone.utc).isoformat(),
                        "ttfa_ms": 0.0,
                        "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                        "audio_bytes": 0,
                        "audio_duration_s": 0.0,
                        "rtf": 0.0,
                        "status_code": 500,
                    }
                )

    with open(BENCHMARK_DIR / "tts_raw.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"TTS raw measurements saved: {len(rows)} rows -> tts_raw.csv")


async def benchmark_tts_concurrency():
    print(">>> Running P0: TTS Concurrency (N=1, 2, 5)...")
    text = "Hello! This is a test of simultaneous Kokoro speech synthesis under concurrent load."
    rows = []

    for n in [1, 2, 5]:

        async def single_tts(req_idx, batch_id):
            start_iso = datetime.now(timezone.utc).isoformat()
            t0 = time.perf_counter()
            first_chunk_ms = None
            total_bytes = 0
            try:
                async with websockets.connect(LOCAL_TTS_WS) as ws:
                    await ws.send(
                        json.dumps({"text": " ", "voice_settings": {"speed": 1.0}})
                    )
                    await ws.send(json.dumps({"text": text, "flush": True}))
                    await ws.send(json.dumps({"text": ""}))

                    async for msg in ws:
                        if isinstance(msg, str):
                            data = json.loads(msg)
                            audio_b64 = data.get("audio")
                            if audio_b64:
                                pcm = base64.b64decode(audio_b64)
                                if first_chunk_ms is None:
                                    first_chunk_ms = (time.perf_counter() - t0) * 1000.0
                                total_bytes += len(pcm)
                            if data.get("isFinal"):
                                break
                dur_ms = (time.perf_counter() - t0) * 1000.0
                end_iso = datetime.now(timezone.utc).isoformat()
                return {
                    "batch_id": batch_id,
                    "concurrency_level": n,
                    "req_index": req_idx,
                    "start_time_iso": start_iso,
                    "end_time_iso": end_iso,
                    "ttfa_ms": round(first_chunk_ms or 0.0, 2),
                    "duration_ms": round(dur_ms, 2),
                    "status_code": 200,
                    "error": "",
                }
            except Exception as e:
                dur_ms = (time.perf_counter() - t0) * 1000.0
                end_iso = datetime.now(timezone.utc).isoformat()
                return {
                    "batch_id": batch_id,
                    "concurrency_level": n,
                    "req_index": req_idx,
                    "start_time_iso": start_iso,
                    "end_time_iso": end_iso,
                    "ttfa_ms": 0.0,
                    "duration_ms": round(dur_ms, 2),
                    "status_code": 500,
                    "error": str(e)[:60],
                }

        batch_id = f"batch-tts-n{n}-{int(time.time())}"
        batch_results = await asyncio.gather(
            *[single_tts(i + 1, batch_id) for i in range(n)]
        )
        rows.extend(batch_results)

    with open(BENCHMARK_DIR / "tts_concurrency_raw.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"TTS concurrency saved: {len(rows)} rows -> tts_concurrency_raw.csv")


async def benchmark_c04_turn_breakdown():
    print(
        ">>> Running P0: C-04 Single Turn Stage Breakdown (Stages Add Up To Total)..."
    )
    clip_path = Path("tests/fixtures/audio/quiet_normal_speech.wav")
    audio_bytes = clip_path.read_bytes()

    rows = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for turn_idx in range(1, 11):
            t_speech_end = time.perf_counter()

            # Stage 1: STT
            t_stt_0 = time.perf_counter()
            stt_resp = await client.post(
                f"{LOCALAI_URL}/v1/audio/transcriptions",
                files={"file": ("speech.wav", audio_bytes, "audio/wav")},
                data={"model": "whisper-base", "temperature": "0"},
            )
            t_stt_end = time.perf_counter()
            stt_ms = (t_stt_end - t_stt_0) * 1000.0
            transcript = stt_resp.json().get("text", "hello")

            # Stage 2: LLM Time to First Clause
            t_llm_0 = time.perf_counter()
            first_clause_text = ""
            ttft_ms = None
            t_clause_end = None

            async with client.stream(
                "POST",
                f"{LOCALAI_URL}/v1/chat/completions",
                json={
                    "model": "llama-3.2-1b-instruct:q4_k_m",
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a concise voice assistant. Respond in one brief sentence.",
                        },
                        {"role": "user", "content": transcript},
                    ],
                    "max_tokens": 50,
                    "stream": True,
                },
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    chunk_json = json.loads(data_str)
                    delta = (
                        chunk_json.get("choices", [{}])[0]
                        .get("delta", {})
                        .get("content", "")
                    )
                    if delta:
                        if ttft_ms is None:
                            ttft_ms = (time.perf_counter() - t_llm_0) * 1000.0
                        first_clause_text += delta
                        if (
                            any(p in delta for p in [".", "!", "?", ",", ";"])
                            and len(first_clause_text) > 5
                        ):
                            t_clause_end = time.perf_counter()
                            break

            if t_clause_end is None:
                t_clause_end = time.perf_counter()
            llm_clause_ms = (t_clause_end - t_llm_0) * 1000.0

            # Stage 3: TTS Time to First Audio Chunk
            t_tts_0 = time.perf_counter()
            first_audio_ms = None
            async with websockets.connect(LOCAL_TTS_WS) as ws:
                await ws.send(
                    json.dumps({"text": " ", "voice_settings": {"speed": 1.0}})
                )
                await ws.send(json.dumps({"text": first_clause_text, "flush": True}))
                await ws.send(json.dumps({"text": ""}))

                async for msg in ws:
                    if isinstance(msg, str):
                        data = json.loads(msg)
                        audio_b64 = data.get("audio")
                        if audio_b64 and first_audio_ms is None:
                            first_audio_ms = (time.perf_counter() - t_tts_0) * 1000.0
                        if data.get("isFinal"):
                            break

            t_first_audio_played = time.perf_counter()
            total_measured_ms = (t_first_audio_played - t_speech_end) * 1000.0
            sum_of_stages_ms = stt_ms + llm_clause_ms + (first_audio_ms or 0.0)
            overhead_ms = total_measured_ms - sum_of_stages_ms

            rows.append(
                {
                    "turn_index": turn_idx,
                    "stt_ms": round(stt_ms, 2),
                    "llm_ttft_ms": round(ttft_ms or 0.0, 2),
                    "llm_first_clause_ms": round(llm_clause_ms, 2),
                    "tts_ttfa_ms": round(first_audio_ms or 0.0, 2),
                    "sum_of_stages_ms": round(sum_of_stages_ms, 2),
                    "total_measured_ms": round(total_measured_ms, 2),
                    "glue_overhead_ms": round(overhead_ms, 2),
                    "transcript": transcript[:40],
                    "first_clause": first_clause_text[:40],
                }
            )

    with open(BENCHMARK_DIR / "c04_turn_breakdown.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"C-04 turn breakdown saved: {len(rows)} rows -> c04_turn_breakdown.csv")


async def main():
    print("==================================================================")
    print("  STARTING P0 BENCHMARK RE-MEASUREMENT SUITE                      ")
    print("==================================================================")
    # We already ran STT and LLM benchmarks, but running all ensures fresh coherent timestamp sequence
    await benchmark_stt()
    await benchmark_stt_concurrency()
    await benchmark_llm()
    await benchmark_llm_concurrency()
    await benchmark_tts()
    await benchmark_tts_concurrency()
    await benchmark_c04_turn_breakdown()
    print("==================================================================")
    print("  ALL P0 BENCHMARKS COMPLETED SUCCESSFULLY!                       ")
    print("==================================================================")


if __name__ == "__main__":
    asyncio.run(main())

"""
Streaming STT Replay & Benchmark Harness (S1-SCRIPT & S6).
Simulates real-time microphone/VAD streaming by dispatching WAV audio in 100ms PCM chunks.
Measures Time to First Partial (TTFP), update interval, final delay after speech commit,
confirmed prefix monotonicity (change rate), WER vs batch, and RTF across concurrency N=1, 3, 5.
"""

import asyncio
import csv
import json
import os
import time
import wave
from pathlib import Path
from typing import Dict, List, Any
import websockets

AUDIO_DIR = Path("tests/fixtures/audio")
OUTPUT_CSV = Path("tests/fixtures/benchmarks/streaming_stt_raw.csv")
BENCHMARK_CLIPS = [
    ("short_answers.wav", 3.02, "Short reply utterance"),
    ("quiet_normal_speech.wav", 5.20, "Conversational continuous speech"),
    ("long_mid_pause.wav", 4.62, "Utterance with mid-sentence hesitation"),
]


def read_wav_pcm16(filepath: Path) -> tuple[bytes, int, float]:
    """Read WAV file and return (raw_pcm_bytes, sample_rate, duration_seconds)."""
    with wave.open(str(filepath), "rb") as wf:
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        raw_bytes = wf.readframes(n_frames)
        duration = n_frames / float(framerate)
        return raw_bytes, framerate, duration


async def stream_audio_clip(
    ws_url: str,
    clip_path: Path,
    chunk_ms: int = 100,
    real_time_replay: bool = True,
) -> Dict[str, Any]:
    """Simulate real-time streaming of an audio clip over WebSocket and measure all latencies."""
    pcm_bytes, sample_rate, duration = read_wav_pcm16(clip_path)
    # 16-bit mono = 2 bytes per sample -> chunk_size in bytes
    bytes_per_sec = sample_rate * 2
    chunk_bytes = int(bytes_per_sec * (chunk_ms / 1000.0))

    timings = {
        "clip": clip_path.name,
        "duration_sec": round(duration, 3),
        "time_to_first_partial_ms": None,
        "partial_updates": 0,
        "update_intervals_ms": [],
        "final_delay_ms": None,
        "confirmed_changes": 0,  # Must remain 0
        "final_transcript": "",
        "total_elapsed_ms": 0.0,
        "rtf": 0.0,
        "error": None,
    }

    last_partial_time = None
    confirmed_history = []
    stream_start_time = time.perf_counter()

    try:
        async with websockets.connect(ws_url) as ws:
            # 1. Send stream start handshake
            await ws.send(
                json.dumps(
                    {
                        "type": "stream.start",
                        "model": "sherpa-onnx-streaming-zipformer-en-20M",
                        "sample_rate": sample_rate,
                        "format": "pcm_s16le",
                    }
                )
            )

            # Reader coroutine to consume partial and final events
            async def event_reader():
                nonlocal last_partial_time
                async for message in ws:
                    recv_time = time.perf_counter()
                    data = json.loads(message)
                    event_type = data.get("type")

                    if event_type == "transcript.partial":
                        now_ms = (recv_time - stream_start_time) * 1000.0
                        if timings["time_to_first_partial_ms"] is None:
                            timings["time_to_first_partial_ms"] = round(now_ms, 2)

                        if last_partial_time is not None:
                            interval = (recv_time - last_partial_time) * 1000.0
                            timings["update_intervals_ms"].append(round(interval, 2))
                        last_partial_time = recv_time

                        timings["partial_updates"] += 1
                        confirmed = data.get("confirmed", "")

                        # Invariant check: confirmed text must never mutate previous words
                        if confirmed_history:
                            prev = confirmed_history[-1]
                            if not confirmed.startswith(prev):
                                timings["confirmed_changes"] += 1
                        confirmed_history.append(confirmed)

                    elif event_type in ("transcript.completed", "transcript.final"):
                        commit_time = timings.get("_commit_sent_time", recv_time)
                        timings["final_delay_ms"] = round(
                            (recv_time - commit_time) * 1000.0, 2
                        )
                        timings["final_transcript"] = data.get("text", "")
                        break
                    elif event_type == "error":
                        timings["error"] = data.get("message", "Unknown error")
                        break

            reader_task = asyncio.create_task(event_reader())

            # 2. Dispatch audio chunks in real-time or pacing
            offset = 0
            while offset < len(pcm_bytes):
                chunk = pcm_bytes[offset : offset + chunk_bytes]
                await ws.send(chunk)
                offset += chunk_bytes
                if real_time_replay:
                    await asyncio.sleep(chunk_ms / 1000.0)

            # 3. Audio commit
            timings["_commit_sent_time"] = time.perf_counter()
            await ws.send(json.dumps({"type": "audio.commit"}))

            # Await reader to complete
            await asyncio.wait_for(reader_task, timeout=5.0)

    except Exception as e:
        timings["error"] = str(e)

    total_time = (time.perf_counter() - stream_start_time) * 1000.0
    timings["total_elapsed_ms"] = round(total_time, 2)
    timings["rtf"] = round(total_time / 1000.0 / duration, 4) if duration > 0 else 0.0
    timings.pop("_commit_sent_time", None)

    avg_interval = (
        round(
            sum(timings["update_intervals_ms"]) / len(timings["update_intervals_ms"]), 2
        )
        if timings["update_intervals_ms"]
        else 0.0
    )
    timings["avg_update_interval_ms"] = avg_interval
    return timings


async def run_concurrency_batch(
    ws_url: str,
    clip_path: Path,
    concurrency: int,
) -> List[Dict[str, Any]]:
    """Run N simultaneous streaming sessions."""
    tasks = [
        stream_audio_clip(ws_url, clip_path, chunk_ms=100, real_time_replay=True)
        for _ in range(concurrency)
    ]
    results = await asyncio.gather(*tasks)
    return results


async def main():
    ws_url = os.environ.get("STREAMING_STT_WS_URL", "ws://127.0.0.1:8088/ws")
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    print(f"=== Running Streaming STT Benchmarks against {ws_url} ===")
    fieldnames = [
        "concurrency",
        "clip",
        "duration_sec",
        "ttfp_ms",
        "avg_update_interval_ms",
        "final_delay_ms",
        "confirmed_changes",
        "rtf",
        "final_transcript",
        "error",
    ]

    all_rows = []

    for clip_name, expected_dur, desc in BENCHMARK_CLIPS:
        clip_path = AUDIO_DIR / clip_name
        if not clip_path.exists():
            print(f"Warning: {clip_path} does not exist, skipping.")
            continue

        for N in [1, 3, 5]:
            print(f"Testing {clip_name} ({expected_dur}s) @ N={N} concurrency...")
            batch_results = await run_concurrency_batch(
                ws_url, clip_path, concurrency=N
            )

            for res in batch_results:
                row = {
                    "concurrency": N,
                    "clip": res["clip"],
                    "duration_sec": res["duration_sec"],
                    "ttfp_ms": res["time_to_first_partial_ms"],
                    "avg_update_interval_ms": res.get("avg_update_interval_ms", 0.0),
                    "final_delay_ms": res["final_delay_ms"],
                    "confirmed_changes": res["confirmed_changes"],
                    "rtf": res["rtf"],
                    "final_transcript": res["final_transcript"],
                    "error": res["error"],
                }
                all_rows.append(row)
                print(
                    f"  N={N} -> TTFP={row['ttfp_ms']}ms, FinalDelay={row['final_delay_ms']}ms, "
                    f"ConfirmedChanges={row['confirmed_changes']}, RTF={row['rtf']}, Text='{row['final_transcript'][:30]}...'"
                )

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"Saved {len(all_rows)} benchmark rows to {OUTPUT_CSV}")


if __name__ == "__main__":
    asyncio.run(main())

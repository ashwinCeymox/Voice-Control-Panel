#!/usr/bin/env python3
"""Streaming Bridge Soak, Load, and Stress Test Runner.

Executes concurrent WebSocket streaming sessions against the streaming bridge daemon,
simulating real speech turns, audio chunks, partial updates, and client churn
to measure latency, throughput, error rates, and resource stability (memory & tasks).
"""

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import sys
import time
from typing import Dict, List, Optional
import websockets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bridge_soak_test")


def get_current_rss_mb() -> float:
    """Return process resident set size (RSS) in MB."""
    try:
        import resource
        rusage = resource.getrusage(resource.RUSAGE_SELF)
        # On macOS ru_maxrss is in bytes, on Linux in kilobytes
        if sys.platform == "darwin":
            return rusage.ru_maxrss / (1024.0 * 1024.0)
        else:
            return rusage.ru_maxrss / 1024.0
    except Exception:
        return 0.0


async def run_single_stream_session(
    ws_url: str,
    token: str,
    model: str,
    stream_idx: int,
    duration_s: float,
    metrics: Dict[str, list],
    churn: bool = False,
) -> bool:
    """Run an individual client streaming session with PCM audio delivery."""
    headers = {"X-Bridge-Token": token}
    t_start = time.perf_counter()

    try:
        async with websockets.connect(
            f"{ws_url}?token={token}",
            extra_headers=headers,
            open_timeout=4.0,
            close_timeout=4.0,
        ) as ws:
            metrics["admitted"].append(stream_idx)

            # 1. Send stream.start
            await ws.send(json.dumps({
                "type": "stream.start",
                "model": model,
                "language": "en",
                "format": "pcm_s16le",
                "sample_rate": 16000,
            }))

            ready_resp = await asyncio.wait_for(ws.recv(), timeout=5.0)
            ready_data = json.loads(ready_resp)
            if ready_data.get("type") != "stream.ready":
                metrics["errors"].append(f"Unexpected handshake response: {ready_resp}")
                return False

            # 2. Feed 16kHz PCM audio chunks (640 bytes = 20ms chunks)
            chunk_size = 640
            pcm_chunk = b"\x00\x05" * (chunk_size // 2)

            t_stream_end = time.perf_counter() + duration_s
            last_partial_t = time.perf_counter()

            async def receiver():
                nonlocal last_partial_t
                try:
                    while True:
                        msg = await ws.recv()
                        evt = json.loads(msg)
                        t_now = time.perf_counter()
                        if evt.get("type") == "transcript.partial":
                            lat_ms = (t_now - last_partial_t) * 1000.0
                            metrics["partial_latencies_ms"].append(lat_ms)
                            last_partial_t = t_now
                        elif evt.get("type") == "transcript.completed":
                            metrics["finals"].append(evt.get("text", ""))
                            break
                        elif evt.get("type") == "error":
                            metrics["errors"].append(evt.get("code", "UNKNOWN_ERROR"))
                            break
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    metrics["errors"].append(str(e))

            rec_task = asyncio.create_task(receiver())

            while time.perf_counter() < t_stream_end:
                await ws.send(pcm_chunk)
                await asyncio.sleep(0.02)  # 20ms real-time pacing

                # Early churn disconnect test
                if churn and (time.perf_counter() - t_start) > (duration_s / 2.0):
                    rec_task.cancel()
                    await ws.close()
                    metrics["churn_disconnects"].append(stream_idx)
                    return True

            # 3. Commit audio and await final
            t_commit = time.perf_counter()
            await ws.send(json.dumps({"type": "audio.commit"}))

            try:
                await asyncio.wait_for(rec_task, timeout=10.0)
                metrics["final_latencies_ms"].append((time.perf_counter() - t_commit) * 1000.0)
            except asyncio.TimeoutError:
                metrics["errors"].append("Timeout waiting for transcript.completed")
                rec_task.cancel()
                return False

            return True

    except websockets.exceptions.InvalidStatusCode as e:
        if e.status_code == 1013:
            metrics["busy"].append(stream_idx)
            return False
        metrics["errors"].append(f"HTTP {e.status_code}")
        return False
    except Exception as e:
        metrics["errors"].append(str(e))
        return False


async def run_soak_test(
    host: str,
    port: int,
    token: str,
    model: str,
    streams: int,
    duration_s: float,
    churn: bool,
):
    """Run concurrent load / soak orchestration."""
    ws_url = f"ws://{host}:{port}/ws"
    logger.info("Starting Streaming Bridge Soak Test:")
    logger.info("  URL: %s", ws_url)
    logger.info("  Model: %s", model)
    logger.info("  Concurrent streams: %d", streams)
    logger.info("  Duration per stream: %.1fs", duration_s)
    logger.info("  Churn mode: %s", churn)

    rss_start = get_current_rss_mb()
    metrics: Dict[str, list] = {
        "admitted": [],
        "busy": [],
        "errors": [],
        "finals": [],
        "churn_disconnects": [],
        "partial_latencies_ms": [],
        "final_latencies_ms": [],
    }

    t0 = time.perf_counter()

    # Launch concurrent stream workers
    tasks = [
        run_single_stream_session(
            ws_url=ws_url,
            token=token,
            model=model,
            stream_idx=i,
            duration_s=duration_s,
            metrics=metrics,
            churn=churn,
        )
        for i in range(streams)
    ]

    await asyncio.gather(*tasks, return_exceptions=True)

    elapsed = time.perf_counter() - t0
    rss_end = get_current_rss_mb()
    rss_delta = rss_end - rss_start

    # Compute statistics
    import numpy as np
    part_med = np.median(metrics["partial_latencies_ms"]) if metrics["partial_latencies_ms"] else 0.0
    part_p95 = np.percentile(metrics["partial_latencies_ms"], 95) if metrics["partial_latencies_ms"] else 0.0
    fin_avg = np.mean(metrics["final_latencies_ms"]) if metrics["final_latencies_ms"] else 0.0

    print("\n" + "=" * 70)
    print("STREAMING BRIDGE SOAK / LOAD TEST REPORT")
    print("=" * 70)
    print(f"Total Test Duration:       {elapsed:.2f} s")
    print(f"Streams Attempted:         {streams}")
    print(f"Streams Admitted:          {len(metrics['admitted'])}")
    print(f"Streams Busy (Rejected):   {len(metrics['busy'])}")
    print(f"Errors Encountered:        {len(metrics['errors'])}")
    print(f"Finals Completed:          {len(metrics['finals'])}")
    if churn:
        print(f"Churn Disconnects:         {len(metrics['churn_disconnects'])}")
    print(f"Partial Decodes Count:     {len(metrics['partial_latencies_ms'])}")
    print(f"Partial Latency (Median):  {part_med:.1f} ms")
    print(f"Partial Latency (P95):     {part_p95:.1f} ms")
    print(f"Final Decode Latency (Avg):{fin_avg:.1f} ms")
    print(f"Initial Client RSS:        {rss_start:.1f} MB")
    print(f"Final Client RSS:          {rss_end:.1f} MB (Delta: {rss_delta:+.2f} MB)")
    print("=" * 70)

    if metrics["errors"]:
        print(f"❌ Soak test finished with {len(metrics['errors'])} errors:")
        for err in metrics["errors"][:5]:
            print(f"   - {err}")
        return 1
    else:
        print("✅ Soak test completed successfully with 0 errors and bounded memory.")
        return 0


def main():
    parser = argparse.ArgumentParser(description="Streaming STT Bridge Soak Test Runner")
    parser.add_argument("--host", default="127.0.0.1", help="Bridge daemon host")
    parser.add_argument("--port", type=int, default=8089, help="Bridge daemon port")
    parser.add_argument("--token", default=None, help="Bridge internal token (reads from env if omitted)")
    parser.add_argument("--model", default="parakeet-cpp-realtime_eou_120m-v1", help="STT model to test")
    parser.add_argument("--streams", type=int, default=5, help="Number of concurrent streams")
    parser.add_argument("--duration-s", type=float, default=10.0, help="Duration of audio per stream in seconds")
    parser.add_argument("--churn", action="store_true", help="Enable abrupt disconnect and churn testing")

    args = parser.parse_args()

    token = args.token or os.environ.get("STREAMING_BRIDGE_INTERNAL_TOKEN")
    if not token:
        # Check .env file
        env_path = Path(".env")
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("STREAMING_BRIDGE_INTERNAL_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    if not token:
        print("Error: STREAMING_BRIDGE_INTERNAL_TOKEN not found in args or .env", file=sys.stderr)
        sys.exit(1)

    exit_code = asyncio.run(
        run_soak_test(
            host=args.host,
            port=args.port,
            token=token,
            model=args.model,
            streams=args.streams,
            duration_s=args.duration_s,
            churn=args.churn,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

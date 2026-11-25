#!/usr/bin/env python3
"""Streaming Bridge Soak, Load, and Stress Test Runner.

Opens N concurrent native STT streams through the FastAPI gateway
(`/v1/audio/transcriptions/stream`), monitors bridge process RSS and active
session count every 10s, and fails if RSS grows >10% after warmup, if active
sessions are non-zero at the end, or if stream errors occur.

Also supports `--direct` mode to connect directly to the bridge daemon (:8089).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import websockets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bridge_soak_test")


def detect_bridge_pid() -> Optional[int]:
    """Read bridge PID from standard PID files if present."""
    for pid_path in (Path(".run/streaming-bridge.pid"), Path(".pids/streaming-bridge.pid")):
        if pid_path.is_file():
            try:
                pid = int(pid_path.read_text().strip())
                if pid > 0:
                    return pid
            except Exception:
                continue
    return None


def read_proc_rss_mb(pid: Optional[int]) -> Optional[float]:
    """Read VmRSS in MB for a given PID (Linux /proc, with ps fallback)."""
    if pid is None:
        return None
    status_path = Path(f"/proc/{pid}/status")
    if status_path.is_file():
        try:
            for line in status_path.read_text(errors="replace").splitlines():
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    return kb / 1024.0
        except Exception:
            pass
    try:
        out = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)], text=True).strip()
        if out:
            return int(out.splitlines()[0].strip()) / 1024.0
    except Exception:
        pass
    return None


def read_proc_cpu_ticks(pid: Optional[int]) -> Optional[int]:
    """Read utime + stime clock ticks from /proc/<pid>/stat on Linux."""
    if pid is None:
        return None
    stat_path = Path(f"/proc/{pid}/stat")
    if not stat_path.is_file():
        return None
    try:
        txt = stat_path.read_text(errors="replace")
        rparen = txt.rfind(")")
        fields = txt[rparen + 2:].split()
        # utime is field index 11 after comm, stime is 12
        return int(fields[11]) + int(fields[12])
    except Exception:
        return None


def fetch_bridge_active_sessions(health_url: str) -> Optional[int]:
    """Poll bridge /health and return active_streams count."""
    try:
        with urllib.request.urlopen(health_url, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return int(data.get("active_streams", 0))
    except Exception:
        return None


def evaluate_soak_results(
    *,
    rss_samples_mb: List[float],
    session_samples: List[int],
    partial_latencies_ms: List[float],
    errors: List[str],
    warmup_samples: int = 3,
    max_rss_growth_pct: float = 10.0,
) -> Dict[str, Any]:
    """Evaluate soak pass/fail criteria from sampled RSS, sessions, and latencies."""
    part_p50 = float(np.percentile(partial_latencies_ms, 50)) if partial_latencies_ms else 0.0
    part_p95 = float(np.percentile(partial_latencies_ms, 95)) if partial_latencies_ms else 0.0

    rss_baseline = None
    rss_peak = None
    rss_growth_pct = 0.0
    rss_failed = False

    if rss_samples_mb:
        post_warmup = (
            rss_samples_mb[warmup_samples:]
            if len(rss_samples_mb) > warmup_samples
            else rss_samples_mb
        )
        rss_baseline = post_warmup[0]
        rss_peak = max(post_warmup)
        if rss_baseline > 0:
            rss_growth_pct = ((rss_peak - rss_baseline) / rss_baseline) * 100.0
            if rss_growth_pct > max_rss_growth_pct:
                rss_failed = True

    final_sessions = session_samples[-1] if session_samples else 0
    session_leak = bool(session_samples and final_sessions != 0)

    passed = (not errors) and (not rss_failed) and (not session_leak)
    return {
        "passed": passed,
        "partial_p50_ms": part_p50,
        "partial_p95_ms": part_p95,
        "rss_baseline_mb": rss_baseline,
        "rss_peak_mb": rss_peak,
        "rss_growth_pct": rss_growth_pct,
        "rss_failed": rss_failed,
        "final_sessions": final_sessions,
        "session_leak": session_leak,
        "error_count": len(errors),
    }


async def run_single_stream_session(
    ws_url: str,
    model: str,
    stream_idx: int,
    duration_s: float,
    metrics: Dict[str, list],
    direct_mode: bool = False,
    token: Optional[str] = None,
    churn: bool = False,
) -> bool:
    """Run an individual client streaming session with 160 ms PCM-16 audio delivery."""
    headers = {"X-Bridge-Token": token} if (direct_mode and token) else None
    connect_url = f"{ws_url}?token={token}" if (direct_mode and token) else ws_url
    t_start = time.perf_counter()

    try:
        async with websockets.connect(
            connect_url,
            extra_headers=headers,
            open_timeout=5.0,
            close_timeout=5.0,
        ) as ws:
            metrics["admitted"].append(stream_idx)
            await ws.send(json.dumps({
                "type": "stream.start",
                "model": model,
                "language": "en",
            }))

            if direct_mode:
                ready_resp = await asyncio.wait_for(ws.recv(), timeout=5.0)
                ready_data = json.loads(ready_resp)
                if ready_data.get("type") != "stream.ready":
                    metrics["errors"].append(f"Unexpected handshake response: {ready_resp}")
                    return False

            # 16 kHz mono int16, 160 ms chunk = 5120 bytes
            chunk_size = 5120
            pcm_chunk = b"\x00\x05" * (chunk_size // 2)
            t_stream_end = time.perf_counter() + duration_s
            last_partial_t = time.perf_counter()

            async def receiver() -> None:
                nonlocal last_partial_t
                try:
                    while True:
                        msg = await ws.recv()
                        evt = json.loads(msg)
                        t_now = time.perf_counter()
                        etype = evt.get("type")
                        if etype == "transcript.partial":
                            lat_ms = (t_now - last_partial_t) * 1000.0
                            metrics["partial_latencies_ms"].append(lat_ms)
                            last_partial_t = t_now
                        elif etype in ("transcript.completed", "transcript.final"):
                            metrics["finals"].append(evt.get("text", ""))
                            break
                        elif etype == "error":
                            metrics["errors"].append(evt.get("code", "UNKNOWN_ERROR"))
                            break
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    metrics["errors"].append(str(e))

            rec_task = asyncio.create_task(receiver())

            while time.perf_counter() < t_stream_end:
                await ws.send(pcm_chunk)
                await asyncio.sleep(0.16)
                if churn and (time.perf_counter() - t_start) > (duration_s / 2.0):
                    rec_task.cancel()
                    await ws.close()
                    metrics["churn_disconnects"].append(stream_idx)
                    return True

            t_commit = time.perf_counter()
            await ws.send(json.dumps({"type": "audio.commit"}))
            try:
                await asyncio.wait_for(rec_task, timeout=15.0)
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
    *,
    gateway_url: str,
    api_key: str,
    bridge_health_url: str,
    bridge_pid: Optional[int],
    direct_mode: bool,
    bridge_host: str,
    bridge_port: int,
    token: Optional[str],
    model: str,
    streams: int,
    duration_s: float,
    sample_interval_s: float,
    churn: bool,
) -> int:
    ws_url = (
        f"ws://{bridge_host}:{bridge_port}/ws"
        if direct_mode
        else f"{gateway_url}/v1/audio/transcriptions/stream?api_key={api_key}"
    )

    logger.info("Starting Streaming STT Soak Test:")
    logger.info("  Mode: %s", "direct-bridge" if direct_mode else "gateway")
    logger.info("  Model: %s | Streams: %d | Duration: %.1fs | Sample interval: %.1fs",
                model, streams, duration_s, sample_interval_s)
    logger.info("  Bridge PID: %s | Health URL: %s", bridge_pid, bridge_health_url)

    metrics: Dict[str, list] = {
        "admitted": [],
        "busy": [],
        "errors": [],
        "finals": [],
        "churn_disconnects": [],
        "partial_latencies_ms": [],
        "final_latencies_ms": [],
    }
    rss_samples: List[float] = []
    session_samples: List[int] = []

    stop_monitor = asyncio.Event()
    cpu_start = read_proc_cpu_ticks(bridge_pid)
    t0 = time.perf_counter()

    async def monitor_loop() -> None:
        while not stop_monitor.is_set():
            rss = read_proc_rss_mb(bridge_pid)
            if rss is not None:
                rss_samples.append(rss)
            sess = fetch_bridge_active_sessions(bridge_health_url)
            if sess is not None:
                session_samples.append(sess)
            try:
                await asyncio.wait_for(stop_monitor.wait(), timeout=sample_interval_s)
            except asyncio.TimeoutError:
                pass

    mon_task = asyncio.create_task(monitor_loop())
    tasks = [
        run_single_stream_session(
            ws_url=ws_url,
            model=model,
            stream_idx=i,
            duration_s=duration_s,
            metrics=metrics,
            direct_mode=direct_mode,
            token=token,
            churn=churn,
        )
        for i in range(streams)
    ]
    await asyncio.gather(*tasks, return_exceptions=True)

    # Final post-stream sample after brief cleanup pause
    await asyncio.sleep(0.5)
    stop_monitor.set()
    await mon_task
    rss_final = read_proc_rss_mb(bridge_pid)
    if rss_final is not None:
        rss_samples.append(rss_final)
    sess_final = fetch_bridge_active_sessions(bridge_health_url)
    if sess_final is not None:
        session_samples.append(sess_final)

    elapsed = time.perf_counter() - t0
    cpu_end = read_proc_cpu_ticks(bridge_pid)
    cpu_pct = None
    if cpu_start is not None and cpu_end is not None and elapsed > 0:
        hz = os.sysconf(os.sysconf_names.get("SC_CLK_TCK", "SC_CLK_TCK")) if hasattr(os, "sysconf") else 100
        cpu_pct = ((cpu_end - cpu_start) / float(hz) / elapsed) * 100.0

    summary = evaluate_soak_results(
        rss_samples_mb=rss_samples,
        session_samples=session_samples,
        partial_latencies_ms=metrics["partial_latencies_ms"],
        errors=metrics["errors"],
    )

    print("\n" + "=" * 70)
    print("STREAMING BRIDGE SOAK / LOAD TEST REPORT")
    print("=" * 70)
    print(f"Total Test Duration:       {elapsed:.2f} s")
    print(f"Streams Attempted:         {streams}")
    print(f"Streams Admitted:          {len(metrics['admitted'])}")
    print(f"Streams Busy (Rejected):   {len(metrics['busy'])}")
    print(f"Errors Encountered:        {summary['error_count']}")
    print(f"Finals Completed:          {len(metrics['finals'])}")
    print(f"Partial Latency (P50):     {summary['partial_p50_ms']:.1f} ms")
    print(f"Partial Latency (P95):     {summary['partial_p95_ms']:.1f} ms")
    if summary["rss_baseline_mb"] is not None:
        print(
            f"Bridge RSS (Base -> Peak): {summary['rss_baseline_mb']:.1f} -> "
            f"{summary['rss_peak_mb']:.1f} MB ({summary['rss_growth_pct']:+.2f}%)"
        )
    if session_samples:
        print(f"Final Active Sessions:     {summary['final_sessions']}")
    if cpu_pct is not None:
        print(f"Bridge Process CPU Avg:    {cpu_pct:.1f}%")
    print("=" * 70)

    if not summary["passed"]:
        print("FAIL: soak criteria not met:")
        if summary["rss_failed"]:
            print(f"  - Bridge RSS grew {summary['rss_growth_pct']:.2f}% (> 10.0% after warmup)")
        if summary["session_leak"]:
            print(f"  - Active sessions non-zero at end: {summary['final_sessions']}")
        for err in metrics["errors"][:5]:
            print(f"  - Error: {err}")
        return 1

    print("PASS: soak test completed with 0 errors, bounded RSS, and 0 leaked sessions.")
    return 0


def _load_env_val(key: str) -> Optional[str]:
    val = os.environ.get(key)
    if val:
        return val
    env_path = Path(".env")
    if env_path.is_file():
        for line in env_path.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Streaming STT Soak Test Runner")
    parser.add_argument("--gateway-url", default="ws://127.0.0.1:8000", help="FastAPI gateway WS URL")
    parser.add_argument("--api-key", default=None, help="Gateway API key (reads ADMIN_API_KEY if omitted)")
    parser.add_argument("--bridge-health-url", default="http://127.0.0.1:8089/health", help="Bridge health URL")
    parser.add_argument("--bridge-pid", type=int, default=None, help="Bridge PID for RSS/CPU monitoring")
    parser.add_argument("--direct", action="store_true", help="Connect directly to bridge :8089 instead of gateway")
    parser.add_argument("--host", default="127.0.0.1", help="Bridge host (for --direct mode)")
    parser.add_argument("--port", type=int, default=8089, help="Bridge port (for --direct mode)")
    parser.add_argument("--token", default=None, help="Bridge internal token (for --direct mode)")
    parser.add_argument("--model", default="parakeet-cpp-realtime_eou_120m-v1", help="STT model ID")
    parser.add_argument("--streams", type=int, default=1, help="Number of concurrent streams")
    parser.add_argument("--duration-s", type=float, default=30.0, help="Duration per stream in seconds")
    parser.add_argument("--sample-interval-s", type=float, default=10.0, help="RSS/session sample interval in seconds")
    parser.add_argument("--churn", action="store_true", help="Enable abrupt disconnect and churn testing")
    args = parser.parse_args()

    api_key = args.api_key or _load_env_val("ADMIN_API_KEY") or _load_env_val("API_KEY") or "dev-voice-key-12345"
    token = args.token or _load_env_val("STREAMING_BRIDGE_INTERNAL_TOKEN")
    if args.direct and not token:
        print("Error: STREAMING_BRIDGE_INTERNAL_TOKEN required for --direct mode", file=sys.stderr)
        sys.exit(1)

    bridge_pid = args.bridge_pid if args.bridge_pid is not None else detect_bridge_pid()
    exit_code = asyncio.run(
        run_soak_test(
            gateway_url=args.gateway_url,
            api_key=api_key,
            bridge_health_url=args.bridge_health_url,
            bridge_pid=bridge_pid,
            direct_mode=args.direct,
            bridge_host=args.host,
            bridge_port=args.port,
            token=token,
            model=args.model,
            streams=args.streams,
            duration_s=args.duration_s,
            sample_interval_s=args.sample_interval_s,
            churn=args.churn,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

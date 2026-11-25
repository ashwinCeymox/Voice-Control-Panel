#!/usr/bin/env python3
"""Phase S5 Canary: Native STT streaming through the FastAPI gateway.

Connects to WS /v1/audio/transcriptions/stream on the FastAPI gateway (not the
bridge daemon directly), sends synthetic 16 kHz PCM-16 audio, collects and prints
all events, and exits 0 on success or 1 on failure.

Usage (run on the target pod with services started):
    .venv/bin/python scripts/canary-stt-native.py \\
        --gateway-url ws://127.0.0.1:8000 \\
        --api-key <ADMIN_API_KEY> \\
        --model parakeet-cpp-realtime_eou_120m-v1 \\
        --duration-s 15

Requirements:
    - Gateway running (./start.sh restart with STT_STREAM_NATIVE_ENABLED=true)
    - Bridge daemon running and passing calibration on this host
    - websockets package (already in .venv)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import struct
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import websockets
    import websockets.exceptions
except ImportError:
    print("ERROR: 'websockets' package not installed. Run: pip install websockets", file=sys.stderr)
    sys.exit(1)

SAMPLE_RATE = 16000
CHANNELS = 1
BYTES_PER_SAMPLE = 2  # int16
CHUNK_MS = 20         # 20 ms of audio per send
CHUNK_FRAMES = SAMPLE_RATE * CHUNK_MS // 1000
CHUNK_BYTES = CHUNK_FRAMES * CHANNELS * BYTES_PER_SAMPLE


def _sine_pcm16(freq_hz: float, num_frames: int, amplitude: int = 4096) -> bytes:
    """Generate mono 16 kHz PCM-16 sine wave at freq_hz for num_frames samples."""
    frames = []
    for i in range(num_frames):
        sample = int(amplitude * math.sin(2 * math.pi * freq_hz * i / SAMPLE_RATE))
        sample = max(-32768, min(32767, sample))
        frames.append(struct.pack("<h", sample))
    return b"".join(frames)


def _silence_pcm16(num_frames: int) -> bytes:
    return bytes(num_frames * BYTES_PER_SAMPLE)


def _make_audio_chunks(duration_s: float) -> List[bytes]:
    """Generate a simple speech-like signal: 500 ms sine + 500 ms silence, repeated."""
    chunks = []
    total_frames = int(SAMPLE_RATE * duration_s)
    generated = 0
    block_frames = int(SAMPLE_RATE * 0.5)  # 500 ms blocks
    is_tone = True
    while generated < total_frames:
        remaining = total_frames - generated
        block = min(block_frames, remaining)
        audio = (_sine_pcm16(300.0, block) if is_tone else _silence_pcm16(block))
        # Split into chunk-sized pieces
        offset = 0
        while offset + CHUNK_BYTES <= len(audio):
            chunks.append(audio[offset:offset + CHUNK_BYTES])
            offset += CHUNK_BYTES
        if offset < len(audio):
            # Pad last partial chunk with silence
            padded = audio[offset:] + bytes(CHUNK_BYTES - (len(audio) - offset))
            chunks.append(padded)
        generated += block
        is_tone = not is_tone
    return chunks


async def run_canary(
    gateway_url: str,
    api_key: str,
    model: str,
    duration_s: float,
    language: str,
    verbose: bool,
) -> int:
    ws_url = f"{gateway_url}/v1/audio/transcriptions/stream?api_key={api_key}"
    print(f"\n{'='*70}")
    print("PHASE S5 CANARY — Native STT via FastAPI Gateway")
    print(f"{'='*70}")
    print(f"  Gateway WS URL : {ws_url[:ws_url.index('api_key')]}api_key=***")
    print(f"  Model          : {model}")
    print(f"  Audio duration : {duration_s}s synthetic PCM-16 @16kHz (tone+silence)")
    print(f"  Language       : {language}")
    print()

    events_received: List[Dict[str, Any]] = []
    t_connect = time.perf_counter()

    try:
        async with websockets.connect(
            ws_url,
            open_timeout=10.0,
            close_timeout=10.0,
            ping_interval=20.0,
            ping_timeout=20.0,
        ) as ws:
            t_connected = time.perf_counter()
            print(f"  [OK] Connected in {(t_connected - t_connect)*1000:.0f} ms")

            # 1. Send stream.start
            start_msg = {"type": "stream.start", "model": model, "language": language}
            await ws.send(json.dumps(start_msg))
            print(f"  --> {json.dumps(start_msg)}")

            # 2. Prepare synthetic audio
            chunks = _make_audio_chunks(duration_s)
            print(f"  Audio chunks to send: {len(chunks)} x {CHUNK_MS}ms ({len(chunks)*CHUNK_BYTES//1024:.1f} KB)")

            # 3. Launch receiver task
            done_event = asyncio.Event()

            async def receiver():
                try:
                    async for raw in ws:
                        t_now = time.perf_counter()
                        try:
                            evt = json.loads(raw)
                        except Exception:
                            continue
                        events_received.append({"t": t_now, **evt})
                        label = evt.get("type", "?")
                        if label in ("transcript.partial", "transcript.completed", "transcript.final"):
                            text = evt.get("text") or evt.get("confirmed", "") + evt.get("tentative", "")
                            print(f"  <-- [{label}] {repr(text)}")
                        elif label in ("stream.paused", "stream.resumed", "session.created"):
                            print(f"  <-- [{label}]")
                        elif label == "error":
                            print(f"  <-- [ERROR] code={evt.get('code')} message={evt.get('message')}")
                        elif verbose:
                            print(f"  <-- {json.dumps(evt)}")
                        if label in ("transcript.completed", "transcript.final", "error"):
                            done_event.set()
                            break
                except websockets.exceptions.ConnectionClosedOK:
                    done_event.set()
                except Exception as exc:
                    print(f"  [WARN] Receiver error: {exc}")
                    done_event.set()

            recv_task = asyncio.create_task(receiver())

            # 4. Stream audio chunks in real-time
            t_stream_start = time.perf_counter()
            for i, chunk in enumerate(chunks):
                await ws.send(chunk)
                await asyncio.sleep(CHUNK_MS / 1000.0)
                # Print progress every second
                if i % (1000 // CHUNK_MS) == 0:
                    elapsed = time.perf_counter() - t_stream_start
                    print(f"  ... {elapsed:.1f}s elapsed, {i+1}/{len(chunks)} chunks sent", end="\r")

            print()  # newline after progress line
            t_audio_done = time.perf_counter()
            print(f"  [OK] Audio sent in {t_audio_done - t_stream_start:.2f}s")

            # 5. Send audio.commit
            commit_msg = {"type": "audio.commit"}
            await ws.send(json.dumps(commit_msg))
            print(f"  --> {json.dumps(commit_msg)}")

            # 6. Wait for transcript.completed or error (up to 15s)
            try:
                await asyncio.wait_for(done_event.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                print("  [WARN] Timed out waiting for transcript.completed after 15s")

            recv_task.cancel()
            try:
                await recv_task
            except asyncio.CancelledError:
                pass

    except websockets.exceptions.InvalidStatusCode as exc:
        print(f"  [FAIL] WebSocket upgrade rejected: HTTP {exc.status_code}")
        return 1
    except Exception as exc:
        print(f"  [FAIL] Connection error: {exc}")
        return 1

    # 7. Report
    print()
    print(f"{'='*70}")
    print(f"CANARY RESULTS — {len(events_received)} events received")
    print(f"{'='*70}")
    for evt in events_received:
        t_rel = evt["t"] - t_connect
        label = evt.get("type", "?")
        text = evt.get("text") or evt.get("confirmed", "")
        print(f"  t={t_rel:6.2f}s  {label:<30} {repr(text)[:60]}")

    types_seen = {e.get("type") for e in events_received}
    has_partial = any("partial" in t for t in types_seen)
    has_completed = "transcript.completed" in types_seen or "transcript.final" in types_seen
    has_error = "error" in types_seen

    print()
    if has_error:
        err = next(e for e in events_received if e.get("type") == "error")
        print(f"  ❌ CANARY FAILED — received error: code={err.get('code')} {err.get('message')}")
        return 1

    if not has_completed:
        print(f"  ❌ CANARY FAILED — no transcript.completed received (types seen: {sorted(types_seen)})")
        return 1

    print(f"  {'✅' if has_partial else '⚠️ '} Partials received: {has_partial}")
    print(f"  ✅ transcript.completed received")
    print(f"  ✅ CANARY PASSED — native STT pipeline end-to-end via gateway confirmed")
    return 0


def _load_api_key(args_key: Optional[str]) -> str:
    if args_key:
        return args_key
    env_key = os.environ.get("ADMIN_API_KEY") or os.environ.get("API_KEY")
    if env_key:
        return env_key
    env_path = Path(".env")
    if env_path.is_file():
        for line in env_path.read_text().splitlines():
            if line.startswith("ADMIN_API_KEY="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val
    return "dev-voice-key-12345"  # development fallback


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase S5 canary: native STT end-to-end through the FastAPI gateway",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example (pod):
  .venv/bin/python scripts/canary-stt-native.py \\
      --gateway-url ws://127.0.0.1:8000 \\
      --model parakeet-cpp-realtime_eou_120m-v1 \\
      --duration-s 15

Prerequisites:
  1. ./start.sh restart    (with STT_STREAM_NATIVE_ENABLED=true in .env)
  2. ./scripts/bridge-calibrate.sh --model parakeet-cpp-realtime_eou_120m-v1
  3. admitted_streams >= 1 in data/bridge_calibration_<fingerprint>_*.json
        """,
    )
    parser.add_argument(
        "--gateway-url",
        default="ws://127.0.0.1:8000",
        help="FastAPI gateway WebSocket base URL (default: ws://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Admin/voice API key (reads ADMIN_API_KEY from env / .env if omitted)",
    )
    parser.add_argument(
        "--model",
        default="parakeet-cpp-realtime_eou_120m-v1",
        help="STT model ID to request (must be S0-approved and calibrated)",
    )
    parser.add_argument(
        "--duration-s",
        type=float,
        default=10.0,
        help="Seconds of synthetic audio to send (default: 10.0)",
    )
    parser.add_argument(
        "--language",
        default="en",
        help="Language code (default: en)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every event (not just partial/completed/error)",
    )
    args = parser.parse_args()
    api_key = _load_api_key(args.api_key)
    exit_code = asyncio.run(
        run_canary(
            gateway_url=args.gateway_url,
            api_key=api_key,
            model=args.model,
            duration_s=args.duration_s,
            language=args.language,
            verbose=args.verbose,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

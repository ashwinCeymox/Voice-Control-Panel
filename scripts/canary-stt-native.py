#!/usr/bin/env python3
"""Phase S5 Canary: Native STT streaming through the FastAPI gateway.

Connects to WS /v1/audio/transcriptions/stream on the FastAPI gateway (not the
bridge daemon directly), streams real speech from tests/fixtures/audio/quiet_normal_speech.wav
(converted to 16 kHz mono PCM-16 if needed) in 160 ms chunks at real-time pacing,
collects and prints all events, and exits 0 on success or 1 on failure.

Usage (run on the target pod with services started):
    .venv/bin/python scripts/canary-stt-native.py \\
        --gateway-url ws://127.0.0.1:8000 \\
        --api-key <ADMIN_API_KEY> \\
        --model parakeet-cpp-realtime_eou_120m-v1 \\
        --wav-path tests/fixtures/audio/quiet_normal_speech.wav

Pass criteria:
    - Stream accepted (no error event or WebSocket rejection)
    - At least one transcript.partial event received
    - One transcript.completed (or transcript.final) event received
    - If --expected-text is provided, final text contains >= 80% of expected words

Note: Output will be pasted from the pod run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

try:
    import websockets
    import websockets.exceptions
except ImportError:
    print("ERROR: 'websockets' package not installed. Run: pip install websockets", file=sys.stderr)
    sys.exit(1)

SAMPLE_RATE = 16000
CHANNELS = 1
BYTES_PER_SAMPLE = 2  # int16
CHUNK_MS = 160        # 160 ms of audio per send
CHUNK_FRAMES = SAMPLE_RATE * CHUNK_MS // 1000
CHUNK_BYTES = CHUNK_FRAMES * CHANNELS * BYTES_PER_SAMPLE  # 5120 bytes
DEFAULT_WAV_PATH = Path("tests/fixtures/audio/quiet_normal_speech.wav")
DEFAULT_EXPECTED_TEXT = "today in new york is partly cloudy with a chance of afternoon rain"


def _load_wav_as_pcm16_chunks(
    wav_path: Path,
    chunk_bytes: int = CHUNK_BYTES,
) -> List[bytes]:
    """Load a WAV file, convert to 16 kHz mono PCM-16 if needed, and split into chunks."""
    if not wav_path.is_file():
        raise FileNotFoundError(f"WAV file not found: {wav_path}")

    with wave.open(str(wav_path), "rb") as wf:
        nchannels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        raw_frames = wf.readframes(wf.getnframes())

    if sampwidth != 2:
        raise ValueError(f"Unsupported WAV sample width {sampwidth} bytes (expected 2-byte PCM-16)")

    if nchannels == CHANNELS and framerate == SAMPLE_RATE:
        pcm_bytes = raw_frames
    else:
        samples = np.frombuffer(raw_frames, dtype=np.int16)
        if nchannels > 1:
            samples = samples.reshape(-1, nchannels).mean(axis=1)
        else:
            samples = samples.astype(np.float64)
        if framerate != SAMPLE_RATE and len(samples) > 0:
            new_len = int(round(len(samples) * SAMPLE_RATE / float(framerate)))
            x_old = np.linspace(0.0, 1.0, num=len(samples), endpoint=False)
            x_new = np.linspace(0.0, 1.0, num=new_len, endpoint=False)
            samples = np.interp(x_new, x_old, samples)
        pcm_int16 = np.clip(np.round(samples), -32768, 32767).astype(np.int16)
        pcm_bytes = pcm_int16.tobytes()

    chunks: List[bytes] = []
    offset = 0
    while offset + chunk_bytes <= len(pcm_bytes):
        chunks.append(pcm_bytes[offset:offset + chunk_bytes])
        offset += chunk_bytes
    if offset < len(pcm_bytes):
        padded = pcm_bytes[offset:] + bytes(chunk_bytes - (len(pcm_bytes) - offset))
        chunks.append(padded)
    return chunks


def _normalize_words(text: str) -> List[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def word_overlap_ratio(actual_text: str, expected_text: str) -> float:
    """Return fraction of expected words present in actual_text (0.0 to 1.0)."""
    expected_words = set(_normalize_words(expected_text))
    if not expected_words:
        return 1.0
    actual_words = set(_normalize_words(actual_text))
    return len(expected_words & actual_words) / float(len(expected_words))


async def run_canary(
    gateway_url: str,
    api_key: str,
    model: str,
    wav_path: Path,
    language: str,
    expected_text: Optional[str],
    verbose: bool,
) -> int:
    ws_url = f"{gateway_url}/v1/audio/transcriptions/stream?api_key={api_key}"
    print(f"\n{'='*70}")
    print("PHASE S5 CANARY — Native STT via FastAPI Gateway")
    print(f"{'='*70}")
    print(f"  Gateway WS URL : {ws_url[:ws_url.index('api_key')]}api_key=***")
    print(f"  Model          : {model}")
    print(f"  WAV file       : {wav_path}")
    print(f"  Language       : {language}")
    print()

    chunks = _load_wav_as_pcm16_chunks(wav_path)
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

            start_msg = {"type": "stream.start", "model": model, "language": language}
            await ws.send(json.dumps(start_msg))
            print(f"  --> {json.dumps(start_msg)}")
            print(
                f"  Audio chunks to send: {len(chunks)} x {CHUNK_MS}ms "
                f"({len(chunks)*CHUNK_BYTES/1024:.1f} KB, {len(chunks)*CHUNK_MS/1000:.2f}s)"
            )

            done_event = asyncio.Event()

            async def receiver() -> None:
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
                            text = evt.get("text") or (evt.get("confirmed", "") + evt.get("tentative", ""))
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

            t_stream_start = time.perf_counter()
            for i, chunk in enumerate(chunks):
                await ws.send(chunk)
                await asyncio.sleep(CHUNK_MS / 1000.0)
                if verbose:
                    elapsed = time.perf_counter() - t_stream_start
                    print(f"  ... {elapsed:.1f}s elapsed, {i+1}/{len(chunks)} chunks sent", end="\r")

            t_audio_done = time.perf_counter()
            print(f"  [OK] Audio sent in {t_audio_done - t_stream_start:.2f}s")

            commit_msg = {"type": "audio.commit"}
            await ws.send(json.dumps(commit_msg))
            print(f"  --> {json.dumps(commit_msg)}")

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

    print()
    print(f"{'='*70}")
    print(f"CANARY RESULTS — {len(events_received)} events received")
    print(f"{'='*70}")
    for evt in events_received:
        t_rel = evt["t"] - t_connect
        label = evt.get("type", "?")
        text = evt.get("text") or evt.get("confirmed", "")
        print(f"  t={t_rel:6.2f}s  {label:<30} {repr(text)[:80]}")

    types_seen = [e.get("type") for e in events_received]
    partial_count = sum(1 for t in types_seen if t and "partial" in t)
    completed_events = [
        e for e in events_received if e.get("type") in ("transcript.completed", "transcript.final")
    ]
    has_error = any(e.get("type") == "error" for e in events_received)

    print()
    if has_error:
        err = next(e for e in events_received if e.get("type") == "error")
        print(f"  FAIL: received error: code={err.get('code')} {err.get('message')}")
        return 1

    if partial_count < 1:
        print("  FAIL: expected at least one transcript.partial event, got 0")
        return 1

    if not completed_events:
        print(f"  FAIL: no transcript.completed received (types seen: {sorted(set(types_seen))})")
        return 1

    final_text = (completed_events[-1].get("text") or "").strip()
    if not final_text:
        print("  FAIL: transcript.completed text is empty")
        return 1

    print(f"  [OK] Partials received: {partial_count}")
    print(f"  [OK] Final text: {repr(final_text)}")

    if expected_text:
        overlap = word_overlap_ratio(final_text, expected_text)
        print(f"  [OK] Expected-word overlap: {overlap * 100:.1f}% (threshold: 80.0%)")
        if overlap < 0.80:
            print("  FAIL: final text word overlap < 80% of expected S0 transcript")
            return 1

    print("  PASS: native STT pipeline end-to-end via gateway confirmed")
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
    return "dev-voice-key-12345"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase S5 canary: native STT end-to-end through the FastAPI gateway",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example (pod):
  .venv/bin/python scripts/canary-stt-native.py \\
      --gateway-url ws://127.0.0.1:8000 \\
      --model parakeet-cpp-realtime_eou_120m-v1 \\
      --wav-path tests/fixtures/audio/quiet_normal_speech.wav

Prerequisites:
  1. Restart gateway and bridge via PID-only scripts (do NOT use ./start.sh restart)
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
        "--wav-path",
        type=Path,
        default=DEFAULT_WAV_PATH,
        help="Path to speech WAV file (default: tests/fixtures/audio/quiet_normal_speech.wav)",
    )
    parser.add_argument(
        "--expected-text",
        default=DEFAULT_EXPECTED_TEXT,
        help="S0 reference transcript for >=80%% word-overlap check",
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
            wav_path=args.wav_path,
            language=args.language,
            expected_text=args.expected_text,
            verbose=args.verbose,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

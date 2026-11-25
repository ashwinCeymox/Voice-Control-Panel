#!/usr/bin/env python3
"""Native Parakeet Engine Smoke Test (Pod-First Verification).

Loads the real libparakeet.so through ParakeetNativeEngine + the ld.so worker
subprocess, verifies parakeet_capi_abi_version() == 11, streams
tests/fixtures/audio/quiet_normal_speech.wav in 160 ms PCM-16 chunks, prints
every partial and finalize tail, and verifies that accumulated-text merging
(empty finalize + partials) produces the complete final transcript.

NOTE: Must run on the Linux pod with libparakeet.so and the Parakeet GGUF
model installed (will fail on macOS without libparakeet.so).

Usage (pod):
    .venv/bin/python scripts/engine-smoke.py
    .venv/bin/python scripts/engine-smoke.py --model-path models/parakeet-cpp/realtime_eou_120m-v1-f16.gguf
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import wave
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.streaming_bridge.engines.base import join_transcript_parts
from services.streaming_bridge.engines.parakeet_native import (
    DEFAULT_EXPECTED_ABI,
    ParakeetNativeEngine,
    resolve_parakeet_library,
)

SAMPLE_RATE = 16000
CHUNK_MS = 160
CHUNK_BYTES = (SAMPLE_RATE * CHUNK_MS // 1000) * 2  # 5120 bytes (16 kHz mono int16)
DEFAULT_WAV = Path("tests/fixtures/audio/quiet_normal_speech.wav")
DEFAULT_MODEL = os.environ.get("LOCALAI_MODELS_PATH", "models") + "/parakeet-cpp/realtime_eou_120m-v1-f16.gguf"


def _read_pcm16_chunks(wav_path: Path) -> List[bytes]:
    if not wav_path.is_file():
        raise FileNotFoundError(f"WAV file not found: {wav_path}")
    with wave.open(str(wav_path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != SAMPLE_RATE:
            raise ValueError(
                f"Expected 16kHz mono PCM16 WAV, got "
                f"ch={wf.getnchannels()} width={wf.getsampwidth()} rate={wf.getframerate()}"
            )
        raw = wf.readframes(wf.getnframes())
    return [raw[i:i + CHUNK_BYTES] for i in range(0, len(raw), CHUNK_BYTES) if raw[i:i + CHUNK_BYTES]]


async def run_smoke(lib_path: str | None, model_path: str, wav_path: Path, language: str) -> int:
    resolved_lib = resolve_parakeet_library(lib_path)
    if not resolved_lib or not Path(resolved_lib).is_file():
        print(f"ERROR: libparakeet shared library not found (resolved={resolved_lib!r})", file=sys.stderr)
        return 1
    if not Path(model_path).is_file():
        print(f"ERROR: model file not found: {model_path}", file=sys.stderr)
        return 1

    chunks = _read_pcm16_chunks(wav_path)
    print(f"Library : {resolved_lib}")
    print(f"Model   : {model_path}")
    print(f"WAV     : {wav_path} ({len(chunks)} x {CHUNK_MS}ms chunks)")

    engine = ParakeetNativeEngine(
        lib_path=resolved_lib,
        expected_abi=DEFAULT_EXPECTED_ABI,
    )
    try:
        await engine.load(model_path, language=language)
        health = engine.health()
        abi = health.get("worker_abi")
        print(f"Worker  : pid={health.get('worker_pid')} abi={abi}")
        if abi != DEFAULT_EXPECTED_ABI:
            print(f"ERROR: ABI mismatch: got {abi}, expected {DEFAULT_EXPECTED_ABI}", file=sys.stderr)
            return 1

        sid = await engine.open_stream(language=language)
        last_accumulated = ""
        partial_count = 0

        for idx, chunk in enumerate(chunks):
            res = await engine.feed(sid, chunk, sample_rate=SAMPLE_RATE)
            if res.text_delta:
                partial_count += 1
                print(
                    f"  [partial #{partial_count:02d} chunk={idx:02d}] "
                    f"delta={res.text_delta!r} | accumulated={res.accumulated_text!r}"
                )
            last_accumulated = res.accumulated_text

        final = await engine.finalize(sid)
        expected_merged = join_transcript_parts(last_accumulated, final.tail_text)
        print()
        print(f"Partials emitted   : {partial_count}")
        print(f"Accumulated text   : {last_accumulated!r}")
        print(f"Finalize tail text : {final.tail_text!r}")
        print(f"Merged final text  : {final.full_text!r}")

        if final.full_text != expected_merged:
            print(
                f"ERROR: merge mismatch: full_text={final.full_text!r} != expected={expected_merged!r}",
                file=sys.stderr,
            )
            return 1
        if not final.full_text.strip():
            print("ERROR: merged final transcript is empty", file=sys.stderr)
            return 1

        print("PASS: ABI 11 verified, partials emitted, accumulated-text merge verified.")
        return 0
    finally:
        await engine.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Real libparakeet.so + ld.so worker smoke test")
    parser.add_argument("--lib-path", default=None, help="Explicit path to libparakeet.so")
    parser.add_argument("--model-path", default=DEFAULT_MODEL, help="Path to Parakeet GGUF model")
    parser.add_argument("--wav-path", type=Path, default=DEFAULT_WAV, help="Path to 16kHz mono PCM16 WAV")
    parser.add_argument("--language", default="en", help="Language code (default: en)")
    args = parser.parse_args()

    rc = asyncio.run(run_smoke(args.lib_path, args.model_path, args.wav_path, args.language))
    sys.exit(rc)


if __name__ == "__main__":
    main()

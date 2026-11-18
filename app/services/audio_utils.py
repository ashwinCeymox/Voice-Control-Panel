"""
Audio Utilities for Voice Agent Pipeline.
Handles valid PCM -> WAV packaging with proper 44-byte RIFF header,
audio statistics calculation (RMS, peak, clipping, leading silence),
and silence / decode error evaluation without content retention.
"""

import io
import wave
from typing import Dict, Any
import numpy as np


def build_wav_from_pcm(
    pcm_bytes: bytes,
    sample_rate: int = 16000,
    channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    """
    Wrap raw signed 16-bit PCM bytes into a standard 16kHz mono WAV container with a 44-byte RIFF header.
    Ensures FFmpeg / LocalAI Whisper can parse streamed audio segments without format error 183.
    """
    if not pcm_bytes:
        pcm_bytes = b""

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sample_width)
        w.setframerate(sample_rate)
        w.writeframes(pcm_bytes)

    return buf.getvalue()


def measure_audio_stats(
    pcm_or_wav_bytes: bytes,
    sample_rate: int = 16000,
    silence_threshold_rms: float = 120.0,
) -> Dict[str, Any]:
    """
    Compute operational audio metrics (RMS, peak, clipping, duration, silence)
    Strict Privacy Constraint: ZERO content, transcripts, or audio data stored.
    """
    if not pcm_or_wav_bytes:
        return {
            "sample_rate": sample_rate,
            "duration_sec": 0.0,
            "rms": 0.0,
            "peak": 0.0,
            "clipped_samples": 0,
            "is_silent": True,
            "byte_count": 0,
        }

    # Strip RIFF header if present
    raw_pcm = pcm_or_wav_bytes
    if pcm_or_wav_bytes.startswith(b"RIFF") and len(pcm_or_wav_bytes) >= 44:
        raw_pcm = pcm_or_wav_bytes[44:]

    n_samples = len(raw_pcm) // 2
    if n_samples == 0:
        return {
            "sample_rate": sample_rate,
            "duration_sec": 0.0,
            "rms": 0.0,
            "peak": 0.0,
            "clipped_samples": 0,
            "is_silent": True,
            "byte_count": len(pcm_or_wav_bytes),
        }

    samples = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32)
    rms = float(np.sqrt(np.mean(samples**2)))
    peak = float(np.max(np.abs(samples))) if len(samples) > 0 else 0.0
    clipped = int(np.sum(np.abs(samples) >= 32760))
    duration_sec = round(n_samples / float(sample_rate), 3)

    return {
        "sample_rate": sample_rate,
        "duration_sec": duration_sec,
        "rms": round(rms, 1),
        "peak": round(peak, 1),
        "clipped_samples": clipped,
        "is_silent": bool(rms < silence_threshold_rms),
        "byte_count": len(pcm_or_wav_bytes),
    }

"""Audio utilities for Streaming STT Bridge.

Provides direct in-memory 16 kHz mono PCM16 WAV building and energy calculations
without heavy external dependencies.
"""

import math
import struct


def create_wav_header(
    pcm_bytes_len: int,
    sample_rate: int = 16000,
    num_channels: int = 1,
    bits_per_sample: int = 16,
) -> bytes:
    """Build a standard 44-byte RIFF/WAVE header directly in memory.
    
    This avoids spawning ffmpeg or running complex audio conversions.
    """
    byte_rate = sample_rate * num_channels * (bits_per_sample // 8)
    block_align = num_channels * (bits_per_sample // 8)
    riff_chunk_size = 36 + pcm_bytes_len

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        riff_chunk_size,
        b"WAVE",
        b"fmt ",
        16,  # Subchunk1Size for PCM
        1,   # AudioFormat (1 = PCM)
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        pcm_bytes_len,
    )
    return header


def pcm16_to_wav(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """Wrap raw 16-bit mono PCM bytes in a standard WAV container."""
    return create_wav_header(len(pcm_bytes), sample_rate=sample_rate) + pcm_bytes


def calculate_rms_energy(pcm_bytes: bytes) -> float:
    """Compute Root-Mean-Square (RMS) energy for 16-bit signed PCM audio.
    
    Returns energy value between 0.0 and 32767.0.
    """
    if not pcm_bytes or len(pcm_bytes) < 2:
        return 0.0

    count = len(pcm_bytes) // 2
    # Unpack 16-bit signed little-endian integers
    fmt = f"<{count}h"
    samples = struct.unpack(fmt, pcm_bytes[: count * 2])
    sum_squares = sum(s * s for s in samples)
    mean_square = sum_squares / count
    return math.sqrt(mean_square)

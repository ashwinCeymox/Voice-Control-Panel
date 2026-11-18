"""Generate deterministic audio fixture for testing without external tools."""

import math
import struct
import wave
from pathlib import Path


def generate_sine_wave_wav(
    filepath: Path,
    duration_seconds: float = 1.0,
    sample_rate: int = 16000,
    frequency: float = 440.0,
    amplitude: float = 0.5,
):
    """Generate a clean 16-bit mono PCM WAV file."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    num_samples = int(duration_seconds * sample_rate)

    with wave.open(str(filepath), "wb") as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit signed
        wav_file.setframerate(sample_rate)

        for i in range(num_samples):
            # Generate pure sine wave
            t = float(i) / sample_rate
            sample = amplitude * math.sin(2.0 * math.pi * frequency * t)
            # Convert float [-1.0, 1.0] to signed 16-bit integer [-32768, 32767]
            packed_sample = struct.pack("<h", int(sample * 32767.0))
            wav_file.writeframesraw(packed_sample)


if __name__ == "__main__":
    out = Path(__file__).parent / "sample_utterance.wav"
    generate_sine_wave_wav(out)
    print(f"Generated test audio fixture: {out} ({out.stat().st_size} bytes)")

"""
Server-side Voice Activity Detection using Silero VAD v5 via sherpa-onnx.
Enables headless audio streaming (e.g. telephony, SIP gateways) without client-side VAD.
"""

from dataclasses import dataclass
import io
import logging
import os
from pathlib import Path
from typing import List, Optional, Union
import numpy as np
import sherpa_onnx
import wave

logger = logging.getLogger("voice_agent.server_vad")

DEFAULT_SILERO_PATH = "frontend/public/vad/silero_vad_v5.onnx"


@dataclass
class SpeechStart:
    timestamp: float = 0.0


@dataclass
class SpeechEnd:
    pcm_bytes: bytes
    duration_sec: float
    sample_count: int


def pcm_to_wav(
    pcm_bytes: bytes, sample_rate: int = 16000, num_channels: int = 1
) -> bytes:
    """Pack raw 16-bit mono PCM into standard RIFF WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(num_channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


class ServerVAD:
    """Streaming server-side VAD wrapping sherpa_onnx.VoiceActivityDetector."""

    def __init__(
        self,
        model_path: Optional[str] = None,
        sample_rate: int = 16000,
        threshold: float = 0.5,
        min_silence_duration: float = 0.5,
        min_speech_duration: float = 0.25,
        window_size: int = 512,
        num_threads: int = 1,
    ):
        resolved_path = model_path or os.getenv(
            "SERVER_VAD_MODEL_PATH", DEFAULT_SILERO_PATH
        )
        if not Path(resolved_path).is_file():
            # Try workspace root relative
            alt_path = Path(__file__).resolve().parent.parent.parent / resolved_path
            if alt_path.is_file():
                resolved_path = str(alt_path)
            else:
                raise FileNotFoundError(
                    f"Silero VAD ONNX model not found at '{resolved_path}'"
                )

        self.sample_rate = sample_rate
        self.window_size = window_size
        self._bytes_per_sample = 2  # 16-bit PCM
        self._window_bytes = self.window_size * self._bytes_per_sample
        self._buffer = bytearray()
        self._in_speech = False

        silero_cfg = sherpa_onnx.SileroVadModelConfig(
            model=resolved_path,
            threshold=threshold,
            min_silence_duration=min_silence_duration,
            min_speech_duration=min_speech_duration,
            window_size=window_size,
        )
        vad_cfg = sherpa_onnx.VadModelConfig(
            silero_vad=silero_cfg,
            sample_rate=sample_rate,
            num_threads=num_threads,
            provider="cpu",
        )
        self._vad = sherpa_onnx.VoiceActivityDetector(
            config=vad_cfg, buffer_size_in_seconds=60
        )
        logger.debug(
            "ServerVAD initialized with model: %s (sr=%d, th=%.2f)",
            resolved_path,
            sample_rate,
            threshold,
        )

    def is_speech_detected(self) -> bool:
        """Check if speech is currently active."""
        return self._in_speech or self._vad.is_speech_detected()

    def feed_pcm(self, pcm_bytes: bytes) -> List[Union[SpeechStart, SpeechEnd]]:
        """Feed 16-bit mono PCM bytes and return state transition events."""
        events: List[Union[SpeechStart, SpeechEnd]] = []
        if not pcm_bytes:
            return events

        self._buffer.extend(pcm_bytes)

        while len(self._buffer) >= self._window_bytes:
            chunk_bytes = self._buffer[: self._window_bytes]
            del self._buffer[: self._window_bytes]

            samples = (
                np.frombuffer(chunk_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            )
            self._vad.accept_waveform(samples)

            if self._vad.is_speech_detected():
                if not self._in_speech:
                    self._in_speech = True
                    events.append(SpeechStart())

            if not self._vad.empty():
                seg = self._vad.front
                seg_samples = np.array(seg.samples, dtype=np.float32)
                pcm_out = (
                    (np.clip(seg_samples, -1.0, 1.0) * 32767.0)
                    .astype(np.int16)
                    .tobytes()
                )
                dur = len(seg.samples) / self.sample_rate
                events.append(
                    SpeechEnd(
                        pcm_bytes=pcm_out,
                        duration_sec=dur,
                        sample_count=len(seg.samples),
                    )
                )
                self._vad.pop()
                self._in_speech = False

        return events

    def flush(self) -> Optional[SpeechEnd]:
        """Flush buffered tail samples and finalize any in-progress speech segment."""
        if len(self._buffer) > 0:
            pad_needed = self._window_bytes - len(self._buffer)
            if pad_needed > 0:
                self._buffer.extend(b"\x00" * pad_needed)
            samples = (
                np.frombuffer(self._buffer, dtype=np.int16).astype(np.float32) / 32768.0
            )
            self._vad.accept_waveform(samples)
            self._buffer.clear()

        self._vad.flush()
        if not self._vad.empty():
            seg = self._vad.front
            seg_samples = np.array(seg.samples, dtype=np.float32)
            pcm_out = (
                (np.clip(seg_samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
            )
            dur = len(seg.samples) / self.sample_rate
            ended = SpeechEnd(
                pcm_bytes=pcm_out,
                duration_sec=dur,
                sample_count=len(seg.samples),
            )
            self._vad.pop()
            self._in_speech = False
            return ended

        self._in_speech = False
        return None

    def reset(self) -> None:
        """Reset detector state for a new audio stream."""
        self._vad.reset()
        self._buffer.clear()
        self._in_speech = False

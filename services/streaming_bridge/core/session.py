"""Bridge Session manages audio buffering, segmenting, and alignment for a stream."""

import time
from typing import Callable, List, Optional, Tuple
from services.streaming_bridge.core.aligner import StreamTextAligner
from services.streaming_bridge.core.audio_utils import calculate_rms_energy, pcm16_to_wav


class BridgeSession:
    """Manages audio buffering, VAD energy detection, and segment life-cycle for a single stream.
    
    Format: 16 kHz mono 16-bit PCM little-endian.
    1 second = 16000 samples = 32000 bytes.
    """

    BYTES_PER_SAMPLE = 2
    SAMPLE_RATE = 16000
    BYTES_PER_SECOND = SAMPLE_RATE * BYTES_PER_SAMPLE  # 32,000 bytes/sec

    def __init__(
        self,
        stream_id: str,
        model: str,
        language: str = "en",
        step_ms: int = 1000,
        min_window_ms: int = 1200,
        segment_max_ms: int = 15000,
        segment_silence_ms: int = 600,
        energy_threshold: float = 250.0,
    ):
        self.stream_id = stream_id
        self.model = model
        self.language = language
        self.step_ms = step_ms
        self.min_window_ms = min_window_ms
        self.segment_max_ms = segment_max_ms
        self.segment_silence_ms = segment_silence_ms
        self.energy_threshold = energy_threshold

        # Audio buffers
        self.current_segment_pcm = bytearray()
        self.all_completed_segments_text: List[str] = []
        self.segment_id: int = 0
        self.seq: int = 0

        # Timing and state
        self.created_at = time.time()
        self.last_audio_received_at = time.time()
        self.last_decode_pcm_len: int = 0
        self.consecutive_silence_ms: int = 0
        self.is_closed: bool = False

        self.aligner = StreamTextAligner()

    @property
    def current_segment_duration_ms(self) -> int:
        return int((len(self.current_segment_pcm) / self.BYTES_PER_SECOND) * 1000)

    def append_pcm(self, pcm_chunk: bytes) -> bool:
        """Append raw 16kHz mono PCM16 bytes.
        
        Returns:
            should_cut_segment (bool): True if silence threshold or segment max duration was reached.
        """
        if self.is_closed:
            return False

        self.last_audio_received_at = time.time()
        self.current_segment_pcm.extend(pcm_chunk)

        # Check chunk energy for silence tracking
        chunk_energy = calculate_rms_energy(pcm_chunk)
        chunk_ms = int((len(pcm_chunk) / self.BYTES_PER_SECOND) * 1000)

        if chunk_energy < self.energy_threshold:
            self.consecutive_silence_ms += chunk_ms
        else:
            self.consecutive_silence_ms = 0

        # Check segment end conditions
        silence_triggered = (
            self.current_segment_duration_ms >= self.min_window_ms
            and self.consecutive_silence_ms >= self.segment_silence_ms
        )
        max_duration_triggered = self.current_segment_duration_ms >= self.segment_max_ms

        return silence_triggered or max_duration_triggered

    def is_eligible_for_partial(self) -> bool:
        """True if current segment has enough audio and active speech to trigger a partial decode."""
        if self.is_closed:
            return False
        if self.current_segment_duration_ms < self.min_window_ms:
            return False
        # Avoid redundant decodes if audio hasn't grown substantially (at least 200ms)
        new_audio_ms = int(((len(self.current_segment_pcm) - self.last_decode_pcm_len) / self.BYTES_PER_SECOND) * 1000)
        if new_audio_ms < 200:
            return False
        return True

    def get_current_segment_wav(self) -> bytes:
        """Package current segment audio into in-memory WAV container."""
        pcm_snapshot = bytes(self.current_segment_pcm)
        self.last_decode_pcm_len = len(pcm_snapshot)
        return pcm16_to_wav(pcm_snapshot, sample_rate=self.SAMPLE_RATE)

    def can_reuse_last_decode_for_final(self) -> bool:
        """True if the last partial decode covered virtually all audio in the current segment."""
        uncovered_bytes = len(self.current_segment_pcm) - self.last_decode_pcm_len
        # Within ~160ms (5120 bytes) of audio
        return uncovered_bytes <= 5120 and bool(self.aligner.last_emitted_partial)

    def process_partial_result(self, raw_text: str, degraded: bool = False) -> dict:
        """Feed decode result to aligner and generate transcript.partial event payload."""
        self.seq += 1
        confirmed, tentative, _ = self.aligner.update(raw_text)
        full_text = (f"{confirmed} {tentative}").strip()
        return {
            "type": "transcript.partial",
            "seq": self.seq,
            "segment_id": self.segment_id,
            "text": full_text,
            "confirmed": confirmed,
            "tentative": tentative,
            "degraded": degraded,
        }

    def cut_segment(self, final_raw_text: Optional[str] = None, final_degraded: bool = False) -> Tuple[dict, Optional[bytes]]:
        """Close current segment, emit segment.final, and advance segment_id.
        
        Returns:
            (event_dict, next_segment_initial_pcm)
        """
        final_text, revised = self.aligner.finalize(final_raw_text)
        if final_text:
            self.all_completed_segments_text.append(final_text)

        event = {
            "type": "segment.final",
            "segment_id": self.segment_id,
            "text": final_text,
            "revised": revised,
        }
        if final_degraded:
            event["final_degraded"] = True

        # Reset segment state
        self.segment_id += 1
        self.current_segment_pcm = bytearray()
        self.last_decode_pcm_len = 0
        self.consecutive_silence_ms = 0
        self.aligner.reset_segment()

        return event, None

    def complete_stream(self, final_raw_text: Optional[str] = None, final_degraded: bool = False) -> dict:
        """Commit entire stream and produce transcript.completed."""
        self.is_closed = True
        if self.current_segment_pcm:
            # Finalize trailing audio segment
            final_text, revised = self.aligner.finalize(final_raw_text)
            if final_text:
                self.all_completed_segments_text.append(final_text)

        joined_text = " ".join(self.all_completed_segments_text).strip()
        duration_s = round(time.time() - self.created_at, 2)
        evt = {
            "type": "transcript.completed",
            "text": joined_text,
            "model_used": self.model,
            "duration": duration_s,
            "revised": False,
        }
        if final_degraded:
            evt["final_degraded"] = True
        return evt

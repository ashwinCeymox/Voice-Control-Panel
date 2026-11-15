"""
Zero-dependency Prometheus metrics collector.
Tracks request counts, active streaming gauges, token/audio counters, and turn latency histograms.
"""
from collections import defaultdict
import threading
from typing import Dict, Tuple

LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float("inf"))


class MetricsCollector:
    """Thread-safe zero-dependency metrics collector that renders Prometheus text format."""

    def __init__(self):
        self._lock = threading.Lock()
        self._requests: Dict[Tuple[str, str], int] = defaultdict(int)
        self._active_streams: Dict[str, int] = defaultdict(int)
        self._tokens: Dict[str, float] = defaultdict(float)
        self._audio_seconds: Dict[str, float] = defaultdict(float)
        self._turn_latency_buckets: Dict[float, int] = {b: 0 for b in LATENCY_BUCKETS}
        self._turn_latency_sum: float = 0.0
        self._turn_latency_count: int = 0

    def inc_request(self, endpoint: str, status: int) -> None:
        with self._lock:
            self._requests[(endpoint, str(status))] += 1

    def inc_active_streams(self, stream_type: str = "voice") -> None:
        with self._lock:
            self._active_streams[stream_type] += 1

    def dec_active_streams(self, stream_type: str = "voice") -> None:
        with self._lock:
            curr = self._active_streams[stream_type]
            self._active_streams[stream_type] = max(0, curr - 1)

    def add_tokens(self, endpoint: str, count: float) -> None:
        with self._lock:
            self._tokens[endpoint] += count

    def add_audio_seconds(self, endpoint: str, seconds: float) -> None:
        with self._lock:
            self._audio_seconds[endpoint] += seconds

    def observe_turn_latency(self, seconds: float) -> None:
        with self._lock:
            self._turn_latency_count += 1
            self._turn_latency_sum += seconds
            for b in LATENCY_BUCKETS:
                if seconds <= b:
                    self._turn_latency_buckets[b] += 1

    def render_prometheus(self) -> str:
        """Render all metrics into standard Prometheus exposition format."""
        lines = []

        # 1. Requests Counter
        lines.append("# HELP voice_agent_requests_total Total number of HTTP/WS requests processed.")
        lines.append("# TYPE voice_agent_requests_total counter")
        with self._lock:
            reqs = list(self._requests.items())
            streams = list(self._active_streams.items())
            tokens = list(self._tokens.items())
            audio = list(self._audio_seconds.items())
            hist_buckets = list(self._turn_latency_buckets.items())
            l_sum = self._turn_latency_sum
            l_count = self._turn_latency_count

        if reqs:
            for (ep, st), val in sorted(reqs):
                lines.append(f'voice_agent_requests_total{{endpoint="{ep}",status="{st}"}} {val}')
        else:
            lines.append('voice_agent_requests_total{endpoint="/v1/health",status="200"} 0')

        # 2. Active Streams Gauge
        lines.append("# HELP voice_agent_active_streams Current number of active streaming connections.")
        lines.append("# TYPE voice_agent_active_streams gauge")
        if streams:
            for s_type, val in sorted(streams):
                lines.append(f'voice_agent_active_streams{{type="{s_type}"}} {val}')
        else:
            lines.append('voice_agent_active_streams{type="voice"} 0')
            lines.append('voice_agent_active_streams{type="stt"} 0')

        # 3. Tokens Total Counter
        lines.append("# HELP voice_agent_tokens_total Total tokens processed by LLM.")
        lines.append("# TYPE voice_agent_tokens_total counter")
        if tokens:
            for ep, val in sorted(tokens):
                lines.append(f'voice_agent_tokens_total{{endpoint="{ep}"}} {val:.0f}')
        else:
            lines.append('voice_agent_tokens_total{endpoint="/v1/chat/completions"} 0')

        # 4. Audio Seconds Total Counter
        lines.append("# HELP voice_agent_audio_seconds_total Total audio duration in seconds processed.")
        lines.append("# TYPE voice_agent_audio_seconds_total counter")
        if audio:
            for ep, val in sorted(audio):
                lines.append(f'voice_agent_audio_seconds_total{{endpoint="{ep}"}} {val:.2f}')
        else:
            lines.append('voice_agent_audio_seconds_total{endpoint="/v1/audio/transcriptions"} 0.00')

        # 5. Turn Latency Histogram
        lines.append("# HELP voice_agent_turn_latency_seconds Voice pipeline turn latency in seconds.")
        lines.append("# TYPE voice_agent_turn_latency_seconds histogram")
        for b_val, b_cnt in hist_buckets:
            le_str = "+Inf" if b_val == float("inf") else str(b_val)
            lines.append(f'voice_agent_turn_latency_seconds_bucket{{le="{le_str}"}} {b_cnt}')
        lines.append(f"voice_agent_turn_latency_seconds_sum {l_sum:.4f}")
        lines.append(f"voice_agent_turn_latency_seconds_count {l_count}")

        return "\n".join(lines) + "\n"


metrics_collector = MetricsCollector()

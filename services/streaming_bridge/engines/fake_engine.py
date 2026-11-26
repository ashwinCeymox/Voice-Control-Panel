"""Deterministic In-Memory Fake Engine for Unit & Protocol Testing (No Model Weights Required)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import uuid

from services.streaming_bridge.engines.base import (
    EngineChunkResult,
    EngineCrashedError,
    EngineFinalResult,
    StreamingEngine,
    join_transcript_parts,
    pcm16_to_float32,
)


@dataclass
class _FakeStreamState:
    stream_id: str
    language: str
    chunks_fed: int = 0
    total_samples: int = 0
    accumulated_text: str = ""
    step_idx: int = 0
    closed: bool = False
    scripted_chunks: List[Dict[str, Any]] = field(default_factory=list)


class FakeStreamingEngine(StreamingEngine):
    """Fake streaming STT engine implementing the native engine contract for tests."""

    def __init__(
        self,
        feed_latency_s: float = 0.0,
        finalize_latency_s: float = 0.0,
        chunk_script: Optional[List[Dict[str, Any]]] = None,
        finalize_tail_text: str = "",
        crash_on_feed_call: Optional[int] = None,
    ):
        self.feed_latency_s = feed_latency_s
        self.finalize_latency_s = finalize_latency_s
        self.chunk_script = list(chunk_script) if chunk_script is not None else [
            {"text": " the", "eou": False},
            {"text": " weather", "eou": False},
            {"text": " today", "eou": False},
            {"text": " is", "eou": False},
            {"text": " part", "eou": False},
            {"text": "ly", "eou": False},
            {"text": " clou", "eou": False},
            {"text": "dy", "eou": True},
        ]
        self.finalize_tail_text = finalize_tail_text
        self.crash_on_feed_call = crash_on_feed_call

        self.loaded: bool = False
        self.model_path: Optional[str] = None
        self.default_language: str = "en"
        self.streams: Dict[str, _FakeStreamState] = {}
        self.closed_stream_ids: List[str] = []
        self.total_feed_calls: int = 0
        self.close_count: int = 0

    @property
    def active_streams(self) -> int:
        return len(self.streams)

    async def load(self, model_path: str, language: str = "en") -> None:
        self.model_path = model_path
        self.default_language = language
        self.loaded = True

    async def open_stream(self, language: str = "en") -> str:
        if not self.loaded:
            raise RuntimeError("Engine model not loaded")
        sid = f"fake_{uuid.uuid4().hex[:8]}"
        self.streams[sid] = _FakeStreamState(
            stream_id=sid,
            language=language or self.default_language,
            scripted_chunks=list(self.chunk_script),
        )
        return sid

    async def feed(
        self,
        stream_id: str,
        pcm16_bytes: bytes,
        sample_rate: int = 16000,
    ) -> EngineChunkResult:
        samples = pcm16_to_float32(pcm16_bytes, sample_rate=sample_rate)
        state = self.streams.get(stream_id)
        if state is None or state.closed:
            raise KeyError(f"Unknown or closed stream: {stream_id}")

        self.total_feed_calls += 1
        if self.crash_on_feed_call is not None and self.total_feed_calls == self.crash_on_feed_call:
            self.streams.clear()
            self.loaded = False
            raise EngineCrashedError("Simulated worker crash during feed")

        if self.feed_latency_s > 0:
            await asyncio.sleep(self.feed_latency_s)

        state.chunks_fed += 1
        state.total_samples += len(samples)

        text_delta = ""
        eou = False
        eob = False
        words: List[Dict[str, Any]] = []
        events: List[Dict[str, Any]] = []

        if samples and state.step_idx < len(state.scripted_chunks):
            entry = state.scripted_chunks[state.step_idx]
            state.step_idx += 1
            text_delta = str(entry.get("text", ""))
            eou = bool(entry.get("eou", False))
            eob = bool(entry.get("eob", False))
            words = list(entry.get("words", []))
            events = list(entry.get("events", []))
            state.accumulated_text = join_transcript_parts(state.accumulated_text, text_delta)

        return EngineChunkResult(
            text_delta=text_delta,
            accumulated_text=state.accumulated_text,
            eou=eou,
            eob=eob,
            words=words,
            events=events,
        )

    async def finalize(self, stream_id: str) -> EngineFinalResult:
        state = self.streams.get(stream_id)
        if state is None or state.closed:
            raise KeyError(f"Unknown or closed stream: {stream_id}")

        if self.finalize_latency_s > 0:
            await asyncio.sleep(self.finalize_latency_s)

        tail = self.finalize_tail_text
        full_text = join_transcript_parts(state.accumulated_text, tail)
        await self.close_stream(stream_id)
        return EngineFinalResult(tail_text=tail, full_text=full_text)

    async def close_stream(self, stream_id: str) -> None:
        state = self.streams.pop(stream_id, None)
        if state is not None and not state.closed:
            state.closed = True
            self.closed_stream_ids.append(stream_id)

    async def close(self) -> None:
        for sid in list(self.streams.keys()):
            await self.close_stream(sid)
        self.loaded = False
        self.close_count += 1

    def health(self) -> Dict[str, Any]:
        return {
            "engine": "FakeStreamingEngine",
            "ready": self.loaded,
            "model_path": self.model_path,
            "active_streams": self.active_streams,
            "total_feed_calls": self.total_feed_calls,
        }

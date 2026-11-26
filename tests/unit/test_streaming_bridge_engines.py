"""Unit tests for Phase S2: StreamingEngine contract, PCM16 conversion, and FakeStreamingEngine."""

import struct
import pytest

from services.streaming_bridge.engines.base import (
    EngineCrashedError,
    UnsupportedAudioFormatError,
    join_transcript_parts,
    pcm16_to_float32,
)
from services.streaming_bridge.engines.fake_engine import FakeStreamingEngine


def test_pcm16_to_float32_conversion_and_validation():
    pcm = struct.pack("<4h", 0, 16384, -16384, -32768)
    floats = pcm16_to_float32(pcm, sample_rate=16000, channels=1, audio_format="pcm_s16le")
    assert len(floats) == 4
    assert floats[0] == 0.0
    assert abs(floats[1] - 0.5) < 1e-4
    assert abs(floats[2] + 0.5) < 1e-4
    assert floats[3] == -1.0

    # Empty chunk allowed cleanly
    assert pcm16_to_float32(b"", sample_rate=16000) == []

    # Wrong sample rates / format / channels / byte alignment rejected clearly
    with pytest.raises(UnsupportedAudioFormatError, match="16000 Hz"):
        pcm16_to_float32(pcm, sample_rate=8000)

    with pytest.raises(UnsupportedAudioFormatError, match="16000 Hz"):
        pcm16_to_float32(pcm, sample_rate=44100)

    with pytest.raises(UnsupportedAudioFormatError, match="mono"):
        pcm16_to_float32(pcm, sample_rate=16000, channels=2)

    with pytest.raises(UnsupportedAudioFormatError, match="pcm_s16le"):
        pcm16_to_float32(pcm, sample_rate=16000, audio_format="opus")

    with pytest.raises(UnsupportedAudioFormatError, match="even"):
        pcm16_to_float32(b"\x01\x02\x03", sample_rate=16000)


def test_join_transcript_parts_preserves_partials_on_empty_tail():
    acc = ""
    acc = join_transcript_parts(acc, "hello")
    acc = join_transcript_parts(acc, "  world ")
    assert acc == "hello world"
    assert join_transcript_parts(acc, "") == "hello world"
    assert join_transcript_parts(acc, "   ") == "hello world"
    assert join_transcript_parts(acc, " again") == "hello world again"


def test_join_transcript_parts_subword_token_pieces():
    acc = ""
    for piece in [" part", "ly", " clou", "dy"]:
        acc = join_transcript_parts(acc, piece)
    assert acc == "partly cloudy"
    assert join_transcript_parts(acc, "") == "partly cloudy"
    assert join_transcript_parts(acc, "   ") == "partly cloudy"


@pytest.mark.asyncio
async def test_fake_engine_default_subword_pieces_and_empty_finalize():
    engine = FakeStreamingEngine(finalize_tail_text="")
    await engine.load("fake-model.gguf", language="en")
    sid = await engine.open_stream("en")
    chunk_pcm = b"\x00\x02" * 1600
    for _ in range(len(engine.chunk_script)):
        res = await engine.feed(sid, chunk_pcm, sample_rate=16000)
    assert res.accumulated_text == "the weather today is partly cloudy"
    final = await engine.finalize(sid)
    assert final.tail_text == ""
    assert final.full_text == "the weather today is partly cloudy"


@pytest.mark.asyncio
async def test_fake_engine_finalize_empty_plus_accumulated_partials():
    """S2 requirement: empty finalize_json tail + accumulated feed partials produces complete final."""
    engine = FakeStreamingEngine(
        chunk_script=[
            {"text": "good morning", "eou": False},
            {"text": " alice", "eou": True},
        ],
        finalize_tail_text="",
    )
    await engine.load("fake-model.gguf", language="en")
    sid = await engine.open_stream("en")
    chunk_pcm = b"\x00\x02" * 1600

    r1 = await engine.feed(sid, chunk_pcm, sample_rate=16000)
    assert r1.text_delta == "good morning"
    assert r1.accumulated_text == "good morning"
    assert r1.eou is False

    r2 = await engine.feed(sid, chunk_pcm, sample_rate=16000)
    assert r2.text_delta == " alice"
    assert r2.accumulated_text == "good morning alice"
    assert r2.eou is True

    final = await engine.finalize(sid)
    assert final.tail_text == ""
    assert final.full_text == "good morning alice"
    assert engine.active_streams == 0
    assert sid in engine.closed_stream_ids

    await engine.close()
    assert engine.loaded is False


@pytest.mark.asyncio
async def test_fake_engine_merges_non_empty_finalize_tail():
    engine = FakeStreamingEngine(
        chunk_script=[{"text": "test", "eou": False}],
        finalize_tail_text=" phrase",
    )
    await engine.load("fake-model.gguf")
    sid = await engine.open_stream()
    await engine.feed(sid, b"\x00\x01" * 800, sample_rate=16000)
    final = await engine.finalize(sid)
    assert final.tail_text == " phrase"
    assert final.full_text == "test phrase"
    assert engine.active_streams == 0


@pytest.mark.asyncio
async def test_fake_engine_multistream_cleanup_and_crash_injection():
    engine = FakeStreamingEngine(crash_on_feed_call=2)
    await engine.load("fake-model.gguf")
    s1 = await engine.open_stream()
    s2 = await engine.open_stream()
    assert engine.active_streams == 2

    await engine.feed(s1, b"\x00\x01" * 400)
    with pytest.raises(EngineCrashedError):
        await engine.feed(s2, b"\x00\x01" * 400)

    assert engine.active_streams == 0
    # close_stream is idempotent even after crash cleanup
    await engine.close_stream(s1)
    await engine.close()
    assert engine.close_count == 1

"""Unit tests for Phase B2: Core Streaming Bridge Engine."""

import pytest
from services.streaming_bridge.core.aligner import StreamTextAligner, filter_hallucinations, normalize_word
from services.streaming_bridge.core.audio_utils import calculate_rms_energy, create_wav_header, pcm16_to_wav
from services.streaming_bridge.core.fake_localai import FakeLocalAI
from services.streaming_bridge.core.session import BridgeSession


def test_wav_header_and_pcm_conversion():
    pcm_data = b"\x00\x01" * 1600  # 3200 bytes = 100ms
    wav = pcm16_to_wav(pcm_data, sample_rate=16000)

    assert len(wav) == len(pcm_data) + 44
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav[12:16] == b"fmt "
    assert wav[36:40] == b"data"


def test_rms_energy_calculation():
    # Complete silence
    silence = b"\x00\x00" * 1600
    assert calculate_rms_energy(silence) == 0.0

    # Sine-like / noisy samples
    signal = b"\xff\x0f" * 1600
    energy = calculate_rms_energy(signal)
    assert energy > 0.0


def test_aligner_normalization_and_hallucination_filter():
    assert normalize_word("Hello,") == "hello"
    assert normalize_word("WORLD?!") == "world"

    # Filter bracketed tags
    assert filter_hallucinations("[BLANK_AUDIO]") is None
    assert filter_hallucinations("  [inaudible]  ") is None
    assert filter_hallucinations("[MUSIC] Hello world [APPLAUSE]") == "Hello world"

    # Filter repetitive loops
    repetitive = "you you you you you you"
    assert filter_hallucinations(repetitive) is None


def test_aligner_prefix_stabilization_and_monotonicity():
    aligner = StreamTextAligner()

    # Step 1: "the weather" -> confirmed: "", tentative: "the weather"
    conf, tent, ok = aligner.update("the weather")
    assert ok is True
    assert conf == ""
    assert tent == "the weather"

    # Step 2: "the weather today" -> confirmed: "the weather", tentative: "today"
    conf, tent, ok = aligner.update("the weather today")
    assert ok is True
    assert conf == "the weather"
    assert tent == "today"

    # Step 3: "the weather today in new york" -> confirmed: "the weather today", tentative: "in new york"
    conf, tent, ok = aligner.update("the weather today in new york")
    assert ok is True
    assert conf == "the weather today"
    assert tent == "in new york"

    # Finalize
    final_text, revised = aligner.finalize()
    assert final_text == "the weather today in new york"


def test_aligner_contradiction_handling():
    aligner = StreamTextAligner()

    # Establish confirmed prefix
    aligner.update("hello world")
    aligner.update("hello world how are you")
    assert aligner.confirmed_words == ["hello", "world"]

    # Contradicting decode: "goodbye world how are you"
    conf, tent, ok = aligner.update("goodbye world how are you")
    assert ok is True
    # Confirmed text is strictly preserved
    assert conf == "hello world"
    assert aligner.confirmed_conflicts == 1


def test_bridge_session_audio_buffering_and_segment_cutting():
    session = BridgeSession(
        stream_id="test-stream-1",
        model="parakeet-cpp-realtime_eou_120m-v1",
        step_ms=1000,
        min_window_ms=1000,
        segment_max_ms=5000,
        segment_silence_ms=600,
        energy_threshold=100.0,
    )

    # Audio < min_window_ms is not eligible for partial decode
    session.append_pcm(b"\x00\x05" * 4000)  # 250 ms
    assert session.is_eligible_for_partial() is False

    # Append up to 1.5s
    session.append_pcm(b"\x00\x05" * 20000)  # +1.25s = 1.5s total
    assert session.is_eligible_for_partial() is True

    # Test partial generation
    part_evt = session.process_partial_result("hello world")
    assert part_evt["type"] == "transcript.partial"
    assert part_evt["seq"] == 1
    assert "hello" in part_evt["text"]

    # Cut segment
    seg_evt, _ = session.cut_segment()
    assert seg_evt["type"] == "segment.final"
    assert seg_evt["segment_id"] == 0
    assert session.segment_id == 1
    assert len(session.current_segment_pcm) == 0

    # Stream completion
    comp_evt = session.complete_stream()
    assert comp_evt["type"] == "transcript.completed"
    assert comp_evt["model_used"] == "parakeet-cpp-realtime_eou_120m-v1"


@pytest.mark.asyncio
async def test_fake_localai_decode_concurrency():
    fake = FakeLocalAI(decode_latency_s=0.05)

    res = await fake.transcribe(model="test-model", audio_bytes=b"dummy")
    assert "text" in res
    assert fake.total_requests == 1
    assert fake.peak_concurrent_decodes == 1

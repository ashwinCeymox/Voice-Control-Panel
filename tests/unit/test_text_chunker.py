"""Unit tests for sentence and phrase text chunker."""
import pytest
from app.services.text_chunker import TextChunker, chunk_stream


def test_chunker_punctuation_split():
    chunker = TextChunker(min_chars=10, max_chars=100)
    chunks = chunker.append("Hello world! How are you today? ")
    assert len(chunks) == 2
    assert chunks[0] == "Hello world!"
    assert chunks[1] == "How are you today?"
    assert chunker.flush() is None


def test_chunker_abbreviation_safety():
    chunker = TextChunker(min_chars=10, max_chars=100)
    # "Dr. Smith" should not split at "Dr."
    chunks1 = chunker.append("Hello Dr. Smith was here. ")
    assert len(chunks1) == 1
    assert chunks1[0] == "Hello Dr. Smith was here."


def test_chunker_long_text_word_boundary():
    chunker = TextChunker(min_chars=10, max_chars=30)
    # Exceeds max_chars without punctuation
    chunks = chunker.append("This is a very long sentence without any punctuation whatsoever")
    assert len(chunks) >= 1
    # Check that it split on a word boundary, not mid-word
    assert not chunks[0].endswith("-")
    remainder = chunker.flush()
    assert remainder is not None


@pytest.mark.asyncio
async def test_chunk_stream_generator():
    async def sample_token_stream():
        tokens = ["The ", "quick ", "brown ", "fox ", "jumped. ", "Over ", "the ", "lazy ", "dog."]
        for t in tokens:
            yield t

    results = []
    async for phrase in chunk_stream(sample_token_stream()):
        results.append(phrase)

    assert len(results) == 2
    assert results[0] == "The quick brown fox jumped."
    assert results[1] == "Over the lazy dog."

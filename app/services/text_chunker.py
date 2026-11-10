"""Sentence and phrase boundary chunker for low-latency streaming TTS."""
import re
from typing import AsyncGenerator, List, Optional
from app.core.config import settings

# Boundary delimiters: sentence-ending punctuation or pause clauses
PUNCTUATION_SPLIT = re.compile(r"([.!?;:\n]+)")
ABBREVIATIONS = {
    "mr.", "ms.", "mrs.", "dr.", "prof.", "e.g.", "i.e.", "etc.", "vs.",
    "jan.", "feb.", "mar.", "apr.", "aug.", "sept.", "oct.", "nov.", "dec."
}


class TextChunker:
    def __init__(
        self,
        max_chars: Optional[int] = None,
        min_chars: int = 20,
    ):
        self.max_chars = max_chars or settings.VOICE_TEXT_CHUNK_MAX_CHARS
        self.min_chars = min_chars
        self._buffer: str = ""

    def append(self, token: str) -> List[str]:
        """Append incoming LLM token delta and return any ready phrase chunks."""
        self._buffer += token
        chunks: List[str] = []

        while len(self._buffer) >= self.min_chars:
            found_chunk = False
            # Find all punctuation occurrences
            for match in PUNCTUATION_SPLIT.finditer(self._buffer):
                end_idx = match.end()
                candidate = self._buffer[:end_idx].strip()

                # Check if punctuation belongs to an abbreviation
                words = candidate.lower().split()
                if words and words[-1] in ABBREVIATIONS:
                    # Skip abbreviation and check subsequent punctuation
                    continue

                chunks.append(candidate)
                self._buffer = self._buffer[end_idx:].lstrip()
                found_chunk = True
                break

            if not found_chunk:
                # If buffer exceeds max chars without valid punctuation, split on word boundary
                if len(self._buffer) >= self.max_chars:
                    space_idx = self._buffer.rfind(" ", 0, self.max_chars)
                    if space_idx != -1:
                        chunk = self._buffer[:space_idx].strip()
                        self._buffer = self._buffer[space_idx:].lstrip()
                        chunks.append(chunk)
                    else:
                        chunk = self._buffer[:self.max_chars].strip()
                        self._buffer = self._buffer[self.max_chars:].lstrip()
                        chunks.append(chunk)
                break

        return chunks

    def flush(self) -> Optional[str]:
        """Flush any remaining buffered text when LLM completes (EOS)."""
        remainder = self._buffer.strip()
        self._buffer = ""
        return remainder if remainder else None


async def chunk_stream(token_stream: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
    """Helper generator wrapping an async token stream into phrase chunks."""
    chunker = TextChunker()
    async for token in token_stream:
        for chunk in chunker.append(token):
            if chunk:
                yield chunk
    final_chunk = chunker.flush()
    if final_chunk:
        yield final_chunk

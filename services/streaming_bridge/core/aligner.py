"""Stream text aligner for stable partial transcription.

Implements prefix stabilization, word normalization, conflict tracking,
and hallucination token filtering per port-stt.md Section 4.3.
"""

import re
import string
from typing import List, Optional, Tuple

# Hallucination / bracketed tokens dropped by filter
HALLUCINATION_PATTERNS = [
    re.compile(r"\[blank_audio\]", re.IGNORECASE),
    re.compile(r"\[inaudible\]", re.IGNORECASE),
    re.compile(r"\[music\]", re.IGNORECASE),
    re.compile(r"\[applause\]", re.IGNORECASE),
    re.compile(r"\[laughter\]", re.IGNORECASE),
    re.compile(r"\[silence\]", re.IGNORECASE),
    re.compile(r"\(music\)", re.IGNORECASE),
]


def normalize_word(w: str) -> str:
    """Normalize a word for comparison: lowercase and strip punctuation."""
    return w.lower().strip(string.punctuation)


def filter_hallucinations(text: str) -> Optional[str]:
    """Filter out known hallucination tokens and repetitive loops.
    
    Returns cleaned text, or None if the entire string should be dropped.
    """
    clean = text.strip()
    if not clean:
        return None

    # Drop explicit bracketed tags
    for pat in HALLUCINATION_PATTERNS:
        clean = pat.sub("", clean).strip()

    if not clean:
        return None

    # Detect repeated-word hallucination loops (e.g. "you you you you you")
    words = clean.split()
    if len(words) >= 5:
        norm_words = [normalize_word(w) for w in words]
        # If one word accounts for > 80% of words in sequence
        counts = {}
        for w in norm_words:
            counts[w] = counts.get(w, 0) + 1
        most_common_count = max(counts.values()) if counts else 0
        if most_common_count / len(words) > 0.8:
            return None

    return clean


class StreamTextAligner:
    """Aligns sequential speech decodes to provide stable partial and final text.
    
    - Confirmed text = Longest common word prefix of decodes, monotonically grows.
    - Tentative text = Newest words beyond the confirmed prefix.
    - Contradictions = Preserves confirmed prefix, aligns tentative, increments conflict count.
    """

    def __init__(self):
        self.confirmed_words: List[str] = []
        self.last_raw_words: List[str] = []
        self.last_raw_text: str = ""
        self.confirmed_conflicts: int = 0
        self.last_emitted_partial: str = ""

    def reset_segment(self):
        """Reset state for a new audio segment."""
        self.confirmed_words = []
        self.last_raw_words = []
        self.last_raw_text = ""
        self.last_emitted_partial = ""

    def update(self, raw_text: str) -> Tuple[str, str, bool]:
        """Process a new partial decode.
        
        Returns:
            (confirmed_str, tentative_str, is_updated)
        """
        cleaned = filter_hallucinations(raw_text)
        if not cleaned:
            # Drop empty / hallucination decodes
            return (" ".join(self.confirmed_words), "", False)

        new_words = cleaned.split()
        if not new_words:
            return (" ".join(self.confirmed_words), "", False)

        norm_new = [normalize_word(w) for w in new_words]
        norm_confirmed = [normalize_word(w) for w in self.confirmed_words]

        # Check for contradiction with confirmed prefix
        contradicts = False
        min_len = min(len(norm_confirmed), len(norm_new))
        for i in range(min_len):
            if norm_confirmed[i] != norm_new[i]:
                contradicts = True
                break

        if contradicts:
            self.confirmed_conflicts += 1
            # Preserve confirmed text; tentative begins after the confirmed word count
            # Find the best match for the last confirmed words in the new decode
            tentative_words = new_words[min_len:] if len(new_words) > min_len else []
        else:
            # Common prefix between last decode and this decode
            if self.last_raw_words:
                norm_last = [normalize_word(w) for w in self.last_raw_words]
                # Match common prefix
                common_len = 0
                max_check = min(len(norm_last), len(norm_new))
                while common_len < max_check and norm_last[common_len] == norm_new[common_len]:
                    common_len += 1

                # Confirmed text only grows
                if common_len > len(self.confirmed_words):
                    self.confirmed_words = new_words[:common_len]

            tentative_words = new_words[len(self.confirmed_words):]

        self.last_raw_words = new_words
        self.last_raw_text = cleaned

        confirmed_str = " ".join(self.confirmed_words)
        tentative_str = " ".join(tentative_words)
        self.last_emitted_partial = (f"{confirmed_str} {tentative_str}").strip()

        return (confirmed_str, tentative_str, True)

    def finalize(self, final_raw_text: Optional[str] = None) -> Tuple[str, bool]:
        """Produce the final segment transcript.
        
        Args:
            final_raw_text: Optional fresh decode text for final audio.
                            If None, reuses the last partial decode.
        Returns:
            (final_text, revised)
        """
        if final_raw_text is not None:
            cleaned = filter_hallucinations(final_raw_text)
            if cleaned:
                final_text = cleaned
            else:
                final_text = self.last_emitted_partial
        else:
            final_text = self.last_emitted_partial

        revised = bool(final_text.strip() != self.last_emitted_partial.strip())
        return (final_text.strip(), revised)

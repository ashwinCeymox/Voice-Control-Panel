import re
from typing import List, Optional, Set

# Regex to strip common quantization and file extensions
# Matches:
#   - Suffixes like: -q8_0, :q4_k_m, _q4_0, -iq3_s, -fp16, -int4, :latest
#   - Extensions like: .gguf, .bin
QUANT_PATTERN = re.compile(
    r"(?:[\-_:](?:i?q\d+_[a-z0-9_]+|i?q\d+[a-z0-9_]*|fp\d+|f\d+|int\d+|bf\d+|latest|default))+$",
    re.IGNORECASE,
)
FILE_EXT_PATTERN = re.compile(r"\.(?:gguf|bin|pt|onnx)$", re.IGNORECASE)


def normalize_model_id(model_id: str) -> str:
    """
    Normalize model ID by stripping file extensions and quant suffixes.
    Examples:
        'llama-3.2-1b-instruct-q8_0.gguf' -> 'llama-3.2-1b-instruct'
        'llama-3.2-1b-instruct:q4_k_m'    -> 'llama-3.2-1b-instruct'
        'whisper-base:latest'             -> 'whisper-base'
    """
    cleaned = (model_id or "").strip().lower()
    # 1. Strip file extension (.gguf, .bin, etc.)
    cleaned = FILE_EXT_PATTERN.sub("", cleaned)
    # 2. Strip docker tag if present (e.g., :q4_k_m or :latest)
    if ":" in cleaned:
        cleaned = cleaned.split(":", 1)[0]
    # 3. Strip hyphen/underscore quant suffix (e.g. -q8_0, _q4_k_m)
    cleaned = QUANT_PATTERN.sub("", cleaned)
    return cleaned.strip("-_")


def is_model_allowed(target_model: str, allowed_list: Optional[List[str]]) -> bool:
    """
    Check if target_model satisfies allowed_list.
    Returns True if:
      - allowed_list is None, empty, or contains '*'
      - target_model matches an item in allowed_list exactly
      - target_model is a quant/tag variant of an allowed base model family
    """
    if not allowed_list:
        return True

    cleaned_allowed = [a.strip() for a in allowed_list if a and a.strip()]
    if not cleaned_allowed or "*" in cleaned_allowed:
        return True

    target_raw = (target_model or "").strip().lower()
    if not target_raw:
        return False

    target_no_ext = FILE_EXT_PATTERN.sub("", target_raw)
    target_base = normalize_model_id(target_raw)

    for item in cleaned_allowed:
        item_raw = item.lower()
        item_no_ext = FILE_EXT_PATTERN.sub("", item_raw)

        # 1. Exact match (with or without extension)
        if target_raw == item_raw or target_no_ext == item_no_ext:
            return True

        # 2. Base family match
        item_base = normalize_model_id(item_raw)
        # If the allowed item specified a base family (e.g. "llama-3.2-1b-instruct")
        # and the target belongs to that family
        if target_base == item_base:
            return True

        # 3. Prefix matching for tags/suffixes (e.g. allowed "llama-3.2-1b" matching "llama-3.2-1b-instruct")
        if target_no_ext.startswith(f"{item_no_ext}:") or target_no_ext.startswith(
            f"{item_no_ext}-"
        ) or target_no_ext.startswith(f"{item_no_ext}_"):
            return True

    return False

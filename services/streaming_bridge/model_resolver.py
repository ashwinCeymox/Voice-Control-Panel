"""Generic LocalAI YAML -> GGUF path resolver for Native Streaming STT (:8089)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from app.core.config import settings


class ModelResolutionError(ValueError):
    """Raised when a streaming STT model name cannot be safely resolved to a GGUF file."""


def _parse_yaml_mapping(yaml_text: str) -> Dict[str, Any]:
    """Parse LocalAI model YAML safely using PyYAML if available, else minimal key parser."""
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(yaml_text)
        return data if isinstance(data, dict) else {}
    except ImportError:
        pass

    result: Dict[str, Any] = {}
    in_params = False
    params: Dict[str, Any] = {}
    for raw in yaml_text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if indent == 0:
            in_params = False
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k == "parameters" and not v:
                    in_params = True
                else:
                    result[k] = v
        elif in_params and ":" in line:
            k, v = line.split(":", 1)
            params[k.strip()] = v.strip().strip('"').strip("'")
    if params:
        result["parameters"] = params
    return result


def _is_model_allowlisted(
    model_name: str,
    allowed_models: Optional[Sequence[str]] = None,
) -> bool:
    """Enforce LOCALAI_ALLOWED_STT_MODELS / STREAMING_BRIDGE_ALLOWED_MODELS allow-listing."""
    if allowed_models is not None:
        allowed_set = {m.strip() for m in allowed_models if m and m.strip()}
        return model_name in allowed_set

    env_allowed = getattr(settings, "LOCALAI_ALLOWED_STT_MODELS", None)
    if env_allowed and str(env_allowed).strip():
        allowed_set = {m.strip() for m in str(env_allowed).split(",") if m.strip()}
        if model_name not in allowed_set:
            return False

    bridge_allowed = getattr(settings, "STREAMING_BRIDGE_ALLOWED_MODELS", "auto")
    if bridge_allowed and str(bridge_allowed).strip().lower() != "auto":
        allowed_set = {m.strip() for m in str(bridge_allowed).split(",") if m.strip()}
        if model_name not in allowed_set:
            return False

    return True


def resolve_streaming_model_gguf(
    model_name: str,
    *,
    models_dir: Optional[str | Path] = None,
    allowed_models: Optional[Sequence[str]] = None,
) -> str:
    """Resolve a LocalAI streaming STT model name to a validated GGUF file path.

    Reads `${LOCALAI_MODELS_PATH:-models}/<model_name>.yaml`, extracts `parameters.model`
    (falling back to top-level `model`), verifies the resolved path stays inside
    `models_dir` (no `..` traversal), and verifies the target GGUF file exists.
    """
    name = (model_name or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        raise ModelResolutionError(f"Invalid streaming model name: {model_name!r}")

    if not _is_model_allowlisted(name, allowed_models=allowed_models):
        raise ModelResolutionError(
            f"Model '{name}' is not allow-listed for native streaming STT"
        )

    raw_dir = models_dir or getattr(settings, "LOCALAI_MODELS_PATH", None) or os.environ.get("LOCALAI_MODELS_PATH") or "models"
    base_dir = Path(str(raw_dir)).resolve()

    yaml_path = (base_dir / f"{name}.yaml").resolve()
    if not yaml_path.is_file():
        alt_yaml = (base_dir / f"{name}.yml").resolve()
        if alt_yaml.is_file():
            yaml_path = alt_yaml

    if not yaml_path.is_relative_to(base_dir):
        raise ModelResolutionError(
            f"Model '{name}' YAML path escapes models dir: {yaml_path}"
        )
    if not yaml_path.is_file():
        raise ModelResolutionError(
            f"Model '{name}' YAML not found (checked: {yaml_path})"
        )

    parsed = _parse_yaml_mapping(yaml_path.read_text(encoding="utf-8", errors="replace"))
    backend = str(parsed.get("backend", "") or "").strip().lower()
    if backend and "parakeet" not in backend:
        raise ModelResolutionError(
            f"Model '{name}' backend '{backend}' in {yaml_path} is not a native parakeet streaming backend"
        )

    params = parsed.get("parameters")
    raw_model_val = ""
    if isinstance(params, dict) and params.get("model"):
        raw_model_val = str(params["model"]).strip()
    elif parsed.get("model"):
        raw_model_val = str(parsed["model"]).strip()

    if not raw_model_val:
        raise ModelResolutionError(
            f"Model '{name}' YAML ({yaml_path}) does not specify parameters.model or model"
        )

    target = Path(raw_model_val)
    resolved_gguf = target.resolve() if target.is_absolute() else (base_dir / target).resolve()

    if not resolved_gguf.is_relative_to(base_dir):
        raise ModelResolutionError(
            f"Model '{name}' resolved path '{resolved_gguf}' escapes models dir '{base_dir}' (from {yaml_path})"
        )

    if not resolved_gguf.is_file():
        raise ModelResolutionError(
            f"Model '{name}' GGUF missing: checked yaml={yaml_path}, resolved_path={resolved_gguf}"
        )

    return str(resolved_gguf)

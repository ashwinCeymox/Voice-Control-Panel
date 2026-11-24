"""Model gallery catalog, asynchronous installation jobs, and fit scoring."""

import logging
import asyncio
import time
import uuid
import re
import httpx
import aiosqlite
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Set
from fastapi import APIRouter, Depends, Query, BackgroundTasks, HTTPException
from app.api.system import compute_fit_score, get_mac_memory_info
from app.core.config import settings
from app.core.security import require_api_key, require_admin_key
from app.services.key_manager import ApiKeyRecord
from app.storage.database import get_db

logger = logging.getLogger("voice_agent.api.gallery")

router = APIRouter(tags=["Model Gallery & Installation"])
gallery_router = router

# In-memory install job store: {job_id: {status, progress, speed, eta, error}}
_INSTALL_JOBS: Dict[str, Dict[str, Any]] = {}


def _localai_headers() -> Dict[str, str]:
    if getattr(settings, "LOCALAI_API_KEY", None):
        return {"Authorization": f"Bearer {settings.LOCALAI_API_KEY}"}
    return {}


# Curated Gallery Models catalog vetted for local and cloud voice agents
GALLERY_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "sherpa-onnx-streaming-zipformer-en-20M",
        "name": "Sherpa ONNX Zipformer 20M (Streaming)",
        "category": "stt",
        "provider": "stream-stt",
        "kind": "streaming",
        "size_mb": 42,
        "ram_required_mb": 150,
        "description": "Ultra-low-latency real-time streaming speech-to-text (Sherpa Zipformer). Delivers live partial transcription chunks in under 50ms.",
        "license": "Apache 2.0",
        "quantization": "INT8",
        "tags": ["streaming", "recommended", "realtime", "ultra-light"],
        "download_url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17.tar.bz2",
    },
    {
        "id": "whisper-tiny",
        "name": "Whisper Tiny (Multilingual)",
        "category": "stt",
        "provider": "localai",
        "kind": "batch",
        "size_mb": 75,
        "ram_required_mb": 110,
        "description": "Ultra-lightweight STT model for low-latency batch audio transcription with minimal memory usage.",
        "license": "MIT",
        "quantization": "FP16/GGUF",
        "tags": ["ultra-light", "multilingual", "batch"],
        "download_url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin",
    },
    {
        "id": "whisper-tiny-en",
        "name": "Whisper Tiny (English)",
        "category": "stt",
        "provider": "localai",
        "kind": "batch",
        "size_mb": 75,
        "ram_required_mb": 110,
        "description": "Ultra-lightweight English-optimized speech-to-text model.",
        "license": "MIT",
        "quantization": "FP16/GGUF",
        "tags": ["ultra-light", "english", "batch"],
        "download_url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin",
    },
    {
        "id": "whisperx-tiny",
        "name": "WhisperX Tiny",
        "category": "stt",
        "provider": "localai",
        "kind": "batch",
        "size_mb": 75,
        "ram_required_mb": 110,
        "description": "Fast word-level aligned lightweight transcription model.",
        "license": "MIT",
        "quantization": "FP16/GGUF",
        "tags": ["ultra-light", "aligned", "batch"],
        "download_url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin",
    },
    {
        "id": "whisper-base",
        "name": "Whisper Base (Multilingual)",
        "category": "stt",
        "provider": "localai",
        "kind": "batch",
        "size_mb": 145,
        "ram_required_mb": 210,
        "description": "Fast multilingual speech-to-text for audio recording transcription. High accuracy and balanced memory footprint.",
        "license": "MIT",
        "quantization": "FP16/GGUF",
        "tags": ["recommended", "balanced", "multilingual", "batch"],
        "download_url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin",
    },
    {
        "id": "whisper-base-en",
        "name": "Whisper Base (English)",
        "category": "stt",
        "provider": "localai",
        "kind": "batch",
        "size_mb": 145,
        "ram_required_mb": 210,
        "description": "English-optimized balanced STT for conversational dialogue.",
        "license": "MIT",
        "quantization": "FP16/GGUF",
        "tags": ["recommended", "english", "balanced", "batch"],
        "download_url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin",
    },
    {
        "id": "whisper-small",
        "name": "Whisper Small (Multilingual)",
        "category": "stt",
        "provider": "localai",
        "kind": "batch",
        "size_mb": 480,
        "ram_required_mb": 650,
        "description": "High-accuracy multilingual STT. Major leap in recognition quality, noisy audio, and accent tolerance.",
        "license": "MIT",
        "quantization": "FP16/GGUF",
        "tags": ["high-accuracy", "multilingual", "batch"],
        "download_url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin",
    },
    {
        "id": "whisper-small-en",
        "name": "Whisper Small (English)",
        "category": "stt",
        "provider": "localai",
        "kind": "batch",
        "size_mb": 480,
        "ram_required_mb": 650,
        "description": "High-accuracy English-optimized speech-to-text model.",
        "license": "MIT",
        "quantization": "FP16/GGUF",
        "tags": ["high-accuracy", "english", "batch"],
        "download_url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin",
    },
    {
        "id": "whisper-medium",
        "name": "Whisper Medium",
        "category": "stt",
        "provider": "localai",
        "kind": "batch",
        "size_mb": 1500,
        "ram_required_mb": 2000,
        "description": "Deep recognition accuracy across dialects and heavy background noise.",
        "license": "MIT",
        "quantization": "FP16/GGUF",
        "tags": ["deep-accuracy", "multilingual", "batch"],
        "download_url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin",
    },
    {
        "id": "whisper-large-turbo",
        "name": "Whisper Large v3 Turbo",
        "category": "stt",
        "provider": "localai",
        "kind": "batch",
        "size_mb": 1600,
        "ram_required_mb": 2200,
        "description": "Flagship Whisper model (809M params). Fast decoding with near-human transcription accuracy.",
        "license": "MIT",
        "quantization": "FP16/GGUF",
        "tags": ["flagship", "turbo", "high-accuracy", "batch"],
        "download_url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin",
    },
    {
        "id": "llama-3.2-1b-instruct",
        "name": "Llama 3.2 1B Instruct",
        "category": "llm",
        "provider": "localai",
        "size_mb": 820,
        "ram_required_mb": 1200,
        "description": "Meta's official lightweight instruct LLM. Fast 1-2 sentence conversational responses optimized for voice dialog.",
        "license": "Llama 3.2 Community",
        "quantization": "Q4_K_M",
        "tags": ["recommended", "default", "voice-tuned"],
        "download_url": "https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_K_M.gguf",
    },
    {
        "id": "llama-3.2-3b-instruct",
        "name": "Llama 3.2 3B Instruct",
        "category": "llm",
        "provider": "localai",
        "size_mb": 1950,
        "ram_required_mb": 2550,
        "description": "Advanced conversational reasoning and tool calling. High quality voice dialogues with deep context.",
        "license": "Llama 3.2 Community",
        "quantization": "Q4_K_M",
        "tags": ["advanced", "reasoning"],
        "download_url": "https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
    },
    {
        "id": "qwen2.5-1.5b-instruct",
        "name": "Qwen 2.5 1.5B Instruct",
        "category": "llm",
        "provider": "localai",
        "size_mb": 980,
        "ram_required_mb": 1400,
        "description": "High-efficiency multilingual conversational LLM. Fast token generation and concise voice replies.",
        "license": "Apache 2.0",
        "quantization": "Q4_K_M",
        "tags": ["voice-tuned", "multilingual"],
        "download_url": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
    },
    {
        "id": "qwen2.5-3b-instruct",
        "name": "Qwen 2.5 3B Instruct",
        "category": "llm",
        "provider": "localai",
        "size_mb": 1950,
        "ram_required_mb": 2550,
        "description": "Strong reasoning, instruction-following, and natural dialogue capabilities for voice agents.",
        "license": "Apache 2.0",
        "quantization": "Q4_K_M",
        "tags": ["advanced", "multilingual"],
        "download_url": "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf",
    },
    {
        "id": "phi-2-chat",
        "name": "Phi-2 Chat (2.7B)",
        "category": "llm",
        "provider": "localai",
        "size_mb": 1800,
        "ram_required_mb": 2200,
        "description": "Microsoft's compact 2.7B reasoning model. Structured, articulate voice replies.",
        "license": "MIT",
        "quantization": "Q4_K_M",
        "tags": ["compact", "structured"],
        "download_url": "https://huggingface.co/TheBloke/phi-2-GGUF/resolve/main/phi-2.Q4_K_M.gguf",
    },
    {
        "id": "kokoro",
        "name": "Kokoro 82M TTS",
        "category": "tts",
        "provider": "local-tts",
        "size_mb": 350,
        "ram_required_mb": 380,
        "description": "Studio-grade 24kHz American and British voices via resident Kokoro engine (PyTorch on Linux, MLX on macOS).",
        "license": "Apache 2.0",
        "quantization": "FP32/BF16",
        "tags": ["recommended", "default", "studio-quality"],
    },
]


def validate_model_id(model_id: Any) -> str:
    """Validate model_id against path traversal, command injection, and abnormal length."""
    if not isinstance(model_id, str) or not model_id.strip():
        raise HTTPException(status_code=400, detail="Missing or invalid model_id")
    cleaned = model_id.strip()
    if (
        len(cleaned) > 128
        or not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_\-\.:]*$", cleaned)
        or ".." in cleaned
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid model_id format: must be alphanumeric and cannot contain path traversal sequences",
        )
    return cleaned


async def get_installed_models_data() -> Dict[str, List[Dict[str, Any]]]:
    """Query live providers and database registry for truly installed models."""
    stt_models: List[Dict[str, Any]] = []
    llm_models: List[Dict[str, Any]] = []
    tts_models: List[Dict[str, Any]] = []
    seen_ids = set()

    # 1. Query LocalAI on 8080
    installed_backends: set = set()
    model_backend_map: Dict[str, str] = {}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            sys_resp = await client.get(
                f"{settings.LOCALAI_BASE_URL}/system", headers=_localai_headers()
            )
            if sys_resp.status_code == 200:
                sys_json = sys_resp.json()
                for b in sys_json.get("backends", []):
                    installed_backends.add(b.lower())
                for lm in sys_json.get("loaded_models", []):
                    mid = lm.get("id")
                    b = lm.get("backend")
                    if mid and b:
                        model_backend_map[mid.lower()] = b.lower()
                        model_backend_map[mid.lower().split(":")[0]] = b.lower()
    except Exception:
        pass

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"{settings.LOCALAI_BASE_URL}/v1/models", headers=_localai_headers()
            )
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                for m in data:
                    raw_id = m.get("id")
                    if not raw_id:
                        continue
                    base_id = raw_id.split(":")[0] if ":" in raw_id else raw_id
                    if raw_id in seen_ids or base_id in seen_ids:
                        continue
                    seen_ids.add(raw_id)
                    seen_ids.add(base_id)
                    catalog_match = next(
                        (
                            c
                            for c in GALLERY_CATALOG
                            if c["id"] == raw_id or c["id"] == base_id
                        ),
                        None,
                    )
                    effective_id = catalog_match["id"] if catalog_match else raw_id
                    size_mb = catalog_match["size_mb"] if catalog_match else 500
                    display_name = (
                        catalog_match["name"] if catalog_match else effective_id
                    )
                    entry = {
                        "id": effective_id,
                        "name": display_name,
                        "provider": "localai",
                        "size_mb": size_mb,
                    }

                    lower_id = raw_id.lower()
                    if catalog_match and catalog_match.get("category"):
                        cat = catalog_match["category"]
                    elif any(
                        k in lower_id
                        for k in (
                            "whisper",
                            "parakeet",
                            "nemo",
                            "crispasr",
                            "stt",
                            "asr",
                            "transcription",
                        )
                    ):
                        cat = "stt"
                    elif any(
                        k in lower_id
                        for k in ("vits", "piper", "tts", "kokoro", "kitten")
                    ):
                        cat = "tts"
                    elif "silero" in lower_id or "vad" in lower_id:
                        continue  # Utility VAD model, not agent triad
                    else:
                        cat = "llm"

                    if cat == "stt":
                        model_backend = model_backend_map.get(raw_id.lower()) or model_backend_map.get(base_id.lower())
                        is_streaming = (
                            (catalog_match and catalog_match.get("kind") == "streaming")
                            or (model_backend and any(b in model_backend for b in ("nemo", "parakeet", "sherpa")))
                            or any(
                                k in lower_id
                                for k in (
                                    "realtime",
                                    "stream",
                                    "streaming",
                                    "online",
                                    "eou",
                                    "zipformer",
                                    "sherpa",
                                )
                            )
                        )
                        entry["kind"] = "streaming" if is_streaming else "batch"
                        if "parakeet" in lower_id or (model_backend and "parakeet" in model_backend):
                            if installed_backends and not any(
                                "parakeet" in b for b in installed_backends
                            ):
                                entry["available"] = False
                                entry["unavailable_reason"] = (
                                    "Missing backend: parakeet-cpp backend not installed in LocalAI"
                                )
                            else:
                                entry["available"] = True
                        elif "nemo" in lower_id or (model_backend and "nemo" in model_backend):
                            if installed_backends and not any(
                                "nemo" in b for b in installed_backends
                            ):
                                entry["available"] = False
                                entry["unavailable_reason"] = (
                                    "Missing backend: nemo-speech-cpp backend not installed in LocalAI"
                                )
                            else:
                                entry["available"] = True
                        elif "whisper" in lower_id or (model_backend and "whisper" in model_backend):
                            if installed_backends and not any(
                                "whisper" in b for b in installed_backends
                            ):
                                entry["available"] = False
                                entry["unavailable_reason"] = (
                                    "Missing backend: whisper backend not installed in LocalAI"
                                )
                            else:
                                entry["available"] = True
                        else:
                            entry["available"] = True
                        stt_models.append(entry)
                    elif cat == "tts":
                        tts_models.append(entry)
                    else:
                        llm_models.append(entry)
    except Exception:
        pass

    # 2. Query local-tts on 8880
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.LOCAL_TTS_HTTP_BASE_URL}/v1/models")
            if resp.status_code == 200:
                data = resp.json()
                raw = data if isinstance(data, list) else data.get("models", [])
                for m in raw:
                    mid = m.get("model_id") or m.get("id")
                    if mid and mid not in seen_ids:
                        seen_ids.add(mid)
                        catalog_match = next(
                            (c for c in GALLERY_CATALOG if c["id"] == mid), None
                        )
                        name = m.get("name") or (
                            catalog_match["name"] if catalog_match else mid.title()
                        )
                        size_mb = catalog_match["size_mb"] if catalog_match else 350
                        tts_models.append(
                            {
                                "id": mid,
                                "name": name,
                                "provider": "local-tts",
                                "size_mb": size_mb,
                            }
                        )
    except Exception:
        pass

    # 3. Query SQLite installed_models table for verified installed models
    try:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, name, category, provider, size_mb FROM installed_models"
            )
            rows = await cursor.fetchall()
            for r in rows:
                mid = r["id"]
                lower_mid = mid.lower()
                # Exclude phantom test fixtures and non-existent models
                if any(
                    k in lower_mid
                    for k in (
                        "probe-model",
                        "test-model",
                        "talk-model",
                        "kitten",
                        "vits-piper",
                    )
                ):
                    continue

                if mid not in seen_ids and lower_mid not in seen_ids:
                    # If model is LocalAI-backed, verify it is either reported by live LocalAI
                    # or physically exists on disk in models/
                    if r["provider"] == "localai":
                        from pathlib import Path
                        m_dir = Path("models")
                        has_file = (
                            mid in seen_ids
                            or lower_mid in seen_ids
                            or (m_dir / f"{mid}.gguf").is_file()
                            or (m_dir / f"{mid}.bin").is_file()
                            or (m_dir / f"{mid}.safetensors").is_file()
                            or (m_dir / f"{mid}.yaml").is_file()
                            or (m_dir / "llm" / f"{mid}.gguf").is_file()
                            or (m_dir / "llm" / f"{lower_mid}.gguf").is_file()
                            or (m_dir / "whisper" / f"{mid}.bin").is_file()
                            or (m_dir / "whisper" / f"{lower_mid}.bin").is_file()
                            or (m_dir / f"{mid}").is_dir()
                            or (m_dir / f"{lower_mid}.gguf").is_file()
                            or (m_dir / f"{lower_mid}.bin").is_file()
                            or (m_dir / f"{lower_mid}.safetensors").is_file()
                            or (m_dir / f"{lower_mid}.yaml").is_file()
                            or any(m_dir.glob(f"**/*{lower_mid}*"))
                        )
                        if not has_file and lower_mid in (
                            "llama-3.2-1b-instruct",
                            "whisper-base",
                            "whisper-tiny",
                            "qwen3.5-0.8b-artemis-q4",
                        ):
                            continue

                    seen_ids.add(mid)
                    seen_ids.add(lower_mid)
                    entry = {
                        "id": mid,
                        "name": r["name"],
                        "provider": r["provider"],
                        "size_mb": r["size_mb"],
                        "available": True,
                    }
                    cat = r["category"]
                    catalog_match = next(
                        (c for c in GALLERY_CATALOG if c["id"].lower() == lower_mid),
                        None,
                    )
                    if cat == "stt":
                        is_streaming = (
                            catalog_match and catalog_match.get("kind") == "streaming"
                        ) or any(
                            k in lower_mid
                            for k in (
                                "realtime",
                                "stream",
                                "streaming",
                                "zipformer",
                            )
                        )
                        entry["kind"] = "streaming" if is_streaming else "batch"
                        stt_models.append(entry)
                    elif cat == "tts":
                        tts_models.append(entry)
                    else:
                        llm_models.append(entry)
    except Exception:
        pass

    # 4. Check for streaming STT models in models/streaming-stt
    canonical_streaming_id = "sherpa-onnx-streaming-zipformer-en-20M"
    try:
        from pathlib import Path

        streaming_base = Path("models/streaming-stt")
        if streaming_base.exists():
            for folder in streaming_base.iterdir():
                if folder.is_dir() and (folder / "tokens.txt").exists():
                    if canonical_streaming_id not in seen_ids:
                        seen_ids.add(canonical_streaming_id)
                        seen_ids.add(canonical_streaming_id.lower())
                        seen_ids.add(folder.name)
                        stt_models.append(
                            {
                                "id": canonical_streaming_id,
                                "name": "Sherpa ONNX Zipformer 20M (Streaming)",
                                "provider": "stream-stt",
                                "kind": "streaming",
                                "size_mb": 42,
                                "available": True,
                            }
                        )
                    break
    except Exception:
        pass

    # 5. Direct disk inspection of models directory for GGUF and BIN weights
    try:
        from pathlib import Path

        models_dir = Path("models")
        if models_dir.is_dir():
            candidate_files = []
            for d in [models_dir, models_dir / "llm", models_dir / "whisper", models_dir / "parakeet-cpp"]:
                if d.is_dir():
                    candidate_files.extend(d.iterdir())
            for mf in candidate_files:
                if mf.is_file() and mf.suffix in (".gguf", ".bin", ".safetensors"):
                    if mf.name.startswith("._"):
                        continue
                    stem = mf.stem
                    lower_stem = stem.lower()
                    if any(p in lower_stem for p in ("part", "download", "tmp", "temp")):
                        continue
                    if stem not in seen_ids and lower_stem not in seen_ids:
                        seen_ids.add(stem)
                        seen_ids.add(lower_stem)
                        sz_mb = max(1, int(mf.stat().st_size / (1024 * 1024)))
                        catalog_match = next(
                            (c for c in GALLERY_CATALOG if c["id"].lower() == lower_stem),
                            None,
                        )
                        display = catalog_match["name"] if catalog_match else stem.replace("-", " ").title()
                        if any(k in lower_stem for k in ("whisper", "stt", "asr")):
                            stt_models.append({
                                "id": stem,
                                "name": display,
                                "provider": "localai",
                                "kind": "batch",
                                "size_mb": sz_mb,
                                "available": True,
                            })
                        else:
                            llm_models.append({
                                "id": stem,
                                "name": display,
                                "provider": "localai",
                                "size_mb": sz_mb,
                                "available": True,
                            })
    except Exception:
        pass

    # Ensure kind is tagged on all STT models
    for m in stt_models:
        lower_m_id = m.get("id", "").lower()
        is_streaming = (
            m.get("kind") == "streaming"
            or m.get("provider") == "stream-stt"
            or any(
                k in lower_m_id
                for k in (
                    "realtime",
                    "stream",
                    "streaming",
                    "zipformer",
                )
            )
        )
        m["kind"] = "streaming" if is_streaming else "batch"
        if m.get("available") is None:
            m["available"] = True

    for m in tts_models:
        if m.get("available") is None:
            m["available"] = True

    for m in llm_models:
        if m.get("available") is None:
            m["available"] = True

    # 6. Apply manual classification overrides from SQLite model_overrides
    try:
        from app.services.model_registry import model_registry

        overrides = await model_registry.get_manual_overrides()
        if overrides:
            final_stt = []
            final_llm = []
            final_tts = []
            for m in stt_models:
                mid = m.get("id", "").lower()
                ovr = overrides.get(mid) or overrides.get(mid.split(":")[0])
                task = (ovr.get("task") if ovr else None) or "stt"
                if task == "llm":
                    final_llm.append(m)
                elif task == "tts":
                    final_tts.append(m)
                else:
                    if ovr and ovr.get("kind"):
                        m["kind"] = ovr["kind"]
                    if ovr and ovr.get("display_name"):
                        m["name"] = ovr["display_name"]
                    final_stt.append(m)

            for m in llm_models:
                mid = m.get("id", "").lower()
                ovr = overrides.get(mid) or overrides.get(mid.split(":")[0])
                task = (ovr.get("task") if ovr else None) or "llm"
                if task == "stt":
                    m["kind"] = ovr.get("kind") or "batch"
                    if ovr.get("display_name"):
                        m["name"] = ovr["display_name"]
                    final_stt.append(m)
                elif task == "tts":
                    if ovr and ovr.get("display_name"):
                        m["name"] = ovr["display_name"]
                    final_tts.append(m)
                else:
                    if ovr and ovr.get("display_name"):
                        m["name"] = ovr["display_name"]
                    final_llm.append(m)

            for m in tts_models:
                mid = m.get("id", "").lower()
                ovr = overrides.get(mid) or overrides.get(mid.split(":")[0])
                task = (ovr.get("task") if ovr else None) or "tts"
                if task == "stt":
                    m["kind"] = ovr.get("kind") or "batch"
                    if ovr.get("display_name"):
                        m["name"] = ovr["display_name"]
                    final_stt.append(m)
                elif task == "llm":
                    if ovr and ovr.get("display_name"):
                        m["name"] = ovr["display_name"]
                    final_llm.append(m)
                else:
                    if ovr and ovr.get("display_name"):
                        m["name"] = ovr["display_name"]
                    final_tts.append(m)

            stt_models = final_stt
            llm_models = final_llm
            tts_models = final_tts
    except Exception:
        pass

    # Kokoro-only policy: restrict installed TTS models strictly to Kokoro
    tts_models = [m for m in tts_models if "kokoro" in m.get("id", "").lower()]
    if not any("kokoro" in m.get("id", "").lower() for m in tts_models):
        tts_models.append(
            {
                "id": "kokoro",
                "name": "Kokoro (Default)",
                "provider": "local-tts",
                "size_mb": 350,
                "available": True,
            }
        )

    res = {
        "stt": stt_models,
        "llm": llm_models,
        "tts": tts_models,
    }
    try:
        from app.services.model_manifest import model_manifest

        existing = model_manifest.load_from_file_sync() or {}
        merged_res: Dict[str, List[Dict[str, Any]]] = {}
        from pathlib import Path
        m_dir = Path("models")
        for cat in ("stt", "llm", "tts"):
            seen = {}
            for m in existing.get(cat, []):
                mid = m.get("id")
                if not mid:
                    continue
                lower_mid = mid.lower()
                if any(p in lower_mid for p in ("probe-model", "test-model", "talk-model", "mock")):
                    continue
                # Prune uninstalled legacy defaults from manifest
                if lower_mid in ("llama-3.2-1b-instruct", "whisper-base", "whisper-tiny", "qwen3.5-0.8b-artemis-q4"):
                    has_w = (
                        (m_dir / f"{mid}.gguf").is_file()
                        or (m_dir / f"{mid}.bin").is_file()
                        or (m_dir / f"{mid}.safetensors").is_file()
                        or (m_dir / f"{mid}.yaml").is_file()
                        or (m_dir / "llm" / f"{mid}.gguf").is_file()
                        or (m_dir / "llm" / f"{lower_mid}.gguf").is_file()
                        or (m_dir / "whisper" / f"{mid}.bin").is_file()
                        or (m_dir / "whisper" / f"{lower_mid}.bin").is_file()
                        or (m_dir / f"{mid}").is_dir()
                        or (m_dir / f"{lower_mid}.gguf").is_file()
                        or (m_dir / f"{lower_mid}.bin").is_file()
                        or (m_dir / f"{lower_mid}.safetensors").is_file()
                        or (m_dir / f"{lower_mid}.yaml").is_file()
                        or any(m_dir.glob(f"**/*{lower_mid}*"))
                    )
                    if not has_w:
                        continue
                seen[lower_mid] = dict(m)
            for m in res.get(cat, []):
                mid = m.get("id")
                if mid and not any(p in mid.lower() for p in ("probe-model", "test-model", "talk-model", "mock")):
                    seen[mid.lower()] = dict(m)
            merged_res[cat] = list(seen.values())

        merged_res["tts"] = [
            m for m in merged_res.get("tts", []) if "kokoro" in m.get("id", "").lower()
        ]
        if not any("kokoro" in m.get("id", "").lower() for m in merged_res["tts"]):
            merged_res["tts"].append(
                {
                    "id": "kokoro",
                    "name": "Kokoro (Default)",
                    "category": "tts",
                    "provider": "local-tts",
                    "size_mb": 350,
                    "available": True,
                }
            )

        model_manifest.save_to_file_sync(
            {
                "version": 1,
                "updated_at": time.time(),
                "stt": merged_res["stt"],
                "llm": merged_res["llm"],
                "tts": merged_res["tts"],
            }
        )
        res = merged_res
    except Exception:
        pass
    return res


# In-memory cache for LocalAI's dynamic available models catalog
_AVAILABLE_MODELS_CACHE: Optional[List[Dict[str, Any]]] = None
_AVAILABLE_MODELS_TIMESTAMP: float = 0.0
_AVAILABLE_MODELS_TTL: float = 300.0  # 5 minutes


async def fetch_localai_available_models() -> List[Dict[str, Any]]:
    global _AVAILABLE_MODELS_CACHE, _AVAILABLE_MODELS_TIMESTAMP
    now = time.time()
    if (
        _AVAILABLE_MODELS_CACHE is not None
        and (now - _AVAILABLE_MODELS_TIMESTAMP) < _AVAILABLE_MODELS_TTL
    ):
        return _AVAILABLE_MODELS_CACHE
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(
                f"{settings.LOCALAI_BASE_URL}/models/available",
                headers=_localai_headers(),
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    _AVAILABLE_MODELS_CACHE = data
                    _AVAILABLE_MODELS_TIMESTAMP = now
                    return data
    except Exception as e:
        logger.debug("LocalAI /models/available not reachable or timed out: %s", e)
    return _AVAILABLE_MODELS_CACHE or []


@router.get("/v1/gallery/models")
async def list_gallery_models(
    category: Optional[str] = Query(None, description="stt, llm, tts, or all"),
    fits_only: bool = Query(False),
    q: Optional[str] = Query(None),
    user: str = Depends(require_api_key),
):
    mem = get_mac_memory_info()
    avail_mb = mem["available_mb"]

    installed_data = await get_installed_models_data()
    installed_ids = {
        m["id"] for cat_models in installed_data.values() for m in cat_models
    }

    curated_map = {c["id"]: c for c in GALLERY_CATALOG}
    localai_models = await fetch_localai_available_models()

    seen_ids = set()
    results: List[Dict[str, Any]] = []

    # 1. Process all available models from LocalAI endpoint
    for lm in localai_models:
        raw_name = lm.get("name") or lm.get("id")
        if not raw_name or raw_name in seen_ids:
            continue

        lower_raw = raw_name.lower()
        if any(
            p in lower_raw
            for p in (
                "probe-model",
                "test-model",
                "talk-model",
                "mock",
                "kitten",
                "vits-piper",
            )
        ):
            continue

        base_name = raw_name.split(":")[0] if ":" in raw_name else raw_name
        curated = curated_map.get(raw_name) or curated_map.get(base_name)

        tags = [str(t).lower() for t in lm.get("tags", [])]
        name_lower = raw_name.lower()

        # Infer category
        if curated and curated.get("category"):
            cat = curated["category"]
        elif any(
            t in tags for t in ("stt", "asr", "speech-to-text", "transcription")
        ) or any(k in name_lower for k in ("whisper", "parakeet", "crispasr")):
            cat = "stt"
        elif any(t in tags for t in ("tts", "text-to-speech", "voice-model")) or any(
            k in name_lower for k in ("vits", "piper", "kokoro", "kitten")
        ):
            cat = "tts"
        elif "silero" in name_lower and "vad" in name_lower:
            continue  # Utility model, omit from agent model gallery
        else:
            cat = "llm"

        # Filter by category if requested
        if category and category != "all" and cat != category:
            continue

        # Filter by query if requested
        if q:
            ql = q.lower()
            desc = (lm.get("description") or "").lower()
            tag_str = " ".join(tags)
            if (
                ql not in name_lower
                and ql not in raw_name.lower()
                and ql not in desc
                and ql not in tag_str
            ):
                continue

        # Size & RAM estimation
        if curated and curated.get("size_mb"):
            sz = curated["size_mb"]
            ram_req = curated.get("ram_required_mb", int(sz * 1.35))
        else:
            sz = 500
            for t in tags + [name_lower]:
                mb_m = re.search(r"(\d+(?:\.\d+)?)\s*b\b", t)
                if mb_m:
                    b = float(mb_m.group(1))
                    sz = max(200, int(b * 650))
                    break
                mm_m = re.search(r"(\d+)\s*m\b", t)
                if mm_m:
                    m_val = int(mm_m.group(1))
                    sz = max(30, int(m_val * 1.5))
                    break
            if "whisper" in name_lower:
                if "tiny" in name_lower:
                    sz = 75
                elif "base" in name_lower:
                    sz = 145
                elif "small" in name_lower:
                    sz = 480
                elif "medium" in name_lower:
                    sz = 1500
                elif "large" in name_lower or "turbo" in name_lower:
                    sz = 1600
            elif "vits" in name_lower or "piper" in name_lower:
                sz = 80
            ram_req = int(sz * 1.35)

        fit_score = compute_fit_score(ram_req, avail_mb)
        if fits_only and fit_score < 60:
            continue

        disp_name = (
            curated["name"]
            if curated
            else raw_name.replace("-", " ").replace("_", " ").title()
        )
        quant = (
            curated.get("quantization")
            if curated
            else (
                "Q4_K_M"
                if "q4" in name_lower
                else (
                    "INT8"
                    if "int8" in name_lower
                    else ("FP16" if "fp16" in name_lower else "GGUF/Metal")
                )
            )
        )
        is_inst = (
            raw_name in installed_ids
            or raw_name.lower() in installed_ids
            or (":" not in raw_name and f"{raw_name}:latest" in installed_ids)
            or (raw_name.endswith(":latest") and base_name in installed_ids)
            or bool(lm.get("installed"))
        )

        desc = lm.get("description") or (
            curated.get("description")
            if curated
            else f"{cat.upper()} model in LocalAI gallery."
        )
        if len(desc) > 300:
            desc = desc[:297].rstrip() + "..."

        model_tags = lm.get("tags") or (curated.get("tags") if curated else [cat])
        if (
            curated
            and "recommended" in curated.get("tags", [])
            and "recommended" not in model_tags
        ):
            model_tags = ["recommended"] + list(model_tags)

        seen_ids.add(raw_name)
        if base_name != raw_name:
            seen_ids.add(base_name)

        model_kind = None
        if cat == "stt":
            lower_name = raw_name.lower()
            is_streaming = (
                (curated and curated.get("kind") == "streaming")
                or any(
                    k in lower_name
                    for k in (
                        "realtime",
                        "stream",
                        "streaming",
                        "eou",
                        "zipformer",
                        "online",
                    )
                )
                or (curated and curated.get("provider") == "stream-stt")
            )
            model_kind = "streaming" if is_streaming else "batch"

        results.append(
            {
                "id": raw_name,
                "name": disp_name,
                "category": cat,
                "kind": model_kind,
                "provider": curated.get("provider", "localai")
                if curated
                else "localai",
                "size_mb": sz,
                "ram_required_mb": ram_req,
                "description": desc.strip(),
                "license": lm.get("license")
                or (curated.get("license") if curated else "Open"),
                "quantization": quant,
                "tags": model_tags,
                "fit_score": fit_score,
                "fits_budget": fit_score >= 60,
                "installed": is_inst,
            }
        )

    # 2. Add any curated models that weren't in LocalAI's models/available (e.g. Kokoro TTS, Sherpa Zipformer)
    for c in GALLERY_CATALOG:
        cid = c["id"]
        if cid in seen_ids:
            continue
        if category and category != "all" and c["category"] != category:
            continue
        if q and q.lower() not in c["name"].lower() and q.lower() not in cid.lower():
            continue

        fit_score = compute_fit_score(c["ram_required_mb"], avail_mb)
        if fits_only and fit_score < 60:
            continue

        item = dict(c)
        if item.get("category") == "stt" and not item.get("kind"):
            is_streaming = (
                any(
                    k in cid.lower()
                    for k in (
                        "realtime",
                        "stream",
                        "streaming",
                        "eou",
                        "zipformer",
                        "online",
                    )
                )
                or item.get("provider") == "stream-stt"
            )
            item["kind"] = "streaming" if is_streaming else "batch"
        item["fit_score"] = fit_score
        item["fits_budget"] = fit_score >= 60
        item["installed"] = cid in installed_ids
        seen_ids.add(cid)
        results.append(item)

    # 3. Ensure any model installed in providers is visible if not yet in results
    for cat_name, cat_models in installed_data.items():
        if category and category != "all" and category != cat_name:
            continue
        for im in cat_models:
            imid = im["id"]
            lower_imid = imid.lower()
            if any(
                p in lower_imid
                for p in (
                    "probe-model",
                    "test-model",
                    "talk-model",
                    "mock",
                    "kitten",
                    "vits-piper",
                )
            ):
                continue
            if imid not in seen_ids:
                if (
                    q
                    and q.lower() not in im.get("name", "").lower()
                    and q.lower() not in imid.lower()
                ):
                    continue
                sz = im.get("size_mb", 500)
                ram_req = int(sz * 1.3)
                score = compute_fit_score(ram_req, avail_mb)
                m_kind = im.get("kind")
                if cat_name == "stt" and not m_kind:
                    is_streaming = (
                        any(
                            k in imid.lower()
                            for k in (
                                "realtime",
                                "stream",
                                "streaming",
                                "eou",
                                "zipformer",
                                "online",
                            )
                        )
                        or im.get("provider") == "stream-stt"
                    )
                    m_kind = "streaming" if is_streaming else "batch"
                results.append(
                    {
                        "id": imid,
                        "name": im.get("name", imid),
                        "category": cat_name,
                        "kind": m_kind,
                        "provider": im.get("provider", "localai"),
                        "size_mb": sz,
                        "ram_required_mb": ram_req,
                        "description": f"Installed {cat_name.upper()} model in {im.get('provider')}.",
                        "license": "Open",
                        "quantization": "Metal/Native",
                        "tags": ["installed", "active"],
                        "fit_score": score,
                        "fits_budget": score >= 60,
                        "installed": True,
                    }
                )
                seen_ids.add(imid)

    # Apply manual classification overrides from SQLite model_overrides to gallery results
    try:
        from app.services.model_registry import model_registry

        overrides = await model_registry.get_manual_overrides()
        for r in results:
            rid = r.get("id", "").lower()
            ovr = overrides.get(rid) or overrides.get(rid.split(":")[0])
            if ovr:
                if ovr.get("task"):
                    r["category"] = ovr["task"]
                if ovr.get("kind") is not None:
                    r["kind"] = ovr["kind"]
                if ovr.get("display_name"):
                    r["name"] = ovr["display_name"]
    except Exception:
        pass

    # Sort: Installed and recommended first, then by fit_score descending
    def sort_key(x):
        is_rec = 1 if "recommended" in x.get("tags", []) else 0
        is_inst = 1 if x.get("installed") else 0
        return (is_inst, is_rec, x.get("fit_score", 0))

    # Kokoro-only restriction: temporarily filter out all non-Kokoro TTS models from gallery listing
    results = [
        r for r in results
        if r.get("category") != "tts" or "kokoro" in r.get("id", "").lower()
    ]

    results.sort(key=sort_key, reverse=True)
    return {"models": results}


@router.post("/v1/models/install", status_code=202)
async def start_install_job(
    payload: Dict[str, Any],
    background_tasks: BackgroundTasks,
    admin: ApiKeyRecord = Depends(require_admin_key),
):
    raw_id = payload.get("model_id")
    model_id = validate_model_id(raw_id)
    lower_id = model_id.lower()

    catalog_model = next((c for c in GALLERY_CATALOG if c["id"] == model_id), None)
    model_name = payload.get("name") or (
        catalog_model["name"] if catalog_model else model_id
    )

    # Determine category accurately: payload > catalog > keyword heuristics (never default parakeet/whisper to llm)
    if payload.get("category"):
        category = payload["category"].lower()
    elif catalog_model and catalog_model.get("category"):
        category = catalog_model["category"].lower()
    elif any(
        k in lower_id
        for k in (
            "whisper",
            "parakeet",
            "nemo",
            "crispasr",
            "stt",
            "asr",
            "transcription",
        )
    ):
        category = "stt"
    elif any(
        k in lower_id
        for k in (
            "vits",
            "piper",
            "kokoro",
            "tts",
            "speech",
            "bark",
            "kitten",
            "neutts",
        )
    ):
        category = "tts"
    elif any(k in lower_id for k in ("silero", "vad")):
        category = "vad"
    else:
        category = "llm"

    # Enforce policy: disable download & installation of any TTS models
    if category == "tts" or any(
        k in lower_id
        for k in (
            "vits",
            "piper",
            "kokoro",
            "tts",
            "kitten",
            "pocket",
            "bark",
            "neutts",
        )
    ):
        raise HTTPException(
            status_code=400,
            detail="TTS model installation is currently disabled. Only the resident Kokoro TTS engine is supported.",
        )

    provider = payload.get("provider") or (
        catalog_model["provider"] if catalog_model else "localai"
    )
    size_mb = payload.get("size_mb") or (
        catalog_model["size_mb"] if catalog_model else 500
    )
    download_url = (
        payload.get("url")
        or payload.get("download_url")
        or (catalog_model.get("download_url") if catalog_model else None)
    )

    # Dynamically detect streaming capability from payload, gallery definition, backend, and tags
    detected_kind: Optional[str] = None
    if category == "stt":
        localai_available = await fetch_localai_available_models()
        gal_match = next(
            (
                m
                for m in localai_available
                if (m.get("id") == model_id or m.get("name") == model_id)
            ),
            None,
        )
        tags = [str(t).lower() for t in (gal_match.get("tags", []) if gal_match else [])]
        backend = (gal_match.get("backend") if gal_match else None) or payload.get("backend")
        is_streaming = (
            payload.get("kind") == "streaming"
            or (catalog_model and catalog_model.get("kind") == "streaming")
            or any(t in ("streaming", "realtime", "live", "stream", "online") for t in tags)
            or (backend and any(b in str(backend).lower() for b in ("nemo", "parakeet", "sherpa")))
            or any(
                k in lower_id
                for k in (
                    "streaming",
                    "realtime",
                    "stream",
                    "online",
                    "eou",
                    "zipformer",
                )
            )
        )
        detected_kind = "streaming" if is_streaming else "batch"

    job_id = f"job-{uuid.uuid4()}"[:14]
    _INSTALL_JOBS[job_id] = {
        "job_id": job_id,
        "model_id": model_id,
        "status": "queued",
        "progress_percent": 0,
        "downloaded_bytes": 0,
        "total_bytes": 1024 * 1024 * size_mb,
        "speed_mbps": 0.0,
        "eta_seconds": 6,
        "created_at": time.time(),
    }

    async def stream_download(url: str, dest_path: Path, job: dict, total_expected: int):
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = dest_path.with_name(f"{dest_path.name}.part")
        start_t = time.time()
        downloaded = 0
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(connect=30.0, read=None, write=30.0, pool=30.0),
        ) as client:
            async with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise RuntimeError(f"Download from {url} returned HTTP {response.status_code}")
                content_len = response.headers.get("Content-Length")
                total = int(content_len) if content_len and content_len.isdigit() else total_expected
                job["total_bytes"] = total

                with open(temp_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=1024 * 512):
                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)
                        job["downloaded_bytes"] = downloaded
                        elapsed = max(0.1, time.time() - start_t)
                        speed_bytes_sec = downloaded / elapsed
                        job["speed_mbps"] = round((speed_bytes_sec * 8) / (1024 * 1024), 2)
                        pct = min(85, int((downloaded / total) * 85)) if total > 0 else 50
                        job["progress_percent"] = max(job.get("progress_percent", 5), pct)
                        if speed_bytes_sec > 0 and total > downloaded:
                            job["eta_seconds"] = int((total - downloaded) / speed_bytes_sec)

        temp_path.replace(dest_path)

    async def run_install(
        jid: str,
        mid: str,
        m_name: str,
        cat: str,
        prov: str,
        sz: int,
        m_kind: Optional[str] = None,
        dl_url: Optional[str] = None,
    ):
        job = _INSTALL_JOBS[jid]
        job["status"] = "downloading"
        job["progress_percent"] = 5

        try:
            # 1. Streaming STT (Sherpa ONNX Zipformer)
            if prov == "stream-stt" or "sherpa-onnx-streaming-zipformer" in mid.lower():
                base_dir = Path("models/streaming-stt")
                base_dir.mkdir(parents=True, exist_ok=True)
                target_folder = base_dir / "sherpa-onnx-streaming-zipformer-en-20M-2023-02-17"
                if (target_folder / "tokens.txt").exists():
                    job["progress_percent"] = 85
                    await asyncio.sleep(0.3)
                else:
                    archive_path = base_dir / "sherpa-onnx-streaming-zipformer-en-20M-2023-02-17.tar.bz2"
                    archive_url = (
                        dl_url
                        or "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17.tar.bz2"
                    )
                    await stream_download(archive_url, archive_path, job, sz * 1024 * 1024)
                    job["status"] = "verifying"
                    job["progress_percent"] = 88
                    import tarfile
                    import sys
                    with tarfile.open(archive_path, "r:bz2") as tar:
                        if sys.version_info >= (3, 12):
                            tar.extractall(path=base_dir, filter="data")
                        else:
                            tar.extractall(path=base_dir)
                    archive_path.unlink(missing_ok=True)

            # 2. LocalAI LLM or Batch STT
            elif prov == "localai":
                models_dir = Path(settings.LOCALAI_MODELS_PATH if settings.LOCALAI_MODELS_PATH else "models")
                target_sub = models_dir / ("whisper" if cat == "stt" else "llm")
                target_sub.mkdir(parents=True, exist_ok=True)
                ext = "bin" if cat == "stt" else "gguf"
                weight_file = target_sub / f"{mid}.{ext}"
                config_file = models_dir / f"{mid}.yaml"

                if (weight_file.is_file() and weight_file.stat().st_size > 1024 * 1024) or ((models_dir / f"{mid}.{ext}").is_file() and (models_dir / f"{mid}.{ext}").stat().st_size > 1024 * 1024):
                    job["progress_percent"] = 85
                    await asyncio.sleep(0.3)
                elif dl_url:
                    await stream_download(dl_url, weight_file, job, sz * 1024 * 1024)
                else:
                    # Fallback to LocalAI /models/apply
                    gallery_id_map = {
                        "llama-3.2-1b-instruct": "llama-3.2-1b-instruct:q4_k_m",
                        "llama-3.2-3b-instruct": "llama-3.2-3b-instruct:q4_k_m",
                        "phi-2-chat": "phi-2-chat",
                    }
                    localai_target = gallery_id_map.get(mid, mid)
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        apply_resp = await client.post(
                            f"{settings.LOCALAI_BASE_URL}/models/apply",
                            json={"id": localai_target},
                            headers=_localai_headers(),
                        )
                        if apply_resp.status_code == 200:
                            apply_data = apply_resp.json()
                            job_uuid = apply_data.get("uuid")
                            if job_uuid:
                                for _ in range(120):
                                    await asyncio.sleep(1.0)
                                    job_resp = await client.get(
                                        f"{settings.LOCALAI_BASE_URL}/models/jobs/{job_uuid}",
                                        headers=_localai_headers(),
                                    )
                                    if job_resp.status_code == 200:
                                        r_job = job_resp.json()
                                        if r_job.get("error"):
                                            raise RuntimeError(f"LocalAI install error: {r_job.get('error')}")
                                        pct = int(r_job.get("progress", 0))
                                        job["progress_percent"] = max(10, min(85, pct))
                                        if r_job.get("processed"):
                                            break
                        else:
                            raise RuntimeError(f"LocalAI returned HTTP {apply_resp.status_code}: {apply_resp.text}")

                # Ensure LocalAI YAML config exists so LocalAI serves model under canonical mid
                effective_weight = weight_file if weight_file.exists() else (models_dir / f"{mid}.{ext}")
                if not config_file.exists() and effective_weight.exists():
                    rel_model_path = f"{target_sub.name}/{effective_weight.name}" if effective_weight.parent == target_sub else effective_weight.name
                    if cat == "stt":
                        yaml_content = f"name: {mid}\nbackend: whisper\nparameters:\n  model: {rel_model_path}\n"
                    else:
                        yaml_content = f"name: {mid}\nbackend: llama-cpp\nparameters:\n  model: {rel_model_path}\ncontext_size: 4096\nf16: true\n"
                    config_file.write_text(yaml_content, encoding="utf-8")

            # 3. Resident Kokoro TTS
            elif cat == "tts" or "kokoro" in mid.lower():
                for step in range(job["progress_percent"], 85, 20):
                    await asyncio.sleep(0.15)
                    job["progress_percent"] = step

            job["status"] = "verifying"
            job["progress_percent"] = 90
            await asyncio.sleep(0.3)

            job["status"] = "registering"
            job["progress_percent"] = 95

            # Persist model to SQLite installed_models and JSON manifest
            async with get_db() as db:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO installed_models (id, name, category, provider, size_mb)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (mid, m_name, cat, prov, sz),
                )
                await db.commit()

            from app.services.model_manifest import model_manifest
            await model_manifest.record_install(mid, m_name, cat, prov, sz, kind=m_kind)

            from app.services.model_registry import model_registry
            model_registry.invalidate_cache()

            job["status"] = "completed"
            job["progress_percent"] = 100
            job["eta_seconds"] = 0

        except Exception as exc:
            logger.exception("Model install failed for %s: %s", mid, exc)
            job["status"] = "failed"
            job["error_message"] = str(exc)

    background_tasks.add_task(
        run_install,
        job_id,
        model_id,
        model_name,
        category,
        provider,
        size_mb,
        detected_kind,
        download_url,
    )
    return {"job_id": job_id, "status": "queued"}


@router.get("/v1/models/install/{job_id}")
async def get_install_job_status(
    job_id: str,
    user: str = Depends(require_api_key),
):
    if job_id not in _INSTALL_JOBS:
        raise HTTPException(status_code=404, detail="Install job not found")
    return _INSTALL_JOBS[job_id]


def is_weight_file_shared(
    models_dir: Union[str, Path],
    weight_filename: str,
    excluding_config: Optional[str] = None,
    excluding_configs: Optional[Union[str, List[str], Set[str]]] = None,
) -> bool:
    """Check if another YAML config in models_dir references the same weight file (H6)."""
    models_path = Path(models_dir)
    if not models_path.is_dir():
        return False

    exclude_set = set()
    if excluding_config:
        exclude_set.add(Path(excluding_config).name.lower())
    if excluding_configs:
        if isinstance(excluding_configs, str):
            exclude_set.add(Path(excluding_configs).name.lower())
        else:
            for c in excluding_configs:
                exclude_set.add(Path(c).name.lower())

    for entry in models_path.iterdir():
        if entry.is_file() and entry.suffix in (".yaml", ".yml"):
            entry_name_lower = entry.name.lower()
            if entry_name_lower in exclude_set:
                continue
            # Also exclude companion gallery metadata files for the excluded configs
            if any(
                entry_name_lower == f"._gallery_{exc}"
                or entry_name_lower
                == f"._gallery_{exc.replace('.yaml', '').replace('.yml', '')}.yaml"
                for exc in exclude_set
            ):
                continue
            try:
                content = entry.read_text(encoding="utf-8", errors="ignore")
                m_match = re.search(r"model:\s*([^\s\n]+)", content)
                if m_match:
                    ref_val = m_match.group(1).strip()
                    if ref_val == weight_filename or Path(ref_val).name == Path(weight_filename).name:
                        return True
            except Exception:
                pass
    return False


@router.delete("/v1/models/{id}")
@router.delete("/models/{id}")
@router.delete("/v1/gallery/models/{id}")
@router.delete("/gallery/models/{id}")
async def uninstall_model(
    id: str,
    admin: ApiKeyRecord = Depends(require_admin_key),
):
    """Fully uninstall a model: unloads from RAM, triggers engine deletion, removes files from disk, and purges registry."""
    model_id = validate_model_id(id)

    # 1. Enforce active agent usage safeguard across all model engines
    from app.services.streaming_model_manager import streaming_model_manager

    in_use = await streaming_model_manager.get_agents_using_model(model_id)
    if in_use:
        from app.core.errors import ModelInUseError

        raise ModelInUseError(model_id, in_use)

    # 2. Check if model has streaming STT artifacts in models/streaming-stt/; purge directory
    try:
        await streaming_model_manager.delete_streaming_model(model_id)
    except Exception as e:
        logger.debug("Streaming model manager pre-cleanup for %s: %s", model_id, e)

    # 3. Unload model from memory if currently resident
    try:
        from app.services.residency_manager import residency_manager

        await residency_manager.unload_model(model_id, force=True)
    except Exception as e:
        logger.debug("Pre-uninstall memory flush for %s: %s", model_id, e)

    # 4. Resolve exact target ID in LocalAI (e.g., llama-3.2-1b-instruct -> llama-3.2-1b-instruct:q4_k_m)
    localai_target = model_id
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"{settings.LOCALAI_BASE_URL}/v1/models", headers=_localai_headers()
            )
            if resp.status_code == 200:
                models = [
                    m.get("id") for m in resp.json().get("data", []) if m.get("id")
                ]
                if model_id in models:
                    localai_target = model_id
                else:
                    for m in models:
                        if m.split(":")[0] == model_id:
                            localai_target = m
                            break
    except Exception:
        pass

    if localai_target != model_id:
        try:
            from app.services.residency_manager import residency_manager

            await residency_manager.unload_model(localai_target, force=True)
        except Exception:
            pass

    # 5. Trigger LocalAI model deletion API: POST /models/delete/{target} or DELETE /models/{target}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            del_resp = await client.post(
                f"{settings.LOCALAI_BASE_URL}/models/delete/{localai_target}",
                headers=_localai_headers(),
            )
            if del_resp.status_code == 200:
                del_data = del_resp.json()
                job_uuid = del_data.get("uuid")
                if job_uuid:
                    for _ in range(12):  # poll up to ~6s
                        await asyncio.sleep(0.5)
                        j_resp = await client.get(
                            f"{settings.LOCALAI_BASE_URL}/models/jobs/{job_uuid}",
                            headers=_localai_headers(),
                        )
                        if j_resp.status_code == 200 and j_resp.json().get("processed"):
                            break
            elif del_resp.status_code in (404, 405):
                await client.delete(
                    f"{settings.LOCALAI_BASE_URL}/models/{localai_target}",
                    headers=_localai_headers(),
                )
    except Exception as e:
        logger.warning("LocalAI deletion API warning for %s: %s", localai_target, e)

    # 6. Direct disk cleanup across candidate model directories
    candidate_dirs: List[Path] = []
    if settings.LOCALAI_MODELS_PATH:
        candidate_dirs.append(Path(settings.LOCALAI_MODELS_PATH))
    candidate_dirs.extend(
        [
            Path("../LocalAI/models"),
            Path("models"),
            Path("models/streaming-stt"),
            Path("models/llm"),
            Path("models/whisper"),
            Path("models/parakeet-cpp"),
        ]
    )

    for base_dir in candidate_dirs:
        try:
            if not base_dir.is_dir():
                continue
        except (OSError, PermissionError):
            continue
        try:
            # Check if this is a directory matching model_id (e.g. models/streaming-stt/<model_id>)
            target_subfolder = base_dir / model_id
            if target_subfolder.is_dir():
                shutil.rmtree(target_subfolder, ignore_errors=True)
                logger.info("Removed model subfolder: %s", target_subfolder)

            # Check for YAML configs to identify referenced weights
            base_prefix = model_id.split(":")[0]
            id_tokens = {
                model_id.lower(),
                localai_target.lower(),
                model_id.lower().replace(":", "-"),
                model_id.lower().replace(":", "_"),
                localai_target.lower().replace(":", "-"),
                localai_target.lower().replace(":", "_"),
                base_prefix.lower(),
            }

            yaml_candidates = []
            for ypath in base_dir.iterdir():
                if ypath.is_file() and ypath.suffix in (".yaml", ".yml"):
                    stem_clean = ypath.stem.lower()
                    if stem_clean.startswith("._gallery_"):
                        stem_clean = stem_clean[len("._gallery_"):]
                    if stem_clean in id_tokens or stem_clean.startswith(f"{base_prefix.lower()}-") or stem_clean.startswith(f"{base_prefix.lower()}_"):
                        yaml_candidates.append(ypath)

            referenced_bins = set()
            for ypath in yaml_candidates:
                if ypath.is_file():
                    try:
                        content = ypath.read_text(encoding="utf-8", errors="ignore")
                        m_match = re.search(r"model:\s*([^\s\n]+)", content)
                        if m_match:
                            referenced_bins.add(m_match.group(1).strip())
                    except Exception:
                        pass
                    try:
                        ypath.unlink(missing_ok=True)
                        logger.info("Removed model config: %s", ypath)
                    except Exception:
                        pass

            exclude_names = {p.name for p in yaml_candidates}

            # Remove referenced binary files only if NOT shared with another model config (H6)
            for bname in referenced_bins:
                if is_weight_file_shared(
                    str(base_dir), bname, excluding_configs=exclude_names
                ):
                    logger.info(
                        "Retaining shared weight file %s (still referenced by another model)",
                        bname,
                    )
                    continue

                bpath = (base_dir / bname).resolve()
                root_dir = base_dir.resolve()
                if not str(bpath).startswith(str(root_dir)):
                    logger.warning(
                        "Path traversal blocked during weight deletion: %s", bpath
                    )
                    continue

                if bpath.is_file():
                    try:
                        bpath.unlink(missing_ok=True)
                        logger.info("Removed model binary: %s", bpath)
                    except Exception:
                        pass

                # If binary was located in a subdirectory (e.g. parakeet-cpp/nemotron...), clean directory if empty
                if bpath.parent != root_dir and bpath.parent.is_dir():
                    try:
                        remaining = [
                            f for f in bpath.parent.iterdir() if f.name != ".DS_Store"
                        ]
                        if not remaining:
                            shutil.rmtree(bpath.parent, ignore_errors=True)
                            logger.info(
                                "Removed empty model subdirectory: %s", bpath.parent
                            )
                    except Exception:
                        pass

            # Also remove any direct weight files or partial downloads matching model_id / base_prefix
            for child in base_dir.iterdir():
                if not child.is_file():
                    continue
                c_stem = child.stem.lower()
                c_suffix = child.suffix.lower()
                # Direct weight binaries (.gguf, .bin, .safetensors)
                if c_suffix in (".gguf", ".bin", ".safetensors", ".pt", ".onnx"):
                    if c_stem in id_tokens or any(c_stem.startswith(f"{t}-") or c_stem.startswith(f"{t}_") for t in id_tokens):
                        if not is_weight_file_shared(str(base_dir), child.name, excluding_configs=exclude_names):
                            try:
                                child.unlink(missing_ok=True)
                                logger.info("Removed matched model weight file: %s", child)
                            except Exception:
                                pass
                elif c_suffix in (".yaml", ".partial", ".download", ".tmp"):
                    if any(t in c_stem for t in id_tokens):
                        try:
                            child.unlink(missing_ok=True)
                        except Exception:
                            pass
        except Exception as fs_err:
            logger.warning(
                "Filesystem cleanup warning for %s in %s: %s",
                model_id,
                base_dir,
                fs_err,
            )

    # 7. Remove from SQLite installed_models and JSON manifest
    try:
        base_prefix = model_id.split(":")[0]
        async with get_db() as db:
            await db.execute(
                """
                DELETE FROM installed_models
                WHERE LOWER(id) IN (?, ?, ?, ?)
                   OR LOWER(name) IN (?, ?, ?, ?)
                   OR LOWER(id) LIKE ?
                   OR LOWER(id) LIKE ?
                """,
                (
                    model_id.lower(),
                    localai_target.lower(),
                    model_id.lower().replace(":", "-"),
                    base_prefix.lower(),
                    model_id.lower(),
                    localai_target.lower(),
                    model_id.lower().replace(":", "-"),
                    base_prefix.lower(),
                    f"{base_prefix.lower()}:%",
                    f"{base_prefix.lower()}-%",
                ),
            )
            await db.commit()
    except Exception as e:
        logger.warning("Error deleting model from DB: %s", e)

    try:
        from app.services.model_manifest import model_manifest

        await model_manifest.record_uninstall(model_id)
        if localai_target and localai_target != model_id:
            await model_manifest.record_uninstall(localai_target)
        await model_manifest.sync_manifest()
    except Exception as e:
        logger.warning("Error recording model uninstall in manifest: %s", e)

    # 8. Invalidate unified model registry cache
    try:
        from app.services.model_registry import model_registry

        model_registry.invalidate_cache()
    except Exception:
        pass

    # 9. Clear from in-memory install jobs if present
    for jid, job in list(_INSTALL_JOBS.items()):
        if job.get("model_id") in (model_id, localai_target):
            _INSTALL_JOBS.pop(jid, None)

    return {"status": "uninstalled", "model_id": model_id}


@router.get("/v1/models/installed")
async def list_installed_models(user: str = Depends(require_api_key)):
    """List currently installed models across LocalAI and local-tts.

    Equivalent to calling get_installed_models_data():
    return await get_installed_models_data()
    """
    data = await get_installed_models_data()
    flattened = []
    for cat in ("stt", "llm", "tts"):
        for m in data.get(cat, []):
            entry = dict(m)
            entry["category"] = cat
            flattened.append(entry)
    data["models"] = flattened
    return data

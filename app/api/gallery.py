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


# Curated Gallery Models catalog vetted for 8GB Mac
GALLERY_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "sherpa-onnx-streaming-zipformer-en-20M-2023-02-17",
        "name": "Sherpa ONNX Zipformer 20M (Streaming)",
        "category": "stt",
        "provider": "stream-stt",
        "kind": "streaming",
        "size_mb": 42,
        "ram_required_mb": 150,
        "description": "Ultra-low-latency real-time streaming STT (Zipformer 20M). Delivers live partial captions with <50ms delay.",
        "license": "Apache 2.0",
        "quantization": "INT8",
        "tags": ["streaming", "recommended", "realtime", "ultra-light"],
    },
    {
        "id": "parakeet-cpp-realtime_eou_120m-v1",
        "name": "Parakeet Cpp Realtime Eou 120M (Batch)",
        "category": "stt",
        "provider": "localai",
        "kind": "batch",
        "size_mb": 500,
        "ram_required_mb": 420,
        "description": "NVIDIA Parakeet C++ 120M transducer with End-Of-Utterance (EOU) detection. Batch audio transcription via LocalAI Metal GPU.",
        "license": "NVIDIA Open Model License",
        "quantization": "Q4_0",
        "tags": ["batch", "parakeet", "eou", "metal"],
    },
    {
        "id": "parakeet-cpp-realtime-scene",
        "name": "Parakeet Cpp Realtime Scene",
        "category": "stt",
        "provider": "localai",
        "kind": "streaming",
        "size_mb": 500,
        "ram_required_mb": 450,
        "description": "NVIDIA Parakeet C++ Realtime streaming transducer with acoustic scene and EOU support via LocalAI Metal GPU.",
        "license": "NVIDIA Open Model License",
        "quantization": "FP16",
        "tags": ["streaming", "parakeet", "realtime", "eou", "metal"],
    },
    {
        "id": "nemo-speech-cpp-nemotron-3.5-asr-streaming-diarized",
        "name": "Nemotron 3.5 ASR Streaming Diarized",
        "category": "stt",
        "provider": "localai",
        "kind": "streaming",
        "size_mb": 500,
        "ram_required_mb": 550,
        "description": "NVIDIA Nemotron 3.5 ASR streaming speech-to-text with live diarization via nemo-speech-cpp on LocalAI Metal GPU.",
        "license": "NVIDIA Open Model License",
        "quantization": "Q8_0",
        "tags": ["streaming", "nemo", "diarized", "realtime", "metal"],
    },
    {
        "id": "whisper-base",
        "name": "Whisper Base (Multilingual)",
        "category": "stt",
        "provider": "localai",
        "size_mb": 145,
        "ram_required_mb": 210,
        "description": "Fast multilingual speech-to-text. High accuracy, RTF 0.07 on Apple Silicon Metal.",
        "license": "MIT",
        "quantization": "FP16/Metal",
        "tags": ["recommended", "balanced", "multilingual"],
    },
    {
        "id": "whisper-base-en",
        "name": "Whisper Base (English)",
        "category": "stt",
        "provider": "localai",
        "size_mb": 145,
        "ram_required_mb": 210,
        "description": "English-optimized balanced STT. High precision for conversational English dialogue.",
        "license": "MIT",
        "quantization": "FP16/Metal",
        "tags": ["recommended", "english", "balanced"],
    },
    {
        "id": "whisper-small",
        "name": "Whisper Small (Multilingual)",
        "category": "stt",
        "provider": "localai",
        "size_mb": 480,
        "ram_required_mb": 650,
        "description": "High-accuracy multilingual STT. Major leap in recognition quality, noisy audio, and accent tolerance.",
        "license": "MIT",
        "quantization": "FP16/Metal",
        "tags": ["recommended", "high-accuracy", "multilingual"],
    },
    {
        "id": "whisper-small-en",
        "name": "Whisper Small (English)",
        "category": "stt",
        "provider": "localai",
        "size_mb": 480,
        "ram_required_mb": 650,
        "description": "Specialized high-accuracy English STT (244M params). Exceptional word error rate.",
        "license": "MIT",
        "quantization": "FP16/Metal",
        "tags": ["high-accuracy", "english"],
    },
    {
        "id": "whisper-large-turbo",
        "name": "Whisper Large v3 Turbo",
        "category": "stt",
        "provider": "localai",
        "size_mb": 1600,
        "ram_required_mb": 2200,
        "description": "OpenAI's latest flagship Whisper model (809M params). 4x faster decoding with large-v3 accuracy.",
        "license": "MIT",
        "quantization": "FP16/Metal",
        "tags": ["flagship", "turbo", "high-accuracy"],
    },
    {
        "id": "whisper-medium",
        "name": "Whisper Medium (Multilingual)",
        "category": "stt",
        "provider": "localai",
        "size_mb": 1500,
        "ram_required_mb": 2100,
        "description": "Advanced multilingual speech recognition (769M params). Near-human transcription accuracy.",
        "license": "MIT",
        "quantization": "FP16/Metal",
        "tags": ["studio-accuracy", "multilingual"],
    },
    {
        "id": "whisper-tiny",
        "name": "Whisper Tiny (Multilingual)",
        "category": "stt",
        "provider": "localai",
        "size_mb": 75,
        "ram_required_mb": 110,
        "description": "Ultra-lightweight STT for minimum memory overhead. English & Multilingual.",
        "license": "MIT",
        "quantization": "FP16/Metal",
        "tags": ["ultra-light", "multilingual"],
    },
    {
        "id": "whisper-tiny-en",
        "name": "Whisper Tiny (English)",
        "category": "stt",
        "provider": "localai",
        "size_mb": 75,
        "ram_required_mb": 110,
        "description": "Ultra-fast English-specialized STT with lower hallucination rate than multilingual tiny.",
        "license": "MIT",
        "quantization": "FP16/Metal",
        "tags": ["ultra-light", "english", "fast"],
    },
    {
        "id": "whisperx-tiny",
        "name": "WhisperX Tiny",
        "category": "stt",
        "provider": "localai",
        "size_mb": 75,
        "ram_required_mb": 120,
        "description": "Ultra-fast speech recognition with phoneme-level timestamp alignment.",
        "license": "BSD-4",
        "quantization": "FP16/Metal",
        "tags": ["alignment", "fast"],
    },
    {
        "id": "qwen2.5-1.5b-instruct",
        "name": "Qwen 2.5 1.5B Instruct",
        "category": "llm",
        "provider": "localai",
        "size_mb": 980,
        "ram_required_mb": 1400,
        "description": "High-efficiency conversational LLM. 54 tokens/sec on M2 Metal. Best conversational voice flow.",
        "license": "Apache 2.0",
        "quantization": "Q4_K_M",
        "tags": ["recommended", "default", "voice-tuned"],
    },
    {
        "id": "gemma-3-270m-it-qat",
        "name": "Gemma 3 270M Instruct (QAT)",
        "category": "llm",
        "provider": "localai",
        "size_mb": 200,
        "ram_required_mb": 320,
        "description": "Google's 270M parameter ultra-compact open model. Sub-second conversational responses, 4-bit QAT precision.",
        "license": "Gemma Terms",
        "quantization": "QAT-4bit",
        "tags": ["sub-1b", "ultra-light", "google"],
    },
    {
        "id": "qwen3.5-0.8b-artemis-q4",
        "name": "Qwen 3.5 0.8B Instruct (Artemis)",
        "category": "llm",
        "provider": "localai",
        "size_mb": 550,
        "ram_required_mb": 780,
        "description": "0.8B parameter text chat, reasoning, and conversational LLM. 70+ tokens/sec on Apple Silicon Metal.",
        "license": "Apache 2.0",
        "quantization": "Q4_K_M",
        "tags": ["sub-1b", "fast", "voice-tuned"],
    },
    {
        "id": "supra2-100m-instruct",
        "name": "Supra2 100M Instruct",
        "category": "llm",
        "provider": "localai",
        "size_mb": 110,
        "ram_required_mb": 180,
        "description": "100M parameter ultra-compact conversational model. Instant inference for edge voice assistants.",
        "license": "Apache 2.0",
        "quantization": "F16/Metal",
        "tags": ["sub-1b", "ultra-light"],
    },
    {
        "id": "llama-3.2-1b-instruct",
        "name": "Llama 3.2 1B Instruct",
        "category": "llm",
        "provider": "localai",
        "size_mb": 820,
        "ram_required_mb": 1200,
        "description": "Meta's ultra-compact instruct model. Crisp 1-2 sentence replies, low RAM.",
        "license": "Llama 3.2 Community",
        "quantization": "Q4_K_M",
        "tags": ["ultra-light"],
    },
    {
        "id": "qwen2.5-3b-instruct",
        "name": "Qwen 2.5 3B Instruct",
        "category": "llm",
        "provider": "localai",
        "size_mb": 1950,
        "ram_required_mb": 2550,
        "description": "Advanced reasoning voice assistant. Tight fit on 8GB when paired with Kokoro.",
        "license": "Apache 2.0",
        "quantization": "Q4_K_M",
        "tags": ["high-iq"],
    },
    {
        "id": "phi-2-chat",
        "name": "Phi-2 Chat (2.7B)",
        "category": "llm",
        "provider": "localai",
        "size_mb": 1800,
        "ram_required_mb": 2200,
        "description": "Microsoft's 2.7B compact language model pre-loaded in LocalAI. Fast, structured voice replies.",
        "license": "MIT",
        "quantization": "Q4_K_M",
        "tags": ["default", "compact", "preloaded"],
    },
    {
        "id": "kokoro",
        "name": "Kokoro 82M TTS",
        "category": "tts",
        "provider": "local-tts",
        "size_mb": 350,
        "ram_required_mb": 380,
        "description": "Studio-grade 24kHz American and British voices via local-tts engine.",
        "license": "Apache 2.0",
        "quantization": "BF16",
        "tags": ["recommended", "default", "studio-quality"],
    },
    {
        "id": "kokoro-82m",
        "name": "Kokoro 82M TTS (Catalog)",
        "category": "tts",
        "provider": "local-tts",
        "size_mb": 350,
        "ram_required_mb": 380,
        "description": "Studio-grade 24kHz American and British voices via MLX / PyTorch.",
        "license": "Apache 2.0",
        "quantization": "BF16",
        "tags": ["recommended", "default", "studio-quality"],
    },
    {
        "id": "kitten",
        "name": "KittenTTS (15M)",
        "category": "tts",
        "provider": "local-tts",
        "size_mb": 60,
        "ram_required_mb": 150,
        "description": "Ultra-low-latency speech synthesis engine in local-tts.",
        "license": "Apache 2.0",
        "quantization": "BF16",
        "tags": ["ultra-light"],
    },
    {
        "id": "pocket",
        "name": "Pocket TTS",
        "category": "tts",
        "provider": "local-tts",
        "size_mb": 120,
        "ram_required_mb": 200,
        "description": "Lightweight, responsive speech synthesis engine in local-tts.",
        "license": "Apache 2.0",
        "quantization": "BF16",
        "tags": ["fast", "lightweight"],
    },
    {
        "id": "kitten-tts-micro",
        "name": "KittenTTS Micro (15M)",
        "category": "tts",
        "provider": "local-tts",
        "size_mb": 60,
        "ram_required_mb": 150,
        "description": "Instant synthesis with near-zero latency. Lower acoustic prosody than Kokoro.",
        "license": "Apache 2.0",
        "quantization": "BF16",
        "tags": ["ultra-light"],
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

    # 3. Query SQLite installed_models table for models installed through the UI/API
    try:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, name, category, provider, size_mb FROM installed_models"
            )
            rows = await cursor.fetchall()
            for r in rows:
                mid = r["id"]
                if mid not in seen_ids:
                    seen_ids.add(mid)
                    entry = {
                        "id": mid,
                        "name": r["name"],
                        "provider": r["provider"],
                        "size_mb": r["size_mb"],
                    }
                    cat = r["category"]
                    lower_mid = mid.lower()
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
                                "eou",
                                "zipformer",
                                "online",
                            )
                        )
                        entry["kind"] = "streaming" if is_streaming else "batch"
                        entry["available"] = True
                        stt_models.append(entry)
                    elif cat == "tts":
                        entry["available"] = True
                        tts_models.append(entry)
                    else:
                        entry["available"] = True
                        llm_models.append(entry)
    except Exception:
        pass

    # 4. Check for streaming STT models (via service and local directory)
    try:
        from app.providers.streaming_stt_client import StreamingSTTClient

        stt_client = StreamingSTTClient()
        models_data = await stt_client.list_models()
        if models_data:
            for sm in models_data:
                mid = sm.get("id")
                if mid and mid not in seen_ids:
                    seen_ids.add(mid)
                    stt_models.append(
                        {
                            "id": mid,
                            "name": sm.get("name", mid),
                            "provider": "stream-stt",
                            "kind": "streaming",
                            "size_mb": sm.get("size_mb", 42),
                        }
                    )
    except Exception:
        pass

    try:
        from pathlib import Path

        streaming_base = Path("models/streaming-stt")
        if streaming_base.exists():
            for folder in streaming_base.iterdir():
                if folder.is_dir() and (folder / "tokens.txt").exists():
                    mid = folder.name
                    if mid not in seen_ids:
                        seen_ids.add(mid)
                        stt_models.append(
                            {
                                "id": mid,
                                "name": "Sherpa ONNX Zipformer 20M (Streaming)",
                                "provider": "stream-stt",
                                "kind": "streaming",
                                "size_mb": 42,
                            }
                        )
                    alias = "sherpa-onnx-streaming-zipformer-en-20M"
                    if alias not in seen_ids:
                        seen_ids.add(alias)
                        stt_models.append(
                            {
                                "id": alias,
                                "name": "Sherpa ONNX Zipformer 20M (Streaming)",
                                "provider": "stream-stt",
                                "kind": "streaming",
                                "size_mb": 42,
                            }
                        )
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
                    "eou",
                    "zipformer",
                    "online",
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

    # 5. Apply manual classification overrides from SQLite model_overrides
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
            }
        )

    res = {
        "stt": stt_models,
        "llm": llm_models,
        "tts": tts_models,
    }
    try:
        if res.get("stt") or res.get("llm") or res.get("tts"):
            from app.services.model_manifest import model_manifest

            existing = model_manifest.load_from_file_sync() or {}
            merged_res: Dict[str, List[Dict[str, Any]]] = {}
            for cat in ("stt", "llm", "tts"):
                seen = {}
                for m in existing.get(cat, []):
                    if m.get("id"):
                        seen[m["id"].lower()] = dict(m)
                for m in res.get(cat, []):
                    if m.get("id"):
                        seen[m["id"].lower()] = dict(m)
                merged_res[cat] = list(seen.values())

            # Ensure manifest tts bucket is strictly Kokoro only
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
            or base_name in installed_ids
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

    async def run_install(
        jid: str, mid: str, m_name: str, cat: str, prov: str, sz: int, m_kind: Optional[str] = None
    ):
        job = _INSTALL_JOBS[jid]
        job["status"] = "downloading"

        # 1. Trigger real LocalAI model apply if provider is localai
        if prov == "localai":
            gallery_id_map = {
                "llama-3.2-1b-instruct": "llama-3.2-1b-instruct:q4_k_m",
                "llama-3.2-3b-instruct": "llama-3.2-3b-instruct:q4_k_m",
                "phi-2-chat": "phi-2-chat",
            }
            localai_target = gallery_id_map.get(mid, mid)
            try:
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
                            # Poll real LocalAI job
                            for _ in range(120):  # up to ~2 minutes
                                await asyncio.sleep(1.0)
                                job_resp = await client.get(
                                    f"{settings.LOCALAI_BASE_URL}/models/jobs/{job_uuid}",
                                    headers=_localai_headers(),
                                )
                                if job_resp.status_code == 200:
                                    r_job = job_resp.json()
                                    if r_job.get("error"):
                                        job["status"] = "failed"
                                        job["error_message"] = (
                                            f"LocalAI install error: {r_job.get('error')}"
                                        )
                                        logger.error(
                                            "LocalAI job %s failed: %s",
                                            job_uuid,
                                            r_job.get("error"),
                                        )
                                        return
                                    pct = int(r_job.get("progress", 0))
                                    job["progress_percent"] = max(10, min(95, pct))
                                    job["downloaded_bytes"] = r_job.get(
                                        "current_bytes",
                                        int(sz * 1024 * 1024 * (pct / 100.0)),
                                    )
                                    if r_job.get("processed"):
                                        break
                    else:
                        job["status"] = "failed"
                        job["error_message"] = (
                            f"LocalAI returned HTTP {apply_resp.status_code}: {apply_resp.text}"
                        )
                        return
            except Exception as e:
                logger.warning("LocalAI model apply warning: %s", e)

        # Fallback smooth progression for visual feedback if local-tts or rapid finish
        if job["progress_percent"] < 80:
            for step in range(job["progress_percent"], 85, 15):
                await asyncio.sleep(0.3)
                job["progress_percent"] = step

        job["status"] = "verifying"
        job["progress_percent"] = 90
        await asyncio.sleep(0.3)

        # Verify backend presence in LocalAI
        if prov == "localai":
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    sys_resp = await client.get(
                        f"{settings.LOCALAI_BASE_URL}/system",
                        headers=_localai_headers(),
                    )
                    if sys_resp.status_code == 200:
                        installed_backends = {
                            b.lower() for b in sys_resp.json().get("backends", [])
                        }
                        if "parakeet" in mid.lower() and not any(
                            "parakeet" in b for b in installed_backends
                        ):
                            job["status"] = "failed"
                            job["error_message"] = (
                                "Backend missing: parakeet-cpp backend not installed in LocalAI"
                            )
                            return
                        elif ("nemo" in mid.lower() or "nemotron" in mid.lower()) and not any(
                            "nemo" in b for b in installed_backends
                        ):
                            job["status"] = "failed"
                            job["error_message"] = (
                                "Backend missing: nemo-speech-cpp backend not installed in LocalAI"
                            )
                            return
            except Exception as e:
                logger.warning("LocalAI backend verification warning: %s", e)

        job["status"] = "registering"
        job["progress_percent"] = 95

        # Persist model to SQLite installed_models and JSON manifest
        try:
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
        except Exception as exc:
            job["status"] = "failed"
            job["error_message"] = str(exc)
            return

        job["status"] = "completed"
        job["progress_percent"] = 100
        job["eta_seconds"] = 0

    background_tasks.add_task(
        run_install, job_id, model_id, model_name, category, provider, size_mb, detected_kind
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
                if m_match and m_match.group(1).strip() == weight_filename:
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
            yaml_candidates = [
                base_dir / f"{model_id}.yaml",
                base_dir / f"{localai_target}.yaml",
                base_dir / f"._gallery_{model_id}.yaml",
                base_dir / f"._gallery_{localai_target}.yaml",
            ]
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

            # Also remove any partial downloads or stray files matching model_id
            for child in base_dir.iterdir():
                if model_id in child.name and (
                    child.suffix in (".yaml", ".partial", ".download")
                ):
                    try:
                        if child.is_file():
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
        async with get_db() as db:
            await db.execute(
                """
                DELETE FROM installed_models
                WHERE LOWER(id) IN (?, ?)
                   OR LOWER(name) IN (?, ?)
                   OR id = ?
                   OR id = ?
                """,
                (
                    model_id.lower(),
                    localai_target.lower(),
                    model_id.lower(),
                    localai_target.lower(),
                    model_id,
                    localai_target,
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

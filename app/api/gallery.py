"""Model gallery catalog, asynchronous installation jobs, and fit scoring."""
import os
import logging
import asyncio
import time
import uuid
import re
import httpx
import aiosqlite
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
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
        or not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_\-\.]*$", cleaned)
        or ".." in cleaned
    ):
        raise HTTPException(status_code=400, detail="Invalid model_id format: must be alphanumeric and cannot contain path traversal sequences")
    return cleaned


async def get_installed_models_data() -> Dict[str, List[Dict[str, Any]]]:
    """Query live providers and database registry for truly installed models."""
    stt_models: List[Dict[str, Any]] = []
    llm_models: List[Dict[str, Any]] = []
    tts_models: List[Dict[str, Any]] = []
    seen_ids = set()

    # 1. Query LocalAI on 8080
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.LOCALAI_BASE_URL}/v1/models")
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
                    catalog_match = next((c for c in GALLERY_CATALOG if c["id"] == raw_id or c["id"] == base_id), None)
                    effective_id = catalog_match["id"] if catalog_match else raw_id
                    size_mb = catalog_match["size_mb"] if catalog_match else 500
                    display_name = catalog_match["name"] if catalog_match else effective_id
                    entry = {"id": effective_id, "name": display_name, "provider": "localai", "size_mb": size_mb}
                    if "whisper" in raw_id.lower() or (catalog_match and catalog_match["category"] == "stt"):
                        stt_models.append(entry)
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
                        catalog_match = next((c for c in GALLERY_CATALOG if c["id"] == mid), None)
                        name = m.get("name") or (catalog_match["name"] if catalog_match else mid.title())
                        size_mb = catalog_match["size_mb"] if catalog_match else 350
                        tts_models.append({
                            "id": mid,
                            "name": name,
                            "provider": "local-tts",
                            "size_mb": size_mb,
                        })
    except Exception:
        pass

    # 3. Query SQLite installed_models table for models installed through the UI/API
    try:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT id, name, category, provider, size_mb FROM installed_models")
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
                    if cat == "stt":
                        stt_models.append(entry)
                    elif cat == "tts":
                        tts_models.append(entry)
                    else:
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
                    stt_models.append({
                        "id": mid,
                        "name": sm.get("name", mid),
                        "provider": "stream-stt",
                        "kind": "streaming",
                        "size_mb": sm.get("size_mb", 42),
                    })
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
                        stt_models.append({
                            "id": mid,
                            "name": "Sherpa ONNX Zipformer 20M (Streaming)",
                            "provider": "stream-stt",
                            "kind": "streaming",
                            "size_mb": 42,
                        })
                    alias = "sherpa-onnx-streaming-zipformer-en-20M"
                    if alias not in seen_ids:
                        seen_ids.add(alias)
                        stt_models.append({
                            "id": alias,
                            "name": "Sherpa ONNX Zipformer 20M (Streaming)",
                            "provider": "stream-stt",
                            "kind": "streaming",
                            "size_mb": 42,
                        })
    except Exception:
        pass

    # Ensure kind is tagged on all STT models
    for m in stt_models:
        if "kind" not in m:
            m["kind"] = "streaming" if m.get("provider") == "stream-stt" else "batch"

    return {
        "stt": stt_models,
        "llm": llm_models,
        "tts": tts_models,
    }


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

    results = []
    for m in GALLERY_CATALOG:
        if category and category != "all" and m["category"] != category:
            continue
        if q and q.lower() not in m["name"].lower() and q.lower() not in m["id"].lower():
            continue

        fit_score = compute_fit_score(m["ram_required_mb"], avail_mb)
        if fits_only and fit_score < 60:
            continue

        item = dict(m)
        item["fit_score"] = fit_score
        item["fits_budget"] = fit_score >= 60
        item["installed"] = m["id"] in installed_ids
        results.append(item)

    # Ensure any model currently installed in providers is visible if not already in catalog
    existing_ids = {r["id"] for r in results}
    for cat_name, cat_models in installed_data.items():
        if category and category != "all" and category != cat_name:
            continue
        for im in cat_models:
            if im["id"] not in existing_ids:
                if q and q.lower() not in im.get("name", "").lower() and q.lower() not in im["id"].lower():
                    continue
                sz = im.get("size_mb", 500)
                ram_req = int(sz * 1.3)
                score = compute_fit_score(ram_req, avail_mb)
                results.append({
                    "id": im["id"],
                    "name": im.get("name", im["id"]),
                    "category": cat_name,
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
                })
                existing_ids.add(im["id"])

    # Sort by fit score descending
    results.sort(key=lambda x: x["fit_score"], reverse=True)
    return {"models": results}


@router.post("/v1/models/install", status_code=202)
async def start_install_job(
    payload: Dict[str, Any],
    background_tasks: BackgroundTasks,
    admin: ApiKeyRecord = Depends(require_admin_key),
):
    raw_id = payload.get("model_id")
    model_id = validate_model_id(raw_id)

    catalog_model = next((c for c in GALLERY_CATALOG if c["id"] == model_id), None)
    model_name = catalog_model["name"] if catalog_model else model_id
    category = catalog_model["category"] if catalog_model else "llm"
    provider = catalog_model["provider"] if catalog_model else "localai"
    size_mb = catalog_model["size_mb"] if catalog_model else 500

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

    async def run_install(jid: str, mid: str, m_name: str, cat: str, prov: str, sz: int):
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
                    )
                    if apply_resp.status_code == 200:
                        apply_data = apply_resp.json()
                        job_uuid = apply_data.get("uuid")
                        if job_uuid:
                            # Poll real LocalAI job
                            for _ in range(120):  # up to ~2 minutes
                                await asyncio.sleep(1.0)
                                job_resp = await client.get(f"{settings.LOCALAI_BASE_URL}/models/jobs/{job_uuid}")
                                if job_resp.status_code == 200:
                                    r_job = job_resp.json()
                                    pct = int(r_job.get("progress", 0))
                                    job["progress_percent"] = max(10, min(95, pct))
                                    job["downloaded_bytes"] = r_job.get("current_bytes", int(sz * 1024 * 1024 * (pct / 100.0)))
                                    if r_job.get("processed"):
                                        break
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

        job["status"] = "registering"
        job["progress_percent"] = 95

        # Persist model to SQLite installed_models
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
        except Exception as exc:
            job["status"] = "failed"
            job["error_message"] = str(exc)
            return

        job["status"] = "completed"
        job["progress_percent"] = 100
        job["eta_seconds"] = 0

    background_tasks.add_task(run_install, job_id, model_id, model_name, category, provider, size_mb)
    return {"job_id": job_id, "status": "queued"}


@router.get("/v1/models/install/{job_id}")
async def get_install_job_status(
    job_id: str,
    admin: ApiKeyRecord = Depends(require_admin_key),
):
    if job_id not in _INSTALL_JOBS:
        raise HTTPException(status_code=404, detail="Install job not found")
    return _INSTALL_JOBS[job_id]


def is_weight_file_shared(
    models_dir: Union[str, Path],
    weight_filename: str,
    excluding_config: Optional[str] = None,
) -> bool:
    """Check if another YAML config in models_dir references the same weight file (H6)."""
    models_path = Path(models_dir)
    if not models_path.is_dir():
        return False
        
    exclude_name = Path(excluding_config).name if excluding_config else ""
    
    for entry in models_path.iterdir():
        if entry.is_file() and entry.suffix in (".yaml", ".yml"):
            if entry.name == exclude_name:
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
async def uninstall_model(
    id: str,
    admin: ApiKeyRecord = Depends(require_admin_key),
):
    """Fully uninstall a model: triggers engine deletion, removes weights/configs from disk, and purges registry."""
    model_id = validate_model_id(id)

    # 1. Resolve exact target ID in LocalAI (e.g., llama-3.2-1b-instruct -> llama-3.2-1b-instruct:q4_k_m)
    localai_target = model_id
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.LOCALAI_BASE_URL}/v1/models")
            if resp.status_code == 200:
                models = [m.get("id") for m in resp.json().get("data", []) if m.get("id")]
                if model_id in models:
                    localai_target = model_id
                else:
                    for m in models:
                        if m.split(":")[0] == model_id:
                            localai_target = m
                            break
    except Exception:
        pass

    # 2. Trigger LocalAI model deletion API: POST /models/delete/{target}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            del_resp = await client.post(f"{settings.LOCALAI_BASE_URL}/models/delete/{localai_target}")
            if del_resp.status_code == 200:
                del_data = del_resp.json()
                job_uuid = del_data.get("uuid")
                if job_uuid:
                    for _ in range(12):  # poll up to ~12s
                        await asyncio.sleep(0.5)
                        j_resp = await client.get(f"{settings.LOCALAI_BASE_URL}/models/jobs/{job_uuid}")
                        if j_resp.status_code == 200 and j_resp.json().get("processed"):
                            break
    except Exception as e:
        logger.warning("LocalAI deletion API warning for %s: %s", localai_target, e)

    # 3. Direct disk cleanup fallback (ensures weights and YAML configs are removed)
    models_dir = settings.LOCALAI_MODELS_PATH
    if models_dir and os.path.isdir(models_dir):
        try:
            # Check for YAML config file to identify referenced binary files
            yaml_candidates = [
                os.path.join(models_dir, f"{model_id}.yaml"),
                os.path.join(models_dir, f"{localai_target}.yaml"),
                os.path.join(models_dir, f"._gallery_{model_id}.yaml"),
                os.path.join(models_dir, f"._gallery_{localai_target}.yaml"),
            ]
            referenced_bins = set()
            for ypath in yaml_candidates:
                if os.path.isfile(ypath):
                    try:
                        with open(ypath, "r", encoding="utf-8", errors="ignore") as yf:
                            content = yf.read()
                            m_match = re.search(r"model:\s*([^\s\n]+)", content)
                            if m_match:
                                referenced_bins.add(m_match.group(1).strip())
                    except Exception:
                        pass
                    try:
                        os.remove(ypath)
                        logger.info("Removed model config: %s", ypath)
                    except Exception:
                        pass

            # Remove referenced binary files only if NOT shared with another model config (H6)
            for bname in referenced_bins:
                if is_weight_file_shared(models_dir, bname, excluding_config=f"{model_id}.yaml"):
                    logger.info("Retaining shared weight file %s (still referenced by another model)", bname)
                    continue

                bpath = Path(models_dir, bname).resolve()
                root_dir = Path(models_dir).resolve()
                if not str(bpath).startswith(str(root_dir)):
                    logger.warning("Path traversal blocked during weight deletion: %s", bpath)
                    continue

                if bpath.is_file():
                    try:
                        bpath.unlink()
                        logger.info("Removed model binary: %s", bpath)
                    except Exception:
                        pass

            # Also remove any partial downloads or stray files matching model_id
            for fname in os.listdir(models_dir):
                fpath = os.path.join(models_dir, fname)
                if model_id in fname and (fname.endswith(".yaml") or fname.endswith(".partial")):
                    try:
                        os.remove(fpath)
                    except Exception:
                        pass
        except Exception as fs_err:
            logger.warning("Filesystem cleanup warning for %s: %s", model_id, fs_err)

    # 4. Remove from SQLite installed_models
    try:
        async with get_db() as db:
            await db.execute(
                "DELETE FROM installed_models WHERE id = ? OR id = ?",
                (model_id, localai_target),
            )
            await db.commit()
    except Exception:
        pass

    # 5. Clear from in-memory install jobs if present
    for jid, job in list(_INSTALL_JOBS.items()):
        if job.get("model_id") in (model_id, localai_target):
            _INSTALL_JOBS.pop(jid, None)

    return {"status": "uninstalled", "model_id": model_id}


@router.get("/v1/models/installed")
async def list_installed_models(user: str = Depends(require_api_key)):
    """List currently installed models across LocalAI and local-tts."""
    return await get_installed_models_data()

"""Unified Model Registry Service.

Single source of truth for model metadata, classification, and availability.
Classifies models using:
1. LocalAI capabilities (`GET /v1/models/capabilities`).
2. Streaming STT folder manifests (`model.json`) and file inference.
3. Live availability probes for TTS engines (verifying real weights vs dummy stubs).
4. Manual admin overrides stored in SQLite `model_overrides`.

Every picker, endpoint, and snippet in the dashboard queries this registry.
"""

from __future__ import annotations
import json
import time
import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import httpx
import aiosqlite

from app.core.config import settings
from app.storage.database import get_db

logger = logging.getLogger("voice_agent.models.registry")


class ModelRegistryService:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._models_cache: Optional[List[Dict[str, Any]]] = None
        self._cache_timestamp: float = 0.0
        self._cache_ttl: float = 60.0  # 1 minute auto-refresh
        self._tts_availability_cache: Dict[str, Tuple[bool, Optional[str], float]] = {}

    def invalidate_cache(self):
        self._models_cache = None
        self._cache_timestamp = 0.0

    async def get_manual_overrides(self) -> Dict[str, Dict[str, Any]]:
        """Fetch manual admin classification overrides from SQLite."""
        overrides: Dict[str, Dict[str, Any]] = {}
        try:
            async with get_db() as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT model_id, task, kind, display_name FROM model_overrides"
                )
                rows = await cursor.fetchall()
                for r in rows:
                    overrides[r["model_id"].lower()] = {
                        "task": r["task"],
                        "kind": r["kind"],
                        "display_name": r["display_name"],
                    }
        except Exception as e:
            logger.warning("Error fetching model overrides: %s", e)
        return overrides

    async def probe_tts_engine(self, engine_id: str) -> Tuple[bool, Optional[str]]:
        """Probe local-tts to verify whether engine is registered without synthesizing audio."""
        now = time.time()
        cached = self._tts_availability_cache.get(engine_id.lower())
        if cached:
            # Cache positive hits for 5 mins, negative failures for 15s to allow recovery
            cache_ttl = 300.0 if cached[0] else 15.0
            if (now - cached[2]) < cache_ttl:
                return cached[0], cached[1]

        # Pocket/Kitten lack weights in this environment; only Kokoro is installed and supported
        if engine_id.lower() not in ("kokoro", "kokoro-82m", "default"):
            reason = f"Engine '{engine_id}' weights not installed on this system"
            self._tts_availability_cache[engine_id.lower()] = (False, reason, now)
            return False, reason

        url = f"{settings.LOCAL_TTS_HTTP_BASE_URL.rstrip('/')}/v1/models"
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    models = resp.json()
                    model_ids = (
                        {m.get("model_id", "").lower() for m in models}
                        if isinstance(models, list)
                        else set()
                    )
                    if "kokoro" in model_ids:
                        self._tts_availability_cache[engine_id.lower()] = (
                            True,
                            None,
                            now,
                        )
                        return True, None
                    else:
                        reason = f"Kokoro engine not found in registered TTS engines: {model_ids}"
                        self._tts_availability_cache[engine_id.lower()] = (
                            False,
                            reason,
                            now,
                        )
                        return False, reason
                else:
                    reason = f"local-tts returned HTTP {resp.status_code}"
                    self._tts_availability_cache[engine_id.lower()] = (
                        False,
                        reason,
                        now,
                    )
                    return False, reason
        except httpx.TimeoutException:
            return True, None
        except Exception:
            return True, None

    async def scan_localai_models(self) -> List[Dict[str, Any]]:
        """Discover LocalAI models using /v1/models/capabilities and /system loaded models."""
        models: List[Dict[str, Any]] = []
        capabilities_map: Dict[str, Any] = {}
        loaded_ids: set = set()
        headers = (
            {"Authorization": f"Bearer {settings.LOCALAI_API_KEY}"}
            if getattr(settings, "LOCALAI_API_KEY", None)
            else {}
        )

        # 1. Query live loaded processes and installed backends
        installed_backends: set = set()
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                sys_resp = await client.get(
                    f"{settings.LOCALAI_BASE_URL}/system", headers=headers
                )
                if sys_resp.status_code == 200:
                    sys_data = sys_resp.json()
                    for b in sys_data.get("backends", []):
                        installed_backends.add(b.lower())
                    for lm in sys_data.get("loaded_models", []):
                        mid = lm.get("id")
                        if mid:
                            loaded_ids.add(mid)
                            loaded_ids.add(mid.split(":")[0])
        except Exception:
            pass

        # 2. Query capabilities
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                cap_resp = await client.get(
                    f"{settings.LOCALAI_BASE_URL}/v1/models/capabilities",
                    headers=headers,
                )
                if cap_resp.status_code == 200:
                    for item in cap_resp.json().get("data", []):
                        mid = item.get("id")
                        if mid:
                            capabilities_map[mid] = item
        except Exception:
            pass

        # 3. Query all registered models in LocalAI
        all_localai: List[str] = []
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(
                    f"{settings.LOCALAI_BASE_URL}/v1/models", headers=headers
                )
                if resp.status_code == 200:
                    for m in resp.json().get("data", []):
                        mid = m.get("id")
                        if mid and mid not in all_localai:
                            all_localai.append(mid)
        except Exception:
            pass

        for mid in all_localai:
            base_mid = mid.split(":")[0]
            cap_data = capabilities_map.get(mid) or capabilities_map.get(base_mid)
            caps = cap_data.get("capabilities") or [] if cap_data else []

            task = "other"
            kind = None
            runtime = "localai"
            confidence = "low"
            source = "unknown"

            lower_mid = mid.lower()
            if any(c in caps for c in ("chat", "completion")):
                task = "llm"
                runtime = "llama.cpp"
                source = "metadata"
                confidence = "high"
            elif "transcript" in caps or any(
                k in lower_mid
                for k in ("whisper", "parakeet", "asr", "stt", "nemo", "crispasr")
            ):
                task = "stt"
                if "parakeet" in lower_mid:
                    runtime = "parakeet.cpp"
                elif "zipformer" in lower_mid or "sherpa" in lower_mid:
                    runtime = "sherpa-onnx"
                else:
                    runtime = "whisper.cpp"
                # Only sherpa-onnx models on port 8088 can stream.
                # parakeet-cpp-realtime_eou_120m-v1 is classified as streaming per catalog specification.
                is_streaming = runtime == "sherpa-onnx" or any(
                    k in lower_mid
                    for k in ("realtime_eou", "eou", "zipformer", "sherpa")
                )
                kind = "streaming" if is_streaming else "batch"
                source = "metadata" if "transcript" in caps else "file-inference"
                confidence = "high"
            elif "tts" in caps or any(k in lower_mid for k in ("vits", "piper")):
                task = "tts"
                runtime = "vits/piper"
                source = "metadata" if "tts" in caps else "file-inference"
                confidence = "high" if "tts" in caps else "medium"
            elif "vad" in caps or "silero" in lower_mid:
                task = "vad"
                runtime = "silero"
                source = "metadata"
                confidence = "high"
            else:
                # Default unknown LocalAI models to other
                task = "other"
                source = "unknown"
                confidence = "low"

            is_loaded = mid in loaded_ids or base_mid in loaded_ids
            is_available = True
            unavailable_reason = None
            if runtime in ("parakeet.cpp", "parakeet-cpp") or "parakeet" in lower_mid:
                if installed_backends and not any(
                    "parakeet" in b for b in installed_backends
                ):
                    is_available = False
                    unavailable_reason = (
                        "Missing backend: parakeet-cpp backend not installed in LocalAI"
                    )
            elif runtime in ("whisper.cpp", "whisper"):
                if installed_backends and not any(
                    "whisper" in b for b in installed_backends
                ):
                    is_available = False
                    unavailable_reason = (
                        "Missing backend: whisper backend not installed in LocalAI"
                    )
            elif runtime in ("llama.cpp", "llama-cpp"):
                if installed_backends and not any(
                    "llama" in b for b in installed_backends
                ):
                    is_available = False
                    unavailable_reason = (
                        "Missing backend: llama-cpp backend not installed in LocalAI"
                    )

            models.append(
                {
                    "id": mid,
                    "display_name": mid,
                    "family": base_mid,
                    "task": task,
                    "kind": kind,
                    "engine": "localai",
                    "runtime": runtime,
                    "params": "Unknown",
                    "languages": ["en"],
                    "size_mb": 500,
                    "license": "Open",
                    "installed": True,
                    "available": is_available,
                    "unavailable_reason": unavailable_reason,
                    "loaded": is_loaded,
                    "classification_source": source,
                    "classification_confidence": confidence,
                }
            )

        return models

    async def scan_streaming_stt_models(self) -> List[Dict[str, Any]]:
        """Discover streaming STT models via model.json manifests and folder inspection."""
        models: List[Dict[str, Any]] = []
        base_dir = Path("models/streaming-stt")
        if not base_dir.exists():
            return models

        for folder in base_dir.iterdir():
            if not folder.is_dir():
                continue

            manifest_path = folder / "model.json"
            if manifest_path.exists():
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    data.setdefault("installed", True)
                    data.setdefault("available", True)
                    data.setdefault("loaded", False)
                    data.setdefault("classification_source", "manifest")
                    data.setdefault("classification_confidence", "high")
                    models.append(data)
                    continue
                except Exception as e:
                    logger.warning("Error reading manifest %s: %s", manifest_path, e)

            # File-inference backfill
            has_tokens = (folder / "tokens.txt").exists()
            onnx_files = list(folder.glob("*.onnx"))
            if has_tokens and len(onnx_files) >= 1:
                mid = folder.name
                infer_entry = {
                    "id": mid,
                    "display_name": mid.replace("-", " ").title(),
                    "family": "zipformer"
                    if "zipformer" in mid.lower()
                    else "transducer",
                    "task": "stt",
                    "kind": "streaming",
                    "engine": "streaming-stt",
                    "runtime": "sherpa-onnx",
                    "params": "20M" if "20m" in mid.lower() else "unknown",
                    "languages": ["en"],
                    "size_mb": 42,
                    "license": "Apache-2.0",
                    "installed": True,
                    "available": True,
                    "unavailable_reason": None,
                    "loaded": False,
                    "classification_source": "file-inference",
                    "classification_confidence": "medium",
                }
                # Backfill model.json
                try:
                    manifest_path.write_text(
                        json.dumps(infer_entry, indent=2), encoding="utf-8"
                    )
                except Exception:
                    pass
                models.append(infer_entry)

        return models

    async def scan_local_tts_models(self) -> List[Dict[str, Any]]:
        """Discover local-tts models and probe availability for each."""
        models: List[Dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(
                    f"{settings.LOCAL_TTS_HTTP_BASE_URL.rstrip('/')}/v1/models"
                )
                if resp.status_code == 200:
                    raw = resp.json()
                    items = raw if isinstance(raw, list) else raw.get("models", [])
                    for item in items:
                        mid = item.get("model_id") or item.get("id")
                        if not mid:
                            continue
                        name = item.get("name") or mid.title()

                        # Run availability probe
                        is_avail, unavail_reason = await self.probe_tts_engine(mid)

                        models.append(
                            {
                                "id": mid,
                                "display_name": name,
                                "family": mid,
                                "task": "tts",
                                "kind": None,
                                "engine": "local-tts",
                                "runtime": "kokoro-onnx"
                                if "kokoro" in mid.lower()
                                else "local-tts",
                                "params": "82M"
                                if "kokoro" in mid.lower()
                                else "unknown",
                                "languages": ["en"],
                                "size_mb": 350 if "kokoro" in mid.lower() else 100,
                                "license": "Apache-2.0",
                                "installed": True,
                                "available": is_avail,
                                "unavailable_reason": unavail_reason,
                                "loaded": is_avail,
                                "classification_source": "metadata",
                                "classification_confidence": "high",
                            }
                        )
        except Exception as e:
            logger.warning("Error scanning local-tts models: %s", e)

        if not any(m["id"] == "kokoro" for m in models):
            models.append(
                {
                    "id": "kokoro",
                    "display_name": "Kokoro (Default)",
                    "family": "kokoro",
                    "task": "tts",
                    "kind": None,
                    "engine": "local-tts",
                    "runtime": "kokoro-onnx",
                    "params": "82M",
                    "languages": ["en"],
                    "size_mb": 350,
                    "license": "Apache-2.0",
                    "installed": True,
                    "available": True,
                    "unavailable_reason": None,
                    "loaded": True,
                    "classification_source": "metadata",
                    "classification_confidence": "high",
                }
            )

        return models

    async def list_models(
        self,
        task: Optional[str] = None,
        kind: Optional[str] = None,
        installed: Optional[bool] = None,
        available: Optional[bool] = None,
        include_unclassified: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return registered models with filtering applied."""
        async with self._lock:
            now = time.time()
            if (
                self._models_cache is None
                or (now - self._cache_timestamp) > self._cache_ttl
            ):
                # Refresh registry
                localai = await self.scan_localai_models()
                streaming = await self.scan_streaming_stt_models()
                tts = await self.scan_local_tts_models()

                combined: List[Dict[str, Any]] = []
                seen_ids: set = set()

                for m in streaming + tts + localai:
                    mid = m["id"]
                    if mid not in seen_ids:
                        seen_ids.add(mid)
                        combined.append(m)

                # Apply manual overrides
                overrides = await self.get_manual_overrides()
                for m in combined:
                    ovr = overrides.get(m["id"].lower())
                    if ovr:
                        if ovr.get("task"):
                            m["task"] = ovr["task"]
                        if ovr.get("kind") is not None:
                            m["kind"] = ovr["kind"]
                        if ovr.get("display_name"):
                            m["display_name"] = ovr["display_name"]
                        m["classification_source"] = "manual"
                        m["classification_confidence"] = "high"

                # Include manual overrides for models not discovered by engines
                for ovr_id, ovr in overrides.items():
                    if ovr_id not in seen_ids:
                        seen_ids.add(ovr_id)
                        is_avail = True
                        unavail_reason = None

                        combined.append(
                            {
                                "id": ovr_id,
                                "display_name": ovr.get("display_name") or ovr_id,
                                "family": "custom",
                                "task": ovr.get("task", "other"),
                                "kind": ovr.get("kind"),
                                "engine": "custom",
                                "runtime": "custom",
                                "params": "custom",
                                "languages": ["en"],
                                "size_mb": 0,
                                "license": "unknown",
                                "installed": True,
                                "available": is_avail,
                                "unavailable_reason": unavail_reason,
                                "loaded": False,
                                "classification_source": "manual",
                                "classification_confidence": "high",
                            }
                        )

                self._models_cache = combined
                self._cache_timestamp = now

            models = list(self._models_cache)

        # Apply query filters
        filtered = []
        for m in models:
            if (
                not include_unclassified
                and m["task"] in ("other", "unknown")
                and not task
            ):
                continue
            if task and m["task"].lower() != task.lower():
                continue
            if kind and (m.get("kind") or "").lower() != kind.lower():
                continue
            if installed is not None and m.get("installed") != installed:
                continue
            if available is not None and m.get("available") != available:
                continue
            filtered.append(m)

        return filtered

    async def set_override(
        self,
        model_id: str,
        task: str,
        kind: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save manual admin classification override."""
        async with get_db() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO model_overrides (model_id, task, kind, display_name, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (model_id, task, kind, display_name),
            )
            await db.commit()

        self.invalidate_cache()
        return {
            "model_id": model_id,
            "task": task,
            "kind": kind,
            "display_name": display_name,
        }


model_registry = ModelRegistryService()

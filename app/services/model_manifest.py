"""Dynamic Installed Models Manifest Service.

Maintains a synchronized, persistent JSON manifest (`data/installed_models_manifest.json`)
of all currently installed models across STT (batch & streaming), LLM, and TTS providers.
Automatically updates when models are installed or deleted to prevent dependent failures
and eliminate hardcoded model fallback references across backend and frontend.
"""

from __future__ import annotations
import os
import json
import time
import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

from app.core.config import settings

logger = logging.getLogger("voice_agent.models.manifest")


class ModelManifest:
    def __init__(self, manifest_path: Optional[str] = None):
        self._path = Path(
            manifest_path
            or getattr(
                settings, "MODELS_MANIFEST_PATH", "data/installed_models_manifest.json"
            )
        )
        self._lock = asyncio.Lock()
        self._cached_manifest: Optional[Dict[str, Any]] = None

    @property
    def path(self) -> Path:
        return self._path

    def _ensure_dir(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load_from_file_sync(self) -> Optional[Dict[str, Any]]:
        """Synchronously read JSON manifest from disk if it exists."""
        try:
            if self._path.exists():
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "stt" in data:
                        return data
        except Exception as e:
            logger.warning("Failed to read models manifest from %s: %s", self._path, e)
        return None

    def save_to_file_sync(self, manifest: Dict[str, Any]):
        """Synchronously write JSON manifest atomically to disk."""
        try:
            self._ensure_dir()
            temp_path = self._path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
            os.replace(temp_path, self._path)
        except Exception as e:
            logger.error("Failed to write models manifest to %s: %s", self._path, e)

    async def get_manifest(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get the current manifest, initializing or syncing if empty."""
        async with self._lock:
            if self._cached_manifest is not None:
                return self._cached_manifest

            # Try disk
            disk_data = self.load_from_file_sync()
            if disk_data is not None:
                self._cached_manifest = disk_data
                return disk_data

        # If not on disk or cache, perform full sync
        return await self.sync_manifest()

    async def sync_manifest(self) -> Dict[str, List[Dict[str, Any]]]:
        """Query live providers and database, then merge into JSON manifest."""
        async with self._lock:
            from app.api.gallery import get_installed_models_data

            try:
                data = await get_installed_models_data()
            except Exception as e:
                logger.warning(
                    "Error fetching installed models for manifest sync: %s", e
                )
                data = {"stt": [], "llm": [], "tts": []}

            existing = self.load_from_file_sync() or self._cached_manifest or {}

            # Merge live discovered data with existing manifest so offline engines don't wipe disk models
            merged: Dict[str, List[Dict[str, Any]]] = {}
            for cat in ("stt", "llm", "tts"):
                seen = {}
                for m in existing.get(cat, []):
                    if m.get("id"):
                        seen[m["id"].lower()] = dict(m)
                for m in data.get(cat, []):
                    if m.get("id"):
                        seen[m["id"].lower()] = dict(m)
                merged[cat] = list(seen.values())

            manifest = {
                "version": 1,
                "updated_at": time.time(),
                "stt": merged["stt"],
                "llm": merged["llm"],
                "tts": merged["tts"],
            }
            self.save_to_file_sync(manifest)
            self._cached_manifest = manifest
            logger.info(
                "Synchronized models manifest with %d STT, %d LLM, %d TTS models",
                len(manifest["stt"]),
                len(manifest["llm"]),
                len(manifest["tts"]),
            )
            return manifest

    async def record_install(
        self,
        model_id: str,
        name: str,
        category: str,
        provider: str,
        size_mb: int,
        kind: Optional[str] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Update manifest with newly installed model and persist to JSON."""
        async with self._lock:
            manifest = (
                self.load_from_file_sync()
                or self._cached_manifest
                or {
                    "version": 1,
                    "updated_at": time.time(),
                    "stt": [],
                    "llm": [],
                    "tts": [],
                }
            )

            cat_key = category.lower()
            if cat_key not in ("stt", "llm", "tts"):
                cat_key = "llm"

            bucket = manifest.setdefault(cat_key, [])
            # Deduplicate by id
            bucket = [m for m in bucket if m.get("id") != model_id]

            entry: Dict[str, Any] = {
                "id": model_id,
                "name": name or model_id,
                "category": cat_key,
                "provider": provider,
                "size_mb": size_mb,
            }
            if cat_key == "stt":
                lower_m_id = model_id.lower()
                if kind is not None:
                    entry["kind"] = kind
                else:
                    is_streaming = (provider == "stream-stt") or any(
                        k in lower_m_id
                        for k in ("realtime_eou", "eou", "zipformer", "sherpa")
                    )
                    entry["kind"] = "streaming" if is_streaming else "batch"

            bucket.append(entry)
            manifest[cat_key] = bucket
            manifest["updated_at"] = time.time()

            self.save_to_file_sync(manifest)
            self._cached_manifest = manifest
            try:
                from app.services.model_registry import model_registry

                model_registry.invalidate_cache()
            except Exception:
                pass
            logger.info(
                "Recorded model installation '%s' in category '%s'", model_id, cat_key
            )
            return manifest

    async def update_model_classification(
        self,
        model_id: str,
        task: str,
        kind: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Update a model's task/category, kind, and display name in manifest."""
        async with self._lock:
            manifest = (
                self.load_from_file_sync()
                or self._cached_manifest
                or {
                    "version": 1,
                    "updated_at": time.time(),
                    "stt": [],
                    "llm": [],
                    "tts": [],
                }
            )

            target_id = model_id.lower()
            target_base = target_id.split(":")[0]
            new_cat = task.lower()
            if new_cat not in ("stt", "llm", "tts"):
                new_cat = "stt"

            found_entry: Optional[Dict[str, Any]] = None

            # Remove from any current bucket
            for cat_key in ("stt", "llm", "tts"):
                bucket = manifest.get(cat_key, [])
                remaining = []
                for m in bucket:
                    mid = m.get("id", "").lower()
                    if mid == target_id or mid.split(":")[0] == target_base:
                        found_entry = dict(m)
                    else:
                        remaining.append(m)
                manifest[cat_key] = remaining

            if not found_entry:
                found_entry = {
                    "id": model_id,
                    "name": display_name or model_id,
                    "category": new_cat,
                    "provider": "localai",
                    "size_mb": 100,
                }

            found_entry["category"] = new_cat
            if display_name:
                found_entry["name"] = display_name
            if new_cat == "stt" and kind is not None:
                found_entry["kind"] = kind

            dest_bucket = manifest.setdefault(new_cat, [])
            dest_bucket.append(found_entry)
            manifest[new_cat] = dest_bucket
            manifest["updated_at"] = time.time()

            self.save_to_file_sync(manifest)
            self._cached_manifest = manifest
            try:
                from app.services.model_registry import model_registry

                model_registry.invalidate_cache()
            except Exception:
                pass
            logger.info(
                "Updated classification for '%s': task=%s, kind=%s",
                model_id,
                new_cat,
                kind,
            )
            return manifest

    async def record_uninstall(self, model_id: str) -> Dict[str, List[Dict[str, Any]]]:
        """Remove uninstalled model from manifest and persist to JSON."""
        async with self._lock:
            manifest = (
                self.load_from_file_sync()
                or self._cached_manifest
                or {
                    "version": 1,
                    "updated_at": time.time(),
                    "stt": [],
                    "llm": [],
                    "tts": [],
                }
            )

            target_id = model_id.lower()
            target_base = target_id.split(":")[0]

            for cat_key in ("stt", "llm", "tts"):
                bucket = manifest.get(cat_key, [])
                filtered = [
                    m
                    for m in bucket
                    if m.get("id", "").lower() != target_id
                    and m.get("id", "").lower().split(":")[0] != target_base
                ]
                manifest[cat_key] = filtered

            manifest["updated_at"] = time.time()
            self.save_to_file_sync(manifest)
            self._cached_manifest = manifest
            try:
                from app.services.model_registry import model_registry

                model_registry.invalidate_cache()
            except Exception:
                pass
            logger.info("Recorded model uninstallation '%s' in manifest", model_id)
            return manifest

    def get_models_by_category(
        self, category: str, kind: Optional[str] = None, available_only: bool = False
    ) -> List[Dict[str, Any]]:
        """Synchronously return current models for a category from cache or disk."""
        manifest = self._cached_manifest or self.load_from_file_sync() or {}
        models = manifest.get(category.lower(), [])
        if available_only:
            models = [m for m in models if m.get("available") is not False]

        if kind and category.lower() == "stt":
            matched = [m for m in models if m.get("kind") == kind]
            if kind == "streaming" and available_only:
                matched = [
                    m
                    for m in matched
                    if m.get("provider") == "stream-stt"
                    or any(
                        k in m.get("id", "").lower() for k in ("sherpa", "zipformer")
                    )
                ]
            return matched
        return models

    def get_default_model(
        self,
        category: str,
        kind: Optional[str] = None,
        exclude: Optional[str] = None,
    ) -> Optional[str]:
        """Dynamically resolve the default installed model for a category without hardcoded strings."""
        models = self.get_models_by_category(category, kind, available_only=True)
        if exclude:
            models = [m for m in models if m.get("id", "").lower() != exclude.lower()]

        if models:
            return models[0]["id"]

        # If filtering by kind returned nothing, try any model in category
        if kind and category.lower() == "stt":
            fallback_models = self.get_models_by_category(category, available_only=True)
            if exclude:
                fallback_models = [
                    m
                    for m in fallback_models
                    if m.get("id", "").lower() != exclude.lower()
                ]
            if fallback_models:
                return fallback_models[0]["id"]

        return None

    def resolve_model_id(
        self,
        category: str,
        requested_model: Optional[str],
        kind: Optional[str] = None,
        available_only: bool = True,
    ) -> str:
        """Resolve requested model to an installed model; if missing/deleted/unavailable, dynamically heal to default."""
        models = self.get_models_by_category(
            category, kind=kind, available_only=available_only
        )
        if not models:
            models = self.get_models_by_category(
                category, available_only=available_only
            )
        if not models:
            return requested_model or ""

        target = (requested_model or "").strip()
        if target:
            search_models = list(models)
            if kind == "batch":
                all_cat = self.get_models_by_category(
                    category, available_only=available_only
                )
                for am in all_cat:
                    if am not in search_models:
                        search_models.append(am)

            # 1. Exact match
            for m in search_models:
                if m.get("id") == target:
                    return m["id"]
            # 2. Case-insensitive match
            for m in search_models:
                if m.get("id", "").lower() == target.lower():
                    return m["id"]
            # 3. Base ID match
            target_base = target.lower().split(":")[0]
            for m in search_models:
                if m.get("id", "").lower().split(":")[0] == target_base:
                    return m["id"]
            # 4. Substring match
            for m in search_models:
                mid = m.get("id", "").lower()
                if target.lower() in mid or mid in target.lower():
                    return m["id"]

        # If not found or empty or unavailable, resolve dynamically to installed default
        resolved = self.get_default_model(category, kind)
        if resolved:
            if target:
                logger.warning(
                    "Requested %s model '%s' is not installed or available; dynamically resolved to installed model '%s'",
                    category,
                    target,
                    resolved,
                )
            return resolved
        return target


model_manifest = ModelManifest()

"""Model Residency and Memory Lifecycle Manager.

Tracks models currently resident in memory, classifies them into Active, Idle (5+ min),
and Stale (7+ min) states, manages manual and automatic unloading (flushing) with per-model
locks and in-flight request protection, and emits live Server-Sent Events (SSE) telemetry.
"""

from __future__ import annotations
import json
import time
import uuid
import asyncio
import logging
from typing import Dict, List, Optional, Any, Tuple, Set
import httpx
import aiosqlite

from app.core.config import settings
from app.storage.database import get_db

logger = logging.getLogger("voice_agent.residency")


class ResidencyManager:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._model_locks: Dict[str, asyncio.Lock] = {}
        self._in_flight: Dict[str, int] = {}
        self._last_used: Dict[str, float] = {}
        self._last_flushed: Dict[str, float] = {}
        self._last_toast_times: Dict[str, float] = {}
        self._toasts: List[Dict[str, Any]] = []

        # Settings
        self.idle_timeout_seconds: int = 300  # 5 minutes
        self.stale_timeout_seconds: int = 420  # 7 minutes
        self.auto_flush_seconds: int = 600  # 10 minutes
        self.auto_flush_enabled: bool = False
        self.protected_models: Set[str] = set()

        self._auto_flush_task: Optional[asyncio.Task] = None

    def get_model_lock(self, model_id: str) -> asyncio.Lock:
        mid = model_id.lower()
        if mid not in self._model_locks:
            self._model_locks[mid] = asyncio.Lock()
        return self._model_locks[mid]

    async def initialize(self):
        """Load settings from database and start background auto-flush worker."""
        try:
            async with get_db() as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT idle_timeout_seconds, stale_timeout_seconds, auto_flush_seconds, auto_flush_enabled, protected_models_json FROM residency_settings WHERE id = 'default'"
                )
                row = await cursor.fetchone()
                if row:
                    self.idle_timeout_seconds = row["idle_timeout_seconds"]
                    self.stale_timeout_seconds = row["stale_timeout_seconds"]
                    self.auto_flush_seconds = row["auto_flush_seconds"]
                    self.auto_flush_enabled = bool(row["auto_flush_enabled"])
                    try:
                        prot = json.loads(row["protected_models_json"] or "[]")
                        self.protected_models = {p.lower() for p in prot}
                    except Exception:
                        self.protected_models = set()
        except Exception as e:
            logger.warning("Error loading residency settings: %s", e)

        # Protect active agent models by default
        try:
            from app.services.agent_manager import AgentManager

            active_info = AgentManager.get_active_state()
            active_id = active_info.get("active_agent_id")
            if active_id:
                agent = await AgentManager.get_agent(active_id)
                if agent:
                    if agent.get("llm", {}).get("model"):
                        self.protected_models.add(agent["llm"]["model"].lower())
                    if agent.get("stt", {}).get("model"):
                        self.protected_models.add(agent["stt"]["model"].lower())
                    if agent.get("stt", {}).get("fallback_model"):
                        self.protected_models.add(
                            agent["stt"]["fallback_model"].lower()
                        )
        except Exception:
            pass

        if self._auto_flush_task is None or self._auto_flush_task.done():
            self._auto_flush_task = asyncio.create_task(self._auto_flush_loop())

    def track_request_start(self, model_id: str):
        mid = model_id.lower()
        self._in_flight[mid] = self._in_flight.get(mid, 0) + 1
        self._last_used[mid] = time.time()

    def track_request_end(self, model_id: str):
        mid = model_id.lower()
        if mid in self._in_flight and self._in_flight[mid] > 0:
            self._in_flight[mid] -= 1
        self._last_used[mid] = time.time()

    def add_toast(
        self, message: str, level: str = "info", model_id: Optional[str] = None
    ):
        now = time.time()
        dedupe_key = f"{model_id or ''}:{message}"
        if (
            dedupe_key in self._last_toast_times
            and (now - self._last_toast_times[dedupe_key]) < 15.0
        ):
            return
        self._last_toast_times[dedupe_key] = now

        toast = {
            "id": f"toast-{int(now * 1000)}-{uuid.uuid4().hex[:6]}",
            "message": message,
            "level": level,
            "model_id": model_id,
            "ts": now,
        }
        self._toasts.append(toast)
        if len(self._toasts) > 20:
            self._toasts = self._toasts[-20:]

    def get_toasts(self, consume: bool = True) -> List[Dict[str, Any]]:
        """Return toasts. When consume=True, drains and clears the unread queue."""
        toasts = list(self._toasts)
        if consume:
            self._toasts.clear()
        return toasts

    async def get_residency_status(self) -> Dict[str, Any]:
        """Fetch live resident models from LocalAI /system and compute states."""
        now = time.time()
        resident_models: List[Dict[str, Any]] = []

        # 1. Query LocalAI /system
        localai_loaded: List[Dict[str, Any]] = []
        headers = (
            {"Authorization": f"Bearer {settings.LOCALAI_API_KEY}"}
            if getattr(settings, "LOCALAI_API_KEY", None)
            else {}
        )
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(
                    f"{settings.LOCALAI_BASE_URL}/system", headers=headers
                )
                if resp.status_code == 200:
                    localai_loaded = resp.json().get("loaded_models", [])
        except Exception as e:
            logger.debug("Failed to query LocalAI /system: %s", e)

        for lm in localai_loaded:
            raw_id = lm.get("id", "")
            norm_id = raw_id.split(":")[0]
            lookup_key = norm_id.lower()

            last_used_t = self._last_used.get(
                lookup_key, self._last_used.get(raw_id.lower(), now - 120)
            )
            idle_sec = max(0, int(now - last_used_t))
            in_flight = self._in_flight.get(
                lookup_key, self._in_flight.get(raw_id.lower(), 0)
            )
            is_protected = (
                lookup_key in self.protected_models
                or raw_id.lower() in self.protected_models
            )

            # State classification
            if in_flight > 0 or idle_sec < self.idle_timeout_seconds:
                state = "active"
            elif idle_sec >= self.stale_timeout_seconds:
                state = "stale"
            else:
                state = "idle"

            proc = lm.get("process") or {}
            rss_bytes = proc.get("rss_bytes") or 0
            rss_mb = round(rss_bytes / (1024 * 1024), 1)

            resident_models.append(
                {
                    "id": raw_id,
                    "display_name": raw_id,
                    "engine": "localai",
                    "backend": lm.get("backend", "llama-cpp"),
                    "state": state,
                    "loaded": True,
                    "in_flight": in_flight,
                    "last_used_ts": last_used_t,
                    "idle_seconds": idle_sec,
                    "memory_mb": rss_mb if rss_mb > 0 else 500.0,
                    "memory_type": "measured" if rss_bytes > 0 else "estimated",
                    "protected": is_protected,
                }
            )

        # 2. Add local-tts Kokoro if available
        try:
            from app.services.model_registry import model_registry

            tts_avail, _ = await model_registry.probe_tts_engine("kokoro")
            if tts_avail:
                last_tts = self._last_used.get("kokoro", now - 60)
                idle_sec = max(0, int(now - last_tts))
                in_flight = self._in_flight.get("kokoro", 0)
                resident_models.append(
                    {
                        "id": "kokoro",
                        "display_name": "Kokoro TTS",
                        "engine": "local-tts",
                        "backend": "onnxruntime",
                        "state": "active"
                        if (in_flight > 0 or idle_sec < self.idle_timeout_seconds)
                        else "idle",
                        "loaded": True,
                        "in_flight": in_flight,
                        "last_used_ts": last_tts,
                        "idle_seconds": idle_sec,
                        "memory_mb": 350.0,
                        "memory_type": "estimated",
                        "protected": "kokoro" in self.protected_models,
                    }
                )
        except Exception:
            pass

        # 3. Include any active in-flight or recently-used models tracked by gateway
        seen_res_ids = {m["id"].lower() for m in resident_models}
        for inf_mid in set(list(self._in_flight.keys()) + list(self._last_used.keys())):
            count = self._in_flight.get(inf_mid, 0)
            last_t = self._last_used.get(inf_mid, 0)
            idle_sec = max(0, int(now - last_t))
            if (
                count > 0 or idle_sec < self.stale_timeout_seconds
            ) and inf_mid not in seen_res_ids:
                is_protected = inf_mid in self.protected_models
                state = (
                    "active"
                    if (count > 0 or idle_sec < self.idle_timeout_seconds)
                    else "idle"
                )
                resident_models.append(
                    {
                        "id": inf_mid,
                        "display_name": inf_mid,
                        "engine": "localai",
                        "backend": "localai",
                        "state": state,
                        "loaded": True,
                        "in_flight": count,
                        "last_used_ts": last_t,
                        "idle_seconds": idle_sec,
                        "memory_mb": 500.0,
                        "memory_type": "estimated",
                        "protected": is_protected,
                    }
                )
                seen_res_ids.add(inf_mid)

        return {
            "models": resident_models,
            "auto_flush_enabled": self.auto_flush_enabled,
            "idle_timeout_seconds": self.idle_timeout_seconds,
            "stale_timeout_seconds": self.stale_timeout_seconds,
            "auto_flush_seconds": self.auto_flush_seconds,
            "protected_models": list(self.protected_models),
        }

    async def flush_model(self, model_id: str, force: bool = False) -> Tuple[bool, str]:
        """Unload model from LocalAI backend with per-model lock and concurrency protection."""
        norm_id = model_id.split(":")[0].lower()
        raw_id = model_id

        if not force and norm_id in self.protected_models:
            return False, f"Model '{model_id}' is protected from flushing."

        in_flight = self._in_flight.get(norm_id, 0) + self._in_flight.get(
            raw_id.lower(), 0
        )
        if in_flight > 0:
            return (
                False,
                f"Cannot flush model '{model_id}': {in_flight} active requests in flight.",
            )

        lock = self.get_model_lock(norm_id)
        async with lock:
            # Check cooldown (60 seconds)
            last_flush = self._last_flushed.get(norm_id, 0)
            if not force and (time.time() - last_flush) < 60.0:
                return False, f"Model '{model_id}' is in cooldown (recently flushed)."

            # Execute LocalAI backend shutdown
            url = f"{settings.LOCALAI_BASE_URL.rstrip('/')}/backend/shutdown"
            headers = (
                {"Authorization": f"Bearer {settings.LOCALAI_API_KEY}"}
                if getattr(settings, "LOCALAI_API_KEY", None)
                else {}
            )
            unloaded = False
            err_msg = ""
            for target in (raw_id, norm_id):
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        resp = await client.post(
                            url, json={"model": target}, headers=headers
                        )
                        if resp.status_code in (200, 404):
                            unloaded = True
                            break
                        else:
                            err_msg = resp.text
                except Exception as e:
                    err_msg = str(e)

            if unloaded or ("connect" in err_msg.lower()):
                self._last_flushed[norm_id] = time.time()
                self._last_used.pop(norm_id, None)
                self._in_flight.pop(norm_id, None)
                self.add_toast(
                    f"'{model_id}' unloaded, freed system memory",
                    level="success",
                    model_id=model_id,
                )
                logger.info("Successfully flushed model: %s", model_id)
                return True, f"Model '{model_id}' flushed successfully."
            else:
                self.add_toast(
                    f"Could not unload '{model_id}': {err_msg}",
                    level="error",
                    model_id=model_id,
                )
                logger.warning("Failed to flush model '%s': %s", model_id, err_msg)
                return False, f"Failed to unload model '{model_id}': {err_msg}"

    async def flush_idle_models(self) -> List[Dict[str, Any]]:
        """Flush all resident models that are idle or stale and not protected."""
        status = await self.get_residency_status()
        flushed = []
        for m in status.get("models", []):
            mid = m["id"]
            if m["state"] in ("idle", "stale") and not m.get("protected"):
                success, msg = await self.flush_model(mid)
                flushed.append({"model_id": mid, "success": success, "message": msg})
        return flushed

    async def update_settings(
        self,
        idle_timeout_seconds: Optional[int] = None,
        stale_timeout_seconds: Optional[int] = None,
        auto_flush_seconds: Optional[int] = None,
        auto_flush_enabled: Optional[bool] = None,
        protected_models: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Update residency thresholds and persistence in SQLite."""
        if idle_timeout_seconds is not None:
            self.idle_timeout_seconds = max(30, idle_timeout_seconds)
        if stale_timeout_seconds is not None:
            self.stale_timeout_seconds = max(
                self.idle_timeout_seconds + 30, stale_timeout_seconds
            )
        if auto_flush_seconds is not None:
            self.auto_flush_seconds = max(60, auto_flush_seconds)
        if auto_flush_enabled is not None:
            self.auto_flush_enabled = auto_flush_enabled
        if protected_models is not None:
            self.protected_models = {p.lower() for p in protected_models}

        async with get_db() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO residency_settings (
                    id, idle_timeout_seconds, stale_timeout_seconds, auto_flush_seconds,
                    auto_flush_enabled, protected_models_json, updated_at
                )
                VALUES ('default', ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.idle_timeout_seconds,
                    self.stale_timeout_seconds,
                    self.auto_flush_seconds,
                    1 if self.auto_flush_enabled else 0,
                    json.dumps(list(self.protected_models)),
                    time.time(),
                ),
            )
            await db.commit()

        return {
            "idle_timeout_seconds": self.idle_timeout_seconds,
            "stale_timeout_seconds": self.stale_timeout_seconds,
            "auto_flush_seconds": self.auto_flush_seconds,
            "auto_flush_enabled": self.auto_flush_enabled,
            "protected_models": list(self.protected_models),
        }

    async def _auto_flush_loop(self):
        """Background worker that periodically checks for models exceeding auto-flush threshold."""
        while True:
            try:
                await asyncio.sleep(60.0)
                if not self.auto_flush_enabled:
                    continue

                status = await self.get_residency_status()
                for m in status.get("models", []):
                    mid = m["id"]
                    norm_id = mid.split(":")[0].lower()
                    if norm_id in self.protected_models or m.get("in_flight", 0) > 0:
                        continue

                    idle_sec = m.get("idle_seconds", 0)
                    if idle_sec >= self.auto_flush_seconds:
                        logger.info(
                            "Auto-flushing model '%s' (idle for %ds)", mid, idle_sec
                        )
                        await self.flush_model(mid)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Error in residency auto-flush loop: %s", e)


residency_manager = ResidencyManager()

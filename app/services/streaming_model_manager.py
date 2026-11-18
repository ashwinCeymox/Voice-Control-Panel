"""
Streaming STT Model Manager.
Handles safe deletion, lifecycle management, agent usage validation (409 MODEL_IN_USE),
and directory cleanup specifically for streaming STT models.
"""

import json
import logging
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional
import aiosqlite
from app.storage.database import get_db
from app.core.errors import ModelInUseError, BadRequestError

logger = logging.getLogger("voice_agent.services.streaming_model_manager")


class StreamingModelManager:
    def __init__(self, models_base_dir: Optional[Path] = None):
        self.models_base_dir = models_base_dir or Path("models/streaming-stt")

    async def get_agents_using_model(self, model_id: str) -> List[str]:
        """
        Scan all configured agents to check if any active agent references this model
        in its LLM, STT (primary or fallback), or TTS config.
        """
        norm_id = model_id.strip().lower()
        matched_agents: List[str] = []

        try:
            async with get_db() as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT id, name, llm_json, stt_json, tts_json FROM agents"
                )
                rows = await cursor.fetchall()

                for row in rows:
                    agent_name = row["name"] or row["id"]
                    try:
                        stt_cfg = json.loads(row["stt_json"]) if row["stt_json"] else {}
                    except Exception:
                        stt_cfg = {}
                    try:
                        llm_cfg = json.loads(row["llm_json"]) if row["llm_json"] else {}
                    except Exception:
                        llm_cfg = {}
                    try:
                        tts_cfg = json.loads(row["tts_json"]) if row["tts_json"] else {}
                    except Exception:
                        tts_cfg = {}

                    stt_m = str(stt_cfg.get("model", "")).strip().lower()
                    stt_fb = str(stt_cfg.get("fallback_model", "")).strip().lower()
                    llm_m = str(llm_cfg.get("model", "")).strip().lower()
                    tts_eng = str(tts_cfg.get("engine", "")).strip().lower()

                    if norm_id in (stt_m, stt_fb, llm_m, tts_eng):
                        matched_agents.append(agent_name)
                    elif (
                        stt_m
                        and norm_id
                        and (
                            stt_m == norm_id
                            or norm_id.startswith(stt_m)
                            or stt_m.startswith(norm_id)
                        )
                    ):
                        matched_agents.append(agent_name)
        except Exception as e:
            logger.warning("Error inspecting agents for model usage: %s", e)

        return list(dict.fromkeys(matched_agents))

    async def delete_streaming_model(self, model_id: str) -> Dict[str, Any]:
        """
        Delete a streaming STT model:
        1. Validates path safety (rejects path traversal).
        2. Enforces agent usage check (refuses with 409 if active agent uses it).
        3. Identifies and deletes matching directory in models/streaming-stt/.
        4. Purges manifest and updates registry cache.
        """
        if not model_id or ".." in model_id or "/" in model_id or "\\" in model_id:
            raise BadRequestError(f"Invalid model identifier: '{model_id}'")

        # 1. Enforce active agent usage safeguard
        in_use = await self.get_agents_using_model(model_id)
        if in_use:
            raise ModelInUseError(model_id, in_use)

        # 2. Locate directory in models_base_dir
        base_dir = self.models_base_dir.resolve()
        norm_id = model_id.lower().replace("-", "").replace("_", "")

        matched_dir: Optional[Path] = None
        if self.models_base_dir.is_dir():
            for child in self.models_base_dir.iterdir():
                if not child.is_dir():
                    continue
                child_norm = child.name.lower().replace("-", "").replace("_", "")
                if (
                    child.name == model_id
                    or child.name.lower() == model_id.lower()
                    or child_norm == norm_id
                    or child_norm.startswith(norm_id)
                    or norm_id.startswith(child_norm)
                ):
                    matched_dir = child.resolve()
                    break

        if matched_dir and str(matched_dir).startswith(str(base_dir)):
            try:
                shutil.rmtree(matched_dir, ignore_errors=True)
                logger.info(
                    "Successfully deleted streaming model directory: %s", matched_dir
                )
            except Exception as e:
                logger.error(
                    "Failed to delete streaming model directory %s: %s", matched_dir, e
                )
                raise RuntimeError(f"Filesystem deletion error: {e}")

        # 3. Purge from installed models manifest
        try:
            from app.services.model_manifest import model_manifest

            await model_manifest.record_uninstall(model_id)
            if matched_dir:
                await model_manifest.record_uninstall(matched_dir.name)
            await model_manifest.sync_manifest()
        except Exception as mf_err:
            logger.warning("Manifest uninstall record warning: %s", mf_err)

        # 4. Invalidate registry cache
        try:
            from app.services.model_registry import model_registry

            model_registry.invalidate_cache()
        except Exception:
            pass

        return {
            "status": "deleted",
            "model_id": model_id,
            "directory_removed": str(matched_dir) if matched_dir else None,
        }


streaming_model_manager = StreamingModelManager()

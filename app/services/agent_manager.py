"""Agent lifecycle, SQLite storage, and active agent management."""
import json
import uuid
import time
import asyncio
from typing import Dict, List, Optional, Any
import aiosqlite
from app.storage.database import get_db
from app.api.system import get_mac_memory_info

DEFAULT_AGENT = {
    "id": "agent-default-assistant",
    "name": "Default Voice Assistant",
    "description": "Standard concise conversational voice assistant optimized for 8GB Mac.",
    "system_prompt": "You are a concise, helpful voice AI assistant. Keep responses natural, direct, and under two sentences.",
    "llm": {"model": "llama-3.2-1b-instruct", "temperature": 0.7, "max_tokens": 512},
    "stt": {"model": "whisper-base", "kind": "batch", "fallback_model": None, "language": "auto"},
    "tts": {"engine": "kokoro", "voice_id": "af_heart", "speed": 1.0},
    "vad": {
        "engine": "silero",
        "positive_speech_threshold": 0.45,
        "negative_speech_threshold": 0.30,
        "redemption_ms": 800,
        "pre_speech_pad_ms": 200,
        "min_speech_ms": 250,
        "min_rms": 0.015,
        "noise_suppression": "browser",
        "barge_in": True,
        "barge_in_confirm_ms": 250,
        "playback_threshold_boost": 0.15,
        "threshold": 0.045,
        "min_silence_ms": 800,
    },
    "audio": {"echo_mode": "laptop_speakers", "barge_in": True},
    "schema_version": 2,
}

# Optimal Silero VAD presets calibrated for real-time streaming (Sherpa Zipformer) vs batch
SILERO_STREAMING_DEFAULTS = {
    "engine": "silero",
    "positive_speech_threshold": 0.50,
    "negative_speech_threshold": 0.35,
    "redemption_ms": 450,
    "pre_speech_pad_ms": 150,
    "min_speech_ms": 200,
    "min_rms": 0.015,
    "noise_suppression": "browser",
    "barge_in": True,
    "barge_in_confirm_ms": 200,
    "playback_threshold_boost": 0.15,
    "threshold": 0.045,
    "min_silence_ms": 450,
}

SILERO_CONVERSATIONAL_DEFAULTS = {
    "engine": "silero",
    "positive_speech_threshold": 0.45,
    "negative_speech_threshold": 0.30,
    "redemption_ms": 700,
    "pre_speech_pad_ms": 200,
    "min_speech_ms": 250,
    "min_rms": 0.015,
    "noise_suppression": "browser",
    "barge_in": True,
    "barge_in_confirm_ms": 250,
    "playback_threshold_boost": 0.15,
    "threshold": 0.045,
    "min_silence_ms": 700,
}

SILERO_BATCH_DEFAULTS = {
    "engine": "silero",
    "positive_speech_threshold": 0.45,
    "negative_speech_threshold": 0.30,
    "redemption_ms": 800,
    "pre_speech_pad_ms": 200,
    "min_speech_ms": 250,
    "min_rms": 0.015,
    "noise_suppression": "browser",
    "barge_in": True,
    "barge_in_confirm_ms": 250,
    "playback_threshold_boost": 0.15,
    "threshold": 0.045,
    "min_silence_ms": 800,
}

VAD_PRESETS = {
    "streaming": SILERO_STREAMING_DEFAULTS,
    "conversational": SILERO_CONVERSATIONAL_DEFAULTS,
    "batch": SILERO_BATCH_DEFAULTS,
}

_ACTIVE_AGENT_ID: str = "agent-default-assistant"
_ACTIVE_LOAD_STATE: str = "ready"  # idle, loading, ready, error
_LOAD_LOCK = asyncio.Lock()


def _normalize_stt(stt_raw: Dict[str, Any]) -> Dict[str, Any]:
    merged = {
        "model": "whisper-base",
        "kind": "batch",
        "fallback_model": None,
        "language": "auto",
        **stt_raw,
    }
    if "kind" not in stt_raw:
        merged["kind"] = "batch"
    if "fallback_model" not in stt_raw:
        merged["fallback_model"] = None
    return merged


def _normalize_vad(vad_raw: Dict[str, Any], stt_kind: str = "batch") -> Dict[str, Any]:
    base = SILERO_STREAMING_DEFAULTS if stt_kind == "streaming" else DEFAULT_AGENT["vad"]
    merged = {**base, **vad_raw}
    if "min_silence_ms" in vad_raw and "redemption_ms" not in vad_raw:
        merged["redemption_ms"] = vad_raw["min_silence_ms"]
    elif "redemption_ms" in vad_raw and "min_silence_ms" not in vad_raw:
        merged["min_silence_ms"] = vad_raw["redemption_ms"]
    return merged


class AgentManager:
    @staticmethod
    async def ensure_default_agent():
        """Ensure default agent exists on startup."""
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                """
                INSERT OR IGNORE INTO agents (id, name, description, system_prompt, llm_json, stt_json, tts_json, vad_json, audio_json, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    DEFAULT_AGENT["id"],
                    DEFAULT_AGENT["name"],
                    DEFAULT_AGENT["description"],
                    DEFAULT_AGENT["system_prompt"],
                    json.dumps(DEFAULT_AGENT["llm"]),
                    json.dumps(DEFAULT_AGENT["stt"]),
                    json.dumps(DEFAULT_AGENT["tts"]),
                    json.dumps(DEFAULT_AGENT["vad"]),
                    json.dumps(DEFAULT_AGENT["audio"]),
                ),
            )
            await db.commit()

    @staticmethod
    async def list_agents() -> List[Dict[str, Any]]:
        await AgentManager.ensure_default_agent()
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM agents ORDER BY updated_at DESC")
            rows = await cursor.fetchall()
            agents = []
            for r in rows:
                stt_norm = _normalize_stt(json.loads(r["stt_json"]))
                agents.append({
                    "id": r["id"],
                    "name": r["name"],
                    "description": r["description"],
                    "system_prompt": r["system_prompt"],
                    "llm": json.loads(r["llm_json"]),
                    "stt": stt_norm,
                    "tts": json.loads(r["tts_json"]),
                    "vad": _normalize_vad(json.loads(r["vad_json"]), stt_kind=stt_norm.get("kind", "batch")),
                    "audio": json.loads(r["audio_json"]),
                    "is_active": bool(r["is_active"]),
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                    "last_used_at": r["last_used_at"],
                    "schema_version": 2,
                })
            return agents

    @staticmethod
    async def get_agent(agent_id: str) -> Optional[Dict[str, Any]]:
        await AgentManager.ensure_default_agent()
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
            r = await cursor.fetchone()
            if not r:
                return None
            stt_norm = _normalize_stt(json.loads(r["stt_json"]))
            return {
                "id": r["id"],
                "name": r["name"],
                "description": r["description"],
                "system_prompt": r["system_prompt"],
                "llm": json.loads(r["llm_json"]),
                "stt": stt_norm,
                "tts": json.loads(r["tts_json"]),
                "vad": _normalize_vad(json.loads(r["vad_json"]), stt_kind=stt_norm.get("kind", "batch")),
                "audio": json.loads(r["audio_json"]),
                "is_active": bool(r["is_active"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "last_used_at": r["last_used_at"],
                "schema_version": 2,
            }

    @staticmethod
    async def create_agent(data: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = data.get("id") or str(uuid.uuid4())
        stt_kind = data.get("stt", {}).get("kind", "batch")
        default_vad = SILERO_STREAMING_DEFAULTS if stt_kind == "streaming" else {"mode": "energy", "threshold": 0.045, "min_silence_ms": 650}
        vad_data = data.get("vad") or default_vad
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO agents (id, name, description, system_prompt, llm_json, stt_json, tts_json, vad_json, audio_json, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    agent_id,
                    data["name"],
                    data.get("description", ""),
                    data["system_prompt"],
                    json.dumps(data["llm"]),
                    json.dumps(data["stt"]),
                    json.dumps(data["tts"]),
                    json.dumps(vad_data),
                    json.dumps(data.get("audio", {"echo_mode": "laptop_speakers", "barge_in": True})),
                ),
            )
            await db.commit()
        return await AgentManager.get_agent(agent_id)

    @staticmethod
    async def update_agent(agent_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        async with get_db() as db:
            await db.execute(
                """
                UPDATE agents SET
                    name = ?,
                    description = ?,
                    system_prompt = ?,
                    llm_json = ?,
                    stt_json = ?,
                    tts_json = ?,
                    vad_json = ?,
                    audio_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    data["name"],
                    data.get("description", ""),
                    data["system_prompt"],
                    json.dumps(data["llm"]),
                    json.dumps(data["stt"]),
                    json.dumps(data["tts"]),
                    json.dumps(data.get("vad", {})),
                    json.dumps(data.get("audio", {})),
                    agent_id,
                ),
            )
            await db.commit()
        return await AgentManager.get_agent(agent_id)

    @staticmethod
    async def delete_agent(agent_id: str) -> bool:
        if agent_id == DEFAULT_AGENT["id"]:
            return False  # Protect default agent
        async with get_db() as db:
            await db.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
            await db.commit()
        return True

    @staticmethod
    async def load_agent(agent_id: str) -> Dict[str, Any]:
        global _ACTIVE_AGENT_ID, _ACTIVE_LOAD_STATE
        async with _LOAD_LOCK:
            agent = await AgentManager.get_agent(agent_id)
            if not agent:
                raise ValueError("Agent not found")

            _ACTIVE_LOAD_STATE = "loading"
            try:
                # Pre-flight memory fit validation per S-REVIEW
                mem = get_mac_memory_info()
                # Simulated estimated agent triad footprint (LLM + STT + TTS ≈ 2360 MB)
                estimated_ram_mb = 2360.0
                safe_headroom_mb = mem["total_mb"] - 1500.0
                if estimated_ram_mb > safe_headroom_mb:
                    _ACTIVE_LOAD_STATE = "error"
                    raise ValueError(
                        f"Insufficient memory: agent triad requires ~{estimated_ram_mb:.0f} MB "
                        f"but only {safe_headroom_mb:.0f} MB is safely available on this host"
                    )

                async with get_db() as db:
                    await db.execute("UPDATE agents SET is_active = 0")
                    await db.execute("UPDATE agents SET is_active = 1, last_used_at = CURRENT_TIMESTAMP WHERE id = ?", (agent_id,))
                    await db.commit()

                _ACTIVE_AGENT_ID = agent_id
                _ACTIVE_LOAD_STATE = "ready"

                return {
                    "status": "ready",
                    "load_id": str(uuid.uuid4()),
                    "active_agent": agent,
                    "memory_verified": True,
                }
            except Exception:
                if _ACTIVE_LOAD_STATE == "loading":
                    _ACTIVE_LOAD_STATE = "error"
                raise

    @staticmethod
    def get_active_state() -> Dict[str, Any]:
        return {
            "active_agent_id": _ACTIVE_AGENT_ID,
            "state": _ACTIVE_LOAD_STATE,
        }

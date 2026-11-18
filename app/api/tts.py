"""ElevenLabs-compatible Text-to-Speech endpoints with extended metadata."""

import re
import time
from typing import Dict, Optional, Any
from fastapi import APIRouter, Depends, Response, Body, HTTPException, Query
from app.core.config import settings
from app.core.errors import PayloadTooLargeError
from app.core.limits import tts_limiter
from app.core.security import require_scope, require_admin_key
from app.services.key_manager import KeyManager, ApiKeyRecord
from app.providers.localtts_client import LocalTTSClient
from app.schemas.voice import TTSModelListResponse, TTSRequest
from app.services.model_registry import model_registry
from app.services.residency_manager import residency_manager

router = APIRouter(tags=["Text-to-Speech"])
tts_client = LocalTTSClient()

# In-memory voice overrides: {voice_id: {gender, name}}
_VOICE_OVERRIDES: Dict[str, Dict[str, str]] = {}


def infer_voice_metadata(voice_id: str) -> Dict[str, str]:
    """Infer gender and language/accent from Kokoro naming conventions."""
    match = re.match(r"^([a-z])([fm])_(.+)$", voice_id)
    if match:
        accent_code, gender_code, base_name = match.groups()
        gender = "female" if gender_code == "f" else "male"
        language_map = {
            "a": "en-us",
            "b": "en-gb",
            "j": "ja",
            "z": "zh",
            "e": "es",
            "f": "fr",
            "h": "hi",
            "i": "it",
            "p": "pt",
        }
        lang = language_map.get(accent_code, "en")
        clean_name = base_name.replace("_", " ").title()
        return {"gender": gender, "language": lang, "inferred_name": clean_name}

    return {"gender": "unknown", "language": "en", "inferred_name": voice_id}


@router.post("/v1/text-to-speech/{voice_id}/stream")
async def text_to_speech_stream(
    voice_id: str,
    request: TTSRequest,
    key: ApiKeyRecord = Depends(require_scope("tts")),
):
    target_engine = request.model_id or settings.LOCAL_TTS_MODEL or "kokoro"
    is_avail, unavail_reason = await model_registry.probe_tts_engine(target_engine)
    if not is_avail:
        raise HTTPException(
            status_code=400,
            detail=f"TTS engine '{target_engine}' is not available: {unavail_reason or 'weights missing or engine failed'}",
        )

    await KeyManager.check_quota(key, cost_type="audio")
    KeyManager.check_model_allowed(key, target_engine)
    if len(request.text) > settings.MAX_TTS_TEXT_CHARS:
        raise PayloadTooLargeError(
            f"TTS text length ({len(request.text)} characters) exceeds limit of {settings.MAX_TTS_TEXT_CHARS}"
        )

    start_t = time.time()
    speed = request.voice_settings.speed if request.voice_settings else 1.0
    status_code = 200
    pcm_bytes = b""
    residency_manager.track_request_start(target_engine)
    try:
        async with tts_limiter.acquire(
            key_id=key.id, backend="tts", max_concurrent=key.max_concurrent
        ):
            try:
                pcm_bytes = await tts_client.synthesize_http(
                    text=request.text,
                    voice_id=voice_id,
                    model_id=target_engine,
                    speed=speed,
                )
                headers = {
                    "Content-Type": "audio/pcm;rate=24000;encoding=signed-int;bits=16",
                    "x-audio-sample-rate": "24000",
                    "x-audio-format": "s16le",
                    "x-audio-channels": "1",
                }
                return Response(
                    content=pcm_bytes, media_type="audio/pcm", headers=headers
                )
            except Exception as e:
                status_code = getattr(e, "status_code", 500)
                raise
            finally:
                dur_ms = (time.time() - start_t) * 1000.0
                # 24kHz 16-bit mono = 48,000 bytes/sec
                audio_sec = round(len(pcm_bytes) / 48000.0, 2)
                await KeyManager.log_usage(
                    key.id,
                    f"/v1/text-to-speech/{voice_id}/stream",
                    dur_ms,
                    audio_sec,
                    status_code,
                )
    finally:
        residency_manager.track_request_end(target_engine)


@router.get("/v1/voices")
async def get_voices(
    engine: Optional[str] = Query(None),
    model_id: Optional[str] = Query(None),
    key: ApiKeyRecord = Depends(require_scope("tts")),
):
    target_engine = engine or model_id or settings.LOCAL_TTS_MODEL or "kokoro"
    is_avail, _ = await model_registry.probe_tts_engine(target_engine)
    if not is_avail:
        return {"voices": []}

    raw_voices = await tts_client.list_voices(model_id=target_engine)
    if not raw_voices and target_engine.lower() in ("kokoro", "kokoro-82m", "default"):
        from app.schemas.voice import Voice, VoiceSettings

        raw_voices = [
            Voice(
                voice_id="af_heart",
                name="Heart",
                category="female",
                settings=VoiceSettings(speed=1.0),
            )
        ]
    enriched = []

    for v in raw_voices:
        vid = v.voice_id
        meta = infer_voice_metadata(vid)
        override = _VOICE_OVERRIDES.get(vid, {})

        gender = override.get(
            "gender", v.category if v.category in ("female", "male") else meta["gender"]
        )
        name = override.get(
            "name", v.name if v.name != "Unknown" else meta["inferred_name"]
        )
        source = "manual" if vid in _VOICE_OVERRIDES else "inferred"

        enriched.append(
            {
                "voice_id": vid,
                "name": name,
                "gender": gender,
                "language": meta["language"],
                "engine": target_engine,
                "source": source,
                "settings": {"speed": v.settings.speed if v.settings else 1.0},
            }
        )

    return {"voices": enriched}


@router.put("/v1/voices/{id}/meta")
async def update_voice_meta(
    id: str,
    payload: Dict[str, Any] = Body(...),
    admin: ApiKeyRecord = Depends(require_admin_key),
):
    _VOICE_OVERRIDES[id] = {
        "gender": payload.get("gender", "unknown"),
        "name": payload.get("name", id),
    }
    return {"status": "updated", "voice_id": id, "metadata": _VOICE_OVERRIDES[id]}


@router.post("/v1/voices/{id}/preview")
async def preview_voice(
    id: str,
    key: ApiKeyRecord = Depends(require_scope("tts")),
):
    sample_text = "Hello! This is a preview of my synthetic voice."
    pcm_bytes = await tts_client.synthesize_http(
        text=sample_text,
        voice_id=id,
        model_id="kokoro",
    )
    headers = {
        "Content-Type": "audio/pcm;rate=24000;encoding=signed-int;bits=16",
        "x-audio-sample-rate": "24000",
        "x-audio-format": "s16le",
        "x-audio-channels": "1",
    }
    return Response(content=pcm_bytes, media_type="audio/pcm", headers=headers)


@router.get("/v1/tts/models", response_model=TTSModelListResponse)
async def get_tts_models(key: ApiKeyRecord = Depends(require_scope("tts"))):
    tts_models = await model_registry.list_models(task="tts", available=True)
    models = [{"id": m["id"], "name": m["display_name"]} for m in tts_models]
    return TTSModelListResponse(models=models)

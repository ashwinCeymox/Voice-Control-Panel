"""ElevenLabs-compatible Text-to-Speech endpoints with extended metadata."""
import re
from typing import Dict, List, Optional, Any
from fastapi import APIRouter, Depends, Response, Body
from app.core.config import settings
from app.core.security import require_api_key
from app.providers.localtts_client import LocalTTSClient
from app.schemas.voice import TTSModelListResponse, TTSRequest

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
    user: str = Depends(require_api_key),
):
    speed = request.voice_settings.speed if request.voice_settings else 1.0
    pcm_bytes = await tts_client.synthesize_http(
        text=request.text,
        voice_id=voice_id,
        model_id=request.model_id or settings.LOCAL_TTS_MODEL,
        speed=speed,
    )

    headers = {
        "Content-Type": "audio/pcm;rate=24000;encoding=signed-int;bits=16",
        "x-audio-sample-rate": "24000",
        "x-audio-format": "s16le",
        "x-audio-channels": "1",
    }
    return Response(content=pcm_bytes, media_type="audio/pcm", headers=headers)


@router.get("/v1/voices")
async def get_voices(
    model_id: Optional[str] = None,
    user: str = Depends(require_api_key),
):
    raw_voices = await tts_client.list_voices(model_id=model_id)
    enriched = []

    for v in raw_voices:
        vid = v.voice_id
        meta = infer_voice_metadata(vid)
        override = _VOICE_OVERRIDES.get(vid, {})

        gender = override.get("gender", meta["gender"])
        name = override.get("name", v.name if v.name != "Unknown" else meta["inferred_name"])
        source = "manual" if vid in _VOICE_OVERRIDES else "inferred"

        enriched.append({
            "voice_id": vid,
            "name": name,
            "gender": gender,
            "language": meta["language"],
            "engine": model_id or "kokoro",
            "source": source,
            "settings": {"speed": v.settings.speed if v.settings else 1.0},
        })

    return {"voices": enriched}


@router.put("/v1/voices/{id}/meta")
async def update_voice_meta(
    id: str,
    payload: Dict[str, Any] = Body(...),
    user: str = Depends(require_api_key),
):
    _VOICE_OVERRIDES[id] = {
        "gender": payload.get("gender", "unknown"),
        "name": payload.get("name", id),
    }
    return {"status": "updated", "voice_id": id, "metadata": _VOICE_OVERRIDES[id]}


@router.post("/v1/voices/{id}/preview")
async def preview_voice(
    id: str,
    user: str = Depends(require_api_key),
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
async def get_tts_models(user: str = Depends(require_api_key)):
    models = await tts_client.list_models()
    return TTSModelListResponse(models=models)

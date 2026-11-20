"""
API Testing Endpoint.

Provides endpoint for developers and automated tools to verify API keys,
inspect permissions, and retrieve the count and list of available (allotted) models.
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.errors import UnauthorizedError
from app.core.security import get_authenticated_key, resolve_client_ip
from app.services.key_manager import KeyManager, ApiKeyRecord
from app.services.model_registry import model_registry
from app.services.model_manifest import model_manifest

router = APIRouter(tags=["API Testing"])


class ApiTestRequestBody(BaseModel):
    api_key: Optional[str] = Field(None, description="Plain API key to test")
    key: Optional[str] = Field(None, description="Alias for api_key")


class ModelAllotmentInfo(BaseModel):
    id: str
    name: Optional[str] = None
    task: Optional[str] = None
    kind: Optional[str] = None
    engine: Optional[str] = None
    available: bool = True


class ApiTestingResponse(BaseModel):
    status: str = "success"
    message: str
    base_url: str = Field(..., description="Public base URL of this running voice agent platform")
    test_endpoint: str = Field(..., description="Full URL of the verified testing endpoint")
    key_id: str
    key_name: str
    role: str
    scopes: List[str]
    model_allowlist: Optional[List[str]] = None
    models_count: int
    models_available_count: int
    models_allotted_count: int
    allotted_models: List[str]
    models: List[ModelAllotmentInfo]


async def _resolve_key_record(
    request: Request,
    authorization: Optional[str],
    xi_api_key: Optional[str],
    api_key_query: Optional[str],
    body_data: Optional[Dict[str, Any]] = None,
) -> ApiKeyRecord:
    """Resolve key from header, query param, body, or panel session."""
    # 1. Try standard get_authenticated_key first if credentials present in header/query/cookie
    auth_header = authorization or request.headers.get("authorization")
    xi_header = xi_api_key or request.headers.get("xi-api-key")
    query_key = api_key_query or request.query_params.get("api_key")
    session_cookie = request.cookies.get("admin_session")

    has_standard_cred = bool(auth_header or xi_header or query_key or session_cookie)

    if has_standard_cred:
        try:
            return await get_authenticated_key(
                request=request,
                authorization=authorization,
                xi_api_key=xi_api_key,
                api_key=api_key_query,
            )
        except UnauthorizedError:
            # If standard auth explicitly failed, don't fallback to body unless body supplied
            if not body_data:
                raise

    # 2. If body supplied (e.g. POST JSON), test plain key from body
    if body_data:
        raw_key = body_data.get("api_key") or body_data.get("key")
        if raw_key and isinstance(raw_key, str) and raw_key.strip():
            plain_key = raw_key.strip()
            client_ip = resolve_client_ip(request)
            record = await KeyManager.authenticate(plain_key, client_ip=client_ip)
            if record:
                return record
            raise UnauthorizedError("Invalid or revoked API key.")

    # 3. If neither worked, raise missing API key error
    raise UnauthorizedError(
        "Missing API key. Provide Bearer, xi-api-key header, api_key query param, or JSON body."
    )


async def get_allotted_models_for_key(key: ApiKeyRecord) -> List[Dict[str, Any]]:
    """Determine all installed and available models allotted to the given API key."""
    # Retrieve all available and installed models
    all_models: List[Dict[str, Any]] = []
    try:
        registered = await model_registry.list_models(available=True, installed=True)
        if registered:
            all_models = list(registered)
    except Exception:
        all_models = []

    # Merge models from manifest so all installed models are included
    for cat in ("stt", "llm", "tts"):
        for m in model_manifest.get_models_by_category(cat, available_only=True):
            if not any(existing.get("id", "").lower() == m["id"].lower() for existing in all_models):
                all_models.append(
                    {
                        "id": m["id"],
                        "display_name": m.get("name") or m["id"],
                        "task": cat,
                        "kind": m.get("kind"),
                        "engine": m.get("provider", "local"),
                        "available": True,
                        "installed": True,
                    }
                )

    # Deduplicate models by lowercase ID
    unique_models: Dict[str, Dict[str, Any]] = {}
    for m in all_models:
        mid = m.get("id", "").strip()
        if mid and mid.lower() not in unique_models:
            unique_models[mid.lower()] = m

    # Ensure any explicitly allowlisted models configured on the key are included as candidates
    if key.model_allowlist and len(key.model_allowlist) > 0:
        for a_id in key.model_allowlist:
            clean_id = a_id.strip() if a_id else ""
            if clean_id and clean_id.lower() not in unique_models:
                lower = clean_id.lower()
                cat = (
                    "stt"
                    if any(k in lower for k in ("whisper", "stt", "asr"))
                    else ("tts" if any(k in lower for k in ("tts", "kokoro", "piper")) else "llm")
                )
                unique_models[lower] = {
                    "id": clean_id,
                    "display_name": clean_id.replace("-", " ").title(),
                    "task": cat,
                    "kind": "batch" if cat == "stt" else None,
                    "engine": "local",
                    "available": True,
                    "installed": True,
                }

    candidates = list(unique_models.values())

    # Case 1: Admin role
    if key.role == "admin":
        if key.model_allowlist and len(key.model_allowlist) > 0:
            allow_set = {a.lower().strip() for a in key.model_allowlist if a.strip()}
            return [m for m in candidates if m["id"].lower() in allow_set]
        return candidates

    # Case 2: Caller role
    scopes = set(s.lower() for s in (key.scopes or []))
    has_admin_scope = "admin" in scopes
    has_voice_scope = bool({"voice", "voice_stream"}.intersection(scopes))

    def is_task_allowed(m: Dict[str, Any]) -> bool:
        if has_admin_scope or has_voice_scope:
            return True
        task = (m.get("task") or "").lower()
        kind = (m.get("kind") or "").lower()

        if task == "llm" and "llm" in scopes:
            return True
        if task == "stt":
            if "stt" in scopes:
                return True
            if kind == "streaming" and "stt_stream" in scopes:
                return True
        if task == "tts" and "tts" in scopes:
            return True
        if task == "vad" and bool({"voice", "voice_stream", "vad"}.intersection(scopes)):
            return True
        return False

    # Filter candidate models by scope
    scoped_models = [m for m in candidates if is_task_allowed(m)]

    # Further restrict by model_allowlist if configured on the key
    if key.model_allowlist and len(key.model_allowlist) > 0:
        allow_set = {a.lower().strip() for a in key.model_allowlist if a.strip()}
        return [m for m in scoped_models if m["id"].lower() in allow_set]

    return scoped_models


async def handle_api_testing(
    request: Request,
    authorization: Optional[str] = Header(None),
    xi_api_key: Optional[str] = Header(None, alias="xi-api-key"),
    api_key: Optional[str] = Query(None),
    body: Optional[ApiTestRequestBody] = None,
) -> ApiTestingResponse:
    """Core logic to verify key and return model allotment counts and details."""
    body_dict = body.model_dump(exclude_none=True) if body else None
    key_record = await _resolve_key_record(
        request=request,
        authorization=authorization,
        xi_api_key=xi_api_key,
        api_key_query=api_key,
        body_data=body_dict,
    )

    allotted_models = await get_allotted_models_for_key(key_record)
    allotted_count = len(allotted_models)

    models_info = [
        ModelAllotmentInfo(
            id=m["id"],
            name=m.get("display_name") or m.get("name") or m["id"],
            task=m.get("task"),
            kind=m.get("kind"),
            engine=m.get("engine"),
            available=m.get("available", True),
        )
        for m in allotted_models
    ]

    message = (
        f"API key '{key_record.name}' is valid and active. "
        f"{allotted_count} model(s) available for this key."
    )

    request_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    request_scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    public_base = settings.get_public_base_url(
        request_host=request_host, request_scheme=request_scheme
    )
    test_url = f"{public_base}/v1/api-testing"

    return ApiTestingResponse(
        status="success",
        message=message,
        base_url=public_base,
        test_endpoint=test_url,
        key_id=key_record.id,
        key_name=key_record.name,
        role=key_record.role,
        scopes=key_record.scopes,
        model_allowlist=key_record.model_allowlist,
        models_count=allotted_count,
        models_available_count=allotted_count,
        models_allotted_count=allotted_count,
        allotted_models=[m["id"] for m in allotted_models],
        models=models_info,
    )


# -------------------------------------------------------------------------
# Route Registrations
# -------------------------------------------------------------------------
@router.get("/v1/api-testing", response_model=ApiTestingResponse)
@router.get("/api-testing", response_model=ApiTestingResponse)
@router.get("/v1/api-test", response_model=ApiTestingResponse)
@router.get("/api-test", response_model=ApiTestingResponse)
async def api_testing_get(
    request: Request,
    authorization: Optional[str] = Header(None),
    xi_api_key: Optional[str] = Header(None, alias="xi-api-key"),
    api_key: Optional[str] = Query(None),
):
    """Test API key authentication via GET and retrieve available model allotment count."""
    return await handle_api_testing(
        request=request,
        authorization=authorization,
        xi_api_key=xi_api_key,
        api_key=api_key,
        body=None,
    )


@router.post("/v1/api-testing", response_model=ApiTestingResponse)
@router.post("/api-testing", response_model=ApiTestingResponse)
@router.post("/v1/api-test", response_model=ApiTestingResponse)
@router.post("/api-test", response_model=ApiTestingResponse)
async def api_testing_post(
    request: Request,
    authorization: Optional[str] = Header(None),
    xi_api_key: Optional[str] = Header(None, alias="xi-api-key"),
    api_key: Optional[str] = Query(None),
    body: Optional[ApiTestRequestBody] = None,
):
    """Test API key authentication via POST (header, query, or JSON body) and retrieve available model allotment count."""
    return await handle_api_testing(
        request=request,
        authorization=authorization,
        xi_api_key=xi_api_key,
        api_key=api_key,
        body=body,
    )

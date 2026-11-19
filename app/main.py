"""FastAPI application entry point and lifespan coordinator."""

import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.models import router as models_router
from app.api.transcriptions import router as transcriptions_router
from app.api.tts import router as tts_router
from app.api.voice import router as voice_router
from app.api.system import system_router
from app.api.gallery import gallery_router
from app.api.agents import agents_router
from app.api.admin_keys import router as admin_keys_router
from app.api.admin_profiles import router as admin_profiles_router
from app.api.admin_usage import router as admin_usage_router
from app.api.metrics import router as metrics_router
from app.api.stream_stt import router as stream_stt_router
from app.api.admin_auth import router as panel_auth_router
from app.api.api_testing import router as api_testing_router
from app.core.config import settings
from app.core.errors import AppError, app_error_handler
from app.core.logging import setup_logging
from app.storage.database import init_db
import asyncio
import logging
from pathlib import Path
import httpx

logger = logging.getLogger("voice_agent.startup")


async def _prewarm_models():
    """Background task to pre-warm default STT and LLM models on server startup."""
    await asyncio.sleep(1.0)
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.LOCALAI_BASE_URL}/readyz")
            if resp.status_code != 200:
                return
    except Exception:
        return

    logger.info("Pre-warming LocalAI STT and LLM models in background...")
    # Pre-warm STT
    try:
        sample_audio = (
            Path(__file__).resolve().parent.parent
            / "tests"
            / "fixtures"
            / "sample_utterance.wav"
        )
        if sample_audio.exists():
            from app.services.voice_orchestrator import orchestrator

            await orchestrator.stt.transcribe(
                audio_bytes=sample_audio.read_bytes(),
                filename="sample.wav",
                content_type="audio/wav",
                model=settings.LOCALAI_STT_MODEL,
            )
            logger.info(
                "LocalAI STT model '%s' pre-warmed successfully",
                settings.LOCALAI_STT_MODEL,
            )
    except Exception as e:
        logger.debug("STT prewarm skipped or failed: %s", e)

    # Pre-warm LLM
    try:
        from app.schemas.chat import ChatCompletionRequest, ChatMessage
        from app.services.voice_orchestrator import orchestrator

        await orchestrator.llm.complete(
            ChatCompletionRequest(
                model=settings.LOCALAI_LLM_MODEL,
                messages=[ChatMessage(role="user", content="hi")],
                max_tokens=2,
            )
        )
        logger.info(
            "LocalAI LLM model '%s' pre-warmed successfully", settings.LOCALAI_LLM_MODEL
        )
    except Exception as e:
        logger.debug("LLM prewarm skipped or failed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    setup_logging()
    env = settings.ENVIRONMENT.lower()
    if env not in ("development", "staging", "production"):
        raise RuntimeError(
            f"Invalid ENVIRONMENT '{settings.ENVIRONMENT}'. Allowed values: development, staging, production."
        )
    if env in ("production", "staging") or settings.GATEWAY_HOST not in ("127.0.0.1", "localhost"):
        if (
            settings.ADMIN_API_KEY == "dev-voice-key-12345"
            or not settings.ADMIN_API_KEY
        ):
            raise RuntimeError(
                f"CRITICAL SECURITY CONFIGURATION ERROR: Running with GATEWAY_HOST='{settings.GATEWAY_HOST}' (mode: {env}) with default or empty ADMIN_API_KEY! "
                "Generate a 256-bit random key with 'openssl rand -hex 32' and set ADMIN_API_KEY in .env before starting."
            )
        if "dev-secret-key" in settings.API_KEY_HASH_SECRET:
            raise RuntimeError(
                f"CRITICAL SECURITY CONFIGURATION ERROR: Running with GATEWAY_HOST='{settings.GATEWAY_HOST}' (mode: {env}) with default API_KEY_HASH_SECRET! "
                "Generate a high-entropy secret with 'openssl rand -hex 32' and set API_KEY_HASH_SECRET in .env."
            )
        if "*" in (settings.CORS_ALLOWED_ORIGINS or []):
            raise RuntimeError(
                f"CRITICAL SECURITY CONFIGURATION ERROR: Running in {env} with wildcard CORS ('*')! "
                "Specify explicit trusted origins in CORS_ALLOWED_ORIGINS in .env."
            )
    await init_db()
    from app.services.admin_auth import AdminAuthService

    await AdminAuthService.seed_admin_user()
    try:
        from app.services.model_manifest import model_manifest

        await model_manifest.sync_manifest()
        from app.services.residency_manager import residency_manager

        await residency_manager.initialize()
    except Exception as e:
        logger.warning("Initial models manifest/residency sync failed: %s", e)
    from app.services.hygiene_manager import hygiene_manager

    hygiene_manager.start()
    asyncio.create_task(_prewarm_models())
    yield
    # Shutdown
    hygiene_manager.stop()


app = FastAPI(
    title="Self-Hosted Voice AI Gateway",
    description="ElevenLabs-style Voice API with LocalAI (LLM/STT) and local-tts (TTS).",
    version="0.1.0",
    docs_url="/docs" if settings.ENABLE_DOCS else None,
    redoc_url="/redoc" if settings.ENABLE_DOCS else None,
    openapi_url="/openapi.json" if settings.ENABLE_DOCS else None,
    lifespan=lifespan,
)

# CORS middleware with restricted origins and credential safety (H2)
cors_origins = settings.CORS_ALLOWED_ORIGINS or [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
cors_allow_credentials = False if "*" in cors_origins else True

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=r"https://.*\.proxy\.runpod\.net",
    allow_credentials=cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    request.state.request_id = f"req-{request_id}"
    response = await call_next(request)
    response.headers["x-request-id"] = request.state.request_id
    return response


# Register custom error handler
app.add_exception_handler(AppError, app_error_handler)

# Register routers
app.include_router(health_router)
app.include_router(transcriptions_router)
app.include_router(chat_router)
app.include_router(tts_router)
app.include_router(models_router)
app.include_router(voice_router)
app.include_router(system_router)
app.include_router(gallery_router)
app.include_router(agents_router)
app.include_router(admin_keys_router)
app.include_router(admin_profiles_router)
app.include_router(admin_usage_router)
app.include_router(stream_stt_router)
app.include_router(metrics_router)
app.include_router(panel_auth_router)
app.include_router(api_testing_router)

# Mount Control Panel static SPA if built (supports single-port / single-origin RunPod & Hetzner deployment)
frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if frontend_dist.is_dir():
    from starlette.staticfiles import StaticFiles
    from starlette.responses import FileResponse, RedirectResponse

    assets_dir = frontend_dist / "assets"
    if assets_dir.is_dir():
        app.mount(
            "/voicecontrolpanel/assets",
            StaticFiles(directory=str(assets_dir)),
            name="controlpanel_assets",
        )

    @app.get("/voicecontrolpanel")
    @app.get("/voicecontrolpanel/")
    async def serve_controlpanel_root():
        return FileResponse(frontend_dist / "index.html")

    @app.get("/voicecontrolpanel/{full_path:path}")
    async def serve_controlpanel(full_path: str = ""):
        target = frontend_dist / full_path
        if full_path and target.is_file():
            return FileResponse(target)
        return FileResponse(frontend_dist / "index.html")


@app.get("/")
async def root(request: Request):
    if settings.SERVE_FRONTEND and frontend_dist.is_dir():
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return RedirectResponse(url="/voicecontrolpanel", status_code=307)
    return {
        "app": "Self-Hosted Voice AI Platform",
        "status": "online",
        "docs": "/docs",
        "panel": "/voicecontrolpanel",
    }

"""Application configuration and settings."""

import os
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_ENV: str = "development"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    APP_DATABASE_PATH: str = os.environ.get("APP_DATABASE_PATH", "voice_platform.db")
    MODELS_MANIFEST_PATH: str = os.environ.get(
        "MODELS_MANIFEST_PATH", "data/installed_models_manifest.json"
    )

    # LocalAI
    LOCALAI_BASE_URL: str = "http://127.0.0.1:8080"
    LOCALAI_MODELS_PATH: Optional[str] = os.environ.get(
        "LOCALAI_MODELS_PATH",
        "../LocalAI/models" if os.path.isdir("../LocalAI/models") else "models",
    )
    LOCALAI_API_KEY: Optional[str] = None
    LOCALAI_LLM_MODEL: str = "llama-3.2-1b-instruct"
    LOCALAI_STT_MODEL: str = "whisper-base"
    LOCALAI_REQUEST_TIMEOUT_SECONDS: int = 120
    LOCALAI_ALLOWED_LLM_MODELS: Optional[str] = None
    LOCALAI_ALLOWED_STT_MODELS: Optional[str] = None

    # local-tts
    LOCAL_TTS_HTTP_BASE_URL: str = "http://127.0.0.1:8880"
    LOCAL_TTS_WS_BASE_URL: str = "ws://127.0.0.1:8880"
    LOCAL_TTS_MODEL: str = "kokoro"
    LOCAL_TTS_VOICE_ID: str = "af_heart"
    LOCAL_TTS_SPEED: float = 1.0

    # Streaming STT (sherpa-onnx / Moonshine)
    STREAMING_STT_URL: str = os.environ.get(
        "STREAMING_STT_URL", "http://127.0.0.1:8088"
    )
    STREAMING_STT_WS_URL: str = os.environ.get(
        "STREAMING_STT_WS_URL", "ws://127.0.0.1:8088/ws"
    )
    STT_STREAM_ENABLED: bool = True
    STREAMING_STT_TIMEOUT_SECONDS: float = 15.0
    MAX_ACTIVE_STT_STREAMS: int = 5
    STREAMING_STT_DEFAULT_MODEL: str = "sherpa-onnx-streaming-zipformer-en-20M"
    STREAMING_STT_PRECISION: str = os.environ.get("STREAMING_STT_PRECISION", "fp32")
    STREAMING_STT_PROVIDER: str = os.environ.get("STREAMING_STT_PROVIDER", "cpu")
    STREAMING_STT_CHUNK_MS: int = int(os.environ.get("STREAMING_STT_CHUNK_MS", "560"))
    STREAMING_STT_LANGUAGE: str = os.environ.get("STREAMING_STT_LANGUAGE", "en")

    # Security & Administration
    ENVIRONMENT: str = os.environ.get("ENVIRONMENT", "development")
    ADMIN_EMAIL: str = os.environ.get("ADMIN_EMAIL", "admin@voicepanel.io")
    ADMIN_INITIAL_PASSWORD: Optional[str] = os.environ.get(
        "ADMIN_INITIAL_PASSWORD", "adminVOICE3.md"
    )
    GATEWAY_HOST: str = os.environ.get("GATEWAY_HOST", "127.0.0.1")
    GATEWAY_PORT: int = int(os.environ.get("GATEWAY_PORT", "8000"))
    COOKIE_SECURE: Optional[bool] = None
    TRUSTED_PROXY_IPS: str = os.environ.get("TRUSTED_PROXY_IPS", "")
    ADMIN_API_KEY: Optional[str] = os.environ.get(
        "ADMIN_API_KEY", "dev-voice-key-12345"
    )
    API_KEY_HASH_SECRET: str = "dev-secret-key-change-me-in-production-use-high-entropy"
    CORS_ALLOWED_ORIGINS: List[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    ENABLE_DOCS: bool = False
    LOG_TRANSCRIPTS: bool = False
    STT_ALLOW_FALLBACK: bool = False

    # Limits & Timeouts
    MAX_AUDIO_UPLOAD_MB: int = 20
    MAX_AUDIO_DURATION_SECONDS: int = 120
    MAX_TTS_TEXT_CHARS: int = 2000
    MAX_SESSION_MESSAGES: int = 30
    MAX_ACTIVE_VOICE_TURNS: int = 1
    VOICE_TEXT_CHUNK_MAX_CHARS: int = 250
    VOICE_TEXT_CHUNK_MAX_WAIT_MS: int = 500

    TIMEOUT_STT_SECONDS: float = 30.0
    TIMEOUT_LLM_SECONDS: float = 60.0
    TIMEOUT_TTS_SECONDS: float = 20.0
    TIMEOUT_VOICE_TURN_SECONDS: float = 45.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cookie_secure(self) -> bool:
        if self.COOKIE_SECURE is not None:
            return self.COOKIE_SECURE
        return self.ENVIRONMENT.lower() in ("staging", "production")

    @property
    def trusted_proxies(self) -> List[str]:
        if not self.TRUSTED_PROXY_IPS:
            return []
        if isinstance(self.TRUSTED_PROXY_IPS, list):
            return self.TRUSTED_PROXY_IPS
        return [
            ip.strip() for ip in str(self.TRUSTED_PROXY_IPS).split(",") if ip.strip()
        ]

    @property
    def allowed_llm_models(self) -> List[str]:
        if self.LOCALAI_ALLOWED_LLM_MODELS:
            return [
                m.strip()
                for m in self.LOCALAI_ALLOWED_LLM_MODELS.split(",")
                if m.strip()
            ]
        return []

    @property
    def allowed_stt_models(self) -> List[str]:
        if self.LOCALAI_ALLOWED_STT_MODELS:
            return [
                m.strip()
                for m in self.LOCALAI_ALLOWED_STT_MODELS.split(",")
                if m.strip()
            ]
        return []


settings = Settings()

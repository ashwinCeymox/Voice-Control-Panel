"""Application configuration and settings."""
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_ENV: str = "development"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    APP_DATABASE_PATH: str = "voice_platform.db"

    # LocalAI
    LOCALAI_BASE_URL: str = "http://127.0.0.1:8080"
    LOCALAI_MODELS_PATH: Optional[str] = "/Users/hashhwin047icloud.com/Downloads/test-pro/arch/LocalAI/models"
    LOCALAI_API_KEY: Optional[str] = None
    LOCALAI_LLM_MODEL: str = "llama-3.2-3b-instruct"
    LOCALAI_STT_MODEL: str = "whisper-1"
    LOCALAI_REQUEST_TIMEOUT_SECONDS: int = 120
    LOCALAI_ALLOWED_LLM_MODELS: Optional[str] = None
    LOCALAI_ALLOWED_STT_MODELS: Optional[str] = None

    # local-tts
    LOCAL_TTS_HTTP_BASE_URL: str = "http://127.0.0.1:8880"
    LOCAL_TTS_WS_BASE_URL: str = "ws://127.0.0.1:8880"
    LOCAL_TTS_MODEL: str = "kokoro"
    LOCAL_TTS_VOICE_ID: str = "af_heart"
    LOCAL_TTS_SPEED: float = 1.0

    # Security & Limits
    API_KEY_HASH_SECRET: str = "dev-secret-key-change-me-in-production-use-high-entropy"
    MAX_AUDIO_UPLOAD_MB: int = 20
    MAX_AUDIO_DURATION_SECONDS: int = 120
    MAX_SESSION_MESSAGES: int = 30
    MAX_ACTIVE_VOICE_TURNS: int = 1
    VOICE_TEXT_CHUNK_MAX_CHARS: int = 250
    VOICE_TEXT_CHUNK_MAX_WAIT_MS: int = 500

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def allowed_llm_models(self) -> List[str]:
        if self.LOCALAI_ALLOWED_LLM_MODELS:
            return [m.strip() for m in self.LOCALAI_ALLOWED_LLM_MODELS.split(",") if m.strip()]
        return []

    @property
    def allowed_stt_models(self) -> List[str]:
        if self.LOCALAI_ALLOWED_STT_MODELS:
            return [m.strip() for m in self.LOCALAI_ALLOWED_STT_MODELS.split(",") if m.strip()]
        return []


settings = Settings()

"""Application configuration and settings."""

import json
import os
from typing import Any, List, Optional, Union
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_ENV: str = "development"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    SERVE_FRONTEND: bool = True
    APP_DATABASE_PATH: str = os.environ.get("APP_DATABASE_PATH", "voice_platform.db")
    MODELS_MANIFEST_PATH: str = os.environ.get(
        "MODELS_MANIFEST_PATH", "data/installed_models_manifest.json"
    )

    # Ports & Internal Engine Base URLs
    LOCALAI_PORT: int = 8080
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
    TTS_PORT: int = 8880
    LOCAL_TTS_HTTP_BASE_URL: str = "http://127.0.0.1:8880"
    LOCAL_TTS_WS_BASE_URL: str = "ws://127.0.0.1:8880"
    LOCAL_TTS_MODEL: str = "kokoro"
    LOCAL_TTS_VOICE_ID: str = "af_heart"
    LOCAL_TTS_SPEED: float = 1.0

    # Streaming STT (sherpa-onnx / Moonshine)
    STT_PORT: int = 8088
    STREAMING_STT_URL: str = "http://127.0.0.1:8088"
    STREAMING_STT_WS_URL: str = "ws://127.0.0.1:8088/ws"

    @model_validator(mode="before")
    @classmethod
    def derive_ports_and_urls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            localai_port = data.get("LOCALAI_PORT") or os.environ.get("LOCALAI_PORT")
            if localai_port:
                base_url = data.get("LOCALAI_BASE_URL") or os.environ.get("LOCALAI_BASE_URL", "")
                if not base_url or base_url.startswith("http://127.0.0.1:") or base_url.startswith("http://localhost:") or "localai:" in base_url:
                    data["LOCALAI_BASE_URL"] = f"http://127.0.0.1:{localai_port}"

            tts_port = data.get("TTS_PORT") or os.environ.get("TTS_PORT")
            if tts_port:
                base_http = data.get("LOCAL_TTS_HTTP_BASE_URL") or os.environ.get("LOCAL_TTS_HTTP_BASE_URL", "")
                if not base_http or base_http.startswith("http://127.0.0.1:") or base_http.startswith("http://localhost:") or "tts:" in base_http:
                    data["LOCAL_TTS_HTTP_BASE_URL"] = f"http://127.0.0.1:{tts_port}"
                base_ws = data.get("LOCAL_TTS_WS_BASE_URL") or os.environ.get("LOCAL_TTS_WS_BASE_URL", "")
                if not base_ws or base_ws.startswith("ws://127.0.0.1:") or base_ws.startswith("ws://localhost:") or "tts:" in base_ws:
                    data["LOCAL_TTS_WS_BASE_URL"] = f"ws://127.0.0.1:{tts_port}"

            stt_port = data.get("STT_PORT") or os.environ.get("STT_PORT")
            if stt_port:
                stt_url = data.get("STREAMING_STT_URL") or os.environ.get("STREAMING_STT_URL", "")
                if not stt_url or stt_url.startswith("http://127.0.0.1:") or stt_url.startswith("http://localhost:") or "streaming-stt:" in stt_url:
                    data["STREAMING_STT_URL"] = f"http://127.0.0.1:{stt_port}"
                stt_ws = data.get("STREAMING_STT_WS_URL") or os.environ.get("STREAMING_STT_WS_URL", "")
                if not stt_ws or stt_ws.startswith("ws://127.0.0.1:") or stt_ws.startswith("ws://localhost:") or "streaming-stt:" in stt_ws:
                    data["STREAMING_STT_WS_URL"] = f"ws://127.0.0.1:{stt_port}/ws"
        return data
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
    ADMIN_INITIAL_PASSWORD: Optional[str] = os.environ.get("ADMIN_INITIAL_PASSWORD")

    @model_validator(mode="after")
    def resolve_admin_initial_password(self) -> "Settings":
        if self.ADMIN_INITIAL_PASSWORD is None and self.ENVIRONMENT == "development":
            self.ADMIN_INITIAL_PASSWORD = "adminVOICE3.md"
        elif self.ADMIN_INITIAL_PASSWORD == "adminVOICE3.md" and self.ENVIRONMENT in ("staging", "production"):
            self.ADMIN_INITIAL_PASSWORD = None
        return self
    GATEWAY_HOST: str = os.environ.get("GATEWAY_HOST", "127.0.0.1")
    GATEWAY_PORT: int = int(os.environ.get("GATEWAY_PORT", "8000"))
    PUBLIC_BASE_URL: Optional[str] = os.environ.get("PUBLIC_BASE_URL") or os.environ.get("BASE_URL")
    COOKIE_SECURE: Optional[Union[bool, str]] = None
    TRUSTED_PROXY_IPS: str = os.environ.get("TRUSTED_PROXY_IPS", "")
    ADMIN_API_KEY: Optional[str] = os.environ.get(
        "ADMIN_API_KEY", "dev-voice-key-12345"
    )
    API_KEY_HASH_SECRET: str = os.environ.get(
        "API_KEY_HASH_SECRET", "dev-secret-key-change-me-in-production-use-high-entropy"
    )
    CORS_ALLOWED_ORIGINS: Union[List[str], str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    @field_validator("CORS_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_cors_allowed_origins(cls, v: Any) -> List[str]:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return ["http://localhost:5173", "http://127.0.0.1:5173"]
            if v.startswith("[") and v.endswith("]"):
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return [str(origin).strip() for origin in parsed if str(origin).strip()]
                except Exception:
                    pass
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        if isinstance(v, (list, tuple)):
            return [str(origin).strip() for origin in v if str(origin).strip()]
        return ["http://localhost:5173", "http://127.0.0.1:5173"]
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

    def get_public_base_url(
        self,
        request_host: Optional[str] = None,
        request_scheme: Optional[str] = None,
    ) -> str:
        """Resolve dynamic public base URL for clients, documentation, and external proxies."""
        if self.PUBLIC_BASE_URL and self.PUBLIC_BASE_URL.strip():
            return self.PUBLIC_BASE_URL.strip().rstrip("/")
        # Auto-detect RunPod cloud pod URL from container environment
        runpod_id = os.environ.get("RUNPOD_POD_ID", "").strip()
        if runpod_id:
            return f"https://{runpod_id}-{self.GATEWAY_PORT}.proxy.runpod.net"
        if request_host and request_host.strip():
            scheme = request_scheme or ("https" if self.cookie_secure else "http")
            return f"{scheme}://{request_host.strip()}".rstrip("/")
        host = self.GATEWAY_HOST if self.GATEWAY_HOST not in ("0.0.0.0", "::") else "127.0.0.1"
        return f"http://{host}:{self.GATEWAY_PORT}"

    @property
    def cookie_secure(self) -> bool:
        if isinstance(self.COOKIE_SECURE, bool):
            return self.COOKIE_SECURE
        if isinstance(self.COOKIE_SECURE, str):
            val = self.COOKIE_SECURE.strip().lower()
            if val in ("true", "1", "yes"):
                return True
            if val in ("false", "0", "no"):
                return False
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

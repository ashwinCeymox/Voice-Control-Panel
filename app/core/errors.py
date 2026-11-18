"""Application exceptions and error formatting."""

import logging
from typing import Any, Optional
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("voice_agent.errors")


class AppError(HTTPException):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Optional[Any] = None,
    ):
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.message = message
        self.details = details


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Invalid or missing API key"):
        super().__init__(status_code=401, code="UNAUTHORIZED", message=message)


class ForbiddenError(AppError):
    def __init__(self, message: str = "Access forbidden"):
        super().__init__(status_code=403, code="FORBIDDEN", message=message)


class BadRequestError(AppError):
    def __init__(self, message: str):
        super().__init__(status_code=400, code="BAD_REQUEST", message=message)


class ProviderUnavailableError(AppError):
    def __init__(self, provider: str, details: Optional[Any] = None):
        super().__init__(
            status_code=502,
            code="PROVIDER_UNAVAILABLE",
            message=f"{provider} service is unreachable or returned a gateway error",
            details=details,
        )


class ProviderTimeoutError(AppError):
    def __init__(self, provider: str):
        super().__init__(
            status_code=504,
            code="PROVIDER_TIMEOUT",
            message=f"{provider} service request timed out",
        )


class PayloadTooLargeError(AppError):
    def __init__(self, message: str):
        super().__init__(status_code=413, code="PAYLOAD_TOO_LARGE", message=message)


class ModelNotFoundError(AppError):
    def __init__(self, model_id: str):
        super().__init__(
            status_code=404,
            code="MODEL_NOT_FOUND",
            message=f"Model '{model_id}' was not found or is not allowed",
        )


class StreamingUnavailableError(ProviderUnavailableError):
    def __init__(
        self,
        message: str = "Streaming service is not running or unreachable",
        details: Optional[Any] = None,
    ):
        super().__init__(provider="Streaming STT", details=details)
        self.status_code = 503
        self.code = "STREAMING_STT_UNAVAILABLE"
        self.message = message
        self.detail = message


class StreamingModelNotLoadedError(AppError):
    def __init__(
        self, model_id: str, details: Optional[Any] = None, status_code: int = 404
    ):
        super().__init__(
            status_code=status_code,
            code="STT_MODEL_NOT_LOADED",
            message=f"Model '{model_id}' is not loaded on the streaming service",
            details=details,
        )


class StreamingBadAudioError(AppError):
    def __init__(self, message: str = "Invalid audio format for streaming STT"):
        super().__init__(
            status_code=400,
            code="STT_BAD_AUDIO",
            message=message,
        )


class ModelInUseError(AppError):
    def __init__(self, model_id: str, agent_names: list):
        agents_str = ", ".join(f"'{a}'" for a in agent_names)
        super().__init__(
            status_code=409,
            code="MODEL_IN_USE",
            message=f"Cannot delete model '{model_id}': currently in use by agent(s): {agents_str}",
            details={"model_id": model_id, "agents": agent_names},
        )


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    if exc.details:
        logger.error(
            "Request %s encountered %s: %s (upstream details: %s)",
            request_id,
            exc.code,
            exc.message,
            exc.details,
        )
    else:
        logger.warning("Request %s: %s - %s", request_id, exc.code, exc.message)

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
            },
            "request_id": request_id,
        },
    )

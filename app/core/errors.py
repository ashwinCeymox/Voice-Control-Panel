"""Application exceptions and error formatting."""
from typing import Any, Optional
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


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


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
            "request_id": request_id,
        },
    )

"""IDIS API error handling.

Provides IdisHttpError exception and FastAPI exception handler to produce
OpenAPI-compliant Error JSON responses with request_id tracing.
"""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """OpenAPI Error schema (components.schemas.Error)."""

    code: str
    message: str
    details: dict[str, Any] | None = None
    request_id: str | None = None


class IdisHttpError(Exception):
    """Application-level HTTP error with structured error envelope.

    Attributes:
        status_code: HTTP status code (e.g., 401, 404, 500).
        code: Machine-readable error code (e.g., "unauthorized", "not_found").
        message: Human-readable error message.
        details: Optional dict with additional error context.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


async def idis_http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI exception handler for IdisHttpError.

    Produces JSON matching OpenAPI Error schema with request_id from middleware.
    """
    assert isinstance(exc, IdisHttpError)

    request_id: str | None = getattr(request.state, "request_id", None)

    error_body = ErrorResponse(
        code=exc.code,
        message=exc.message,
        details=exc.details,
        request_id=request_id,
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=error_body.model_dump(exclude_none=True),
    )

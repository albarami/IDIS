"""IDIS API error handling.

Provides IdisHttpError exception and FastAPI exception handlers to produce
OpenAPI-compliant Error JSON responses with request_id tracing.

Global exception handlers:
- IdisHttpError: Application-specific errors with structured envelope
- HTTPException: FastAPI/Starlette HTTP exceptions
- RequestValidationError: Pydantic validation errors
- Exception: Catch-all for unhandled exceptions (fail closed, no stack traces)
"""

import logging
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from idis.api.error_model import (
    get_error_code_for_status,
    make_error_response,
)

logger = logging.getLogger(__name__)


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

    return make_error_response(
        request,
        code=exc.code,
        message=exc.message,
        http_status=exc.status_code,
        details=exc.details,
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI exception handler for HTTPException.

    Maps standard HTTP exceptions to normative error envelope.
    """
    assert isinstance(exc, HTTPException)

    code = get_error_code_for_status(exc.status_code)
    message = str(exc.detail) if exc.detail else f"HTTP {exc.status_code}"

    return make_error_response(
        request,
        code=code,
        message=message,
        http_status=exc.status_code,
        details=None,
    )


async def request_validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI exception handler for RequestValidationError.

    Maps Pydantic validation errors to normative error envelope.
    Does not expose raw validation internals to avoid information leakage.
    """
    assert isinstance(exc, RequestValidationError)

    errors = exc.errors()
    safe_details: list[dict[str, Any]] = []

    for error in errors:
        loc = error.get("loc", ())
        safe_loc = [str(part) for part in loc if part not in ("body", "query", "path")]
        safe_details.append(
            {
                "field": ".".join(safe_loc) if safe_loc else "request",
                "message": error.get("msg", "Validation error"),
            }
        )

    return make_error_response(
        request,
        code="REQUEST_VALIDATION_FAILED",
        message="Request validation failed",
        http_status=422,
        details={"errors": safe_details} if safe_details else None,
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all exception handler for unhandled exceptions.

    Fails closed: returns 500 with safe generic message.
    Does NOT expose stack traces or exception details to clients.
    Logs the exception for debugging.
    """
    request_id = getattr(request.state, "request_id", None)

    logger.exception(
        "Unhandled exception: %s",
        type(exc).__name__,
        extra={"request_id": request_id},
    )

    return make_error_response(
        request,
        code="INTERNAL_ERROR",
        message="An internal error occurred",
        http_status=500,
        details=None,
    )

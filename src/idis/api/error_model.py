"""Shared error response builder for IDIS API.

Provides a unified make_error_response() function that all middlewares and
exception handlers use to produce v6.3-compliant error envelopes.

Error envelope schema (normative):
- code: str - machine-readable error code (e.g., "INVALID_JSON", "UNAUTHORIZED")
- message: str - human-readable error message
- details: dict | None - optional additional context (no sensitive data)
- request_id: str - request correlation ID (always present)
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


def _get_request_id(request: Request) -> str:
    """Extract or generate request_id for error responses.

    Priority:
    1. request.state.request_id (set by RequestIdMiddleware)
    2. X-Request-Id header (if present)
    3. Generate new UUID (fallback)

    Returns:
        Request ID string (never None).
    """
    request_id: str | None = getattr(request.state, "request_id", None)
    if request_id is not None:
        return str(request_id)

    header_id: str | None = request.headers.get("X-Request-Id")
    if header_id:
        return header_id

    return str(uuid.uuid4())


def make_error_response(
    request: Request,
    *,
    code: str,
    message: str,
    http_status: int,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    """Build a v6.3-compliant error JSON response.

    Args:
        request: The FastAPI request object (for request_id extraction).
        code: Machine-readable error code (e.g., "INVALID_JSON").
        message: Human-readable error message.
        http_status: HTTP status code (e.g., 400, 401, 500).
        details: Optional dict with additional context (no sensitive data).

    Returns:
        JSONResponse with normative error envelope and X-Request-Id header.
    """
    request_id = _get_request_id(request)

    body: dict[str, Any] = {
        "code": code,
        "message": message,
        "details": details,
        "request_id": request_id,
    }

    response = JSONResponse(status_code=http_status, content=body)
    response.headers["X-Request-Id"] = request_id

    return response


def make_error_response_no_request(
    *,
    code: str,
    message: str,
    http_status: int,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    """Build error response when Request object is not available.

    Used in contexts where we don't have access to the full Request object
    (e.g., some middleware error paths).

    Args:
        code: Machine-readable error code.
        message: Human-readable error message.
        http_status: HTTP status code.
        request_id: Request ID if known, otherwise generates new UUID.
        details: Optional dict with additional context.

    Returns:
        JSONResponse with normative error envelope and X-Request-Id header.
    """
    if request_id is None:
        request_id = str(uuid.uuid4())

    body: dict[str, Any] = {
        "code": code,
        "message": message,
        "details": details,
        "request_id": request_id,
    }

    response = JSONResponse(status_code=http_status, content=body)
    response.headers["X-Request-Id"] = request_id

    return response


HTTP_STATUS_TO_CODE: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "UNPROCESSABLE_ENTITY",
    429: "RATE_LIMIT_EXCEEDED",
    500: "INTERNAL_ERROR",
    502: "BAD_GATEWAY",
    503: "SERVICE_UNAVAILABLE",
    504: "GATEWAY_TIMEOUT",
}


def get_error_code_for_status(status_code: int) -> str:
    """Get standard error code for HTTP status code.

    Args:
        status_code: HTTP status code.

    Returns:
        Standard error code string.
    """
    return HTTP_STATUS_TO_CODE.get(status_code, "ERROR")

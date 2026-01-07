"""Health check endpoint for IDIS API."""

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["Health"])

IDIS_VERSION = "6.3"


class HealthResponse(BaseModel):
    """Health check response schema per OpenAPI spec."""

    status: str
    time: str
    version: str


@router.get("/health", response_model=HealthResponse)
def get_health(request: Request) -> HealthResponse:
    """Health check endpoint.

    Returns JSON with status, time (ISO-8601), and version.
    The X-Request-Id header is added by the request ID middleware.

    Args:
        request: The incoming request (used for request state access).

    Returns:
        HealthResponse with status "ok", current time, and version "6.3".
    """
    return HealthResponse(
        status="ok",
        time=datetime.now(UTC).isoformat(),
        version=IDIS_VERSION,
    )

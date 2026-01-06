"""IDIS FastAPI application entry point."""

from datetime import UTC, datetime

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(
    title="IDIS API",
    description="Institutional Deal Intelligence System - VC Edition",
    version="6.3",
)


class HealthResponse(BaseModel):
    """Health check response schema."""

    status: str
    time: str
    version: str


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Health check endpoint.

    Returns:
        HealthResponse with status, current time, and version.
    """
    return HealthResponse(
        status="ok",
        time=datetime.now(UTC).isoformat(),
        version="6.3",
    )

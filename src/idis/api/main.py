"""IDIS FastAPI application factory.

This module provides the create_app() factory for bootstrapping the IDIS API.
"""

from fastapi import FastAPI

from idis.api.middleware.request_id import RequestIdMiddleware
from idis.api.routes.health import router as health_router

IDIS_VERSION = "6.3"


def create_app() -> FastAPI:
    """Create and configure the IDIS FastAPI application.

    This factory:
    - Creates a FastAPI app with IDIS metadata
    - Registers the request-id middleware
    - Mounts the health router

    Returns:
        Configured FastAPI application instance.
    """
    app = FastAPI(
        title="IDIS API (VC Edition)",
        description="Institutional Deal Intelligence System - Enterprise API",
        version=IDIS_VERSION,
    )

    app.add_middleware(RequestIdMiddleware)

    app.include_router(health_router)

    return app

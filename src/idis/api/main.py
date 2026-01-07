"""IDIS FastAPI application factory.

This module provides the create_app() factory for bootstrapping the IDIS API.
"""

from fastapi import FastAPI

from idis.api.errors import IdisHttpError, idis_http_error_handler
from idis.api.middleware.request_id import RequestIdMiddleware
from idis.api.routes.health import router as health_router
from idis.api.routes.tenancy import router as tenancy_router

IDIS_VERSION = "6.3"


def create_app() -> FastAPI:
    """Create and configure the IDIS FastAPI application.

    This factory:
    - Creates a FastAPI app with IDIS metadata
    - Registers the request-id middleware
    - Registers the IdisHttpError exception handler
    - Mounts the health router (no auth required)
    - Mounts the /v1 tenancy router (auth required)

    Returns:
        Configured FastAPI application instance.
    """
    app = FastAPI(
        title="IDIS API (VC Edition)",
        description="Institutional Deal Intelligence System - Enterprise API",
        version=IDIS_VERSION,
    )

    app.add_middleware(RequestIdMiddleware)

    app.add_exception_handler(IdisHttpError, idis_http_error_handler)

    app.include_router(health_router)
    app.include_router(tenancy_router)

    return app

"""Database transaction middleware for IDIS API.

Provides request-scoped database connections with automatic transaction management.
Applies only to /v1 requests when PostgreSQL is configured.

Design Requirements (v6.3):
    - Opens app connection at request start
    - Begins transaction automatically
    - Stores connection on request.state.db_conn
    - Commits on 2xx-4xx responses, rolls back on 5xx
    - Always closes connection (never leaks)
    - Fail closed: DB errors don't propagate as unhandled exceptions
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from idis.api.error_model import make_error_response_no_request

logger = logging.getLogger(__name__)


class DBTransactionMiddleware(BaseHTTPMiddleware):
    """Middleware for request-scoped database transactions.

    Behavior:
    - Only applies to /v1 paths when PostgreSQL is configured
    - Opens connection from app engine at request start
    - Stores connection on request.state.db_conn
    - Commits transaction if response status < 500
    - Rolls back transaction if response status >= 500
    - Always closes connection in finally block

    Ordering:
    - Must run after RequestIdMiddleware (needs request_id for error responses)
    - Must run before AuditMiddleware (audit needs db_conn for in-tx emission)
    - Must run before OpenAPIValidationMiddleware (needs db_conn for tenant SET LOCAL)
    """

    def __init__(self, app: ASGIApp) -> None:
        """Initialize the DB transaction middleware.

        Args:
            app: The ASGI application
        """
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Process request with database transaction management."""
        path = request.url.path
        request_id: str | None = getattr(request.state, "request_id", None)

        if not path.startswith("/v1"):
            return await call_next(request)

        from idis.persistence.db import is_postgres_configured

        if not is_postgres_configured():
            return await call_next(request)

        from idis.persistence.db import get_app_engine

        engine = None
        conn = None

        try:
            engine = get_app_engine()
            conn = engine.connect()
            trans = conn.begin()
            request.state.db_conn = conn
            request.state.db_trans = trans

            logger.debug("Opened DB connection for request %s", request_id)

        except Exception as e:
            logger.error("Failed to open DB connection: %s", e, extra={"request_id": request_id})
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()
            return make_error_response_no_request(
                code="DATABASE_UNAVAILABLE",
                message="Database connection failed",
                http_status=503,
                request_id=request_id,
                details=None,
            )

        try:
            response = await call_next(request)

            if response.status_code < 500:
                try:
                    trans.commit()
                    logger.debug("Committed DB transaction for request %s", request_id)
                except Exception as e:
                    logger.error(
                        "Failed to commit transaction: %s", e, extra={"request_id": request_id}
                    )
                    with contextlib.suppress(Exception):
                        trans.rollback()
                    return make_error_response_no_request(
                        code="DATABASE_COMMIT_FAILED",
                        message="Database transaction commit failed",
                        http_status=500,
                        request_id=request_id,
                        details=None,
                    )
            else:
                try:
                    trans.rollback()
                    logger.debug(
                        "Rolled back DB transaction for request %s (status=%d)",
                        request_id,
                        response.status_code,
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to rollback transaction: %s", e, extra={"request_id": request_id}
                    )

            return response

        except Exception as e:
            logger.error(
                "Unexpected error in DB transaction middleware: %s",
                e,
                extra={"request_id": request_id},
            )
            with contextlib.suppress(Exception):
                trans.rollback()
            raise

        finally:
            try:
                conn.close()
                logger.debug("Closed DB connection for request %s", request_id)
            except Exception as e:
                logger.warning(
                    "Failed to close DB connection: %s", e, extra={"request_id": request_id}
                )

            request.state.db_conn = None
            request.state.db_trans = None

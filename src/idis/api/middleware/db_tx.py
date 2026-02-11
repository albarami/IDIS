"""Database transaction middleware for IDIS API.

Provides request-scoped database connections with automatic transaction management.
Applies only to /v1 requests when PostgreSQL is configured.

Implemented as a pure ASGI middleware (not BaseHTTPMiddleware) to avoid
event-loop deadlocks when calling sync psycopg2 from async context.
Sync DB operations run via asyncio.to_thread().

Design Requirements (v6.3):
    - Opens app connection at request start
    - Begins transaction automatically
    - Stores connection on request.state.db_conn
    - Commits on 2xx-4xx responses, rolls back on 5xx
    - Always closes connection (never leaks)
    - Fail closed: DB errors don't propagate as unhandled exceptions
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from idis.api.error_model import make_error_response_no_request

logger = logging.getLogger(__name__)


def _open_connection() -> tuple[Any, Any]:
    """Open a DB connection and begin a transaction (sync, runs in thread).

    Returns:
        Tuple of (connection, transaction).

    Raises:
        Exception: Any DB connection or transaction error.
    """
    from idis.persistence.db import get_app_engine

    engine = get_app_engine()
    conn = engine.connect()
    trans = conn.begin()
    return conn, trans


def _commit(trans: Any) -> None:
    """Commit a transaction (sync, runs in thread)."""
    trans.commit()


def _rollback(trans: Any) -> None:
    """Rollback a transaction (sync, runs in thread)."""
    trans.rollback()


def _close(conn: Any) -> None:
    """Close a connection (sync, runs in thread)."""
    conn.close()


class DBTransactionMiddleware:
    """Pure ASGI middleware for request-scoped database transactions.

    Behavior:
    - Only applies to /v1 paths when PostgreSQL is configured
    - Opens connection from app engine at request start
    - Stores connection on request.state.db_conn
    - Commits transaction if response status < 500
    - Rolls back transaction if response status >= 500
    - Always closes connection (never leaks)

    Ordering:
    - Must run after RequestIdMiddleware (needs request_id for error responses)
    - Must run before AuditMiddleware (audit needs db_conn for in-tx emission)
    - Must run before OpenAPIValidationMiddleware (needs db_conn for tenant SET LOCAL)
    """

    def __init__(self, app: ASGIApp) -> None:
        """Initialize the DB transaction middleware.

        Args:
            app: The ASGI application.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI entry point."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        path = request.url.path

        if not path.startswith("/v1"):
            await self.app(scope, receive, send)
            return

        from idis.persistence.db import is_postgres_configured

        if not is_postgres_configured():
            await self.app(scope, receive, send)
            return

        request_id: str | None = getattr(request.state, "request_id", None)
        conn = None
        trans = None

        try:
            conn, trans = await asyncio.to_thread(_open_connection)
            request.state.db_conn = conn
            request.state.db_trans = trans
            logger.debug("Opened DB connection for request %s", request_id)
        except Exception as e:
            logger.error("Failed to open DB connection: %s", e, extra={"request_id": request_id})
            if conn is not None:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(_close, conn)
            error_response = make_error_response_no_request(
                code="DATABASE_UNAVAILABLE",
                message="Database connection failed",
                http_status=503,
                request_id=request_id,
                details=None,
            )
            await error_response(scope, receive, send)
            return

        response_status: int | None = None

        async def send_wrapper(message: Any) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message.get("status", 500)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)

            if response_status is not None and response_status < 500:
                try:
                    await asyncio.to_thread(_commit, trans)
                    logger.debug("Committed DB transaction for request %s", request_id)
                except Exception as e:
                    logger.error(
                        "Failed to commit transaction: %s",
                        e,
                        extra={"request_id": request_id},
                    )
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(_rollback, trans)
            else:
                try:
                    await asyncio.to_thread(_rollback, trans)
                    logger.debug(
                        "Rolled back DB transaction for request %s (status=%s)",
                        request_id,
                        response_status,
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to rollback transaction: %s",
                        e,
                        extra={"request_id": request_id},
                    )

        except Exception as e:
            logger.error(
                "Unexpected error in DB transaction middleware: %s",
                e,
                extra={"request_id": request_id},
            )
            with contextlib.suppress(Exception):
                await asyncio.to_thread(_rollback, trans)
            raise

        finally:
            try:
                await asyncio.to_thread(_close, conn)
                logger.debug("Closed DB connection for request %s", request_id)
            except Exception as e:
                logger.warning(
                    "Failed to close DB connection: %s",
                    e,
                    extra={"request_id": request_id},
                )

            request.state.db_conn = None
            request.state.db_trans = None

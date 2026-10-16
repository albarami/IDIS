"""Outermost HTTP guard for the route-restricted Task-4 bootstrap API."""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from idis.runtime.task4_ingestion_scope import task4_server_call_allowed


class Task4IngestionScopeMiddleware:
    """Make every non-Task-4 route absent before auth or contract processing."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope.get("type") == "http":
            method = scope.get("method")
            path = scope.get("path")
            raw_path = scope.get("raw_path")
            try:
                canonical_raw_path = path.encode("ascii") if isinstance(path, str) else None
            except UnicodeEncodeError:
                canonical_raw_path = None
            if (
                not isinstance(method, str)
                or not isinstance(path, str)
                or not isinstance(raw_path, bytes)
                or raw_path != canonical_raw_path
                or not task4_server_call_allowed(method=method, path=path)
            ):
                response = JSONResponse(
                    status_code=404,
                    content={"code": "NOT_FOUND", "message": "Not found"},
                )
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)


__all__ = ["Task4IngestionScopeMiddleware"]

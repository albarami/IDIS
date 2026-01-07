"""Idempotency middleware for IDIS API.

Implements tenant-scoped idempotency for safe API retries on mutating endpoints.
Returns stored response for replay, 409 for payload collision, 500 on store failure.

Design requirements (v6.3 API Contracts §4.1):
- Apply to POST/PATCH on /v1 paths when Idempotency-Key header is present
- Scope by tenant_id + actor_id + method + operation_id + idempotency_key
- Replay: same key + same payload → return stored 2xx response
- Collision: same key + different payload → 409 IDEMPOTENCY_KEY_CONFLICT
- Fail closed: store unavailable + header present → 500 IDEMPOTENCY_STORE_FAILED
- Only store 2xx responses
- Add X-IDIS-Idempotency-Replay: true header on replayed responses
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from idis.idempotency.store import (
    IdempotencyRecord,
    IdempotencyStoreError,
    ScopeKey,
    SqliteIdempotencyStore,
    get_current_timestamp,
)

logger = logging.getLogger(__name__)

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
IDEMPOTENCY_REPLAY_HEADER = "X-IDIS-Idempotency-Replay"
IDEMPOTENT_METHODS = {"POST", "PATCH"}


def _build_error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: str | None,
) -> JSONResponse:
    """Build a structured error JSON response."""
    body: dict[str, Any] = {"code": code, "message": message}
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(status_code=status_code, content=body)


def _compute_payload_sha256(body_bytes: bytes) -> str:
    """Compute SHA-256 digest of request body.

    Args:
        body_bytes: Raw request body bytes

    Returns:
        Digest string in format "sha256:<hex>"
    """
    if not body_bytes:
        return "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    return f"sha256:{hashlib.sha256(body_bytes).hexdigest()}"


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Middleware for idempotent request handling on /v1 mutating endpoints.

    Behavior:
    - Only applies when Idempotency-Key header is present
    - Only applies to POST/PATCH methods on /v1 paths
    - Requires tenant_context and openapi_operation_id on request.state
    - Replays stored response if key matches and payload hash matches
    - Returns 409 if key matches but payload hash differs
    - Returns 500 if store is unavailable (fail closed)
    - Only stores 2xx responses

    Ordering:
    - Must run after OpenAPIValidationMiddleware (needs tenant_context, operation_id, body_sha256)
    - Must run before route handlers
    """

    def __init__(self, app: ASGIApp, store: SqliteIdempotencyStore | None = None) -> None:
        """Initialize the idempotency middleware.

        Args:
            app: The ASGI application
            store: Optional idempotency store. If None, creates default store.
        """
        super().__init__(app)
        self._store = store

    def _get_store(self) -> SqliteIdempotencyStore:
        """Get or lazily create the idempotency store.

        Returns:
            Idempotency store instance.

        Raises:
            IdempotencyStoreError: If store cannot be created.
        """
        if self._store is None:
            self._store = SqliteIdempotencyStore()
        return self._store

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Process request with idempotency handling."""
        path = request.url.path
        method = request.method
        request_id: str | None = getattr(request.state, "request_id", None)

        if not path.startswith("/v1"):
            return await call_next(request)

        if method not in IDEMPOTENT_METHODS:
            return await call_next(request)

        idempotency_key = request.headers.get(IDEMPOTENCY_KEY_HEADER)
        if not idempotency_key:
            return await call_next(request)

        tenant_ctx = getattr(request.state, "tenant_context", None)
        if tenant_ctx is None:
            return await call_next(request)

        operation_id = getattr(request.state, "openapi_operation_id", None)
        if operation_id is None:
            return await call_next(request)

        actor_id = tenant_ctx.actor_id

        payload_sha256 = getattr(request.state, "request_body_sha256", None)
        if payload_sha256 is None:
            try:
                body_bytes = await request.body()
                payload_sha256 = _compute_payload_sha256(body_bytes)
            except Exception:
                payload_sha256 = _compute_payload_sha256(b"")

        scope_key = ScopeKey(
            tenant_id=tenant_ctx.tenant_id,
            actor_id=actor_id,
            method=method,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
        )

        try:
            store = self._get_store()
        except IdempotencyStoreError as e:
            logger.error(
                "Idempotency store initialization failed: %s",
                str(e),
                extra={"request_id": request_id},
            )
            return _build_error_response(
                500,
                "IDEMPOTENCY_STORE_FAILED",
                "Idempotency store is unavailable",
                request_id,
            )

        try:
            existing_record = store.get(scope_key)
        except IdempotencyStoreError as e:
            logger.error(
                "Idempotency store lookup failed: %s",
                str(e),
                extra={"request_id": request_id},
            )
            return _build_error_response(
                500,
                "IDEMPOTENCY_STORE_FAILED",
                "Idempotency store is unavailable",
                request_id,
            )

        if existing_record is not None:
            if existing_record.payload_sha256 == payload_sha256:
                response = Response(
                    content=existing_record.body_bytes,
                    status_code=existing_record.status_code,
                    media_type=existing_record.media_type,
                )
                response.headers[IDEMPOTENCY_REPLAY_HEADER] = "true"
                return response
            else:
                return _build_error_response(
                    409,
                    "IDEMPOTENCY_KEY_CONFLICT",
                    "Idempotency key already used with different payload",
                    request_id,
                )

        response = await call_next(request)

        if 200 <= response.status_code < 300:
            try:
                body_iterator = getattr(response, "body_iterator", None)
                if body_iterator is None:
                    return response

                body_bytes = b""
                async for chunk in body_iterator:
                    if isinstance(chunk, bytes):
                        body_bytes += chunk
                    else:
                        body_bytes += chunk.encode("utf-8")

                media_type = response.media_type or "application/json"

                record = IdempotencyRecord(
                    payload_sha256=payload_sha256,
                    status_code=response.status_code,
                    media_type=media_type,
                    body_bytes=body_bytes,
                    created_at=get_current_timestamp(),
                )

                try:
                    store.put(scope_key, record)
                except IdempotencyStoreError as e:
                    logger.error(
                        "Failed to store idempotency record: %s",
                        str(e),
                        extra={"request_id": request_id},
                    )
                    return _build_error_response(
                        500,
                        "IDEMPOTENCY_STORE_FAILED",
                        "Idempotency store is unavailable",
                        request_id,
                    )

                return Response(
                    content=body_bytes,
                    status_code=response.status_code,
                    media_type=media_type,
                    headers=dict(response.headers),
                )

            except Exception as e:
                logger.warning(
                    "Failed to read response body for idempotency storage: %s",
                    str(e),
                    extra={"request_id": request_id},
                )
                return response

        return response

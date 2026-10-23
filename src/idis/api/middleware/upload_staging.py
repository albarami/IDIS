"""Bounded disk staging for the authenticated raw document-upload endpoint."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from idis.services.ingestion.service import DEFAULT_MAX_BYTES
from idis.services.ingestion.upload_staging import (
    StagedUpload,
    UploadIntegrityError,
    UploadSizeExceededError,
    UploadStagingError,
    stage_request_stream,
)

logger = logging.getLogger(__name__)

_UPLOAD_PATH = re.compile(r"^/v1/deals/[0-9a-fA-F-]+/documents/upload$")


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message},
    )


def _configured_max_bytes(request: Request) -> int:
    service = getattr(request.app.state, "ingestion_service", None)
    value = getattr(service, "_max_bytes", DEFAULT_MAX_BYTES)
    try:
        max_bytes = int(value)
    except (TypeError, ValueError):
        return 0
    return max_bytes


def _declared_size(request: Request) -> int | None:
    raw_value = request.headers.get("content-length")
    if raw_value is None:
        return None
    try:
        value = int(raw_value)
    except ValueError:
        raise UploadIntegrityError(
            "Upload has an invalid Content-Length",
            code="DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE",
        ) from None
    if value < 0:
        raise UploadIntegrityError(
            "Upload has an invalid Content-Length",
            code="DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE",
        )
    return value


def _cleanup_failure_response(staged: StagedUpload) -> JSONResponse | None:
    """Clean one private spool, returning only a safe terminal response on failure."""
    try:
        staged.cleanup()
    except Exception as error:
        # Cleanup is a confidentiality boundary. Never expose the source path or the
        # filesystem exception, and never allow a downstream success to survive it.
        logger.error("Upload staging cleanup failed: %s", type(error).__name__)
        return _error_response(
            500,
            "INGEST_STAGING_CLEANUP_FAILED",
            "Document upload cleanup failed",
        )
    return None


class UploadStagingMiddleware(BaseHTTPMiddleware):
    """Stage raw upload bodies once so idempotency and ingestion share one digest/path."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method != "POST" or _UPLOAD_PATH.fullmatch(request.url.path) is None:
            return await call_next(request)

        max_bytes = _configured_max_bytes(request)
        if max_bytes <= 0:
            return _error_response(
                500,
                "INGEST_ADMISSION_CONFIG_INVALID",
                "Document upload admission is unavailable",
            )

        staged = None
        response: Response
        try:
            staged = await stage_request_stream(
                request.stream(),
                max_bytes=max_bytes,
                expected_sha256=request.query_params.get("sha256"),
                expected_size_bytes=_declared_size(request),
            )
            if staged.size_bytes == 0:
                response = _error_response(400, "EMPTY_FILE", "Upload body must not be empty")
            else:
                request.state.staged_upload = staged
                request.state.request_body_sha256 = f"sha256:{staged.sha256}"
                response = await call_next(request)
        except UploadSizeExceededError:
            response = _error_response(
                413,
                "INGEST_ADMISSION_MAX_SIZE_EXCEEDED",
                "Upload exceeds the configured ingestion size limit",
            )
        except UploadIntegrityError as error:
            response = _error_response(400, error.code, "Upload integrity validation failed")
        except UploadStagingError as error:
            logger.warning("Upload staging failed: %s", type(error).__name__)
            response = _error_response(400, error.code, "Document upload staging failed")
        except BaseException:
            if staged is not None:
                _cleanup_failure_response(staged)
            raise

        if staged is not None:
            cleanup_failure = _cleanup_failure_response(staged)
            if cleanup_failure is not None:
                return cleanup_failure
        return response


__all__ = ["UploadStagingMiddleware"]

"""Idempotency middleware for IDIS API.

Implements tenant-scoped idempotency for safe API retries on mutating endpoints.
Returns stored response for replay, 409 for payload collision, 500 on store failure.

Design requirements (v6.3 API Contracts §4.1):
- Apply to POST/PATCH on /v1 paths when Idempotency-Key header is present
- Scope by tenant_id + actor_id + method + operation_id + idempotency_key
- Replay: same key + same payload → return stored 2xx response
- Collision: same key + different payload → 409 IDEMPOTENCY_KEY_CONFLICT
- Fail closed: store unavailable + header present → 500 IDEMPOTENCY_STORE_FAILED
- Only store 2xx responses and explicit side-effecting lifecycle 409s
- Add X-IDIS-Idempotency-Replay: true header on replayed responses
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from idis.api.error_model import make_error_response_no_request
from idis.audit.sink import AuditSink
from idis.idempotency.store import (
    IdempotencyKeyConflictError,
    IdempotencyRecord,
    IdempotencyStoreError,
    ScopeKey,
    SqliteIdempotencyStore,
    get_current_timestamp,
    load_idempotency_ttl_days,
)
from idis.observability.runtime_signals import IDEMPOTENCY_CLEANUP, emit_run_signal

try:
    from idis.idempotency.postgres_store import PostgresIdempotencyStore
except ImportError:
    PostgresIdempotencyStore = None  # type: ignore[misc,assignment]

IdempotencyStore = SqliteIdempotencyStore | PostgresIdempotencyStore | None

logger = logging.getLogger(__name__)

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
IDEMPOTENCY_REPLAY_HEADER = "X-IDIS-Idempotency-Replay"
IDEMPOTENT_METHODS = {"POST", "PATCH"}
_UPLOAD_OPERATION_ID = "uploadDealDocument"
_CONFIRMED_NO_SUBSTANTIVE_CONTENT_DISPOSITION = "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"
_CONFIRMED_NO_SUBSTANTIVE_CONTENT_RECEIPT_KEY = "document_no_substantive_content_receipt"
_SUPERSEDED_TERMINAL_RECEIPT_KEYS = frozenset({"document_illegibility_receipt"})


def _build_error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: str | None,
) -> JSONResponse:
    """Build a structured error JSON response using shared error model."""
    return make_error_response_no_request(
        code=code,
        message=message,
        http_status=status_code,
        request_id=request_id,
        details=None,
    )


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


def _canonical_query_string(request: Request) -> str:
    """Return query params in a deterministic representation for idempotency."""
    query_pairs = sorted(
        (str(key), str(value)) for key, value in request.query_params.multi_items()
    )
    return urlencode(query_pairs)


def _compute_request_fingerprint(payload_sha256: str, canonical_query: str) -> str:
    """Combine body and query metadata into the idempotency payload fingerprint."""
    if not canonical_query:
        return payload_sha256
    fingerprint_input = f"body={payload_sha256}\nquery={canonical_query}".encode()
    return f"sha256:{hashlib.sha256(fingerprint_input).hexdigest()}"


def _cached_confirmed_no_substantive_content_body(
    record: IdempotencyRecord,
) -> dict[str, object] | None:
    """Route governed terminal/page receipts to durable replay validation."""

    if record.status_code != 201:
        return None
    if record.media_type.split(";", maxsplit=1)[0].strip().lower() != "application/json":
        raise ValueError("cached upload 201 is not JSON")
    try:
        decoded = json.loads(record.body_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("cached upload 201 is malformed") from None
    if not isinstance(decoded, dict):
        raise ValueError("cached upload 201 is not an object")
    terminal_disposition = decoded.get("terminal_disposition")
    metadata = decoded.get("metadata")
    metadata_keys = set(metadata) if isinstance(metadata, dict) else set()
    if metadata_keys & _SUPERSEDED_TERMINAL_RECEIPT_KEYS:
        raise ValueError("superseded terminal receipt")
    if terminal_disposition is None:
        if _CONFIRMED_NO_SUBSTANTIVE_CONTENT_RECEIPT_KEY in metadata_keys:
            receipt = (
                metadata.get(_CONFIRMED_NO_SUBSTANTIVE_CONTENT_RECEIPT_KEY)
                if isinstance(metadata, Mapping)
                else None
            )
            if (
                not isinstance(receipt, Mapping)
                or receipt.get("schema_version") != "document-no-substantive-content-receipt-v2"
                or receipt.get("whole_document_terminal") is not False
            ):
                raise ValueError("terminal receipt without terminal disposition")
            if decoded.get("parse_status") != "PARSED":
                raise ValueError("cached upload 201 is not an admitted parsed response")
            return decoded
        if decoded.get("parse_status") != "PARSED":
            raise ValueError("cached upload 201 is not an admitted parsed response")
        return None
    if terminal_disposition != _CONFIRMED_NO_SUBSTANTIVE_CONTENT_DISPOSITION:
        raise ValueError("unknown terminal disposition")
    return decoded


def _validate_confirmed_no_substantive_content_replay(
    *,
    request: Request,
    record: IdempotencyRecord,
    body: Mapping[str, object],
    tenant_id: str,
    db_conn: object,
) -> None:
    """Re-read the tenant/RLS corpus and prove a terminal upload remains admissible."""

    from idis.api.document_upload_replay import validate_upload_replay_invariant
    from idis.persistence.repositories.documents import PostgresDocumentsRepository
    from idis.services.ingestion.no_substantive_content_attestation import (
        VerifiedDocumentNoSubstantiveContentAttestation,
        VerifiedDocumentNoSubstantiveContentAttestationManifest,
    )

    document_id = body.get("document_id")
    if not isinstance(document_id, str):
        raise RuntimeError("terminal replay document binding is missing")

    repository = PostgresDocumentsRepository(db_conn, tenant_id)  # type: ignore[arg-type]
    durable_document = repository.get_document(document_id)
    if not isinstance(durable_document, dict):
        raise RuntimeError("terminal replay durable document is missing")

    deal_id = durable_document.get("deal_id")
    if not isinstance(deal_id, str):
        raise RuntimeError("terminal replay deal binding is missing")
    spans = repository.list_spans_by_document(deal_id=deal_id, document_id=document_id)
    current_document: dict[str, object] = {
        **durable_document,
        "span_count": len(spans),
        "spans": spans,
    }

    source_metadata = durable_document.get("source_metadata")
    if not isinstance(source_metadata, Mapping):
        raise RuntimeError("terminal replay source binding is missing")
    source_tree_fingerprint = source_metadata.get("source_tree_fingerprint")
    upload_filename = source_metadata.get("upload_filename")
    safe_alias = durable_document.get("document_name")
    content_sha256 = durable_document.get("sha256")
    if (
        not isinstance(source_tree_fingerprint, str)
        or not isinstance(upload_filename, str)
        or not isinstance(safe_alias, str)
        or upload_filename != safe_alias
        or not isinstance(content_sha256, str)
    ):
        raise RuntimeError("terminal replay source binding is invalid")

    app_state = getattr(getattr(request.scope.get("app"), "state", None), "ingestion_service", None)
    attestation = getattr(app_state, "_no_substantive_content_attestation", None)
    if not isinstance(
        attestation,
        (
            VerifiedDocumentNoSubstantiveContentAttestation,
            VerifiedDocumentNoSubstantiveContentAttestationManifest,
        ),
    ):
        raise RuntimeError("terminal replay current attestation is unavailable")
    validate_upload_replay_invariant(
        cached_response={
            "status_code": record.status_code,
            "media_type": record.media_type,
            "body": body,
        },
        durable_document=current_document,
        attestation=attestation,
        tenant_id=tenant_id,
        source_tree_fingerprint=source_tree_fingerprint,
        safe_alias=safe_alias,
        content_sha256=content_sha256,
        now=datetime.now(UTC),
    )


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Middleware for idempotent request handling on /v1 mutating endpoints.

    Behavior:
    - Only applies when Idempotency-Key header is present
    - Only applies to POST/PATCH methods on /v1 paths
    - Requires tenant_context and openapi_operation_id on request.state
    - Replays stored response if key matches and payload hash matches
    - Returns 409 if key matches but payload hash differs
    - Returns 500 if store is unavailable (fail closed)
    - Only stores 2xx responses and explicit side-effecting lifecycle 409s
    - Uses Postgres store when db_conn is available, else SQLite

    Ordering:
    - Must run after OpenAPIValidationMiddleware (needs tenant_context, operation_id, body_sha256)
    - Must run before route handlers
    """

    def __init__(
        self,
        app: ASGIApp,
        store: SqliteIdempotencyStore | None = None,
        postgres_store: PostgresIdempotencyStore | None = None,
        *,
        ttl_days: int | None = None,
        cleanup_interval_seconds: float = 3600.0,
    ) -> None:
        """Initialize the idempotency middleware.

        Args:
            app: The ASGI application
            store: Optional SQLite idempotency store. If None, creates default store.
            postgres_store: Optional Postgres idempotency store for in-transaction ops.
            ttl_days: Idempotency-record TTL in days for opportunistic cleanup. Defaults to the
                configured value (``IDIS_IDEMPOTENCY_TTL_DAYS``, ~30).
            cleanup_interval_seconds: Minimum seconds between opportunistic cleanups per tenant, so
                cleanup does not run on every request (throttle). 0 disables throttling.
        """
        super().__init__(app)
        self._store = store
        self._postgres_store = postgres_store
        self._ttl_days = ttl_days if ttl_days is not None else load_idempotency_ttl_days()
        self._cleanup_interval_seconds = cleanup_interval_seconds
        self._last_cleanup: dict[str, float] = {}
        self._cleanup_lock = threading.Lock()

    def _get_store(self) -> SqliteIdempotencyStore:
        """Get or lazily create the SQLite idempotency store.

        Returns:
            Idempotency store instance.

        Raises:
            IdempotencyStoreError: If store cannot be created.
        """
        if self._store is None:
            self._store = SqliteIdempotencyStore()
        return self._store

    def _get_postgres_store(self) -> PostgresIdempotencyStore | None:
        """Get or lazily create the Postgres idempotency store.

        Returns:
            PostgresIdempotencyStore instance or None if not available.
        """
        if PostgresIdempotencyStore is None:
            return None
        if self._postgres_store is None:
            self._postgres_store = PostgresIdempotencyStore()
        return self._postgres_store

    def _maybe_cleanup(
        self,
        tenant_id: str,
        store: SqliteIdempotencyStore | PostgresIdempotencyStore,
        audit_sink: AuditSink | None = None,
    ) -> None:
        """Opportunistically reclaim this tenant's expired idempotency records (throttled).

        Best-effort and tenant-scoped: runs at most once per tenant per
        ``cleanup_interval_seconds`` and removes only records older than the configured TTL. Any
        failure is swallowed -- cleanup must never affect the request or its replay/conflict
        semantics.
        """
        now = time.monotonic()
        with self._cleanup_lock:
            last = self._last_cleanup.get(tenant_id)
            if last is not None and (now - last) < self._cleanup_interval_seconds:
                return
            self._last_cleanup[tenant_id] = now
        try:
            cutoff = datetime.now(UTC) - timedelta(days=self._ttl_days)
            deleted = store.delete_expired(tenant_id=tenant_id, older_than=cutoff)
            if deleted > 0:  # only signal a real outcome (records reclaimed), not routine no-ops
                emit_run_signal(
                    audit_sink,
                    event_type=IDEMPOTENCY_CLEANUP,
                    tenant_id=tenant_id,
                    details={"deleted_count": int(deleted)},
                )
        except Exception as exc:  # best-effort: never break the request
            logger.warning("Idempotency TTL cleanup failed: %s", str(exc))

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
        payload_sha256 = _compute_request_fingerprint(
            payload_sha256,
            _canonical_query_string(request),
        )

        scope_key = ScopeKey(
            tenant_id=tenant_ctx.tenant_id,
            actor_id=actor_id,
            method=method,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
        )

        db_conn = getattr(request.state, "db_conn", None)
        use_postgres = db_conn is not None and PostgresIdempotencyStore is not None

        if use_postgres:
            postgres_store = self._get_postgres_store()
            if postgres_store is None:
                use_postgres = False

        if not use_postgres:
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

        # Opportunistic, throttled, best-effort TTL cleanup for this tenant (DEC-E). Runs before the
        # lookup and never affects replay/conflict: it removes only OTHER already-expired records.
        # Best-effort observability sink lookup. Real requests always carry scope["app"], but a
        # hand-built scope (e.g. driving dispatch directly) may not -- use scope.get so a missing
        # "app" degrades to no sink instead of raising KeyError and breaking the request.
        app_obj = request.scope.get("app")
        cleanup_audit_sink = getattr(getattr(app_obj, "state", None), "audit_sink", None)
        task4_ingestion_scope = (
            getattr(getattr(app_obj, "state", None), "task4_ingestion_scope", False) is True
        )
        if use_postgres and postgres_store is not None:
            self._maybe_cleanup(scope_key.tenant_id, postgres_store, cleanup_audit_sink)
        else:
            self._maybe_cleanup(scope_key.tenant_id, store, cleanup_audit_sink)

        try:
            if use_postgres and postgres_store is not None:
                existing_record = postgres_store.get(scope_key, conn=db_conn)
            else:
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
                try:
                    confirmed_no_substantive_content_body = (
                        _cached_confirmed_no_substantive_content_body(existing_record)
                        if operation_id == _UPLOAD_OPERATION_ID and method == "POST"
                        else None
                    )
                except ValueError:
                    logger.error(
                        "Durable document-upload replay used an invalid terminal contract",
                        extra={"request_id": request_id},
                    )
                    return _build_error_response(
                        500,
                        "IDEMPOTENCY_REPLAY_INVARIANT_FAILED",
                        "Stored document-upload response failed its durable invariant",
                        request_id,
                    )
                if confirmed_no_substantive_content_body is not None:
                    try:
                        if not use_postgres or postgres_store is None or db_conn is None:
                            raise RuntimeError("terminal replay requires durable admission")
                        _validate_confirmed_no_substantive_content_replay(
                            request=request,
                            record=existing_record,
                            body=confirmed_no_substantive_content_body,
                            tenant_id=scope_key.tenant_id,
                            db_conn=db_conn,
                        )
                    except Exception:
                        # Do not log exception text: driver and trust-proof failures can
                        # contain the raw idempotency key or source bindings.
                        logger.error(
                            "Durable document-upload replay invariant proof failed",
                            extra={"request_id": request_id},
                        )
                        return _build_error_response(
                            500,
                            "IDEMPOTENCY_REPLAY_INVARIANT_FAILED",
                            "Stored document-upload response failed its durable invariant",
                            request_id,
                        )
                if (
                    task4_ingestion_scope
                    and use_postgres
                    and operation_id == "createDeal"
                    and method == "POST"
                ):
                    try:
                        from idis.api import deal_creator_replay

                        outcome = deal_creator_replay.repair_create_deal_replay(
                            db_conn,
                            scope_key=scope_key,
                            record=existing_record,
                        )
                    except Exception:
                        # Never log the exception: a DB driver may include the raw
                        # idempotency-key bind in its diagnostic representation.
                        logger.error(
                            "Durable createDeal replay invariant proof failed",
                            extra={"request_id": request_id},
                        )
                        return _build_error_response(
                            500,
                            "IDEMPOTENCY_REPLAY_INVARIANT_FAILED",
                            "Stored createDeal response failed its durable invariant",
                            request_id,
                        )
                    if outcome.repaired:
                        # AuditMiddleware is outside this middleware and shares the
                        # same DBTransactionMiddleware connection.  These request-local
                        # markers cause one truthful assignment event before commit.
                        request.state.audit_resource_id = outcome.deal_id
                        request.state.audit_event_type_override = "rbac.assignment.created"
                        request.state.audit_severity_override = "MEDIUM"
                        request.state.audit_idempotency_replay_repair_occurred = True
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

        if _should_store_response(request, response):
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
                    if use_postgres and postgres_store is not None:
                        postgres_store.put(scope_key, record, conn=db_conn)
                    else:
                        store.put(scope_key, record)
                except IdempotencyKeyConflictError:
                    return _build_error_response(
                        409,
                        "IDEMPOTENCY_KEY_CONFLICT",
                        "Idempotency key already used with different payload",
                        request_id,
                    )
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

            except Exception:
                # The route may already have staged database mutations.  Returning its
                # success response here would let DBTransactionMiddleware commit without
                # a replay record, so every unexpected capture/store failure is terminal.
                logger.error(
                    "Unexpected post-route idempotency persistence failure",
                    extra={"request_id": request_id},
                )
                return _build_error_response(
                    500,
                    "IDEMPOTENCY_STORE_FAILED",
                    "Idempotency store is unavailable",
                    request_id,
                )

        return response


def _should_store_response(request: Request, response: Response) -> bool:
    """Return whether this response is safe and useful to replay."""
    if 200 <= response.status_code < 300:
        return True
    return (
        response.status_code == 409
        and getattr(request.state, "audit_mutation_occurred_on_error", False) is True
    )

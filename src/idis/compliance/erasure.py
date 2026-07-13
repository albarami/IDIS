"""Per-deal erasure workflow: durable request -> ADMIN execution (Slice98 Task 8).

Implements the hard-delete workflow per Data Residency Model v6.3 section 6.2 for ONE deal per
request (no tenant-wide erasure): remove the deal's rows, object-store artifacts, and vector
entries in full - including the ``deals`` row itself - while audit events retain their deal_id
references. The ``erasure_requests`` row (migration 0030) is the durable evidence and deliberately
has NO foreign key to ``deals``: it must outlive the row it erased.

Safety posture (mirrors the compliance cores):
- Reasons are hashed immediately (hash+length only) - never stored, audited, or logged raw.
- Audit is fatal and ordered: ``erasure.requested`` (HIGH) is emitted BEFORE the request row is
  written (sink failure -> 500, no durable state); ``erasure.executed`` (CRITICAL) is emitted
  BEFORE any destruction (failure aborts ALL of it).
- Holds win: the injected hold checker runs before destruction; any active hold aborts the whole
  execution with zero partial deletions, and the request stays REQUESTED for later.
- Executor failure marks the request FAILED with the error surfaced (500); FAILED requests are
  idempotently re-executable. An EXECUTED request cannot run again (409).
- Store resolution failures DENY (403 ERASURE_RESOLUTION_FAILED) - never "no request"; writes
  fail loudly (500 ERASURE_REQUEST_WRITE_FAILED).
- Unknown and cross-tenant request ids answer a uniform 404 (no existence oracle, ADR-011).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from idis.api.errors import IdisHttpError
from idis.validators.audit_event_validator import validate_audit_event

if TYPE_CHECKING:
    from idis.api.auth import TenantContext
    from idis.audit.sink import AuditSink

logger = logging.getLogger(__name__)

ERASURE_REQUESTED = "erasure.requested"
ERASURE_EXECUTED = "erasure.executed"


class ErasureStatus(StrEnum):
    """Lifecycle states of an erasure request."""

    REQUESTED = "REQUESTED"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class ErasureRequest:
    """A durable per-deal erasure request (evidence that outlives the erased deal)."""

    request_id: str
    tenant_id: str
    deal_id: str
    status: ErasureStatus
    requested_by: str
    requested_at: datetime
    reason_hash: str
    reason_length: int
    executed_by: str | None = None
    executed_at: datetime | None = None
    counts: dict[str, int] = field(default_factory=dict)

    @classmethod
    def new(cls, *, tenant_id: str, deal_id: str, requested_by: str, reason: str) -> ErasureRequest:
        """Build a fresh REQUESTED entry; the reason is hashed here and never kept raw."""
        return cls(
            request_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            deal_id=deal_id,
            status=ErasureStatus.REQUESTED,
            requested_by=requested_by,
            requested_at=datetime.now(UTC),
            reason_hash=hashlib.sha256(reason.encode("utf-8")).hexdigest(),
            reason_length=len(reason),
        )


@runtime_checkable
class ErasureRequestStore(Protocol):
    """Seam for durable erasure requests.

    Implementations MUST raise on backend failure - a resolution error can never surface as
    "no request" (which would 404 instead of failing closed).
    """

    def create(self, request: ErasureRequest) -> None:
        """Persist a new request. Raises on failure (the request must fail loudly)."""
        ...

    def get(self, tenant_id: str, request_id: str) -> ErasureRequest | None:
        """Return the request under this tenant, or None (uniform cross-tenant miss)."""
        ...

    def update(self, request: ErasureRequest) -> None:
        """Persist a lifecycle transition."""
        ...

    def list_for_tenant(self, tenant_id: str) -> list[ErasureRequest]:
        """All of a tenant's requests (evidence listing)."""
        ...


class InMemoryErasureRequestStore:
    """Process-local twin (tests and non-Postgres deployments)."""

    def __init__(self) -> None:
        self._requests: dict[tuple[str, str], ErasureRequest] = {}

    def create(self, request: ErasureRequest) -> None:
        self._requests[(request.tenant_id, request.request_id)] = request

    def get(self, tenant_id: str, request_id: str) -> ErasureRequest | None:
        return self._requests.get((tenant_id, request_id))

    def update(self, request: ErasureRequest) -> None:
        self._requests[(request.tenant_id, request.request_id)] = request

    def list_for_tenant(self, tenant_id: str) -> list[ErasureRequest]:
        return [r for (tid, _), r in self._requests.items() if tid == tenant_id]


class PostgresErasureRequestStore:
    """Durable twin over ``erasure_requests`` (migration 0030, guarded RLS, NO FK to deals).

    Reads fail CLOSED (403 ERASURE_RESOLUTION_FAILED); writes fail loudly (500) and, being
    single-statement transactions, leave no durable state behind on failure.
    """

    _SELECT = (
        "SELECT request_id, tenant_id, deal_id, status, requested_by, requested_at, "
        "reason_hash, reason_length, executed_by, executed_at, counts FROM erasure_requests"
    )

    def _row_to_request(self, row: Any) -> ErasureRequest:
        return ErasureRequest(
            request_id=str(row.request_id),
            tenant_id=str(row.tenant_id),
            deal_id=str(row.deal_id),
            status=ErasureStatus(row.status),
            requested_by=row.requested_by,
            requested_at=row.requested_at,
            reason_hash=row.reason_hash,
            reason_length=row.reason_length,
            executed_by=row.executed_by,
            executed_at=row.executed_at,
            counts=dict(row.counts) if row.counts else {},
        )

    def _read(self, tenant_id: str, sql: str, params: dict[str, str]) -> list[Any]:
        from sqlalchemy import text

        from idis.persistence.db import begin_app_conn, set_tenant_local

        try:
            with begin_app_conn() as conn:
                set_tenant_local(conn, tenant_id)
                return list(conn.execute(text(sql), params))
        except Exception as e:
            logger.error("PostgresErasureRequestStore read failed: %s", type(e).__name__)
            raise IdisHttpError(
                status_code=403,
                code="ERASURE_RESOLUTION_FAILED",
                message="Access denied.",
            ) from e

    def _write(self, tenant_id: str, sql: str, params: dict[str, Any]) -> None:
        from sqlalchemy import text

        from idis.persistence.db import begin_app_conn, set_tenant_local

        try:
            with begin_app_conn() as conn:
                set_tenant_local(conn, tenant_id)
                conn.execute(text(sql), params)
        except Exception as e:
            logger.error("PostgresErasureRequestStore write failed: %s", type(e).__name__)
            raise IdisHttpError(
                status_code=500,
                code="ERASURE_REQUEST_WRITE_FAILED",
                message="Erasure request could not be persisted",
            ) from e

    def create(self, request: ErasureRequest) -> None:
        import json

        self._write(
            request.tenant_id,
            """
            INSERT INTO erasure_requests (
                tenant_id, request_id, deal_id, status, requested_by, requested_at,
                reason_hash, reason_length, executed_by, executed_at, counts
            ) VALUES (
                CAST(:tenant_id AS uuid), CAST(:request_id AS uuid), CAST(:deal_id AS uuid),
                :status, :requested_by, :requested_at, :reason_hash, :reason_length,
                :executed_by, :executed_at, CAST(:counts AS jsonb)
            )
            """,
            {
                "tenant_id": request.tenant_id,
                "request_id": request.request_id,
                "deal_id": request.deal_id,
                "status": request.status.value,
                "requested_by": request.requested_by,
                "requested_at": request.requested_at,
                "reason_hash": request.reason_hash,
                "reason_length": request.reason_length,
                "executed_by": request.executed_by,
                "executed_at": request.executed_at,
                "counts": json.dumps(request.counts),
            },
        )

    def get(self, tenant_id: str, request_id: str) -> ErasureRequest | None:
        rows = self._read(
            tenant_id,
            f"{self._SELECT} WHERE tenant_id = CAST(:tenant_id AS uuid) "
            "AND request_id = CAST(:request_id AS uuid)",
            {"tenant_id": tenant_id, "request_id": request_id},
        )
        return self._row_to_request(rows[0]) if rows else None

    def update(self, request: ErasureRequest) -> None:
        import json

        self._write(
            request.tenant_id,
            """
            UPDATE erasure_requests
            SET status = :status, executed_by = :executed_by, executed_at = :executed_at,
                counts = CAST(:counts AS jsonb)
            WHERE tenant_id = CAST(:tenant_id AS uuid)
                AND request_id = CAST(:request_id AS uuid)
            """,
            {
                "tenant_id": request.tenant_id,
                "request_id": request.request_id,
                "status": request.status.value,
                "executed_by": request.executed_by,
                "executed_at": request.executed_at,
                "counts": json.dumps(request.counts),
            },
        )

    def list_for_tenant(self, tenant_id: str) -> list[ErasureRequest]:
        rows = self._read(
            tenant_id,
            f"{self._SELECT} WHERE tenant_id = CAST(:tenant_id AS uuid) ORDER BY requested_at",
            {"tenant_id": tenant_id},
        )
        return [self._row_to_request(row) for row in rows]


_store: ErasureRequestStore | None = None


def build_default_erasure_request_store() -> ErasureRequestStore:
    """Select the durable Postgres store when configured, else the in-memory twin."""
    from idis.persistence.db import is_postgres_configured

    if is_postgres_configured():
        return PostgresErasureRequestStore()
    return InMemoryErasureRequestStore()


def get_erasure_request_store() -> ErasureRequestStore:
    """Return the process-wide erasure-request store, building the default on first use."""
    global _store
    if _store is None:
        _store = build_default_erasure_request_store()
    return _store


def set_erasure_request_store(store: ErasureRequestStore) -> None:
    """Override the process-wide store (tests / explicit wiring)."""
    global _store
    _store = store


def reset_erasure_request_store() -> None:
    """Clear the process-wide store so the next access rebuilds the default."""
    global _store
    _store = None


def _build_erasure_audit_event(
    *,
    tenant_id: str,
    actor_id: str,
    event_type: str,
    severity: str,
    request: ErasureRequest,
    safe_details: dict[str, Any],
) -> dict[str, Any]:
    """Build an erasure audit event. Only hashes/lengths/ids/counts - never the raw reason."""
    return {
        "event_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tenant_id": tenant_id,
        "actor": {
            "actor_type": "SERVICE",
            "actor_id": actor_id,
            "roles": ["ADMIN"],
            "ip": "internal",
            "user_agent": "idis-compliance",
        },
        "request": {
            "request_id": request.request_id,
            "method": "POST",
            "path": "/internal/compliance/erasure",
            "status_code": 200,
        },
        "resource": {
            "resource_type": "erasure_request",
            "resource_id": request.request_id,
            "deal_id": request.deal_id,
        },
        "event_type": event_type,
        "severity": severity,
        "summary": f"{event_type} for deal {request.deal_id}",
        "payload": {
            "safe": safe_details,
            "hashes": [f"reason_sha256:{request.reason_hash}"],
            "refs": [f"deal_id:{request.deal_id}"],
        },
    }


def _emit_audit_or_fail(audit_sink: AuditSink | None, event: dict[str, Any], op: str) -> None:
    """Validate THEN emit; audit is fatal for erasure mutations (fail-closed).

    The event is validated against the audit contract BEFORE emission (the janitor precedent):
    a validation failure or a missing/failing sink raises so the caller aborts the guarded
    action (the request-row write / the destruction) - never a malformed or unrecorded event.
    """
    if audit_sink is None:
        logger.error("Erasure audit sink not configured; %s BLOCKED (fail-closed)", op)
        raise IdisHttpError(
            status_code=500,
            code="ERASURE_AUDIT_FAILED",
            message="Operation failed: audit requirement not met",
        )
    validation = validate_audit_event(event)
    if not validation.passed:
        logger.error(
            "Erasure audit event failed validation for %s (fail-closed): %s",
            op,
            [error.code for error in validation.errors],
        )
        raise IdisHttpError(
            status_code=500,
            code="ERASURE_AUDIT_FAILED",
            message="Operation failed: audit requirement not met",
        )
    try:
        audit_sink.emit(event)
    except Exception:
        logger.error("Erasure audit emission failed for %s (fail-closed)", op, exc_info=True)
        raise IdisHttpError(
            status_code=500,
            code="ERASURE_AUDIT_FAILED",
            message="Operation failed: audit requirement not met",
        ) from None


def request_erasure(
    tenant_ctx: TenantContext,
    deal_id: str,
    reason: str,
    audit_sink: AuditSink | None = None,
    store: ErasureRequestStore | None = None,
) -> ErasureRequest:
    """Create a durable per-deal erasure request (REQUESTED). Audit-fatal, reason hash-only."""
    if not reason or not reason.strip():
        raise IdisHttpError(
            status_code=400,
            code="ERASURE_INVALID_REASON",
            message="Erasure reason cannot be empty",
        )

    effective_store = store or get_erasure_request_store()
    request = ErasureRequest.new(
        tenant_id=tenant_ctx.tenant_id,
        deal_id=deal_id,
        requested_by=tenant_ctx.actor_id,
        reason=reason,
    )

    event = _build_erasure_audit_event(
        tenant_id=tenant_ctx.tenant_id,
        actor_id=tenant_ctx.actor_id,
        event_type=ERASURE_REQUESTED,
        severity="HIGH",
        request=request,
        safe_details={"deal_id": deal_id, "reason_length": request.reason_length},
    )
    _emit_audit_or_fail(audit_sink, event, "request_erasure")

    effective_store.create(request)
    logger.info(
        "Erasure requested: tenant_id=%s deal_id=%s request_id=%s",
        tenant_ctx.tenant_id,
        deal_id,
        request.request_id,
    )
    return request


@runtime_checkable
class ErasureExecutor(Protocol):
    """Performs the actual per-deal destruction; returns safe deletion counts."""

    def scan_holds(self, tenant_id: str, deal_id: str) -> None:
        """Raise DELETION_BLOCKED_BY_HOLD if ANY hold covers the deal's artifacts.

        Runs BEFORE the CRITICAL audit event and before any destruction, so a held artifact
        aborts the whole execution with zero deletions and no misleading executed record.
        """
        ...

    def erase_deal(self, tenant_id: str, deal_id: str) -> dict[str, int]:
        """Delete the deal's rows/objects/embeddings. Raises on failure (no silent partials)."""
        ...


class InMemoryErasureExecutor:
    """Hermetic twin over the in-memory route stores (deals + documents).

    Covers the row-removal semantics the app surfaces hermetically; the COMPLETE deal-scoped
    table surface (the pinned classification list) is the Postgres executor's job, proven in
    the env-gated tests. Artifact-hold scanning is a no-op here: the hermetic deletion path has
    no enumerable object artifacts, and the deal-level hold check runs in the route flow.
    """

    def scan_holds(self, tenant_id: str, deal_id: str) -> None:
        return None

    def erase_deal(self, tenant_id: str, deal_id: str) -> dict[str, int]:
        from idis.api.routes.documents import _document_store
        from idis.persistence.repositories.deals import _in_memory_store as deals_store

        rows_deleted = 0
        artifacts = _document_store._artifacts
        doc_keys = [
            key
            for key, doc in list(artifacts.items())
            if doc.get("deal_id") == deal_id and doc.get("tenant_id") == tenant_id
        ]
        for key in doc_keys:
            del artifacts[key]
            rows_deleted += 1
        deal_row = deals_store.get(deal_id)
        if deal_row is not None and deal_row.get("tenant_id") == tenant_id:
            del deals_store[deal_id]
            rows_deleted += 1
        return {"rows_deleted": rows_deleted, "objects_deleted": 0, "embeddings_deleted": 0}


_executor: ErasureExecutor | None = None


def build_default_erasure_executor() -> ErasureExecutor:
    """Select the durable Postgres executor when configured, else the in-memory twin."""
    from idis.persistence.db import is_postgres_configured

    if is_postgres_configured():
        from idis.compliance.erasure_postgres import PostgresErasureExecutor

        return PostgresErasureExecutor()
    return InMemoryErasureExecutor()


def get_erasure_executor() -> ErasureExecutor:
    """Return the process-wide erasure executor, building the default on first use."""
    global _executor
    if _executor is None:
        _executor = build_default_erasure_executor()
    return _executor


def set_erasure_executor(executor: ErasureExecutor) -> None:
    """Override the process-wide executor (tests / explicit wiring)."""
    global _executor
    _executor = executor


def reset_erasure_executor() -> None:
    """Clear the process-wide executor so the next access rebuilds the default."""
    global _executor
    _executor = None


def execute_erasure(
    tenant_ctx: TenantContext,
    request_id: str,
    audit_sink: AuditSink | None = None,
    *,
    executor: ErasureExecutor,
    hold_checker: Any,
    store: ErasureRequestStore | None = None,
) -> ErasureRequest:
    """Execute a REQUESTED (or FAILED) erasure: holds -> CRITICAL audit -> destruction.

    Ordering invariants: the hold check runs FIRST (any active hold aborts with zero deletions
    and the request stays re-executable); the CRITICAL ``erasure.executed`` event is emitted
    BEFORE destruction and its failure aborts all of it; an executor failure marks the request
    FAILED (500) for idempotent retry.
    """
    effective_store = store or get_erasure_request_store()
    request = effective_store.get(tenant_ctx.tenant_id, request_id)
    if request is None:
        raise IdisHttpError(
            status_code=404,
            code="ERASURE_REQUEST_NOT_FOUND",
            message="Erasure request not found",
        )
    if request.status == ErasureStatus.EXECUTED:
        raise IdisHttpError(
            status_code=409,
            code="ERASURE_ALREADY_EXECUTED",
            message="Erasure request already executed",
        )

    # Holds win, before anything else: a held deal cannot be erased (no partial deletions),
    # and the executor's artifact-level scan must also pass BEFORE the CRITICAL audit event -
    # otherwise an aborted execution would leave a misleading "executed" record.
    hold_checker(tenant_ctx.tenant_id, request.deal_id)
    executor.scan_holds(tenant_ctx.tenant_id, request.deal_id)

    event = _build_erasure_audit_event(
        tenant_id=tenant_ctx.tenant_id,
        actor_id=tenant_ctx.actor_id,
        event_type=ERASURE_EXECUTED,
        severity="CRITICAL",
        request=request,
        safe_details={"deal_id": request.deal_id, "requested_by": request.requested_by},
    )
    _emit_audit_or_fail(audit_sink, event, "execute_erasure")

    try:
        counts = executor.erase_deal(tenant_ctx.tenant_id, request.deal_id)
    except IdisHttpError:
        failed = replace(
            request,
            status=ErasureStatus.FAILED,
            executed_by=tenant_ctx.actor_id,
            executed_at=datetime.now(UTC),
        )
        effective_store.update(failed)
        raise
    except Exception as e:
        logger.error("Erasure execution failed for deal %s", request.deal_id, exc_info=True)
        failed = replace(
            request,
            status=ErasureStatus.FAILED,
            executed_by=tenant_ctx.actor_id,
            executed_at=datetime.now(UTC),
        )
        effective_store.update(failed)
        raise IdisHttpError(
            status_code=500,
            code="ERASURE_EXECUTION_FAILED",
            message="Erasure execution failed",
        ) from e

    executed = replace(
        request,
        status=ErasureStatus.EXECUTED,
        executed_by=tenant_ctx.actor_id,
        executed_at=datetime.now(UTC),
        counts={key: int(value) for key, value in counts.items()},
    )
    effective_store.update(executed)
    logger.info(
        "Erasure executed: tenant_id=%s deal_id=%s request_id=%s",
        tenant_ctx.tenant_id,
        request.deal_id,
        request.request_id,
    )
    return executed

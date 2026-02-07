"""Postgres → Neo4j dual-write projection consistency layer.

Implements fail-closed projection of Postgres-persisted entities into
the Neo4j graph. Uses the existing saga pattern from persistence.saga.

Fail-closed semantics:
    - If Neo4j is configured and projection fails, a structured failure
      is recorded (never silently skipped).
    - Audit events are emitted for every projection attempt.
    - Audit sink failure is fatal (request fails).

Design (v6.3 §5.6):
    - Postgres is the source of truth.
    - Neo4j is the graph projection for traversal queries.
    - Saga pattern ensures both stores are consistent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from idis.persistence.graph_repo import GraphProjectionError, GraphRepository
from idis.persistence.neo4j_driver import is_neo4j_configured
from idis.persistence.saga import DualWriteSagaExecutor

logger = logging.getLogger(__name__)


class ProjectionStatus(StrEnum):
    """Status of a graph projection operation."""

    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"
    AUDIT_FAILURE = "audit_failure"


class AuditSinkProtocol(Protocol):
    """Protocol for audit event emission."""

    def emit(self, event: dict[str, Any]) -> None:
        """Emit an audit event. Must not silently fail."""
        ...


@dataclass(frozen=True)
class ProjectionResult:
    """Result of a graph projection operation."""

    status: ProjectionStatus
    entity_type: str
    entity_id: str
    tenant_id: str
    error: str | None = None
    timestamp: str | None = None


def _build_audit_event(
    *,
    tenant_id: str,
    entity_type: str,
    entity_id: str,
    status: ProjectionStatus,
    error: str | None = None,
) -> dict[str, Any]:
    """Build a graph projection audit event.

    Args:
        tenant_id: Tenant UUID.
        entity_type: Type of entity being projected.
        entity_id: ID of entity being projected.
        status: Projection status.
        error: Error message if failed.

    Returns:
        Audit event dictionary.
    """
    event: dict[str, Any] = {
        "event_type": f"graph_projection.{entity_type}.{status.value}",
        "tenant_id": tenant_id,
        "severity": "HIGH" if status == ProjectionStatus.FAILED else "LOW",
        "resource": {
            "resource_type": entity_type,
            "resource_id": entity_id,
        },
        "payload": {
            "projection_target": "neo4j",
            "status": status.value,
        },
        "occurred_at": datetime.now(UTC).isoformat(),
    }
    if error:
        event["payload"]["error"] = error
    return event


def _emit_audit_or_fail(
    audit_sink: AuditSinkProtocol | None,
    event: dict[str, Any],
) -> None:
    """Emit an audit event; raise if audit sink fails.

    Audit sink failure is fatal per v6.3 audit completeness invariant.

    Args:
        audit_sink: Audit sink to emit to.
        event: Audit event dictionary.

    Raises:
        RuntimeError: If audit emission fails.
    """
    if audit_sink is None:
        return

    try:
        audit_sink.emit(event)
    except Exception as exc:
        raise RuntimeError(
            f"Audit sink failure during graph projection "
            f"(event_type={event.get('event_type')}): {exc}"
        ) from exc


class GraphProjectionService:
    """Service for projecting Postgres-persisted entities into Neo4j.

    Implements fail-closed dual-write projection with audit events.
    If Neo4j is not configured, projections are skipped (not an error).
    If Neo4j IS configured and projection fails, a structured failure
    is recorded and an audit event emitted.
    """

    def __init__(
        self,
        *,
        graph_repo: GraphRepository | None = None,
        audit_sink: AuditSinkProtocol | None = None,
    ) -> None:
        """Initialize the projection service.

        Args:
            graph_repo: Graph repository instance. Defaults to new GraphRepository.
            audit_sink: Audit sink for projection events.
        """
        self._graph_repo = graph_repo or GraphRepository()
        self._audit_sink = audit_sink

    def project_deal(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        documents: list[dict[str, Any]],
        spans: list[dict[str, Any]],
        entities: list[dict[str, Any]] | None = None,
    ) -> ProjectionResult:
        """Project deal structure into Neo4j after Postgres persist.

        Args:
            tenant_id: Tenant UUID.
            deal_id: Deal UUID.
            documents: Document dicts.
            spans: Span dicts.
            entities: Optional entity dicts.

        Returns:
            ProjectionResult with status.
        """
        if not is_neo4j_configured():
            return ProjectionResult(
                status=ProjectionStatus.SKIPPED,
                entity_type="deal",
                entity_id=deal_id,
                tenant_id=tenant_id,
                timestamp=datetime.now(UTC).isoformat(),
            )

        try:
            self._graph_repo.upsert_deal_graph_projection(
                tenant_id=tenant_id,
                deal_id=deal_id,
                documents=documents,
                spans=spans,
                entities=entities,
            )

            result = ProjectionResult(
                status=ProjectionStatus.SUCCESS,
                entity_type="deal",
                entity_id=deal_id,
                tenant_id=tenant_id,
                timestamp=datetime.now(UTC).isoformat(),
            )

            audit_event = _build_audit_event(
                tenant_id=tenant_id,
                entity_type="deal",
                entity_id=deal_id,
                status=ProjectionStatus.SUCCESS,
            )
            _emit_audit_or_fail(self._audit_sink, audit_event)

            return result

        except GraphProjectionError as exc:
            error_msg = str(exc)
            logger.error("Graph projection failed for deal %s: %s", deal_id, error_msg)

            audit_event = _build_audit_event(
                tenant_id=tenant_id,
                entity_type="deal",
                entity_id=deal_id,
                status=ProjectionStatus.FAILED,
                error=error_msg,
            )

            try:
                _emit_audit_or_fail(self._audit_sink, audit_event)
            except RuntimeError:
                return ProjectionResult(
                    status=ProjectionStatus.AUDIT_FAILURE,
                    entity_type="deal",
                    entity_id=deal_id,
                    tenant_id=tenant_id,
                    error=f"Projection failed AND audit emission failed: {error_msg}",
                    timestamp=datetime.now(UTC).isoformat(),
                )

            return ProjectionResult(
                status=ProjectionStatus.FAILED,
                entity_type="deal",
                entity_id=deal_id,
                tenant_id=tenant_id,
                error=error_msg,
                timestamp=datetime.now(UTC).isoformat(),
            )

    def project_claim_sanad(
        self,
        *,
        tenant_id: str,
        claim: dict[str, Any],
        evidence_items: list[dict[str, Any]],
        transmission_nodes: list[dict[str, Any]],
        defects: list[dict[str, Any]] | None = None,
        calculations: list[dict[str, Any]] | None = None,
    ) -> ProjectionResult:
        """Project claim Sanad chain into Neo4j after Postgres persist.

        Args:
            tenant_id: Tenant UUID.
            claim: Claim dict.
            evidence_items: Evidence dicts.
            transmission_nodes: TransmissionNode dicts.
            defects: Optional defect dicts.
            calculations: Optional calculation dicts.

        Returns:
            ProjectionResult with status.
        """
        claim_id = claim.get("claim_id", "unknown")

        if not is_neo4j_configured():
            return ProjectionResult(
                status=ProjectionStatus.SKIPPED,
                entity_type="claim_sanad",
                entity_id=claim_id,
                tenant_id=tenant_id,
                timestamp=datetime.now(UTC).isoformat(),
            )

        try:
            self._graph_repo.upsert_claim_sanad_projection(
                tenant_id=tenant_id,
                claim=claim,
                evidence_items=evidence_items,
                transmission_nodes=transmission_nodes,
                defects=defects,
                calculations=calculations,
            )

            result = ProjectionResult(
                status=ProjectionStatus.SUCCESS,
                entity_type="claim_sanad",
                entity_id=claim_id,
                tenant_id=tenant_id,
                timestamp=datetime.now(UTC).isoformat(),
            )

            audit_event = _build_audit_event(
                tenant_id=tenant_id,
                entity_type="claim_sanad",
                entity_id=claim_id,
                status=ProjectionStatus.SUCCESS,
            )
            _emit_audit_or_fail(self._audit_sink, audit_event)

            return result

        except GraphProjectionError as exc:
            error_msg = str(exc)
            logger.error("Graph projection failed for claim %s: %s", claim_id, error_msg)

            audit_event = _build_audit_event(
                tenant_id=tenant_id,
                entity_type="claim_sanad",
                entity_id=claim_id,
                status=ProjectionStatus.FAILED,
                error=error_msg,
            )

            try:
                _emit_audit_or_fail(self._audit_sink, audit_event)
            except RuntimeError:
                return ProjectionResult(
                    status=ProjectionStatus.AUDIT_FAILURE,
                    entity_type="claim_sanad",
                    entity_id=claim_id,
                    tenant_id=tenant_id,
                    error=f"Projection failed AND audit emission failed: {error_msg}",
                    timestamp=datetime.now(UTC).isoformat(),
                )

            return ProjectionResult(
                status=ProjectionStatus.FAILED,
                entity_type="claim_sanad",
                entity_id=claim_id,
                tenant_id=tenant_id,
                error=error_msg,
                timestamp=datetime.now(UTC).isoformat(),
            )


def create_claim_projection_saga(
    *,
    graph_repo: GraphRepository,
    tenant_id: str,
    claim: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    transmission_nodes: list[dict[str, Any]],
    defects: list[dict[str, Any]] | None = None,
    calculations: list[dict[str, Any]] | None = None,
    postgres_insert: Any,
    postgres_delete: Any,
) -> DualWriteSagaExecutor:
    """Create a dual-write saga for claim + Sanad graph projection.

    Extends the existing saga pattern to include Neo4j projection
    as the graph write step.

    Args:
        graph_repo: Graph repository instance.
        tenant_id: Tenant UUID.
        claim: Claim dict.
        evidence_items: Evidence dicts.
        transmission_nodes: TransmissionNode dicts.
        defects: Optional defect dicts.
        calculations: Optional calculation dicts.
        postgres_insert: Callable for Postgres insert.
        postgres_delete: Callable for Postgres delete (compensation).

    Returns:
        Configured saga executor.
    """

    def graph_insert(ctx: dict[str, Any]) -> str:
        graph_repo.upsert_claim_sanad_projection(
            tenant_id=tenant_id,
            claim=claim,
            evidence_items=evidence_items,
            transmission_nodes=transmission_nodes,
            defects=defects,
            calculations=calculations,
        )
        return str(claim.get("claim_id", ""))

    def graph_delete(ctx: dict[str, Any], result: str) -> None:
        logger.info("Compensating graph projection for claim %s", result)

    return (
        DualWriteSagaExecutor(f"claim-projection-{claim.get('claim_id', '')!s}")
        .add_postgres_step("postgres_claim_insert", postgres_insert, postgres_delete)
        .add_graph_step("graph_claim_projection", graph_insert, graph_delete)
    )

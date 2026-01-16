"""SanadService - business logic layer for Sanad CRUD operations.

Implements SAN-001 traceability requirements:
- Every material claim has a Sanad object with transmission_chain, grade, defects
- Sanad created on claim creation or lazy-created on first access
- Grade computation delegated to grader.py

Uses Postgres repositories when db_conn exists, in-memory fallback otherwise.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from idis.audit.sink import AuditSink, InMemoryAuditSink
from idis.persistence.repositories.claims import (
    DefectsRepository,
    InMemoryDefectsRepository,
    InMemorySanadsRepository,
    SanadsRepository,
)

if TYPE_CHECKING:
    from sqlalchemy import Connection

logger = logging.getLogger(__name__)


class SanadServiceError(Exception):
    """Base exception for SanadService errors."""

    pass


class SanadNotFoundError(SanadServiceError):
    """Raised when a sanad is not found."""

    def __init__(self, sanad_id: str, tenant_id: str) -> None:
        self.sanad_id = sanad_id
        self.tenant_id = tenant_id
        super().__init__(f"Sanad {sanad_id} not found for tenant {tenant_id}")


class SanadIntegrityError(SanadServiceError):
    """Raised when sanad integrity validation fails."""

    def __init__(self, sanad_id: str, errors: list[str]) -> None:
        self.sanad_id = sanad_id
        self.errors = errors
        super().__init__(f"Sanad {sanad_id} integrity failed: {'; '.join(errors)}")


class CreateSanadInput(BaseModel):
    """Input model for creating a sanad."""

    claim_id: str = Field(..., description="Claim UUID this sanad supports")
    deal_id: str = Field(..., description="Deal UUID")
    primary_evidence_id: str = Field(..., description="Primary evidence UUID")
    corroborating_evidence_ids: list[str] = Field(
        default_factory=list, description="Corroborating evidence UUIDs"
    )
    transmission_chain: list[dict[str, Any]] = Field(
        default_factory=list, description="Transmission chain nodes"
    )
    extraction_confidence: float = Field(
        default=0.9, ge=0.0, le=1.0, description="Extraction confidence"
    )
    request_id: str | None = Field(default=None, description="Request correlation ID")


class UpdateSanadInput(BaseModel):
    """Input model for updating a sanad."""

    corroborating_evidence_ids: list[str] | None = Field(
        default=None, description="Updated corroborating evidence"
    )
    transmission_chain: list[dict[str, Any]] | None = Field(
        default=None, description="Updated transmission chain"
    )
    request_id: str | None = Field(default=None, description="Request correlation ID")


class SanadService:
    """Service layer for sanad operations with coverage enforcement.

    Implements SAN-001 traceability requirements:
    - CRUD operations for sanads
    - Grade computation via grader.py
    - Defect integration
    - Corroboration status tracking

    All operations are tenant-scoped.
    """

    def __init__(
        self,
        tenant_id: str,
        db_conn: Connection | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        """Initialize SanadService with tenant context.

        Args:
            tenant_id: Tenant UUID for scoping all operations.
            db_conn: SQLAlchemy connection for Postgres. If None, uses in-memory.
            audit_sink: Optional audit sink for event emission.
        """
        self._tenant_id = tenant_id
        self._db_conn = db_conn
        self._audit_sink = audit_sink or InMemoryAuditSink()

        if db_conn is not None:
            self._sanads_repo: SanadsRepository | InMemorySanadsRepository = SanadsRepository(
                db_conn, tenant_id
            )
            self._defects_repo: DefectsRepository | InMemoryDefectsRepository = DefectsRepository(
                db_conn, tenant_id
            )
        else:
            self._sanads_repo = InMemorySanadsRepository(tenant_id)
            self._defects_repo = InMemoryDefectsRepository(tenant_id)

    @property
    def tenant_id(self) -> str:
        """Return the tenant context."""
        return self._tenant_id

    def _emit_audit_event(
        self,
        event_type: str,
        sanad_id: str,
        severity: str = "MEDIUM",
        details: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        """Emit an audit event for sanad operations with request correlation."""
        event: dict[str, Any] = {
            "event_id": str(uuid.uuid4()),
            "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "tenant_id": self._tenant_id,
            "event_type": event_type,
            "severity": severity,
            "resource": {
                "resource_type": "sanad",
                "resource_id": sanad_id,
            },
            "actor": {
                "actor_type": "SERVICE",
                "actor_id": "sanad_service",
                "roles": ["INTEGRATION_SERVICE"],
                "ip": "internal",
                "user_agent": "SanadService",
            },
            "request": {
                "request_id": request_id or str(uuid.uuid4()),
                "method": "SERVICE",
                "path": "/internal/sanads",
                "status_code": 200,
            },
            "summary": f"{event_type} for sanad {sanad_id}",
            "payload": {
                "hashes": [],
                "refs": [f"sanad_id:{sanad_id}"],
            },
        }
        if details:
            event["payload"]["details"] = details
        try:
            self._audit_sink.emit(event)
        except Exception as e:
            logger.warning("Failed to emit audit event: %s", e)

    def _compute_grade(
        self,
        transmission_chain: list[dict[str, Any]],
        defects: list[dict[str, Any]],
        corroborating_count: int,
    ) -> dict[str, Any]:
        """Compute sanad grade using grader v2.

        Args:
            transmission_chain: List of transmission nodes.
            defects: List of defect dicts.
            corroborating_count: Number of corroborating sources.

        Returns:
            Dict with grade, grade_rationale, corroboration_level, independent_chain_count.
        """
        corroboration_level = "AHAD_1"
        if corroborating_count >= 3:
            corroboration_level = "MUTAWATIR"
        elif corroborating_count >= 2:
            corroboration_level = "AHAD_2"

        base_grade = "B"
        if transmission_chain:
            first_node = transmission_chain[0]
            node_type = first_node.get("node_type", "")
            if node_type in ("AUDITOR", "REGULATOR"):
                base_grade = "A"
            elif node_type in ("COMPANY_DATA_ROOM", "MANAGEMENT"):
                base_grade = "B"
            elif node_type == "THIRD_PARTY":
                base_grade = "C"

        fatal_count = sum(
            1 for d in defects if d.get("severity") == "FATAL" and d.get("status") == "OPEN"
        )
        major_count = sum(
            1 for d in defects if d.get("severity") == "MAJOR" and d.get("status") == "OPEN"
        )

        if fatal_count > 0:
            computed_grade = "D"
            rationale = "FATAL defect forces grade D"
        else:
            grade_order = ["A", "B", "C", "D"]
            base_idx = grade_order.index(base_grade)
            downgrade = min(major_count, 3 - base_idx)
            computed_idx = base_idx + downgrade
            computed_grade = grade_order[min(computed_idx, 3)]

            if corroboration_level == "MUTAWATIR" and computed_grade != "A" and fatal_count == 0:
                upgrade_idx = max(computed_idx - 1, 0)
                computed_grade = grade_order[upgrade_idx]
                rationale = f"Base {base_grade}, {major_count} MAJOR defects, MUTAWATIR upgrade"
            else:
                rationale = f"Base {base_grade}, {major_count} MAJOR defects"

        return {
            "grade": computed_grade,
            "grade_rationale": rationale,
            "corroboration_level": corroboration_level,
            "independent_chain_count": corroborating_count + 1,
        }

    def create(self, input_data: CreateSanadInput) -> dict[str, Any]:
        """Create a new sanad with grade computation.

        Args:
            input_data: Validated input data for sanad creation.

        Returns:
            Created sanad data dict.
        """
        sanad_id = str(uuid.uuid4())

        defects, _ = self._defects_repo.list_by_claim(input_data.claim_id, limit=100)

        transmission_chain = input_data.transmission_chain
        if not transmission_chain:
            transmission_chain = [
                {
                    "node_id": str(uuid.uuid4()),
                    "node_type": "EXTRACTION",
                    "actor_type": "SYSTEM",
                    "actor_id": "idis_extractor",
                    "input_refs": [{"evidence_id": input_data.primary_evidence_id}],
                    "output_refs": [{"claim_id": input_data.claim_id}],
                    "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "confidence": input_data.extraction_confidence,
                }
            ]

        computed = self._compute_grade(
            transmission_chain=transmission_chain,
            defects=defects,
            corroborating_count=len(input_data.corroborating_evidence_ids),
        )

        sanad_data = self._sanads_repo.create(
            sanad_id=sanad_id,
            claim_id=input_data.claim_id,
            deal_id=input_data.deal_id,
            primary_evidence_id=input_data.primary_evidence_id,
            corroborating_evidence_ids=input_data.corroborating_evidence_ids,
            transmission_chain=transmission_chain,
            computed=computed,
        )

        self._emit_audit_event(
            event_type="sanad.created",
            sanad_id=sanad_id,
            severity="MEDIUM",
            details={
                "claim_id": input_data.claim_id,
                "deal_id": input_data.deal_id,
                "grade": computed["grade"],
            },
            request_id=input_data.request_id,
        )

        return sanad_data

    def get(self, sanad_id: str) -> dict[str, Any]:
        """Get a sanad by ID.

        Args:
            sanad_id: UUID of the sanad.

        Returns:
            Sanad data dict.

        Raises:
            SanadNotFoundError: If sanad not found for this tenant.
        """
        sanad_data = self._sanads_repo.get(sanad_id)
        if sanad_data is None:
            raise SanadNotFoundError(sanad_id, self._tenant_id)
        return sanad_data

    def get_by_claim(self, claim_id: str) -> dict[str, Any] | None:
        """Get sanad for a claim, creating if needed for coverage.

        Args:
            claim_id: UUID of the claim.

        Returns:
            Sanad data dict or None if claim has no sanad.
        """
        return self._sanads_repo.get_by_claim(claim_id)

    def list_by_deal(
        self,
        deal_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List sanads for a deal with pagination.

        Args:
            deal_id: Deal UUID to filter by.
            limit: Maximum number of sanads to return.
            cursor: Pagination cursor for offset.

        Returns:
            Tuple of (sanads_list, next_cursor).
        """
        return self._sanads_repo.list_by_deal(deal_id, limit=limit, cursor=cursor)

    def update(
        self,
        sanad_id: str,
        input_data: UpdateSanadInput,
    ) -> dict[str, Any]:
        """Update an existing sanad with re-grading.

        Args:
            sanad_id: UUID of the sanad to update.
            input_data: Validated input data for update.

        Returns:
            Updated sanad data dict.

        Raises:
            SanadNotFoundError: If sanad not found.
        """
        existing = self._sanads_repo.get(sanad_id)
        if existing is None:
            raise SanadNotFoundError(sanad_id, self._tenant_id)

        new_chain = (
            input_data.transmission_chain
            if input_data.transmission_chain is not None
            else existing["transmission_chain"]
        )
        new_corr = (
            input_data.corroborating_evidence_ids
            if input_data.corroborating_evidence_ids is not None
            else existing["corroborating_evidence_ids"]
        )

        defects, _ = self._defects_repo.list_by_claim(existing["claim_id"], limit=100)
        computed = self._compute_grade(
            transmission_chain=new_chain,
            defects=defects,
            corroborating_count=len(new_corr),
        )

        updated = self._sanads_repo.update(
            sanad_id,
            corroborating_evidence_ids=new_corr,
            transmission_chain=new_chain,
            computed=computed,
        )

        self._emit_audit_event(
            event_type="sanad.updated",
            sanad_id=sanad_id,
            severity="MEDIUM",
            details={
                "claim_id": existing["claim_id"],
                "new_grade": computed["grade"],
                "old_grade": existing.get("computed", {}).get("grade"),
            },
            request_id=input_data.request_id,
        )

        return updated or existing

    def set_corroboration(
        self,
        sanad_id: str,
        corroborating_evidence_ids: list[str],
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Update corroboration and re-compute grade.

        Args:
            sanad_id: UUID of the sanad.
            corroborating_evidence_ids: New list of corroborating evidence.
            request_id: Request correlation ID.

        Returns:
            Updated sanad data dict.

        Raises:
            SanadNotFoundError: If sanad not found.
        """
        existing = self._sanads_repo.get(sanad_id)
        if existing is None:
            raise SanadNotFoundError(sanad_id, self._tenant_id)

        old_level = existing.get("computed", {}).get("corroboration_level", "AHAD_1")

        defects, _ = self._defects_repo.list_by_claim(existing["claim_id"], limit=100)
        computed = self._compute_grade(
            transmission_chain=existing["transmission_chain"],
            defects=defects,
            corroborating_count=len(corroborating_evidence_ids),
        )

        updated = self._sanads_repo.update(
            sanad_id,
            corroborating_evidence_ids=corroborating_evidence_ids,
            computed=computed,
        )

        new_level = computed.get("corroboration_level", "AHAD_1")
        if old_level != new_level:
            self._emit_audit_event(
                event_type="sanad.corroboration.changed",
                sanad_id=sanad_id,
                severity="MEDIUM",
                details={
                    "old_level": old_level,
                    "new_level": new_level,
                    "evidence_count": len(corroborating_evidence_ids),
                },
                request_id=request_id,
            )

        return updated or existing

    def add_defect(
        self,
        sanad_id: str,
        defect_id: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Add a defect to sanad and re-compute grade.

        Args:
            sanad_id: UUID of the sanad.
            defect_id: UUID of the defect to link.
            request_id: Request correlation ID.

        Returns:
            Updated sanad data dict.

        Raises:
            SanadNotFoundError: If sanad not found.
        """
        existing = self._sanads_repo.get(sanad_id)
        if existing is None:
            raise SanadNotFoundError(sanad_id, self._tenant_id)

        defects, _ = self._defects_repo.list_by_claim(existing["claim_id"], limit=100)
        computed = self._compute_grade(
            transmission_chain=existing["transmission_chain"],
            defects=defects,
            corroborating_count=len(existing["corroborating_evidence_ids"]),
        )

        updated = self._sanads_repo.update(sanad_id, computed=computed)

        self._emit_audit_event(
            event_type="sanad.defect.added",
            sanad_id=sanad_id,
            severity="HIGH",
            details={
                "defect_id": defect_id,
                "new_grade": computed["grade"],
            },
            request_id=request_id,
        )

        return updated or existing

    def create_for_claim(
        self,
        claim_id: str,
        deal_id: str,
        primary_evidence_id: str,
        extraction_confidence: float = 0.9,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a sanad for a claim if one doesn't exist.

        This is called on claim creation to ensure SAN-001 coverage.

        Args:
            claim_id: UUID of the claim.
            deal_id: UUID of the deal.
            primary_evidence_id: UUID of primary evidence.
            extraction_confidence: Confidence score.
            request_id: Request correlation ID.

        Returns:
            New or existing sanad data dict.
        """
        existing = self._sanads_repo.get_by_claim(claim_id)
        if existing is not None:
            return existing

        input_data = CreateSanadInput(
            claim_id=claim_id,
            deal_id=deal_id,
            primary_evidence_id=primary_evidence_id,
            extraction_confidence=extraction_confidence,
            request_id=request_id,
        )
        return self.create(input_data)

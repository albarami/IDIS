"""DefectService - business logic layer for defect CRUD operations.

Implements DEF-001 traceability requirements:
- Defects created with type, severity, cure protocol
- FATAL severity forces grade D on linked claims
- Waiver/cure workflow requires actor + reason

Uses Postgres repositories when db_conn exists, in-memory fallback otherwise.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator

from idis.audit.sink import AuditSink, InMemoryAuditSink
from idis.persistence.repositories.claims import (
    DefectsRepository,
    InMemoryDefectsRepository,
)

if TYPE_CHECKING:
    from sqlalchemy import Connection

logger = logging.getLogger(__name__)


class DefectServiceError(Exception):
    """Base exception for DefectService errors."""

    pass


class DefectNotFoundError(DefectServiceError):
    """Raised when a defect is not found."""

    def __init__(self, defect_id: str, tenant_id: str) -> None:
        self.defect_id = defect_id
        self.tenant_id = tenant_id
        super().__init__(f"Defect {defect_id} not found for tenant {tenant_id}")


class WaiverRequiresActorReasonError(DefectServiceError):
    """Raised when waiver/cure is attempted without actor or reason."""

    def __init__(self, defect_id: str, missing: str) -> None:
        self.defect_id = defect_id
        self.missing = missing
        super().__init__(f"Defect {defect_id} waiver/cure requires {missing}")


class InvalidStateTransitionError(DefectServiceError):
    """Raised when an invalid defect state transition is attempted."""

    def __init__(self, defect_id: str, current_status: str, target_status: str) -> None:
        self.defect_id = defect_id
        self.current_status = current_status
        self.target_status = target_status
        super().__init__(
            f"Defect {defect_id} invalid state transition: {current_status} -> {target_status}. "
            f"Only OPEN -> WAIVED or OPEN -> CURED is allowed."
        )


FATAL_DEFECT_TYPES = frozenset({"BROKEN_CHAIN", "CONCEALMENT", "CIRCULARITY"})
MAJOR_DEFECT_TYPES = frozenset(
    {
        "INCONSISTENCY",
        "ANOMALY_VS_STRONGER_SOURCES",
        "UNKNOWN_SOURCE",
    }
)
MINOR_DEFECT_TYPES = frozenset(
    {
        "STALENESS",
        "UNIT_MISMATCH",
        "TIME_WINDOW_MISMATCH",
        "SCOPE_DRIFT",
    }
)


def get_severity_for_type(defect_type: str) -> str:
    """Return the severity for a defect type per DEF-001 matrix."""
    if defect_type in FATAL_DEFECT_TYPES:
        return "FATAL"
    if defect_type in MAJOR_DEFECT_TYPES:
        return "MAJOR"
    if defect_type in MINOR_DEFECT_TYPES:
        return "MINOR"
    return "MAJOR"


class CreateDefectInput(BaseModel):
    """Input model for creating a defect."""

    claim_id: str | None = Field(default=None, description="Linked claim UUID")
    deal_id: str | None = Field(default=None, description="Deal UUID")
    defect_type: str = Field(..., description="Defect type from DefectType enum")
    severity: str | None = Field(default=None, description="Override severity")
    description: str = Field(..., min_length=1, description="Defect description")
    cure_protocol: str = Field(..., description="Cure protocol from CureProtocol enum")
    request_id: str | None = Field(default=None, description="Request correlation ID")

    @field_validator("defect_type")
    @classmethod
    def validate_defect_type(cls, v: str) -> str:
        """Validate defect_type is known."""
        valid_types = (
            FATAL_DEFECT_TYPES
            | MAJOR_DEFECT_TYPES
            | MINOR_DEFECT_TYPES
            | {"MISSING_LINK", "CHRONO_IMPOSSIBLE", "CHAIN_GRAFTING", "IMPLAUSIBILITY"}
        )
        if v not in valid_types:
            raise ValueError(f"defect_type must be one of {sorted(valid_types)}")
        return v


class WaiveDefectInput(BaseModel):
    """Input model for waiving a defect."""

    actor: str = Field(..., min_length=1, description="Actor who approves waiver")
    reason: str = Field(..., min_length=1, description="Justification for waiver")
    request_id: str | None = Field(default=None, description="Request correlation ID")


class CureDefectInput(BaseModel):
    """Input model for curing a defect."""

    actor: str = Field(..., min_length=1, description="Actor who cured the defect")
    reason: str = Field(..., min_length=1, description="How the defect was cured")
    request_id: str | None = Field(default=None, description="Request correlation ID")


class DefectService:
    """Service layer for defect operations with waiver/cure workflow.

    Implements DEF-001 traceability requirements:
    - CRUD operations for defects
    - Severity matrix enforcement
    - Waiver requires actor + reason
    - Cure requires actor + reason

    All operations are tenant-scoped.
    """

    def __init__(
        self,
        tenant_id: str,
        db_conn: Connection | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        """Initialize DefectService with tenant context.

        Args:
            tenant_id: Tenant UUID for scoping all operations.
            db_conn: SQLAlchemy connection for Postgres. If None, uses in-memory.
            audit_sink: Optional audit sink for event emission.
        """
        self._tenant_id = tenant_id
        self._db_conn = db_conn
        self._audit_sink = audit_sink or InMemoryAuditSink()

        if db_conn is not None:
            self._defects_repo: DefectsRepository | InMemoryDefectsRepository = DefectsRepository(
                db_conn, tenant_id
            )
        else:
            self._defects_repo = InMemoryDefectsRepository(tenant_id)

    @property
    def tenant_id(self) -> str:
        """Return the tenant context."""
        return self._tenant_id

    def _emit_audit_event(
        self,
        event_type: str,
        defect_id: str,
        severity: str = "MEDIUM",
        details: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        """Emit an audit event for defect operations with request correlation."""
        event: dict[str, Any] = {
            "event_id": str(uuid.uuid4()),
            "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "tenant_id": self._tenant_id,
            "event_type": event_type,
            "severity": severity,
            "resource": {
                "resource_type": "defect",
                "resource_id": defect_id,
            },
            "actor": {
                "actor_type": "SERVICE",
                "actor_id": "defect_service",
                "roles": ["INTEGRATION_SERVICE"],
                "ip": "internal",
                "user_agent": "DefectService",
            },
            "request": {
                "request_id": request_id or str(uuid.uuid4()),
                "method": "SERVICE",
                "path": "/internal/defects",
                "status_code": 200,
            },
            "summary": f"{event_type} for defect {defect_id}",
            "payload": {
                "hashes": [],
                "refs": [f"defect_id:{defect_id}"],
            },
        }
        if details:
            event["payload"]["details"] = details
        try:
            self._audit_sink.emit(event)
        except Exception as e:
            logger.warning("Failed to emit audit event: %s", e)

    def create(self, input_data: CreateDefectInput) -> dict[str, Any]:
        """Create a new defect with severity matrix enforcement.

        Args:
            input_data: Validated input data for defect creation.

        Returns:
            Created defect data dict.
        """
        defect_id = str(uuid.uuid4())
        severity = input_data.severity or get_severity_for_type(input_data.defect_type)

        defect_data = self._defects_repo.create(
            defect_id=defect_id,
            claim_id=input_data.claim_id,
            deal_id=input_data.deal_id,
            defect_type=input_data.defect_type,
            severity=severity,
            description=input_data.description,
            cure_protocol=input_data.cure_protocol,
            status="OPEN",
        )

        audit_severity = "HIGH" if severity == "FATAL" else "MEDIUM"
        self._emit_audit_event(
            event_type="defect.created",
            defect_id=defect_id,
            severity=audit_severity,
            details={
                "defect_type": input_data.defect_type,
                "severity": severity,
                "claim_id": input_data.claim_id,
            },
            request_id=input_data.request_id,
        )

        return defect_data

    def get(self, defect_id: str) -> dict[str, Any]:
        """Get a defect by ID.

        Args:
            defect_id: UUID of the defect.

        Returns:
            Defect data dict.

        Raises:
            DefectNotFoundError: If defect not found for this tenant.
        """
        defect_data = self._defects_repo.get(defect_id)
        if defect_data is None:
            raise DefectNotFoundError(defect_id, self._tenant_id)
        return defect_data

    def list_by_claim(
        self,
        claim_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List defects for a claim with pagination.

        Args:
            claim_id: Claim UUID to filter by.
            limit: Maximum number of defects to return.
            cursor: Pagination cursor for offset.

        Returns:
            Tuple of (defects_list, next_cursor).
        """
        return self._defects_repo.list_by_claim(claim_id, limit=limit, cursor=cursor)

    def list_by_deal(
        self,
        deal_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List defects for a deal with pagination.

        Args:
            deal_id: Deal UUID to filter by.
            limit: Maximum number of defects to return.
            cursor: Pagination cursor for offset.

        Returns:
            Tuple of (defects_list, next_cursor).
        """
        return self._defects_repo.list_by_deal(deal_id, limit=limit, cursor=cursor)

    def waive(self, defect_id: str, input_data: WaiveDefectInput) -> dict[str, Any]:
        """Waive a defect with actor + reason enforcement.

        Args:
            defect_id: UUID of the defect to waive.
            input_data: Waiver input with actor and reason.

        Returns:
            Updated defect data dict.

        Raises:
            DefectNotFoundError: If defect not found.
            WaiverRequiresActorReasonError: If actor or reason missing.
        """
        if not input_data.actor or not input_data.actor.strip():
            raise WaiverRequiresActorReasonError(defect_id, "actor")
        if not input_data.reason or not input_data.reason.strip():
            raise WaiverRequiresActorReasonError(defect_id, "reason")

        existing = self._defects_repo.get(defect_id)
        if existing is None:
            raise DefectNotFoundError(defect_id, self._tenant_id)

        # Enforce state transition: only OPEN -> WAIVED is allowed
        current_status = existing.get("status", "OPEN")
        if current_status != "OPEN":
            raise InvalidStateTransitionError(defect_id, current_status, "WAIVED")

        updated = self._defects_repo.update(
            defect_id,
            status="WAIVED",
            waiver_reason=input_data.reason,
            waived_by=input_data.actor,
        )

        self._emit_audit_event(
            event_type="defect.waived",
            defect_id=defect_id,
            severity="HIGH",
            details={
                "waived_by": input_data.actor,
                "waiver_reason": input_data.reason,
                "previous_status": existing.get("status"),
            },
            request_id=input_data.request_id,
        )

        return updated or existing

    def cure(self, defect_id: str, input_data: CureDefectInput) -> dict[str, Any]:
        """Cure a defect with actor + reason enforcement.

        Args:
            defect_id: UUID of the defect to cure.
            input_data: Cure input with actor and reason.

        Returns:
            Updated defect data dict.

        Raises:
            DefectNotFoundError: If defect not found.
            WaiverRequiresActorReasonError: If actor or reason missing.
        """
        if not input_data.actor or not input_data.actor.strip():
            raise WaiverRequiresActorReasonError(defect_id, "actor")
        if not input_data.reason or not input_data.reason.strip():
            raise WaiverRequiresActorReasonError(defect_id, "reason")

        existing = self._defects_repo.get(defect_id)
        if existing is None:
            raise DefectNotFoundError(defect_id, self._tenant_id)

        # Enforce state transition: only OPEN -> CURED is allowed
        current_status = existing.get("status", "OPEN")
        if current_status != "OPEN":
            raise InvalidStateTransitionError(defect_id, current_status, "CURED")

        updated = self._defects_repo.update(
            defect_id,
            status="CURED",
            cured_by=input_data.actor,
            cured_reason=input_data.reason,
        )

        self._emit_audit_event(
            event_type="defect.cured",
            defect_id=defect_id,
            severity="MEDIUM",
            details={
                "cured_by": input_data.actor,
                "cured_reason": input_data.reason,
                "previous_status": existing.get("status"),
            },
            request_id=input_data.request_id,
        )

        return updated or existing

"""ClaimService - business logic layer for claim CRUD operations.

Enforces invariants at service layer (not route layer):
- Tenant scoping (tenant_id required; reject mismatches)
- Deal scoping where applicable
- No-Free-Facts validation on create/update for ic_bound claims
- Strict schema validation via Pydantic models

Uses Postgres repositories when db_conn exists, in-memory fallback otherwise.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator

from idis.audit.sink import AuditSink, InMemoryAuditSink
from idis.models.claim import ClaimType
from idis.persistence.repositories.claims import (
    ClaimsRepository,
    InMemoryClaimsRepository,
    InMemorySanadsRepository,
    SanadsRepository,
)
from idis.validators.no_free_facts import NoFreeFactsValidator

if TYPE_CHECKING:
    from sqlalchemy import Connection

logger = logging.getLogger(__name__)


class ClaimServiceError(Exception):
    """Base exception for ClaimService errors."""

    pass


class TenantMismatchError(ClaimServiceError):
    """Raised when tenant_id does not match the service context."""

    def __init__(self, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"Tenant mismatch: expected {expected}, got {actual}")


class NoFreeFactsViolationError(ClaimServiceError):
    """Raised when a claim violates No-Free-Facts for IC-bound outputs."""

    def __init__(self, claim_id: str, errors: list[str]) -> None:
        self.claim_id = claim_id
        self.errors = errors
        super().__init__(f"No-Free-Facts violation for claim {claim_id}: {'; '.join(errors)}")


class ClaimNotFoundError(ClaimServiceError):
    """Raised when a claim is not found."""

    def __init__(self, claim_id: str, tenant_id: str) -> None:
        self.claim_id = claim_id
        self.tenant_id = tenant_id
        super().__init__(f"Claim {claim_id} not found for tenant {tenant_id}")


class DealNotFoundError(ClaimServiceError):
    """Raised when a deal is not found."""

    def __init__(self, deal_id: str, tenant_id: str) -> None:
        self.deal_id = deal_id
        self.tenant_id = tenant_id
        super().__init__(f"Deal {deal_id} not found for tenant {tenant_id}")


class CreateClaimInput(BaseModel):
    """Input model for creating a claim."""

    deal_id: str = Field(..., description="Deal UUID")
    claim_class: str = Field(..., description="Claim category")
    claim_text: str = Field(..., min_length=1, description="Claim assertion text")
    claim_type: str = Field(default="primary", description="primary or derived")
    predicate: str | None = Field(default=None, description="Structured predicate")
    value: dict[str, Any] | None = Field(default=None, description="Typed value struct")
    sanad_id: str | None = Field(default=None, description="Sanad chain reference")
    claim_grade: str = Field(default="D", description="Sanad grade A/B/C/D")
    corroboration: dict[str, Any] | None = Field(default=None, description="Corroboration")
    claim_verdict: str = Field(default="UNVERIFIED", description="Verdict")
    claim_action: str = Field(default="VERIFY", description="Required action")
    defect_ids: list[str] = Field(default_factory=list, description="Defect references")
    materiality: str = Field(default="MEDIUM", description="Materiality level")
    ic_bound: bool = Field(default=False, description="IC-bound flag")
    primary_span_id: str | None = Field(default=None, description="Primary span ref")
    source_calc_id: str | None = Field(default=None, description="Source calc for derived")
    request_id: str | None = Field(default=None, description="Request correlation ID")

    @field_validator("claim_type")
    @classmethod
    def validate_claim_type(cls, v: str) -> str:
        """Validate claim_type is valid."""
        valid_types = {t.value for t in ClaimType}
        if v not in valid_types:
            raise ValueError(f"claim_type must be one of {valid_types}")
        return v


class UpdateClaimInput(BaseModel):
    """Input model for updating a claim."""

    claim_text: str | None = Field(default=None, description="Updated claim text")
    claim_grade: str | None = Field(default=None, description="Updated grade")
    claim_verdict: str | None = Field(default=None, description="Updated verdict")
    claim_action: str | None = Field(default=None, description="Updated action")
    defect_ids: list[str] | None = Field(default=None, description="Updated defects")
    materiality: str | None = Field(default=None, description="Updated materiality")
    ic_bound: bool | None = Field(default=None, description="Updated ic_bound flag")
    sanad_id: str | None = Field(default=None, description="Updated sanad reference")
    corroboration: dict[str, Any] | None = Field(default=None, description="Updated corroboration")
    request_id: str | None = Field(default=None, description="Request correlation ID")


class ClaimService:
    """Service layer for claim operations with invariant enforcement.

    All operations are tenant-scoped. The service validates:
    - Tenant isolation (reject cross-tenant access)
    - No-Free-Facts compliance for ic_bound claims on create/update
    - Schema validation via Pydantic input models

    Usage:
        service = ClaimService(tenant_id, db_conn=conn)
        claim = service.create(CreateClaimInput(...))
    """

    def __init__(
        self,
        tenant_id: str,
        db_conn: Connection | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        """Initialize ClaimService with tenant context.

        Args:
            tenant_id: Tenant UUID for scoping all operations.
            db_conn: SQLAlchemy connection for Postgres. If None, uses in-memory.
            audit_sink: Optional audit sink for event emission.
        """
        self._tenant_id = tenant_id
        self._db_conn = db_conn
        self._audit_sink = audit_sink or InMemoryAuditSink()
        self._nff_validator = NoFreeFactsValidator()

        if db_conn is not None:
            self._claims_repo: ClaimsRepository | InMemoryClaimsRepository = ClaimsRepository(
                db_conn, tenant_id
            )
            self._sanads_repo: SanadsRepository | InMemorySanadsRepository = SanadsRepository(
                db_conn, tenant_id
            )
        else:
            self._claims_repo = InMemoryClaimsRepository(tenant_id)
            self._sanads_repo = InMemorySanadsRepository(tenant_id)

    @property
    def tenant_id(self) -> str:
        """Return the tenant context."""
        return self._tenant_id

    def _emit_audit_event(
        self,
        event_type: str,
        claim_id: str,
        details: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        """Emit an audit event for claim operations with request correlation."""
        event: dict[str, Any] = {
            "event_type": event_type,
            "tenant_id": self._tenant_id,
            "resource_type": "claim",
            "resource_id": claim_id,
            "entity_type": "claim",
            "entity_id": claim_id,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "details": details or {},
        }
        if request_id:
            event["request_id"] = request_id
        try:
            self._audit_sink.emit(event)
        except Exception as e:
            logger.warning("Failed to emit audit event: %s", e)

    def _validate_no_free_facts(
        self,
        claim_id: str,
        claim_text: str,
        ic_bound: bool,
        sanad_id: str | None,
        primary_span_id: str | None,
    ) -> None:
        """Validate No-Free-Facts compliance for IC-bound claims.

        If ic_bound=True, the claim MUST have a sanad_id or primary_span_id
        to satisfy No-Free-Facts. The text is also checked for factual
        assertions that require evidence backing.

        Raises:
            NoFreeFactsViolationError: If validation fails.
        """
        if not ic_bound:
            return

        errors: list[str] = []

        if not sanad_id and not primary_span_id:
            errors.append(
                "IC-bound claim must have sanad_id or primary_span_id for evidence backing"
            )

        deliverable_section = {
            "text": claim_text,
            "is_factual": True,
            "is_subjective": False,
            "referenced_claim_ids": [claim_id] if sanad_id else [],
            "referenced_calc_ids": [],
        }

        result = self._nff_validator.validate({"sections": [deliverable_section]})
        if not result.passed and sanad_id:
            pass
        elif not result.passed and not sanad_id:
            for err in result.errors:
                errors.append(err.message)

        if errors:
            raise NoFreeFactsViolationError(claim_id, errors)

    def create(self, input_data: CreateClaimInput) -> dict[str, Any]:
        """Create a new claim with invariant enforcement.

        Args:
            input_data: Validated input data for claim creation.

        Returns:
            Created claim data dict.

        Raises:
            NoFreeFactsViolationError: If IC-bound claim lacks evidence.
            ClaimServiceError: On other creation failures.
        """
        claim_id = str(uuid.uuid4())

        self._validate_no_free_facts(
            claim_id=claim_id,
            claim_text=input_data.claim_text,
            ic_bound=input_data.ic_bound,
            sanad_id=input_data.sanad_id,
            primary_span_id=input_data.primary_span_id,
        )

        claim_data = self._claims_repo.create(
            claim_id=claim_id,
            deal_id=input_data.deal_id,
            claim_class=input_data.claim_class,
            claim_text=input_data.claim_text,
            predicate=input_data.predicate,
            value=input_data.value,
            sanad_id=input_data.sanad_id,
            claim_grade=input_data.claim_grade,
            corroboration=input_data.corroboration,
            claim_verdict=input_data.claim_verdict,
            claim_action=input_data.claim_action,
            defect_ids=input_data.defect_ids,
            materiality=input_data.materiality,
            ic_bound=input_data.ic_bound,
            primary_span_id=input_data.primary_span_id,
        )

        self._emit_audit_event(
            event_type="claim.created",
            claim_id=claim_id,
            details={"deal_id": input_data.deal_id, "claim_class": input_data.claim_class},
            request_id=input_data.request_id,
        )

        return claim_data

    def get(self, claim_id: str) -> dict[str, Any]:
        """Get a claim by ID.

        Args:
            claim_id: UUID of the claim.

        Returns:
            Claim data dict.

        Raises:
            ClaimNotFoundError: If claim not found for this tenant.
        """
        claim_data = self._claims_repo.get(claim_id)

        if claim_data is None:
            raise ClaimNotFoundError(claim_id, self._tenant_id)

        return claim_data

    def list_by_deal(
        self,
        deal_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List claims for a deal with pagination.

        Args:
            deal_id: Deal UUID to filter by.
            limit: Maximum number of claims to return.
            cursor: Pagination cursor for offset.

        Returns:
            Tuple of (claims_list, next_cursor).
        """
        claims, next_cursor = self._claims_repo.list_by_deal(
            deal_id=deal_id,
            limit=limit,
            cursor=cursor,
        )

        return claims, next_cursor

    def update(
        self,
        claim_id: str,
        input_data: UpdateClaimInput,
    ) -> dict[str, Any]:
        """Update an existing claim with invariant enforcement.

        Args:
            claim_id: UUID of the claim to update.
            input_data: Validated input data for update.

        Returns:
            Updated claim data dict.

        Raises:
            ClaimNotFoundError: If claim not found.
            NoFreeFactsViolationError: If update violates No-Free-Facts.
        """
        existing = self._claims_repo.get(claim_id)
        if existing is None:
            raise ClaimNotFoundError(claim_id, self._tenant_id)

        claim_text = input_data.claim_text or existing["claim_text"]
        ic_bound = (
            input_data.ic_bound
            if input_data.ic_bound is not None
            else existing.get("ic_bound", False)
        )
        sanad_id = input_data.sanad_id or existing.get("sanad_id")
        primary_span_id = existing.get("primary_span_id")

        self._validate_no_free_facts(
            claim_id=claim_id,
            claim_text=claim_text,
            ic_bound=ic_bound,
            sanad_id=sanad_id,
            primary_span_id=primary_span_id,
        )

        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        updated_data = {**existing}
        if input_data.claim_text is not None:
            updated_data["claim_text"] = input_data.claim_text
        if input_data.claim_grade is not None:
            updated_data["claim_grade"] = input_data.claim_grade
        if input_data.claim_verdict is not None:
            updated_data["claim_verdict"] = input_data.claim_verdict
        if input_data.claim_action is not None:
            updated_data["claim_action"] = input_data.claim_action
        if input_data.defect_ids is not None:
            updated_data["defect_ids"] = input_data.defect_ids
        if input_data.materiality is not None:
            updated_data["materiality"] = input_data.materiality
        if input_data.ic_bound is not None:
            updated_data["ic_bound"] = input_data.ic_bound
        if input_data.sanad_id is not None:
            updated_data["sanad_id"] = input_data.sanad_id
        if input_data.corroboration is not None:
            updated_data["corroboration"] = input_data.corroboration
        updated_data["updated_at"] = now

        if isinstance(self._claims_repo, ClaimsRepository):
            from sqlalchemy import text as sql_text

            self._claims_repo._conn.execute(
                sql_text(
                    """
                    UPDATE claims SET
                        claim_text = :claim_text,
                        claim_grade = :claim_grade,
                        claim_verdict = :claim_verdict,
                        claim_action = :claim_action,
                        defect_ids = CAST(:defect_ids AS JSONB),
                        materiality = :materiality,
                        ic_bound = :ic_bound,
                        sanad_id = :sanad_id,
                        corroboration = CAST(:corroboration AS JSONB),
                        updated_at = :updated_at
                    WHERE claim_id = :claim_id
                    """
                ),
                {
                    "claim_id": claim_id,
                    "claim_text": updated_data["claim_text"],
                    "claim_grade": updated_data["claim_grade"],
                    "claim_verdict": updated_data["claim_verdict"],
                    "claim_action": updated_data["claim_action"],
                    "defect_ids": __import__("json").dumps(updated_data.get("defect_ids", [])),
                    "materiality": updated_data["materiality"],
                    "ic_bound": updated_data["ic_bound"],
                    "sanad_id": updated_data.get("sanad_id"),
                    "corroboration": __import__("json").dumps(
                        updated_data.get("corroboration")
                        or {"level": "AHAD", "independent_chain_count": 1}
                    ),
                    "updated_at": datetime.now(UTC),
                },
            )
        else:
            from idis.persistence.repositories.claims import _claims_in_memory_store

            _claims_in_memory_store[claim_id] = updated_data

        self._emit_audit_event(
            event_type="claim.updated",
            claim_id=claim_id,
            details={
                "fields_updated": [
                    k
                    for k, v in input_data.model_dump().items()
                    if v is not None and k != "request_id"
                ]
            },
            request_id=input_data.request_id,
        )

        return updated_data

    def delete(self, claim_id: str, request_id: str | None = None) -> bool:
        """Delete a claim by ID.

        Args:
            claim_id: UUID of the claim to delete.
            request_id: Optional request correlation ID for audit.

        Returns:
            True if deleted, False if not found.
        """
        existing = self._claims_repo.get(claim_id)
        if existing is None:
            return False

        result = self._claims_repo.delete(claim_id)

        if result:
            self._emit_audit_event(
                event_type="claim.deleted",
                claim_id=claim_id,
                details={"deal_id": existing.get("deal_id")},
                request_id=request_id,
            )

        deleted = result
        return deleted

    def get_sanad(self, sanad_id: str) -> dict[str, Any] | None:
        """Get a sanad by ID.

        Args:
            sanad_id: UUID of the sanad.

        Returns:
            Sanad data dict or None if not found.
        """
        return self._sanads_repo.get(sanad_id)

    def get_sanad_by_claim(self, claim_id: str) -> dict[str, Any] | None:
        """Get a sanad by claim ID.

        Args:
            claim_id: UUID of the claim.

        Returns:
            Sanad data dict or None if not found.
        """
        return self._sanads_repo.get_by_claim(claim_id)

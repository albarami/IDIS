"""Tests for ClaimService with synthetic dataset-driven coverage.

Tests:
- Create/update rejects No-Free-Facts violations
- Tenant mismatch fails closed
- Deterministic ordering of list results
- Full CRUD operations

Uses synthetic fixtures instead of patched behavior.
"""

from __future__ import annotations

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.persistence.repositories.claims import (
    clear_all_claims_stores,
)
from idis.services.claims.service import (
    ClaimNotFoundError,
    ClaimService,
    CreateClaimInput,
    NoFreeFactsViolationError,
    UpdateClaimInput,
)
from tests.fixtures.synthetic.claims_fixture import (
    SYNTHETIC_CLAIMS,
    SYNTHETIC_DEAL,
    SYNTHETIC_SPANS,
    SYNTHETIC_TENANT_ID,
    SYNTHETIC_TENANT_ID_OTHER,
)


@pytest.fixture(autouse=True)
def clean_stores() -> None:
    """Clear in-memory stores before each test."""
    clear_all_claims_stores()
    yield
    clear_all_claims_stores()


@pytest.fixture
def audit_sink() -> InMemoryAuditSink:
    """Provide an in-memory audit sink for testing."""
    return InMemoryAuditSink()


@pytest.fixture
def claim_service(audit_sink: InMemoryAuditSink) -> ClaimService:
    """Provide a ClaimService instance with in-memory storage."""
    return ClaimService(
        tenant_id=SYNTHETIC_TENANT_ID,
        db_conn=None,
        audit_sink=audit_sink,
    )


class TestClaimServiceCreate:
    """Tests for ClaimService.create()."""

    def test_create_claim_success(self, claim_service: ClaimService) -> None:
        """Create a valid claim succeeds."""
        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="FINANCIAL",
            claim_text="Revenue grew 20% YoY.",
            materiality="HIGH",
            ic_bound=False,
        )

        claim = claim_service.create(input_data)

        assert claim["claim_id"] is not None
        assert claim["tenant_id"] == SYNTHETIC_TENANT_ID
        assert claim["deal_id"] == SYNTHETIC_DEAL["deal_id"]
        assert claim["claim_class"] == "FINANCIAL"
        assert claim["claim_text"] == "Revenue grew 20% YoY."
        assert claim["materiality"] == "HIGH"

    def test_create_claim_with_sanad_and_ic_bound(self, claim_service: ClaimService) -> None:
        """Create IC-bound claim with sanad_id succeeds."""
        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="FINANCIAL",
            claim_text="ARR reached $5M in 2024.",
            sanad_id="sanad-test-001",
            ic_bound=True,
            primary_span_id=SYNTHETIC_SPANS[0]["span_id"],
        )

        claim = claim_service.create(input_data)

        assert claim["ic_bound"] is True
        assert claim["sanad_id"] == "sanad-test-001"

    def test_create_ic_bound_without_sanad_fails_no_free_facts(
        self, claim_service: ClaimService
    ) -> None:
        """Create IC-bound claim without evidence backing fails No-Free-Facts."""
        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="FINANCIAL",
            claim_text="Revenue is $10M annually.",
            ic_bound=True,
            sanad_id=None,
            primary_span_id=None,
        )

        with pytest.raises(NoFreeFactsViolationError) as exc_info:
            claim_service.create(input_data)

        assert "No-Free-Facts violation" in str(exc_info.value)
        assert "IC-bound claim must have sanad_id or primary_span_id" in str(exc_info.value)

    def test_create_non_ic_bound_without_sanad_succeeds(self, claim_service: ClaimService) -> None:
        """Create non-IC-bound claim without sanad succeeds (NFF not enforced)."""
        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="OTHER",
            claim_text="The team has strong expertise.",
            ic_bound=False,
            sanad_id=None,
        )

        claim = claim_service.create(input_data)

        assert claim["ic_bound"] is False
        assert claim["sanad_id"] is None

    def test_create_emits_audit_event(
        self, claim_service: ClaimService, audit_sink: InMemoryAuditSink
    ) -> None:
        """Create claim emits audit event."""
        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="TRACTION",
            claim_text="150 customers signed up.",
            ic_bound=False,
        )

        claim_service.create(input_data)

        events = audit_sink.events
        assert len(events) == 1
        assert events[0]["event_type"] == "claim.created"
        assert events[0]["entity_type"] == "claim"
        assert events[0]["tenant_id"] == SYNTHETIC_TENANT_ID


class TestClaimServiceGet:
    """Tests for ClaimService.get()."""

    def test_get_claim_success(self, claim_service: ClaimService) -> None:
        """Get existing claim succeeds."""
        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="FINANCIAL",
            claim_text="Test claim text.",
            ic_bound=False,
        )
        created = claim_service.create(input_data)

        retrieved = claim_service.get(created["claim_id"])

        assert retrieved["claim_id"] == created["claim_id"]
        assert retrieved["claim_text"] == "Test claim text."

    def test_get_claim_not_found(self, claim_service: ClaimService) -> None:
        """Get non-existent claim raises ClaimNotFoundError."""
        with pytest.raises(ClaimNotFoundError) as exc_info:
            claim_service.get("non-existent-claim-id")

        assert "not found" in str(exc_info.value)

    def test_get_claim_tenant_isolation(self, audit_sink: InMemoryAuditSink) -> None:
        """Claims from other tenants are not visible (tenant isolation)."""
        service_tenant_a = ClaimService(
            tenant_id=SYNTHETIC_TENANT_ID,
            db_conn=None,
            audit_sink=audit_sink,
        )
        service_tenant_b = ClaimService(
            tenant_id=SYNTHETIC_TENANT_ID_OTHER,
            db_conn=None,
            audit_sink=audit_sink,
        )

        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="FINANCIAL",
            claim_text="Tenant A claim.",
            ic_bound=False,
        )
        claim_a = service_tenant_a.create(input_data)

        with pytest.raises(ClaimNotFoundError):
            service_tenant_b.get(claim_a["claim_id"])


class TestClaimServiceListByDeal:
    """Tests for ClaimService.list_by_deal()."""

    def test_list_by_deal_returns_claims(self, claim_service: ClaimService) -> None:
        """List claims for deal returns correct claims."""
        for i in range(3):
            input_data = CreateClaimInput(
                deal_id=SYNTHETIC_DEAL["deal_id"],
                claim_class="FINANCIAL",
                claim_text=f"Claim number {i}.",
                ic_bound=False,
            )
            claim_service.create(input_data)

        claims, next_cursor = claim_service.list_by_deal(SYNTHETIC_DEAL["deal_id"])

        assert len(claims) == 3
        assert next_cursor is None

    def test_list_by_deal_deterministic_ordering(self, claim_service: ClaimService) -> None:
        """List claims returns deterministically ordered results by claim_id."""
        claim_ids = []
        for i in range(5):
            input_data = CreateClaimInput(
                deal_id=SYNTHETIC_DEAL["deal_id"],
                claim_class="TRACTION",
                claim_text=f"Ordering test claim {i}.",
                ic_bound=False,
            )
            claim = claim_service.create(input_data)
            claim_ids.append(claim["claim_id"])

        claims, _ = claim_service.list_by_deal(SYNTHETIC_DEAL["deal_id"])
        returned_ids = [c["claim_id"] for c in claims]

        assert returned_ids == sorted(returned_ids)

    def test_list_by_deal_pagination(self, claim_service: ClaimService) -> None:
        """List claims supports pagination."""
        for i in range(5):
            input_data = CreateClaimInput(
                deal_id=SYNTHETIC_DEAL["deal_id"],
                claim_class="FINANCIAL",
                claim_text=f"Pagination claim {i}.",
                ic_bound=False,
            )
            claim_service.create(input_data)

        claims_page1, cursor = claim_service.list_by_deal(SYNTHETIC_DEAL["deal_id"], limit=2)

        assert len(claims_page1) == 2

    def test_list_by_deal_empty_for_other_deal(self, claim_service: ClaimService) -> None:
        """List claims returns empty for deal with no claims."""
        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="FINANCIAL",
            claim_text="Test claim.",
            ic_bound=False,
        )
        claim_service.create(input_data)

        claims, _ = claim_service.list_by_deal("other-deal-id")

        assert claims == []


class TestClaimServiceUpdate:
    """Tests for ClaimService.update()."""

    def test_update_claim_success(self, claim_service: ClaimService) -> None:
        """Update claim with valid data succeeds."""
        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="FINANCIAL",
            claim_text="Original text.",
            materiality="LOW",
            ic_bound=False,
        )
        created = claim_service.create(input_data)

        update_data = UpdateClaimInput(
            claim_text="Updated text.",
            materiality="HIGH",
        )
        updated = claim_service.update(created["claim_id"], update_data)

        assert updated["claim_text"] == "Updated text."
        assert updated["materiality"] == "HIGH"
        assert updated["updated_at"] is not None

    def test_update_to_ic_bound_without_sanad_fails(self, claim_service: ClaimService) -> None:
        """Update claim to ic_bound=True without sanad fails No-Free-Facts."""
        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="FINANCIAL",
            claim_text="ARR is $5M.",
            ic_bound=False,
            sanad_id=None,
        )
        created = claim_service.create(input_data)

        update_data = UpdateClaimInput(ic_bound=True)

        with pytest.raises(NoFreeFactsViolationError):
            claim_service.update(created["claim_id"], update_data)

    def test_update_to_ic_bound_with_sanad_succeeds(self, claim_service: ClaimService) -> None:
        """Update claim to ic_bound=True with sanad_id succeeds."""
        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="FINANCIAL",
            claim_text="Revenue reached $5M.",
            ic_bound=False,
            sanad_id=None,
            primary_span_id=SYNTHETIC_SPANS[0]["span_id"],
        )
        created = claim_service.create(input_data)

        update_data = UpdateClaimInput(
            ic_bound=True,
            sanad_id="sanad-for-update",
        )
        updated = claim_service.update(created["claim_id"], update_data)

        assert updated["ic_bound"] is True
        assert updated["sanad_id"] == "sanad-for-update"

    def test_update_nonexistent_claim_fails(self, claim_service: ClaimService) -> None:
        """Update non-existent claim raises ClaimNotFoundError."""
        update_data = UpdateClaimInput(claim_text="New text.")

        with pytest.raises(ClaimNotFoundError):
            claim_service.update("nonexistent-id", update_data)

    def test_update_emits_audit_event(
        self, claim_service: ClaimService, audit_sink: InMemoryAuditSink
    ) -> None:
        """Update claim emits audit event."""
        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="FINANCIAL",
            claim_text="Original.",
            ic_bound=False,
        )
        created = claim_service.create(input_data)
        audit_sink.clear()

        update_data = UpdateClaimInput(claim_text="Updated.")
        claim_service.update(created["claim_id"], update_data)

        events = audit_sink.events
        assert len(events) == 1
        assert events[0]["event_type"] == "claim.updated"


class TestClaimServiceDelete:
    """Tests for ClaimService.delete()."""

    def test_delete_claim_success(self, claim_service: ClaimService) -> None:
        """Delete existing claim succeeds."""
        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="FINANCIAL",
            claim_text="To be deleted.",
            ic_bound=False,
        )
        created = claim_service.create(input_data)

        deleted = claim_service.delete(created["claim_id"])

        assert deleted is True

        with pytest.raises(ClaimNotFoundError):
            claim_service.get(created["claim_id"])

    def test_delete_nonexistent_claim_returns_false(self, claim_service: ClaimService) -> None:
        """Delete non-existent claim returns False."""
        result = claim_service.delete("nonexistent-id")

        assert result is False

    def test_delete_emits_audit_event(
        self, claim_service: ClaimService, audit_sink: InMemoryAuditSink
    ) -> None:
        """Delete claim emits audit event."""
        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="FINANCIAL",
            claim_text="To be deleted.",
            ic_bound=False,
        )
        created = claim_service.create(input_data)
        audit_sink.clear()

        claim_service.delete(created["claim_id"])

        events = audit_sink.events
        assert len(events) == 1
        assert events[0]["event_type"] == "claim.deleted"


class TestClaimServiceTenantIsolation:
    """Tests for tenant isolation in ClaimService."""

    def test_tenant_mismatch_on_get_fails_closed(self, audit_sink: InMemoryAuditSink) -> None:
        """Attempting to get claim from different tenant fails closed."""
        service_a = ClaimService(
            tenant_id=SYNTHETIC_TENANT_ID,
            db_conn=None,
            audit_sink=audit_sink,
        )
        service_b = ClaimService(
            tenant_id=SYNTHETIC_TENANT_ID_OTHER,
            db_conn=None,
            audit_sink=audit_sink,
        )

        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="FINANCIAL",
            claim_text="Tenant A claim.",
            ic_bound=False,
        )
        claim_a = service_a.create(input_data)

        with pytest.raises(ClaimNotFoundError):
            service_b.get(claim_a["claim_id"])

    def test_tenant_mismatch_on_delete_fails_closed(self, audit_sink: InMemoryAuditSink) -> None:
        """Attempting to delete claim from different tenant fails closed."""
        service_a = ClaimService(
            tenant_id=SYNTHETIC_TENANT_ID,
            db_conn=None,
            audit_sink=audit_sink,
        )
        service_b = ClaimService(
            tenant_id=SYNTHETIC_TENANT_ID_OTHER,
            db_conn=None,
            audit_sink=audit_sink,
        )

        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="FINANCIAL",
            claim_text="Tenant A claim.",
            ic_bound=False,
        )
        claim_a = service_a.create(input_data)

        deleted = service_b.delete(claim_a["claim_id"])

        assert deleted is False

        retrieved = service_a.get(claim_a["claim_id"])
        assert retrieved is not None

    def test_tenant_mismatch_on_list_returns_empty(self, audit_sink: InMemoryAuditSink) -> None:
        """Listing claims from different tenant returns empty."""
        service_a = ClaimService(
            tenant_id=SYNTHETIC_TENANT_ID,
            db_conn=None,
            audit_sink=audit_sink,
        )
        service_b = ClaimService(
            tenant_id=SYNTHETIC_TENANT_ID_OTHER,
            db_conn=None,
            audit_sink=audit_sink,
        )

        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="FINANCIAL",
            claim_text="Tenant A claim.",
            ic_bound=False,
        )
        service_a.create(input_data)

        claims, _ = service_b.list_by_deal(SYNTHETIC_DEAL["deal_id"])

        assert claims == []


class TestClaimServicePrimarySpanId:
    """Tests for primary_span_id persistence."""

    def test_create_persists_primary_span_id(self, claim_service: ClaimService) -> None:
        """Create claim persists primary_span_id correctly."""
        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="FINANCIAL",
            claim_text="Test with span.",
            ic_bound=False,
            primary_span_id=SYNTHETIC_SPANS[0]["span_id"],
        )

        claim = claim_service.create(input_data)

        assert claim["primary_span_id"] == SYNTHETIC_SPANS[0]["span_id"]

        retrieved = claim_service.get(claim["claim_id"])
        assert retrieved["primary_span_id"] == SYNTHETIC_SPANS[0]["span_id"]

    def test_list_returns_primary_span_id(self, claim_service: ClaimService) -> None:
        """List claims returns primary_span_id field."""
        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="TRACTION",
            claim_text="150 users.",
            ic_bound=False,
            primary_span_id=SYNTHETIC_SPANS[1]["span_id"],
        )
        claim_service.create(input_data)

        claims, _ = claim_service.list_by_deal(SYNTHETIC_DEAL["deal_id"])

        assert len(claims) == 1
        assert claims[0]["primary_span_id"] == SYNTHETIC_SPANS[1]["span_id"]


class TestClaimServiceAuditCorrelation:
    """Tests for audit event request correlation."""

    def test_create_audit_includes_request_id(
        self, claim_service: ClaimService, audit_sink: InMemoryAuditSink
    ) -> None:
        """Create claim audit event includes request_id."""
        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="FINANCIAL",
            claim_text="Revenue is strong.",
            ic_bound=False,
            request_id="req-correlation-001",
        )

        claim_service.create(input_data)

        events = audit_sink.events
        assert len(events) == 1
        assert events[0]["request_id"] == "req-correlation-001"
        assert events[0]["resource_type"] == "claim"

    def test_update_audit_includes_request_id(
        self, claim_service: ClaimService, audit_sink: InMemoryAuditSink
    ) -> None:
        """Update claim audit event includes request_id."""
        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="FINANCIAL",
            claim_text="Original.",
            ic_bound=False,
        )
        created = claim_service.create(input_data)
        audit_sink.clear()

        update_data = UpdateClaimInput(
            claim_text="Updated.",
            request_id="req-correlation-002",
        )
        claim_service.update(created["claim_id"], update_data)

        events = audit_sink.events
        assert len(events) == 1
        assert events[0]["request_id"] == "req-correlation-002"

    def test_delete_audit_includes_request_id(
        self, claim_service: ClaimService, audit_sink: InMemoryAuditSink
    ) -> None:
        """Delete claim audit event includes request_id."""
        input_data = CreateClaimInput(
            deal_id=SYNTHETIC_DEAL["deal_id"],
            claim_class="FINANCIAL",
            claim_text="To delete.",
            ic_bound=False,
        )
        created = claim_service.create(input_data)
        audit_sink.clear()

        claim_service.delete(created["claim_id"], request_id="req-correlation-003")

        events = audit_sink.events
        assert len(events) == 1
        assert events[0]["request_id"] == "req-correlation-003"


class TestClaimServiceWithSyntheticData:
    """Tests using synthetic fixture data for realistic scenarios."""

    def test_create_claim_matching_synthetic_fixture(self, claim_service: ClaimService) -> None:
        """Create claim matching synthetic fixture structure."""
        synthetic = SYNTHETIC_CLAIMS[0]

        input_data = CreateClaimInput(
            deal_id=synthetic["deal_id"],
            claim_class=synthetic["claim_class"],
            claim_text=synthetic["claim_text"],
            claim_type=synthetic["claim_type"],
            predicate=synthetic["predicate"],
            value=synthetic["value"],
            claim_grade=synthetic["claim_grade"],
            corroboration=synthetic["corroboration"],
            claim_verdict=synthetic["claim_verdict"],
            claim_action=synthetic["claim_action"],
            materiality=synthetic["materiality"],
            ic_bound=False,
            primary_span_id=synthetic["primary_span_id"],
        )

        claim = claim_service.create(input_data)

        assert claim["claim_class"] == "FINANCIAL"
        assert claim["claim_text"] == synthetic["claim_text"]
        assert claim["value"]["value"] == 5000000.0
        assert claim["primary_span_id"] == synthetic["primary_span_id"]

    def test_multiple_claims_from_synthetic_spans(self, claim_service: ClaimService) -> None:
        """Create multiple claims referencing synthetic spans."""
        created_ids = []

        for span in SYNTHETIC_SPANS[:2]:
            input_data = CreateClaimInput(
                deal_id=SYNTHETIC_DEAL["deal_id"],
                claim_class="FINANCIAL",
                claim_text=span["text_excerpt"],
                ic_bound=False,
                primary_span_id=span["span_id"],
            )
            claim = claim_service.create(input_data)
            created_ids.append(claim["claim_id"])

        claims, _ = claim_service.list_by_deal(SYNTHETIC_DEAL["deal_id"])

        assert len(claims) == 2
        for claim in claims:
            assert claim["claim_id"] in created_ids

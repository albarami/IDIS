"""Defect waiver tests for DEF-001 traceability.

Tests waiver workflow requirements:
- Waiver requires actor + reason
- Waiver emits defect.waived audit event (HIGH severity)
"""

from __future__ import annotations

import uuid

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.persistence.repositories.claims import clear_all_claims_stores
from idis.services.defects.service import (
    CreateDefectInput,
    DefectNotFoundError,
    DefectService,
    WaiveDefectInput,
)


@pytest.fixture(autouse=True)
def clear_stores() -> None:
    """Clear in-memory stores before each test."""
    clear_all_claims_stores()


@pytest.fixture
def audit_sink() -> InMemoryAuditSink:
    """Provide in-memory audit sink."""
    return InMemoryAuditSink()


class TestDefectWaiverWorkflow:
    """Tests for DEF-001 waiver workflow."""

    def test_waiver_requires_actor(self, audit_sink: InMemoryAuditSink) -> None:
        """Waiver fails without actor - Pydantic rejects empty string."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            WaiveDefectInput(actor="", reason="Some reason")

        assert "actor" in str(exc_info.value)

    def test_waiver_requires_reason(self, audit_sink: InMemoryAuditSink) -> None:
        """Waiver fails without reason - Pydantic rejects empty string."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            WaiveDefectInput(actor="partner@example.com", reason="")

        assert "reason" in str(exc_info.value)

    def test_waiver_succeeds_with_actor_and_reason(self, audit_sink: InMemoryAuditSink) -> None:
        """Waiver succeeds with both actor and reason."""
        tenant_id = str(uuid.uuid4())
        service = DefectService(tenant_id=tenant_id, audit_sink=audit_sink)

        create_input = CreateDefectInput(
            defect_type="INCONSISTENCY",
            description="Test defect",
            cure_protocol="HUMAN_ARBITRATION",
        )
        defect = service.create(create_input)

        waived = service.waive(
            defect["defect_id"],
            WaiveDefectInput(
                actor="partner@example.com",
                reason="Accepted business risk per IC discussion",
            ),
        )

        assert waived["status"] == "WAIVED"
        assert waived["waived"] is True
        assert waived["waived_by"] == "partner@example.com"
        assert waived["waiver_reason"] == "Accepted business risk per IC discussion"
        assert waived["waived_at"] is not None

    def test_waiver_emits_audit_event(self, audit_sink: InMemoryAuditSink) -> None:
        """Waiver emits defect.waived audit event."""
        tenant_id = str(uuid.uuid4())
        service = DefectService(tenant_id=tenant_id, audit_sink=audit_sink)

        create_input = CreateDefectInput(
            defect_type="INCONSISTENCY",
            description="Test defect",
            cure_protocol="HUMAN_ARBITRATION",
        )
        defect = service.create(create_input)

        initial_events = len(audit_sink.events)

        service.waive(
            defect["defect_id"],
            WaiveDefectInput(
                actor="partner@example.com",
                reason="Accepted risk",
            ),
        )

        waiver_events = [
            e for e in audit_sink.events[initial_events:] if e.get("event_type") == "defect.waived"
        ]
        assert len(waiver_events) == 1

        event = waiver_events[0]
        assert event["severity"] == "HIGH"
        assert event["resource"]["resource_type"] == "defect"

    def test_waiver_fails_for_nonexistent_defect(self, audit_sink: InMemoryAuditSink) -> None:
        """Waiver raises DefectNotFoundError for nonexistent defect."""
        tenant_id = str(uuid.uuid4())
        service = DefectService(tenant_id=tenant_id, audit_sink=audit_sink)

        with pytest.raises(DefectNotFoundError):
            service.waive(
                str(uuid.uuid4()),
                WaiveDefectInput(actor="partner@example.com", reason="Test"),
            )

    def test_waiver_fails_for_cross_tenant_defect(self, audit_sink: InMemoryAuditSink) -> None:
        """Waiver fails for defect belonging to different tenant."""
        tenant_a = str(uuid.uuid4())
        tenant_b = str(uuid.uuid4())

        service_a = DefectService(tenant_id=tenant_a, audit_sink=audit_sink)
        service_b = DefectService(tenant_id=tenant_b, audit_sink=audit_sink)

        create_input = CreateDefectInput(
            defect_type="INCONSISTENCY",
            description="Test defect",
            cure_protocol="HUMAN_ARBITRATION",
        )
        defect = service_a.create(create_input)

        with pytest.raises(DefectNotFoundError):
            service_b.waive(
                defect["defect_id"],
                WaiveDefectInput(actor="attacker@evil.com", reason="Bypass"),
            )

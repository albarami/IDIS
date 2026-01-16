"""Defect cure protocol tests for DEF-001 traceability.

Tests cure workflow requirements:
- Cure requires actor + reason
- Cure emits defect.cured audit event
"""

from __future__ import annotations

import uuid

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.persistence.repositories.claims import clear_all_claims_stores
from idis.services.defects.service import (
    CreateDefectInput,
    CureDefectInput,
    DefectNotFoundError,
    DefectService,
)


@pytest.fixture(autouse=True)
def clear_stores() -> None:
    """Clear in-memory stores before each test."""
    clear_all_claims_stores()


@pytest.fixture
def audit_sink() -> InMemoryAuditSink:
    """Provide in-memory audit sink."""
    return InMemoryAuditSink()


class TestDefectCureWorkflow:
    """Tests for DEF-001 cure workflow."""

    def test_cure_requires_actor(self, audit_sink: InMemoryAuditSink) -> None:
        """Cure fails without actor - Pydantic rejects empty string."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            CureDefectInput(actor="", reason="Got updated data")

        assert "actor" in str(exc_info.value)

    def test_cure_requires_reason(self, audit_sink: InMemoryAuditSink) -> None:
        """Cure fails without reason - Pydantic rejects empty string."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            CureDefectInput(actor="analyst@example.com", reason="")

        assert "reason" in str(exc_info.value)

    def test_cure_succeeds_with_actor_and_reason(self, audit_sink: InMemoryAuditSink) -> None:
        """Cure succeeds with both actor and reason."""
        tenant_id = str(uuid.uuid4())
        service = DefectService(tenant_id=tenant_id, audit_sink=audit_sink)

        create_input = CreateDefectInput(
            defect_type="STALENESS",
            description="Outdated data",
            cure_protocol="REQUEST_SOURCE",
        )
        defect = service.create(create_input)

        cured = service.cure(
            defect["defect_id"],
            CureDefectInput(
                actor="analyst@example.com",
                reason="Obtained updated financials from company CFO",
            ),
        )

        assert cured["status"] == "CURED"
        assert cured["cured_by"] == "analyst@example.com"
        assert cured["cured_reason"] == "Obtained updated financials from company CFO"
        assert cured["updated_at"] is not None

    def test_cure_emits_audit_event(self, audit_sink: InMemoryAuditSink) -> None:
        """Cure emits defect.cured audit event."""
        tenant_id = str(uuid.uuid4())
        service = DefectService(tenant_id=tenant_id, audit_sink=audit_sink)

        create_input = CreateDefectInput(
            defect_type="STALENESS",
            description="Outdated data",
            cure_protocol="REQUEST_SOURCE",
        )
        defect = service.create(create_input)

        initial_events = len(audit_sink.events)

        service.cure(
            defect["defect_id"],
            CureDefectInput(
                actor="analyst@example.com",
                reason="Fixed issue",
            ),
        )

        cure_events = [
            e for e in audit_sink.events[initial_events:] if e.get("event_type") == "defect.cured"
        ]
        assert len(cure_events) == 1

        event = cure_events[0]
        assert event["severity"] == "MEDIUM"
        assert event["resource"]["resource_type"] == "defect"

    def test_cure_fails_for_nonexistent_defect(self, audit_sink: InMemoryAuditSink) -> None:
        """Cure raises DefectNotFoundError for nonexistent defect."""
        tenant_id = str(uuid.uuid4())
        service = DefectService(tenant_id=tenant_id, audit_sink=audit_sink)

        with pytest.raises(DefectNotFoundError):
            service.cure(
                str(uuid.uuid4()),
                CureDefectInput(actor="analyst@example.com", reason="Test"),
            )


class TestCureProtocolTypes:
    """Tests for cure protocol handling."""

    def test_cure_protocol_stored(self, audit_sink: InMemoryAuditSink) -> None:
        """Cure protocol is correctly stored on defect."""
        tenant_id = str(uuid.uuid4())
        service = DefectService(tenant_id=tenant_id, audit_sink=audit_sink)

        protocols = [
            "REQUEST_SOURCE",
            "REQUIRE_REAUDIT",
            "HUMAN_ARBITRATION",
            "RECONSTRUCT_CHAIN",
            "DISCARD_CLAIM",
        ]

        for protocol in protocols:
            create_input = CreateDefectInput(
                defect_type="INCONSISTENCY",
                description=f"Test defect for {protocol}",
                cure_protocol=protocol,
            )
            defect = service.create(create_input)
            assert defect["cure_protocol"] == protocol

    def test_defect_status_transitions(self, audit_sink: InMemoryAuditSink) -> None:
        """Defect status transitions correctly through workflow."""
        tenant_id = str(uuid.uuid4())
        service = DefectService(tenant_id=tenant_id, audit_sink=audit_sink)

        create_input = CreateDefectInput(
            defect_type="STALENESS",
            description="Test defect",
            cure_protocol="REQUEST_SOURCE",
        )
        defect = service.create(create_input)
        assert defect["status"] == "OPEN"

        cured = service.cure(
            defect["defect_id"],
            CureDefectInput(actor="analyst", reason="Fixed"),
        )
        assert cured["status"] == "CURED"

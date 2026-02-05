"""Tests for retention and legal hold enforcement (v6.3 Task 7.5).

Requirements (per Data Residency Model v6.3 §6):
- Legal hold apply/lift emits CRITICAL audit events
- Held items cannot be deleted until hold is lifted
- All hold actions audited with CRITICAL severity
- Hold reason content never logged raw (hash/length only)

Test strategy:
- Unit tests for retention.py primitives
- Audit emission failure tests
- Legal hold blocking deletion tests
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from idis.api.auth import TenantContext
from idis.api.errors import IdisHttpError
from idis.compliance.retention import (
    HoldTarget,
    LegalHoldRegistry,
    RetentionClass,
    RetentionPolicy,
    apply_hold,
    block_deletion_if_held,
    evaluate_retention,
    lift_hold,
)


class MockAuditSink:
    """Mock audit sink for testing."""

    def __init__(self, should_fail: bool = False) -> None:
        self.events: list[dict[str, Any]] = []
        self.should_fail = should_fail

    def emit(self, event: dict[str, Any]) -> None:
        if self.should_fail:
            raise RuntimeError("Audit sink failure")
        self.events.append(event)


def make_tenant_ctx(tenant_id: str = "tenant-123") -> TenantContext:
    """Create a TenantContext for testing."""
    return TenantContext(
        tenant_id=tenant_id,
        actor_id="actor-1",
        name="Test Tenant",
        timezone="UTC",
        data_region="me-south-1",
    )


class TestApplyHold:
    """Tests for apply_hold()."""

    def test_creates_active_hold(self) -> None:
        """Applied hold is active."""
        registry = LegalHoldRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        hold = apply_hold(ctx, HoldTarget.DEAL, "deal-123", "Legal investigation", sink, registry)

        assert hold.is_active
        assert hold.target_type == HoldTarget.DEAL
        assert hold.target_id == "deal-123"
        assert hold.tenant_id == ctx.tenant_id
        assert hold.applied_by == ctx.actor_id

    def test_emits_critical_audit_event(self) -> None:
        """Apply hold emits CRITICAL severity audit event."""
        registry = LegalHoldRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        apply_hold(ctx, HoldTarget.DEAL, "deal-123", "Legal reason", sink, registry)

        assert len(sink.events) == 1
        event = sink.events[0]
        assert event["event_type"] == "legal_hold.applied"
        assert event["severity"] == "CRITICAL"

    def test_hashes_reason_in_audit(self) -> None:
        """Audit event contains hashed reason, not raw reason."""
        registry = LegalHoldRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        apply_hold(
            ctx,
            HoldTarget.DEAL,
            "deal-123",
            "Confidential legal investigation details",
            sink,
            registry,
        )

        event = sink.events[0]
        payload = event["payload"]

        assert "Confidential legal investigation" not in str(event)
        assert "reason_hash" in payload
        assert "reason_length" in payload
        assert len(payload["reason_hash"]) == 64

    def test_audit_failure_blocks_hold(self) -> None:
        """Apply hold fails if audit emission fails."""
        registry = LegalHoldRegistry()
        sink = MockAuditSink(should_fail=True)
        ctx = make_tenant_ctx()

        with pytest.raises(IdisHttpError) as exc_info:
            apply_hold(ctx, HoldTarget.DEAL, "deal-123", "Reason", sink, registry)

        assert exc_info.value.status_code == 500
        assert exc_info.value.code == "HOLD_AUDIT_FAILED"
        assert not registry.has_active_hold(ctx.tenant_id, HoldTarget.DEAL, "deal-123")

    def test_requires_audit_sink(self) -> None:
        """Apply hold requires audit sink (fails closed)."""
        registry = LegalHoldRegistry()
        ctx = make_tenant_ctx()

        with pytest.raises(IdisHttpError) as exc_info:
            apply_hold(ctx, HoldTarget.DEAL, "deal-123", "Reason", None, registry)

        assert exc_info.value.code == "HOLD_AUDIT_REQUIRED"

    def test_rejects_empty_target_id(self) -> None:
        """Empty target_id is rejected."""
        registry = LegalHoldRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        with pytest.raises(IdisHttpError) as exc_info:
            apply_hold(ctx, HoldTarget.DEAL, "", "Reason", sink, registry)

        assert exc_info.value.status_code == 400
        assert exc_info.value.code == "HOLD_INVALID_TARGET"

    def test_rejects_empty_reason(self) -> None:
        """Empty reason is rejected."""
        registry = LegalHoldRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        with pytest.raises(IdisHttpError) as exc_info:
            apply_hold(ctx, HoldTarget.DEAL, "deal-123", "", sink, registry)

        assert exc_info.value.status_code == 400
        assert exc_info.value.code == "HOLD_INVALID_REASON"


class TestLiftHold:
    """Tests for lift_hold()."""

    def test_lifts_active_hold(self) -> None:
        """Lifting sets lifted_at and lifted_by."""
        registry = LegalHoldRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        hold = apply_hold(ctx, HoldTarget.DEAL, "deal-123", "Legal reason", sink, registry)
        lifted = lift_hold(ctx, hold.hold_id, sink, registry)

        assert not lifted.is_active
        assert lifted.lifted_at is not None
        assert lifted.lifted_by == ctx.actor_id

    def test_emits_critical_audit_event(self) -> None:
        """Lift hold emits CRITICAL severity audit event."""
        registry = LegalHoldRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        hold = apply_hold(ctx, HoldTarget.DEAL, "deal-123", "Legal reason", sink, registry)
        sink.events.clear()
        lift_hold(ctx, hold.hold_id, sink, registry)

        assert len(sink.events) == 1
        event = sink.events[0]
        assert event["event_type"] == "legal_hold.lifted"
        assert event["severity"] == "CRITICAL"

    def test_audit_failure_blocks_lift(self) -> None:
        """Lift hold fails if audit emission fails."""
        registry = LegalHoldRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        hold = apply_hold(ctx, HoldTarget.DEAL, "deal-123", "Legal reason", sink, registry)

        sink.should_fail = True
        with pytest.raises(IdisHttpError) as exc_info:
            lift_hold(ctx, hold.hold_id, sink, registry)

        assert exc_info.value.code == "HOLD_AUDIT_FAILED"
        stored_hold = registry.get(hold.hold_id)
        assert stored_hold.is_active

    def test_not_found_error(self) -> None:
        """Lift fails if hold not found."""
        registry = LegalHoldRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        with pytest.raises(IdisHttpError) as exc_info:
            lift_hold(ctx, "nonexistent-id", sink, registry)

        assert exc_info.value.status_code == 404
        assert exc_info.value.code == "HOLD_NOT_FOUND"

    def test_already_lifted_error(self) -> None:
        """Lift fails if hold already lifted."""
        registry = LegalHoldRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        hold = apply_hold(ctx, HoldTarget.DEAL, "deal-123", "Legal reason", sink, registry)
        lift_hold(ctx, hold.hold_id, sink, registry)

        with pytest.raises(IdisHttpError) as exc_info:
            lift_hold(ctx, hold.hold_id, sink, registry)

        assert exc_info.value.status_code == 400
        assert exc_info.value.code == "HOLD_ALREADY_LIFTED"

    def test_cross_tenant_denied(self) -> None:
        """Lift fails if different tenant."""
        registry = LegalHoldRegistry()
        sink = MockAuditSink()

        ctx1 = make_tenant_ctx("tenant-1")
        ctx2 = make_tenant_ctx("tenant-2")

        hold = apply_hold(ctx1, HoldTarget.DEAL, "deal-123", "Legal reason", sink, registry)

        with pytest.raises(IdisHttpError) as exc_info:
            lift_hold(ctx2, hold.hold_id, sink, registry)

        assert exc_info.value.status_code == 403
        assert exc_info.value.code == "ACCESS_DENIED"


class TestBlockDeletionIfHeld:
    """Tests for block_deletion_if_held()."""

    def test_allows_deletion_without_hold(self) -> None:
        """Deletion allowed when no hold exists."""
        registry = LegalHoldRegistry()
        ctx = make_tenant_ctx()

        block_deletion_if_held(ctx, HoldTarget.DEAL, "deal-123", registry)

    def test_blocks_deletion_with_active_hold(self) -> None:
        """Deletion blocked when active hold exists."""
        registry = LegalHoldRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        apply_hold(ctx, HoldTarget.DEAL, "deal-123", "Legal reason", sink, registry)

        with pytest.raises(IdisHttpError) as exc_info:
            block_deletion_if_held(ctx, HoldTarget.DEAL, "deal-123", registry)

        assert exc_info.value.status_code == 403
        assert exc_info.value.code == "DELETION_BLOCKED_BY_HOLD"

    def test_allows_deletion_after_hold_lifted(self) -> None:
        """Deletion allowed after hold is lifted."""
        registry = LegalHoldRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        hold = apply_hold(ctx, HoldTarget.DEAL, "deal-123", "Legal reason", sink, registry)
        lift_hold(ctx, hold.hold_id, sink, registry)

        block_deletion_if_held(ctx, HoldTarget.DEAL, "deal-123", registry)

    def test_blocks_with_multiple_holds(self) -> None:
        """Deletion blocked when any hold is active."""
        registry = LegalHoldRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        hold1 = apply_hold(ctx, HoldTarget.DEAL, "deal-123", "Reason 1", sink, registry)
        apply_hold(ctx, HoldTarget.DEAL, "deal-123", "Reason 2", sink, registry)

        lift_hold(ctx, hold1.hold_id, sink, registry)

        with pytest.raises(IdisHttpError) as exc_info:
            block_deletion_if_held(ctx, HoldTarget.DEAL, "deal-123", registry)

        assert exc_info.value.code == "DELETION_BLOCKED_BY_HOLD"

    def test_tenant_isolation(self) -> None:
        """Holds from other tenants don't block deletion."""
        registry = LegalHoldRegistry()
        sink = MockAuditSink()

        ctx1 = make_tenant_ctx("tenant-1")
        ctx2 = make_tenant_ctx("tenant-2")

        apply_hold(ctx1, HoldTarget.DEAL, "deal-123", "Reason", sink, registry)

        block_deletion_if_held(ctx2, HoldTarget.DEAL, "deal-123", registry)


class TestEvaluateRetention:
    """Tests for evaluate_retention()."""

    def test_within_retention_period(self) -> None:
        """Returns True when within retention period."""
        created_at = datetime.now(UTC)
        within, earliest = evaluate_retention(RetentionClass.DELIVERABLES, created_at)

        assert within is True
        assert earliest is not None

    def test_outside_retention_period(self) -> None:
        """Returns False when outside retention period."""
        created_at = datetime.now(UTC) - timedelta(days=3000)
        within, earliest = evaluate_retention(RetentionClass.DELIVERABLES, created_at)

        assert within is False

    def test_indefinite_retention(self) -> None:
        """Returns False, None for indefinite retention."""
        created_at = datetime.now(UTC) - timedelta(days=3650)
        within, earliest = evaluate_retention(RetentionClass.RAW_DOCUMENTS, created_at)

        assert within is False
        assert earliest is None

    def test_custom_policies(self) -> None:
        """Uses custom policies when provided."""
        policies = {
            RetentionClass.DELIVERABLES: RetentionPolicy(
                retention_class=RetentionClass.DELIVERABLES,
                retention_days=30,
            )
        }
        created_at = datetime.now(UTC) - timedelta(days=60)

        within, earliest = evaluate_retention(RetentionClass.DELIVERABLES, created_at, policies)

        assert within is False


class TestLegalHoldRegistry:
    """Tests for LegalHoldRegistry."""

    def test_get_by_id(self) -> None:
        """Get hold by ID."""
        registry = LegalHoldRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        hold = apply_hold(ctx, HoldTarget.DEAL, "deal-123", "Reason", sink, registry)

        retrieved = registry.get(hold.hold_id)
        assert retrieved is not None
        assert retrieved.hold_id == hold.hold_id

    def test_list_active_for_target(self) -> None:
        """List only active holds for target."""
        registry = LegalHoldRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        hold1 = apply_hold(ctx, HoldTarget.DEAL, "deal-123", "Reason 1", sink, registry)
        apply_hold(ctx, HoldTarget.DEAL, "deal-123", "Reason 2", sink, registry)
        lift_hold(ctx, hold1.hold_id, sink, registry)

        active = registry.list_active_for_target(ctx.tenant_id, HoldTarget.DEAL, "deal-123")

        assert len(active) == 1

    def test_has_active_hold(self) -> None:
        """Check for active hold."""
        registry = LegalHoldRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        assert not registry.has_active_hold(ctx.tenant_id, HoldTarget.DEAL, "deal-123")

        hold = apply_hold(ctx, HoldTarget.DEAL, "deal-123", "Reason", sink, registry)

        assert registry.has_active_hold(ctx.tenant_id, HoldTarget.DEAL, "deal-123")

        lift_hold(ctx, hold.hold_id, sink, registry)

        assert not registry.has_active_hold(ctx.tenant_id, HoldTarget.DEAL, "deal-123")

    def test_clear(self) -> None:
        """Clear removes all holds."""
        registry = LegalHoldRegistry()
        sink = MockAuditSink()
        ctx = make_tenant_ctx()

        hold = apply_hold(ctx, HoldTarget.DEAL, "deal-123", "Reason", sink, registry)
        registry.clear()

        assert registry.get(hold.hold_id) is None

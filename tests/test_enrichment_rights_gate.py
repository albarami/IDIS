"""Tests for enrichment rights-class gating.

Verifies:
- GREEN providers allowed without BYOL in all environments
- RED providers blocked in PROD without BYOL credentials
- RED providers allowed in DEV (personal-use tier)
- RED providers allowed in PROD with BYOL credentials
- YELLOW providers allowed with warning
- Audit event emitted on deny
- RightsGateError raised if audit emission fails on deny
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from idis.audit.sink import AuditSinkError, InMemoryAuditSink
from idis.services.enrichment.models import RightsClass
from idis.services.enrichment.rights_gate import (
    EnvironmentMode,
    RightsGateError,
    check_rights,
)

TENANT_ID = "tenant-test-001"
PROVIDER_ID = "test_provider"
REQUEST_ID = "req-001"


class TestGreenProvider:
    """GREEN providers should always be allowed."""

    def test_green_allowed_in_dev(self) -> None:
        sink = InMemoryAuditSink()
        decision = check_rights(
            rights_class=RightsClass.GREEN,
            provider_id=PROVIDER_ID,
            tenant_id=TENANT_ID,
            environment=EnvironmentMode.DEV,
            has_byol_credentials=False,
            audit_sink=sink,
            request_id=REQUEST_ID,
        )
        assert decision.allowed is True
        assert decision.rights_class == RightsClass.GREEN

    def test_green_allowed_in_prod(self) -> None:
        sink = InMemoryAuditSink()
        decision = check_rights(
            rights_class=RightsClass.GREEN,
            provider_id=PROVIDER_ID,
            tenant_id=TENANT_ID,
            environment=EnvironmentMode.PROD,
            has_byol_credentials=False,
            audit_sink=sink,
            request_id=REQUEST_ID,
        )
        assert decision.allowed is True

    def test_green_no_audit_events_emitted(self) -> None:
        sink = InMemoryAuditSink()
        check_rights(
            rights_class=RightsClass.GREEN,
            provider_id=PROVIDER_ID,
            tenant_id=TENANT_ID,
            environment=EnvironmentMode.PROD,
            has_byol_credentials=False,
            audit_sink=sink,
            request_id=REQUEST_ID,
        )
        assert len(sink.events) == 0


class TestRedProvider:
    """RED providers require BYOL in PROD, allowed in DEV."""

    def test_red_blocked_in_prod_without_byol(self) -> None:
        sink = InMemoryAuditSink()
        decision = check_rights(
            rights_class=RightsClass.RED,
            provider_id=PROVIDER_ID,
            tenant_id=TENANT_ID,
            environment=EnvironmentMode.PROD,
            has_byol_credentials=False,
            audit_sink=sink,
            request_id=REQUEST_ID,
        )
        assert decision.allowed is False
        assert decision.rights_class == RightsClass.RED

    def test_red_emits_audit_on_deny(self) -> None:
        sink = InMemoryAuditSink()
        check_rights(
            rights_class=RightsClass.RED,
            provider_id=PROVIDER_ID,
            tenant_id=TENANT_ID,
            environment=EnvironmentMode.PROD,
            has_byol_credentials=False,
            audit_sink=sink,
            request_id=REQUEST_ID,
        )
        assert len(sink.events) == 1
        event = sink.events[0]
        assert event["event_type"] == "enrichment.rights_denied"
        assert event["severity"] == "HIGH"
        assert event["tenant_id"] == TENANT_ID

    def test_red_allowed_in_dev(self) -> None:
        sink = InMemoryAuditSink()
        decision = check_rights(
            rights_class=RightsClass.RED,
            provider_id=PROVIDER_ID,
            tenant_id=TENANT_ID,
            environment=EnvironmentMode.DEV,
            has_byol_credentials=False,
            audit_sink=sink,
            request_id=REQUEST_ID,
        )
        assert decision.allowed is True

    def test_red_allowed_in_prod_with_byol(self) -> None:
        sink = InMemoryAuditSink()
        decision = check_rights(
            rights_class=RightsClass.RED,
            provider_id=PROVIDER_ID,
            tenant_id=TENANT_ID,
            environment=EnvironmentMode.PROD,
            has_byol_credentials=True,
            audit_sink=sink,
            request_id=REQUEST_ID,
        )
        assert decision.allowed is True


class TestYellowProvider:
    """YELLOW providers allowed with attribution requirements."""

    def test_yellow_allowed_in_dev(self) -> None:
        sink = InMemoryAuditSink()
        decision = check_rights(
            rights_class=RightsClass.YELLOW,
            provider_id=PROVIDER_ID,
            tenant_id=TENANT_ID,
            environment=EnvironmentMode.DEV,
            has_byol_credentials=False,
            audit_sink=sink,
            request_id=REQUEST_ID,
        )
        assert decision.allowed is True
        assert decision.rights_class == RightsClass.YELLOW

    def test_yellow_allowed_in_prod(self) -> None:
        sink = InMemoryAuditSink()
        decision = check_rights(
            rights_class=RightsClass.YELLOW,
            provider_id=PROVIDER_ID,
            tenant_id=TENANT_ID,
            environment=EnvironmentMode.PROD,
            has_byol_credentials=False,
            audit_sink=sink,
            request_id=REQUEST_ID,
        )
        assert decision.allowed is True


class TestAuditFailureFatal:
    """Audit emission failure must be fatal on deny."""

    def test_audit_failure_raises_rights_gate_error(self) -> None:
        broken_sink = MagicMock()
        broken_sink.emit.side_effect = AuditSinkError("disk full")

        with pytest.raises(RightsGateError) as exc_info:
            check_rights(
                rights_class=RightsClass.RED,
                provider_id=PROVIDER_ID,
                tenant_id=TENANT_ID,
                environment=EnvironmentMode.PROD,
                has_byol_credentials=False,
                audit_sink=broken_sink,
                request_id=REQUEST_ID,
            )
        assert "audit emission failed" in str(exc_info.value)

"""Slice98 Task 6 audit-core repair - BYOK/legal-hold core domain events must be schema-valid.

Scoped RED-first repair (post-Task-8 acceptance). The accepted Task 6 core emitters built audit
events with ``method="INTERNAL"`` (the validator allows only GET/POST/PUT/PATCH/DELETE) and placed
non-safe fields at the top level of ``payload`` (the schema's payload is additionalProperties:false
with only safe/hashes/refs), and they never validated before emitting. This brings all five core
domain events to the Task 8 standard:

- schema-valid ``POST`` with an ``/internal/...`` path and a payload shaped as {safe, hashes, refs};
- ``validate_audit_event()`` BEFORE ``audit_sink.emit`` so a validation failure fails closed
  BEFORE the state change (the registry write) - the Task 7 janitor / Task 8 precedent.

Covers byok.key.configured / rotated / revoked and legal_hold.applied / lifted. PYTHONPATH is
pinned to this worktree's src for every run.
"""

from __future__ import annotations

from typing import Any

import pytest

from idis.api.auth import TenantContext
from idis.api.errors import IdisHttpError
from idis.audit.sink import InMemoryAuditSink
from idis.validators.audit_event_validator import validate_audit_event

_TENANT = "11111111-1111-1111-1111-111111111111"


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id=_TENANT,
        actor_id="compliance-admin",
        name="Compliance Admin",
        timezone="UTC",
        data_region="us-east-1",
        roles=frozenset({"ADMIN"}),
    )


class _Failed:
    passed = False
    errors: list[Any] = []


def _assert_valid_internal_post(sink: InMemoryAuditSink, event_types: set[str]) -> None:
    seen: set[str] = set()
    for event in sink.events:
        if event["event_type"] in event_types:
            seen.add(event["event_type"])
            result = validate_audit_event(event)
            assert result.passed, (event["event_type"], [e.code for e in result.errors])
            assert event["request"]["method"] == "POST"
            assert event["request"]["path"].startswith("/internal/")
    assert seen == event_types, f"missing core events: {event_types - seen}"


class TestByokCoreEventsValidate:
    def test_configure_rotate_revoke_core_events_are_schema_valid(self) -> None:
        from idis.compliance.byok import (
            BYOKPolicyRegistry,
            configure_key,
            revoke_key,
            rotate_key,
        )

        reg = BYOKPolicyRegistry()
        sink = InMemoryAuditSink()
        configure_key(_ctx(), "kms-alias-1", sink, reg)
        rotate_key(_ctx(), "kms-alias-2", sink, reg)
        revoke_key(_ctx(), sink, reg)
        _assert_valid_internal_post(
            sink, {"byok.key.configured", "byok.key.rotated", "byok.key.revoked"}
        )

    def test_configure_validation_failure_aborts_policy_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import idis.compliance.byok as byok_module
        from idis.compliance.byok import BYOKPolicyRegistry, configure_key

        monkeypatch.setattr(byok_module, "validate_audit_event", lambda event: _Failed())
        reg = BYOKPolicyRegistry()
        with pytest.raises(IdisHttpError) as exc_info:
            configure_key(_ctx(), "kms-alias-x", InMemoryAuditSink(), reg)
        assert exc_info.value.code == "BYOK_AUDIT_FAILED"
        assert reg.get(_TENANT) is None  # fail-closed BEFORE the policy write


class TestLegalHoldCoreEventsValidate:
    def test_apply_and_lift_core_events_are_schema_valid(self) -> None:
        from idis.compliance.retention import (
            HoldTarget,
            LegalHoldRegistry,
            apply_hold,
            lift_hold,
        )

        reg = LegalHoldRegistry()
        sink = InMemoryAuditSink()
        hold = apply_hold(
            _ctx(), HoldTarget.DEAL, "deal-1", "Litigation hold for the repair test.", sink, reg
        )
        lift_hold(_ctx(), hold.hold_id, sink, reg)
        _assert_valid_internal_post(sink, {"legal_hold.applied", "legal_hold.lifted"})

    def test_apply_validation_failure_aborts_hold_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import idis.compliance.retention as retention_module
        from idis.compliance.retention import HoldTarget, LegalHoldRegistry, apply_hold

        monkeypatch.setattr(retention_module, "validate_audit_event", lambda event: _Failed())
        reg = LegalHoldRegistry()
        with pytest.raises(IdisHttpError) as exc_info:
            apply_hold(
                _ctx(),
                HoldTarget.DEAL,
                "deal-1",
                "Litigation hold for the repair test.",
                InMemoryAuditSink(),
                reg,
            )
        assert exc_info.value.code == "HOLD_AUDIT_FAILED"
        assert reg.has_active_hold(_TENANT, HoldTarget.DEAL, "deal-1") is False  # no hold written

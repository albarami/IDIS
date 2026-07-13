"""Slice98 Task 7 - retention enforcement janitor (hermetic).

RED-first. Approved decisions: destructive work = the two infra orphans (expired idempotency
records, terminal webhook-outbox rows); retention-class deletion is a fully-tested mechanism that
is INERT under default policies (RAW_DOCUMENTS never expires, DELIVERABLES requires admin
approval, AUDIT_EVENTS forbids hard delete - and is protected unconditionally); destruction
requires BOTH IDIS_ENABLE_COMPLIANCE_JANITOR=1 AND IDIS_COMPLIANCE_JANITOR_DRY_RUN=0 (dry-run
defaults ON); the fail-closed ``retention.sweep.executed`` audit event is emitted BEFORE any
destructive work and its failure aborts ALL of it; hold-blocked or hold-resolution-failed
candidates are SKIPPED, never deleted; no migration (no new durable state). PYTHONPATH is pinned
to this worktree's src for every run.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from idis.api.errors import IdisHttpError
from idis.audit.sink import InMemoryAuditSink
from idis.compliance.retention import HoldTarget, RetentionClass, RetentionPolicy

_TENANT_A = "11111111-1111-1111-1111-111111111111"
_TENANT_B = "22222222-2222-2222-2222-222222222222"
_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


def _candidate(
    *,
    resource_id: str = "res-1",
    age_days: int,
    retention_class: RetentionClass = RetentionClass.DELIVERABLES,
) -> Any:
    from idis.services.compliance.janitor import RetentionCandidate

    return RetentionCandidate(
        resource_id=resource_id,
        created_at=_NOW - timedelta(days=age_days),
        retention_class=retention_class,
        hold_target_type=HoldTarget.ARTIFACT,
    )


class _StaticSource:
    """A sweep source yielding a fixed candidate list."""

    def __init__(self, name: str, candidates: list[Any]) -> None:
        self.name = name
        self._candidates = candidates

    def list_candidates(self, tenant_id: str) -> list[Any]:
        return list(self._candidates)


class _ExplodingSource:
    name = "exploding"

    def list_candidates(self, tenant_id: str) -> list[Any]:
        raise RuntimeError("source unavailable")


class _RecordingDeleter:
    """Deleter recording calls; optionally raising a chosen error per resource_id."""

    def __init__(self, errors: dict[str, IdisHttpError] | None = None) -> None:
        self.calls: list[str] = []
        self._errors = errors or {}

    def __call__(self, tenant_id: str, candidate: Any) -> None:
        error = self._errors.get(candidate.resource_id)
        if error is not None:
            raise error
        self.calls.append(candidate.resource_id)


_PERMISSIVE_30D = {
    RetentionClass.DELIVERABLES: RetentionPolicy(
        retention_class=RetentionClass.DELIVERABLES,
        retention_days=30,
        hard_delete_allowed=True,
        requires_admin_approval=False,
    )
}


def _sweep(
    sources: list[Any],
    deleter: Any,
    *,
    destructive: bool,
    policies: dict[RetentionClass, RetentionPolicy] | None = None,
) -> dict[str, Any]:
    from idis.services.compliance.janitor import sweep_tenant_retention

    return sweep_tenant_retention(
        _TENANT_A,
        sources,
        deleter,
        policies=policies,
        now=_NOW,
        destructive=destructive,
    )


class TestRetentionSweepCore:
    """Unit A: eligibility math with a fake clock; default policies are inert."""

    def test_default_policies_are_inert_even_for_ancient_candidates(self) -> None:
        deleter = _RecordingDeleter()
        counts = _sweep(
            [
                _StaticSource(
                    "all-classes",
                    [
                        _candidate(
                            resource_id="raw",
                            age_days=9000,
                            retention_class=RetentionClass.RAW_DOCUMENTS,
                        ),
                        _candidate(
                            resource_id="deliverable",
                            age_days=9000,
                            retention_class=RetentionClass.DELIVERABLES,
                        ),
                        _candidate(
                            resource_id="audit",
                            age_days=9000,
                            retention_class=RetentionClass.AUDIT_EVENTS,
                        ),
                    ],
                )
            ],
            deleter,
            destructive=True,
        )
        assert deleter.calls == []  # nothing is ever auto-deletable under defaults
        assert counts["eligible"] == 0
        assert counts["deleted"] == 0
        assert counts["no_expiry"] == 1  # RAW_DOCUMENTS: retention_days=0
        assert counts["skipped_admin_approval"] == 1  # DELIVERABLES
        assert counts["skipped_hard_delete_disallowed"] == 1  # AUDIT_EVENTS

    def test_audit_events_class_is_protected_even_under_permissive_policy(self) -> None:
        # Unconditional guard: even a (mis)configured policy that marks AUDIT_EVENTS deletable
        # must not let the janitor touch them - audit immutability is a core invariant.
        deleter = _RecordingDeleter()
        reckless = {
            RetentionClass.AUDIT_EVENTS: RetentionPolicy(
                retention_class=RetentionClass.AUDIT_EVENTS,
                retention_days=1,
                hard_delete_allowed=True,
                requires_admin_approval=False,
            )
        }
        counts = _sweep(
            [
                _StaticSource(
                    "audit",
                    [
                        _candidate(
                            resource_id="audit",
                            age_days=10,
                            retention_class=RetentionClass.AUDIT_EVENTS,
                        )
                    ],
                )
            ],
            deleter,
            destructive=True,
            policies=reckless,
        )
        assert deleter.calls == []
        assert counts["deleted"] == 0
        assert counts["skipped_hard_delete_disallowed"] == 1

    def test_permissive_policy_deletes_expired_and_keeps_fresh(self) -> None:
        deleter = _RecordingDeleter()
        counts = _sweep(
            [
                _StaticSource(
                    "deliverables",
                    [
                        _candidate(resource_id="old", age_days=31),
                        _candidate(resource_id="fresh", age_days=29),
                    ],
                )
            ],
            deleter,
            destructive=True,
            policies=_PERMISSIVE_30D,
        )
        assert deleter.calls == ["old"]
        assert counts["eligible"] == 1
        assert counts["deleted"] == 1
        assert counts["within_retention"] == 1
        assert counts["by_class"]["DELIVERABLES"]["eligible"] == 1

    def test_dry_run_counts_but_never_deletes(self) -> None:
        deleter = _RecordingDeleter()
        counts = _sweep(
            [_StaticSource("deliverables", [_candidate(resource_id="old", age_days=31)])],
            deleter,
            destructive=False,
            policies=_PERMISSIVE_30D,
        )
        assert deleter.calls == []
        assert counts["eligible"] == 1
        assert counts["deleted"] == 0

    def test_held_candidate_is_skipped_and_sweep_continues(self) -> None:
        held_error = IdisHttpError(
            status_code=403, code="DELETION_BLOCKED_BY_HOLD", message="Access denied."
        )
        deleter = _RecordingDeleter(errors={"held": held_error})
        counts = _sweep(
            [
                _StaticSource(
                    "deliverables",
                    [
                        _candidate(resource_id="held", age_days=31),
                        _candidate(resource_id="free", age_days=31),
                    ],
                )
            ],
            deleter,
            destructive=True,
            policies=_PERMISSIVE_30D,
        )
        assert deleter.calls == ["free"]  # the held one skipped, the sweep continued
        assert counts["deleted"] == 1
        assert counts["held_skipped"] == 1

    def test_hold_resolution_error_skips_not_deletes(self) -> None:
        resolution_error = IdisHttpError(
            status_code=403, code="LEGAL_HOLD_RESOLUTION_FAILED", message="Access denied."
        )
        deleter = _RecordingDeleter(errors={"unresolved": resolution_error})
        counts = _sweep(
            [_StaticSource("deliverables", [_candidate(resource_id="unresolved", age_days=31)])],
            deleter,
            destructive=True,
            policies=_PERMISSIVE_30D,
        )
        assert counts["deleted"] == 0
        assert counts["resolution_failed"] == 1

    def test_unexpected_deleter_error_is_counted_and_sweep_continues(self) -> None:
        boom = IdisHttpError(status_code=500, code="STORE_ERROR", message="boom")
        deleter = _RecordingDeleter(errors={"broken": boom})
        counts = _sweep(
            [
                _StaticSource(
                    "deliverables",
                    [
                        _candidate(resource_id="broken", age_days=31),
                        _candidate(resource_id="ok", age_days=31),
                    ],
                )
            ],
            deleter,
            destructive=True,
            policies=_PERMISSIVE_30D,
        )
        assert deleter.calls == ["ok"]
        assert counts["errors"] == 1
        assert counts["deleted"] == 1

    def test_missing_policy_never_deletes(self) -> None:
        deleter = _RecordingDeleter()
        counts = _sweep(
            [_StaticSource("deliverables", [_candidate(resource_id="x", age_days=9000)])],
            deleter,
            destructive=True,
            policies={},
        )
        assert deleter.calls == []
        assert counts["no_policy"] == 1

    def test_source_failure_is_isolated(self) -> None:
        deleter = _RecordingDeleter()
        counts = _sweep(
            [
                _ExplodingSource(),
                _StaticSource("deliverables", [_candidate(resource_id="ok", age_days=31)]),
            ],
            deleter,
            destructive=True,
            policies=_PERMISSIVE_30D,
        )
        assert counts["source_errors"] == 1
        assert deleter.calls == ["ok"]  # the healthy source still swept


@pytest.fixture
def _janitor_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    monkeypatch.delenv("IDIS_ENABLE_COMPLIANCE_JANITOR", raising=False)
    monkeypatch.delenv("IDIS_COMPLIANCE_JANITOR_DRY_RUN", raising=False)
    monkeypatch.delenv("IDIS_JANITOR_OUTBOX_RETENTION_DAYS", raising=False)
    monkeypatch.setenv("IDIS_WORKER_TENANT_IDS", f"{_TENANT_A},{_TENANT_B}")
    yield monkeypatch


class _RecordingOrphans:
    """Fake idempotency store + outbox repo recording cleanup calls on a shared timeline."""

    def __init__(self, timeline: list[tuple[str, Any]]) -> None:
        self.timeline = timeline

    def delete_expired(self, *, tenant_id: str, older_than: datetime) -> int:
        self.timeline.append(("idempotency", (tenant_id, older_than)))
        return 3

    def delete_terminal(self, *, tenant_id: str, older_than: datetime, conn: Any = None) -> int:
        self.timeline.append(("outbox", (tenant_id, older_than)))
        return 2


class _TimelineSink(InMemoryAuditSink):
    """Audit sink stamping emissions onto the shared timeline (ordering proof)."""

    def __init__(self, timeline: list[tuple[str, Any]], *, fail: bool = False) -> None:
        super().__init__()
        self._timeline = timeline
        self._fail = fail

    def emit(self, event: dict[str, Any]) -> None:
        if self._fail and event.get("event_type") == "retention.sweep.executed":
            raise RuntimeError("audit sink down")
        self._timeline.append(("audit", event.get("event_type")))
        super().emit(event)


class TestJanitorFlags:
    """Unit B: double opt-in - enabled default off; dry-run default ON."""

    def test_janitor_disabled_by_default(self, _janitor_env: pytest.MonkeyPatch) -> None:
        from idis.services.compliance.janitor import is_compliance_janitor_enabled

        assert is_compliance_janitor_enabled() is False

    def test_dry_run_defaults_on_when_enabled(self, _janitor_env: pytest.MonkeyPatch) -> None:
        from idis.services.compliance.janitor import (
            is_compliance_janitor_enabled,
            is_janitor_destructive,
        )

        _janitor_env.setenv("IDIS_ENABLE_COMPLIANCE_JANITOR", "1")
        assert is_compliance_janitor_enabled() is True
        assert is_janitor_destructive() is False  # dry-run is the default posture

    def test_destruction_requires_both_flags(self, _janitor_env: pytest.MonkeyPatch) -> None:
        from idis.services.compliance.janitor import is_janitor_destructive

        _janitor_env.setenv("IDIS_ENABLE_COMPLIANCE_JANITOR", "1")
        _janitor_env.setenv("IDIS_COMPLIANCE_JANITOR_DRY_RUN", "0")
        assert is_janitor_destructive() is True
        # enabling dry-run alone can never be destructive
        _janitor_env.setenv("IDIS_ENABLE_COMPLIANCE_JANITOR", "0")
        assert is_janitor_destructive() is False

    @pytest.mark.parametrize(
        "dry_run_value",
        ["", "false", "off", "no", "typo", "1", " 1 ", "disabled"],
    )
    def test_only_literal_zero_disables_dry_run(
        self, _janitor_env: pytest.MonkeyPatch, dry_run_value: str
    ) -> None:
        # The double opt-in is LITERAL: destruction requires IDIS_COMPLIANCE_JANITOR_DRY_RUN to
        # be exactly "0" (after strip). Every other value - including falsy-looking words and
        # typos - fails SAFE to dry-run; a misspelling must never unlock destruction.
        from idis.services.compliance.janitor import is_janitor_destructive

        _janitor_env.setenv("IDIS_ENABLE_COMPLIANCE_JANITOR", "1")
        _janitor_env.setenv("IDIS_COMPLIANCE_JANITOR_DRY_RUN", dry_run_value)
        assert is_janitor_destructive() is False

    def test_absent_dry_run_env_is_dry_run(self, _janitor_env: pytest.MonkeyPatch) -> None:
        from idis.services.compliance.janitor import is_janitor_destructive

        _janitor_env.setenv("IDIS_ENABLE_COMPLIANCE_JANITOR", "1")
        _janitor_env.delenv("IDIS_COMPLIANCE_JANITOR_DRY_RUN", raising=False)
        assert is_janitor_destructive() is False

    def test_literal_zero_with_whitespace_is_destructive(
        self, _janitor_env: pytest.MonkeyPatch
    ) -> None:
        from idis.services.compliance.janitor import is_janitor_destructive

        _janitor_env.setenv("IDIS_ENABLE_COMPLIANCE_JANITOR", "1")
        _janitor_env.setenv("IDIS_COMPLIANCE_JANITOR_DRY_RUN", " 0 ")
        assert is_janitor_destructive() is True


class TestJanitorTenantSweep:
    """Unit B: orchestrated per-tenant sweep - audit-before-destruction, orphans, signals."""

    def _run_sweep(
        self,
        *,
        destructive: bool,
        timeline: list[tuple[str, Any]],
        sink: InMemoryAuditSink,
    ) -> dict[str, Any]:
        from idis.services.compliance.janitor import sweep_tenant

        orphans = _RecordingOrphans(timeline)
        return sweep_tenant(
            _TENANT_A,
            sources=[],
            deleter=_RecordingDeleter(),
            idempotency_store=orphans,
            outbox_repo=orphans,
            audit_sink=sink,
            now=_NOW,
            destructive=destructive,
        )

    def test_dry_run_reports_without_touching_orphans_or_emitting_executed(self) -> None:
        timeline: list[tuple[str, Any]] = []
        sink = _TimelineSink(timeline)
        result = self._run_sweep(destructive=False, timeline=timeline, sink=sink)

        assert [t for t in timeline if t[0] in ("idempotency", "outbox")] == []
        executed = [e for e in sink.events if e.get("event_type") == "retention.sweep.executed"]
        assert executed == []  # nothing destructive happened, so no executed event
        completed = [e for e in sink.events if e.get("event_type") == "retention.sweep.completed"]
        assert len(completed) == 1  # best-effort signal always reports
        assert result["dry_run"] is True

    def test_destructive_emits_executed_before_any_deletion(self) -> None:
        timeline: list[tuple[str, Any]] = []
        sink = _TimelineSink(timeline)
        result = self._run_sweep(destructive=True, timeline=timeline, sink=sink)

        audit_index = timeline.index(("audit", "retention.sweep.executed"))
        deletion_indexes = [i for i, t in enumerate(timeline) if t[0] in ("idempotency", "outbox")]
        assert deletion_indexes, "destructive sweep must clean the orphans"
        assert audit_index < min(deletion_indexes)  # audit strictly precedes destruction
        assert result["idempotency_deleted"] == 3
        assert result["outbox_deleted"] == 2

    def test_destructive_uses_configured_cutoffs(self, _janitor_env: pytest.MonkeyPatch) -> None:
        _janitor_env.setenv("IDIS_JANITOR_OUTBOX_RETENTION_DAYS", "30")
        timeline: list[tuple[str, Any]] = []
        sink = _TimelineSink(timeline)
        self._run_sweep(destructive=True, timeline=timeline, sink=sink)

        outbox_calls = [t for t in timeline if t[0] == "outbox"]
        assert outbox_calls[0][1][1] == _NOW - timedelta(days=30)
        idem_calls = [t for t in timeline if t[0] == "idempotency"]
        from idis.idempotency.store import load_idempotency_ttl_days

        assert idem_calls[0][1][1] == _NOW - timedelta(days=load_idempotency_ttl_days())

    def test_audit_failure_aborts_all_destructive_work(self) -> None:
        timeline: list[tuple[str, Any]] = []
        sink = _TimelineSink(timeline, fail=True)
        result = self._run_sweep(destructive=True, timeline=timeline, sink=sink)

        assert [t for t in timeline if t[0] in ("idempotency", "outbox")] == []
        assert result["destructive_aborted"] is True
        assert result["idempotency_deleted"] == 0
        assert result["outbox_deleted"] == 0

    def test_executed_event_is_schema_valid_and_safe_shape(self) -> None:
        from idis.validators.audit_event_validator import validate_audit_event

        timeline: list[tuple[str, Any]] = []
        sink = _TimelineSink(timeline)
        self._run_sweep(destructive=True, timeline=timeline, sink=sink)

        executed = [e for e in sink.events if e.get("event_type") == "retention.sweep.executed"]
        assert len(executed) == 1
        event = executed[0]
        result = validate_audit_event(event)
        assert result.passed, [e.code for e in result.errors]
        assert event["severity"] == "HIGH"
        assert event["resource"]["resource_type"] == "retention_sweep"
        assert event["tenant_id"] == _TENANT_A
        for value in event["payload"]["safe"].values():
            assert isinstance(value, (str, int, bool)), f"unsafe payload value: {value!r}"


class TestJanitorWorker:
    """Unit B: worker start/stop honors the enable flag; tenant failures are isolated."""

    def test_worker_start_is_noop_when_disabled(self, _janitor_env: pytest.MonkeyPatch) -> None:
        from idis.services.compliance.janitor import ComplianceJanitorWorker

        worker = ComplianceJanitorWorker(audit_sink=InMemoryAuditSink())

        async def _exercise() -> bool:
            await worker.start()
            running = worker.is_running
            await worker.stop()
            return running

        assert asyncio.run(_exercise()) is False

    def test_worker_starts_and_stops_when_enabled(self, _janitor_env: pytest.MonkeyPatch) -> None:
        from idis.services.compliance.janitor import ComplianceJanitorWorker

        _janitor_env.setenv("IDIS_ENABLE_COMPLIANCE_JANITOR", "1")
        worker = ComplianceJanitorWorker(audit_sink=InMemoryAuditSink(), poll_interval=3600)

        async def _exercise() -> tuple[bool, bool]:
            await worker.start()
            running = worker.is_running
            await worker.stop()
            return running, worker.is_running

        started, stopped_state = asyncio.run(_exercise())
        assert started is True
        assert stopped_state is False

    def test_one_tenant_failure_does_not_starve_others(
        self, _janitor_env: pytest.MonkeyPatch
    ) -> None:
        from idis.services.compliance.janitor import ComplianceJanitorWorker

        swept: list[str] = []

        class _Worker(ComplianceJanitorWorker):
            def _sweep_one_tenant(self, tenant_id: str) -> None:
                if tenant_id == _TENANT_A:
                    raise RuntimeError("tenant A sweep failed")
                swept.append(tenant_id)

        worker = _Worker(audit_sink=InMemoryAuditSink())
        worker._sweep_all_tenants()
        assert swept == [_TENANT_B]  # tenant A failed; tenant B still swept

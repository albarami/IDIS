"""Retention enforcement janitor (Slice98 Task 7).

A dispatcher-pattern background worker (off-loop, disabled by default, dry-run by default) that
turns the previously passive retention primitives into scheduled enforcement:

- Retention-class sweep: applies the retention-policy table (via ``evaluate_retention``'s policy
  math) to candidates yielded by ``RetentionSweepSource``s. Delete-ELIGIBILITY requires a finite
  ``retention_days`` elapsed AND ``hard_delete_allowed`` AND NOT ``requires_admin_approval`` - so
  under the DEFAULT policies nothing is ever auto-deletable (RAW_DOCUMENTS never expires,
  DELIVERABLES requires admin approval, AUDIT_EVENTS forbids hard delete and is additionally
  protected unconditionally here). Deletion happens ONLY through the injected hold-aware deleter
  (the ``ComplianceEnforcedStore`` path): an active legal hold or a hold-resolution failure SKIPS
  the candidate - never deletes - and the sweep continues.
- Infra-orphan cleanup (the actual destructive work with default policies): expired idempotency
  records (``delete_expired``, scheduled rather than opportunistic-only) and terminal
  webhook-outbox rows (``delete_terminal``, previously uncalled).

Safety posture:
- Destruction requires BOTH ``IDIS_ENABLE_COMPLIANCE_JANITOR=1`` (worker starts) AND
  ``IDIS_COMPLIANCE_JANITOR_DRY_RUN=0`` (dry-run is the default even when enabled).
- The fail-closed ``retention.sweep.executed`` audit event (HIGH) is emitted and validated BEFORE
  any destructive work; if that emission fails, ALL destructive work for the sweep is aborted.
  The best-effort ``retention.sweep.completed`` signal reports actual safe-shape counts always.
- Tenant scoping is fail-safe (``get_worker_tenant_ids``: empty means no scan); one tenant's or
  one source's failure never starves the others.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from idis.api.errors import IdisHttpError
from idis.audit.sink import AuditSink
from idis.compliance.retention import (
    DEFAULT_RETENTION_POLICIES,
    HoldTarget,
    RetentionClass,
    RetentionPolicy,
    evaluate_retention,
)
from idis.observability.runtime_signals import RETENTION_SWEEP_COMPLETED, emit_run_signal
from idis.pipeline.worker import get_worker_tenant_ids
from idis.validators.audit_event_validator import validate_audit_event

logger = logging.getLogger(__name__)

IDIS_JANITOR_ENABLED_ENV = "IDIS_ENABLE_COMPLIANCE_JANITOR"
IDIS_JANITOR_DRY_RUN_ENV = "IDIS_COMPLIANCE_JANITOR_DRY_RUN"
IDIS_JANITOR_INTERVAL_ENV = "IDIS_COMPLIANCE_JANITOR_INTERVAL_SECONDS"
IDIS_JANITOR_OUTBOX_DAYS_ENV = "IDIS_JANITOR_OUTBOX_RETENTION_DAYS"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_DEFAULT_INTERVAL_SECONDS = 3600
_DEFAULT_OUTBOX_RETENTION_DAYS = 30

RETENTION_SWEEP_EXECUTED = "retention.sweep.executed"


def is_compliance_janitor_enabled() -> bool:
    """True when the janitor worker may run at all (default off)."""
    return os.environ.get(IDIS_JANITOR_ENABLED_ENV, "").strip().lower() in _TRUTHY


def _is_dry_run() -> bool:
    """Dry-run is the DEFAULT posture and fails SAFE.

    Only the LITERAL value "0" (after strip) disables dry-run - the locked double opt-in is
    IDIS_ENABLE_COMPLIANCE_JANITOR=1 AND IDIS_COMPLIANCE_JANITOR_DRY_RUN=0, exactly. Any other
    value (absent, empty, "false", "off", "no", a typo) stays dry-run: a misspelling must never
    unlock destruction.
    """
    return os.environ.get(IDIS_JANITOR_DRY_RUN_ENV, "1").strip() != "0"


def is_janitor_destructive() -> bool:
    """Destruction requires BOTH the enable flag on AND dry-run explicitly off."""
    return is_compliance_janitor_enabled() and not _is_dry_run()


def janitor_interval_seconds() -> int:
    raw = os.environ.get(IDIS_JANITOR_INTERVAL_ENV, "").strip()
    try:
        return int(raw) if raw else _DEFAULT_INTERVAL_SECONDS
    except ValueError:
        return _DEFAULT_INTERVAL_SECONDS


def outbox_retention_days() -> int:
    raw = os.environ.get(IDIS_JANITOR_OUTBOX_DAYS_ENV, "").strip()
    try:
        return int(raw) if raw else _DEFAULT_OUTBOX_RETENTION_DAYS
    except ValueError:
        return _DEFAULT_OUTBOX_RETENTION_DAYS


@dataclass(frozen=True, slots=True)
class RetentionCandidate:
    """A resource considered by the retention sweep."""

    resource_id: str
    created_at: datetime
    retention_class: RetentionClass
    hold_target_type: HoldTarget


@runtime_checkable
class RetentionSweepSource(Protocol):
    """Yields retention candidates for one tenant. ``name`` labels counts and logs."""

    name: str

    def list_candidates(self, tenant_id: str) -> list[RetentionCandidate]:
        """Return this source's candidates for the tenant (tenant-scoped reads only)."""
        ...


_COUNT_KEYS = (
    "eligible",
    "deleted",
    "held_skipped",
    "within_retention",
    "skipped_admin_approval",
    "skipped_hard_delete_disallowed",
    "no_expiry",
    "no_policy",
    "resolution_failed",
    "errors",
    "source_errors",
)


def _empty_counts() -> dict[str, Any]:
    counts: dict[str, Any] = dict.fromkeys(_COUNT_KEYS, 0)
    counts["by_class"] = {}
    return counts


def sweep_tenant_retention(
    tenant_id: str,
    sources: list[RetentionSweepSource],
    deleter: Any,
    *,
    policies: dict[RetentionClass, RetentionPolicy] | None = None,
    now: datetime | None = None,
    destructive: bool,
) -> dict[str, Any]:
    """Evaluate (and, when destructive, enforce) retention over the sources' candidates.

    Returns safe-shape counts only. Never raises for per-candidate or per-source failures:
    a held candidate, a hold-resolution failure, or an unexpected deleter error is counted and
    skipped, and the sweep continues (deny-by-default: on any doubt, nothing is deleted).
    """
    effective_now = now or datetime.now(UTC)
    effective_policies = DEFAULT_RETENTION_POLICIES if policies is None else policies
    counts = _empty_counts()

    for source in sources:
        try:
            candidates = source.list_candidates(tenant_id)
        except Exception:
            logger.warning(
                "Retention sweep source failed (skipped): %s", source.name, exc_info=True
            )
            counts["source_errors"] += 1
            continue

        for candidate in candidates:
            class_name = candidate.retention_class.value
            by_class = counts["by_class"].setdefault(class_name, {"eligible": 0, "deleted": 0})

            # Unconditional protection: audit events are append-only by core invariant; not
            # even an operator-supplied permissive policy lets the janitor touch them.
            if candidate.retention_class == RetentionClass.AUDIT_EVENTS:
                counts["skipped_hard_delete_disallowed"] += 1
                continue

            policy = effective_policies.get(candidate.retention_class)
            if policy is None:
                counts["no_policy"] += 1
                continue
            if not policy.hard_delete_allowed:
                counts["skipped_hard_delete_disallowed"] += 1
                continue
            _, earliest_delete = evaluate_retention(
                candidate.retention_class, candidate.created_at, effective_policies
            )
            if earliest_delete is None:
                counts["no_expiry"] += 1  # retention_days == 0: retained while active
                continue
            if effective_now < earliest_delete:
                counts["within_retention"] += 1
                continue
            if policy.requires_admin_approval:
                # The janitor never substitutes for admin approval; these are reported only.
                counts["skipped_admin_approval"] += 1
                continue

            counts["eligible"] += 1
            by_class["eligible"] += 1
            if not destructive:
                continue

            try:
                deleter(tenant_id, candidate)
            except IdisHttpError as e:
                if e.code == "DELETION_BLOCKED_BY_HOLD":
                    counts["held_skipped"] += 1
                elif e.code == "LEGAL_HOLD_RESOLUTION_FAILED":
                    counts["resolution_failed"] += 1  # cannot resolve holds: skip, never delete
                else:
                    counts["errors"] += 1
            except Exception:
                logger.warning("Retention deletion failed (skipped)", exc_info=True)
                counts["errors"] += 1
            else:
                counts["deleted"] += 1
                by_class["deleted"] += 1

    return counts


def _build_executed_event(
    tenant_id: str, sweep_id: str, now: datetime, planned: dict[str, Any]
) -> dict[str, Any]:
    """The fail-closed pre-destruction audit record (validated before emission)."""
    return {
        "event_id": str(uuid.uuid4()),
        "occurred_at": now.isoformat().replace("+00:00", "Z"),
        "tenant_id": tenant_id,
        "actor": {"actor_type": "SERVICE", "actor_id": "compliance-janitor"},
        "request": {
            "request_id": sweep_id,
            "method": "POST",
            "path": "/internal/compliance-janitor",
            "status_code": 200,
        },
        "resource": {"resource_type": "retention_sweep", "resource_id": sweep_id},
        "event_type": RETENTION_SWEEP_EXECUTED,
        "severity": "HIGH",
        "summary": f"retention sweep destructive phase for tenant {tenant_id}",
        "payload": {"safe": planned, "hashes": [], "refs": []},
    }


def sweep_tenant(
    tenant_id: str,
    *,
    sources: list[RetentionSweepSource],
    deleter: Any,
    idempotency_store: Any | None,
    outbox_repo: Any | None,
    audit_sink: AuditSink | None,
    now: datetime | None = None,
    destructive: bool,
) -> dict[str, Any]:
    """One tenant's full sweep: evaluate -> (audit-then-destroy) -> report.

    Ordering invariant: the ``retention.sweep.executed`` audit event is emitted and validated
    BEFORE any destructive action; a validation or emission failure aborts ALL destructive work
    for this sweep (``destructive_aborted``). The ``retention.sweep.completed`` signal reporting
    actual counts is best-effort and always attempted.
    """
    effective_now = now or datetime.now(UTC)
    sweep_id = str(uuid.uuid4())

    counts = sweep_tenant_retention(
        tenant_id, sources, deleter, now=effective_now, destructive=False
    )
    result: dict[str, Any] = dict(counts)
    result.update(
        {
            "dry_run": not destructive,
            "destructive_aborted": False,
            "idempotency_deleted": 0,
            "outbox_deleted": 0,
        }
    )

    if destructive:
        planned = {
            "sweep_id": sweep_id,
            "planned_retention_deletions": int(counts["eligible"]),
            "idempotency_cleanup": idempotency_store is not None,
            "outbox_cleanup": outbox_repo is not None,
        }
        event = _build_executed_event(tenant_id, sweep_id, effective_now, planned)
        aborted = False
        validation = validate_audit_event(event)
        if not validation.passed:
            logger.error(
                "retention.sweep.executed failed validation; aborting destructive work: %s",
                [error.code for error in validation.errors],
            )
            aborted = True
        else:
            try:
                if audit_sink is None:
                    raise RuntimeError("no audit sink configured")
                audit_sink.emit(event)
            except Exception:
                logger.error(
                    "retention.sweep.executed emission failed; aborting destructive work",
                    exc_info=True,
                )
                aborted = True

        if aborted:
            result["destructive_aborted"] = True
        else:
            destructive_counts = sweep_tenant_retention(
                tenant_id, sources, deleter, now=effective_now, destructive=True
            )
            for key in _COUNT_KEYS:
                result[key] = destructive_counts[key]
            result["by_class"] = destructive_counts["by_class"]

            if idempotency_store is not None:
                try:
                    from idis.idempotency.store import load_idempotency_ttl_days

                    cutoff = effective_now - timedelta(days=load_idempotency_ttl_days())
                    result["idempotency_deleted"] = int(
                        idempotency_store.delete_expired(tenant_id=tenant_id, older_than=cutoff)
                    )
                except Exception:
                    logger.warning("Idempotency orphan cleanup failed", exc_info=True)
                    result["errors"] += 1
            if outbox_repo is not None:
                try:
                    cutoff = effective_now - timedelta(days=outbox_retention_days())
                    result["outbox_deleted"] = int(
                        outbox_repo.delete_terminal(tenant_id=tenant_id, older_than=cutoff)
                    )
                except Exception:
                    logger.warning("Webhook outbox orphan cleanup failed", exc_info=True)
                    result["errors"] += 1

    signal_details = {key: result[key] for key in _COUNT_KEYS}
    signal_details.update(
        {
            "sweep_id": sweep_id,
            "dry_run": bool(result["dry_run"]),
            "destructive_aborted": bool(result["destructive_aborted"]),
            "idempotency_deleted": int(result["idempotency_deleted"]),
            "outbox_deleted": int(result["outbox_deleted"]),
        }
    )
    emit_run_signal(
        audit_sink,
        event_type=RETENTION_SWEEP_COMPLETED,
        tenant_id=tenant_id,
        details=signal_details,
    )
    return result


class PostgresDeliverablesSweepSource:
    """Deliverables rows as DELIVERABLES-class candidates (report-only under defaults)."""

    name = "deliverables"

    def list_candidates(self, tenant_id: str) -> list[RetentionCandidate]:
        from sqlalchemy import text

        from idis.persistence.db import begin_app_conn, set_tenant_local

        with begin_app_conn() as conn:
            set_tenant_local(conn, tenant_id)
            rows = list(
                conn.execute(
                    text(
                        "SELECT deliverable_id, created_at FROM deliverables "
                        "WHERE tenant_id = CAST(:tenant_id AS uuid)"
                    ),
                    {"tenant_id": tenant_id},
                )
            )
        return [
            RetentionCandidate(
                resource_id=str(row.deliverable_id),
                created_at=row.created_at,
                retention_class=RetentionClass.DELIVERABLES,
                hold_target_type=HoldTarget.ARTIFACT,
            )
            for row in rows
        ]


def _default_deleter(tenant_id: str, candidate: RetentionCandidate) -> None:
    """Hold-aware deletion through the real ComplianceEnforcedStore boundary."""
    from idis.api.auth import TenantContext
    from idis.services.ingestion.defaults import build_default_compliance_store

    ctx = TenantContext(
        tenant_id=tenant_id,
        actor_id="compliance-janitor",
        name="Compliance Janitor",
        timezone="UTC",
        data_region=None,
        roles=frozenset({"ADMIN"}),
    )
    build_default_compliance_store().delete(
        ctx,
        candidate.resource_id,
        resource_id=candidate.resource_id,
        hold_target_type=candidate.hold_target_type,
    )


class ComplianceJanitorWorker:
    """Asyncio polling janitor mirroring the webhook dispatcher (off-loop, errors isolated)."""

    def __init__(
        self,
        *,
        audit_sink: AuditSink | None,
        poll_interval: int | None = None,
        sources: list[RetentionSweepSource] | None = None,
        deleter: Any | None = None,
        idempotency_store: Any | None = None,
        outbox_repo: Any | None = None,
    ) -> None:
        self._audit_sink = audit_sink
        self._poll_interval = poll_interval or janitor_interval_seconds()
        self._sources = sources
        self._deleter = deleter or _default_deleter
        self._idempotency_store = idempotency_store
        self._outbox_repo = outbox_repo
        self._task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if not is_compliance_janitor_enabled():
            logger.info("Compliance janitor disabled (%s unset)", IDIS_JANITOR_ENABLED_ENV)
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "Compliance janitor started (interval=%ss, dry_run=%s)",
            self._poll_interval,
            not is_janitor_destructive(),
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("Compliance janitor stopped")

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                # Off-loop like the other workers: sweeps do blocking DB I/O.
                await asyncio.to_thread(self._sweep_all_tenants)
            except Exception:  # the poll loop must survive any sweep failure
                logger.exception("Compliance janitor iteration failed")
            await asyncio.sleep(self._poll_interval)

    def _sweep_all_tenants(self) -> None:
        # Fail-safe scoping (mirrors the other workers): no tenants configured -> no scan.
        for tenant_id in get_worker_tenant_ids():
            try:
                self._sweep_one_tenant(tenant_id)
            except Exception:  # one tenant's failure must not starve the others
                logger.warning("Compliance sweep failed for a tenant", exc_info=True)

    def _build_sources(self) -> list[RetentionSweepSource]:
        if self._sources is not None:
            return self._sources
        from idis.persistence.db import is_postgres_configured

        if is_postgres_configured():
            return [PostgresDeliverablesSweepSource()]
        return []

    def _build_idempotency_store(self) -> Any | None:
        if self._idempotency_store is not None:
            return self._idempotency_store
        from idis.persistence.db import is_postgres_configured

        if not is_postgres_configured():
            return None
        try:
            from idis.idempotency.postgres_store import PostgresIdempotencyStore

            return PostgresIdempotencyStore()
        except ImportError:
            return None

    def _build_outbox_repo(self) -> Any | None:
        if self._outbox_repo is not None:
            return self._outbox_repo
        from idis.persistence.db import is_postgres_configured

        if not is_postgres_configured():
            return None
        from idis.persistence.repositories.webhook_outbox import default_webhook_outbox

        return default_webhook_outbox()

    def _sweep_one_tenant(self, tenant_id: str) -> None:
        sweep_tenant(
            tenant_id,
            sources=self._build_sources(),
            deleter=self._deleter,
            idempotency_store=self._build_idempotency_store(),
            outbox_repo=self._build_outbox_repo(),
            audit_sink=self._audit_sink,
            destructive=is_janitor_destructive(),
        )


_janitor_worker: ComplianceJanitorWorker | None = None


async def start_compliance_janitor_worker(audit_sink: AuditSink | None = None) -> None:
    """Start the process-wide compliance janitor (no-op unless the enable flag is on)."""
    global _janitor_worker
    if _janitor_worker is None:
        if audit_sink is None:
            from idis.audit.sink import get_audit_sink

            audit_sink = get_audit_sink()
        _janitor_worker = ComplianceJanitorWorker(audit_sink=audit_sink)
    await _janitor_worker.start()


async def stop_compliance_janitor_worker() -> None:
    """Stop the process-wide compliance janitor (app shutdown)."""
    global _janitor_worker
    if _janitor_worker is not None:
        await _janitor_worker.stop()
        _janitor_worker = None

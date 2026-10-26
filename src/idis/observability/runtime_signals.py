"""Safe-shape observability signals for Slice96 runtime controls (queue, lifecycle, denials).

These emit IDs / counts / stable codes ONLY -- never prompts, claim text, transcripts, raw provider
responses, secrets, env values, or paths -- to the existing audit sink (reuse, not a parallel
system). Emission is best-effort: a signal failure is swallowed and logged so it can never affect
the operation being observed. Formal fail-closed audit remains the orchestrator's strict
``_emit_audit_event`` path; these are lighter observability events (like the existing
``_emit_run_completed_audit``).
"""

from __future__ import annotations

import logging
from typing import Any

from idis.audit.canonical import HttpMethod, build_canonical_audit_event
from idis.audit.sink import AuditSink

logger = logging.getLogger(__name__)

# Runtime-control observability event types (safe, stable strings).
RUN_CLAIMED = "worker.run_claimed_observed"
RUN_CANCELLED = "worker.run_cancelled_observed"
RUN_QUEUE_OBSERVED = "worker.queue_observed"
RATE_LIMIT_DENIED = "auth.rate_limit.denied"
IDEMPOTENCY_CLEANUP = "integration.idempotency.cleanup"
RETENTION_SWEEP_COMPLETED = "retention.sweep.completed"


def emit_run_signal(
    audit_sink: AuditSink | None,
    *,
    event_type: str,
    tenant_id: str,
    details: dict[str, Any],
    actor_id: str = "idis-runtime-observer",
    request_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    method: HttpMethod = "POST",
    path: str = "/internal/runtime-signals",
    status_code: int = 200,
) -> None:
    """Emit a safe-shape observability event to ``audit_sink``, best-effort.

    ``details`` must contain only safe fields (IDs, counts, stable codes). Any emission failure is
    swallowed and logged -- observability must never break the observed operation. A ``None`` sink
    is a no-op (the signal is simply not recorded).

    Args:
        audit_sink: The audit sink to emit to (or ``None`` to skip).
        event_type: Safe, stable observability event type (dotted lower-case).
        tenant_id: Tenant the signal is scoped to.
        details: Safe payload (IDs / counts / codes only).
    """
    if audit_sink is None:
        return
    try:
        inferred_resource_type = resource_type
        inferred_resource_id = resource_id
        if inferred_resource_id is None and details.get("run_id"):
            inferred_resource_type = "run"
            inferred_resource_id = str(details["run_id"])
        elif inferred_resource_id is None and details.get("sweep_id"):
            inferred_resource_type = "retention_sweep"
            inferred_resource_id = str(details["sweep_id"])
        if inferred_resource_id is None:
            inferred_resource_type = "integration"
            inferred_resource_id = {
                RUN_QUEUE_OBSERVED: "run-queue",
                IDEMPOTENCY_CLEANUP: "idempotency-store",
                RATE_LIMIT_DENIED: "api-rate-limiter",
            }.get(event_type, "runtime-observer")

        event = build_canonical_audit_event(
            tenant_id=tenant_id,
            event_type=event_type,
            severity="LOW",
            summary=f"Runtime signal recorded: {event_type}",
            actor_id=actor_id,
            request_id=request_id or f"{event_type}:{inferred_resource_id}",
            method=method,
            path=path,
            status_code=status_code,
            resource_type=inferred_resource_type or "integration",
            resource_id=inferred_resource_id,
            safe=details,
            refs=[f"resource:{inferred_resource_id}"],
        )
        audit_sink.emit(event)
    except Exception:  # observability is explicitly best-effort; never break the observed operation
        logger.warning("observability signal %s failed to emit", event_type)

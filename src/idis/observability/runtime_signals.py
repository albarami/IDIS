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
import uuid
from datetime import UTC, datetime
from typing import Any

from idis.audit.sink import AuditSink

logger = logging.getLogger(__name__)

# Runtime-control observability event types (safe, stable strings).
RUN_CLAIMED = "run.claimed"
RUN_CANCELLED = "run.cancelled"
RUN_QUEUE_OBSERVED = "run.queue.observed"
RATE_LIMIT_DENIED = "rate_limit.denied"
IDEMPOTENCY_CLEANUP = "idempotency.cleanup"


def emit_run_signal(
    audit_sink: AuditSink | None,
    *,
    event_type: str,
    tenant_id: str,
    details: dict[str, Any],
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
    event = {
        "event_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tenant_id": tenant_id,
        "event_type": event_type,
        "severity": "LOW",
        "summary": event_type,
        "payload": {"safe": dict(details)},
    }
    try:
        audit_sink.emit(event)
    except Exception as exc:  # observability is best-effort; never break the observed operation
        logger.warning("observability signal %s failed to emit: %s", event_type, str(exc))

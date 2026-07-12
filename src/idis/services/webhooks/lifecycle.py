"""Slice97 Task 4 — wire the lifecycle emitter into real run/deliverable/gate/package points.

``notify_webhook_lifecycle`` is the best-effort call-site wrapper used at every lifecycle point. It
builds the default webhook service + outbox, obtains a tenant-scoped connection (reusing a caller
``conn`` when one is in scope — e.g. ``request.state.db_conn`` — else opening its own when Postgres
is configured, since subscriptions live only in the RLS-scoped ``webhooks`` table), and calls
``emit_lifecycle_event``. The ENTIRE body is wrapped so it can NEVER raise into, or roll back, the
audited mutation/run it runs alongside (acceptance A1) — mirroring ``emit_run_signal``. Without a
caller conn and without Postgres there is no subscription store to list, so the call is a no-op.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from idis.persistence.repositories.webhook_outbox import default_webhook_outbox
from idis.services.webhooks.emitter import emit_lifecycle_event
from idis.services.webhooks.service import get_webhook_service

if TYPE_CHECKING:
    from sqlalchemy import Connection

logger = logging.getLogger(__name__)

# Webhook lifecycle event types (the names subscribers subscribe to via ``webhooks.events``).
RUN_CLAIMED = "run.claimed"
RUN_COMPLETED = "run.completed"
RUN_FAILED = "run.failed"
RUN_CANCELLED = "run.cancelled"
DELIVERABLE_PRODUCED = "deliverable.produced"
DELIVERABLE_FAILED = "deliverable.failed"
HUMAN_GATE_ACTION_SUBMITTED = "human_gate.action.submitted"
DATA_ROOM_PACKAGE_CREATED = "data_room_package.created"


def notify_webhook_lifecycle(
    *,
    tenant_id: str,
    event_type: str,
    resource_type: str,
    resource_id: str,
    data: Mapping[str, Any] | None = None,
    conn: Connection | None = None,
) -> None:
    """Best-effort: emit a lifecycle webhook via the default service + outbox. Never raises."""
    try:
        service = get_webhook_service()
        outbox = default_webhook_outbox()
        if conn is not None:
            emit_lifecycle_event(
                tenant_id=tenant_id,
                event_type=event_type,
                resource_type=resource_type,
                resource_id=resource_id,
                data=data,
                webhook_service=service,
                outbox=outbox,
                conn=conn,
            )
            return

        from idis.persistence.db import begin_app_conn, is_postgres_configured, set_tenant_local

        if not is_postgres_configured():
            return  # no caller conn and no Postgres subscription store -> nothing to emit
        with begin_app_conn() as own_conn:
            set_tenant_local(own_conn, tenant_id)
            emit_lifecycle_event(
                tenant_id=tenant_id,
                event_type=event_type,
                resource_type=resource_type,
                resource_id=resource_id,
                data=data,
                webhook_service=service,
                outbox=outbox,
                conn=own_conn,
            )
    except Exception as exc:  # best-effort: a webhook must never break the audited operation
        logger.warning(
            "webhook lifecycle notify failed (event_type=%s resource_id=%s): %s",
            event_type,
            resource_id,
            exc,
        )

"""Canonical AuditEvent construction for non-middleware producers.

The durable sink deliberately does not repair producer output.  Internal
services use this builder to state their real actor, operation correlation,
resource, and safe payload before the event reaches any sink.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal

ActorType = Literal["HUMAN", "SERVICE"]
HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]


def build_canonical_audit_event(
    *,
    tenant_id: str,
    event_type: str,
    severity: str,
    summary: str,
    actor_id: str,
    request_id: str,
    method: str,
    path: str,
    status_code: int,
    resource_type: str,
    resource_id: str,
    actor_type: str = "SERVICE",
    actor_roles: Iterable[str] = (),
    deal_id: str | None = None,
    idempotency_key_sha256: str | None = None,
    safe: Mapping[str, Any] | None = None,
    hashes: Iterable[str] = (),
    refs: Iterable[str] = (),
) -> dict[str, Any]:
    """Build one immutable v6.3 AuditEvent for final sink validation.

    All provenance-bearing inputs are required.  This function only generates
    the event's own immutable identity and server occurrence time; it never
    substitutes an actor, request, or affected resource.
    """
    actor: dict[str, Any] = {
        "actor_type": actor_type,
        "actor_id": actor_id,
    }
    roles = sorted(set(actor_roles))
    if roles:
        actor["roles"] = roles

    request: dict[str, Any] = {
        "request_id": request_id,
        "method": method,
        "path": path,
        "status_code": status_code,
    }
    if idempotency_key_sha256 is not None:
        request["idempotency_key_sha256"] = idempotency_key_sha256

    resource: dict[str, Any] = {
        "resource_type": resource_type,
        "resource_id": resource_id,
    }
    if deal_id is not None:
        resource["deal_id"] = deal_id

    event: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tenant_id": tenant_id,
        "actor": actor,
        "request": request,
        "resource": resource,
        "event_type": event_type,
        "severity": severity,
        "summary": summary,
        "payload": {
            "safe": dict(safe or {}),
            "hashes": list(hashes),
            "refs": list(refs),
        },
    }
    return event

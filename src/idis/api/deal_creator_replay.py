"""Narrow compatibility repair for a historical ``createDeal`` replay.

Before Slice100 Task 4, a successful durable deal creation could commit without
the creator's direct ABAC assignment.  The durable idempotency response must not
continue to look successful while that promised invariant is absent.  This
module repairs only an exact, same-actor Postgres replay whose immutable audit
lineage is unambiguous.  It is deliberately not a legacy-deal backfill.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Never
from uuid import UUID

from sqlalchemy import text

from idis.idempotency.store import IdempotencyRecord, ScopeKey


class DealCreatorReplayInvariantError(RuntimeError):
    """Raised when a cached create response cannot be repaired safely."""

    code = "IDEMPOTENCY_REPLAY_INVARIANT_FAILED"

    def __init__(self) -> None:
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class DealCreatorReplayRepairOutcome:
    """Safe result of checking one durable ``createDeal`` replay."""

    deal_id: str
    repaired: bool


_IDEMPOTENCY_RECHECK_SQL = text(
    """
    SELECT payload_sha256, status_code, media_type, body_bytes, created_at
    FROM idempotency_records
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND actor_id = :actor_id
      AND method = :method
      AND operation_id = :operation_id
      AND idempotency_key = :idempotency_key
    FOR UPDATE
    """
)

_DEAL_LOCK_SQL = text(
    """
    SELECT deal_id, tenant_id, name, company_name, status, stage, tags,
           created_at, updated_at
    FROM deals
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND deal_id = CAST(:deal_id AS uuid)
    FOR UPDATE
    """
)

_ASSIGNMENT_EXISTS_SQL = text(
    """
    SELECT 1
    FROM deal_assignments
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND deal_id = CAST(:deal_id AS uuid)
      AND assignee_type = 'ACTOR'
      AND assignee_id = :actor_id
    LIMIT 1
    """
)

_CREATION_AUDIT_SQL = text(
    """
    SELECT event_id, tenant_id, occurred_at, event_type, request_id,
           idempotency_key, event
    FROM audit_events
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND event_type = 'deal.created'
      AND event -> 'resource' ->> 'resource_id' = :deal_id
    ORDER BY occurred_at, event_id
    """
)

_LATER_ASSIGNMENT_AUDIT_SQL = text(
    """
    SELECT event_id
    FROM audit_events
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND event_type IN ('rbac.assignment.created', 'rbac.assignment.deleted')
      AND event -> 'resource' ->> 'resource_id' = :deal_id
      AND occurred_at >= :creation_occurred_at
    LIMIT 1
    """
)

_INSERT_ASSIGNMENT_SQL = text(
    """
    INSERT INTO deal_assignments (tenant_id, deal_id, assignee_type, assignee_id)
    VALUES (CAST(:tenant_id AS uuid), CAST(:deal_id AS uuid), 'ACTOR', :actor_id)
    ON CONFLICT (tenant_id, deal_id, assignee_type, assignee_id) DO NOTHING
    RETURNING 1
    """
)

_DEAL_RESPONSE_KEYS = frozenset(
    {
        "deal_id",
        "name",
        "company_name",
        "status",
        "stage",
        "tags",
        "created_at",
        "updated_at",
    }
)


def repair_create_deal_replay(
    connection: Any,
    *,
    scope_key: ScopeKey,
    record: IdempotencyRecord,
) -> DealCreatorReplayRepairOutcome:
    """Repair one proven historical creator-assignment omission.

    The caller owns the request transaction and has already acquired the full
    idempotency-scope advisory lock through ``PostgresIdempotencyStore.get``.
    This function re-locks/rechecks the exact raw-key row, locks the visible
    deal, verifies immutable creation lineage, and writes on that same
    connection.  Any ambiguity fails closed without changing access state.
    """

    _require_scope(scope_key)
    response = _strict_deal_response(record)
    deal_id = response["deal_id"]
    assert isinstance(deal_id, str)

    _recheck_idempotency_record(connection, scope_key=scope_key, record=record)
    deal_row = connection.execute(
        _DEAL_LOCK_SQL,
        {"tenant_id": scope_key.tenant_id, "deal_id": deal_id},
    ).fetchone()
    if deal_row is None or not _deal_matches_response(
        deal_row,
        tenant_id=scope_key.tenant_id,
        response=response,
    ):
        _fail()

    assignment_params = {
        "tenant_id": scope_key.tenant_id,
        "deal_id": deal_id,
        "actor_id": scope_key.actor_id,
    }
    if connection.execute(_ASSIGNMENT_EXISTS_SQL, assignment_params).fetchone() is not None:
        return DealCreatorReplayRepairOutcome(deal_id=deal_id, repaired=False)

    creation_rows = connection.execute(
        _CREATION_AUDIT_SQL,
        {"tenant_id": scope_key.tenant_id, "deal_id": deal_id},
    ).fetchall()
    if len(creation_rows) != 1:
        _fail()
    creation_occurred_at = _validate_creation_event(
        creation_rows[0],
        scope_key=scope_key,
        deal_id=deal_id,
        deal_created_at=_as_utc(deal_row.created_at),
        payload_sha256=record.payload_sha256,
    )

    later_event = connection.execute(
        _LATER_ASSIGNMENT_AUDIT_SQL,
        {
            "tenant_id": scope_key.tenant_id,
            "deal_id": deal_id,
            "creation_occurred_at": creation_occurred_at,
        },
    ).fetchone()
    if later_event is not None:
        _fail()

    inserted = connection.execute(_INSERT_ASSIGNMENT_SQL, assignment_params).fetchone()
    if inserted is None:
        # A concurrent legitimate writer may have won the unique-index race.
        # Recheck on the same transaction; never claim that this request wrote it.
        if connection.execute(_ASSIGNMENT_EXISTS_SQL, assignment_params).fetchone() is None:
            _fail()
        return DealCreatorReplayRepairOutcome(deal_id=deal_id, repaired=False)
    return DealCreatorReplayRepairOutcome(deal_id=deal_id, repaired=True)


def _require_scope(scope_key: ScopeKey) -> None:
    if (
        scope_key.method != "POST"
        or scope_key.operation_id != "createDeal"
        or not _is_canonical_uuid(scope_key.tenant_id)
        or not scope_key.actor_id
        or not scope_key.idempotency_key
    ):
        _fail()


def _strict_deal_response(record: IdempotencyRecord) -> dict[str, Any]:
    if record.status_code != 201 or record.media_type != "application/json":
        _fail()
    try:
        decoded = record.body_bytes.decode("utf-8", errors="strict")
        value = json.loads(decoded, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        _fail()
    if not isinstance(value, dict) or set(value) != _DEAL_RESPONSE_KEYS:
        _fail()
    if (
        not _is_canonical_uuid(value.get("deal_id"))
        or not isinstance(value.get("name"), str)
        or not isinstance(value.get("company_name"), str)
        or value.get("status") != "NEW"
        or not (value.get("stage") is None or isinstance(value.get("stage"), str))
        or not isinstance(value.get("tags"), list)
        or not all(isinstance(tag, str) for tag in value["tags"])
        or value.get("updated_at") is not None
    ):
        _fail()
    _parse_utc_timestamp(value.get("created_at"))
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _recheck_idempotency_record(
    connection: Any,
    *,
    scope_key: ScopeKey,
    record: IdempotencyRecord,
) -> None:
    row = connection.execute(
        _IDEMPOTENCY_RECHECK_SQL,
        {
            "tenant_id": scope_key.tenant_id,
            "actor_id": scope_key.actor_id,
            "method": scope_key.method,
            "operation_id": scope_key.operation_id,
            "idempotency_key": scope_key.idempotency_key,
        },
    ).fetchone()
    if row is None:
        _fail()
    try:
        body_bytes = bytes(row.body_bytes)
        created_at = _as_utc(row.created_at)
        expected_created_at = _parse_utc_timestamp(record.created_at)
    except (AttributeError, TypeError, ValueError):
        _fail()
    if (
        row.payload_sha256 != record.payload_sha256
        or row.status_code != record.status_code
        or row.media_type != record.media_type
        or body_bytes != record.body_bytes
        or created_at != expected_created_at
    ):
        _fail()


def _deal_matches_response(
    row: Any,
    *,
    tenant_id: str,
    response: Mapping[str, Any],
) -> bool:
    try:
        row_tags = row.tags if isinstance(row.tags, list) else json.loads(row.tags)
        return (
            str(row.tenant_id) == tenant_id
            and str(row.deal_id) == response["deal_id"]
            and row.name == response["name"]
            and row.company_name == response["company_name"]
            and row.status == response["status"]
            and row.stage == response["stage"]
            and row_tags == response["tags"]
            and _as_utc(row.created_at) == _parse_utc_timestamp(response["created_at"])
            and row.updated_at is None
        )
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _validate_creation_event(
    row: Any,
    *,
    scope_key: ScopeKey,
    deal_id: str,
    deal_created_at: datetime,
    payload_sha256: str,
) -> datetime:
    expected_key_hash = sha256(scope_key.idempotency_key.encode("utf-8")).hexdigest()
    try:
        event = row.event
        if isinstance(event, str):
            event = json.loads(event, object_pairs_hook=_unique_object)
        occurred_at = _as_utc(row.occurred_at)
        actor = event["actor"]
        request = event["request"]
        resource = event["resource"]
        payload = event["payload"]
        event_occurred_at = _parse_utc_timestamp(event["occurred_at"])
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        _fail()
    if (
        str(row.tenant_id) != scope_key.tenant_id
        or row.event_type != "deal.created"
        or not isinstance(row.idempotency_key, str)
        or not hmac.compare_digest(row.idempotency_key, expected_key_hash)
        or not isinstance(event, Mapping)
        or str(event.get("event_id")) != str(row.event_id)
        or event.get("tenant_id") != scope_key.tenant_id
        or event.get("event_type") != "deal.created"
        or event_occurred_at != occurred_at
        or occurred_at < deal_created_at
        or not isinstance(actor, Mapping)
        or actor.get("actor_type") not in {"HUMAN", "SERVICE"}
        or not isinstance(actor.get("actor_id"), str)
        or not actor["actor_id"]
        or actor.get("actor_id") != scope_key.actor_id
        or not isinstance(request, Mapping)
        or not isinstance(request.get("request_id"), str)
        or not request["request_id"]
        or row.request_id != request["request_id"]
        or request.get("method") != "POST"
        or request.get("path") != "/v1/deals"
        or request.get("status_code") != 201
        or not isinstance(request.get("idempotency_key_sha256"), str)
        or not hmac.compare_digest(
            str(request["idempotency_key_sha256"]),
            expected_key_hash,
        )
        or not isinstance(resource, Mapping)
        or resource.get("resource_type") != "deal"
        or resource.get("resource_id") != deal_id
        or not isinstance(payload, Mapping)
        or not isinstance(payload.get("hashes"), list)
        or payload_sha256 not in payload["hashes"]
    ):
        _fail()
    return occurred_at


def _is_canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _parse_utc_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail()
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail()
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        _fail()
    return parsed.astimezone(UTC)


def _as_utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _fail() -> Never:
    raise DealCreatorReplayInvariantError()

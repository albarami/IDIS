"""Deterministic Task-7 erasure lineage and dedicated orchestration seam.

This module is intentionally persistence-neutral.  It owns the closed v19
identity/hash contract and coordinates injected ports around one dedicated,
session-locked connection.  PostgreSQL SQL, role construction, API routing,
and middleware suppression live at their respective composition boundaries.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid5

ERASURE_ATTEMPT_NAMESPACE = UUID("4520d8ed-e929-5486-821c-82f8901ae7fa")
ERASURE_EVENT_NAMESPACE = UUID("dd76cee1-28d5-55f8-b21a-eb64bc2e4be4")

TASK7_ERASURE_EVENT_OUTCOMES: tuple[tuple[str, str], ...] = (
    ("task7.erasure.attempted", "ATTEMPTED"),
    ("task7.erasure.failed", "FAILED"),
    ("task7.erasure.succeeded", "SUCCEEDED"),
)
TASK7_ERASURE_EVENT_TYPES: tuple[str, ...] = tuple(
    event_type for event_type, _outcome in TASK7_ERASURE_EVENT_OUTCOMES
)

_EVENT_OUTCOME_BY_TYPE = dict(TASK7_ERASURE_EVENT_OUTCOMES)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OBJECT_TARGET_HASH_PREFIX = "object_target_set_sha256:"


class Task7ErasureExecutionError(RuntimeError):
    """Fail-closed service boundary carrying a stable, content-free code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ErasureAttemptLineage:
    """Hash-bound coordinates shared by every event in one erasure attempt."""

    request_id: str
    attempt_id: str
    attempt_ordinal: int
    prior_status: str | None
    prior_attempt_event_id: str | None
    prior_attempt_event_hash: str | None


@dataclass(frozen=True, slots=True)
class Task7ErasureExecutionCommand:
    """Authenticated tenant/deal/request scope for one execution attempt."""

    tenant_id: str
    deal_id: str
    request_id: str
    actor_id: str

    def __post_init__(self) -> None:
        _require_uuid(self.tenant_id, "tenant_id")
        _require_uuid(self.deal_id, "deal_id")
        _require_uuid(self.request_id, "request_id")
        _require_nonblank(self.actor_id, "actor_id")


@dataclass(frozen=True, slots=True)
class Task7ErasureActorIdentity:
    """Deterministic auth identity persisted in every event of one attempt."""

    actor_type: str
    actor_id: str
    roles: tuple[str, ...]
    ip: str = "internal"
    user_agent: str = "idis-compliance"

    def __post_init__(self) -> None:
        actor_type = _require_nonblank(self.actor_type, "actor_type")
        if actor_type not in {"HUMAN", "SERVICE"}:
            raise ValueError("actor_type is not admitted")
        actor_id = _require_nonblank(self.actor_id, "actor_id")
        if isinstance(self.roles, (str, bytes)):
            raise ValueError("roles must be a sequence")
        normalized = tuple(sorted({_require_nonblank(role, "actor role") for role in self.roles}))
        if "ADMIN" not in normalized:
            raise ValueError("erasure actor must carry ADMIN")
        ip = _require_nonblank(self.ip, "actor ip")
        user_agent = _require_nonblank(self.user_agent, "actor user_agent")
        object.__setattr__(self, "actor_type", actor_type)
        object.__setattr__(self, "actor_id", actor_id)
        object.__setattr__(self, "roles", normalized)
        object.__setattr__(self, "ip", ip)
        object.__setattr__(self, "user_agent", user_agent)


@dataclass(frozen=True, slots=True)
class Task7ErasureExecutionResult:
    """Safe terminal projection returned to the authenticated API boundary."""

    request_id: str
    status: str
    executed_at: datetime
    counts: dict[str, int]
    terminal_event_id: str
    terminal_event_sha256: str

    def __post_init__(self) -> None:
        _require_uuid(self.request_id, "request_id")
        if self.status != "EXECUTED":
            raise ValueError("status must be EXECUTED")
        _require_aware_datetime(self.executed_at, "executed_at")
        normalized = _validated_counts(self.counts, require_object_reconciliation=True)
        object.__setattr__(self, "counts", normalized)
        _require_uuid(self.terminal_event_id, "terminal_event_id")
        _require_sha256(self.terminal_event_sha256, "terminal_event_sha256")


class Task7ErasureDedicatedConnection(Protocol):
    """Dedicated executor connection held for the complete serialized workflow."""

    def acquire_tenant_session_lock(self, tenant_id: str) -> None:
        """Acquire the reviewed tenant-scoped session advisory lock."""
        ...

    def release_tenant_session_lock(self, tenant_id: str) -> None:
        """Release the session advisory lock before returning the connection."""
        ...

    def begin(self) -> AbstractContextManager[Task7ErasureDedicatedConnection]:
        """Open one short database transaction on this same backend."""
        ...


class Task7ErasureObjectStore(Protocol):
    """Idempotently makes one already-bound object URI absent."""

    def reconcile_absent(self, uri: str) -> str:
        """Return DELETED or ALREADY_ABSENT after the object is absent."""
        ...


class Task7ErasurePort(Protocol):
    """Durable app/dedicated operations composed by the pure service."""

    def load_request_state(
        self,
        *,
        command: Task7ErasureExecutionCommand,
    ) -> Mapping[str, object]:
        """Load the tenant-scoped erasure request."""
        ...

    def load_lineage_events(
        self,
        *,
        command: Task7ErasureExecutionCommand,
    ) -> Sequence[Mapping[str, object]]:
        """Load deterministic Task-7 lineage events in durable order."""
        ...

    def list_object_targets(
        self,
        *,
        connection: Task7ErasureDedicatedConnection,
        command: Task7ErasureExecutionCommand,
    ) -> tuple[str, ...]:
        """Return the exact ordered object target set under the session lock."""
        ...

    def assert_no_active_holds(
        self,
        *,
        connection: Task7ErasureDedicatedConnection,
        command: Task7ErasureExecutionCommand,
    ) -> None:
        """Fail when any deal, document, or artifact target is held."""
        ...

    def persist_lineage_event(
        self,
        *,
        event: Mapping[str, object],
        request_status: str | None = None,
    ) -> str:
        """Insert-or-verify one event; optionally atomically mark FAILED."""
        ...

    def stage_object_rows_reconciled(
        self,
        *,
        command: Task7ErasureExecutionCommand,
        object_rows_reconciled: int,
    ) -> None:
        """Persist only the honest count required by the dedicated function."""
        ...

    def invoke_approved_erasure(
        self,
        *,
        connection: Task7ErasureDedicatedConnection,
        command: Task7ErasureExecutionCommand,
        attempt_id: str,
    ) -> Mapping[str, object]:
        """Invoke the exact hash-bound dedicated function inside a short transaction."""
        ...


@dataclass(frozen=True, slots=True)
class _ValidatedLineageEvent:
    raw: dict[str, Any]
    event_type: str
    outcome: str
    lineage: ErasureAttemptLineage
    occurred_at: datetime
    actor_identity: Task7ErasureActorIdentity
    object_target_set_sha256: str
    counts: dict[str, int] | None
    canonical_sha256: str

    @property
    def actor_id(self) -> str:
        return self.actor_identity.actor_id


@dataclass(frozen=True, slots=True)
class _LineageResolution:
    lineage: ErasureAttemptLineage
    open_attempt: _ValidatedLineageEvent | None
    succeeded: _ValidatedLineageEvent | None
    actor_identity: Task7ErasureActorIdentity

    @property
    def actor_id(self) -> str:
        return self.actor_identity.actor_id


@dataclass(frozen=True, slots=True)
class _RequestState:
    status: str
    executed_by: str | None
    executed_at: datetime | None
    counts: dict[str, int]


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("value is not canonical JSON") from exc


def _require_nonblank(value: object, name: str) -> str:
    if type(value) is not str or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be a nonblank exact string")
    return value


def _require_uuid(value: object, name: str) -> str:
    text = _require_nonblank(value, name)
    try:
        parsed = UUID(text)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{name} must be a UUID") from exc
    if str(parsed) != text:
        raise ValueError(f"{name} must use canonical lowercase UUID form")
    return text


def _require_sha256(value: object, name: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _require_aware_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be an aware datetime")
    return value


def _parse_timestamp(value: object, name: str) -> datetime:
    if isinstance(value, datetime):
        return _require_aware_datetime(value, name)
    text = _require_nonblank(value, name)
    try:
        suffix = "+00:00" if text.endswith("Z") else ""
        parsed = datetime.fromisoformat(text.removesuffix("Z") + suffix)
    except ValueError as exc:
        raise ValueError(f"{name} must be ISO-8601") from exc
    return _require_aware_datetime(parsed, name)


def _canonical_timestamp(value: datetime) -> str:
    aware = _require_aware_datetime(value, "occurred_at")
    return aware.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validated_counts(
    counts: object,
    *,
    require_object_reconciliation: bool,
) -> dict[str, int]:
    if not isinstance(counts, Mapping):
        raise ValueError("counts must be a mapping")
    normalized: dict[str, int] = {}
    for raw_key, raw_value in counts.items():
        key = _require_nonblank(raw_key, "count key")
        if type(raw_value) is not int or raw_value < 0:
            raise ValueError("count values must be non-negative integers")
        normalized[key] = raw_value
    if "objects_deleted" in normalized:
        raise ValueError("objects_deleted is not an admitted v19 count")
    if require_object_reconciliation and "object_rows_reconciled" not in normalized:
        raise ValueError("object_rows_reconciled is required")
    return normalized


def attempt_name_preimage(
    *,
    lineage: ErasureAttemptLineage,
    tenant_id: str,
    deal_id: str,
) -> tuple[object, ...]:
    """Return the exact reviewed UUIDv5 name tuple, including JSON nulls."""

    _require_uuid(tenant_id, "tenant_id")
    _require_uuid(deal_id, "deal_id")
    _validate_lineage_shape(lineage)
    return (
        "task7-erasure-attempt-v1",
        tenant_id,
        deal_id,
        lineage.request_id,
        lineage.attempt_ordinal,
        lineage.prior_status,
        lineage.prior_attempt_event_id,
        lineage.prior_attempt_event_hash,
    )


def _validate_lineage_shape(lineage: ErasureAttemptLineage) -> None:
    _require_uuid(lineage.request_id, "request_id")
    _require_uuid(lineage.attempt_id, "attempt_id")
    if type(lineage.attempt_ordinal) is not int or lineage.attempt_ordinal < 1:
        raise ValueError("attempt_ordinal must be a positive integer")
    prior = (
        lineage.prior_status,
        lineage.prior_attempt_event_id,
        lineage.prior_attempt_event_hash,
    )
    if lineage.attempt_ordinal == 1:
        if prior != (None, None, None):
            raise ValueError("first attempt requires explicit null prior coordinates")
        return
    if lineage.prior_status != "FAILED":
        raise ValueError("only FAILED permits a successor attempt")
    _require_uuid(lineage.prior_attempt_event_id, "prior_attempt_event_id")
    _require_sha256(lineage.prior_attempt_event_hash, "prior_attempt_event_hash")


def derive_attempt_lineage(
    *,
    tenant_id: str,
    deal_id: str,
    request_id: str,
    attempt_ordinal: int,
    prior_status: str | None,
    prior_attempt_event_id: str | None,
    prior_attempt_event_hash: str | None,
) -> ErasureAttemptLineage:
    """Build and validate one deterministic erasure attempt identity."""

    _require_uuid(tenant_id, "tenant_id")
    _require_uuid(deal_id, "deal_id")
    _require_uuid(request_id, "request_id")
    provisional = ErasureAttemptLineage(
        request_id=request_id,
        attempt_id="00000000-0000-0000-0000-000000000000",
        attempt_ordinal=attempt_ordinal,
        prior_status=prior_status,
        prior_attempt_event_id=prior_attempt_event_id,
        prior_attempt_event_hash=prior_attempt_event_hash,
    )
    _validate_lineage_shape(provisional)
    name = _canonical_bytes(
        attempt_name_preimage(
            lineage=provisional,
            tenant_id=tenant_id,
            deal_id=deal_id,
        )
    ).decode("utf-8")
    result = ErasureAttemptLineage(
        request_id=request_id,
        attempt_id=str(uuid5(ERASURE_ATTEMPT_NAMESPACE, name)),
        attempt_ordinal=attempt_ordinal,
        prior_status=prior_status,
        prior_attempt_event_id=prior_attempt_event_id,
        prior_attempt_event_hash=prior_attempt_event_hash,
    )
    _validate_lineage_shape(result)
    return result


def derive_event_identity(
    *,
    attempt_id: str,
    event_type: str,
    outcome: str,
) -> str:
    """Return the deterministic identity for one of the three admitted events."""

    _require_uuid(attempt_id, "attempt_id")
    expected_outcome = _EVENT_OUTCOME_BY_TYPE.get(event_type)
    if expected_outcome is None or outcome != expected_outcome:
        raise ValueError("unreviewed erasure event/outcome pair")
    preimage = (
        "task7-erasure-audit-event-v1",
        attempt_id,
        event_type,
        outcome,
    )
    return str(
        uuid5(
            ERASURE_EVENT_NAMESPACE,
            _canonical_bytes(preimage).decode("utf-8"),
        )
    )


def canonical_event_sha256(event: Mapping[str, object]) -> str:
    """Hash the exact canonical bytes of one complete stored audit event."""

    if not isinstance(event, Mapping):
        raise ValueError("event must be a mapping")
    return hashlib.sha256(_canonical_bytes(dict(event))).hexdigest()


def object_target_set_sha256(
    *,
    tenant_id: str,
    deal_id: str,
    ordered_uris: Sequence[str],
) -> str:
    """Hash the ordered URI-digest projection without exposing any raw URI."""

    _require_uuid(tenant_id, "tenant_id")
    _require_uuid(deal_id, "deal_id")
    if isinstance(ordered_uris, (str, bytes)) or not isinstance(ordered_uris, Sequence):
        raise ValueError("ordered_uris must be a sequence")
    uri_hashes: list[str] = []
    for uri in ordered_uris:
        exact_uri = _require_nonblank(uri, "object uri")
        if not exact_uri.startswith("idis://"):
            raise ValueError("object uri must use the idis scheme")
        uri_hashes.append(hashlib.sha256(exact_uri.encode("utf-8")).hexdigest())
    preimage = (
        "task7-erasure-object-target-set-v1",
        tenant_id,
        deal_id,
        len(uri_hashes),
        uri_hashes,
    )
    return hashlib.sha256(_canonical_bytes(preimage)).hexdigest()


def build_erasure_audit_event(
    *,
    tenant_id: str,
    deal_id: str,
    actor_id: str,
    actor_type: str = "HUMAN",
    actor_roles: Sequence[str] = ("ADMIN",),
    actor_ip: str = "internal",
    actor_user_agent: str = "idis-compliance",
    occurred_at: datetime,
    lineage: ErasureAttemptLineage,
    event_type: str,
    object_target_set_sha256: str,
    counts: Mapping[str, int] | None,
) -> dict[str, Any]:
    """Build one closed, safe Task-7 erasure lineage event."""

    _require_uuid(tenant_id, "tenant_id")
    _require_uuid(deal_id, "deal_id")
    actor = Task7ErasureActorIdentity(
        actor_type=actor_type,
        actor_id=actor_id,
        roles=tuple(actor_roles),
        ip=actor_ip,
        user_agent=actor_user_agent,
    )
    _validate_lineage_shape(lineage)
    target_digest = _require_sha256(
        object_target_set_sha256,
        "object_target_set_sha256",
    )
    outcome = _EVENT_OUTCOME_BY_TYPE.get(event_type)
    if outcome is None:
        raise ValueError("unreviewed erasure event type")
    if event_type == "task7.erasure.succeeded":
        safe_counts = _validated_counts(
            counts,
            require_object_reconciliation=True,
        )
    elif counts is not None:
        raise ValueError("only the succeeded event may carry counts")
    else:
        safe_counts = None

    safe: dict[str, Any] = {
        "request_id": lineage.request_id,
        "attempt_id": lineage.attempt_id,
        "attempt_ordinal": lineage.attempt_ordinal,
        "prior_status": lineage.prior_status,
        "prior_attempt_event_id": lineage.prior_attempt_event_id,
        "prior_attempt_event_hash": lineage.prior_attempt_event_hash,
        "outcome": outcome,
    }
    if safe_counts is not None:
        safe["counts"] = safe_counts

    event_id = derive_event_identity(
        attempt_id=lineage.attempt_id,
        event_type=event_type,
        outcome=outcome,
    )
    return {
        "event_id": event_id,
        "occurred_at": _canonical_timestamp(occurred_at),
        "tenant_id": tenant_id,
        "actor": {
            "actor_type": actor.actor_type,
            "actor_id": actor.actor_id,
            "roles": list(actor.roles),
            "ip": actor.ip,
            "user_agent": actor.user_agent,
        },
        "request": {
            "request_id": lineage.request_id,
            "method": "POST",
            "path": f"/v1/erasure-requests/{lineage.request_id}/execute",
            "status_code": 200,
        },
        "resource": {
            "resource_type": "erasure_request",
            "resource_id": lineage.request_id,
            "deal_id": deal_id,
        },
        "event_type": event_type,
        "severity": "CRITICAL",
        "summary": f"Task-7 erasure {outcome.lower()}",
        "payload": {
            "safe": safe,
            "hashes": [f"{_OBJECT_TARGET_HASH_PREFIX}{target_digest}"],
            "refs": [f"deal_id:{deal_id}"],
        },
    }


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _actor_identity_from_mapping(
    value: object,
) -> Task7ErasureActorIdentity:
    actor = _mapping(value, "event actor")
    roles = actor.get("roles")
    if not isinstance(roles, list) or any(type(role) is not str for role in roles):
        raise ValueError("event actor roles must be a string list")
    identity = Task7ErasureActorIdentity(
        actor_type=_require_nonblank(actor.get("actor_type"), "event actor_type"),
        actor_id=_require_nonblank(actor.get("actor_id"), "event actor_id"),
        roles=tuple(roles),
        ip=_require_nonblank(actor.get("ip"), "event actor ip"),
        user_agent=_require_nonblank(actor.get("user_agent"), "event actor user_agent"),
    )
    if roles != list(identity.roles):
        raise ValueError("event actor roles must be sorted and unique")
    return identity


def _lineage_from_safe(
    *,
    safe: Mapping[str, object],
    tenant_id: str,
    deal_id: str,
) -> ErasureAttemptLineage:
    expected_fields = {
        "request_id",
        "attempt_id",
        "attempt_ordinal",
        "prior_status",
        "prior_attempt_event_id",
        "prior_attempt_event_hash",
        "outcome",
    }
    unknown = set(safe) - (expected_fields | {"counts"})
    if unknown or not expected_fields <= set(safe):
        raise ValueError("erasure lineage safe fields are not closed")
    lineage = ErasureAttemptLineage(
        request_id=_require_uuid(safe["request_id"], "request_id"),
        attempt_id=_require_uuid(safe["attempt_id"], "attempt_id"),
        attempt_ordinal=safe["attempt_ordinal"],  # type: ignore[arg-type]
        prior_status=safe["prior_status"],  # type: ignore[arg-type]
        prior_attempt_event_id=safe["prior_attempt_event_id"],  # type: ignore[arg-type]
        prior_attempt_event_hash=safe["prior_attempt_event_hash"],  # type: ignore[arg-type]
    )
    _validate_lineage_shape(lineage)
    expected = derive_attempt_lineage(
        tenant_id=tenant_id,
        deal_id=deal_id,
        request_id=lineage.request_id,
        attempt_ordinal=lineage.attempt_ordinal,
        prior_status=lineage.prior_status,
        prior_attempt_event_id=lineage.prior_attempt_event_id,
        prior_attempt_event_hash=lineage.prior_attempt_event_hash,
    )
    if expected != lineage:
        raise ValueError("erasure attempt identity does not rederive")
    return lineage


def _target_digest_from_event(event: Mapping[str, object]) -> str:
    payload = _mapping(event.get("payload"), "event payload")
    hashes = payload.get("hashes")
    if not isinstance(hashes, list) or len(hashes) != 1:
        raise ValueError("event must carry exactly one object target digest")
    entry = hashes[0]
    if type(entry) is not str or not entry.startswith(_OBJECT_TARGET_HASH_PREFIX):
        raise ValueError("event object target digest is malformed")
    return _require_sha256(
        entry.removeprefix(_OBJECT_TARGET_HASH_PREFIX),
        "object_target_set_sha256",
    )


def _validate_stored_lineage_event(
    *,
    raw_event: Mapping[str, object],
    command: Task7ErasureExecutionCommand,
) -> _ValidatedLineageEvent:
    raw = copy_json_mapping(raw_event)
    event_type = _require_nonblank(raw.get("event_type"), "event_type")
    outcome = _EVENT_OUTCOME_BY_TYPE.get(event_type)
    if outcome is None:
        raise ValueError("unreviewed stored erasure event type")
    if raw.get("tenant_id") != command.tenant_id:
        raise ValueError("stored erasure tenant scope mismatch")
    request = _mapping(raw.get("request"), "event request")
    resource = _mapping(raw.get("resource"), "event resource")
    if request.get("request_id") != command.request_id:
        raise ValueError("stored erasure request scope mismatch")
    if (
        resource.get("resource_id") != command.request_id
        or resource.get("deal_id") != command.deal_id
    ):
        raise ValueError("stored erasure resource scope mismatch")
    actor_identity = _actor_identity_from_mapping(raw.get("actor"))
    payload = _mapping(raw.get("payload"), "event payload")
    safe = _mapping(payload.get("safe"), "event safe payload")
    lineage = _lineage_from_safe(
        safe=safe,
        tenant_id=command.tenant_id,
        deal_id=command.deal_id,
    )
    if lineage.request_id != command.request_id or safe.get("outcome") != outcome:
        raise ValueError("stored erasure lineage scope/outcome mismatch")
    expected_event_id = derive_event_identity(
        attempt_id=lineage.attempt_id,
        event_type=event_type,
        outcome=outcome,
    )
    if raw.get("event_id") != expected_event_id:
        raise ValueError("stored erasure event identity does not rederive")

    raw_counts = safe.get("counts")
    if event_type == "task7.erasure.succeeded":
        counts: dict[str, int] | None = _validated_counts(
            raw_counts,
            require_object_reconciliation=True,
        )
    elif raw_counts is not None:
        raise ValueError("non-success event cannot carry counts")
    else:
        counts = None
    return _ValidatedLineageEvent(
        raw=raw,
        event_type=event_type,
        outcome=outcome,
        lineage=lineage,
        occurred_at=_parse_timestamp(raw.get("occurred_at"), "event occurred_at"),
        actor_identity=actor_identity,
        object_target_set_sha256=_target_digest_from_event(raw),
        counts=counts,
        canonical_sha256=canonical_event_sha256(raw),
    )


def copy_json_mapping(value: Mapping[str, object]) -> dict[str, Any]:
    """Return an isolated exact-JSON mapping or fail closed."""

    decoded = json.loads(_canonical_bytes(dict(value)).decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("mapping canonicalization did not produce an object")
    return decoded


def _resolve_lineage(
    *,
    command: Task7ErasureExecutionCommand,
    raw_events: Sequence[Mapping[str, object]],
    current_actor_identity: Task7ErasureActorIdentity,
) -> _LineageResolution:
    if current_actor_identity.actor_id != command.actor_id:
        raise ValueError("current actor identity does not match command")
    events = tuple(
        _validate_stored_lineage_event(raw_event=raw, command=command) for raw in raw_events
    )
    if not events:
        lineage = derive_attempt_lineage(
            tenant_id=command.tenant_id,
            deal_id=command.deal_id,
            request_id=command.request_id,
            attempt_ordinal=1,
            prior_status=None,
            prior_attempt_event_id=None,
            prior_attempt_event_hash=None,
        )
        return _LineageResolution(
            lineage=lineage,
            open_attempt=None,
            succeeded=None,
            actor_identity=current_actor_identity,
        )

    index = 0
    expected_ordinal = 1
    prior_failed: _ValidatedLineageEvent | None = None
    while index < len(events):
        attempted = events[index]
        if attempted.event_type != "task7.erasure.attempted":
            raise ValueError("each erasure attempt must begin with attempted")
        lineage = attempted.lineage
        if lineage.attempt_ordinal != expected_ordinal:
            raise ValueError("erasure attempt ordinals must be contiguous")
        if expected_ordinal == 1:
            if (
                lineage.prior_status,
                lineage.prior_attempt_event_id,
                lineage.prior_attempt_event_hash,
            ) != (None, None, None):
                raise ValueError("first erasure attempt has non-null prior lineage")
        else:
            assert prior_failed is not None
            if (
                lineage.prior_status,
                lineage.prior_attempt_event_id,
                lineage.prior_attempt_event_hash,
            ) != (
                "FAILED",
                prior_failed.raw["event_id"],
                prior_failed.canonical_sha256,
            ):
                raise ValueError("erasure retry does not point to immediate failed event")

        index += 1
        if index == len(events):
            return _LineageResolution(
                lineage=lineage,
                open_attempt=attempted,
                succeeded=None,
                actor_identity=attempted.actor_identity,
            )
        terminal = events[index]
        if terminal.lineage != lineage or terminal.actor_identity != attempted.actor_identity:
            raise ValueError("erasure terminal does not share attempted lineage/actor")
        if terminal.object_target_set_sha256 != attempted.object_target_set_sha256:
            raise ValueError("erasure terminal target digest differs from attempted")
        if terminal.event_type == "task7.erasure.succeeded":
            if index != len(events) - 1:
                raise ValueError("succeeded erasure lineage cannot have successors")
            return _LineageResolution(
                lineage=lineage,
                open_attempt=None,
                succeeded=terminal,
                actor_identity=attempted.actor_identity,
            )
        if terminal.event_type != "task7.erasure.failed":
            raise ValueError("erasure attempt has no valid terminal")
        prior_failed = terminal
        expected_ordinal += 1
        index += 1

    assert prior_failed is not None
    lineage = derive_attempt_lineage(
        tenant_id=command.tenant_id,
        deal_id=command.deal_id,
        request_id=command.request_id,
        attempt_ordinal=expected_ordinal,
        prior_status="FAILED",
        prior_attempt_event_id=str(prior_failed.raw["event_id"]),
        prior_attempt_event_hash=prior_failed.canonical_sha256,
    )
    return _LineageResolution(
        lineage=lineage,
        open_attempt=None,
        succeeded=None,
        actor_identity=current_actor_identity,
    )


def _validate_request_state(
    raw_state: Mapping[str, object],
    *,
    command: Task7ErasureExecutionCommand,
) -> _RequestState:
    state = _mapping(raw_state, "request state")
    if (
        state.get("tenant_id") != command.tenant_id
        or state.get("deal_id") != command.deal_id
        or state.get("request_id") != command.request_id
    ):
        raise Task7ErasureExecutionError("ERASURE_RESOLUTION_FAILED")
    status = state.get("status")
    if status not in {"REQUESTED", "FAILED", "EXECUTED"}:
        raise Task7ErasureExecutionError("ERASURE_RESOLUTION_FAILED")
    executed_by_raw = state.get("executed_by")
    executed_by = (
        None if executed_by_raw is None else _require_nonblank(executed_by_raw, "executed_by")
    )
    executed_at_raw = state.get("executed_at")
    executed_at = (
        None if executed_at_raw is None else _parse_timestamp(executed_at_raw, "executed_at")
    )
    counts = _validated_counts(
        state.get("counts", {}),
        require_object_reconciliation=status == "EXECUTED",
    )
    if status == "EXECUTED" and (executed_by is None or executed_at is None):
        raise Task7ErasureExecutionError("ERASURE_RESOLUTION_FAILED")
    return _RequestState(
        status=str(status),
        executed_by=executed_by,
        executed_at=executed_at,
        counts=counts,
    )


def _result_from_succeeded(
    *,
    command: Task7ErasureExecutionCommand,
    state: _RequestState,
    succeeded: _ValidatedLineageEvent,
) -> Task7ErasureExecutionResult:
    if (
        state.status != "EXECUTED"
        or state.executed_at is None
        or state.executed_by is None
        or succeeded.counts != state.counts
        or succeeded.actor_id != state.executed_by
        or succeeded.occurred_at != state.executed_at
    ):
        raise Task7ErasureExecutionError("ERASURE_LINEAGE_INVALID")
    return Task7ErasureExecutionResult(
        request_id=command.request_id,
        status="EXECUTED",
        executed_at=state.executed_at,
        counts=state.counts,
        terminal_event_id=str(succeeded.raw["event_id"]),
        terminal_event_sha256=succeeded.canonical_sha256,
    )


def _validated_function_result(
    raw_result: Mapping[str, object],
    *,
    command: Task7ErasureExecutionCommand,
    expected_actor_id: str,
    expected_object_rows: int,
) -> _RequestState:
    result = _mapping(raw_result, "dedicated erasure result")
    if set(result) != {"status", "executed_by", "executed_at", "counts"}:
        raise ValueError("dedicated erasure result fields are not closed")
    if result.get("status") != "EXECUTED":
        raise ValueError("dedicated erasure result status mismatch")
    # Request/tenant/deal scope is bound by the three arguments passed to the
    # hash-bound function.  Its frozen v19 return shape deliberately contains
    # only the atomically persisted terminal projection above.
    _ = command
    executed_by = _require_nonblank(result.get("executed_by"), "executed_by")
    if executed_by != expected_actor_id:
        raise ValueError("dedicated erasure actor differs from attempted actor")
    executed_at = _parse_timestamp(result.get("executed_at"), "executed_at")
    counts = _validated_counts(
        result.get("counts"),
        require_object_reconciliation=True,
    )
    if counts["object_rows_reconciled"] != expected_object_rows:
        raise ValueError("object reconciliation count differs from target cardinality")
    return _RequestState(
        status="EXECUTED",
        executed_by=executed_by,
        executed_at=executed_at,
        counts=counts,
    )


class Task7ErasureExecutionService:
    """Compose deterministic audit, object reconciliation, and dedicated deletion."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], AbstractContextManager[Task7ErasureDedicatedConnection]],
        port: Task7ErasurePort,
        object_store: Task7ErasureObjectStore,
        clock: Callable[[], datetime],
        hold_checker: Callable[[str, str], None],
        actor_identity: Task7ErasureActorIdentity,
    ) -> None:
        self._connection_factory = connection_factory
        self._port = port
        self._object_store = object_store
        self._clock = clock
        self._hold_checker = hold_checker
        self._actor_identity = actor_identity

    def execute(
        self,
        command: Task7ErasureExecutionCommand,
    ) -> Task7ErasureExecutionResult:
        """Execute, recover, or idempotently return one exact erasure lineage."""

        # Request state and lineage are separate RLS reads.  Neither may grant
        # terminal or reconstruction authority before serialization: another
        # worker can commit between them.  Every path acquires the tenant lock,
        # then reloads both projections under that serialization point.
        return self._execute_open_or_new_attempt(command=command)

    def _load_execution_snapshot(
        self,
        *,
        command: Task7ErasureExecutionCommand,
    ) -> tuple[_RequestState, _LineageResolution]:
        state = _validate_request_state(
            self._port.load_request_state(command=command),
            command=command,
        )
        resolution = _resolve_lineage(
            command=command,
            raw_events=self._port.load_lineage_events(command=command),
            current_actor_identity=self._actor_identity,
        )
        if state.status == "FAILED" and resolution.lineage.attempt_ordinal == 1:
            raise Task7ErasureExecutionError("ERASURE_LINEAGE_INVALID")
        if state.status == "REQUESTED" and resolution.lineage.attempt_ordinal > 1:
            raise Task7ErasureExecutionError("ERASURE_LINEAGE_INVALID")
        return state, resolution

    def _reconstruct_succeeded(
        self,
        *,
        command: Task7ErasureExecutionCommand,
        state: _RequestState,
        resolution: _LineageResolution,
    ) -> Task7ErasureExecutionResult:
        open_attempt = resolution.open_attempt
        if (
            open_attempt is None
            or state.executed_at is None
            or state.executed_by is None
            or state.executed_by != open_attempt.actor_id
        ):
            raise Task7ErasureExecutionError("ERASURE_LINEAGE_INVALID")
        succeeded = build_erasure_audit_event(
            tenant_id=command.tenant_id,
            deal_id=command.deal_id,
            actor_id=state.executed_by,
            actor_type=resolution.actor_identity.actor_type,
            actor_roles=resolution.actor_identity.roles,
            actor_ip=resolution.actor_identity.ip,
            actor_user_agent=resolution.actor_identity.user_agent,
            occurred_at=state.executed_at,
            lineage=open_attempt.lineage,
            event_type="task7.erasure.succeeded",
            object_target_set_sha256=open_attempt.object_target_set_sha256,
            counts=state.counts,
        )
        terminal_hash = self._port.persist_lineage_event(event=succeeded)
        if terminal_hash != canonical_event_sha256(succeeded):
            raise Task7ErasureExecutionError("ERASURE_LINEAGE_INVALID")
        return Task7ErasureExecutionResult(
            request_id=command.request_id,
            status="EXECUTED",
            executed_at=state.executed_at,
            counts=state.counts,
            terminal_event_id=str(succeeded["event_id"]),
            terminal_event_sha256=terminal_hash,
        )

    def _execute_open_or_new_attempt(
        self,
        *,
        command: Task7ErasureExecutionCommand,
    ) -> Task7ErasureExecutionResult:
        with self._connection_factory() as connection:
            connection.acquire_tenant_session_lock(command.tenant_id)
            try:
                # This is the only authoritative snapshot: both app-role reads
                # occur after the dedicated session lock is held.
                state, resolution = self._load_execution_snapshot(command=command)
                if resolution.succeeded is not None:
                    return _result_from_succeeded(
                        command=command,
                        state=state,
                        succeeded=resolution.succeeded,
                    )
                if state.status == "EXECUTED":
                    return self._reconstruct_succeeded(
                        command=command,
                        state=state,
                        resolution=resolution,
                    )

                # Hold mutations acquire the same tenant advisory lock.  This
                # check must therefore occur after our session lock is held and
                # before target enumeration or any external object mutation.
                self._hold_checker(command.tenant_id, command.deal_id)
                self._port.assert_no_active_holds(
                    connection=connection,
                    command=command,
                )

                ordered_uris = tuple(
                    self._port.list_object_targets(
                        connection=connection,
                        command=command,
                    )
                )
                target_digest = object_target_set_sha256(
                    tenant_id=command.tenant_id,
                    deal_id=command.deal_id,
                    ordered_uris=ordered_uris,
                )
                if resolution.open_attempt is None:
                    attempt_event = build_erasure_audit_event(
                        tenant_id=command.tenant_id,
                        deal_id=command.deal_id,
                        actor_id=resolution.actor_id,
                        actor_type=resolution.actor_identity.actor_type,
                        actor_roles=resolution.actor_identity.roles,
                        actor_ip=resolution.actor_identity.ip,
                        actor_user_agent=resolution.actor_identity.user_agent,
                        occurred_at=self._clock(),
                        lineage=resolution.lineage,
                        event_type="task7.erasure.attempted",
                        object_target_set_sha256=target_digest,
                        counts=None,
                    )
                    persisted_hash = self._port.persist_lineage_event(event=attempt_event)
                    if persisted_hash != canonical_event_sha256(attempt_event):
                        raise Task7ErasureExecutionError("ERASURE_LINEAGE_INVALID")
                elif resolution.open_attempt.object_target_set_sha256 != target_digest:
                    raise Task7ErasureExecutionError("ERASURE_LINEAGE_INVALID")

                try:
                    object_rows_reconciled = self._reconcile_objects(ordered_uris)
                    self._port.stage_object_rows_reconciled(
                        command=command,
                        object_rows_reconciled=object_rows_reconciled,
                    )
                    with connection.begin():
                        raw_result = self._port.invoke_approved_erasure(
                            connection=connection,
                            command=command,
                            attempt_id=resolution.lineage.attempt_id,
                        )
                    executed_state = _validated_function_result(
                        raw_result,
                        command=command,
                        expected_actor_id=resolution.actor_id,
                        expected_object_rows=object_rows_reconciled,
                    )
                except Task7ErasureExecutionError:
                    raise
                except Exception as exc:
                    self._persist_failed(
                        command=command,
                        resolution=resolution,
                        target_digest=target_digest,
                    )
                    raise Task7ErasureExecutionError("ERASURE_EXECUTION_FAILED") from exc

                assert executed_state.executed_at is not None
                assert executed_state.executed_by is not None
                succeeded = build_erasure_audit_event(
                    tenant_id=command.tenant_id,
                    deal_id=command.deal_id,
                    actor_id=executed_state.executed_by,
                    actor_type=resolution.actor_identity.actor_type,
                    actor_roles=resolution.actor_identity.roles,
                    actor_ip=resolution.actor_identity.ip,
                    actor_user_agent=resolution.actor_identity.user_agent,
                    occurred_at=executed_state.executed_at,
                    lineage=resolution.lineage,
                    event_type="task7.erasure.succeeded",
                    object_target_set_sha256=target_digest,
                    counts=executed_state.counts,
                )
                terminal_hash = self._port.persist_lineage_event(event=succeeded)
                if terminal_hash != canonical_event_sha256(succeeded):
                    raise Task7ErasureExecutionError("ERASURE_LINEAGE_INVALID")
                return Task7ErasureExecutionResult(
                    request_id=command.request_id,
                    status="EXECUTED",
                    executed_at=executed_state.executed_at,
                    counts=executed_state.counts,
                    terminal_event_id=str(succeeded["event_id"]),
                    terminal_event_sha256=terminal_hash,
                )
            finally:
                connection.release_tenant_session_lock(command.tenant_id)

    def _reconcile_objects(self, ordered_uris: Sequence[str]) -> int:
        reconciled = 0
        for uri in ordered_uris:
            disposition = self._object_store.reconcile_absent(uri)
            if disposition not in {"DELETED", "ALREADY_ABSENT"}:
                raise ValueError("object store returned an unreviewed reconciliation disposition")
            reconciled += 1
        return reconciled

    def _persist_failed(
        self,
        *,
        command: Task7ErasureExecutionCommand,
        resolution: _LineageResolution,
        target_digest: str,
    ) -> None:
        failed = build_erasure_audit_event(
            tenant_id=command.tenant_id,
            deal_id=command.deal_id,
            actor_id=resolution.actor_id,
            actor_type=resolution.actor_identity.actor_type,
            actor_roles=resolution.actor_identity.roles,
            actor_ip=resolution.actor_identity.ip,
            actor_user_agent=resolution.actor_identity.user_agent,
            occurred_at=self._clock(),
            lineage=resolution.lineage,
            event_type="task7.erasure.failed",
            object_target_set_sha256=target_digest,
            counts=None,
        )
        expected_hash = canonical_event_sha256(failed)
        persisted_hash = self._port.persist_lineage_event(
            event=failed,
            request_status="FAILED",
        )
        if persisted_hash != expected_hash:
            raise Task7ErasureExecutionError("ERASURE_LINEAGE_INVALID")


@dataclass(frozen=True, slots=True)
class ErasureApiExecutionProjection:
    """Existing safe API result plus the private middleware proof seam."""

    request_id: str
    status: str
    executed_at: datetime
    counts: dict[str, int]
    terminal_lineage_proof: dict[str, object] | None


@dataclass(frozen=True, slots=True)
class _Task7PostgresErasureComposition:
    service: Task7ErasureExecutionService
    port: Any


@dataclass(frozen=True, slots=True)
class Task7ComplianceErasureObjectStore:
    """Idempotent adapter over the existing hold-aware production store."""

    tenant_ctx: Any
    store: Any

    def reconcile_absent(self, uri: str) -> str:
        exact_uri = _require_nonblank(uri, "object uri")
        if not exact_uri.startswith("idis://") or exact_uri == "idis://":
            raise ValueError("object uri must be a nonempty idis locator")
        key = exact_uri.removeprefix("idis://")

        from idis.compliance.retention import HoldTarget
        from idis.storage.errors import ObjectNotFoundError

        try:
            self.store.delete(
                self.tenant_ctx,
                key,
                resource_id=key,
                hold_target_type=HoldTarget.ARTIFACT,
            )
        except ObjectNotFoundError:
            return "ALREADY_ABSENT"
        return "DELETED"


class Task7ErasureAuthority:
    """Fail-closed marker returned by legacy global factories under PostgreSQL."""

    def scan_holds(self, tenant_id: str, deal_id: str) -> None:
        _ = (tenant_id, deal_id)
        raise RuntimeError("TASK7_ERASURE_API_COMPOSITION_REQUIRED")

    def erase_deal(self, tenant_id: str, deal_id: str) -> dict[str, int]:
        _ = (tenant_id, deal_id)
        raise RuntimeError("TASK7_ERASURE_API_COMPOSITION_REQUIRED")


def _build_postgres_erasure_api_composition(
    *,
    tenant_ctx: Any,
    hold_checker: Callable[[str, str], None],
    client_ip: str,
    user_agent: str,
) -> _Task7PostgresErasureComposition:
    """Build the app-role/dedicated-role split without any credential fallback."""

    from idis.persistence.db import (
        IDIS_ERASURE_DATABASE_URL_ENV,
        get_app_engine,
        get_erasure_engine,
    )
    from idis.persistence.erasure_db import (
        Task7DedicatedErasureConnectionFactory,
        Task7PostgresErasurePort,
    )
    from idis.services.ingestion.defaults import build_default_compliance_store

    if IDIS_ERASURE_DATABASE_URL_ENV != "IDIS_ERASURE_DATABASE_URL":
        raise RuntimeError("ERASURE_DATABASE_CONFIGURATION_INVALID")
    port = Task7PostgresErasurePort(app_engine=get_app_engine())
    object_store = Task7ComplianceErasureObjectStore(
        tenant_ctx=tenant_ctx,
        store=build_default_compliance_store(),
    )
    service = Task7ErasureExecutionService(
        connection_factory=Task7DedicatedErasureConnectionFactory(engine=get_erasure_engine()),
        port=port,
        object_store=object_store,
        clock=lambda: datetime.now(UTC),
        hold_checker=hold_checker,
        actor_identity=Task7ErasureActorIdentity(
            actor_type=tenant_ctx.actor_type,
            actor_id=tenant_ctx.actor_id,
            roles=tuple(tenant_ctx.roles),
            ip=client_ip,
            user_agent=user_agent,
        ),
    )
    return _Task7PostgresErasureComposition(
        service=service,
        port=port,
    )


def _terminal_lineage_proof(
    *,
    command: Task7ErasureExecutionCommand,
    result: Task7ErasureExecutionResult,
    raw_events: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    matches = tuple(
        _validate_stored_lineage_event(raw_event=raw, command=command)
        for raw in raw_events
        if raw.get("event_id") == result.terminal_event_id
    )
    if len(matches) != 1:
        raise Task7ErasureExecutionError("ERASURE_LINEAGE_INVALID")
    terminal = matches[0]
    if (
        terminal.event_type != "task7.erasure.succeeded"
        or terminal.outcome != "SUCCEEDED"
        or terminal.canonical_sha256 != result.terminal_event_sha256
    ):
        raise Task7ErasureExecutionError("ERASURE_LINEAGE_INVALID")
    return {
        "schema_version": "task7-erasure-terminal-lineage-proof-v1",
        "request_id": command.request_id,
        "attempt_id": terminal.lineage.attempt_id,
        "event_id": result.terminal_event_id,
        "event_type": terminal.event_type,
        "outcome": terminal.outcome,
        "durable": True,
        "replacement_event": copy_json_mapping(terminal.raw),
    }


def _execute_postgres_erasure_for_api(
    *,
    tenant_ctx: Any,
    request_id: str,
    hold_checker: Callable[[str, str], None],
    client_ip: str,
    user_agent: str,
) -> ErasureApiExecutionProjection:
    """Resolve tenant scope and execute only through the dedicated authority."""

    from idis.api.errors import IdisHttpError
    from idis.persistence.erasure_db import DedicatedErasureDatabaseError

    composition = _build_postgres_erasure_api_composition(
        tenant_ctx=tenant_ctx,
        hold_checker=hold_checker,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    try:
        request_state = composition.port.resolve_request(
            tenant_id=tenant_ctx.tenant_id,
            request_id=request_id,
        )
        if request_state is None:
            raise IdisHttpError(
                status_code=404,
                code="ERASURE_REQUEST_NOT_FOUND",
                message="Erasure request not found",
            )
        command = Task7ErasureExecutionCommand(
            tenant_id=tenant_ctx.tenant_id,
            deal_id=str(request_state["deal_id"]),
            request_id=request_id,
            actor_id=tenant_ctx.actor_id,
        )
        result = composition.service.execute(command)
        proof = _terminal_lineage_proof(
            command=command,
            result=result,
            raw_events=composition.port.load_lineage_events(command=command),
        )
    except IdisHttpError:
        raise
    except DedicatedErasureDatabaseError as exc:
        if str(exc) == "ERASURE_REQUEST_NOT_FOUND":
            raise IdisHttpError(
                status_code=404,
                code="ERASURE_REQUEST_NOT_FOUND",
                message="Erasure request not found",
            ) from exc
        raise IdisHttpError(
            status_code=500,
            code="ERASURE_EXECUTION_FAILED",
            message="Erasure execution failed",
        ) from exc
    except (Task7ErasureExecutionError, TypeError, ValueError) as exc:
        raise IdisHttpError(
            status_code=500,
            code="ERASURE_EXECUTION_FAILED",
            message="Erasure execution failed",
        ) from exc

    return ErasureApiExecutionProjection(
        request_id=result.request_id,
        status=result.status,
        executed_at=result.executed_at,
        counts=dict(result.counts),
        terminal_lineage_proof=proof,
    )


def execute_erasure_for_api(
    tenant_ctx: Any,
    request_id: str,
    audit_sink: Any,
    *,
    hold_checker: Callable[[str, str], None],
    client_ip: str,
    user_agent: str,
) -> ErasureApiExecutionProjection:
    """Select the dedicated PostgreSQL path or the hermetic non-PG twin."""

    from idis.persistence.db import is_postgres_configured

    if is_postgres_configured():
        return _execute_postgres_erasure_for_api(
            tenant_ctx=tenant_ctx,
            request_id=request_id,
            hold_checker=hold_checker,
            client_ip=client_ip,
            user_agent=user_agent,
        )

    from idis.compliance import erasure as legacy_erasure

    executed = legacy_erasure.execute_erasure(
        tenant_ctx,
        request_id,
        audit_sink,
        executor=legacy_erasure.get_erasure_executor(),
        hold_checker=hold_checker,
    )
    status = getattr(executed.status, "value", executed.status)
    if type(status) is not str or executed.executed_at is None:
        raise RuntimeError("ERASURE_LEGACY_RESULT_INVALID")
    return ErasureApiExecutionProjection(
        request_id=executed.request_id,
        status=status,
        executed_at=executed.executed_at,
        counts={key: int(value) for key, value in executed.counts.items()},
        terminal_lineage_proof=None,
    )

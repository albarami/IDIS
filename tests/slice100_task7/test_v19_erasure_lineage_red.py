"""RED-first pure contracts for the full-strict-v19 erasure lineage.

These tests deliberately wish one small production API at
``idis.compliance.task7_erasure``.  They are hermetic: the reference vectors
are derived independently from the reviewer-approved canonical tuples and do
not touch PostgreSQL, object storage, or any live provider.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
from dataclasses import fields
from datetime import UTC, datetime
from types import ModuleType
from typing import Any
from uuid import UUID

import pytest

TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEAL_ID = "22222222-2222-4222-8222-222222222222"
REQUEST_ID = "33333333-3333-4333-8333-333333333333"
ACTOR_ID = "admin-1"

ATTEMPT_NAMESPACE = UUID("4520d8ed-e929-5486-821c-82f8901ae7fa")
EVENT_NAMESPACE = UUID("dd76cee1-28d5-55f8-b21a-eb64bc2e4be4")

FIRST_ATTEMPT_ID = "fbb3b87f-d9d0-56b4-a983-084fb804d4ae"
FIRST_ATTEMPTED_EVENT_ID = "593d952b-1aff-5e4e-b673-fe02a28e8f2c"
FIRST_FAILED_EVENT_ID = "1407c4f8-0229-5b82-97ff-5f44eeb29e97"
FIRST_SUCCEEDED_EVENT_ID = "70e0d93e-ecb4-5099-944d-37c786d57854"
SECOND_ATTEMPT_ID = "6322f7a9-21dc-5c1a-bbe6-7fe1b7bec48d"

ORDERED_URIS = (
    "idis://tenant-safe/deal-safe/object-02",
    "idis://tenant-safe/deal-safe/object-01",
)
OBJECT_TARGET_SET_SHA256 = "96ebc50af7b3577d238cef7a32324a51f67a4d434d8f52b9b73eddff92eb2e3f"

EVENT_OUTCOMES = (
    ("task7.erasure.attempted", "ATTEMPTED"),
    ("task7.erasure.failed", "FAILED"),
    ("task7.erasure.succeeded", "SUCCEEDED"),
)


def _module() -> ModuleType:
    try:
        return importlib.import_module("idis.compliance.task7_erasure")
    except ModuleNotFoundError as exc:
        if exc.name != "idis.compliance.task7_erasure":
            raise
        pytest.fail(
            "full-strict-v19 erasure lineage module is missing: idis.compliance.task7_erasure",
            pytrace=False,
        )


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _first_lineage(module: ModuleType) -> Any:
    return module.derive_attempt_lineage(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        request_id=REQUEST_ID,
        attempt_ordinal=1,
        prior_status=None,
        prior_attempt_event_id=None,
        prior_attempt_event_hash=None,
    )


def _stored_succeeded_event() -> dict[str, Any]:
    """A fixed, validator-shaped stored event for the canonical hash vector."""

    return {
        "event_id": FIRST_SUCCEEDED_EVENT_ID,
        "occurred_at": "2026-07-17T16:00:00.000000Z",
        "tenant_id": TENANT_ID,
        "actor": {
            "actor_type": "HUMAN",
            "actor_id": ACTOR_ID,
            "roles": ["ADMIN"],
            "ip": "internal",
            "user_agent": "idis-compliance",
        },
        "request": {
            "request_id": REQUEST_ID,
            "method": "POST",
            "path": f"/v1/erasure-requests/{REQUEST_ID}/execute",
            "status_code": 200,
        },
        "resource": {
            "resource_type": "erasure_request",
            "resource_id": REQUEST_ID,
            "deal_id": DEAL_ID,
        },
        "event_type": "task7.erasure.succeeded",
        "severity": "CRITICAL",
        "summary": "Task-7 erasure succeeded",
        "payload": {
            "safe": {
                "request_id": REQUEST_ID,
                "attempt_id": FIRST_ATTEMPT_ID,
                "attempt_ordinal": 1,
                "prior_status": None,
                "prior_attempt_event_id": None,
                "prior_attempt_event_hash": None,
                "outcome": "SUCCEEDED",
                "counts": {
                    "document_artifacts": 2,
                    "object_rows_reconciled": 2,
                },
            },
            "hashes": [f"object_target_set_sha256:{'b' * 64}"],
            "refs": [f"deal_id:{DEAL_ID}"],
        },
    }


def _set_path(target: dict[str, Any], path: tuple[str, ...], value: object) -> None:
    current: dict[str, Any] = target
    for key in path[:-1]:
        next_value = current[key]
        assert isinstance(next_value, dict)
        current = next_value
    current[path[-1]] = value


def test_public_lineage_contract_is_small_typed_and_closed() -> None:
    module = _module()

    assert module.ERASURE_ATTEMPT_NAMESPACE == ATTEMPT_NAMESPACE
    assert module.ERASURE_EVENT_NAMESPACE == EVENT_NAMESPACE
    assert module.TASK7_ERASURE_EVENT_OUTCOMES == EVENT_OUTCOMES
    assert tuple(row[0] for row in EVENT_OUTCOMES) == module.TASK7_ERASURE_EVENT_TYPES
    assert tuple(field.name for field in fields(module.ErasureAttemptLineage)) == (
        "request_id",
        "attempt_id",
        "attempt_ordinal",
        "prior_status",
        "prior_attempt_event_id",
        "prior_attempt_event_hash",
    )


def test_first_attempt_uses_fixed_uuidv5_vector_and_explicit_null_prior_coordinates() -> None:
    module = _module()

    lineage = _first_lineage(module)

    assert lineage == module.ErasureAttemptLineage(
        request_id=REQUEST_ID,
        attempt_id=FIRST_ATTEMPT_ID,
        attempt_ordinal=1,
        prior_status=None,
        prior_attempt_event_id=None,
        prior_attempt_event_hash=None,
    )
    assert module.attempt_name_preimage(lineage=lineage, tenant_id=TENANT_ID, deal_id=DEAL_ID) == (
        "task7-erasure-attempt-v1",
        TENANT_ID,
        DEAL_ID,
        REQUEST_ID,
        1,
        None,
        None,
        None,
    )


def test_retry_after_failed_uses_fixed_uuidv5_vector_and_immediate_terminal_chain() -> None:
    module = _module()

    lineage = module.derive_attempt_lineage(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        request_id=REQUEST_ID,
        attempt_ordinal=2,
        prior_status="FAILED",
        prior_attempt_event_id=FIRST_FAILED_EVENT_ID,
        prior_attempt_event_hash="a" * 64,
    )

    assert lineage == module.ErasureAttemptLineage(
        request_id=REQUEST_ID,
        attempt_id=SECOND_ATTEMPT_ID,
        attempt_ordinal=2,
        prior_status="FAILED",
        prior_attempt_event_id=FIRST_FAILED_EVENT_ID,
        prior_attempt_event_hash="a" * 64,
    )


@pytest.mark.parametrize(
    (
        "attempt_ordinal",
        "prior_status",
        "prior_event_id",
        "prior_event_hash",
    ),
    (
        (0, None, None, None),
        (1, "FAILED", FIRST_FAILED_EVENT_ID, "a" * 64),
        (2, None, None, None),
        (2, "REQUESTED", FIRST_FAILED_EVENT_ID, "a" * 64),
        (2, "FAILED", None, "a" * 64),
        (2, "FAILED", FIRST_FAILED_EVENT_ID, None),
        (2, "FAILED", FIRST_FAILED_EVENT_ID, "not-a-sha256"),
    ),
)
def test_attempt_lineage_rejects_every_illegal_ordinal_or_prior_shape(
    attempt_ordinal: int,
    prior_status: str | None,
    prior_event_id: str | None,
    prior_event_hash: str | None,
) -> None:
    module = _module()

    with pytest.raises(ValueError):
        module.derive_attempt_lineage(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            request_id=REQUEST_ID,
            attempt_ordinal=attempt_ordinal,
            prior_status=prior_status,
            prior_attempt_event_id=prior_event_id,
            prior_attempt_event_hash=prior_event_hash,
        )


@pytest.mark.parametrize(
    ("event_type", "outcome", "expected_event_id"),
    (
        ("task7.erasure.attempted", "ATTEMPTED", FIRST_ATTEMPTED_EVENT_ID),
        ("task7.erasure.failed", "FAILED", FIRST_FAILED_EVENT_ID),
        ("task7.erasure.succeeded", "SUCCEEDED", FIRST_SUCCEEDED_EVENT_ID),
    ),
)
def test_only_the_three_reviewed_event_outcome_pairs_have_fixed_uuidv5_identities(
    event_type: str,
    outcome: str,
    expected_event_id: str,
) -> None:
    module = _module()

    assert (
        module.derive_event_identity(
            attempt_id=FIRST_ATTEMPT_ID,
            event_type=event_type,
            outcome=outcome,
        )
        == expected_event_id
    )


@pytest.mark.parametrize(
    ("event_type", "outcome"),
    (
        ("task7.erasure.executed", "EXECUTED"),
        ("task7.erasure.attempted", "FAILED"),
        ("task7.erasure.failed", "ATTEMPTED"),
        ("task7.erasure.succeeded", "FAILED"),
        ("task7.unrelated.succeeded", "SUCCEEDED"),
    ),
)
def test_unreviewed_event_names_or_mismatched_outcomes_fail_closed(
    event_type: str,
    outcome: str,
) -> None:
    module = _module()

    with pytest.raises(ValueError):
        module.derive_event_identity(
            attempt_id=FIRST_ATTEMPT_ID,
            event_type=event_type,
            outcome=outcome,
        )


def test_ordered_object_target_digest_matches_fixed_vector_and_is_order_sensitive() -> None:
    module = _module()

    digest = module.object_target_set_sha256(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        ordered_uris=ORDERED_URIS,
    )
    reversed_digest = module.object_target_set_sha256(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        ordered_uris=tuple(reversed(ORDERED_URIS)),
    )

    assert digest == OBJECT_TARGET_SET_SHA256
    assert reversed_digest != digest
    assert all(uri not in digest for uri in ORDERED_URIS)


def test_canonical_event_hash_is_sha256_of_exact_stored_event_bytes() -> None:
    module = _module()
    event = _stored_succeeded_event()

    digest = module.canonical_event_sha256(event)

    assert digest == hashlib.sha256(_canonical_bytes(event)).hexdigest()
    assert digest == "1b00a63496273ecc76bdfe9801d46d61123dbdff08ec6ac3deb9d6cd5a602082"


@pytest.mark.parametrize(
    ("path", "replacement"),
    (
        (("actor", "actor_id"), "different-admin"),
        (("tenant_id",), "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        (("resource", "deal_id"), "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        (("payload", "safe", "outcome"), "FAILED"),
        (("occurred_at",), "2026-07-17T16:00:00.000001Z"),
        (("payload", "safe", "attempt_ordinal"), 2),
        (("payload", "safe", "counts"), {"object_rows_reconciled": 3}),
        (("payload", "hashes"), [f"object_target_set_sha256:{'c' * 64}"]),
    ),
)
def test_canonical_event_hash_binds_every_reviewed_lineage_and_evidence_coordinate(
    path: tuple[str, ...],
    replacement: object,
) -> None:
    module = _module()
    baseline = _stored_succeeded_event()
    mutated = copy.deepcopy(baseline)
    _set_path(mutated, path, replacement)

    assert module.canonical_event_sha256(mutated) != module.canonical_event_sha256(baseline)


def test_attempted_event_carries_safe_lineage_and_target_digest_without_raw_uris() -> None:
    module = _module()
    lineage = _first_lineage(module)

    event = module.build_erasure_audit_event(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        actor_id=ACTOR_ID,
        occurred_at=datetime(2026, 7, 17, 16, 0, tzinfo=UTC),
        lineage=lineage,
        event_type="task7.erasure.attempted",
        object_target_set_sha256=OBJECT_TARGET_SET_SHA256,
        counts=None,
    )

    assert event["event_id"] == FIRST_ATTEMPTED_EVENT_ID
    assert event["event_type"] == "task7.erasure.attempted"
    assert event["tenant_id"] == TENANT_ID
    assert event["actor"]["actor_id"] == ACTOR_ID
    assert event["resource"]["deal_id"] == DEAL_ID
    assert event["payload"]["safe"] == {
        "request_id": REQUEST_ID,
        "attempt_id": FIRST_ATTEMPT_ID,
        "attempt_ordinal": 1,
        "prior_status": None,
        "prior_attempt_event_id": None,
        "prior_attempt_event_hash": None,
        "outcome": "ATTEMPTED",
    }
    assert event["payload"]["hashes"] == [f"object_target_set_sha256:{OBJECT_TARGET_SET_SHA256}"]
    encoded = json.dumps(event, sort_keys=True)
    assert all(uri not in encoded for uri in ORDERED_URIS)


def test_succeeded_event_binds_exact_persisted_counts_and_executed_timestamp() -> None:
    module = _module()
    lineage = _first_lineage(module)
    counts = {
        "document_artifacts": 2,
        "object_rows_reconciled": 2,
        "runs": 1,
    }
    executed_at = datetime(2026, 7, 17, 16, 1, 2, 345678, tzinfo=UTC)

    event = module.build_erasure_audit_event(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        actor_id=ACTOR_ID,
        occurred_at=executed_at,
        lineage=lineage,
        event_type="task7.erasure.succeeded",
        object_target_set_sha256=OBJECT_TARGET_SET_SHA256,
        counts=counts,
    )

    assert event["occurred_at"] == "2026-07-17T16:01:02.345678Z"
    assert event["payload"]["safe"]["outcome"] == "SUCCEEDED"
    assert event["payload"]["safe"]["counts"] == counts
    assert "objects_deleted" not in event["payload"]["safe"]["counts"]


@pytest.mark.parametrize(
    "event_type",
    (
        "task7.erasure.attempted",
        "task7.erasure.failed",
        "task7.erasure.succeeded",
    ),
)
def test_every_reviewed_task7_erasure_event_passes_the_production_audit_validator(
    event_type: str,
) -> None:
    from idis.validators.audit_event_validator import validate_audit_event

    module = _module()
    lineage = _first_lineage(module)
    event = module.build_erasure_audit_event(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        actor_id=ACTOR_ID,
        occurred_at=datetime(2026, 7, 17, 16, 0, tzinfo=UTC),
        lineage=lineage,
        event_type=event_type,
        object_target_set_sha256=OBJECT_TARGET_SET_SHA256,
        counts=(
            {"document_artifacts": 2, "object_rows_reconciled": 2}
            if event_type == "task7.erasure.succeeded"
            else None
        ),
    )

    result = validate_audit_event(event)

    assert result.passed is True, [error.code for error in result.errors]


def test_unreviewed_task7_event_name_remains_rejected_by_the_audit_validator() -> None:
    from idis.validators.audit_event_validator import validate_audit_event

    event = _stored_succeeded_event()
    event["event_type"] = "task7.erasure.executed"

    result = validate_audit_event(event)

    assert result.passed is False
    assert "INVALID_EVENT_TYPE" in {error.code for error in result.errors}

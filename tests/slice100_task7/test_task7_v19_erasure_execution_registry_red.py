"""RED-first closed registry contract for full-strict-v19 erasure authority."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V18_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v18_reviewed_preimage_v1.json"
_V19_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v19_reviewed_preimage_v1.json"

_RUNNER_SIGNATURE = "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)"
_ATTEMPT_NAMESPACE = "4520d8ed-e929-5486-821c-82f8901ae7fa"
_EVENT_NAMESPACE = "dd76cee1-28d5-55f8-b21a-eb64bc2e4be4"
_ATTEMPT_NAME_TUPLE = (
    "task7-erasure-attempt-v1",
    "tenant_id",
    "deal_id",
    "request_id",
    "attempt_ordinal",
    "prior_status",
    "prior_event_id_or_null",
    "prior_event_hash_or_null",
)
_EVENT_NAME_TUPLE = (
    "task7-erasure-audit-event-v1",
    "attempt_id",
    "event_type",
    "outcome",
)
_LINEAGE_FIELDS = (
    "request_id",
    "attempt_id",
    "attempt_ordinal",
    "prior_status",
    "prior_attempt_event_id",
    "prior_attempt_event_hash",
    "outcome",
)
_EVENT_OUTCOME_ROWS = (
    ("task7.erasure.attempted", "ATTEMPTED", False, False),
    ("task7.erasure.failed", "FAILED", True, True),
    ("task7.erasure.succeeded", "SUCCEEDED", True, False),
)
_AUDIT_LINEAGE_CONTRACT = (
    "task7-erasure-audit-lineage-v1",
    _EVENT_OUTCOME_ROWS,
    _LINEAGE_FIELDS,
    "ADMIN",
    1,
    "OPEN_ATTEMPT_REUSED",
    "FAILED_TERMINAL_REQUIRED_FOR_NEXT_ATTEMPT",
    "NEXT_ATTEMPT_ORDINAL_EQUALS_PRIOR_PLUS_ONE",
    "PRIOR_COORDINATES_BIND_IMMEDIATE_FAILED_TERMINAL",
    "SHA256_CANONICAL_VALIDATED_STORED_EVENT",
    "DETERMINISTIC_EVENT_ID_CONFLICT_VERIFY",
)
_OBJECT_RECONCILIATION_CONTRACT = (
    "task7-erasure-object-reconciliation-v1",
    "task7-erasure-object-target-set-v1",
    ("tenant_id", "deal_id", "artifact_row_count", "ordered_uri_sha256_tuple"),
    "SHA256_CANONICAL_JSON",
    "object_rows_reconciled",
    "NON_NEGATIVE_INTEGER_EQUALS_ARTIFACT_ROW_COUNT",
    "ALREADY_ABSENT_COUNTS_AS_RECONCILED",
    "RAW_URIS_FORBIDDEN",
    "objects_deleted",
    "FORBIDDEN",
    ("EXECUTED", "executed_by", "executed_at", "counts"),
    "SAME_TRANSACTION_AS_RELATION_DELETION",
    "EXECUTED_OPEN_ATTEMPT_RECONSTRUCTS_SUCCEEDED_FROM_PERSISTED_COUNTS_AND_EXECUTED_AT",
    (
        "ZERO_OBJECT_RECONCILIATION",
        "ZERO_DATABASE_DELETION",
        "ZERO_FUNCTION_INVOCATION",
        "ZERO_RECOUNT",
    ),
)

_V1_FIELDS = (
    "execution_ordinal",
    "execution_id",
    "execution_version",
    "runner_function_signature",
    "activation_state",
    "current_relation_tuple",
    "future_relation_tuple",
    "complete_relation_tuple",
    "declared_read_relation_tuple",
    "advisory_lock_tuple",
    "current_body_sql_utf8_hex_tuple",
    "current_acl_sql_utf8_hex_tuple",
    "future_body_sql_utf8_hex_tuple",
    "future_acl_sql_utf8_hex_tuple",
    "future_activation_census_tuple",
    "future_activation_census_sha256",
    "hold_lock_contract_tuple",
    "guard_authority_contract_tuple",
    "legacy_guard_replacement_tuple",
    "hold_lock_upgrade_sql_utf8_hex_tuple",
    "hold_lock_downgrade_sql_utf8_hex_tuple",
    "legacy_guard_upgrade_sql_utf8_hex_tuple",
    "legacy_guard_downgrade_sql_utf8_hex_tuple",
    "future_activation_sql_utf8_hex_tuple",
    "future_restoration_sql_utf8_hex_tuple",
    "legal_red_case_tuple",
    "illegal_red_case_tuple",
    "direct_sql_red_case_tuple",
    "expected_sha256",
)
_V2_FIELDS = (
    *_V1_FIELDS[:10],
    "audit_lineage_contract_tuple",
    "object_reconciliation_contract_tuple",
    *_V1_FIELDS[10:],
)
_READ_CLOSURE = (
    "erasure_requests",
    "deterministic_calculations",
    "runs",
    "human_gates",
    "legal_holds",
    "document_artifacts",
    "audit_events",
)
_DELETE_RE = re.compile(
    r"\bDELETE\s+FROM\s+public\.(?P<table>[a-z][a-z0-9_]*)\b",
    re.IGNORECASE,
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _v18() -> dict[str, Any]:
    return _load_json(_V18_ARTIFACT)


def _v19() -> dict[str, Any]:
    assert _V19_ARTIFACT.is_file(), (
        "TASK7_V19_ERASURE_EXECUTION_REGISTRY_NOT_IMPLEMENTED:"
        "contracts/full_strict_v19_reviewed_preimage_v1.json"
    )
    artifact = _load_json(_V19_ARTIFACT)
    assert artifact["metadata"]["pipeline_contract_version"] == "full-strict-v19"
    return artifact


def _execution_row(artifact: dict[str, Any]) -> dict[str, Any]:
    section = artifact["erasure_execution_registry"]
    assert tuple(section) == ("rows", "expected_sha256")
    rows = section["rows"]
    assert isinstance(rows, list) and len(rows) == 1
    row = rows[0]
    assert isinstance(row, dict)
    return row


def _deep_tuple(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_deep_tuple(item) for item in value)
    if isinstance(value, dict):
        return {key: _deep_tuple(item) for key, item in value.items()}
    return value


def _decode_sql(row: dict[str, Any], field: str) -> tuple[str, ...]:
    encoded = row[field]
    assert isinstance(encoded, list) and encoded
    decoded = tuple(bytes.fromhex(item).decode("utf-8") for item in encoded)
    assert all(statement == statement.strip() for statement in decoded)
    assert all("\r" not in statement and "\n" not in statement for statement in decoded)
    assert all(statement == " ".join(statement.split()) for statement in decoded)
    return decoded


def _delete_order(statements: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        match.group("table").lower()
        for statement in statements
        for match in _DELETE_RE.finditer(statement)
    )


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_v19_adds_exact_uuid_v5_namespaces_and_preimages() -> None:
    v18_rows = _v18()["uuid_namespace_registry"]["rows"]
    v19_rows = _v19()["uuid_namespace_registry"]["rows"]

    assert v19_rows[: len(v18_rows)] == v18_rows
    assert _deep_tuple(v19_rows[len(v18_rows) :]) == (
        ("erasure_attempt_id", _ATTEMPT_NAMESPACE, _ATTEMPT_NAME_TUPLE),
        ("erasure_audit_event_id", _EVENT_NAMESPACE, _EVENT_NAME_TUPLE),
    )
    assert len({row[0] for row in v19_rows}) == len(v19_rows)
    assert len({row[1] for row in v19_rows}) == len(v19_rows)


def test_v19_uses_v2_execution_schema_without_mutating_v1() -> None:
    v18_schemas = dict(_v18()["tuple_schema_registry"]["rows"])
    v19_schemas = dict(_v19()["tuple_schema_registry"]["rows"])

    assert tuple(v18_schemas["ErasureExecutionSpecV1"]) == _V1_FIELDS
    assert tuple(v19_schemas["ErasureExecutionSpecV1"]) == _V1_FIELDS
    assert tuple(v19_schemas["ErasureExecutionSpecV2"]) == _V2_FIELDS
    assert tuple(v19_schemas["ErasureAuditLineageSpecV1"]) == _LINEAGE_FIELDS

    row = _execution_row(_v19())
    assert tuple(row) == _V2_FIELDS
    assert row["execution_version"] == "2"
    assert row["runner_function_signature"] == _RUNNER_SIGNATURE


def test_v19_closes_event_outcome_lineage_and_object_reconstruction_semantics() -> None:
    row = _execution_row(_v19())

    assert _deep_tuple(row["audit_lineage_contract_tuple"]) == _AUDIT_LINEAGE_CONTRACT
    assert (
        _deep_tuple(row["object_reconciliation_contract_tuple"]) == _OBJECT_RECONCILIATION_CONTRACT
    )

    event_rows = _deep_tuple(row["audit_lineage_contract_tuple"])[1]
    assert event_rows == _EVENT_OUTCOME_ROWS
    assert {item[0] for item in event_rows} == {
        "task7.erasure.attempted",
        "task7.erasure.failed",
        "task7.erasure.succeeded",
    }
    assert {item[1] for item in event_rows} == {"ATTEMPTED", "FAILED", "SUCCEEDED"}
    assert sum(not item[2] for item in event_rows) == 1
    assert sum(item[3] for item in event_rows) == 1

    canonical = _canonical_bytes(row["audit_lineage_contract_tuple"])
    assert (
        hashlib.sha256(canonical).hexdigest()
        != hashlib.sha256(canonical.replace(b"ADMIN", b"USER")).hexdigest()
    )


def test_v19_preserves_current48_future38_runner86_exactly() -> None:
    v18_row = _execution_row(_v18())
    v19_row = _execution_row(_v19())

    current = tuple(v19_row["current_relation_tuple"])
    future = tuple(v19_row["future_relation_tuple"])
    complete = tuple(v19_row["complete_relation_tuple"])
    assert current == tuple(v18_row["current_relation_tuple"])
    assert future == tuple(v18_row["future_relation_tuple"])
    assert complete == tuple(v18_row["complete_relation_tuple"])
    assert len(current) == 48
    assert len(future) == 38
    assert len(complete) == 86
    assert set(current).isdisjoint(future)
    assert set(current) | set(future) == set(complete)
    assert tuple(relation for relation in complete if relation in set(current)) == current

    assert _delete_order(_decode_sql(v19_row, "current_body_sql_utf8_hex_tuple")) == current
    assert _delete_order(_decode_sql(v19_row, "future_body_sql_utf8_hex_tuple")) == complete


def test_v19_declares_exact_seven_relation_read_closure_with_audit_events_last() -> None:
    row = _execution_row(_v19())

    assert tuple(row["declared_read_relation_tuple"]) == _READ_CLOSURE
    assert tuple(_execution_row(_v18())["declared_read_relation_tuple"]) == _READ_CLOSURE[:-1]
    assert len(row["declared_read_relation_tuple"]) == 7

    for field in ("current_body_sql_utf8_hex_tuple", "future_body_sql_utf8_hex_tuple"):
        sql = " ".join(_decode_sql(row, field))
        compact = "".join(sql.lower().split())
        assert "frompublic.audit_events" in compact
        assert "'task7.erasure.attempted'" in compact
        assert "'attempted'" in compact
        assert compact.count("frompublic.audit_events") == 1


def test_v19_current_and_future_acl_are_exactly_column_limited() -> None:
    row = _execution_row(_v19())

    for field in ("current_acl_sql_utf8_hex_tuple", "future_acl_sql_utf8_hex_tuple"):
        statements = _decode_sql(row, field)
        statement_set = set(statements)
        assert (
            "GRANT SELECT (event_id,tenant_id,event_type,event) ON TABLE "
            "public.audit_events TO idis_erasure_function_owner"
        ) in statement_set
        assert (
            "GRANT SELECT (request_id,tenant_id,deal_id,status,counts) ON TABLE "
            "public.erasure_requests TO idis_erasure_function_owner"
        ) in statement_set
        assert (
            "GRANT UPDATE (request_id,status,executed_by,executed_at,counts) ON TABLE "
            "public.erasure_requests TO idis_erasure_function_owner"
        ) in statement_set
        assert (
            "REVOKE ALL PRIVILEGES ON TABLE public.audit_events FROM idis_erasure_function_owner"
        ) in statement_set
        assert (
            "REVOKE ALL PRIVILEGES ON TABLE public.audit_events FROM idis_erasure_executor"
        ) in statement_set
        assert (
            f"GRANT EXECUTE ON FUNCTION {_RUNNER_SIGNATURE} TO idis_erasure_executor"
        ) in statement_set

        grants = tuple(item for item in statements if item.startswith("GRANT "))
        update_grants = tuple(item for item in grants if item.startswith("GRANT UPDATE "))
        audit_grants = tuple(item for item in grants if "public.audit_events" in item)
        assert update_grants == (
            "GRANT UPDATE (request_id,status,executed_by,executed_at,counts) ON TABLE "
            "public.erasure_requests TO idis_erasure_function_owner",
        )
        assert audit_grants == (
            "GRANT SELECT (event_id,tenant_id,event_type,event) ON TABLE "
            "public.audit_events TO idis_erasure_function_owner",
        )
        assert not any(item.startswith("GRANT INSERT ") for item in grants)
        assert not any(" TO idis_app" in item for item in grants)
        assert not any("WITH ADMIN OPTION" in item for item in grants)
        assert not any("GRANT idis_" in item for item in grants)


def test_v19_function_atomically_persists_terminal_state_counts_and_returns_them() -> None:
    row = _execution_row(_v19())

    for field in ("current_body_sql_utf8_hex_tuple", "future_body_sql_utf8_hex_tuple"):
        sql = " ".join(_decode_sql(row, field))
        compact = "".join(sql.lower().split())

        assert "object_rows_reconciled" in compact
        assert "objects_deleted" not in compact
        assert "status='executed'" in compact
        assert "executed_by=" in compact
        assert "executed_at=pg_catalog.statement_timestamp()" in compact
        assert "counts=" in compact
        assert "updatepublic.erasure_requests" in compact
        assert "returning" in compact
        assert all(
            column in compact for column in ("status", "executed_by", "executed_at", "counts")
        )

        last_delete = compact.rindex("deletefrompublic.")
        terminal_update = compact.index("updatepublic.erasure_requests")
        assert terminal_update > last_delete
        assert compact.rindex("returning") > terminal_update


def test_v19_registry_hash_covers_both_new_contract_tuples() -> None:
    row = _execution_row(_v19())
    row_hash = row["expected_sha256"]
    assert re.fullmatch(r"[0-9a-f]{64}", row_hash)

    for field in ("audit_lineage_contract_tuple", "object_reconciliation_contract_tuple"):
        mutated = json.loads(json.dumps(row))
        mutated[field][0] += "-CORRUPTED"
        payload = {key: value for key, value in mutated.items() if key != "expected_sha256"}
        original_payload = {key: value for key, value in row.items() if key != "expected_sha256"}
        assert (
            hashlib.sha256(_canonical_bytes(payload)).hexdigest()
            != hashlib.sha256(_canonical_bytes(original_payload)).hexdigest()
        )

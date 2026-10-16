"""Materialize the bounded, unfrozen full-strict-v19 reviewed candidate.

V18 is immutable, append-frozen, inactive, and superseded. V19 changes only
the reviewed erasure-composition authority, its closed lineage declarations,
version-bearing SQL predicates/recipes, and dependent hashes. This command
cannot freeze, sign, publish, or generate a fixture.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any, Never, cast

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_V18_PATH = _SCRIPT_DIR / "materialize_full_strict_v18_preimage.py"
_V18_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v18_reviewed_preimage_v1.json"
_V18_SOURCE_SHA512 = (
    "24ddfa5d6bbcb7ef0d05f4db10ec6244ab5b2fa4a9b7abd4e0e0d425e8bfd307"
    "32d588f387f89d4fa50c1b8d996025c8a8d13a6055af51eaaa299b5e12aa0b5b"
)
_V18_ARTIFACT_SHA256 = "554409ac36f3defeceda209e9e59e7587e4df630b06b5dc049ba9958f60abd1c"
_V18_ARTIFACT_SHA512 = (
    "349ca9d4b6b2d5873b8dbe5c229a1fb72f1099f29294ce76a3f9f23b3208a528"
    "9ecb3bd74989207ae9f2d7e885624af1a792c93d041165837dfae53ce1b378d1"
)
_V18 = "full-strict-v18"
_V19 = "full-strict-v19"
_V19_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v19-sql-authority-registry-v1"
_ERASURE_ROW_HASH_DOMAIN = b"full-strict-v4-erasure-execution-row-sha256-v1\x00"
_ERASURE_REGISTRY_HASH_DOMAIN = "full-strict-v4-erasure-execution-registry-v1"
_VERSION_AUTHORITIES = (
    (19, "current-receipt-equality-v1", 2, 3),
    (26, "current-task5-coverage-replacement-v1", 0, 1),
)
_ALLOWED_CHANGED_AUTHORITY_ORDINALS = {19, 26}
_APPROVED_CHANGED_SECTIONS = {
    "metadata",
    "tuple_schema_registry",
    "digest_recipe_registry",
    "uuid_namespace_registry",
    "sql_authority_registry",
    "erasure_execution_registry",
}
_SEMANTIC_INVARIANT_SECTIONS = (
    "canonicalization_contract",
    "schema_registry",
    "semantic_step_registry",
    "current_ddl_registry",
    "baseline_parent_authority_registry",
    "future_native_authority_registry",
    "future_reference_authority_registry",
    "future_child_authority_registry",
    "provider_registry",
    "rights_index_policy_registry",
    "role_registry",
    "deliverable_registry",
    "producer_selector_registry",
    "consumer_selector_registry",
    "applicability_registry",
    "edge_rule_registry",
    "usage_route_registry",
    "usage_extractor_registry",
    "erasure_baseline",
    "erasure_materialized",
    "activation_assertions",
)
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
_READ_CLOSURE = (
    "erasure_requests",
    "deterministic_calculations",
    "runs",
    "human_gates",
    "legal_holds",
    "document_artifacts",
    "audit_events",
)
_FIXED_TENANT = "11111111-1111-4111-8111-111111111111"
_FIXED_DEAL = "22222222-2222-4222-8222-222222222222"
_FIXED_REQUEST = "33333333-3333-4333-8333-333333333333"
_FIXED_ATTEMPT_ID = "fbb3b87f-d9d0-56b4-a983-084fb804d4ae"
_FIXED_EVENT_IDS = (
    ("task7.erasure.attempted", "ATTEMPTED", "593d952b-1aff-5e4e-b673-fe02a28e8f2c"),
    ("task7.erasure.failed", "FAILED", "1407c4f8-0229-5b82-97ff-5f44eeb29e97"),
    ("task7.erasure.succeeded", "SUCCEEDED", "70e0d93e-ecb4-5099-944d-37c786d57854"),
)


class MaterializationError(RuntimeError):
    """Stable fail-closed v19 materialization error."""


def _error(code: str, detail: str | None = None) -> Never:
    raise MaterializationError(code if detail is None else f"{code}:{detail}")


def _load_pinned_script(path: Path, sha512: str, name: str) -> ModuleType:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise MaterializationError(f"TASK7_V19_HELPER_MISSING:{path.name}") from exc
    if hashlib.sha512(raw).hexdigest() != sha512:
        _error("TASK7_V19_PINNED_HELPER_DRIFT", path.name)
    spec = importlib.util.spec_from_loader(name, loader=None, origin=str(path))
    if spec is None:
        _error("TASK7_V19_HELPER_IMPORT_FAILED", path.name)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(raw, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


_M18 = _load_pinned_script(
    _V18_PATH,
    _V18_SOURCE_SHA512,
    "_task7_v18_materializer_for_v19",
)
_H = _M18._H


def _canonical_bytes(value: object) -> bytes:
    return bytes(_M18._canonical_bytes(value))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: object) -> str:
    return _sha256(_canonical_bytes(value))


def _render(artifact: dict[str, Any]) -> bytes:
    return bytes(_M18._render(artifact))


def _changed_paths(
    old: Any,
    new: Any,
    path: tuple[str | int, ...] = (),
) -> set[tuple[str | int, ...]]:
    return set(_M18._changed_paths(old, new, path))


def _load_pinned_v18() -> dict[str, Any]:
    try:
        raw = _V18_ARTIFACT.read_bytes()
    except OSError as exc:
        raise MaterializationError("TASK7_V19_BASE_ARTIFACT_MISSING") from exc
    if (
        _sha256(raw) != _V18_ARTIFACT_SHA256
        or hashlib.sha512(raw).hexdigest() != _V18_ARTIFACT_SHA512
    ):
        _error("TASK7_V19_BASE_ARTIFACT_HASH_MISMATCH")
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _error("TASK7_V19_BASE_ARTIFACT_NOT_CANONICAL")
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError("TASK7_V19_BASE_ARTIFACT_INVALID") from exc
    if (
        not isinstance(value, dict)
        or value.get("metadata", {}).get("artifact_schema")
        != "full-strict-v18-reviewed-preimage-v1"
        or value.get("metadata", {}).get("pipeline_contract_version") != _V18
        or value.get("metadata", {}).get("append_frozen") is not True
    ):
        _error("TASK7_V19_BASE_ARTIFACT_INVALID")
    return value


def _authority_row(
    artifact: dict[str, Any],
    *,
    authority_ordinal: int,
    authority_id: str,
) -> dict[str, Any]:
    rows = artifact["sql_authority_registry"]["rows"]
    if authority_ordinal >= len(rows):
        _error("TASK7_V19_AUTHORITY_SHAPE_INVALID", authority_id)
    row = rows[authority_ordinal]
    if (
        not isinstance(row, dict)
        or row.get("authority_ordinal") != authority_ordinal
        or row.get("authority_id") != authority_id
    ):
        _error("TASK7_V19_AUTHORITY_SHAPE_INVALID", authority_id)
    return cast(dict[str, Any], row)


def _transform_version_authorities(
    artifact: dict[str, Any],
    *,
    reverse: bool,
) -> None:
    old = _V19 if reverse else _V18
    new = _V18 if reverse else _V19
    for authority_ordinal, authority_id, statement_ordinal, expected_count in _VERSION_AUTHORITIES:
        row = _authority_row(
            artifact,
            authority_ordinal=authority_ordinal,
            authority_id=authority_id,
        )
        statements = list(_H._decode_statements(row))
        if statement_ordinal >= len(statements):
            _error("TASK7_V19_VERSION_STATEMENT_CENSUS_INVALID", authority_id)
        observed_count = sum(statement.count(old) for statement in statements)
        replacement = statements[statement_ordinal]
        if (
            observed_count != expected_count
            or replacement.count(old) != expected_count
            or replacement.count(new) != 0
        ):
            _error("TASK7_V19_VERSION_LITERAL_BASE_INVALID", authority_id)
        statements[statement_ordinal] = replacement.replace(old, new)
        row["body_sql_utf8_hex_tuple"] = list(_H._encode_statements(statements))
        _H._rehash_authority_row(row)


def _rehash_sql_registry(
    artifact: dict[str, Any],
    *,
    hash_domain: str = _V19_SQL_REGISTRY_HASH_DOMAIN,
) -> None:
    section = artifact["sql_authority_registry"]
    if tuple(section) != ("rows", "guard_function_acl_registry", "expected_sha256"):
        _error("TASK7_V19_SQL_REGISTRY_SHAPE_INVALID")
    section["expected_sha256"] = _h(
        [
            hash_domain,
            section["rows"],
            section["guard_function_acl_registry"],
        ]
    )


def _transform_tuple_schemas(artifact: dict[str, Any]) -> None:
    rows = artifact["tuple_schema_registry"]["rows"]
    names = [row[0] for row in rows]
    if names.count("ErasureExecutionSpecV1") != 1 or any(
        name in names for name in ("ErasureExecutionSpecV2", "ErasureAuditLineageSpecV1")
    ):
        _error("TASK7_V19_TUPLE_SCHEMA_BASE_INVALID")
    if tuple(dict(rows)["ErasureExecutionSpecV1"]) != _V1_FIELDS:
        _error("TASK7_V19_TUPLE_SCHEMA_BASE_INVALID")
    rows.extend(
        [
            ["ErasureExecutionSpecV2", list(_V2_FIELDS)],
            ["ErasureAuditLineageSpecV1", list(_LINEAGE_FIELDS)],
        ]
    )


def _uuid5(namespace: str, name_tuple: object) -> str:
    name = _canonical_bytes(name_tuple).decode("utf-8")
    return str(uuid.uuid5(uuid.UUID(namespace), name))


def validate_uuidv5_fixed_vectors(artifact: dict[str, Any]) -> None:
    """Bind both namespace/name schemas to independently fixed UUIDv5 vectors."""

    rows = artifact["uuid_namespace_registry"]["rows"]
    by_name = {row[0]: row for row in rows}
    if by_name.get("erasure_attempt_id") != [
        "erasure_attempt_id",
        _ATTEMPT_NAMESPACE,
        list(_ATTEMPT_NAME_TUPLE),
    ]:
        _error("TASK7_V19_ATTEMPT_UUID_PREIMAGE_INVALID")
    if by_name.get("erasure_audit_event_id") != [
        "erasure_audit_event_id",
        _EVENT_NAMESPACE,
        list(_EVENT_NAME_TUPLE),
    ]:
        _error("TASK7_V19_EVENT_UUID_PREIMAGE_INVALID")
    first_attempt = [
        "task7-erasure-attempt-v1",
        _FIXED_TENANT,
        _FIXED_DEAL,
        _FIXED_REQUEST,
        1,
        None,
        None,
        None,
    ]
    if _uuid5(_ATTEMPT_NAMESPACE, first_attempt) != _FIXED_ATTEMPT_ID:
        _error("TASK7_V19_ATTEMPT_UUID_FIXED_VECTOR_INVALID")
    for event_type, outcome, expected in _FIXED_EVENT_IDS:
        event_name = [
            "task7-erasure-audit-event-v1",
            _FIXED_ATTEMPT_ID,
            event_type,
            outcome,
        ]
        if _uuid5(_EVENT_NAMESPACE, event_name) != expected:
            _error("TASK7_V19_EVENT_UUID_FIXED_VECTOR_INVALID", event_type)


def _transform_uuid_registry(artifact: dict[str, Any]) -> None:
    section = artifact["uuid_namespace_registry"]
    if tuple(section) != ("rows", "expected_sha256"):
        _error("TASK7_V19_UUID_REGISTRY_SHAPE_INVALID")
    rows = section["rows"]
    names = {row[0] for row in rows}
    namespaces = {row[1] for row in rows}
    if names & {"erasure_attempt_id", "erasure_audit_event_id"}:
        _error("TASK7_V19_UUID_REGISTRY_BASE_INVALID")
    if namespaces & {_ATTEMPT_NAMESPACE, _EVENT_NAMESPACE}:
        _error("TASK7_V19_UUID_REGISTRY_BASE_INVALID")
    rows.extend(
        [
            ["erasure_attempt_id", _ATTEMPT_NAMESPACE, list(_ATTEMPT_NAME_TUPLE)],
            ["erasure_audit_event_id", _EVENT_NAMESPACE, list(_EVENT_NAME_TUPLE)],
        ]
    )
    section["expected_sha256"] = _h(["task7-uuid-namespace-registry-v1", rows])
    validate_uuidv5_fixed_vectors(artifact)


def _normalize_sql(value: str) -> str:
    return " ".join(value.split())


def _inline_uuidv5_block(*, namespace: str, name_variable: str, result_variable: str) -> str:
    """Inline the reviewed SHA-1/UUIDv5 algorithm without a helper ACL edge."""

    return _normalize_sql(
        f"""
        DECLARE
            payload bytea;
            original_octets bigint;
            zero_count integer;
            block_offset integer;
            word_ordinal integer;
            round_ordinal integer;
            words bigint[]:=pg_catalog.array_fill(0::bigint,ARRAY[80],ARRAY[0]);
            h0 bigint:=1732584193;
            h1 bigint:=4023233417;
            h2 bigint:=2562383102;
            h3 bigint:=271733878;
            h4 bigint:=3285377520;
            a bigint;
            b bigint;
            c bigint;
            d bigint;
            e bigint;
            f bigint;
            k bigint;
            temporary bigint;
            mask constant bigint:=4294967295;
            length_hex text;
            digest bytea;
            value_hex text;
        BEGIN
            payload:=pg_catalog.decode(
                replace('{namespace}'::uuid::text,'-',''),'hex'
            )||pg_catalog.convert_to({name_variable},'UTF8');
            original_octets:=pg_catalog.octet_length(payload);
            payload:=payload||pg_catalog.decode('80','hex');
            zero_count:=(56-(pg_catalog.octet_length(payload)%64)+64)%64;
            IF zero_count>0 THEN
                payload:=payload||pg_catalog.decode(repeat('00',zero_count),'hex');
            END IF;
            length_hex:=pg_catalog.lpad(pg_catalog.to_hex(original_octets*8),16,'0');
            payload:=payload||pg_catalog.decode(length_hex,'hex');
            block_offset:=0;
            WHILE block_offset<pg_catalog.octet_length(payload) LOOP
                FOR word_ordinal IN 0..15 LOOP
                    words[word_ordinal]:=(
                        (pg_catalog.get_byte(payload,block_offset+word_ordinal*4)::bigint<<24) |
                        (pg_catalog.get_byte(payload,block_offset+word_ordinal*4+1)::bigint<<16) |
                        (pg_catalog.get_byte(payload,block_offset+word_ordinal*4+2)::bigint<<8) |
                        pg_catalog.get_byte(payload,block_offset+word_ordinal*4+3)::bigint
                    )&mask;
                END LOOP;
                FOR word_ordinal IN 16..79 LOOP
                    temporary:=(
                        words[word_ordinal-3] # words[word_ordinal-8] #
                        words[word_ordinal-14] # words[word_ordinal-16]
                    )&mask;
                    words[word_ordinal]:=((temporary<<1)|(temporary>>31))&mask;
                END LOOP;
                a:=h0; b:=h1; c:=h2; d:=h3; e:=h4;
                FOR round_ordinal IN 0..79 LOOP
                    IF round_ordinal<=19 THEN
                        f:=((b&c)|((mask#b)&d))&mask; k:=1518500249;
                    ELSIF round_ordinal<=39 THEN
                        f:=(b#c#d)&mask; k:=1859775393;
                    ELSIF round_ordinal<=59 THEN
                        f:=((b&c)|(b&d)|(c&d))&mask; k:=2400959708;
                    ELSE
                        f:=(b#c#d)&mask; k:=3395469782;
                    END IF;
                    temporary:=(
                        (((a<<5)|(a>>27))&mask)+f+e+k+words[round_ordinal]
                    )&mask;
                    e:=d; d:=c; c:=((b<<30)|(b>>2))&mask; b:=a; a:=temporary;
                END LOOP;
                h0:=(h0+a)&mask; h1:=(h1+b)&mask; h2:=(h2+c)&mask;
                h3:=(h3+d)&mask; h4:=(h4+e)&mask;
                block_offset:=block_offset+64;
            END LOOP;
            digest:=pg_catalog.decode(
                pg_catalog.lpad(pg_catalog.to_hex(h0),8,'0')||
                pg_catalog.lpad(pg_catalog.to_hex(h1),8,'0')||
                pg_catalog.lpad(pg_catalog.to_hex(h2),8,'0')||
                pg_catalog.lpad(pg_catalog.to_hex(h3),8,'0')||
                pg_catalog.lpad(pg_catalog.to_hex(h4),8,'0'),
                'hex'
            );
            digest:=pg_catalog.substring(digest,1,16);
            digest:=pg_catalog.set_byte(
                digest,6,(pg_catalog.get_byte(digest,6)&15)|80
            );
            digest:=pg_catalog.set_byte(
                digest,8,(pg_catalog.get_byte(digest,8)&63)|128
            );
            value_hex:=pg_catalog.encode(digest,'hex');
            {result_variable}:=(
                pg_catalog.substring(value_hex,1,8)||'-'||
                pg_catalog.substring(value_hex,9,4)||'-'||
                pg_catalog.substring(value_hex,13,4)||'-'||
                pg_catalog.substring(value_hex,17,4)||'-'||
                pg_catalog.substring(value_hex,21,12)
            )::uuid;
        END;
        """
    )


def _canonical_failed_event_sql() -> str:
    """Return a closed canonical reconstruction for the known failed-event shape."""

    scalar = "pg_catalog.to_json({value})::text"
    null_scalar = "COALESCE(pg_catalog.to_json({value})::text,'null')"
    template = _normalize_sql(
        """
        canonical_prior_event:=
            '{"actor":{'||
            '"actor_id":'||__actor_id__||','||
            '"actor_type":'||__actor_type__||','||
            '"ip":'||__actor_ip__||','||
            '"roles":'||replace((prior_event->'actor'->'roles')::text,', ',',')||','||
            '"user_agent":'||__actor_user_agent__||
            '},"event_id":'||__event_id__||
            ',"event_type":'||__event_type__||
            ',"occurred_at":'||__occurred_at__||
            ',"payload":{"hashes":'||
            replace((prior_event->'payload'->'hashes')::text,', ',',')||
            ',"refs":'||replace((prior_event->'payload'->'refs')::text,', ', ',')||
            ',"safe":{"attempt_id":'||__attempt_id__||
            ',"attempt_ordinal":'||(prior_event->'payload'->'safe'->>'attempt_ordinal')||
            ',"outcome":'||__outcome__||
            ',"prior_attempt_event_hash":'||__prior_hash__||
            ',"prior_attempt_event_id":'||__prior_id__||
            ',"prior_status":'||__prior_status__||
            ',"request_id":'||__request_id__||
            '}},"request":{"method":'||__request_method__||
            ',"path":'||__request_path__||
            ',"request_id":'||__request_request_id__||
            ',"status_code":'||(prior_event->'request'->>'status_code')||
            '},"resource":{"deal_id":'||__deal_id__||
            ',"resource_id":'||__resource_id__||
            ',"resource_type":'||__resource_type__||
            '},"severity":'||__severity__||
            ',"summary":'||__summary__||
            ',"tenant_id":'||__tenant_id__||'}';
        """
    )
    replacements = {
        "__actor_id__": scalar.format(value="prior_event->'actor'->>'actor_id'"),
        "__actor_type__": scalar.format(value="prior_event->'actor'->>'actor_type'"),
        "__actor_ip__": scalar.format(value="prior_event->'actor'->>'ip'"),
        "__actor_user_agent__": scalar.format(value="prior_event->'actor'->>'user_agent'"),
        "__event_id__": scalar.format(value="prior_event->>'event_id'"),
        "__event_type__": scalar.format(value="prior_event->>'event_type'"),
        "__occurred_at__": scalar.format(value="prior_event->>'occurred_at'"),
        "__attempt_id__": scalar.format(value="prior_event->'payload'->'safe'->>'attempt_id'"),
        "__outcome__": scalar.format(value="prior_event->'payload'->'safe'->>'outcome'"),
        "__prior_hash__": null_scalar.format(
            value="prior_event->'payload'->'safe'->>'prior_attempt_event_hash'"
        ),
        "__prior_id__": null_scalar.format(
            value="prior_event->'payload'->'safe'->>'prior_attempt_event_id'"
        ),
        "__prior_status__": null_scalar.format(
            value="prior_event->'payload'->'safe'->>'prior_status'"
        ),
        "__request_id__": scalar.format(value="prior_event->'payload'->'safe'->>'request_id'"),
        "__request_method__": scalar.format(value="prior_event->'request'->>'method'"),
        "__request_path__": scalar.format(value="prior_event->'request'->>'path'"),
        "__request_request_id__": scalar.format(value="prior_event->'request'->>'request_id'"),
        "__deal_id__": scalar.format(value="prior_event->'resource'->>'deal_id'"),
        "__resource_id__": scalar.format(value="prior_event->'resource'->>'resource_id'"),
        "__resource_type__": scalar.format(value="prior_event->'resource'->>'resource_type'"),
        "__severity__": scalar.format(value="prior_event->>'severity'"),
        "__summary__": scalar.format(value="prior_event->>'summary'"),
        "__tenant_id__": scalar.format(value="prior_event->>'tenant_id'"),
    }
    for token, value in replacements.items():
        if template.count(token) != 1:
            _error("TASK7_V19_CANONICAL_EVENT_TEMPLATE_INVALID", token)
        template = template.replace(token, value)
    return template


def _runner_body(base_body: str) -> str:
    start = base_body.index("SET CONSTRAINTS ALL DEFERRED;")
    end = base_body.rindex("SET CONSTRAINTS ALL IMMEDIATE;")
    delete_segment = base_body[start:end]
    prefix = _normalize_sql(
        """
        CREATE FUNCTION public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)
        RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp AS $$
        DECLARE
            deletion_counts JSONB:='{}'::jsonb;
            deleted_count BIGINT;
            request_status TEXT;
            staged_counts JSONB;
            staged_object_rows BIGINT;
            artifact_row_count BIGINT;
            object_target_set_sha256 TEXT;
            lineage_events JSONB;
            attempted_matches JSONB;
            attempted_event JSONB;
            attempted_count BIGINT;
            attempt_id UUID;
            attempt_ordinal INTEGER;
            prior_status TEXT;
            prior_event_id UUID;
            prior_event_hash TEXT;
            attempted_actor_id TEXT;
            attempt_name TEXT;
            event_name TEXT;
            expected_attempt_id UUID;
            expected_event_id UUID;
            prior_event JSONB;
            prior_matches JSONB;
            prior_count BIGINT;
            prior_attempt_id UUID;
            prior_attempt_name TEXT;
            expected_prior_attempt_id UUID;
            prior_event_name TEXT;
            expected_prior_event_id UUID;
            canonical_prior_event TEXT;
            persisted_status TEXT;
            persisted_actor TEXT;
            persisted_at TIMESTAMPTZ;
            persisted_counts JSONB;
        BEGIN
            IF NOT (
                session_user='idis_erasure_executor' AND
                current_user='idis_erasure_function_owner'
            ) THEN
                RAISE EXCEPTION 'TASK7_ERASURE_EXECUTOR_UNAUTHORIZED'
                    USING ERRCODE='42501';
            END IF;
            PERFORM pg_catalog.set_config('idis.tenant_id',$2::text,true);
            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'idis-task7-erasure-tenant-lock-v1:'||$2::text,0
                )
            );
            SELECT r.status,r.counts
            INTO STRICT request_status,staged_counts
            FROM public.erasure_requests r
            WHERE r.request_id=$1 AND r.tenant_id=$2 AND r.deal_id=$3
            FOR UPDATE;
            IF request_status NOT IN ('REQUESTED','FAILED') THEN
                RAISE EXCEPTION 'TASK7_ERASURE_REQUEST_NOT_EXECUTABLE'
                    USING ERRCODE='42501';
            END IF;
            IF pg_catalog.jsonb_typeof(staged_counts)<>'object'
               OR pg_catalog.jsonb_typeof(staged_counts->'object_rows_reconciled')<>'number'
               OR (staged_counts->>'object_rows_reconciled')!~'^(0|[1-9][0-9]*)$'
               OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(staged_counts))<>1
            THEN
                RAISE EXCEPTION 'TASK7_ERASURE_OBJECT_RECONCILIATION_INVALID'
                    USING ERRCODE='23514';
            END IF;
            staged_object_rows:=(staged_counts->>'object_rows_reconciled')::bigint;
            IF EXISTS (
                SELECT 1 FROM public.document_artifacts a
                WHERE a.tenant_id=$2 AND a.deal_id=$3
                  AND (a.uri IS NULL OR a.uri NOT LIKE 'idis://%' OR a.uri='idis://')
            ) THEN
                RAISE EXCEPTION 'TASK7_ERASURE_ARTIFACT_LOCATOR_INVALID'
                    USING ERRCODE='23514';
            END IF;
            SELECT count(*),
                   pg_catalog.encode(
                       pg_catalog.sha256(
                           pg_catalog.convert_to(
                               '["task7-erasure-object-target-set-v1",'||
                               pg_catalog.to_json($2::text)::text||','||
                               pg_catalog.to_json($3::text)::text||','||
                               count(*)::text||',['||
                               COALESCE(
                                   pg_catalog.string_agg(
                                       pg_catalog.to_json(
                                           pg_catalog.encode(
                                               pg_catalog.sha256(
                                                   pg_catalog.convert_to(a.uri,'UTF8')
                                               ),
                                               'hex'
                                           )
                                       )::text,
                                       ',' ORDER BY a.uri COLLATE "C"
                                   ),
                                   ''
                               )||']]',
                               'UTF8'
                           )
                       ),
                       'hex'
                   )
            INTO artifact_row_count,object_target_set_sha256
            FROM public.document_artifacts a
            WHERE a.tenant_id=$2 AND a.deal_id=$3;
            IF staged_object_rows<>artifact_row_count THEN
                RAISE EXCEPTION 'TASK7_ERASURE_OBJECT_RECONCILIATION_INVALID'
                    USING ERRCODE='23514';
            END IF;
            SELECT COALESCE(pg_catalog.jsonb_agg(e.event ORDER BY e.event_id),'[]'::jsonb)
            INTO lineage_events
            FROM public.audit_events e
            WHERE e.tenant_id=$2
              AND e.event_type IN (
                  'task7.erasure.attempted',
                  'task7.erasure.failed',
                  'task7.erasure.succeeded'
              )
              AND e.event->>'event_id'=e.event_id::text
              AND e.event->>'tenant_id'=$2::text
              AND e.event->'resource'->>'deal_id'=$3::text
              AND e.event->'payload'->'safe'->>'request_id'=$1::text;
            SELECT count(*),COALESCE(pg_catalog.jsonb_agg(item),'[]'::jsonb)
            INTO attempted_count,attempted_matches
            FROM pg_catalog.jsonb_array_elements(lineage_events) AS events(item)
            WHERE item->>'event_type'='task7.erasure.attempted'
              AND item->'payload'->'safe'->>'outcome'='ATTEMPTED'
              AND item->'actor'->'roles' @> '["ADMIN"]'::jsonb
              AND item->'payload'->'hashes'=
                  pg_catalog.jsonb_build_array(
                      'object_target_set_sha256:'||object_target_set_sha256
                  )
              AND NOT EXISTS (
                  SELECT 1
                  FROM pg_catalog.jsonb_array_elements(lineage_events) AS terminals(terminal)
                  WHERE terminal->'payload'->'safe'->>'attempt_id'=
                        item->'payload'->'safe'->>'attempt_id'
                    AND terminal->>'event_type' IN (
                        'task7.erasure.failed','task7.erasure.succeeded'
                    )
              );
            IF attempted_count<>1 THEN
                RAISE EXCEPTION 'TASK7_ERASURE_ATTEMPTED_EVENT_INVALID'
                    USING ERRCODE='23514';
            END IF;
            attempted_event:=attempted_matches->0;
            attempt_id:=(attempted_event->'payload'->'safe'->>'attempt_id')::uuid;
            attempt_ordinal:=(attempted_event->'payload'->'safe'->>'attempt_ordinal')::integer;
            prior_status:=attempted_event->'payload'->'safe'->>'prior_status';
            prior_event_id:=(attempted_event->'payload'->'safe'->>'prior_attempt_event_id')::uuid;
            prior_event_hash:=attempted_event->'payload'->'safe'->>'prior_attempt_event_hash';
            attempted_actor_id:=attempted_event->'actor'->>'actor_id';
            IF attempted_actor_id IS NULL OR btrim(attempted_actor_id)='' THEN
                RAISE EXCEPTION 'TASK7_ERASURE_ATTEMPTED_EVENT_INVALID'
                    USING ERRCODE='23514';
            END IF;
            attempt_name:=
                '['||pg_catalog.to_json('task7-erasure-attempt-v1'::text)::text||','||
                pg_catalog.to_json($2::text)::text||','||
                pg_catalog.to_json($3::text)::text||','||
                pg_catalog.to_json($1::text)::text||','||
                attempt_ordinal::text||','||
                COALESCE(pg_catalog.to_json(prior_status)::text,'null')||','||
                COALESCE(pg_catalog.to_json(prior_event_id::text)::text,'null')||','||
                COALESCE(pg_catalog.to_json(prior_event_hash)::text,'null')||']';
        """
    )
    attempt_uuid = _inline_uuidv5_block(
        namespace=_ATTEMPT_NAMESPACE,
        name_variable="attempt_name",
        result_variable="expected_attempt_id",
    )
    event_uuid = _inline_uuidv5_block(
        namespace=_EVENT_NAMESPACE,
        name_variable="event_name",
        result_variable="expected_event_id",
    )
    prior_attempt_uuid = _inline_uuidv5_block(
        namespace=_ATTEMPT_NAMESPACE,
        name_variable="prior_attempt_name",
        result_variable="expected_prior_attempt_id",
    )
    prior_event_uuid = _inline_uuidv5_block(
        namespace=_EVENT_NAMESPACE,
        name_variable="prior_event_name",
        result_variable="expected_prior_event_id",
    )
    validation = _normalize_sql(
        """
            IF expected_attempt_id IS DISTINCT FROM attempt_id THEN
                RAISE EXCEPTION 'TASK7_ERASURE_ATTEMPT_PREIMAGE_MISMATCH'
                    USING ERRCODE='23514';
            END IF;
            event_name:=
                '['||pg_catalog.to_json('task7-erasure-audit-event-v1'::text)::text||','||
                pg_catalog.to_json(attempt_id::text)::text||','||
                pg_catalog.to_json('task7.erasure.attempted'::text)::text||','||
                pg_catalog.to_json('ATTEMPTED'::text)::text||']';
        """
    )
    after_event_uuid = _normalize_sql(
        """
            IF expected_event_id IS DISTINCT FROM (attempted_event->>'event_id')::uuid THEN
                RAISE EXCEPTION 'TASK7_ERASURE_EVENT_PREIMAGE_MISMATCH'
                    USING ERRCODE='23514';
            END IF;
            IF request_status='REQUESTED' THEN
                IF attempt_ordinal<>1 OR prior_status IS NOT NULL
                   OR prior_event_id IS NOT NULL OR prior_event_hash IS NOT NULL
                THEN
                    RAISE EXCEPTION 'TASK7_ERASURE_ATTEMPT_LINEAGE_INVALID'
                        USING ERRCODE='23514';
                END IF;
            ELSE
                IF attempt_ordinal<=1 OR prior_status<>'FAILED'
                   OR prior_event_id IS NULL
                   OR prior_event_hash!~'^[0-9a-f]{64}$'
                THEN
                    RAISE EXCEPTION 'TASK7_ERASURE_ATTEMPT_LINEAGE_INVALID'
                        USING ERRCODE='23514';
                END IF;
                SELECT count(*),COALESCE(pg_catalog.jsonb_agg(item),'[]'::jsonb)
                INTO prior_count,prior_matches
                FROM pg_catalog.jsonb_array_elements(lineage_events) AS events(item)
                WHERE item->>'event_id'=prior_event_id::text
                  AND item->>'event_type'='task7.erasure.failed'
                  AND item->'payload'->'safe'->>'outcome'='FAILED'
                  AND (item->'payload'->'safe'->>'attempt_ordinal')::integer=
                      attempt_ordinal-1
                  AND item->'payload'->'safe'->>'request_id'=$1::text;
                IF prior_count<>1 THEN
                    RAISE EXCEPTION 'TASK7_ERASURE_PRIOR_EVENT_INVALID'
                        USING ERRCODE='23514';
                END IF;
                prior_event:=prior_matches->0;
                prior_attempt_id:=(prior_event->'payload'->'safe'->>'attempt_id')::uuid;
                prior_attempt_name:=
                    '['||pg_catalog.to_json('task7-erasure-attempt-v1'::text)::text||','||
                    pg_catalog.to_json($2::text)::text||','||
                    pg_catalog.to_json($3::text)::text||','||
                    pg_catalog.to_json($1::text)::text||','||
                    (prior_event->'payload'->'safe'->>'attempt_ordinal')||','||
                    COALESCE(
                        pg_catalog.to_json(
                            prior_event->'payload'->'safe'->>'prior_status'
                        )::text,
                        'null'
                    )||','||
                    COALESCE(
                        pg_catalog.to_json(
                            prior_event->'payload'->'safe'->>'prior_attempt_event_id'
                        )::text,
                        'null'
                    )||','||
                    COALESCE(
                        pg_catalog.to_json(
                            prior_event->'payload'->'safe'->>'prior_attempt_event_hash'
                        )::text,
                        'null'
                    )||']';
        """
    )
    after_prior_attempt_uuid = _normalize_sql(
        """
                IF expected_prior_attempt_id IS DISTINCT FROM prior_attempt_id THEN
                    RAISE EXCEPTION 'TASK7_ERASURE_PRIOR_ATTEMPT_PREIMAGE_MISMATCH'
                        USING ERRCODE='23514';
                END IF;
                prior_event_name:=
                    '['||
                    pg_catalog.to_json('task7-erasure-audit-event-v1'::text)::text||','||
                    pg_catalog.to_json(prior_attempt_id::text)::text||','||
                    pg_catalog.to_json('task7.erasure.failed'::text)::text||','||
                    pg_catalog.to_json('FAILED'::text)::text||']';
        """
    )
    failed_canonical = _canonical_failed_event_sql()
    after_prior_event_uuid = _normalize_sql(
        """
                IF expected_prior_event_id IS DISTINCT FROM prior_event_id THEN
                    RAISE EXCEPTION 'TASK7_ERASURE_PRIOR_EVENT_PREIMAGE_MISMATCH'
                        USING ERRCODE='23514';
                END IF;
                IF (SELECT count(*) FROM pg_catalog.jsonb_object_keys(prior_event))<>10
                   OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(
                       prior_event->'actor'))<>5
                   OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(
                       prior_event->'request'))<>4
                   OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(
                       prior_event->'resource'))<>3
                   OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(
                       prior_event->'payload'))<>3
                   OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(
                       prior_event->'payload'->'safe'))<>7
                THEN
                    RAISE EXCEPTION 'TASK7_ERASURE_PRIOR_EVENT_SHAPE_INVALID'
                        USING ERRCODE='23514';
                END IF;
        """
    )
    canonical_hash_check = _normalize_sql(
        """
                IF pg_catalog.encode(
                    pg_catalog.sha256(
                        pg_catalog.convert_to(canonical_prior_event,'UTF8')
                    ),
                    'hex'
                ) IS DISTINCT FROM prior_event_hash
                THEN
                    RAISE EXCEPTION 'TASK7_ERASURE_PRIOR_EVENT_HASH_MISMATCH'
                        USING ERRCODE='23514';
                END IF;
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.legal_holds h
                WHERE h.tenant_id=$2 AND h.lifted_at IS NULL
                  AND (
                      (h.target_type='DEAL' AND h.target_id=$3::text)
                      OR (
                          h.target_type='DOCUMENT' AND EXISTS (
                              SELECT 1 FROM public.document_artifacts a
                              WHERE a.tenant_id=$2 AND a.deal_id=$3
                                AND h.target_id=a.doc_id::text
                          )
                      )
                      OR (
                          h.target_type='ARTIFACT' AND EXISTS (
                              SELECT 1 FROM public.document_artifacts a
                              WHERE a.tenant_id=$2 AND a.deal_id=$3
                                AND a.uri='idis://'||h.target_id
                          )
                      )
                  )
            ) THEN
                RAISE EXCEPTION 'TASK7_ERASURE_BLOCKED_BY_HOLD'
                    USING ERRCODE='23514';
            END IF;
        """
    )
    suffix = _normalize_sql(
        """
            deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object(
                'object_rows_reconciled',staged_object_rows
            );
            SET CONSTRAINTS ALL IMMEDIATE;
            UPDATE public.erasure_requests
            SET status='EXECUTED',
                executed_by=attempted_actor_id,
                executed_at=pg_catalog.statement_timestamp(),
                counts=deletion_counts
            WHERE request_id=$1 AND tenant_id=$2 AND deal_id=$3
              AND status=request_status
            RETURNING status,executed_by,executed_at,counts
            INTO persisted_status,persisted_actor,persisted_at,persisted_counts;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'TASK7_ERASURE_REQUEST_FENCED'
                    USING ERRCODE='40001';
            END IF;
            RETURN pg_catalog.jsonb_build_object(
                'status',persisted_status,
                'executed_by',persisted_actor,
                'executed_at',pg_catalog.to_char(
                    persisted_at AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                ),
                'counts',persisted_counts
            );
        EXCEPTION
            WHEN no_data_found OR too_many_rows OR invalid_text_representation
                 OR numeric_value_out_of_range THEN
                RAISE EXCEPTION 'TASK7_ERASURE_ATTEMPTED_EVENT_INVALID'
                    USING ERRCODE='23514';
        END$$
        """
    )
    return _normalize_sql(
        " ".join(
            (
                prefix,
                attempt_uuid,
                validation,
                event_uuid,
                after_event_uuid,
                prior_attempt_uuid,
                after_prior_attempt_uuid,
                prior_event_uuid,
                after_prior_event_uuid,
                failed_canonical,
                canonical_hash_check,
                delete_segment,
                suffix,
            )
        )
    )


def _runner_acl(base_acl: list[str]) -> list[str]:
    statements = list(base_acl)
    usage = "GRANT USAGE ON SCHEMA public TO idis_erasure_function_owner"
    request_select = (
        "GRANT SELECT (request_id,tenant_id,deal_id,status) ON TABLE "
        "public.erasure_requests TO idis_erasure_function_owner"
    )
    request_update = (
        "GRANT UPDATE (request_id) ON TABLE public.erasure_requests TO idis_erasure_function_owner"
    )
    if statements.count(usage) != 1 or statements.count(request_select) != 1:
        _error("TASK7_V19_ERASURE_ACL_BASE_INVALID")
    if statements.count(request_update) != 1:
        _error("TASK7_V19_ERASURE_ACL_BASE_INVALID")
    usage_ordinal = statements.index(usage)
    statements[usage_ordinal:usage_ordinal] = [
        "REVOKE ALL PRIVILEGES ON TABLE public.audit_events FROM idis_erasure_function_owner",
        "REVOKE ALL PRIVILEGES ON TABLE public.audit_events FROM idis_erasure_executor",
    ]
    request_ordinal = statements.index(request_select)
    statements[request_ordinal] = (
        "GRANT SELECT (request_id,tenant_id,deal_id,status,counts) ON TABLE "
        "public.erasure_requests TO idis_erasure_function_owner"
    )
    statements.insert(
        request_ordinal + 1,
        "GRANT SELECT (event_id,tenant_id,event_type,event) ON TABLE "
        "public.audit_events TO idis_erasure_function_owner",
    )
    update_ordinal = statements.index(request_update)
    statements[update_ordinal] = (
        "GRANT UPDATE (request_id,status,executed_by,executed_at,counts) ON TABLE "
        "public.erasure_requests TO idis_erasure_function_owner"
    )
    return statements


def _decode_hex_tuple(row: dict[str, Any], field: str) -> list[str]:
    try:
        return [bytes.fromhex(item).decode("utf-8") for item in row[field]]
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise MaterializationError("TASK7_V19_ERASURE_SQL_BASE_INVALID") from exc


def _encode_hex_tuple(statements: list[str]) -> list[str]:
    if any(statement != _normalize_sql(statement) for statement in statements):
        _error("TASK7_V19_ERASURE_SQL_NOT_CANONICAL")
    return [statement.encode("utf-8").hex() for statement in statements]


def _rehash_erasure_row(row: dict[str, Any]) -> None:
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    row["expected_sha256"] = _sha256(_ERASURE_ROW_HASH_DOMAIN + _canonical_bytes(payload))


def _transform_erasure_registry(artifact: dict[str, Any]) -> None:
    section = artifact["erasure_execution_registry"]
    if tuple(section) != ("rows", "expected_sha256") or len(section["rows"]) != 1:
        _error("TASK7_V19_ERASURE_REGISTRY_BASE_INVALID")
    old = section["rows"][0]
    if tuple(old) != _V1_FIELDS or old["execution_version"] != "1":
        _error("TASK7_V19_ERASURE_REGISTRY_BASE_INVALID")
    current_body = _runner_body(_decode_hex_tuple(old, "current_body_sql_utf8_hex_tuple")[0])
    future_body = _runner_body(_decode_hex_tuple(old, "future_body_sql_utf8_hex_tuple")[0])
    current_acl = _runner_acl(_decode_hex_tuple(old, "current_acl_sql_utf8_hex_tuple"))
    future_acl = _runner_acl(_decode_hex_tuple(old, "future_acl_sql_utf8_hex_tuple"))
    drop_sql = _decode_hex_tuple(old, "future_activation_sql_utf8_hex_tuple")[0]
    if drop_sql != "DROP FUNCTION public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)":
        _error("TASK7_V19_ERASURE_ACTIVATION_BASE_INVALID")
    values = {key: copy.deepcopy(value) for key, value in old.items()}
    values.update(
        {
            "execution_version": "2",
            "declared_read_relation_tuple": list(_READ_CLOSURE),
            "audit_lineage_contract_tuple": copy.deepcopy(_AUDIT_LINEAGE_CONTRACT),
            "object_reconciliation_contract_tuple": copy.deepcopy(_OBJECT_RECONCILIATION_CONTRACT),
            "current_body_sql_utf8_hex_tuple": _encode_hex_tuple([current_body]),
            "current_acl_sql_utf8_hex_tuple": _encode_hex_tuple(current_acl),
            "future_body_sql_utf8_hex_tuple": _encode_hex_tuple([future_body]),
            "future_acl_sql_utf8_hex_tuple": _encode_hex_tuple(future_acl),
            "future_activation_sql_utf8_hex_tuple": _encode_hex_tuple(
                [drop_sql, future_body, *future_acl]
            ),
            "future_restoration_sql_utf8_hex_tuple": _encode_hex_tuple(
                [drop_sql, current_body, *current_acl]
            ),
            "legal_red_case_tuple": [
                "tests/slice100_task7/"
                "test_task7_v19_erasure_execution_registry_red.py::"
                "test_v19_preserves_current48_future38_runner86_exactly"
            ],
            "illegal_red_case_tuple": [
                "tests/slice100_task7/"
                "test_task7_v19_erasure_execution_registry_red.py::"
                "test_v19_declares_exact_seven_relation_read_closure_with_audit_events_last"
            ],
            "direct_sql_red_case_tuple": [
                "tests/slice100_task7/"
                "test_task7_v19_erasure_execution_registry_red.py::"
                "test_v19_current_and_future_acl_are_exactly_column_limited"
            ],
        }
    )
    values.pop("expected_sha256")
    row = {field: values[field] for field in _V2_FIELDS if field != "expected_sha256"}
    row["expected_sha256"] = ""
    _rehash_erasure_row(row)
    section["rows"] = [row]
    section["expected_sha256"] = _h([_ERASURE_REGISTRY_HASH_DOMAIN, section["rows"]])


def _transform_reviewed_design(artifact: dict[str, Any]) -> None:
    metadata = artifact["metadata"]
    try:
        design = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]))
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError("TASK7_V19_REVIEWED_DESIGN_BASE_INVALID") from exc
    if not isinstance(design, list) or design[:2] != [
        "full-strict-v18-reviewed-design-literal-v1",
        ["pipeline_contract_version", _V18],
    ]:
        _error("TASK7_V19_REVIEWED_DESIGN_BASE_INVALID")
    design[0] = "full-strict-v19-reviewed-design-literal-v1"
    design[1] = ["pipeline_contract_version", _V19]
    design.extend(
        [
            ["erasure_audit_lineage_contract", copy.deepcopy(_AUDIT_LINEAGE_CONTRACT)],
            [
                "erasure_object_reconciliation_contract",
                copy.deepcopy(_OBJECT_RECONCILIATION_CONTRACT),
            ],
            [
                "erasure_uuidv5_fixed_vectors",
                [
                    [_ATTEMPT_NAMESPACE, _FIXED_ATTEMPT_ID],
                    [_EVENT_NAMESPACE, [list(row) for row in _FIXED_EVENT_IDS]],
                ],
            ],
            ["erasure_execution_authority_version", "2"],
        ]
    )
    rendered = _canonical_bytes(design)
    metadata["artifact_schema"] = "full-strict-v19-reviewed-preimage-v1"
    metadata["pipeline_contract_version"] = _V19
    metadata["reviewed_design_sha256"] = _sha256(rendered)
    metadata["reviewed_design_utf8_hex"] = rendered.hex()


def _transform_digest_recipe(artifact: dict[str, Any]) -> None:
    rows = artifact["digest_recipe_registry"]["rows"]
    expected = [
        "pipeline_contract_hash",
        '["pipeline-contract-v18","full-strict-v18",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]
    if rows[5] != expected:
        _error("TASK7_V19_PIPELINE_RECIPE_BASE_INVALID")
    rows[5][1] = (
        '["pipeline-contract-v19","full-strict-v19",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(rows).hex()


def _build_v19_core(v18: dict[str, Any]) -> dict[str, Any]:
    artifact = copy.deepcopy(v18)
    artifact["metadata"]["append_frozen"] = False
    _transform_tuple_schemas(artifact)
    _transform_uuid_registry(artifact)
    _transform_version_authorities(artifact, reverse=False)
    _rehash_sql_registry(artifact)
    _transform_erasure_registry(artifact)
    _transform_reviewed_design(artifact)
    _transform_digest_recipe(artifact)
    return artifact


def _validate_exact_diff(v18: dict[str, Any], v19: dict[str, Any]) -> None:
    if _render(v18) != _V18_ARTIFACT.read_bytes():
        _error("TASK7_V19_BASE_ARTIFACT_HASH_MISMATCH")
    if tuple(v19) != tuple(v18):
        _error("TASK7_V19_SECTION_ORDER_DRIFT")
    for section in _SEMANTIC_INVARIANT_SECTIONS:
        if v19[section] != v18[section]:
            _error("TASK7_V19_INVARIANT_DRIFT", section)
    changed = _changed_paths(v18, v19)
    if {path[0] for path in changed} != _APPROVED_CHANGED_SECTIONS:
        _error("TASK7_V19_EXACT_DIFF_INVALID", "top-level")
    changed_rows = {
        path[2]
        for path in changed
        if path[:2] == ("sql_authority_registry", "rows") and len(path) > 2
    }
    if changed_rows != _ALLOWED_CHANGED_AUTHORITY_ORDINALS:
        _error("TASK7_V19_EXACT_DIFF_INVALID", "authority rows")
    if (
        v19.get("metadata", {}).get("artifact_schema") != "full-strict-v19-reviewed-preimage-v1"
        or v19.get("metadata", {}).get("pipeline_contract_version") != _V19
        or v19.get("metadata", {}).get("append_frozen") is not False
    ):
        _error("TASK7_V19_UNFROZEN_CANDIDATE_REQUIRED")
    expected = _build_v19_core(v18)
    if _render(expected) != _render(v19):
        _error("TASK7_V19_EXACT_DIFF_INVALID", "candidate bytes")
    validate_uuidv5_fixed_vectors(v19)


def build_v19(v18: dict[str, Any]) -> dict[str, Any]:
    """Build the deterministic, unfrozen v19 erasure-composition candidate."""

    pinned = _load_pinned_v18()
    if _render(v18) != _render(pinned):
        _error("TASK7_V19_BASE_ARTIFACT_HASH_MISMATCH")
    artifact = _build_v19_core(v18)
    _validate_exact_diff(v18, artifact)
    return artifact


def project_v19_to_v18(v19: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the exact immutable append-frozen v18 bytes from v19."""

    v18 = _load_pinned_v18()
    _validate_exact_diff(v18, v19)
    projected = copy.deepcopy(v19)
    for section in _APPROVED_CHANGED_SECTIONS:
        projected[section] = copy.deepcopy(v18[section])
    if _render(projected) != _V18_ARTIFACT.read_bytes():
        _error("TASK7_V19_EXACT_V18_PROJECTION_HASH_MISMATCH")
    return projected


def _write_exclusive(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        _error("TASK7_V19_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
    except FileExistsError as exc:
        raise MaterializationError("TASK7_V19_OUTPUT_ALREADY_EXISTS") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-artifact", type=Path, default=_V18_ARTIFACT)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        raw = args.base_artifact.read_bytes()
        if (
            _sha256(raw) != _V18_ARTIFACT_SHA256
            or hashlib.sha512(raw).hexdigest() != _V18_ARTIFACT_SHA512
        ):
            _error("TASK7_V19_BASE_ARTIFACT_HASH_MISMATCH")
        v18 = json.loads(raw)
        if not isinstance(v18, dict):
            _error("TASK7_V19_BASE_ARTIFACT_INVALID")
        rendered = _render(build_v19(v18))
        _write_exclusive(args.output, rendered)
    except (
        MaterializationError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"TASK7_V19_UNFROZEN_CANDIDATE_MATERIALIZED:{_sha256(rendered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MaterializationError",
    "_changed_paths",
    "_render",
    "_validate_exact_diff",
    "build_v19",
    "main",
    "project_v19_to_v18",
    "validate_uuidv5_fixed_vectors",
]

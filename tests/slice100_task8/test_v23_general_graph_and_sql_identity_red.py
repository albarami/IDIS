"""RED contracts for v23 SQL identity closure and the general dependency graph."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Any

import pytest

from . import _v23_oracle as oracle

_ACL_ROW_DOMAIN = b"full-strict-v12-guard-function-acl-row-sha256-v1\x00"
_ACL_REGISTRY_DOMAIN = "full-strict-v12-guard-function-acl-registry-v1"
_SQL_ROW_DOMAIN = "full-strict-v22-sql-authority-row-v1"
_SQL_REGISTRY_DOMAIN = "full-strict-v22-sql-authority-registry-v1"
_DASHBOARD_AUTHORITY = "dashboard-root-equation-v2"
_OLD_PROJECTION_PREFIX = (
    'preimage:=\'["methodology-truth-dashboard-root-projection-v1","\'||'
    "dashboard_row.tenant_id::text||'\",\"'||dashboard_row.deal_id::text||'\",\"'||"
    "dashboard_row.run_id::text||'\",\"'||dashboard_row.dashboard_row_id::text||'\",\"'||"
    "dashboard_row.run_output_object_id::text||'\",\"'||"
    "dashboard_row.coverage_record_id"
)
_REQUIRED_PROJECTION_PREFIX = (
    'preimage:=\'["methodology-truth-dashboard-root-projection-v2","\'||'
    "dashboard_row.tenant_id::text||'\",\"'||dashboard_row.deal_id::text||'\",\"'||"
    "dashboard_row.run_id::text||'\",\"'||dashboard_row.dashboard_row_id::text||'\",\"'||"
    "dashboard_row.coverage_record_id"
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _authority(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    matches = [
        row
        for row in artifact["sql_authority_registry"]["rows"]
        if row["authority_id"] == _DASHBOARD_AUTHORITY
    ]
    assert len(matches) == 1, "TASK8_V23_DASHBOARD_SQL_AUTHORITY_CARDINALITY_INVALID"
    return matches[0]


def _function_statement(authority: Mapping[str, Any]) -> str:
    statements = [
        bytes.fromhex(value).decode("utf-8") for value in authority["body_sql_utf8_hex_tuple"]
    ]
    matches = [
        value
        for value in statements
        if value.startswith("CREATE FUNCTION public.idis_task8_dashboard_root_equation_v2()")
    ]
    assert len(matches) == 1, "TASK8_V23_DASHBOARD_SQL_FUNCTION_CARDINALITY_INVALID"
    return matches[0]


def _function_body(statement: str) -> str:
    body = statement.partition(" AS $$")[2].rsplit("$$", 1)[0]
    assert body, "TASK8_V23_DASHBOARD_SQL_FUNCTION_BODY_INVALID"
    return body


def _without_hash(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "expected_sha256"}


def _pointer_value(
    pointer: str,
    full_strict: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> object:
    artifact, encoded = pointer.split(":#/", 1)
    value: object = full_strict if artifact == "full_strict" else policy
    for raw in encoded.split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]  # type: ignore[index]
    return value


def _flatten(value: object, path: str = "$") -> list[tuple[str, object]]:
    if isinstance(value, dict):
        return [item for key in sorted(value) for item in _flatten(value[key], f"{path}.{key}")]
    if isinstance(value, list):
        return [
            item
            for ordinal, child in enumerate(value)
            for item in _flatten(child, f"{path}[{ordinal}]")
        ]
    return [(path, value)]


def _payload(pointer: str, value: object) -> object:
    if "/uuid_namespace_registry/rows/" in pointer:
        return value[2]  # type: ignore[index]
    if "/digest_recipe_registry/rows/" in pointer:
        return value[1]  # type: ignore[index]
    return value


def _derive(materializer: Any, artifact: Mapping[str, Any]) -> dict[str, Any]:
    return materializer.derive_identity_hash_dependency_graph(
        full_strict_artifact=copy.deepcopy(dict(artifact)),
        policy_artifact=copy.deepcopy(oracle.load_policy_v2()),
    )


def test_dashboard_sql_replays_projection_v2_without_its_own_output_identity() -> None:
    authority = _authority(oracle.load_v23())
    body = _function_body(_function_statement(authority))

    if _REQUIRED_PROJECTION_PREFIX not in body:
        pytest.fail("TASK8_V23_DASHBOARD_SQL_PROJECTION_V2_MISSING")
    if _OLD_PROJECTION_PREFIX in body:
        pytest.fail("TASK8_V23_DASHBOARD_SQL_PROJECTION_V1_RETAINED")
    projection_sql = body[body.index("preimage:=") : body.index("actual_hash:=")]
    if "dashboard_row.run_output_object_id" in projection_sql:
        pytest.fail("TASK8_V23_DASHBOARD_SQL_SELF_IDENTITY_RETAINED")


def test_dashboard_sql_body_authority_and_acl_hashes_form_one_exact_closure() -> None:
    artifact = oracle.load_v23()
    authority = _authority(artifact)
    statement = _function_statement(authority)
    body_sha256 = hashlib.sha256(_function_body(statement).encode("utf-8")).hexdigest()
    acl_registry = artifact["sql_authority_registry"]["guard_function_acl_registry"]
    acl_matches = [
        row for row in acl_registry["function_rows"] if row["authority_id"] == _DASHBOARD_AUTHORITY
    ]
    assert len(acl_matches) == 1, "TASK8_V23_DASHBOARD_ACL_CARDINALITY_INVALID"
    acl = acl_matches[0]

    evidence_hashes = {
        evidence[1]
        for _relation, _columns, evidence in acl["direct_read_column_tuple"]
        if evidence[0] == "SQL_BODY_SHA256"
    }
    evidence_hashes.update(
        evidence[1]
        for _relation, _columns, evidence_rows in acl["transitive_read_column_tuple"]
        for evidence in evidence_rows
        if evidence[0] == "SQL_BODY_SHA256"
    )
    if evidence_hashes != {body_sha256}:
        pytest.fail("TASK8_V23_DASHBOARD_ACL_BODY_HASH_CLOSURE_INVALID")

    acl_expected = hashlib.sha256(
        _ACL_ROW_DOMAIN + _canonical_bytes(_without_hash(acl))
    ).hexdigest()
    if acl["expected_sha256"] != acl_expected:
        pytest.fail("TASK8_V23_DASHBOARD_ACL_ROW_HASH_CLOSURE_INVALID")
    registry_expected = _canonical_sha256(
        [
            _ACL_REGISTRY_DOMAIN,
            acl_registry["guard_owner_role"],
            acl_registry["function_rows"],
            acl_registry["current_install_function_signature_tuple"],
            acl_registry["current_column_grant_sql_utf8_hex_tuple"],
            acl_registry["current_column_revoke_sql_utf8_hex_tuple"],
            acl_registry["retained_0037_catalog_projection_tuple"],
            acl_registry["retained_0037_trigger_catalog_projection_tuple"],
            acl_registry["retained_0037_catalog_preflight_sql_utf8_hex"],
            acl_registry["installation_sql_utf8_hex_tuple"],
            acl_registry["downgrade_authority"],
        ]
    )
    if acl_registry["expected_sha256"] != registry_expected:
        pytest.fail("TASK8_V23_DASHBOARD_ACL_REGISTRY_HASH_CLOSURE_INVALID")

    authority_expected = _canonical_sha256([_SQL_ROW_DOMAIN, _without_hash(authority)])
    if authority["expected_sha256"] != authority_expected:
        pytest.fail("TASK8_V23_DASHBOARD_SQL_ROW_HASH_CLOSURE_INVALID")
    sql_registry = artifact["sql_authority_registry"]
    sql_expected = _canonical_sha256(
        [
            _SQL_REGISTRY_DOMAIN,
            sql_registry["rows"],
            acl_registry,
        ]
    )
    if sql_registry["expected_sha256"] != sql_expected:
        pytest.fail("TASK8_V23_DASHBOARD_SQL_REGISTRY_HASH_CLOSURE_INVALID")


def test_general_guard_rejects_cycle_between_existing_non_dashboard_declarations() -> None:
    materializer = oracle.load_materializer()
    artifact = copy.deepcopy(oracle.load_v23())
    rows = {row[0]: row for row in artifact["uuid_namespace_registry"]["rows"]}
    rows["step_id"][2].append("source_lineage_object_id")
    rows["source_lineage_object_id"][2].append("step_id")

    with pytest.raises(
        materializer.IdentityHashDependencyError,
        match="TASK8_IDENTITY_HASH_CYCLE",
    ):
        _derive(materializer, artifact)


def test_every_independently_enumerated_source_token_has_edge_or_typed_leaf_receipt() -> None:
    materializer = oracle.load_materializer()
    artifact = oracle.load_v23()
    policy = oracle.load_policy_v2()
    graph = _derive(materializer, artifact)

    if tuple(graph["source_pointer_tuple"]) != oracle.EXPECTED_SOURCE_POINTER_TUPLE:
        pytest.fail("TASK8_IDENTITY_HASH_INDEPENDENT_SOURCE_MANIFEST_MISMATCH")
    independently_enumerated = {
        (pointer, path)
        for pointer in oracle.EXPECTED_SOURCE_POINTER_TUPLE
        for path, _value in _flatten(_payload(pointer, _pointer_value(pointer, artifact, policy)))
    }
    edge_receipts = {
        (token_pointer.split(":$", 1)[0], "$" + token_pointer.split(":$", 1)[1])
        for _derived, _dependency, token_pointer in graph["edge_tuple"]
        if ":$" in token_pointer
    }
    leaf_receipts = {(leaf[4], leaf[1]) for leaf in graph["leaf_tuple"]}
    allowed_leaf_authorities = {
        "BOUND_RUNTIME_INPUT",
        "CONTRACT_LITERAL",
        "FOREIGN_ROOT_INPUT",
        "PINNED_CONTRACT_INPUT",
        "SCALAR_OR_FOREIGN_INPUT",
    }
    if any(leaf[3] not in allowed_leaf_authorities for leaf in graph["leaf_tuple"]):
        pytest.fail("TASK8_IDENTITY_HASH_LEAF_AUTHORITY_UNKNOWN")
    if independently_enumerated != edge_receipts | leaf_receipts:
        pytest.fail("TASK8_IDENTITY_HASH_TOKEN_RECEIPT_CLOSURE_INVALID")

    # Expected governed symbols come from the independently walked artifact
    # authorities, not from graph nodes emitted by the implementation under
    # test.  This prevents graph discovery from certifying itself.
    produced_symbols = {str(row[0]) for row in artifact["uuid_namespace_registry"]["rows"]}
    produced_symbols.update(str(row[0]) for row in artifact["digest_recipe_registry"]["rows"])
    produced_symbols.update(
        {
            "native_identity_sha256",
            "native_output_sha256",
            "root_projection_sha256",
            "row_payload_sha256",
        }
    )
    node_by_id = {oracle.graph_node_id(node): node for node in graph["node_tuple"]}
    governed_edges = {
        (token_pointer.split(":$", 1)[0], "$" + token_pointer.split(":$", 1)[1], dependency)
        for _derived, dependency, token_pointer in graph["edge_tuple"]
        if ":$" in token_pointer
    }
    for pointer in oracle.EXPECTED_SOURCE_POINTER_TUPLE:
        for path, value in _flatten(_payload(pointer, _pointer_value(pointer, artifact, policy))):
            if isinstance(value, str) and value in produced_symbols:
                matches = [
                    dependency
                    for source, token_path, dependency in governed_edges
                    if source == pointer and token_path == path
                ]
                if not matches or any(node_by_id[item][4] != value for item in matches):
                    pytest.fail("TASK8_IDENTITY_HASH_GOVERNED_TOKEN_EDGE_MISSING")


def test_non_dashboard_dependency_mutation_changes_edge_count_and_graph_receipt() -> None:
    materializer = oracle.load_materializer()
    artifact = oracle.load_v23()
    baseline = _derive(materializer, artifact)
    mutated_artifact = copy.deepcopy(artifact)
    step_row = next(
        row for row in mutated_artifact["uuid_namespace_registry"]["rows"] if row[0] == "step_id"
    )
    step_row[2].append("source_lineage_object_id")

    mutated = _derive(materializer, mutated_artifact)

    if mutated["counts"]["edge_count"] != baseline["counts"]["edge_count"] + 1:
        pytest.fail("TASK8_IDENTITY_HASH_NON_DASHBOARD_EDGE_COUNT_UNCHANGED")
    if mutated["graph_sha256"] == baseline["graph_sha256"]:
        pytest.fail("TASK8_IDENTITY_HASH_NON_DASHBOARD_GRAPH_RECEIPT_UNCHANGED")

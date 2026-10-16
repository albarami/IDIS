"""RED contracts for the reviewed Task-7 complete erasure-runner surface.

The 72-relation contract census and the executable erasure surface are deliberately
different things.  These tests derive both sides from their normative byte/code
sources so a production eraser change cannot silently escape the hash-bound runner.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from typing import Any

import pytest

from idis.compliance import erasure_postgres

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V4_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v4_reviewed_preimage_v1.json"

_RUNNER_NAME = "idis_task7_execute_approved_erasure"
_DECLARED_READ_CLOSURE = (
    "erasure_requests",
    "deterministic_calculations",
    "runs",
    "human_gates",
    "legal_holds",
    "document_artifacts",
)
_REVIEWED_LEGACY_DELTA = (
    "break_glass_grants",
    "claims",
    "deal_assignments",
    "deals",
    "defects",
    "evidence_items",
    "human_gate_actions",
    "human_gates",
    "methodology_extraction_attempts",
    "methodology_extraction_task_spans",
    "overrides",
    "run_methodology_selections",
    "sanads",
    "strict_preflight_proofs",
)
_INDIRECT_CHILD_PARENTS = {
    "calc_sanads": "deterministic_calculations",
    "run_steps": "runs",
    "human_gate_actions": "human_gates",
}

_DELETE_RE = re.compile(
    r"\bDELETE\s+FROM\s+public\.(?P<table>[a-z][a-z0-9_]*)\b(?P<body>.*?);",
    re.IGNORECASE | re.DOTALL,
)
_EXPLICIT_PRODUCTION_DELETE_RE = re.compile(
    r"\bDELETE\s+FROM\s+(?P<table>[a-z][a-z0-9_]*)\b",
    re.IGNORECASE,
)


def _artifact() -> dict[str, Any]:
    assert _V4_ARTIFACT.is_file(), (
        "TASK7_ERASURE_RUNNER_V4_ARTIFACT_ABSENT: materialize the admitted v4 bytes "
        "before the erasure runner can be accepted"
    )
    value = json.loads(_V4_ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _reviewed_relation_tuple(artifact: dict[str, Any]) -> tuple[str, ...]:
    section = artifact.get("erasure_materialized")
    assert isinstance(section, dict)
    assert section.get("physical_table_count") == 72
    assert section.get("phase_count") == 66
    rows = section.get("rows")
    assert isinstance(rows, list) and rows

    relations: list[str] = []
    for expected_rank, row in enumerate(rows):
        assert isinstance(row, list) and len(row) == 3
        rank, phase, phase_relations = row
        assert rank == expected_rank
        assert isinstance(phase, int) and not isinstance(phase, bool)
        assert isinstance(phase_relations, list) and phase_relations
        assert all(
            isinstance(relation, str) and re.fullmatch(r"[a-z][a-z0-9_]*", relation)
            for relation in phase_relations
        )
        relations.extend(phase_relations)

    assert len(relations) == len(set(relations)) == 72
    return tuple(relations)


def _authority_row(artifact: dict[str, Any]) -> dict[str, Any]:
    section = artifact.get("erasure_execution_registry")
    assert isinstance(section, dict)
    rows = section.get("rows")
    assert isinstance(rows, list) and len(rows) == 1
    row = rows[0]
    assert isinstance(row, dict)
    return row


def _runner_sql(authority_row: dict[str, Any]) -> str:
    decoded = _authority_sql(authority_row, "future_body_sql_utf8_hex_tuple")
    matches = [
        statement
        for statement in decoded
        if re.search(rf"\bCREATE\s+FUNCTION\s+public\.{_RUNNER_NAME}\b", statement, re.I)
    ]
    assert len(matches) == 1
    return matches[0]


def _authority_sql(authority_row: dict[str, Any], field: str) -> tuple[str, ...]:
    encoded = authority_row.get(field)
    assert isinstance(encoded, list) and encoded
    return tuple(bytes.fromhex(item).decode("utf-8") for item in encoded)


def _grant_table_privileges(sql: str, *, principal: str) -> dict[str, dict[str, set[str] | None]]:
    grants: dict[str, dict[str, set[str] | None]] = {}
    pattern = re.compile(
        rf"\bGRANT\s+(?P<privilege>[A-Z]+)"
        rf"(?:\s*\((?P<columns>[a-z0-9_,\s]+)\))?\s+ON\s+TABLE\s+"
        rf"(?P<relations>[^;\r\n]+?)\s+TO\s+{re.escape(principal)}\b",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(sql):
        privilege = match.group("privilege").lower()
        columns_literal = match.group("columns")
        columns = (
            {column.strip().lower() for column in columns_literal.split(",")}
            if columns_literal is not None
            else None
        )
        relations = {
            relation.strip().lower().removeprefix("public.")
            for relation in match.group("relations").split(",")
        }
        assert all(re.fullmatch(r"[a-z][a-z0-9_]*", relation) for relation in relations)
        for relation in relations:
            relation_grants = grants.setdefault(relation, {})
            assert privilege not in relation_grants
            relation_grants[privilege] = columns
    return grants


def _runner_delete_entries(authority_row: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    entries = tuple(
        (match.group("table").lower(), match.group(0))
        for match in _DELETE_RE.finditer(_runner_sql(authority_row))
    )
    assert len(entries) == 86, (
        f"TASK7_ERASURE_RUNNER_RELATION_COUNT_INVALID: expected 86 DELETEs, got {len(entries)}"
    )
    assert len({table for table, _statement in entries}) == 86, (
        "TASK7_ERASURE_RUNNER_RELATION_DUPLICATE"
    )
    return entries


def _production_eraser_relation_set() -> frozenset[str]:
    """Derive all targets from production constants plus literal post-loop SQL."""

    child_targets = {table for table, _sql in erasure_postgres._CHILD_TABLE_DELETES}
    direct_targets = set(erasure_postgres._DIRECT_DELETE_ORDER)
    method_source = inspect.getsource(erasure_postgres.PostgresErasureExecutor.erase_deal)
    explicit_targets = {
        match.group("table").lower()
        for match in _EXPLICIT_PRODUCTION_DELETE_RE.finditer(method_source)
    }
    # The f-string loop is represented by the reviewed _DIRECT_DELETE_ORDER.  Only
    # the two literal post-loop statements may augment that derived target set.
    assert explicit_targets == {"deals", "strict_preflight_proofs"}
    result = frozenset((*child_targets, *direct_targets, *explicit_targets))
    assert len(result) == 39
    return result


def _assert_complete_superset(
    *,
    reviewed: frozenset[str],
    production: frozenset[str],
    runner: frozenset[str],
) -> None:
    assert reviewed < runner, "TASK7_ERASURE_RUNNER_REVIEWED_NOT_STRICT_SUBSET"
    assert runner == reviewed | production, "TASK7_ERASURE_RUNNER_PRODUCTION_DRIFT"
    assert runner - reviewed == production - reviewed, "TASK7_ERASURE_RUNNER_LEGACY_DELTA_DRIFT"


def _argument_pattern(positional_argument: int) -> str:
    return (
        rf"(?:\${positional_argument}(?:\s*::\s*uuid)?|"
        rf"cast\(\s*\${positional_argument}\s+as\s+uuid\s*\))"
    )


def _bound_coordinate(fragment: str, column: str, positional_argument: int) -> bool:
    normalized = " ".join(fragment.lower().split())
    qualified_column = rf"(?:[a-z][a-z0-9_]*\.)?{column}"
    argument = _argument_pattern(positional_argument)
    return bool(
        re.search(rf"\b{qualified_column}\s*=\s*{argument}\b", normalized)
        or re.search(rf"{argument}\s*=\s*\b{qualified_column}\b", normalized)
    )


def _bound_qualified_coordinate(
    fragment: str, relation: str, column: str, positional_argument: int
) -> bool:
    normalized = " ".join(fragment.lower().split())
    coordinate = rf"\b{relation}\.{column}"
    argument = _argument_pattern(positional_argument)
    return bool(
        re.search(rf"{coordinate}\s*=\s*{argument}\b", normalized)
        or re.search(rf"{argument}\s*=\s*{coordinate}\b", normalized)
    )


def test_runner_is_the_exact_86_relation_superset_derived_from_production() -> None:
    artifact = _artifact()
    reviewed = frozenset(_reviewed_relation_tuple(artifact))
    production = _production_eraser_relation_set()
    entries = _runner_delete_entries(_authority_row(artifact))
    runner = frozenset(table for table, _statement in entries)

    _assert_complete_superset(reviewed=reviewed, production=production, runner=runner)
    assert len(reviewed) == 72
    assert len(runner) == 86
    assert len(reviewed & production) == 25
    assert tuple(sorted(production - reviewed)) == _REVIEWED_LEGACY_DELTA


def test_future_production_target_without_runner_amendment_fails_closed() -> None:
    artifact = _artifact()
    reviewed = frozenset(_reviewed_relation_tuple(artifact))
    production = _production_eraser_relation_set()
    runner = frozenset(table for table, _ in _runner_delete_entries(_authority_row(artifact)))

    with pytest.raises(AssertionError, match="TASK7_ERASURE_RUNNER_PRODUCTION_DRIFT"):
        _assert_complete_superset(
            reviewed=reviewed,
            production=production | {"future_unreviewed_legacy_relation"},
            runner=runner,
        )


def test_runner_declares_and_uses_only_the_six_approved_read_parents() -> None:
    row = _authority_row(_artifact())
    assert tuple(row.get("declared_read_relation_tuple", ())) == _DECLARED_READ_CLOSURE

    runner_sql = _runner_sql(row).lower()
    referenced_relations = set(re.findall(r"\bpublic\.([a-z][a-z0-9_]*)\b", runner_sql))
    referenced_relations.discard(_RUNNER_NAME)
    delete_relations = {table for table, _statement in _runner_delete_entries(row)}
    assert referenced_relations <= delete_relations | set(_DECLARED_READ_CLOSURE)
    assert "for update" in " ".join(runner_sql.split())
    for parent in _DECLARED_READ_CLOSURE:
        assert f"public.{parent}" in runner_sql


def test_every_delete_binds_exact_tenant_and_deal_arguments() -> None:
    entries = _runner_delete_entries(_authority_row(_artifact()))
    for table, fragment in entries:
        assert _bound_coordinate(fragment, "tenant_id", 2), (
            f"TASK7_ERASURE_RUNNER_TENANT_PREDICATE_MISSING:{table}"
        )
        assert _bound_coordinate(fragment, "deal_id", 3), (
            f"TASK7_ERASURE_RUNNER_DEAL_PREDICATE_MISSING:{table}"
        )


def test_indirect_children_use_their_own_tenant_and_exact_declared_parent_scope() -> None:
    entries = dict(_runner_delete_entries(_authority_row(_artifact())))
    for child, parent in _INDIRECT_CHILD_PARENTS.items():
        fragment = " ".join(entries[child].lower().split())
        assert _bound_qualified_coordinate(fragment, child, "tenant_id", 2)
        assert f"from public.{parent}" in fragment
        assert _bound_qualified_coordinate(fragment, parent, "tenant_id", 2)
        assert _bound_qualified_coordinate(fragment, parent, "deal_id", 3)


def test_function_owner_and_executor_acl_is_exactly_least_privilege() -> None:
    row = _authority_row(_artifact())
    sql = "\n".join(_authority_sql(row, "future_acl_sql_utf8_hex_tuple"))
    runner_relations = {table for table, _statement in _runner_delete_entries(row)}

    owner_grants = _grant_table_privileges(sql, principal="idis_erasure_function_owner")
    expected_select = {table: {"tenant_id", "deal_id"} for table in runner_relations}
    expected_select.update(
        {
            "erasure_requests": {"request_id", "tenant_id", "deal_id", "status"},
            "deterministic_calculations": {"tenant_id", "deal_id", "calc_id"},
            "runs": {"tenant_id", "deal_id", "run_id"},
            "human_gates": {"tenant_id", "deal_id", "gate_id"},
            "legal_holds": {"tenant_id", "target_type", "target_id", "lifted_at"},
            "document_artifacts": {"tenant_id", "deal_id", "doc_id", "uri"},
            "calc_sanads": {"tenant_id", "calc_id"},
            "run_steps": {"tenant_id", "run_id"},
            "human_gate_actions": {"tenant_id", "gate_id"},
        }
    )
    assert set(owner_grants) == runner_relations | {"erasure_requests", "legal_holds"}
    for relation, privileges in owner_grants.items():
        assert privileges.get("select") == expected_select[relation]
        if relation in runner_relations:
            assert privileges.get("delete") is None
        else:
            assert "delete" not in privileges
        if relation == "erasure_requests":
            assert privileges.get("update") == {"request_id"}
        else:
            assert "update" not in privileges
        assert "insert" not in privileges
    assert _grant_table_privileges(sql, principal="idis_erasure_executor") == {}
    assert re.search(
        r"\bGRANT\s+USAGE\s+ON\s+SCHEMA\s+public\s+TO\s+"
        r"idis_erasure_function_owner\b",
        sql,
        re.IGNORECASE,
    )
    assert not re.search(
        r"\bGRANT\s+(?:INSERT|TRUNCATE|ALL|CREATE)\b.+?\bTO\s+"
        r"idis_erasure_function_owner\b",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    # PostgreSQL requires the approved request_id-only UPDATE grant for FOR UPDATE;
    # an unqualified/broader table UPDATE grant remains forbidden.
    assert not re.search(
        r"\bGRANT\s+UPDATE\s+ON\s+TABLE\b.+?\bTO\s+"
        r"idis_erasure_function_owner\b",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert not re.search(
        r"\bGRANT\s+[a-z][a-z0-9_]*\s+TO\s+"
        r"(?:idis_erasure_function_owner|idis_erasure_executor)\b",
        sql,
        re.IGNORECASE,
    )
    assert re.search(
        rf"\bGRANT\s+EXECUTE\s+ON\s+FUNCTION\s+public\.{_RUNNER_NAME}"
        r"\s*\(\s*uuid\s*,\s*uuid\s*,\s*uuid\s*\)\s+TO\s+"
        r"idis_erasure_executor\b",
        sql,
        re.IGNORECASE,
    )

"""Admission gate for the literal full-strict-v4 SQL-authority registry."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V4_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v4_reviewed_preimage_v1.json"
_ARTIFACT = _V4_ARTIFACT
_GENERATOR_PATH = _REPO_ROOT / "scripts" / "generate_task7_v4_contract_fixture.py"
_GENERATOR_MODULE: ModuleType | None = None

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_HEX_RE = re.compile(r"^(?:[0-9a-f]{2})+$")
_ROW_HASH_DOMAIN = b"full-strict-v4-sql-authority-row-sha256-v1\x00"
_REGISTRY_HASH_DOMAIN = "full-strict-v4-sql-authority-registry-v1"


def _generator() -> ModuleType:
    global _GENERATOR_MODULE
    if _GENERATOR_MODULE is None:
        module_name = "task7_contract_generator_sql_materialization"
        spec = importlib.util.spec_from_file_location(module_name, _GENERATOR_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        _GENERATOR_MODULE = module
    return _GENERATOR_MODULE


def _assertion_from_contract_error(callback: Any) -> Any:
    try:
        return callback()
    except ValueError as exc:
        raise AssertionError(str(exc)) from exc


_SQL_AUTHORITY_FIELDS = (
    "authority_ordinal",
    "authority_id",
    "authority_version",
    "authority_kind",
    "target_tuple",
    "declared_parent_relation_tuple",
    "body_sql_utf8_hex_tuple",
    "legal_red_case_tuple",
    "illegal_red_case_tuple",
    "direct_sql_red_case_tuple",
    "expected_sha256",
)

_REQUIRED_AUTHORITY_IDS = (
    "dashboard-root-equation-v1",
    "graph-rag-namespace-v1",
    "rag-query-parent-coordinate-v1",
    "layer2-readiness-v1",
    "extract-rag-context-coordinate-v1",
    "debate-muhasabah-exact-one-v1",
    "analysis-muhasabah-exact-one-v1",
    "layer2-muhasabah-exact-one-v1",
    "grading-dispatch-and-consistency-v1",
    "scoring-binary64-finite-v1",
    "scoring-binary64-negative-zero-v1",
    "approved-erasure-append-only-v1",
    "native-root-exact-one-v1",
    "identity-identifier-projection-v1",
    "scoring-dimension-projection-v1",
    "generic-claim-projection-v1",
    "generic-evidence-projection-v1",
)


_REQUIRED_RED_CASES = dict(_generator()._REQUIRED_SQL_RED_CASES)
_UNRESOLVED_AUTHORITY_IDS: frozenset[str] = frozenset()

_DEFERRED_INSERT_UPDATE_DELETE_AUTHORITIES = (
    "dashboard-root-equation-v1",
    "rag-query-parent-coordinate-v1",
    "extract-rag-context-coordinate-v1",
    "debate-muhasabah-exact-one-v1",
    "analysis-muhasabah-exact-one-v1",
    "layer2-muhasabah-exact-one-v1",
    "grading-dispatch-and-consistency-v1",
    "identity-identifier-projection-v1",
    "scoring-dimension-projection-v1",
    "generic-claim-projection-v1",
    "generic-evidence-projection-v1",
)

_PROVIDERS18 = (
    "sec_edgar",
    "companies_house",
    "github",
    "fred",
    "finnhub",
    "fmp",
    "world_bank",
    "escwa_catalog",
    "escwa_ispar",
    "qatar_open_data",
    "hackernews",
    "gdelt",
    "patentsview",
    "wayback",
    "google_news_rss",
    "epo_open_patent",
    "semantic_scholar",
    "perplexity",
)

_FUTURE_NATIVE_TABLES = (
    "methodology_truth_dashboard_rows",
    "company_identity_outputs",
    "enrichment_provider_outcomes",
    "enrichment_normalized_facts",
    "graph_projection_results",
    "rag_index_results",
    "rag_query_results",
    "external_intelligence_conflict_plans",
    "external_intelligence_conflict_executions",
    "external_intelligence_conflict_findings",
    "validated_evidence_package_references",
    "debate_role_outputs",
    "analysis_agent_outputs",
    "scoring_outputs",
    "layer2_readiness_outputs",
    "layer2_role_outputs",
    "generic_extraction_outputs",
    "run_claim_grading_links",
)

_FUTURE_REFERENCE_TABLES = (
    "company_identity_input_references",
    "generic_extraction_input_references",
    "graph_result_input_references",
    "rag_index_input_references",
    "rag_query_input_references",
    "conflict_input_references",
    "evidence_trust_court_input_references",
    "validated_evidence_package_input_references",
    "debate_role_input_references",
    "analysis_agent_input_references",
    "scoring_input_references",
    "layer2_readiness_input_references",
    "layer2_role_input_references",
    "deliverable_artifact_input_references",
    "step_receipt_input_references",
)

_FUTURE_CHILD_TABLES = (
    "company_identity_identifiers",
    "scoring_output_dimensions",
    "generic_extraction_claim_links",
    "generic_extraction_evidence_links",
    "methodology_truth_dashboard_root_links",
)

_FUTURE_RELATION_TABLES = (
    *_FUTURE_NATIVE_TABLES,
    *_FUTURE_REFERENCE_TABLES,
    *_FUTURE_CHILD_TABLES,
)

# These tuples are intentionally literal rather than inferred from the authority SQL.
# They are the reviewed relational capability boundary: ``target_tuple`` says where
# an authority may install an object, while ``declared_parent_relation_tuple`` says
# which durable relations its body may read. A bidirectional deferred guard lists
# both halves as targets and, because either half can be the other trigger's parent,
# also lists both halves in the readable-parent tuple.
_REQUIRED_TARGET_TABLES: dict[str, tuple[str, ...]] = {
    "dashboard-root-equation-v1": (
        "methodology_truth_dashboard_rows",
        "methodology_truth_dashboard_root_links",
    ),
    "graph-rag-namespace-v1": (
        "graph_projection_results",
        "rag_index_results",
        "rag_query_results",
    ),
    "rag-query-parent-coordinate-v1": ("rag_query_results",),
    "layer2-readiness-v1": ("layer2_readiness_outputs",),
    "extract-rag-context-coordinate-v1": ("generic_extraction_outputs",),
    "debate-muhasabah-exact-one-v1": (
        "debate_role_outputs",
        "muhasabah_records",
    ),
    "analysis-muhasabah-exact-one-v1": (
        "analysis_agent_outputs",
        "muhasabah_records",
    ),
    "layer2-muhasabah-exact-one-v1": (
        "layer2_role_outputs",
        "muhasabah_records",
    ),
    "grading-dispatch-and-consistency-v1": (
        "run_claim_grading_links",
        "methodology_result_claim_links",
        "generic_extraction_claim_links",
    ),
    "scoring-binary64-finite-v1": ("scoring_output_dimensions",),
    "scoring-binary64-negative-zero-v1": ("scoring_output_dimensions",),
    "approved-erasure-append-only-v1": _FUTURE_RELATION_TABLES,
    "native-root-exact-one-v1": (
        "run_output_objects",
        *_FUTURE_NATIVE_TABLES,
        "generic_extraction_claim_links",
        "generic_extraction_evidence_links",
    ),
    "identity-identifier-projection-v1": (
        "company_identity_outputs",
        "company_identity_identifiers",
    ),
    "scoring-dimension-projection-v1": (
        "scoring_outputs",
        "scoring_output_dimensions",
    ),
    "generic-claim-projection-v1": (
        "generic_extraction_claim_links",
        "run_output_objects",
    ),
    "generic-evidence-projection-v1": (
        "generic_extraction_evidence_links",
        "run_output_objects",
    ),
}

_REQUIRED_PARENT_RELATIONS: dict[str, tuple[str, ...]] = {
    "dashboard-root-equation-v1": (
        "methodology_truth_dashboard_rows",
        "methodology_truth_dashboard_root_links",
        "methodology_coverage_records",
        "methodology_questions",
        "methodology_extraction_tasks",
        "methodology_extraction_results",
        "methodology_result_factual_outputs",
        "methodology_result_evidence_links",
        "methodology_result_sanad_links",
        "methodology_result_defect_links",
        "deterministic_calculations",
        "run_output_objects",
    ),
    "graph-rag-namespace-v1": (),
    "rag-query-parent-coordinate-v1": ("rag_index_results",),
    "layer2-readiness-v1": (),
    "extract-rag-context-coordinate-v1": ("run_step_outputs",),
    "debate-muhasabah-exact-one-v1": (
        "debate_role_outputs",
        "muhasabah_records",
    ),
    "analysis-muhasabah-exact-one-v1": (
        "analysis_agent_outputs",
        "muhasabah_records",
    ),
    "layer2-muhasabah-exact-one-v1": (
        "layer2_role_outputs",
        "muhasabah_records",
    ),
    "grading-dispatch-and-consistency-v1": (
        "run_claim_grading_links",
        "methodology_result_claim_links",
        "generic_extraction_claim_links",
        "run_output_objects",
        "claims",
        "sanads",
        "methodology_result_sanad_links",
    ),
    "scoring-binary64-finite-v1": (),
    "scoring-binary64-negative-zero-v1": (),
    "approved-erasure-append-only-v1": ("erasure_requests",),
    "native-root-exact-one-v1": (
        "run_output_objects",
        *_FUTURE_NATIVE_TABLES,
        "generic_extraction_claim_links",
        "generic_extraction_evidence_links",
        "run_step_outputs",
    ),
    "identity-identifier-projection-v1": (
        "company_identity_outputs",
        "company_identity_identifiers",
    ),
    "scoring-dimension-projection-v1": (
        "scoring_outputs",
        "scoring_output_dimensions",
    ),
    "generic-claim-projection-v1": (
        "generic_extraction_claim_links",
        "run_output_objects",
        "run_step_outputs",
        "generic_extraction_outputs",
        "claims",
    ),
    "generic-evidence-projection-v1": (
        "generic_extraction_evidence_links",
        "run_output_objects",
        "run_step_outputs",
        "generic_extraction_outputs",
        "generic_extraction_claim_links",
        "evidence_items",
        "document_spans",
    ),
}

_KNOWN_CURRENT_RELATIONS = {
    "claims",
    "document_spans",
    "deterministic_calculations",
    "erasure_requests",
    "evidence_items",
    "methodology_coverage_records",
    "methodology_extraction_results",
    "methodology_extraction_tasks",
    "methodology_questions",
    "methodology_result_claim_links",
    "methodology_result_defect_links",
    "methodology_result_evidence_links",
    "methodology_result_factual_outputs",
    "methodology_result_sanad_links",
    "muhasabah_records",
    "run_output_objects",
    "run_step_outputs",
    "sanads",
}

_FUTURE_NATIVE_V1_FIELDS = (
    "authority_ordinal",
    "schema_id",
    "schema_version",
    "planned_table",
    "primary_key",
    "scope_columns",
    "immutable_fields",
    "check_tuple",
    "reference_fields",
    "unique_tuple",
    "child_relation_tuple",
    "rls_tuple",
    "erasure_ordinal",
    "subrole_field",
    "namespace_field",
    "schema_sha256",
)

_FUTURE_NATIVE_V2_FIELDS = (
    *_FUTURE_NATIVE_V1_FIELDS[:-1],
    "sql_authority_id_tuple",
    "schema_sha256",
)

_FUTURE_REFERENCE_V1_FIELDS = (
    "relation_ordinal",
    "table_name",
    "column_tuple",
    "consumer_parent_tuple",
    "reference_kind_tuple",
    "check_tuple",
    "foreign_key_tuple",
    "unique_tuple",
    "index_tuple",
    "rls_tuple",
    "erasure_ordinal",
    "uuid_namespace",
    "uuid_name_tuple_schema",
    "schema_sha256",
)

_FUTURE_REFERENCE_V2_FIELDS = (
    *_FUTURE_REFERENCE_V1_FIELDS[:-1],
    "sql_authority_id_tuple",
    "schema_sha256",
)

_FUTURE_CHILD_V1_FIELDS = (
    "relation_ordinal",
    "table_name",
    "column_tuple",
    "check_tuple",
    "foreign_key_tuple",
    "unique_tuple",
    "index_tuple",
    "rls_tuple",
    "erasure_ordinal",
    "uuid_namespace",
    "uuid_name_tuple_schema",
    "projection_guard",
    "append_only_trigger",
    "schema_sha256",
)

_FUTURE_CHILD_V2_FIELDS = (
    *_FUTURE_CHILD_V1_FIELDS[:-1],
    "sql_authority_id_tuple",
    "schema_sha256",
)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _artifact() -> dict[str, Any]:
    value = json.loads(_ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _row_hash(row: dict[str, Any]) -> str:
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    return hashlib.sha256(_ROW_HASH_DOMAIN + _canonical_json_bytes(payload)).hexdigest()


def _registry_hash(rows: object) -> str:
    return hashlib.sha256(_canonical_json_bytes([_REGISTRY_HASH_DOMAIN, rows])).hexdigest()


def _decoded_sql(row: dict[str, Any]) -> tuple[str, ...]:
    statements: list[str] = []
    encoded_tuple = row["body_sql_utf8_hex_tuple"]
    assert isinstance(encoded_tuple, list) and encoded_tuple
    for encoded in encoded_tuple:
        assert isinstance(encoded, str) and _HEX_RE.fullmatch(encoded)
        statement = bytes.fromhex(encoded).decode("utf-8")
        assert statement == statement.strip()
        assert "\r" not in statement
        assert "PROVIDERS18" not in statement
        assert "TO" + "DO" not in statement.upper()
        assert "<configured" not in statement
        statements.append(statement)
    return tuple(statements)


def _relation_accesses(
    statements: tuple[str, ...], *, expressions: bool = False
) -> tuple[set[str], set[str]]:
    """Return reads/writes through the generator's hermetic fail-closed scanner."""

    return _assertion_from_contract_error(
        lambda: _generator().scan_sql_relation_accesses(statements, expressions=expressions)
    )


def _ordered_target_tables(targets: list[list[str]]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(target[0] for target in targets))


def _installed_trigger_pairs(statements: tuple[str, ...]) -> set[tuple[str, str]]:
    return _assertion_from_contract_error(
        lambda: {
            parsed[0]
            for statement in statements
            if (parsed := _generator()._parse_trigger_binding(statement)) is not None
        }
    )


def _support_function_sets(
    statements: tuple[str, ...], *, authority_id: str | None = None
) -> tuple[set[str], set[str]]:
    def inspect() -> tuple[set[str], set[str]]:
        result = _generator().validate_sql_function_security(
            statements,
            authority_id=authority_id,
        )

        def render(identity: tuple[str, str, tuple[str, ...]]) -> str:
            schema, name, arguments = identity
            return f"{schema}.{name}({','.join(arguments)})"

        created = {render(identity) for identity in result["created"]}
        fully_bound = {render(identity) for identity in result["triggered"] & result["revoked"]}
        return created, fully_bound

    return _assertion_from_contract_error(inspect)


def _assert_no_unbound_or_dynamic_function_calls(
    statements: tuple[str, ...], *, authority_id: str | None = None
) -> None:
    _assertion_from_contract_error(
        lambda: _generator().validate_sql_function_security(statements, authority_id=authority_id)
    )


def _row_by_id(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    section = artifact.get("sql_authority_registry")
    assert isinstance(section, dict), "v3 omits sql_authority_registry"
    assert tuple(section) == ("rows", "expected_sha256")
    rows = section["rows"]
    assert isinstance(rows, list)
    return {row["authority_id"]: row for row in rows}


def _erasure_execution_row(artifact: dict[str, Any]) -> dict[str, Any]:
    section = artifact.get("erasure_execution_registry")
    assert isinstance(section, dict), "v3 omits erasure_execution_registry"
    assert tuple(section) == ("rows", "expected_sha256")
    rows = section["rows"]
    assert isinstance(rows, list) and len(rows) == 1
    row = rows[0]
    assert isinstance(row, dict)
    return row


def _decoded_execution_sql(row: dict[str, Any], field: str) -> tuple[str, ...]:
    encoded_tuple = row[field]
    assert isinstance(encoded_tuple, list) and encoded_tuple
    statements = tuple(bytes.fromhex(encoded).decode("utf-8") for encoded in encoded_tuple)
    assert all(statement == statement.strip() and "\r" not in statement for statement in statements)
    return statements


def _assert_contains_all(sql: str, fragments: tuple[str, ...]) -> None:
    normalized = "".join(sql.lower().split())
    missing = [
        fragment for fragment in fragments if "".join(fragment.lower().split()) not in normalized
    ]
    assert not missing, f"SQL authority omits reviewed fragments: {missing!r}"


def _assert_ordered_fragments(sql: str, fragments: tuple[str, ...]) -> None:
    """Require reviewed tuple members in order without selecting SQL formatting."""

    normalized = "".join(sql.lower().split())
    cursor = 0
    for fragment in fragments:
        needle = "".join(fragment.lower().split())
        position = normalized.find(needle, cursor)
        assert position >= 0, (
            f"SQL authority omits or reorders reviewed tuple fragment {fragment!r}"
        )
        cursor = position + len(needle)


def _collect_unskipped_task7_node_ids() -> set[str]:
    """Collect exact top-level nodes and exclude every statically skipped function."""

    relative_paths = (
        "tests/slice100_task7/test_sql_authority_guards_postgres_red.py",
        "tests/slice100_task7/test_sql_authority_float8_postgres_red.py",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-qq",
            "-m",
            "not skip and not skipif",
            *relative_paths,
        ],
        cwd=_REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    collected = {
        line.strip().replace("\\", "/")
        for line in completed.stdout.splitlines()
        if line.startswith("tests/") or line.startswith("tests\\")
    }

    dynamically_skipped: set[tuple[str, str]] = set()
    for relative_path in relative_paths:
        path = _REPO_ROOT / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls_pytest_skip = any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "pytest"
                and child.func.attr == "skip"
                for child in ast.walk(node)
            )
            if calls_pytest_skip:
                dynamically_skipped.add((relative_path, node.name))

    return {
        node_id
        for node_id in collected
        if not any(
            node_id.startswith(f"{path}::{function_name}")
            for path, function_name in dynamically_skipped
        )
    }


def test_relation_access_scanner_distinguishes_delete_target_from_parent_read() -> None:
    reads, writes = _relation_accesses(
        (
            "DELETE FROM public.child_rows USING public.erasure_requests "
            "WHERE child_rows.tenant_id=erasure_requests.tenant_id",
        )
    )
    assert reads == {"erasure_requests"}
    assert writes == {"child_rows"}


def test_relation_access_scanner_rejects_unapproved_table_function() -> None:
    with pytest.raises(
        AssertionError,
        match="TASK7_SQL_AUTHORITY_UNDECLARED_RELATION_FUNCTION",
    ):
        _relation_accesses(("SELECT * FROM public.hidden_mutable_reader()",))
    assert _relation_accesses(("SELECT * FROM jsonb_array_elements('[]'::jsonb)",)) == (
        set(),
        set(),
    )


def test_target_table_projection_allows_multiple_objects_per_relation() -> None:
    assert _ordered_target_tables(
        [["alpha", "trg_alpha_one"], ["alpha", "trg_alpha_two"], ["beta", "trg_beta"]]
    ) == ("alpha", "beta")


def test_support_functions_are_bound_inside_the_same_literal_body() -> None:
    statements = (
        "CREATE FUNCTION public.guard_row() RETURNS trigger LANGUAGE plpgsql "
        "SECURITY DEFINER SET search_path=pg_catalog,pg_temp "
        "AS $$BEGIN RETURN NEW; END$$",
        "CREATE CONSTRAINT TRIGGER guard_alpha AFTER INSERT ON public.alpha "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.guard_row()",
        "REVOKE ALL ON FUNCTION public.guard_row() FROM PUBLIC",
    )
    assert _installed_trigger_pairs(statements) == {("alpha", "guard_alpha")}
    assert _support_function_sets(statements) == (
        {"public.guard_row()"},
        {"public.guard_row()"},
    )
    assert _relation_accesses((statements[1],)) == (set(), set())
    _assert_no_unbound_or_dynamic_function_calls(statements)


def test_dynamic_or_external_public_function_call_is_rejected() -> None:
    with pytest.raises(AssertionError, match="TASK7_SQL_AUTHORITY_DYNAMIC_SQL_FORBIDDEN"):
        _assert_no_unbound_or_dynamic_function_calls(("EXECUTE dynamic_sql",))
    with pytest.raises(AssertionError, match="TASK7_SQL_AUTHORITY_UNBOUND_PUBLIC_FUNCTION"):
        _assert_no_unbound_or_dynamic_function_calls(("SELECT public.hidden_reader()",))
    with pytest.raises(
        AssertionError,
        match="TASK7_SQL_AUTHORITY_UNBOUND_UNQUALIFIED_FUNCTION",
    ):
        _assert_no_unbound_or_dynamic_function_calls(("SELECT hidden_reader()",))
    with pytest.raises(
        AssertionError,
        match="TASK7_SQL_AUTHORITY_UNAPPROVED_FUNCTION_SCHEMA",
    ):
        _assert_no_unbound_or_dynamic_function_calls(("SELECT evil.hidden_reader()",))


def test_sql_authority_registry_is_closed_literal_and_hash_bound() -> None:
    artifact = _artifact()
    tuple_specs = dict(artifact["tuple_schema_registry"]["rows"])
    assert tuple(tuple_specs.get("SqlAuthoritySpecV1", ())) == _SQL_AUTHORITY_FIELDS

    section = artifact.get("sql_authority_registry")
    assert isinstance(section, dict), "v3 omits sql_authority_registry"
    assert tuple(section) == ("rows", "expected_sha256")
    rows = section["rows"]
    assert isinstance(rows, list)
    assert [row.get("authority_ordinal") for row in rows] == list(range(len(rows)))
    assert tuple(row.get("authority_id") for row in rows) == _REQUIRED_AUTHORITY_IDS

    errors: list[str] = []
    for ordinal, row in enumerate(rows):
        path = f"sql_authority_registry.rows[{ordinal}]"
        if not isinstance(row, dict) or tuple(row) != _SQL_AUTHORITY_FIELDS:
            errors.append(f"{path} differs from SqlAuthoritySpecV1")
            continue
        if row["authority_version"] != "1":
            errors.append(f"{path}.authority_version must equal '1'")
        if row["authority_kind"] not in {
            "CHECK",
            "CONSTRAINT_TRIGGER",
            "BEFORE_ROW_TRIGGER",
            "APPEND_ONLY_TRIGGER",
        }:
            errors.append(f"{path}.authority_kind is not closed")
        targets = row["target_tuple"]
        if not isinstance(targets, list) or not targets:
            errors.append(f"{path}.target_tuple must be non-empty")
        elif any(
            not isinstance(target, list)
            or len(target) != 2
            or not all(isinstance(part, str) and part.strip() for part in target)
            for target in targets
        ):
            errors.append(f"{path}.target_tuple is malformed")
        elif len(targets) != len({tuple(target) for target in targets}):
            errors.append(f"{path}.target_tuple contains a duplicate")
        parents = row["declared_parent_relation_tuple"]
        if not isinstance(parents, list) or any(
            not isinstance(parent, str) or not parent.strip() for parent in parents
        ):
            errors.append(f"{path}.declared_parent_relation_tuple is malformed")
        elif len(parents) != len(set(parents)):
            errors.append(f"{path}.declared_parent_relation_tuple contains a duplicate")
        for case_field in (
            "legal_red_case_tuple",
            "illegal_red_case_tuple",
            "direct_sql_red_case_tuple",
        ):
            cases = row[case_field]
            if (
                not isinstance(cases, list)
                or not cases
                or any(not isinstance(case, str) or not case.strip() for case in cases)
            ):
                errors.append(f"{path}.{case_field} must name paired RED cases")
        expected_cases = _REQUIRED_RED_CASES[row["authority_id"]]
        actual_cases = (
            row["legal_red_case_tuple"],
            row["illegal_red_case_tuple"],
            row["direct_sql_red_case_tuple"],
        )
        if actual_cases != tuple([case] for case in expected_cases):
            errors.append(f"{path} RED-case IDs differ from the reviewed executable nodes")
        try:
            _decoded_sql(row)
        except (AssertionError, UnicodeDecodeError, ValueError) as exc:
            errors.append(f"{path}.body_sql_utf8_hex_tuple is not literal canonical SQL: {exc}")
        expected_hash = row["expected_sha256"]
        if not isinstance(expected_hash, str) or not _HASH_RE.fullmatch(expected_hash):
            errors.append(f"{path}.expected_sha256 is malformed")
        elif expected_hash != _row_hash(row):
            errors.append(f"{path}.expected_sha256 does not rederive")
    assert not errors, "invalid SQL authorities:\n" + "\n".join(errors)
    assert section["expected_sha256"] == _registry_hash(rows)


def test_sql_authority_target_and_parent_relation_closure_is_exact() -> None:
    rows = _row_by_id(_artifact())
    assert tuple(_REQUIRED_TARGET_TABLES) == _REQUIRED_AUTHORITY_IDS
    assert tuple(_REQUIRED_PARENT_RELATIONS) == _REQUIRED_AUTHORITY_IDS

    all_target_pairs = [
        tuple(target)
        for authority_id in _REQUIRED_AUTHORITY_IDS
        for target in rows[authority_id]["target_tuple"]
    ]
    assert len(all_target_pairs) == len(set(all_target_pairs)), (
        "a relation/object pair may be owned by exactly one SQL authority"
    )

    known_relations = set(_FUTURE_RELATION_TABLES) | _KNOWN_CURRENT_RELATIONS
    for authority_id in _REQUIRED_AUTHORITY_IDS:
        row = rows[authority_id]
        targets = row["target_tuple"]
        assert isinstance(targets, list)
        target_tables = _ordered_target_tables(targets)
        assert target_tables == _REQUIRED_TARGET_TABLES[authority_id], (
            f"{authority_id} target tables differ from the reviewed installation closure"
        )
        assert set(target_tables) <= known_relations, (
            f"{authority_id} names an unknown target relation"
        )

        parents = row["declared_parent_relation_tuple"]
        assert isinstance(parents, list)
        assert tuple(parents) == _REQUIRED_PARENT_RELATIONS[authority_id], (
            f"{authority_id} parent reads differ from the reviewed relation closure"
        )
        assert set(parents) <= known_relations, (
            f"{authority_id} names an unknown durable parent relation"
        )

        statements = _decoded_sql(row)
        _assert_no_unbound_or_dynamic_function_calls(statements, authority_id=authority_id)
        actual_reads, actual_writes = _relation_accesses(
            statements,
            expressions=row["authority_kind"] == "CHECK",
        )
        assert actual_reads == set(parents), (
            f"{authority_id} SQL reads must equal its declared durable-parent set; "
            f"undeclared={sorted(actual_reads - set(parents))!r}, "
            f"unused={sorted(set(parents) - actual_reads)!r}"
        )
        allowed_writes = set(target_tables)
        assert actual_writes <= allowed_writes, (
            f"{authority_id} SQL writes an undeclared target relation: "
            f"{sorted(actual_writes - allowed_writes)!r}"
        )

        installed_triggers = _installed_trigger_pairs(statements)
        declared_target_objects = {(target[0].lower(), target[1].lower()) for target in targets}
        if row["authority_kind"] != "CHECK":
            assert installed_triggers == declared_target_objects, (
                f"{authority_id} target_tuple must bind every and only its installed "
                "relation-local trigger objects"
            )

        created_functions, bound_functions = _support_function_sets(
            statements,
            authority_id=authority_id,
        )
        assert created_functions == bound_functions, (
            f"{authority_id} helper/SECURITY-DEFINER functions must be created and bound "
            "inside the same hash-bound authority body"
        )


def test_resolved_sql_bodies_encode_the_reviewed_semantics() -> None:
    rows = _row_by_id(_artifact())

    dashboard_sql = "\n".join(_decoded_sql(rows["dashboard-root-equation-v1"]))
    _assert_contains_all(
        dashboard_sql,
        (
            "row_disposition='GAP'",
            "row_disposition IN ('ANSWERED','RED_FLAG')",
            "root_projection_sha256",
            "methodology_truth_dashboard_root_links",
            "root_kind='RESULT'",
            "root_kind='EVIDENCE'",
            "root_kind='SANAD'",
            "root_kind='CALCULATION'",
            "root_kind='ADVERSE_CONTRADICTION'",
            "root_output_object_id",
            "run_output_objects",
            "run_output_object_kind='METHODOLOGY_EXTRACTION_RESULT'",
            "run_output_object_kind='METHODOLOGY_EVIDENCE_LINK'",
            "run_output_object_kind='METHODOLOGY_SANAD_LINK'",
            "run_output_object_kind='METHODOLOGY_CALCULATION'",
            "run_output_object_kind='METHODOLOGY_DEFECT_LINK'",
            "outcome='EVIDENCE_NOT_FOUND'",
            "result_payload<>'{}'::jsonb",
            "gap_finding<>'{}'::jsonb",
            "cardinality(r.searched_document_ids)>0",
            "cardinality(r.searched_span_ids)>0",
            "searched_scope_sha256",
            "provider_provenance_sha256",
            "required_calculations",
            "jsonb_typeof",
            "bool_or",
            "->>'required'",
            "?'required'",
            "::boolean",
            "coverage_record_id",
            "methodology_question_record_id",
            "producer_step_name='METHODOLOGY_DETERMINISTIC_CALCULATION'",
            "methodology_result_defect_links",
            "row_disposition='RED_FLAG'",
            "row_disposition='ANSWERED'",
        ),
    )
    compact_dashboard = "".join(dashboard_sql.lower().split())
    assert "result_output_object_id" not in compact_dashboard
    assert "evidence_output_object_id" not in compact_dashboard
    assert "sanad_output_object_id" not in compact_dashboard
    assert "calculation_output_object_id" not in compact_dashboard
    assert "methodology_questions" in compact_dashboard
    assert "methodology_coverage_records" in compact_dashboard
    assert re.search(
        r"jsonb_typeof\([^)]*->\s*'required'\)\s*=\s*'boolean'",
        dashboard_sql,
        flags=re.IGNORECASE,
    )
    assert re.search(
        r"jsonb_typeof\([^)]*required_calculations[^)]*\)\s*=\s*'array'",
        dashboard_sql,
        flags=re.IGNORECASE,
    )
    assert re.search(
        r"jsonb_typeof\([^)]*\)\s*=\s*'object'",
        dashboard_sql,
        flags=re.IGNORECASE,
    )
    assert "selectcoalesce(bool_or((item->>'required')::boolean),false)" in compact_dashboard
    assert dashboard_sql.upper().count("EXCEPT") >= 2, (
        "dashboard roots require bidirectional exact-set equality, not representative "
        "membership or count equality"
    )
    assert compact_dashboard.count("root_projection_sha256") >= 2, (
        "the relational equality proof must additionally bind the ordered root projection digest"
    )

    namespace_sql = "\n".join(_decoded_sql(rows["graph-rag-namespace-v1"]))
    _assert_contains_all(
        namespace_sql,
        (
            "namespace='internal_evidence' AND provider_id IS NULL AND fact_domain IS NULL",
            "namespace='external_context' AND provider_id IS NOT NULL AND fact_domain IS NOT NULL",
            "fact_domain IN ('commercial_diligence','financial_diligence')",
            *tuple(f"'{provider}'" for provider in _PROVIDERS18),
        ),
    )

    rag_sql = "\n".join(_decoded_sql(rows["rag-query-parent-coordinate-v1"]))
    assert rag_sql.upper().count("IS NOT DISTINCT FROM") >= 3
    _assert_contains_all(rag_sql, ("namespace", "provider_id", "fact_domain", "rag_index_results"))

    readiness_sql = "\n".join(_decoded_sql(rows["layer2-readiness-v1"]))
    _assert_contains_all(
        readiness_sql,
        (
            "status='READY'",
            "blocker_count=0",
            "deferred_count=0",
            "default_count=0",
            "required_projection_sha256=observed_projection_sha256",
        ),
    )

    extract_sql = "\n".join(_decoded_sql(rows["extract-rag-context-coordinate-v1"]))
    _assert_contains_all(extract_sql, ("step_name='EXTRACT'", "subrole='rag_context'"))

    for authority_id, step, kind in (
        ("debate-muhasabah-exact-one-v1", "DEBATE", "DEBATE_MUHASABAH"),
        ("analysis-muhasabah-exact-one-v1", "ANALYSIS", "ANALYSIS_MUHASABAH"),
        (
            "layer2-muhasabah-exact-one-v1",
            "LAYER2_IC_CHALLENGE",
            "LAYER2_MUHASABAH",
        ),
    ):
        sql = "\n".join(_decoded_sql(rows[authority_id]))
        _assert_contains_all(sql, (f"source_step='{step}'", f"run_output_object_kind='{kind}'"))
        assert "count(*)" in sql.lower()
        assert "<>1" in "".join(sql.split())

    grading_sql = "\n".join(_decoded_sql(rows["grading-dispatch-and-consistency-v1"]))
    _assert_contains_all(
        grading_sql,
        (
            "METHODOLOGY_CLAIM_MATERIALIZATION",
            "EXTRACT",
            "METHODOLOGY_CLAIM_LINK",
            "source_claim_output_object_id",
            "methodology_result_claim_links",
            "generic_extraction_claim_links",
            "claim_id",
            "sanad_id",
            "grade",
            "grading_sha256",
            "g.grade NOT IN ('A','B','C','D')",
        ),
    )
    compact_grading = "".join(grading_sql.lower().split())
    assert re.search(
        r"count\(distinct(?:row)?\([^)]*sanad_id[^)]*,[^)]*grade[^)]*,"
        r"[^)]*grading_sha256[^)]*\)\)<>1",
        compact_grading,
    ), (
        "physical source-root rows must converge on exactly one semantic "
        "(sanad_id,grade,grading_sha256) tuple per canonical claim/run"
    )

    finite_sql = "\n".join(_decoded_sql(rows["scoring-binary64-finite-v1"]))
    for column in ("score", "confidence"):
        _assert_contains_all(
            finite_sql,
            (
                f"{column}<>'NaN'::double precision",
                f"{column}<>'Infinity'::double precision",
                f"{column}<>'-Infinity'::double precision",
                f"{column}>=0::double precision",
                f"{column}<=1::double precision",
            ),
        )

    negative_zero_sql = "\n".join(_decoded_sql(rows["scoring-binary64-negative-zero-v1"]))
    _assert_contains_all(
        negative_zero_sql,
        (
            "IF NEW.score=0::double precision THEN NEW.score:=0::double precision",
            "IF NEW.confidence=0::double precision THEN NEW.confidence:=0::double precision",
            "BEFORE INSERT OR UPDATE",
        ),
    )

    append_sql = "\n".join(_decoded_sql(rows["approved-erasure-append-only-v1"]))
    _assert_contains_all(
        append_sql,
        (
            "TG_OP='INSERT'",
            "TG_OP='UPDATE'",
            "TG_OP='DELETE'",
            "current_setting(",
            "status IN ('REQUESTED','FAILED')",
            "SECURITY DEFINER",
            "SET search_path=pg_catalog,pg_temp",
            "session_user='idis_erasure_executor'",
            "current_user='idis_erasure_function_owner'",
        ),
    )
    assert "disable trigger" not in append_sql.lower()
    assert "set_config(" not in append_sql.lower()
    assert "idis_task7_execute_approved_erasure" not in append_sql
    assert "TO idis_app" not in append_sql
    assert "CREATE ROLE" not in append_sql.upper()
    assert "ALTER ROLE" not in append_sql.upper()
    assert "GRANT EXECUTE ON FUNCTION" not in append_sql.upper()

    exact_one_sql = "\n".join(_decoded_sql(rows["native-root-exact-one-v1"]))
    _assert_contains_all(exact_one_sql, ("run_output_objects", "output_class='NATIVE_ROW'"))
    assert "count(*)" in exact_one_sql.lower()
    assert "<>1" in "".join(exact_one_sql.split())

    projection_expectations = {
        "identity-identifier-projection-v1": (
            "identifier_projection_sha256",
            "company-identity-identifier-projection-v1",
            "tenant_id",
            "deal_id",
            "run_id",
            "identity_output_id",
            "run_output_object_id",
            "count",
            "identifier_kind",
            "identifier_hash",
            "identity_identifier_id",
            "verification_provenance_sha256",
        ),
        "scoring-dimension-projection-v1": (
            "dimension_projection_sha256",
            "scoring-dimension-projection-v1",
            "tenant_id",
            "deal_id",
            "run_id",
            "scoring_output_id",
            "run_output_object_id",
            "count",
            "dimension_ordinal",
            "dimension_key",
            "float8send(score)",
            "float8send(confidence)",
            "rationale_sha256",
            "confidence_justification_sha256",
            "support_projection_sha256",
            "muhasabah_projection_sha256",
            "scoring_dimension_id",
        ),
        "generic-claim-projection-v1": (
            "generic_extraction_claim_links",
            "run_output_objects",
            "METHODOLOGY_CLAIM_LINK",
            "claim_id",
        ),
        "generic-evidence-projection-v1": (
            "generic_extraction_evidence_links",
            "generic_extraction_claim_links",
            "METHODOLOGY_EVIDENCE_LINK",
            "claim_id",
            "evidence_id",
        ),
    }
    for authority_id, fragments in projection_expectations.items():
        sql = "\n".join(_decoded_sql(rows[authority_id]))
        _assert_contains_all(sql, fragments)
        assert "CREATE CONSTRAINT TRIGGER" in sql.upper()
        assert "DEFERRABLE INITIALLY DEFERRED" in sql.upper()

    identity_sql = "\n".join(_decoded_sql(rows["identity-identifier-projection-v1"]))
    _assert_ordered_fragments(
        identity_sql,
        (
            "company-identity-identifier-projection-v1",
            "tenant_id",
            "deal_id",
            "run_id",
            "identity_output_id",
            "run_output_object_id",
            "count",
            "identifier_kind",
            "identifier_hash",
            "identity_identifier_id",
            "verification_provenance_sha256",
        ),
    )
    _assert_contains_all(
        identity_sql,
        ("ORDER BY identifier_kind,identifier_hash,identity_identifier_id",),
    )

    scoring_sql = "\n".join(_decoded_sql(rows["scoring-dimension-projection-v1"]))
    _assert_ordered_fragments(
        scoring_sql,
        (
            "scoring-dimension-projection-v1",
            "tenant_id",
            "deal_id",
            "run_id",
            "scoring_output_id",
            "run_output_object_id",
            "count",
            "dimension_ordinal",
            "dimension_key",
            "float8send(score)",
            "float8send(confidence)",
            "rationale_sha256",
            "confidence_justification_sha256",
            "support_projection_sha256",
            "muhasabah_projection_sha256",
            "scoring_dimension_id",
        ),
    )
    _assert_contains_all(scoring_sql, ("ORDER BY dimension_ordinal", "'hex'"))


def test_future_relation_rows_reference_the_closed_sql_authority_registry() -> None:
    artifact = _artifact()
    tuple_specs = dict(artifact["tuple_schema_registry"]["rows"])
    assert tuple(tuple_specs.get("FutureNativeAuthoritySpecV1", ())) == _FUTURE_NATIVE_V1_FIELDS
    assert (
        tuple(tuple_specs.get("FutureReferenceAuthoritySpecV1", ())) == _FUTURE_REFERENCE_V1_FIELDS
    )
    assert tuple(tuple_specs.get("FutureChildAuthoritySpecV1", ())) == _FUTURE_CHILD_V1_FIELDS
    assert tuple(tuple_specs.get("FutureNativeAuthoritySpecV2", ())) == _FUTURE_NATIVE_V2_FIELDS
    assert (
        tuple(tuple_specs.get("FutureReferenceAuthoritySpecV2", ())) == _FUTURE_REFERENCE_V2_FIELDS
    )
    assert tuple(tuple_specs.get("FutureChildAuthoritySpecV2", ())) == _FUTURE_CHILD_V2_FIELDS

    authority_rows_by_id = _row_by_id(artifact)
    known_ids = set(authority_rows_by_id)
    native_rows = artifact["future_native_authority_registry"]["rows"]
    reference_rows = artifact["future_reference_authority_registry"]["rows"]
    child_rows = artifact["future_child_authority_registry"]["rows"]
    assert tuple(row["planned_table"] for row in native_rows) == _FUTURE_NATIVE_TABLES
    assert tuple(row["table_name"] for row in reference_rows) == _FUTURE_REFERENCE_TABLES
    assert tuple(row["table_name"] for row in child_rows) == _FUTURE_CHILD_TABLES

    relation_rows = (
        *((f"native:{row['planned_table']}", row) for row in native_rows),
        *((f"reference:{row['table_name']}", row) for row in reference_rows),
        *((f"child:{row['table_name']}", row) for row in child_rows),
    )
    relation_row_by_table = {
        relation_key.partition(":")[2]: row for relation_key, row in relation_rows
    }
    for authority_id, authority_row in authority_rows_by_id.items():
        if authority_row["authority_kind"] != "CHECK":
            continue
        targets = authority_row["target_tuple"]
        statements = _decoded_sql(authority_row)
        assert len(targets) == len(statements), (
            f"{authority_id} must supply one exact expression per CHECK target object"
        )
        for target, expression_sql in zip(targets, statements, strict=True):
            table_name, object_name = target
            relation_row = relation_row_by_table.get(table_name)
            assert relation_row is not None, (
                f"{authority_id} CHECK target {table_name}.{object_name} is not a future relation"
            )
            check_tuple = relation_row["check_tuple"]
            assert all(isinstance(item, list) and len(item) == 2 for item in check_tuple)
            check_by_name = dict(check_tuple)
            assert check_by_name.get(object_name) == expression_sql, (
                f"{authority_id} CHECK target name/expression must exactly equal the owning "
                "future relation's check_tuple"
            )

    actual_owners: dict[str, set[str]] = {authority_id: set() for authority_id in known_ids}
    for relation_key, row in relation_rows:
        authority_ids = row["sql_authority_id_tuple"]
        assert isinstance(authority_ids, list) and authority_ids
        assert len(authority_ids) == len(set(authority_ids))
        assert set(authority_ids) <= known_ids
        assert "approved-erasure-append-only-v1" in authority_ids
        table_name = relation_key.partition(":")[2]
        expected_authority_order = tuple(
            authority_id
            for authority_id in _REQUIRED_AUTHORITY_IDS
            if table_name in _REQUIRED_TARGET_TABLES[authority_id]
        )
        assert tuple(authority_ids) == expected_authority_order, (
            f"{relation_key} sql_authority_id_tuple must follow the exact authority-ordinal "
            "projection; set equality cannot mask order drift"
        )
        for authority_id in authority_ids:
            actual_owners[authority_id].add(relation_key)

    relation_key_by_table = {
        **{table: f"native:{table}" for table in _FUTURE_NATIVE_TABLES},
        **{table: f"reference:{table}" for table in _FUTURE_REFERENCE_TABLES},
        **{table: f"child:{table}" for table in _FUTURE_CHILD_TABLES},
    }
    expected_owners = {
        authority_id: {
            relation_key_by_table[table]
            for table in _REQUIRED_TARGET_TABLES[authority_id]
            if table in relation_key_by_table
        }
        for authority_id in _REQUIRED_AUTHORITY_IDS
    }
    assert set(expected_owners) == known_ids, "authority registry contains an unknown authority ID"
    assert actual_owners == expected_owners, (
        "SQL authorities must be bound by every and only their future target relations; "
        "an unknown or unreferenced target is forbidden"
    )


def test_every_sql_authority_red_case_id_resolves_to_an_executable_test_node() -> None:
    """Resolved authorities bind distinct, exact, collected, unskipped pytest nodes."""

    discovered = _collect_unskipped_task7_node_ids()
    overlaps = {
        authority_id: cases
        for authority_id, cases in _REQUIRED_RED_CASES.items()
        if len(cases) != len(set(cases))
    }
    assert not overlaps, f"legal/illegal/direct RED categories overlap: {overlaps!r}"

    missing = {
        authority_id: tuple(node_id for node_id in cases if node_id not in discovered)
        for authority_id, cases in _REQUIRED_RED_CASES.items()
        if authority_id not in _UNRESOLVED_AUTHORITY_IDS
        if any(node_id not in discovered for node_id in cases)
    }
    assert not missing, (
        "resolved SQL authority RED cases are not exact collected unskipped nodes:\n"
        + "\n".join(
            f"{authority_id}: {node_ids!r}" for authority_id, node_ids in sorted(missing.items())
        )
    )
    unresolved_node_ids = {
        node_id
        for authority_id in _UNRESOLVED_AUTHORITY_IDS
        for node_id in _REQUIRED_RED_CASES[authority_id]
    }
    assert unresolved_node_ids.isdisjoint(discovered), (
        "unresolved authority cases must not be satisfied by place" + "holder tests"
    )


def test_grading_cardinality_preserves_source_roots_and_one_semantic_tuple() -> None:
    """Physical provenance cardinality and canonical semantic consistency are both bound."""

    artifact = _artifact()
    grading_row = next(
        row
        for row in artifact["future_native_authority_registry"]["rows"]
        if row["planned_table"] == "run_claim_grading_links"
    )
    assert "unique_tuple" in grading_row, (
        "full-strict-v4 grading authority omits its normalized source-root unique tuple"
    )
    normalized_unique = "".join(
        json.dumps(grading_row["unique_tuple"], separators=(",", ":")).lower().split()
    )
    assert "source_claim_output_object_id" in normalized_unique
    assert "claim_id" not in normalized_unique, (
        "one physical row per claim would discard distinct source claim-link roots"
    )

    sql = "\n".join(_decoded_sql(_row_by_id(artifact)["grading-dispatch-and-consistency-v1"]))
    _assert_contains_all(
        sql,
        (
            "source_claim_output_object_id",
            "claim_id",
            "sanad_id",
            "grade",
            "grading_sha256",
            "g.grade NOT IN ('A','B','C','D')",
        ),
    )
    assert re.search(
        r"count\(distinct(?:row)?\([^)]*sanad_id[^)]*,[^)]*grade[^)]*,"
        r"[^)]*grading_sha256[^)]*\)\)<>1",
        "".join(sql.lower().split()),
    )


def test_erasure_parent_status_is_exactly_requested_or_failed_and_locked() -> None:
    """The fixed runner alone admits the two executable request states."""

    row = _erasure_execution_row(_artifact())
    sql = "\n".join(_decoded_execution_sql(row, "current_body_sql_utf8_hex_tuple"))
    _assert_contains_all(
        sql,
        (
            "status IN ('REQUESTED','FAILED')",
            "FOR UPDATE",
            "request_id",
            "tenant_id",
            "deal_id",
        ),
    )
    compact = "".join(sql.upper().split())
    assert "STATUSIN('REQUESTED','FAILED')" in compact
    assert "STATUSIN('REQUESTED','FAILED','COMPLETED')" not in compact


def test_dashboard_root_links_preserve_complete_lineage_and_govern_red_flag() -> None:
    """The dashboard owns an exact normalized root set, including same-coverage defects."""

    artifact = _artifact()
    native_row = next(
        row
        for row in artifact["future_native_authority_registry"]["rows"]
        if row["planned_table"] == "methodology_truth_dashboard_rows"
    )
    assert "immutable_fields" in native_row, (
        "full-strict-v4 dashboard authority omits its normalized immutable field tuple"
    )
    immutable = json.dumps(native_row["immutable_fields"], separators=(",", ":"))
    assert "root_projection_sha256" in immutable
    assert all(
        old not in immutable
        for old in (
            "result_output_object_id",
            "evidence_output_object_id",
            "sanad_output_object_id",
            "calculation_output_object_id",
        )
    )

    child = next(
        row
        for row in artifact["future_child_authority_registry"]["rows"]
        if row["table_name"] == "methodology_truth_dashboard_root_links"
    )
    assert child["relation_ordinal"] == 19
    assert child["erasure_ordinal"] == 65
    child_shape = json.dumps(child, separators=(",", ":"))
    _assert_contains_all(
        child_shape,
        (
            "dashboard_row_id",
            "root_output_object_id",
            "RESULT",
            "EVIDENCE",
            "SANAD",
            "CALCULATION",
            "ADVERSE_CONTRADICTION",
            "METHODOLOGY_EXTRACTION_RESULT",
            "METHODOLOGY_EVIDENCE_LINK",
            "METHODOLOGY_SANAD_LINK",
            "METHODOLOGY_CALCULATION",
            "METHODOLOGY_DEFECT_LINK",
        ),
    )

    sql = "\n".join(_decoded_sql(_row_by_id(artifact)["dashboard-root-equation-v1"]))
    _assert_contains_all(
        sql,
        (
            "methodology_truth_dashboard_root_links",
            "methodology_result_defect_links",
            "root_kind='ADVERSE_CONTRADICTION'",
            "run_output_object_kind='METHODOLOGY_DEFECT_LINK'",
            "row_disposition='RED_FLAG'",
            "row_disposition='ANSWERED'",
            "coverage_record_id",
        ),
    )
    assert sql.upper().count("EXCEPT") >= 2


def test_dashboard_calculation_root_is_scoped_to_frozen_coverage_question() -> None:
    """A calculation root proves the exact selected methodology row, not mere presence."""

    artifact = _artifact()
    current_row = next(
        row
        for row in artifact["current_ddl_registry"]["rows"]
        if row["table_name"] == "deterministic_calculations"
    )
    shape = json.dumps(current_row, separators=(",", ":"))
    _assert_contains_all(
        shape,
        (
            "coverage_record_id TEXT NULL",
            "methodology_question_record_id UUID NULL",
            "producer_step_name='METHODOLOGY_DETERMINISTIC_CALCULATION'",
            "producer_step_name='CALC'",
            "methodology_coverage_records",
            "coverage_record_id",
            "methodology_question_record_id",
        ),
    )

    dashboard_sql = "\n".join(_decoded_sql(_row_by_id(artifact)["dashboard-root-equation-v1"]))
    _assert_contains_all(
        dashboard_sql,
        (
            "deterministic_calculations",
            "coverage_record_id",
            "methodology_question_record_id",
            "producer_step_name='METHODOLOGY_DETERMINISTIC_CALCULATION'",
        ),
    )


def test_dashboard_guard_treats_an_empty_required_calculation_array_as_none_required() -> None:
    """The real CDD/FDD frozen rows legitimately encode no requirements as ``[]``."""

    sql = " ".join(
        "\n".join(_decoded_sql(_row_by_id(_artifact())["dashboard-root-equation-v1"])).split()
    )
    assert (
        "OR NOT EXISTS (SELECT 1 FROM "
        "jsonb_array_elements(dashboard_row.question_payload->'required_calculations'))" not in sql
    )
    assert "COALESCE(bool_or((item->>'required')::boolean),false)" in sql


def test_dashboard_guard_requires_the_complete_required_calculation_set_without_extras() -> None:
    """Existence of any one calculation cannot mask a missing second requirement."""

    sql = " ".join(
        "\n".join(_decoded_sql(_row_by_id(_artifact())["dashboard-root-equation-v1"])).split()
    )
    compact = "".join(sql.split())

    # Required item -> exact calculation anti-join.
    assert (
        "jsonb_array_elements(dashboard_row.question_payload->'required_calculations')item"
        "WHERE(item->>'required')::booleanANDNOTEXISTS(SELECT1FROM"
        "public.deterministic_calculationscWHERE"
    ) in compact
    assert "item->>'calc_type'=c.calc_type" in compact

    # Exact scoped calculation -> required item anti-join. This rejects extras and false-only types.
    assert (
        "EXISTS(SELECT1FROMpublic.deterministic_calculationscWHERE"
        "c.tenant_id=dashboard_row.tenant_idANDc.deal_id=dashboard_row.deal_idAND"
        "c.run_id=dashboard_row.run_idANDc.coverage_record_id="
        "dashboard_row.coverage_record_idANDc.methodology_question_record_id="
        "dashboard_row.methodology_question_record_idANDc.producer_step_name="
        "'METHODOLOGY_DETERMINISTIC_CALCULATION'ANDNOTEXISTS(SELECT1FROM"
        "jsonb_array_elements(dashboard_row.question_payload->'required_calculations')itemWHERE"
    ) in compact
    assert "(item->>'required')::booleanANDitem->>'calc_type'=c.calc_type" in compact

    # The rejected existential shape proved only that any one required type existed.
    assert (
        "calculation_requiredANDNOTEXISTS(SELECT1FROMpublic.deterministic_calculations"
        not in compact
    )


def test_erasure_runner_uses_only_the_dedicated_fixed_roles_and_signature() -> None:
    """The app credential and a forgeable GUC never become erasure authority."""

    row = _erasure_execution_row(_artifact())
    sql = "\n".join(
        (
            *_decoded_execution_sql(row, "current_body_sql_utf8_hex_tuple"),
            *_decoded_execution_sql(row, "current_acl_sql_utf8_hex_tuple"),
        )
    )
    _assert_contains_all(
        sql,
        (
            "CREATE FUNCTION public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)",
            "RETURNS jsonb",
            "SECURITY DEFINER",
            "SET search_path=pg_catalog,pg_temp",
            "ALTER FUNCTION public.idis_task7_execute_approved_erasure(uuid,uuid,uuid) "
            "OWNER TO idis_erasure_function_owner",
            "REVOKE ALL ON FUNCTION "
            "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid) FROM PUBLIC",
            "GRANT EXECUTE ON FUNCTION "
            "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid) "
            "TO idis_erasure_executor",
            "session_user='idis_erasure_executor'",
            "current_user='idis_erasure_function_owner'",
        ),
    )
    compact = "".join(sql.lower().split())
    assert "toidis_app" not in compact
    assert "torole_configured_app" not in compact
    assert "createrole" not in compact and "alterrole" not in compact
    assert sql.upper().count("GRANT EXECUTE ON FUNCTION") == 1


def test_identity_identifier_projection_preimage_is_exact_and_ordered() -> None:
    sql = "\n".join(_decoded_sql(_row_by_id(_artifact())["identity-identifier-projection-v1"]))
    _assert_ordered_fragments(
        sql,
        (
            "company-identity-identifier-projection-v1",
            "tenant_id",
            "deal_id",
            "run_id",
            "identity_output_id",
            "run_output_object_id",
            "count",
            "identifier_kind",
            "identifier_hash",
            "identity_identifier_id",
            "verification_provenance_sha256",
        ),
    )
    _assert_contains_all(sql, ("ORDER BY identifier_kind,identifier_hash,identity_identifier_id",))


def test_scoring_dimension_projection_preimage_is_exact_and_ordered() -> None:
    sql = "\n".join(_decoded_sql(_row_by_id(_artifact())["scoring-dimension-projection-v1"]))
    _assert_ordered_fragments(
        sql,
        (
            "scoring-dimension-projection-v1",
            "tenant_id",
            "deal_id",
            "run_id",
            "scoring_output_id",
            "run_output_object_id",
            "count",
            "dimension_ordinal",
            "dimension_key",
            "float8send(score)",
            "float8send(confidence)",
            "rationale_sha256",
            "confidence_justification_sha256",
            "support_projection_sha256",
            "muhasabah_projection_sha256",
            "scoring_dimension_id",
        ),
    )
    _assert_contains_all(sql, ("ORDER BY dimension_ordinal", "'hex'"))


def test_constraint_trigger_event_sets_are_all_exactly_insert_update_delete() -> None:
    """All eleven deferred guards observe every row mutation in reviewed order."""

    expected = ("INSERT", "UPDATE", "DELETE")
    generator = _generator()
    assert all(
        generator._REQUIRED_SQL_AUTHORITY_EVENTS[authority_id] == expected
        for authority_id in _DEFERRED_INSERT_UPDATE_DELETE_AUTHORITIES
    ), "TASK7_SQL_AUTHORITY_EVENT_SET_UNRESOLVED"

    rows = _row_by_id(_artifact())
    for authority_id in _DEFERRED_INSERT_UPDATE_DELETE_AUTHORITIES:
        sql = "\n".join(_decoded_sql(rows[authority_id]))
        assert "AFTER INSERT OR UPDATE OR DELETE" in sql.upper(), authority_id


@pytest.mark.parametrize("authority_id", _REQUIRED_AUTHORITY_IDS)
def test_corrupting_any_sql_authority_body_changes_its_hash(authority_id: str) -> None:
    artifact = _artifact()
    row = _row_by_id(artifact)[authority_id]
    original = _row_hash(row)
    mutated = json.loads(json.dumps(row))
    encoded = mutated["body_sql_utf8_hex_tuple"][0]
    mutated["body_sql_utf8_hex_tuple"][0] = encoded[:-2] + ("00" if encoded[-2:] != "00" else "01")
    assert _row_hash(mutated) != original

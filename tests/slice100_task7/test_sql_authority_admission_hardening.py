"""Meta-regressions for the hermetic Task 7 SQL-authority admission gate."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest
from tests.slice100_task7 import test_sql_authority_materialization_red as sql_gate

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v4_contract_fixture.py"
_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v4_preimage.py"
_V4_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v4_reviewed_preimage_v1.json"

_ERASURE_GUARD_AUTHORITY_ID = "approved-erasure-append-only-v1"
_ERASURE_AUTHORITY_ID = "approved-erasure-execution-v1"
_ERASURE_RUNNER_NAME = "idis_task7_execute_approved_erasure"
_ERASURE_RUNNER_IDENTITY = ("public", _ERASURE_RUNNER_NAME, ("uuid", "uuid", "uuid"))
_ERASURE_OWNER = "idis_erasure_function_owner"
_ERASURE_EXECUTOR = "idis_erasure_executor"
_REVIEWED_DEFERRED_EVENTS = ("INSERT", "UPDATE", "DELETE")
_ERASURE_RUNNER_ORDER = (
    "deliverable_manifest_references",
    "run_consumer_receipts",
    "run_expected_consumer_edges",
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
    "run_claim_grading_links",
    "methodology_result_defect_links",
    "methodology_result_evidence_links",
    "methodology_result_sanad_links",
    "generic_extraction_evidence_links",
    "generic_extraction_claim_links",
    "methodology_result_claim_links",
    "generic_extraction_outputs",
    "enrichment_normalized_facts",
    "enrichment_provider_outcomes",
    "rag_query_results",
    "rag_index_results",
    "external_intelligence_conflict_findings",
    "external_intelligence_conflict_executions",
    "external_intelligence_conflict_plans",
    "validated_evidence_package_references",
    "debate_role_outputs",
    "analysis_agent_outputs",
    "layer2_role_outputs",
    "methodology_truth_dashboard_root_links",
    "methodology_truth_dashboard_rows",
    "company_identity_identifiers",
    "company_identity_outputs",
    "graph_projection_results",
    "scoring_output_dimensions",
    "scoring_outputs",
    "layer2_readiness_outputs",
    "run_applicability_decisions",
    "run_step_outputs",
    "methodology_result_factual_outputs",
    "methodology_extraction_results",
    "calc_sanads",
    "deterministic_calculations",
    "layer2_ic_findings",
    "layer2_ic_challenges",
    "debate_sessions",
    "evidence_trust_findings",
    "validated_evidence_packages",
    "muhasabah_records",
    "deliverables",
    "vector_embeddings",
    "run_input_bindings",
    "data_room_package_files",
    "break_glass_grants",
    "deal_assignments",
    "defects",
    "human_gate_actions",
    "human_gates",
    "methodology_extraction_attempts",
    "run_steps",
    "methodology_extraction_task_spans",
    "methodology_extraction_tasks",
    "methodology_coverage_records",
    "run_output_objects",
    "document_spans",
    "document_parse_attempts",
    "documents",
    "document_artifacts",
    "data_room_packages",
    "source_lineage_objects",
    "overrides",
    "run_methodology_selections",
    "runs",
    "sanads",
    "evidence_items",
    "claims",
    "deals",
    "strict_preflight_proofs",
)
_ERASURE_INDIRECT_PARENTS = {
    "calc_sanads": ("deterministic_calculations", "calc_id"),
    "run_steps": ("runs", "run_id"),
    "human_gate_actions": ("human_gates", "gate_id"),
}
assert len(_ERASURE_RUNNER_ORDER) == len(set(_ERASURE_RUNNER_ORDER)) == 86


def _load_generator() -> ModuleType:
    name = "task7_contract_generator_sql_admission"
    spec = importlib.util.spec_from_file_location(name, _GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_materializer() -> ModuleType:
    name = "task7_full_strict_v4_materializer_sql_admission"
    spec = importlib.util.spec_from_file_location(name, _MATERIALIZER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _canonical_artifact_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _refresh_sql_hashes(generator: ModuleType, artifact: dict[str, object]) -> None:
    section = artifact["sql_authority_registry"]
    assert isinstance(section, dict)
    rows = section["rows"]
    assert isinstance(rows, list)
    for row in rows:
        payload = {key: value for key, value in row.items() if key != "expected_sha256"}
        row["expected_sha256"] = hashlib.sha256(
            generator._SQL_AUTHORITY_ROW_HASH_DOMAIN + generator._canonical_bytes(payload)
        ).hexdigest()
    section["expected_sha256"] = generator._h(["full-strict-v4-sql-authority-registry-v1", rows])


def _synthetic_validate_path_artifact(generator: ModuleType) -> dict[str, object]:
    """Inject a hermetic in-memory SQL registry into canonical v4 bytes."""

    artifact = copy.deepcopy(json.loads(_V4_ARTIFACT.read_text(encoding="utf-8")))

    future_rows: dict[str, dict[str, object]] = {}
    for section_name, table_key in (
        ("future_native_authority_registry", "planned_table"),
        ("future_reference_authority_registry", "table_name"),
        ("future_child_authority_registry", "table_name"),
    ):
        section = artifact[section_name]
        assert isinstance(section, dict)
        for row in section["rows"]:
            table = row[table_key]
            row["check_tuple"] = []
            row["sql_authority_id_tuple"] = [
                authority_id
                for authority_id in generator._REQUIRED_SQL_AUTHORITY_IDS
                if table in generator._REQUIRED_TARGET_TABLES[authority_id]
            ]
            future_rows[table] = row

    check_ids = {
        "graph-rag-namespace-v1",
        "layer2-readiness-v1",
        "scoring-binary64-finite-v1",
    }
    before_ids = {"scoring-binary64-negative-zero-v1"}
    append_ids = {_ERASURE_GUARD_AUTHORITY_ID}
    sql_rows: list[dict[str, object]] = []
    for ordinal, authority_id in enumerate(generator._REQUIRED_SQL_AUTHORITY_IDS):
        targets = [
            [table, f"task7_guard_{ordinal:02d}_{target_ordinal:02d}"]
            for target_ordinal, table in enumerate(generator._REQUIRED_TARGET_TABLES[authority_id])
        ]
        parents = list(generator._REQUIRED_PARENT_RELATIONS[authority_id])
        if authority_id in check_ids:
            authority_kind = "CHECK"
            statements = ["TRUE" for _ in targets]
            for target, expression in zip(targets, statements, strict=True):
                relation_row = future_rows[target[0]]
                relation_row["check_tuple"].append([target[1], expression])
        else:
            authority_kind = (
                "BEFORE_ROW_TRIGGER"
                if authority_id in before_ids
                else "APPEND_ONLY_TRIGGER"
                if authority_id in append_ids
                else "CONSTRAINT_TRIGGER"
            )
            helper = f"task7_guard_{ordinal:02d}"
            if parents:
                parent_read = f" FROM public.{parents[0]}" + "".join(
                    f" JOIN public.{parent} ON TRUE" for parent in parents[1:]
                )
                body = f"BEGIN PERFORM 1{parent_read}; RETURN NEW; END"
            else:
                body = "BEGIN RETURN NEW; END"
            if authority_kind == "CONSTRAINT_TRIGGER":
                events = "INSERT OR UPDATE OR DELETE"
                trigger_template = (
                    "CREATE CONSTRAINT TRIGGER {trigger} AFTER {events} ON public.{table} "
                    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                    "EXECUTE FUNCTION public.{helper}()"
                )
            elif authority_kind == "BEFORE_ROW_TRIGGER":
                trigger_template = (
                    "CREATE TRIGGER {trigger} BEFORE INSERT OR UPDATE ON public.{table} "
                    "FOR EACH ROW EXECUTE FUNCTION public.{helper}()"
                )
            else:
                trigger_template = (
                    "CREATE TRIGGER {trigger} BEFORE INSERT OR UPDATE OR DELETE "
                    "ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.{helper}()"
                )
            statements = [
                "CREATE FUNCTION public."
                f"{helper}() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER "
                f"SET search_path=pg_catalog,pg_temp AS $${body}$$",
                *(
                    trigger_template.format(
                        trigger=target[1],
                        table=target[0],
                        helper=helper,
                        events=events if authority_kind == "CONSTRAINT_TRIGGER" else "",
                    )
                    for target in targets
                ),
                f"REVOKE ALL ON FUNCTION public.{helper}() FROM PUBLIC",
            ]
        sql_rows.append(
            {
                "authority_ordinal": ordinal,
                "authority_id": authority_id,
                "authority_version": "1",
                "authority_kind": authority_kind,
                "target_tuple": targets,
                "declared_parent_relation_tuple": parents,
                "body_sql_utf8_hex_tuple": [
                    statement.encode("utf-8").hex() for statement in statements
                ],
                "legal_red_case_tuple": [generator._REQUIRED_SQL_RED_CASES[authority_id][0]],
                "illegal_red_case_tuple": [generator._REQUIRED_SQL_RED_CASES[authority_id][1]],
                "direct_sql_red_case_tuple": [generator._REQUIRED_SQL_RED_CASES[authority_id][2]],
                "expected_sha256": "",
            }
        )
    sql_section = artifact["sql_authority_registry"]
    assert isinstance(sql_section, dict)
    sql_section["rows"] = sql_rows
    _refresh_sql_hashes(generator, artifact)
    return artifact


def test_trigger_ddl_cannot_smuggle_a_second_top_level_delete() -> None:
    statement = (
        "CREATE CONSTRAINT TRIGGER guard_alpha AFTER INSERT ON public.alpha "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION public.guard_alpha(); "
        "DELETE FROM public.audit_events"
    )
    with pytest.raises(AssertionError, match="TASK7_SQL_AUTHORITY_MULTIPLE_STATEMENTS"):
        sql_gate._relation_accesses((statement,))


def test_data_modifying_cte_is_counted_as_a_durable_write() -> None:
    reads, writes = sql_gate._relation_accesses(
        ("WITH changed AS (DELETE FROM public.secret_rows RETURNING *) SELECT * FROM changed",)
    )
    assert reads == set()
    assert writes == {"secret_rows"}


@pytest.mark.parametrize(
    "statement",
    (
        "SELECT * FROM secret_rows",
        "SELECT * FROM evil.secret_rows",
        "SELECT * FROM secret_rows.hidden",
        "SELECT * FROM public.secret_rows.extra",
        "DELETE FROM secret_rows",
        "DELETE FROM evil.secret_rows",
        "INSERT INTO secret_rows(row_id) VALUES (1)",
        "UPDATE evil.secret_rows SET row_id=1",
        "SELECT * FROM public.allowed_rows, hidden_rows",
        'SELECT * FROM public.allowed_rows AS "where", hidden_rows',
        "DELETE FROM public.child_rows USING public.parent_rows, hidden_rows",
    ),
)
def test_every_durable_relation_requires_exact_public_qualification(statement: str) -> None:
    with pytest.raises(
        AssertionError,
        match="TASK7_SQL_AUTHORITY_RELATION_QUALIFICATION_INVALID",
    ):
        sql_gate._relation_accesses((statement,))


def test_public_relations_and_unqualified_cte_names_remain_admitted() -> None:
    assert sql_gate._relation_accesses(
        ("WITH selected AS (SELECT * FROM public.parent_rows) SELECT * FROM selected",)
    ) == ({"parent_rows"}, set())


def test_parenthesized_only_source_fails_closed() -> None:
    with pytest.raises(
        AssertionError,
        match="TASK7_SQL_AUTHORITY_UNDECLARED_RELATION_FUNCTION",
    ):
        sql_gate._relation_accesses(("SELECT * FROM ONLY (public.secret_rows)",))


@pytest.mark.parametrize(
    "statement",
    (
        "TABLE public.secret_rows",
        "MERGE INTO public.target USING public.source ON TRUE WHEN MATCHED THEN DELETE",
        "TRUNCATE public.target",
        "COPY public.target TO STDOUT",
        "LOCK TABLE public.target IN ACCESS EXCLUSIVE MODE",
        "ANALYZE public.target",
        "VACUUM public.target",
        "CLUSTER public.target",
        "REFRESH MATERIALIZED VIEW public.target",
        "MYSTERY public.target",
    ),
)
def test_unsupported_top_level_sql_forms_fail_closed(statement: str) -> None:
    with pytest.raises(AssertionError, match="TASK7_SQL_AUTHORITY_UNSUPPORTED_STATEMENT"):
        sql_gate._relation_accesses((statement,))


@pytest.mark.parametrize(
    "statement",
    (
        "SELECT 1 -- FROM public.fabricated_parent",
        "SELECT 1 FROM public.real_parent /* JOIN public.fabricated_parent ON TRUE */",
        "SELECT 1 FR/* hidden token */OM public.masked_parent",
    ),
)
def test_comments_cannot_create_or_mask_relation_reads(statement: str) -> None:
    with pytest.raises(AssertionError, match="TASK7_SQL_AUTHORITY_COMMENTS_FORBIDDEN"):
        sql_gate._relation_accesses((statement,))


@pytest.mark.parametrize(
    "statement",
    (
        'SELECT * FROM public."safe; DELETE FROM public.hidden"',
        'SELECT * FROM public."safe--FROM public.hidden"',
        'SELECT * FROM public."safe/*FROM public.hidden*/"',
    ),
)
def test_quoted_identifier_cannot_smuggle_separator_or_comment_tokens(statement: str) -> None:
    with pytest.raises(AssertionError, match="TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID"):
        sql_gate._relation_accesses((statement,))


@pytest.mark.parametrize(
    "statement",
    (
        "CREATE FUNCTION public.guard_row() RETURNS trigger LANGUAGE plpgsql "
        "SECURITY DEFINER SET search_path=pg_catalog,pg_temp "
        "AS $$BEGIN RETURN NEW; END$$ DELETE FROM public.hidden",
        "CREATE FUNCTION public.guard_row() RETURNS trigger LANGUAGE plpgsql "
        "SECURITY DEFINER SET search_path=pg_catalog,pg_temp "
        "AS $$BEGIN $nested$DELETE FROM public.hidden$nested$; RETURN NEW; END$$",
    ),
)
def test_dollar_quoted_body_cannot_hide_an_extra_executable_statement(statement: str) -> None:
    with pytest.raises(AssertionError):
        sql_gate._relation_accesses((statement,))


@pytest.mark.parametrize(
    "utility_statement",
    (
        "ANALYZE public.target",
        "VACUUM public.target",
        "CLUSTER public.target",
        "REFRESH MATERIALIZED VIEW public.target",
        "MYSTERY public.target",
    ),
)
def test_function_body_unknown_or_utility_statement_fails_closed(
    utility_statement: str,
) -> None:
    function = (
        "CREATE FUNCTION public.guard_row() RETURNS trigger LANGUAGE plpgsql "
        "SECURITY DEFINER SET search_path=pg_catalog,pg_temp "
        f"AS $$BEGIN {utility_statement}; RETURN NEW; END$$"
    )
    with pytest.raises(AssertionError, match="TASK7_SQL_AUTHORITY_UNSUPPORTED_STATEMENT"):
        sql_gate._relation_accesses((function,))


def _valid_bound_helper() -> tuple[str, ...]:
    return (
        "CREATE FUNCTION public.guard_row() RETURNS trigger LANGUAGE plpgsql "
        "SECURITY DEFINER SET search_path=pg_catalog,pg_temp "
        "AS $$BEGIN RETURN NEW; END$$",
        "CREATE CONSTRAINT TRIGGER guard_alpha AFTER INSERT ON public.alpha "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION public.guard_row()",
        "REVOKE ALL ON FUNCTION public.guard_row() FROM PUBLIC",
    )


def _reviewed_constraint_helper(body: str) -> tuple[str, ...]:
    return (
        "CREATE FUNCTION public.guard_row() RETURNS trigger LANGUAGE plpgsql "
        "SECURITY DEFINER SET search_path=pg_catalog,pg_temp "
        f"AS $${body}$$",
        *_valid_bound_helper()[1:],
    )


def _reviewed_before_row_helper(body: str) -> tuple[str, ...]:
    return (
        "CREATE FUNCTION public.guard_row() RETURNS trigger LANGUAGE plpgsql "
        "SECURITY DEFINER SET search_path=pg_catalog,pg_temp "
        f"AS $${body}$$",
        "CREATE TRIGGER guard_alpha BEFORE INSERT OR UPDATE ON public.alpha "
        "FOR EACH ROW EXECUTE FUNCTION public.guard_row()",
        "REVOKE ALL ON FUNCTION public.guard_row() FROM PUBLIC",
    )


def test_reviewed_null_safe_is_not_distinct_from_is_not_a_relation_clause() -> None:
    generator = _load_generator()
    assert generator.scan_sql_relation_accesses(
        ("SELECT 1 WHERE child.namespace IS NOT DISTINCT FROM parent.namespace",)
    ) == (set(), set())


def test_reviewed_raise_using_errcode_is_not_a_relation_clause() -> None:
    generator = _load_generator()
    statements = _reviewed_constraint_helper(
        "BEGIN IF TRUE THEN RAISE EXCEPTION 'TASK7_REVIEWED' USING ERRCODE='23514'; "
        "END IF; RETURN NEW; END"
    )
    generator.validate_sql_authority_statement_tuple(
        "extract-rag-context-coordinate-v1", "CONSTRAINT_TRIGGER", statements
    )


def test_reviewed_plpgsql_for_query_loop_is_admitted() -> None:
    generator = _load_generator()
    statements = _reviewed_constraint_helper(
        "DECLARE item RECORD; BEGIN FOR item IN SELECT source_id FROM public.source_rows "
        "LOOP PERFORM item.source_id; END LOOP; RETURN NEW; END"
    )
    analysis = generator.validate_sql_authority_statement_tuple(
        "identity-identifier-projection-v1", "CONSTRAINT_TRIGGER", statements
    )
    assert analysis["reads"] == {"source_rows"}


def test_reviewed_count_distinct_row_constructor_is_admitted() -> None:
    generator = _load_generator()
    statements = _reviewed_constraint_helper(
        "BEGIN IF EXISTS (SELECT 1 FROM public.grading_rows GROUP BY claim_id HAVING "
        "count(DISTINCT ROW(sanad_id,grade,grading_sha256))<>1) THEN "
        "RAISE EXCEPTION 'TASK7_REVIEWED' USING ERRCODE='23514'; END IF; RETURN NULL; END"
    )
    analysis = generator.validate_sql_authority_statement_tuple(
        "grading-dispatch-and-consistency-v1", "CONSTRAINT_TRIGGER", statements
    )
    assert analysis["reads"] == {"grading_rows"}


def test_reviewed_new_binary64_negative_zero_assignments_are_admitted() -> None:
    generator = _load_generator()
    statements = _reviewed_before_row_helper(
        "BEGIN IF NEW.score=0::double precision THEN NEW.score:=0::double precision; "
        "END IF; IF NEW.confidence=0::double precision THEN "
        "NEW.confidence:=0::double precision; END IF; RETURN NEW; END"
    )
    generator.validate_sql_authority_statement_tuple(
        "scoring-binary64-negative-zero-v1", "BEFORE_ROW_TRIGGER", statements
    )


def test_reviewed_boolean_parentheses_and_named_cte_column_list_are_admitted() -> None:
    generator = _load_generator()
    statements = _reviewed_constraint_helper(
        "DECLARE ready BOOLEAN:=false; BEGIN IF EXISTS (WITH "
        "registered_coordinates(output_kind,step_name) AS (VALUES ('OUTPUT','STEP')) "
        "SELECT 1 FROM registered_coordinates WHERE (output_kind='OUTPUT' AND "
        "(step_name='STEP' OR NOT (ready)))) THEN "
        "RAISE EXCEPTION 'TASK7_REVIEWED' USING ERRCODE='23514'; END IF; RETURN NEW; END"
    )
    generator.validate_sql_authority_statement_tuple(
        "native-root-exact-one-v1", "CONSTRAINT_TRIGGER", statements
    )


@pytest.mark.parametrize("quoted_name", ('"and"', '"as"', '"not"', '"row"', '"where"'))
def test_quoted_identifier_cannot_borrow_a_grammatical_keyword_exemption(
    quoted_name: str,
) -> None:
    generator = _load_generator()
    statements = _reviewed_constraint_helper(f"BEGIN PERFORM {quoted_name}(TRUE); RETURN NEW; END")
    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_SQL_AUTHORITY_UNBOUND_UNQUALIFIED_FUNCTION",
    ):
        generator.validate_sql_authority_statement_tuple(
            "native-root-exact-one-v1", "CONSTRAINT_TRIGGER", statements
        )


@pytest.mark.parametrize(
    "builder_name",
    (
        "_rag_parent_authority",
        "_extract_parent_authority",
        "_grading_authority",
        "_negative_zero_authority",
        "_identity_projection_authority",
        "_scoring_projection_authority",
        "_native_root_authority",
        "_dashboard_authority",
    ),
)
def test_current_reviewed_v4_authority_bodies_clear_the_closed_parser(
    builder_name: str,
) -> None:
    generator = _load_generator()
    materializer = _load_materializer()
    row = getattr(materializer, builder_name)()
    statements = tuple(
        bytes.fromhex(encoded).decode("utf-8") for encoded in row["body_sql_utf8_hex_tuple"]
    )
    generator.validate_sql_authority_statement_tuple(
        row["authority_id"], row["authority_kind"], statements
    )


def test_helper_binding_uses_exact_schema_signature_search_path_and_acl() -> None:
    sql_gate._assert_no_unbound_or_dynamic_function_calls(_valid_bound_helper())

    mutations = (
        (
            *_valid_bound_helper()[:1],
            _valid_bound_helper()[1].replace("public.guard_row()", "public.guard_row(1)"),
            _valid_bound_helper()[2],
        ),
        (
            _valid_bound_helper()[0],
            _valid_bound_helper()[1],
            _valid_bound_helper()[2].replace("public.guard_row()", "public.guard_row(text)"),
        ),
        (
            _valid_bound_helper()[0].replace("public.guard_row", "private.guard_row"),
            _valid_bound_helper()[1],
            _valid_bound_helper()[2],
        ),
        (
            _valid_bound_helper()[0].replace(" SET search_path=pg_catalog,pg_temp", ""),
            _valid_bound_helper()[1],
            _valid_bound_helper()[2],
        ),
        (
            _valid_bound_helper()[0].replace(
                "search_path=pg_catalog,pg_temp", "search_path=public,pg_catalog"
            ),
            _valid_bound_helper()[1],
            _valid_bound_helper()[2],
        ),
    )
    for statements in mutations:
        with pytest.raises(AssertionError, match="TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID"):
            sql_gate._assert_no_unbound_or_dynamic_function_calls(statements)


@pytest.mark.parametrize(
    "statements",
    (
        (
            _valid_bound_helper()[0].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"),
            *_valid_bound_helper()[1:],
        ),
        (*_valid_bound_helper(), "GRANT EXECUTE ON FUNCTION public.guard_row() TO PUBLIC"),
        (*_valid_bound_helper(), "GRANT EXECUTE ON FUNCTION public.guard_row() TO idis_app"),
        (*_valid_bound_helper(), _valid_bound_helper()[2]),
    ),
)
def test_helper_acl_rejects_replace_grants_and_duplicate_acl(statements: tuple[str, ...]) -> None:
    with pytest.raises(AssertionError, match="TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID"):
        sql_gate._assert_no_unbound_or_dynamic_function_calls(statements)


def _valid_erasure_guard() -> tuple[str, ...]:
    return (
        "CREATE FUNCTION public.task7_erasure_guard() RETURNS trigger LANGUAGE plpgsql "
        "SECURITY DEFINER SET search_path=pg_catalog,pg_temp "
        "AS $$BEGIN RETURN NEW; END$$",
        "CREATE TRIGGER task7_erasure_guard BEFORE INSERT OR UPDATE OR DELETE "
        "ON public.erased_rows FOR EACH ROW "
        "EXECUTE FUNCTION public.task7_erasure_guard()",
        "REVOKE ALL ON FUNCTION public.task7_erasure_guard() FROM PUBLIC",
    )


def _valid_erasure_runner(*, body: str = "BEGIN RETURN '{}'::jsonb; END") -> tuple[str, ...]:
    signature = f"public.{_ERASURE_RUNNER_NAME}(uuid,uuid,uuid)"
    return (
        f"CREATE FUNCTION {signature} RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER "
        "SET search_path=pg_catalog,pg_temp "
        f"AS $${body}$$",
        f"ALTER FUNCTION {signature} OWNER TO {_ERASURE_OWNER}",
        f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION {signature} TO {_ERASURE_EXECUTOR}",
    )


def _erasure_schedule_tables(artifact: dict[str, object]) -> tuple[str, ...]:
    section = artifact["erasure_materialized"]
    assert isinstance(section, dict)
    rows = section["rows"]
    assert isinstance(rows, list)
    return tuple(table for row in rows for table in row[2])


def _synthetic_erasure_delete(table: str) -> str:
    indirect = _ERASURE_INDIRECT_PARENTS.get(table)
    if indirect is None:
        return f"DELETE FROM public.{table} WHERE tenant_id=$2 AND deal_id=$3;"
    parent, join_key = indirect
    return (
        f"DELETE FROM public.{table} WHERE {table}.tenant_id=$2 "
        f"AND {table}.{join_key} IN (SELECT {parent}.{join_key} FROM public.{parent} "
        f"WHERE {parent}.tenant_id=$2 AND {parent}.deal_id=$3);"
    )


def _synthetic_erasure_runner_body(artifact: dict[str, object]) -> str:
    assert frozenset(_erasure_schedule_tables(artifact)) < frozenset(_ERASURE_RUNNER_ORDER)
    deletes = " ".join(_synthetic_erasure_delete(table) for table in _ERASURE_RUNNER_ORDER)
    return f"BEGIN {deletes} RETURN '{{}}'::jsonb; END"


def _valid_erasure_function_contract() -> tuple[str, ...]:
    return _valid_erasure_runner()


def test_erasure_runner_grammar_admits_only_the_dedicated_unbound_callable() -> None:
    """The execution registry admits one exact callable and no trigger helper."""

    generator = _load_generator()
    result = generator.validate_sql_function_security(
        _valid_erasure_function_contract(), authority_id=_ERASURE_AUTHORITY_ID
    )
    assert result["created"] == {_ERASURE_RUNNER_IDENTITY}
    assert result["triggered"] == set()
    assert result["revoked"] == {_ERASURE_RUNNER_IDENTITY}
    assert result["granted"] == {_ERASURE_RUNNER_IDENTITY}


def test_erasure_runner_admits_only_its_three_exact_positional_arguments() -> None:
    generator = _load_generator()
    runner = _valid_erasure_runner(body="BEGIN PERFORM $1,$2,$3; RETURN '{}'::jsonb; END")
    generator.validate_sql_function_security(runner, authority_id=_ERASURE_AUTHORITY_ID)

    for unreviewed in ("$0", "$4", "$01", "$request_id"):
        invalid = _valid_erasure_runner(
            body=f"BEGIN PERFORM {unreviewed}; RETURN '{{}}'::jsonb; END"
        )
        with pytest.raises(ValueError, match="TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID"):
            generator.validate_sql_function_security(invalid, authority_id=_ERASURE_AUTHORITY_ID)


def test_execution_registry_cannot_smuggle_a_trigger_helper() -> None:
    statements = (*_valid_erasure_guard(), *_valid_erasure_function_contract())
    with pytest.raises(ValueError, match="TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID"):
        _load_generator().validate_sql_function_security(
            statements, authority_id=_ERASURE_AUTHORITY_ID
        )


@pytest.mark.parametrize(
    ("statement_index", "old", "new", "error_code"),
    (
        (
            1,
            f"OWNER TO {_ERASURE_OWNER}",
            "OWNER TO idis_app",
            "TASK7_SQL_AUTHORITY_ERASURE_ACL_INVALID",
        ),
        (
            3,
            f"TO {_ERASURE_EXECUTOR}",
            "TO idis_app",
            "TASK7_SQL_AUTHORITY_ERASURE_ACL_INVALID",
        ),
        (
            3,
            f"TO {_ERASURE_EXECUTOR}",
            "TO PUBLIC",
            "TASK7_SQL_AUTHORITY_ERASURE_ACL_INVALID",
        ),
        (
            0,
            "(uuid,uuid,uuid)",
            "(uuid,uuid,text)",
            "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID",
        ),
        (
            0,
            _ERASURE_RUNNER_NAME,
            "idis_task7_execute_erasure",
            "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID",
        ),
        (
            0,
            "CREATE FUNCTION",
            "CREATE OR REPLACE FUNCTION",
            "TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID",
        ),
    ),
)
def test_erasure_runner_rejects_wrong_roles_overloads_and_replace(
    statement_index: int,
    old: str,
    new: str,
    error_code: str,
) -> None:
    statements = list(_valid_erasure_function_contract())
    statements[statement_index] = statements[statement_index].replace(old, new, 1)
    with pytest.raises(ValueError, match=error_code):
        _load_generator().validate_sql_function_security(
            tuple(statements), authority_id=_ERASURE_AUTHORITY_ID
        )


@pytest.mark.parametrize("removed_runner_index", (1, 2, 3))
def test_erasure_runner_requires_exact_owner_revoke_and_executor_grant(
    removed_runner_index: int,
) -> None:
    runner = list(_valid_erasure_runner())
    del runner[removed_runner_index]
    with pytest.raises(ValueError, match="TASK7_SQL_AUTHORITY_ERASURE_ACL_INVALID"):
        _load_generator().validate_sql_function_security(
            tuple(runner), authority_id=_ERASURE_AUTHORITY_ID
        )


def test_erasure_runner_cannot_be_bound_as_a_trigger_helper() -> None:
    statements = (
        *_valid_erasure_function_contract(),
        "CREATE TRIGGER runner_smuggling BEFORE INSERT OR UPDATE OR DELETE "
        "ON public.erased_rows FOR EACH ROW EXECUTE FUNCTION "
        f"public.{_ERASURE_RUNNER_NAME}()",
    )
    with pytest.raises(ValueError, match="TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID"):
        _load_generator().validate_sql_function_security(
            statements, authority_id=_ERASURE_AUTHORITY_ID
        )


def test_erasure_runner_rejects_an_additional_overload() -> None:
    overload = tuple(
        statement.replace("(uuid,uuid,uuid)", "(uuid,uuid,text)")
        for statement in _valid_erasure_runner()
    )
    with pytest.raises(ValueError, match="TASK7_SQL_AUTHORITY_ERASURE_RUNNER_INVALID"):
        _load_generator().validate_sql_function_security(
            (*_valid_erasure_function_contract(), *overload),
            authority_id=_ERASURE_AUTHORITY_ID,
        )


def test_ordinary_trigger_helper_cannot_receive_the_executor_grant() -> None:
    statements = (
        *_valid_bound_helper(),
        "GRANT EXECUTE ON FUNCTION public.guard_row() TO idis_erasure_executor",
    )
    with pytest.raises(AssertionError, match="TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID"):
        sql_gate._assert_no_unbound_or_dynamic_function_calls(
            statements, authority_id=_ERASURE_AUTHORITY_ID
        )


@pytest.mark.parametrize(
    "statement",
    (
        "CREATE ROLE idis_erasure_function_owner NOLOGIN NOSUPERUSER NOBYPASSRLS",
        "ALTER ROLE idis_erasure_function_owner LOGIN",
        "ALTER ROLE idis_erasure_executor SUPERUSER",
    ),
)
def test_contract_sql_cannot_create_or_mutate_cluster_roles(statement: str) -> None:
    with pytest.raises(ValueError, match="TASK7_SQL_AUTHORITY_UNSUPPORTED_STATEMENT"):
        _load_generator().validate_sql_authority_statement_tuple(
            _ERASURE_GUARD_AUTHORITY_ID,
            "APPEND_ONLY_TRIGGER",
            (statement,),
        )


def _assert_trigger_shape_rejected(authority_kind: str, trigger_statement: str) -> None:
    generator = _load_generator()
    statements = (_valid_bound_helper()[0], trigger_statement, _valid_bound_helper()[2])
    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
    ):
        generator.validate_sql_authority_statement_tuple(
            "native-root-exact-one-v1",
            authority_kind,
            statements,
        )


@pytest.mark.parametrize(
    ("authority_kind", "trigger_statement"),
    (
        (
            "CONSTRAINT_TRIGGER",
            _valid_bound_helper()[1].replace("ON public.alpha", "ON alpha"),
        ),
        (
            "CONSTRAINT_TRIGGER",
            _valid_bound_helper()[1].replace(
                "EXECUTE FUNCTION",
                "WHEN (public.unreviewed_predicate()) EXECUTE FUNCTION",
            ),
        ),
        (
            "CONSTRAINT_TRIGGER",
            _valid_bound_helper()[1].replace(
                "DEFERRABLE",
                "REFERENCING NEW TABLE AS new_rows DEFERRABLE",
            ),
        ),
        (
            "CONSTRAINT_TRIGGER",
            _valid_bound_helper()[1].replace("CREATE CONSTRAINT TRIGGER", "CREATE TRIGGER"),
        ),
        (
            "CONSTRAINT_TRIGGER",
            _valid_bound_helper()[1].replace(" AFTER ", " BEFORE "),
        ),
        (
            "CONSTRAINT_TRIGGER",
            _valid_bound_helper()[1].replace("DEFERRABLE INITIALLY DEFERRED ", ""),
        ),
        (
            "BEFORE_ROW_TRIGGER",
            "CREATE TRIGGER guard_alpha AFTER INSERT OR UPDATE ON public.alpha "
            "FOR EACH ROW EXECUTE FUNCTION public.guard_row()",
        ),
        (
            "BEFORE_ROW_TRIGGER",
            "CREATE TRIGGER guard_alpha BEFORE INSERT ON public.alpha "
            "FOR EACH ROW EXECUTE FUNCTION public.guard_row()",
        ),
        (
            "APPEND_ONLY_TRIGGER",
            "CREATE TRIGGER guard_alpha BEFORE UPDATE OR DELETE ON public.alpha "
            "FOR EACH ROW EXECUTE FUNCTION public.guard_row()",
        ),
    ),
)
def test_trigger_ddl_uses_the_closed_kind_specific_shape(
    authority_kind: str,
    trigger_statement: str,
) -> None:
    _assert_trigger_shape_rejected(authority_kind, trigger_statement)


@pytest.mark.parametrize(
    "function_call",
    (
        "pg_catalog.current_setting('idis.erasure_context',true)",
        "pg_catalog.set_config('idis.erasure_context','true',true)",
    ),
)
def test_session_state_functions_are_scoped_to_erasure_authority(function_call: str) -> None:
    statement = f"SELECT {function_call}"
    with pytest.raises(AssertionError, match="TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN"):
        sql_gate._assert_no_unbound_or_dynamic_function_calls(
            (statement,), authority_id="native-root-exact-one-v1"
        )
    sql_gate._assert_no_unbound_or_dynamic_function_calls(
        (statement,), authority_id=_ERASURE_AUTHORITY_ID
    )
    if "current_setting" in function_call:
        sql_gate._assert_no_unbound_or_dynamic_function_calls(
            (statement,), authority_id=_ERASURE_GUARD_AUTHORITY_ID
        )
    else:
        with pytest.raises(AssertionError, match="TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN"):
            sql_gate._assert_no_unbound_or_dynamic_function_calls(
                (statement,), authority_id=_ERASURE_GUARD_AUTHORITY_ID
            )


def test_erasure_runner_tenant_context_is_exactly_argument_two_and_transaction_local() -> None:
    """FORCE-RLS admission binds tenant context to the callable's tenant argument only."""

    valid = _valid_erasure_runner(
        body="BEGIN PERFORM pg_catalog.set_config('idis.tenant_id',$2::text,true); "
        "RETURN '{}'::jsonb; END"
    )
    _load_generator().validate_sql_function_security(valid, authority_id=_ERASURE_AUTHORITY_ID)

    for value in ("$1::text", "$3::text", "'literal-tenant'", "current_user"):
        invalid = _valid_erasure_runner(
            body=f"BEGIN PERFORM pg_catalog.set_config('idis.tenant_id',{value},true); "
            "RETURN '{}'::jsonb; END"
        )
        with pytest.raises(
            ValueError,
            match="TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
        ):
            _load_generator().validate_sql_function_security(
                invalid, authority_id=_ERASURE_AUTHORITY_ID
            )

    nonlocal_setting = _valid_erasure_runner(
        body="BEGIN PERFORM pg_catalog.set_config('idis.tenant_id',$2::text,false); "
        "RETURN '{}'::jsonb; END"
    )
    with pytest.raises(ValueError, match="TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN"):
        _load_generator().validate_sql_function_security(
            nonlocal_setting, authority_id=_ERASURE_AUTHORITY_ID
        )


def test_erasure_context_marker_is_the_exact_literal_true_value() -> None:
    valid = _valid_erasure_runner(
        body="BEGIN PERFORM pg_catalog.set_config('idis.erasure_context','true',true); "
        "RETURN '{}'::jsonb; END"
    )
    _load_generator().validate_sql_function_security(valid, authority_id=_ERASURE_AUTHORITY_ID)

    for value in ("'1'", "'yes'", "$1::text", "$2::text", "$3::text"):
        invalid = _valid_erasure_runner(
            body=f"BEGIN PERFORM pg_catalog.set_config('idis.erasure_context',{value},true); "
            "RETURN '{}'::jsonb; END"
        )
        with pytest.raises(
            ValueError,
            match="TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
        ):
            _load_generator().validate_sql_function_security(
                invalid, authority_id=_ERASURE_AUTHORITY_ID
            )


def test_execution_parser_admits_the_exact_lock_and_deferred_trigger_fence() -> None:
    body = (
        "BEGIN PERFORM pg_catalog.pg_advisory_xact_lock("
        "pg_catalog.hashtextextended("
        "'idis-task7-erasure-tenant-lock-v1:'||$2::text,0)); "
        "SET CONSTRAINTS ALL DEFERRED; "
        "SET CONSTRAINTS ALL IMMEDIATE; RETURN '{}'::jsonb; END"
    )
    _load_generator().validate_sql_function_security(
        _valid_erasure_runner(body=body), authority_id=_ERASURE_AUTHORITY_ID
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "pg_catalog.pg_advisory_lock",
        "pg_catalog.pg_try_advisory_xact_lock",
        "'unreviewed-domain:'||$2::text,0",
        "'idis-task7-erasure-tenant-lock-v1:'||$2::text,1",
        "'idis-task7-erasure-tenant-lock-v1:'||$1::text,0",
        "SET CONSTRAINTS ALL DEFERRED; RETURN '{}'::jsonb; END",
        "SET CONSTRAINTS ALL IMMEDIATE; RETURN '{}'::jsonb; END",
    ),
)
def test_execution_parser_rejects_lock_or_constraint_fence_drift(mutation: str) -> None:
    canonical = (
        "BEGIN PERFORM pg_catalog.pg_advisory_xact_lock("
        "pg_catalog.hashtextextended("
        "'idis-task7-erasure-tenant-lock-v1:'||$2::text,0)); "
        "SET CONSTRAINTS ALL DEFERRED; "
        "SET CONSTRAINTS ALL IMMEDIATE; RETURN '{}'::jsonb; END"
    )
    if mutation.startswith("pg_catalog"):
        changed = canonical.replace("pg_catalog.pg_advisory_xact_lock", mutation, 1)
    elif mutation.startswith("'"):
        changed = canonical.replace(
            "'idis-task7-erasure-tenant-lock-v1:'||$2::text,0",
            mutation,
            1,
        )
    else:
        changed = canonical.replace(
            "SET CONSTRAINTS ALL DEFERRED; SET CONSTRAINTS ALL IMMEDIATE; RETURN '{}'::jsonb; END",
            mutation,
            1,
        )
    with pytest.raises(ValueError, match="TASK7_SQL_AUTHORITY_ERASURE_"):
        _load_generator().validate_sql_function_security(
            _valid_erasure_runner(body=changed), authority_id=_ERASURE_AUTHORITY_ID
        )


def test_erasure_session_state_function_cannot_use_search_path_resolution() -> None:
    with pytest.raises(AssertionError, match="TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN"):
        sql_gate._assert_no_unbound_or_dynamic_function_calls(
            ("SELECT current_setting('idis.erasure_context',true)",),
            authority_id="approved-erasure-append-only-v1",
        )


@pytest.mark.parametrize(
    ("statement", "error_code"),
    (
        (
            "SELECT \"set_config\"('session_replication_role','replica',false)",
            "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
        ),
        (
            "SELECT \"set_config\"(E'session_replication_role',E'replica',false)",
            "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
        ),
        (
            "SELECT pg_catalog . \"set_config\"('session_replication_role','replica',false)",
            "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
        ),
        (
            "SELECT pg_catalog .set_config('session_replication_role','replica',false)",
            "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
        ),
        (
            "SELECT \"pg_catalog\" . \"set_config\"('session_replication_role','replica',false)",
            "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
        ),
        (
            "SELECT U&\"pg_catalog\".U&\"set_config\"('session_replication_role','replica',false)",
            "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
        ),
        (
            "SELECT pg_catalog.U&\"set_config\"('session_replication_role','replica',false)",
            "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
        ),
    ),
)
def test_quoted_or_spaced_identifier_cannot_hide_session_state_call(
    statement: str,
    error_code: str,
) -> None:
    """PostgreSQL identifier spelling must not bypass the closed call scanner."""

    with pytest.raises(AssertionError, match=error_code):
        sql_gate._assert_no_unbound_or_dynamic_function_calls(
            (statement,),
            authority_id=_ERASURE_AUTHORITY_ID,
        )


@pytest.mark.parametrize(
    "statement",
    (
        'SELECT "hidden_reader"()',
        'SELECT "public"."hidden_reader"()',
        'SELECT "evil"."hidden_reader"()',
        "SELECT evil . hidden_reader()",
        "SELECT coalesce(\"pg_read_file\"('/not-a-reviewed-path'),'')",
    ),
)
def test_quoted_or_spaced_identifier_cannot_hide_unreviewed_function(
    statement: str,
) -> None:
    """All callable identifiers remain visible under valid PostgreSQL spelling."""

    with pytest.raises(AssertionError, match="TASK7_SQL_AUTHORITY_"):
        sql_gate._assert_no_unbound_or_dynamic_function_calls((statement,))


@pytest.mark.parametrize(
    "statement",
    (
        "SELECT evil$reader()",
        "SELECT public.evil$reader()",
        "SELECT evil$schema.reader()",
        "SELECT α()",
        "SELECT public.α()",
        "SELECT α.reader()",
    ),
)
def test_noncanonical_unquoted_identifier_cannot_hide_function_call(
    statement: str,
) -> None:
    """Normalized authority SQL admits ASCII identifier bytes only."""

    with pytest.raises(AssertionError, match="TASK7_SQL_AUTHORITY_"):
        sql_gate._assert_no_unbound_or_dynamic_function_calls((statement,))


@pytest.mark.parametrize(
    "statement",
    (
        r"SELECT E'a\'b' || public.evil_reader() || E'c\'d'",
        r"SELECT E'a\'b' || pg_catalog.set_config("
        r"'session_replication_role','replica',false) || E'c\'d'",
    ),
)
def test_escape_string_literal_cannot_mask_unreviewed_function_call(
    statement: str,
) -> None:
    """The normalized SQL grammar rejects alternate string-literal lexers."""

    with pytest.raises(AssertionError, match="TASK7_SQL_AUTHORITY_"):
        sql_gate._assert_no_unbound_or_dynamic_function_calls((statement,))


@pytest.mark.parametrize(
    "statement",
    (
        "SELECT pg_catalog.current_setting('other.setting',true)",
        "SELECT pg_catalog.current_setting('idis.erasure_context',false)",
        "SELECT pg_catalog.set_config('other.setting','1',true)",
        "SELECT pg_catalog.set_config('session_replication_role','replica',true)",
        "SELECT pg_catalog.set_config('idis.erasure_context','1',false)",
        "SELECT pg_catalog.set_config('idis.erasure_context','1',false::boolean)",
        "SELECT pg_catalog.pg_backend_pid()",
        "SELECT pg_catalog.txid_current()",
    ),
)
def test_erasure_session_calls_use_only_the_exact_transaction_local_contract(
    statement: str,
) -> None:
    with pytest.raises(AssertionError, match="TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN"):
        sql_gate._assert_no_unbound_or_dynamic_function_calls(
            (statement,),
            authority_id="approved-erasure-append-only-v1",
        )


@pytest.mark.parametrize(
    "statement",
    (
        "SELECT current_user",
        "SELECT session_user",
        "SELECT current_role",
        "SELECT current_schema",
        "SELECT current_catalog",
        "SELECT user",
        "SELECT system_user",
        "SELECT current_date",
        "SELECT current_time",
        "SELECT current_timestamp",
        "SELECT localtime",
        "SELECT localtimestamp",
    ),
)
def test_session_pseudo_values_are_closed_and_authority_scoped(statement: str) -> None:
    with pytest.raises(AssertionError, match="TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN"):
        sql_gate._assert_no_unbound_or_dynamic_function_calls(
            (statement,), authority_id="native-root-exact-one-v1"
        )


def test_erasure_authority_admits_only_its_required_principal_pseudo_values() -> None:
    for authority_id in (_ERASURE_AUTHORITY_ID, _ERASURE_GUARD_AUTHORITY_ID):
        sql_gate._assert_no_unbound_or_dynamic_function_calls(
            (
                "SELECT current_user='idis_erasure_function_owner' "
                "AND session_user='idis_erasure_executor'",
            ),
            authority_id=authority_id,
        )
    with pytest.raises(AssertionError, match="TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN"):
        sql_gate._assert_no_unbound_or_dynamic_function_calls(
            ("SELECT current_timestamp",),
            authority_id="approved-erasure-append-only-v1",
        )


@pytest.mark.parametrize(
    "statement",
    (
        "SELECT current_user",
        "SELECT session_user",
        "SELECT current_user='idis_app'",
        "SELECT session_user=current_user",
        "SELECT current_user<>session_user",
        "SELECT current_user='idis_erasure_executor' AND "
        "session_user='idis_erasure_function_owner'",
        "SELECT current_user='idis_erasure_function_owner' OR session_user='idis_erasure_executor'",
    ),
)
def test_erasure_principal_pseudo_values_require_the_exact_pair_predicate(
    statement: str,
) -> None:
    with pytest.raises(AssertionError, match="TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN"):
        sql_gate._assert_no_unbound_or_dynamic_function_calls(
            (statement,),
            authority_id="approved-erasure-append-only-v1",
        )


def test_extension_digest_function_is_not_a_generic_guard_builtin() -> None:
    for statement in (
        "SELECT digest('payload','sha256')",
        "SELECT public.digest('payload','sha256')",
        "SELECT pg_catalog.digest('payload','sha256')",
    ):
        with pytest.raises(AssertionError):
            sql_gate._assert_no_unbound_or_dynamic_function_calls((statement,))


def test_core_sha256_is_admitted_only_with_exact_pg_catalog_qualification() -> None:
    sql_gate._assert_no_unbound_or_dynamic_function_calls(
        ("SELECT pg_catalog.sha256(pg_catalog.convert_to('payload','UTF8'))",)
    )
    for statement in (
        "SELECT sha256(pg_catalog.convert_to('payload','UTF8'))",
        "SELECT public.sha256(pg_catalog.convert_to('payload','UTF8'))",
    ):
        with pytest.raises(AssertionError):
            sql_gate._assert_no_unbound_or_dynamic_function_calls((statement,))


def test_generator_exposes_independent_closed_sql_registry_validation() -> None:
    generator = _load_generator()
    assert callable(generator.validate_sql_authority_registry)
    assert callable(generator.validate_sql_authority_statement_tuple)


def test_authority_kind_and_event_registry_is_literal_and_complete() -> None:
    generator = _load_generator()
    expected_kinds = {
        "dashboard-root-equation-v1": "CONSTRAINT_TRIGGER",
        "graph-rag-namespace-v1": "CHECK",
        "rag-query-parent-coordinate-v1": "CONSTRAINT_TRIGGER",
        "layer2-readiness-v1": "CHECK",
        "extract-rag-context-coordinate-v1": "CONSTRAINT_TRIGGER",
        "debate-muhasabah-exact-one-v1": "CONSTRAINT_TRIGGER",
        "analysis-muhasabah-exact-one-v1": "CONSTRAINT_TRIGGER",
        "layer2-muhasabah-exact-one-v1": "CONSTRAINT_TRIGGER",
        "grading-dispatch-and-consistency-v1": "CONSTRAINT_TRIGGER",
        "scoring-binary64-finite-v1": "CHECK",
        "scoring-binary64-negative-zero-v1": "BEFORE_ROW_TRIGGER",
        "approved-erasure-append-only-v1": "APPEND_ONLY_TRIGGER",
        "native-root-exact-one-v1": "CONSTRAINT_TRIGGER",
        "identity-identifier-projection-v1": "CONSTRAINT_TRIGGER",
        "scoring-dimension-projection-v1": "CONSTRAINT_TRIGGER",
        "generic-claim-projection-v1": "CONSTRAINT_TRIGGER",
        "generic-evidence-projection-v1": "CONSTRAINT_TRIGGER",
    }
    assert expected_kinds == generator._REQUIRED_SQL_AUTHORITY_KINDS
    expected_events = {
        "dashboard-root-equation-v1": ("INSERT", "UPDATE", "DELETE"),
        "graph-rag-namespace-v1": (),
        "rag-query-parent-coordinate-v1": ("INSERT", "UPDATE", "DELETE"),
        "layer2-readiness-v1": (),
        "extract-rag-context-coordinate-v1": ("INSERT", "UPDATE", "DELETE"),
        "debate-muhasabah-exact-one-v1": ("INSERT", "UPDATE", "DELETE"),
        "analysis-muhasabah-exact-one-v1": ("INSERT", "UPDATE", "DELETE"),
        "layer2-muhasabah-exact-one-v1": ("INSERT", "UPDATE", "DELETE"),
        "grading-dispatch-and-consistency-v1": ("INSERT", "UPDATE", "DELETE"),
        "scoring-binary64-finite-v1": (),
        "scoring-binary64-negative-zero-v1": ("INSERT", "UPDATE"),
        "approved-erasure-append-only-v1": ("INSERT", "UPDATE", "DELETE"),
        "native-root-exact-one-v1": ("INSERT", "UPDATE", "DELETE"),
        "identity-identifier-projection-v1": ("INSERT", "UPDATE", "DELETE"),
        "scoring-dimension-projection-v1": ("INSERT", "UPDATE", "DELETE"),
        "generic-claim-projection-v1": ("INSERT", "UPDATE", "DELETE"),
        "generic-evidence-projection-v1": ("INSERT", "UPDATE", "DELETE"),
    }
    assert expected_events == generator._REQUIRED_SQL_AUTHORITY_EVENTS


def _admit_reviewed_event_choices_for_parser_tests(generator: ModuleType) -> None:
    """Let parser/security tests isolate later failures while production remains RED."""

    generator._REQUIRED_SQL_AUTHORITY_EVENTS = {
        authority_id: (
            _REVIEWED_DEFERRED_EVENTS
            if authority_id in sql_gate._DEFERRED_INSERT_UPDATE_DELETE_AUTHORITIES
            else events
        )
        for authority_id, events in generator._REQUIRED_SQL_AUTHORITY_EVENTS.items()
    }


def test_registry_contains_no_unresolved_constraint_event_latches() -> None:
    generator = _load_generator()
    assert all(
        generator._REQUIRED_SQL_AUTHORITY_EVENTS[authority_id] == _REVIEWED_DEFERRED_EVENTS
        for authority_id in sql_gate._DEFERRED_INSERT_UPDATE_DELETE_AUTHORITIES
    ), "TASK7_SQL_AUTHORITY_EVENT_SET_UNRESOLVED"


def test_generator_independently_accepts_a_complete_in_memory_sql_registry() -> None:
    generator = _load_generator()
    artifact = _synthetic_validate_path_artifact(generator)
    _admit_reviewed_event_choices_for_parser_tests(generator)
    assert (
        generator.validate_sql_authority_registry(artifact)
        == artifact["sql_authority_registry"]["expected_sha256"]
    )


def _mutate_smuggled_statement(artifact: dict[str, object]) -> None:
    row = artifact["sql_authority_registry"]["rows"][0]
    statement = bytes.fromhex(row["body_sql_utf8_hex_tuple"][0]).decode("utf-8")
    row["body_sql_utf8_hex_tuple"][0] = (
        (statement + "; DELETE FROM public.audit_events").encode("utf-8").hex()
    )


def _mutate_unknown_parent(artifact: dict[str, object]) -> None:
    artifact["sql_authority_registry"]["rows"][0]["declared_parent_relation_tuple"].pop()


def _mutate_unknown_target(artifact: dict[str, object]) -> None:
    artifact["sql_authority_registry"]["rows"][0]["target_tuple"][0][0] = "shadow_rows"


def _mutate_open_spec(artifact: dict[str, object]) -> None:
    artifact["sql_authority_registry"]["rows"][0]["unreviewed_field"] = "smuggled"


def _mutate_noncanonical_hex(artifact: dict[str, object]) -> None:
    # Row 1 encodes TRUE using digit-only hex, so use a helper body with a-f bytes.
    row = artifact["sql_authority_registry"]["rows"][0]
    row["body_sql_utf8_hex_tuple"][0] = row["body_sql_utf8_hex_tuple"][0].upper()


def _mutate_acl_overload(artifact: dict[str, object]) -> None:
    row = artifact["sql_authority_registry"]["rows"][0]
    statement = bytes.fromhex(row["body_sql_utf8_hex_tuple"][-1]).decode("utf-8")
    row["body_sql_utf8_hex_tuple"][-1] = (
        statement.replace("() FROM PUBLIC", "(text) FROM PUBLIC").encode("utf-8").hex()
    )


def _mutate_wrong_authority_session_state(artifact: dict[str, object]) -> None:
    row = artifact["sql_authority_registry"]["rows"][0]
    statement = bytes.fromhex(row["body_sql_utf8_hex_tuple"][0]).decode("utf-8")
    statement = statement.replace(
        "BEGIN ",
        "BEGIN PERFORM pg_catalog.current_setting('idis.erasure_context',true); ",
        1,
    )
    row["body_sql_utf8_hex_tuple"][0] = statement.encode("utf-8").hex()


def _mutate_create_or_replace_helper(artifact: dict[str, object]) -> None:
    row = artifact["sql_authority_registry"]["rows"][0]
    statement = bytes.fromhex(row["body_sql_utf8_hex_tuple"][0]).decode("utf-8")
    row["body_sql_utf8_hex_tuple"][0] = (
        statement.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).encode("utf-8").hex()
    )


def _mutate_unreviewed_helper_grant(artifact: dict[str, object]) -> None:
    row = artifact["sql_authority_registry"]["rows"][0]
    row["body_sql_utf8_hex_tuple"].append(
        b"GRANT EXECUTE ON FUNCTION public.task7_guard_00() TO PUBLIC".hex()
    )


def _mutate_unqualified_parent_relation(artifact: dict[str, object]) -> None:
    row = artifact["sql_authority_registry"]["rows"][0]
    statement = bytes.fromhex(row["body_sql_utf8_hex_tuple"][0]).decode("utf-8")
    row["body_sql_utf8_hex_tuple"][0] = (
        statement.replace(
            "public.methodology_coverage_records",
            "methodology_coverage_records",
            1,
        )
        .encode("utf-8")
        .hex()
    )


def _mutate_trigger_predicate(artifact: dict[str, object]) -> None:
    row = artifact["sql_authority_registry"]["rows"][0]
    statement = bytes.fromhex(row["body_sql_utf8_hex_tuple"][1]).decode("utf-8")
    row["body_sql_utf8_hex_tuple"][1] = (
        statement.replace(
            "EXECUTE FUNCTION",
            "WHEN (public.unreviewed_predicate()) EXECUTE FUNCTION",
            1,
        )
        .encode("utf-8")
        .hex()
    )


def _mutate_nonlocal_erasure_session_state(artifact: dict[str, object]) -> None:
    row = artifact["sql_authority_registry"]["rows"][11]
    statement = bytes.fromhex(row["body_sql_utf8_hex_tuple"][0]).decode("utf-8")
    row["body_sql_utf8_hex_tuple"][0] = (
        statement.replace(
            "BEGIN ",
            "BEGIN PERFORM pg_catalog.set_config("
            "'session_replication_role','replica',false::boolean); ",
            1,
        )
        .encode("utf-8")
        .hex()
    )


def _mutate_quoted_session_state_in_helper(artifact: dict[str, object]) -> None:
    row = artifact["sql_authority_registry"]["rows"][11]
    statement = bytes.fromhex(row["body_sql_utf8_hex_tuple"][0]).decode("utf-8")
    row["body_sql_utf8_hex_tuple"][0] = (
        statement.replace(
            "BEGIN ",
            "BEGIN PERFORM \"set_config\"('session_replication_role','replica',false); ",
            1,
        )
        .encode("utf-8")
        .hex()
    )


def _mutate_noncanonical_public_function_in_helper(artifact: dict[str, object]) -> None:
    row = artifact["sql_authority_registry"]["rows"][0]
    statement = bytes.fromhex(row["body_sql_utf8_hex_tuple"][0]).decode("utf-8")
    row["body_sql_utf8_hex_tuple"][0] = (
        statement.replace("BEGIN ", "BEGIN PERFORM public.evil$reader(); ", 1).encode("utf-8").hex()
    )


def _mutate_escape_string_masked_function_in_helper(artifact: dict[str, object]) -> None:
    row = artifact["sql_authority_registry"]["rows"][0]
    statement = bytes.fromhex(row["body_sql_utf8_hex_tuple"][0]).decode("utf-8")
    row["body_sql_utf8_hex_tuple"][0] = (
        statement.replace(
            "BEGIN ",
            r"BEGIN PERFORM E'a\'b' || public.evil_reader() || E'c\'d'; ",
            1,
        )
        .encode("utf-8")
        .hex()
    )


def _mutate_negative_zero_authority_kind_and_events(artifact: dict[str, object]) -> None:
    row = artifact["sql_authority_registry"]["rows"][10]
    row["authority_kind"] = "APPEND_ONLY_TRIGGER"
    for index, encoded in enumerate(row["body_sql_utf8_hex_tuple"]):
        statement = bytes.fromhex(encoded).decode("utf-8")
        if "CREATE TRIGGER" in statement:
            row["body_sql_utf8_hex_tuple"][index] = (
                statement.replace(
                    "BEFORE INSERT OR UPDATE",
                    "BEFORE INSERT OR UPDATE OR DELETE",
                    1,
                )
                .encode("utf-8")
                .hex()
            )


def _mutate_constraint_trigger_event(artifact: dict[str, object]) -> None:
    row = artifact["sql_authority_registry"]["rows"][12]
    for index, encoded in enumerate(row["body_sql_utf8_hex_tuple"]):
        statement = bytes.fromhex(encoded).decode("utf-8")
        if "CREATE CONSTRAINT TRIGGER" in statement:
            row["body_sql_utf8_hex_tuple"][index] = (
                statement.replace(
                    "AFTER INSERT OR UPDATE OR DELETE",
                    "AFTER INSERT",
                    1,
                )
                .encode("utf-8")
                .hex()
            )


def _mutate_deferred_event_missing_delete(artifact: dict[str, object]) -> None:
    row = artifact["sql_authority_registry"]["rows"][0]
    for index, encoded in enumerate(row["body_sql_utf8_hex_tuple"]):
        statement = bytes.fromhex(encoded).decode("utf-8")
        if "CREATE CONSTRAINT TRIGGER" in statement:
            row["body_sql_utf8_hex_tuple"][index] = (
                statement.replace(
                    "AFTER INSERT OR UPDATE OR DELETE",
                    "AFTER INSERT OR UPDATE",
                    1,
                )
                .encode("utf-8")
                .hex()
            )


def _mutate_deferred_event_reordered(artifact: dict[str, object]) -> None:
    row = artifact["sql_authority_registry"]["rows"][0]
    for index, encoded in enumerate(row["body_sql_utf8_hex_tuple"]):
        statement = bytes.fromhex(encoded).decode("utf-8")
        if "CREATE CONSTRAINT TRIGGER" in statement:
            row["body_sql_utf8_hex_tuple"][index] = (
                statement.replace(
                    "AFTER INSERT OR UPDATE OR DELETE",
                    "AFTER UPDATE OR INSERT OR DELETE",
                    1,
                )
                .encode("utf-8")
                .hex()
            )


def _mutate_deferred_event_duplicate(artifact: dict[str, object]) -> None:
    row = artifact["sql_authority_registry"]["rows"][0]
    for index, encoded in enumerate(row["body_sql_utf8_hex_tuple"]):
        statement = bytes.fromhex(encoded).decode("utf-8")
        if "CREATE CONSTRAINT TRIGGER" in statement:
            row["body_sql_utf8_hex_tuple"][index] = (
                statement.replace(
                    "AFTER INSERT OR UPDATE OR DELETE",
                    "AFTER INSERT OR UPDATE OR DELETE OR INSERT",
                    1,
                )
                .encode("utf-8")
                .hex()
            )


def _mutate_reviewed_red_node_mapping(artifact: dict[str, object]) -> None:
    row = artifact["sql_authority_registry"]["rows"][0]
    row["legal_red_case_tuple"] = [
        "tests/slice100_task7/test_sql_authority_guards_postgres_red.py::"
        "test_graph_rag_namespace_accepts_legal_row"
    ]


def _mutate_erasure_runner_statement(
    artifact: dict[str, object],
    transform: Callable[[str], str],
) -> None:
    rows = artifact["sql_authority_registry"]["rows"]
    row = next(item for item in rows if item["authority_id"] == _ERASURE_AUTHORITY_ID)
    encoded_statements = row["body_sql_utf8_hex_tuple"]
    runner_index = next(
        index
        for index, encoded in enumerate(encoded_statements)
        if f"CREATE FUNCTION public.{_ERASURE_RUNNER_NAME}"
        in bytes.fromhex(encoded).decode("utf-8")
    )
    statement = bytes.fromhex(encoded_statements[runner_index]).decode("utf-8")
    mutated = transform(statement)
    assert isinstance(mutated, str) and mutated != statement
    encoded_statements[runner_index] = mutated.encode("utf-8").hex()


def _mutate_erasure_runner_missing_write(artifact: dict[str, object]) -> None:
    first = _ERASURE_RUNNER_ORDER[0]
    _mutate_erasure_runner_statement(
        artifact,
        lambda statement: statement.replace(_synthetic_erasure_delete(first), "", 1),
    )


def _mutate_erasure_runner_extra_write(artifact: dict[str, object]) -> None:
    first = _ERASURE_RUNNER_ORDER[0]
    _mutate_erasure_runner_statement(
        artifact,
        lambda statement: statement.replace(
            _synthetic_erasure_delete(first),
            "DELETE FROM public.erasure_requests WHERE tenant_id=$2 AND deal_id=$3; "
            + _synthetic_erasure_delete(first),
            1,
        ),
    )


def _mutate_erasure_runner_duplicate_write(artifact: dict[str, object]) -> None:
    first = _ERASURE_RUNNER_ORDER[0]
    _mutate_erasure_runner_statement(
        artifact,
        lambda statement: statement.replace(
            _synthetic_erasure_delete(first),
            f"{_synthetic_erasure_delete(first)} {_synthetic_erasure_delete(first)}",
            1,
        ),
    )


def _mutate_erasure_runner_reordered_writes(artifact: dict[str, object]) -> None:
    first, second = _ERASURE_RUNNER_ORDER[:2]

    def reorder(statement: str) -> str:
        pair = f"{_synthetic_erasure_delete(first)} {_synthetic_erasure_delete(second)}"
        reversed_pair = f"{_synthetic_erasure_delete(second)} {_synthetic_erasure_delete(first)}"
        return statement.replace(pair, reversed_pair, 1)

    _mutate_erasure_runner_statement(artifact, reorder)


def _mutate_erasure_runner_unscoped_write(artifact: dict[str, object]) -> None:
    first = _ERASURE_RUNNER_ORDER[0]
    _mutate_erasure_runner_statement(
        artifact,
        lambda statement: statement.replace(
            _synthetic_erasure_delete(first),
            f"DELETE FROM public.{first};",
            1,
        ),
    )


@pytest.mark.parametrize(
    ("mutator", "error_code"),
    (
        (_mutate_smuggled_statement, "TASK7_SQL_AUTHORITY_MULTIPLE_STATEMENTS"),
        (_mutate_unknown_parent, "TASK7_SQL_AUTHORITY_PARENT_CLOSURE_MISMATCH"),
        (_mutate_unknown_target, "TASK7_SQL_AUTHORITY_TARGET_CLOSURE_MISMATCH"),
        (_mutate_open_spec, "TASK7_SQL_AUTHORITY_SPEC_INVALID"),
        (_mutate_noncanonical_hex, "TASK7_CONTRACT_ARTIFACT_INVALID"),
        (_mutate_acl_overload, "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID"),
        (_mutate_reviewed_red_node_mapping, "TASK7_SQL_AUTHORITY_RED_CASE_INVALID"),
        (
            _mutate_wrong_authority_session_state,
            "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
        ),
        (
            _mutate_create_or_replace_helper,
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
        ),
        (
            _mutate_unreviewed_helper_grant,
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
        ),
        (
            _mutate_unqualified_parent_relation,
            "TASK7_SQL_AUTHORITY_RELATION_QUALIFICATION_INVALID",
        ),
        (
            _mutate_trigger_predicate,
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
        ),
        (
            _mutate_nonlocal_erasure_session_state,
            "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
        ),
        (
            _mutate_quoted_session_state_in_helper,
            "TASK7_SQL_AUTHORITY_SESSION_STATE_FORBIDDEN",
        ),
        (
            _mutate_noncanonical_public_function_in_helper,
            "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
        ),
        (
            _mutate_escape_string_masked_function_in_helper,
            "TASK7_SQL_AUTHORITY_CANONICAL_GRAMMAR_INVALID",
        ),
        (
            _mutate_negative_zero_authority_kind_and_events,
            "TASK7_SQL_AUTHORITY_SPEC_INVALID",
        ),
        (
            _mutate_constraint_trigger_event,
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
        ),
        (
            _mutate_deferred_event_missing_delete,
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
        ),
        (
            _mutate_deferred_event_reordered,
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
        ),
        (
            _mutate_deferred_event_duplicate,
            "TASK7_SQL_AUTHORITY_FUNCTION_BINDING_INVALID",
        ),
    ),
)
def test_sql_registry_validator_rejects_rehashed_bypasses(mutator: object, error_code: str) -> None:
    generator = _load_generator()
    artifact = _synthetic_validate_path_artifact(generator)
    mutator(artifact)
    _refresh_sql_hashes(generator, artifact)
    _admit_reviewed_event_choices_for_parser_tests(generator)
    with pytest.raises(generator.ContractArtifactError, match=error_code):
        # Exercise the complete v4 SQL-registry admission root without masking its code.
        generator.validate_sql_authority_registry(artifact)


def test_red_case_contracts_are_exact_distinct_collected_node_ids() -> None:
    for authority_id, categories in sql_gate._REQUIRED_RED_CASES.items():
        assert len(categories) == len(set(categories)), authority_id
        assert all(node_id.startswith("tests/slice100_task7/") for node_id in categories)
        assert all("::test_" in node_id for node_id in categories)

    collected = sql_gate._collect_unskipped_task7_node_ids()
    resolved = {
        node_id
        for authority_id, categories in sql_gate._REQUIRED_RED_CASES.items()
        if authority_id not in sql_gate._UNRESOLVED_AUTHORITY_IDS
        for node_id in categories
    }
    assert resolved <= collected

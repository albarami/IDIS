"""RED contracts for the approved full-strict-v24 PostgreSQL census.

These tests are hermetic.  They exercise admission, planning, role boundaries,
identity derivation, and receipt signing without opening a database connection
or reading the operator profile.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import inspect
import json
import os
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
SQL_RUNNER = REPO_ROOT / "scripts" / "run_task8_v24_sql_authority_census.py"
SIGNED_RUNNER = REPO_ROOT / "scripts" / "run_task8_v24_signed_census.py"
V22_ENGINE = REPO_ROOT / "scripts" / "run_task8_v22_sql_authority_census.py"
V24_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v24_reviewed_preimage_v1.json"
V24_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "full_strict_v24_contract_hash_v1.json"
)
POLICY_V2 = REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
CDD_TEMPLATE = REPO_ROOT / "src" / "idis" / "methodology" / "templates" / "commercial_dd_v1.json"
REVIEWER_MAP = (
    REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
GOLDEN = (
    REPO_ROOT
    / "tests"
    / "slice100_task8"
    / "fixtures"
    / "full_strict_v24_dashboard_identity_golden_v1.json"
)

ARTIFACT_SHA256 = "70834e33249a414e5dcc744cdc60934d9d2ed6c8ab3f09c64cf3edfa3f05e447"
FIXTURE_SHA256 = "444e4c573e7ba44185efe0b4c39339797b2df91a0fcbf7841129e131e406526e"
CONTRACT_SHA256 = "f05f17f98ea6a8d0356cef5939b7395fef294eb490a77cf8aeecf1ece25dd164"
GRAPH_SHA256 = "5c8b00d2e0930d4c1d5e5a4fbcc1db9ca74f9b28515e48da3a7021a835d84c5f"
CONSERVATION_SHA256 = "635035b2e2a9e88c4cf1a90a1fb4023197f0c0df0bb84e181db2daefddfb36de"
POLICY_BINDING_SHA256 = "6e6841d07b1891b6692ba210bc1be55aaee7f40a3bb6ffafdad56e5a21212bb3"
POLICY_ARTIFACT_SHA256 = "e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064"
POLICY_SHA256 = "2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9"
REVIEWER_MAP_SHA256 = "98cf84d4498f57465cd61501ea8bbbd93f7507d8628a61d7cbcf9cede459fe2a"
V22_ENGINE_SHA256 = "4f614ab2788cb0137430dba11c1a82ecb0fdd0e3693186fe6242bafd5b186268"
CDD_TEMPLATE_SHA256 = "57cfec98436514cae717e359fa872504a7b9e1115c7bc12a4561e0989c4281fc"
WORKBOOK_SHA256 = "a3b83addfff02815c9fd44c4a3dcb7f56ce39410fe182faff12acfb845bd4352"
WORKBOOK_BYTE_COUNT = 24_905
CURRENT_PIPELINE_VERSION = "full-strict-v21"
CURRENT_PIPELINE_HASH = "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"
SUCCESSOR_VERSION_ID = f"financial_dd:{WORKBOOK_SHA256}:fdd-formula-policy-v2:{POLICY_SHA256}"
EXPECTED_STEP_ROWS = (
    (
        4,
        "METHODOLOGY_COVERAGE_INIT",
        "5b157c8329a0a1c229f6ffb3ec2cfc0c0be1c6fb85f0dc0a669883aa1eb75dbc",
    ),
    (
        5,
        "METHODOLOGY_EXTRACTION_TASK_PLANNING",
        "07e0980ebfa04d3aae5042ec39b07200cacc87cc5f8ad40db6338faa56bb38af",
    ),
    (
        6,
        "METHODOLOGY_EXTRACTION_TASK_EXECUTION",
        "0550f5d7335852771e830ae9f376a9633813a24fd7afc677c32c45ee1446dded",
    ),
    (
        7,
        "METHODOLOGY_CLAIM_MATERIALIZATION",
        "63ae11aa758eea76bfa4931cc35ea6bd2b42a827d58b7f058f000db2e439792c",
    ),
    (
        8,
        "METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION",
        "7ca82a99ce4bf6782d76b17fdc8434bf98fc92e1c4626bcf2121004bed63f94e",
    ),
    (
        9,
        "METHODOLOGY_SANAD_CREATION_LINKING_GRADING",
        "ca4f4b8c77d274999f9593829ff76cbe0839e05f82082a37d8554cc1bff2dd52",
    ),
    (
        10,
        "METHODOLOGY_DETERMINISTIC_CALCULATION",
        "2db75b6695f26cfbf7f09ef0a440da557c15eb9b960be86d78f62f70412617ed",
    ),
    (
        11,
        "METHODOLOGY_TRUTH_DASHBOARD",
        "29a48e733fe07251ed0bb509311ddfbb36936876913c84ce149e79cd5336eea9",
    ),
)


def _load(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"missing approved implementation: {path.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _sql() -> ModuleType:
    return _load(SQL_RUNNER, "_task8_v24_sql_census_red")


def _signed() -> ModuleType:
    return _load(SIGNED_RUNNER, "_task8_v24_signed_census_red")


def _artifact() -> dict[str, Any]:
    return json.loads(V24_ARTIFACT.read_text(encoding="utf-8"))


def _policy() -> dict[str, Any]:
    return json.loads(POLICY_V2.read_text(encoding="utf-8"))


def test_v24_census_implementation_files_exist() -> None:
    assert SQL_RUNNER.is_file()
    assert SIGNED_RUNNER.is_file()


def test_v24_runner_is_native_and_preserves_historical_v22_engine() -> None:
    runner = _sql()
    assert hashlib.sha256(V22_ENGINE.read_bytes()).hexdigest() == V22_ENGINE_SHA256
    assert runner.PIPELINE_CONTRACT_VERSION == "full-strict-v24"

    source = SQL_RUNNER.read_text(encoding="utf-8")
    assert "full_strict_v22_reviewed_preimage" not in source
    assert "task8-v22-postgres-authority-census-receipt" not in source
    assert 'pipeline_contract_version": "full-strict-v22' not in source
    assert "run_task8_v22_sql_authority_census" not in source
    assert "exec(" not in source
    assert "importlib" not in source


def test_v24_independent_admission_precedes_profile_and_database_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed = _signed()
    order: list[str] = []
    real_preflight = signed.load_and_verify_preflight_assets

    def preflight() -> Any:
        order.append("independent-admission")
        return real_preflight()

    def requirement_binding() -> None:
        order.append("requirement-binding")

    def operator(_process_env: dict[str, str]) -> Any:
        order.append("profile")
        raise signed.Task8V24SignedCensusError("INTENDED_PROFILE_STOP")

    monkeypatch.setattr(signed, "load_and_verify_preflight_assets", preflight)
    monkeypatch.setattr(
        signed,
        "validate_v24_dashboard_requirement_assets",
        requirement_binding,
    )
    monkeypatch.setattr(signed, "load_operator_material", operator)
    with pytest.raises(signed.Task8V24SignedCensusError, match="INTENDED_PROFILE_STOP"):
        signed.execute_signed_census(
            output=REPO_ROOT / ".local_reports" / "slice100" / "task8" / "unused.json",
            process_env={"IDIS_STRICT_DOTENV_PATH": "C:/private/strict.env"},
        )
    assert order == ["independent-admission", "requirement-binding", "profile"]


def test_v24_preflight_binds_all_six_identities_and_exact_assets() -> None:
    signed = _signed()
    preflight = signed.load_and_verify_preflight_assets()
    assert preflight == {
        "schema_version": "task8-v24-postgres-census-preflight-v1",
        "pipeline_contract_version": "full-strict-v24",
        "activation_status": "INACTIVE",
        "artifact_sha256": ARTIFACT_SHA256,
        "fixture_sha256": FIXTURE_SHA256,
        "contract_sha256": CONTRACT_SHA256,
        "identity_graph_sha256": GRAPH_SHA256,
        "identity_graph_counts": {
            "node_count": 67,
            "edge_count": 35,
            "leaf_count": 834,
            "source_pointer_count": 67,
        },
        "artifact_counts": {
            "semantic_step_count": 28,
            "edge_rule_count": 48,
            "usage_route_count": 1756,
            "usage_extractor_count": 19,
            "provider_count": 18,
            "schema_count": 68,
            "current_ddl_table_count": 34,
            "installed_tenant_rls_table_count": 69,
            "erasure_current_relation_count": 48,
            "erasure_future_relation_count": 38,
            "erasure_complete_relation_count": 86,
            "task11_restart_cut_count": 27,
            "task11_undischarged_rule_count": 10,
        },
        "conservation_sha256": CONSERVATION_SHA256,
        "policy_artifact_sha256": POLICY_ARTIFACT_SHA256,
        "policy_sha256": POLICY_SHA256,
        "policy_reviewer_map_sha256": REVIEWER_MAP_SHA256,
        "policy_binding_sha256": POLICY_BINDING_SHA256,
    }


def test_v24_preflight_rejects_tamper_before_operator_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed = _signed()
    real_read = signed._read_exact_regular_file

    def tampered(path: Path, *, max_bytes: int, code: str) -> bytes:
        raw = real_read(path, max_bytes=max_bytes, code=code)
        return raw + b" " if path == V24_FIXTURE else raw

    monkeypatch.setattr(signed, "_read_exact_regular_file", tampered)
    with pytest.raises(
        signed.Task8V24SignedCensusError,
        match="TASK8_V24_SIGNED_CENSUS_PREFLIGHT_INVALID",
    ):
        signed.load_and_verify_preflight_assets()


def test_v24_manifest_is_exact_100_row_21_cdd_79_fdd_and_role_closed() -> None:
    runner = _sql()
    artifact = _artifact()
    manifest = runner.build_census_case_manifest(artifact)
    runner.validate_census_case_manifest(manifest, artifact)

    assert manifest["schema_version"] == "task8-v24-postgres-authority-census-v1"
    assert manifest["dashboard_coverage_row_count"] == 100
    assert manifest["dashboard_commercial_coverage_row_count"] == 21
    assert manifest["dashboard_financial_coverage_row_count"] == 79
    assert manifest["behavior_role_contract"] == {
        "guard_default": "application",
        "erasure_function": "erasure_executor",
        "admin_behavior_case_count": 0,
        "catalog_observer": "application",
    }
    cases = runner.census_case_rows(manifest)
    assert len(cases) == 25
    assert {case["execution_role"] for case in cases} == {
        "application",
        "erasure_executor",
    }
    assert all(case["execution_role"] != "admin" for case in cases)


def test_v24_dashboard_identity_matches_handwritten_golden() -> None:
    runner = _sql()
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert runner.derive_v24_dashboard_identity(**golden["inputs"]) == golden["expected"]


def _operator_workbook_bytes() -> bytes:
    configured = os.environ.get("IDIS_FDD_METHODOLOGY_PATH", "").strip()
    if not configured:
        pytest.skip("operator-backed workbook proof is not enabled")
    path = Path(configured)
    raw = path.read_bytes()
    if len(raw) != WORKBOOK_BYTE_COUNT or hashlib.sha256(raw).hexdigest() != WORKBOOK_SHA256:
        pytest.fail("TASK8_V24_CENSUS_WORKBOOK_IDENTITY_INVALID", pytrace=False)
    return raw


def _safe_registry_identity(registry: Any) -> dict[str, Any]:
    from idis.persistence.repositories.methodologies import (
        methodology_question_payload_hashes,
        methodology_source_trace_digest,
        methodology_version_hash,
    )

    question_hashes = methodology_question_payload_hashes(registry)
    return {
        "version_id": registry.current_version.methodology_version_id,
        "question_count": len(question_hashes),
        "ordered_question_hashes_sha256": hashlib.sha256(
            json.dumps(
                list(question_hashes),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "source_trace_sha256": methodology_source_trace_digest(registry),
        "version_sha256": methodology_version_hash(registry),
    }


def test_v24_seed_plan_has_one_closed_production_constructor_path() -> None:
    runner = _sql()
    source = inspect.getsource(runner.build_v24_dashboard_seed_plan)
    assert "workbook_raw" in inspect.signature(runner.build_v24_dashboard_seed_plan).parameters
    assert "import_cdd_template" in source
    assert "import_fdd_formula_policy_v2_registry" in source
    assert "policy_artifact=policy_raw" in source
    assert "build_methodology_question_seed_rows" not in source
    assert "_derive_methodology_question_seed" not in source

    execution_source = inspect.getsource(runner.run_disposable_postgres_census)
    assert "workbook_raw" in inspect.signature(runner.run_disposable_postgres_census).parameters
    assert "workbook_raw=workbook_raw" in execution_source


def test_v24_policy_v2_plan_matches_named_constructor_by_safe_identities() -> None:
    from idis.methodology.importers.fdd_formula_policy_v2 import (
        import_fdd_formula_policy_v2_registry,
    )

    workbook_raw = _operator_workbook_bytes()
    runner = _sql()
    plan = runner.build_v24_dashboard_seed_plan(
        _artifact(),
        POLICY_V2.read_bytes(),
        workbook_raw,
        tenant_id="11111111-1111-4111-8111-111111111111",
        deal_id="22222222-2222-4222-8222-222222222222",
        run_id="33333333-3333-4333-8333-333333333333",
    )
    expected = _safe_registry_identity(
        import_fdd_formula_policy_v2_registry(
            workbook_raw,
            policy_artifact=POLICY_V2.read_bytes(),
        )
    )
    if plan.get("financial_registry_identity") != expected:
        pytest.fail("TASK8_V24_CENSUS_FDD_REGISTRY_IDENTITY_DRIFT", pytrace=False)
    assert expected["version_id"] == SUCCESSOR_VERSION_ID
    assert expected["question_count"] == 79


def test_v24_task5_repository_and_plan_are_the_only_database_seed_authority() -> None:
    runner = _sql()
    source = SQL_RUNNER.read_text(encoding="utf-8")
    persistence_source = inspect.getsource(runner._persist_task5_seed_plan)
    assert "PostgresMethodologiesRepository" in persistence_source
    assert ".import_registry(" in persistence_source
    assert ".freeze_version(" in persistence_source
    assert ".create_run_selection(" in persistence_source
    assert ".insert_coverage_records(" in persistence_source
    assert "dashboard_seed_plan" in persistence_source
    assert "INSERT INTO public.methodology_questions" not in source
    assert "INSERT INTO methodology_questions" not in source
    assert "mc_task8_" not in source

    execution_source = inspect.getsource(runner._run_database_execution)
    validation_source = inspect.getsource(runner.validate_census_receipt)
    for field in (
        "dashboard_seed_plan_sha256",
        "dashboard_seed_db_projection_sha256",
        "seed_fk_closure_sha256",
        "seed_native_helper_equality_sha256",
    ):
        assert field in execution_source
        assert field in validation_source


def test_v24_current_run_and_steps_use_exact_0038_v21_coordinates() -> None:
    runner = _sql()
    assert runner.CURRENT_RUNTIME_PIPELINE_CONTRACT_VERSION == CURRENT_PIPELINE_VERSION
    assert runner.CURRENT_RUNTIME_PIPELINE_CONTRACT_HASH == CURRENT_PIPELINE_HASH
    steps = runner.build_current_run_step_plan(
        tenant_id="11111111-1111-4111-8111-111111111111",
        deal_id="22222222-2222-4222-8222-222222222222",
        run_id="33333333-3333-4333-8333-333333333333",
    )
    assert [
        (row["step_order"], row["step_name"], row["semantic_contract_hash"]) for row in steps
    ] == list(EXPECTED_STEP_ROWS)
    assert all(row["semantic_contract_version"] == "2" for row in steps)
    assert len({row["step_id"] for row in steps}) == len(steps)
    assert all(row["pipeline_contract_version"] == CURRENT_PIPELINE_VERSION for row in steps)
    assert all(row["pipeline_contract_hash"] == CURRENT_PIPELINE_HASH for row in steps)


def test_v24_post_0038_source_freeze_and_native_output_path_are_guarded() -> None:
    runner = _sql()
    source_stage = inspect.getsource(runner._stage_and_freeze_source_graph)
    assert "freeze_source_package" in source_stage
    assert "urls.application" in source_stage
    assert "write_origin" in source_stage
    assert "RUNTIME" in source_stage
    assert "SET CONSTRAINTS ALL IMMEDIATE" in source_stage

    native_source = inspect.getsource(runner._persist_seed_native_root)
    assert "INSERT INTO public.run_output_objects" in native_source
    assert "UPDATE public.run_output_objects" not in native_source
    assert "INSERT INTO public.run_step_outputs" in native_source
    assert "idis_task7_native_output_identity_v1" in native_source
    assert "idis_task7_native_output_hash_v1" in native_source

    output_manifest = runner.build_seed_native_output_manifest()
    assert set(output_manifest["output_kind_tuple"]) == {
        "METHODOLOGY_COVERAGE_IDENTITY",
        "METHODOLOGY_EXTRACTION_TASK",
        "METHODOLOGY_EXTRACTION_RESULT",
        "METHODOLOGY_FACTUAL_OUTPUT",
        "METHODOLOGY_CLAIM_LINK",
        "METHODOLOGY_EVIDENCE_LINK",
        "METHODOLOGY_SANAD_LINK",
        "METHODOLOGY_DEFECT_LINK",
        "METHODOLOGY_CALCULATION",
        "METHODOLOGY_TRUTH_DASHBOARD_ROW",
    }


def _dashboard_authority_sql(artifact: dict[str, Any]) -> str:
    rows = artifact["sql_authority_registry"]["rows"]
    authority = next(row for row in rows if row.get("authority_id") == "dashboard-root-equation-v2")
    return "\n".join(
        bytes.fromhex(value).decode("utf-8") for value in authority["body_sql_utf8_hex_tuple"]
    )


def _hermetic_policy_v2_registry(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Execute the named constructor without reading the private workbook."""

    from idis.methodology.importers import fdd_formula_policy_v2 as importer
    from idis.methodology.importers.fdd_excel import (
        FddWorkbookProjection,
        FddWorkbookRowProjection,
    )

    policy = _policy()
    projection = FddWorkbookProjection(
        source_hash=WORKBOOK_SHA256,
        source_byte_count=WORKBOOK_BYTE_COUNT,
        rows=tuple(
            FddWorkbookRowProjection(
                row_ordinal=row["row_ordinal"],
                sheet_ordinal=row["sheet_ordinal"],
                source_sheet=row["source_sheet"],
                source_row=row["source_row"],
                source_anchor_cell=row["source_anchor_cell"],
                term=row["term"],
                nature=row["nature"],
                line_item=row["line_item"],
                question_text=row["question_text"],
                methodology_question_id=row["methodology_question_id"],
            )
            for row in policy["workbook_row_registry"]["rows"]
        ),
    )
    monkeypatch.setattr(
        importer,
        "validated_production_fdd_workbook_projection",
        lambda _workbook: projection,
    )
    return importer.import_fdd_formula_policy_v2_registry(
        b"hermetic-projection-only",
        policy_artifact=POLICY_V2.read_bytes(),
    )


def test_v24_frozen_dashboard_sql_rejects_policy_v2_requirement_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _sql()
    authority = next(
        row
        for row in _artifact()["sql_authority_registry"]["rows"]
        if row.get("authority_id") == "dashboard-root-equation-v2"
    )
    assert authority["expected_sha256"] == (
        "513ca92f3e3c1c610af724413a1342743fc09b781fcb6fe6d6481b50f8570708"
    )
    registry = _hermetic_policy_v2_registry(monkeypatch)
    required = [
        item.model_dump(mode="json")
        for question in registry.current_version.questions
        for item in question.required_calculations
    ]
    assert len(required) == 10
    assert all(
        set(item) == {"formula_ordinal", "formula_type", "required", "scope"} for item in required
    )
    assert all(item["required"] is True for item in required)
    assert all("calc_type" not in item for item in required)

    dashboard_sql = _dashboard_authority_sql(_artifact())
    assert hashlib.sha256(dashboard_sql.encode("utf-8")).hexdigest() == (
        "6a6113a72f6e68011e7d6c992060faccf63201c3dd76153b61e5a56386cff34c"
    )
    assert dashboard_sql.count("item->>'calc_type'") == 3
    assert "item->>'formula_type'" not in dashboard_sql
    assert "item->>'formula_ordinal'" not in dashboard_sql
    with pytest.raises(
        runner.Task8V24CensusError,
        match="TASK8_V24_DASHBOARD_REQUIREMENT_BINDING_INVALID",
    ):
        runner.validate_v24_dashboard_requirement_binding(
            _artifact(),
            registry,
        )


def test_v24_requirement_binding_blocks_before_profile_or_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed = _signed()
    touched: list[str] = []
    real_module_loader = signed._load_hashed_module

    def profile(_mapping: dict[str, str]) -> Any:
        touched.append("profile")
        raise AssertionError("profile must remain untouched")

    def module_loader(*args: Any, **kwargs: Any) -> Any:
        path = kwargs.get("path")
        if path == signed.SQL_RUNNER_PATH:
            touched.append("database-runner")
            raise AssertionError("database runner must remain untouched")
        return real_module_loader(*args, **kwargs)

    monkeypatch.setattr(signed, "load_operator_material", profile)
    monkeypatch.setattr(signed, "_load_hashed_module", module_loader)
    with pytest.raises(
        signed.Task8V24SignedCensusError,
        match="TASK8_V24_DASHBOARD_REQUIREMENT_BINDING_INVALID",
    ):
        signed.execute_signed_census(
            output=REPO_ROOT / ".local_reports" / "slice100" / "task8" / "unused.json",
            process_env={"IDIS_STRICT_DOTENV_PATH": "C:/private/strict.env"},
        )
    assert touched == []


def test_v24_production_writers_can_bind_required_task7_roots_at_insert() -> None:
    from idis.persistence.repositories.methodologies import PostgresMethodologiesRepository
    from idis.persistence.repositories.methodology_extraction import (
        PostgresMethodologyExtractionRepository,
    )

    task5_source = inspect.getsource(PostgresMethodologiesRepository.insert_coverage_records)
    task6_source = inspect.getsource(PostgresMethodologyExtractionRepository.persist_planned_tasks)
    for source in (task5_source, task6_source):
        assert "run_output_object_ids" in source
        assert "validate_run_output_object_ids" in source
    assert (
        "run_output_object_ids"
        in inspect.signature(PostgresMethodologiesRepository.insert_coverage_records).parameters
    )
    assert (
        "run_output_object_ids"
        in inspect.signature(
            PostgresMethodologyExtractionRepository.persist_planned_tasks
        ).parameters
    )


def test_v24_legal_seed_has_no_trigger_bypass_or_placeholder_hashes() -> None:
    runner = _sql()
    source = SQL_RUNNER.read_text(encoding="utf-8")
    assert "session_replication_role" not in source
    for function in (
        runner._seed_scope,
        runner._insert_methodology_question_and_coverage,
        runner._seed_dashboard_graph,
        runner._seed_dashboard_batch,
        runner._insert_seed_output,
        runner._seed_erasure_request,
    ):
        function_source = inspect.getsource(function)
        assert '"a" * 64' not in function_source
        assert '"b" * 64' not in function_source
    assert "validate_governed_seed_hashes" in source


def test_v24_seed_fk_closure_is_complete_and_application_observed() -> None:
    runner = _sql()
    manifest = runner.build_seed_fk_closure_manifest(_artifact())
    expected_parents = {
        "methodology_versions",
        "run_methodology_selections",
        "documents",
        "document_spans",
        "methodology_extraction_attempts",
        "methodology_result_claim_links",
        "claims",
        "evidence_items",
        "sanads",
        "defects",
        "run_output_objects",
        "run_step_outputs",
    }
    assert expected_parents <= set(manifest["required_parent_relation_tuple"])
    assert manifest["session_replication_role_bypass_count"] == 0
    assert manifest["expected_fk_violation_count"] == 0
    observer_source = inspect.getsource(runner._observe_seed_fk_closure)
    assert "expected_application_role" in observer_source
    assert "_attest_connection_role_posture" in observer_source
    assert "pg_constraint" in observer_source
    assert "NOT EXISTS" in observer_source


def test_v24_execution_lease_is_database_clock_relative_but_receipt_stable() -> None:
    runner = _sql()
    source = SQL_RUNNER.read_text(encoding="utf-8")
    seed_source = inspect.getsource(runner._seed_scope)
    assert "_FIXTURE_LEASE_EXPIRES_AT" not in source
    assert "transaction_timestamp()+interval '1 hour'" in seed_source
    assert "transaction_timestamp()" in seed_source
    manifest = runner.build_zero_mutation_projection_manifest(_artifact())
    assert manifest["timestamp_policy"] == "compare-exact-redact-from-digest-v1"


def test_v24_restoration_plan_proves_both_authority_cycles_with_app_observation() -> None:
    runner = _sql()
    plan = runner.build_authority_restoration_plan(_artifact())
    assert plan["schema_version"] == "task8-v24-authority-restoration-plan-v1"
    assert plan["ddl_executor"] == "admin_setup_only"
    assert plan["catalog_observer"] == "application"
    assert [row["authority_id"] for row in plan["authority_tuple"]] == [
        "dashboard-partial-native-root-exact-one-v1",
        "task8-current48-dashboard-erasure-refusal-v1",
    ]
    assert all(row["phase_tuple"] == ["UP", "DOWN", "UP"] for row in plan["authority_tuple"])
    assert all(row["observer_tuple"] == ["application"] * 3 for row in plan["authority_tuple"])
    assert all(
        row["initial_catalog_sha256"] == row["reinstalled_catalog_sha256"]
        for row in plan["authority_tuple"]
    )


def test_v24_operator_mapping_is_private_and_does_not_inherit_process_secrets() -> None:
    signed = _signed()
    source = {
        "IDIS_STRICT_DOTENV_PATH": "C:/private/strict.env",
        "IDIS_DATABASE_URL": "must-not-pass",
        "IDIS_ERASURE_DATABASE_URL": "must-not-pass",
        "UNRELATED_SECRET": "must-not-pass",
    }
    assert signed.private_profile_mapping(source) == {
        "IDIS_STRICT_DOTENV_PATH": "C:/private/strict.env"
    }
    with pytest.raises(signed.Task8V24SignedCensusError):
        signed.private_profile_mapping({})


def _safe_census() -> dict[str, Any]:
    return {
        "schema_version": "task8-v24-postgres-authority-census-receipt-v1",
        "result": "PASS",
        "pipeline_contract_version": "full-strict-v24",
        "artifact_sha256": ARTIFACT_SHA256,
        "manifest_sha256": "1" * 64,
        "database_execution": {
            "admin_behavior_case_count": 0,
            "admin_observation_case_count": 0,
            "application_case_execution_count": 22,
            "erasure_executor_case_execution_count": 3,
            "legal_execution_count": 6,
            "illegal_rejection_count": 11,
            "direct_sql_rejection_count": 8,
            "rejected_case_projection_count": 19,
            "rejected_case_zero_mutation_count": 19,
            "unexpected_outcome_count": 0,
            "behavior_role_match_count": 25,
            "dashboard_coverage_row_count": 100,
            "dashboard_commercial_coverage_row_count": 21,
            "dashboard_financial_coverage_row_count": 79,
            "forward_lock_race_passed": True,
            "reverse_lock_race_passed": True,
            "rls_behavior_passed": True,
            "installed_tenant_rls_table_count": 69,
            "catalog_observer_application_role": True,
            "partial_authority_target_count": 19,
            "partial_authority_event_count": 57,
            "partial_authority_udu_passed": True,
            "erasure_authority_udu_passed": True,
            "rejected_case_projection_sha256": "2" * 64,
            "behavior_role_attestation_sha256": "3" * 64,
            "rls_behavior_sha256": "4" * 64,
            "case_result_sha256": "5" * 64,
            "catalog_projection_sha256": "6" * 64,
            "race_result_sha256": "7" * 64,
            "partial_authority_udu_sha256": "8" * 64,
            "erasure_authority_udu_sha256": "9" * 64,
            "partial_initial_catalog_sha256": "b" * 64,
            "partial_downgrade_catalog_sha256": "c" * 64,
            "partial_final_catalog_sha256": "b" * 64,
            "erasure_initial_catalog_sha256": "d" * 64,
            "erasure_downgrade_catalog_sha256": "e" * 64,
            "erasure_final_catalog_sha256": "d" * 64,
            "complete_execution_sha256": "a" * 64,
        },
        "disposable_database_dropped": True,
    }


def test_v24_signed_receipt_is_safe_verifiable_and_tamper_evident() -> None:
    signed = _signed()
    private = Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    preflight = signed.load_and_verify_preflight_assets()
    receipt = signed.build_signed_receipt(
        signer_id="slice100-governance-signer-v1",
        preflight=preflight,
        census_receipt=_safe_census(),
        base_profile_hmac="f" * 64,
        private_key=private,
        trusted_public_key_pem=public_pem,
    )
    assert tuple(receipt) == (
        "schema_version",
        "signer_id",
        "payload",
        "payload_sha256",
        "public_key_sha256",
        "signature_base64",
    )
    assert tuple(receipt["payload"]) == (
        "schema_version",
        "result",
        "artifact_schema",
        "fixture_schema",
        "pipeline_contract_version",
        "artifact_sha256",
        "fixture_sha256",
        "full_strict_contract_hash",
        "identity_graph_sha256",
        "identity_graph_counts",
        "approved_semantics_conservation_sha256",
        "policy_id",
        "policy_artifact_sha256",
        "policy_sha256",
        "reviewer_map_sha256",
        "policy_binding_sha256",
        "base_profile_hmac",
        "runner_sha256",
        "case_manifest_sha256",
        "database_execution",
        "disposable_database_dropped",
    )
    assert receipt["payload"]["schema_version"] == (
        "task8-v24-postgres-authority-census-payload-v1"
    )
    assert signed.build_signable_tuple(
        signer_id=receipt["signer_id"],
        payload_sha256=receipt["payload_sha256"],
        public_key_sha256=receipt["public_key_sha256"],
        payload=receipt["payload"],
    ) == [
        "task8-v24-postgres-authority-census-signing-v1",
        "slice100-governance-signer-v1",
        receipt["payload_sha256"],
        receipt["public_key_sha256"],
        receipt["payload"],
    ]
    rendered = signed.render_signed_receipt(receipt)
    verified = signed.verify_signed_receipt(
        receipt_raw=rendered,
        preflight=preflight,
        trusted_public_key_pem=public_pem,
    )
    assert verified["result"] == "PASS"
    assert verified["artifact_sha256"] == preflight["artifact_sha256"]
    assert set(verified["database_execution"]) == {
        "admin_behavior_case_count",
        "admin_observation_case_count",
        "application_case_execution_count",
        "erasure_executor_case_execution_count",
        "legal_execution_count",
        "illegal_rejection_count",
        "direct_sql_rejection_count",
        "rejected_case_projection_count",
        "rejected_case_zero_mutation_count",
        "unexpected_outcome_count",
        "behavior_role_match_count",
        "dashboard_coverage_row_count",
        "dashboard_commercial_coverage_row_count",
        "dashboard_financial_coverage_row_count",
        "forward_lock_race_passed",
        "reverse_lock_race_passed",
        "rls_behavior_passed",
        "installed_tenant_rls_table_count",
        "catalog_observer_application_role",
        "partial_authority_target_count",
        "partial_authority_event_count",
        "partial_authority_udu_passed",
        "erasure_authority_udu_passed",
        "rejected_case_projection_sha256",
        "behavior_role_attestation_sha256",
        "rls_behavior_sha256",
        "case_result_sha256",
        "catalog_projection_sha256",
        "race_result_sha256",
        "partial_authority_udu_sha256",
        "erasure_authority_udu_sha256",
        "partial_initial_catalog_sha256",
        "partial_downgrade_catalog_sha256",
        "partial_final_catalog_sha256",
        "erasure_initial_catalog_sha256",
        "erasure_downgrade_catalog_sha256",
        "erasure_final_catalog_sha256",
        "complete_execution_sha256",
    }
    assert verified["disposable_database_dropped"] is True
    assert "database_url" not in rendered.decode("utf-8").lower()

    tampered = copy.deepcopy(receipt)
    tampered["payload"]["database_execution"]["legal_execution_count"] = 7
    with pytest.raises(
        signed.Task8V24SignedCensusError,
        match="TASK8_V24_SIGNED_CENSUS_RECEIPT_INVALID",
    ):
        signed.verify_signed_receipt(
            receipt_raw=signed.render_signed_receipt(tampered),
            preflight=preflight,
            trusted_public_key_pem=public_pem,
        )


def test_v24_database_entrypoint_always_drops_disposable_database() -> None:
    runner = _sql()
    source = inspect.getsource(runner.run_disposable_postgres_census)
    assert "finally:" in source
    assert "drop_disposable_database" in source
    signature = inspect.signature(runner.run_disposable_postgres_census)
    assert tuple(signature.parameters) == (
        "admin_database_url",
        "application_database_url",
        "erasure_database_url",
        "artifact_raw",
        "policy_artifact_raw",
    )


def _run_v24_lifecycle(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    events: list[str] | None = None,
    create_failure: bool = False,
    setup_failure: bool = False,
) -> tuple[list[str], dict[str, Any] | None]:
    observed = [] if events is None else events

    def create(_url: str, _name: str) -> None:
        observed.append("CREATE")
        if create_failure:
            raise RuntimeError("create failed")

    def setup(*_args: object, **_kwargs: object) -> dict[str, Any]:
        observed.append("SETUP")
        if setup_failure:
            raise RuntimeError("setup failed")
        return {}

    def drop(_url: str, _name: str) -> None:
        observed.append("DROP")

    monkeypatch.setattr(runner, "_create_disposable_database", create)
    monkeypatch.setattr(runner, "_setup_disposable_schema", setup)
    monkeypatch.setattr(runner, "_drop_disposable_database", drop)
    monkeypatch.setattr(runner, "_run_database_execution", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(runner, "validate_census_receipt", lambda *_args, **_kwargs: None)
    receipt = runner.run_disposable_postgres_census(
        "postgresql://admin@localhost/base",
        "postgresql://app@localhost/base",
        "postgresql://eraser@localhost/base",
        V24_ARTIFACT.read_bytes(),
        POLICY_V2.read_bytes(),
    )
    return observed, receipt


def test_v24_database_create_failure_never_drops_an_unowned_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _sql()
    events: list[str] = []

    def create(_url: str, _name: str) -> None:
        events.append("CREATE")
        raise RuntimeError("create failed")

    monkeypatch.setattr(runner, "_create_disposable_database", create)
    monkeypatch.setattr(
        runner,
        "_drop_disposable_database",
        lambda *_args, **_kwargs: events.append("DROP"),
    )
    with pytest.raises(RuntimeError, match="create failed"):
        runner.run_disposable_postgres_census(
            "postgresql://admin@localhost/base",
            "postgresql://app@localhost/base",
            "postgresql://eraser@localhost/base",
            V24_ARTIFACT.read_bytes(),
            POLICY_V2.read_bytes(),
        )
    assert events == ["CREATE"]


def test_v24_database_setup_failure_drops_the_owned_database_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _sql()
    events: list[str] = []
    with pytest.raises(RuntimeError, match="setup failed"):
        _run_v24_lifecycle(
            runner,
            monkeypatch,
            events=events,
            setup_failure=True,
        )
    assert events == ["CREATE", "SETUP", "DROP"]


def test_v24_database_success_drops_the_owned_database_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _sql()
    events, receipt = _run_v24_lifecycle(runner, monkeypatch)
    assert events == ["CREATE", "SETUP", "DROP"]
    assert receipt is not None
    assert receipt["disposable_database_dropped"] is True


def test_v24_sql_runner_contains_no_operator_profile_or_environment_access() -> None:
    source = SQL_RUNNER.read_text(encoding="utf-8")
    assert "load_strict_runtime_profile" not in source
    assert "os.environ" not in source
    assert "IDIS_STRICT_DOTENV_PATH" not in source
    assert "IDIS_DATABASE_URL" not in source


def test_v24_execution_path_has_no_random_or_v1_dashboard_identity_seeds() -> None:
    source = SQL_RUNNER.read_text(encoding="utf-8")
    # Randomness is allowed only for the disposable database namespace.  Every
    # row/object/link/hash fixture is a reviewed deterministic derivation.
    assert source.count("uuid.uuid4()") <= 1
    assert "methodology-truth-dashboard-root-projection-v1" not in source
    assert "task8-v24-native-identity-placeholder" not in source
    assert "task8-v24-native-output-placeholder" not in source


def test_v24_authority_cycles_are_executed_and_app_observed_not_asserted() -> None:
    runner = _sql()
    source = inspect.getsource(runner._run_database_execution)
    assert "_execute_authority_restoration_cycles" in source
    assert '"partial_authority_udu_passed": True' not in source
    assert '"erasure_authority_udu_passed": True' not in source
    restoration_source = inspect.getsource(runner._execute_authority_restoration_cycles)
    assert "urls.application" in restoration_source
    assert "urls.admin" in restoration_source
    assert "initial_catalog_sha256" in restoration_source
    assert "downgrade_catalog_sha256" in restoration_source
    assert "final_catalog_sha256" in restoration_source


def test_v24_zero_mutation_projections_are_application_role_observations() -> None:
    runner = _sql()
    source = SQL_RUNNER.read_text(encoding="utf-8")
    assert "_tenant_deal_projection(\n                urls.admin" not in source
    assert "_tenant_deal_projection(\n            urls.admin" not in source
    projection_source = inspect.getsource(runner._tenant_deal_projection)
    assert "expected_application_role" in projection_source
    assert "_attest_connection_role_posture" in projection_source
    assert "_set_tenant(cursor, tenant_id)" in projection_source


def test_v24_zero_admin_and_app_catalog_counts_are_derived_from_role_attestations() -> None:
    runner = _sql()
    source = inspect.getsource(runner._run_database_execution)
    assert '"admin_behavior_case_count": 0' not in source
    assert '"admin_observation_case_count": 0' not in source
    assert '"catalog_observer_application_role": True' not in source
    assert "observation_role_rows" in source


def test_v24_complete_execution_digest_binds_both_observed_udu_cycles() -> None:
    runner = _sql()
    values = [str(index) * 64 for index in range(1, 7)]
    baseline = runner.complete_execution_sha256(*values)
    assert runner.complete_execution_sha256(*values) == baseline
    for ordinal in (4, 5):
        changed = list(values)
        changed[ordinal] = "f" * 64
        assert runner.complete_execution_sha256(*changed) != baseline
    producer = inspect.getsource(runner._run_database_execution)
    validator = inspect.getsource(runner.validate_census_receipt)
    assert "complete_execution_sha256(" in producer
    assert "complete_execution_sha256(" in validator


def test_v24_seed_and_persisted_projection_hashes_exclude_clock_variance() -> None:
    runner = _sql()
    source = SQL_RUNNER.read_text(encoding="utf-8")
    assert "datetime.now" not in source
    assert "statement_timestamp()" not in source
    manifest = runner.build_zero_mutation_projection_manifest(_artifact())
    assert manifest["timestamp_policy"] == "compare-exact-redact-from-digest-v1"
    projection_source = inspect.getsource(runner._tenant_deal_projection)
    assert "full_projection_sha256" in projection_source
    assert "timestamp_columns" in projection_source
    case_source = inspect.getsource(runner._execute_behavior_case)
    assert 'pre_projection["full_projection_sha256"]' not in case_source
    assert 'post_projection["full_projection_sha256"]' not in case_source


def test_v24_timestamp_only_projection_change_is_compared_but_not_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _sql()

    class Cursor:
        def __init__(self, full_rows: str) -> None:
            self.full_rows = full_rows
            self.sql = ""

        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, sql: str, _params: object = None) -> None:
            self.sql = sql

        def fetchone(self) -> tuple[object, ...]:
            if "current_user,session_user" in self.sql:
                return ("idis_app", "idis_app", False, False)
            if "information_schema.columns" in self.sql:
                return (
                    ["tenant_id", "deal_id", "created_at"],
                    ["created_at"],
                )
            if "jsonb_agg" in self.sql:
                return (
                    1,
                    self.full_rows,
                    '[{"deal_id":"d","tenant_id":"t"}]',
                )
            raise AssertionError(self.sql)

    class Connection:
        def __init__(self, full_rows: str) -> None:
            self._cursor = Cursor(full_rows)

        def cursor(self) -> Cursor:
            return self._cursor

        def rollback(self) -> None:
            return None

        def close(self) -> None:
            return None

    connections = iter(
        (
            Connection('[{"created_at":"2026-08-14T12:00:00Z"}]'),
            Connection('[{"created_at":"2026-08-14T12:00:01Z"}]'),
        )
    )
    monkeypatch.setattr(runner, "_connect", lambda _url: next(connections))
    manifest = {
        "control_relation_tuple": ["deals"],
        "current_relation_tuple": ["deals"],
        "timestamp_policy": "compare-exact-redact-from-digest-v1",
        "expected_sha256": "f" * 64,
    }
    arguments = {
        "expected_application_role": "idis_app",
        "tenant_id": uuid.UUID("11111111-1111-4111-8111-111111111111"),
        "deal_id": uuid.UUID("22222222-2222-4222-8222-222222222222"),
        "projection_manifest": manifest,
    }
    first = runner._tenant_deal_projection("postgresql://unused", **arguments)
    second = runner._tenant_deal_projection("postgresql://unused", **arguments)
    assert first["full_projection_sha256"] != second["full_projection_sha256"]
    assert first["projection_sha256"] == second["projection_sha256"]


def test_v24_every_behavior_observation_uses_shared_role_posture_attestation() -> None:
    runner = _sql()
    source = SQL_RUNNER.read_text(encoding="utf-8")
    helper_source = inspect.getsource(runner._attest_connection_role_posture)
    assert source.count("SELECT current_user,session_user") == 1
    assert "pg_catalog.pg_roles" in helper_source
    assert "rolsuper" in helper_source
    assert "rolbypassrls" in helper_source
    for function in (
        runner._tenant_deal_projection,
        runner._run_rls_behavior_matrix,
        runner._execute_behavior_case,
        runner._catalog_projection,
        runner._run_lock_races,
        runner._observe_authority_catalog,
        runner._run_database_execution,
    ):
        assert "_attest_connection_role_posture" in inspect.getsource(function)

    class Cursor:
        def __init__(self, row: tuple[object, ...]) -> None:
            self.row = row

        def execute(self, sql: str) -> None:
            assert "current_user,session_user" in sql

        def fetchone(self) -> tuple[object, ...]:
            return self.row

    assert (
        runner._attest_connection_role_posture(
            Cursor(("idis_app", "idis_app", False, False)),
            "idis_app",
        )
        == "idis_app"
    )
    for row in (
        ("idis_app", "idis_app", True, False),
        ("idis_app", "idis_app", False, True),
        ("other", "other", False, False),
    ):
        with pytest.raises(
            runner.Task8V24CensusError,
            match="TASK8_V24_CENSUS_ROLE_POSTURE_INVALID",
        ):
            runner._attest_connection_role_posture(Cursor(row), "idis_app")


def test_v24_signed_runner_has_exclusive_task8_report_boundary() -> None:
    signed = _signed()
    assert signed.REPORT_ROOT == REPO_ROOT / ".local_reports" / "slice100" / "task8"
    assert signed.DEFAULT_OUTPUT.name == "task8_v24_postgres_census_signed_receipt.json"
    assert str(signed.PRIVATE_KEY_PATH) == r"C:\IDIS-secrets\keys\governance-signing-private.pem"
    assert signed.APPROVED_SIGNER_ID == "slice100-governance-signer-v1"


def test_v24_sql_runner_does_not_open_database_at_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(os, "getenv", lambda key, default=None: calls.append(key) or default)
    _sql()
    assert calls == []

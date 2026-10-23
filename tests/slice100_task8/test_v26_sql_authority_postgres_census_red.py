"""RED-first contracts for the full-strict-v26 PostgreSQL census.

This module is intentionally hermetic: it may inspect and execute pure planning
code, but it must never load an operator profile, open PostgreSQL, or read a
signing key.  Operator-backed execution is a later, separately reviewed gate.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import ast
import sys
import uuid
from copy import deepcopy
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

SQL_RUNNER = REPO_ROOT / "scripts" / "run_task8_v26_sql_authority_census.py"
SIGNED_RUNNER = REPO_ROOT / "scripts" / "run_task8_v26_signed_census.py"
STAGED_ARTIFACT = (
    REPO_ROOT
    / ".local_reports"
    / "slice100"
    / "task8"
    / "v26-candidate"
    / "full_strict_v26_reviewed_preimage_v1.json"
)
PARENT_V21 = REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
POLICY_V2 = REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
REVIEWER_MAP = (
    REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
INDEPENDENT_VERIFIER = REPO_ROOT / "scripts" / "verify_full_strict_v26_fixture_independently.py"

ARTIFACT_SHA256 = "7d8d8691a65efcced6e9253f5b7ba835e36785115f98846fcbc0c66054978c27"
FIXTURE_SHA256 = "8ad6e65ddded53f9711160a8b1fbbc1dc129f72c9a715148518a13e2bb18f0c7"
CONTRACT_SHA256 = "bb0bd8deb5c7c85aa292a860773353f04e60db01c80d2d3a02ff25aed7e840fc"
GRAPH_SHA256 = "4fa300c83e6fff35d85768ec5978a26e13ab60c800b5a3229a67cfbaf8057ece"
CONSERVATION_SHA256 = "952e76bf57c1d9e4985aee04a1eaf906ff6dc8313bf8485242a9e56b7720d235"
POLICY_BINDING_SHA256 = "3e07e20d1b304e0e22d8956f408562ac194103184d475623118497a21ae5a467"
POLICY_ARTIFACT_SHA256 = "e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064"
POLICY_SHA256 = "2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9"
REVIEWER_MAP_SHA256 = "98cf84d4498f57465cd61501ea8bbbd93f7507d8628a61d7cbcf9cede459fe2a"
PARENT_ARTIFACT_SHA256 = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
CURRENT_PIPELINE_VERSION = "full-strict-v21"
CURRENT_PIPELINE_HASH = "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"

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
    return _load(SQL_RUNNER, "_task8_v26_sql_census_red")


def _signed() -> ModuleType:
    return _load(SIGNED_RUNNER, "_task8_v26_signed_census_red")


def _verifier() -> ModuleType:
    return _load(INDEPENDENT_VERIFIER, "_task8_v26_census_independent_verifier")


def _task5_roundtrip_fixture(
    runner: ModuleType,
) -> tuple[tuple[dict[str, object], ...], dict[str, object], tuple[str, str, str]]:
    tenant_id = "11111111-1111-4111-8111-111111111111"
    deal_id = "22222222-2222-4222-8222-222222222222"
    run_id = "33333333-3333-4333-8333-333333333333"
    identities: list[dict[str, object]] = []
    for methodology_type, count, header_row, source_type in (
        ("commercial_dd", 21, None, "checked_in_template"),
        ("financial_dd", 79, 2, "operator_workbook"),
    ):
        version_id = f"{methodology_type}:version-v2"
        source_sha256 = hashlib.sha256(
            f"{methodology_type}:source".encode("ascii")
        ).hexdigest()
        record_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"idis:methodology-version:{tenant_id}:{version_id}:{source_sha256}",
            )
        )
        source_alias = f"{methodology_type}-source"
        rows: list[list[object]] = []
        for ordinal in range(1, count + 1):
            question_id = f"{methodology_type}:question:{ordinal:03d}"
            question_record_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"idis:methodology-question:{tenant_id}:{record_id}:{question_id}",
                )
            )
            if methodology_type == "commercial_dd" and ordinal == count:
                requirements = [{"calc_type": "LTV_CAC_RATIO", "required": False}]
            elif methodology_type == "financial_dd" and ordinal <= 10:
                requirements = [
                    {
                        "formula_ordinal": ordinal,
                        "formula_type": f"FORMULA_{ordinal:02d}",
                        "required": True,
                        "scope": "line_item",
                    }
                ]
            else:
                requirements = []
            semantic_sha256 = hashlib.sha256(
                f"{methodology_type}:semantic:{ordinal}".encode("ascii")
            ).hexdigest()
            payload_sha256 = hashlib.sha256(
                f"{methodology_type}:payload:{ordinal}".encode("ascii")
            ).hexdigest()
            rows.append(
                [
                    tenant_id,
                    question_record_id,
                    record_id,
                    version_id,
                    question_id,
                    methodology_type,
                    methodology_type,
                    ordinal,
                    "Section A",
                    source_type,
                    source_alias,
                    "Section A",
                    ordinal,
                    f"A{ordinal}",
                    source_sha256,
                    semantic_sha256,
                    payload_sha256,
                    payload_sha256,
                    requirements,
                ]
            )
        required_tuple = [[row[1], row[18]] for row in rows]
        identities.append(
            {
                "schema_version": "task8-v26-task5-version-roundtrip-identity-v2",
                "tenant_id": tenant_id,
                "methodology_version_record_id": record_id,
                "methodology_version_id": version_id,
                "methodology_id": methodology_type,
                "methodology_type": methodology_type,
                "version_label": "v2",
                "source_type": source_type,
                "source_alias": source_alias,
                "source_sha256": source_sha256,
                "source_byte_count": 100 + count,
                "source_contract_hash": hashlib.sha256(
                    f"{methodology_type}:contract".encode("ascii")
                ).hexdigest(),
                "source_trace_sha256": hashlib.sha256(
                    f"{methodology_type}:trace".encode("ascii")
                ).hexdigest(),
                "registry_sha256": hashlib.sha256(
                    f"{methodology_type}:registry".encode("ascii")
                ).hexdigest(),
                "version_sha256": hashlib.sha256(
                    f"{methodology_type}:version".encode("ascii")
                ).hexdigest(),
                "question_count": count,
                "source_section_count": 1,
                "header_row": header_row,
                "status": "FROZEN",
                "timestamp_redaction_field_tuple": ["imported_at", "frozen_at"],
                "deterministic_registry_bytes_sha256": hashlib.sha256(
                    f"{methodology_type}:registry-bytes".encode("ascii")
                ).hexdigest(),
                "methodology_question_payload_hashes": [row[16] for row in rows],
                "question_identity_field_tuple": list(
                    runner._TASK5_QUESTION_IDENTITY_FIELDS
                ),
                "question_identity_tuple": rows,
                "question_identity_tuple_sha256": runner._canonical_sha256(rows),
                "required_calculation_tuple": required_tuple,
                "required_calculation_tuple_sha256": runner._canonical_sha256(
                    required_tuple
                ),
            }
        )
    commercial, financial = identities
    selection_hash = runner._canonical_sha256(
        {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "commercial_dd_version_record_id": commercial[
                "methodology_version_record_id"
            ],
            "commercial_methodology_version_id": commercial["methodology_version_id"],
            "commercial_source_sha256": commercial["source_sha256"],
            "commercial_registry_hash": commercial["registry_sha256"],
            "commercial_version_hash": commercial["version_sha256"],
            "financial_dd_version_record_id": financial[
                "methodology_version_record_id"
            ],
            "financial_methodology_version_id": financial["methodology_version_id"],
            "financial_source_sha256": financial["source_sha256"],
            "financial_registry_hash": financial["registry_sha256"],
            "financial_version_hash": financial["version_sha256"],
            "selected_question_count": 100,
        }
    )
    selection = {
        "selection_id": str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"idis:run-methodology-selection:{tenant_id}:{run_id}:{selection_hash}",
            )
        ),
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "run_id": run_id,
        "commercial_dd_version_record_id": commercial["methodology_version_record_id"],
        "financial_dd_version_record_id": financial["methodology_version_record_id"],
        "selected_version_ids": [
            commercial["methodology_version_id"],
            financial["methodology_version_id"],
        ],
        "selected_source_hashes": [
            commercial["source_sha256"],
            financial["source_sha256"],
        ],
        "selected_registry_hashes": [
            commercial["registry_sha256"],
            financial["registry_sha256"],
        ],
        "selected_version_hashes": [
            commercial["version_sha256"],
            financial["version_sha256"],
        ],
        "selection_hash": selection_hash,
        "selected_question_count": 100,
        "state": "FROZEN",
        "immutable": True,
    }
    question_projection_sha256 = runner._canonical_sha256(
        [
            "task8-v26-task5-question-db-projection-v2",
            [row for identity in identities for row in identity["question_identity_tuple"]],
        ]
    )
    version_projection_sha256 = runner._canonical_sha256(
        [
            "task8-v26-task5-version-db-projection-v1",
            [
                [identity[field] for field in (
                    "methodology_version_record_id",
                    "methodology_version_id",
                    "methodology_id",
                    "methodology_type",
                    "version_label",
                    "source_type",
                    "source_alias",
                    "source_sha256",
                    "source_byte_count",
                    "source_contract_hash",
                    "source_trace_sha256",
                    "registry_sha256",
                    "version_sha256",
                    "question_count",
                    "source_section_count",
                    "header_row",
                    "status",
                )]
                for identity in identities
            ],
        ]
    )
    selection_projection_sha256 = runner._canonical_sha256(
        ["task8-v26-task5-selection-db-projection-v1", selection]
    )
    return (
        tuple(identities),
        selection,
        (
            question_projection_sha256,
            version_projection_sha256,
            selection_projection_sha256,
        ),
    )


def _task5_selection_immutability_sha256() -> str:
    return hashlib.sha256(b"task8-v26-selection-immutability-proof").hexdigest()


def _artifact_raw() -> bytes:
    return STAGED_ARTIFACT.read_bytes()


def _fixture_raw() -> bytes:
    verifier = _verifier()
    return verifier.derive_fixture_bytes(
        parent_artifact_raw=PARENT_V21.read_bytes(),
        policy_artifact_raw=POLICY_V2.read_bytes(),
        reviewer_map_raw=REVIEWER_MAP.read_bytes(),
        v26_artifact_raw=_artifact_raw(),
    )


def test_v26_census_implementation_files_exist() -> None:
    assert SQL_RUNNER.is_file()
    assert SIGNED_RUNNER.is_file()


def test_v26_runner_is_native_and_never_consumes_void_v24_v25() -> None:
    runner = _sql()
    assert runner.PIPELINE_CONTRACT_VERSION == "full-strict-v26"
    source = SQL_RUNNER.read_text(encoding="utf-8")
    for forbidden in (
        "run_task8_v24_sql_authority_census",
        "run_task8_v25_sql_authority_census",
        "full_strict_v24_reviewed_preimage",
        "full_strict_v25_reviewed_preimage",
        "task8-v24-postgres-authority-census",
        "task8-v25-postgres-authority-census",
    ):
        assert forbidden not in source
    assert "exec(" not in source
    assert "importlib" not in source


def test_v26_preflight_binds_explicit_staged_bytes_and_all_six_identities() -> None:
    signed = _signed()
    preflight = signed.validate_preflight_assets(
        artifact_raw=_artifact_raw(),
        fixture_raw=_fixture_raw(),
        parent_artifact_raw=PARENT_V21.read_bytes(),
        policy_artifact_raw=POLICY_V2.read_bytes(),
        reviewer_map_raw=REVIEWER_MAP.read_bytes(),
    )
    assert preflight == {
        "schema_version": "task8-v26-postgres-census-preflight-v1",
        "artifact_schema": "full-strict-v26-reviewed-preimage-v1",
        "fixture_schema": "full-strict-v26-contract-hash-fixture-v1",
        "pipeline_contract_version": "full-strict-v26",
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
        "policy_id": "fdd-formula-policy-v2",
        "policy_artifact_sha256": POLICY_ARTIFACT_SHA256,
        "policy_sha256": POLICY_SHA256,
        "reviewer_map_sha256": REVIEWER_MAP_SHA256,
        "policy_binding_sha256": POLICY_BINDING_SHA256,
        "parent_artifact_sha256": PARENT_ARTIFACT_SHA256,
    }


def test_v26_preflight_rejects_any_tamper_before_profile_or_database() -> None:
    signed = _signed()
    fixture = bytearray(_fixture_raw())
    fixture[-2] ^= 1
    with pytest.raises(
        signed.Task8V26SignedCensusError,
        match="^TASK8_V26_SIGNED_CENSUS_PREFLIGHT_INVALID$",
    ):
        signed.validate_preflight_assets(
            artifact_raw=_artifact_raw(),
            fixture_raw=bytes(fixture),
            parent_artifact_raw=PARENT_V21.read_bytes(),
            policy_artifact_raw=POLICY_V2.read_bytes(),
            reviewer_map_raw=REVIEWER_MAP.read_bytes(),
        )


def test_v26_current_run_and_steps_use_exact_0038_v21_coordinates() -> None:
    runner = _sql()
    assert runner.CURRENT_RUNTIME_PIPELINE_CONTRACT_VERSION == CURRENT_PIPELINE_VERSION
    assert runner.CURRENT_RUNTIME_PIPELINE_CONTRACT_HASH == CURRENT_PIPELINE_HASH
    rows = runner.build_current_run_step_plan(
        tenant_id="11111111-1111-4111-8111-111111111111",
        deal_id="22222222-2222-4222-8222-222222222222",
        run_id="33333333-3333-4333-8333-333333333333",
    )
    assert [
        (row["step_order"], row["step_name"], row["semantic_contract_hash"]) for row in rows
    ] == list(EXPECTED_STEP_ROWS)
    assert all(row["semantic_contract_version"] == "2" for row in rows)
    assert all(row["pipeline_contract_version"] == CURRENT_PIPELINE_VERSION for row in rows)
    assert all(row["pipeline_contract_hash"] == CURRENT_PIPELINE_HASH for row in rows)
    assert len({row["step_id"] for row in rows}) == 8


def test_v26_manifest_preserves_25_behavior_cases_and_adds_closed_v3_matrix() -> None:
    runner = _sql()
    artifact = json.loads(_artifact_raw())
    manifest = runner.build_census_case_manifest(artifact)
    runner.validate_census_case_manifest(manifest, artifact)
    assert manifest["dashboard_coverage_row_count"] == 100
    assert manifest["dashboard_commercial_coverage_row_count"] == 21
    assert manifest["dashboard_financial_coverage_row_count"] == 79
    assert manifest["dashboard_expected_row_count"] == 100
    behavior = runner.census_case_rows(manifest)
    assert len(behavior) == 25
    assert all(row["execution_role"] in {"application", "erasure_executor"} for row in behavior)
    assert all(row["execution_role"] != "admin" for row in behavior)
    requirement = manifest["requirement_matrix"]
    assert requirement["legal_case_id_tuple"] == [
        "LEGACY_EMPTY",
        "LEGACY_TWO_FIELD",
        "POLICY_V2_EMPTY",
        "POLICY_V2_TEN_LITERAL_TUPLES",
    ]
    assert set(requirement["negative_case_id_tuple"]) == {
        "LEGACY_FOUR_FIELD",
        "POLICY_V2_TWO_FIELD",
        "UNKNOWN_FORMULA_TYPE",
        "ORDINAL_DRIFT",
        "SCOPE_DRIFT",
        "UNDECLARED_FDD_CALCULATION_ROOT",
        "MIXED_SHAPES",
        "POLICY_V2_EXTRA_FIELD",
        "POLICY_V2_MISSING_FIELD",
        "LEGACY_EXTRA_FIELD",
        "LEGACY_MISSING_FIELD",
        "CROSS_VERSION_JOIN",
        "POLICY_V2_DUPLICATE_REQUIREMENT_TUPLE",
    }


def test_v26_seed_plan_is_named_constructor_and_production_repository_only() -> None:
    runner = _sql()
    source = inspect.getsource(runner.build_v26_dashboard_seed_plan)
    assert "workbook_raw" in inspect.signature(runner.build_v26_dashboard_seed_plan).parameters
    assert "import_cdd_template" in source
    assert "import_fdd_formula_policy_v2_registry" in source
    assert "policy_artifact=policy_artifact_raw" in source
    persistence = inspect.getsource(runner._persist_task5_seed_plan)
    for call in (
        ".import_registry(",
        ".freeze_version(",
        ".get_frozen_version(",
        ".create_run_selection(",
        ".insert_coverage_records(",
    ):
        assert call in persistence
    assert "run_output_object_ids=" in persistence
    assert "_source_contract_hash" in persistence
    assert "task8-v26-methodology-source-contract-v1" not in persistence
    assert "e42ae90f9f8b34657ba022c056cb3a0d4ef20006b89cc71cd1dfbe82b56b9b9a" in persistence
    assert "ed191040179c85ee2298b9d53df024600e069a8a66ba758b7ec50ddb76d1612e" in persistence
    sql_source = SQL_RUNNER.read_text(encoding="utf-8")
    # A direct question INSERT is admitted only for the closed negative matrix.
    # The legal/main/99 production paths must remain constructor/repository-only.
    tree = ast.parse(sql_source)
    direct_insert_functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and "INSERT INTO public.methodology_questions" in ast.get_source_segment(
            sql_source, node
        )
    }
    assert direct_insert_functions == {"_persist_adversarial_requirement_task5_seed"}
    for production_function in (
        runner._persist_task5_seed_plan,
        runner._persist_bound_runtime_graph,
        runner._persist_partial_dashboard_runtime_graph,
        runner._run_main_dashboard_census,
    ):
        assert "_persist_adversarial_requirement_task5_seed" not in inspect.getsource(
            production_function
        )
    assert "INSERT INTO methodology_questions" not in sql_source


def test_v26_seed_graph_is_trigger_enabled_final_bound_and_placeholder_free() -> None:
    runner = _sql()
    source = SQL_RUNNER.read_text(encoding="utf-8")
    assert "SET LOCAL session_replication_role" not in source
    assert "SET session_replication_role" not in source
    assert source.count("uuid.uuid4()") <= 1
    assert "datetime.now" not in source
    assert "'RUNTIME'" in source or '"RUNTIME"' in source
    for repeated in "0123456789abcdef":
        assert f'"{repeated}" * 64' not in source
        assert f"'{repeated}' * 64" not in source
    native = inspect.getsource(runner._persist_seed_native_root)
    assert "INSERT INTO public.run_output_objects" in native
    assert "UPDATE public.run_output_objects" not in native
    assert "INSERT INTO public.run_step_outputs" in native
    assert "SET CONSTRAINTS ALL IMMEDIATE" in native
    assert "run_output_object_id" in inspect.getsource(runner._persist_runtime_graph_core)


def test_v26_post_0038_source_freeze_and_root_aware_paths_are_reachable() -> None:
    runner = _sql()
    source_stage = inspect.getsource(runner._stage_and_freeze_source_graph)
    assert "freeze_source_package" in source_stage
    assert "application_engine" in source_stage
    assert "RUNTIME" in source_stage
    assert "SET CONSTRAINTS ALL IMMEDIATE" in source_stage

    execution = inspect.getsource(runner._persist_runtime_graph_core)
    coverage_persistence = inspect.getsource(runner._persist_task5_seed_plan)
    for production_boundary in (
        "_persist_task5_seed_plan",
        "persist_planned_tasks",
        "persist_claim_links",
        "persist_evidence_links",
        "persist_sanad_links",
        "persist_defect_links",
    ):
        assert production_boundary in execution
    assert (execution + coverage_persistence).count("run_output_object_ids=") >= 6


def test_v26_obsolete_unrooted_seed_contracts_are_extinct() -> None:
    runner = _sql()
    source = SQL_RUNNER.read_text(encoding="utf-8")
    for symbol in (
        "_insert_methodology_question_and_coverage",
        "_insert_seed_output",
        "_refresh_seed_output_hashes",
    ):
        assert not hasattr(runner, symbol)
        assert symbol not in source
    assert "'BACKFILL'" not in source
    assert '"BACKFILL"' not in source
    assert source.count("INSERT INTO public.methodology_questions") == 1
    assert "def _persist_adversarial_requirement_task5_seed" in source


def test_v26_requirement_matrix_is_application_observed_and_zero_mutation() -> None:
    runner = _sql()
    source = inspect.getsource(runner._run_dashboard_requirement_matrix)
    case_source = inspect.getsource(runner._execute_requirement_case)
    legal_source = inspect.getsource(runner._observe_legal_requirement_cases)
    assert "_attest_connection_role_posture" in case_source + legal_source
    assert (
        "application_url" in inspect.signature(runner._run_dashboard_requirement_matrix).parameters
    )
    assert {
        "main_scope",
        "main_dashboard_census_sha256",
    } <= set(inspect.signature(runner._run_dashboard_requirement_matrix).parameters)
    assert "_tenant_deal_projection" in case_source
    assert "zero_mutation" in case_source
    vectors = runner._requirement_case_vectors(json.loads(_artifact_raw()))
    assert sum(vector["accepted"] is False for vector in vectors) == 13


def test_v26_requirement_matrix_uses_the_installed_equation_v3_write_path() -> None:
    """The matrix must exercise the installed guard, never a copied predicate."""

    runner = _sql()
    source = inspect.getsource(runner._run_dashboard_requirement_matrix)
    runner_source = SQL_RUNNER.read_text(encoding="utf-8")
    assert 'predicate_sql = """' not in runner_source
    assert "SELECT %s::text AS methodology_version_id" not in runner_source
    assert "predicate_sql" not in source
    assert "_observe_installed_dashboard_authority" in source
    assert "_execute_requirement_case" in source
    case_source = inspect.getsource(runner._execute_requirement_case)
    validator_source = inspect.getsource(runner.validate_requirement_matrix_result_rows)
    assert "_insert_dashboard_batch" in case_source
    assert "SET CONSTRAINTS ctrg_v2_dashboard_root_equation IMMEDIATE" in case_source
    assert "SET CONSTRAINTS ctrg_v3_dashboard_root_equation IMMEDIATE" in case_source
    assert "_DASHBOARD_REJECTION_SQLSTATE" in validator_source
    assert "_DASHBOARD_REJECTION_CODE" in validator_source
    assert "authority_body_sha256" in source


def test_v26_requirement_authority_hashes_exact_artifact_body() -> None:
    runner = _sql()
    artifact = json.loads(_artifact_raw())
    authority = next(
        row
        for row in artifact["sql_authority_registry"]["rows"]
        if row["authority_id"] == "dashboard-root-equation-v3"
    )
    body = runner._dashboard_authority_sql(artifact)
    assert authority["expected_sha256"] == (
        "20250e787f770872cb782c48cb9c65deec186acd5751b6f8ebd39c5c10faeeb5"
    )
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == (
        "9dc3d588a8014796b2da01ac4942dd0b2037db2f0fff6a809197ed786cdf95d1"
    )
    binding = runner._dashboard_authority_binding(artifact)
    assert binding == {
        "authority_row_sha256": (
            "20250e787f770872cb782c48cb9c65deec186acd5751b6f8ebd39c5c10faeeb5"
        ),
        "install_tuple_sha256": (
            "9dc3d588a8014796b2da01ac4942dd0b2037db2f0fff6a809197ed786cdf95d1"
        ),
        "install_tuple_utf8_bytes": 26016,
        "create_function_sha256": (
            "6433d7c462b90804977dd5999d79a816a7386750e00748f0121dbfa75d088619"
        ),
        "create_function_utf8_bytes": 25367,
        "semantic_body_sha256": (
            "509789807246a51eb4f449e67548ec229dd928d3fac554925d6fa13a8ef713b7"
        ),
        "semantic_body_utf8_bytes": 25212,
    }


def test_v26_requirement_result_validator_enforces_12_trigger_plus_one_schema_preemption() -> None:
    runner = _sql()
    vectors = runner._requirement_case_vectors(json.loads(_artifact_raw()))
    rows: list[dict[str, object]] = []
    for vector in vectors:
        accepted = bool(vector["accepted"])
        case_id = str(vector["case_id"])
        cross_version = case_id == "CROSS_VERSION_JOIN"
        rows.append(
            {
                "case_id": case_id,
                "expected_accepted": accepted,
                "observed_accepted": accepted,
                "observer_role": "app_role",
                "session_replication_role": "origin",
                "observer_posture_sha256": "2" * 64,
                "pre_observer_posture_sha256": "3" * 64,
                "post_observer_posture_sha256": "4" * 64,
                "sqlstate": (
                    None if accepted else "23514"
                ),
                "reason_code": (
                    None
                    if accepted
                    else (
                        "METHODOLOGY_COVERAGE_SCOPE_INVALID"
                        if cross_version
                        else "TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION"
                    )
                ),
                "rejection_path": (
                    None
                    if accepted
                    else ("SCHEMA_PREEMPTION" if cross_version else "EQUATION_V3")
                ),
                "guard_reached": accepted or not cross_version,
                "zero_mutation": True,
                "dashboard_row_delta": 0,
                "dashboard_root_delta": 0,
                "pre_projection_sha256": (None if accepted else "1" * 64),
                "post_projection_sha256": (None if accepted else "1" * 64),
                "stable_pre_projection_sha256": (None if accepted else "5" * 64),
                "stable_post_projection_sha256": (None if accepted else "5" * 64),
                "production_graph_sha256": ("6" * 64 if accepted else None),
                "legal_projection_sha256": ("7" * 64 if accepted else None),
            }
        )
    receipt = runner.validate_requirement_matrix_result_rows(
        rows,
        expected_application_role="app_role",
    )
    assert receipt["equation_v3_rejection_count"] == 12
    assert receipt["schema_preemption_count"] == 1
    assert receipt["negative_zero_mutation_count"] == 13
    assert receipt["negative_zero_dashboard_derivation_count"] == 13

    cross_index = next(
        index for index, row in enumerate(rows) if row["case_id"] == "CROSS_VERSION_JOIN"
    )
    rows[cross_index] = {
        **rows[cross_index],
        "reason_code": "TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION",
        "rejection_path": "EQUATION_V3",
    }
    with pytest.raises(
        runner.Task8V26CensusError,
        match="^TASK8_V26_CENSUS_REQUIREMENT_MATRIX_INVALID$",
    ):
        runner.validate_requirement_matrix_result_rows(
            rows,
            expected_application_role="app_role",
        )


def test_v26_legal_requirement_vectors_only_observe_the_committed_production_graph() -> None:
    runner = _sql()
    matrix = inspect.getsource(runner._run_dashboard_requirement_matrix)
    case = inspect.getsource(runner._execute_requirement_case)
    seed = inspect.getsource(runner._build_requirement_seed_spec)
    assert "_observe_legal_requirement_cases" in matrix
    assert 'if vector["accepted"] is False' in matrix
    assert "if expected_accepted" in case
    assert "TASK8_V26_CENSUS_REQUIREMENT_FIXTURE_SCOPE_INVALID" in case
    assert 'vector.get("accepted") is not False' in seed


def test_v26_undeclared_calculation_uses_named_production_graph_then_one_valid_root() -> None:
    runner = _sql()
    case = inspect.getsource(runner._execute_requirement_case)
    helper = inspect.getsource(runner._attach_undeclared_calculation_root)
    assert 'case_id in {"CROSS_VERSION_JOIN", "UNDECLARED_FDD_CALCULATION_ROOT"}' in case
    assert "_persist_bound_runtime_graph" in case
    assert "_attach_undeclared_calculation_root" in case
    assert "_persist_seed_native_root" in helper
    assert "deterministic_calculations" in helper
    assert "calc_sanads" in helper
    assert "SET CONSTRAINTS ALL IMMEDIATE" in helper
    assert "_verify_seed_native_roots" in helper


def test_v26_global_99_case_reaches_the_installed_guard_after_valid_partial_seed() -> None:
    """A setup rejection is not evidence for the reviewed 99-row guard case."""

    runner = _sql()
    execution = inspect.getsource(runner._execute_behavior_case)
    dispatcher = inspect.getsource(runner._seed_dashboard_batch)
    partial = inspect.getsource(runner._persist_partial_dashboard_runtime_graph)
    task5 = inspect.getsource(runner._persist_task5_seed_plan)
    assert "_persist_partial_dashboard_runtime_graph" in dispatcher
    assert "global_dashboard_plan.seed_coverage_row_count" in execution
    assert "_insert_coverage_record" in task5
    assert "SET CONSTRAINTS ALL IMMEDIATE" in task5
    assert "_insert_dashboard_batch" in execution
    assert "ctrg_v3_dashboard_root_equation" in execution
    assert "guard_reached" in execution
    assert "TASK8_V26_CENSUS_COVERAGE_ROW_COUNT_INVALID" not in partial


def test_v26_partial_99_plan_is_an_explicit_exact_delta_from_normative_100() -> None:
    runner = _sql()
    rows = [
        {
            "coverage_record_id": f"mc_{ordinal:024d}",
            "methodology_type": "commercial_dd" if ordinal <= 21 else "financial_dd",
            "required_calculation_count": 1 if 22 <= ordinal <= 31 else 0,
            "coverage_ordinal": ordinal,
        }
        for ordinal in range(1, 101)
    ]
    payload = {
        "schema_version": "task8-v26-dashboard-seed-plan-v2",
        "coverage_row_count": 100,
        "commercial_row_count": 21,
        "financial_row_count": 79,
        "required_calculation_count": 10,
        "row_tuple": rows,
    }
    full = {**payload, "expected_sha256": runner._canonical_sha256(payload)}
    partial = runner._partial_dashboard_seed_plan(
        full,
        coverage_row_count=99,
        financial_coverage_row_count=78,
    )
    assert partial["coverage_row_count"] == 99
    assert partial["commercial_row_count"] == 21
    assert partial["financial_row_count"] == 78
    assert sum(row["required_calculation_count"] for row in partial["row_tuple"]) == 10
    omitted = {row["coverage_record_id"] for row in rows} - {
        row["coverage_record_id"] for row in partial["row_tuple"]
    }
    assert len(omitted) == 1


def test_v26_task5_roundtrip_binding_rejects_any_reloaded_byte_drift() -> None:
    runner = _sql()
    identities, selection, projections = _task5_roundtrip_fixture(runner)
    question_projection, version_projection, selection_projection = projections
    receipt = runner.build_task5_roundtrip_binding(
        planned_registry_identity_tuple=identities,
        reloaded_registry_identity_tuple=deepcopy(identities),
        planned_selection_identity=selection,
        reloaded_selection_identity=deepcopy(selection),
        app_observed_question_projection_sha256=question_projection,
        app_observed_version_projection_sha256=version_projection,
        app_observed_selection_projection_sha256=selection_projection,
        selection_immutability_sha256=_task5_selection_immutability_sha256(),
    )
    assert receipt["schema_version"] == "task8-v26-task5-roundtrip-binding-v2"
    assert receipt["registry_roundtrip_equal"] is True
    assert receipt["selection_roundtrip_equal"] is True
    assert receipt["question_projection_sha256"] == question_projection
    assert receipt["version_projection_sha256"] == version_projection
    assert receipt["selection_projection_sha256"] == selection_projection
    assert len(receipt["expected_sha256"]) == 64

    drifted = list(deepcopy(identities))
    drifted[1]["required_calculation_tuple_sha256"] = "0" * 64
    with pytest.raises(
        runner.Task8V26CensusError,
        match="^TASK8_V26_CENSUS_TASK5_ROUNDTRIP_INVALID$",
    ):
        runner.build_task5_roundtrip_binding(
            planned_registry_identity_tuple=identities,
            reloaded_registry_identity_tuple=tuple(drifted),
            planned_selection_identity=selection,
            reloaded_selection_identity=selection,
            app_observed_question_projection_sha256=question_projection,
            app_observed_version_projection_sha256=version_projection,
            app_observed_selection_projection_sha256=selection_projection,
            selection_immutability_sha256=_task5_selection_immutability_sha256(),
        )

    invalid_family = list(deepcopy(identities))
    invalid_family[1]["source_type"] = "checked_in_template"
    with pytest.raises(
        runner.Task8V26CensusError,
        match="^TASK8_V26_CENSUS_TASK5_ROUNDTRIP_INVALID$",
    ):
        runner.build_task5_roundtrip_binding(
            planned_registry_identity_tuple=invalid_family,
            reloaded_registry_identity_tuple=deepcopy(invalid_family),
            planned_selection_identity=selection,
            reloaded_selection_identity=selection,
            app_observed_question_projection_sha256=question_projection,
            app_observed_version_projection_sha256=version_projection,
            app_observed_selection_projection_sha256=selection_projection,
            selection_immutability_sha256=_task5_selection_immutability_sha256(),
        )

    with pytest.raises(
        runner.Task8V26CensusError,
        match="^TASK8_V26_CENSUS_TASK5_ROUNDTRIP_INVALID$",
    ):
        runner.build_task5_roundtrip_binding(
            planned_registry_identity_tuple=identities,
            reloaded_registry_identity_tuple=identities,
            planned_selection_identity=selection,
            reloaded_selection_identity=selection,
            app_observed_question_projection_sha256="d" * 64,
            app_observed_version_projection_sha256=version_projection,
            app_observed_selection_projection_sha256=selection_projection,
            selection_immutability_sha256=_task5_selection_immutability_sha256(),
        )


def test_v26_task5_roundtrip_rejects_missing_extra_and_order_drift() -> None:
    runner = _sql()
    identities, selection, projections = _task5_roundtrip_fixture(runner)
    question_projection, version_projection, selection_projection = projections
    mutations = (
        identities[:1],
        (*identities, {**identities[1], "methodology_type": "extra"}),
        tuple(reversed(identities)),
    )
    for observed in mutations:
        with pytest.raises(
            runner.Task8V26CensusError,
            match="^TASK8_V26_CENSUS_TASK5_ROUNDTRIP_INVALID$",
        ):
            runner.build_task5_roundtrip_binding(
                planned_registry_identity_tuple=identities,
                reloaded_registry_identity_tuple=observed,
                planned_selection_identity=selection,
                reloaded_selection_identity=selection,
                app_observed_question_projection_sha256=question_projection,
                app_observed_version_projection_sha256=version_projection,
                app_observed_selection_projection_sha256=selection_projection,
                selection_immutability_sha256=_task5_selection_immutability_sha256(),
            )

    for invalid_selection in (
        {key: value for key, value in selection.items() if key != "immutable"},
        {**selection, "unknown": True},
        {**selection, "selected_question_count": 99},
    ):
        with pytest.raises(
            runner.Task8V26CensusError,
            match="^TASK8_V26_CENSUS_TASK5_ROUNDTRIP_INVALID$",
        ):
            runner.build_task5_roundtrip_binding(
                planned_registry_identity_tuple=identities,
                reloaded_registry_identity_tuple=identities,
                planned_selection_identity=invalid_selection,
                reloaded_selection_identity=deepcopy(invalid_selection),
                app_observed_question_projection_sha256=question_projection,
                app_observed_version_projection_sha256=version_projection,
                app_observed_selection_projection_sha256=selection_projection,
                selection_immutability_sha256=_task5_selection_immutability_sha256(),
            )


def test_v26_task5_db_projection_binds_full_reloaded_registry_and_selection() -> None:
    runner = _sql()
    persistence = inspect.getsource(runner._persist_task5_seed_plan)
    identity = inspect.getsource(runner._methodology_registry_roundtrip_identity)
    for authority in (
        "methodology_question_payload_hashes",
        "methodology_source_trace_digest",
        "methodology_version_hash",
        "to_deterministic_json",
        "load_selected_registries",
        "required_calculation_tuple_sha256",
        "app_observed_version_projection_sha256",
        "app_observed_selection_projection_sha256",
        "build_task5_roundtrip_binding",
    ):
        assert authority in persistence + identity


def test_v26_task5_selection_immutability_is_installed_and_behaviorally_proven() -> None:
    runner = _sql()
    authority = runner._task5_selection_guard_authority(json.loads(_artifact_raw()))
    assert authority["upgrade_statement_sha256"] == (
        "564ea8d682639b79cf12ef4f4e96b634deffc9facf3f4a354354079c5975ee47"
    )
    assert authority["semantic_body_sha256"] == (
        "56083eb83dadb9d1ea7855af2f0f964ae01222f623e70bf548498d598ac1a763"
    )
    assert authority["proconfig_tuple"] == ["search_path=pg_catalog, pg_temp"]
    assert authority["trigger_tuple"] == [
        "public.run_methodology_selections",
        "trg_run_methodology_selections_guard",
        "BEFORE",
        ["INSERT", "UPDATE", "DELETE"],
        "ROW",
        "O",
        False,
        False,
    ]
    observer = inspect.getsource(runner._observe_task5_selection_immutability)
    persistence = inspect.getsource(runner._persist_task5_seed_plan)
    for required in (
        "pg_catalog.pg_proc",
        "pg_catalog.pg_trigger",
        "_attest_connection_role_posture",
        "SAVEPOINT task8_v26_task5_immutability",
        "UPDATE public.run_methodology_selections",
        "DELETE FROM public.run_methodology_selections",
        "RUN_METHODOLOGY_SELECTION_IMMUTABLE",
        "zero_mutation",
    ):
        assert required in observer
    assert "_observe_task5_selection_immutability" in persistence
    assert '"immutable": True' not in persistence
    assert "selection_immutability_sha256" in inspect.getsource(
        runner.build_task5_roundtrip_binding
    )


def test_v26_task5_roundtrip_rejects_every_field_and_row_shape_drift() -> None:
    runner = _sql()
    identities, selection, projections = _task5_roundtrip_fixture(runner)
    question_projection, version_projection, selection_projection = projections

    def changed(value: object) -> object:
        if type(value) is bool:
            return not value
        if type(value) is int:
            return value + 1
        if value is None:
            return "drift"
        if isinstance(value, str):
            return value + "-drift"
        if isinstance(value, list):
            return [*deepcopy(value), "drift"]
        raise AssertionError(type(value))

    def reject(
        reloaded_identities: object,
        reloaded_selection: object = selection,
    ) -> None:
        with pytest.raises(
            runner.Task8V26CensusError,
            match="^TASK8_V26_CENSUS_TASK5_ROUNDTRIP_INVALID$",
        ):
            runner.build_task5_roundtrip_binding(
                planned_registry_identity_tuple=identities,
                reloaded_registry_identity_tuple=reloaded_identities,
                planned_selection_identity=selection,
                reloaded_selection_identity=reloaded_selection,
                app_observed_question_projection_sha256=question_projection,
                app_observed_version_projection_sha256=version_projection,
                app_observed_selection_projection_sha256=selection_projection,
                selection_immutability_sha256=_task5_selection_immutability_sha256(),
            )

    for registry_index, identity in enumerate(identities):
        for field in runner._TASK5_REGISTRY_IDENTITY_FIELDS:
            mutated = list(deepcopy(identities))
            mutated[registry_index][field] = changed(identity[field])
            reject(tuple(mutated))
        for question_field_index in range(len(runner._TASK5_QUESTION_IDENTITY_FIELDS)):
            mutated = list(deepcopy(identities))
            row = mutated[registry_index]["question_identity_tuple"][0]
            row[question_field_index] = changed(row[question_field_index])
            mutated[registry_index]["question_identity_tuple_sha256"] = (
                runner._canonical_sha256(mutated[registry_index]["question_identity_tuple"])
            )
            reject(tuple(mutated))
        for shape in ("missing", "extra", "reordered"):
            mutated = list(deepcopy(identities))
            rows = mutated[registry_index]["question_identity_tuple"]
            if shape == "missing":
                rows.pop()
            elif shape == "extra":
                rows.append(deepcopy(rows[-1]))
            else:
                rows[0], rows[1] = rows[1], rows[0]
            mutated[registry_index]["question_identity_tuple_sha256"] = (
                runner._canonical_sha256(rows)
            )
            reject(tuple(mutated))

    for field in runner._TASK5_SELECTION_IDENTITY_FIELDS:
        mutated_selection = deepcopy(selection)
        mutated_selection[field] = changed(selection[field])
        reject(identities, mutated_selection)

    for registry_index, row_index in ((0, 20), (1, 0)):
        requirements = identities[registry_index]["question_identity_tuple"][row_index][18]
        for requirement_index, requirement in enumerate(requirements):
            for field in tuple(requirement):
                mutated = list(deepcopy(identities))
                target = mutated[registry_index]["question_identity_tuple"][row_index][18][
                    requirement_index
                ]
                target[field] = changed(target[field])
                mutated[registry_index]["question_identity_tuple_sha256"] = (
                    runner._canonical_sha256(mutated[registry_index]["question_identity_tuple"])
                )
                mutated[registry_index]["required_calculation_tuple"] = [
                    [row[1], row[18]]
                    for row in mutated[registry_index]["question_identity_tuple"]
                ]
                mutated[registry_index]["required_calculation_tuple_sha256"] = (
                    runner._canonical_sha256(
                        mutated[registry_index]["required_calculation_tuple"]
                    )
                )
                reject(tuple(mutated))

        base_requirement = requirements[0]
        requirement_shapes: list[dict[str, object]] = [
            {key: value for key, value in base_requirement.items() if key != next(iter(base_requirement))},
            {**base_requirement, "unknown": "forbidden"},
        ]
        for field, value in base_requirement.items():
            wrong_type = list(value) if isinstance(value, str) else str(value)
            requirement_shapes.append({**base_requirement, field: wrong_type})
        for replacement in requirement_shapes:
            mutated = list(deepcopy(identities))
            target_row = mutated[registry_index]["question_identity_tuple"][row_index]
            target_row[18] = [replacement]
            replacement_payload_sha256 = runner._canonical_sha256(
                ["task8-v26-mutated-question-payload-v1", target_row[:16], target_row[18]]
            )
            target_row[16] = replacement_payload_sha256
            target_row[17] = replacement_payload_sha256
            mutated[registry_index]["methodology_question_payload_hashes"] = [
                row[16] for row in mutated[registry_index]["question_identity_tuple"]
            ]
            mutated[registry_index]["question_identity_tuple_sha256"] = (
                runner._canonical_sha256(mutated[registry_index]["question_identity_tuple"])
            )
            mutated[registry_index]["required_calculation_tuple"] = [
                [row[1], row[18]]
                for row in mutated[registry_index]["question_identity_tuple"]
            ]
            mutated[registry_index]["required_calculation_tuple_sha256"] = (
                runner._canonical_sha256(mutated[registry_index]["required_calculation_tuple"])
            )
            reject(tuple(mutated))

    reordered = list(deepcopy(identities))
    reordered[1]["required_calculation_tuple"][0], reordered[1][
        "required_calculation_tuple"
    ][1] = (
        reordered[1]["required_calculation_tuple"][1],
        reordered[1]["required_calculation_tuple"][0],
    )
    reordered[1]["required_calculation_tuple_sha256"] = runner._canonical_sha256(
        reordered[1]["required_calculation_tuple"]
    )
    reject(tuple(reordered))


def test_v26_admin_is_schema_only_and_all_fixture_dml_is_role_attested() -> None:
    runner = _sql()
    setup = inspect.getsource(runner._setup_disposable_schema)
    grants = inspect.getsource(runner._grant_and_verify_application_schema_privileges)
    seed_scope = inspect.getsource(runner._seed_scope)
    seed_source = inspect.getsource(runner._stage_and_freeze_source_graph)
    seed_erasure = inspect.getsource(runner._seed_erasure_request)
    runner_source = SQL_RUNNER.read_text(encoding="utf-8")

    assert "_grant_and_verify_application_schema_privileges" in setup
    assert "_migration_privilege_catalog_is_exact" in grants
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" in grants
    assert "application_url" in inspect.signature(runner._seed_scope).parameters
    assert "expected_application_role" in inspect.signature(runner._seed_scope).parameters
    assert "application_url" in inspect.signature(runner._seed_erasure_request).parameters
    assert "expected_application_role" in inspect.signature(
        runner._seed_erasure_request
    ).parameters
    assert "admin_url" not in inspect.signature(
        runner._stage_and_freeze_source_graph
    ).parameters
    assert "_attest_connection_role_posture" in seed_scope
    assert "_attest_connection_role_posture" in seed_source
    assert "_attest_connection_role_posture" in seed_erasure
    assert "_set_tenant" in seed_scope
    assert "set_tenant_local" in seed_source
    assert "_set_tenant" in seed_erasure
    assert "_record_fixture_dml_role" in seed_scope + seed_source + seed_erasure
    assert "_seed_scope(urls.admin" not in runner_source
    assert "_seed_erasure_request(urls.admin" not in runner_source
    assert "create_engine(admin_url)" not in seed_source


def test_v26_receipt_binds_zero_admin_fixture_dml_from_role_attestations() -> None:
    runner = _sql()
    execution_source = inspect.getsource(runner._run_database_execution)
    validator_source = inspect.getsource(runner.validate_census_receipt)
    assert "_fixture_dml_role_rows" in execution_source
    assert '"admin_dml_case_count"' in execution_source + validator_source
    assert '"application_seed_dml_count"' in execution_source + validator_source
    assert "seed_dml_role_attestation_sha256" in execution_source
    assert "seed_dml_rows" in execution_source


def test_v26_complete_execution_digest_commits_every_central_evidence_section() -> None:
    runner = _sql()
    expected_fields = (
        "case_result_sha256",
        "catalog_projection_sha256",
        "race_result_sha256",
        "rls_behavior_sha256",
        "partial_authority_udu_sha256",
        "erasure_authority_udu_sha256",
        "dashboard_seed_plan_sha256",
        "dashboard_seed_db_projection_sha256",
        "task5_roundtrip_sha256",
        "seed_fk_closure_sha256",
        "seed_native_helper_equality_sha256",
        "source_freeze_sha256",
        "main_dashboard_census_sha256",
        "requirement_matrix_sha256",
        "requirement_predicate_sha256",
    )
    assert runner.COMPLETE_EXECUTION_SHA256_FIELDS == expected_fields
    base = {field: hashlib.sha256(field.encode("ascii")).hexdigest() for field in expected_fields}
    original = runner.complete_execution_sha256(base)
    for field in expected_fields:
        changed = dict(base)
        changed[field] = hashlib.sha256((field + "-changed").encode("ascii")).hexdigest()
        assert runner.complete_execution_sha256(changed) != original, field
    expected_preimage = [
        "task8-v26-census-complete-execution-v2",
        [[field, base[field]] for field in expected_fields],
    ]
    assert original == runner._canonical_sha256(expected_preimage)
    with pytest.raises(
        runner.Task8V26CensusError,
        match="^TASK8_V26_CENSUS_EXECUTION_DIGEST_INVALID$",
    ):
        runner.complete_execution_sha256(
            {key: value for key, value in base.items() if key != "task5_roundtrip_sha256"}
        )
    with pytest.raises(
        runner.Task8V26CensusError,
        match="^TASK8_V26_CENSUS_EXECUTION_DIGEST_INVALID$",
    ):
        runner.complete_execution_sha256({**base, "non_evidence_field": "rejected"})
    producer = inspect.getsource(runner._run_database_execution)
    validator = inspect.getsource(runner.validate_census_receipt)
    for source in (producer, validator):
        assert "complete_execution_sha256(" in source
        assert "_complete_execution_evidence(execution)" in source


def test_v26_fk_manifest_and_actual_catalog_inventory_are_runtime_bound() -> None:
    runner = _sql()
    artifact = json.loads(_artifact_raw())
    manifest = runner.build_seed_fk_closure_manifest(artifact)
    assert manifest["seed_write_relation_count"] == 36
    assert len(manifest["seed_write_relation_tuple"]) == 36
    assert manifest["seed_write_relation_sha256"] == (
        "0ef6453642b8f475ba4b724da27af34a1cf68387e959e00fb01625fa8c58b79b"
    )
    assert manifest["seed_fk_relation_closure_count"] == 37
    assert len(manifest["seed_fk_relation_closure_tuple"]) == 37
    assert manifest["seed_fk_relation_closure_sha256"] == (
        "dfbf294032fb6ffa4ec99d51996c4863a34dd9976c15c2a19be1f417974c475c"
    )
    assert {
        "deterministic_calculations",
        "calc_sanads",
        "methodology_truth_dashboard_rows",
        "methodology_truth_dashboard_root_links",
    } <= set(manifest["seed_write_relation_tuple"])
    assert len(manifest["migration_lf_sha256_tuple"]) == 38
    assert manifest["migration_lf_sha256_tuple"][-3:] == [
        [
            "src/idis/persistence/migrations/versions/0036_durable_methodology_selection.py",
            "b3f69b79b6ffb1a827900670d50f4d2dc3813ef0e40a81604f70b6c756ba8fb5",
        ],
        [
            "src/idis/persistence/migrations/versions/0037_durable_methodology_extraction.py",
            "41faa2cfc3f37ae008aea39d6ad7cd1ce46013266cd747990b84e6b403271794",
        ],
        [
            "src/idis/persistence/migrations/versions/0038_full_strict_v16_receipts_and_edges.py",
            "37ba0627fc9bccc13dc332e1540a74f09d9fb0ef557bc1336e88e6765c3ca4ce",
        ],
    ]
    assert manifest["migration_lf_sha256_tuple_sha256"] == (
        "67d6048ed049b328e0fe08e53f3faed8056ef91f0644af33eb493a32c6ae9813"
    )
    assert manifest["expected_foreign_key_inventory_count"] == 92
    assert manifest["expected_foreign_key_inventory_sha256"] == (
        "655a8654badf793301af4cc08683d22daa5517b5718e540d7d6ad11db494c7c3"
    )
    assert len(manifest["expected_foreign_key_inventory_tuple"]) == 92
    observer = inspect.getsource(runner._observe_seed_fk_closure)
    for required in (
        "closure_manifest",
        "pg_catalog.pg_constraint",
        "conkey",
        "confkey",
        "relation_inventory_sha256",
        "foreign_key_inventory_sha256",
        "foreign_key_inventory_count",
        "confupdtype",
        "confdeltype",
        "confmatchtype",
        "closure_manifest_sha256",
        "session_replication_role",
    ):
        assert required in observer
    execution = inspect.getsource(runner._run_main_dashboard_census)
    assert "build_seed_fk_closure_manifest" in execution
    assert "closure_manifest=" in execution


def test_v26_every_evidence_connection_rejects_replica_role() -> None:
    runner = _sql()

    class Cursor:
        def __init__(self, row: tuple[object, ...]) -> None:
            self.row = row

        def execute(self, _sql: str) -> None:
            return None

        def fetchone(self) -> tuple[object, ...]:
            return self.row

    posture = runner._attest_connection_role_posture(
        Cursor(("app", "app", False, False, "origin")), "app"
    )
    assert posture == {
        "current_user": "app",
        "session_user": "app",
        "rolsuper": False,
        "rolbypassrls": False,
        "session_replication_role": "origin",
    }
    assert len(runner._connection_posture_sha256(posture)) == 64
    with pytest.raises(
        runner.Task8V26CensusError,
        match="^TASK8_V26_CENSUS_ROLE_POSTURE_INVALID$",
    ):
        runner._attest_connection_role_posture(
            Cursor(("app", "app", False, False, "replica")), "app"
        )
    helper = inspect.getsource(runner._attest_connection_role_posture)
    assert "current_setting('session_replication_role')" in helper
    source = SQL_RUNNER.read_text(encoding="utf-8")
    for forbidden in (
        "SET LOCAL session_replication_role",
        "SET session_replication_role",
        "set_config('session_replication_role'",
        "DISABLE TRIGGER",
    ):
        assert forbidden not in source


def test_v26_execution_binds_seed_plan_and_app_observed_closure_receipts() -> None:
    runner = _sql()
    execution = inspect.getsource(runner._run_database_execution)
    validation = inspect.getsource(runner.validate_census_receipt)
    for field in (
        "dashboard_seed_plan_sha256",
        "dashboard_seed_db_projection_sha256",
        "seed_fk_closure_sha256",
        "seed_native_helper_equality_sha256",
        "requirement_matrix_sha256",
        "requirement_legal_case_count",
        "requirement_negative_case_count",
        "requirement_negative_zero_mutation_count",
    ):
        assert field in execution
        assert field in validation


def test_v26_native_and_fk_closure_manifests_are_complete() -> None:
    runner = _sql()
    native = runner.build_seed_native_output_manifest()
    assert set(native["output_kind_tuple"]) == {
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
    closure = runner.build_seed_fk_closure_manifest(json.loads(_artifact_raw()))
    assert closure["session_replication_role_bypass_count"] == 0
    assert closure["expected_fk_violation_count"] == 0
    assert closure["observer_role"] == "application"
    assert {
        "methodology_versions",
        "run_methodology_selections",
        "documents",
        "document_spans",
        "methodology_extraction_attempts",
        "claims",
        "evidence_items",
        "sanads",
        "defects",
        "run_output_objects",
        "run_step_outputs",
    } <= set(closure["seed_fk_relation_closure_tuple"])
    assert closure["seed_write_relation_count"] == 36
    assert closure["seed_fk_relation_closure_count"] == 37
    assert closure["expected_foreign_key_inventory_count"] == 92


def test_v26_every_observation_uses_shared_least_privilege_posture_attestation() -> None:
    runner = _sql()
    source = SQL_RUNNER.read_text(encoding="utf-8")
    helper = inspect.getsource(runner._attest_connection_role_posture)
    assert source.count("SELECT current_user,session_user") == 1
    assert "rolsuper" in helper and "rolbypassrls" in helper
    for function in (
        runner._tenant_deal_projection,
        runner._run_rls_behavior_matrix,
        runner._execute_behavior_case,
        runner._catalog_projection,
        runner._run_lock_races,
        runner._observe_authority_catalog,
        runner._observe_seed_fk_closure,
        runner._run_database_execution,
    ):
        assert "_attest_connection_role_posture" in inspect.getsource(function)


def test_v26_database_lifecycle_drops_only_a_database_created_by_this_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _sql()
    events: list[str] = []

    monkeypatch.setattr(runner, "_create_disposable_database", lambda *_: events.append("CREATE"))
    monkeypatch.setattr(runner, "_setup_disposable_schema", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(runner, "_run_database_execution", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(runner, "build_v26_dashboard_seed_plan", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(runner, "validate_census_receipt", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_drop_disposable_database", lambda *_: events.append("DROP"))
    receipt = runner.run_disposable_postgres_census(
        "postgresql://admin@localhost/base",
        "postgresql://app@localhost/base",
        "postgresql://eraser@localhost/base",
        _artifact_raw(),
        _fixture_raw(),
        PARENT_V21.read_bytes(),
        POLICY_V2.read_bytes(),
        REVIEWER_MAP.read_bytes(),
        b"operator-workbook-held-by-test-double",
    )
    assert events == ["CREATE", "DROP"]
    assert receipt["disposable_database_dropped"] is True

    events.clear()
    monkeypatch.setattr(
        runner,
        "_create_disposable_database",
        lambda *_: (_ for _ in ()).throw(RuntimeError("create failed")),
    )
    with pytest.raises(RuntimeError, match="create failed"):
        runner.run_disposable_postgres_census(
            "postgresql://admin@localhost/base",
            "postgresql://app@localhost/base",
            "postgresql://eraser@localhost/base",
            _artifact_raw(),
            _fixture_raw(),
            PARENT_V21.read_bytes(),
            POLICY_V2.read_bytes(),
            REVIEWER_MAP.read_bytes(),
            b"operator-workbook-held-by-test-double",
        )
    assert events == []


def test_v26_runner_import_is_hermetic_and_has_no_profile_or_environment_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("os.getenv", lambda key, default=None: calls.append(key) or default)
    _sql()
    assert calls == []
    source = SQL_RUNNER.read_text(encoding="utf-8")
    assert "load_strict_runtime_profile" not in source
    assert "os.environ" not in source
    assert "IDIS_STRICT_DOTENV_PATH" not in source


def test_v26_input_digests_are_exact_and_not_self_affirmed() -> None:
    assert hashlib.sha256(_artifact_raw()).hexdigest() == ARTIFACT_SHA256
    assert hashlib.sha256(_fixture_raw()).hexdigest() == FIXTURE_SHA256
    assert hashlib.sha256(PARENT_V21.read_bytes()).hexdigest() == PARENT_ARTIFACT_SHA256
    assert hashlib.sha256(POLICY_V2.read_bytes()).hexdigest() == POLICY_ARTIFACT_SHA256
    assert hashlib.sha256(REVIEWER_MAP.read_bytes()).hexdigest() == REVIEWER_MAP_SHA256

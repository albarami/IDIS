"""RED-first admission contracts for the bounded full-strict-v14 successor."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from tests.slice100_task7 import _void_contract_hash_evidence as void_evidence

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V13_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v13_reviewed_preimage_v1.json"
_V14_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v14_reviewed_preimage_v1.json"
_V14_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v14_preimage.py"
_V14_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v14_contract_fixture.py"
_V14_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v14_contract_hash_v1.json"
)
_PLAN = _REPO_ROOT / "docs" / "plans" / ("2026-07-15-slice100-strict-real-example-live-dry-run.md")
_DESIGN = (
    _REPO_ROOT / "docs" / "plans" / ("2026-07-21-slice100-task7-receipt-edge-schema-design.md")
)
_RECONCILIATION = (
    _REPO_ROOT / ".local_reports" / "slice100" / "task7_contract_reconciliation_log.jsonl"
)

_TASK6_AUTHORITY_ID = "current-task6-immutable-replacement-v1"
_TASK6_AUTHORITY_ORDINAL = 27
_TASK6_HELPER_SIGNATURE = (
    "public.idis_task6_output_root_attach_is_valid_v1(jsonb,jsonb,uuid,uuid,uuid)"
)
_TASK6_GUARD_SIGNATURE = "public.idis_task6_immutable_guard()"
_LEGAL_ATTACH_TARGETS = {
    "methodology_extraction_results": "METHODOLOGY_EXTRACTION_RESULT",
    "methodology_result_factual_outputs": "METHODOLOGY_FACTUAL_OUTPUT",
}
_GENERATED_FIELDS = ("run_output_object_class", "run_output_object_kind")

_SEMANTIC_INVARIANT_SECTIONS = (
    "canonicalization_contract",
    "tuple_schema_registry",
    "uuid_namespace_registry",
    "schema_registry",
    "semantic_step_registry",
    "current_ddl_registry",
    "baseline_parent_authority_registry",
    "future_native_authority_registry",
    "future_reference_authority_registry",
    "future_child_authority_registry",
    "erasure_execution_registry",
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


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _task6_authority(artifact: dict[str, Any]) -> dict[str, Any]:
    return next(
        row
        for row in artifact["sql_authority_registry"]["rows"]
        if row["authority_id"] == _TASK6_AUTHORITY_ID
    )


def _task6_body(artifact: dict[str, Any]) -> str:
    row = _task6_authority(artifact)
    assert len(row["body_sql_utf8_hex_tuple"]) == 1
    return bytes.fromhex(row["body_sql_utf8_hex_tuple"][0]).decode("utf-8")


def _mutated_task6_body(
    artifact: dict[str, Any],
    needle: str,
    replacement: str,
) -> dict[str, Any]:
    mutated = copy.deepcopy(artifact)
    row = _task6_authority(mutated)
    body = _task6_body(mutated)
    assert needle in body
    row["body_sql_utf8_hex_tuple"][0] = body.replace(needle, replacement, 1).encode().hex()
    return mutated


def test_v13_candidate_identifier_is_permanently_blocklisted_under_v14() -> None:
    identifiers = {
        void_evidence.VOID_V13_ARTIFACT_SHA256,
        void_evidence.VOID_V13_PROSPECTIVE_FROZEN_ARTIFACT_SHA256,
        void_evidence.VOID_V13_PROSPECTIVE_CONTRACT_SHA256,
    }
    assert identifiers.issubset(set(void_evidence.VOID_CONTRACT_HASHES))


def test_v14_assets_are_new_unfrozen_versioned_files() -> None:
    for path in (_V14_ARTIFACT, _V14_MATERIALIZER, _V14_GENERATOR):
        assert path.is_file(), path
    artifact = _load_json(_V14_ARTIFACT)
    assert artifact["metadata"] == {
        **artifact["metadata"],
        "artifact_schema": "full-strict-v14-reviewed-preimage-v1",
        "append_frozen": False,
        "pipeline_contract_version": "full-strict-v14",
    }
    assert not _V14_FIXTURE.exists()


def test_v14_exact_successor_changes_only_task6_guard_and_dependent_hashes() -> None:
    materializer = _load_module(_V14_MATERIALIZER, "_task7_v14_exact_diff_red")
    v13 = _load_json(_V13_ARTIFACT)
    v14 = _load_json(_V14_ARTIFACT)

    assert materializer.project_v14_to_v13(v14) == v13
    assert materializer._render(materializer.build_v14(v13)) == _V14_ARTIFACT.read_bytes()
    changed = materializer._changed_paths(v13, v14)
    assert changed
    assert all(
        path[:1] in {("metadata",), ("digest_recipe_registry",), ("sql_authority_registry",)}
        for path in changed
    )
    changed_authorities = {
        path[2]
        for path in changed
        if path[:2] == ("sql_authority_registry", "rows") and len(path) > 2
    }
    assert changed_authorities == {_TASK6_AUTHORITY_ORDINAL}
    assert (
        v14["sql_authority_registry"]["guard_function_acl_registry"]
        == (v13["sql_authority_registry"]["guard_function_acl_registry"])
    )
    for section in _SEMANTIC_INVARIANT_SECTIONS:
        assert v14[section] == v13[section], section


def test_v14_preserves_task6_helper_and_acl_but_normalizes_guard_call_site() -> None:
    generator = _load_module(_V14_GENERATOR, "_task7_v14_helper_stability_red")
    v13 = _load_json(_V13_ARTIFACT)
    v14 = _load_json(_V14_ARTIFACT)

    assert generator._function_sql(v14, _TASK6_HELPER_SIGNATURE) == (
        generator._function_sql(v13, _TASK6_HELPER_SIGNATURE)
    )
    v13_helper_acl = next(
        row
        for row in v13["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"]
        if row["function_signature"] == _TASK6_HELPER_SIGNATURE
    )
    v14_helper_acl = next(
        row
        for row in v14["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"]
        if row["function_signature"] == _TASK6_HELPER_SIGNATURE
    )
    assert v14_helper_acl == v13_helper_acl

    coverage = generator.task6_target_literal_normalization_coverage(v14)
    assert coverage == tuple(
        {
            "authority_id": _TASK6_AUTHORITY_ID,
            "function_signature": _TASK6_GUARD_SIGNATURE,
            "table_name": table,
            "generated_column_tuple": list(_GENERATED_FIELDS),
            "literal_class": "NATIVE_ROW",
            "literal_kind": kind,
            "mode": "PROVEN_TARGET_LITERAL_NORMALIZATION",
        }
        for table, kind in _LEGAL_ATTACH_TARGETS.items()
    )
    assert (
        tuple(
            row
            for row in generator.before_generated_column_coverage(v14)
            if row["authority_id"] == _TASK6_AUTHORITY_ID
        )
        == coverage
    )


@pytest.mark.parametrize(
    ("needle", "replacement"),
    (
        (
            "ARRAY['run_output_object_class','run_output_object_kind']::text[]",
            "ARRAY['run_output_object_class']::text[]",
        ),
        (
            "ARRAY['run_output_object_class','run_output_object_kind']::text[]",
            "ARRAY['run_output_object_class','run_output_object_kind',"
            "'run_output_object_id']::text[]",
        ),
        (
            "'run_output_object_class','NATIVE_ROW'",
            "'run_output_object_class','EXTERNAL_CONTEXT'",
        ),
        (
            "WHEN 'methodology_extraction_results' THEN 'METHODOLOGY_EXTRACTION_RESULT'",
            "WHEN 'methodology_extraction_results' THEN 'METHODOLOGY_FACTUAL_OUTPUT'",
        ),
        (
            " WHEN 'methodology_result_factual_outputs' THEN 'METHODOLOGY_FACTUAL_OUTPUT'",
            "",
        ),
        (
            ")||pg_catalog.jsonb_build_object(",
            ")-pg_catalog.jsonb_build_object(",
        ),
        (
            "(pg_catalog.to_jsonb(NEW)-ARRAY['run_output_object_class',"
            "'run_output_object_kind']::text[])||pg_catalog.jsonb_build_object("
            "'run_output_object_class','NATIVE_ROW','run_output_object_kind',"
            "CASE TG_TABLE_NAME WHEN 'methodology_extraction_results' THEN "
            "'METHODOLOGY_EXTRACTION_RESULT' WHEN "
            "'methodology_result_factual_outputs' THEN "
            "'METHODOLOGY_FACTUAL_OUTPUT' END)",
            "pg_catalog.jsonb_build_object("
            "'run_output_object_class','NATIVE_ROW','run_output_object_kind',"
            "CASE TG_TABLE_NAME WHEN 'methodology_extraction_results' THEN "
            "'METHODOLOGY_EXTRACTION_RESULT' WHEN "
            "'methodology_result_factual_outputs' THEN "
            "'METHODOLOGY_FACTUAL_OUTPUT' END)"
            "||(pg_catalog.to_jsonb(NEW)-ARRAY['run_output_object_class',"
            "'run_output_object_kind']::text[])",
        ),
        (
            "(pg_catalog.to_jsonb(NEW)-ARRAY['run_output_object_class',"
            "'run_output_object_kind']::text[])||pg_catalog.jsonb_build_object("
            "'run_output_object_class','NATIVE_ROW','run_output_object_kind',"
            "CASE TG_TABLE_NAME WHEN 'methodology_extraction_results' THEN "
            "'METHODOLOGY_EXTRACTION_RESULT' WHEN "
            "'methodology_result_factual_outputs' THEN "
            "'METHODOLOGY_FACTUAL_OUTPUT' END)",
            "pg_catalog.to_jsonb(NEW)",
        ),
        (
            "IF TG_OP='UPDATE' AND TG_TABLE_NAME IN "
            "('methodology_extraction_results','methodology_result_factual_outputs')",
            "IF TG_OP='UPDATE' AND TG_TABLE_NAME IN "
            "('methodology_extraction_tasks','methodology_extraction_results',"
            "'methodology_result_factual_outputs')",
        ),
        (
            "NEW.tenant_id,NEW.deal_id,NEW.run_id) THEN RETURN NEW; END IF;",
            "NEW.tenant_id,NEW.deal_id,NEW.run_id) OR "
            "TG_TABLE_NAME='methodology_extraction_tasks' THEN RETURN NEW; END IF;",
        ),
        (
            "THEN RETURN NEW; END IF; RAISE EXCEPTION 'METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE'",
            "AND NEW.run_output_object_kind IS NOT NULL THEN RETURN NEW; END IF; "
            "RAISE EXCEPTION 'METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE'",
        ),
        (
            "THEN RETURN NEW; END IF; RAISE EXCEPTION 'METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE'",
            "AND pg_catalog.to_jsonb(NEW) IS NOT NULL THEN RETURN NEW; END IF; "
            "RAISE EXCEPTION 'METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE'",
        ),
        (
            "THEN RETURN NEW; END IF; RAISE EXCEPTION 'METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE'",
            "AND public.idis_task7_v14_bare_record_probe(NEW) "
            "THEN RETURN NEW; END IF; RAISE EXCEPTION "
            "'METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE'",
        ),
        (
            "IF TG_OP='INSERT' THEN IF NOT",
            "IF TG_OP='INSERT' AND NEW IS DISTINCT FROM OLD THEN IF NOT",
        ),
        (
            "END IF; RETURN NEW; END IF; IF TG_OP='DELETE'",
            "END IF; NEW := OLD; RETURN NEW; END IF; IF TG_OP='DELETE'",
        ),
        (
            "END IF; RETURN NEW; END IF; IF TG_OP='DELETE'",
            "END IF; PERFORM NEW::jsonb; RETURN NEW; END IF; IF TG_OP='DELETE'",
        ),
        (
            "END IF; RETURN NEW; END IF; IF TG_OP='DELETE'",
            "END IF; PERFORM NEW; RETURN NEW; END IF; IF TG_OP='DELETE'",
        ),
    ),
)
def test_v14_admission_rejects_mutated_task6_literal_normalization(
    needle: str,
    replacement: str,
) -> None:
    generator = _load_module(_V14_GENERATOR, "_task7_v14_normalization_mutation_red")
    artifact = _load_json(_V14_ARTIFACT)
    mutated = _mutated_task6_body(artifact, needle, replacement)

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
    ):
        generator.validate_before_generated_column_access(mutated)


def test_v14_bare_new_allowlist_applies_to_every_current_before_guard() -> None:
    generator = _load_module(_V14_GENERATOR, "_task7_v14_global_bare_new_red")
    artifact = _load_json(_V14_ARTIFACT)
    row = next(
        value
        for value in artifact["sql_authority_registry"]["rows"]
        if value["authority_id"] == "current-source-bound-v1"
    )
    body = bytes.fromhex(row["body_sql_utf8_hex_tuple"][0]).decode("utf-8")
    needle = "AS $$BEGIN IF TG_OP='DELETE'"
    replacement = (
        "AS $$BEGIN IF NEW IS DISTINCT FROM OLD THEN "
        "RAISE EXCEPTION 'TASK7_TEST_COMPOSITE_NEW'; END IF; IF TG_OP='DELETE'"
    )
    assert needle in body
    row["body_sql_utf8_hex_tuple"][0] = body.replace(needle, replacement, 1).encode().hex()

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
    ):
        generator.validate_before_generated_column_access(artifact)


@pytest.mark.parametrize(
    "sql",
    (
        "BEGIN RETURN NEW; END",
        "BEGIN IF NEW.tenant_id IS NULL THEN RETURN NEW; END IF; END",
        "BEGIN PERFORM pg_catalog.to_jsonb(NEW); RETURN NEW; END",
        "BEGIN PERFORM pg_catalog.row_to_json(NEW); RETURN NEW; END",
    ),
)
def test_v14_bare_new_allowlist_accepts_only_named_contexts(sql: str) -> None:
    generator = _load_module(_V14_GENERATOR, "_task7_v14_bare_new_allowlist_green")

    assert generator._invalid_bare_new_contexts(generator._V13._sql_tokens(sql)) == ()


@pytest.mark.parametrize(
    "sql",
    (
        "BEGIN IF NEW IS DISTINCT FROM OLD THEN RETURN NEW; END IF; END",
        "BEGIN NEW := OLD; RETURN NEW; END",
        "BEGIN PERFORM NEW::jsonb; RETURN NEW; END",
        "BEGIN PERFORM NEW; RETURN NEW; END",
        "BEGIN PERFORM public.other_helper(NEW); RETURN NEW; END",
        "BEGIN RETURN NEW::jsonb; END",
        "BEGIN RETURN NEW + OLD; END",
    ),
)
def test_v14_bare_new_allowlist_rejects_every_other_context(sql: str) -> None:
    generator = _load_module(_V14_GENERATOR, "_task7_v14_bare_new_allowlist_closed")

    assert generator._invalid_bare_new_contexts(generator._V13._sql_tokens(sql))


def test_v14_semantic_vectors_event_coordinates_and_candidate_state_are_invariant() -> None:
    generator = _load_module(_V14_GENERATOR, "_task7_v14_vectors_red")
    v13 = _load_json(_V13_ARTIFACT)
    v14_raw = _V14_ARTIFACT.read_bytes()
    v14 = generator._decode_artifact(v14_raw)

    generator._validate_shape(v14_raw, v14)
    assert (
        generator.invariant_vector(v14)
        == generator.invariant_vector(v13)
        == {
            "semantic_step_count": 28,
            "edge_rule_count": 48,
            "usage_route_count": 1756,
            "usage_extractor_count": 19,
            "provider_count": 18,
            "current_ddl_count": 34,
            "materialized_physical_table_count": 72,
            "erasure_phase_count": 66,
        }
    )
    current_signatures = {
        row["function_signature"]
        for row in v14["sql_authority_registry"]["guard_function_acl_registry"]["function_rows"]
        if row["install_scope"] == "CURRENT_0038"
    }
    coordinates = tuple(
        row for row in generator.guard_target_event_authority(v14) if row[0] in current_signatures
    )
    assert len(coordinates) == 192
    assert v14["metadata"]["append_frozen"] is False
    assert not _V14_FIXTURE.exists()
    assert hashlib.sha256(v14_raw).hexdigest() != void_evidence.VOID_V13_ARTIFACT_SHA256


def test_v14_prospective_fixture_derivation_projects_to_v11_and_rejects_corruption() -> None:
    generator = _load_module(_V14_GENERATOR, "_task7_v14_fixture_derivation_red")
    candidate_raw = _V14_ARTIFACT.read_bytes()
    frozen_raw = generator.derive_prospective_frozen_bytes(candidate_raw)
    fixture = generator.derive_contract_bytes(frozen_raw, validate=True)
    frozen = generator._decode_artifact(frozen_raw)

    assert fixture["fixture_schema"] == "full-strict-v14-contract-hash-fixture-v1"
    assert fixture["pipeline_contract_version"] == "full-strict-v14"
    assert fixture["artifact_sha256"] == hashlib.sha256(frozen_raw).hexdigest()
    assert (
        fixture["component_hashes"]["sql_authority_registry_hash"]
        == (frozen["sql_authority_registry"]["expected_sha256"])
    )
    assert (
        fixture["FULL_STRICT_V14_CONTRACT_HASH"]
        == (fixture["component_hashes"]["pipeline_contract_hash"])
    )

    corrupted = copy.deepcopy(frozen)
    row = _task6_authority(corrupted)
    row["body_sql_utf8_hex_tuple"][0] += "20"
    corrupted_raw = generator._render_artifact(corrupted)
    with pytest.raises(generator.ContractArtifactError):
        generator.derive_contract_bytes(corrupted_raw, validate=True)


def test_v14_approval_void_and_autonomy_are_recorded() -> None:
    plan = _PLAN.read_text(encoding="utf-8")
    design = _DESIGN.read_text(encoding="utf-8")
    records = [
        json.loads(line)
        for line in _RECONCILIATION.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert "Approved full-strict-v14 target-literal normalization" in plan
    assert "Standing contract-materialization autonomy" in plan
    assert "Approved full-strict-v14 target-literal normalization" in design
    record = next(
        row
        for row in records
        if row["event"] == "V13_PRE_FREEZE_TASK6_GENERATED_COLUMN_CANDIDATE_VOIDED"
    )
    assert record["artifact_sha256"] == void_evidence.VOID_V13_ARTIFACT_SHA256
    assert record["disposition"] == "PRE_FREEZE_VOID_NEVER_ADMITTED"
    assert record["superseding_contract_version"] == "full-strict-v14"
    assert record["candidate_voided"] is True
    assert record["never_frozen"] is True
    assert record["never_consumed"] is True

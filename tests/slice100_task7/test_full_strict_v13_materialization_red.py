"""RED-first admission contracts for the bounded full-strict-v13 successor."""

from __future__ import annotations

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
_V12_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v12_reviewed_preimage_v1.json"
_V13_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v13_reviewed_preimage_v1.json"
_V13_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v13_preimage.py"
_V13_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v13_contract_fixture.py"
_V13_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v13_contract_hash_v1.json"
)

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

_SOURCE_TARGETS = (
    "data_room_packages",
    "data_room_package_files",
    "document_artifacts",
    "documents",
    "document_parse_attempts",
    "document_spans",
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


def _source_authority(artifact: dict[str, Any]) -> dict[str, Any]:
    return next(
        row
        for row in artifact["sql_authority_registry"]["rows"]
        if row["authority_id"] == "current-source-bound-v1"
    )


def _source_body(artifact: dict[str, Any]) -> str:
    return bytes.fromhex(_source_authority(artifact)["body_sql_utf8_hex_tuple"][0]).decode("utf-8")


def test_v12_candidate_identifiers_are_permanently_blocklisted_under_v13() -> None:
    identifiers = {
        void_evidence.VOID_V12_ARTIFACT_SHA256,
        void_evidence.VOID_V12_PROSPECTIVE_FROZEN_ARTIFACT_SHA256,
        void_evidence.VOID_V12_PROSPECTIVE_CONTRACT_SHA256,
    }
    assert identifiers.issubset(set(void_evidence.VOID_CONTRACT_HASHES))


def test_v13_assets_are_new_unfrozen_versioned_files() -> None:
    for path in (_V13_ARTIFACT, _V13_MATERIALIZER, _V13_GENERATOR):
        assert path.is_file(), path
    artifact = _load_json(_V13_ARTIFACT)
    assert artifact["metadata"] == {
        **artifact["metadata"],
        "artifact_schema": "full-strict-v13-reviewed-preimage-v1",
        "append_frozen": False,
        "pipeline_contract_version": "full-strict-v13",
    }
    assert not _V13_FIXTURE.exists()


def test_v13_exact_successor_changes_only_source_guard_and_dependent_version_hashes() -> None:
    materializer = _load_module(_V13_MATERIALIZER, "_task7_v13_exact_diff_red")
    v12 = _load_json(_V12_ARTIFACT)
    v13 = _load_json(_V13_ARTIFACT)

    assert materializer.project_v13_to_v12(v13) == v12
    assert materializer._render(materializer.build_v13(v12)) == _V13_ARTIFACT.read_bytes()
    changed = materializer._changed_paths(v12, v13)
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
    assert changed_authorities == {22}
    for section in _SEMANTIC_INVARIANT_SECTIONS:
        assert v13[section] == v12[section], section


def test_v13_retains_before_timing_and_all_192_event_coordinates() -> None:
    generator = _load_module(_V13_GENERATOR, "_task7_v13_event_invariance_red")
    artifact = _load_json(_V13_ARTIFACT)
    current_signatures = {
        row["function_signature"]
        for row in artifact["sql_authority_registry"]["guard_function_acl_registry"][
            "function_rows"
        ]
        if row["install_scope"] == "CURRENT_0038"
    }
    rows = tuple(
        row
        for row in generator.guard_target_event_authority(artifact)
        if row[0] in current_signatures
    )
    source_signature = "public.idis_task7_current_source_bound_v1()"
    source_rows = [row for row in rows if row[0] == source_signature]

    assert len(rows) == 192
    assert len(source_rows) == 12
    assert {row[1] for row in source_rows} == set(_SOURCE_TARGETS)
    assert {row[3] for row in source_rows} == {"BEFORE"}
    assert {row[4] for row in source_rows} == {"UPDATE", "DELETE"}


def test_v13_source_guard_has_exact_before_generated_column_normalization() -> None:
    artifact = _load_json(_V13_ARTIFACT)
    body = _source_body(artifact)

    package_mutable = (
        "ARRAY['status','source_binding_state','source_lineage_object_id',"
        "'source_corpus_receipt_id','source_lineage_object_kind','updated_at']::text[]"
    )
    span_mutable = (
        "ARRAY['source_binding_state','source_lineage_object_id',"
        "'source_corpus_receipt_id','source_lineage_object_kind','parse_attempt_id']::text[]"
    )
    generic_mutable = (
        "ARRAY['source_binding_state','source_lineage_object_id',"
        "'source_corpus_receipt_id','source_lineage_object_kind']::text[]"
    )
    corpus_exemption = (
        "(TG_TABLE_NAME NOT IN ('data_room_packages','data_room_package_files') "
        "AND NEW.source_corpus_receipt_id IS NULL)"
    )

    assert body.count(package_mutable) == 2
    assert body.count(span_mutable) == 2
    assert body.count(generic_mutable) == 2
    assert body.count(corpus_exemption) == 1
    assert "NEW.source_lineage_object_kind" not in body
    assert "NEW.source_corpus_receipt_id IS NULL" in body
    assert body.count("BEFORE") == 0


def test_v13_generated_columns_are_derived_only_from_current_ddl() -> None:
    generator = _load_module(_V13_GENERATOR, "_task7_v13_generated_ddl_red")
    artifact = _load_json(_V13_ARTIFACT)
    generated = generator.current_generated_column_authority(artifact)

    assert generated["data_room_packages"] == (
        "source_lineage_object_kind",
        "source_corpus_receipt_id",
    )
    assert generated["data_room_package_files"] == (
        "source_lineage_object_kind",
        "source_corpus_receipt_id",
    )
    for table in _SOURCE_TARGETS[2:]:
        assert generated[table] == ("source_lineage_object_kind",)
    generator.validate_before_generated_column_access(artifact)


@pytest.mark.parametrize(
    ("needle", "replacement"),
    (
        (
            "IF OLD.source_binding_state='BOUND'",
            "IF NEW.source_lineage_object_kind IS NULL OR OLD.source_binding_state='BOUND'",
        ),
        (
            "pg_catalog.to_jsonb(NEW)-ARRAY['source_binding_state',"
            "'source_lineage_object_id','source_corpus_receipt_id',"
            "'source_lineage_object_kind','parse_attempt_id']::text[]",
            "pg_catalog.to_jsonb(NEW)",
        ),
        (
            "'source_corpus_receipt_id','source_lineage_object_kind','updated_at'",
            "'source_lineage_object_kind','updated_at'",
        ),
    ),
)
def test_v13_admission_rejects_before_generated_column_masking(
    needle: str,
    replacement: str,
) -> None:
    generator = _load_module(_V13_GENERATOR, "_task7_v13_generated_mask_red")
    artifact = _load_json(_V13_ARTIFACT)
    row = _source_authority(artifact)
    body = _source_body(artifact)
    assert needle in body
    row["body_sql_utf8_hex_tuple"][0] = body.replace(needle, replacement, 1).encode().hex()

    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V13_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
    ):
        generator.validate_before_generated_column_access(artifact)


def test_v13_semantic_vectors_and_candidate_state_are_invariant() -> None:
    generator = _load_module(_V13_GENERATOR, "_task7_v13_vectors_red")
    v12 = _load_json(_V12_ARTIFACT)
    v13_raw = _V13_ARTIFACT.read_bytes()
    v13 = generator._decode_artifact(v13_raw)

    generator._validate_shape(v13_raw, v13)
    assert (
        generator.invariant_vector(v13)
        == generator.invariant_vector(v12)
        == {
            "step_count": 28,
            "edge_rule_count": 48,
            "usage_route_count": 1756,
            "usage_extractor_count": 19,
        }
    )
    assert v13["metadata"]["append_frozen"] is False
    assert v13["activation_assertions"]["status"] == "INACTIVE"
    assert hashlib.sha256(v13_raw).hexdigest() not in {
        void_evidence.VOID_V12_ARTIFACT_SHA256,
        void_evidence.VOID_V12_PROSPECTIVE_FROZEN_ARTIFACT_SHA256,
        void_evidence.VOID_V12_PROSPECTIVE_CONTRACT_SHA256,
    }

"""RED-first admission for the reviewed full-strict-v8 current guards."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from tests.slice100_task7 import _void_contract_hash_evidence as void_evidence

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V7_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v7_reviewed_preimage_v1.json"
_V8_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v8_reviewed_preimage_v1.json"
_V8_FIXTURE = Path(__file__).with_name("fixtures") / "full_strict_v8_contract_hash_v1.json"
_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v8_preimage.py"
_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v8_contract_fixture.py"

DEFERRED_CURRENT_TARGETS = (
    ("source_lineage_objects", "ctrg_task7_source_lineage_objects_exact_one"),
    ("data_room_packages", "ctrg_task7_data_room_packages_source_exact_one"),
    (
        "data_room_package_files",
        "ctrg_task7_data_room_package_files_source_exact_one",
    ),
    ("document_artifacts", "ctrg_task7_document_artifacts_source_exact_one"),
    ("documents", "ctrg_task7_documents_source_exact_one"),
    (
        "document_parse_attempts",
        "ctrg_task7_document_parse_attempts_source_exact_one",
    ),
    ("document_spans", "ctrg_task7_document_spans_source_exact_one"),
    ("run_output_objects", "ctrg_task7_run_output_objects_exact_one"),
    (
        "methodology_coverage_records",
        "ctrg_task7_methodology_coverage_records_output_exact_one",
    ),
    (
        "methodology_extraction_tasks",
        "ctrg_task7_methodology_extraction_tasks_output_exact_one",
    ),
    (
        "methodology_extraction_results",
        "ctrg_task7_methodology_extraction_results_output_exact_one",
    ),
    (
        "methodology_result_factual_outputs",
        "ctrg_task7_methodology_result_factual_outputs_output_exact_one",
    ),
    (
        "methodology_result_claim_links",
        "ctrg_task7_methodology_result_claim_links_output_exact_one",
    ),
    (
        "methodology_result_evidence_links",
        "ctrg_task7_methodology_result_evidence_links_output_exact_one",
    ),
    (
        "methodology_result_sanad_links",
        "ctrg_task7_methodology_result_sanad_links_output_exact_one",
    ),
    (
        "methodology_result_defect_links",
        "ctrg_task7_methodology_result_defect_links_output_exact_one",
    ),
    (
        "deterministic_calculations",
        "ctrg_task7_deterministic_calculations_output_exact_one",
    ),
    ("vector_embeddings", "ctrg_task7_vector_embeddings_output_exact_one"),
    (
        "evidence_trust_findings",
        "ctrg_task7_evidence_trust_findings_output_exact_one",
    ),
    ("muhasabah_records", "ctrg_task7_muhasabah_records_output_exact_one"),
    (
        "validated_evidence_packages",
        "ctrg_task7_validated_evidence_packages_output_exact_one",
    ),
    ("debate_sessions", "ctrg_task7_debate_sessions_output_exact_one"),
    (
        "layer2_ic_challenges",
        "ctrg_task7_layer2_ic_challenges_output_exact_one",
    ),
    ("layer2_ic_findings", "ctrg_task7_layer2_ic_findings_output_exact_one"),
    ("deliverables", "ctrg_task7_deliverables_output_exact_one"),
    ("run_steps", "ctrg_task7_run_steps_receipt_complete"),
    ("run_step_outputs", "ctrg_task7_run_step_outputs_receipt_complete"),
    ("run_output_objects", "ctrg_task7_run_output_objects_receipt_complete"),
    (
        "run_expected_consumer_edges",
        "ctrg_task7_expected_edges_decision_complete",
    ),
    (
        "run_consumer_receipts",
        "ctrg_task7_consumer_receipts_edge_complete",
    ),
    (
        "deliverable_manifest_references",
        "ctrg_task7_deliverable_manifest_references_exact_one",
    ),
    (
        "deliverable_manifest_references",
        "ctrg_task7_deliverable_manifest_references_exact_projection",
    ),
)

APPEND_ONLY_CURRENT_TARGETS = (
    ("run_input_bindings", "trg_task7_run_input_bindings_append_only"),
    ("run_output_objects", "trg_task7_run_output_objects_append_only"),
    ("run_step_outputs", "trg_task7_run_step_outputs_append_only"),
    (
        "run_applicability_decisions",
        "trg_task7_run_applicability_decisions_append_only",
    ),
    (
        "run_expected_consumer_edges",
        "trg_task7_run_expected_consumer_edges_append_only",
    ),
    ("run_consumer_receipts", "trg_task7_run_consumer_receipts_append_only"),
    (
        "deliverable_manifest_references",
        "trg_task7_deliverable_manifest_references_append_only",
    ),
)

BOUND_CURRENT_TARGETS = (
    ("data_room_packages", "trg_task7_data_room_packages_source_bound"),
    (
        "data_room_package_files",
        "trg_task7_data_room_package_files_source_bound",
    ),
    ("document_artifacts", "trg_task7_document_artifacts_source_bound"),
    ("documents", "trg_task7_documents_source_bound"),
    (
        "document_parse_attempts",
        "trg_task7_document_parse_attempts_source_bound",
    ),
    ("document_spans", "trg_task7_document_spans_source_bound"),
    (
        "methodology_coverage_records",
        "trg_task7_methodology_coverage_records_output_bound",
    ),
    (
        "methodology_extraction_tasks",
        "trg_task7_methodology_extraction_tasks_output_bound",
    ),
    (
        "methodology_extraction_results",
        "trg_task7_methodology_extraction_results_output_bound",
    ),
    (
        "methodology_result_factual_outputs",
        "trg_task7_methodology_result_factual_outputs_output_bound",
    ),
    (
        "methodology_result_claim_links",
        "trg_task7_methodology_result_claim_links_output_bound",
    ),
    (
        "methodology_result_evidence_links",
        "trg_task7_methodology_result_evidence_links_output_bound",
    ),
    (
        "methodology_result_sanad_links",
        "trg_task7_methodology_result_sanad_links_output_bound",
    ),
    (
        "methodology_result_defect_links",
        "trg_task7_methodology_result_defect_links_output_bound",
    ),
    (
        "deterministic_calculations",
        "trg_task7_deterministic_calculations_output_bound",
    ),
    ("vector_embeddings", "trg_task7_vector_embeddings_output_bound"),
    (
        "evidence_trust_findings",
        "trg_task7_evidence_trust_findings_output_bound",
    ),
    ("muhasabah_records", "trg_task7_muhasabah_records_output_bound"),
    (
        "validated_evidence_packages",
        "trg_task7_validated_evidence_packages_output_bound",
    ),
    ("debate_sessions", "trg_task7_debate_sessions_output_bound"),
    (
        "layer2_ic_challenges",
        "trg_task7_layer2_ic_challenges_output_bound",
    ),
    ("layer2_ic_findings", "trg_task7_layer2_ic_findings_output_bound"),
    ("deliverables", "trg_task7_deliverables_output_bound"),
    ("runs", "trg_task7_runs_contract_immutable"),
    ("run_steps", "trg_task7_run_steps_contract_immutable"),
)

RETAINED_CURRENT_TARGETS = (
    ("methodology_coverage_records", "trg_methodology_coverage_scope"),
    (
        "methodology_extraction_tasks",
        "trg_methodology_extraction_tasks_immutable",
    ),
    (
        "methodology_extraction_task_spans",
        "trg_methodology_extraction_task_spans_immutable",
    ),
    (
        "methodology_extraction_results",
        "trg_methodology_extraction_results_immutable",
    ),
    (
        "methodology_result_factual_outputs",
        "trg_methodology_result_factual_outputs_immutable",
    ),
    (
        "methodology_result_claim_links",
        "trg_methodology_result_claim_links_immutable",
    ),
    (
        "methodology_result_evidence_links",
        "trg_methodology_result_evidence_links_immutable",
    ),
    (
        "methodology_result_sanad_links",
        "trg_methodology_result_sanad_links_immutable",
    ),
    (
        "methodology_result_defect_links",
        "trg_methodology_result_defect_links_immutable",
    ),
)

V8_AUTHORITY_TARGETS = {
    "current-source-exact-one-v1": DEFERRED_CURRENT_TARGETS[:7],
    "current-output-exact-one-v1": DEFERRED_CURRENT_TARGETS[7:25],
    "current-receipt-equality-v1": DEFERRED_CURRENT_TARGETS[25:],
    "current-input-binding-admission-v1": APPEND_ONLY_CURRENT_TARGETS[:1],
    "current-append-only-lease-v1": APPEND_ONLY_CURRENT_TARGETS[1:],
    "current-source-bound-v1": BOUND_CURRENT_TARGETS[:6],
    "current-output-bound-v1": BOUND_CURRENT_TARGETS[6:23],
    "current-runs-contract-immutable-v1": BOUND_CURRENT_TARGETS[23:24],
    "current-run-steps-contract-immutable-v1": BOUND_CURRENT_TARGETS[24:],
    "current-task5-coverage-replacement-v1": RETAINED_CURRENT_TARGETS[:1],
    "current-task6-immutable-replacement-v1": RETAINED_CURRENT_TARGETS[1:],
}
V8_AUTHORITY_IDS = tuple(V8_AUTHORITY_TARGETS)


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _artifact(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _strip_version_fields(value: dict[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(value))
    copied["metadata"].pop("artifact_schema")
    copied["metadata"].pop("append_frozen")
    copied["metadata"].pop("pipeline_contract_version")
    copied["metadata"].pop("reviewed_design_sha256")
    copied["metadata"].pop("reviewed_design_utf8_hex")
    copied["digest_recipe_registry"].pop("reviewed_literal_utf8_hex")
    copied["digest_recipe_registry"]["rows"].pop(5)
    copied["sql_authority_registry"].pop("expected_sha256")
    copied["sql_authority_registry"]["rows"] = copied["sql_authority_registry"]["rows"][:17]
    return copied


def test_v8_materializer_builds_an_unfrozen_candidate_by_default() -> None:
    materializer = _load_module(_MATERIALIZER, "_task7_v8_materializer")
    _raw, v7 = materializer._load_v7(_V7_ARTIFACT)
    built = materializer._build_v8(v7)
    assert built["metadata"]["append_frozen"] is False
    assert built == _artifact(_V8_ARTIFACT)
    frozen = materializer._build_v8(v7, append_frozen=True)
    assert frozen["metadata"]["append_frozen"] is True
    built["metadata"]["append_frozen"] = True
    assert built == frozen


def test_v8_diff_is_only_authorities_and_dependent_version_hash_fields() -> None:
    v7 = _artifact(_V7_ARTIFACT)
    v8 = _artifact(_V8_ARTIFACT)
    assert _strip_version_fields(v8) == _strip_version_fields(v7)
    assert v8["sql_authority_registry"]["rows"][:17] == (v7["sql_authority_registry"]["rows"])


def test_v8_has_the_exact_reviewed_current_guard_partition() -> None:
    rows = _artifact(_V8_ARTIFACT)["sql_authority_registry"]["rows"]
    by_id = {row["authority_id"]: row for row in rows}
    assert tuple(by_id) == tuple(
        [row["authority_id"] for row in rows[:17]] + list(V8_AUTHORITY_IDS)
    )
    expected = tuple(V8_AUTHORITY_TARGETS.values())
    assert tuple(
        sum(len(group) for group in expected[start:stop])
        for start, stop in ((0, 3), (3, 5), (5, 9), (9, 11))
    ) == (32, 7, 25, 9)
    for authority_id, targets in V8_AUTHORITY_TARGETS.items():
        assert tuple(map(tuple, by_id[authority_id]["target_tuple"])) == targets
    all_targets = tuple(target for group in expected for target in group)
    assert len(all_targets) == 73
    assert len(set(all_targets)) == 73


def test_v8_sql_binds_the_five_input_binding_invariants() -> None:
    rows = _artifact(_V8_ARTIFACT)["sql_authority_registry"]["rows"]
    row = next(row for row in rows if row["authority_id"] == "current-input-binding-admission-v1")
    sql = "\n".join(
        bytes.fromhex(value).decode("utf-8") for value in row["body_sql_utf8_hex_tuple"]
    )
    required = (
        "run_input_bindings",
        "data_room_packages",
        "source_lineage_objects",
        "strict_preflight_proofs",
        "status='SEALED'",
        "corpus_receipt_sha256",
        "pipeline_contract_version",
        "pipeline_contract_hash",
        "run-input-binding-digest-v2",
        "binding_sha256",
        "STRICT_PREFLIGHT_PROOF_MISMATCH",
        "PIPELINE_CONTRACT_HASH_MISMATCH",
        "STEP_REHYDRATION_FAILED",
    )
    assert [literal for literal in required if literal not in sql] == []


def test_v8_guard_outward_codes_are_locked_existing_codes_only() -> None:
    rows = _artifact(_V8_ARTIFACT)["sql_authority_registry"]["rows"][17:]
    sql = "\n".join(
        bytes.fromhex(value).decode("utf-8")
        for row in rows
        for value in row["body_sql_utf8_hex_tuple"]
    )
    banned = {
        "TASK7_INPUT_BINDING_ADMISSION_INVALID",
        "TASK7_RUN_GRAPH_APPEND_ONLY",
        "TASK7_SOURCE_EXACT_ONE_INVALID",
        "TASK7_OUTPUT_EXACT_ONE_INVALID",
    }
    assert banned.isdisjoint(sql.split("'"))
    assert "TASK7_SOURCE_TERMINAL_STATE_INVALID" in sql
    assert "PIPELINE_CONTRACT_HASH_MISMATCH" in sql
    assert "STRICT_PREFLIGHT_PROOF_MISMATCH" in sql


def test_v8_binding_preimage_has_explicit_nfc_for_every_text_scalar() -> None:
    rows = _artifact(_V8_ARTIFACT)["sql_authority_registry"]["rows"]
    row = next(row for row in rows if row["authority_id"] == "current-input-binding-admission-v1")
    sql = bytes.fromhex(row["body_sql_utf8_hex_tuple"][0]).decode("utf-8")
    normalized_fields = (
        "tenant_id::text",
        "deal_id::text",
        "run_id::text",
        "pipeline_contract_version",
        "pipeline_contract_hash",
        "corpus_receipt_id::text",
        "source_lineage_object_id::text",
        "source_object_kind",
        "source_disposition",
        "binding_purpose",
        "selected_source_version",
        "selected_identity_sha256",
        "selected_content_sha256",
        "corpus_receipt_sha256",
    )
    assert all(f"normalize(NEW.{field},NFC)" in sql for field in normalized_fields)


def test_v8_generator_admits_all_and_only_current_ddl_guard_targets() -> None:
    generator = _load_module(_GENERATOR, "_task7_v8_generator")
    raw = _V8_ARTIFACT.read_bytes()
    artifact = generator._decode_artifact(raw)
    generator._validate_shape(raw, artifact)
    closure = generator.derive_current_guard_closure(artifact)
    assert tuple(map(tuple, closure["ordered_target_tuple"])) == (
        DEFERRED_CURRENT_TARGETS
        + APPEND_ONLY_CURRENT_TARGETS
        + BOUND_CURRENT_TARGETS
        + RETAINED_CURRENT_TARGETS
    )
    assert closure["orphan_authority_target_tuple"] == []
    assert closure["missing_ddl_or_downgrade_target_tuple"] == []


def test_v8_preserves_every_nonversion_semantic_vector() -> None:
    generator = _load_module(_GENERATOR, "_task7_v8_generator_vectors")
    v7 = _artifact(_V7_ARTIFACT)
    v8 = _artifact(_V8_ARTIFACT)
    assert generator.invariant_vector(v8) == generator.invariant_vector(v7)
    assert generator.invariant_vector(v8) == {
        "semantic_step_count": 28,
        "edge_rule_count": 48,
        "usage_route_count": 1_756,
        "usage_extractor_count": 19,
        "rights_policy_count": 18,
        "current_ddl_count": 34,
        "erasure_baseline_count": 59,
        "erasure_materialized_count": 66,
    }


def test_v7_identifiers_are_permanently_blocklisted() -> None:
    historical_fixture = (
        Path(__file__).with_name("fixtures") / "full_strict_v7_contract_hash_v1.json"
    )
    assert (
        hashlib.sha256(_V7_ARTIFACT.read_bytes()).hexdigest()
        == void_evidence.VOID_V7_ARTIFACT_SHA256
    )
    assert not historical_fixture.exists()
    assert {
        void_evidence.VOID_V7_ARTIFACT_SHA256,
        void_evidence.VOID_V7_FIXTURE_SHA256,
        void_evidence.VOID_V7_CONTRACT_SHA256,
    }.issubset(set(void_evidence.VOID_CONTRACT_HASHES))


def test_v8_fixture_is_bound_to_the_executing_postgres_census() -> None:
    fixture = _artifact(_V8_FIXTURE)
    assert fixture["artifact_sha256"] == hashlib.sha256(_V8_ARTIFACT.read_bytes()).hexdigest()
    assert fixture["pipeline_contract_version"] == "full-strict-v8"
    census = fixture["postgres_authority_census"]
    assert census["current_guard_target_count"] == 73
    assert census["legal_execution_count"] == 73
    assert census["illegal_rejection_count"] == 73
    assert census["direct_sql_rejection_count"] == 73
    assert census["database_rejection_count"] == 0


@pytest.mark.parametrize(
    "section",
    (
        "metadata",
        "digest_recipe_registry",
        "sql_authority_registry",
        "current_ddl_registry",
        "semantic_step_registry",
        "edge_rule_registry",
        "usage_route_registry",
        "usage_extractor_registry",
    ),
)
def test_v8_corruption_in_every_relevant_section_changes_contract_hash(
    section: str,
) -> None:
    generator = _load_module(_GENERATOR, f"_task7_v8_corruption_{section}")
    raw = _V8_ARTIFACT.read_bytes()
    baseline = generator.derive_contract_bytes(raw, validate=True)
    artifact = generator._decode_artifact(raw)
    if section == "metadata":
        artifact[section]["reviewed_design_utf8_hex"] += "00"
    elif section == "digest_recipe_registry":
        artifact[section]["rows"][0][1] += "-corrupt"
    else:
        artifact[section]["rows"][0]["_corrupt"] = True
    changed = json.dumps(
        artifact,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    with pytest.raises(generator.ContractArtifactError):
        generator.derive_contract_bytes(changed, validate=True)
    assert (
        generator.derive_contract_bytes(
            raw,
            validate=True,
        )["FULL_STRICT_V8_CONTRACT_HASH"]
        == baseline["FULL_STRICT_V8_CONTRACT_HASH"]
    )

"""Contracts for the full-strict-v16 typed semantic dispatch matrix."""

from __future__ import annotations

import json
from dataclasses import fields, replace
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from idis.pipeline.task7_census import (
    CENSUS_SCHEMA_REGISTRY,
    CensusEnvelopeV1,
    CensusValidationError,
    build_census_envelope,
)
from idis.pipeline.task7_native_registry import (
    NATIVE_OUTPUT_REGISTRY,
    NativeAuthorityState,
    NativeRegistryError,
    require_current_native_authority,
    require_native_authority,
)
from idis.pipeline.task7_semantics import (
    PIPELINE_CONTRACT_HASH_MISMATCH,
    PIPELINE_CONTRACT_UNAVAILABLE,
    STEP_OUTPUT_MISSING,
    STEP_SEMANTIC_REGISTRY,
    STEP_SEMANTIC_VALIDATION_FAILED,
    ApplicabilityDecisionEvidence,
    NativeOutputEvidence,
    SemanticValidationInput,
    Step5ApplicabilityEvidenceV1,
    Step16ProviderApplicabilityEvidenceV1,
    StepSemanticValidationError,
    StepSemanticValidator,
    SubroleReceiptEvidence,
)
from idis.pipeline.task7_usage import ProducerRefV1

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v16_reviewed_preimage_v1.json"
_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v16_contract_hash_v1.json"
)
_UUID = "11111111-1111-4111-8111-111111111111"
_HASH = "a" * 64


def _artifact() -> dict[str, Any]:
    value = json.loads(_ARTIFACT_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _fixture() -> dict[str, Any]:
    value = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _semantic_artifact_rows() -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for row in _artifact()["semantic_step_registry"]["rows"]:
        fields = dict(row[4])
        rows.append(
            {
                "step_order": row[0],
                "step_name": row[1],
                "semantic_rule_version": "2",
                "semantic_rule_hash": row[3],
                "aggregate_receipt_kind": fields["aggregate"],
                "census_schema_id": fields["census"],
                "mandatory_subroles": (
                    () if fields["subroles"] == "-" else tuple(fields["subroles"].split(","))
                ),
                "native_output_kinds": (
                    () if fields["natives"] == "-" else tuple(fields["natives"].split(","))
                ),
                "terminal_status": fields["terminal"],
            }
        )
    return tuple(rows)


def _producer_ref() -> dict[str, Any]:
    return {
        "producer_type": "OUTPUT_OBJECT",
        "producer_id": _UUID,
        "producer_identity_sha256": _HASH,
        "producer_value_sha256": _HASH,
    }


def _native_root() -> dict[str, Any]:
    return {
        "output_kind": "METHODOLOGY_COVERAGE_IDENTITY",
        "native_identity_sha256": _HASH,
        "native_output_sha256": _HASH,
        "output_object_id": _UUID,
    }


def _subrole_receipt(subrole: str = "role") -> dict[str, Any]:
    return {
        "subrole": subrole,
        "native_identity_sha256": _HASH,
        "native_output_sha256": _HASH,
    }


def _key_domain(type_expression: str) -> tuple[str, ...]:
    artifact = _artifact()
    if type_expression.startswith("KeyMap<provider_id,"):
        return tuple(str(row[0]) for row in artifact["provider_registry"]["rows"])
    if type_expression.startswith("KeyMap<debate_role,"):
        return tuple(str(value) for value in artifact["role_registry"]["rows"][0])
    if type_expression.startswith("KeyMap<analysis_role,"):
        return tuple(str(value) for value in artifact["role_registry"]["rows"][1])
    if type_expression.startswith("KeyMap<layer2_role,"):
        return tuple(str(value) for value in artifact["role_registry"]["rows"][2])
    if type_expression.startswith("KeyMap<internal_evidence|external_context,"):
        return ("internal_evidence", "external_context")
    if type_expression.startswith("KeyMap<deliverable_artifact_subrole,"):
        return tuple(str(row[3]) for row in artifact["deliverable_registry"]["rows"])
    if type_expression.startswith("KeyMap<deliverable_receipt_subrole,"):
        return (
            "reconciliation",
            *(str(row[3]) for row in artifact["deliverable_registry"]["rows"]),
        )
    if type_expression.startswith("KeyMap<(step,subrole),"):
        return ("DATA_ROOM_INVENTORY_PACKAGE:__step__",)
    raise AssertionError(f"unsupported test key domain: {type_expression}")


def _value_for(type_expression: str, field_name: str) -> Any:
    if type_expression == "Literal[1]":
        return "1"
    if type_expression.startswith("Literal["):
        return type_expression.removeprefix("Literal[").removesuffix("]")
    if type_expression == "Count":
        return 0
    if type_expression in {"Hash", "HashSet"}:
        return _HASH if type_expression == "Hash" else [_HASH]
    if type_expression == "HashOrNull":
        return None
    if type_expression == "Id":
        return _UUID
    if type_expression == "IdOrNull":
        return None
    if type_expression == "IdSet":
        return [_UUID]
    if type_expression == "TextIdSet":
        return ["court-finding-1"]
    if type_expression == "TextOrNull":
        return None
    if type_expression in {"bool", "Boolean"}:
        return True
    if type_expression == "str":
        return f"{field_name}-value"
    if type_expression == "NativeRootRefV1":
        return _native_root()
    if type_expression == "RootSet":
        return [_native_root()]
    if type_expression == "ProducerRefV1":
        return _producer_ref()
    if type_expression == "RefSet":
        return [_producer_ref()]
    if type_expression.startswith("Histogram<"):
        domain = type_expression.removeprefix("Histogram<").removesuffix(">")
        keys = (
            tuple(domain.split("|"))
            if domain not in {"registered_format", "registered_status"}
            else (f"{domain}-value",)
        )
        return [[key, 0] for key in keys]
    if type_expression.startswith("KeyMap<"):
        value_type = type_expression.rsplit(",", 1)[1].removesuffix(">")
        if value_type == "SubroleReceiptRefV1":
            return [[key, _subrole_receipt(key)] for key in _key_domain(type_expression)]
        if value_type == "RefSet":
            return [[key, [_producer_ref()]] for key in _key_domain(type_expression)]
        if value_type == "ProducerRefV1":
            return [[key, _producer_ref()] for key in _key_domain(type_expression)]
        if value_type == "APPLICABLE|NOT_APPLICABLE":
            return [[key, "APPLICABLE"] for key in _key_domain(type_expression)]
        raise AssertionError(f"unsupported test map value: {type_expression}")
    if type_expression == "SET[Count]":
        return [0]
    if type_expression == "SET[str]":
        return ["value"]
    if type_expression.startswith("SET[("):
        return []
    if type_expression.startswith("Ordered<"):
        return []
    if type_expression == "provider_id":
        return str(_artifact()["provider_registry"]["rows"][0][0])
    if type_expression == "debate_role":
        return str(_artifact()["role_registry"]["rows"][0][0])
    if type_expression == "analysis_role":
        return str(_artifact()["role_registry"]["rows"][1][0])
    if type_expression == "layer2_role":
        return str(_artifact()["role_registry"]["rows"][2][0])
    if type_expression == "internal_evidence|external_context":
        return "internal_evidence"
    if type_expression == "HIT|MISS|NOT_APPLICABLE":
        return "NOT_APPLICABLE"
    if type_expression == "GREEN|YELLOW|RED|null":
        return None
    if type_expression == "PDF|DOCX|JSON":
        return "JSON"
    if type_expression == "READY":
        return "READY"
    raise AssertionError(f"unsupported test type: {type_expression}")


def _valid_envelope(schema_id: str) -> CensusEnvelopeV1:
    schema = CENSUS_SCHEMA_REGISTRY[schema_id]
    payload = {
        field_name: _value_for(type_expression, field_name)
        for field_name, type_expression in schema.field_types
    }
    return build_census_envelope(schema_id=schema_id, payload=payload)


def _valid_request(step_name: str) -> SemanticValidationInput:
    spec = STEP_SEMANTIC_REGISTRY[step_name]
    native_outputs = tuple(
        NativeOutputEvidence(
            output_kind=output_kind,
            output_object_id=UUID(int=index + 1),
            native_identity_sha256=f"{index + 1:064x}",
            native_output_sha256=f"{index + 101:064x}",
            persisted=True,
        )
        for index, output_kind in enumerate(spec.native_output_kinds)
    )
    subrole_receipts = tuple(
        SubroleReceiptEvidence(
            subrole=subrole,
            native_identity_sha256=f"{index + 201:064x}",
            native_output_sha256=f"{index + 301:064x}",
            persisted=True,
        )
        for index, subrole in enumerate(spec.mandatory_subroles)
    )
    if step_name == "METHODOLOGY_COVERAGE_INIT":
        applicability_decisions = _step5_decisions()
    elif step_name == "METHODOLOGY_COMPANY_IDENTITY_PACKAGE":
        applicability_decisions = _step16_decisions()
    else:
        applicability_decisions = ()
    return SemanticValidationInput(
        step_name=step_name,
        semantic_rule_version=spec.semantic_rule_version,
        semantic_rule_hash=spec.semantic_rule_hash,
        terminal_status="COMPLETED",
        result_payload={"status": "COMPLETED", "persistence": {"status": "persisted"}},
        census=_valid_envelope(spec.census_schema_id),
        native_outputs=native_outputs,
        subrole_receipts=subrole_receipts,
        producer_persistence_confirmed=True,
        applicability_decisions=applicability_decisions,
    )


def _producer_evidence(*, producer_id: int = 91) -> ProducerRefV1:
    return ProducerRefV1(
        producer_type="OUTPUT_OBJECT",
        producer_id=UUID(int=producer_id),
        producer_identity_sha256=f"{producer_id + 1:064x}",
        producer_value_sha256=f"{producer_id + 2:064x}",
    )


def _step5_decision(
    rule_id: str = "E009_COVERAGE_TO_PLANNING",
) -> ApplicabilityDecisionEvidence:
    rule = __import__(
        "idis.pipeline.task7_edges",
        fromlist=["EDGE_RULE_REGISTRY"],
    ).EDGE_RULE_REGISTRY[rule_id]
    return ApplicabilityDecisionEvidence(
        upstream_producer=_producer_evidence(),
        contract_rule_id=rule.rule_id,
        contract_rule_version=rule.rule_version,
        contract_rule_hash=rule.rule_sha256,
        evidence=Step5ApplicabilityEvidenceV1(
            coverage_record_id="coverage-record-001",
            subject_key="coverage-subject-001",
            methodology_type="commercial",
            selection_hash="b" * 64,
            question_semantic_hash="c" * 64,
            outcome="APPLICABLE",
            basis_code="FROZEN_ROW_APPLICABLE",
        ),
    )


def _step16_decision(
    provider_id: str = "sec_edgar",
) -> ApplicabilityDecisionEvidence:
    rule = __import__(
        "idis.pipeline.task7_edges",
        fromlist=["EDGE_RULE_REGISTRY"],
    ).EDGE_RULE_REGISTRY["E025_IDENTITY_TO_PROVIDERS"]
    return ApplicabilityDecisionEvidence(
        upstream_producer=_producer_evidence(producer_id=93),
        contract_rule_id=rule.rule_id,
        contract_rule_version=rule.rule_version,
        contract_rule_hash=rule.rule_sha256,
        evidence=Step16ProviderApplicabilityEvidenceV1(
            provider_id=provider_id,
            identity_output_ref=_producer_evidence(producer_id=93),
            listing_state="PRIVATE",
            verified_identifier_kind="company_number",
            verified_identifier_hash="d" * 64,
            applicability_rule_id="private-company-provider-applicability",
            applicability_rule_version="1",
            applicability_rule_hash="e" * 64,
            outcome="NOT_APPLICABLE",
            basis_code="PRIVATE_COMPANY_VERIFIED",
        ),
    )


def _step5_decisions() -> tuple[ApplicabilityDecisionEvidence, ...]:
    return tuple(
        _step5_decision(rule_id)
        for rule_id in (
            "E009_COVERAGE_TO_PLANNING",
            "E012_TASK_TO_EXECUTION",
            "E046_APPLICABLE_COVERAGE_DOWNSTREAM",
        )
    )


def _step16_decisions() -> tuple[ApplicabilityDecisionEvidence, ...]:
    return tuple(
        _step16_decision(provider_id)
        for provider_id in STEP_SEMANTIC_REGISTRY["ENRICHMENT"].mandatory_subroles
    )


def _error_code(error: BaseException) -> str | None:
    return getattr(error, "code", None)


def test_semantic_registry_is_exactly_the_28_reviewed_v5_rows() -> None:
    expected = _semantic_artifact_rows()
    actual = tuple(
        {
            "step_order": spec.step_order,
            "step_name": spec.step_name,
            "semantic_rule_version": spec.semantic_rule_version,
            "semantic_rule_hash": spec.semantic_rule_hash,
            "aggregate_receipt_kind": spec.aggregate_receipt_kind,
            "census_schema_id": spec.census_schema_id,
            "mandatory_subroles": spec.mandatory_subroles,
            "native_output_kinds": spec.native_output_kinds,
            "terminal_status": spec.terminal_status,
        }
        for spec in STEP_SEMANTIC_REGISTRY.values()
    )

    assert actual == expected
    assert _fixture()["FULL_STRICT_V16_CONTRACT_HASH"] == (
        "dadf149672f80d66a913cb6459c2465d512adfb05ce8031624c9a5e725ac65f5"
    )


def test_census_registry_is_the_exact_artifact_census_subset() -> None:
    artifact_rows = tuple(
        row
        for row in _artifact()["schema_registry"]["rows"]
        if row["authority_locator"].startswith("task7-census:")
    )
    assert len(CENSUS_SCHEMA_REGISTRY) == 36
    assert tuple(CENSUS_SCHEMA_REGISTRY) == tuple(row["schema_id"] for row in artifact_rows)

    for row in artifact_rows:
        schema = CENSUS_SCHEMA_REGISTRY[row["schema_id"]]
        assert schema.schema_version == row["schema_version"]
        assert schema.authority_locator == row["authority_locator"]
        assert schema.field_types == tuple(
            (name, definition["x-idis-type"])
            for name, definition in row["normalized_schema_snapshot"]["properties"].items()
        )
        assert schema.required_fields == tuple(
            dict.fromkeys(row["normalized_schema_snapshot"]["required"])
        )
        assert schema.collection_paths == tuple(
            (path, contract) for path, contract in row["collection_paths"]
        )
        assert schema.schema_sha256 == row["expected_schema_sha256"]


def test_dispatch_covers_all_28_rows_and_accepts_typed_complete_evidence() -> None:
    validator = StepSemanticValidator()
    assert validator.dispatch_keys == tuple(STEP_SEMANTIC_REGISTRY)

    for step_name, spec in STEP_SEMANTIC_REGISTRY.items():
        validated = validator.validate(_valid_request(step_name))

        assert validated.step_name == step_name
        assert validated.semantic_rule_hash == spec.semantic_rule_hash
        assert validated.census.schema_sha256 == (
            CENSUS_SCHEMA_REGISTRY[spec.census_schema_id].schema_sha256
        )
        assert len(validated.native_outputs) == len(spec.native_output_kinds)
        assert len(validated.subrole_receipts) == len(spec.mandatory_subroles)


@pytest.mark.parametrize(
    ("step_name", "decision_factory"),
    [
        ("METHODOLOGY_COVERAGE_INIT", _step5_decisions),
        ("METHODOLOGY_COMPANY_IDENTITY_PACKAGE", _step16_decisions),
    ],
)
def test_steps_5_and_16_return_only_validated_typed_applicability_decisions(
    step_name: str,
    decision_factory: Any,
) -> None:
    decisions = decision_factory()
    request = replace(
        _valid_request(step_name),
        applicability_decisions=decisions,
    )

    result = StepSemanticValidator().validate(request)

    assert result.applicability_decisions == decisions
    assert result.applicability_decisions is not request.result_payload


def test_applicability_decisions_fail_closed_outside_their_exact_step_and_shape() -> None:
    with pytest.raises(StepSemanticValidationError) as wrong_step:
        StepSemanticValidator().validate(
            replace(
                _valid_request("DATA_ROOM_INVENTORY_PACKAGE"),
                applicability_decisions=_step5_decisions(),
            )
        )
    assert _error_code(wrong_step.value) == STEP_SEMANTIC_VALIDATION_FAILED

    malformed = replace(
        _step16_decision(),
        evidence=replace(
            _step16_decision().evidence,
            listing_state="UNKNOWN",
            verified_identifier_kind=None,
            verified_identifier_hash=None,
            outcome="NOT_APPLICABLE",
        ),
    )
    with pytest.raises(StepSemanticValidationError) as unsupported_na:
        StepSemanticValidator().validate(
            replace(
                _valid_request("METHODOLOGY_COMPANY_IDENTITY_PACKAGE"),
                applicability_decisions=(malformed,),
            )
        )
    assert _error_code(unsupported_na.value) == STEP_SEMANTIC_VALIDATION_FAILED

    with pytest.raises(StepSemanticValidationError) as missing:
        StepSemanticValidator().validate(
            replace(
                _valid_request("METHODOLOGY_COVERAGE_INIT"),
                applicability_decisions=(),
            )
        )
    assert _error_code(missing.value) == STEP_SEMANTIC_VALIDATION_FAILED


def test_producer_cannot_author_the_deciding_step_output_identity() -> None:
    assert "deciding_step_output_id" not in {
        field.name for field in fields(Step5ApplicabilityEvidenceV1)
    }
    assert "deciding_step_output_id" not in {
        field.name for field in fields(Step16ProviderApplicabilityEvidenceV1)
    }


@pytest.mark.parametrize(
    "status",
    ["FAILED", "PARTIAL", "DEFERRED", "BLOCKED", "SKIPPED", "NOT_ATTEMPTED"],
)
def test_nested_internal_non_success_status_fails_closed(status: str) -> None:
    request = _valid_request("METHODOLOGY_COVERAGE_INIT")
    request = replace(
        request,
        result_payload={
            "status": "COMPLETED",
            "nested": [{"provider": {"status": status}}],
        },
    )

    with pytest.raises(StepSemanticValidationError) as caught:
        StepSemanticValidator().validate(request)

    assert _error_code(caught.value) == STEP_SEMANTIC_VALIDATION_FAILED


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("all_failed", True),
        ("rejected_count", 1),
        ("failed_count", 1),
        ("deferred_count", 1),
        ("blocked_count", 1),
    ],
)
def test_global_failure_flags_and_counters_fail_closed(field_name: str, value: object) -> None:
    request = _valid_request("METHODOLOGY_COVERAGE_INIT")
    request = replace(
        request,
        result_payload={"status": "COMPLETED", "nested": {field_name: value}},
    )

    with pytest.raises(StepSemanticValidationError) as caught:
        StepSemanticValidator().validate(request)

    assert _error_code(caught.value) == STEP_SEMANTIC_VALIDATION_FAILED


def test_durable_export_false_fails_closed() -> None:
    request = _valid_request("DELIVERABLES")
    payload = dict(request.census.payload)
    payload["durable_export"] = False
    request = replace(
        request,
        census=build_census_envelope(
            schema_id=request.census.schema_id,
            payload=payload,
        ),
    )

    with pytest.raises(StepSemanticValidationError) as caught:
        StepSemanticValidator().validate(request)

    assert _error_code(caught.value) == STEP_SEMANTIC_VALIDATION_FAILED


@pytest.mark.parametrize(
    "mutation",
    ["producer_persistence", "native_output", "mandatory_subrole"],
)
def test_missing_persisted_producer_evidence_fails_step_output_missing(mutation: str) -> None:
    request = _valid_request("ENRICHMENT")
    if mutation == "producer_persistence":
        request = replace(request, producer_persistence_confirmed=False)
    elif mutation == "native_output":
        request = replace(request, native_outputs=request.native_outputs[1:])
    else:
        request = replace(request, subrole_receipts=request.subrole_receipts[1:])

    with pytest.raises(StepSemanticValidationError) as caught:
        StepSemanticValidator().validate(request)

    assert _error_code(caught.value) == STEP_OUTPUT_MISSING


def test_unknown_step_and_rule_drift_fail_with_locked_contract_codes() -> None:
    request = _valid_request("METHODOLOGY_COVERAGE_INIT")

    with pytest.raises(StepSemanticValidationError) as unknown:
        StepSemanticValidator().validate(replace(request, step_name="UNREVIEWED_STEP"))
    assert _error_code(unknown.value) == PIPELINE_CONTRACT_UNAVAILABLE

    with pytest.raises(StepSemanticValidationError) as drift:
        StepSemanticValidator().validate(replace(request, semantic_rule_hash="0" * 64))
    assert _error_code(drift.value) == PIPELINE_CONTRACT_HASH_MISMATCH


def test_census_extra_missing_wrong_type_and_wrong_schema_hash_fail_closed() -> None:
    envelope = _valid_envelope("coverage-census-v1")
    payload = dict(envelope.payload)

    with pytest.raises(CensusValidationError) as extra:
        build_census_envelope(
            schema_id=envelope.schema_id,
            payload={**payload, "unreviewed": 1},
        )
    assert _error_code(extra.value) == STEP_SEMANTIC_VALIDATION_FAILED

    payload.pop("total_count")
    with pytest.raises(CensusValidationError) as missing:
        build_census_envelope(schema_id=envelope.schema_id, payload=payload)
    assert _error_code(missing.value) == STEP_SEMANTIC_VALIDATION_FAILED

    payload = dict(envelope.payload)
    payload["total_count"] = True
    with pytest.raises(CensusValidationError) as wrong_type:
        build_census_envelope(schema_id=envelope.schema_id, payload=payload)
    assert _error_code(wrong_type.value) == STEP_SEMANTIC_VALIDATION_FAILED

    with pytest.raises(CensusValidationError) as wrong_hash:
        build_census_envelope(
            schema_id=envelope.schema_id,
            payload=envelope.payload,
            expected_schema_sha256="0" * 64,
        )
    assert _error_code(wrong_hash.value) == PIPELINE_CONTRACT_HASH_MISMATCH


def test_census_hash_is_order_neutral_for_declared_set_collections() -> None:
    envelope = _valid_envelope("inventory-census-v1")
    payload = dict(envelope.payload)
    first_ref = _producer_ref()
    second_ref = {
        **first_ref,
        "producer_id": "22222222-2222-4222-8222-222222222222",
        "producer_identity_sha256": "b" * 64,
        "producer_value_sha256": "c" * 64,
    }
    first_root = _native_root()
    second_root = {
        **first_root,
        "output_object_id": "33333333-3333-4333-8333-333333333333",
        "native_identity_sha256": "d" * 64,
        "native_output_sha256": "e" * 64,
    }
    payload["input_refs"] = [first_ref, second_ref]
    payload["package_file_roots"] = [first_root, second_root]

    forward = build_census_envelope(schema_id=envelope.schema_id, payload=payload)
    reverse = build_census_envelope(
        schema_id=envelope.schema_id,
        payload={
            **payload,
            "input_refs": list(reversed(payload["input_refs"])),
            "package_file_roots": list(reversed(payload["package_file_roots"])),
        },
    )

    assert reverse.payload == forward.payload
    assert reverse.canonical_sha256 == forward.canonical_sha256


def test_native_registry_closes_every_reviewed_step_output_coordinate() -> None:
    expected = tuple(
        (row["step_name"], output_kind)
        for row in _semantic_artifact_rows()
        for output_kind in row["native_output_kinds"]
    )
    assert tuple(NATIVE_OUTPUT_REGISTRY) == expected

    for key, authority in NATIVE_OUTPUT_REGISTRY.items():
        assert require_native_authority(*key) is authority
        assert authority.relation_name
        assert authority.schema_sha256 == authority.schema_sha256.lower()
        assert len(authority.schema_sha256) == 64


def test_future_native_specs_are_visible_but_not_claimed_implemented() -> None:
    future_rows = _artifact()["future_native_authority_registry"]["rows"]
    future_tables = {str(row["planned_table"]) for row in future_rows}
    exposed_future_tables = {
        spec.relation_name
        for spec in NATIVE_OUTPUT_REGISTRY.values()
        if spec.state is NativeAuthorityState.FUTURE_INACTIVE
    }

    assert future_tables <= exposed_future_tables
    future = require_native_authority(
        "METHODOLOGY_TRUTH_DASHBOARD",
        "METHODOLOGY_TRUTH_DASHBOARD_ROW",
    )
    assert future.state is NativeAuthorityState.FUTURE_INACTIVE

    with pytest.raises(NativeRegistryError) as caught:
        require_current_native_authority(
            "METHODOLOGY_TRUTH_DASHBOARD",
            "METHODOLOGY_TRUTH_DASHBOARD_ROW",
        )
    assert _error_code(caught.value) == STEP_OUTPUT_MISSING


def test_native_registry_unknown_coordinate_fails_closed() -> None:
    with pytest.raises(NativeRegistryError) as caught:
        require_native_authority("ANALYSIS", "UNREVIEWED_JSON_OUTPUT")

    assert _error_code(caught.value) == STEP_OUTPUT_MISSING

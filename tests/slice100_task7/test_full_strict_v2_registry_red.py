"""Contracts for the reviewed full-strict-v16 registry and activation gate."""

from __future__ import annotations

import importlib
import json
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v16_reviewed_preimage_v1.json"
_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v16_contract_hash_v1.json"
)

_UNAVAILABLE = "PIPELINE_CONTRACT_UNAVAILABLE"
_HASH_MISMATCH = "PIPELINE_CONTRACT_HASH_MISMATCH"
_STRICT_VERSION = "full-strict-v16"


def _registry_module() -> ModuleType:
    try:
        return importlib.import_module("idis.pipeline.contract_registry")
    except ModuleNotFoundError as exc:
        if exc.name != "idis.pipeline.contract_registry":
            raise
        pytest.fail(
            "Task 7 production registry is missing: idis.pipeline.contract_registry",
            pytrace=False,
        )


def _artifact() -> dict[str, Any]:
    value = json.loads(_ARTIFACT_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _fixture() -> dict[str, Any]:
    value = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _error_code(error: BaseException) -> str | None:
    return getattr(error, "code", None)


def _expected_strict_steps() -> tuple[tuple[int, str, str, str], ...]:
    return tuple(
        (row[0], row[1], "2", row[3]) for row in _artifact()["semantic_step_registry"]["rows"]
    )


def _strict_spec(module: ModuleType) -> Any:
    return module.PIPELINE_CONTRACTS[_STRICT_VERSION]


def _exact_census(module: ModuleType) -> Any:
    return module.FULL_STRICT_V16_ACTIVATION_CENSUS_V1


def test_code_owned_registry_has_exact_versions_counts_and_initial_state() -> None:
    module = _registry_module()
    fixture = _fixture()

    expected_versions = (
        "snapshot-v1",
        "full-v1",
        _STRICT_VERSION,
        "full-strict-v17",
        "full-strict-v18",
        "full-strict-v20",
        "full-strict-v21",
    )
    assert tuple(module.PIPELINE_CONTRACTS) == expected_versions
    assert [len(module.PIPELINE_CONTRACTS[name].steps) for name in module.PIPELINE_CONTRACTS] == [
        8,
        28,
        28,
        28,
        28,
        28,
        28,
    ]
    strict = _strict_spec(module)
    assert {
        version: module.PIPELINE_CONTRACTS[version].state.value for version in expected_versions[2:]
    } == {
        "full-strict-v16": "INACTIVE",
        "full-strict-v17": "INACTIVE",
        "full-strict-v18": "INACTIVE",
        "full-strict-v20": "INACTIVE",
        "full-strict-v21": "INACTIVE",
    }
    assert strict.contract_hash == fixture["FULL_STRICT_V16_CONTRACT_HASH"]
    assert fixture["FULL_STRICT_V16_CONTRACT_HASH"] == module.FULL_STRICT_V16_CONTRACT_HASH
    assert strict.deterministic_step_ids is True
    assert module.PIPELINE_CONTRACTS["snapshot-v1"].deterministic_step_ids is False
    assert module.PIPELINE_CONTRACTS["full-v1"].deterministic_step_ids is False


def test_strict_registry_rows_equal_frozen_semantics_and_exclude_auxiliary() -> None:
    module = _registry_module()
    strict = _strict_spec(module)

    actual = tuple(
        (step.step_order, step.step_name, step.semantic_rule_version, step.semantic_rule_hash)
        for step in strict.steps
    )
    assert actual == _expected_strict_steps()
    assert [step.step_order for step in strict.steps] == list(range(28))
    assert "RUN_LIFECYCLE" not in {step.step_name for step in strict.steps}
    assert len(strict.steps[19].step_name) == 53
    assert strict.steps[19].step_name == ("METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN")


def test_activation_census_is_exactly_bound_to_frozen_fixture() -> None:
    module = _registry_module()
    artifact = _artifact()
    fixture = _fixture()
    census = _exact_census(module)

    assert census.contract_version == _STRICT_VERSION
    assert census.contract_hash == fixture["FULL_STRICT_V16_CONTRACT_HASH"]
    assert census.artifact_sha256 == fixture["artifact_sha256"]
    assert census.component_hashes == fixture["component_hashes"]
    assert census.section_hashes == tuple(
        (row["ordinal"], row["name"], row["sha256"]) for row in fixture["section_hashes"]
    )
    assertions = artifact["activation_assertions"]
    assert census.semantic_step_count == assertions["semantic_step_count"] == 28
    assert census.edge_rule_count == assertions["edge_rule_count"] == 48
    assert census.usage_route_count == assertions["usage_route_count"] == 1_756
    assert census.usage_extractor_count == assertions["usage_extractor_count"] == 19
    assert census.current_ddl_table_count == assertions["current_ddl_table_count"] == 34
    assert census.future_native_table_count == assertions["future_native_table_count"] == 18
    assert census.future_reference_table_count == assertions["future_reference_table_count"] == 15
    assert census.future_child_table_count == assertions["future_child_table_count"] == 5


@pytest.mark.parametrize(
    "section_name",
    [
        "current_ddl_registry",
        "baseline_parent_authority_registry",
        "future_native_authority_registry",
        "future_reference_authority_registry",
        "future_child_authority_registry",
    ],
)
def test_missing_current_or_future_authority_fails_unavailable(section_name: str) -> None:
    module = _registry_module()
    exact = _exact_census(module)
    actual = exact.without_authority(section_name=section_name, row_ordinal=0)

    with pytest.raises(module.PipelineContractError) as caught:
        module.verify_full_strict_v16_activation_census(actual)

    assert _error_code(caught.value) == _UNAVAILABLE


@pytest.mark.parametrize(
    "section_name",
    [
        "semantic_step_registry",
        "schema_registry",
        "producer_selector_registry",
        "consumer_selector_registry",
        "edge_rule_registry",
        "usage_route_registry",
    ],
)
def test_available_but_reviewed_byte_drift_fails_hash_mismatch(section_name: str) -> None:
    module = _registry_module()
    exact = _exact_census(module)
    actual = exact.with_replaced_authority_byte(
        section_name=section_name,
        row_ordinal=0,
        byte_ordinal=0,
        replacement=0x7F,
    )

    with pytest.raises(module.PipelineContractError) as caught:
        module.verify_full_strict_v16_activation_census(actual)

    assert _error_code(caught.value) == _HASH_MISMATCH


def test_strict_contract_cannot_be_admitted_before_explicit_activation() -> None:
    module = _registry_module()
    registry = module.PIPELINE_CONTRACTS

    with pytest.raises(module.PipelineContractError) as caught:
        module.require_pipeline_contract_for_admission(
            registry=registry,
            contract_version=_STRICT_VERSION,
            proof_contract_hash=module.FULL_STRICT_V16_CONTRACT_HASH,
        )

    assert _error_code(caught.value) == _UNAVAILABLE


def test_exact_census_and_separately_reviewed_activation_enable_admission() -> None:
    module = _registry_module()
    receipt = module.verify_full_strict_v16_activation_census(_exact_census(module))
    assert receipt.contract_hash == module.FULL_STRICT_V16_CONTRACT_HASH

    with pytest.raises(module.PipelineContractError) as caught:
        module.activate_full_strict_v16(
            registry=module.PIPELINE_CONTRACTS,
            census_receipt=receipt,
            separately_reviewed=False,
        )
    assert _error_code(caught.value) == _UNAVAILABLE

    activated = module.activate_full_strict_v16(
        registry=module.PIPELINE_CONTRACTS,
        census_receipt=receipt,
        separately_reviewed=True,
    )
    admitted = module.require_pipeline_contract_for_admission(
        registry=activated,
        contract_version=_STRICT_VERSION,
        proof_contract_hash=module.FULL_STRICT_V16_CONTRACT_HASH,
    )

    assert admitted.state.value == "ACTIVE"
    assert admitted.steps == _strict_spec(module).steps
    # Activation must return a new immutable registry rather than mutate the code-owned constant.
    assert _strict_spec(module).state.value == "INACTIVE"


def test_wrong_proof_hash_is_rejected_after_activation() -> None:
    module = _registry_module()
    receipt = module.verify_full_strict_v16_activation_census(_exact_census(module))
    activated = module.activate_full_strict_v16(
        registry=module.PIPELINE_CONTRACTS,
        census_receipt=receipt,
        separately_reviewed=True,
    )

    with pytest.raises(module.PipelineContractError) as caught:
        module.require_pipeline_contract_for_admission(
            registry=activated,
            contract_version=_STRICT_VERSION,
            proof_contract_hash="0" * 64,
        )

    assert _error_code(caught.value) == _HASH_MISMATCH


@pytest.mark.parametrize("mutation", ["order", "name", "semantic_hash", "auxiliary"])
def test_admission_rejects_bad_step_rows_and_auxiliary_members(mutation: str) -> None:
    module = _registry_module()
    strict = _strict_spec(module)
    rows = list(strict.steps)
    if mutation == "order":
        rows[4] = replace(rows[4], step_order=99)
    elif mutation == "name":
        rows[4] = replace(rows[4], step_name="UNREVIEWED_STEP")
    elif mutation == "semantic_hash":
        rows[4] = replace(rows[4], semantic_rule_hash="0" * 64)
    else:
        rows.append(
            module.PipelineStepSpec(
                step_order=28,
                step_name="RUN_LIFECYCLE",
                semantic_rule_version="auxiliary-v1",
                semantic_rule_hash="1" * 64,
                auxiliary=True,
            )
        )

    with pytest.raises(module.PipelineContractError) as caught:
        module.validate_admission_step_rows(contract=strict, rows=tuple(rows))

    assert _error_code(caught.value) == _HASH_MISMATCH


def test_exact_strict_step_rows_validate_without_using_legacy_global_order() -> None:
    module = _registry_module()
    strict = _strict_spec(module)

    validated = module.validate_admission_step_rows(contract=strict, rows=strict.steps)

    assert validated == strict.steps
    # The strict order deliberately puts Court at 20; legacy FULL_STEPS puts it at 12.
    assert validated[20].step_name == "METHODOLOGY_EVIDENCE_TRUST_COURT"

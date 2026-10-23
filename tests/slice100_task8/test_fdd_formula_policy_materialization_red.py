"""RED-first admission tests for the reviewed FDD formula-policy bytes."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import pytest

from ._artifact_oracle import (
    COMPLETENESS_REQUESTS,
    DIRECT_SIGNED_CONVENTIONS,
    FORMULA_ANCHORS,
    HASHED_COMPONENTS,
    POLICY_ARTIFACT,
    POLICY_FIXTURE,
    POLICY_ID,
    REVIEW_MAP,
    WORKBOOK_SHA256,
    OracleError,
    expected_completeness_registry,
    expected_direct_signed_registry,
    expected_formula_rows,
    expected_policy,
    formula_sha256,
    load_fixture,
    load_policy,
    recompute_component_hashes,
    recompute_policy_hash,
    require_review_map,
    requires_operator_workbook,
    sha256_bytes,
    validate_derived_counts,
    validate_policy,
)


def _policy() -> dict[str, Any]:
    policy = load_policy()
    validate_policy(policy)
    return policy


@requires_operator_workbook
def test_independent_oracle_is_self_consistent_before_materialization() -> None:
    validate_policy(expected_policy())


def test_policy_artifact_canonical_bytes_without_workbook() -> None:
    """Frozen artifact bytes do not read the operator workbook."""
    policy = load_policy()
    raw = POLICY_ARTIFACT.read_bytes()

    expected = (json.dumps(policy, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode(
        "utf-8"
    )
    if raw.startswith(b"\xef\xbb\xbf"):
        pytest.fail("TASK8_FDD_POLICY_BOM_FORBIDDEN")
    if b"\r\n" in raw or raw != expected:
        pytest.fail("TASK8_FDD_POLICY_BYTES_NOT_CANONICAL")
    assert policy["metadata"]["append_frozen"] is True
    assert policy["metadata"]["activation_status"] == "INACTIVE"


@requires_operator_workbook
def test_policy_artifact_exists_as_exact_frozen_utf8_lf_bytes() -> None:
    _policy()


@requires_operator_workbook
def test_policy_matches_the_independent_complete_oracle() -> None:
    _policy()


def test_policy_row_anchor_transition_without_workbook() -> None:
    policy = load_policy()
    rows = policy["workbook_row_registry"]["rows"]
    required_rows = [row for row in rows if row["required_calculations"]]

    assert len(rows) == 79
    assert len(required_rows) == 10
    assert sum(not row["required_calculations"] for row in rows) == 69
    observed_anchors = [
        (
            requirement["formula_ordinal"],
            requirement["formula_type"],
            row["source_sheet"],
            row["source_row"],
            requirement["scope"],
        )
        for row in required_rows
        for requirement in row["required_calculations"]
    ]
    assert sorted(observed_anchors) == list(FORMULA_ANCHORS)

    transition = policy["methodology_transition"]
    assert transition["source_artifact_sha256"] == WORKBOOK_SHA256
    assert transition["successor_version_id_preimage"] == [
        "financial_dd",
        WORKBOOK_SHA256,
        POLICY_ID,
        "$FDD_FORMULA_POLICY_V1_SHA256",
    ]
    assert "successor_version_id" not in transition


@requires_operator_workbook
def test_policy_has_exact_79_row_ten_anchor_requirement_transition() -> None:
    _policy()


def test_expanded_bindings_and_parent_dag_without_workbook() -> None:
    policy = load_policy()
    validate_derived_counts(policy)

    formulas = policy["formula_registry"]["rows"]
    assert formulas == expected_formula_rows()
    assert [row["dependency_tuple"] for row in formulas] == [
        [],
        ["GROSS_MARGIN"],
        ["FDD_EBITDA_BRIDGE", "GROSS_MARGIN"],
        ["FDD_EBIT_BRIDGE"],
        ["FDD_EBIT_BRIDGE", "FDD_TAX_RECONCILIATION", "GROSS_MARGIN"],
        [],
        ["FDD_WORKING_CAPITAL_MOVEMENT"],
        [],
        [],
        ["FDD_ASSET_TIE_OUT"],
    ]


@requires_operator_workbook
def test_all_expanded_bindings_and_parent_dag_are_literal_and_complete() -> None:
    _policy()


def test_debt_scope_exclusions_without_workbook() -> None:
    policy = load_policy()
    scope_rows = {row["scope_id"]: row for row in policy["debt_scope_exclusions"]["rows"]}

    debt = scope_rows["fdd-debt-roll-forward-balance-scope-v1"]
    assert debt["included_balance_sources"] == [
        {
            "source_sheet": "Liabilities",
            "source_row": 9,
            "predicate": "fdd.interest_bearing_debt_balance",
        }
    ]
    assert debt["excluded_balance_rows"] == [
        {"source_sheet": "Liabilities", "source_row": 17},
        {"source_sheet": "Liabilities", "source_row": 18},
    ]
    assert debt["included_movement_rows"] == [
        {"source_sheet": "Cash Flow", "source_row": 11},
        {"source_sheet": "Cash Flow", "source_row": 12},
    ]
    assert debt["excluded_rows_remain_required_answers"] is True
    assert debt["unmatched_movement_disposition"] == "OUT_OF_BALANCE"
    assert debt["runtime_scope_expansion"] == "FORBIDDEN"

    liabilities = scope_rows["fdd-liability-equity-tie-out-scope-v1"]
    assert [row["source_row"] for row in liabilities["included_component_rows"]] == list(
        range(4, 19)
    )
    assert [row["source_row"] for row in liabilities["excluded_off_balance_sheet_rows"]] == list(
        range(19, 25)
    )
    assert liabilities["excluded_rows_remain_required_answers"] is True
    assert liabilities["manufactured_reported_total"] == "FORBIDDEN"


@requires_operator_workbook
def test_debt_and_off_balance_scope_exclusions_are_deliberate_and_non_silent() -> None:
    _policy()


def test_completeness_facts_without_workbook() -> None:
    policy = load_policy()
    registry = policy["completeness_request_registry"]

    assert registry == expected_completeness_registry()
    assert len(registry["rows"]) == len(COMPLETENESS_REQUESTS) == 2
    for row in registry["rows"]:
        assert row["admitted_result"] == "CITED_BOOLEAN_TRUE_OR_EVIDENCE_NOT_FOUND"
        assert row["false_result"] == "FORBIDDEN"
        assert row["uncited_true"] == "FORBIDDEN"
        assert row["prose_only"] == "FORBIDDEN"
        request = row["request_text"]
        assert "cited" in request
        assert "EVIDENCE_NOT_FOUND" in request
        assert "Never infer completeness" in request


@requires_operator_workbook
def test_completeness_facts_require_cited_documentary_basis_and_natural_not_found() -> None:
    _policy()


def test_direct_signed_mappings_without_workbook() -> None:
    policy = load_policy()
    registry = policy["direct_signed_convention_registry"]
    assert registry == expected_direct_signed_registry()
    assert len(registry["rows"]) == len(DIRECT_SIGNED_CONVENTIONS) == 17

    by_predicate = {row["predicate"]: row for row in registry["rows"]}
    direct_bindings = [
        binding
        for formula in policy["formula_registry"]["rows"]
        for binding in formula["ordered_input_specs"]
        if binding["binding_kind"] == "SOURCE" and binding["sign_transform"] == "DIRECT_SIGNED"
    ]
    assert len(direct_bindings) == 19
    assert {binding["predicate"] for binding in direct_bindings} == set(by_predicate)
    for binding in direct_bindings:
        predicate = binding["predicate"]
        assert binding["direct_signed_convention_id"] == predicate
        convention = by_predicate[predicate]
        assert convention["positive_value"].strip()
        assert convention["negative_value"].strip()
        assert convention["extractor_request_requirement"].strip()

    assert registry["reported_delta_nwc_cash_effect_rule"] == {
        "predicate": "fdd.reported_delta_nwc",
        "source_semantics": "MOVEMENT",
        "derived_cash_effect": "-reported_delta_nwc",
        "cash_effect_input_under_movement_predicate": "FORBIDDEN",
    }
    delta_request = by_predicate["fdd.reported_delta_nwc"]["extractor_request_requirement"]
    assert "balance movement, not its cash effect" in delta_request
    assert "never return a cash-effect sign under this movement predicate" in (
        delta_request.lower()
    )


@requires_operator_workbook
def test_every_direct_signed_mapping_resolves_one_pinned_convention() -> None:
    _policy()


def test_incomplete_binding_schema_without_workbook() -> None:
    policy = load_policy()
    failure = policy["incomplete_binding_failure_schema"]

    assert failure["schema_version"] == "methodology-calculation-incomplete-v1"
    assert failure["code"] == "METHODOLOGY_CALCULATION_INCOMPLETE"
    assert failure["issue_kind_enum"] == [
        "MISSING",
        "DUPLICATE",
        "AMBIGUOUS",
        "UNCONSUMED",
    ]
    assert failure["resolution_mode"] == "COMPLETE_PASS_NEVER_FIRST_FAILURE"
    assert failure["issue_sort_tuple"] == [
        "formula_ordinal",
        "binding_ordinal",
        "issue_kind",
        "candidate_factual_output_ids",
    ]
    for field in (
        "role",
        "predicate",
        "source_sheet",
        "source_row",
        "economic_coordinate",
    ):
        assert field in failure["issue_field_tuple"]
    assert failure["private_fact_values"] == "FORBIDDEN"
    assert failure["claim_prose"] == "FORBIDDEN"
    assert failure["source_document_names"] == "FORBIDDEN"


@requires_operator_workbook
def test_incomplete_binding_failure_schema_enumerates_the_complete_safe_diagnostic() -> None:
    _policy()


def test_formula_hash_preimage_without_workbook() -> None:
    policy = load_policy()
    fields = policy["formula_registry"]["formula_hash_preimage_field_tuple"]
    first = policy["formula_registry"]["rows"][0]
    original = first["formula_sha256"]

    for field in fields:
        changed = copy.deepcopy(first)
        value = changed[field]
        if isinstance(value, list):
            changed[field] = [*value, "__corruption__"]
        elif isinstance(value, dict):
            changed[field] = {**value, "__corruption__": True}
        elif value is None:
            changed[field] = "__corruption__"
        elif isinstance(value, int):
            changed[field] = value + 1
        else:
            changed[field] = f"{value}__corruption__"
        assert formula_sha256(changed) != original, field


@requires_operator_workbook
def test_formula_hash_binds_every_governed_semantic_field() -> None:
    _policy()


@pytest.mark.parametrize("section", HASHED_COMPONENTS)
def test_governed_section_hash_changes_without_workbook(section: str) -> None:
    policy = load_policy()
    original = policy["digest_recipe_registry"]["fdd_formula_policy_v1_sha256"]
    changed = copy.deepcopy(policy)
    changed[section]["__corruption__"] = True

    assert recompute_policy_hash(changed) != original


@pytest.mark.parametrize("section", HASHED_COMPONENTS)
@requires_operator_workbook
def test_every_governed_artifact_section_changes_the_final_policy_hash(
    section: str,
) -> None:
    policy = _policy()
    changed = copy.deepcopy(policy)
    changed[section]["__corruption__"] = True
    with pytest.raises(OracleError):
        validate_policy(changed)


def test_hash_fixture_binding_without_workbook() -> None:
    policy = load_policy()
    fixture = load_fixture()
    review_map = require_review_map()
    component_hashes = recompute_component_hashes(policy)
    policy_hash = recompute_policy_hash(policy)

    assert fixture == {
        "artifact_schema": "fdd-formula-policy-v1-hash-fixture-v1",
        "policy_id": POLICY_ID,
        "artifact_sha256": sha256_bytes(POLICY_ARTIFACT.read_bytes()),
        "fdd_formula_policy_v1_sha256": policy_hash,
        "component_hashes": component_hashes,
        "review_map_sha256": sha256_bytes(review_map),
        "counts": {
            "question_count": 79,
            "required_question_count": 10,
            "empty_requirement_count": 69,
            "formula_count": 10,
            "expanded_source_binding_count": 107,
            "distinct_source_mapping_count": 101,
            "parent_binding_count": 9,
            "direct_signed_binding_count": 19,
            "completeness_binding_count": 2,
        },
        "activation_status": "INACTIVE",
    }
    assert POLICY_FIXTURE.is_file()
    assert REVIEW_MAP.is_file()


@requires_operator_workbook
def test_hash_fixture_binds_raw_artifact_policy_components_and_review_map() -> None:
    _policy()


def test_policy_hash_domain_separation_without_workbook() -> None:
    policy = load_policy()
    fixture = load_fixture()
    policy_hash = recompute_policy_hash(policy)

    assert policy["digest_recipe_registry"]["fdd_formula_policy_v1_sha256"] == policy_hash
    assert fixture["fdd_formula_policy_v1_sha256"] == policy_hash
    assert fixture["artifact_sha256"] == hashlib.sha256(POLICY_ARTIFACT.read_bytes()).hexdigest()
    assert "artifact_sha256" not in policy
    assert "artifact_sha256" not in policy["metadata"]
    assert "artifact_sha256" not in policy["digest_recipe_registry"]


@requires_operator_workbook
def test_policy_hash_is_domain_separated_while_raw_hash_is_fixture_only() -> None:
    _policy()

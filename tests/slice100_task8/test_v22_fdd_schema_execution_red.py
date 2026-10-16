"""Executable, implementation-independent FDD schema admission for v22.

These tests exercise the literal Draft 2020-12 bytes.  They intentionally do
not import the materializer or the future runtime models: a permissive model
implementation must not be able to make an incomplete frozen schema look
valid.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from . import _v22_oracle as oracle

REPO_ROOT = Path(__file__).resolve().parents[2]
V22_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v22_reviewed_preimage_v1.json"
POLICY_ARTIFACT = REPO_ROOT / "contracts" / "fdd_formula_policy_v1.json"

UUID_1 = "00000000-0000-4000-8000-000000000001"
UUID_2 = "00000000-0000-4000-8000-000000000002"
UUID_3 = "00000000-0000-4000-8000-000000000003"
UUID_4 = "00000000-0000-4000-8000-000000000004"
UUID_5 = "00000000-0000-4000-8000-000000000005"
UUID_6 = "00000000-0000-4000-8000-000000000006"
UUID_7 = "00000000-0000-4000-8000-000000000007"
UUID_8 = "00000000-0000-4000-8000-000000000008"
UUID_9 = "00000000-0000-4000-8000-000000000009"
COVERAGE_ID = "mc_0123456789abcdef01234567"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64

CALC_TYPES = (
    "GROSS_MARGIN",
    "FDD_EBITDA_BRIDGE",
    "FDD_EBIT_BRIDGE",
    "FDD_TAX_RECONCILIATION",
    "FDD_NET_INCOME_BRIDGE",
    "FDD_WORKING_CAPITAL_MOVEMENT",
    "FDD_FREE_CASH_FLOW_CASH_BRIDGE",
    "FDD_DEBT_ROLL_FORWARD",
    "FDD_ASSET_TIE_OUT",
    "FDD_LIABILITY_TIE_OUT",
)

FORMULA_FIELD_TUPLES: dict[str, tuple[str, ...]] = {
    "FddCalcInputsV1": (
        "schema_version",
        "formula_policy_id",
        "formula_policy_sha256",
        "formula_sha256",
        "coordinate",
        "bindings",
        "parent_calculations",
        "assumptions",
    ),
    "FddEconomicCoordinateV1": (
        "period_kind",
        "period_start",
        "period_end",
        "as_of_date",
        "base_currency",
        "monetary_scale_unit",
    ),
    "FddNumericBindingV1": (
        "binding_kind",
        "ordinal",
        "role",
        "source_decimal",
        "normalized_decimal",
        "sign_transform",
        "source_sheet",
        "source_row",
        "predicate",
        "coverage_record_id",
        "methodology_version_record_id",
        "methodology_question_record_id",
        "factual_output_record_id",
        "claim_id",
        "evidence_ids",
        "sanad_ids",
        "period",
        "currency",
        "unit",
        "value_sha256",
    ),
    "FddSetCompletenessBindingV1": (
        "binding_kind",
        "ordinal",
        "role",
        "confirmed",
        "source_sheet",
        "source_row",
        "predicate",
        "coverage_record_id",
        "methodology_version_record_id",
        "methodology_question_record_id",
        "factual_output_record_id",
        "claim_id",
        "evidence_ids",
        "sanad_ids",
        "period",
        "value_sha256",
    ),
    "FddParentCalculationV1": (
        "ordinal",
        "calc_type",
        "calc_id",
        "economic_coordinate_sha256",
        "formula_sha256",
        "reproducibility_sha256",
    ),
    "FddGovernedAssumptionV1": ("assumption_id", "value"),
    "FddCalcOutputV1": (
        "primary_name",
        "primary_decimal",
        "primary_scale",
        "secondary_values",
        "unit",
        "currency",
        "formula_disposition",
    ),
    "FddSecondaryOutputV1": ("name", "decimal", "scale", "unit", "currency"),
    "FddDeterministicCalculationV1": ("calc_type", "inputs", "output"),
}

FACTUAL_OUTPUT_FIELDS = (
    "subject",
    "predicate",
    "claim_class",
    "value",
    "period",
    "unit",
    "claim_text",
    "accepted_source_span_ids",
    "accepted_locator_sha256s",
    "methodology_question_record_id",
    "factual_output_id",
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _v22() -> dict[str, Any]:
    return _load_json(V22_ARTIFACT)


def _built_v22() -> dict[str, Any]:
    materializer = oracle.load_module(
        oracle.V22_MATERIALIZER,
        "_task8_v22_schema_materializer_red",
        "TASK8_V22_MATERIALIZER_NOT_IMPLEMENTED",
    )
    return materializer.build_v22(
        oracle.load_v21(),
        oracle.load_policy(),
        policy_artifact_sha256=hashlib.sha256(oracle.POLICY_ARTIFACT.read_bytes()).hexdigest(),
        policy_fixture_sha256=hashlib.sha256(oracle.POLICY_FIXTURE.read_bytes()).hexdigest(),
    )


def _policy_sha256() -> str:
    policy = _load_json(POLICY_ARTIFACT)
    value = policy["digest_recipe_registry"]["fdd_formula_policy_v1_sha256"]
    assert isinstance(value, str)
    return value


def _contract(
    schema_id: str,
    *,
    artifact: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    rows = (artifact or _v22())["task8_fdd_contract"]["fdd_schema_contracts"]
    matches = [row for row in rows if row["schema_id"] == schema_id]
    assert len(matches) == 1
    return matches[0]


def _all_definitions(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    definitions = copy.deepcopy(snapshot.get("$defs", {}))
    for _target_name, target in snapshot["targets"]:
        for name, schema in target.get("$defs", {}).items():
            existing = definitions.get(name)
            assert existing is None or existing == schema, name
            definitions[name] = copy.deepcopy(schema)
    return definitions


def _schema(
    schema_id: str,
    name: str,
    *,
    artifact: Mapping[str, Any] | None = None,
    definition: bool = False,
) -> dict[str, Any]:
    snapshot = _contract(schema_id, artifact=artifact)["normalized_schema_snapshot"]
    definitions = _all_definitions(snapshot)
    if definition:
        assert name in definitions
        schema = copy.deepcopy(definitions[name])
    else:
        targets = dict(snapshot["targets"])
        assert name in targets
        schema = copy.deepcopy(targets[name])
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$defs"] = definitions
    Draft202012Validator.check_schema(schema)
    return schema


def _errors(schema: Mapping[str, Any], payload: object) -> tuple[str, ...]:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return tuple(
        f"{'/'.join(str(item) for item in error.absolute_path)}:{error.message}"
        for error in sorted(
            validator.iter_errors(payload),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
    )


def _accepts(schema: Mapping[str, Any], payload: object) -> None:
    assert _errors(schema, payload) == ()


def _rejects(schema: Mapping[str, Any], payload: object) -> None:
    assert _errors(schema, payload)


def _local_refs(value: object) -> tuple[str, ...]:
    if isinstance(value, dict):
        refs = [value["$ref"]] if "$ref" in value else []
        return tuple(refs) + tuple(ref for child in value.values() for ref in _local_refs(child))
    if isinstance(value, list):
        return tuple(ref for child in value for ref in _local_refs(child))
    return ()


def _range_period() -> dict[str, Any]:
    return {
        "kind": "range",
        "date": None,
        "start_date": "2025-01-01",
        "end_date": "2025-12-31",
    }


def _as_of_period() -> dict[str, Any]:
    return {
        "kind": "as_of",
        "date": "2025-12-31",
        "start_date": None,
        "end_date": None,
    }


def _range_coordinate() -> dict[str, Any]:
    return {
        "period_kind": "range",
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "as_of_date": None,
        "base_currency": "QAR",
        "monetary_scale_unit": "units",
    }


def _as_of_coordinate() -> dict[str, Any]:
    return {
        "period_kind": "as_of",
        "period_start": None,
        "period_end": None,
        "as_of_date": "2025-12-31",
        "base_currency": "USD",
        "monetary_scale_unit": "thousands",
    }


def _factual_output(*, signed_ratio: bool) -> dict[str, Any]:
    if signed_ratio:
        value: dict[str, Any] = {
            "type": "signed_ratio",
            "value": "-0.2501",
            "as_of": None,
            "time_window": {
                "label": "FY2025",
                "start_date": "2025-01-01",
                "end_date": "2025-12-31",
            },
        }
        predicate = "fdd.reported_gross_margin_ratio"
        unit = "ratio"
    else:
        value = {
            "type": "monetary",
            "amount": "1250000",
            "currency": "QAR",
            "as_of": None,
            "time_window": {
                "label": "FY2025",
                "start_date": "2025-01-01",
                "end_date": "2025-12-31",
            },
        }
        predicate = "fdd.recurring_revenue"
        unit = "units"
    return {
        "subject": "company",
        "predicate": predicate,
        "claim_class": "FINANCIAL_FACT",
        "value": value,
        "period": _range_period(),
        "unit": unit,
        "claim_text": "Governed factual output with cited source lineage.",
        "accepted_source_span_ids": [UUID_6, UUID_7],
        "accepted_locator_sha256s": [SHA_A, SHA_B],
        "methodology_question_record_id": UUID_2,
        "factual_output_id": UUID_3,
    }


def _numeric_binding(*, ordinal: int = 0) -> dict[str, Any]:
    return {
        "binding_kind": "NUMERIC",
        "ordinal": ordinal,
        "role": "reported_value",
        "source_decimal": "1250000",
        "normalized_decimal": "1250000",
        "sign_transform": "DIRECT_SIGNED",
        "source_sheet": "P&L",
        "source_row": 30,
        "predicate": "fdd.reported_net_income",
        "coverage_record_id": COVERAGE_ID,
        "methodology_version_record_id": UUID_1,
        "methodology_question_record_id": UUID_2,
        "factual_output_record_id": UUID_3,
        "claim_id": UUID_4,
        "evidence_ids": [UUID_5],
        "sanad_ids": [UUID_6],
        "period": _range_period(),
        "currency": "QAR",
        "unit": "units",
        "value_sha256": SHA_C,
    }


def _set_completeness_binding(*, ordinal: int = 1) -> dict[str, Any]:
    return {
        "binding_kind": "SET_COMPLETENESS",
        "ordinal": ordinal,
        "role": "reconciling_item_set",
        "confirmed": True,
        "source_sheet": "P&L",
        "source_row": 30,
        "predicate": "fdd.tax_reconciling_item_set_complete",
        "coverage_record_id": COVERAGE_ID,
        "methodology_version_record_id": UUID_1,
        "methodology_question_record_id": UUID_2,
        "factual_output_record_id": UUID_3,
        "claim_id": UUID_4,
        "evidence_ids": [UUID_5],
        "sanad_ids": [UUID_6],
        "period": _range_period(),
        "value_sha256": SHA_D,
    }


def _parent_calculation() -> dict[str, Any]:
    return {
        "ordinal": 0,
        "calc_type": "FDD_TAX_RECONCILIATION",
        "calc_id": UUID_8,
        "economic_coordinate_sha256": SHA_A,
        "formula_sha256": SHA_B,
        "reproducibility_sha256": SHA_C,
    }


def _secondary_output(*, ratio: bool = False) -> dict[str, Any]:
    if ratio:
        return {
            "name": "variance_ratio",
            "decimal": "-0.015",
            "scale": 4,
            "unit": "ratio",
            "currency": None,
        }
    return {
        "name": "variance",
        "decimal": "-25000",
        "scale": 2,
        "unit": "units",
        "currency": "QAR",
    }


def _formula_output(*, ratio: bool = False) -> dict[str, Any]:
    if ratio:
        return {
            "primary_name": "gross_margin",
            "primary_decimal": "-0.2501",
            "primary_scale": 4,
            "secondary_values": [_secondary_output(ratio=True)],
            "unit": "ratio",
            "currency": None,
            "formula_disposition": "CALCULATED",
        }
    return {
        "primary_name": "net_income_variance",
        "primary_decimal": "-25000",
        "primary_scale": 2,
        "secondary_values": [_secondary_output()],
        "unit": "units",
        "currency": "QAR",
        "formula_disposition": "OUT_OF_BALANCE",
    }


def _formula() -> dict[str, Any]:
    return {
        "calc_type": "FDD_NET_INCOME_BRIDGE",
        "inputs": {
            "schema_version": "fdd-calc-inputs-v1",
            "formula_policy_id": "fdd-formula-policy-v1",
            "formula_policy_sha256": _policy_sha256(),
            "formula_sha256": SHA_A,
            "coordinate": _range_coordinate(),
            "bindings": [_numeric_binding(), _set_completeness_binding()],
            "parent_calculations": [_parent_calculation()],
            "assumptions": [],
        },
        "output": _formula_output(),
    }


def test_every_fdd_target_and_definition_is_valid_draft_2020_12_with_closed_refs() -> None:
    expected_targets = {
        "fdd-methodology-factual-output": {
            "FddSignedRatioValueV1",
            "FddMethodologyFactualOutputV1",
        },
        "fdd-deterministic-calculation": {
            "FddDeterministicCalculationV1",
            "FddCalcInputsV1",
            "FddCalcOutputV1",
        },
    }
    for schema_id, target_names in expected_targets.items():
        snapshot = _contract(schema_id)["normalized_schema_snapshot"]
        definitions = _all_definitions(snapshot)
        assert {name for name, _schema_value in snapshot["targets"]} == target_names
        for target_name in target_names:
            schema = _schema(schema_id, target_name)
            assert all(
                ref.startswith("#/$defs/") and ref.removeprefix("#/$defs/") in definitions
                for ref in _local_refs(schema)
            )
        for definition_name in definitions:
            schema = _schema(schema_id, definition_name, definition=True)
            assert all(
                ref.startswith("#/$defs/") and ref.removeprefix("#/$defs/") in definitions
                for ref in _local_refs(schema)
            )


def test_fdd_models_have_the_exact_reviewed_closed_field_sets() -> None:
    signed = _schema(
        "fdd-methodology-factual-output",
        "FddSignedRatioValueV1",
        definition=True,
    )
    assert tuple(signed["required"]) == ("type", "value", "as_of", "time_window")
    assert set(signed["properties"]) == set(signed["required"])
    assert signed["additionalProperties"] is False

    factual = _schema("fdd-methodology-factual-output", "FddMethodologyFactualOutputV1")
    assert set(factual["properties"]) == set(FACTUAL_OUTPUT_FIELDS)
    assert set(factual["required"]) == set(FACTUAL_OUTPUT_FIELDS)
    assert factual["additionalProperties"] is False

    for model_name, fields in FORMULA_FIELD_TUPLES.items():
        schema = _schema(
            "fdd-deterministic-calculation",
            model_name,
            definition=True,
        )
        assert tuple(schema["required"]) == fields
        assert set(schema["properties"]) == set(fields)
        assert schema["additionalProperties"] is False

    calc_type = _schema("fdd-deterministic-calculation", "FddCalcTypeV1", definition=True)
    assert tuple(calc_type["enum"]) == CALC_TYPES


def test_fdd_factual_schema_accepts_realistic_signed_ratio_and_frozen_legacy_value() -> None:
    schema = _schema("fdd-methodology-factual-output", "FddMethodologyFactualOutputV1")
    _accepts(schema, _factual_output(signed_ratio=True))
    _accepts(schema, _factual_output(signed_ratio=False))

    as_of = _factual_output(signed_ratio=True)
    as_of["value"]["as_of"] = "2025-12-31"
    as_of["value"]["time_window"] = None
    as_of["period"] = _as_of_period()
    _accepts(schema, as_of)


def test_fdd_factual_source_locator_pair_has_every_json_schema_expressible_bound() -> None:
    schema = _schema("fdd-methodology-factual-output", "FddMethodologyFactualOutputV1")
    spans = schema["properties"]["accepted_source_span_ids"]
    locators = schema["properties"]["accepted_locator_sha256s"]
    assert spans["minItems"] == locators["minItems"] == 1
    assert spans["uniqueItems"] is True
    assert spans["items"]["pattern"] == (
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    assert locators["items"]["pattern"] == r"^[0-9a-f]{64}$"
    # Draft 2020-12 cannot compare the lengths of sibling arrays.  The exact
    # runtime constraint therefore remains hash-bound as an explicit tuple.
    assert schema["x-idis-cross-field-constraint-tuple"] == [
        [
            "accepted-source-locator-cardinality-v1",
            "len(accepted_source_span_ids)=len(accepted_locator_sha256s)",
        ],
        [
            "methodology-factual-output-uuid5-v1",
            "factual_output_id=NULL_OR_UUID5(canonical_factual_output_preimage)",
        ],
        [
            "signed-ratio-period-coordinate-equality-v1",
            (
                "value.as_of=period.date OR "
                "value.time_window.(start_date,end_date)="
                "period.(start_date,end_date)"
            ),
        ],
    ]


def test_fdd_calc_inputs_cross_field_constraints_are_exact_and_mutation_sensitive() -> None:
    artifact = _built_v22()
    schema = _schema(
        "fdd-deterministic-calculation",
        "FddCalcInputsV1",
        artifact=artifact,
        definition=True,
    )
    expected = [
        [
            "binding-ordinal-total-order-v1",
            "bindings.ordinal=0..N-1",
        ],
        [
            "parent-calculation-ordinal-total-order-v1",
            "parent_calculations.ordinal=0..N-1",
        ],
        [
            "economic-coordinate-date-order-v1",
            "period_start<period_end AND as_of_date XOR (period_start,period_end)",
        ],
    ]

    def assert_exact(candidate: Mapping[str, Any]) -> None:
        assert candidate["x-idis-cross-field-constraint-tuple"] == expected

    assert_exact(schema)
    removed = copy.deepcopy(schema)
    removed.pop("x-idis-cross-field-constraint-tuple")
    with pytest.raises((AssertionError, KeyError)):
        assert_exact(removed)
    changed = copy.deepcopy(schema)
    changed["x-idis-cross-field-constraint-tuple"][0][1] = "bindings.ordinal=1..N"
    with pytest.raises(AssertionError):
        assert_exact(changed)


def test_numeric_binding_preserves_source_value_and_rejects_negative_positive_magnitude() -> None:
    artifact = _built_v22()
    schema = _schema(
        "fdd-deterministic-calculation",
        "FddNumericBindingV1",
        artifact=artifact,
        definition=True,
    )
    assert schema["x-idis-cross-field-constraint-tuple"] == [
        [
            "numeric-binding-source-normalization-v1",
            "normalized_decimal=source_decimal",
        ],
        [
            "positive-magnitude-source-nonnegative-v1",
            (
                "sign_transform='POSITIVE_MAGNITUDE' => "
                "decimal(source_decimal)>=0 AND decimal(normalized_decimal)>=0"
            ),
        ],
    ]

    positive = _numeric_binding()
    positive["sign_transform"] = "POSITIVE_MAGNITUDE"
    _accepts(schema, positive)

    negative_positive = copy.deepcopy(positive)
    negative_positive["source_decimal"] = "-1"
    negative_positive["normalized_decimal"] = "-1"
    _rejects(schema, negative_positive)

    negative_direct = copy.deepcopy(negative_positive)
    negative_direct["sign_transform"] = "DIRECT_SIGNED"
    _accepts(schema, negative_direct)


@pytest.mark.parametrize(
    "case",
    (
        "missing-question-id",
        "malformed-question-id",
        "missing-factual-output-id",
        "malformed-factual-output-id",
        "empty-spans",
        "duplicate-spans",
        "malformed-span-id",
        "empty-locators",
        "malformed-locator-hash",
        "wrong-ratio-unit",
        "both-period-forms",
        "neither-period-form",
        "malformed-date",
        "binary-float",
        "boolean",
        "nan",
        "positive-infinity",
        "negative-infinity",
        "negative-zero",
        "exponent",
        "trailing-zero",
        "leading-zero",
        "unknown-discriminator",
        "extra-field",
        "missing-field",
    ),
)
def test_fdd_factual_schema_rejects_invalid_identity_lineage_value_and_period(
    case: str,
) -> None:
    schema = _schema("fdd-methodology-factual-output", "FddMethodologyFactualOutputV1")
    payload = _factual_output(signed_ratio=True)
    if case == "missing-question-id":
        del payload["methodology_question_record_id"]
    elif case == "malformed-question-id":
        payload["methodology_question_record_id"] = "not-a-uuid"
    elif case == "missing-factual-output-id":
        del payload["factual_output_id"]
    elif case == "malformed-factual-output-id":
        payload["factual_output_id"] = "not-a-uuid"
    elif case == "empty-spans":
        payload["accepted_source_span_ids"] = []
    elif case == "duplicate-spans":
        payload["accepted_source_span_ids"] = [UUID_6, UUID_6]
    elif case == "malformed-span-id":
        payload["accepted_source_span_ids"][0] = "span-1"
    elif case == "empty-locators":
        payload["accepted_locator_sha256s"] = []
    elif case == "malformed-locator-hash":
        payload["accepted_locator_sha256s"][0] = "A" * 64
    elif case == "wrong-ratio-unit":
        payload["unit"] = "percentage_points"
    elif case == "both-period-forms":
        payload["value"]["as_of"] = "2025-12-31"
    elif case == "neither-period-form":
        payload["value"]["time_window"] = None
    elif case == "malformed-date":
        payload["value"]["time_window"]["end_date"] = "2025-13-40"
    elif case == "binary-float":
        payload["value"]["value"] = -0.2501
    elif case == "boolean":
        payload["value"]["value"] = True
    elif case == "nan":
        payload["value"]["value"] = "NaN"
    elif case == "positive-infinity":
        payload["value"]["value"] = "Infinity"
    elif case == "negative-infinity":
        payload["value"]["value"] = "-Infinity"
    elif case == "negative-zero":
        payload["value"]["value"] = "-0"
    elif case == "exponent":
        payload["value"]["value"] = "-2.501e-1"
    elif case == "trailing-zero":
        payload["value"]["value"] = "-0.25010"
    elif case == "leading-zero":
        payload["value"]["value"] = "-00.2501"
    elif case == "unknown-discriminator":
        payload["value"]["type"] = "ratio"
    elif case == "extra-field":
        payload["value"]["percentage_points"] = -25.01
    else:
        del payload["value"]["as_of"]
    _rejects(schema, payload)


def test_fdd_formula_schema_executes_numeric_completeness_parent_and_secondary_shapes() -> None:
    calculation = _formula()
    _accepts(
        _schema("fdd-deterministic-calculation", "FddDeterministicCalculationV1"),
        calculation,
    )
    _accepts(
        _schema("fdd-deterministic-calculation", "FddCalcInputsV1"),
        calculation["inputs"],
    )
    _accepts(
        _schema("fdd-deterministic-calculation", "FddCalcOutputV1"),
        calculation["output"],
    )
    _accepts(
        _schema("fdd-deterministic-calculation", "FddNumericBindingV1", definition=True),
        calculation["inputs"]["bindings"][0],
    )
    _accepts(
        _schema(
            "fdd-deterministic-calculation",
            "FddSetCompletenessBindingV1",
            definition=True,
        ),
        calculation["inputs"]["bindings"][1],
    )
    _accepts(
        _schema(
            "fdd-deterministic-calculation",
            "FddParentCalculationV1",
            definition=True,
        ),
        calculation["inputs"]["parent_calculations"][0],
    )
    _accepts(
        _schema(
            "fdd-deterministic-calculation",
            "FddSecondaryOutputV1",
            definition=True,
        ),
        calculation["output"]["secondary_values"][0],
    )


def test_fdd_formula_period_coordinate_currency_unit_and_scale_legal_variants() -> None:
    period = _schema("fdd-deterministic-calculation", "FddMethodologyPeriodV1", definition=True)
    coordinate = _schema(
        "fdd-deterministic-calculation", "FddEconomicCoordinateV1", definition=True
    )
    output = _schema("fdd-deterministic-calculation", "FddCalcOutputV1", definition=True)
    secondary = _schema("fdd-deterministic-calculation", "FddSecondaryOutputV1", definition=True)

    _accepts(period, _range_period())
    _accepts(period, _as_of_period())
    _accepts(coordinate, _range_coordinate())
    _accepts(coordinate, _as_of_coordinate())
    _accepts(output, _formula_output())
    _accepts(output, _formula_output(ratio=True))
    _accepts(secondary, _secondary_output())
    _accepts(secondary, _secondary_output(ratio=True))


@pytest.mark.parametrize(
    "case",
    (
        "bad-coverage-prefix",
        "uppercase-coverage-hash",
        "malformed-version-id",
        "malformed-question-id",
        "malformed-factual-id",
        "malformed-claim-id",
        "malformed-evidence-id",
        "malformed-sanad-id",
        "duplicate-evidence-id",
        "empty-evidence-ids",
        "empty-sanad-ids",
        "malformed-value-hash",
        "wrong-policy-hash",
        "malformed-formula-hash",
        "malformed-parent-id",
        "malformed-parent-coordinate-hash",
        "malformed-parent-formula-hash",
        "malformed-parent-reproducibility-hash",
    ),
)
def test_fdd_formula_schema_rejects_malformed_ids_hashes_and_lineage(case: str) -> None:
    schema = _schema("fdd-deterministic-calculation", "FddDeterministicCalculationV1")
    payload = _formula()
    numeric = payload["inputs"]["bindings"][0]
    parent = payload["inputs"]["parent_calculations"][0]
    if case == "bad-coverage-prefix":
        numeric["coverage_record_id"] = UUID_1
    elif case == "uppercase-coverage-hash":
        numeric["coverage_record_id"] = "mc_0123456789ABCDEF01234567"
    elif case == "malformed-version-id":
        numeric["methodology_version_record_id"] = "not-a-uuid"
    elif case == "malformed-question-id":
        numeric["methodology_question_record_id"] = "not-a-uuid"
    elif case == "malformed-factual-id":
        numeric["factual_output_record_id"] = "not-a-uuid"
    elif case == "malformed-claim-id":
        numeric["claim_id"] = "not-a-uuid"
    elif case == "malformed-evidence-id":
        numeric["evidence_ids"] = ["not-a-uuid"]
    elif case == "malformed-sanad-id":
        numeric["sanad_ids"] = ["not-a-uuid"]
    elif case == "duplicate-evidence-id":
        numeric["evidence_ids"] = [UUID_5, UUID_5]
    elif case == "empty-evidence-ids":
        numeric["evidence_ids"] = []
    elif case == "empty-sanad-ids":
        numeric["sanad_ids"] = []
    elif case == "malformed-value-hash":
        numeric["value_sha256"] = "A" * 64
    elif case == "wrong-policy-hash":
        payload["inputs"]["formula_policy_sha256"] = "0" * 64
    elif case == "malformed-formula-hash":
        payload["inputs"]["formula_sha256"] = "sha256"
    elif case == "malformed-parent-id":
        parent["calc_id"] = "not-a-uuid"
    elif case == "malformed-parent-coordinate-hash":
        parent["economic_coordinate_sha256"] = "bad"
    elif case == "malformed-parent-formula-hash":
        parent["formula_sha256"] = "bad"
    else:
        parent["reproducibility_sha256"] = "bad"
    _rejects(schema, payload)


@pytest.mark.parametrize(
    "case",
    (
        "empty-bindings",
        "fractional-ordinal",
        "negative-ordinal",
        "unknown-discriminator",
        "numeric-labeled-completeness",
        "completeness-labeled-numeric",
        "false-completeness",
        "completeness-extra-currency",
        "nonempty-assumptions",
        "unknown-calc-type",
        "wrong-input-version",
        "wrong-policy-id",
        "extra-top-level-field",
        "extra-nested-field",
        "missing-top-level-field",
        "missing-nested-field",
    ),
)
def test_fdd_formula_schema_rejects_bad_arrays_ordinals_discriminators_and_closure(
    case: str,
) -> None:
    schema = _schema("fdd-deterministic-calculation", "FddDeterministicCalculationV1")
    payload = _formula()
    numeric = payload["inputs"]["bindings"][0]
    completeness = payload["inputs"]["bindings"][1]
    if case == "empty-bindings":
        payload["inputs"]["bindings"] = []
    elif case == "fractional-ordinal":
        numeric["ordinal"] = 0.5
    elif case == "negative-ordinal":
        numeric["ordinal"] = -1
    elif case == "unknown-discriminator":
        numeric["binding_kind"] = "OTHER"
    elif case == "numeric-labeled-completeness":
        numeric["binding_kind"] = "SET_COMPLETENESS"
    elif case == "completeness-labeled-numeric":
        completeness["binding_kind"] = "NUMERIC"
    elif case == "false-completeness":
        completeness["confirmed"] = False
    elif case == "completeness-extra-currency":
        completeness["currency"] = "QAR"
    elif case == "nonempty-assumptions":
        payload["inputs"]["assumptions"] = [{"assumption_id": "unreviewed-default", "value": "0"}]
    elif case == "unknown-calc-type":
        payload["calc_type"] = "CUSTOM"
    elif case == "wrong-input-version":
        payload["inputs"]["schema_version"] = "fdd-calc-inputs-v2"
    elif case == "wrong-policy-id":
        payload["inputs"]["formula_policy_id"] = "fdd-formula-policy-v2"
    elif case == "extra-top-level-field":
        payload["extension"] = {}
    elif case == "extra-nested-field":
        numeric["source_document_name"] = "private-name.pdf"
    elif case == "missing-top-level-field":
        del payload["output"]
    else:
        del numeric["predicate"]
    _rejects(schema, payload)


@pytest.mark.parametrize(
    "case",
    (
        "binary-float",
        "boolean",
        "negative-zero",
        "exponent",
        "trailing-zero",
        "range-with-as-of",
        "range-missing-end",
        "as-of-with-start",
        "none-period",
        "coordinate-range-with-as-of",
        "coordinate-as-of-with-end",
        "coordinate-missing-date",
        "bad-date",
        "unsupported-currency",
        "lowercase-currency",
        "blank-scale-unit",
        "ratio-with-currency",
        "ratio-with-monetary-scale",
        "monetary-with-null-currency",
        "monetary-with-ratio-scale",
        "secondary-ratio-with-currency",
        "secondary-monetary-with-null-currency",
        "invalid-disposition",
    ),
)
def test_fdd_formula_schema_rejects_noncanonical_numeric_period_currency_unit_and_scale(
    case: str,
) -> None:
    schema = _schema("fdd-deterministic-calculation", "FddDeterministicCalculationV1")
    payload = _formula()
    numeric = payload["inputs"]["bindings"][0]
    coordinate = payload["inputs"]["coordinate"]
    output = payload["output"]
    secondary = output["secondary_values"][0]
    if case == "binary-float":
        numeric["normalized_decimal"] = 1.25
    elif case == "boolean":
        numeric["normalized_decimal"] = True
    elif case == "negative-zero":
        output["primary_decimal"] = "-0"
    elif case == "exponent":
        output["primary_decimal"] = "-2.5e4"
    elif case == "trailing-zero":
        output["primary_decimal"] = "-25000.00"
    elif case == "range-with-as-of":
        numeric["period"]["date"] = "2025-12-31"
    elif case == "range-missing-end":
        numeric["period"]["end_date"] = None
    elif case == "as-of-with-start":
        numeric["period"] = _as_of_period()
        numeric["period"]["start_date"] = "2025-01-01"
    elif case == "none-period":
        numeric["period"] = {
            "kind": "none",
            "date": None,
            "start_date": None,
            "end_date": None,
        }
    elif case == "coordinate-range-with-as-of":
        coordinate["as_of_date"] = "2025-12-31"
    elif case == "coordinate-as-of-with-end":
        payload["inputs"]["coordinate"] = _as_of_coordinate()
        payload["inputs"]["coordinate"]["period_end"] = "2025-12-31"
    elif case == "coordinate-missing-date":
        payload["inputs"]["coordinate"] = _as_of_coordinate()
        payload["inputs"]["coordinate"]["as_of_date"] = None
    elif case == "bad-date":
        coordinate["period_end"] = "2025-13-40"
    elif case == "unsupported-currency":
        coordinate["base_currency"] = "ZZZ"
    elif case == "lowercase-currency":
        numeric["currency"] = "qar"
    elif case == "blank-scale-unit":
        coordinate["monetary_scale_unit"] = ""
    elif case == "ratio-with-currency":
        payload["output"] = _formula_output(ratio=True)
        payload["output"]["currency"] = "QAR"
    elif case == "ratio-with-monetary-scale":
        payload["output"] = _formula_output(ratio=True)
        payload["output"]["primary_scale"] = 2
    elif case == "monetary-with-null-currency":
        output["currency"] = None
    elif case == "monetary-with-ratio-scale":
        output["primary_scale"] = 4
    elif case == "secondary-ratio-with-currency":
        output["secondary_values"] = [_secondary_output(ratio=True)]
        output["secondary_values"][0]["currency"] = "QAR"
    elif case == "secondary-monetary-with-null-currency":
        secondary["currency"] = None
    else:
        output["formula_disposition"] = "PARTIAL"
    _rejects(schema, payload)

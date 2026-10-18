"""Task 8.2 pre-slice contracts for the policy-v2 requirement model.

The production FDD workbook is private operator input.  Tests that need its
bytes use the already-reviewed local Task-5 source when it is available; the
checked-in policy-v2 artifact remains the frozen, hash-bound authority.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import hmac
import importlib.util
import inspect
import json
import uuid
from dataclasses import asdict
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from idis.methodology.importers.cdd_json import import_cdd_template
from idis.methodology.importers.fdd_excel import import_fdd_workbook
from idis.methodology.models import MethodologyQuestion, MethodologyRegistry
from idis.persistence.repositories.methodologies import (
    PostgresMethodologiesRepository,
    methodology_question_payload_hashes,
    methodology_source_trace_digest,
    methodology_version_hash,
)
from tests.slice100_task8._artifact_oracle import requires_operator_workbook

REPO_ROOT = Path(__file__).resolve().parents[2]
CDD_TEMPLATE = REPO_ROOT / "src" / "idis" / "methodology" / "templates" / "commercial_dd_v1.json"
POLICY_V2 = REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
V24_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v24_reviewed_preimage_v1.json"
V21_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
V21_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v21_contract_hash_v1.json"
)
V24_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "full_strict_v24_contract_hash_v1.json"
)
V24_REVIEWER_MAP = (
    REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
V24_MATERIALIZER = REPO_ROOT / "scripts" / "materialize_full_strict_v24_preimage.py"
CURRENT_0038_RUN_INSERT_SQL = (
    "INSERT INTO runs "
    "(run_id,tenant_id,deal_id,mode,status,started_at,created_at,"
    "pipeline_contract_version,pipeline_contract_hash,pipeline_resume_state,"
    "pipeline_resume_error_code) "
    "VALUES (CAST(:run_id AS uuid),CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid),"
    "'FULL','QUEUED',now(),now(),:pipeline_contract_version,:pipeline_contract_hash,"
    ":pipeline_resume_state,:pipeline_resume_error_code)"
)

WORKBOOK_SHA256 = "a3b83addfff02815c9fd44c4a3dcb7f56ce39410fe182faff12acfb845bd4352"
POLICY_ARTIFACT_SHA256 = "e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064"
POLICY_SHA256 = "2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9"
SUCCESSOR_VERSION_ID = f"financial_dd:{WORKBOOK_SHA256}:fdd-formula-policy-v2:{POLICY_SHA256}"
V24_ARTIFACT_SHA256 = "70834e33249a414e5dcc744cdc60934d9d2ed6c8ab3f09c64cf3edfa3f05e447"

EXPECTED_FORMULA_TYPES = {
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
}
EXPECTED_SCOPES = {
    "line_item",
    "p_and_l_control",
    "cash_flow_control",
    "debt_control",
    "sheet_control",
}

# These digests were captured from the actual frozen Task-5 source bytes before
# the pre-slice production change.  The tuple digest binds every ordered
# question_payload_hash, not a sample.
FROZEN_TASK5_BYTES: dict[str, dict[str, str | int]] = {
    "commercial_dd": {
        "question_count": 21,
        "registry_sha256": "13f38ef7afa9e96828ff723d32b650a16a9d4817f4dd6b5fab1bf0aafcdde2db",
        "question_payload_hash_tuple_sha256": (
            "e3fa416430481f658564b251ef80fbfd81364f46263d3bae0994c993a59a06ca"
        ),
        "source_trace_digest": "919dd0c838641d2841ddce76679c15c508a83d584aa163ce0480c0d4fdd7f6d9",
        "version_hash": "3e32fa8728949c06772ea60b91f8dd0becbec849bd494a89bb4806528aa3f227",
        "rehydration_sha256": "13f38ef7afa9e96828ff723d32b650a16a9d4817f4dd6b5fab1bf0aafcdde2db",
    },
    "financial_dd": {
        "question_count": 79,
        "registry_sha256": "229950a1571699223e22706dc1cfa760a4024c1904b2118719b4be8467e6cf2a",
        "question_payload_hash_tuple_sha256": (
            "cb1f79a8e8e60b19c969505d0eca5745a7a9626cb1ebe29cc2901a63718d9615"
        ),
        "source_trace_digest": "6e295e13990ac226a64a47be84b43ba5635a238f13ea943736bc24d41a67d119",
        "version_hash": "d8e6d7e9dd937c28d9d41664f6ad2f2317791700718b91df9788729705e55663",
        "rehydration_sha256": "229950a1571699223e22706dc1cfa760a4024c1904b2118719b4be8467e6cf2a",
    },
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _assert_safe_bytes_equal(observed: bytes, expected: bytes, *, code: str) -> None:
    if len(observed) != len(expected) or not hmac.compare_digest(
        hashlib.sha256(observed).digest(),
        hashlib.sha256(expected).digest(),
    ):
        pytest.fail(code, pytrace=False)


def _assert_safe_value_equal(observed: object, expected: object, *, code: str) -> None:
    if not hmac.compare_digest(
        hashlib.sha256(_canonical(observed)).digest(),
        hashlib.sha256(_canonical(expected)).digest(),
    ):
        pytest.fail(code, pytrace=False)


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _operator_workbook() -> Path:
    from tests.slice100_task8._artifact_oracle import require_operator_workbook

    path = require_operator_workbook()
    try:
        raw = path.read_bytes()
    except OSError:
        pytest.fail("TASK8_FROZEN_WORKBOOK_UNAVAILABLE", pytrace=False)
    if len(raw) != 24_905 or hashlib.sha256(raw).hexdigest() != WORKBOOK_SHA256:
        pytest.fail("TASK8_FROZEN_WORKBOOK_IDENTITY_MISMATCH", pytrace=False)
    return path


def test_present_fake_workbook_stays_an_identity_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A present file is not recorded as not-executed; identity still fails closed."""

    workbook = tmp_path / "fake.xlsx"
    workbook.write_bytes(b"not-the-pinned-workbook")
    monkeypatch.setenv("IDIS_OPERATOR_WORKBOOK_PATH", str(workbook))
    monkeypatch.delenv("IDIS_REQUIRE_OPERATOR_WORKBOOK", raising=False)

    with pytest.raises(pytest.fail.Exception, match="^TASK8_FROZEN_WORKBOOK_IDENTITY_MISMATCH$"):
        _operator_workbook()

    assert "OPERATOR_WORKBOOK NOT_EXECUTED" not in capsys.readouterr().out


def _policy() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(POLICY_V2.read_text("utf-8")))


def _current_0038_run_coordinate() -> dict[str, str | None]:
    fixture = json.loads(V21_FIXTURE.read_text("utf-8"))
    return {
        "pipeline_contract_version": fixture["pipeline_contract_version"],
        "pipeline_contract_hash": fixture["FULL_STRICT_V21_CONTRACT_HASH"],
        "pipeline_resume_state": "RESUMABLE",
        "pipeline_resume_error_code": None,
    }


def _assert_frozen_task5_bytes(
    registry: MethodologyRegistry,
    expected: dict[str, str | int],
) -> None:
    raw = registry.to_deterministic_json().encode("utf-8")
    payload_hashes = methodology_question_payload_hashes(registry)
    if len(payload_hashes) != expected["question_count"]:
        pytest.fail("TASK8_FROZEN_METHODOLOGY_COUNT_DRIFT", pytrace=False)
    _assert_safe_value_equal(
        hashlib.sha256(raw).hexdigest(),
        expected["registry_sha256"],
        code="TASK8_FROZEN_METHODOLOGY_REGISTRY_DRIFT",
    )
    _assert_safe_value_equal(
        hashlib.sha256(_canonical(list(payload_hashes))).hexdigest(),
        expected["question_payload_hash_tuple_sha256"],
        code="TASK8_FROZEN_METHODOLOGY_PAYLOAD_DRIFT",
    )
    _assert_safe_value_equal(
        methodology_source_trace_digest(registry),
        expected["source_trace_digest"],
        code="TASK8_FROZEN_METHODOLOGY_TRACE_DRIFT",
    )
    _assert_safe_value_equal(
        methodology_version_hash(registry),
        expected["version_hash"],
        code="TASK8_FROZEN_METHODOLOGY_VERSION_DRIFT",
    )

    rehydrated = MethodologyRegistry.model_validate_json(raw)
    rehydrated_raw = rehydrated.to_deterministic_json().encode("utf-8")
    _assert_safe_bytes_equal(
        rehydrated_raw,
        raw,
        code="TASK8_FROZEN_METHODOLOGY_REHYDRATION_DRIFT",
    )
    _assert_safe_value_equal(
        hashlib.sha256(rehydrated_raw).hexdigest(),
        expected["rehydration_sha256"],
        code="TASK8_FROZEN_METHODOLOGY_REHYDRATION_HASH_DRIFT",
    )
    _assert_safe_value_equal(
        list(methodology_question_payload_hashes(rehydrated)),
        list(payload_hashes),
        code="TASK8_FROZEN_METHODOLOGY_REHYDRATED_PAYLOAD_DRIFT",
    )


def _successor_registry() -> MethodologyRegistry:
    from idis.methodology.importers.fdd_formula_policy_v2 import (
        import_fdd_formula_policy_v2_registry,
    )

    return import_fdd_formula_policy_v2_registry(
        _operator_workbook(),
        policy_artifact=POLICY_V2,
    )


def _rehash_policy(policy: dict[str, Any]) -> bytes:
    """Independent test-only renderer for deep mismatch vectors."""

    digest = policy["digest_recipe_registry"]
    component_order = digest["component_order"]
    component_hashes = {
        section: hashlib.sha256(
            _canonical(["fdd-formula-policy-v2-component-v1", section, policy[section]])
        ).hexdigest()
        for section in component_order
    }
    preimage = [
        "fdd-formula-policy-v2",
        [[section, component_hashes[section]] for section in component_order],
    ]
    digest["component_hashes"] = component_hashes
    digest["policy_hash_preimage"] = preimage
    digest["fdd_formula_policy_v2_sha256"] = hashlib.sha256(_canonical(preimage)).hexdigest()
    return (json.dumps(policy, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode(
        "utf-8"
    )


def test_private_question_byte_comparisons_emit_only_a_safe_stable_code() -> None:
    private_marker = "PRIVATE_FDD_QUESTION_PROSE_MUST_NOT_LEAK"
    with pytest.raises(pytest.fail.Exception) as raised:
        _assert_safe_bytes_equal(
            private_marker.encode("utf-8"),
            b"different",
            code="TASK8_FROZEN_METHODOLOGY_BYTES_DRIFT",
        )
    assert str(raised.value) == "TASK8_FROZEN_METHODOLOGY_BYTES_DRIFT"
    assert private_marker not in str(raised.value)

    source = inspect.getsource(_assert_frozen_task5_bytes)
    assert "rehydrated_raw == raw" not in source
    assert "question.model_dump" not in source
    tree = ast.parse(Path(__file__).read_text("utf-8"))
    rendered_equalities = [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare) and any(isinstance(op, ast.Eq) for op in node.ops)
    ]
    assert not any("to_deterministic_json()" in row for row in rendered_equalities)
    assert not any("question.model_dump(" in row for row in rendered_equalities)


def test_fdd_formula_requirement_is_closed_and_uses_only_frozen_enums() -> None:
    from idis.methodology.models import (
        FddFormulaRequirement,
        FddFormulaScope,
        FddFormulaType,
    )

    assert {item.value for item in FddFormulaType} == EXPECTED_FORMULA_TYPES
    assert {item.value for item in FddFormulaScope} == EXPECTED_SCOPES
    for row in _policy()["methodology_transition"]["successor_required_map"]:
        requirement = FddFormulaRequirement.model_validate(
            {key: row[key] for key in ("formula_ordinal", "formula_type", "required", "scope")}
        )
        assert tuple(requirement.model_dump(mode="json")) == (
            "formula_ordinal",
            "formula_type",
            "required",
            "scope",
        )

    invalid_rows = (
        {
            "formula_ordinal": 1,
            "formula_type": "UNREVIEWED_FORMULA",
            "required": True,
            "scope": "line_item",
        },
        {
            "formula_ordinal": 1,
            "formula_type": "GROSS_MARGIN",
            "required": True,
            "scope": "free_string_scope",
        },
        {
            "formula_ordinal": 1,
            "formula_type": "GROSS_MARGIN",
            "required": True,
            "scope": "line_item",
            "extra": "forbidden",
        },
    )
    for row in invalid_rows:
        with pytest.raises(ValidationError):
            FddFormulaRequirement.model_validate(row)


def test_required_calculation_shapes_are_homogeneous_and_exact_version_bound() -> None:
    from idis.methodology.models import FddFormulaRequirement, RequiredCalculation

    # Model-only vectors use the checked-in CDD fixture so a failing assertion
    # can never expose private operator-workbook prose through pytest locals.
    legacy = import_cdd_template(CDD_TEMPLATE).current_version.questions[0]
    legacy_payload = legacy.model_dump(mode="json")
    legacy_payload["required_calculations"] = [{"calc_type": "GROSS_MARGIN", "required": False}]
    parsed_legacy = MethodologyQuestion.model_validate(legacy_payload)
    if type(parsed_legacy.required_calculations[0]) is not RequiredCalculation:
        pytest.fail("TASK8_LEGACY_REQUIREMENT_RUNTIME_TYPE_DRIFT", pytrace=False)
    _assert_safe_value_equal(
        hashlib.sha256(_canonical(parsed_legacy.model_dump(mode="json"))).hexdigest(),
        hashlib.sha256(_canonical(legacy_payload)).hexdigest(),
        code="TASK8_LEGACY_QUESTION_PAYLOAD_DRIFT",
    )

    formula: dict[str, Any] = {
        "formula_ordinal": 1,
        "formula_type": "GROSS_MARGIN",
        "required": True,
        "scope": "line_item",
    }
    successor_payload = {**legacy_payload, "methodology_version_id": SUCCESSOR_VERSION_ID}
    successor_payload["required_calculations"] = [formula]
    parsed_successor = MethodologyQuestion.model_validate(successor_payload)
    if type(parsed_successor.required_calculations[0]) is not FddFormulaRequirement:
        pytest.fail("TASK8_POLICY_V2_REQUIREMENT_RUNTIME_TYPE_DRIFT", pytrace=False)
    _assert_safe_value_equal(
        parsed_successor.model_dump(mode="json")["required_calculations"],
        [formula],
        code="TASK8_POLICY_V2_REQUIREMENT_PAYLOAD_DRIFT",
    )
    parsed_successor_tuple = MethodologyQuestion.model_validate(
        {**successor_payload, "required_calculations": (formula,)}
    )
    if type(parsed_successor_tuple.required_calculations[0]) is not FddFormulaRequirement:
        pytest.fail("TASK8_POLICY_V2_TUPLE_REQUIREMENT_TYPE_DRIFT", pytrace=False)
    exact_successor_requirement = FddFormulaRequirement(**formula)
    parsed_successor_instance = MethodologyQuestion.model_validate(
        {
            **successor_payload,
            "required_calculations": [exact_successor_requirement],
        }
    )
    if parsed_successor_instance.required_calculations[0] is not exact_successor_requirement:
        pytest.fail("TASK8_POLICY_V2_INSTANCE_IDENTITY_DRIFT", pytrace=False)
    exact_legacy_requirement = RequiredCalculation(calc_type="GROSS_MARGIN", required=True)
    parsed_legacy_instance = MethodologyQuestion.model_validate(
        {**legacy_payload, "required_calculations": [exact_legacy_requirement]}
    )
    if parsed_legacy_instance.required_calculations[0] is not exact_legacy_requirement:
        pytest.fail("TASK8_LEGACY_INSTANCE_IDENTITY_DRIFT", pytrace=False)

    rejected = (
        {**legacy_payload, "required_calculations": [formula]},
        {
            **successor_payload,
            "required_calculations": [{"calc_type": "GROSS_MARGIN", "required": True}],
        },
        {
            **successor_payload,
            "required_calculations": [
                formula,
                {"calc_type": "GROSS_MARGIN", "required": True},
            ],
        },
        {
            **successor_payload,
            "required_calculations": [
                {**formula, "calc_type": "GROSS_MARGIN"},
            ],
        },
    )
    for payload in rejected:
        with pytest.raises(ValidationError):
            MethodologyQuestion.model_validate(payload)

    invalid_formula_scalars = (
        {key: value for key, value in formula.items() if key != "scope"},
        {**formula, "formula_ordinal": "1"},
        {**formula, "formula_ordinal": 1.0},
        {**formula, "formula_ordinal": True},
        {**formula, "required": 0},
        {**formula, "required": 1},
        {**formula, "required": "true"},
        {**formula, "required": "false"},
        {**formula, "formula_type": "gross_margin"},
        {**formula, "formula_type": b"GROSS_MARGIN"},
        {**formula, "scope": b"line_item"},
    )
    for invalid_formula in invalid_formula_scalars:
        with pytest.raises(ValidationError):
            MethodologyQuestion.model_validate(
                {**successor_payload, "required_calculations": [invalid_formula]}
            )

    with pytest.raises(ValidationError):
        RequiredCalculation.model_validate({"calc_type": "GROSS_MARGIN", "required": "false"})
    with pytest.raises(ValidationError):
        RequiredCalculation.model_validate({"calc_type": b"GROSS_MARGIN", "required": True})
    assert RequiredCalculation(calc_type="GROSS_MARGIN").model_dump(mode="json") == {
        "calc_type": "GROSS_MARGIN",
        "required": True,
    }
    set_container = {
        RequiredCalculation(calc_type="GROSS_MARGIN", required=True),
    }
    with pytest.raises(ValidationError):
        MethodologyQuestion.model_validate(
            {**legacy_payload, "required_calculations": set_container}
        )
    with pytest.raises(ValidationError):
        MethodologyQuestion.model_validate(
            {**legacy_payload, "required_calculations": "GROSS_MARGIN"}
        )
    with pytest.raises(ValidationError):
        MethodologyQuestion.model_validate(
            {
                **successor_payload,
                "required_calculations": (item for item in (formula,)),
            }
        )
    with pytest.raises(ValidationError):
        MethodologyQuestion.model_validate(
            {
                **legacy_payload,
                "required_calculations": [FddFormulaRequirement(**formula)],
            }
        )
    with pytest.raises(ValidationError):
        MethodologyQuestion.model_validate(
            {
                **successor_payload,
                "required_calculations": [
                    FddFormulaRequirement(**formula),
                    RequiredCalculation(calc_type="GROSS_MARGIN", required=True),
                ],
            }
        )

    class ExtendedLegacyRequirement(RequiredCalculation):
        smuggled: str

    class ExtendedFddRequirement(FddFormulaRequirement):
        smuggled: str

    with pytest.raises(ValidationError):
        MethodologyQuestion.model_validate(
            {
                **legacy_payload,
                "required_calculations": [
                    ExtendedLegacyRequirement(
                        calc_type="GROSS_MARGIN",
                        required=True,
                        smuggled="forbidden",
                    )
                ],
            }
        )
    with pytest.raises(ValidationError):
        MethodologyQuestion.model_validate(
            {
                **successor_payload,
                "required_calculations": [
                    ExtendedFddRequirement(
                        **formula,
                        smuggled="forbidden",
                    )
                ],
            }
        )


@requires_operator_workbook
def test_actual_frozen_task5_question_and_rehydration_bytes_are_unchanged() -> None:
    _assert_frozen_task5_bytes(
        import_cdd_template(CDD_TEMPLATE),
        FROZEN_TASK5_BYTES["commercial_dd"],
    )
    _assert_frozen_task5_bytes(
        import_fdd_workbook(_operator_workbook()),
        FROZEN_TASK5_BYTES["financial_dd"],
    )


@requires_operator_workbook
def test_named_constructor_verifies_frozen_identities_and_exact_79_row_join() -> None:
    from idis.methodology.models import FddFormulaRequirement

    legacy = import_fdd_workbook(_operator_workbook())
    successor = _successor_registry()
    policy_rows = _policy()["workbook_row_registry"]["rows"]

    _assert_safe_value_equal(
        {
            "methodology_version_id": successor.current_version.methodology_version_id,
            "version_label": successor.current_version.version_label,
            "source_hash": successor.current_version.source_hash,
            "question_count": len(successor.current_version.questions),
            "question_ids": [
                question.methodology_question_id for question in successor.current_version.questions
            ],
        },
        {
            "methodology_version_id": SUCCESSOR_VERSION_ID,
            "version_label": _policy()["metadata"]["policy_id"],
            "source_hash": WORKBOOK_SHA256,
            "question_count": 79,
            "question_ids": [
                question.methodology_question_id for question in legacy.current_version.questions
            ],
        },
        code="TASK8_POLICY_V2_SUCCESSOR_IDENTITY_DRIFT",
    )
    _assert_safe_value_equal(
        [
            [item.model_dump(mode="json") for item in question.required_calculations]
            for question in successor.current_version.questions
        ],
        [row["required_calculations"] for row in policy_rows],
        code="TASK8_POLICY_V2_REQUIREMENT_JOIN_DRIFT",
    )
    if not all(
        type(item) is FddFormulaRequirement
        for question in successor.current_version.questions
        for item in question.required_calculations
    ):
        pytest.fail("TASK8_POLICY_V2_REQUIREMENT_RUNTIME_TYPE_DRIFT", pytrace=False)
    repository_rehydrated = tuple(
        PostgresMethodologiesRepository._question_from_payload(  # noqa: SLF001
            question.model_dump(mode="json")
        )
        for question in successor.current_version.questions
    )
    _assert_safe_value_equal(
        [
            hashlib.sha256(_canonical(question.model_dump(mode="json"))).hexdigest()
            for question in repository_rehydrated
        ],
        list(methodology_question_payload_hashes(successor)),
        code="TASK8_POLICY_V2_REPOSITORY_REHYDRATION_DRIFT",
    )


@requires_operator_workbook
def test_named_constructor_normalizes_policy_failures_before_any_downstream_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import idis.methodology.importers.fdd_formula_policy_v2 as importer

    workbook_parse_calls = 0
    question_calls = 0
    original_projection = importer.validated_production_fdd_workbook_projection
    original_question_init = importer.MethodologyQuestion.__init__

    def _record_projection(value: Any) -> Any:
        nonlocal workbook_parse_calls
        workbook_parse_calls += 1
        return original_projection(value)

    def _record_question_init(self: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal question_calls
        question_calls += 1
        original_question_init(self, *args, **kwargs)

    monkeypatch.setattr(
        importer, "validated_production_fdd_workbook_projection", _record_projection
    )
    monkeypatch.setattr(importer.MethodologyQuestion, "__init__", _record_question_init)

    missing_component = copy.deepcopy(_policy())
    missing_component.pop("activation_assertions")
    missing_component_raw = (
        json.dumps(missing_component, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    ).encode("utf-8")
    monkeypatch.setattr(
        importer,
        "FDD_FORMULA_POLICY_V2_ARTIFACT_SHA256",
        hashlib.sha256(missing_component_raw).hexdigest(),
    )
    with pytest.raises(importer.FddFormulaPolicyV2Error) as missing_error:
        importer.import_fdd_formula_policy_v2_registry(
            _operator_workbook(),
            policy_artifact=missing_component_raw,
        )
    assert missing_error.value.code == "FDD_FORMULA_POLICY_V2_POLICY_MISMATCH"
    assert (workbook_parse_calls, question_calls) == (0, 0)

    noncanonical = copy.deepcopy(_policy())
    noncanonical["metadata"]["question_count"] = float("nan")
    noncanonical_raw = (
        json.dumps(noncanonical, ensure_ascii=False, allow_nan=True, indent=2) + "\n"
    ).encode("utf-8")
    monkeypatch.setattr(
        importer,
        "FDD_FORMULA_POLICY_V2_ARTIFACT_SHA256",
        hashlib.sha256(noncanonical_raw).hexdigest(),
    )
    with pytest.raises(importer.FddFormulaPolicyV2Error) as noncanonical_error:
        importer.import_fdd_formula_policy_v2_registry(
            _operator_workbook(),
            policy_artifact=noncanonical_raw,
        )
    assert noncanonical_error.value.code == "FDD_FORMULA_POLICY_V2_ARTIFACT_MISMATCH"
    assert (workbook_parse_calls, question_calls) == (0, 0)

    private_marker = "PRIVATE_FDD_POLICY_PATH_MUST_NOT_LEAK"
    with pytest.raises(importer.FddFormulaPolicyV2Error) as unreadable_error:
        importer.import_fdd_formula_policy_v2_registry(
            _operator_workbook(),
            policy_artifact=tmp_path / private_marker / "policy.json",
        )
    assert unreadable_error.value.code == "FDD_FORMULA_POLICY_V2_ARTIFACT_MISMATCH"
    assert unreadable_error.value.__cause__ is None
    assert unreadable_error.value.__suppress_context__ is True
    assert private_marker not in str(unreadable_error.value)
    assert (workbook_parse_calls, question_calls) == (0, 0)


@requires_operator_workbook
def test_named_constructor_fails_closed_with_safe_stable_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.methodology.importers.fdd_formula_policy_v2 as importer

    workbook = _operator_workbook().read_bytes()
    original_artifact_sha256 = importer.FDD_FORMULA_POLICY_V2_ARTIFACT_SHA256
    original_policy_sha256 = importer.FDD_FORMULA_POLICY_V2_POLICY_SHA256
    workbook_parse_calls = 0
    constructed_questions = 0
    original_projection = importer.validated_production_fdd_workbook_projection
    original_question_init = importer.MethodologyQuestion.__init__

    def _record_projection(value: Any) -> Any:
        nonlocal workbook_parse_calls
        workbook_parse_calls += 1
        return original_projection(value)

    def _record_question_init(self: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal constructed_questions
        constructed_questions += 1
        original_question_init(self, *args, **kwargs)

    monkeypatch.setattr(
        importer, "validated_production_fdd_workbook_projection", _record_projection
    )
    monkeypatch.setattr(importer.MethodologyQuestion, "__init__", _record_question_init)

    with pytest.raises(importer.FddFormulaPolicyV2Error) as artifact_error:
        importer.import_fdd_formula_policy_v2_registry(
            workbook,
            policy_artifact=POLICY_V2.read_bytes() + b" ",
        )
    assert artifact_error.value.code == "FDD_FORMULA_POLICY_V2_ARTIFACT_MISMATCH"
    assert str(artifact_error.value) == artifact_error.value.code
    assert (workbook_parse_calls, constructed_questions) == (0, 0)

    invalid_digest = copy.deepcopy(_policy())
    invalid_digest["digest_recipe_registry"]["fdd_formula_policy_v2_sha256"] = "0" * 64
    invalid_digest_raw = (
        json.dumps(invalid_digest, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    ).encode("utf-8")
    monkeypatch.setattr(
        importer,
        "FDD_FORMULA_POLICY_V2_ARTIFACT_SHA256",
        hashlib.sha256(invalid_digest_raw).hexdigest(),
    )
    with pytest.raises(importer.FddFormulaPolicyV2Error) as policy_error:
        importer.import_fdd_formula_policy_v2_registry(
            workbook,
            policy_artifact=invalid_digest_raw,
        )
    assert policy_error.value.code == "FDD_FORMULA_POLICY_V2_POLICY_MISMATCH"
    assert str(policy_error.value) == policy_error.value.code
    assert (workbook_parse_calls, constructed_questions) == (0, 0)

    monkeypatch.setattr(
        importer,
        "FDD_FORMULA_POLICY_V2_ARTIFACT_SHA256",
        original_artifact_sha256,
    )

    with pytest.raises(importer.FddFormulaPolicyV2Error) as workbook_error:
        importer.import_fdd_formula_policy_v2_registry(
            workbook[:-1] + bytes([workbook[-1] ^ 1]),
            policy_artifact=POLICY_V2,
        )
    assert workbook_error.value.code == "FDD_FORMULA_POLICY_V2_WORKBOOK_MISMATCH"
    assert str(workbook_error.value) == workbook_error.value.code
    assert (workbook_parse_calls, constructed_questions) == (1, 0)

    policy = copy.deepcopy(_policy())
    policy["workbook_row_registry"]["rows"][0]["question_text"] += " changed"
    tampered_raw = _rehash_policy(policy)
    monkeypatch.setattr(
        importer,
        "FDD_FORMULA_POLICY_V2_ARTIFACT_SHA256",
        hashlib.sha256(tampered_raw).hexdigest(),
    )
    monkeypatch.setattr(
        importer,
        "FDD_FORMULA_POLICY_V2_POLICY_SHA256",
        policy["digest_recipe_registry"]["fdd_formula_policy_v2_sha256"],
    )
    with pytest.raises(importer.FddFormulaPolicyV2Error) as join_error:
        importer.import_fdd_formula_policy_v2_registry(
            workbook,
            policy_artifact=tampered_raw,
        )
    assert join_error.value.code == "FDD_FORMULA_POLICY_V2_ROW_JOIN_INVALID"
    assert str(join_error.value) == join_error.value.code
    assert (workbook_parse_calls, constructed_questions) == (2, 0)

    monkeypatch.setattr(
        importer,
        "FDD_FORMULA_POLICY_V2_ARTIFACT_SHA256",
        original_artifact_sha256,
    )
    monkeypatch.setattr(
        importer,
        "FDD_FORMULA_POLICY_V2_POLICY_SHA256",
        original_policy_sha256,
    )
    importer.import_fdd_formula_policy_v2_registry(
        workbook,
        policy_artifact=POLICY_V2,
    )
    assert (workbook_parse_calls, constructed_questions) == (3, 79)


def test_v24_all_68_schema_snapshots_and_contract_lock_remain_byte_identical() -> None:
    from idis.contracts import verify_contract_lock

    materializer = _load_module(V24_MATERIALIZER, "_task8_v24_preslice_materializer")
    artifact_raw = V24_ARTIFACT.read_bytes()
    artifact = json.loads(artifact_raw)
    rebuilt = materializer.build_v24_from_parent_raw(
        parent_artifact_raw=V21_ARTIFACT.read_bytes(),
        policy_artifact_raw=POLICY_V2.read_bytes(),
        reviewer_map_raw=V24_REVIEWER_MAP.read_bytes(),
    )
    rebuilt_raw = materializer.frozen_bytes(rebuilt)

    assert len(artifact["schema_registry"]["rows"]) == 68
    assert len(rebuilt["schema_registry"]["rows"]) == 68
    assert rebuilt["schema_registry"] == artifact["schema_registry"]
    assert rebuilt_raw == artifact_raw
    assert hashlib.sha256(rebuilt_raw).hexdigest() == V24_ARTIFACT_SHA256
    assert json.loads(V24_FIXTURE.read_text("utf-8"))["artifact_sha256"] == (V24_ARTIFACT_SHA256)
    lock_result = verify_contract_lock(REPO_ROOT)
    assert lock_result["ok"] is True
    assert lock_result["findings"] == []

    snapshot_checker = _load_module(
        REPO_ROOT / "scripts" / "check_task7_pydantic_schema_drift.py",
        "_task8_preslice_snapshot_checker",
    )
    pydantic_rows = [
        row
        for row in artifact["schema_registry"]["rows"]
        if isinstance(row["origin"], dict) and row["origin"].get("kind") == "pydantic"
    ]
    literal_rows = [
        row
        for row in artifact["schema_registry"]["rows"]
        if isinstance(row["origin"], dict) and row["origin"].get("kind") == "literal-validator"
    ]
    append_frozen_rows = [
        row for row in artifact["schema_registry"]["rows"] if isinstance(row["origin"], str)
    ]
    assert (len(pydantic_rows), len(literal_rows), len(append_frozen_rows)) == (11, 55, 2)
    for row in pydantic_rows:
        assert (
            snapshot_checker._snapshot(row["origin"]["import_targets"])
            == (  # noqa: SLF001
                row["normalized_schema_snapshot"]
            )
        )


def test_operator_workbook_helper_fails_with_only_a_safe_stable_identity_code() -> None:
    source = inspect.getsource(_operator_workbook)
    assert "assert len(raw)" not in source
    assert "assert hashlib.sha256" not in source
    assert "TASK8_FROZEN_WORKBOOK_IDENTITY_MISMATCH" in source


def test_successor_uses_the_unchanged_task5_methodology_source_contract_hashes() -> None:
    from idis.api.routes.methodology_versions import _source_contract_hash
    from idis.methodology.models import MethodologyType

    assert _source_contract_hash(MethodologyType.FINANCIAL_DD) == (
        "ed191040179c85ee2298b9d53df024600e069a8a66ba758b7ec50ddb76d1612e"
    )
    assert _source_contract_hash(MethodologyType.COMMERCIAL_DD) == (
        "e42ae90f9f8b34657ba022c056cb3a0d4ef20006b89cc71cd1dfbe82b56b9b9a"
    )


def test_preslice_roundtrip_targets_0038_and_has_no_live_composition_wiring() -> None:
    proof_source = inspect.getsource(
        test_policy_v2_import_freeze_reload_preserves_four_fields_in_production_repository
    )
    assert 'migrate(database.admin_engine, "0038")' in proof_source

    constructor_symbol = "import_fdd_formula_policy_v2_registry"
    constructor_module = (
        REPO_ROOT / "src" / "idis" / "methodology" / "importers" / ("fdd_formula_policy_v2.py")
    )
    constructor_source = constructor_module.read_text("utf-8")
    assert "PostgresMethodologiesRepository" not in constructor_source
    assert ".import_registry(" not in constructor_source
    assert ".freeze_version(" not in constructor_source
    wired_sources = [
        str(path.relative_to(REPO_ROOT))
        for path in sorted((REPO_ROOT / "src" / "idis").rglob("*.py"))
        if path != constructor_module and constructor_symbol in path.read_text("utf-8")
    ]
    assert wired_sources == []


def test_operator_roundtrip_run_seed_uses_the_exact_current_0038_coordinate() -> None:
    migration = _load_module(
        REPO_ROOT
        / "src"
        / "idis"
        / "persistence"
        / "migrations"
        / "versions"
        / "0038_full_strict_v16_receipts_and_edges.py",
        "_task8_requirement_model_0038_run_authority",
    )
    assert tuple(migration._DDL_ROWS["runs"]["column_tuple"]) == (  # noqa: SLF001
        "pipeline_contract_version TEXT NOT NULL",
        "pipeline_contract_hash TEXT NOT NULL",
        "pipeline_resume_state TEXT NOT NULL",
        "pipeline_resume_error_code VARCHAR(100) NULL",
    )
    assert _current_0038_run_coordinate() == {
        "pipeline_contract_version": "full-strict-v21",
        "pipeline_contract_hash": (
            "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"
        ),
        "pipeline_resume_state": "RESUMABLE",
        "pipeline_resume_error_code": None,
    }
    assert CURRENT_0038_RUN_INSERT_SQL == (
        "INSERT INTO runs "
        "(run_id,tenant_id,deal_id,mode,status,started_at,created_at,"
        "pipeline_contract_version,pipeline_contract_hash,pipeline_resume_state,"
        "pipeline_resume_error_code) "
        "VALUES (CAST(:run_id AS uuid),CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid),"
        "'FULL','QUEUED',now(),now(),:pipeline_contract_version,:pipeline_contract_hash,"
        ":pipeline_resume_state,:pipeline_resume_error_code)"
    )

    proof_source = inspect.getsource(
        test_policy_v2_import_freeze_reload_preserves_four_fields_in_production_repository
    )
    assert "run_coordinate = _current_0038_run_coordinate()" in proof_source
    assert "text(CURRENT_0038_RUN_INSERT_SQL)" in proof_source
    assert "**run_coordinate" in proof_source


@requires_operator_workbook
def test_policy_v2_import_freeze_reload_preserves_four_fields_in_production_repository() -> None:
    """Operator-backed proof; setup is admin, all repository behavior is app-role."""

    from idis.api.routes.methodology_versions import _source_contract_hash
    from idis.methodology.models import MethodologyType
    from tests.slice100_task2b._support import (
        disposable_postgres,
        grant_application_access,
        migrate,
    )

    workbook = _operator_workbook()
    cdd = import_cdd_template(CDD_TEMPLATE)
    successor = _successor_registry()
    tenant_id = str(uuid.uuid4())
    deal_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    run_coordinate = _current_0038_run_coordinate()
    source_contract_hash = _source_contract_hash(MethodologyType.FINANCIAL_DD)
    cdd_source_contract_hash = _source_contract_hash(MethodologyType.COMMERCIAL_DD)
    _assert_safe_value_equal(
        source_contract_hash,
        "ed191040179c85ee2298b9d53df024600e069a8a66ba758b7ec50ddb76d1612e",
        code="TASK8_FDD_SOURCE_CONTRACT_HASH_DRIFT",
    )
    _assert_safe_value_equal(
        cdd_source_contract_hash,
        "e42ae90f9f8b34657ba022c056cb3a0d4ef20006b89cc71cd1dfbe82b56b9b9a",
        code="TASK8_CDD_SOURCE_CONTRACT_HASH_DRIFT",
    )

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0038")
        grant_application_access(database)
        with database.admin_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO tenants (tenant_id,name) VALUES (CAST(:id AS uuid),:name)"),
                {"id": tenant_id, "name": "Task 8 requirement-model proof"},
            )
            connection.execute(
                text(
                    "INSERT INTO deals (deal_id,tenant_id,name,created_at) "
                    "VALUES (CAST(:deal_id AS uuid),CAST(:tenant_id AS uuid),:name,now())"
                ),
                {"deal_id": deal_id, "tenant_id": tenant_id, "name": "Task 8 proof deal"},
            )
            connection.execute(
                text(CURRENT_0038_RUN_INSERT_SQL),
                {
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "deal_id": deal_id,
                    **run_coordinate,
                },
            )

        with database.app_engine.begin() as connection:
            repository = PostgresMethodologiesRepository(connection, tenant_id)
            cdd_imported = repository.import_registry(
                registry=cdd,
                source_type="checked_in_template",
                source_alias=str(cdd.current_version.source_name),
                source_byte_count=CDD_TEMPLATE.stat().st_size,
                source_contract_hash=cdd_source_contract_hash,
                source_trace_digest=methodology_source_trace_digest(cdd),
                imported_by_actor_id="task8-requirement-model-operator",
                import_audit_event_id=str(uuid.uuid4()),
            )
            imported = repository.import_registry(
                registry=successor,
                source_type="operator_workbook",
                source_alias="configured-fdd-workbook",
                source_byte_count=workbook.stat().st_size,
                source_contract_hash=source_contract_hash,
                source_trace_digest=methodology_source_trace_digest(successor),
                imported_by_actor_id="task8-requirement-model-operator",
                import_audit_event_id=str(uuid.uuid4()),
            )
            cdd_frozen = repository.freeze_version(
                methodology_version_record_id=cdd_imported.methodology_version_record_id,
                expected_source_sha256=cdd.current_version.source_hash,
                expected_registry=cdd,
                frozen_by_actor_id="task8-requirement-model-reviewer",
                freeze_audit_event_id=str(uuid.uuid4()),
            )
            frozen = repository.freeze_version(
                methodology_version_record_id=imported.methodology_version_record_id,
                expected_source_sha256=WORKBOOK_SHA256,
                expected_registry=successor,
                frozen_by_actor_id="task8-requirement-model-reviewer",
                freeze_audit_event_id=str(uuid.uuid4()),
            )
            selection = repository.create_run_selection(
                deal_id=deal_id,
                run_id=run_id,
                commercial_dd_version_record_id=cdd_frozen.methodology_version_record_id,
                financial_dd_version_record_id=frozen.methodology_version_record_id,
                created_by_actor_id="task8-requirement-model-operator",
                selection_audit_event_id=str(uuid.uuid4()),
            )

        with database.app_engine.begin() as connection:
            resumed_repository = PostgresMethodologiesRepository(connection, tenant_id)
            reloaded = resumed_repository.get_frozen_version(
                methodology_version_record_id=frozen.methodology_version_record_id
            )
            loaded_selection = resumed_repository.load_run_selection(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
            )
            if loaded_selection is None:
                pytest.fail("TASK8_POLICY_V2_SELECTION_MISSING", pytrace=False)
            loaded_cdd, loaded_fdd = resumed_repository.load_selected_registries(
                selection=loaded_selection
            )

    _assert_safe_bytes_equal(
        reloaded.registry.to_deterministic_json().encode("utf-8"),
        successor.to_deterministic_json().encode("utf-8"),
        code="TASK8_POLICY_V2_REPOSITORY_ROUNDTRIP_DRIFT",
    )
    _assert_safe_bytes_equal(
        loaded_cdd.to_deterministic_json().encode("utf-8"),
        cdd.to_deterministic_json().encode("utf-8"),
        code="TASK8_POLICY_V2_CDD_SELECTION_ROUNDTRIP_DRIFT",
    )
    _assert_safe_bytes_equal(
        loaded_fdd.to_deterministic_json().encode("utf-8"),
        successor.to_deterministic_json().encode("utf-8"),
        code="TASK8_POLICY_V2_FDD_SELECTION_ROUNDTRIP_DRIFT",
    )
    _assert_safe_value_equal(
        asdict(loaded_selection),
        asdict(selection),
        code="TASK8_POLICY_V2_SELECTION_REHYDRATION_DRIFT",
    )
    _assert_safe_value_equal(
        [
            [item.model_dump(mode="json") for item in question.required_calculations]
            for question in reloaded.registry.current_version.questions
        ],
        [
            [item.model_dump(mode="json") for item in question.required_calculations]
            for question in successor.current_version.questions
        ],
        code="TASK8_POLICY_V2_REPOSITORY_REQUIREMENT_DRIFT",
    )

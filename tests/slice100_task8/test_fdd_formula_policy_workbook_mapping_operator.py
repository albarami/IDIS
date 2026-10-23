"""Operator-backed, read-only mapping proof for the Task-8.1 policy gate."""

from __future__ import annotations

from idis.methodology.importers.fdd_excel import import_fdd_workbook

from ._artifact_oracle import (
    POLICY_ID,
    WORKBOOK_BYTE_COUNT,
    WORKBOOK_SHA256,
    load_policy,
    read_operator_rows,
    render_review_map,
    require_review_map,
    requires_operator_workbook,
    resolve_operator_workbook_path,
    sha256_bytes,
    validate_policy,
)


@requires_operator_workbook
def test_pinned_operator_workbook_has_exact_safe_aggregate_shape_and_no_formulas() -> None:
    rows = read_operator_rows()
    workbook = resolve_operator_workbook_path()

    assert workbook.stat().st_size == WORKBOOK_BYTE_COUNT
    assert sha256_bytes(workbook.read_bytes()) == WORKBOOK_SHA256
    assert len(rows) == 79
    assert sum(bool(row["required_calculations"]) for row in rows) == 10


@requires_operator_workbook
def test_current_sheet_wide_fallback_is_captured_only_as_the_legacy_negative_control() -> None:
    policy = load_policy()
    validate_policy(policy)
    registry = import_fdd_workbook(resolve_operator_workbook_path())
    version = registry.versions[0]

    observed: dict[str, tuple[str, bool, int]] = {}
    for sheet, expected_type in (("P&L", "GROSS_MARGIN"), ("Cash Flow", "BURN_RATE")):
        questions = [
            question
            for question in version.questions
            if question.source_trace.sheet_or_section == sheet
        ]
        calculations = {
            (calculation.calc_type, calculation.required)
            for question in questions
            for calculation in question.required_calculations
        }
        if calculations != {(expected_type, False)}:
            raise AssertionError("TASK8_LEGACY_FALLBACK_NEGATIVE_CONTROL_DRIFT")
        observed[sheet] = (expected_type, False, len(questions))

    assert observed == {
        "P&L": ("GROSS_MARGIN", False, 29),
        "Cash Flow": ("BURN_RATE", False, 13),
    }


def test_legacy_sheet_fallback_record_does_not_require_the_operator_workbook() -> None:
    policy = load_policy()
    assert policy["methodology_transition"]["legacy_sheet_fallbacks"] == [
        {
            "source_sheet": "P&L",
            "calc_type": "GROSS_MARGIN",
            "required": False,
            "affected_question_count": 29,
        },
        {
            "source_sheet": "Cash Flow",
            "calc_type": "BURN_RATE",
            "required": False,
            "affected_question_count": 13,
        },
    ]


@requires_operator_workbook
def test_all_79_artifact_rows_join_byte_exactly_to_the_pinned_workbook() -> None:
    policy = load_policy()
    validate_policy(policy)
    actual = policy["workbook_row_registry"]["rows"]
    expected = read_operator_rows()

    if actual != expected:
        raise AssertionError("TASK8_FDD_WORKBOOK_LITERAL_JOIN_FAILED")


def test_artifact_row_identity_counts_do_not_require_the_operator_workbook() -> None:
    actual = load_policy()["workbook_row_registry"]["rows"]
    assert len({row["methodology_question_id"] for row in actual}) == 79
    assert len({row["source_row_sha256"] for row in actual}) == 79


def test_review_map_projection_does_not_require_the_operator_workbook() -> None:
    policy = load_policy()
    observed = require_review_map()
    expected = render_review_map(policy)

    if observed != expected:
        raise AssertionError("TASK8_FDD_REVIEW_MAP_DRIFT")
    if not observed.startswith(
        b"# Slice100 Task 8 FDD Formula Policy v1 \xe2\x80\x94 reviewer mapping\n"
    ):
        raise AssertionError("TASK8_FDD_REVIEW_MAP_HEADER_INVALID")
    if observed.count(b"\n## ") != 10:
        raise AssertionError("TASK8_FDD_REVIEW_MAP_ANCHOR_COUNT_INVALID")
    if observed.count(b"fdd-formula-source-mapping-v1") != 0:
        raise AssertionError("TASK8_FDD_REVIEW_MAP_INTERNAL_PREIMAGE_LEAK")


@requires_operator_workbook
def test_review_map_is_the_exact_deterministic_projection_of_ten_anchors_and_107_bindings() -> None:
    validate_policy(load_policy())


def test_review_map_authority_claims_do_not_require_the_operator_workbook() -> None:
    policy = load_policy()
    review_map = require_review_map().decode("utf-8")

    if policy["metadata"]["policy_id"] != POLICY_ID:
        raise AssertionError("TASK8_FDD_POLICY_ID_INVALID")
    if "Deterministic projection" not in review_map:
        raise AssertionError("TASK8_FDD_REVIEW_MAP_PROJECTION_MARKER_MISSING")
    lowered = review_map.lower()
    if "runtime selectable" in lowered:
        raise AssertionError("TASK8_FDD_REVIEW_MAP_RUNTIME_AUTHORITY_FORBIDDEN")
    if "migration consumable" in lowered:
        raise AssertionError("TASK8_FDD_REVIEW_MAP_MIGRATION_AUTHORITY_FORBIDDEN")


@requires_operator_workbook
def test_review_map_contains_no_independent_authority_or_runtime_activation_claim() -> None:
    validate_policy(load_policy())

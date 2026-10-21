"""Pass and mutation tests for invariants I-10 through I-18."""

from __future__ import annotations

import json
from pathlib import Path

from idis.methodology.obligations.commands import _render_validation
from idis.methodology.obligations.jsonl import read_jsonl, write_jsonl
from idis.methodology.obligations.models_authored import (
    Applicability,
    BaselineExpectation,
    CalculationRule,
)
from idis.methodology.obligations.models_records import Finding, ValidationReport
from idis.methodology.obligations.register_io import load_register
from idis.methodology.obligations.validate import invariant_status, validate_register
from idis.methodology.obligations.vocab import (
    RESERVED_CODES,
    SKIPPED_LEDGER_LATER,
    SKIPPED_POLICY_ABSENT,
    SKIPPED_WORKBOOK_ABSENT,
    ApplicabilityDecision,
    BaselineKind,
    CalculationKind,
    Code,
    FindingSeverity,
    InvariantStatus,
)
from tests.gl02_register.mutate import (
    clone_register,
    finding_codes,
    replace_report,
    update_child,
    update_profile,
)
from tests.gl02_register.support import build_authored_register

_PIN = Code.GL02_SOURCE_PIN_MISMATCH.value


def _assert_added(before: set[str], after: set[str], code: str) -> None:
    assert after - before == {code}


def test_i10_policy_coverage_passes(tmp_path: Path) -> None:
    root = _policy_register(tmp_path / "pass")
    assert Code.GL02_POLICY_UNCARRIED.value not in finding_codes(root)


def test_i10_policy_coverage_mutation(tmp_path: Path) -> None:
    root = _policy_register(tmp_path / "mutation")
    before = finding_codes(root)
    update_child(
        root, "FDD-PL-004", lambda child: child.model_copy(update={"calculation_rules": ()})
    )
    _assert_added(before, finding_codes(root), Code.GL02_POLICY_UNCARRIED.value)


def test_i11_named_clarifications_passes(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    assert Code.GL02_AMBIGUITY_TOPIC_MISSING.value not in finding_codes(root)


def test_i11_named_clarifications_mutation(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _workbook = authored_register
    before = finding_codes(root)
    copied = clone_register(root, tmp_path / "i11")
    from idis.methodology.obligations.models_authored import Ambiguity

    path = copied / "authored" / "ambiguities" / "XS.jsonl"
    records = [
        item for item in read_jsonl(path, Ambiguity) if item.topic_code.value != "ZERO_EBITDA"
    ]
    write_jsonl(path, records, key=lambda item: item.ambiguity_id)
    _assert_added(before, finding_codes(copied), Code.GL02_AMBIGUITY_TOPIC_MISSING.value)


def test_i12_scope_retention_passes(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    assert Code.GL02_SCOPE_NOT_RETAINED.value not in finding_codes(root)


def test_i12_scope_retention_mutation(authored_register: tuple[Path, Path], tmp_path: Path) -> None:
    root, _workbook = authored_register
    before = finding_codes(root)
    copied = clone_register(root, tmp_path / "i12")
    dropped = Applicability(
        condition="Fictional condition.",
        decision=ApplicabilityDecision.NOT_APPLICABLE_BY_DEFAULT,
    )
    for row in range(17, 25):
        update_child(
            copied,
            f"FDD-LIAB-{row:03d}",
            lambda child, decision=dropped: child.model_copy(update={"applicability": decision}),
        )
    _assert_added(before, finding_codes(copied), Code.GL02_SCOPE_NOT_RETAINED.value)


def test_i13_commercial_alignment_passes(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    assert Code.GL02_COMMERCIAL_DESTINATION.value not in finding_codes(root)


def test_i13_commercial_alignment_mutation(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _workbook = authored_register
    before = finding_codes(root)
    copied = clone_register(root, tmp_path / "i13")
    register = load_register(copied)
    commercial = next(
        parent.public_coordinate
        for parent in register.parents
        if parent.public_coordinate.startswith("CDD-")
    )

    def _shift(profile):  # type: ignore[no-untyped-def]
        destination = profile.default_output_destination.model_copy(
            update={"report_section": "Fictional Section", "deviation_reason": None}
        )
        return profile.model_copy(update={"default_output_destination": destination})

    update_profile(copied, commercial, _shift)
    _assert_added(before, finding_codes(copied), Code.GL02_COMMERCIAL_DESTINATION.value)


def test_i14_determinism_passes(authored_register: tuple[Path, Path]) -> None:
    root, workbook = authored_register
    assert Code.GL02_DERIVED_STALE.value not in finding_codes(root, workbook_path=workbook)


def test_i14_determinism_mutation(authored_register: tuple[Path, Path], tmp_path: Path) -> None:
    root, workbook = authored_register
    before = finding_codes(root, workbook_path=workbook)
    copied = clone_register(root, tmp_path / "i14")
    report = load_register(copied).extraction_report
    replace_report(copied, report.model_copy(update={"non_source_input_spec_count": 1}))
    _assert_added(
        before, finding_codes(copied, workbook_path=workbook), Code.GL02_DERIVED_STALE.value
    )


def test_i16_ledger_code_is_reserved(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    report = validate_register(root)
    assert Code.GL02_LEDGER_INTEGRITY.value in RESERVED_CODES
    assert Code.GL02_LEDGER_INTEGRITY.value not in {item.code for item in report.findings}
    assert SKIPPED_LEDGER_LATER in report.skipped_checks
    assert report.register_complete is False


def test_ledger_skip_alone_blocks_completion() -> None:
    from idis.methodology.obligations.validate import _complete

    assert _complete((), (SKIPPED_LEDGER_LATER,)) is False
    assert _complete((), ()) is True


def test_i17_baseline_passes(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    assert Code.GL02_BASELINE_UNMET.value not in finding_codes(root)


def test_i17_baseline_mutation(authored_register: tuple[Path, Path], tmp_path: Path) -> None:
    root, _workbook = authored_register
    before = finding_codes(root)
    copied = clone_register(root, tmp_path / "i17")
    from idis.methodology.obligations.models_authored import BaselineExpectation

    path = copied / "authored" / "baseline" / "XS.jsonl"
    records = [
        item.model_copy(update={"source_coordinate": "FDD-PL-032"})
        for item in read_jsonl(path, BaselineExpectation)
    ]
    write_jsonl(path, records, key=lambda item: item.expectation_id)
    _assert_added(before, finding_codes(copied), Code.GL02_BASELINE_UNMET.value)


def test_i18_views_passes(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    assert Code.GL02_VIEW_INVALID.value not in finding_codes(root)


def test_i18_views_mutation(authored_register: tuple[Path, Path], tmp_path: Path) -> None:
    root, _workbook = authored_register
    before = finding_codes(root)
    copied = clone_register(root, tmp_path / "i18")
    from idis.methodology.obligations.models_authored import ViewRecord

    path = copied / "authored" / "views" / "XS.jsonl"
    records = [
        item.model_copy(update={"parent_ids": ("FDD-PL-999",)})
        for item in read_jsonl(path, ViewRecord)
    ]
    write_jsonl(path, records, key=lambda item: item.view_id)
    _assert_added(before, finding_codes(copied), Code.GL02_VIEW_INVALID.value)


def test_skipped_checks_never_count_as_a_pass(authored_register: tuple[Path, Path]) -> None:
    root, workbook = authored_register
    without_workbook = validate_register(root)
    assert SKIPPED_WORKBOOK_ABSENT in without_workbook.skipped_checks
    assert without_workbook.register_complete is False
    with_workbook = validate_register(root, workbook_path=workbook)
    assert SKIPPED_POLICY_ABSENT in with_workbook.skipped_checks
    assert with_workbook.register_complete is False
    assert _PIN in {item.code for item in with_workbook.findings}


def test_shard_scope_reports_pending(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    report = validate_register(root, shards=["PL"])
    assert Code.PENDING_CROSS_SHARD.value in {item.code for item in report.findings}
    assert report.register_complete is False


def test_i14_missing_policy_file_skips_the_derived_comparison(tmp_path: Path) -> None:
    base = tmp_path / "i14-policy"
    root = _policy_register(base)
    workbook = root.parent / "fictional.xlsx"
    policy = base / "fictional-policy.json"
    skipped = validate_register(root, workbook_path=workbook)
    assert Code.GL02_DERIVED_STALE.value not in {item.code for item in skipped.findings}
    assert SKIPPED_POLICY_ABSENT in skipped.skipped_checks
    assert skipped.register_complete is False
    assert invariant_status("I-14", skipped) == InvariantStatus.SKIPPED.value
    assert ("I-14", SKIPPED_POLICY_ABSENT) in skipped.skipped_invariants
    assert "I-10" not in {item_id for item_id, _code in skipped.skipped_invariants}
    rendered = _render_validation(skipped)
    assert "message=derived comparison skipped because the policy file was absent" in rendered
    assert "GL02_DERIVED_STALE" not in rendered
    compared = validate_register(root, workbook_path=workbook, policy_path=policy)
    assert Code.GL02_DERIVED_STALE.value not in {item.code for item in compared.findings}
    assert invariant_status("I-14", compared) == InvariantStatus.PASS.value


def test_merged_baseline_outside_a_shard_is_pending(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _workbook = authored_register
    copied = clone_register(root, tmp_path / "merged-shard")
    _retarget_merged_baseline(copied, "FDD-PL-032")
    full = validate_register(copied)
    assert _has(full, Code.GL02_BASELINE_UNMET.value, "BASE-LIAB-004")
    assert not _has(full, Code.PENDING_CROSS_SHARD.value, "BASE-LIAB-004")
    limited = validate_register(copied, shards=["CF"])
    pending = _matching(limited, Code.PENDING_CROSS_SHARD.value, "BASE-LIAB-004")
    assert pending
    assert all(item.severity is FindingSeverity.INFO for item in pending)
    assert not _has(limited, Code.GL02_BASELINE_UNMET.value, "BASE-LIAB-004")
    in_scope = validate_register(copied, shards=["PL"])
    assert _has(in_scope, Code.GL02_BASELINE_UNMET.value, "BASE-LIAB-004")
    assert not _has(in_scope, Code.PENDING_CROSS_SHARD.value, "BASE-LIAB-004")


def test_cross_row_baseline_outside_a_shard_is_pending(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _workbook = authored_register
    copied = clone_register(root, tmp_path / "cross-shard")
    path = copied / "authored" / "baseline" / "XS.jsonl"
    records = list(read_jsonl(path, BaselineExpectation))
    records.append(
        BaselineExpectation(
            expectation_id="BASE-CROSS-01",
            kind=BaselineKind.CROSS_ROW_INSTRUCTION,
            source_coordinate="FDD-PL-004",
            target_coordinates=("FDD-CF-004",),
            note="Fictional cross-row baseline.",
        )
    )
    write_jsonl(path, records, key=lambda item: item.expectation_id)
    full = validate_register(copied)
    assert _has(full, Code.GL02_BASELINE_UNMET.value, "BASE-CROSS-01")
    assert not _has(full, Code.PENDING_CROSS_SHARD.value, "BASE-CROSS-01")
    outside = validate_register(copied, shards=["LIAB"])
    pending = _matching(outside, Code.PENDING_CROSS_SHARD.value, "BASE-CROSS-01")
    assert pending
    assert all(item.severity is FindingSeverity.INFO for item in pending)
    assert not _has(outside, Code.GL02_BASELINE_UNMET.value, "BASE-CROSS-01")
    both = validate_register(copied, shards=["PL", "CF"])
    assert _has(both, Code.GL02_BASELINE_UNMET.value, "BASE-CROSS-01")
    assert not _has(both, Code.PENDING_CROSS_SHARD.value, "BASE-CROSS-01")


def _retarget_merged_baseline(root: Path, coordinate: str) -> None:
    path = root / "authored" / "baseline" / "XS.jsonl"
    records = [
        item.model_copy(update={"source_coordinate": coordinate})
        if item.kind is BaselineKind.MERGED_INSTRUCTION
        else item
        for item in read_jsonl(path, BaselineExpectation)
    ]
    write_jsonl(path, records, key=lambda item: item.expectation_id)


def _matching(report: ValidationReport, code: str, expectation_id: str) -> list[Finding]:
    return [item for item in report.findings if item.code == code and expectation_id in item.ids]


def _has(report: ValidationReport, code: str, expectation_id: str) -> bool:
    return bool(_matching(report, code, expectation_id))


def _policy_register(directory: Path) -> Path:
    preview, _workbook = build_authored_register(directory / "preview")
    parent = load_register(preview).parents_by_coordinate["FDD-PL-004"]
    policy = directory / "fictional-policy.json"
    policy.write_text(
        json.dumps(
            {
                "workbook_row_registry": {
                    "rows": [
                        {
                            "source_sheet": "P&L",
                            "source_row": 4,
                            "methodology_question_id": parent.parent_id,
                            "required_calculations": [],
                        }
                    ]
                },
                "formula_registry": {
                    "rows": [
                        {
                            "formula_ordinal": 1,
                            "formula_type": "fictional",
                            "anchor_sheet": "P&L",
                            "anchor_row": 4,
                            "scope": "fictional",
                            "required": True,
                            "ordered_input_specs": [
                                {
                                    "binding_ordinal": 1,
                                    "binding_kind": "SOURCE",
                                    "role": "fictional",
                                    "predicate": None,
                                    "source_sheet": "P&L",
                                    "source_row": 4,
                                    "sign_transform": None,
                                    "cardinality": "1",
                                    "period_role": None,
                                },
                                {
                                    "binding_ordinal": 2,
                                    "binding_kind": "CONSTANT",
                                    "role": "fictional",
                                    "predicate": None,
                                    "source_sheet": None,
                                    "source_row": None,
                                    "sign_transform": None,
                                    "cardinality": "1",
                                    "period_role": None,
                                },
                            ],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    root, _workbook = build_authored_register(directory / "register", policy=policy)
    update_child(
        root,
        "FDD-PL-004",
        lambda child: child.model_copy(
            update={
                "calculation_rules": (
                    CalculationRule(kind=CalculationKind.POLICY_V2_ANCHOR, formula_ordinal=1),
                    CalculationRule(
                        kind=CalculationKind.POLICY_V2_BINDING,
                        formula_ordinal=1,
                        binding_ordinal=1,
                    ),
                )
            }
        ),
    )
    return root

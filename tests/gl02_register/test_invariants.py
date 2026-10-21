"""Pass and mutation tests for invariants I-01 through I-18."""

from __future__ import annotations

from pathlib import Path

from idis.methodology.ids import normalize_text
from idis.methodology.obligations.checks import (
    check_importer_parity,
    check_source_coverage,
)
from idis.methodology.obligations.jsonl import read_jsonl, sha256_text, write_jsonl
from idis.methodology.obligations.models_authored import (
    ChildObligation,
    ParentProfile,
    SourceQuote,
)
from idis.methodology.obligations.models_records import (
    ExtractionReport,
    FeatureCensus,
    InputFingerprint,
    SheetCount,
    SourceCell,
)
from idis.methodology.obligations.register_io import load_register, parent_shard
from idis.methodology.obligations.validate import validate_register
from idis.methodology.obligations.vocab import (
    EXPECTED_DATA_ROW_COUNTS,
    EXPECTED_MERGED_RANGE_COUNTS,
    PINNED_WORKBOOK_BYTE_COUNT,
    PINNED_WORKBOOK_SHA256,
    REGISTER_SCHEMA,
    REGISTER_VERSION,
    SHEET_NAMES,
    Code,
)
from tests.gl02_register.mutate import (
    clone_register,
    finding_codes,
    parent_sort_key,
    replace_report,
    update_child,
    update_profile,
)

_PIN = Code.GL02_SOURCE_PIN_MISMATCH.value


def _pinned_report() -> ExtractionReport:
    fingerprint = InputFingerprint(sha256="a" * 64, byte_count=1)
    return ExtractionReport(
        register_schema=REGISTER_SCHEMA,
        register_version=REGISTER_VERSION,
        workbook=InputFingerprint(
            sha256=PINNED_WORKBOOK_SHA256, byte_count=PINNED_WORKBOOK_BYTE_COUNT
        ),
        template=fingerprint,
        category_map=fingerprint,
        sheet_names=SHEET_NAMES,
        data_row_counts=tuple(
            SheetCount(sheet=name, count=EXPECTED_DATA_ROW_COUNTS[name]) for name in SHEET_NAMES
        ),
        merged_range_counts=tuple(
            SheetCount(sheet=name, count=EXPECTED_MERGED_RANGE_COUNTS[name]) for name in SHEET_NAMES
        ),
        feature_census=FeatureCensus(),
    )


def _assert_added(before: set[str], after: set[str], code: str) -> None:
    assert after - before == {code}


def test_i01_source_coverage_passes() -> None:
    assert check_source_coverage(_pinned_report()) == []


def test_i01_source_coverage_mutation(authored_register: tuple[Path, Path], tmp_path: Path) -> None:
    root, _workbook = authored_register
    before = finding_codes(root)
    copied = clone_register(root, tmp_path / "i01")
    report = load_register(copied).extraction_report
    counts = tuple(
        item.model_copy(update={"count": item.count - 1}) if item.sheet == "P&L" else item
        for item in report.merged_range_counts
    )
    replace_report(copied, report.model_copy(update={"merged_range_counts": counts}))
    _assert_added(before, finding_codes(copied), Code.GL02_SOURCE_COVERAGE.value)


def test_i02_importer_parity_passes(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    assert check_importer_parity(load_register(root).extraction_report) == []


def test_i02_importer_parity_mutation(authored_register: tuple[Path, Path], tmp_path: Path) -> None:
    root, _workbook = authored_register
    before = finding_codes(root)
    copied = clone_register(root, tmp_path / "i02")
    report = load_register(copied).extraction_report
    replace_report(copied, report.model_copy(update={"parity_failure_ids": ("FDD-PL-004",)}))
    _assert_added(before, finding_codes(copied), Code.GL02_IMPORTER_PARITY_FAILED.value)


def test_i03_parent_coverage_passes(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    assert Code.GL02_PARENT_COVERAGE.value not in finding_codes(root)


def test_i03_parent_coverage_mutation(authored_register: tuple[Path, Path], tmp_path: Path) -> None:
    root, _workbook = authored_register
    before = finding_codes(root)
    copied = clone_register(root, tmp_path / "i03")
    register = load_register(copied)
    parent = register.parents_by_coordinate["FDD-ASSET-019"]
    parents = [item for item in register.parents if item.parent_id != parent.parent_id]
    write_jsonl(copied / "derived" / "parents.jsonl", parents, key=parent_sort_key)
    shard = parent_shard(parent)
    path = copied / "authored" / "children" / f"{shard.value}.jsonl"
    children = [
        child for child in read_jsonl(path, ChildObligation) if child.parent_id != parent.parent_id
    ]
    write_jsonl(path, children, key=lambda item: item.child_id)
    profile_path = copied / "authored" / "profiles" / f"{shard.value}.jsonl"
    profiles = [
        item
        for item in read_jsonl(profile_path, ParentProfile)
        if item.parent_id != parent.parent_id
    ]
    write_jsonl(profile_path, profiles, key=lambda item: item.parent_id)
    _assert_added(before, finding_codes(copied), Code.GL02_PARENT_COVERAGE.value)


def test_i04_active_child_passes(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    assert Code.GL02_PARENT_WITHOUT_CHILD.value not in finding_codes(root)


def test_i04_active_child_mutation(authored_register: tuple[Path, Path], tmp_path: Path) -> None:
    root, _workbook = authored_register
    before = finding_codes(root)
    copied = clone_register(root, tmp_path / "i04")
    register = load_register(copied)
    parent = register.parents_by_coordinate["FDD-PL-026"]
    _shorten_parent_fields(copied, register, parent)
    shard = parent_shard(parent)
    path = copied / "authored" / "children" / f"{shard.value}.jsonl"
    children = [
        child for child in read_jsonl(path, ChildObligation) if child.parent_id != parent.parent_id
    ]
    write_jsonl(path, children, key=lambda item: item.child_id)
    _assert_added(before, finding_codes(copied), Code.GL02_PARENT_WITHOUT_CHILD.value)


def _shorten_parent_fields(root: Path, register, parent) -> None:
    """Replace one unmerged parent's fields with a non-material marker-free sentence."""
    text = "Hello."
    normalized = normalize_text(text)
    digest = sha256_text(text)
    updated = parent.model_copy(
        update={
            "term_raw": text,
            "nature_raw": text,
            "line_item_raw": text,
            "question_raw": text,
            "question_raw_sha256": digest,
            "question_normalized": normalized,
            "question_normalized_sha256": sha256_text(normalized),
        }
    )
    parents = [updated if item.parent_id == parent.parent_id else item for item in register.parents]
    write_jsonl(root / "derived" / "parents.jsonl", parents, key=parent_sort_key)
    holders = {
        f"{parent.sheet}!{ref.merge_anchor}" if ref.merge_anchor else ref.cell_id
        for ref in (
            parent.term_cell,
            parent.nature_cell,
            parent.line_item_cell,
            parent.question_cell,
        )
        if ref is not None
    }
    cells = [
        cell.model_copy(update={"raw_text": text, "raw_sha256": digest})
        if cell.cell_id in holders
        else cell
        for cell in register.source_cells
    ]
    write_jsonl(root / "derived" / "source_cells.jsonl", cells, key=_cell_sort_key)


def _replace_question(root: Path, register, parent, text: str) -> None:
    """Replace one question and the source cell that holds it."""
    normalized = normalize_text(text)
    digest = sha256_text(text)
    updated = parent.model_copy(
        update={
            "question_raw": text,
            "question_raw_sha256": digest,
            "question_normalized": normalized,
            "question_normalized_sha256": sha256_text(normalized),
        }
    )
    parents = [updated if item.parent_id == parent.parent_id else item for item in register.parents]
    write_jsonl(root / "derived" / "parents.jsonl", parents, key=parent_sort_key)
    ref = parent.question_cell
    assert ref is not None
    holder = f"{parent.sheet}!{ref.merge_anchor}" if ref.merge_anchor else ref.cell_id
    matched = False
    cells = []
    for cell in register.source_cells:
        if cell.cell_id == holder:
            cells.append(cell.model_copy(update={"raw_text": text, "raw_sha256": digest}))
            matched = True
        else:
            cells.append(cell)
    assert matched
    write_jsonl(root / "derived" / "source_cells.jsonl", cells, key=_cell_sort_key)


def _cell_sort_key(cell: SourceCell) -> tuple[int, int, int]:
    return (cell.sheet_ordinal, cell.row, cell.column)


def test_i05_raw_text_integrity_passes(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    assert Code.GL02_RAW_TEXT_INTEGRITY.value not in finding_codes(root)


def test_i05_raw_text_integrity_mutation(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _workbook = authored_register
    before = finding_codes(root)
    copied = clone_register(root, tmp_path / "i05")
    register = load_register(copied)
    parent = register.parents_by_coordinate["FDD-PL-020"]
    parents = [
        item.model_copy(update={"question_raw_sha256": "b" * 64})
        if item.parent_id == parent.parent_id
        else item
        for item in register.parents
    ]
    write_jsonl(copied / "derived" / "parents.jsonl", parents, key=parent_sort_key)
    _assert_added(before, finding_codes(copied), Code.GL02_RAW_TEXT_INTEGRITY.value)


def test_i06_wording_passes(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    assert Code.GL02_RUN_UNDISPOSITIONED.value not in finding_codes(root)


def test_i06_wording_mutation(authored_register: tuple[Path, Path], tmp_path: Path) -> None:
    root, _workbook = authored_register
    before = finding_codes(root)
    copied = clone_register(root, tmp_path / "i06")
    question = load_register(copied).parents_by_coordinate["FDD-PL-020"].question_raw
    shortened = question[: question.rfind(" row")]

    def _shorten(child: ChildObligation) -> ChildObligation:
        quotes = tuple(
            quote.model_copy(update={"text": shortened}) if quote.field == "question" else quote
            for quote in child.source_quotes
        )
        return child.model_copy(update={"source_quotes": quotes})

    update_child(copied, "FDD-PL-020", _shorten)
    _assert_added(before, finding_codes(copied), Code.GL02_RUN_UNDISPOSITIONED.value)


def test_i06b_focus_passes(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    assert Code.GL02_SEGMENT_NOT_FOCUSED.value not in finding_codes(root)


def test_i06b_focus_mutation(authored_register: tuple[Path, Path], tmp_path: Path) -> None:
    root, _workbook = authored_register
    before = finding_codes(root)
    copied = clone_register(root, tmp_path / "i06b")
    sentences = (
        "Fictional alpha evidence row 01.",
        "Fictional beta evidence row 02.",
        "Fictional gamma evidence row 03.",
    )
    register = load_register(copied)
    parent = register.parents_by_coordinate["FDD-PL-020"]
    _replace_question(copied, register, parent, " ".join(sentences))

    def _cover(child: ChildObligation) -> ChildObligation:
        kept = tuple(quote for quote in child.source_quotes if quote.field != "question")
        added = tuple(
            SourceQuote(field="question", occurrence=0, parent_id=None, text=sentence)
            for sentence in sentences
        )
        return child.model_copy(
            update={"joint_answer_reason": None, "source_quotes": (*kept, *added)}
        )

    update_child(copied, "FDD-PL-020", _cover)
    assert finding_codes(copied) - before == {
        Code.GL02_SEGMENT_NOT_FOCUSED.value,
        Code.GL02_JOINT_REASON_MISSING.value,
    }


def test_i07_shared_instruction_passes(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    assert Code.GL02_SHARED_INSTRUCTION_UNAPPLIED.value not in finding_codes(root)


def test_i07_shared_instruction_mutation(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _workbook = authored_register
    before = finding_codes(root)
    copied = clone_register(root, tmp_path / "i07")
    update_child(
        copied,
        "FDD-LIAB-004",
        lambda child: child.model_copy(update={"shared_instruction_ids": ()}),
    )
    _assert_added(before, finding_codes(copied), Code.GL02_SHARED_INSTRUCTION_UNAPPLIED.value)


def test_i08_field_completeness_passes(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    assert Code.GL02_FIELD_UNRESOLVED.value not in finding_codes(root)


def test_i08_field_completeness_mutation(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _workbook = authored_register
    before = finding_codes(root)
    copied = clone_register(root, tmp_path / "i08")
    update_profile(
        copied,
        "FDD-PL-020",
        lambda profile: profile.model_copy(update={"default_coordinates": None}),
    )
    _assert_added(before, finding_codes(copied), Code.GL02_FIELD_UNRESOLVED.value)


def test_i09_plan_minimum_passes(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    assert Code.GL02_PLAN_MINIMUM_MISSING.value not in finding_codes(root)


def test_i09_plan_minimum_mutation(authored_register: tuple[Path, Path], tmp_path: Path) -> None:
    root, _workbook = authored_register
    before = finding_codes(root)
    copied = clone_register(root, tmp_path / "i09")
    for shard in ("PL", "CF", "LIAB", "ASSET"):
        path = copied / "authored" / "children" / f"{shard}.jsonl"
        children = [
            child.model_copy(update={"plan_minimum_codes": ()})
            for child in read_jsonl(path, ChildObligation)
        ]
        write_jsonl(path, children, key=lambda item: item.child_id)
    _assert_added(before, finding_codes(copied), Code.GL02_PLAN_MINIMUM_MISSING.value)


def test_joint_reason_passes(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    assert Code.GL02_JOINT_REASON_MISSING.value not in finding_codes(root)


def test_joint_reason_mutation(authored_register: tuple[Path, Path], tmp_path: Path) -> None:
    """A three-segment child fails even when focused children co-cover those segments."""
    root, _workbook = authored_register
    before = finding_codes(root)
    copied = clone_register(root, tmp_path / "joint")
    sentences = (
        "Fictional alpha evidence row 01.",
        "Fictional beta evidence row 02.",
        "Fictional gamma evidence row 03.",
    )
    register = load_register(copied)
    parent = register.parents_by_coordinate["FDD-PL-020"]
    _replace_question(copied, register, parent, " ".join(sentences))
    path = copied / "authored" / "children" / "PL.jsonl"
    children: list[ChildObligation] = []
    wide: ChildObligation | None = None
    for child in read_jsonl(path, ChildObligation):
        if child.parent_id != parent.parent_id:
            children.append(child)
            continue
        kept = tuple(quote for quote in child.source_quotes if quote.field != "question")
        added = tuple(
            SourceQuote(field="question", occurrence=0, parent_id=None, text=sentence)
            for sentence in sentences
        )
        wide = child.model_copy(
            update={"joint_answer_reason": None, "source_quotes": (*kept, *added)}
        )
        children.append(wide)
    assert wide is not None
    for suffix, sentence in zip(("002", "003", "004"), sentences, strict=True):
        children.append(
            wide.model_copy(
                update={
                    "child_id": f"GL02-FDD-PL-020-C{suffix}",
                    "plan_minimum_codes": (),
                    "source_quotes": (
                        SourceQuote(field="question", occurrence=0, parent_id=None, text=sentence),
                    ),
                }
            )
        )
    write_jsonl(path, children, key=lambda item: item.child_id)
    _assert_added(before, finding_codes(copied), Code.GL02_JOINT_REASON_MISSING.value)
    hits = [
        item
        for item in validate_register(copied).findings
        if item.code == Code.GL02_JOINT_REASON_MISSING.value
    ]
    assert {tuple(sorted(item.ids)) for item in hits} == {("GL02-FDD-PL-020-C001", "question")}
    assert Code.GL02_SEGMENT_NOT_FOCUSED.value not in finding_codes(copied)

    def _restore(child: ChildObligation) -> ChildObligation:
        if child.child_id != "GL02-FDD-PL-020-C001":
            return child
        return child.model_copy(update={"joint_answer_reason": "Fictional joint answer reason."})

    update_child(copied, "FDD-PL-020", _restore)
    assert Code.GL02_JOINT_REASON_MISSING.value not in finding_codes(copied)
    assert Code.GL02_SEGMENT_NOT_FOCUSED.value not in finding_codes(copied)

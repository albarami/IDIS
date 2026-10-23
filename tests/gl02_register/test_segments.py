"""Segments, tokens, quotes, runs, markers and dispositions."""

from __future__ import annotations

from pathlib import Path

from idis.methodology.obligations.jsonl import sha256_text
from idis.methodology.obligations.models_authored import Disposition, SourceQuote
from idis.methodology.obligations.quote import locate_phrase, resolve_in_text
from idis.methodology.obligations.register_io import load_register
from idis.methodology.obligations.runs import account_field, is_material
from idis.methodology.obligations.segments import (
    escape_text,
    fold_text,
    split_segments,
    tokenize,
    trim_span,
)
from idis.methodology.obligations.validate import validate_register
from idis.methodology.obligations.vocab import (
    DIRECTIVE_TOKENS,
    NON_BREAKING_ABBREVIATIONS,
    QUALIFIER_TOKENS,
    REFERENCE_MARKERS,
    SCOPE_POINTER_MARKERS,
    STOPWORDS,
    ChildStatus,
    Code,
    Derivation,
    DerivedRuleCode,
    DispositionKind,
    FindingSeverity,
    RunDisposition,
)
from tests.gl02_register.mutate import clone_register, update_child

_STOPWORD_PIN = "b888c2f83dcaca39ccb709e630beb19ee19dc1244603a645e08ee53330e7117a"
_QUALIFIER_PIN = "c7a5abec949d813f889e56050341d077c12070eefcefe6899e25ee6eb394a8bb"
_DERIVED_RULE_PIN = "afd9368f4a23781ba735b57e200867b85eb04679b7d424c523418ee948c531fe"
_DIRECTIVE_PIN = "f9da72f92297ae97d68c97a53108ab0fda25d0d72be5d8350a86b32d172cda04"
_ABBREVIATION_PIN = "baa9d48087132f8768bc7d9aa79cd71c87b7ee61f815476a213241a31de20e16"
_MARKER_PIN = "7fb51765dcc1f2a8df5b3adff6afcb06a879d11f9f2458c1a4d9ddc125505dfb"


def test_segments_tile_generated_texts() -> None:
    samples = (
        "",
        "Fictional sentence one. Fictional sentence two?",
        "Fictional line one.\nFictional line two!",
        "Fictional intro (alpha item, beta item, gamma item) trailing.",
        "Fictional; next clause stays after the semicolon.",
        "Alpha i.e. beta. Gamma.",
        "See vs. approx. incl. excl. no. 3 e.g. items. Next.",
        "Fictional etc. splits here.",
        "Alpha.\r\nBeta.\rGamma.\nDelta.",
        "Done.) Next sentence.",
        "See i.e.) kept together. Next.",
    )
    for text in samples:
        parts = split_segments(text)
        assert "".join(part.text for part in parts) == text


def test_stopword_and_qualifier_pins() -> None:
    assert len(STOPWORDS) <= 120
    assert len(QUALIFIER_TOKENS) == 35
    assert not STOPWORDS & QUALIFIER_TOKENS
    assert not STOPWORDS & DIRECTIVE_TOKENS
    for token in ("excluding", "including", "excl", "incl", "net", "gross"):
        assert token in QUALIFIER_TOKENS
        assert token not in STOPWORDS
    assert "should" not in STOPWORDS
    assert "must" not in STOPWORDS
    assert sha256_text("\n".join(sorted(STOPWORDS)) + "\n") == _STOPWORD_PIN
    assert sha256_text("\n".join(sorted(QUALIFIER_TOKENS)) + "\n") == _QUALIFIER_PIN
    assert sha256_text("\n".join(sorted(DIRECTIVE_TOKENS)) + "\n") == _DIRECTIVE_PIN
    assert sha256_text("\n".join(NON_BREAKING_ABBREVIATIONS) + "\n") == _ABBREVIATION_PIN
    payload = "\n".join(REFERENCE_MARKERS) + "\n--\n" + "\n".join(SCOPE_POINTER_MARKERS) + "\n"
    assert sha256_text(payload) == _MARKER_PIN
    assert len(DerivedRuleCode) == 19
    derived = "\n".join(item.value for item in DerivedRuleCode) + "\n"
    assert sha256_text(derived) == _DERIVED_RULE_PIN


def test_non_breaking_abbreviations_keep_the_sentence() -> None:
    kept = "Alpha i.e. beta. Gamma."
    parts = split_segments(kept)
    assert [part.text for part in parts] == ["Alpha i.e. beta.", " Gamma."]
    folded = "Alpha I.E. beta. Gamma."
    assert [part.text for part in split_segments(folded)] == ["Alpha I.E. beta.", " Gamma."]
    abbreviations = "See vs. approx. incl. excl. no. 3 e.g. items. Next."
    assert [part.text for part in split_segments(abbreviations)] == [
        "See vs. approx. incl. excl. no. 3 e.g. items.",
        " Next.",
    ]
    etc = "Fictional etc. splits here."
    assert [part.text for part in split_segments(etc)] == ["Fictional etc.", " splits here."]
    assert [part.text for part in split_segments("anno. Next.")] == ["anno.", " Next."]
    newline = "Alpha i.e.\nBeta."
    assert [part.text for part in split_segments(newline)] == ["Alpha i.e.\n", "Beta."]
    assert [part.text for part in split_segments("Done.) Next.")] == ["Done.)", " Next."]
    assert [part.text for part in split_segments("Done.] Next.")] == ["Done.]", " Next."]
    assert [part.text for part in split_segments('Done." Next.')] == ['Done."', " Next."]
    assert [part.text for part in split_segments("Done.' Next.")] == ["Done.'", " Next."]
    assert [part.text for part in split_segments("Done.\u201d Next.")] == ["Done.\u201d", " Next."]
    assert [part.text for part in split_segments("Done.\u2019 Next.")] == ["Done.\u2019", " Next."]
    assert [part.text for part in split_segments("See i.e.) next item.")] == [
        "See i.e.) next item."
    ]
    assert [part.text for part in split_segments("Alpha.\r\nBeta.")] == ["Alpha.", "\r\n", "Beta."]
    assert [part.text for part in split_segments("Alpha.\rBeta.")] == ["Alpha.", "\r", "Beta."]
    assert [part.text for part in split_segments("Alpha.\nBeta.")] == ["Alpha.", "\n", "Beta."]


def test_digits_count_as_qualifiers_and_content() -> None:
    tokens = tokenize("Fictional 07 row")
    digit = next(token for token in tokens if token.text == "07")
    assert digit.qualifier is True
    assert digit.content is True


def test_quote_occurrence_overlap_and_fold() -> None:
    text = "Fictional fictional fictional"
    first = resolve_in_text(text, "fictional", 0)
    second = resolve_in_text(text, "fictional", 1)
    assert not isinstance(first, str)
    assert not isinstance(second, str)
    assert second.start >= first.end
    assert resolve_in_text(text, "fictional", 9) == Code.GL02_QUOTE_NOT_FOUND.value
    folded = "Fictional\u00a0question \u201cquoted\u201d \u2013 dash"
    needle = 'fictional question "quoted" - dash'
    assert resolve_in_text(folded, needle, 0) == Code.GL02_QUOTE_FOLD_MATCH_ONLY.value
    found = locate_phrase(folded, needle)
    assert not isinstance(found, str)
    assert found[0].folded_only is True
    assert "\\u" in escape_text(folded[found[0].start : found[0].end])
    assert "\\n" in escape_text("Fictional\nline")
    assert fold_text("Fictional\u00a0Question") == "fictional question"
    assert fold_text("Fictional\nquestion") == "fictional question"
    newline_hit = locate_phrase("Fictional\nquestion", "fictional question")
    assert not isinstance(newline_hit, str)
    assert newline_hit[0].folded_only is True
    overlap = "Fictional evidence row"
    left = resolve_in_text(overlap, "Fictional evidence", 0)
    right = resolve_in_text(overlap, "evidence row", 0)
    assert not isinstance(left, str)
    assert not isinstance(right, str)
    assert left.start < right.start < left.end


def test_materiality_uses_the_label_threshold() -> None:
    four = "alpha beta gamma delta"
    five = "alpha beta gamma delta epsilon"
    assert is_material(four, label=False) is True
    assert is_material(four, label=True) is False
    assert is_material(five, label=True) is True
    assert is_material("row 01", label=False) is True
    short_question = "what are the terms of the deal?"
    assert is_material(short_question, label=False) is True
    assert is_material("what are the terms of the deal", label=False) is False
    assert is_material(f"{short_question}  ", label=False) is True
    assert is_material("Check the row", label=False) is True
    assert is_material("Check the row", label=True) is True
    assert is_material("checking the row now", label=False) is False


def test_cross_parent_quote_and_disposition_failures(
    authored_register: tuple[Path, Path],
) -> None:
    root, _workbook = authored_register
    register = load_register(root)
    cited = register.parents_by_coordinate["FDD-PL-020"]
    quoter = next(
        child for _shard, child in register.children if child.parent_id != cited.parent_id
    )
    covered = quoter.model_copy(
        update={
            "source_quotes": (
                SourceQuote(
                    field="question",
                    occurrence=0,
                    parent_id=cited.parent_id,
                    text=cited.question_raw,
                ),
            )
        }
    )
    accounted = account_field(cited, "question", (covered,), ())
    assert accounted.quotes
    assert Code.GL02_RUN_UNDISPOSITIONED.value not in {item.code for item in accounted.findings}
    shortened = cited.question_raw[: cited.question_raw.rfind(" row")]
    partial = covered.model_copy(
        update={
            "source_quotes": (
                SourceQuote(
                    field="question",
                    occurrence=0,
                    parent_id=cited.parent_id,
                    text=shortened,
                ),
            )
        }
    )
    uncovered = account_field(cited, "question", (partial,), ())
    assert Code.GL02_RUN_UNDISPOSITIONED.value in {item.code for item in uncovered.findings}
    run = next(item for item in uncovered.runs if item.material)
    assert any(
        f"question:{run.start}:{run.end}" in item.ids
        for item in uncovered.findings
        if item.code == Code.GL02_RUN_UNDISPOSITIONED.value
    )
    invalid = Disposition(
        kind=DispositionKind.RUN,
        parent_id=cited.parent_id,
        field="question",
        start=run.start,
        end=run.end,
        text_sha256=run.text_sha256,
        disposition="not-a-real-kind",
    )
    invalid_account = account_field(cited, "question", (partial,), (invalid,))
    assert Code.GL02_DISPOSITION_KIND_INVALID.value in {
        item.code for item in invalid_account.findings
    }
    unlinked = invalid.model_copy(
        update={"disposition": RunDisposition.RESTATES_CHILD.value, "child_ids": ()}
    )
    unlinked_account = account_field(cited, "question", (partial,), (unlinked,))
    assert Code.GL02_DISPOSITION_RESTATES_UNLINKED.value in {
        item.code for item in unlinked_account.findings
    }


def test_focus_whole_cell_fails_until_a_joint_reason(
    authored_register: tuple[Path, Path],
) -> None:
    root, _workbook = authored_register
    register = load_register(root)
    parent = register.parents_by_coordinate["FDD-PL-020"]
    text = (
        "Fictional alpha evidence row 01. "
        "Fictional beta evidence row 02. "
        "Fictional gamma evidence row 03."
    )
    parent = parent.model_copy(update={"question_raw": text})
    child = next(
        child for _shard, child in register.children if child.parent_id == parent.parent_id
    )
    quoted = child.model_copy(
        update={
            "joint_answer_reason": None,
            "source_quotes": (
                SourceQuote(field="question", occurrence=0, parent_id=None, text=text),
            ),
        }
    )
    failed = account_field(parent, "question", (quoted,), ())
    focused = [item for item in failed.findings if item.code == Code.GL02_SEGMENT_NOT_FOCUSED.value]
    assert len(focused) > 2
    expected_spans = {
        f"question:{left}:{right}"
        for segment in split_segments(text)
        for left, right in (trim_span(text, segment.start, segment.end),)
    }
    assert {token for item in focused for token in item.ids if token.startswith("question:")} == (
        expected_spans
    )
    assert all(quoted.child_id in item.ids for item in focused)
    passed = account_field(
        parent,
        "question",
        (quoted.model_copy(update={"joint_answer_reason": "Fictional joint answer reason."}),),
        (),
    )
    assert Code.GL02_SEGMENT_NOT_FOCUSED.value not in {item.code for item in passed.findings}


def test_uncovered_dispositioned_segment_passes_both_rules(
    authored_register: tuple[Path, Path],
) -> None:
    parent, child, run = _preamble_case(authored_register)
    disposition = Disposition(
        kind=DispositionKind.RUN,
        parent_id=parent.parent_id,
        field="question",
        start=run.start,
        end=run.end,
        text_sha256=run.text_sha256,
        disposition=RunDisposition.RATIONALE_ONLY.value,
    )
    accounted = account_field(parent, "question", (child,), (disposition,))
    errors = {item.code for item in accounted.findings if item.severity is FindingSeverity.ERROR}
    assert Code.GL02_RUN_UNDISPOSITIONED.value not in errors
    assert Code.GL02_SEGMENT_NOT_FOCUSED.value not in errors
    assert errors == set()


def test_uncovered_undispositioned_segment_is_only_a_run(
    authored_register: tuple[Path, Path],
) -> None:
    parent, child, run = _preamble_case(authored_register)
    accounted = account_field(parent, "question", (child,), ())
    errors = [item for item in accounted.findings if item.severity is FindingSeverity.ERROR]
    assert {item.code for item in errors} == {Code.GL02_RUN_UNDISPOSITIONED.value}
    assert Code.GL02_SEGMENT_NOT_FOCUSED.value not in {item.code for item in errors}
    assert all(f"question:{run.start}:{run.end}" in item.ids for item in errors)
    assert all(parent.parent_id in item.ids for item in errors)


def _preamble_case(authored_register: tuple[Path, Path]):
    root, _workbook = authored_register
    register = load_register(root)
    preamble = "Fictional rationale only row 01."
    obligation = "Fictional obligation evidence row 02."
    text = f"{preamble} {obligation}"
    parent = register.parents_by_coordinate["FDD-PL-020"].model_copy(update={"question_raw": text})
    child = next(
        child for _shard, child in register.children if child.parent_id == parent.parent_id
    )
    quoted = child.model_copy(
        update={
            "joint_answer_reason": None,
            "source_quotes": (
                SourceQuote(field="question", occurrence=0, parent_id=None, text=obligation),
            ),
        }
    )
    preview = account_field(parent, "question", (quoted,), ())
    run = next(item for item in preview.runs if item.material)
    return parent, quoted, run


def test_marker_tiers_and_dispositions(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    register = load_register(root)
    parent = register.parents_by_coordinate["FDD-PL-020"].model_copy(
        update={"question_raw": "Fictional same as above for P&L row 01."}
    )
    child = next(
        child for _shard, child in register.children if child.parent_id == parent.parent_id
    )
    explicit = child.model_copy(
        update={
            "source_quotes": (
                SourceQuote(
                    field="question",
                    occurrence=0,
                    parent_id=None,
                    text=parent.question_raw,
                ),
            )
        }
    )
    accounted = account_field(parent, "question", (explicit,), ())
    unaccounted = [
        item for item in accounted.findings if item.code == Code.GL02_MARKER_UNACCOUNTED.value
    ]
    assert unaccounted
    assert all(any(token.startswith("question:") for token in item.ids) for item in unaccounted)
    applied = explicit.model_copy(update={"derivation": Derivation.CROSS_REFERENCE_APPLIED})
    covered = account_field(parent, "question", (applied,), ())
    assert Code.GL02_MARKER_UNACCOUNTED.value not in {item.code for item in covered.findings}
    stale = Disposition(
        kind=DispositionKind.RUN,
        parent_id=parent.parent_id,
        field="question",
        start=0,
        end=1,
        text_sha256="a" * 64,
        disposition=RunDisposition.CONNECTIVE.value,
    )
    stale_account = account_field(parent, "question", (explicit,), (stale,))
    stale_findings = [
        item for item in stale_account.findings if item.code == Code.GL02_DISPOSITION_STALE.value
    ]
    assert stale_findings
    assert all("question:0:1" in item.ids for item in stale_findings)
    assert explicit.status is ChildStatus.ACTIVE


def test_quote_failure_findings_carry_spans(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _workbook = authored_register
    register = load_register(root)
    parent = register.parents_by_coordinate["FDD-PL-004"]
    child_id = f"GL02-{parent.public_coordinate}-C001"
    folded = parent.question_raw.casefold()
    assert folded != parent.question_raw
    fold_root = clone_register(root, tmp_path / "fold-span")

    def _fold(child):
        quotes = tuple(
            quote.model_copy(update={"text": folded}) if quote.field == "question" else quote
            for quote in child.source_quotes
        )
        return child.model_copy(update={"source_quotes": quotes})

    update_child(fold_root, "FDD-PL-004", _fold)
    fold_findings = [
        item
        for item in validate_register(fold_root).findings
        if item.code == Code.GL02_QUOTE_FOLD_MATCH_ONLY.value and child_id in item.ids
    ]
    assert fold_findings
    assert all(f"question:0:{len(parent.question_raw)}" in item.ids for item in fold_findings)
    missing_root = clone_register(root, tmp_path / "missing-span")

    def _missing(child):
        quotes = tuple(
            quote.model_copy(update={"text": "Fictional absent phrase zz."})
            if quote.field == "question"
            else quote
            for quote in child.source_quotes
        )
        return child.model_copy(update={"source_quotes": quotes})

    update_child(missing_root, "FDD-PL-004", _missing)
    missing = [
        item
        for item in validate_register(missing_root).findings
        if item.code == Code.GL02_QUOTE_NOT_FOUND.value and child_id in item.ids
    ]
    assert missing
    assert all(child_id in item.ids and "question:0:0" in item.ids for item in missing)

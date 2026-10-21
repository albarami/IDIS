"""Duplicate dispositions and stale spans excluded from the heavy warning."""

from __future__ import annotations

from pathlib import Path

from idis.methodology.obligations.jsonl import write_jsonl
from idis.methodology.obligations.models_authored import ChildObligation, Disposition
from idis.methodology.obligations.register_io import load_register
from idis.methodology.obligations.runs import account_field
from idis.methodology.obligations.validate import validate_register
from idis.methodology.obligations.vocab import Code, DispositionKind, RunDisposition
from tests.gl02_register.mutate import clone_register, finding_codes, update_child


def test_disposition_duplicate_passes(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    assert Code.GL02_DISPOSITION_DUPLICATE.value not in finding_codes(root)


def test_disposition_duplicate_mutation(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _workbook = authored_register
    before = finding_codes(root)
    copied = clone_register(root, tmp_path / "duplicate")
    question = load_register(copied).parents_by_coordinate["FDD-PL-020"].question_raw
    shortened = question[: question.rfind(" row")]

    def _shorten(child: ChildObligation) -> ChildObligation:
        quotes = tuple(
            quote.model_copy(update={"text": shortened}) if quote.field == "question" else quote
            for quote in child.source_quotes
        )
        return child.model_copy(update={"source_quotes": quotes})

    update_child(copied, "FDD-PL-020", _shorten)
    register = load_register(copied)
    parent = register.parents_by_coordinate["FDD-PL-020"]
    child = next(item for _shard, item in register.children if item.parent_id == parent.parent_id)
    preview = account_field(parent, "question", (child,), ())
    run = next(item for item in preview.runs if item.material)
    disposition = Disposition(
        kind=DispositionKind.RUN,
        parent_id=parent.parent_id,
        field="question",
        start=run.start,
        end=run.end,
        text_sha256=run.text_sha256,
        disposition=RunDisposition.CONNECTIVE.value,
    )
    write_jsonl(
        copied / "authored" / "dispositions" / "PL.jsonl",
        (disposition, disposition.model_copy(update={"note": "Fictional second copy."})),
        key=lambda item: (item.parent_id, item.field, item.kind.value, item.start, item.end),
    )
    added = finding_codes(copied) - before
    assert added == {Code.GL02_DISPOSITION_DUPLICATE.value}
    hits = [
        item
        for item in validate_register(copied).findings
        if item.code == Code.GL02_DISPOSITION_DUPLICATE.value
    ]
    assert hits
    assert all(f"question:{run.start}:{run.end}" in item.ids for item in hits)


def test_disposition_heavy_ignores_stale_spans(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    parent = load_register(root).parents_by_coordinate["FDD-PL-020"]
    text = "Fictional rationale only row 01 evidence."
    parent = parent.model_copy(update={"question_raw": text})
    stale = Disposition(
        kind=DispositionKind.RUN,
        parent_id=parent.parent_id,
        field="question",
        start=0,
        end=len(text),
        text_sha256="a" * 64,
        disposition=RunDisposition.RATIONALE_ONLY.value,
    )
    stale_account = account_field(parent, "question", (), (stale,))
    stale_codes = {item.code for item in stale_account.findings}
    assert Code.GL02_DISPOSITION_HEAVY.value not in stale_codes
    assert Code.GL02_DISPOSITION_STALE.value in stale_codes
    run = next(item for item in stale_account.runs if item.material)
    live = stale.model_copy(
        update={"start": run.start, "end": run.end, "text_sha256": run.text_sha256}
    )
    live_codes = {item.code for item in account_field(parent, "question", (), (live,)).findings}
    assert Code.GL02_DISPOSITION_HEAVY.value in live_codes
    assert Code.GL02_DISPOSITION_STALE.value not in live_codes

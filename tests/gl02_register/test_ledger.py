"""Ledger seal and rule I-16. Fixtures are fictional."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from idis.methodology.obligations.errors import Gl02Error
from idis.methodology.obligations.findings import make_finding
from idis.methodology.obligations.jsonl import read_jsonl, tree_hash, write_jsonl
from idis.methodology.obligations.ledger import command_seal, seal_block_reason, sealed_version
from idis.methodology.obligations.manifest import assemble_public_manifest, assert_public_safe
from idis.methodology.obligations.models_authored import ChildObligation, LedgerRecord, SealRecord
from idis.methodology.obligations.models_records import ValidationReport
from idis.methodology.obligations.register_io import load_register, parent_shard
from idis.methodology.obligations.validate import invariant_status, validate_register
from idis.methodology.obligations.vocab import (
    SKIPPED_BASELINE_ABSENT,
    SKIPPED_LEDGER_ABSENT,
    SKIPPED_VIEWS_ABSENT,
    SKIPPED_WORKBOOK_ABSENT,
    ChildStatus,
    Code,
    FindingSeverity,
    InvariantStatus,
    Shard,
)
from tests.gl02_register.mutate import clone_register, finding_codes
from tests.gl02_register.test_invariants_later import _policy_register

_NOW = "2026-10-21T01:02:03Z"
_NOW_2 = "2026-10-21T01:02:04Z"
_PIN = Code.GL02_SOURCE_PIN_MISMATCH.value


def test_seal_block_reason_distinguishes_full_and_shard_scope() -> None:
    skip = ValidationReport(
        findings=(),
        skipped_checks=(SKIPPED_WORKBOOK_ABSENT, SKIPPED_LEDGER_ABSENT),
        register_complete=False,
    )
    assert (
        seal_block_reason(skip, shard_names=None, owners={}) == f"skipped={SKIPPED_WORKBOOK_ABSENT}"
    )
    ledger_only = ValidationReport(
        findings=(),
        skipped_checks=(SKIPPED_LEDGER_ABSENT,),
        register_complete=False,
    )
    assert seal_block_reason(ledger_only, shard_names=None, owners={}) is None
    assert seal_block_reason(skip, shard_names=["PL"], owners={}) is None
    pending = make_finding(
        Code.PENDING_CROSS_SHARD.value,
        severity=FindingSeverity.INFO,
        ids=("FDD-CF-004",),
    )
    pending_report = ValidationReport(
        findings=(pending,),
        skipped_checks=(SKIPPED_LEDGER_ABSENT,),
        register_complete=False,
    )
    assert seal_block_reason(pending_report, shard_names=None, owners={}) == pending.message
    assert (
        seal_block_reason(pending_report, shard_names=["PL"], owners={"FDD-CF-004": Shard.CF})
        is None
    )
    pin = make_finding(_PIN, scope="workbook", count=1)
    pin_report = ValidationReport(
        findings=(pin,),
        skipped_checks=(SKIPPED_LEDGER_ABSENT,),
        register_complete=False,
    )
    assert seal_block_reason(pin_report, shard_names=None, owners={}) == pin.message
    assert seal_block_reason(pin_report, shard_names=["PL"], owners={}) == pin.message
    outside = make_finding(Code.GL02_PARENT_WITHOUT_CHILD.value, ids=("cf-parent",))
    outside_report = ValidationReport(
        findings=(outside,),
        skipped_checks=(SKIPPED_LEDGER_ABSENT,),
        register_complete=False,
    )
    assert (
        seal_block_reason(outside_report, shard_names=["PL"], owners={"cf-parent": Shard.CF})
        is None
    )
    missing = make_finding(
        Code.GL02_LEDGER_ID_MISSING.value,
        scope="ledger",
        ids=("GL02-FDD-CF-004-C001", "cf-parent"),
    )
    missing_report = ValidationReport(
        findings=(missing,),
        skipped_checks=(SKIPPED_LEDGER_ABSENT,),
        register_complete=False,
    )
    assert (
        seal_block_reason(
            missing_report,
            shard_names=["PL"],
            owners={"GL02-FDD-CF-004-C001": Shard.CF, "cf-parent": Shard.CF},
        )
        == missing.message
    )


def test_full_seal_refuses_and_writes_nothing(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    root = clone_register(authored_register[0], tmp_path / "refused")
    before = _files(root)
    code, text = command_seal(root, now=_NOW)
    assert code == 1
    assert text.startswith("GL02_")
    assert _files(root) == before
    assert not (root.parent / f"{root.name}-snapshots").exists()


def test_snapshot_root_inside_the_private_root_is_refused(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    root = clone_register(authored_register[0], tmp_path / "inside")
    with pytest.raises(Gl02Error) as error:
        command_seal(root, shards=["PL"], snapshot_root=root / "snaps", now=_NOW)
    assert error.value.code == Code.GL02_USAGE.value
    assert not (root / "ledger").exists()


def test_shard_seal_ledgers_only_that_shard_and_matches_the_tree(
    authored_register: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = clone_register(authored_register[0], tmp_path / "sealed")
    _pin_fictional(monkeypatch, root)
    code, text = command_seal(root, shards=["PL"], now=_NOW)
    assert code == 0
    assert text.startswith("seq=1 ")
    assert "new_ids=29 " in text
    assert f"tree_hash={tree_hash(root)} " in text
    records = read_jsonl(root / "ledger" / "issued_ids.jsonl", LedgerRecord)
    assert [item.child_id for item in records] == sorted(item.child_id for item in records)
    assert all("FDD-PL-" in item.child_id for item in records)
    assert len(records) == 29
    assert {item.first_seal for item in records} == {1}
    seals = read_jsonl(root / "ledger" / "seals.jsonl", SealRecord)
    assert [item.seq for item in seals] == [1]
    assert seals[0].scope == ("PL",)
    assert seals[0].created_utc == _NOW
    assert seals[0].register_complete is False
    name = text.strip().split("snapshot=", 1)[1]
    snapshot = root.parent / f"{root.name}-snapshots" / name
    assert tree_hash(snapshot) == tree_hash(root)
    assert (snapshot / "ledger" / "issued_ids.jsonl").read_bytes() == (
        root / "ledger" / "issued_ids.jsonl"
    ).read_bytes()
    assert not (snapshot / "ledger" / "seals.jsonl").exists()
    assert sealed_version(root) == seals[0].tree_hash


def test_second_seal_is_unchanged_until_an_authored_edit(
    authored_register: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = clone_register(authored_register[0], tmp_path / "twice")
    _pin_fictional(monkeypatch, root)
    assert command_seal(root, shards=["PL"], now=_NOW)[0] == 0
    issued = (root / "ledger" / "issued_ids.jsonl").read_bytes()
    seals = (root / "ledger" / "seals.jsonl").read_bytes()
    (root / "authoring").mkdir()
    (root / "authoring" / "note.py").write_text("x\n", encoding="utf-8", newline="\n")
    (root / "manifest").mkdir()
    (root / "manifest" / "extra.json").write_text("{}\n", encoding="utf-8", newline="\n")
    (root / "ledger" / "note.txt").write_text("x\n", encoding="utf-8", newline="\n")
    code, text = command_seal(root, shards=["PL"], now=_NOW_2)
    assert code == 0
    assert text == "UNCHANGED seq=1\n"
    assert (root / "ledger" / "issued_ids.jsonl").read_bytes() == issued
    assert (root / "ledger" / "seals.jsonl").read_bytes() == seals
    _add_child(root, "FDD-PL-004")
    code, text = command_seal(root, shards=["PL"], now="2026-10-21T01:02:05Z")
    assert code == 0
    assert text.startswith("seq=2 ")
    assert "new_ids=1 " in text
    assert (root / "ledger" / "issued_ids.jsonl").read_bytes().startswith(issued)
    records = read_jsonl(root / "ledger" / "issued_ids.jsonl", LedgerRecord)
    assert records[-1].child_id == "GL02-FDD-PL-004-C002"
    assert records[-1].first_seal == 2
    assert [item.seq for item in read_jsonl(root / "ledger" / "seals.jsonl", SealRecord)] == [1, 2]


def test_i16_ledger_passes(
    authored_register: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = clone_register(authored_register[0], tmp_path / "pass")
    _pin_fictional(monkeypatch, root)
    command_seal(root, shards=["PL"], now=_NOW)
    report = validate_register(root)
    assert Code.GL02_LEDGER_ID_MISSING.value not in {item.code for item in report.findings}
    assert Code.GL02_LEDGER_ID_MOVED.value not in {item.code for item in report.findings}
    assert Code.GL02_LEDGER_FILE_INVALID.value not in {item.code for item in report.findings}
    assert Code.GL02_LEDGER_INTEGRITY.value not in {item.code for item in report.findings}
    assert Code.GL02_LEDGER_TREE_CHANGED.value not in {item.code for item in report.findings}
    assert SKIPPED_LEDGER_ABSENT not in report.skipped_checks
    assert invariant_status("I-16", report) == InvariantStatus.PASS.value
    manifest = assemble_public_manifest(load_register(root), report)
    assert_public_safe(manifest)
    ledger = manifest["ledger"]
    assert isinstance(ledger, dict)
    assert ledger["sealed"] is True
    assert ledger["seal_count"] == 1
    assert ledger["last_seal_seq"] == 1
    assert ledger["issued_ids"] == 29
    assert ledger["last_tree_hash"] == sealed_version(root)


def test_i16_ledger_id_missing(
    authored_register: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _sealed(authored_register[0], tmp_path / "missing", monkeypatch)
    before = finding_codes(root)
    parent_id = load_register(root).parents_by_coordinate["FDD-PL-005"].parent_id
    _drop_child(root, "FDD-PL-005")
    report = validate_register(root)
    missing = [item for item in report.findings if item.code == Code.GL02_LEDGER_ID_MISSING.value]
    assert len(missing) == 1
    assert "GL02-FDD-PL-005-C001" in missing[0].ids
    assert parent_id in missing[0].ids
    assert finding_codes(root) - before == {
        Code.GL02_LEDGER_ID_MISSING.value,
        Code.GL02_LEDGER_TREE_CHANGED.value,
        Code.GL02_MARKER_UNACCOUNTED.value,
        Code.GL02_PARENT_WITHOUT_CHILD.value,
        Code.GL02_RUN_UNDISPOSITIONED.value,
    }


def test_i16_ledger_id_moved(
    authored_register: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _sealed(authored_register[0], tmp_path / "moved", monkeypatch)
    before = finding_codes(root)
    register = load_register(root)
    source_parent = register.parents_by_coordinate["FDD-PL-006"].parent_id
    target = register.parents_by_coordinate["FDD-PL-007"].parent_id
    _replace_child(
        root,
        "FDD-PL-006",
        lambda child: child.model_copy(update={"parent_id": target}),
    )
    moved = [
        item
        for item in validate_register(root).findings
        if item.code == Code.GL02_LEDGER_ID_MOVED.value
    ]
    assert len(moved) == 1
    assert "GL02-FDD-PL-006-C001" in moved[0].ids
    assert source_parent in moved[0].ids
    assert finding_codes(root) - before == {
        Code.GL02_CHILD_ID_INVALID.value,
        Code.GL02_LEDGER_ID_MOVED.value,
        Code.GL02_LEDGER_TREE_CHANGED.value,
        Code.GL02_MARKER_UNACCOUNTED.value,
        Code.GL02_PARENT_WITHOUT_CHILD.value,
        Code.GL02_QUOTE_NOT_FOUND.value,
        Code.GL02_RUN_UNDISPOSITIONED.value,
    }


def test_i16_ledger_id_reused(
    authored_register: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _sealed(authored_register[0], tmp_path / "reused", monkeypatch)
    before = finding_codes(root)
    register = load_register(root)
    path = root / "authored" / "children" / "PL.jsonl"
    children = list(read_jsonl(path, ChildObligation))
    source = next(child for child in children if child.child_id == "GL02-FDD-PL-006-C001")
    other = register.parents_by_coordinate["FDD-PL-007"].parent_id
    children.append(source.model_copy(update={"parent_id": other}))
    write_jsonl(path, children, key=lambda item: (item.parent_id, item.child_id))
    assert finding_codes(root) - before == {
        Code.GL02_CHILD_ID_DUPLICATE.value,
        Code.GL02_CHILD_ID_INVALID.value,
        Code.GL02_LEDGER_INTEGRITY.value,
        Code.GL02_LEDGER_TREE_CHANGED.value,
        Code.GL02_QUOTE_NOT_FOUND.value,
    }


def test_i16_ledger_retired_missing(
    authored_register: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _sealed(authored_register[0], tmp_path / "retired", monkeypatch)
    _add_child(root, "FDD-PL-008", retired=True)
    assert command_seal(root, shards=["PL"], now=_NOW_2)[0] == 0
    assert "GL02-FDD-PL-008-C002" in {
        item.child_id for item in read_jsonl(root / "ledger" / "issued_ids.jsonl", LedgerRecord)
    }
    before = finding_codes(root)
    _drop_child_id(root, "FDD-PL-008", "GL02-FDD-PL-008-C002")
    assert finding_codes(root) - before == {
        Code.GL02_LEDGER_ID_MISSING.value,
        Code.GL02_LEDGER_TREE_CHANGED.value,
    }


def test_i16_ledger_file_invalid(
    authored_register: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _sealed(authored_register[0], tmp_path / "corrupt", monkeypatch)
    before = finding_codes(root)
    path = root / "ledger" / "issued_ids.jsonl"
    path.write_bytes(path.read_bytes() + b"not-json\n")
    assert finding_codes(root) - before == {Code.GL02_LEDGER_FILE_INVALID.value}


def test_i16_ledger_seq_gap(
    authored_register: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _sealed(authored_register[0], tmp_path / "gap", monkeypatch)
    before = finding_codes(root)
    path = root / "ledger" / "seals.jsonl"
    seals = [item.model_copy(update={"seq": 2}) for item in read_jsonl(path, SealRecord)]
    write_jsonl(path, seals, key=lambda item: item.seq)
    assert finding_codes(root) - before == {
        Code.GL02_LEDGER_FILE_INVALID.value,
        Code.GL02_LEDGER_INTEGRITY.value,
    }


def test_tree_change_is_informational(
    authored_register: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _sealed(authored_register[0], tmp_path / "changed", monkeypatch)
    from tests.gl02_register.mutate import update_profile

    update_profile(
        root,
        "FDD-PL-004",
        lambda profile: profile.model_copy(
            update={"parent_summary_note": "Fictional edited summary."}
        ),
    )
    report = validate_register(root)
    found = [item for item in report.findings if item.code == Code.GL02_LEDGER_TREE_CHANGED.value]
    assert len(found) == 1
    assert found[0].severity is FindingSeverity.INFO
    assert found[0].ids == ("1",)


def test_register_complete_stays_false_on_a_fictional_pin(tmp_path: Path) -> None:
    root = _policy_register(tmp_path)
    workbook = root.parent / "fictional.xlsx"
    policy = tmp_path / "fictional-policy.json"
    before = validate_register(root, workbook_path=workbook, policy_path=policy)
    assert before.register_complete is False
    assert SKIPPED_LEDGER_ABSENT in before.skipped_checks
    assert SKIPPED_BASELINE_ABSENT not in before.skipped_checks
    assert SKIPPED_VIEWS_ABSENT not in before.skipped_checks
    files = _files(root)
    code, text = command_seal(root, shards=["PL"], workbook=workbook, policy=policy, now=_NOW)
    assert code == 1
    assert _PIN in text
    assert _files(root) == files
    assert not (root / "ledger").exists()


def _sealed(source: Path, destination: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = clone_register(source, destination)
    _pin_fictional(monkeypatch, root)
    code, text = command_seal(root, shards=["PL"], now=_NOW)
    assert code == 0, text
    return root


def _pin_fictional(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Point the workbook pins at this fictional extraction."""
    from idis.methodology.obligations import checks

    report = load_register(root).extraction_report
    monkeypatch.setattr(checks, "PINNED_WORKBOOK_SHA256", report.workbook.sha256)
    monkeypatch.setattr(checks, "PINNED_WORKBOOK_BYTE_COUNT", report.workbook.byte_count)
    monkeypatch.setattr(
        checks,
        "EXPECTED_DATA_ROW_COUNTS",
        {item.sheet: item.count for item in report.data_row_counts},
    )
    monkeypatch.setattr(
        checks,
        "EXPECTED_MERGED_RANGE_COUNTS",
        {item.sheet: item.count for item in report.merged_range_counts},
    )


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _add_child(root: Path, coordinate: str, *, retired: bool = False) -> None:
    register = load_register(root)
    parent = register.parents_by_coordinate[coordinate]
    shard = parent_shard(parent)
    path = root / "authored" / "children" / f"{shard.value}.jsonl"
    children = list(read_jsonl(path, ChildObligation))
    source = next(child for child in children if child.parent_id == parent.parent_id)
    update: dict[str, object] = {"child_id": f"GL02-{coordinate}-C002"}
    if retired:
        update["status"] = ChildStatus.RETIRED
        update["retired_reason"] = "Fictional retirement."
    children.append(source.model_copy(update=update))
    write_jsonl(path, children, key=lambda item: item.child_id)


def _drop_child(root: Path, coordinate: str) -> None:
    register = load_register(root)
    parent = register.parents_by_coordinate[coordinate]
    shard = parent_shard(parent)
    path = root / "authored" / "children" / f"{shard.value}.jsonl"
    children = [
        child for child in read_jsonl(path, ChildObligation) if child.parent_id != parent.parent_id
    ]
    write_jsonl(path, children, key=lambda item: item.child_id)


def _drop_child_id(root: Path, coordinate: str, child_id: str) -> None:
    register = load_register(root)
    parent = register.parents_by_coordinate[coordinate]
    shard = parent_shard(parent)
    path = root / "authored" / "children" / f"{shard.value}.jsonl"
    children = [child for child in read_jsonl(path, ChildObligation) if child.child_id != child_id]
    write_jsonl(path, children, key=lambda item: item.child_id)


def _replace_child(
    root: Path,
    coordinate: str,
    transform: Callable[[ChildObligation], ChildObligation],
) -> None:
    from tests.gl02_register.mutate import update_child

    update_child(root, coordinate, transform)

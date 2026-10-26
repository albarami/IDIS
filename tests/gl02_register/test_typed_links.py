"""Typed-link targets of active children resolve to exactly one record (rule I-08).

Fixtures are fictional. The new diagnostic codes are pinned as literals so the
mutation tests exercise the accepted baseline before the enum lands.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from idis.methodology.obligations.jsonl import read_jsonl, write_jsonl
from idis.methodology.obligations.ledger import command_seal
from idis.methodology.obligations.models_authored import (
    Ambiguity,
    CrossRowInstruction,
    SourceQuote,
)
from idis.methodology.obligations.models_records import Finding, ValidationReport
from idis.methodology.obligations.register_io import load_register, parent_shard
from idis.methodology.obligations.validate import invariant_status, validate_register
from idis.methodology.obligations.vocab import (
    AmbiguityDecisionOwner,
    AmbiguityStatus,
    AmbiguityTopic,
    ChildStatus,
    Code,
    CrossRowKind,
    Derivation,
    FindingSeverity,
    InvariantStatus,
    Shard,
)
from tests.gl02_register.mutate import clone_register, finding_codes, update_child
from tests.gl02_register.test_ledger import _NOW, _files, _pin_fictional

_UNRESOLVED = "GL02_TYPED_LINK_UNRESOLVED"
_AMBIGUOUS = "GL02_TYPED_LINK_AMBIGUOUS"
_TYPED_CODES = frozenset({_UNRESOLVED, _AMBIGUOUS})
_MISSING_AMBIGUITY = "AMB-FICTIONAL-MISSING-01"
_MISSING_CROSS_ROW = "XR-FICTIONAL-MISSING-01"
_OTHER_AMBIGUITY = "AMB-FICTIONAL-OTHER-01"
_OTHER_CROSS_ROW = "XR-FICTIONAL-01"


def _typed(report: ValidationReport) -> list[Finding]:
    return [item for item in report.findings if item.code in _TYPED_CODES]


def _write_ambiguity(root: Path, shard: str, ambiguity_id: str, parent_id: str) -> None:
    """Append one OTHER-topic ambiguity record to a shard file."""
    path = root / "authored" / "ambiguities" / f"{shard}.jsonl"
    records = list(read_jsonl(path, Ambiguity)) if path.is_file() else []
    records.append(
        Ambiguity(
            ambiguity_id=ambiguity_id,
            topic_code=AmbiguityTopic.OTHER,
            affected_parent_ids=(parent_id,),
            description="Fictional typed-link ambiguity.",
            decision_owner=AmbiguityDecisionOwner.FUND,
            blocks_definitive_answer=False,
            status=AmbiguityStatus.OPEN,
        )
    )
    write_jsonl(path, records, key=lambda item: item.ambiguity_id)


def _write_cross_row(
    root: Path,
    shard: str,
    source_coordinate: str,
    target_coordinate: str,
    instruction_id: str,
) -> None:
    """Append one cross-row instruction whose quote resolves in the source parent."""
    register = load_register(root)
    source = register.parents_by_coordinate[source_coordinate]
    target = register.parents_by_coordinate[target_coordinate]
    path = root / "authored" / "cross_row" / f"{shard}.jsonl"
    records = list(read_jsonl(path, CrossRowInstruction)) if path.is_file() else []
    records.append(
        CrossRowInstruction(
            instruction_id=instruction_id,
            kind=CrossRowKind.SAME_AS_ABOVE,
            source_parent_id=source.parent_id,
            source_field="question",
            source_quote=SourceQuote(
                field="question",
                occurrence=0,
                parent_id=None,
                text=source.question_raw,
            ),
            target_parent_ids=(target.parent_id,),
            note="Fictional typed-link instruction.",
        )
    )
    write_jsonl(path, records, key=lambda item: item.instruction_id)


def _apply_cross_row(
    root: Path, coordinate: str, instruction_id: str, source_coordinate: str
) -> None:
    """Make one child an applied cross-reference child of the instruction."""
    register = load_register(root)
    source = register.parents_by_coordinate[source_coordinate]

    def _edit(child):  # type: ignore[no-untyped-def]
        return child.model_copy(
            update={
                "cross_row_instruction_ids": (
                    *child.cross_row_instruction_ids,
                    instruction_id,
                ),
                "derivation": Derivation.CROSS_REFERENCE_APPLIED,
                "derivation_rationale": "Fictional cross-reference rationale.",
                "source_quotes": (
                    *child.source_quotes,
                    SourceQuote(
                        field="question",
                        occurrence=0,
                        parent_id=source.parent_id,
                        text=source.question_raw,
                    ),
                ),
            }
        )

    update_child(root, coordinate, _edit)


def test_typed_links_pass(authored_register: tuple[Path, Path]) -> None:
    """A clean register has no typed-link finding; legal empty arrays stay valid."""
    root, _workbook = authored_register
    assert not _typed(validate_register(root))


def test_unlinked_other_ambiguity_is_legal(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    """An OTHER-topic record no child cites produces no finding."""
    root = clone_register(authored_register[0], tmp_path / "unlinked")
    parent_id = load_register(root).parents_by_coordinate["FDD-PL-004"].parent_id
    _write_ambiguity(root, "XS", "AMB-FICTIONAL-UNLINKED-01", parent_id)
    assert not _typed(validate_register(root))


def test_missing_ambiguity_target_fails(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    root = clone_register(authored_register[0], tmp_path / "amb-missing")
    update_child(
        root,
        "FDD-PL-004",
        lambda child: child.model_copy(update={"ambiguity_ids": (_MISSING_AMBIGUITY,)}),
    )
    report = validate_register(root)
    typed = _typed(report)
    assert len(typed) == 1
    assert typed[0].code == _UNRESOLVED
    assert typed[0].count == 0
    assert "GL02-FDD-PL-004-C001" in typed[0].ids
    assert "ambiguity_ids" in typed[0].ids
    assert _MISSING_AMBIGUITY in typed[0].ids
    assert invariant_status("I-08", report) == InvariantStatus.FAIL.value


def test_missing_cross_row_target_fails(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    root = clone_register(authored_register[0], tmp_path / "xr-missing")
    update_child(
        root,
        "FDD-PL-004",
        lambda child: child.model_copy(update={"cross_row_instruction_ids": (_MISSING_CROSS_ROW,)}),
    )
    report = validate_register(root)
    typed = _typed(report)
    assert len(typed) == 1
    assert typed[0].code == _UNRESOLVED
    assert typed[0].count == 0
    assert "GL02-FDD-PL-004-C001" in typed[0].ids
    assert "cross_row_instruction_ids" in typed[0].ids
    assert _MISSING_CROSS_ROW in typed[0].ids
    assert invariant_status("I-08", report) == InvariantStatus.FAIL.value


def test_duplicate_typed_target_is_ambiguous(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    """Same ambiguity id in two shard files is not one record."""
    root = clone_register(authored_register[0], tmp_path / "amb-dup")
    register = load_register(root)
    pl_parent = register.parents_by_coordinate["FDD-PL-004"].parent_id
    _write_ambiguity(root, "XS", "AMB-FICTIONAL-DUP-01", pl_parent)
    _write_ambiguity(root, "PL", "AMB-FICTIONAL-DUP-01", pl_parent)
    update_child(
        root,
        "FDD-PL-004",
        lambda child: child.model_copy(update={"ambiguity_ids": ("AMB-FICTIONAL-DUP-01",)}),
    )
    report = validate_register(root)
    typed = _typed(report)
    assert len(typed) == 1
    assert typed[0].code == _AMBIGUOUS
    assert typed[0].count == 2
    assert "AMB-FICTIONAL-DUP-01" in typed[0].ids
    assert invariant_status("I-08", report) == InvariantStatus.FAIL.value


def test_exactly_one_target_resolves_across_shards(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    """A target in another shard still resolves for a selected child."""
    root = clone_register(authored_register[0], tmp_path / "cross-shard")
    register = load_register(root)
    pl_parent = register.parents_by_coordinate["FDD-PL-004"].parent_id
    _write_ambiguity(root, "XS", _OTHER_AMBIGUITY, pl_parent)
    _write_cross_row(root, "CF", "FDD-CF-004", "FDD-CF-005", _OTHER_CROSS_ROW)
    _apply_cross_row(root, "FDD-CF-005", _OTHER_CROSS_ROW, "FDD-CF-004")

    def _link(child):  # type: ignore[no-untyped-def]
        return child.model_copy(
            update={
                "ambiguity_ids": (_OTHER_AMBIGUITY,),
                "cross_row_instruction_ids": (_OTHER_CROSS_ROW,),
            }
        )

    update_child(root, "FDD-PL-004", _link)
    report = validate_register(root)
    assert not _typed(report)
    assert Code.GL02_CROSS_ROW_UNAPPLIED.value not in finding_codes(root)


def test_multiple_declared_ids_only_the_missing_one_fails(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    root = clone_register(authored_register[0], tmp_path / "mixed")
    pl_parent = load_register(root).parents_by_coordinate["FDD-PL-004"].parent_id
    _write_ambiguity(root, "XS", _OTHER_AMBIGUITY, pl_parent)
    update_child(
        root,
        "FDD-PL-004",
        lambda child: child.model_copy(
            update={"ambiguity_ids": (_OTHER_AMBIGUITY, _MISSING_AMBIGUITY)}
        ),
    )
    typed = _typed(validate_register(root))
    assert len(typed) == 1
    assert typed[0].code == _UNRESOLVED
    assert _MISSING_AMBIGUITY in typed[0].ids
    assert _OTHER_AMBIGUITY not in typed[0].ids


def test_retired_child_declares_no_typed_findings(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    """Retired children keep old checks but are outside this obligation."""
    root = clone_register(authored_register[0], tmp_path / "retired")
    update_child(
        root,
        "FDD-PL-004",
        lambda child: child.model_copy(
            update={
                "status": ChildStatus.RETIRED,
                "retired_reason": "Fictional retirement.",
                "ambiguity_ids": (_MISSING_AMBIGUITY,),
                "cross_row_instruction_ids": (_MISSING_CROSS_ROW,),
            }
        ),
    )
    assert not _typed(validate_register(root))


def test_outside_shard_child_gains_no_typed_finding(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    root = clone_register(authored_register[0], tmp_path / "outside-shard")
    update_child(
        root,
        "FDD-CF-004",
        lambda child: child.model_copy(update={"ambiguity_ids": (_MISSING_AMBIGUITY,)}),
    )
    limited = validate_register(root, shards=["PL"])
    assert not _typed(limited)
    full = validate_register(root)
    assert _typed(full)


def test_outside_view_child_gains_no_typed_finding(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    root = clone_register(authored_register[0], tmp_path / "outside-view")
    update_child(
        root,
        "FDD-PL-005",
        lambda child: child.model_copy(update={"ambiguity_ids": (_MISSING_AMBIGUITY,)}),
    )
    update_child(
        root,
        "FDD-PL-004",
        lambda child: child.model_copy(update={"ambiguity_ids": (_MISSING_CROSS_ROW,)}),
    )
    scoped = validate_register(root, view="fictional-pl")
    typed = _typed(scoped)
    assert typed
    assert all("GL02-FDD-PL-004-C001" in item.ids for item in typed)
    assert all("GL02-FDD-PL-005-C001" not in item.ids for item in typed)


def test_unresolved_selected_child_fails_without_pending(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    """A missing target is an error, never an outside-shard pending guess."""
    root = clone_register(authored_register[0], tmp_path / "selected")
    update_child(
        root,
        "FDD-PL-004",
        lambda child: child.model_copy(update={"ambiguity_ids": (_MISSING_AMBIGUITY,)}),
    )
    report = validate_register(root, shards=["PL"])
    typed = _typed(report)
    assert len(typed) == 1
    pending = [
        item
        for item in report.findings
        if item.code == Code.PENDING_CROSS_SHARD.value and _MISSING_AMBIGUITY in item.ids
    ]
    assert not pending


def test_cross_row_unapplied_guard_is_preserved(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    """A record a child names but does not apply keeps the old I-07 error."""
    root = clone_register(authored_register[0], tmp_path / "unapplied")
    _write_cross_row(root, "CF", "FDD-CF-004", "FDD-CF-005", _OTHER_CROSS_ROW)
    update_child(
        root,
        "FDD-CF-005",
        lambda child: child.model_copy(update={"cross_row_instruction_ids": (_OTHER_CROSS_ROW,)}),
    )
    report = validate_register(root)
    assert not _typed(report)
    unapplied = [
        item
        for item in report.findings
        if item.code == Code.GL02_CROSS_ROW_UNAPPLIED.value and _OTHER_CROSS_ROW in item.ids
    ]
    assert unapplied


def test_free_text_target_id_is_not_echoed(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    """The error still fails but never prints an unsafe token."""
    root = clone_register(authored_register[0], tmp_path / "hostile")
    hostile = "private analyst note ebitda 12.3m"
    update_child(
        root,
        "FDD-PL-004",
        lambda child: child.model_copy(
            update={
                "ambiguity_ids": (hostile,),
                "cross_row_instruction_ids": ("another private token",),
            }
        ),
    )
    typed = _typed(validate_register(root))
    assert len(typed) == 2
    assert all(item.code == _UNRESOLVED for item in typed)
    for item in typed:
        assert hostile not in item.message
        assert "another private token" not in item.message
        assert all(hostile != identifier for identifier in item.ids)


def test_malformed_child_id_is_not_echoed_in_typed_findings(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    root = clone_register(authored_register[0], tmp_path / "bad-child")
    bad_id = "GL02 FDD-PL-004 C001 spaces"
    update_child(
        root,
        "FDD-PL-004",
        lambda child: child.model_copy(
            update={"child_id": bad_id, "ambiguity_ids": (_MISSING_AMBIGUITY,)}
        ),
    )
    typed = _typed(validate_register(root))
    assert len(typed) == 1
    assert typed[0].code == _UNRESOLVED
    assert bad_id not in typed[0].ids
    assert bad_id not in typed[0].message
    assert "ambiguity_ids" in typed[0].ids
    assert _MISSING_AMBIGUITY in typed[0].ids


def test_seal_refuses_a_missing_typed_target_and_writes_nothing(
    authored_register: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = clone_register(authored_register[0], tmp_path / "seal")
    _pin_fictional(monkeypatch, root)
    pre = validate_register(root, shards=["PL"])
    pre_errors = [item for item in pre.findings if item.severity is FindingSeverity.ERROR]
    assert not pre_errors
    update_child(
        root,
        "FDD-PL-004",
        lambda child: child.model_copy(update={"cross_row_instruction_ids": (_MISSING_CROSS_ROW,)}),
    )
    post = validate_register(root, shards=["PL"])
    typed = _typed(post)
    assert len(typed) == 1
    assert typed[0].code == _UNRESOLVED
    before = _files(root)
    code, text = command_seal(root, shards=["PL"], now=_NOW)
    assert code == 1
    assert text.startswith(_UNRESOLVED)
    assert _files(root) == before
    assert not (root / "ledger").exists()
    assert not (root.parent / f"{root.name}-snapshots").exists()


# --- TYPED-02: the NEW findings publish bounded ASCII tokens only -------------
#
# The tokens below are fictional. Several of them printed verbatim under the
# R1 whitespace-only filter; the closed ASCII filter must omit them from
# ``Finding.ids`` and ``Finding.message`` while resolution still fails.

_UNSAFE_TARGETS = (
    "AMB-ZERO\u200bWIDTH-01",  # U+200B zero-width space (Unicode format)
    "XR-BIDI\u202eOVERRIDE-01",  # U+202E right-to-left override (bidi)
    "AMB-<em>markup</em>-01",  # angle-bracket markup
    "XR-COMMA,DELIMITED-01",  # comma collides with the ids joiner
    "AMB-LINE\nBREAK-01",  # newline control
    "XR-" + "9" * 170,  # excessive length
)

_UNSAFE_CHILD_IDS = (
    "GL02-FDD-PL-004-C001\u200bINJ",  # U+200B format char inside the id
    "GL02-FDD-PL-004-C001,<b>",  # comma plus markup
    "GL02-FDD-PL-004-C001\nTAIL",  # newline control
    "GL02-FDD-PL-004-C001-" + "Z" * 170,  # excessive length
)


@pytest.mark.parametrize("target", _UNSAFE_TARGETS)
def test_unsafe_target_tokens_are_never_echoed(
    authored_register: tuple[Path, Path], tmp_path: Path, target: str
) -> None:
    """Resolution still fails but the raw token never enters the diagnostic."""
    root = clone_register(authored_register[0], tmp_path / "unsafe-target")
    field = "ambiguity_ids" if target.startswith("AMB") else "cross_row_instruction_ids"
    update_child(
        root,
        "FDD-PL-004",
        lambda child: child.model_copy(update={field: (target,)}),
    )
    typed = _typed(validate_register(root))
    assert len(typed) == 1
    assert typed[0].code == _UNRESOLVED
    assert typed[0].count == 0
    assert all(target != identifier for identifier in typed[0].ids)
    assert target not in typed[0].message
    assert field in typed[0].ids
    assert "GL02-FDD-PL-004-C001" in typed[0].ids


@pytest.mark.parametrize("bad_id", _UNSAFE_CHILD_IDS)
def test_unsafe_child_ids_are_never_echoed_in_typed_findings(
    authored_register: tuple[Path, Path], tmp_path: Path, bad_id: str
) -> None:
    """The NEW finding drops an unprintable child id while still failing."""
    root = clone_register(authored_register[0], tmp_path / "unsafe-child")
    update_child(
        root,
        "FDD-PL-004",
        lambda child: child.model_copy(
            update={"child_id": bad_id, "ambiguity_ids": (_MISSING_AMBIGUITY,)}
        ),
    )
    typed = _typed(validate_register(root))
    assert len(typed) == 1
    assert typed[0].code == _UNRESOLVED
    assert all(bad_id != identifier for identifier in typed[0].ids)
    assert bad_id not in typed[0].message
    assert _MISSING_AMBIGUITY in typed[0].ids


def test_unusual_unicode_target_with_one_record_still_resolves(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    """The filter is diagnostic-only; it never restricts the source namespace."""
    root = clone_register(authored_register[0], tmp_path / "unusual")
    parent_id = load_register(root).parents_by_coordinate["FDD-PL-004"].parent_id
    unusual = "AMB-ÜNICODE-ZIEL-01"
    _write_ambiguity(root, "XS", unusual, parent_id)
    update_child(
        root,
        "FDD-PL-004",
        lambda child: child.model_copy(update={"ambiguity_ids": (unusual,)}),
    )
    assert not _typed(validate_register(root))


def test_cross_shard_targets_resolve_under_scoped_validation(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    """A selected child resolves global targets under shard and view scoping."""
    root = clone_register(authored_register[0], tmp_path / "scoped-valid")
    pl_parent = load_register(root).parents_by_coordinate["FDD-PL-004"].parent_id
    _write_ambiguity(root, "XS", _OTHER_AMBIGUITY, pl_parent)
    _write_cross_row(root, "CF", "FDD-CF-004", "FDD-CF-005", _OTHER_CROSS_ROW)
    _apply_cross_row(root, "FDD-CF-005", _OTHER_CROSS_ROW, "FDD-CF-004")
    update_child(
        root,
        "FDD-PL-004",
        lambda child: child.model_copy(
            update={
                "ambiguity_ids": (_OTHER_AMBIGUITY,),
                "cross_row_instruction_ids": (_OTHER_CROSS_ROW,),
            }
        ),
    )
    for scope in ({"shards": ["PL"]}, {"view": "fictional-pl"}):
        scoped = validate_register(root, **scope)
        assert not _typed(scoped)


def test_duplicate_cross_row_targets_are_ambiguous(
    authored_register: tuple[Path, Path], tmp_path: Path
) -> None:
    """Same instruction id twice in one file or across files counts as 2 records."""
    root = clone_register(authored_register[0], tmp_path / "xr-dup")
    register = load_register(root)
    asset = sorted(
        parent.public_coordinate
        for parent in register.parents
        if parent_shard(parent) is Shard.ASSET
    )
    _write_cross_row(root, "CF", "FDD-CF-004", "FDD-CF-005", "XR-DUP-SAME-01")
    _write_cross_row(root, "CF", "FDD-CF-006", "FDD-CF-005", "XR-DUP-SAME-01")
    _write_cross_row(root, "CF", "FDD-CF-004", "FDD-CF-005", "XR-DUP-XFILE-01")
    _write_cross_row(root, "ASSET", asset[0], asset[1], "XR-DUP-XFILE-01")
    _apply_cross_row(root, "FDD-CF-005", "XR-DUP-SAME-01", "FDD-CF-004")
    _apply_cross_row(root, "FDD-CF-005", "XR-DUP-SAME-01", "FDD-CF-006")
    _apply_cross_row(root, "FDD-CF-005", "XR-DUP-XFILE-01", "FDD-CF-004")
    _apply_cross_row(root, asset[1], "XR-DUP-XFILE-01", asset[0])
    update_child(
        root,
        "FDD-PL-004",
        lambda child: child.model_copy(
            update={"cross_row_instruction_ids": ("XR-DUP-SAME-01", "XR-DUP-XFILE-01")}
        ),
    )
    report = validate_register(root)
    typed = _typed(report)
    same = [item for item in typed if "XR-DUP-SAME-01" in item.ids]
    xfile = [item for item in typed if "XR-DUP-XFILE-01" in item.ids]
    assert same
    assert xfile
    assert all(item.code == _AMBIGUOUS for item in typed)
    assert all(item.count == 2 for item in typed)
    assert invariant_status("I-08", report) == InvariantStatus.FAIL.value

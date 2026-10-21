"""Public manifest, CLI and validation idempotence."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from idis.methodology.obligations.errors import Gl02Error
from idis.methodology.obligations.manifest import (
    PUBLIC_MANIFEST_KEYS,
    assemble_public_manifest,
    assert_public_safe,
    public_manifest_bytes,
)
from idis.methodology.obligations.validate import validate_register
from idis.methodology.obligations.vocab import (
    EXPECTED_DATA_ROW_COUNTS,
    NAMED_AMBIGUITY_TOPICS,
    Code,
)
from tests.gl02_register.support import structural_workbook_bytes


def _cli():
    spec = importlib.util.spec_from_file_location(
        "gl02_register_cli", Path("scripts/gl02_register.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_i15_public_manifest_passes(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    report = validate_register(root)
    manifest = assemble_public_manifest(load_register_manifest_source(root), report)
    assert_public_safe(manifest)
    assert public_manifest_bytes(manifest) == public_manifest_bytes(manifest)
    assert "question_raw_sha256" not in PUBLIC_MANIFEST_KEYS


def test_i15_public_manifest_mutation(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    report = validate_register(root)
    manifest = assemble_public_manifest(load_register_manifest_source(root), report)
    free_text = dict(manifest)
    free_text["fictional_note"] = "Fictional free text"
    per_text = dict(manifest)
    per_text["question_raw_sha256"] = "a" * 64
    unknown = dict(manifest)
    unknown["unknown_key"] = "FDD-PL-004"
    for broken in (free_text, per_text, unknown):
        with pytest.raises(Gl02Error) as error:
            assert_public_safe(broken)
        assert error.value.code == Code.GL02_MANIFEST_NOT_PUBLIC_SAFE.value
    counts = dict(manifest["counts"])
    warnings = list(counts["warnings_by_code"])
    allowed = dict(manifest)
    allowed_counts = dict(counts)
    allowed_counts["warnings_by_code"] = [
        *warnings,
        {"code": "SKIPPED_LEDGER_LATER", "count": 1},
    ]
    allowed["counts"] = allowed_counts
    assert_public_safe(allowed)
    for code in ("SKIPPED_FREE_TEXT", "SKIPPED_"):
        rejected = dict(manifest)
        rejected_counts = dict(counts)
        rejected_counts["warnings_by_code"] = [*warnings, {"code": code, "count": 1}]
        rejected["counts"] = rejected_counts
        with pytest.raises(Gl02Error) as error:
            assert_public_safe(rejected)
        assert error.value.code == Code.GL02_MANIFEST_NOT_PUBLIC_SAFE.value


def test_public_manifest_is_byte_stable(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    report = validate_register(root)
    register = load_register_manifest_source(root)
    first = public_manifest_bytes(assemble_public_manifest(register, report))
    second = public_manifest_bytes(assemble_public_manifest(register, report))
    assert first == second


def test_validate_does_not_write(authored_register: tuple[Path, Path]) -> None:
    root, _workbook = authored_register
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    validate_register(root)
    after = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert before == after


def test_cli_exit_codes_and_hidden_text(
    authored_register: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, workbook = authored_register
    cli = _cli()
    assert cli.main(["validate", "--private-root", str(root)]) == 1
    validation = capsys.readouterr().out
    assert "GL02_SOURCE_PIN_MISMATCH" in validation
    assert "Review fictional" not in validation
    fresh = tmp_path / "cli"
    workbook_path = tmp_path / "fictional.xlsx"
    workbook_path.write_bytes(structural_workbook_bytes())
    assert (
        cli.main(["extract", "--workbook", str(workbook_path), "--private-root", str(fresh)]) == 0
    )
    parent = "FDD-PL-004"
    assert cli.main(["runs", "--private-root", str(root), "--parent", parent]) == 0
    runs = capsys.readouterr().out
    assert "segment " in runs
    assert "Review fictional" not in runs
    assert (
        cli.main(
            ["quote", "--private-root", str(root), "--parent", parent, "--text", "Review fictional"]
        )
        == 0
    )
    quoted = capsys.readouterr().out
    assert "occurrence=" in quoted
    assert "Review fictional" not in quoted
    assert (
        cli.main(
            [
                "quote",
                "--private-root",
                str(root),
                "--parent",
                parent,
                "--text",
                "review fictional",
                "--show-text",
            ]
        )
        == 0
    )
    shown = capsys.readouterr().out
    assert "text=" in shown
    assert cli.main([]) == 2
    assert cli.main(["validate", "--private-root", str(tmp_path / "missing")]) == 2


def test_runs_json_hides_text_until_requested(
    authored_register: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    root, _workbook = authored_register
    cli = _cli()
    parent = "FDD-PL-004"
    assert (
        cli.main(["runs", "--private-root", str(root), "--parent", parent, "--format", "json"]) == 0
    )
    hidden = capsys.readouterr().out
    assert "Review fictional" not in hidden
    payload = json.loads(hidden)
    assert payload["field"] == "question"
    assert payload["public_coordinate"] == parent
    assert payload["segments"]
    assert "text" not in payload["segments"][0]
    assert "runs" in payload
    assert payload["markers"]
    assert set(payload["markers"][0]) == {"end", "start", "status", "tier"}
    assert (
        cli.main(
            [
                "runs",
                "--private-root",
                str(root),
                "--parent",
                parent,
                "--format",
                "json",
                "--show-text",
            ]
        )
        == 0
    )
    shown = json.loads(capsys.readouterr().out)
    assert "Review fictional" in shown["segments"][0]["text"]


def test_status_counts_do_not_print_text_or_write(
    authored_register: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _workbook = authored_register
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    cli = _cli()
    assert cli.main(["status", "--private-root", str(root), "--format", "json"]) == 0
    raw = capsys.readouterr().out
    assert "Review fictional" not in raw
    assert "fictional-" not in raw
    payload = json.loads(raw)
    by_shard = {row["shard"]: row for row in payload["shards"]}
    assert list(by_shard) == ["PL", "CF", "LIAB", "ASSET", "CDD", "XS"]
    assert by_shard["PL"]["parents"] == EXPECTED_DATA_ROW_COUNTS["P&L"]
    assert by_shard["CF"]["parents"] == EXPECTED_DATA_ROW_COUNTS["Cash Flow"]
    assert by_shard["LIAB"]["parents"] == EXPECTED_DATA_ROW_COUNTS["Liabilities"]
    assert by_shard["ASSET"]["parents"] == EXPECTED_DATA_ROW_COUNTS["Assets"]
    assert by_shard["CDD"]["parents"] == 21
    assert by_shard["XS"]["parents"] == 0
    assert by_shard["XS"]["ambiguities"] == len(NAMED_AMBIGUITY_TOPICS)
    assert by_shard["PL"]["active_children"] == by_shard["PL"]["parents"]
    assert by_shard["PL"]["parents_with_active_child"] == by_shard["PL"]["parents"]
    assert by_shard["PL"]["children_by_derivation"] == {"explicit": by_shard["PL"]["parents"]}
    assert by_shard["CDD"]["children_by_derivation"] == {"template_field": 21}
    assert by_shard["PL"]["material_runs"] == 0
    assert by_shard["PL"]["undispositioned_runs"] == 0
    assert by_shard["PL"]["uncovered_dispositioned_runs"] == 0
    assert by_shard["PL"]["unfocused_covered_segments"] == 0
    assert by_shard["PL"]["unaccounted_markers"] == 0
    assert by_shard["PL"]["marker_occurrences"] > 0
    assert by_shard["PL"]["quotes"] > 0
    assert "GL02_SOURCE_PIN_MISMATCH" in by_shard["PL"]["errors_by_code"]
    assert cli.main(["status", "--private-root", str(root), "--shard", "PL"]) == 0
    text = capsys.readouterr().out
    lines = [line for line in text.splitlines() if line]
    assert len(lines) == 1
    assert lines[0].startswith("shard=PL ")
    assert "parents=29 " in lines[0] or f"parents={EXPECTED_DATA_ROW_COUNTS['P&L']} " in lines[0]
    assert "GL02_SOURCE_PIN_MISMATCH" in lines[0]
    assert "Review fictional" not in text
    after = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert before == after
    assert cli.main(["status", "--private-root", str(tmp_path / "missing")]) == 2
    assert cli.main(["status", "--private-root", str(root), "--shard", "NOPE"]) == 2


def load_register_manifest_source(root: Path):
    """Load a register for manifest assembly."""
    from idis.methodology.obligations.register_io import load_register

    return load_register(root)

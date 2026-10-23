"""Local real-register check. It does nothing unless both paths are set."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from idis.methodology.obligations.checks import check_importer_parity, check_source_coverage
from idis.methodology.obligations.extract import extract_register
from idis.methodology.obligations.manifest import assemble_public_manifest, assert_public_safe
from idis.methodology.obligations.register_io import load_register
from idis.methodology.obligations.validate import validate_register
from idis.methodology.obligations.vocab import Code, FindingSeverity, ParentKind

_SOURCE_PARITY_STALE = frozenset(
    {
        Code.GL02_SOURCE_COVERAGE.value,
        Code.GL02_SOURCE_PIN_MISMATCH.value,
        Code.GL02_SOURCE_FEATURE_PRESENT.value,
        Code.GL02_SOURCE_OTHER_CELL.value,
        Code.GL02_IMPORTER_PARITY_FAILED.value,
        Code.GL02_DERIVED_STALE.value,
    }
)


def test_real_register_local(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Skip unless both coordinator paths are set. Never print source text."""
    workbook = os.environ.get("IDIS_GL02_WORKBOOK_PATH")
    private_root = os.environ.get("IDIS_GL02_PRIVATE_ROOT")
    if not workbook or not private_root:
        pytest.skip("IDIS_GL02_WORKBOOK_PATH and IDIS_GL02_PRIVATE_ROOT are both required")
    root = tmp_path / "extracted"
    report = extract_register(workbook, root)
    register = load_register(root)
    financial = sum(parent.parent_kind is ParentKind.FINANCIAL for parent in register.parents)
    commercial = sum(parent.parent_kind is ParentKind.COMMERCIAL for parent in register.parents)
    assert financial == 79
    assert commercial == 21
    assert sum(item.count for item in report.merged_range_counts) == 33
    assert report.feature_census.other_role_stored_cells == 0
    assert report.parity_failure_ids == ()
    assert check_importer_parity(report) == []
    assert check_source_coverage(report) == []
    private = validate_register(private_root, workbook_path=workbook)
    error_codes = {item.code for item in private.findings if item.severity is FindingSeverity.ERROR}
    assert error_codes.isdisjoint(_SOURCE_PARITY_STALE)
    manifest = assemble_public_manifest(register, validate_register(root))
    assert_public_safe(manifest)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

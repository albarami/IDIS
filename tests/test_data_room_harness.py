"""Tests for Slice 17 local data-room FULL-run harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from idis.evaluation.data_room_harness import run_data_room_harness
from tests.test_xlsx_parser import create_test_xlsx

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
COMPANY_NAME = "Acme Corp"


def test_harness_runs_nested_fixture_and_emits_safe_summary(tmp_path: Path) -> None:
    """A generated nested data room should hand supported docs into the run path safely."""
    _write_mixed_data_room(tmp_path)

    summary = run_data_room_harness(
        data_room_root=tmp_path,
        mode="FULL",
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        company_name=COMPANY_NAME,
    )
    serialized = json.dumps(summary, sort_keys=True, ensure_ascii=False)

    assert summary["mode"] == "FULL"
    assert summary["run_status"] in {"SUCCEEDED", "FAILED"}
    assert summary["inventory"]["file_count"] == 5
    assert summary["inventory"]["supported_file_count"] == 2
    assert summary["inventory"]["deferred_file_count"] == 2
    assert summary["inventory"]["blocked_file_count"] == 1
    assert summary["inventory"]["supported_document_ids"]
    assert (
        summary["preflight"]["eligible_document_ids"]
        == summary["inventory"]["supported_document_ids"]
    )
    assert summary["preflight"]["blocked_document_ids"] == []
    assert _step(summary, "DATA_ROOM_INVENTORY_PACKAGE")["status"] == "COMPLETED"
    assert _step(summary, "INGEST_CHECK")["status"] == "COMPLETED"
    assert _step(summary, "DOCUMENT_PREFLIGHT")["status"] == "COMPLETED"
    assert "战略合作/模型.xlsx" in serialized
    assert "ARR was $5M" not in serialized
    assert "<html>secret</html>" not in serialized
    assert "text_excerpt" not in serialized
    assert "file_contents" not in serialized
    assert "raw_content" not in serialized


def test_harness_writes_optional_json_output(tmp_path: Path) -> None:
    """The local harness should optionally persist the same safe summary as JSON."""
    _write_mixed_data_room(tmp_path)
    output_path = tmp_path / "summary.json"

    summary = run_data_room_harness(
        data_room_root=tmp_path,
        mode="FULL",
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        company_name=COMPANY_NAME,
        output_path=output_path,
    )

    assert output_path.exists()
    assert json.loads(output_path.read_text(encoding="utf-8")) == summary


def test_harness_reports_blocked_steps_without_forcing_success(tmp_path: Path) -> None:
    """Unsupported-only folders should report the blocking step and not fake green output."""
    (tmp_path / "Media").mkdir()
    (tmp_path / "Media" / "Demo.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (tmp_path / "Notes").mkdir()
    # Slice78: HTML is now canonical-supported, so use a genuinely-unsupported file (.csv)
    # to keep exercising the "no ingested documents -> blocked" path honestly.
    (tmp_path / "Notes" / "export.csv").write_text("col1,col2\nsecret,1\n", encoding="utf-8")

    summary = run_data_room_harness(
        data_room_root=tmp_path,
        mode="FULL",
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        company_name=COMPANY_NAME,
    )

    assert summary["run_status"] == "FAILED"
    assert summary["block_reason"] == "NO_INGESTED_DOCUMENTS"
    assert _step(summary, "DATA_ROOM_INVENTORY_PACKAGE")["status"] == "COMPLETED"
    assert _step(summary, "INGEST_CHECK")["status"] == "FAILED"
    assert _step(summary, "DOCUMENT_PREFLIGHT")["status"] == "NOT_STARTED"
    assert "NO_INGESTED_DOCUMENTS" in summary["block_reasons"]
    assert summary["deferred_reasons"]
    assert "secret" not in json.dumps(summary, ensure_ascii=False)


def test_real_example_acceptance_when_fixture_is_available() -> None:
    """The real fixture path should be accepted without fixed folder-shape assertions."""
    fixture_root = _real_example_root()
    if fixture_root is None:
        pytest.skip("real_example fixture is not available in this checkout")

    summary = run_data_room_harness(
        data_room_root=fixture_root,
        mode="FULL",
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        company_name=COMPANY_NAME,
        parse_supported_files=False,
    )

    assert summary["inventory"]["file_count"] >= 267
    assert summary["inventory"]["by_extension"][".pdf"] >= 223
    assert summary["inventory"]["by_extension"][".xlsx"] >= 27
    assert summary["inventory"]["by_extension"][".mp4"] >= 8
    assert summary["inventory"]["deferred_file_count"] >= 1
    assert any("/" in file["relative_path"] for file in summary["inventory"]["files"])


def _step(summary: dict[str, object], step_name: str) -> dict[str, object]:
    steps = summary["steps"]
    assert isinstance(steps, list)
    for step in steps:
        assert isinstance(step, dict)
        if step["step_name"] == step_name:
            return step
    raise AssertionError(f"missing step summary for {step_name}")


def _write_mixed_data_room(root: Path) -> None:
    unicode_file = root / "战略合作" / "模型.xlsx"
    unicode_file.parent.mkdir(parents=True)
    unicode_file.write_bytes(create_test_xlsx({"Sheet1": [["ARR was $5M"]]}))
    (root / "Media").mkdir()
    (root / "Media" / "Demo.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (root / "Scans").mkdir()
    (root / "Scans" / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (root / "Notes").mkdir()
    (root / "Notes" / "overview.html").write_text("<html>secret</html>", encoding="utf-8")
    (root / "Broken").mkdir()
    (root / "Broken" / "corrupt.pdf").write_bytes(b"%PDF-corrupt")


def _real_example_root() -> Path | None:
    search_roots = [Path.cwd(), *Path.cwd().parents, Path(__file__).resolve().parents[1]]
    seen: set[Path] = set()
    for root in search_roots:
        candidate = root / "real_example"
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None

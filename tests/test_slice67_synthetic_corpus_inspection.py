"""Slice67 synthetic corpus inspection tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


def test_synthetic_corpus_discovery_reports_gdbs_deal_dirs_and_loader_proof() -> None:
    """Corpus discovery should prove the GDBS-F fixture count from dirs and loader."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import discover_synthetic_corpus

    report = discover_synthetic_corpus(dataset_root=Path("datasets/gdbs_full"))
    serialized = json.dumps(report, sort_keys=True)

    assert report["synthetic_rehearsal_only"] is True
    assert report["dataset_id"] == "gdbs-f"
    assert report["dataset_root"] == "datasets/gdbs_full"
    assert report["deal_directory_count"] == 100
    assert report["loader_case_count"] == 100
    assert report["safe_synthetic_data"] is True
    assert report["formats"] == {".json": 813, ".md": 1, ".pdf": 102, ".xlsx": 100}
    assert report["artifact_file_count"] == 202
    assert report["total_size_bytes"] > 0
    assert "C:\\Projects" not in serialized
    assert "file://datasets/gdbs_full" not in serialized
    assert "real_example/" not in serialized.lower()
    assert "real_example\\" not in serialized.lower()


def test_synthetic_corpus_rejects_non_gdbs_root() -> None:
    """Slice67 must stay scoped to repo-local datasets/gdbs_full only."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        SyntheticRehearsalScopeError,
        discover_synthetic_corpus,
    )

    try:
        discover_synthetic_corpus(dataset_root=Path("tests/fixtures/gdbs_mini"))
    except SyntheticRehearsalScopeError as exc:
        assert "SYNTHETIC_GDBS_ONLY" in str(exc)
    else:
        raise AssertionError("non-GDBS root was accepted")


def test_bounded_synthetic_corpus_inspection_requires_explicit_max_cases() -> None:
    """Corpus inspection must not default to all 100 synthetic deals."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        SyntheticRehearsalScopeError,
        build_bounded_synthetic_corpus_inspection,
    )

    try:
        build_bounded_synthetic_corpus_inspection(
            dataset_root=Path("datasets/gdbs_full"),
            env={},
            max_cases=None,
            allow_synthetic_inspection=True,
        )
    except SyntheticRehearsalScopeError as exc:
        assert "SYNTHETIC_MAX_CASES_REQUIRED" in str(exc)
    else:
        raise AssertionError("bounded inspection accepted missing max_cases")


def test_bounded_synthetic_corpus_inspection_blocks_without_selecting_execution() -> None:
    """Strict blockers should not make inspection look like upload/run execution."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        build_bounded_synthetic_corpus_inspection,
    )

    report = build_bounded_synthetic_corpus_inspection(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=2,
        allow_synthetic_inspection=False,
    )
    serialized = json.dumps(report, sort_keys=True)

    assert report["synthetic_rehearsal_only"] is True
    assert report["real_example_not_run"] is True
    assert report["not_vc_ready"] is True
    assert report["strict_global_may_proceed"] is False
    assert report["bounded_inspection"]["enabled"] is False
    assert report["bounded_inspection"]["requested_case_count"] == 2
    assert report["bounded_inspection"]["inspected_case_count"] == 0
    assert report["bounded_inspection"]["selected_case_ids"] == []
    assert report["strict_runtime_blocked_reason_code"] == "STRICT_FULL_LIVE_BLOCKED"
    assert "blocked_reason_code" not in report
    assert report["strict_blockers"]
    assert "executed_case_count" not in serialized
    assert "runtime_rehearsal" not in serialized
    assert "C:\\Projects" not in serialized
    assert "file://datasets/gdbs_full" not in serialized
    assert "object_key" not in serialized
    assert "raw_text" not in serialized
    assert "prompt_transcript" not in serialized
    assert "embedding" not in serialized
    assert "vector" not in serialized.lower()


def test_bounded_synthetic_corpus_inspection_resolves_artifact_uris_without_report_leakage() -> (
    None
):
    """Explicit synthetic inspection can inspect internal artifact URIs safely."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        build_bounded_synthetic_corpus_inspection,
    )

    report = build_bounded_synthetic_corpus_inspection(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_inspection=True,
    )
    serialized = json.dumps(report, sort_keys=True)

    assert report["synthetic_rehearsal_only"] is True
    assert report["real_example_not_run"] is True
    assert report["not_vc_ready"] is True
    assert report["bounded_inspection"]["enabled"] is True
    assert report["bounded_inspection"]["requested_case_count"] == 1
    assert report["bounded_inspection"]["inspected_case_count"] == 1
    assert report["bounded_inspection"]["selected_case_ids"] == ["deal_001"]
    assert report["bounded_inspection"]["artifact_count"] == 2
    assert report["bounded_inspection"]["artifact_types"] == ["FIN_MODEL", "PITCH_DECK"]
    assert report["bounded_inspection"]["artifact_formats"] == [".pdf", ".xlsx"]
    assert all(len(item) == 64 for item in report["bounded_inspection"]["artifact_sha256"])
    assert report["bounded_inspection"]["artifact_sha256_mismatches"] == []
    assert report["strict_runtime_blocked_reason_code"] == "STRICT_FULL_LIVE_BLOCKED"
    assert "blocked_reason_code" not in report
    assert "executed_case_count" not in serialized
    assert "runtime_rehearsal" not in serialized
    assert "C:\\Projects" not in serialized
    assert "file://datasets/gdbs_full" not in serialized
    assert "datasets/gdbs_full/deals" not in serialized
    assert "pitch_deck.pdf" not in serialized
    assert "financials.xlsx" not in serialized
    assert "object_key" not in serialized
    assert "raw_text" not in serialized
    assert "prompt_transcript" not in serialized
    assert "embedding" not in serialized
    assert "vector" not in serialized.lower()


def test_bounded_synthetic_corpus_inspection_never_clears_strict_global_readiness(
    monkeypatch: Any,
) -> None:
    """Corpus inspection must not become approval evidence even if strict report passes."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    class _PassingStrictReport:
        may_proceed = True
        components: list[Any] = []
        component_inventory: list[Any] = []
        blocking_components: list[str] = []

    monkeypatch.setattr(
        rehearsal,
        "build_strict_full_live_readiness_report",
        lambda **_kwargs: _PassingStrictReport(),
    )

    report = rehearsal.build_bounded_synthetic_corpus_inspection(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_inspection=True,
    )

    assert report["strict_global_may_proceed"] is False
    assert report["strict_runtime_blocked_reason_code"] is None
    assert report["approval_evidence"] is False
    assert report["not_vc_ready"] is True


def test_bounded_synthetic_corpus_inspection_recomputes_artifact_sha_from_file(
    monkeypatch: Any,
) -> None:
    """Artifact SHA must be recomputed from bytes, not echoed from artifacts.json."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    def fake_manifest(path: Path) -> list[dict[str, Any]]:
        artifact_file = path.parent / "artifacts" / "pitch_deck.pdf"
        return [
            {
                "artifact_type": "PITCH_DECK",
                "storage_uri": "file://datasets/gdbs_full/deals/deal_001_clean/artifacts/pitch_deck.pdf",
                "sha256": "0" * 64,
                "file_size_bytes": artifact_file.stat().st_size,
            }
        ]

    monkeypatch.setattr(rehearsal, "_load_artifact_manifest", fake_manifest)

    report = rehearsal.build_bounded_synthetic_corpus_inspection(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_inspection=True,
    )

    assert report["bounded_inspection"]["artifact_count"] == 1
    assert report["bounded_inspection"]["artifact_sha256"] != ["0" * 64]
    assert report["bounded_inspection"]["artifact_sha256_mismatches"] == ["PITCH_DECK:.pdf"]


def test_bounded_synthetic_corpus_inspection_reports_sha_mismatch_safely(
    tmp_path: Path,
) -> None:
    """SHA mismatches should be safe reason-code style fields without paths."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        build_bounded_synthetic_corpus_inspection,
    )

    source_root = Path("datasets/gdbs_full")
    copied_root = tmp_path / "datasets" / "gdbs_full"
    shutil.copytree(source_root, copied_root)
    artifacts_path = copied_root / "deals" / "deal_001_clean" / "artifacts.json"
    artifacts = json.loads(artifacts_path.read_text(encoding="utf-8"))
    artifacts["artifacts"][0]["sha256"] = "f" * 64
    artifacts_path.write_text(json.dumps(artifacts), encoding="utf-8")

    report = build_bounded_synthetic_corpus_inspection(
        dataset_root=copied_root,
        env={},
        max_cases=1,
        allow_synthetic_inspection=True,
        repo_root=tmp_path,
    )
    serialized = json.dumps(report, sort_keys=True)

    assert report["bounded_inspection"]["artifact_sha256_mismatches"] == ["PITCH_DECK:.pdf"]
    assert "file://datasets/gdbs_full" not in serialized
    assert str(tmp_path) not in serialized
    assert "pitch_deck.pdf" not in serialized

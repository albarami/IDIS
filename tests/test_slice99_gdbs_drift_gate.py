"""Slice99 Task 3 - GDBS benchmark drift gate (hermetic validate-mode only, RED-first).

Pins the drift-gate contract:

1. ``python -m idis test gdbs-s --dataset ... --baseline <file>`` FAILS CLOSED (non-zero exit,
   safe error code) when the baseline file is missing or malformed.
2. It passes (exit 0) when the current validate-mode report matches the pinned baseline within
   the baseline's explicit thresholds.
3. Drift beyond threshold in case counts, PASS/FAIL/BLOCKED distribution, dataset identity
   (portable canonical-manifest sha256 - the loader's ``dataset_hash`` bakes in the resolved
   filesystem path, so the gate pins the manifest hash instead), or the expected sanad-grade
   distribution exits non-zero with a DETERMINISTIC, path-free drift report.
4. The committed repo baseline (``tests/fixtures/gdbs_baseline/gdbs_mini_gdbs_s_baseline.json``)
   matches the real ``tests/fixtures/gdbs_mini`` dataset (the pin is correct).
5. The REAL ``.github/workflows/ci.yml`` evaluation-harness job runs the drift-gated command
   (``--baseline ...``), not only the old validate command.

No live providers, no execute mode, no real_example. PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from idis.cli import main as cli_main
from idis.evaluation.baseline import (
    build_baseline_document,
    compute_manifest_sha256,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GDBS_MINI = _REPO_ROOT / "tests" / "fixtures" / "gdbs_mini"
_PINNED_BASELINE = (
    _REPO_ROOT / "tests" / "fixtures" / "gdbs_baseline" / "gdbs_mini_gdbs_s_baseline.json"
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _run_gdbs_s(dataset: Path, baseline: Path, out: Path) -> tuple[int, dict[str, Any]]:
    code = cli_main(
        [
            "test",
            "gdbs-s",
            "--dataset",
            str(dataset),
            "--baseline",
            str(baseline),
            "--out",
            str(out),
        ]
    )
    report = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    return code, report


def _fresh_baseline(tmp_path: Path, **overrides: Any) -> Path:
    """Baseline built from the real gdbs_mini dataset, optionally mutated."""
    document = build_baseline_document(dataset_root=_GDBS_MINI, suite="gdbs-s")
    document.update(overrides)
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _mutated_dataset(tmp_path: Path) -> Path:
    """Writable copy of gdbs_mini for dataset-mutation drift cases."""
    target = tmp_path / "gdbs_mini_mutant"
    shutil.copytree(_GDBS_MINI, target)
    return target


def _drift_metrics(report: dict[str, Any]) -> dict[str, bool]:
    comparison = report["baseline_comparison"]
    return {d["metric"]: d["exceeded"] for d in comparison["drifts"]}


# ---------------------------------------------------------------------------
# 1. fail-closed baseline handling
# ---------------------------------------------------------------------------


def test_missing_baseline_fails_closed(tmp_path: Path) -> None:
    code, report = _run_gdbs_s(
        _GDBS_MINI, tmp_path / "does_not_exist.json", tmp_path / "report.json"
    )

    assert code != 0
    comparison = report["baseline_comparison"]
    assert comparison["ok"] is False
    assert comparison["error_code"] == "BASELINE_MISSING"


def test_malformed_baseline_fails_closed(tmp_path: Path) -> None:
    bad = tmp_path / "baseline.json"
    bad.write_text("{ this is not json", encoding="utf-8")

    code, report = _run_gdbs_s(_GDBS_MINI, bad, tmp_path / "report.json")

    assert code != 0
    comparison = report["baseline_comparison"]
    assert comparison["ok"] is False
    assert comparison["error_code"] == "BASELINE_INVALID"


def test_baseline_for_wrong_suite_fails_closed(tmp_path: Path) -> None:
    baseline = _fresh_baseline(tmp_path, suite_id="gdbs-f")

    code, report = _run_gdbs_s(_GDBS_MINI, baseline, tmp_path / "report.json")

    assert code != 0
    assert report["baseline_comparison"]["error_code"] == "BASELINE_INVALID"


# ---------------------------------------------------------------------------
# 2. within-thresholds pass
# ---------------------------------------------------------------------------


def test_matching_baseline_passes(tmp_path: Path) -> None:
    baseline = _fresh_baseline(tmp_path)

    code, report = _run_gdbs_s(_GDBS_MINI, baseline, tmp_path / "report.json")

    assert code == 0, report.get("baseline_comparison")
    comparison = report["baseline_comparison"]
    assert comparison["ok"] is True
    assert all(d["exceeded"] is False for d in comparison["drifts"])


# ---------------------------------------------------------------------------
# 3. drift dimensions exit non-zero with a deterministic safe report
# ---------------------------------------------------------------------------


def test_case_count_drift_fails(tmp_path: Path) -> None:
    baseline = _fresh_baseline(tmp_path, case_count=19)

    code, report = _run_gdbs_s(_GDBS_MINI, baseline, tmp_path / "report.json")

    assert code != 0
    metrics = _drift_metrics(report)
    assert metrics["case_count"] is True


def test_status_distribution_drift_fails(tmp_path: Path) -> None:
    baseline = _fresh_baseline(tmp_path, status_counts={"PASS": 19, "FAIL": 1})

    code, report = _run_gdbs_s(_GDBS_MINI, baseline, tmp_path / "report.json")

    assert code != 0
    metrics = _drift_metrics(report)
    assert metrics["status_counts.FAIL"] is True or metrics["status_counts.PASS"] is True


def test_dataset_identity_drift_fails(tmp_path: Path) -> None:
    """Mutating the manifest changes the portable manifest hash: the gate must fail."""
    mutant = _mutated_dataset(tmp_path)
    manifest_path = mutant / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["description"] = "mutated for drift-gate test"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    baseline = _fresh_baseline(tmp_path)
    code, report = _run_gdbs_s(mutant, baseline, tmp_path / "report.json")

    assert code != 0
    metrics = _drift_metrics(report)
    assert metrics["manifest_sha256"] is True


def test_expected_grade_distribution_drift_fails(tmp_path: Path) -> None:
    """Changing a declared expected sanad grade in the dataset must trip the gate."""
    mutant = _mutated_dataset(tmp_path)
    expected_path = mutant / "expected_outcomes" / "deal_001_expected.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    assert expected["expected_grade"] == "A"
    expected["expected_grade"] = "C"
    expected_path.write_text(json.dumps(expected, indent=2), encoding="utf-8")

    baseline = _fresh_baseline(tmp_path)
    code, report = _run_gdbs_s(mutant, baseline, tmp_path / "report.json")

    assert code != 0
    metrics = _drift_metrics(report)
    grade_metrics = [
        m for m, exceeded in metrics.items() if m.startswith("expected_grade") and exceeded
    ]
    assert grade_metrics, f"expected a grade-distribution drift, got: {metrics}"


def test_drift_report_is_deterministic_and_path_free(tmp_path: Path) -> None:
    baseline = _fresh_baseline(tmp_path, case_count=19)

    _, first = _run_gdbs_s(_GDBS_MINI, baseline, tmp_path / "r1.json")
    _, second = _run_gdbs_s(_GDBS_MINI, baseline, tmp_path / "r2.json")

    assert first["baseline_comparison"] == second["baseline_comparison"]
    encoded = json.dumps(first["baseline_comparison"])
    assert str(tmp_path) not in encoded, "drift report must not leak filesystem paths"
    assert str(_REPO_ROOT) not in encoded


# ---------------------------------------------------------------------------
# 4. the committed pin matches the real dataset
# ---------------------------------------------------------------------------


def test_pinned_repo_baseline_matches_real_gdbs_mini(tmp_path: Path) -> None:
    assert _PINNED_BASELINE.is_file(), (
        "pinned baseline missing: tests/fixtures/gdbs_baseline/gdbs_mini_gdbs_s_baseline.json"
    )

    code, report = _run_gdbs_s(_GDBS_MINI, _PINNED_BASELINE, tmp_path / "report.json")

    assert code == 0, report.get("baseline_comparison")
    assert report["baseline_comparison"]["ok"] is True


def test_pinned_baseline_manifest_hash_is_portable() -> None:
    """The pinned dataset-identity hash must be reproducible from the manifest alone."""
    pinned = json.loads(_PINNED_BASELINE.read_text(encoding="utf-8"))
    assert pinned["manifest_sha256"] == compute_manifest_sha256(_GDBS_MINI)


# ---------------------------------------------------------------------------
# 5. CI wiring: the evaluation-harness job runs the drift-gated command
# ---------------------------------------------------------------------------


def test_ci_evaluation_harness_runs_drift_gated_command() -> None:
    ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    command_lines = [
        line.strip()
        for line in ci.splitlines()
        if "python -m idis test gdbs-s" in line and not line.strip().startswith("echo")
    ]
    assert command_lines, "evaluation-harness job must run the gdbs-s command"
    drift_gated = [
        line
        for line in command_lines
        if "--baseline tests/fixtures/gdbs_baseline/gdbs_mini_gdbs_s_baseline.json" in line
        and "--dataset tests/fixtures/gdbs_mini" in line
    ]
    assert drift_gated, (
        "the ACTUAL gdbs-s invocation in ci.yml must include the pinned --baseline "
        f"(found commands: {command_lines})"
    )

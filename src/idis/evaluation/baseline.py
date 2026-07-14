"""GDBS drift baseline - pinned metrics + thresholds for the hermetic validate-mode gate.

Slice99 Task 3: the CI evaluation-harness job compares the current GDBS validate report against
a PINNED baseline with EXPLICIT thresholds and fails on drift. All comparisons are hermetic and
deterministic:

- Dataset identity is pinned via ``compute_manifest_sha256`` - the sha256 of the canonical
  (sorted-keys) manifest JSON. The loader's ``dataset_hash`` additionally hashes the resolved
  filesystem path, so it can never match across machines; the manifest hash is portable.
- Case counts and PASS/FAIL/BLOCKED/SKIPPED distribution come from the suite result.
- The expected sanad-grade distribution comes from the dataset's declared
  ``expected_outcomes/*.json`` files (``expected_grade``), so changing a declared grade trips
  the gate without any live execution.

Fail-closed: a missing or malformed baseline is an error (``BASELINE_MISSING`` /
``BASELINE_INVALID``), never a silent pass. Drift reports carry metric names, counts, hashes,
and thresholds only - no filesystem paths.

Regenerating the pin after an INTENTIONAL dataset change:
    python -c "from pathlib import Path; import json; from idis.evaluation.baseline import \
build_baseline_document; print(json.dumps(build_baseline_document(\
dataset_root=Path('tests/fixtures/gdbs_mini'), suite='gdbs-s'), indent=2, sort_keys=True))"
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from idis.evaluation.benchmarks.gdbs import load_gdbs_suite

if TYPE_CHECKING:
    from idis.evaluation.types import SuiteId, SuiteResult

BASELINE_VERSION = 1

_REQUIRED_BASELINE_KEYS = (
    "baseline_version",
    "suite_id",
    "mode",
    "manifest_sha256",
    "case_count",
    "status_counts",
    "expected_grade_distribution",
    "thresholds",
)

_DEFAULT_THRESHOLDS: dict[str, Any] = {
    "case_count_delta": 0,
    "status_count_delta": 0,
    "expected_grade_delta": 0,
    "require_manifest_match": True,
}


def compute_manifest_sha256(dataset_root: Path) -> str:
    """Portable dataset-identity hash: sha256 over the canonical manifest JSON only."""
    manifest_path = Path(dataset_root) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _expected_grade_distribution(dataset_root: Path, suite: SuiteId) -> dict[str, int]:
    """Aggregate declared ``expected_grade`` values across the suite's expected outcomes.

    Fail-closed at the distribution level: a case whose declared expected-outcome file is
    missing or unreadable counts under ``"UNREADABLE"`` and a case with no declared expected
    outcome counts under ``"UNDECLARED"`` - both drift-visible, never silently dropped.
    """
    load = load_gdbs_suite(Path(dataset_root), suite, strict=False)
    distribution: Counter[str] = Counter()
    for case in load.cases:
        if not case.expected_outcome_path:
            distribution["UNDECLARED"] += 1
            continue
        expected_path = Path(dataset_root) / case.expected_outcome_path
        try:
            expected = json.loads(expected_path.read_text(encoding="utf-8"))
            grade = str(expected.get("expected_grade", "UNDECLARED"))
        except (OSError, json.JSONDecodeError):
            grade = "UNREADABLE"
        distribution[grade] += 1
    return dict(sorted(distribution.items()))


def collect_current_metrics(
    dataset_root: Path,
    suite: SuiteId,
    suite_result: SuiteResult,
) -> dict[str, Any]:
    """Current drift-gate metrics for a validate-mode suite result."""
    status_counts: Counter[str] = Counter(case.status.value for case in suite_result.cases)
    return {
        "case_count": len(suite_result.cases),
        "status_counts": dict(sorted(status_counts.items())),
        "manifest_sha256": compute_manifest_sha256(dataset_root),
        "expected_grade_distribution": _expected_grade_distribution(dataset_root, suite),
    }


def build_baseline_document(
    *,
    dataset_root: Path,
    suite: SuiteId,
    suite_result: SuiteResult | None = None,
) -> dict[str, Any]:
    """Build a pinnable baseline document from a validate-mode run of the suite."""
    if suite_result is None:
        from idis.evaluation.harness import run_suite

        suite_result = run_suite(dataset_root=Path(dataset_root), suite=suite, mode="validate")

    metrics = collect_current_metrics(Path(dataset_root), suite, suite_result)
    return {
        "baseline_version": BASELINE_VERSION,
        "suite_id": suite,
        "mode": "validate",
        "thresholds": dict(_DEFAULT_THRESHOLDS),
        **metrics,
    }


def load_baseline(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Load a baseline document. Returns (document, None) or (None, error_code) fail-closed."""
    baseline_path = Path(path)
    if not baseline_path.is_file():
        return None, "BASELINE_MISSING"
    try:
        document = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "BASELINE_INVALID"
    if not isinstance(document, dict):
        return None, "BASELINE_INVALID"
    if any(key not in document for key in _REQUIRED_BASELINE_KEYS):
        return None, "BASELINE_INVALID"
    return document, None


def _count_drifts(
    metric_prefix: str,
    baseline_counts: dict[str, Any],
    current_counts: dict[str, Any],
    threshold: int,
) -> list[dict[str, Any]]:
    drifts: list[dict[str, Any]] = []
    for key in sorted(set(baseline_counts) | set(current_counts)):
        baseline_value = int(baseline_counts.get(key, 0))
        current_value = int(current_counts.get(key, 0))
        delta = abs(current_value - baseline_value)
        drifts.append(
            {
                "metric": f"{metric_prefix}.{key}",
                "baseline": baseline_value,
                "current": current_value,
                "delta": delta,
                "threshold": threshold,
                "exceeded": delta > threshold,
            }
        )
    return drifts


def compare_to_baseline(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    suite: SuiteId,
) -> dict[str, Any]:
    """Deterministic drift comparison. ``ok`` is False when any threshold is exceeded."""
    if baseline.get("suite_id") != suite or baseline.get("mode") != "validate":
        return {
            "ok": False,
            "error_code": "BASELINE_INVALID",
            "detail": (
                f"baseline is for suite '{baseline.get('suite_id')}' mode "
                f"'{baseline.get('mode')}', expected suite '{suite}' mode 'validate'"
            ),
            "drifts": [],
        }

    thresholds = {**_DEFAULT_THRESHOLDS, **dict(baseline.get("thresholds") or {})}
    drifts: list[dict[str, Any]] = []

    case_delta = abs(int(current["case_count"]) - int(baseline["case_count"]))
    drifts.append(
        {
            "metric": "case_count",
            "baseline": int(baseline["case_count"]),
            "current": int(current["case_count"]),
            "delta": case_delta,
            "threshold": int(thresholds["case_count_delta"]),
            "exceeded": case_delta > int(thresholds["case_count_delta"]),
        }
    )

    hash_matches = str(current["manifest_sha256"]) == str(baseline["manifest_sha256"])
    drifts.append(
        {
            "metric": "manifest_sha256",
            "baseline": str(baseline["manifest_sha256"]),
            "current": str(current["manifest_sha256"]),
            "delta": 0 if hash_matches else 1,
            "threshold": "exact-match" if thresholds["require_manifest_match"] else "ignored",
            "exceeded": bool(thresholds["require_manifest_match"]) and not hash_matches,
        }
    )

    drifts.extend(
        _count_drifts(
            "status_counts",
            dict(baseline.get("status_counts") or {}),
            dict(current.get("status_counts") or {}),
            int(thresholds["status_count_delta"]),
        )
    )
    drifts.extend(
        _count_drifts(
            "expected_grade_distribution",
            dict(baseline.get("expected_grade_distribution") or {}),
            dict(current.get("expected_grade_distribution") or {}),
            int(thresholds["expected_grade_delta"]),
        )
    )

    drifts.sort(key=lambda d: str(d["metric"]))
    exceeded = [d["metric"] for d in drifts if d["exceeded"]]
    return {
        "ok": not exceeded,
        "baseline_version": baseline.get("baseline_version"),
        "suite_id": suite,
        "exceeded_metrics": exceeded,
        "drifts": drifts,
    }

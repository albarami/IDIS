"""Evaluation harness orchestrator with deterministic JSON report output.

Modes:
- validate: Only validate dataset structure, produce report
- execute: Attempt to run cases via API (returns BLOCKED if endpoint unavailable)

Uses httpx as the project-standard HTTP client.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal

import httpx

from idis.evaluation.benchmarks.gdbs import load_gdbs_suite
from idis.evaluation.types import (
    VALID_SUITE_IDS,
    CaseResult,
    CaseStatus,
    GateStatus,
    GdbsCase,
    LoadResult,
    SuiteId,
    SuiteResult,
)

KNOWN_BLOCKERS = [
    "Document ingestion pipeline not integrated with claim extraction",
    "Claim extraction service not operational",
    "Sanad chain building not automated",
    "Debate execution not integrated with deliverable generation",
    "No /v1/deals/{dealId}/runs endpoint that executes full pipeline",
]


def _validate_case(case: GdbsCase, dataset_root: Path) -> CaseResult:
    """Validate a single case (dataset presence check)."""
    errors: list[str] = []
    warnings: list[str] = []

    deal_dir = dataset_root / case.directory
    if not deal_dir.exists():
        errors.append(f"Deal directory missing: {case.directory}")

    if case.expected_outcome_path:
        expected_path = dataset_root / case.expected_outcome_path
        if not expected_path.exists():
            warnings.append(f"Expected outcome file missing: {case.expected_outcome_path}")

    status = CaseStatus.PASS if not errors else CaseStatus.FAIL

    return CaseResult(
        case_id=case.case_id,
        deal_id=case.deal_id,
        status=status,
        errors=errors,
        warnings=warnings,
    )


def _check_api_availability(base_url: str, api_key: str | None) -> tuple[bool, str]:
    """Check if the IDIS API is available and has the runs endpoint.

    Returns (available, reason).
    """
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        with httpx.Client(timeout=5.0) as client:
            health_resp = client.get(f"{base_url}/health", headers=headers)
            if health_resp.status_code != 200:
                return False, f"Health check failed: HTTP {health_resp.status_code}"

            runs_test_url = f"{base_url}/v1/deals/00000000-0000-0000-0000-000000000000/runs"
            runs_resp = client.post(
                runs_test_url,
                headers=headers,
                json={},
            )
            if runs_resp.status_code == 404:
                return False, "Runs endpoint not found (404)"
            if runs_resp.status_code == 501:
                return False, "Runs endpoint not implemented (501)"

            return True, "API available"

    except httpx.ConnectError:
        return False, f"Cannot connect to {base_url}"
    except httpx.TimeoutException:
        return False, f"Connection timeout to {base_url}"
    except Exception as e:
        return False, f"API check failed: {e}"


def _execute_case(
    case: GdbsCase,
    base_url: str,
    api_key: str | None,
) -> CaseResult:
    """Execute a single case via API.

    Currently returns BLOCKED because the full pipeline is not integrated.
    """
    start_time = time.monotonic()

    blockers = [
        "runs_endpoint_missing_or_not_full_pipeline",
        "Full E2E pipeline execution not yet integrated",
    ]

    execution_time_ms = int((time.monotonic() - start_time) * 1000)

    return CaseResult(
        case_id=case.case_id,
        deal_id=case.deal_id,
        status=CaseStatus.BLOCKED,
        blockers=blockers,
        execution_time_ms=execution_time_ms,
    )


def run_suite(
    dataset_root: Path,
    suite: SuiteId,
    *,
    mode: Literal["validate", "execute"] = "validate",
    base_url: str | None = None,
    api_key: str | None = None,
    out_path: Path | None = None,
) -> SuiteResult:
    """Run evaluation suite in validate or execute mode.

    Args:
        dataset_root: Path to GDBS dataset
        suite: Suite identifier (gdbs-s, gdbs-f, gdbs-a)
        mode: 'validate' (dataset check only) or 'execute' (run via API)
        base_url: API base URL (required for execute mode)
        api_key: Optional API key
        out_path: Optional path to write JSON report

    Returns:
        SuiteResult with status, cases, and metrics

    Exit code semantics:
        PASS (0): All validations/executions succeeded
        FAIL (1): Validation or execution errors
        BLOCKED (2): Execution blocked due to missing dependencies
    """
    started_at = SuiteResult.now_iso()

    if suite not in VALID_SUITE_IDS:
        finished_at = SuiteResult.now_iso()
        result = SuiteResult(
            suite_id="gdbs-s",
            status=GateStatus.FAIL,
            mode=mode,
            started_at=started_at,
            finished_at=finished_at,
            dataset_hash="",
            errors=[f"Unknown suite: '{suite}'. Valid: {sorted(VALID_SUITE_IDS)}"],
        )
        _write_report(result, out_path)
        return result

    load_result = load_gdbs_suite(dataset_root, suite, strict=True)

    if not load_result.success:
        finished_at = SuiteResult.now_iso()
        result = SuiteResult(
            suite_id=suite,
            status=GateStatus.FAIL,
            mode=mode,
            started_at=started_at,
            finished_at=finished_at,
            dataset_hash=load_result.dataset_hash,
            errors=load_result.errors,
        )
        _write_report(result, out_path)
        return result

    if mode == "validate":
        return _run_validate_mode(load_result, suite, started_at, dataset_root, out_path)
    else:
        return _run_execute_mode(
            load_result, suite, started_at, dataset_root, base_url, api_key, out_path
        )


def _run_validate_mode(
    load_result: LoadResult,
    suite: SuiteId,
    started_at: str,
    dataset_root: Path,
    out_path: Path | None,
) -> SuiteResult:
    """Run validation-only mode."""
    case_results: list[CaseResult] = []
    all_errors: list[str] = []

    for case in load_result.cases:
        case_result = _validate_case(case, dataset_root)
        case_results.append(case_result)
        all_errors.extend(case_result.errors)

    finished_at = SuiteResult.now_iso()

    passed = len([c for c in case_results if c.status == CaseStatus.PASS])
    failed = len([c for c in case_results if c.status == CaseStatus.FAIL])

    status = GateStatus.PASS if not all_errors else GateStatus.FAIL

    result = SuiteResult(
        suite_id=suite,
        status=status,
        mode="validate",
        started_at=started_at,
        finished_at=finished_at,
        dataset_hash=load_result.dataset_hash,
        cases=case_results,
        errors=all_errors,
        metrics={
            "cases_failed": failed,
            "cases_passed": passed,
            "cases_total": len(case_results),
        },
    )

    _write_report(result, out_path)
    return result


def _run_execute_mode(
    load_result: LoadResult,
    suite: SuiteId,
    started_at: str,
    dataset_root: Path,
    base_url: str | None,
    api_key: str | None,
    out_path: Path | None,
) -> SuiteResult:
    """Run execute mode (attempts API calls)."""
    blockers: list[str] = []

    if not base_url:
        blockers.append("base_url not provided for execute mode")
        finished_at = SuiteResult.now_iso()
        result = SuiteResult(
            suite_id=suite,
            status=GateStatus.BLOCKED,
            mode="execute",
            started_at=started_at,
            finished_at=finished_at,
            dataset_hash=load_result.dataset_hash,
            blockers=blockers,
        )
        _write_report(result, out_path)
        return result

    api_available, reason = _check_api_availability(base_url, api_key)
    if not api_available:
        blockers.append(reason)
        blockers.extend(KNOWN_BLOCKERS)

    case_results: list[CaseResult] = []

    for case in load_result.cases:
        if api_available:
            case_result = _execute_case(case, base_url, api_key)
        else:
            case_result = CaseResult(
                case_id=case.case_id,
                deal_id=case.deal_id,
                status=CaseStatus.BLOCKED,
                blockers=[reason],
            )
        case_results.append(case_result)

    finished_at = SuiteResult.now_iso()

    passed = len([c for c in case_results if c.status == CaseStatus.PASS])
    failed = len([c for c in case_results if c.status == CaseStatus.FAIL])
    blocked = len([c for c in case_results if c.status == CaseStatus.BLOCKED])

    if blocked > 0:
        status = GateStatus.BLOCKED
    elif failed > 0:
        status = GateStatus.FAIL
    else:
        status = GateStatus.PASS

    result = SuiteResult(
        suite_id=suite,
        status=status,
        mode="execute",
        started_at=started_at,
        finished_at=finished_at,
        dataset_hash=load_result.dataset_hash,
        cases=case_results,
        blockers=blockers,
        metrics={
            "cases_blocked": blocked,
            "cases_failed": failed,
            "cases_passed": passed,
            "cases_total": len(case_results),
        },
    )

    _write_report(result, out_path)
    return result


def _write_report(result: SuiteResult, out_path: Path | None) -> None:
    """Write JSON report to file if path provided."""
    if out_path is None:
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, indent=2, sort_keys=True)
        f.write("\n")


def get_exit_code(result: SuiteResult) -> int:
    """Get CLI exit code from suite result."""
    return result.status.to_exit_code().value


def format_summary(result: SuiteResult) -> str:
    """Format a human-readable summary of the result."""
    lines = [
        f"Suite: {result.suite_id}",
        f"Mode: {result.mode}",
        f"Status: {result.status.value}",
        f"Dataset Hash: {result.dataset_hash[:16]}...",
    ]

    if result.metrics:
        lines.append("Metrics:")
        for k, v in sorted(result.metrics.items()):
            lines.append(f"  {k}: {v}")

    if result.errors:
        lines.append(f"Errors ({len(result.errors)}):")
        for e in result.errors[:5]:
            lines.append(f"  - {e}")
        if len(result.errors) > 5:
            lines.append(f"  ... and {len(result.errors) - 5} more")

    if result.blockers:
        lines.append(f"Blockers ({len(result.blockers)}):")
        for b in result.blockers[:5]:
            lines.append(f"  - {b}")
        if len(result.blockers) > 5:
            lines.append(f"  ... and {len(result.blockers) - 5} more")

    return "\n".join(lines)

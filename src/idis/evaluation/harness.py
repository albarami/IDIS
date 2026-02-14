"""Evaluation harness orchestrator with deterministic JSON report output.

Modes:
- validate: Only validate dataset structure, produce report
- execute: Attempt to run cases via API (returns BLOCKED if endpoint unavailable)

Uses httpx as the project-standard HTTP client.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

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

_DOCUMENT_EXTENSIONS = frozenset({"pdf", "xlsx", "docx", "pptx", "txt"})
_HTTP_TIMEOUT = 120.0
_GATE3_TENANT_ID = "00000000-0000-0000-0000-000000000001"

logger = logging.getLogger(__name__)


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

            api_available = runs_resp.status_code < 500
            return api_available, "API available" if api_available else "API unavailable"

    except httpx.ConnectError:
        return False, f"Cannot connect to {base_url}"
    except httpx.TimeoutException:
        return False, f"Connection timeout to {base_url}"
    except Exception as e:
        return False, f"API check failed: {e}"


def _build_headers(api_key: str | None) -> dict[str, str]:
    """Build request headers with optional API key authentication."""
    headers: dict[str, str] = {}
    if api_key:
        headers["X-IDIS-API-Key"] = api_key
    return headers


def _load_deal_artifacts(deal_dir: Path) -> list[dict[str, Any]]:
    """Load artifact manifest from a deal directory.

    Reads artifacts.json and returns the list of artifact descriptors.
    Falls back to scanning for document files if no manifest exists.

    Args:
        deal_dir: Path to the deal directory.

    Returns:
        List of artifact dicts with at least 'filename' and optionally
        'sha256', 'artifact_type', 'storage_uri'.
    """
    manifest_path = deal_dir / "artifacts.json"
    if manifest_path.exists():
        with open(manifest_path, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
        result: list[dict[str, Any]] = data.get("artifacts", [])
        return result

    doc_files = [
        fp
        for fp in deal_dir.iterdir()
        if fp.is_file() and fp.suffix.lstrip(".").lower() in _DOCUMENT_EXTENSIONS
    ]
    return [{"filename": fp.name, "artifact_type": "DATA_ROOM_FILE"} for fp in sorted(doc_files)]


def _seed_artifact_bytes(
    *,
    fs_store: Any,
    tenant_id: str,
    storage_key: str,
    data: bytes,
) -> None:
    """Write artifact bytes into the shared FilesystemObjectStore.

    This mirrors the E2E test pattern: the harness writes bytes directly
    to the same filesystem store the server reads from.

    Args:
        fs_store: A FilesystemObjectStore instance.
        tenant_id: Tenant UUID string.
        storage_key: Deterministic storage key for the artifact.
        data: Raw file bytes.
    """
    fs_store.put(tenant_id=tenant_id, key=storage_key, data=data)


def _map_artifact_type_to_doc_type(artifact_type: str) -> str:
    """Map dataset artifact_type to API DocType enum value.

    Args:
        artifact_type: Artifact type from artifacts.json.

    Returns:
        DocType string for the API.
    """
    mapping = {
        "PITCH_DECK": "PITCH_DECK",
        "FIN_MODEL": "FINANCIAL_MODEL",
        "FINANCIAL_MODEL": "FINANCIAL_MODEL",
        "DATA_ROOM_FILE": "DATA_ROOM_FILE",
        "TRANSCRIPT": "TRANSCRIPT",
        "TERM_SHEET": "TERM_SHEET",
    }
    return mapping.get(artifact_type, "DATA_ROOM_FILE")


def _seed_and_ingest_documents(
    *,
    client: httpx.Client,
    auth_headers: dict[str, str],
    base_url: str,
    deal_id: str,
    deal_dir: Path,
    shared_store_dir: Path | None,
    case_id: str,
) -> tuple[int, list[str]]:
    """Seed document bytes into the shared store and create documents via API.

    For each artifact in the deal's artifacts.json:
    1. Read raw bytes from the artifacts/ subdirectory
    2. Write bytes into the shared FilesystemObjectStore
    3. Create document via POST /v1/deals/{dealId}/documents with
       uri=file://{storage_key} and auto_ingest=True

    Args:
        client: HTTP client.
        auth_headers: Auth headers for API calls.
        base_url: API base URL.
        deal_id: Server-assigned deal UUID.
        deal_dir: Path to the deal directory in the dataset.
        shared_store_dir: Path to the shared filesystem store (None = skip seeding).
        case_id: Case identifier for deterministic storage keys.

    Returns:
        (ingested_count, errors) tuple.
    """
    artifacts = _load_deal_artifacts(deal_dir)
    if not artifacts:
        return 0, ["No artifacts found in deal directory"]

    fs_store = None
    if shared_store_dir is not None:
        from idis.storage.filesystem_store import FilesystemObjectStore

        fs_store = FilesystemObjectStore(base_dir=shared_store_dir)

    ingested = 0
    errors: list[str] = []

    for artifact in artifacts:
        filename = artifact.get("filename", "")
        if not filename:
            errors.append("Artifact missing filename")
            continue

        artifact_type = artifact.get("artifact_type", "DATA_ROOM_FILE")
        expected_sha256 = artifact.get("sha256")

        artifacts_subdir = deal_dir / "artifacts"
        file_path = artifacts_subdir / filename
        if not file_path.exists():
            file_path = deal_dir / filename

        storage_key = f"gdbs/{case_id}/{filename}"
        uri = f"file://{storage_key}"
        doc_type = _map_artifact_type_to_doc_type(artifact_type)

        if file_path.exists() and fs_store is not None:
            raw_bytes = file_path.read_bytes()
            actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()

            _seed_artifact_bytes(
                fs_store=fs_store,
                tenant_id=_GATE3_TENANT_ID,
                storage_key=storage_key,
                data=raw_bytes,
            )
            logger.debug(
                "Seeded %s (%d bytes, sha256=%s) -> %s",
                filename,
                len(raw_bytes),
                actual_sha256[:16],
                storage_key,
            )
        elif not file_path.exists():
            errors.append(f"Artifact file not found: {filename}")
            continue

        create_body: dict[str, Any] = {
            "doc_type": doc_type,
            "title": filename,
            "uri": uri,
            "auto_ingest": True,
        }
        if expected_sha256:
            create_body["sha256"] = expected_sha256

        create_resp = client.post(
            f"{base_url}/v1/deals/{deal_id}/documents",
            json=create_body,
            headers={**auth_headers, "Content-Type": "application/json"},
        )

        if create_resp.status_code == 201:
            ingested += 1
        else:
            errors.append(
                f"Document creation failed for {filename}: "
                f"HTTP {create_resp.status_code} {create_resp.text[:200]}"
            )

    return ingested, errors


def _execute_case(
    case: GdbsCase,
    base_url: str,
    api_key: str | None,
    dataset_root: Path,
    shared_store_dir: Path | None = None,
    http_timeout: float | None = None,
) -> CaseResult:
    """Execute a single case via the IDIS API.

    Steps:
        1. Create deal via POST /v1/deals
        2. Seed document bytes into shared store and create documents with auto_ingest
        3. Start FULL run via POST /v1/deals/{deal_id}/runs
        4. Interpret run result status

    Args:
        case: GDBS benchmark case to execute.
        base_url: API base URL.
        api_key: Optional API key for authentication.
        dataset_root: Path to GDBS dataset root.
        shared_store_dir: Path to the shared filesystem store for seeding bytes.

    Returns:
        CaseResult with status, errors, metrics, and timing.
    """
    start_time = time.monotonic()
    metrics: dict[str, object] = {}
    auth_headers = _build_headers(api_key)

    try:
        effective_timeout = http_timeout if http_timeout is not None else _HTTP_TIMEOUT
        with httpx.Client(timeout=effective_timeout) as client:
            create_resp = client.post(
                f"{base_url}/v1/deals",
                json={"name": case.deal_key, "company_name": case.deal_key},
                headers=auth_headers,
            )
            if create_resp.status_code != 201:
                return CaseResult(
                    case_id=case.case_id,
                    deal_id=case.deal_id,
                    status=CaseStatus.FAIL,
                    errors=[
                        f"Deal creation failed: HTTP {create_resp.status_code} "
                        f"{create_resp.text[:500]}"
                    ],
                    execution_time_ms=int((time.monotonic() - start_time) * 1000),
                )

            deal_id = create_resp.json()["deal_id"]
            metrics["deal_id"] = deal_id

            deal_dir = dataset_root / case.directory
            if deal_dir.exists():
                ingested_count, seed_errors = _seed_and_ingest_documents(
                    client=client,
                    auth_headers=auth_headers,
                    base_url=base_url,
                    deal_id=deal_id,
                    deal_dir=deal_dir,
                    shared_store_dir=shared_store_dir,
                    case_id=case.case_id,
                )
                metrics["documents_ingested"] = ingested_count
                if seed_errors:
                    metrics["seed_errors"] = seed_errors

                if ingested_count == 0:
                    return CaseResult(
                        case_id=case.case_id,
                        deal_id=case.deal_id,
                        status=CaseStatus.FAIL,
                        errors=seed_errors or ["No documents ingested"],
                        metrics=metrics,
                        execution_time_ms=int((time.monotonic() - start_time) * 1000),
                    )

            run_headers = {**auth_headers, "Idempotency-Key": case.case_id}
            run_resp = client.post(
                f"{base_url}/v1/deals/{deal_id}/runs",
                json={"mode": "FULL"},
                headers=run_headers,
            )

            execution_time_ms = int((time.monotonic() - start_time) * 1000)

            if run_resp.status_code >= 500:
                return CaseResult(
                    case_id=case.case_id,
                    deal_id=case.deal_id,
                    status=CaseStatus.FAIL,
                    errors=[
                        f"Run request server error: HTTP {run_resp.status_code} "
                        f"{run_resp.text[:500]}"
                    ],
                    metrics=metrics,
                    execution_time_ms=execution_time_ms,
                )

            if run_resp.status_code >= 400:
                body = (
                    run_resp.json()
                    if run_resp.headers.get("content-type", "").startswith("application/json")
                    else {}
                )
                return CaseResult(
                    case_id=case.case_id,
                    deal_id=case.deal_id,
                    status=CaseStatus.FAIL,
                    errors=[
                        f"Run request failed: HTTP {run_resp.status_code} "
                        f"code={body.get('code', 'UNKNOWN')} "
                        f"message={body.get('message', run_resp.text[:500])}"
                    ],
                    metrics=metrics,
                    execution_time_ms=execution_time_ms,
                )

            run_body = run_resp.json()
            run_id = run_body.get("run_id", "")
            run_status = run_body.get("status", "UNKNOWN")
            steps = run_body.get("steps", [])

            metrics["run_id"] = run_id
            metrics["run_status"] = run_status
            metrics["steps_completed"] = len([s for s in steps if s.get("status") == "COMPLETED"])
            metrics["steps_failed"] = len([s for s in steps if s.get("status") == "FAILED"])

            failed_steps = [s for s in steps if s.get("status") == "FAILED"]
            if failed_steps:
                metrics["failing_step"] = failed_steps[0].get("step_name", "UNKNOWN")

            if run_status == "SUCCEEDED":
                return CaseResult(
                    case_id=case.case_id,
                    deal_id=case.deal_id,
                    status=CaseStatus.PASS,
                    metrics=metrics,
                    execution_time_ms=execution_time_ms,
                )

            if run_status == "FAILED":
                block_reason = run_body.get("block_reason")
                if block_reason:
                    return CaseResult(
                        case_id=case.case_id,
                        deal_id=case.deal_id,
                        status=CaseStatus.BLOCKED,
                        blockers=[block_reason],
                        metrics=metrics,
                        execution_time_ms=execution_time_ms,
                    )
                step_errors = [
                    f"{s.get('step_name', '?')}: {s.get('error', 'failed')}" for s in failed_steps
                ]
                return CaseResult(
                    case_id=case.case_id,
                    deal_id=case.deal_id,
                    status=CaseStatus.FAIL,
                    errors=step_errors or [f"Run status: {run_status}"],
                    metrics=metrics,
                    execution_time_ms=execution_time_ms,
                )

            return CaseResult(
                case_id=case.case_id,
                deal_id=case.deal_id,
                status=CaseStatus.FAIL,
                errors=[f"unknown_run_status:{run_status}"],
                metrics=metrics,
                execution_time_ms=execution_time_ms,
            )

    except Exception as exc:
        execution_time_ms = int((time.monotonic() - start_time) * 1000)
        return CaseResult(
            case_id=case.case_id,
            deal_id=case.deal_id,
            status=CaseStatus.FAIL,
            errors=[f"{type(exc).__name__}: {exc}"],
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
    shared_store_dir: Path | None = None,
    case_limit: int | None = None,
    http_timeout: float | None = None,
    pre_case_fn: Callable[[], None] | None = None,
) -> SuiteResult:
    """Run evaluation suite in validate or execute mode.

    Args:
        dataset_root: Path to GDBS dataset
        suite: Suite identifier (gdbs-s, gdbs-f, gdbs-a)
        mode: 'validate' (dataset check only) or 'execute' (run via API)
        base_url: API base URL (required for execute mode)
        api_key: Optional API key
        out_path: Optional path to write JSON report
        shared_store_dir: Path to shared filesystem store for seeding document bytes.
            When provided, _execute_case seeds artifact bytes into this directory
            so the server's ComplianceEnforcedStore can read them.
        case_limit: Optional max number of cases to execute (for diagnostic runs).
        http_timeout: Optional per-request HTTP timeout override in seconds.
        pre_case_fn: Optional callback invoked before each case (e.g. server restart).

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

    if case_limit is not None and case_limit > 0:
        load_result.cases = load_result.cases[:case_limit]

    if mode == "validate":
        return _run_validate_mode(load_result, suite, started_at, dataset_root, out_path)
    else:
        return _run_execute_mode(
            load_result,
            suite,
            started_at,
            dataset_root,
            base_url,
            api_key,
            out_path,
            shared_store_dir=shared_store_dir,
            http_timeout=http_timeout,
            pre_case_fn=pre_case_fn,
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
    shared_store_dir: Path | None = None,
    http_timeout: float | None = None,
    pre_case_fn: Callable[[], None] | None = None,
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

    case_results: list[CaseResult] = []

    for idx, case in enumerate(load_result.cases):
        if pre_case_fn is not None:
            logger.info(
                "pre_case_fn: resetting server before case %d/%d (%s)",
                idx + 1,
                len(load_result.cases),
                case.case_id,
            )
            pre_case_fn()
        if api_available:
            case_result = _execute_case(
                case,
                base_url,
                api_key,
                dataset_root,
                shared_store_dir=shared_store_dir,
                http_timeout=http_timeout,
            )
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

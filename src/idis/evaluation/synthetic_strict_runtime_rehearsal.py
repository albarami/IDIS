"""Synthetic GDBS status, corpus inspection, and package-surface helpers."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.deliverables import clear_deliverables_store
from idis.api.routes.documents import clear_document_store
from idis.api.routes.runs import clear_runs_store
from idis.audit.sink import InMemoryAuditSink
from idis.deliverables.artifact_catalog import resolve_content_type
from idis.evaluation.benchmarks.gdbs import load_gdbs_suite
from idis.idempotency.store import SqliteIdempotencyStore
from idis.models.run_step import FULL_STEPS
from idis.persistence.db import get_app_engine
from idis.persistence.repositories.run_steps import clear_run_steps_store
from idis.services.runs.strict_full_live import build_strict_full_live_readiness_report

MAX_SYNTHETIC_REHEARSAL_CASES = 20
SYNTHETIC_API_REHEARSAL_API_KEY = "slice68-synthetic-api-upload-key"
SYNTHETIC_API_REHEARSAL_TENANT_ID = "11111111-1111-4111-8111-111111111111"
SYNTHETIC_FULL_EXECUTION_LIVE_PROVIDER_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "IDIS_EXTRACT_BACKEND",
    "IDIS_DEBATE_BACKEND",
    "OPENAI_API_KEY",
    "FINNHUB_API_KEY",
    "FMP_API_KEY",
)


class SyntheticRehearsalScopeError(ValueError):
    """Raised when a rehearsal attempts to leave the approved synthetic scope."""


def discover_synthetic_corpus(
    *, dataset_root: Path, repo_root: Path | None = None
) -> dict[str, Any]:
    """Discover the approved GDBS-F synthetic corpus without exposing paths."""
    resolved_dataset_root = _require_gdbs_dataset_root(dataset_root, repo_root=repo_root)
    deal_dirs = _gdbs_deal_dirs(resolved_dataset_root)
    load_result = load_gdbs_suite(resolved_dataset_root, "gdbs-f", strict=True)
    files = [path for path in resolved_dataset_root.rglob("*") if path.is_file()]
    artifact_files = [path for path in files if path.suffix.lower() in {".pdf", ".xlsx"}]

    report = {
        "synthetic_rehearsal_only": True,
        "real_example_not_run": True,
        "not_vc_ready": True,
        "runtime_proof_required": True,
        "dataset_id": "gdbs-f",
        "dataset_root": "datasets/gdbs_full",
        "safe_synthetic_data": True,
        "deal_directory_count": len(deal_dirs),
        "loader_case_count": len(load_result.cases),
        "loader_success": load_result.success,
        "dataset_hash": load_result.dataset_hash,
        "total_file_count": len(files),
        "total_size_bytes": sum(path.stat().st_size for path in files),
        "formats": _format_counts(files),
        "artifact_file_count": len(artifact_files),
    }
    _assert_no_report_leakage(report)
    return report


def build_bounded_synthetic_corpus_inspection(
    *,
    dataset_root: Path,
    env: dict[str, str],
    max_cases: int | None,
    allow_synthetic_inspection: bool,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build a bounded, non-approval synthetic corpus inspection report."""
    if max_cases is None or max_cases <= 0:
        raise SyntheticRehearsalScopeError("SYNTHETIC_MAX_CASES_REQUIRED")
    if max_cases > MAX_SYNTHETIC_REHEARSAL_CASES:
        raise SyntheticRehearsalScopeError("SYNTHETIC_MAX_CASES_TOO_LARGE")

    resolved_dataset_root = _require_gdbs_dataset_root(dataset_root, repo_root=repo_root)
    corpus = discover_synthetic_corpus(dataset_root=resolved_dataset_root, repo_root=repo_root)
    load_result = load_gdbs_suite(resolved_dataset_root, "gdbs-f", strict=True)
    strict_report = build_strict_full_live_readiness_report(env=env)
    selected_cases = load_result.cases[:max_cases]

    bounded_inspection: dict[str, Any] = {
        "enabled": allow_synthetic_inspection,
        "requested_case_count": max_cases,
        "selected_case_ids": [],
        "inspected_case_count": 0,
        "artifact_count": 0,
        "artifact_types": [],
        "artifact_formats": [],
        "artifact_sha256": [],
        "artifact_sha256_mismatches": [],
    }
    if allow_synthetic_inspection:
        bounded_inspection["selected_case_ids"] = [case.case_id for case in selected_cases]
        bounded_inspection.update(
            _summarize_selected_artifacts(resolved_dataset_root, selected_cases)
        )
        bounded_inspection["inspected_case_count"] = len(selected_cases)

    report = {
        "synthetic_rehearsal_only": True,
        "real_example_not_run": True,
        "not_vc_ready": True,
        "runtime_proof_required": True,
        "real_example_gate_cleared": False,
        "strict_global_may_proceed": False,
        "dataset": corpus,
        "bounded_inspection": bounded_inspection,
        "strict_blockers": _safe_strict_blockers(strict_report),
        "strict_runtime_blocked_reason_code": (
            "STRICT_FULL_LIVE_BLOCKED" if not strict_report.may_proceed else None
        ),
        "approval_evidence": False,
    }
    _assert_no_report_leakage(report)
    return report


def build_bounded_synthetic_api_upload_rehearsal(
    *,
    dataset_root: Path,
    env: dict[str, str],
    max_cases: int | None,
    allow_synthetic_api_upload: bool,
    object_store_base_dir: Path | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Upload a bounded GDBS-F case through the public API upload boundary only."""
    if max_cases is None or max_cases <= 0:
        raise SyntheticRehearsalScopeError("SYNTHETIC_MAX_CASES_REQUIRED")
    if max_cases > MAX_SYNTHETIC_REHEARSAL_CASES:
        raise SyntheticRehearsalScopeError("SYNTHETIC_MAX_CASES_TOO_LARGE")

    resolved_dataset_root = _require_gdbs_dataset_root(dataset_root, repo_root=repo_root)
    corpus = _api_rehearsal_corpus_summary(
        discover_synthetic_corpus(dataset_root=resolved_dataset_root, repo_root=repo_root)
    )
    strict_report = build_strict_full_live_readiness_report(env=env)
    load_result = load_gdbs_suite(resolved_dataset_root, "gdbs-f", strict=True)
    selected_cases = load_result.cases[:max_cases]

    upload_report: dict[str, Any] = {
        "enabled": allow_synthetic_api_upload,
        "requested_case_count": max_cases,
        "selected_case_ids": [case.case_id for case in selected_cases],
        "uploaded_case_count": 0,
        "uploaded_document_count": 0,
        "artifact_types": [],
        "artifact_formats": [],
        "uploaded_documents": [],
    }
    report = {
        "synthetic_rehearsal_only": True,
        "real_example_not_run": True,
        "not_vc_ready": True,
        "runtime_proof_required": True,
        "real_example_gate_cleared": False,
        "strict_global_may_proceed": False,
        "approval_evidence": False,
        "dataset": corpus,
        "api_upload_rehearsal": upload_report,
        "strict_blockers": _safe_strict_blockers(strict_report),
        "strict_runtime_blocked_reason_code": (
            "STRICT_FULL_LIVE_BLOCKED" if not strict_report.may_proceed else None
        ),
        "run_attempt": {
            "enabled": False,
            "status": "not_run",
            "reason_code": "SYNTHETIC_RUN_NOT_INCLUDED_IN_UPLOAD_REHEARSAL",
        },
        "package_surface_verification": {
            "status": "not_run",
            "verified": False,
        },
    }
    if not allow_synthetic_api_upload:
        raise SyntheticRehearsalScopeError("SYNTHETIC_API_UPLOAD_NOT_ALLOWED")

    upload_result = _upload_selected_gdbs_cases_via_api(
        dataset_root=resolved_dataset_root,
        selected_cases=selected_cases,
        object_store_base_dir=object_store_base_dir,
    )
    upload_report.update(upload_result)
    _assert_no_report_leakage(report)
    return report


def build_bounded_synthetic_api_run_rehearsal(
    *,
    dataset_root: Path,
    env: dict[str, str],
    max_cases: int | None,
    allow_synthetic_api_upload: bool,
    allow_synthetic_run: bool,
    object_store_base_dir: Path | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Attempt a bounded GDBS-F FULL run only to prove strict blocking."""
    if not allow_synthetic_run:
        raise SyntheticRehearsalScopeError("SYNTHETIC_RUN_NOT_ALLOWED")

    resolved_dataset_root = _require_gdbs_dataset_root(dataset_root, repo_root=repo_root)
    corpus = _api_rehearsal_corpus_summary(
        discover_synthetic_corpus(dataset_root=resolved_dataset_root, repo_root=repo_root)
    )
    strict_report = build_strict_full_live_readiness_report(env=env)
    selected_cases = load_gdbs_suite(resolved_dataset_root, "gdbs-f", strict=True).cases[
        : max_cases or 0
    ]
    if max_cases is None or max_cases <= 0:
        raise SyntheticRehearsalScopeError("SYNTHETIC_MAX_CASES_REQUIRED")
    if max_cases > MAX_SYNTHETIC_REHEARSAL_CASES:
        raise SyntheticRehearsalScopeError("SYNTHETIC_MAX_CASES_TOO_LARGE")
    if not allow_synthetic_api_upload:
        raise SyntheticRehearsalScopeError("SYNTHETIC_API_UPLOAD_NOT_ALLOWED")

    upload_result, run_attempt = _upload_and_attempt_strict_blocked_run_via_api(
        dataset_root=resolved_dataset_root,
        selected_cases=selected_cases,
        object_store_base_dir=object_store_base_dir,
    )
    report = {
        "synthetic_rehearsal_only": True,
        "real_example_not_run": True,
        "not_vc_ready": True,
        "runtime_proof_required": True,
        "real_example_gate_cleared": False,
        "strict_global_may_proceed": False,
        "approval_evidence": False,
        "dataset": corpus,
        "api_upload_rehearsal": {
            "enabled": True,
            "requested_case_count": max_cases,
            "selected_case_ids": [case.case_id for case in selected_cases],
            **upload_result,
        },
        "strict_blockers": _safe_strict_blockers(strict_report),
        "run_attempt": run_attempt,
        "package_surface_verification": {
            "status": "not_run",
            "verified": False,
        },
    }
    if (
        run_attempt.get("status") == "blocked"
        and run_attempt.get("reason_code") == "STRICT_FULL_LIVE_BLOCKED"
    ):
        report["strict_runtime_blocked_reason_code"] = "STRICT_FULL_LIVE_BLOCKED"
    _assert_no_report_leakage(report)
    return report


def build_bounded_synthetic_full_execution_rehearsal(
    *,
    dataset_root: Path,
    env: dict[str, str],
    max_cases: int | None,
    allow_synthetic_api_upload: bool,
    allow_synthetic_execution: bool,
    object_store_base_dir: Path | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Execute one bounded GDBS-F FULL run as a non-approval synthetic rehearsal."""
    if not allow_synthetic_execution:
        raise SyntheticRehearsalScopeError("SYNTHETIC_EXECUTION_NOT_ALLOWED")

    resolved_dataset_root = _require_gdbs_dataset_root(dataset_root, repo_root=repo_root)
    corpus = _api_rehearsal_corpus_summary(
        discover_synthetic_corpus(dataset_root=resolved_dataset_root, repo_root=repo_root)
    )
    strict_report = build_strict_full_live_readiness_report(env=env)
    selected_cases = load_gdbs_suite(resolved_dataset_root, "gdbs-f", strict=True).cases[
        : max_cases or 0
    ]
    if max_cases is None or max_cases <= 0:
        raise SyntheticRehearsalScopeError("SYNTHETIC_MAX_CASES_REQUIRED")
    if max_cases > MAX_SYNTHETIC_REHEARSAL_CASES:
        raise SyntheticRehearsalScopeError("SYNTHETIC_MAX_CASES_TOO_LARGE")
    if max_cases != 1:
        raise SyntheticRehearsalScopeError("SYNTHETIC_EXECUTION_SINGLE_CASE_ONLY")
    if not allow_synthetic_api_upload:
        raise SyntheticRehearsalScopeError("SYNTHETIC_API_UPLOAD_NOT_ALLOWED")

    upload_result, execution_result, package_surface = _upload_and_execute_non_strict_full_via_api(
        dataset_root=resolved_dataset_root,
        selected_cases=selected_cases,
        object_store_base_dir=object_store_base_dir,
    )
    report = {
        "synthetic_rehearsal_only": True,
        "real_example_not_run": True,
        "not_vc_ready": True,
        "runtime_proof_required": True,
        "real_example_gate_cleared": False,
        "strict_global_may_proceed": False,
        "approval_evidence": False,
        "dataset": corpus,
        "api_upload_rehearsal": {
            "enabled": True,
            "requested_case_count": max_cases,
            "selected_case_ids": [case.case_id for case in selected_cases],
            **upload_result,
        },
        "strict_blockers": _safe_strict_blockers(strict_report),
        "full_execution_rehearsal": execution_result,
        "package_surface_status": package_surface["status"],
        "package_surface_verification": package_surface,
    }
    _assert_no_report_leakage(report)
    return report


def build_bounded_synthetic_package_surface_rehearsal(
    *,
    dataset_root: Path,
    env: dict[str, str],
    max_cases: int | None,
    allow_synthetic_api_upload: bool,
    allow_synthetic_execution: bool,
    allow_synthetic_package_surface_verification: bool,
    object_store_base_dir: Path | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Prove one synthetic FULL run creates same-run durable package surfaces."""
    if not allow_synthetic_package_surface_verification:
        raise SyntheticRehearsalScopeError("SYNTHETIC_PACKAGE_SURFACE_VERIFICATION_NOT_ALLOWED")
    if not allow_synthetic_execution:
        raise SyntheticRehearsalScopeError("SYNTHETIC_EXECUTION_NOT_ALLOWED")

    resolved_dataset_root = _require_gdbs_dataset_root(dataset_root, repo_root=repo_root)
    corpus = _api_rehearsal_corpus_summary(
        discover_synthetic_corpus(dataset_root=resolved_dataset_root, repo_root=repo_root)
    )
    strict_report = build_strict_full_live_readiness_report(env=env)
    selected_cases = load_gdbs_suite(resolved_dataset_root, "gdbs-f", strict=True).cases[
        : max_cases or 0
    ]
    if max_cases is None or max_cases <= 0:
        raise SyntheticRehearsalScopeError("SYNTHETIC_MAX_CASES_REQUIRED")
    if max_cases > MAX_SYNTHETIC_REHEARSAL_CASES:
        raise SyntheticRehearsalScopeError("SYNTHETIC_MAX_CASES_TOO_LARGE")
    if max_cases != 1:
        raise SyntheticRehearsalScopeError("SYNTHETIC_EXECUTION_SINGLE_CASE_ONLY")
    if not allow_synthetic_api_upload:
        raise SyntheticRehearsalScopeError("SYNTHETIC_API_UPLOAD_NOT_ALLOWED")
    if not os.environ.get("IDIS_DATABASE_URL"):
        raise SyntheticRehearsalScopeError("SYNTHETIC_POSTGRES_REQUIRED")
    _require_available_postgres_for_synthetic_package_surface()

    upload_result, execution_result, package_surface = (
        _upload_execute_and_verify_package_surfaces_via_api(
            dataset_root=resolved_dataset_root,
            selected_cases=selected_cases,
            object_store_base_dir=object_store_base_dir,
        )
    )
    report = {
        "synthetic_rehearsal_only": True,
        "real_example_not_run": True,
        "not_vc_ready": True,
        "runtime_proof_required": True,
        "real_example_gate_cleared": False,
        "strict_global_may_proceed": False,
        "approval_evidence": False,
        "dataset": corpus,
        "api_upload_rehearsal": {
            "enabled": True,
            "requested_case_count": max_cases,
            "selected_case_ids": [case.case_id for case in selected_cases],
            **upload_result,
        },
        "strict_blockers": _safe_strict_blockers(strict_report),
        "full_execution_rehearsal": execution_result,
        "package_surface_status": package_surface["status"],
        "package_surface_verification": package_surface,
    }
    _assert_no_report_leakage(report)
    return report


def build_synthetic_rehearsal_status(
    *,
    dataset_root: Path,
    env: dict[str, str],
    max_cases: int | None = None,
) -> dict[str, Any]:
    """Build a safe status/dry-run report for the GDBS synthetic rehearsal."""
    resolved_dataset_root = _require_gdbs_dataset_root(dataset_root)
    load_result = load_gdbs_suite(resolved_dataset_root, "gdbs-f", strict=True)
    strict_report = build_strict_full_live_readiness_report(env=env)
    selected_case_count = min(max_cases or 0, len(load_result.cases)) if max_cases else 0

    return {
        "synthetic_rehearsal_only": True,
        "real_example_not_run": True,
        "not_vc_ready": True,
        "runtime_proof_required": True,
        "real_example_gate_cleared": False,
        "strict_global_may_proceed": False,
        "dataset_id": "gdbs-f",
        "dataset_root": "datasets/gdbs_full",
        "dataset_loaded": load_result.success,
        "dataset_case_count": len(load_result.cases),
        "dataset_hash": load_result.dataset_hash,
        "bounded_execution": {
            "enabled": False,
            "max_cases": selected_case_count,
            "executed_case_count": 0,
        },
        "strict_blockers": _safe_strict_blockers(strict_report),
        "package_surface_verification": {
            "required": True,
            "status": "not_run",
        },
    }


def verify_package_surfaces(
    *,
    client: TestClient,
    api_key: str,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    export_summary: dict[str, Any],
) -> dict[str, Any]:
    """Verify synthetic final package list, manifest review, downloads, and hashes."""
    headers = {"X-IDIS-API-Key": api_key}
    listed_response = client.get(f"/v1/deals/{deal_id}/deliverables", headers=headers)
    listed_response.raise_for_status()
    listed = listed_response.json()["items"]

    manifest_response = client.get(
        f"/v1/deals/{deal_id}/runs/{run_id}/product-bundle/manifest",
        headers=headers,
    )
    manifest_response.raise_for_status()
    manifest = manifest_response.json()
    artifacts = [artifact for artifact in manifest["artifacts"] if isinstance(artifact, dict)]
    rows_by_type_format = {
        (str(row["deliverable_type"]), str(row.get("format") or "")): row for row in listed
    }

    downloaded: list[dict[str, Any]] = []
    sha_mismatches: list[str] = []
    content_type_mismatches: list[str] = []
    missing_listed_artifacts: list[str] = []
    download_failures: list[dict[str, Any]] = []
    for artifact in artifacts:
        artifact_type = str(artifact["type"])
        artifact_format = str(artifact["format"])
        artifact_key = f"{artifact_type}:{artifact_format}"
        row = rows_by_type_format.get((artifact_type, artifact_format))
        if row is None:
            missing_listed_artifacts.append(artifact_key)
            continue
        response = client.get(f"/v1/deliverables/{row['deliverable_id']}/content", headers=headers)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            download_failures.append(
                {
                    "artifact": artifact_key,
                    "status_code": response.status_code,
                }
            )
            continue
        actual_sha = hashlib.sha256(response.content).hexdigest()
        expected_sha = str(artifact["sha256"])
        if actual_sha != expected_sha:
            sha_mismatches.append(artifact_key)
        expected_content_type = resolve_content_type(artifact_type, artifact_format)
        actual_content_type = response.headers.get("content-type", "").split(";")[0]
        if actual_content_type != expected_content_type:
            content_type_mismatches.append(artifact_key)
        downloaded.append(
            {
                "type": artifact_type,
                "format": artifact_format,
                "content_type": actual_content_type,
                "sha256": actual_sha,
                "size_bytes": len(response.content),
            }
        )

    report = {
        "synthetic_rehearsal_only": True,
        "real_example_not_run": True,
        "not_vc_ready": True,
        "runtime_proof_required": True,
        "package_surface_verified": not (
            sha_mismatches
            or content_type_mismatches
            or missing_listed_artifacts
            or download_failures
        ),
        "manifest_review_verified": True,
        "manifest_artifact_count": int(manifest["artifact_count"]),
        "listed_deliverable_count": len(listed),
        "export_artifact_count": int(export_summary["artifact_count"]),
        "listed_types": sorted({str(item["deliverable_type"]) for item in listed}),
        "downloaded_artifact_count": len(downloaded),
        "downloaded_artifacts": downloaded,
        "download_sha256_mismatches": sha_mismatches,
        "content_type_mismatches": content_type_mismatches,
        "missing_listed_artifacts": missing_listed_artifacts,
        "download_failures": download_failures,
    }
    _assert_no_report_leakage(report)
    return report


def _require_gdbs_dataset_root(dataset_root: Path, *, repo_root: Path | None = None) -> Path:
    base = (repo_root or Path.cwd()).resolve()
    expected = (base / "datasets" / "gdbs_full").resolve()
    resolved = (
        (base / dataset_root).resolve()
        if not dataset_root.is_absolute()
        else dataset_root.resolve()
    )
    if resolved != expected:
        raise SyntheticRehearsalScopeError("SYNTHETIC_GDBS_ONLY")
    if not resolved.exists():
        raise SyntheticRehearsalScopeError("SYNTHETIC_GDBS_ONLY")
    return resolved


def _gdbs_deal_dirs(dataset_root: Path) -> list[Path]:
    deals_root = dataset_root / "deals"
    if not deals_root.exists():
        raise SyntheticRehearsalScopeError("SYNTHETIC_GDBS_ONLY")
    return sorted(path for path in deals_root.iterdir() if path.is_dir())


def _format_counts(files: list[Path]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in files:
        suffix = path.suffix.lower() or "<none>"
        counts[suffix] = counts.get(suffix, 0) + 1
    return dict(sorted(counts.items()))


def _summarize_selected_artifacts(dataset_root: Path, selected_cases: list[Any]) -> dict[str, Any]:
    artifact_types: set[str] = set()
    artifact_formats: set[str] = set()
    artifact_sha256: set[str] = set()
    artifact_sha256_mismatches: set[str] = set()
    artifact_count = 0
    for case in selected_cases:
        artifacts_path = dataset_root / case.directory / "artifacts.json"
        artifacts = _load_artifact_manifest(artifacts_path)
        for artifact in artifacts:
            resolved_path = _resolve_gdbs_artifact_uri(
                dataset_root,
                str(artifact.get("storage_uri") or ""),
            )
            if not resolved_path.exists():
                raise SyntheticRehearsalScopeError("SYNTHETIC_ARTIFACT_NOT_FOUND")
            artifact_count += 1
            artifact_type = str(artifact.get("artifact_type") or "DATA_ROOM_FILE")
            artifact_format = resolved_path.suffix.lower()
            artifact_types.add(artifact_type)
            artifact_formats.add(artifact_format)
            actual_sha256 = hashlib.sha256(resolved_path.read_bytes()).hexdigest()
            artifact_sha256.add(actual_sha256)
            expected_sha256 = str(artifact.get("sha256") or "")
            if expected_sha256 and expected_sha256 != actual_sha256:
                artifact_sha256_mismatches.add(f"{artifact_type}:{artifact_format}")
    return {
        "artifact_count": artifact_count,
        "artifact_types": sorted(artifact_types),
        "artifact_formats": sorted(artifact_formats),
        "artifact_sha256": sorted(artifact_sha256),
        "artifact_sha256_mismatches": sorted(artifact_sha256_mismatches),
    }


def _load_artifact_manifest(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyntheticRehearsalScopeError("SYNTHETIC_ARTIFACT_MANIFEST_INVALID") from exc
    artifacts = data.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise SyntheticRehearsalScopeError("SYNTHETIC_ARTIFACT_MANIFEST_INVALID")
    return [artifact for artifact in artifacts if isinstance(artifact, dict)]


def _upload_selected_gdbs_cases_via_api(
    *,
    dataset_root: Path,
    selected_cases: list[Any],
    object_store_base_dir: Path | None,
) -> dict[str, Any]:
    if object_store_base_dir is None:
        with tempfile.TemporaryDirectory(prefix="idis_slice68_upload_") as tmp_dir:
            return _upload_selected_gdbs_cases_via_api(
                dataset_root=dataset_root,
                selected_cases=selected_cases,
                object_store_base_dir=Path(tmp_dir),
            )

    previous_api_keys = os.environ.get(IDIS_API_KEYS_ENV)
    previous_database_url = os.environ.get("IDIS_DATABASE_URL")
    previous_object_store_backend = os.environ.get("IDIS_OBJECT_STORE_BACKEND")
    previous_object_store_base = os.environ.get("IDIS_OBJECT_STORE_BASE_DIR")
    try:
        os.environ[IDIS_API_KEYS_ENV] = json.dumps(
            {
                SYNTHETIC_API_REHEARSAL_API_KEY: {
                    "tenant_id": SYNTHETIC_API_REHEARSAL_TENANT_ID,
                    "actor_id": "slice68-synthetic-api-upload",
                    "name": "Slice68 Synthetic API Upload",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST", "ADMIN"],
                }
            }
        )
        os.environ.pop("IDIS_DATABASE_URL", None)
        os.environ["IDIS_OBJECT_STORE_BACKEND"] = "filesystem"
        os.environ["IDIS_OBJECT_STORE_BASE_DIR"] = str(object_store_base_dir)
        clear_deals_store()
        clear_document_store()
        app = create_app(
            audit_sink=InMemoryAuditSink(),
            idempotency_store=SqliteIdempotencyStore(in_memory=True),
            service_region="me-south-1",
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            return _upload_selected_gdbs_cases_with_client(
                client=client,
                dataset_root=dataset_root,
                selected_cases=selected_cases,
            )
    finally:
        _restore_env(IDIS_API_KEYS_ENV, previous_api_keys)
        _restore_env("IDIS_DATABASE_URL", previous_database_url)
        _restore_env("IDIS_OBJECT_STORE_BACKEND", previous_object_store_backend)
        _restore_env("IDIS_OBJECT_STORE_BASE_DIR", previous_object_store_base)


def _upload_and_attempt_strict_blocked_run_via_api(
    *,
    dataset_root: Path,
    selected_cases: list[Any],
    object_store_base_dir: Path | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if object_store_base_dir is None:
        with tempfile.TemporaryDirectory(prefix="idis_slice69_run_") as tmp_dir:
            return _upload_and_attempt_strict_blocked_run_via_api(
                dataset_root=dataset_root,
                selected_cases=selected_cases,
                object_store_base_dir=Path(tmp_dir),
            )

    previous_api_keys = os.environ.get(IDIS_API_KEYS_ENV)
    previous_database_url = os.environ.get("IDIS_DATABASE_URL")
    previous_require_full_live = os.environ.get("IDIS_REQUIRE_FULL_LIVE")
    previous_strict_dotenv_path = os.environ.get("IDIS_STRICT_DOTENV_PATH")
    previous_object_store_backend = os.environ.get("IDIS_OBJECT_STORE_BACKEND")
    previous_object_store_base = os.environ.get("IDIS_OBJECT_STORE_BASE_DIR")
    try:
        os.environ[IDIS_API_KEYS_ENV] = json.dumps(
            {
                SYNTHETIC_API_REHEARSAL_API_KEY: {
                    "tenant_id": SYNTHETIC_API_REHEARSAL_TENANT_ID,
                    "actor_id": "slice69-synthetic-api-run",
                    "name": "Slice69 Synthetic API Run",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST", "ADMIN"],
                }
            }
        )
        os.environ.pop("IDIS_DATABASE_URL", None)
        os.environ["IDIS_REQUIRE_FULL_LIVE"] = "1"
        os.environ.pop("IDIS_STRICT_DOTENV_PATH", None)
        os.environ["IDIS_OBJECT_STORE_BACKEND"] = "filesystem"
        os.environ["IDIS_OBJECT_STORE_BASE_DIR"] = str(object_store_base_dir)
        clear_deals_store()
        clear_document_store()
        app = create_app(
            audit_sink=InMemoryAuditSink(),
            idempotency_store=SqliteIdempotencyStore(in_memory=True),
            service_region="me-south-1",
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            upload_result, deal_id = _upload_selected_gdbs_cases_for_run_with_client(
                client=client,
                dataset_root=dataset_root,
                selected_cases=selected_cases,
            )
            run_attempt = _attempt_strict_blocked_run_with_client(
                client=client,
                deal_id=deal_id,
                document_ids=[item["document_id"] for item in upload_result["uploaded_documents"]],
            )
            return upload_result, run_attempt
    finally:
        _restore_env(IDIS_API_KEYS_ENV, previous_api_keys)
        _restore_env("IDIS_DATABASE_URL", previous_database_url)
        _restore_env("IDIS_REQUIRE_FULL_LIVE", previous_require_full_live)
        _restore_env("IDIS_STRICT_DOTENV_PATH", previous_strict_dotenv_path)
        _restore_env("IDIS_OBJECT_STORE_BACKEND", previous_object_store_backend)
        _restore_env("IDIS_OBJECT_STORE_BASE_DIR", previous_object_store_base)


def _upload_and_execute_non_strict_full_via_api(
    *,
    dataset_root: Path,
    selected_cases: list[Any],
    object_store_base_dir: Path | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if object_store_base_dir is None:
        with tempfile.TemporaryDirectory(prefix="idis_slice70_full_run_") as tmp_dir:
            return _upload_and_execute_non_strict_full_via_api(
                dataset_root=dataset_root,
                selected_cases=selected_cases,
                object_store_base_dir=Path(tmp_dir),
            )

    previous_api_keys = os.environ.get(IDIS_API_KEYS_ENV)
    previous_database_url = os.environ.get("IDIS_DATABASE_URL")
    previous_require_full_live = os.environ.get("IDIS_REQUIRE_FULL_LIVE")
    previous_strict_dotenv_path = os.environ.get("IDIS_STRICT_DOTENV_PATH")
    previous_object_store_backend = os.environ.get("IDIS_OBJECT_STORE_BACKEND")
    previous_object_store_base = os.environ.get("IDIS_OBJECT_STORE_BASE_DIR")
    previous_vector_search = os.environ.get("IDIS_ENABLE_VECTOR_SEARCH")
    previous_live_provider_env = {
        key: os.environ.get(key) for key in SYNTHETIC_FULL_EXECUTION_LIVE_PROVIDER_ENV_KEYS
    }
    try:
        os.environ[IDIS_API_KEYS_ENV] = json.dumps(
            {
                SYNTHETIC_API_REHEARSAL_API_KEY: {
                    "tenant_id": SYNTHETIC_API_REHEARSAL_TENANT_ID,
                    "actor_id": "slice70-synthetic-full-execution",
                    "name": "Slice70 Synthetic FULL Execution",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST", "ADMIN"],
                }
            }
        )
        os.environ.pop("IDIS_DATABASE_URL", None)
        os.environ["IDIS_REQUIRE_FULL_LIVE"] = "0"
        os.environ.pop("IDIS_STRICT_DOTENV_PATH", None)
        os.environ["IDIS_OBJECT_STORE_BACKEND"] = "filesystem"
        os.environ["IDIS_OBJECT_STORE_BASE_DIR"] = str(object_store_base_dir)
        os.environ["IDIS_ENABLE_VECTOR_SEARCH"] = "0"
        for key in SYNTHETIC_FULL_EXECUTION_LIVE_PROVIDER_ENV_KEYS:
            os.environ.pop(key, None)
        clear_deals_store()
        clear_document_store()
        clear_runs_store()
        clear_run_steps_store()
        clear_deliverables_store()
        app = create_app(
            audit_sink=InMemoryAuditSink(),
            idempotency_store=SqliteIdempotencyStore(in_memory=True),
            service_region="me-south-1",
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            upload_result, deal_id = _upload_selected_gdbs_cases_for_run_with_client(
                client=client,
                dataset_root=dataset_root,
                selected_cases=selected_cases,
            )
            execution_result = _execute_non_strict_full_run_with_client(
                client=client,
                deal_id=deal_id,
                document_ids=[item["document_id"] for item in upload_result["uploaded_documents"]],
            )
            package_surface = _same_run_package_surface_status(
                client=client,
                deal_id=deal_id,
                run_id=str(execution_result.get("run_id") or ""),
            )
            return upload_result, execution_result, package_surface
    finally:
        _restore_env(IDIS_API_KEYS_ENV, previous_api_keys)
        _restore_env("IDIS_DATABASE_URL", previous_database_url)
        _restore_env("IDIS_REQUIRE_FULL_LIVE", previous_require_full_live)
        _restore_env("IDIS_STRICT_DOTENV_PATH", previous_strict_dotenv_path)
        _restore_env("IDIS_OBJECT_STORE_BACKEND", previous_object_store_backend)
        _restore_env("IDIS_OBJECT_STORE_BASE_DIR", previous_object_store_base)
        _restore_env("IDIS_ENABLE_VECTOR_SEARCH", previous_vector_search)
        for key, previous_value in previous_live_provider_env.items():
            _restore_env(key, previous_value)


def _upload_execute_and_verify_package_surfaces_via_api(
    *,
    dataset_root: Path,
    selected_cases: list[Any],
    object_store_base_dir: Path | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if object_store_base_dir is None:
        with tempfile.TemporaryDirectory(prefix="idis_slice71_package_") as tmp_dir:
            return _upload_execute_and_verify_package_surfaces_via_api(
                dataset_root=dataset_root,
                selected_cases=selected_cases,
                object_store_base_dir=Path(tmp_dir),
            )

    previous_api_keys = os.environ.get(IDIS_API_KEYS_ENV)
    previous_require_full_live = os.environ.get("IDIS_REQUIRE_FULL_LIVE")
    previous_strict_dotenv_path = os.environ.get("IDIS_STRICT_DOTENV_PATH")
    previous_object_store_backend = os.environ.get("IDIS_OBJECT_STORE_BACKEND")
    previous_object_store_base = os.environ.get("IDIS_OBJECT_STORE_BASE_DIR")
    previous_vector_search = os.environ.get("IDIS_ENABLE_VECTOR_SEARCH")
    previous_live_provider_env = {
        key: os.environ.get(key) for key in SYNTHETIC_FULL_EXECUTION_LIVE_PROVIDER_ENV_KEYS
    }
    try:
        os.environ[IDIS_API_KEYS_ENV] = json.dumps(
            {
                SYNTHETIC_API_REHEARSAL_API_KEY: {
                    "tenant_id": SYNTHETIC_API_REHEARSAL_TENANT_ID,
                    "actor_id": "slice71-synthetic-package-surface",
                    "name": "Slice71 Synthetic Package Surface",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST", "ADMIN"],
                }
            }
        )
        os.environ["IDIS_REQUIRE_FULL_LIVE"] = "0"
        os.environ.pop("IDIS_STRICT_DOTENV_PATH", None)
        os.environ["IDIS_OBJECT_STORE_BACKEND"] = "filesystem"
        os.environ["IDIS_OBJECT_STORE_BASE_DIR"] = str(object_store_base_dir)
        os.environ["IDIS_ENABLE_VECTOR_SEARCH"] = "0"
        for key in SYNTHETIC_FULL_EXECUTION_LIVE_PROVIDER_ENV_KEYS:
            os.environ.pop(key, None)
        clear_deals_store()
        clear_document_store()
        clear_runs_store()
        clear_run_steps_store()
        clear_deliverables_store()
        app = create_app(
            audit_sink=InMemoryAuditSink(),
            idempotency_store=SqliteIdempotencyStore(in_memory=True),
            service_region="me-south-1",
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            upload_result, deal_id = _upload_selected_gdbs_cases_for_run_with_client(
                client=client,
                dataset_root=dataset_root,
                selected_cases=selected_cases,
            )
            execution_result = _execute_non_strict_full_run_with_client(
                client=client,
                deal_id=deal_id,
                document_ids=[item["document_id"] for item in upload_result["uploaded_documents"]],
            )
            package_surface = _verified_same_run_package_surface_status(
                client=client,
                deal_id=deal_id,
                run_id=str(execution_result.get("run_id") or ""),
            )
            return upload_result, execution_result, package_surface
    finally:
        _restore_env(IDIS_API_KEYS_ENV, previous_api_keys)
        _restore_env("IDIS_REQUIRE_FULL_LIVE", previous_require_full_live)
        _restore_env("IDIS_STRICT_DOTENV_PATH", previous_strict_dotenv_path)
        _restore_env("IDIS_OBJECT_STORE_BACKEND", previous_object_store_backend)
        _restore_env("IDIS_OBJECT_STORE_BASE_DIR", previous_object_store_base)
        _restore_env("IDIS_ENABLE_VECTOR_SEARCH", previous_vector_search)
        for key, previous_value in previous_live_provider_env.items():
            _restore_env(key, previous_value)


def _upload_selected_gdbs_cases_with_client(
    *,
    client: TestClient,
    dataset_root: Path,
    selected_cases: list[Any],
) -> dict[str, Any]:
    upload_result, _deal_ids = _upload_selected_gdbs_cases_core_with_client(
        client=client,
        dataset_root=dataset_root,
        selected_cases=selected_cases,
    )
    return upload_result


def _upload_selected_gdbs_cases_core_with_client(
    *,
    client: TestClient,
    dataset_root: Path,
    selected_cases: list[Any],
) -> tuple[dict[str, Any], list[str]]:
    artifact_types: set[str] = set()
    artifact_formats: set[str] = set()
    uploaded_documents: list[dict[str, Any]] = []
    deal_ids: list[str] = []
    headers = {"X-IDIS-API-Key": SYNTHETIC_API_REHEARSAL_API_KEY}
    for case in selected_cases:
        deal_response = client.post(
            "/v1/deals",
            headers=headers,
            json={"name": case.case_id, "company_name": case.case_id},
        )
        _raise_safe_api_error(deal_response, reason_code="SYNTHETIC_API_DEAL_CREATE_FAILED")
        deal_id = str(deal_response.json()["deal_id"])
        deal_ids.append(deal_id)
        for artifact in _load_artifact_manifest(dataset_root / case.directory / "artifacts.json"):
            artifact_type = str(artifact.get("artifact_type") or "DATA_ROOM_FILE")
            resolved_path = _resolve_gdbs_artifact_uri(
                dataset_root,
                str(artifact.get("storage_uri") or ""),
            )
            if not resolved_path.exists():
                raise SyntheticRehearsalScopeError("SYNTHETIC_ARTIFACT_NOT_FOUND")
            artifact_format = resolved_path.suffix.lower()
            artifact_types.add(artifact_type)
            artifact_formats.add(artifact_format)
            data = resolved_path.read_bytes()
            sha256 = hashlib.sha256(data).hexdigest()
            try:
                response = client.post(
                    f"/v1/deals/{deal_id}/documents/upload",
                    headers={**headers, "Content-Type": "application/octet-stream"},
                    params={
                        "filename": _safe_upload_filename(artifact_type, artifact_format),
                        "doc_type": _api_doc_type_for_artifact(artifact_type),
                        "sha256": sha256,
                        "source_system": "slice68-synthetic-api-upload",
                    },
                    content=data,
                )
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code if exc.response is not None else 0
                raise SyntheticRehearsalScopeError(
                    f"SYNTHETIC_API_UPLOAD_FAILED:status_code={status_code}"
                ) from None
            _raise_safe_api_error(response, reason_code="SYNTHETIC_API_UPLOAD_FAILED")
            body = response.json()
            uploaded_documents.append(
                {
                    "document_id": str(body["document_id"]),
                    "doc_type": str(body["doc_type"]),
                    "format": artifact_format,
                    "sha256": str(body["sha256"]),
                    "status": str(body.get("parse_status") or "UNKNOWN"),
                }
            )
    upload_result = {
        "uploaded_case_count": len(selected_cases),
        "uploaded_document_count": len(uploaded_documents),
        "artifact_types": sorted(artifact_types),
        "artifact_formats": sorted(artifact_formats),
        "uploaded_documents": uploaded_documents,
    }
    return upload_result, deal_ids


def _upload_selected_gdbs_cases_for_run_with_client(
    *,
    client: TestClient,
    dataset_root: Path,
    selected_cases: list[Any],
) -> tuple[dict[str, Any], str]:
    if len(selected_cases) != 1:
        raise SyntheticRehearsalScopeError("SYNTHETIC_RUN_SINGLE_CASE_ONLY")
    upload_result, deal_ids = _upload_selected_gdbs_cases_core_with_client(
        client=client,
        dataset_root=dataset_root,
        selected_cases=selected_cases,
    )
    if len(deal_ids) != 1:
        raise SyntheticRehearsalScopeError("SYNTHETIC_RUN_DEAL_SELECTION_INVALID")
    return upload_result, deal_ids[0]


def _attempt_strict_blocked_run_with_client(
    *,
    client: TestClient,
    deal_id: str,
    document_ids: list[str],
) -> dict[str, Any]:
    response = client.post(
        f"/v1/deals/{deal_id}/runs",
        headers={"X-IDIS-API-Key": SYNTHETIC_API_REHEARSAL_API_KEY},
        json={
            "mode": "FULL",
            "source": {
                "type": "deal_documents",
                "document_ids": document_ids,
            },
        },
    )
    try:
        response_body = response.json()
    except ValueError:
        response_body = {}
    if response.status_code == 409 and response_body.get("code") == "STRICT_FULL_LIVE_BLOCKED":
        return {
            "enabled": True,
            "status": "blocked",
            "reason_code": "STRICT_FULL_LIVE_BLOCKED",
            "http_status_code": response.status_code,
            "run_created": False,
        }
    if response.status_code == 202:
        return {
            "enabled": True,
            "status": "failed_safe",
            "reason_code": "SYNTHETIC_RUN_UNEXPECTEDLY_ACCEPTED",
            "http_status_code": response.status_code,
            "run_created": True,
        }
    return {
        "enabled": True,
        "status": "failed_safe",
        "reason_code": str(response_body.get("code") or "SYNTHETIC_RUN_FAILED"),
        "http_status_code": response.status_code,
        "run_created": False,
    }


def _execute_non_strict_full_run_with_client(
    *,
    client: TestClient,
    deal_id: str,
    document_ids: list[str],
) -> dict[str, Any]:
    response = client.post(
        f"/v1/deals/{deal_id}/runs",
        headers={"X-IDIS-API-Key": SYNTHETIC_API_REHEARSAL_API_KEY},
        json={
            "mode": "FULL",
            "source": {
                "type": "deal_documents",
                "document_ids": document_ids,
            },
        },
    )
    try:
        response_body = response.json()
    except ValueError:
        response_body = {}
    if response.status_code != 202:
        return {
            "enabled": True,
            "status": "failed_safe",
            "reason_code": str(response_body.get("code") or "SYNTHETIC_FULL_EXECUTION_FAILED"),
            "http_status_code": response.status_code,
            "run_created": False,
            "strict_full_live_required": False,
            "terminal": False,
            "run_status": "FAILED",
            "status_run_status": "FAILED",
            "completed_step_count": 0,
            "completed_step_names": [],
            "failed_step_names": [],
        }

    run_id = str(response_body.get("run_id") or "")
    status_body = response_body
    if run_id:
        status_response = client.get(
            f"/v1/runs/{run_id}",
            headers={"X-IDIS-API-Key": SYNTHETIC_API_REHEARSAL_API_KEY},
        )
        _raise_safe_api_error(
            status_response,
            reason_code="SYNTHETIC_FULL_EXECUTION_STATUS_FAILED",
        )
        status_body = status_response.json()
    completed_step_names = [
        str(step.get("step_name"))
        for step in status_body.get("steps", [])
        if step.get("status") == "COMPLETED"
    ]
    failed_step_names = [
        str(step.get("step_name"))
        for step in status_body.get("steps", [])
        if step.get("status") == "FAILED"
    ]
    run_status = str(status_body.get("status") or response_body.get("status") or "UNKNOWN")
    step_summaries = {
        str(step.get("step_name")): step.get("summary")
        for step in status_body.get("steps", [])
        if isinstance(step, dict)
    }
    return {
        "enabled": True,
        "status": "completed" if run_status == "SUCCEEDED" else "failed_safe",
        "reason_code": None
        if run_status == "SUCCEEDED"
        else "SYNTHETIC_FULL_EXECUTION_NOT_SUCCEEDED",
        "http_status_code": response.status_code,
        "run_created": True,
        "run_id": run_id,
        "strict_full_live_required": False,
        "terminal": run_status in {"SUCCEEDED", "FAILED", "BLOCKED", "CANCELLED"},
        "run_status": run_status,
        "status_run_status": run_status,
        "completed_step_count": len(completed_step_names),
        "completed_step_names": completed_step_names,
        "expected_completed_step_count": len(FULL_STEPS),
        "expected_completed_step_names": [step.value for step in FULL_STEPS],
        "failed_step_names": failed_step_names,
        "deliverables_step_summary": step_summaries.get("DELIVERABLES") or {},
    }


def _same_run_package_surface_status(
    *,
    client: TestClient,
    deal_id: str,
    run_id: str,
) -> dict[str, Any]:
    listed_response = client.get(
        f"/v1/deals/{deal_id}/deliverables",
        headers={"X-IDIS-API-Key": SYNTHETIC_API_REHEARSAL_API_KEY},
    )
    listed_count = 0
    if listed_response.status_code == 200:
        try:
            listed_body = listed_response.json()
        except ValueError:
            listed_body = {}
        listed_count = len(
            [
                item
                for item in listed_body.get("items", [])
                if str(item.get("run_id") or "") == run_id
            ]
        )

    manifest_response = client.get(
        f"/v1/deals/{deal_id}/runs/{run_id}/product-bundle/manifest",
        headers={"X-IDIS-API-Key": SYNTHETIC_API_REHEARSAL_API_KEY},
    )
    if listed_count == 0 or manifest_response.status_code == 404:
        return {
            "status": "not_created",
            "verified": False,
            "reason_code": "SAME_RUN_PACKAGE_SURFACES_NOT_CREATED",
            "listed_deliverable_count_for_run": listed_count,
            "manifest_http_status_code": manifest_response.status_code,
            "downloaded_artifact_count": 0,
        }
    return {
        "status": "not_verified",
        "verified": False,
        "reason_code": "SAME_RUN_PACKAGE_SURFACES_PRESENT_BUT_NOT_VERIFIED_IN_SLICE70",
        "listed_deliverable_count_for_run": listed_count,
        "manifest_http_status_code": manifest_response.status_code,
        "downloaded_artifact_count": 0,
    }


def _verified_same_run_package_surface_status(
    *,
    client: TestClient,
    deal_id: str,
    run_id: str,
) -> dict[str, Any]:
    headers = {"X-IDIS-API-Key": SYNTHETIC_API_REHEARSAL_API_KEY}
    listed_response = client.get(f"/v1/deals/{deal_id}/deliverables", headers=headers)
    _raise_safe_api_error(listed_response, reason_code="SYNTHETIC_PACKAGE_LIST_FAILED")
    listed = [
        item
        for item in listed_response.json().get("items", [])
        if str(item.get("run_id") or "") == run_id
    ]
    manifest_response = client.get(
        f"/v1/deals/{deal_id}/runs/{run_id}/product-bundle/manifest",
        headers=headers,
    )
    _raise_safe_api_error(manifest_response, reason_code="SYNTHETIC_PACKAGE_MANIFEST_FAILED")
    manifest = manifest_response.json()
    artifacts = [
        artifact for artifact in manifest.get("artifacts", []) if isinstance(artifact, dict)
    ]
    manifest_run_id = str(manifest.get("run_id") or "")
    manifest_deal_id = str(manifest.get("deal_id") or "")
    manifest_identity_mismatch = manifest_run_id != run_id or manifest_deal_id != deal_id
    listed_ids = {str(row.get("deliverable_id") or "") for row in listed}
    manifest_artifact_ids = {
        str(artifact.get("deliverable_id") or "")
        for artifact in artifacts
        if artifact.get("deliverable_id")
    }
    missing_manifest_deliverable_id_artifacts = [
        f"{str(artifact.get('type') or '')}:{str(artifact.get('format') or '')}"
        for artifact in artifacts
        if not artifact.get("deliverable_id")
    ]
    missing_manifest_deliverable_ids = sorted(manifest_artifact_ids - listed_ids)
    rows_by_id = {str(row.get("deliverable_id") or ""): row for row in listed}

    downloaded: list[dict[str, Any]] = []
    sha_mismatches: list[str] = []
    content_type_mismatches: list[str] = []
    missing_listed_artifacts: list[str] = []
    download_failures: list[dict[str, Any]] = []
    for artifact in artifacts:
        artifact_type = str(artifact.get("type") or "")
        artifact_format = str(artifact.get("format") or "")
        artifact_key = f"{artifact_type}:{artifact_format}"
        artifact_deliverable_id = str(artifact.get("deliverable_id") or "")
        row = rows_by_id.get(artifact_deliverable_id)
        if row is None:
            missing_listed_artifacts.append(artifact_key)
            continue
        response = client.get(f"/v1/deliverables/{row['deliverable_id']}/content", headers=headers)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            download_failures.append(
                {"artifact": artifact_key, "status_code": response.status_code}
            )
            continue
        actual_sha = hashlib.sha256(response.content).hexdigest()
        expected_sha = str(artifact.get("sha256") or "")
        if actual_sha != expected_sha:
            sha_mismatches.append(artifact_key)
        expected_content_type = resolve_content_type(artifact_type, artifact_format)
        actual_content_type = response.headers.get("content-type", "").split(";")[0]
        if actual_content_type != expected_content_type:
            content_type_mismatches.append(artifact_key)
        downloaded.append(
            {
                "type": artifact_type,
                "format": artifact_format,
                "content_type": actual_content_type,
                "sha256": actual_sha,
                "manifest_sha256": expected_sha,
                "size_bytes": len(response.content),
            }
        )

    verified = not (
        sha_mismatches
        or content_type_mismatches
        or missing_listed_artifacts
        or missing_manifest_deliverable_id_artifacts
        or missing_manifest_deliverable_ids
        or download_failures
        or manifest_identity_mismatch
        or not downloaded
    )
    return {
        "status": "verified" if verified else "failed_safe",
        "verified": verified,
        "same_run_id": run_id,
        "listed_deliverable_count_for_run": len(listed),
        "listed_types": sorted({str(item["deliverable_type"]) for item in listed}),
        "manifest_http_status_code": manifest_response.status_code,
        "manifest_run_id": manifest_run_id,
        "manifest_artifact_count": len(artifacts),
        "manifest_identity_mismatch": manifest_identity_mismatch,
        "missing_manifest_deliverable_id_artifacts": missing_manifest_deliverable_id_artifacts,
        "missing_manifest_deliverable_ids": missing_manifest_deliverable_ids,
        "downloaded_artifact_count": len(downloaded),
        "downloaded_artifacts": downloaded,
        "download_sha256_mismatches": sha_mismatches,
        "content_type_mismatches": content_type_mismatches,
        "missing_listed_artifacts": missing_listed_artifacts,
        "download_failures": download_failures,
    }


def _api_rehearsal_corpus_summary(corpus: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in corpus.items() if key not in {"dataset_root"}}


def _require_available_postgres_for_synthetic_package_surface() -> None:
    try:
        engine = get_app_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise SyntheticRehearsalScopeError("SYNTHETIC_POSTGRES_UNAVAILABLE") from exc


def _raise_safe_api_error(response: httpx.Response, *, reason_code: str) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        raise SyntheticRehearsalScopeError(
            f"{reason_code}:status_code={response.status_code}"
        ) from None


def _api_doc_type_for_artifact(artifact_type: str) -> str:
    mapping = {
        "PITCH_DECK": "PITCH_DECK",
        "FIN_MODEL": "FINANCIAL_MODEL",
        "FINANCIAL_MODEL": "FINANCIAL_MODEL",
        "TRANSCRIPT": "TRANSCRIPT",
        "TERM_SHEET": "TERM_SHEET",
    }
    return mapping.get(artifact_type, "DATA_ROOM_FILE")


def _safe_upload_filename(artifact_type: str, artifact_format: str) -> str:
    normalized_format = (
        artifact_format if artifact_format.startswith(".") else f".{artifact_format}"
    )
    return f"{artifact_type.lower()}{normalized_format}"


def _restore_env(key: str, previous_value: str | None) -> None:
    if previous_value is None:
        os.environ.pop(key, None)
        return
    os.environ[key] = previous_value


def _resolve_gdbs_artifact_uri(dataset_root: Path, uri: str) -> Path:
    prefix = "file://datasets/gdbs_full/"
    if not uri.startswith(prefix):
        raise SyntheticRehearsalScopeError("SYNTHETIC_ARTIFACT_URI_REJECTED")
    relative = Path(uri.removeprefix(prefix))
    resolved = (dataset_root / relative).resolve()
    dataset_root_resolved = dataset_root.resolve()
    if dataset_root_resolved not in (resolved, *resolved.parents):
        raise SyntheticRehearsalScopeError("SYNTHETIC_ARTIFACT_URI_REJECTED")
    return resolved


def _safe_strict_blockers(report: Any) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    components = {component.component_name: component for component in report.components}
    inventory = {item.component_name: item for item in report.component_inventory}
    for component_name in sorted(report.blocking_components):
        component = components.get(component_name)
        if component is not None:
            blockers.append(
                {
                    "component_name": _safe_component_label(component.component_name),
                    "status": component.status.value,
                    "required_env_var_count": len(component.required_env_vars),
                    "required_service_count": len(component.required_services),
                }
            )
            continue
        item = inventory.get(component_name)
        if item is not None:
            blockers.append(
                {
                    "component_name": _safe_component_label(item.component_name),
                    "status": item.health_check_status,
                    "required_env_var_count": 0,
                    "required_service_count": 0,
                }
            )
    return blockers


def _safe_component_label(value: str) -> str:
    label = value.lower().replace("/", "_").replace(" ", "_")
    replacements = {
        "real_example": "private_gate",
        "anthropic": "live_model",
        "openai": "live_model",
        "finnhub": "byol_market_data",
        "fmp": "byol_market_data",
        "vectors": "rag",
        "vector": "rag",
        "embedding": "retrieval_model",
    }
    for source, replacement in replacements.items():
        label = label.replace(source, replacement)
    return label


def _assert_no_report_leakage(report: dict[str, Any]) -> None:
    serialized = str(report).lower()
    forbidden = (
        "object_key",
        "local_path",
        "raw_text",
        "prompt_transcript",
        "embedding",
        "vector",
        "file://datasets/gdbs_full",
        "datasets/gdbs_full/deals",
        "pitch_deck.pdf",
        "financials.xlsx",
        "filename",
        "secret",
        "c:\\projects",
        ".local_reports",
    )
    leaked = [token for token in forbidden if token in serialized]
    if leaked:
        raise ValueError(f"SYNTHETIC_REHEARSAL_REPORT_LEAKAGE: {sorted(leaked)}")

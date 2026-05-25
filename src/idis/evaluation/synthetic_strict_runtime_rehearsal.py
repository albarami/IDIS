"""Synthetic strict-runtime rehearsal helpers for GDBS-only shakeout."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from idis.deliverables.artifact_catalog import resolve_content_type
from idis.evaluation.benchmarks.gdbs import load_gdbs_suite
from idis.services.runs.strict_full_live import build_strict_full_live_readiness_report


class SyntheticRehearsalScopeError(ValueError):
    """Raised when a rehearsal attempts to leave the approved synthetic scope."""


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


def _require_gdbs_dataset_root(dataset_root: Path) -> Path:
    expected = (Path.cwd() / "datasets" / "gdbs_full").resolve()
    resolved = (
        (Path.cwd() / dataset_root).resolve()
        if not dataset_root.is_absolute()
        else dataset_root.resolve()
    )
    if resolved != expected:
        raise SyntheticRehearsalScopeError("SYNTHETIC_GDBS_ONLY")
    if not resolved.exists():
        raise SyntheticRehearsalScopeError("SYNTHETIC_GDBS_ONLY")
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
        "c:\\projects",
        ".local_reports",
    )
    leaked = [token for token in forbidden if token in serialized]
    if leaked:
        raise ValueError(f"SYNTHETIC_REHEARSAL_REPORT_LEAKAGE: {sorted(leaked)}")

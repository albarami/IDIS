"""Slice66 synthetic strict-runtime rehearsal tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.deliverables import _IN_MEMORY_DELIVERABLES, clear_deliverables_store
from idis.audit.sink import InMemoryAuditSink
from idis.deliverables.artifact_catalog import MANIFEST_ARTIFACT_TYPE
from idis.deliverables.generator import DeliverablesGenerator
from idis.deliverables.product_bundle import ProductBundleExporter
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.test_deliverables_generator import (
    _TIMESTAMP,
    _make_bundle,
    _make_context,
    _make_scorecard,
)
from tests.test_slice59_product_export_bundle import RecordingDeliverablesRepository

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"
API_KEY = "slice66-synthetic-key"


class _ApiKeys:
    @staticmethod
    def config() -> dict[str, dict[str, str | list[str]]]:
        return {
            API_KEY: {
                "tenant_id": TENANT_ID,
                "actor_id": "slice66-synthetic-actor",
                "name": "Slice66 Synthetic Rehearsal",
                "timezone": "UTC",
                "data_region": "me-south-1",
                "roles": ["ANALYST"],
            }
        }


def _export_synthetic_bundle(
    tmp_path: Path,
) -> tuple[RecordingDeliverablesRepository, dict[str, Any]]:
    object_store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    repository = RecordingDeliverablesRepository()
    bundle = DeliverablesGenerator(audit_sink=InMemoryAuditSink()).generate(
        ctx=_make_context(),
        bundle=_make_bundle(),
        scorecard=_make_scorecard(),
        deal_name="GDBS Synthetic Slice66",
        generated_at=_TIMESTAMP,
        deliverable_id_prefix="del-slice66",
    )
    exporter = ProductBundleExporter(
        deliverables_repo=repository,
        object_store=object_store,
        object_store_backend=object_store.backend_name,
    )
    export_summary = exporter.export_bundle(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        bundle=bundle,
        analysis_context=_make_context(),
        scorecard=_make_scorecard(),
        export_timestamp=_TIMESTAMP,
        layer2_evidence={
            "status": "completed",
            "layer2_challenge_ids": ["layer2-synthetic-001"],
            "source_debate_ids": ["debate-synthetic-001"],
            "claim_ids": ["claim-a"],
            "calc_ids": ["calc-a"],
            "finding_count": 1,
            "unresolved_question_count": 1,
            "muhasabah_passed": True,
        },
    )
    return repository, export_summary


def _seed_deliverables(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        _IN_MEMORY_DELIVERABLES[row["deliverable_id"]] = {
            **row,
            "created_at": "2026-05-25T00:00:00Z",
        }


def test_synthetic_rehearsal_status_report_is_gdbs_only_and_not_approval() -> None:
    """Status mode should report safe synthetic rehearsal scope without running private data."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        build_synthetic_rehearsal_status,
    )

    report = build_synthetic_rehearsal_status(dataset_root=Path("datasets/gdbs_full"), env={})
    serialized = json.dumps(report, sort_keys=True)

    assert report["synthetic_rehearsal_only"] is True
    assert report["real_example_not_run"] is True
    assert report["not_vc_ready"] is True
    assert report["runtime_proof_required"] is True
    assert report["dataset_id"] == "gdbs-f"
    assert report["bounded_execution"]["enabled"] is False
    assert report["real_example_gate_cleared"] is False
    assert report["strict_global_may_proceed"] is False
    assert report["strict_blockers"]
    assert "real_example/" not in serialized.lower()
    assert "real_example\\" not in serialized.lower()
    assert "C:\\Projects\\IDIS\\real_example" not in serialized
    assert "object_key" not in serialized
    assert "raw_text" not in serialized
    assert "prompt_transcript" not in serialized
    assert "embedding" not in serialized
    assert "vector" not in serialized.lower()


def test_synthetic_rehearsal_status_accepts_absolute_repo_local_gdbs_path() -> None:
    """Absolute repo-local datasets/gdbs_full path is still inside the approved scope."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        build_synthetic_rehearsal_status,
    )

    absolute_root = (Path.cwd() / "datasets" / "gdbs_full").resolve()

    report = build_synthetic_rehearsal_status(dataset_root=absolute_root, env={})

    assert report["synthetic_rehearsal_only"] is True
    assert report["dataset_root"] == "datasets/gdbs_full"
    assert report["dataset_id"] == "gdbs-f"


def test_synthetic_rehearsal_rejects_non_gdbs_dataset_root() -> None:
    """The Slice66 rehearsal boundary must not accept private or arbitrary roots."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        SyntheticRehearsalScopeError,
        build_synthetic_rehearsal_status,
    )

    private_root = Path("real_example")

    try:
        build_synthetic_rehearsal_status(dataset_root=private_root, env={})
    except SyntheticRehearsalScopeError as exc:
        assert "SYNTHETIC_GDBS_ONLY" in str(exc)
    else:
        raise AssertionError("private dataset root was accepted")


def test_package_surface_verification_walks_manifest_and_downloads_without_leakage(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Synthetic package verification should traverse export/list/manifest/download/hash."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import verify_package_surfaces

    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_ApiKeys.config()))
    monkeypatch.setenv("IDIS_OBJECT_STORE_BACKEND", "filesystem")
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(tmp_path / "objects"))
    clear_deals_store()
    clear_deliverables_store()
    _IN_MEMORY_DELIVERABLES.clear()

    repository, export_summary = _export_synthetic_bundle(tmp_path)
    _seed_deliverables(repository.rows)
    app = create_app(audit_sink=InMemoryAuditSink(), service_region="me-south-1")

    with TestClient(app) as client:
        report = verify_package_surfaces(
            client=client,
            api_key=API_KEY,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            export_summary=export_summary,
        )

    serialized = json.dumps(report, sort_keys=True)
    assert report["package_surface_verified"] is True
    assert report["manifest_review_verified"] is True
    assert report["downloaded_artifact_count"] == 13
    assert report["manifest_artifact_count"] == 13
    assert report["listed_deliverable_count"] == 14
    assert report["download_sha256_mismatches"] == []
    assert report["content_type_mismatches"] == []
    assert MANIFEST_ARTIFACT_TYPE in report["listed_types"]
    assert all(len(item["sha256"]) == 64 for item in report["downloaded_artifacts"])
    assert all(
        hashlib.sha256(bytes.fromhex(item["sha256"][:2])).hexdigest()
        for item in report["downloaded_artifacts"]
    )
    assert "object_key" not in serialized
    assert "local_path" not in serialized
    assert "raw_text" not in serialized
    assert "prompt_transcript" not in serialized
    assert "embedding" not in serialized
    assert "vector" not in serialized.lower()
    assert "C:\\Projects" not in serialized
    assert ".local_reports" not in serialized


def test_package_surface_verification_reports_missing_listed_row_without_crashing(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Manifest artifacts without listed deliverable rows become safe mismatches."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import verify_package_surfaces

    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_ApiKeys.config()))
    monkeypatch.setenv("IDIS_OBJECT_STORE_BACKEND", "filesystem")
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(tmp_path / "objects"))
    clear_deals_store()
    clear_deliverables_store()
    _IN_MEMORY_DELIVERABLES.clear()

    repository, export_summary = _export_synthetic_bundle(tmp_path)
    rows = [
        row
        for row in repository.rows
        if not (row["deliverable_type"] == "ic_memo" and row["format"] == "PDF")
    ]
    _seed_deliverables(rows)
    app = create_app(audit_sink=InMemoryAuditSink(), service_region="me-south-1")

    with TestClient(app) as client:
        report = verify_package_surfaces(
            client=client,
            api_key=API_KEY,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            export_summary=export_summary,
        )

    serialized = json.dumps(report, sort_keys=True)
    assert report["package_surface_verified"] is False
    assert report["missing_listed_artifacts"] == ["ic_memo:PDF"]
    assert "object_key" not in serialized
    assert "C:\\Projects" not in serialized


def test_package_surface_verification_reports_download_failure_without_crashing(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Download failures should be captured as safe mismatches instead of exceptions."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import verify_package_surfaces

    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_ApiKeys.config()))
    monkeypatch.setenv("IDIS_OBJECT_STORE_BACKEND", "filesystem")
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(tmp_path / "objects"))
    clear_deals_store()
    clear_deliverables_store()
    _IN_MEMORY_DELIVERABLES.clear()

    repository, export_summary = _export_synthetic_bundle(tmp_path)
    rows = [
        {
            **row,
            "status": "QUEUED"
            if row["deliverable_type"] == "ic_memo" and row["format"] == "PDF"
            else row["status"],
        }
        for row in repository.rows
    ]
    _seed_deliverables(rows)
    app = create_app(audit_sink=InMemoryAuditSink(), service_region="me-south-1")

    with TestClient(app) as client:
        report = verify_package_surfaces(
            client=client,
            api_key=API_KEY,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            export_summary=export_summary,
        )

    serialized = json.dumps(report, sort_keys=True)
    assert report["package_surface_verified"] is False
    assert report["download_failures"] == [{"artifact": "ic_memo:PDF", "status_code": 404}]
    assert "object_key" not in serialized
    assert "C:\\Projects" not in serialized

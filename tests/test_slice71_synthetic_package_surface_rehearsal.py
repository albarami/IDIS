"""Slice71 durable same-run synthetic package-surface rehearsal tests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from idis.deliverables.artifact_catalog import MANIFEST_ARTIFACT_TYPE, resolve_content_type
from idis.models.run_step import FULL_STEPS, StepName
from tests import test_ingestion_persists_documents_postgres as pg_helpers
from tests.test_deliverables_generator import _make_bundle, _make_context, _make_scorecard

pytest_plugins = ("tests.test_ingestion_persists_documents_postgres",)


def test_synthetic_package_surface_rehearsal_requires_explicit_package_opt_in() -> None:
    """Package-surface rehearsal must not run without explicit package verification opt-in."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    try:
        rehearsal.build_bounded_synthetic_package_surface_rehearsal(
            dataset_root=Path("datasets/gdbs_full"),
            env={},
            max_cases=1,
            allow_synthetic_api_upload=True,
            allow_synthetic_execution=True,
            allow_synthetic_package_surface_verification=False,
        )
    except rehearsal.SyntheticRehearsalScopeError as exc:
        assert "SYNTHETIC_PACKAGE_SURFACE_VERIFICATION_NOT_ALLOWED" in str(exc)
    else:
        raise AssertionError("package-surface rehearsal accepted missing package opt-in")


def test_synthetic_package_surface_rehearsal_fails_closed_without_postgres(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Slice71 must not silently fall back to in-memory package surfaces."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    monkeypatch.delenv("IDIS_DATABASE_URL", raising=False)
    monkeypatch.delenv("IDIS_DATABASE_ADMIN_URL", raising=False)

    try:
        rehearsal.build_bounded_synthetic_package_surface_rehearsal(
            dataset_root=Path("datasets/gdbs_full"),
            env={},
            max_cases=1,
            allow_synthetic_api_upload=True,
            allow_synthetic_execution=True,
            allow_synthetic_package_surface_verification=True,
            object_store_base_dir=tmp_path / "objects",
        )
    except rehearsal.SyntheticRehearsalScopeError as exc:
        assert "SYNTHETIC_POSTGRES_REQUIRED" in str(exc)
    else:
        raise AssertionError("package-surface rehearsal accepted missing Postgres")


def test_synthetic_package_surface_rehearsal_fails_closed_when_postgres_unreachable(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Configured but unavailable Postgres should produce a clear Slice71 blocker."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal
    from idis.persistence.db import reset_engines

    monkeypatch.setenv("IDIS_DATABASE_URL", "postgresql://slice71:secret@127.0.0.1:1/idis")
    reset_engines()

    try:
        rehearsal.build_bounded_synthetic_package_surface_rehearsal(
            dataset_root=Path("datasets/gdbs_full"),
            env={},
            max_cases=1,
            allow_synthetic_api_upload=True,
            allow_synthetic_execution=True,
            allow_synthetic_package_surface_verification=True,
            object_store_base_dir=tmp_path / "objects",
        )
    except rehearsal.SyntheticRehearsalScopeError as exc:
        assert "SYNTHETIC_POSTGRES_UNAVAILABLE" in str(exc)
    else:
        raise AssertionError("package-surface rehearsal accepted unavailable Postgres")
    finally:
        reset_engines()


def test_run_full_deliverables_marks_durable_export_when_db_and_object_store(
    monkeypatch: Any,
) -> None:
    """DELIVERABLES summary must honestly signal same-run durable package export."""
    from idis.api.routes import runs as runs_route

    class FakeObjectStore:
        backend_name = "filesystem"

    class FakeRepository:
        def __init__(self, conn: object, tenant_id: str) -> None:
            self.conn = conn
            self.tenant_id = tenant_id

    class FakeProductBundleExporter:
        def __init__(
            self,
            *,
            deliverables_repo: object,
            object_store: object,
            object_store_backend: str,
        ) -> None:
            self.deliverables_repo = deliverables_repo
            self.object_store = object_store
            self.object_store_backend = object_store_backend

        def export_bundle(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "artifact_count": 14,
                "manifest_uri": "object:filesystem:0123456789abcdef:fedcba9876543210",
                "deliverable_ids": ["11111111-1111-4111-8111-111111111111"],
                "types": [MANIFEST_ARTIFACT_TYPE],
            }

    monkeypatch.setattr(
        "idis.deliverables.product_bundle.ProductBundleExporter",
        FakeProductBundleExporter,
    )
    monkeypatch.setattr(
        "idis.persistence.repositories.deliverables.PostgresDeliverablesRepository",
        FakeRepository,
    )

    summary = runs_route._run_full_deliverables(
        run_id="22222222-2222-4222-8222-222222222222",
        tenant_id=str(pg_helpers.TENANT_ID),
        deal_id=str(pg_helpers.DEAL_ID),
        analysis_bundle=_make_bundle(),
        analysis_context=_make_context(),
        scorecard=_make_scorecard(),
        db_conn=object(),
        object_store=FakeObjectStore(),
    )

    assert summary["durable_export"] is True
    assert summary["artifact_count"] == 14
    assert summary["types"] == [MANIFEST_ARTIFACT_TYPE]


def test_verified_same_run_package_surface_rejects_mismatched_manifest_identity() -> None:
    """Same-run verification must include manifest run/deal identity."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    client = _FakePackageClient(
        deal_id="33333333-3333-4333-8333-333333333333",
        run_id="22222222-2222-4222-8222-222222222222",
        manifest_overrides={"run_id": "99999999-9999-4999-8999-999999999999"},
    )

    result = rehearsal._verified_same_run_package_surface_status(
        client=client,
        deal_id=client.deal_id,
        run_id=client.run_id,
    )

    assert result["verified"] is False
    assert result["status"] == "failed_safe"
    assert result["manifest_identity_mismatch"] is True


def test_verified_same_run_package_surface_requires_manifest_deliverable_ids() -> None:
    """Downloads must be tied to the exact manifest deliverable IDs, not just type/format."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    client = _FakePackageClient(
        deal_id="33333333-3333-4333-8333-333333333333",
        run_id="22222222-2222-4222-8222-222222222222",
        row_deliverable_id="44444444-4444-4444-8444-444444444444",
        manifest_deliverable_id="55555555-5555-4555-8555-555555555555",
    )

    result = rehearsal._verified_same_run_package_surface_status(
        client=client,
        deal_id=client.deal_id,
        run_id=client.run_id,
    )

    assert result["verified"] is False
    assert result["status"] == "failed_safe"
    assert result["missing_manifest_deliverable_ids"] == ["55555555-5555-4555-8555-555555555555"]


def test_verified_same_run_package_surface_rejects_missing_manifest_deliverable_id() -> None:
    """Manifest artifacts must identify exact public deliverable rows."""
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    client = _FakePackageClient(
        deal_id="33333333-3333-4333-8333-333333333333",
        run_id="22222222-2222-4222-8222-222222222222",
        manifest_deliverable_id=None,
    )

    result = rehearsal._verified_same_run_package_surface_status(
        client=client,
        deal_id=client.deal_id,
        run_id=client.run_id,
    )

    assert result["verified"] is False
    assert result["status"] == "failed_safe"
    assert result["missing_manifest_deliverable_id_artifacts"] == ["qa_brief:JSON"]


def test_synthetic_package_surface_rehearsal_proves_same_run_package_surfaces(
    app_engine: Any,
    clean_tables: None,
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """One public GDBS upload/run must create same-run package rows and downloads."""
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        build_bounded_synthetic_package_surface_rehearsal,
    )

    original_database_url = os.environ["IDIS_DATABASE_URL"]
    original_admin_url = os.environ["IDIS_DATABASE_ADMIN_URL"]
    object_store_base_dir = tmp_path / "objects"
    monkeypatch.setenv("IDIS_OBJECT_STORE_BACKEND", "s3")
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(tmp_path / "ambient-objects"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret-slice71")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret-slice71")
    monkeypatch.setenv("IDIS_ENABLE_VECTOR_SEARCH", "1")

    report = build_bounded_synthetic_package_surface_rehearsal(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_api_upload=True,
        allow_synthetic_execution=True,
        allow_synthetic_package_surface_verification=True,
        object_store_base_dir=object_store_base_dir,
    )
    serialized = json.dumps(report, sort_keys=True)

    assert report["synthetic_rehearsal_only"] is True
    assert report["real_example_not_run"] is True
    assert report["not_vc_ready"] is True
    assert report["strict_global_may_proceed"] is False
    assert report["approval_evidence"] is False
    assert report["api_upload_rehearsal"]["uploaded_case_count"] == 1
    assert report["api_upload_rehearsal"]["uploaded_document_count"] == 2

    execution = report["full_execution_rehearsal"]
    run_id = execution["run_id"]
    assert execution["run_status"] == "SUCCEEDED"
    assert execution["completed_step_count"] == len(FULL_STEPS) == 28
    assert StepName.DELIVERABLES.value in execution["completed_step_names"]
    assert execution["deliverables_step_summary"]["durable_export"] is True
    assert execution["deliverables_step_summary"]["artifact_count"] >= 1

    package = report["package_surface_verification"]
    assert report["package_surface_status"] == "verified"
    assert package["status"] == "verified"
    assert package["verified"] is True
    assert package["same_run_id"] == run_id
    assert package["listed_deliverable_count_for_run"] == package["manifest_artifact_count"] + 1
    assert package["manifest_http_status_code"] == 200
    assert package["manifest_run_id"] == run_id
    assert package["manifest_artifact_count"] >= 1
    assert package["downloaded_artifact_count"] >= 1
    assert package["download_sha256_mismatches"] == []
    assert package["content_type_mismatches"] == []
    assert package["missing_listed_artifacts"] == []
    assert package["download_failures"] == []
    assert MANIFEST_ARTIFACT_TYPE in package["listed_types"]
    downloaded = package["downloaded_artifacts"][0]
    assert downloaded["sha256"] == downloaded["manifest_sha256"]
    assert downloaded["content_type"] == resolve_content_type(
        downloaded["type"],
        downloaded["format"],
    )

    assert os.environ["IDIS_DATABASE_URL"] == original_database_url
    assert os.environ["IDIS_DATABASE_ADMIN_URL"] == original_admin_url
    assert os.environ["IDIS_OBJECT_STORE_BACKEND"] == "s3"
    assert os.environ["IDIS_OBJECT_STORE_BASE_DIR"] == str(tmp_path / "ambient-objects")
    assert os.environ["ANTHROPIC_API_KEY"] == "anthropic-secret-slice71"
    assert os.environ["OPENAI_API_KEY"] == "openai-secret-slice71"
    assert os.environ["IDIS_ENABLE_VECTOR_SEARCH"] == "1"

    assert report["real_example_not_run"] is True
    assert report["real_example_gate_cleared"] is False
    assert "vc-ready" not in serialized.lower()
    assert "object_key" not in serialized
    assert "local_path" not in serialized
    assert "raw_text" not in serialized
    assert "prompt_transcript" not in serialized
    assert "embedding" not in serialized
    assert "vector" not in serialized.lower()
    assert "secret" not in serialized.lower()
    assert str(object_store_base_dir) not in serialized
    assert "anthropic" not in serialized.lower()
    assert "openai" not in serialized.lower()


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        body: dict[str, Any] | None = None,
        content: bytes = b"",
        content_type: str = "application/json",
    ) -> None:
        self.status_code = status_code
        self._body = body or {}
        self.content = content
        self.headers = {"content-type": content_type}

    def json(self) -> dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"unexpected HTTP status in fake client: {self.status_code}")


class _FakePackageClient:
    def __init__(
        self,
        *,
        deal_id: str,
        run_id: str,
        row_deliverable_id: str = "44444444-4444-4444-8444-444444444444",
        manifest_deliverable_id: str | None = "44444444-4444-4444-8444-444444444444",
        manifest_overrides: dict[str, Any] | None = None,
    ) -> None:
        self.deal_id = deal_id
        self.run_id = run_id
        self.row_deliverable_id = row_deliverable_id
        self.manifest_deliverable_id = manifest_deliverable_id
        self.content = b'{"ok": true}'
        self.sha256 = hashlib.sha256(self.content).hexdigest()
        self.manifest_overrides = manifest_overrides or {}

    def get(self, url: str, **_kwargs: Any) -> _FakeResponse:
        if url == f"/v1/deals/{self.deal_id}/deliverables":
            return _FakeResponse(
                body={
                    "items": [
                        {
                            "deliverable_id": self.row_deliverable_id,
                            "run_id": self.run_id,
                            "deliverable_type": "qa_brief",
                            "format": "JSON",
                        },
                        {
                            "deliverable_id": "66666666-6666-4666-8666-666666666666",
                            "run_id": self.run_id,
                            "deliverable_type": MANIFEST_ARTIFACT_TYPE,
                            "format": "JSON",
                        },
                    ]
                }
            )
        if url == f"/v1/deals/{self.deal_id}/runs/{self.run_id}/product-bundle/manifest":
            artifact = {
                "type": "qa_brief",
                "format": "JSON",
                "sha256": self.sha256,
            }
            if self.manifest_deliverable_id is not None:
                artifact["deliverable_id"] = self.manifest_deliverable_id
            manifest = {
                "deal_id": self.deal_id,
                "run_id": self.run_id,
                "artifact_count": 1,
                "artifacts": [artifact],
            }
            manifest.update(self.manifest_overrides)
            return _FakeResponse(body=manifest)
        if url == f"/v1/deliverables/{self.row_deliverable_id}/content":
            return _FakeResponse(
                content=self.content,
                content_type="application/json",
            )
        return _FakeResponse(status_code=404)

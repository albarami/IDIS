"""Slice 64 final package download and manifest review tests."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.deliverables import _IN_MEMORY_DELIVERABLES, clear_deliverables_store
from idis.audit.sink import InMemoryAuditSink
from idis.deliverables.artifact_catalog import (
    MANIFEST_ARTIFACT_TYPE,
    build_product_bundle_object_key,
    resolve_object_key,
)
from idis.deliverables.generator import DeliverablesGenerator
from idis.deliverables.manifest_review import sanitize_product_bundle_manifest
from idis.deliverables.product_bundle import ProductBundleExporter
from idis.persistence.repositories.deliverables import deterministic_deliverable_row_id
from idis.services.runs.strict_full_live import build_strict_full_live_readiness_report
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.abac_seed import seed_deal_access
from tests.test_deliverables_generator import (
    _TIMESTAMP,
    _make_bundle,
    _make_context,
    _make_scorecard,
)
from tests.test_slice59_product_export_bundle import RecordingDeliverablesRepository

TENANT_ID = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"
DEAL_ID = "44444444-4444-4444-4444-444444444444"
API_KEY = "slice64-api-key"


class _ApiKeys:
    @staticmethod
    def config() -> dict[str, dict[str, str | list[str]]]:
        return {
            API_KEY: {
                "tenant_id": TENANT_ID,
                "actor_id": "actor-slice64",
                "name": "Slice64",
                "timezone": "UTC",
                "data_region": "me-south-1",
                "roles": ["ANALYST"],
            },
            "other-tenant-key": {
                "tenant_id": OTHER_TENANT_ID,
                "actor_id": "actor-other",
                "name": "Other",
                "timezone": "UTC",
                "data_region": "me-south-1",
                "roles": ["ANALYST"],
            },
        }


@pytest.fixture
def object_store(tmp_path: Path) -> FilesystemObjectStore:
    """Filesystem object store rooted in a temp directory."""
    return FilesystemObjectStore(base_dir=str(tmp_path / "objects"))


@pytest.fixture
def exported_bundle(
    object_store: FilesystemObjectStore,
) -> tuple[RecordingDeliverablesRepository, dict[str, Any]]:
    """Export a minimal product bundle into object storage and repository rows."""
    repository = RecordingDeliverablesRepository()
    bundle = DeliverablesGenerator(audit_sink=InMemoryAuditSink()).generate(
        ctx=_make_context(),
        bundle=_make_bundle(),
        scorecard=_make_scorecard(),
        deal_name="Slice64 Co",
        generated_at=_TIMESTAMP,
        deliverable_id_prefix="del-slice64",
    )
    exporter = ProductBundleExporter(
        deliverables_repo=repository,
        object_store=object_store,
        object_store_backend=object_store.backend_name,
    )
    summary = exporter.export_bundle(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        bundle=bundle,
        analysis_context=_make_context(),
        scorecard=_make_scorecard(),
        export_timestamp=_TIMESTAMP,
    )
    return repository, summary


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    exported_bundle: tuple[RecordingDeliverablesRepository, dict[str, Any]],
) -> TestClient:
    """Test client with object-store env configured."""
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_ApiKeys.config()))
    monkeypatch.setenv("IDIS_OBJECT_STORE_BACKEND", "filesystem")
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(tmp_path / "objects"))
    clear_deals_store()
    clear_deliverables_store()
    seed_deal_access(TENANT_ID, DEAL_ID, "actor-slice64")
    _IN_MEMORY_DELIVERABLES.clear()
    repository, _summary = exported_bundle
    for row in repository.rows:
        _IN_MEMORY_DELIVERABLES[row["deliverable_id"]] = {
            **row,
            "created_at": "2026-05-25T00:00:00Z",
        }
    app = create_app(audit_sink=InMemoryAuditSink(), service_region="me-south-1")
    return TestClient(app)


def test_resolve_object_key_matches_product_bundle_exporter_conventions() -> None:
    """Artifact resolver must share ProductBundleExporter object-key layout."""
    assert resolve_object_key(RUN_ID, "screening_snapshot", "PDF") == (
        build_product_bundle_object_key(RUN_ID, "screening_snapshot.pdf")
    )
    assert resolve_object_key(RUN_ID, MANIFEST_ARTIFACT_TYPE, "JSON") == (
        build_product_bundle_object_key(RUN_ID, "manifest.json")
    )
    assert resolve_object_key(RUN_ID, "unknown_type", "PDF") is None


def test_sanitize_product_bundle_manifest_strips_object_key_and_paths() -> None:
    """Manifest review must remove storage internals and path-like values."""
    sanitized = sanitize_product_bundle_manifest(
        {
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "run_id": RUN_ID,
            "artifact_count": 1,
            "artifacts": [
                {
                    "type": "ic_memo",
                    "format": "PDF",
                    "sha256": "a" * 64,
                    "size_bytes": 10,
                    "content_type": "application/pdf",
                    "object_key": f"runs/{RUN_ID}/product_bundle/ic_memo.pdf",
                    "uri": "object:filesystem:0123456789abcdef:fedcba9876543210",
                    "deliverable_id": str(uuid.uuid4()),
                    "local_path": "C:\\Projects\\secret.pdf",
                }
            ],
        }
    )

    artifact = sanitized["artifacts"][0]
    assert "object_key" not in artifact
    assert "local_path" not in artifact
    assert artifact["type"] == "ic_memo"
    assert artifact["uri"].startswith("object:filesystem:")


def _memo_pdf_row(repository: RecordingDeliverablesRepository) -> dict[str, Any]:
    return next(
        row
        for row in repository.rows
        if row["deliverable_type"] == "ic_memo" and row["format"] == "PDF"
    )


def test_download_completed_deliverable_streams_bytes(
    client: TestClient,
    exported_bundle: tuple[RecordingDeliverablesRepository, dict[str, Any]],
) -> None:
    """Download route serves COMPLETED artifact bytes with headers preserved."""
    repository, _summary = exported_bundle
    memo_row = _memo_pdf_row(repository)

    response = client.get(
        f"/v1/deliverables/{memo_row['deliverable_id']}/content",
        headers={"X-IDIS-API-Key": API_KEY},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert "attachment" in response.headers.get("content-disposition", "").lower()
    assert "ic_memo.pdf" in response.headers.get("content-disposition", "")
    assert len(response.content) > 0
    assert response.content.startswith(b"%PDF")


def test_download_rejects_queued_or_cross_tenant(
    client: TestClient,
    exported_bundle: tuple[RecordingDeliverablesRepository, dict[str, Any]],
) -> None:
    """Download is fail-closed for non-completed rows and other tenants."""
    repository, _summary = exported_bundle
    memo_row = _memo_pdf_row(repository)
    queued_id = str(uuid.uuid4())
    _IN_MEMORY_DELIVERABLES[queued_id] = {
        "deliverable_id": queued_id,
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "deliverable_type": "ic_memo",
        "format": "PDF",
        "status": "QUEUED",
        "uri": memo_row["uri"],
        "created_at": "2026-05-25T00:00:00Z",
    }

    queued_response = client.get(
        f"/v1/deliverables/{queued_id}/content",
        headers={"X-IDIS-API-Key": API_KEY},
    )
    assert queued_response.status_code == 404

    cross_tenant_response = client.get(
        f"/v1/deliverables/{memo_row['deliverable_id']}/content",
        headers={"X-IDIS-API-Key": "other-tenant-key"},
    )
    assert cross_tenant_response.status_code == 404


def test_download_rejects_unsafe_or_null_uri(client: TestClient) -> None:
    """Rows without safe public URIs must not be downloadable."""
    deliverable_id = str(uuid.uuid4())
    _IN_MEMORY_DELIVERABLES[deliverable_id] = {
        "deliverable_id": deliverable_id,
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "deliverable_type": "ic_memo",
        "format": "PDF",
        "status": "COMPLETED",
        "uri": "C:\\Projects\\IDIS\\secret.pdf",
        "created_at": "2026-05-25T00:00:00Z",
    }

    response = client.get(
        f"/v1/deliverables/{deliverable_id}/content",
        headers={"X-IDIS-API-Key": API_KEY},
    )

    assert response.status_code == 404


def test_manifest_review_returns_safe_json(
    client: TestClient,
    object_store: FilesystemObjectStore,
) -> None:
    """Manifest review returns sanitized manifest without storage internals."""
    manifest_body = {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "generated_at": _TIMESTAMP,
        "artifact_count": 1,
        "artifacts": [
            {
                "type": "ic_memo",
                "format": "PDF",
                "sha256": "b" * 64,
                "size_bytes": 4,
                "content_type": "application/pdf",
                "object_key": build_product_bundle_object_key(RUN_ID, "ic_memo.pdf"),
                "uri": "object:filesystem:0123456789abcdef:fedcba9876543210",
                "deliverable_id": str(uuid.uuid4()),
            }
        ],
    }
    object_store.put(
        tenant_id=TENANT_ID,
        key=build_product_bundle_object_key(RUN_ID, "manifest.json"),
        data=json.dumps(manifest_body, sort_keys=True).encode("utf-8"),
        content_type="application/json",
    )
    manifest_id = deterministic_deliverable_row_id(
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        deliverable_type=MANIFEST_ARTIFACT_TYPE,
        format_="JSON",
    )
    _IN_MEMORY_DELIVERABLES[manifest_id] = {
        "deliverable_id": manifest_id,
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "deliverable_type": MANIFEST_ARTIFACT_TYPE,
        "format": "JSON",
        "status": "COMPLETED",
        "uri": "object:filesystem:0123456789abcdef:fedcba9876543210",
        "created_at": "2026-05-25T00:00:00Z",
    }

    response = client.get(
        f"/v1/deals/{DEAL_ID}/runs/{RUN_ID}/product-bundle/manifest",
        headers={"X-IDIS-API-Key": API_KEY},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == RUN_ID
    assert "object_key" not in json.dumps(body)
    assert body["artifacts"][0]["type"] == "ic_memo"


def _seed_manifest_for_review(
    object_store: FilesystemObjectStore,
    manifest_body: dict[str, Any],
) -> None:
    object_store.put(
        tenant_id=TENANT_ID,
        key=build_product_bundle_object_key(RUN_ID, "manifest.json"),
        data=json.dumps(manifest_body, sort_keys=True).encode("utf-8"),
        content_type="application/json",
    )
    manifest_id = deterministic_deliverable_row_id(
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        deliverable_type=MANIFEST_ARTIFACT_TYPE,
        format_="JSON",
    )
    _IN_MEMORY_DELIVERABLES[manifest_id] = {
        "deliverable_id": manifest_id,
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "deliverable_type": MANIFEST_ARTIFACT_TYPE,
        "format": "JSON",
        "status": "COMPLETED",
        "uri": "object:filesystem:0123456789abcdef:fedcba9876543210",
        "created_at": "2026-05-25T00:00:00Z",
    }


def test_sanitize_manifest_skips_non_dict_and_empty_artifact_entries() -> None:
    """Manifest sanitization drops invalid artifact entries before review."""
    sanitized = sanitize_product_bundle_manifest(
        {
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "run_id": RUN_ID,
            "artifact_count": 99,
            "artifacts": [
                "not-a-dict",
                {},
                {
                    "type": "ic_memo",
                    "format": "PDF",
                    "object_key": f"runs/{RUN_ID}/product_bundle/ic_memo.pdf",
                    "uri": "object:filesystem:0123456789abcdef:fedcba9876543210",
                },
                {
                    "object_key": "runs/secret/manifest.json",
                    "local_path": "C:\\Projects\\secret.pdf",
                },
            ],
        }
    )

    assert sanitized["artifacts"] == [
        {
            "type": "ic_memo",
            "format": "PDF",
            "uri": "object:filesystem:0123456789abcdef:fedcba9876543210",
        }
    ]
    assert sanitized["artifact_count"] == 1


def test_sanitize_manifest_artifact_count_matches_safe_artifact_list() -> None:
    """Corrupt stored artifact_count must not leak through review sanitization."""
    sanitized = sanitize_product_bundle_manifest(
        {
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "run_id": RUN_ID,
            "artifact_count": "not-an-int",
            "artifacts": [
                {
                    "type": "qa_brief",
                    "format": "JSON",
                    "uri": "object:filesystem:0123456789abcdef:fedcba9876543210",
                },
                {
                    "type": "run_summary",
                    "format": "JSON",
                    "uri": "object:filesystem:0123456789abcdef:0123456789abcdef",
                },
            ],
        }
    )

    assert sanitized["artifact_count"] == 2
    assert len(sanitized["artifacts"]) == sanitized["artifact_count"]


def test_manifest_review_corrupt_artifact_count_does_not_500(
    client: TestClient,
    object_store: FilesystemObjectStore,
) -> None:
    """Manifest review must fail closed without server errors on corrupt counts."""
    _seed_manifest_for_review(
        object_store,
        {
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "run_id": RUN_ID,
            "generated_at": _TIMESTAMP,
            "artifact_count": "not-an-int",
            "artifacts": [
                {
                    "type": "ic_memo",
                    "format": "PDF",
                    "sha256": "b" * 64,
                    "size_bytes": 4,
                    "content_type": "application/pdf",
                    "object_key": build_product_bundle_object_key(RUN_ID, "ic_memo.pdf"),
                    "uri": "object:filesystem:0123456789abcdef:fedcba9876543210",
                    "deliverable_id": str(uuid.uuid4()),
                }
            ],
        },
    )

    response = client.get(
        f"/v1/deals/{DEAL_ID}/runs/{RUN_ID}/product-bundle/manifest",
        headers={"X-IDIS-API-Key": API_KEY},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["artifact_count"] == 1
    assert len(body["artifacts"]) == 1
    assert body["artifacts"][0]["type"] == "ic_memo"


def test_manifest_review_requires_completed_manifest_row(client: TestClient) -> None:
    """Manifest review is unavailable when the manifest row is not completed."""
    missing_run_id = str(uuid.uuid4())
    response = client.get(
        f"/v1/deals/{DEAL_ID}/runs/{missing_run_id}/product-bundle/manifest",
        headers={"X-IDIS-API-Key": API_KEY},
    )

    assert response.status_code == 404


def test_api_list_includes_format_and_run_id(
    client: TestClient,
    exported_bundle: tuple[RecordingDeliverablesRepository, dict[str, Any]],
) -> None:
    """List API exposes run_id and format for final package grouping."""
    repository, _summary = exported_bundle
    memo_row = _memo_pdf_row(repository)

    response = client.get(
        f"/v1/deals/{DEAL_ID}/deliverables",
        headers={"X-IDIS-API-Key": API_KEY},
    )

    assert response.status_code == 200
    items = response.json()["items"]
    matched = next(item for item in items if item["deliverable_id"] == memo_row["deliverable_id"])
    assert matched["format"] == "PDF"
    assert matched["run_id"] == RUN_ID


def test_strict_ui_api_download_clears_when_code_path_wired(tmp_path: Path) -> None:
    """Strict inventory clears UI/API download when routes, resolver, store, and UI are wired."""
    report = build_strict_full_live_readiness_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://app@db/idis",
            "IDIS_API_KEYS": "configured",
            "IDIS_OBJECT_STORE_BACKEND": "filesystem",
            "IDIS_OBJECT_STORE_BASE_DIR": str(tmp_path / "objects"),
        }
    )
    inventory = {item.component_name: item for item in report.component_inventory}
    ui_download = inventory["UI/API download"]

    assert ui_download.full_wired is True
    assert ui_download.output_visible is True
    assert ui_download.health_check_status == "healthy"
    assert ui_download.blocker == ""


def test_ui_api_download_code_path_wired_returns_false_when_import_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Import failures during code-path proof must fail closed."""
    import importlib

    from idis.services.runs.strict_full_live import _ui_api_download_code_path_wired

    original_import_module = importlib.import_module

    def fail_import_module(name: str, *args: object, **kwargs: object) -> object:
        if name == "idis.deliverables.artifact_resolver":
            raise ImportError("module unavailable test")
        return original_import_module(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fail_import_module)
    assert _ui_api_download_code_path_wired() is False


def test_strict_readiness_report_survives_ui_download_inspection_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Readiness report must remain constructible when UI download inspection fails."""
    import inspect

    def raise_oserror(*_args: object, **_kwargs: object) -> str:
        raise OSError("source unavailable test")

    monkeypatch.setattr(inspect, "getsource", raise_oserror)
    report = build_strict_full_live_readiness_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://app@db/idis",
            "IDIS_OBJECT_STORE_BACKEND": "filesystem",
            "IDIS_OBJECT_STORE_BASE_DIR": str(tmp_path / "objects"),
        }
    )
    inventory = {item.component_name: item for item in report.component_inventory}
    assert inventory["UI/API download"].full_wired is False
    assert report.may_proceed is False

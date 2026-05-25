"""Slice 59 durable product export bundle tests."""

from __future__ import annotations

import json
import uuid
import zipfile
from functools import partial
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.deliverables import _IN_MEMORY_DELIVERABLES, clear_deliverables_store
from idis.api.routes.runs import _safe_public_run_summary_dict
from idis.audit.sink import InMemoryAuditSink
from idis.deliverables.generator import DeliverablesGenerator
from idis.services.runs.steps import build_run_context
from idis.services.runs.strict_full_live import build_strict_full_live_readiness_report
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.test_deliverables_generator import (
    _TIMESTAMP,
    _make_bundle,
    _make_context,
    _make_scorecard,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
RUN_ID = "22222222-2222-2222-2222-222222222222"
DEAL_ID = "33333333-3333-3333-3333-333333333333"


class RecordingDeliverablesRepository:
    """Record completed deliverable rows written by the product exporter."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def create_completed(
        self,
        *,
        deliverable_id: str,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        deliverable_type: str,
        format_: str,
        uri: str,
    ) -> dict[str, Any]:
        row = {
            "deliverable_id": deliverable_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "deliverable_type": deliverable_type,
            "format": format_,
            "status": "COMPLETED",
            "uri": uri,
        }
        self.rows.append(row)
        return row


def _make_deliverables_bundle() -> Any:
    return DeliverablesGenerator(audit_sink=InMemoryAuditSink()).generate(
        ctx=_make_context(),
        bundle=_make_bundle(),
        scorecard=_make_scorecard(),
        deal_name="Acme Corp",
        generated_at=_TIMESTAMP,
        deliverable_id_prefix="del-slice59",
    )


def _make_leaky_deliverables_bundle(local_path: str) -> Any:
    bundle = _make_deliverables_bundle()
    leaky_item = bundle.qa_brief.items[0].model_copy(
        update={
            "question": (
                f"raw_text C:\\Projects\\private .local_reports/secret "
                f"confidential marker {local_path}"
            ),
            "rationale": "/tmp/slice59-secret/report.json /Users/alice/secret/report.json",
        }
    )
    preserved_item = bundle.qa_brief.items[1].model_copy(
        update={
            "question": "Q: legitimate question",
            "rationale": "/Users/alice/secret/report.json",
        }
    )
    leaky_qa_brief = bundle.qa_brief.model_copy(
        update={"items": [leaky_item, preserved_item, *bundle.qa_brief.items[2:]]}
    )
    return bundle.model_copy(update={"qa_brief": leaky_qa_brief})


def _make_binary_leaky_deliverables_bundle(local_path: str) -> Any:
    bundle = _make_deliverables_bundle()
    leaky_text = (
        f"raw_text C:\\Projects\\private .local_reports/secret "
        f"/tmp/slice59-secret/report.json /Users/alice/secret/report.json "
        f"confidential marker {local_path}"
    )
    preserved_text = "Q: legitimate question"

    snapshot_fact = bundle.screening_snapshot.summary_section.facts[0].model_copy(
        update={"text": leaky_text}
    )
    snapshot_section = bundle.screening_snapshot.summary_section.model_copy(
        update={
            "facts": [snapshot_fact, *bundle.screening_snapshot.summary_section.facts[1:]],
            "narrative": preserved_text,
        }
    )
    screening_snapshot = bundle.screening_snapshot.model_copy(
        update={"summary_section": snapshot_section}
    )

    memo_fact = bundle.ic_memo.executive_summary.facts[0].model_copy(update={"text": leaky_text})
    memo_section = bundle.ic_memo.executive_summary.model_copy(
        update={
            "facts": [memo_fact, *bundle.ic_memo.executive_summary.facts[1:]],
            "narrative": preserved_text,
        }
    )
    ic_memo = bundle.ic_memo.model_copy(update={"executive_summary": memo_section})
    return bundle.model_copy(update={"screening_snapshot": screening_snapshot, "ic_memo": ic_memo})


def test_deterministic_deliverable_row_id_is_stable_and_scoped() -> None:
    from idis.persistence.repositories.deliverables import deterministic_deliverable_row_id

    expected = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{TENANT_ID}:{RUN_ID}:ic_memo:PDF"))
    base = deterministic_deliverable_row_id(
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        deliverable_type="ic_memo",
        format_="PDF",
    )

    assert base == expected
    assert base == deterministic_deliverable_row_id(
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        deliverable_type="ic_memo",
        format_="PDF",
    )
    assert base != deterministic_deliverable_row_id(
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        deliverable_type="ic_memo",
        format_="DOCX",
    )
    assert base != deterministic_deliverable_row_id(
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        deliverable_type="screening_snapshot",
        format_="PDF",
    )
    assert base != deterministic_deliverable_row_id(
        tenant_id=TENANT_ID,
        run_id=DEAL_ID,
        deliverable_type="ic_memo",
        format_="PDF",
    )
    assert base != deterministic_deliverable_row_id(
        tenant_id=DEAL_ID,
        run_id=RUN_ID,
        deliverable_type="ic_memo",
        format_="PDF",
    )


def test_product_bundle_export_persists_rows_artifacts_and_manifest(tmp_path: Path) -> None:
    from idis.deliverables.product_bundle import ProductBundleExporter

    repository = RecordingDeliverablesRepository()
    object_store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    exporter = ProductBundleExporter(
        deliverables_repo=repository,
        object_store=object_store,
        object_store_backend="filesystem",
    )

    summary = exporter.export_bundle(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        bundle=_make_deliverables_bundle(),
        analysis_context=_make_context(),
        scorecard=_make_scorecard(),
        export_timestamp=_TIMESTAMP,
    )

    assert summary["artifact_count"] == 14
    assert summary["manifest_uri"].startswith("object:filesystem:")
    assert "product_bundle_manifest" in summary["types"]
    assert len(repository.rows) == 14
    manifest_rows = [
        row for row in repository.rows if row["deliverable_type"] == "product_bundle_manifest"
    ]
    assert len(manifest_rows) == 1
    assert manifest_rows[0]["format"] == "JSON"
    assert manifest_rows[0]["status"] == "COMPLETED"

    manifest = object_store.get(
        tenant_id=TENANT_ID,
        key=f"runs/{RUN_ID}/product_bundle/manifest.json",
    )
    manifest_body = json.loads(manifest.body.decode("utf-8"))
    assert manifest_body["artifact_count"] == 13
    assert {artifact["type"] for artifact in manifest_body["artifacts"]} == {
        "screening_snapshot",
        "ic_memo",
        "truth_dashboard",
        "qa_brief",
        "executive_summary",
        "commercial_diligence",
        "financial_diligence",
        "risk_register",
        "layer2_ic_challenge",
        "evidence_index",
        "run_summary",
    }
    for artifact in manifest_body["artifacts"]:
        assert artifact["sha256"]
        assert artifact["size_bytes"] > 0
        assert artifact["uri"].startswith("object:filesystem:")
        assert ".local_reports" not in artifact["uri"]


def test_deliverables_step_summary_exposes_only_safe_manifest_uri_and_numeric_count() -> None:
    public = _safe_public_run_summary_dict(
        {
            "artifact_count": 13,
            "manifest_uri": "object:filesystem:0123456789abcdef:fedcba9876543210",
            "deliverable_ids": ["11111111-1111-1111-1111-111111111111"],
            "types": ["product_bundle_manifest"],
            "local_path": "C:\\Projects\\secret",
            "raw_text": "confidential private text",
        }
    )

    assert public == {
        "artifact_count": 13,
        "manifest_uri": "object:filesystem:0123456789abcdef:fedcba9876543210",
        "deliverable_ids": ["11111111-1111-1111-1111-111111111111"],
        "types": ["product_bundle_manifest"],
    }


def test_deliverables_step_summary_strips_unsafe_manifest_uri_and_string_count() -> None:
    unsafe_values = [
        "C:\\Projects\\IDIS\\manifest.json",
        ".local_reports/manifest.json",
        "https://example.com/manifest.json",
        "object:filesystem:manifest-safe",
        "sk_live_secret_like_value",
    ]

    for unsafe_manifest_uri in unsafe_values:
        public = _safe_public_run_summary_dict(
            {
                "artifact_count": "13",
                "manifest_uri": unsafe_manifest_uri,
                "types": ["product_bundle_manifest"],
            }
        )
        assert public == {"types": ["product_bundle_manifest"]}


def test_build_run_context_injects_durable_export_dependencies(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    db_conn = object()
    monkeypatch.setenv("IDIS_OBJECT_STORE_BACKEND", "filesystem")
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(tmp_path / "objects"))

    ctx = build_run_context(
        db_conn=db_conn,
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=[],
        audit_sink=object(),
    )

    assert isinstance(ctx.deliverables_fn, partial)
    assert ctx.deliverables_fn.keywords["db_conn"] is db_conn
    assert ctx.deliverables_fn.keywords["object_store"].backend_name == "filesystem"


def test_build_run_context_ignores_unsupported_product_export_backend(
    monkeypatch: Any,
) -> None:
    db_conn = object()
    monkeypatch.setenv("IDIS_OBJECT_STORE_BACKEND", "s3")
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", "ignored")

    ctx = build_run_context(
        db_conn=db_conn,
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=[],
        audit_sink=object(),
    )

    assert isinstance(ctx.deliverables_fn, partial)
    assert ctx.deliverables_fn.keywords["db_conn"] is db_conn
    assert ctx.deliverables_fn.keywords["object_store"] is None


def test_strict_readiness_product_export_clears_only_with_durable_export_config(
    tmp_path: Path,
) -> None:
    blocked = build_strict_full_live_readiness_report(env={})
    blocked_inventory = {item.component_name: item for item in blocked.component_inventory}
    assert blocked_inventory["product export"].full_wired is False
    assert "product_export_bundle" in blocked.blocking_components

    configured = build_strict_full_live_readiness_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://app@db/idis",
            "IDIS_API_KEYS": "configured",
            "IDIS_OBJECT_STORE_BACKEND": "filesystem",
            "IDIS_OBJECT_STORE_BASE_DIR": str(tmp_path / "objects"),
        }
    )
    inventory = {item.component_name: item for item in configured.component_inventory}
    assert inventory["product export"].full_wired is True
    assert inventory["product export"].output_visible is True
    assert configured.component("product_export_bundle").may_proceed is True
    assert "product_export_bundle" not in configured.blocking_components

    unhealthy_base = tmp_path / "not-a-dir"
    unhealthy_base.write_text("not a directory", encoding="utf-8")
    unhealthy = build_strict_full_live_readiness_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://app@db/idis",
            "IDIS_API_KEYS": "configured",
            "IDIS_OBJECT_STORE_BACKEND": "filesystem",
            "IDIS_OBJECT_STORE_BASE_DIR": str(unhealthy_base),
        }
    )
    unhealthy_inventory = {item.component_name: item for item in unhealthy.component_inventory}
    assert unhealthy_inventory["product export"].output_visible is False
    assert unhealthy.component("product_export_bundle").may_proceed is False
    assert "product_export_bundle" in unhealthy.blocking_components


def test_api_list_returns_completed_deliverable_rows_with_safe_uris(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                "slice59-api-key": {
                    "tenant_id": TENANT_ID,
                    "actor_id": "actor-slice59",
                    "name": "Slice59",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST"],
                }
            }
        ),
    )
    clear_deals_store()
    clear_deliverables_store()
    app = create_app(audit_sink=InMemoryAuditSink(), service_region="me-south-1")
    client = TestClient(app)
    deal_response = client.post(
        "/v1/deals",
        headers={"X-IDIS-API-Key": "slice59-api-key"},
        json={"name": "Slice59 Deal", "company_name": "Slice59 Co"},
    )
    assert deal_response.status_code == 201
    deal_id = str(deal_response.json()["deal_id"])
    _IN_MEMORY_DELIVERABLES["unsafe-row"] = {
        "deliverable_id": "unsafe-row",
        "tenant_id": TENANT_ID,
        "deal_id": deal_id,
        "deliverable_type": "product_bundle_manifest",
        "format": "JSON",
        "status": "COMPLETED",
        "uri": "C:\\Projects\\IDIS\\.local_reports\\manifest.json",
        "created_at": "2026-05-25T00:00:00Z",
    }
    _IN_MEMORY_DELIVERABLES["manifest-row"] = {
        "deliverable_id": "manifest-row",
        "tenant_id": TENANT_ID,
        "deal_id": deal_id,
        "deliverable_type": "product_bundle_manifest",
        "format": "JSON",
        "status": "COMPLETED",
        "uri": "object:filesystem:0123456789abcdef:fedcba9876543210",
        "created_at": "2026-05-24T00:00:00Z",
    }

    response = client.get(
        f"/v1/deals/{deal_id}/deliverables",
        headers={"X-IDIS-API-Key": "slice59-api-key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == [
        {
            "deliverable_id": "unsafe-row",
            "deal_id": deal_id,
            "deliverable_type": "product_bundle_manifest",
            "status": "COMPLETED",
            "uri": None,
            "created_at": "2026-05-25T00:00:00Z",
            "run_id": None,
            "format": "JSON",
        },
        {
            "deliverable_id": "manifest-row",
            "deal_id": deal_id,
            "deliverable_type": "product_bundle_manifest",
            "status": "COMPLETED",
            "uri": "object:filesystem:0123456789abcdef:fedcba9876543210",
            "created_at": "2026-05-24T00:00:00Z",
            "run_id": None,
            "format": "JSON",
        },
    ]


def test_product_export_public_outputs_do_not_leak_private_paths_or_raw_text(
    tmp_path: Path,
) -> None:
    from idis.deliverables.product_bundle import ProductBundleExporter

    repository = RecordingDeliverablesRepository()
    object_store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    exporter = ProductBundleExporter(
        deliverables_repo=repository,
        object_store=object_store,
        object_store_backend="filesystem",
    )

    summary = exporter.export_bundle(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        bundle=_make_leaky_deliverables_bundle(str(tmp_path)),
        analysis_context=_make_context(),
        scorecard=_make_scorecard(),
        export_timestamp=_TIMESTAMP,
    )

    encoded = json.dumps({"summary": summary, "rows": repository.rows}, sort_keys=True)
    assert str(tmp_path) not in encoded
    assert ".local_reports" not in encoded
    assert "C:\\Projects" not in encoded
    assert "raw_text" not in encoded
    assert "confidential" not in encoded.lower()

    manifest = object_store.get(
        tenant_id=TENANT_ID,
        key=f"runs/{RUN_ID}/product_bundle/manifest.json",
    )
    manifest_body = json.loads(manifest.body.decode("utf-8"))
    stored_payloads = [manifest.body.decode("utf-8")]
    for artifact in manifest_body["artifacts"]:
        if artifact["format"] == "JSON":
            stored_payloads.append(
                object_store.get(
                    tenant_id=TENANT_ID,
                    key=artifact["object_key"],
                ).body.decode("utf-8")
            )
    stored_json = "\n".join(stored_payloads)
    forbidden_fragments = [
        ".local_reports",
        "C:\\Projects",
        "raw_text",
        "confidential marker",
        str(tmp_path),
        "/tmp/slice59-secret",
        "/Users/alice/secret",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in stored_json
    assert "Q: legitimate question" in stored_json


def test_product_export_binary_outputs_do_not_leak_private_paths_or_raw_text(
    tmp_path: Path,
) -> None:
    from idis.deliverables.product_bundle import ProductBundleExporter

    repository = RecordingDeliverablesRepository()
    object_store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    exporter = ProductBundleExporter(
        deliverables_repo=repository,
        object_store=object_store,
        object_store_backend="filesystem",
    )

    exporter.export_bundle(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        bundle=_make_binary_leaky_deliverables_bundle(str(tmp_path)),
        analysis_context=_make_context(),
        scorecard=_make_scorecard(),
        export_timestamp=_TIMESTAMP,
    )

    binary_payloads: list[str] = []
    for filename in (
        "screening_snapshot.pdf",
        "screening_snapshot.docx",
        "ic_memo.pdf",
        "ic_memo.docx",
    ):
        stored = object_store.get(
            tenant_id=TENANT_ID,
            key=f"runs/{RUN_ID}/product_bundle/{filename}",
        )
        if filename.endswith(".docx"):
            with zipfile.ZipFile(BytesIO(stored.body)) as archive:
                binary_payloads.extend(
                    archive.read(name).decode("utf-8", errors="ignore")
                    for name in archive.namelist()
                )
        else:
            binary_payloads.append(stored.body.decode("latin-1", errors="ignore"))

    binary_text = "\n".join(binary_payloads)
    forbidden_fragments = [
        ".local_reports",
        "C:\\Projects",
        "C:\\\\Projects",
        "/tmp/slice59-secret",
        "/Users/alice/secret",
        "raw_text",
        "confidential marker",
        str(tmp_path),
    ]
    for fragment in forbidden_fragments:
        assert fragment not in binary_text
    assert "Q: legitimate question" in binary_text

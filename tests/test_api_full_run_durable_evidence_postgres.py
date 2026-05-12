"""Postgres acceptance tests for selected FULL run durable evidence persistence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.documents import clear_document_store
from idis.audit.sink import InMemoryAuditSink
from idis.idempotency.store import SqliteIdempotencyStore
from idis.models.run_step import StepName, StepStatus
from tests import test_ingestion_persists_documents_postgres as pg_helpers
from tests.test_api_default_upload_ingestion_postgres import _configure_api_key
from tests.test_docx_parser import create_test_docx
from tests.test_pptx_parser import create_test_pptx
from tests.test_xlsx_parser import create_test_xlsx

pytest_plugins = ("tests.test_ingestion_persists_documents_postgres",)

FORBIDDEN_PUBLIC_TOKENS = (
    "content_b64",
    "raw_bytes",
    "raw_text",
    "parsed_text",
    "text_excerpt",
    "base64",
    "source_span_id",
    "source_span_ids",
    "slice26_nested_pdf_claim",
    "slice26_nested_xlsx_claim",
    "slice26_nested_docx_claim",
    "slice26_nested_pptx_claim",
)


def test_selected_full_run_persists_durable_claims_and_evidence_for_uploaded_room(
    app_engine: Any,
    clean_tables: None,
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Selected FULL runs over uploaded supported files must persist evidence rows."""
    clear_deals_store()
    clear_document_store()
    _configure_api_key(monkeypatch)
    storage_base_dir = str(tmp_path / "objects")
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", storage_base_dir)
    audit_sink = InMemoryAuditSink()

    app = create_app(
        audit_sink=audit_sink,
        idempotency_store=SqliteIdempotencyStore(in_memory=True),
    )
    client = TestClient(app, raise_server_exceptions=False)

    deal_response = client.post(
        "/v1/deals",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
        json={"name": "Slice 26 Durable Evidence Deal", "company_name": "Synthetic Co"},
    )
    assert deal_response.status_code == 201
    deal_id = str(deal_response.json()["deal_id"])

    fixture_files = _write_supported_fixture_tree(tmp_path / "room")
    document_ids: list[str] = []
    for path in fixture_files:
        upload_body = _upload_supported_file(client, deal_id=deal_id, path=path)
        document_ids.append(str(upload_body["document_id"]))
        _assert_safe_payload(upload_body, storage_base_dir=storage_base_dir)

    assert len(document_ids) == len(fixture_files)

    for unsupported in _unsupported_upload_examples():
        response = client.post(
            f"/v1/deals/{deal_id}/documents/upload",
            headers={
                "X-IDIS-API-Key": pg_helpers.API_KEY,
                "Content-Type": "application/octet-stream",
            },
            params={
                "filename": unsupported["filename"],
                "doc_type": "DATA_ROOM_FILE",
                "sha256": hashlib.sha256(unsupported["data"]).hexdigest(),
                "source_system": "slice26-unsupported",
            },
            content=unsupported["data"],
        )
        assert response.status_code == 400
        assert response.json()["code"] == "UNSUPPORTED_FORMAT"
        _assert_safe_payload(response.json(), storage_base_dir=storage_base_dir)

    run_response = client.post(
        f"/v1/deals/{deal_id}/runs",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
        json={
            "mode": "FULL",
            "source": {
                "type": "deal_documents",
                "document_ids": document_ids,
            },
        },
    )
    assert run_response.status_code == 202
    run_body = run_response.json()
    _assert_safe_payload(run_body, storage_base_dir=storage_base_dir)

    run_id = str(run_body["run_id"])
    status_response = client.get(
        f"/v1/runs/{run_id}",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
    )
    assert status_response.status_code == 200
    status_body = status_response.json()
    _assert_safe_payload(status_body, storage_base_dir=storage_base_dir)
    _assert_safe_payload(audit_sink.events, storage_base_dir=storage_base_dir)

    status_steps = {step["step_name"]: step for step in status_body["steps"]}
    assert status_steps[StepName.EXTRACT.value]["status"] == StepStatus.COMPLETED.value

    with app_engine.begin() as conn:
        conn.execute(
            text("SET LOCAL idis.tenant_id = :tenant_id"),
            {"tenant_id": str(pg_helpers.TENANT_ID)},
        )
        claim_rows = (
            conn.execute(
                text(
                    """
                SELECT claim_id, deal_id, primary_span_id
                FROM claims
                WHERE deal_id = :deal_id
                ORDER BY claim_id
                """
                ),
                {"deal_id": deal_id},
            )
            .mappings()
            .all()
        )
        evidence_rows = (
            conn.execute(
                text(
                    """
                SELECT evidence_id, deal_id, claim_id, source_span_id
                FROM evidence_items
                WHERE deal_id = :deal_id
                ORDER BY evidence_id
                """
                ),
                {"deal_id": deal_id},
            )
            .mappings()
            .all()
        )
        orphan_evidence_count = conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM evidence_items evidence
                LEFT JOIN claims claim ON claim.claim_id = evidence.claim_id
                WHERE evidence.deal_id = :deal_id
                  AND claim.claim_id IS NULL
                """
            ),
            {"deal_id": deal_id},
        ).scalar_one()
        evidence_source_document_count = conn.execute(
            text(
                """
                SELECT COUNT(DISTINCT spans.document_id)
                FROM evidence_items evidence
                JOIN document_spans spans ON spans.span_id = evidence.source_span_id
                WHERE evidence.deal_id = :deal_id
                  AND spans.document_id::text = ANY(:document_ids)
                """
            ),
            {"deal_id": deal_id, "document_ids": document_ids},
        ).scalar_one()

    assert claim_rows
    assert all(str(row["deal_id"]) == deal_id for row in claim_rows)
    assert all(row["primary_span_id"] is not None for row in claim_rows)
    assert evidence_rows
    assert orphan_evidence_count == 0
    assert {str(row["claim_id"]) for row in evidence_rows}.issubset(
        {str(row["claim_id"]) for row in claim_rows}
    )
    assert evidence_source_document_count >= 2


def _write_supported_fixture_tree(root: Path) -> list[Path]:
    finance = root / "Finance"
    legal = root / "Legal" / "Contracts"
    market = root / "Market"
    product = root / "Product"
    finance.mkdir(parents=True)
    legal.mkdir(parents=True)
    market.mkdir(parents=True)
    product.mkdir(parents=True)

    files = [
        finance / "slice26_metrics.pdf",
        finance / "slice26_model.xlsx",
        legal / "slice26_contract.docx",
        product / "slice26_deck.pptx",
    ]
    files[0].write_bytes(_pdf_bytes())
    files[1].write_bytes(
        create_test_xlsx({"P&L": [["Metric", "Value"], ["slice26_nested_xlsx_claim", 1000]]})
    )
    files[2].write_bytes(create_test_docx(["slice26_nested_docx_claim supports renewal."]))
    files[3].write_bytes(create_test_pptx([["slice26_nested_pptx_claim shows pipeline."]]))
    market.joinpath("ignored_unsupported.txt").write_text("not uploaded", encoding="utf-8")
    return files


def _pdf_bytes() -> bytes:
    from tests.test_pdf_parser import create_test_pdf

    return create_test_pdf(["slice26_nested_pdf_claim shows revenue."])


def _upload_supported_file(client: TestClient, *, deal_id: str, path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    response = client.post(
        f"/v1/deals/{deal_id}/documents/upload",
        headers={
            "X-IDIS-API-Key": pg_helpers.API_KEY,
            "Content-Type": "application/octet-stream",
        },
        params={
            "filename": path.name,
            "doc_type": "DATA_ROOM_FILE",
            "sha256": hashlib.sha256(data).hexdigest(),
            "source_system": "slice26-durable-evidence",
        },
        content=data,
    )
    assert response.status_code == 201
    return response.json()


def _unsupported_upload_examples() -> list[dict[str, Any]]:
    return [
        {"filename": "slice26-video.mp4", "data": b"\x00\x00\x00\x18ftypmp42"},
        {"filename": "slice26-image.png", "data": b"\x89PNG\r\n\x1a\n"},
        {"filename": "slice26-page.html", "data": b"<html><body>unsupported</body></html>"},
        {"filename": "slice26-note.txt", "data": b"unsupported plain text"},
    ]


def _assert_safe_payload(payload: object, *, storage_base_dir: str) -> None:
    encoded = json.dumps(payload, default=str)
    for forbidden in FORBIDDEN_PUBLIC_TOKENS:
        assert forbidden not in encoded
    assert storage_base_dir not in encoded

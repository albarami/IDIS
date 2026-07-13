"""No-DB public upload to selected run preflight contract tests."""

from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import idis.api.routes.runs as runs_route
from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.documents import clear_document_store
from idis.audit.sink import InMemoryAuditSink
from idis.idempotency.store import SqliteIdempotencyStore
from idis.services.runs.document_preflight import InMemoryRunDocumentPreflightService
from idis.services.runs.execution import RunExecutionResult
from tests.abac_seed import seed_deal_access

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False


API_KEY = "slice43-upload-run-key"
TENANT_ID = "11111111-1111-4111-8111-111111111111"


def test_uploaded_parsed_document_is_usable_in_selected_full_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, audit_sink = _client(tmp_path=tmp_path, monkeypatch=monkeypatch)
    deal_id = _create_deal(client)
    upload = _upload_pdf(client, deal_id=deal_id, filename="financial-model.pdf")
    selected_document_id = upload["document_id"]
    captured_preflight: list[dict[str, Any]] = []
    captured_eligible: list[dict[str, Any]] = []

    class CapturingRunExecutionService:
        def __init__(self, **kwargs: object) -> None:
            self.audit_sink = kwargs["audit_sink"]

        def execute(self, ctx: object) -> RunExecutionResult:
            corpus = list(ctx.preflight_corpus)  # type: ignore[attr-defined]
            result, eligible = InMemoryRunDocumentPreflightService().run(
                tenant_id=TENANT_ID,
                deal_id=deal_id,
                run_id=str(ctx.run_id),  # type: ignore[attr-defined]
                corpus=corpus,
            )
            captured_preflight.extend(corpus)
            captured_eligible.extend(eligible)
            status = "SUCCEEDED" if eligible else "FAILED"
            return RunExecutionResult(
                claimed=True,
                status=status,
                block_reason=None if eligible else "NO_USABLE_DOCUMENTS",
                finished_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )

    monkeypatch.setattr(runs_route, "RunExecutionService", CapturingRunExecutionService)

    run_response = client.post(
        f"/v1/deals/{deal_id}/runs",
        headers={"X-IDIS-API-Key": API_KEY},
        json={
            "mode": "FULL",
            "source": {
                "type": "deal_documents",
                "document_ids": [selected_document_id],
            },
        },
    )

    assert run_response.status_code == 202
    assert run_response.json()["block_reason"] != "NO_USABLE_DOCUMENTS"
    assert [doc["document_id"] for doc in captured_preflight] == [selected_document_id]
    assert [doc["document_id"] for doc in captured_eligible] == [selected_document_id]
    assert captured_preflight[0]["parse_status"] == "PARSED"
    assert captured_preflight[0]["metadata"]["parser_doc_type"] == "PDF"
    assert captured_preflight[0]["source_metadata"]["source_system"] == "api-upload"
    assert captured_preflight[0]["spans"]
    _assert_safe_public_payload(run_response.json())
    _assert_safe_public_payload(audit_sink.events)


def test_uploaded_document_ids_are_the_preflight_document_id_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _audit_sink = _client(tmp_path=tmp_path, monkeypatch=monkeypatch)
    deal_id = _create_deal(client)
    first_upload = _upload_pdf(client, deal_id=deal_id, filename="financial-model.pdf")
    second_upload = _upload_pdf(client, deal_id=deal_id, filename="cash-flow.pdf")
    selected_document_id = second_upload["document_id"]
    captured_ids: list[str] = []

    class CapturingRunExecutionService:
        def __init__(self, **kwargs: object) -> None:
            self.audit_sink = kwargs["audit_sink"]

        def execute(self, ctx: object) -> RunExecutionResult:
            captured_ids.extend(
                str(document["document_id"])
                for document in ctx.preflight_corpus  # type: ignore[attr-defined]
            )
            return RunExecutionResult(
                claimed=True,
                status="SUCCEEDED",
                finished_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )

    monkeypatch.setattr(runs_route, "RunExecutionService", CapturingRunExecutionService)

    response = client.post(
        f"/v1/deals/{deal_id}/runs",
        headers={"X-IDIS-API-Key": API_KEY},
        json={
            "mode": "FULL",
            "source": {
                "type": "deal_documents",
                "document_ids": [selected_document_id],
            },
        },
    )

    assert response.status_code == 202
    assert first_upload["document_id"] != second_upload["document_id"]
    assert captured_ids == [selected_document_id]


def _client(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, InMemoryAuditSink]:
    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                API_KEY: {
                    "tenant_id": TENANT_ID,
                    "actor_id": "slice43-actor",
                    "name": "Slice 43 Tenant",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST", "ADMIN"],
                }
            }
        ),
    )
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(tmp_path / "objects"))
    clear_deals_store()
    clear_document_store()
    audit_sink = InMemoryAuditSink()
    app = create_app(
        audit_sink=audit_sink,
        idempotency_store=SqliteIdempotencyStore(in_memory=True),
        service_region="me-south-1",
    )
    return TestClient(app, raise_server_exceptions=False), audit_sink


def _create_deal(client: TestClient) -> str:
    response = client.post(
        "/v1/deals",
        headers={"X-IDIS-API-Key": API_KEY},
        json={"name": "Slice 43 Deal", "company_name": "Synthetic Co"},
    )
    assert response.status_code == 201
    deal_id = str(response.json()["deal_id"])
    # Task 2.6: both tests drive this deal as the authorized actor (API_KEY -> "slice43-actor").
    # uploadDealDocument and startRun are ABAC deny-by-default, so seed the operating actor's
    # assignment through the app's default store before any deal-scoped call.
    seed_deal_access(TENANT_ID, deal_id, "slice43-actor")
    return deal_id


def _upload_pdf(client: TestClient, *, deal_id: str, filename: str) -> dict[str, Any]:
    pdf = _pdf_bytes()
    response = client.post(
        f"/v1/deals/{deal_id}/documents/upload",
        headers={
            "X-IDIS-API-Key": API_KEY,
            "Content-Type": "application/octet-stream",
        },
        params={
            "filename": filename,
            "doc_type": "DATA_ROOM_FILE",
            "sha256": hashlib.sha256(pdf).hexdigest(),
            "source_system": "api-upload",
        },
        content=pdf,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["parse_status"] == "PARSED"
    return body


def _pdf_bytes() -> bytes:
    if not REPORTLAB_AVAILABLE:
        pytest.skip("reportlab not installed")
    buffer = io.BytesIO()
    pdf_canvas = canvas.Canvas(buffer, pagesize=letter)
    pdf_canvas.drawString(72, 750, "financial model revenue schedule")
    pdf_canvas.drawString(72, 735, "cash flow and revenue support")
    pdf_canvas.save()
    return buffer.getvalue()


def _assert_safe_public_payload(payload: object) -> None:
    encoded = json.dumps(payload)
    for forbidden in (
        "financial model revenue schedule",
        "cash flow and revenue support",
        "text_excerpt",
        "spans",
        "raw_text",
        "parsed_text",
    ):
        assert forbidden not in encoded

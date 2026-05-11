"""Postgres-backed default app upload ingestion wiring tests."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.documents import clear_document_store
from idis.idempotency.store import SqliteIdempotencyStore
from idis.persistence.repositories.documents import PostgresDocumentsRepository
from tests import test_ingestion_persists_documents_postgres as pg_helpers

pytest_plugins = ("tests.test_ingestion_persists_documents_postgres",)

FORBIDDEN_PUBLIC_TOKENS = (
    "content_b64",
    "raw_bytes",
    "raw_text",
    "parsed_text",
    "text_excerpt",
    "spans",
    "Revenue was 10M",
    "EBITDA was 2M",
)


def _configure_api_key(monkeypatch: Any) -> None:
    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                pg_helpers.API_KEY: {
                    "tenant_id": str(pg_helpers.TENANT_ID),
                    "actor_id": pg_helpers.ACTOR_ID,
                    "name": "Tenant A",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST", "ADMIN"],
                }
            }
        ),
    )


def _assert_safe_public_payload(payload: object) -> None:
    encoded = json.dumps(payload)
    for forbidden in FORBIDDEN_PUBLIC_TOKENS:
        assert forbidden not in encoded


def test_create_app_default_upload_persists_parsed_document_without_ingestion_shim(
    app_engine: Any,
    clean_tables: None,
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    """Production-style create_app() should upload into durable parsed corpus rows."""
    clear_deals_store()
    clear_document_store()
    _configure_api_key(monkeypatch)
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(tmp_path / "objects"))

    app = create_app(idempotency_store=SqliteIdempotencyStore(in_memory=True))
    client = TestClient(app, raise_server_exceptions=False)

    deal_response = client.post(
        "/v1/deals",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
        json={"name": "Slice 24 Default Upload Deal", "company_name": "Synthetic Co"},
    )
    assert deal_response.status_code == 201
    deal_id = deal_response.json()["deal_id"]

    data = pg_helpers._pdf_bytes()
    upload_response = client.post(
        f"/v1/deals/{deal_id}/documents/upload",
        headers={
            "X-IDIS-API-Key": pg_helpers.API_KEY,
            "Content-Type": "application/octet-stream",
        },
        params={
            "filename": "slice24-default-upload.pdf",
            "doc_type": "DATA_ROOM_FILE",
            "sha256": hashlib.sha256(data).hexdigest(),
            "source_system": "slice24-default-upload",
        },
        content=data,
    )
    assert upload_response.status_code == 201
    upload_body = upload_response.json()
    durable_document_id = upload_body["document_id"]
    assert durable_document_id
    _assert_safe_public_payload(upload_body)

    with app_engine.begin() as conn:
        conn.execute(
            text("SET LOCAL idis.tenant_id = :tenant_id"),
            {"tenant_id": str(pg_helpers.TENANT_ID)},
        )
        repo = PostgresDocumentsRepository(conn, str(pg_helpers.TENANT_ID))
        documents = repo.list_documents_by_deal(deal_id)
        spans = repo.list_spans_by_document(
            deal_id=deal_id,
            document_id=durable_document_id,
        )

    assert [document["document_id"] for document in documents] == [durable_document_id]
    assert documents[0]["parse_status"] == "PARSED"
    assert len(spans) > 0

    list_response = client.get(
        f"/v1/deals/{deal_id}/documents",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
    )
    assert list_response.status_code == 200
    listed = list_response.json()
    assert [item["document_id"] for item in listed["items"]] == [durable_document_id]
    _assert_safe_public_payload(listed)

    get_response = client.get(
        f"/v1/deals/{deal_id}/documents/{durable_document_id}",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
    )
    assert get_response.status_code == 200
    summary = get_response.json()
    assert summary["document_id"] == durable_document_id
    _assert_safe_public_payload(summary)

"""Postgres API acceptance tests for long FULL run step-name persistence."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi.testclient import TestClient

from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.documents import clear_document_store
from idis.idempotency.store import SqliteIdempotencyStore
from idis.models.run_step import StepName
from tests import test_ingestion_persists_documents_postgres as pg_helpers
from tests.abac_seed import seed_deal_access
from tests.test_api_default_upload_ingestion_postgres import _configure_api_key

pytest_plugins = ("tests.test_ingestion_persists_documents_postgres",)

FORBIDDEN_PUBLIC_TOKENS = (
    "content_b64",
    "raw_bytes",
    "raw_text",
    "parsed_text",
    "text_excerpt",
    "base64",
    "spans",
    "Revenue was 10M",
    "EBITDA was 2M",
)


def _assert_safe_public_payload(payload: object, *, storage_base_dir: str) -> None:
    encoded = json.dumps(payload)
    for forbidden in FORBIDDEN_PUBLIC_TOKENS:
        assert forbidden not in encoded
    assert storage_base_dir not in encoded


def test_default_upload_selected_full_run_persists_long_step_name_without_sql_truncation(
    app_engine: Any,
    clean_tables: None,
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    """Selected FULL runs must persist the long methodology step name in Postgres."""
    clear_deals_store()
    clear_document_store()
    _configure_api_key(monkeypatch)
    storage_base_dir = str(tmp_path / "objects")
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", storage_base_dir)

    app = create_app(idempotency_store=SqliteIdempotencyStore(in_memory=True))
    client = TestClient(app, raise_server_exceptions=False)

    deal_response = client.post(
        "/v1/deals",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
        json={"name": "Slice 25 Step Name Deal", "company_name": "Synthetic Co"},
    )
    assert deal_response.status_code == 201
    deal_id = deal_response.json()["deal_id"]
    # Deal-scoped ABAC is deny-by-default: assign the creating actor to operate on its own deal.
    seed_deal_access(str(pg_helpers.TENANT_ID), deal_id, pg_helpers.ACTOR_ID)

    data = pg_helpers._pdf_bytes()
    upload_response = client.post(
        f"/v1/deals/{deal_id}/documents/upload",
        headers={
            "X-IDIS-API-Key": pg_helpers.API_KEY,
            "Content-Type": "application/octet-stream",
        },
        params={
            "filename": "slice25-selected-full.pdf",
            "doc_type": "DATA_ROOM_FILE",
            "sha256": hashlib.sha256(data).hexdigest(),
            "source_system": "slice25-step-name",
        },
        content=data,
    )
    assert upload_response.status_code == 201
    upload_body = upload_response.json()
    document_id = upload_body["document_id"]
    assert document_id
    _assert_safe_public_payload(upload_body, storage_base_dir=storage_base_dir)

    run_response = client.post(
        f"/v1/deals/{deal_id}/runs",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
        json={
            "mode": "FULL",
            "source": {
                "type": "deal_documents",
                "document_ids": [document_id],
            },
        },
    )

    assert run_response.status_code == 202
    run_body = run_response.json()
    assert run_body["status"] in {"SUCCEEDED", "FAILED"}
    assert "block_reason" in run_body
    assert any(
        step["step_name"] == StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN.value
        for step in run_body["steps"]
    )
    _assert_safe_public_payload(run_body, storage_base_dir=storage_base_dir)

    status_response = client.get(
        f"/v1/runs/{run_body['run_id']}",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
    )
    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["source"] == {
        "type": "deal_documents",
        "document_ids": [document_id],
    }
    assert any(
        step["step_name"] == StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN.value
        for step in status_body["steps"]
    )
    _assert_safe_public_payload(status_body, storage_base_dir=storage_base_dir)

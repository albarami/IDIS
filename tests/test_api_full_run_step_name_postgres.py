"""Postgres API acceptance tests for long FULL run step-name persistence."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.documents import clear_document_store
from idis.idempotency.store import SqliteIdempotencyStore
from idis.models.run_step import StepName
from tests import test_ingestion_persists_documents_postgres as pg_helpers
from tests.abac_seed import seed_deal_access
from tests.test_api_default_upload_ingestion_postgres import _configure_api_key
from tests.test_api_selected_multidocument_full_acceptance_postgres import (
    clear_frozen_methodology_rows,
    frozen_full_methodology_selection,
    public_fdd_fixture,
    upload_and_bind_preview_members,
)

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


@pytest.fixture
def methodology_cleanup(admin_engine: Any, clean_tables: None) -> Any:
    yield
    clear_frozen_methodology_rows(admin_engine)


def test_default_upload_selected_full_run_persists_long_step_name_without_sql_truncation(
    app_engine: Any,
    admin_engine: Any,
    clean_tables: None,
    methodology_cleanup: None,
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    """Selected FULL runs must persist the long methodology step name in Postgres."""
    clear_deals_store()
    clear_document_store()
    _configure_api_key(monkeypatch)
    storage_base_dir = str(tmp_path / "objects")
    monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", storage_base_dir)
    fdd_fixture = public_fdd_fixture(tmp_path)
    monkeypatch.setenv("IDIS_FDD_METHODOLOGY_PATH", str(fdd_fixture))

    app = create_app(idempotency_store=SqliteIdempotencyStore(in_memory=True))
    client = TestClient(app, raise_server_exceptions=False)

    deal_response = client.post(
        "/v1/deals",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
        json={
            "name": "Slice 25 Step Name Deal",
            "company_name": "Synthetic Co",
            "stage": "SERIES_A",
        },
    )
    assert deal_response.status_code == 201
    deal_id = deal_response.json()["deal_id"]
    # Deal-scoped ABAC is deny-by-default: assign the creating actor to operate on its own deal.
    seed_deal_access(str(pg_helpers.TENANT_ID), deal_id, pg_helpers.ACTOR_ID)

    data = pg_helpers._pdf_bytes()
    upload_bodies = upload_and_bind_preview_members(
        client,
        app_engine,
        deal_id=deal_id,
        members=[("pdf", data)],
    )
    upload_body = upload_bodies[0]
    document_id = upload_body["document_id"]
    assert document_id
    _assert_safe_public_payload(upload_body, storage_base_dir=storage_base_dir)
    methodology_selection = frozen_full_methodology_selection(
        app_engine,
        admin_engine,
        str(pg_helpers.TENANT_ID),
        fdd_fixture,
    )

    run_response = client.post(
        f"/v1/deals/{deal_id}/runs",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
        json={
            "mode": "FULL",
            "source": {
                "type": "deal_documents",
                "document_ids": [document_id],
            },
            "methodology_selection": methodology_selection,
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

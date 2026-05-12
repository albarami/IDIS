"""Slice 28 Postgres acceptance for durable Sanad auto-grade persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.documents import clear_document_store
from idis.audit.sink import InMemoryAuditSink
from idis.idempotency.store import SqliteIdempotencyStore
from idis.models.run_step import FULL_STEPS, StepName, StepStatus
from tests import test_ingestion_persists_documents_postgres as pg_helpers
from tests.test_api_default_upload_ingestion_postgres import _configure_api_key
from tests.test_api_selected_multidocument_full_acceptance_postgres import (
    KNOWN_SANAD_BLOCKER,
    _assert_safe_payload,
    _upload_supported_file,
    _write_supported_data_room_like_fixture,
)

pytest_plugins = ("tests.test_ingestion_persists_documents_postgres",)


def test_slice28_selected_full_run_persists_sanad_grades_without_known_blocker(
    app_engine: Any,
    clean_tables: None,
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Selected FULL runs must persist Sanad grades instead of blocking at GRADE."""
    clear_deals_store()
    clear_document_store()
    _configure_api_key(monkeypatch)
    storage_base_dir = str(tmp_path / "objects")
    room_root = tmp_path / "generated_nested_data_room"
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
        json={"name": "Slice 28 Sanad Auto Grade Deal", "company_name": "Synthetic Co"},
    )
    assert deal_response.status_code == 201
    deal_id = str(deal_response.json()["deal_id"])

    document_ids: list[str] = []
    for path in _write_supported_data_room_like_fixture(room_root):
        upload_body = _upload_supported_file(client, deal_id=deal_id, path=path)
        document_ids.append(str(upload_body["document_id"]))
        _assert_safe_payload(
            upload_body,
            storage_base_dir=storage_base_dir,
            room_root=room_root,
        )

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
    _assert_no_known_sanad_blocker(run_body)

    run_id = str(run_body["run_id"])
    status_response = client.get(
        f"/v1/runs/{run_id}",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
    )
    assert status_response.status_code == 200
    status_body = status_response.json()
    _assert_safe_payload(status_body, storage_base_dir=storage_base_dir, room_root=room_root)
    _assert_safe_payload(audit_sink.events, storage_base_dir=storage_base_dir, room_root=room_root)
    _assert_no_known_sanad_blocker(status_body)

    steps_by_name = {step["step_name"]: step for step in status_body["steps"]}
    assert steps_by_name[StepName.EXTRACT.value]["status"] == StepStatus.COMPLETED.value
    assert steps_by_name[StepName.GRADE.value]["status"] == StepStatus.COMPLETED.value

    with app_engine.begin() as conn:
        conn.execute(
            text("SET LOCAL idis.tenant_id = :tenant_id"),
            {"tenant_id": str(pg_helpers.TENANT_ID)},
        )
        durable = _load_durable_sanad_grade_state(
            conn,
            deal_id=deal_id,
            document_ids=document_ids,
            run_id=run_id,
        )

    assert durable["claim_count"] >= len(document_ids)
    assert durable["evidence_count"] >= len(document_ids)
    assert durable["sanad_count"] == durable["claim_count"]
    assert durable["graded_claim_count"] == durable["claim_count"]
    assert durable["linked_sanad_count"] == durable["claim_count"]
    assert durable["sanad_primary_evidence_count"] == durable["claim_count"]
    assert durable["evidence_source_document_ids"] == set(document_ids)
    assert KNOWN_SANAD_BLOCKER not in durable["step_error_codes"]
    _assert_terminal_state_contract(status_body, durable["persisted_steps"])


def _assert_no_known_sanad_blocker(payload: dict[str, Any]) -> None:
    assert payload.get("block_reason") != KNOWN_SANAD_BLOCKER
    for step in payload.get("steps", []):
        error = step.get("error")
        if isinstance(error, dict):
            assert error.get("code") != KNOWN_SANAD_BLOCKER


def _assert_terminal_state_contract(
    status_body: dict[str, Any],
    persisted_steps: list[dict[str, Any]],
) -> None:
    canonical_full_step_names = [step.value for step in FULL_STEPS]
    response_step_names = [step["step_name"] for step in status_body["steps"]]
    persisted_step_names = [str(step["step_name"]) for step in persisted_steps]

    assert status_body["status"] == "SUCCEEDED"
    assert status_body["block_reason"] is None
    assert response_step_names == canonical_full_step_names
    assert persisted_step_names == canonical_full_step_names
    assert all(
        step["status"] not in {StepStatus.FAILED.value, StepStatus.BLOCKED.value}
        for step in status_body["steps"]
    )
    assert all(
        step["status"] not in {StepStatus.FAILED.value, StepStatus.BLOCKED.value}
        and step["error_code"] is None
        for step in persisted_steps
    )


def _load_durable_sanad_grade_state(
    conn: Any,
    *,
    deal_id: str,
    document_ids: list[str],
    run_id: str,
) -> dict[str, Any]:
    claim_count = conn.execute(
        text("SELECT COUNT(*) FROM claims WHERE deal_id = :deal_id"),
        {"deal_id": deal_id},
    ).scalar_one()
    evidence_count = conn.execute(
        text("SELECT COUNT(*) FROM evidence_items WHERE deal_id = :deal_id"),
        {"deal_id": deal_id},
    ).scalar_one()
    sanad_count = conn.execute(
        text("SELECT COUNT(*) FROM sanads WHERE deal_id = :deal_id"),
        {"deal_id": deal_id},
    ).scalar_one()
    graded_claim_count = conn.execute(
        text(
            """
            SELECT COUNT(*)
            FROM claims
            WHERE deal_id = :deal_id
              AND sanad_id IS NOT NULL
              AND claim_grade IN ('A', 'B', 'C', 'D')
            """
        ),
        {"deal_id": deal_id},
    ).scalar_one()
    linked_sanad_count = conn.execute(
        text(
            """
            SELECT COUNT(*)
            FROM claims claim
            JOIN sanads sanad ON sanad.sanad_id = claim.sanad_id
            WHERE claim.deal_id = :deal_id
              AND sanad.claim_id = claim.claim_id
              AND sanad.computed->>'grade' = claim.claim_grade
            """
        ),
        {"deal_id": deal_id},
    ).scalar_one()
    sanad_primary_evidence_count = conn.execute(
        text(
            """
            SELECT COUNT(*)
            FROM sanads sanad
            JOIN evidence_items evidence
              ON evidence.evidence_id::text = sanad.primary_evidence_id
             AND evidence.claim_id = sanad.claim_id
            WHERE sanad.deal_id = :deal_id
            """
        ),
        {"deal_id": deal_id},
    ).scalar_one()
    evidence_source_document_ids = conn.execute(
        text(
            """
            SELECT DISTINCT spans.document_id
            FROM evidence_items evidence
            JOIN document_spans spans ON spans.span_id = evidence.source_span_id
            WHERE evidence.deal_id = :deal_id
              AND spans.document_id::text = ANY(:document_ids)
            """
        ),
        {"deal_id": deal_id, "document_ids": document_ids},
    ).scalars()
    step_error_codes = conn.execute(
        text(
            """
            SELECT error_code
            FROM run_steps
            WHERE run_id = :run_id
              AND error_code IS NOT NULL
            """
        ),
        {"run_id": run_id},
    ).scalars()
    persisted_steps = conn.execute(
        text(
            """
            SELECT step_name, step_order, status, error_code
            FROM run_steps
            WHERE run_id = :run_id
            ORDER BY step_order
            """
        ),
        {"run_id": run_id},
    ).mappings()

    return {
        "claim_count": claim_count,
        "evidence_count": evidence_count,
        "sanad_count": sanad_count,
        "graded_claim_count": graded_claim_count,
        "linked_sanad_count": linked_sanad_count,
        "sanad_primary_evidence_count": sanad_primary_evidence_count,
        "evidence_source_document_ids": {
            str(document_id) for document_id in evidence_source_document_ids
        },
        "step_error_codes": {str(error_code) for error_code in step_error_codes},
        "persisted_steps": [dict(row) for row in persisted_steps],
    }

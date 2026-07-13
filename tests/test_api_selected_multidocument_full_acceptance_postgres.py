"""Slice 27 Postgres acceptance for selected multi-document FULL runs."""

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
from idis.models.run_step import FULL_STEPS, STEP_ORDER, StepName, StepStatus
from tests import test_ingestion_persists_documents_postgres as pg_helpers
from tests.abac_seed import seed_deal_access
from tests.test_api_default_upload_ingestion_postgres import _configure_api_key
from tests.test_docx_parser import create_test_docx
from tests.test_pdf_parser import create_test_pdf
from tests.test_pptx_parser import create_test_pptx
from tests.test_xlsx_parser import create_test_xlsx

pytest_plugins = ("tests.test_ingestion_persists_documents_postgres",)

KNOWN_SANAD_BLOCKER = "SANAD_AUTO_GRADE_PERSISTENCE_BLOCKED"
SUPPORTED_CONTENT_TOKENS = (
    "slice27_pdf_confidential_revenue_marker",
    "slice27_xlsx_confidential_pipeline_marker",
    "slice27_docx_confidential_contract_marker",
    "slice27_pptx_confidential_market_marker",
)
UNSUPPORTED_CONTENT_TOKENS = (
    "slice27 unsupported binary video confidential marker",
    "slice27 unsupported binary image confidential marker",
    "slice27 unsupported csv confidential marker",
)
FORBIDDEN_PUBLIC_TOKENS = (
    "content_b64",
    "raw_bytes",
    "raw_text",
    "parsed_text",
    "text_excerpt",
    "base64",
    "source_span_id",
    "source_span_ids",
    "local_path",
    "object_path",
    *SUPPORTED_CONTENT_TOKENS,
    *UNSUPPORTED_CONTENT_TOKENS,
)


def test_slice27_selected_multidocument_full_acceptance_is_durable_and_safe(
    app_engine: Any,
    clean_tables: None,
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Selected FULL runs accept many uploaded docs and expose only safe contracts."""
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
        json={"name": "Slice 27 Multi Document Deal", "company_name": "Synthetic Co"},
    )
    assert deal_response.status_code == 201
    deal_id = str(deal_response.json()["deal_id"])
    # Deal-scoped ABAC is deny-by-default: assign the creating actor to operate on its own deal.
    seed_deal_access(str(pg_helpers.TENANT_ID), deal_id, pg_helpers.ACTOR_ID)

    supported_files = _write_supported_data_room_like_fixture(room_root)
    document_ids: list[str] = []
    for path in supported_files:
        upload_body = _upload_supported_file(client, deal_id=deal_id, path=path)
        document_ids.append(str(upload_body["document_id"]))
        _assert_safe_payload(
            upload_body,
            storage_base_dir=storage_base_dir,
            room_root=room_root,
        )

    assert len(document_ids) == len(supported_files)
    assert len(set(document_ids)) == len(document_ids)

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
                "source_system": "slice27-unsupported",
            },
            content=unsupported["data"],
        )
        assert response.status_code == 400
        assert response.json()["code"] == "UNSUPPORTED_FORMAT"
        _assert_safe_payload(
            response.json(),
            storage_base_dir=storage_base_dir,
            room_root=room_root,
        )

    with app_engine.begin() as conn:
        conn.execute(
            text("SET LOCAL idis.tenant_id = :tenant_id"),
            {"tenant_id": str(pg_helpers.TENANT_ID)},
        )
        unsupported_artifact_count = _count_unsupported_upload_side_effects(
            conn,
            deal_id=deal_id,
        )

    assert unsupported_artifact_count == 0

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
    _assert_safe_payload(run_body, storage_base_dir=storage_base_dir, room_root=room_root)
    _assert_clean_known_blocker_if_present(run_body)
    _assert_expected_terminal_state(run_body)

    run_id = str(run_body["run_id"])
    status_response = client.get(
        f"/v1/runs/{run_id}",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
    )
    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["mode"] == "FULL"
    assert status_body["source"] == {
        "type": "deal_documents",
        "document_ids": document_ids,
    }
    _assert_safe_payload(status_body, storage_base_dir=storage_base_dir, room_root=room_root)
    _assert_safe_payload(audit_sink.events, storage_base_dir=storage_base_dir, room_root=room_root)
    with app_engine.begin() as conn:
        conn.execute(
            text("SET LOCAL idis.tenant_id = :tenant_id"),
            {"tenant_id": str(pg_helpers.TENANT_ID)},
        )
        durable = _load_durable_slice27_counts(conn, deal_id=deal_id, document_ids=document_ids)
        persisted_steps = _load_persisted_steps(conn, run_id=run_id)

    assert durable["document_ids"] == set(document_ids)
    assert durable["artifact_count"] == len(document_ids)
    assert durable["span_document_ids"] == set(document_ids)
    assert durable["claim_count"] >= len(document_ids)
    assert durable["evidence_count"] >= len(document_ids)
    assert durable["claim_primary_document_ids"] == set(document_ids)
    assert durable["evidence_source_document_ids"] == set(document_ids)
    assert durable["orphan_evidence_count"] == 0

    _assert_persisted_steps_are_ordered_and_untruncated(persisted_steps, status_body["steps"])
    _assert_slice27_terminal_contract(status_body, persisted_steps)


def _write_supported_data_room_like_fixture(root: Path) -> list[Path]:
    finance = root / "01. Financials" / "Board Pack"
    legal = root / "02. Legal" / "Commercial Contracts"
    commercial = root / "03. Commercial" / "Pipeline"
    product = root / "04. Product" / "Market"
    finance.mkdir(parents=True)
    legal.mkdir(parents=True)
    commercial.mkdir(parents=True)
    product.mkdir(parents=True)

    files = [
        finance / "slice27_board_metrics.pdf",
        commercial / "slice27_pipeline_model.xlsx",
        legal / "slice27_customer_contract.docx",
        product / "slice27_market_update.pptx",
    ]
    files[0].write_bytes(
        create_test_pdf([f"{SUPPORTED_CONTENT_TOKENS[0]} states revenue was 11.1 million."])
    )
    files[1].write_bytes(
        create_test_xlsx(
            {"Pipeline": [["Metric", "Value"], [f"{SUPPORTED_CONTENT_TOKENS[1]} ARR", 1000]]}
        )
    )
    files[2].write_bytes(
        create_test_docx([f"{SUPPORTED_CONTENT_TOKENS[2]} confirms a signed renewal clause."])
    )
    files[3].write_bytes(
        create_test_pptx([[f"{SUPPORTED_CONTENT_TOKENS[3]} shows qualified pipeline growth."]])
    )
    return files


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
            "source_system": "slice27-multidocument-full",
        },
        content=data,
    )
    assert response.status_code == 201
    return response.json()


def _unsupported_upload_examples() -> list[dict[str, Any]]:
    return [
        {
            "filename": "slice27-board-recording.bin",
            "data": b"\x00\x00\x00\x18ftypmp42" + UNSUPPORTED_CONTENT_TOKENS[0].encode(),
        },
        {
            "filename": "slice27-warehouse-photo.bin",
            "data": b"\x89PNG\r\n\x1a\n" + UNSUPPORTED_CONTENT_TOKENS[1].encode(),
        },
        # Slice78: HTML/TXT are now canonical-supported (admitted); .csv stays unsupported.
        {
            "filename": "slice27-export.csv",
            "data": f"col1,col2\n{UNSUPPORTED_CONTENT_TOKENS[2]},1\n".encode(),
        },
    ]


def _count_unsupported_upload_side_effects(conn: Any, *, deal_id: str) -> int:
    return int(
        conn.execute(
            text(
                """
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM document_artifacts
                        WHERE deal_id = :deal_id
                          AND source_system = 'slice27-unsupported'
                    )
                    +
                    (
                        SELECT COUNT(*)
                        FROM documents
                        JOIN document_artifacts USING (doc_id)
                        WHERE documents.deal_id = :deal_id
                          AND document_artifacts.source_system = 'slice27-unsupported'
                    )
                    +
                    (
                        SELECT COUNT(*)
                        FROM document_spans
                        JOIN documents USING (document_id)
                        JOIN document_artifacts USING (doc_id)
                        WHERE document_spans.deal_id = :deal_id
                          AND document_artifacts.source_system = 'slice27-unsupported'
                    )
                """
            ),
            {"deal_id": deal_id},
        ).scalar_one()
    )


def _load_durable_slice27_counts(
    conn: Any,
    *,
    deal_id: str,
    document_ids: list[str],
) -> dict[str, Any]:
    document_rows = conn.execute(
        text(
            """
            SELECT document_id, doc_id
            FROM documents
            WHERE deal_id = :deal_id
              AND document_id::text = ANY(:document_ids)
              AND parse_status = 'PARSED'
            """
        ),
        {"deal_id": deal_id, "document_ids": document_ids},
    ).mappings()
    documents = list(document_rows)
    doc_ids = [str(row["doc_id"]) for row in documents]

    artifact_count = conn.execute(
        text(
            """
            SELECT COUNT(*)
            FROM document_artifacts
            WHERE deal_id = :deal_id
              AND doc_id::text = ANY(:doc_ids)
            """
        ),
        {"deal_id": deal_id, "doc_ids": doc_ids},
    ).scalar_one()
    span_document_ids = conn.execute(
        text(
            """
            SELECT DISTINCT document_id
            FROM document_spans
            WHERE deal_id = :deal_id
              AND document_id::text = ANY(:document_ids)
            """
        ),
        {"deal_id": deal_id, "document_ids": document_ids},
    ).scalars()
    claim_count = conn.execute(
        text("SELECT COUNT(*) FROM claims WHERE deal_id = :deal_id"),
        {"deal_id": deal_id},
    ).scalar_one()
    evidence_count = conn.execute(
        text("SELECT COUNT(*) FROM evidence_items WHERE deal_id = :deal_id"),
        {"deal_id": deal_id},
    ).scalar_one()
    claim_primary_document_ids = conn.execute(
        text(
            """
            SELECT DISTINCT spans.document_id
            FROM claims claim
            JOIN document_spans spans ON spans.span_id = claim.primary_span_id
            WHERE claim.deal_id = :deal_id
            """
        ),
        {"deal_id": deal_id},
    ).scalars()
    evidence_source_document_ids = conn.execute(
        text(
            """
            SELECT DISTINCT spans.document_id
            FROM evidence_items evidence
            JOIN document_spans spans ON spans.span_id = evidence.source_span_id
            WHERE evidence.deal_id = :deal_id
            """
        ),
        {"deal_id": deal_id},
    ).scalars()
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

    return {
        "document_ids": {str(row["document_id"]) for row in documents},
        "artifact_count": artifact_count,
        "span_document_ids": {str(document_id) for document_id in span_document_ids},
        "claim_count": claim_count,
        "evidence_count": evidence_count,
        "claim_primary_document_ids": {
            str(document_id) for document_id in claim_primary_document_ids
        },
        "evidence_source_document_ids": {
            str(document_id) for document_id in evidence_source_document_ids
        },
        "orphan_evidence_count": orphan_evidence_count,
    }


def _load_persisted_steps(conn: Any, *, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
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
    return [dict(row) for row in rows]


def _assert_persisted_steps_are_ordered_and_untruncated(
    persisted_steps: list[dict[str, Any]],
    response_steps: list[dict[str, Any]],
) -> None:
    persisted_names = [str(step["step_name"]) for step in persisted_steps]
    response_names = [str(step["step_name"]) for step in response_steps]
    assert persisted_names == response_names
    assert [step["step_order"] for step in persisted_steps] == sorted(
        step["step_order"] for step in persisted_steps
    )
    for name in persisted_names:
        canonical = StepName(name)
        assert name == canonical.value
        assert persisted_steps[persisted_names.index(name)]["step_order"] == STEP_ORDER[canonical]

    assert StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN.value in persisted_names


def _assert_slice27_terminal_contract(
    status_body: dict[str, Any],
    persisted_steps: list[dict[str, Any]],
) -> None:
    _assert_clean_known_blocker_if_present(status_body)
    _assert_expected_terminal_state(status_body)
    if status_body.get("block_reason") == KNOWN_SANAD_BLOCKER:
        _assert_known_sanad_blocker_contract(status_body, persisted_steps)
        return

    _assert_successful_full_contract(status_body, persisted_steps)


def _assert_known_sanad_blocker_contract(
    status_body: dict[str, Any],
    persisted_steps: list[dict[str, Any]],
) -> None:
    step_by_name = {step["step_name"]: step for step in status_body["steps"]}
    persisted_by_name = {str(step["step_name"]): step for step in persisted_steps}
    assert status_body["status"] == "FAILED"
    assert status_body["block_reason"] == KNOWN_SANAD_BLOCKER
    assert step_by_name[StepName.EXTRACT.value]["status"] == StepStatus.COMPLETED.value

    grade_step = step_by_name[StepName.GRADE.value]
    assert grade_step["status"] in {StepStatus.FAILED.value, StepStatus.BLOCKED.value}
    assert grade_step["error"]["code"] == KNOWN_SANAD_BLOCKER
    assert persisted_by_name[StepName.GRADE.value]["error_code"] == KNOWN_SANAD_BLOCKER

    grade_order = STEP_ORDER[StepName.GRADE]
    for step in status_body["steps"]:
        if STEP_ORDER[StepName(step["step_name"])] > grade_order:
            assert step["status"] != StepStatus.COMPLETED.value
    for step in persisted_steps:
        if STEP_ORDER[StepName(str(step["step_name"]))] > grade_order:
            assert step["status"] != StepStatus.COMPLETED.value


def _assert_successful_full_contract(
    status_body: dict[str, Any],
    persisted_steps: list[dict[str, Any]],
) -> None:
    canonical_full_step_names = [step.value for step in FULL_STEPS]
    response_step_names = [step["step_name"] for step in status_body["steps"]]
    persisted_step_names = [str(step["step_name"]) for step in persisted_steps]
    assert response_step_names == canonical_full_step_names
    assert persisted_step_names == canonical_full_step_names
    assert status_body["status"] == "SUCCEEDED"
    assert status_body.get("block_reason") is None
    assert all(
        step["status"] not in {StepStatus.FAILED.value, StepStatus.BLOCKED.value}
        for step in status_body["steps"]
    )
    assert all(
        step["status"] not in {StepStatus.FAILED.value, StepStatus.BLOCKED.value}
        for step in persisted_steps
    )


def _assert_clean_known_blocker_if_present(payload: dict[str, Any]) -> None:
    if payload.get("block_reason") != KNOWN_SANAD_BLOCKER:
        return

    assert payload["status"] == "FAILED"
    encoded = json.dumps(payload, default=str)
    assert "traceback" not in encoded.lower()
    assert "sqlalchemy" not in encoded.lower()


def _assert_expected_terminal_state(payload: dict[str, Any]) -> None:
    if payload.get("block_reason") == KNOWN_SANAD_BLOCKER:
        return

    assert payload["status"] == "SUCCEEDED"
    assert payload.get("block_reason") is None


def _assert_safe_payload(payload: object, *, storage_base_dir: str, room_root: Path) -> None:
    encoded = json.dumps(payload, default=str)
    for forbidden in FORBIDDEN_PUBLIC_TOKENS:
        assert forbidden not in encoded
    assert storage_base_dir not in encoded
    assert str(room_root) not in encoded

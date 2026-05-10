"""Integration tests proving ingestion persists parsed corpus to Postgres."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import uuid
from collections.abc import Callable, Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import idis.api.routes.runs as runs_route
from idis.api.auth import IDIS_API_KEYS_ENV, TenantContext
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.documents import clear_document_store
from idis.audit.sink import InMemoryAuditSink
from idis.compliance.byok import BYOKPolicyRegistry, configure_key
from idis.idempotency.store import SqliteIdempotencyStore
from idis.models.data_room_inventory_package_materialization import (
    RunScopedDataRoomInventoryFileRecord,
)
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.documents import PostgresDocumentsRepository
from idis.services.ingestion import IngestionContext, IngestionService
from idis.services.runs.data_room_ingestion_handoff import (
    InMemoryRunDataRoomIngestionHandoffService,
)
from idis.services.runs.data_room_inventory_package import (
    InMemoryRunDataRoomInventoryPackageService,
)
from idis.services.runs.execution import RunExecutionResult
from idis.services.runs.steps import load_document_preflight_corpus_for_deal
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore

if TYPE_CHECKING:
    from sqlalchemy import Engine

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")
DEAL_ID = UUID("33333333-3333-3333-3333-333333333333")
API_KEY = "test-key-tenant-a-ingestion-postgres"
ACTOR_ID = "actor-ingestion-postgres"


def _skip_or_fail_if_no_postgres() -> None:
    admin_url = os.environ.get(ADMIN_URL_ENV)
    app_url = os.environ.get(APP_URL_ENV)
    require_postgres = os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1"
    if not admin_url or not app_url:
        msg = f"PostgreSQL integration tests require {ADMIN_URL_ENV} and {APP_URL_ENV}"
        if require_postgres:
            pytest.fail(f"REQUIRED: {msg} (IDIS_REQUIRE_POSTGRES=1)")
        pytest.skip(msg)


@pytest.fixture(scope="module")
def admin_engine() -> Generator[Engine, None, None]:
    _skip_or_fail_if_no_postgres()
    from idis.persistence.db import get_admin_engine, reset_engines

    engine = get_admin_engine()
    yield engine
    reset_engines()


@pytest.fixture(scope="module")
def app_engine() -> Generator[Engine, None, None]:
    _skip_or_fail_if_no_postgres()
    from idis.persistence.db import get_app_engine, reset_engines

    engine = get_app_engine()
    yield engine
    reset_engines()


@pytest.fixture(scope="module")
def migrated_db(admin_engine: Engine) -> Generator[None, None, None]:
    from alembic import command
    from alembic.config import Config

    import idis.persistence.migrations as migrations_pkg

    config = Config()
    config.set_main_option("script_location", os.path.dirname(migrations_pkg.__file__))
    with admin_engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")

    yield

    with admin_engine.begin() as conn:
        config.attributes["connection"] = conn
        command.downgrade(config, "base")


@pytest.fixture
def clean_tables(admin_engine: Engine, migrated_db: None) -> Generator[None, None, None]:
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE run_steps, runs, document_spans, "
                "documents, document_artifacts, deals CASCADE"
            )
        )

    yield

    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE run_steps, runs, document_spans, "
                "documents, document_artifacts, deals CASCADE"
            )
        )


@pytest.fixture
def compliant_store() -> Generator[ComplianceEnforcedStore, None, None]:
    with tempfile.TemporaryDirectory(prefix="idis_test_ingestion_pg_") as tmpdir:
        store = FilesystemObjectStore(base_dir=Path(tmpdir))
        yield ComplianceEnforcedStore(inner_store=store)


def _create_deal(conn: object) -> None:
    set_tenant_local(conn, str(TENANT_ID))
    conn.execute(
        text(
            """
            INSERT INTO deals (deal_id, tenant_id, name, company_name, status, created_at)
            VALUES (:deal_id, :tenant_id, :name, :name, 'ACTIVE', :created_at)
            """
        ),
        {
            "deal_id": str(DEAL_ID),
            "tenant_id": str(TENANT_ID),
            "name": "Synthetic Ingestion Deal",
            "created_at": datetime.now(UTC),
        },
    )


def _pdf_bytes() -> bytes:
    from tests.test_pdf_parser import create_test_pdf

    return create_test_pdf(["Revenue was 10M.", "EBITDA was 2M."])


def _xlsx_bytes() -> bytes:
    from tests.test_xlsx_parser import create_test_xlsx

    return create_test_xlsx({"P&L": [["Metric", "Value"], ["Revenue", 10000000]]})


def _docx_bytes() -> bytes:
    from docx import Document

    doc = Document()
    doc.add_paragraph("ARR was 5M.")
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("filename", "media_type", "factory", "expected_doc_type"),
    [
        ("synthetic.pdf", "application/pdf", _pdf_bytes, "PDF"),
        (
            "synthetic.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            _xlsx_bytes,
            "XLSX",
        ),
        (
            "synthetic.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            _docx_bytes,
            "DOCX",
        ),
    ],
)
def test_ingestion_persists_successful_parse_to_postgres_documents_and_spans(
    app_engine: Engine,
    clean_tables: None,
    compliant_store: ComplianceEnforcedStore,
    filename: str,
    media_type: str,
    factory: Callable[[], bytes],
    expected_doc_type: str,
) -> None:
    data = factory()
    with app_engine.begin() as conn:
        _create_deal(conn)
        service = IngestionService(
            compliant_store=compliant_store,
            audit_sink=InMemoryAuditSink(),
            db_conn=conn,
        )

        result = service.ingest_bytes(
            ctx=IngestionContext(
                tenant_id=TENANT_ID,
                actor_id="synthetic-tester",
                request_id=f"req-{expected_doc_type}",
            ),
            deal_id=DEAL_ID,
            filename=filename,
            media_type=media_type,
            data=data,
            metadata={"source_system": "synthetic-test"},
        )

        repo = PostgresDocumentsRepository(conn, str(TENANT_ID))
        documents = repo.list_documents_by_deal(str(DEAL_ID))
        spans = repo.list_spans_by_document(
            deal_id=str(DEAL_ID),
            document_id=str(result.document_id),
        )

    assert result.success is True
    assert result.storage_uri is not None
    assert documents[0]["document_id"] == str(result.document_id)
    assert documents[0]["doc_id"] == str(result.artifact_id)
    assert documents[0]["doc_type"] == expected_doc_type
    assert documents[0]["parse_status"] == "PARSED"
    assert documents[0]["sha256"] == result.sha256
    assert len(spans) == result.span_count
    assert len(spans) > 0
    assert all(span["deal_id"] == str(DEAL_ID) for span in spans)
    assert all(span["content_hash"] for span in spans)


def test_data_room_handoff_persists_supported_inventory_files_to_postgres(
    app_engine: Engine,
    clean_tables: None,
    compliant_store: ComplianceEnforcedStore,
    tmp_path: Path,
) -> None:
    (tmp_path / "Finance").mkdir()
    (tmp_path / "Finance" / "Model.xlsx").write_bytes(_xlsx_bytes())
    (tmp_path / "Media").mkdir()
    (tmp_path / "Media" / "Demo.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    _inventory_result, packages, inventory_corpus = (
        InMemoryRunDataRoomInventoryPackageService().run(
            tenant_id=str(TENANT_ID),
            deal_id=str(DEAL_ID),
            run_id="slice-18-pg-run",
            root_path=tmp_path,
        )
    )
    package = packages[0]

    with app_engine.begin() as conn:
        _create_deal(conn)
        ingestion_service = IngestionService(
            compliant_store=compliant_store,
            audit_sink=InMemoryAuditSink(),
            db_conn=conn,
        )

        def existing_document_lookup(
            file_record: RunScopedDataRoomInventoryFileRecord,
        ) -> dict[str, object] | None:
            return PostgresDocumentsRepository(
                conn, str(TENANT_ID)
            ).get_document_by_inventory_file_id(
                deal_id=str(DEAL_ID),
                inventory_file_id=file_record.file_id,
            )

        def ingest_bytes(**kwargs: object) -> dict[str, object]:
            file_record = kwargs["file_record"]
            assert isinstance(file_record, RunScopedDataRoomInventoryFileRecord)
            result = ingestion_service.ingest_bytes(
                ctx=IngestionContext(
                    tenant_id=TENANT_ID,
                    actor_id="slice-18-handoff",
                    request_id="slice-18-handoff-request",
                    idempotency_key=f"data-room:{file_record.file_id}",
                ),
                deal_id=DEAL_ID,
                filename=str(file_record.relative_path),
                media_type=None,
                data=kwargs["data"],
                metadata=kwargs["metadata"],
                db_conn=conn,
            )
            return result.to_dict()

        result, _corpus = InMemoryRunDataRoomIngestionHandoffService().run(
            tenant_id=str(TENANT_ID),
            deal_id=str(DEAL_ID),
            run_id="slice-18-pg-run",
            root_path=tmp_path,
            inventory_package=package,
            inventory_corpus=inventory_corpus,
            ingest_bytes_fn=ingest_bytes,
            existing_document_lookup_fn=existing_document_lookup,
        )
        documents = PostgresDocumentsRepository(conn, str(TENANT_ID)).list_documents_by_deal(
            str(DEAL_ID),
            parsed_only=False,
        )
        preflight_corpus = load_document_preflight_corpus_for_deal(
            db_conn=conn,
            deal_id=str(DEAL_ID),
            tenant_id=str(TENANT_ID),
        )

    summary = result.to_run_step_summary()
    assert summary["handoff_status"] == "durable_ingested"
    assert summary["durable_ingested_file_count"] == 1
    assert documents[0]["source_metadata"]["inventory_file_id"] == package.files[0].file_id
    assert documents[0]["source_metadata"]["source_system"] == "data_room_inventory"
    assert len(preflight_corpus) == 1


def test_ingestion_persists_failed_parse_without_extraction_ready_spans(
    app_engine: Engine,
    clean_tables: None,
    compliant_store: ComplianceEnforcedStore,
) -> None:
    with app_engine.begin() as conn:
        _create_deal(conn)
        service = IngestionService(
            compliant_store=compliant_store,
            audit_sink=InMemoryAuditSink(),
            db_conn=conn,
        )

        result = service.ingest_bytes(
            ctx=IngestionContext(
                tenant_id=TENANT_ID,
                actor_id="synthetic-tester",
                request_id="req-failed-parse",
            ),
            deal_id=DEAL_ID,
            filename="unsupported.bin",
            media_type="application/octet-stream",
            data=b"not a supported document",
        )

        repo = PostgresDocumentsRepository(conn, str(TENANT_ID))
        documents = repo.list_documents_by_deal(str(DEAL_ID), parsed_only=False)
        parsed_documents = repo.list_documents_by_deal(str(DEAL_ID), parsed_only=True)
        spans = repo.list_spans_by_document(
            deal_id=str(DEAL_ID),
            document_id=str(result.document_id),
        )

    assert result.success is False
    assert documents[0]["parse_status"] == "FAILED"
    assert documents[0]["metadata"]["parse_error_codes"] == ["unsupported_format"]
    assert documents[0]["metadata"]["parse_warning_codes"] == []
    assert documents[0]["metadata"]["detected_format"] == "UNKNOWN"
    assert documents[0]["metadata"]["parser_doc_type"] == "UNKNOWN"
    assert "not a supported document" not in json.dumps(documents[0]["metadata"])
    assert parsed_documents == []
    assert spans == []


def test_oversized_pre_ingestion_validation_creates_no_corpus_row(
    app_engine: Engine,
    clean_tables: None,
    compliant_store: ComplianceEnforcedStore,
) -> None:
    """Slice 2 Option A: pre-ingestion MAX_BYTES rejection is outside preflight."""
    with app_engine.begin() as conn:
        _create_deal(conn)
        service = IngestionService(
            compliant_store=compliant_store,
            audit_sink=InMemoryAuditSink(),
            db_conn=conn,
            max_bytes=4,
        )

        result = service.ingest_bytes(
            ctx=IngestionContext(
                tenant_id=TENANT_ID,
                actor_id="synthetic-tester",
                request_id="req-too-large-before-persistence",
            ),
            deal_id=DEAL_ID,
            filename="oversized.pdf",
            media_type="application/pdf",
            data=b"too large",
        )

        repo = PostgresDocumentsRepository(conn, str(TENANT_ID))
        documents = repo.list_documents_by_deal(str(DEAL_ID), parsed_only=False)

    assert result.success is False
    assert result.artifact_id is None
    assert result.document_id is None
    assert documents == []


def test_api_ingestion_persists_corpus_and_api_run_loads_persisted_spans(
    app_engine: Engine,
    clean_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_deals_store()
    clear_document_store()
    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                API_KEY: {
                    "tenant_id": str(TENANT_ID),
                    "actor_id": ACTOR_ID,
                    "name": "Tenant A",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST", "ADMIN"],
                }
            }
        ),
    )

    with tempfile.TemporaryDirectory(prefix="idis_api_ingestion_pg_") as tmpdir:
        byok_registry = BYOKPolicyRegistry()
        audit_sink = InMemoryAuditSink()
        compliant_store = ComplianceEnforcedStore(
            inner_store=FilesystemObjectStore(base_dir=Path(tmpdir)),
            byok_registry=byok_registry,
        )
        ingestion_service = IngestionService(
            compliant_store=compliant_store,
            audit_sink=audit_sink,
        )
        app = create_app(
            audit_sink=audit_sink,
            idempotency_store=SqliteIdempotencyStore(in_memory=True),
            ingestion_service=ingestion_service,
        )
        client = TestClient(app, raise_server_exceptions=False)

        tenant_ctx = TenantContext(
            tenant_id=str(TENANT_ID),
            actor_id=ACTOR_ID,
            name="Tenant A",
            timezone="UTC",
            data_region="me-south-1",
        )
        configure_key(tenant_ctx, "api-ingestion-pg-key", audit_sink, registry=byok_registry)
        pdf_data = _pdf_bytes()
        storage_key = "synthetic/api-ingestion.pdf"
        compliant_store.put(tenant_ctx=tenant_ctx, key=storage_key, data=pdf_data)

        deal_resp = client.post(
            "/v1/deals",
            headers={"X-IDIS-API-Key": API_KEY},
            json={"name": "API Persisted Corpus Deal", "company_name": "Synthetic Co"},
        )
        assert deal_resp.status_code == 201
        deal_id = deal_resp.json()["deal_id"]

        create_resp = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": API_KEY, "Content-Type": "application/json"},
            json={
                "doc_type": "DATA_ROOM_FILE",
                "title": "api-ingestion.pdf",
                "uri": f"idis://{storage_key}",
                "sha256": hashlib.sha256(pdf_data).hexdigest(),
                "auto_ingest": False,
            },
        )
        assert create_resp.status_code == 201
        doc_id = create_resp.json()["doc_id"]

        ingest_resp = client.post(
            f"/v1/documents/{doc_id}/ingest",
            headers={"X-IDIS-API-Key": API_KEY, "Content-Type": "application/json"},
            json={},
        )
        assert ingest_resp.status_code == 202
        assert ingest_resp.json()["status"] == "SUCCEEDED"
        durable_document_id = ingest_resp.json()["document_id"]

        with app_engine.begin() as conn:
            repo = PostgresDocumentsRepository(conn, str(TENANT_ID))
            documents = repo.list_documents_by_deal(deal_id)
            spans = repo.list_spans_by_document(
                deal_id=deal_id,
                document_id=documents[0]["document_id"] if documents else str(uuid.uuid4()),
            )

        assert len(documents) == 1
        assert documents[0]["document_id"] == durable_document_id
        assert len(spans) > 0

        list_resp = client.get(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": API_KEY},
        )
        assert list_resp.status_code == 200
        listed_doc = list_resp.json()["items"][0]
        assert listed_doc["document_id"] == durable_document_id
        assert "content_b64" not in listed_doc
        assert "text_excerpt" not in listed_doc

        summary_resp = client.get(
            f"/v1/deals/{deal_id}/documents/{durable_document_id}",
            headers={"X-IDIS-API-Key": API_KEY},
        )
        assert summary_resp.status_code == 200
        summary = summary_resp.json()
        assert summary["document_id"] == durable_document_id
        assert "content_b64" not in summary
        assert "content_sha256" not in summary
        assert "text_excerpt" not in summary
        assert "spans" not in summary

        captured_documents: list[list[dict[str, object]]] = []
        captured_preflight_corpus: list[list[dict[str, object]]] = []

        class CapturingRunExecutionService:
            def __init__(self, **kwargs: object) -> None:
                self.audit_sink = kwargs["audit_sink"]

            def execute(self, ctx: object) -> RunExecutionResult:
                captured_documents.append(ctx.documents)  # type: ignore[attr-defined]
                captured_preflight_corpus.append(ctx.preflight_corpus)  # type: ignore[attr-defined]
                return RunExecutionResult(
                    claimed=True,
                    status="SUCCEEDED",
                    finished_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                )

        monkeypatch.setattr(runs_route, "RunExecutionService", CapturingRunExecutionService)

        run_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            headers={"X-IDIS-API-Key": API_KEY},
            json={
                "mode": "SNAPSHOT",
                "source": {
                    "type": "deal_documents",
                    "document_ids": [durable_document_id],
                },
            },
        )

        assert run_resp.status_code == 202
        assert captured_documents
        assert captured_documents[0][0]["document_id"] == documents[0]["document_id"]
        assert captured_documents[0][0]["spans"][0]["span_id"] == spans[0]["span_id"]
        assert captured_preflight_corpus[0][0]["document_id"] == documents[0]["document_id"]

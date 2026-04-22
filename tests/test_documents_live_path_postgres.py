"""Live-path wiring tests for document/artifact/span persistence (Sprint 1 Wave 2, Task 6).

Proves the ingestion/document routes and the SNAPSHOT run gather path now go
through the durable Postgres repositories from Task 5 when a DB connection is
available, and that durable state survives losing the in-process
IngestionService / _DocumentStore.

Follows the Postgres-gated pattern from tests/test_api_deals_postgres.py:
skips under no Postgres, fails hard under IDIS_REQUIRE_POSTGRES=1.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Generator
from datetime import datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.audit.sink import InMemoryAuditSink
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.documents import (
    DocumentArtifactsRepository,
    DocumentSpansRepository,
    DocumentsRepository,
)

if TYPE_CHECKING:
    from sqlalchemy import Engine


ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

TENANT_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
ACTOR_ID = "actor-task6"
API_KEY = "test-key-task6"


def _skip_or_fail_if_no_postgres() -> None:
    admin_url = os.environ.get(ADMIN_URL_ENV)
    app_url = os.environ.get(APP_URL_ENV)
    require_postgres = os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1"
    if not admin_url or not app_url:
        msg = f"Requires {ADMIN_URL_ENV} and {APP_URL_ENV}"
        if require_postgres:
            pytest.fail(f"REQUIRED: {msg} (IDIS_REQUIRE_POSTGRES=1)")
        else:
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
                "TRUNCATE document_spans, documents, document_artifacts, "
                "deals RESTART IDENTITY CASCADE"
            )
        )
    yield
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE document_spans, documents, document_artifacts, "
                "deals RESTART IDENTITY CASCADE"
            )
        )


def _insert_deal(admin_engine: Engine, *, deal_id: str) -> None:
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO deals (
                    deal_id, tenant_id, name, company_name, status,
                    stage, tags, created_at, updated_at
                ) VALUES (
                    :deal_id, :tenant_id, 'd', 'c', 'NEW',
                    NULL, CAST('[]' AS JSONB), now(), NULL
                )
                """
            ),
            {"deal_id": deal_id, "tenant_id": TENANT_ID},
        )


@pytest.fixture
def api_keys_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                API_KEY: {
                    "tenant_id": TENANT_ID,
                    "actor_id": ACTOR_ID,
                    "name": "Task6",
                    "timezone": "UTC",
                    "data_region": "us-east-1",
                    "roles": ["ADMIN"],
                }
            }
        ),
    )


@pytest.fixture
def client(api_keys_env: None, clean_tables: None) -> TestClient:
    app = create_app(audit_sink=InMemoryAuditSink(), service_region="us-east-1")
    return TestClient(app, raise_server_exceptions=False)


class TestCreateDealDocumentPersistsDurably:
    """POST /v1/deals/{id}/documents must write to document_artifacts."""

    def test_artifact_row_present_in_postgres(
        self, client: TestClient, admin_engine: Engine
    ) -> None:
        deal_id = str(uuid.uuid4())
        _insert_deal(admin_engine, deal_id=deal_id)

        response = client.post(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": API_KEY, "Content-Type": "application/json"},
            content=json.dumps(
                {
                    "doc_type": "PITCH_DECK",
                    "title": "Durable deck",
                    "source_system": "api",
                    "auto_ingest": False,
                }
            ),
        )
        assert response.status_code == 201, response.text
        doc_id = response.json()["doc_id"]

        with admin_engine.begin() as conn:
            row = conn.execute(
                text("SELECT doc_id, deal_id, title FROM document_artifacts WHERE doc_id = :d"),
                {"d": doc_id},
            ).fetchone()

        assert row is not None, "artifact row must be persisted in document_artifacts"
        assert str(row.doc_id) == doc_id
        assert str(row.deal_id) == deal_id
        assert row.title == "Durable deck"


class TestListDealDocumentsReadsDurableStore:
    """GET /v1/deals/{id}/documents must read from document_artifacts when
    db_conn is available, not from the in-memory _DocumentStore.
    """

    def test_artifact_inserted_directly_is_listed_by_api(
        self, client: TestClient, admin_engine: Engine, app_engine: Engine
    ) -> None:
        deal_id = str(uuid.uuid4())
        _insert_deal(admin_engine, deal_id=deal_id)
        doc_id = str(uuid.uuid4())

        # Insert directly via the app-role connection so it's a pure DB
        # presence, never touching _DocumentStore.
        with app_engine.begin() as conn:
            DocumentArtifactsRepository(conn, TENANT_ID).create(
                doc_id=doc_id,
                deal_id=deal_id,
                doc_type="FINANCIAL_MODEL",
                title="direct insert",
                source_system="backfill",
                version_id="v0",
            )

        # Clear the in-memory store to prove the API does NOT rely on it.
        from idis.api.routes.documents import clear_document_store

        clear_document_store()

        response = client.get(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": API_KEY},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        ids = {item["doc_id"] for item in body["items"]}
        assert doc_id in ids, (
            "durable artifact must be listed even after the in-memory store is cleared"
        )


class TestSnapshotGatherReadsDurableRepos:
    """runs.py `_gather_snapshot_documents` must source documents and spans
    from DocumentsRepository / DocumentSpansRepository when a DB connection
    is attached to request.state, and MUST NOT depend on
    ingestion_service._documents for the durable code path.
    """

    def test_gather_returns_docs_from_durable_repos(
        self, admin_engine: Engine, app_engine: Engine, clean_tables: None
    ) -> None:
        from idis.api.routes.runs import _gather_snapshot_documents

        deal_id = str(uuid.uuid4())
        _insert_deal(admin_engine, deal_id=deal_id)

        art_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        span_id = str(uuid.uuid4())

        with app_engine.begin() as conn:
            DocumentArtifactsRepository(conn, TENANT_ID).create(
                doc_id=art_id,
                deal_id=deal_id,
                doc_type="PITCH_DECK",
                title="t",
                source_system="s",
                version_id="1",
            )
            DocumentsRepository(conn, TENANT_ID).create(
                document_id=document_id,
                deal_id=deal_id,
                doc_id=art_id,
                doc_type="PDF",
                parse_status="PARSED",
            )
            DocumentSpansRepository(conn, TENANT_ID).create_many(
                [
                    {
                        "span_id": span_id,
                        "document_id": document_id,
                        "span_type": "PAGE_TEXT",
                        "locator": {"page": 1},
                        "text_excerpt": "hello",
                    }
                ]
            )

        # Build a stand-in request with db_conn on request.state and no
        # ingestion_service on app.state — the durable-path branch must be
        # taken regardless of whether a service is present.
        class _State:
            pass

        class _AppState:
            pass

        class _App:
            state = _AppState()

        class _Request:
            def __init__(self) -> None:
                self.state = _State()
                self.app = _App()

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_ID)
            req = _Request()
            req.state.db_conn = conn  # type: ignore[attr-defined]

            documents = _gather_snapshot_documents(req, TENANT_ID, deal_id)

        assert len(documents) == 1
        doc = documents[0]
        assert doc["document_id"] == document_id
        assert doc["doc_type"] == "PDF"
        assert len(doc["spans"]) == 1
        assert doc["spans"][0]["span_id"] == span_id
        assert doc["spans"][0]["text_excerpt"] == "hello"
        assert doc["spans"][0]["span_type"] == "PAGE_TEXT"

    def test_gather_does_not_touch_ingestion_service_when_db_conn_present(
        self, admin_engine: Engine, app_engine: Engine, clean_tables: None
    ) -> None:
        """Regression guard: with a db_conn, the durable path must not read
        ingestion_service._documents.
        """
        from idis.api.routes.runs import _gather_snapshot_documents

        deal_id = str(uuid.uuid4())
        _insert_deal(admin_engine, deal_id=deal_id)
        # Nothing in documents/spans → empty list from durable path.

        class _Sentinel:
            """Access to `_documents` must raise, not silently return a dict."""

            @property
            def _documents(self) -> dict:
                raise AssertionError(
                    "durable gather path must not read ingestion_service._documents"
                )

            def get_spans(self, *_args, **_kwargs):  # pragma: no cover - should not be called
                raise AssertionError(
                    "durable gather path must not call ingestion_service.get_spans"
                )

        class _State:
            pass

        class _AppState:
            ingestion_service = _Sentinel()

        class _App:
            state = _AppState()

        class _Request:
            def __init__(self) -> None:
                self.state = _State()
                self.app = _App()

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_ID)
            req = _Request()
            req.state.db_conn = conn  # type: ignore[attr-defined]

            documents = _gather_snapshot_documents(req, TENANT_ID, deal_id)

        assert documents == []


class TestIngestBytesPersistsViaDurableRepos:
    """IngestionService.ingest_bytes(..., db_conn=conn) must insert document
    artifacts / documents / spans rows into Postgres.
    """

    def test_small_pdf_round_trip(
        self, admin_engine: Engine, app_engine: Engine, clean_tables: None
    ) -> None:
        from uuid import UUID as _UUID

        from idis.services.ingestion import IngestionContext
        from idis.services.ingestion.service import IngestionService

        deal_id = str(uuid.uuid4())
        _insert_deal(admin_engine, deal_id=deal_id)

        # Minimal valid PDF body (single blank page). We don't care whether
        # the parser extracts zero or more spans; we care that the artifact
        # and document rows are written when db_conn is passed in.
        pdf_bytes = (
            b"%PDF-1.4\n"
            b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
            b"2 0 obj<< /Type /Pages /Count 1 /Kids [3 0 R] >>endobj\n"
            b"3 0 obj<< /Type /Page /Parent 2 0 R "
            b"/MediaBox [0 0 612 792] /Resources <<>> >>endobj\n"
            b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
            b"0000000053 00000 n \n0000000101 00000 n \n"
            b"trailer<< /Size 4 /Root 1 0 R >>\nstartxref\n176\n%%EOF\n"
        )

        from idis.storage.compliant_store import ComplianceEnforcedStore
        from idis.storage.filesystem_store import FilesystemObjectStore

        store = ComplianceEnforcedStore(
            inner_store=FilesystemObjectStore(base_dir="/tmp/idis-task6")
        )
        svc = IngestionService(compliant_store=store)
        ctx = IngestionContext(
            tenant_id=_UUID(TENANT_ID),
            actor_id=ACTOR_ID,
            request_id="req-task6",
        )

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_ID)
            result = svc.ingest_bytes(
                ctx,
                _UUID(deal_id),
                filename="tiny.pdf",
                media_type="application/pdf",
                data=pdf_bytes,
                db_conn=conn,
            )

        assert result.artifact_id is not None
        assert result.document_id is not None

        # Durable rows exist in Postgres, independent of the in-memory dicts.
        with admin_engine.begin() as conn:
            artifact_row = conn.execute(
                text("SELECT doc_id FROM document_artifacts WHERE doc_id = :d"),
                {"d": str(result.artifact_id)},
            ).fetchone()
            document_row = conn.execute(
                text("SELECT document_id FROM documents WHERE document_id = :d"),
                {"d": str(result.document_id)},
            ).fetchone()

        assert artifact_row is not None, "artifact row must be durable"
        assert document_row is not None, "document row must be durable"


class TestRestartAndLossRegressionGuard:
    """A fresh app with an empty _DocumentStore must still see artifacts
    persisted by a prior request — proving live persistence is not
    _DocumentStore-dependent.
    """

    def test_list_finds_durable_artifact_after_store_reset(
        self, api_keys_env: None, clean_tables: None, admin_engine: Engine, app_engine: Engine
    ) -> None:
        from idis.api.routes.documents import clear_document_store

        deal_id = str(uuid.uuid4())
        _insert_deal(admin_engine, deal_id=deal_id)

        # Simulate another process / a previous session writing a row.
        doc_id = str(uuid.uuid4())
        with app_engine.begin() as conn:
            DocumentArtifactsRepository(conn, TENANT_ID).create(
                doc_id=doc_id,
                deal_id=deal_id,
                doc_type="OTHER",
                title="from another session",
                source_system="sys",
                version_id="v1",
            )

        # Simulate "restart": brand-new app factory instance with a cleared
        # module-level _DocumentStore. The API must still return the row.
        clear_document_store()
        fresh_app = create_app(
            audit_sink=InMemoryAuditSink(), service_region="us-east-1"
        )
        client = TestClient(fresh_app, raise_server_exceptions=False)

        response = client.get(
            f"/v1/deals/{deal_id}/documents",
            headers={"X-IDIS-API-Key": API_KEY},
        )
        assert response.status_code == 200, response.text
        ids = {item["doc_id"] for item in response.json()["items"]}
        assert doc_id in ids, (
            "after in-memory store reset, durable Postgres row must still be visible"
        )


class TestRepositoryPackageExports:
    """Task 6 adds the durable document repos to the persistence.repositories
    package's public surface so live-path imports can use the canonical
    short form.
    """

    def test_repositories_package_reexports_task5_classes(self) -> None:
        import idis.persistence.repositories as repo_pkg

        for name in (
            "DocumentArtifactsRepository",
            "DocumentsRepository",
            "DocumentSpansRepository",
            "DocumentArtifactNotFoundError",
            "DocumentNotFoundError",
        ):
            assert hasattr(repo_pkg, name), f"{name} missing from package exports"
            assert name in getattr(repo_pkg, "__all__", ()), f"{name} missing from __all__"


_ = datetime  # keep datetime imported for potential future assertions

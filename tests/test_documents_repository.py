"""Postgres document repository tests for persisted ingestion corpus."""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.documents import PostgresDocumentsRepository
from idis.services.runs.steps import load_documents_for_deal

if TYPE_CHECKING:
    from sqlalchemy import Engine

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

TENANT_A_ID = "11111111-1111-1111-1111-111111111111"
TENANT_B_ID = "22222222-2222-2222-2222-222222222222"
DEAL_A_ID = "33333333-3333-3333-3333-333333333333"
DEAL_B_ID = "44444444-4444-4444-4444-444444444444"


class _RepositoryResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def fetchone(self) -> Any | None:
        return self._rows[0] if self._rows else None


class _RepositoryConnection:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(
        self,
        statement: object,
        params: dict[str, Any] | None = None,
    ) -> _RepositoryResult:
        self.calls.append((str(statement), dict(params or {})))
        return _RepositoryResult(self._rows)


def _joined_document_row(document_id: str, *, doc_id: str) -> Any:
    now = datetime(2026, 7, 20, tzinfo=UTC)
    return SimpleNamespace(
        _mapping={
            "document_id": document_id,
            "tenant_id": TENANT_A_ID,
            "deal_id": DEAL_A_ID,
            "doc_id": doc_id,
            "doc_type": "PDF",
            "artifact_doc_type": "DATA_ROOM_FILE",
            "parse_status": "PARSED",
            "document_metadata": {"page_count": 1},
            "artifact_metadata": {"size_bytes": 1},
            "document_name": "fictional-source.pdf",
            "sha256": "a" * 64,
            "uri": "durable/fictional-source",
            "created_at": now,
            "updated_at": now,
        }
    )


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
        conn.execute(text("TRUNCATE document_spans, documents, document_artifacts, deals CASCADE"))

    yield

    with admin_engine.begin() as conn:
        conn.execute(text("TRUNCATE document_spans, documents, document_artifacts, deals CASCADE"))


def _create_deal(conn: object, tenant_id: str, deal_id: str, name: str) -> None:
    set_tenant_local(conn, tenant_id)
    conn.execute(
        text(
            """
            INSERT INTO deals (deal_id, tenant_id, name, company_name, status, created_at)
            VALUES (:deal_id, :tenant_id, :name, :name, 'ACTIVE', :created_at)
            """
        ),
        {
            "deal_id": deal_id,
            "tenant_id": tenant_id,
            "name": name,
            "created_at": datetime.now(UTC),
        },
    )


def _seed_parsed_document(repo: PostgresDocumentsRepository, *, suffix: str) -> str:
    now = datetime.now(UTC)
    doc_id = f"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa{suffix}"
    document_id = f"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb{suffix}"
    repo.create_artifact(
        doc_id=doc_id,
        deal_id=DEAL_A_ID,
        doc_type="DATA_ROOM_FILE",
        title=f"source-{suffix}.pdf",
        source_system="synthetic-test",
        version_id=f"v-{suffix}",
        ingested_at=now,
        sha256=("a" * 62) + suffix,
        uri=f"deals/{DEAL_A_ID}/source-{suffix}.pdf",
        metadata={"source": "synthetic"},
    )
    repo.create_document(
        document_id=document_id,
        deal_id=DEAL_A_ID,
        doc_id=doc_id,
        doc_type="PDF",
        parse_status="PARSED",
        metadata={"page_count": 1, "name": f"source-{suffix}.pdf"},
    )
    return document_id


def test_artifact_document_lookup_has_total_bounded_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01"
    artifact_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01"
    connection = _RepositoryConnection([_joined_document_row(document_id, doc_id=artifact_id)])
    monkeypatch.setattr(
        "idis.persistence.repositories.documents.set_tenant_local",
        lambda *_args: None,
    )

    repository = PostgresDocumentsRepository(
        connection,  # type: ignore[arg-type]
        TENANT_A_ID,
    )
    document = repository.get_document_by_artifact_id(artifact_id)

    assert document is not None
    assert document["document_id"] == document_id
    assert len(connection.calls) == 1
    sql, params = connection.calls[0]
    assert "WHERE documents.doc_id = :doc_id" in sql
    assert "ORDER BY documents.document_id ASC" in sql
    assert "LIMIT 2" in sql
    assert params == {"doc_id": artifact_id}


def test_artifact_document_lookup_rejects_multiplicity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01"
    connection = _RepositoryConnection(
        [
            _joined_document_row(
                "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01",
                doc_id=artifact_id,
            ),
            _joined_document_row(
                "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02",
                doc_id=artifact_id,
            ),
        ]
    )
    monkeypatch.setattr(
        "idis.persistence.repositories.documents.set_tenant_local",
        lambda *_args: None,
    )
    repository = PostgresDocumentsRepository(
        connection,  # type: ignore[arg-type]
        TENANT_A_ID,
    )

    with pytest.raises(RuntimeError, match="multiple durable rows"):
        repository.get_document_by_artifact_id(artifact_id)


def test_inventory_document_lookup_uses_unique_tie_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01"
    artifact_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01"
    connection = _RepositoryConnection([_joined_document_row(document_id, doc_id=artifact_id)])
    monkeypatch.setattr(
        "idis.persistence.repositories.documents.set_tenant_local",
        lambda *_args: None,
    )
    repository = PostgresDocumentsRepository(
        connection,  # type: ignore[arg-type]
        TENANT_A_ID,
    )

    document = repository.get_document_by_inventory_file_id(
        deal_id=DEAL_A_ID,
        inventory_file_id="00000000-0000-4000-8000-000000000001",
    )

    assert document is not None
    sql, params = connection.calls[0]
    assert "ORDER BY documents.created_at ASC, documents.document_id ASC" in sql
    assert "LIMIT 2" in sql
    assert params == {
        "deal_id": DEAL_A_ID,
        "inventory_file_id": "00000000-0000-4000-8000-000000000001",
    }


def test_inventory_document_lookup_rejects_multiplicity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory_file_id = "00000000-0000-4000-8000-000000000001"
    connection = _RepositoryConnection(
        [
            _joined_document_row(
                "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01",
                doc_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01",
            ),
            _joined_document_row(
                "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02",
                doc_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa02",
            ),
        ]
    )
    monkeypatch.setattr(
        "idis.persistence.repositories.documents.set_tenant_local",
        lambda *_args: None,
    )
    repository = PostgresDocumentsRepository(
        connection,  # type: ignore[arg-type]
        TENANT_A_ID,
    )

    with pytest.raises(RuntimeError, match="multiple durable rows"):
        repository.get_document_by_inventory_file_id(
            deal_id=DEAL_A_ID,
            inventory_file_id=inventory_file_id,
        )


def test_document_repository_lists_documents_by_deal_deterministically(
    app_engine: Engine, clean_tables: None
) -> None:
    with app_engine.begin() as conn:
        _create_deal(conn, TENANT_A_ID, DEAL_A_ID, "Tenant A Deal")
        repo = PostgresDocumentsRepository(conn, TENANT_A_ID)
        second_id = _seed_parsed_document(repo, suffix="02")
        first_id = _seed_parsed_document(repo, suffix="01")

        documents = repo.list_documents_by_deal(DEAL_A_ID)

    assert [document["document_id"] for document in documents] == [first_id, second_id]
    assert [document["document_name"] for document in documents] == [
        "source-01.pdf",
        "source-02.pdf",
    ]
    assert all(document["parse_status"] == "PARSED" for document in documents)


def test_document_repository_lists_spans_by_deal_and_document_deterministically(
    app_engine: Engine, clean_tables: None
) -> None:
    with app_engine.begin() as conn:
        _create_deal(conn, TENANT_A_ID, DEAL_A_ID, "Tenant A Deal")
        repo = PostgresDocumentsRepository(conn, TENANT_A_ID)
        document_id = _seed_parsed_document(repo, suffix="01")
        repo.create_document_span(
            span_id="cccccccc-cccc-cccc-cccc-cccccccccc02",
            deal_id=DEAL_A_ID,
            document_id=document_id,
            span_type="PAGE_TEXT",
            locator={"page": 1, "line": 2},
            text_excerpt="Second span.",
            content_hash="2" * 64,
        )
        repo.create_document_span(
            span_id="cccccccc-cccc-cccc-cccc-cccccccccc01",
            deal_id=DEAL_A_ID,
            document_id=document_id,
            span_type="PAGE_TEXT",
            locator={"page": 1, "line": 1},
            text_excerpt="First span.",
            content_hash="1" * 64,
        )

        spans = repo.list_spans_by_document(deal_id=DEAL_A_ID, document_id=document_id)

    assert [span["span_id"] for span in spans] == [
        "cccccccc-cccc-cccc-cccc-cccccccccc01",
        "cccccccc-cccc-cccc-cccc-cccccccccc02",
    ]
    assert [span["deal_id"] for span in spans] == [DEAL_A_ID, DEAL_A_ID]
    assert [span["content_hash"] for span in spans] == ["1" * 64, "2" * 64]


def test_document_repository_lists_timecodes_in_semantic_non_uuid_order(
    app_engine: Engine, clean_tables: None
) -> None:
    with app_engine.begin() as conn:
        _create_deal(conn, TENANT_A_ID, DEAL_A_ID, "Tenant A Media Deal")
        repo = PostgresDocumentsRepository(conn, TENANT_A_ID)
        document_id = _seed_parsed_document(repo, suffix="01")
        repo.create_document_span(
            span_id="cccccccc-cccc-cccc-cccc-cccccccccc01",
            deal_id=DEAL_A_ID,
            document_id=document_id,
            span_type="TIMECODE",
            locator={"start_ms": 1_000, "end_ms": 2_000},
            text_excerpt="Later span.",
            content_hash="2" * 64,
        )
        repo.create_document_span(
            span_id="cccccccc-cccc-cccc-cccc-cccccccccc02",
            deal_id=DEAL_A_ID,
            document_id=document_id,
            span_type="TIMECODE",
            locator={"start_ms": 0, "end_ms": 900},
            text_excerpt="Earlier span.",
            content_hash="1" * 64,
        )

        spans = repo.list_spans_by_document(deal_id=DEAL_A_ID, document_id=document_id)

    assert [span["locator"]["start_ms"] for span in spans] == [0, 1_000]
    assert [span["content_hash"] for span in spans] == ["1" * 64, "2" * 64]


def test_document_repository_rls_blocks_cross_tenant_document_and_span_reads(
    app_engine: Engine, clean_tables: None
) -> None:
    with app_engine.begin() as conn:
        _create_deal(conn, TENANT_A_ID, DEAL_A_ID, "Tenant A Deal")
        _create_deal(conn, TENANT_B_ID, DEAL_B_ID, "Tenant B Deal")

        tenant_a_repo = PostgresDocumentsRepository(conn, TENANT_A_ID)
        document_id = _seed_parsed_document(tenant_a_repo, suffix="01")
        tenant_a_repo.create_document_span(
            span_id="cccccccc-cccc-cccc-cccc-cccccccccc01",
            deal_id=DEAL_A_ID,
            document_id=document_id,
            span_type="PAGE_TEXT",
            locator={"page": 1},
            text_excerpt="Tenant A only.",
            content_hash="a" * 64,
        )

        tenant_b_repo = PostgresDocumentsRepository(conn, TENANT_B_ID)
        documents = tenant_b_repo.list_documents_by_deal(DEAL_A_ID)
        spans = tenant_b_repo.list_spans_by_document(deal_id=DEAL_A_ID, document_id=document_id)
        document = tenant_b_repo.get_document(document_id)
        document_by_artifact = tenant_b_repo.get_document_by_artifact_id(
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01"
        )

    assert documents == []
    assert spans == []
    assert document is None
    assert document_by_artifact is None


def test_run_document_loader_cross_tenant_reads_fail_closed_under_rls(
    app_engine: Engine, clean_tables: None
) -> None:
    with app_engine.begin() as conn:
        _create_deal(conn, TENANT_A_ID, DEAL_A_ID, "Tenant A Deal")
        _create_deal(conn, TENANT_B_ID, DEAL_B_ID, "Tenant B Deal")

        tenant_a_repo = PostgresDocumentsRepository(conn, TENANT_A_ID)
        document_id = _seed_parsed_document(tenant_a_repo, suffix="01")
        tenant_a_repo.create_document_span(
            span_id="cccccccc-cccc-cccc-cccc-cccccccccc01",
            deal_id=DEAL_A_ID,
            document_id=document_id,
            span_type="PAGE_TEXT",
            locator={"page": 1},
            text_excerpt="Tenant A only.",
            content_hash="a" * 64,
        )

        tenant_b_documents = load_documents_for_deal(
            db_conn=conn,
            tenant_id=TENANT_B_ID,
            deal_id=DEAL_A_ID,
        )

    assert tenant_b_documents == []

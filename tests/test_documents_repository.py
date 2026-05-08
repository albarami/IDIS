"""Postgres document repository tests for persisted ingestion corpus."""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime
from typing import TYPE_CHECKING

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

    assert documents == []
    assert spans == []
    assert document is None


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

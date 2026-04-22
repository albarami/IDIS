"""Postgres repository tests for document_artifacts, documents, document_spans
(Sprint 1 Wave 2, Task 5).

Mirrors the pattern used by tests/test_api_deals_postgres.py:
- Skips (or fails, under IDIS_REQUIRE_POSTGRES=1) when no Postgres is configured.
- Uses admin_engine for migrations + TRUNCATE setup, app_engine for the
  app-role connection under which repositories operate.

Scope: repository layer only. No API routes, no IngestionService wiring.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

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

TENANT_A_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _skip_or_fail_if_no_postgres() -> None:
    admin_url = os.environ.get(ADMIN_URL_ENV)
    app_url = os.environ.get(APP_URL_ENV)
    require_postgres = os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1"

    if not admin_url or not app_url:
        msg = f"PostgreSQL tests require {ADMIN_URL_ENV} and {APP_URL_ENV} env vars"
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
    """Run alembic upgrade head once per module."""
    from alembic import command
    from alembic.config import Config

    import idis.persistence.migrations as migrations_pkg

    migrations_dir = os.path.dirname(migrations_pkg.__file__)
    config = Config()
    config.set_main_option("script_location", migrations_dir)

    with admin_engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")

    yield

    with admin_engine.begin() as conn:
        config.attributes["connection"] = conn
        command.downgrade(config, "base")


@pytest.fixture
def clean_ingestion_tables(
    admin_engine: Engine, migrated_db: None
) -> Generator[None, None, None]:
    """Truncate ingestion-gate tables (cascades to spans) before and after each test.

    Also truncates deals (document_artifacts.deal_id FKs into it).
    """
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


def _insert_deal(conn: Engine | None, *, deal_id: str, tenant_id: str) -> None:
    """Insert a minimal deal row for FK satisfaction, bypassing app-role RLS."""
    assert conn is not None
    with conn.begin() as c:
        c.execute(
            text(
                """
                INSERT INTO deals (
                    deal_id, tenant_id, name, company_name, status,
                    stage, tags, created_at, updated_at
                ) VALUES (
                    :deal_id, :tenant_id, 'test-deal', 'test-company', 'NEW',
                    NULL, CAST('[]' AS JSONB), now(), NULL
                )
                """
            ),
            {"deal_id": deal_id, "tenant_id": tenant_id},
        )


class TestDocumentArtifactsRepository:
    def test_create_and_get_roundtrip(
        self, app_engine: Engine, admin_engine: Engine, clean_ingestion_tables: None
    ) -> None:
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        _insert_deal(admin_engine, deal_id=deal_id, tenant_id=TENANT_A_ID)

        with app_engine.begin() as conn:
            repo = DocumentArtifactsRepository(conn, TENANT_A_ID)
            created = repo.create(
                doc_id=doc_id,
                deal_id=deal_id,
                doc_type="PITCH_DECK",
                title="Deck v1",
                source_system="manual-upload",
                version_id="v1",
                sha256="a" * 64,
                uri="idis://bucket/key",
                metadata={"pages": 12},
            )
            fetched = repo.get(doc_id)

        assert created["doc_id"] == doc_id
        assert fetched is not None
        assert fetched["doc_id"] == doc_id
        assert fetched["tenant_id"] == TENANT_A_ID
        assert fetched["deal_id"] == deal_id
        assert fetched["doc_type"] == "PITCH_DECK"
        assert fetched["title"] == "Deck v1"
        assert fetched["sha256"] == "a" * 64
        assert fetched["uri"] == "idis://bucket/key"
        assert fetched["metadata"] == {"pages": 12}

    def test_cross_tenant_read_returns_none(
        self, app_engine: Engine, admin_engine: Engine, clean_ingestion_tables: None
    ) -> None:
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        _insert_deal(admin_engine, deal_id=deal_id, tenant_id=TENANT_A_ID)

        with app_engine.begin() as conn:
            DocumentArtifactsRepository(conn, TENANT_A_ID).create(
                doc_id=doc_id,
                deal_id=deal_id,
                doc_type="OTHER",
                title="tenant A only",
                source_system="x",
                version_id="1",
            )

        with app_engine.begin() as conn:
            other = DocumentArtifactsRepository(conn, TENANT_B_ID)
            assert other.get(doc_id) is None


class TestDocumentsRepository:
    def test_create_get_and_list_by_deal(
        self, app_engine: Engine, admin_engine: Engine, clean_ingestion_tables: None
    ) -> None:
        deal_id = str(uuid.uuid4())
        other_deal_id = str(uuid.uuid4())
        art_id_1 = str(uuid.uuid4())
        art_id_2 = str(uuid.uuid4())
        art_id_other = str(uuid.uuid4())
        doc_id_1 = str(uuid.uuid4())
        doc_id_2 = str(uuid.uuid4())
        doc_id_other = str(uuid.uuid4())

        _insert_deal(admin_engine, deal_id=deal_id, tenant_id=TENANT_A_ID)
        _insert_deal(admin_engine, deal_id=other_deal_id, tenant_id=TENANT_A_ID)

        with app_engine.begin() as conn:
            arts = DocumentArtifactsRepository(conn, TENANT_A_ID)
            arts.create(
                doc_id=art_id_1,
                deal_id=deal_id,
                doc_type="PITCH_DECK",
                title="a1",
                source_system="s",
                version_id="1",
            )
            arts.create(
                doc_id=art_id_2,
                deal_id=deal_id,
                doc_type="PITCH_DECK",
                title="a2",
                source_system="s",
                version_id="1",
            )
            arts.create(
                doc_id=art_id_other,
                deal_id=other_deal_id,
                doc_type="PITCH_DECK",
                title="other-deal",
                source_system="s",
                version_id="1",
            )

            docs = DocumentsRepository(conn, TENANT_A_ID)
            docs.create(
                document_id=doc_id_1,
                deal_id=deal_id,
                doc_id=art_id_1,
                doc_type="PDF",
            )
            docs.create(
                document_id=doc_id_2,
                deal_id=deal_id,
                doc_id=art_id_2,
                doc_type="PDF",
                parse_status="PARSED",
                metadata={"pages": 3},
            )
            docs.create(
                document_id=doc_id_other,
                deal_id=other_deal_id,
                doc_id=art_id_other,
                doc_type="PDF",
            )

            # get()
            fetched = docs.get(doc_id_2)
            assert fetched is not None
            assert fetched["parse_status"] == "PARSED"
            assert fetched["metadata"] == {"pages": 3}

            # list_by_deal filters to that deal only
            items, next_cursor = docs.list_by_deal(deal_id)
            assert next_cursor is None
            ids = {d["document_id"] for d in items}
            assert ids == {doc_id_1, doc_id_2}
            assert all(d["deal_id"] == deal_id for d in items)


class TestDocumentSpansRepository:
    def test_batch_create_and_deterministic_list(
        self, app_engine: Engine, admin_engine: Engine, clean_ingestion_tables: None
    ) -> None:
        deal_id = str(uuid.uuid4())
        art_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        _insert_deal(admin_engine, deal_id=deal_id, tenant_id=TENANT_A_ID)

        span_a = str(uuid.uuid4())
        span_b = str(uuid.uuid4())
        span_c = str(uuid.uuid4())
        # Order by span_id so the "expected order" is well-defined regardless
        # of which UUIDs random.uuid4 happens to generate.
        expected_order = sorted([span_a, span_b, span_c])

        with app_engine.begin() as conn:
            DocumentArtifactsRepository(conn, TENANT_A_ID).create(
                doc_id=art_id,
                deal_id=deal_id,
                doc_type="PITCH_DECK",
                title="spanner",
                source_system="s",
                version_id="1",
            )
            DocumentsRepository(conn, TENANT_A_ID).create(
                document_id=document_id,
                deal_id=deal_id,
                doc_id=art_id,
                doc_type="PDF",
            )
            spans_repo = DocumentSpansRepository(conn, TENANT_A_ID)
            persisted = spans_repo.create_many(
                [
                    {
                        "span_id": span_a,
                        "document_id": document_id,
                        "span_type": "PAGE_TEXT",
                        "locator": {"page": 1},
                        "text_excerpt": "first",
                    },
                    {
                        "span_id": span_b,
                        "document_id": document_id,
                        "span_type": "PARAGRAPH",
                        "locator": {"page": 1, "paragraph_index": 0},
                        "text_excerpt": "second",
                    },
                    {
                        "span_id": span_c,
                        "document_id": document_id,
                        "span_type": "PAGE_TEXT",
                        "locator": {"page": 2},
                        "text_excerpt": "third",
                    },
                ]
            )
            assert len(persisted) == 3

            listed = spans_repo.list_by_document(document_id)

        assert [s["span_id"] for s in listed] == expected_order
        # Locator JSONB round-trips as a dict.
        by_id = {s["span_id"]: s for s in listed}
        assert by_id[span_a]["locator"] == {"page": 1}
        assert by_id[span_b]["locator"] == {"page": 1, "paragraph_index": 0}
        assert by_id[span_c]["locator"] == {"page": 2}
        # All rows carry the expected tenant and document scope.
        assert all(s["tenant_id"] == TENANT_A_ID for s in listed)
        assert all(s["document_id"] == document_id for s in listed)

    def test_spans_tenant_scoping(
        self, app_engine: Engine, admin_engine: Engine, clean_ingestion_tables: None
    ) -> None:
        deal_id = str(uuid.uuid4())
        art_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        _insert_deal(admin_engine, deal_id=deal_id, tenant_id=TENANT_A_ID)

        with app_engine.begin() as conn:
            DocumentArtifactsRepository(conn, TENANT_A_ID).create(
                doc_id=art_id,
                deal_id=deal_id,
                doc_type="PITCH_DECK",
                title="x",
                source_system="s",
                version_id="1",
            )
            DocumentsRepository(conn, TENANT_A_ID).create(
                document_id=document_id,
                deal_id=deal_id,
                doc_id=art_id,
                doc_type="PDF",
            )
            DocumentSpansRepository(conn, TENANT_A_ID).create_many(
                [
                    {
                        "span_id": str(uuid.uuid4()),
                        "document_id": document_id,
                        "span_type": "PAGE_TEXT",
                        "locator": {"page": 1},
                    }
                ]
            )

        with app_engine.begin() as conn:
            other = DocumentSpansRepository(conn, TENANT_B_ID)
            assert other.list_by_document(document_id) == []

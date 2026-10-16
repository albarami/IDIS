"""Real-PostgreSQL contracts for strict durable source-span admission."""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import text

from idis.models.extraction_task import SourceSpanReference
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.methodology_extraction import (
    MethodologyExtractionRepositoryError,
    PostgresMethodologyExtractionRepository,
)
from tests.slice100_task2b._support import (
    disposable_postgres,
    grant_application_access,
    migrate,
)

TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEAL_ID = "22222222-2222-4222-8222-222222222222"
ARTIFACT_ID = "33333333-3333-4333-8333-333333333333"
DOCUMENT_ID = "44444444-4444-4444-8444-444444444444"
SPAN_ID = "55555555-5555-4555-8555-555555555555"
SOURCE_TEXT = "Fictional durable source text with exact bytes.\r\n"
SOURCE_HASH = hashlib.sha256(SOURCE_TEXT.encode("utf-8")).hexdigest()
LOCATOR = {"page": 7, "paragraph": 2}


def _safe_reference() -> SourceSpanReference:
    return SourceSpanReference(
        document_id=DOCUMENT_ID,
        span_id=SPAN_ID,
        evidence_tags=[],
        locator=LOCATOR,
        span_type="PAGE_TEXT",
        content_hash=SOURCE_HASH,
        text_excerpt=None,
    )


def test_source_span_reference_preserves_exact_durable_text_bytes() -> None:
    span = SourceSpanReference(
        document_id=DOCUMENT_ID,
        span_id=SPAN_ID,
        evidence_tags=[],
        locator=LOCATOR,
        span_type=" PAGE_TEXT ",
        content_hash=SOURCE_HASH,
        text_excerpt=SOURCE_TEXT,
    )

    assert span.text_excerpt == SOURCE_TEXT
    assert span.span_type == "PAGE_TEXT"
    assert hashlib.sha256(span.text_excerpt.encode("utf-8")).hexdigest() == span.content_hash


def _seed_source_span(database: object) -> None:
    admin_engine = database.admin_engine  # type: ignore[attr-defined]
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO tenants (tenant_id, name)
                VALUES (CAST(:tenant_id AS uuid), 'Strict span tenant');
                INSERT INTO deals (deal_id, tenant_id, name, created_at)
                VALUES (
                    CAST(:deal_id AS uuid), CAST(:tenant_id AS uuid),
                    'Fictional strict span deal', now()
                );
                INSERT INTO document_artifacts (
                    doc_id, tenant_id, deal_id, doc_type, title,
                    source_system, version_id, sha256
                ) VALUES (
                    CAST(:artifact_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), 'DATA_ROOM_FILE', 'Fictional source',
                    'fixture', 'v1', :source_hash
                );
                INSERT INTO documents (
                    document_id, tenant_id, deal_id, doc_id, doc_type, parse_status
                ) VALUES (
                    CAST(:document_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:artifact_id AS uuid), 'PDF', 'PARSED'
                );
                INSERT INTO document_spans (
                    span_id, tenant_id, deal_id, document_id, span_type,
                    locator, text_excerpt, content_hash
                ) VALUES (
                    CAST(:span_id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:deal_id AS uuid), CAST(:document_id AS uuid), 'PAGE_TEXT',
                    CAST(:locator AS jsonb), :source_text, :source_hash
                )
                """
            ),
            {
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "artifact_id": ARTIFACT_ID,
                "document_id": DOCUMENT_ID,
                "span_id": SPAN_ID,
                "locator": '{"page":7,"paragraph":2}',
                "source_text": SOURCE_TEXT,
                "source_hash": SOURCE_HASH,
            },
        )


def test_postgres_strict_span_loader_reloads_exact_text_and_rehashes_before_planning() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        _seed_source_span(database)

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            loaded = PostgresMethodologyExtractionRepository(
                connection, TENANT_ID
            ).load_strict_planning_source_spans(
                deal_id=DEAL_ID,
                source_span_references_by_document_id={DOCUMENT_ID: [_safe_reference()]},
            )

        assert set(loaded) == {DOCUMENT_ID}
        assert len(loaded[DOCUMENT_ID]) == 1
        span = loaded[DOCUMENT_ID][0]
        assert span.text_excerpt == SOURCE_TEXT
        assert span.content_hash == hashlib.sha256(SOURCE_TEXT.encode("utf-8")).hexdigest()
        assert span.locator == LOCATOR
        assert span.evidence_tags == []


def test_postgres_strict_span_loader_rejects_stored_text_hash_drift() -> None:
    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        grant_application_access(database)
        _seed_source_span(database)
        with database.admin_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE document_spans
                    SET content_hash = :wrong_hash
                    WHERE span_id = CAST(:span_id AS uuid)
                    """
                ),
                {"wrong_hash": "9" * 64, "span_id": SPAN_ID},
            )

        with database.app_engine.begin() as connection:
            set_tenant_local(connection, TENANT_ID)
            with pytest.raises(MethodologyExtractionRepositoryError):
                PostgresMethodologyExtractionRepository(
                    connection, TENANT_ID
                ).load_strict_planning_source_spans(
                    deal_id=DEAL_ID,
                    source_span_references_by_document_id={DOCUMENT_ID: [_safe_reference()]},
                )

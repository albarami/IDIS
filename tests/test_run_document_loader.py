"""Tests for unified run document corpus loading."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from idis.api.routes.runs import _gather_snapshot_documents
from idis.services.runs.steps import load_documents_for_deal

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"


class ParsedCorpusConnection:
    """Small SQLAlchemy-like test double for parsed document corpus reads."""

    def __init__(self, *, with_documents: bool = True) -> None:
        self.with_documents = with_documents
        self.executed_sql: list[str] = []

    def execute(self, statement: object, params: dict[str, str] | None = None) -> MagicMock:
        sql = str(statement)
        self.executed_sql.append(sql)
        result = MagicMock()
        if "SET LOCAL idis.tenant_id" in sql:
            return result
        if "FROM documents" in sql:
            result.fetchall.return_value = [
                MagicMock(
                    _mapping={
                        "document_id": "doc-1",
                        "tenant_id": TENANT_ID,
                        "deal_id": DEAL_ID,
                        "doc_id": "artifact-1",
                        "doc_type": "PDF",
                        "parse_status": "PARSED",
                        "document_metadata": {"name": "source.pdf"},
                        "artifact_metadata": {"source": "synthetic"},
                        "document_name": "source.pdf",
                        "sha256": "a" * 64,
                        "uri": "deals/source.pdf",
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:00:00Z",
                    }
                )
            ] if self.with_documents else []
            return result
        if "FROM document_spans" in sql:
            result.fetchall.return_value = [
                MagicMock(
                    _mapping={
                        "span_id": "span-1",
                        "tenant_id": TENANT_ID,
                        "deal_id": DEAL_ID,
                        "document_id": "doc-1",
                        "span_type": "PAGE_TEXT",
                        "locator": {"page": 1},
                        "text_excerpt": "Revenue was $5M.",
                        "content_hash": "b" * 64,
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:00:00Z",
                    }
                )
            ]
            return result
        raise AssertionError(f"Unexpected SQL: {sql}")


def _request_with_db(conn: ParsedCorpusConnection, *, stale_memory_docs: bool = True) -> Any:
    stale_docs = [
        {
            "document_id": "memory-doc",
            "doc_type": "PDF",
            "document_name": "memory.pdf",
            "spans": [],
        }
    ] if stale_memory_docs else []
    return SimpleNamespace(
        state=SimpleNamespace(db_conn=conn, snapshot_documents=stale_docs),
        app=SimpleNamespace(state=SimpleNamespace(deal_documents={DEAL_ID: stale_docs})),
    )


def test_api_and_worker_load_identical_persisted_document_span_corpus() -> None:
    """API and worker run paths must hydrate documents from the same loader when DB exists."""
    worker_conn = ParsedCorpusConnection()
    api_conn = ParsedCorpusConnection()

    worker_docs = load_documents_for_deal(
        db_conn=worker_conn,
        deal_id=DEAL_ID,
        tenant_id=TENANT_ID,
    )
    api_docs = _gather_snapshot_documents(
        _request_with_db(api_conn),
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
    )

    assert api_docs == worker_docs == [
        {
            "document_id": "doc-1",
            "doc_type": "PDF",
            "document_name": "source.pdf",
            "spans": [
                {
                    "span_id": "span-1",
                    "text_excerpt": "Revenue was $5M.",
                    "locator": {"page": 1},
                    "span_type": "PAGE_TEXT",
                    "content_hash": "b" * 64,
                }
            ],
        }
    ]
    assert any("FROM documents" in sql for sql in api_conn.executed_sql)


def test_api_loader_does_not_use_memory_fallback_when_db_has_no_parsed_documents() -> None:
    """Persisted empty corpus must stay empty so NO_INGESTED_DOCUMENTS remains truthful."""
    api_conn = ParsedCorpusConnection(with_documents=False)

    api_docs = _gather_snapshot_documents(
        _request_with_db(api_conn, stale_memory_docs=True),
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
    )

    assert api_docs == []
    assert any("FROM documents" in sql for sql in api_conn.executed_sql)


def test_load_documents_for_deal_requires_tenant_scope() -> None:
    """The shared production loader must fail closed when tenant_id is omitted."""
    conn = ParsedCorpusConnection()

    try:
        load_documents_for_deal(db_conn=conn, deal_id=DEAL_ID)  # type: ignore[call-arg]
    except TypeError:
        pass
    else:
        raise AssertionError("load_documents_for_deal must require tenant_id")

    assert conn.executed_sql == []


def test_load_documents_for_deal_has_no_unscoped_raw_sql_fallback() -> None:
    """The loader contract must not include a fallback path outside tenant-scoped repo."""
    source = inspect.getsource(load_documents_for_deal)

    assert "PostgresDocumentsRepository" in source
    assert "db_conn.execute" not in source
    assert "FROM documents" not in source


def test_explicit_test_memory_injection_still_works_without_db_connection() -> None:
    """Test-only memory injection remains available when no DB connection is configured."""
    request = SimpleNamespace(
        state=SimpleNamespace(
            snapshot_documents=[
                {
                    "document_id": "test-doc",
                    "doc_type": "PDF",
                    "document_name": "test.pdf",
                    "spans": [],
                }
            ]
        ),
        app=SimpleNamespace(state=SimpleNamespace(deal_documents={})),
    )

    docs = _gather_snapshot_documents(request, tenant_id=TENANT_ID, deal_id=DEAL_ID)

    assert docs == [
        {
            "document_id": "test-doc",
            "doc_type": "PDF",
            "document_name": "test.pdf",
            "spans": [],
        }
    ]

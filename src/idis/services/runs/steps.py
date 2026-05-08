"""Shared run step wiring for canonical execution."""

from __future__ import annotations

from functools import partial
from typing import Any

from idis.audit.sink import AuditSink
from idis.persistence.repositories.documents import PostgresDocumentsRepository
from idis.services.runs.orchestrator import RunContext


def build_run_context(
    *,
    db_conn: Any,
    tenant_id: str,
    run_id: str,
    deal_id: str,
    mode: str,
    documents: list[dict[str, Any]],
    audit_sink: AuditSink,
) -> RunContext:
    """Build a RunContext with the canonical step callables.

    The callables still delegate to the existing route helper implementations
    until Phase 2.1 finishes extracting all route-local wiring.
    """
    from idis.api.routes.runs import (
        _run_full_analysis,
        _run_full_debate,
        _run_full_deliverables,
        _run_full_enrichment,
        _run_full_scoring,
        _run_snapshot_auto_grade,
        _run_snapshot_calc,
        _run_snapshot_extraction,
    )

    _ = audit_sink
    is_full = mode == "FULL"
    return RunContext(
        run_id=run_id,
        tenant_id=tenant_id,
        deal_id=deal_id,
        mode=mode,
        documents=documents,
        extract_fn=partial(_run_snapshot_extraction, db_conn=db_conn),
        grade_fn=partial(_run_snapshot_auto_grade, db_conn=db_conn),
        calc_fn=partial(_run_snapshot_calc, db_conn=db_conn),
        enrich_fn=_run_full_enrichment if is_full else None,
        debate_fn=partial(_run_full_debate, db_conn=db_conn) if is_full else None,
        analysis_fn=_run_full_analysis if is_full else None,
        scoring_fn=_run_full_scoring if is_full else None,
        deliverables_fn=_run_full_deliverables if is_full else None,
    )


def load_documents_for_deal(
    *,
    db_conn: Any,
    deal_id: str,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """Load parsed documents and spans for a deal from Postgres.

    The tenant-scoped repository sets RLS context before querying the corpus.
    """
    if db_conn is None:
        return []

    repo = PostgresDocumentsRepository(db_conn, tenant_id)
    run_documents: list[dict[str, Any]] = []
    for document in repo.list_documents_by_deal(deal_id, parsed_only=True):
        spans = repo.list_spans_by_document(
            deal_id=deal_id,
            document_id=document["document_id"],
        )
        if not spans:
            continue
        run_documents.append(_document_for_run(document, spans))
    return run_documents


def _span_for_run(span: Any) -> dict[str, Any]:
    """Convert a repository span row to the run document span shape."""
    span_dict = (
        dict(span)
        if isinstance(span, dict)
        else {
            "span_id": str(span.span_id),
            "text_excerpt": span.text_excerpt,
            "locator": span.locator if isinstance(span.locator, dict) else {},
            "span_type": str(span.span_type),
            "content_hash": getattr(span, "content_hash", None),
        }
    )
    result: dict[str, Any] = {
        "span_id": str(span_dict["span_id"]),
        "text_excerpt": span_dict.get("text_excerpt"),
        "locator": span_dict.get("locator") if isinstance(span_dict.get("locator"), dict) else {},
        "span_type": str(span_dict["span_type"]),
    }
    if span_dict.get("content_hash"):
        result["content_hash"] = span_dict["content_hash"]
    return result


def _document_for_run(document: dict[str, Any], spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert a repository document and spans to the run document shape."""
    return {
        "document_id": document["document_id"],
        "doc_type": document["doc_type"],
        "document_name": document["document_name"],
        "spans": [_span_for_run(span) for span in spans],
    }

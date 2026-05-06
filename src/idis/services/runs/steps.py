"""Shared run step wiring for canonical execution."""

from __future__ import annotations

from functools import partial
from typing import Any

from sqlalchemy import text

from idis.audit.sink import AuditSink
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
) -> list[dict[str, Any]]:
    """Load parsed documents and spans for a deal from Postgres.

    RLS tenant context must already be set on the connection.
    """
    if db_conn is None:
        return []

    document_rows = db_conn.execute(
        text(
            """
            SELECT document_id, doc_type, metadata
            FROM documents
            WHERE deal_id = :deal_id AND parse_status = 'PARSED'
            ORDER BY created_at, document_id
            """
        ),
        {"deal_id": deal_id},
    ).fetchall()

    documents: list[dict[str, Any]] = []
    for document in document_rows:
        spans = db_conn.execute(
            text(
                """
                SELECT span_id, text_excerpt, locator, span_type
                FROM document_spans
                WHERE document_id = :document_id
                ORDER BY created_at, span_id
                """
            ),
            {"document_id": document.document_id},
        ).fetchall()
        if not spans:
            continue

        metadata = document.metadata if isinstance(document.metadata, dict) else {}
        documents.append(
            {
                "document_id": str(document.document_id),
                "doc_type": str(document.doc_type),
                "document_name": str(
                    metadata.get("name")
                    or metadata.get("document_name")
                    or document.document_id
                ),
                "spans": [
                    {
                        "span_id": str(span.span_id),
                        "text_excerpt": span.text_excerpt,
                        "locator": span.locator if isinstance(span.locator, dict) else {},
                        "span_type": str(span.span_type),
                    }
                    for span in spans
                ],
            }
        )

    return documents

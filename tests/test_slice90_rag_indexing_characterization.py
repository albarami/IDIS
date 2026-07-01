"""Slice90 Task 1 — characterization pinning the CURRENT RAG/pgvector-indexing truth.

The pgvector storage, the live OpenAI embedding provider, the strict no-fake gate, and the
document-span indexing + probe-retrieval pipeline are all built (Slice62/63) and FULL-wired, so the
master acceptance (approved-live-only, no-fake in strict) is already enforced. This pins the
in-scope gaps Slice90 will close (per locked decisions DEC-A..DEC-E):

  1. (G1, partly closed) `document_span`, `calc_output` (Task 4), and `graph_summary` (Task 5) are
     indexed — the remaining scoped types (OCR text, transcripts, enrichment records) are not yet.
  2. The embedding provider is approved-live-only: `ALLOWED_EMBEDDING_BACKENDS == {"openai"}`.
  3. `check_embedding_health` rejects the `deterministic` backend (no fake embeddings) — before any
     live API call.
  4. FULL strict blocks fail-closed: `RAG_CONFIG_BLOCKED` (vector search off) and
     `RAG_HEALTH_BLOCKED` (embedding/pgvector unhealthy).
  5. (G2 closed, Task 3) The strict readiness doc is reconciled: the RAG embedding/index/query path
     is FULL-wired, strict-gated, OpenAI-only, and indexes document_span; other types remain scope.

GREEN-on-arrival expected (characterization pins current truth). Any RED → STOP + report.
No production changes. Tests inject embedding/pgvector health fakes — no real OpenAI calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import idis.services.rag.indexing as indexing_mod
from idis.api.routes.runs import _run_full_rag_evidence
from idis.services.rag.constants import ALLOWED_EMBEDDING_BACKENDS, VECTOR_EMBEDDING_DIMENSIONS
from idis.services.rag.embedding_health import (
    EmbeddingHealthCheck,
    EmbeddingHealthStatus,
    check_embedding_health,
)
from idis.services.rag.pgvector_health import PgvectorHealthCheck
from idis.services.runs.orchestrator import RunStepBlockedError
from tests.test_slice63_rag_full_wiring import DEAL_ID, RUN_ID, TENANT_ID, _documents

_READINESS_DOC = Path("docs/architecture/strict_full_live_readiness.md")

# An env that passes the missing-credentials check so the backend gate is what fails.
_DETERMINISTIC_ENV = {
    "IDIS_ENABLE_VECTOR_SEARCH": "true",
    "IDIS_EMBEDDING_BACKEND": "deterministic",
    "OPENAI_API_KEY": "sk-test-not-real",
    "IDIS_EMBEDDING_MODEL": "text-embedding-3-small",
    "IDIS_EMBEDDING_DIMENSIONS": str(VECTOR_EMBEDDING_DIMENSIONS),
}


# --- G1: indexed source types (document_span; calc_output T4; graph_summary T5) ---


def test_indexed_source_types_today() -> None:
    assert indexing_mod.SOURCE_TYPE_DOCUMENT_SPAN == "document_span"
    assert indexing_mod.SOURCE_TYPE_CALC_OUTPUT == "calc_output"  # Task 4
    assert indexing_mod.SOURCE_TYPE_GRAPH_SUMMARY == "graph_summary"  # Task 5
    # The remaining scoped evidence types are not indexed yet (later Slice90 tasks add them).
    for missing_const in (
        "SOURCE_TYPE_OCR_TEXT",
        "SOURCE_TYPE_TRANSCRIPT",
        "SOURCE_TYPE_ENRICHMENT",
    ):
        assert not hasattr(indexing_mod, missing_const)


# --- Approved-live-only provider ---


def test_allowed_embedding_backends_openai_only() -> None:
    assert frozenset({"openai"}) == ALLOWED_EMBEDDING_BACKENDS


# --- No fake/deterministic embeddings ---


def test_embedding_health_rejects_deterministic_backend() -> None:
    # Rejected before any live API call (no client_factory needed, no real OpenAI).
    result = check_embedding_health(env=_DETERMINISTIC_ENV)
    assert result.status == EmbeddingHealthStatus.FAILED


# --- Strict fail-closed gates ---


def test_full_strict_rag_blocks_when_vector_search_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("IDIS_ENABLE_VECTOR_SEARCH", raising=False)
    with pytest.raises(RunStepBlockedError) as exc_info:
        _run_full_rag_evidence(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=[],
            db_conn=None,
            strict_full_live=True,
        )
    assert exc_info.value.code == "RAG_CONFIG_BLOCKED"


def test_full_strict_rag_blocks_when_embedding_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IDIS_ENABLE_VECTOR_SEARCH", "true")
    with pytest.raises(RunStepBlockedError) as exc_info:
        _run_full_rag_evidence(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=_documents(),
            db_conn=None,
            strict_full_live=True,
            pgvector_health_checker=lambda _env: PgvectorHealthCheck.healthy(),
            embedding_health_checker=lambda _env: EmbeddingHealthCheck.failed(
                error="injected unhealthy embedding provider"
            ),
        )
    assert exc_info.value.code == "RAG_HEALTH_BLOCKED"


# --- G2 closed (Task 3): readiness doc reconciled to the wired reality ---


def test_readiness_doc_reconciled_rag_wording() -> None:
    doc = _READINESS_DOC.read_text(encoding="utf-8")
    # The stale "not-implemented / no app path" wording is gone...
    assert "no app embedding/index/query path exists" not in doc
    # ...replaced by the as-built reality: document_span (spans/OCR/transcripts) + calc_output +
    # graph_summary indexed; enrichment deferred (no durable UUID source).
    assert "Indexes `document_span`" in doc
    assert "`calc_output`, and `graph_summary`" in doc
    assert "Enrichment-record indexing is deferred" in doc

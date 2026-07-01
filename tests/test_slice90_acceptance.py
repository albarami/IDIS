"""Slice90 Task 2 — acceptance proof for the master-plan RAG/pgvector acceptance.

Acceptance: a strict run persists embeddings using the approved live provider only; no
fake/deterministic embeddings in strict mode.

  - Strict FULL with a deterministic/disallowed backend blocks safely (`RAG_HEALTH_BLOCKED`) and
    persists nothing.
  - Strict FULL with the approved live backend persists embeddings via the real indexing pipeline
    (the OpenAI embed boundary is replaced by an injected fake — no real OpenAI call).

Proven with injected fakes (recording vector repository + fake embed batch). A guard asserts the
real OpenAI embed batch is never constructed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from idis.api.routes.runs import _run_full_rag_evidence
from idis.services.rag.constants import VECTOR_EMBEDDING_DIMENSIONS
from idis.services.rag.embedding_health import EmbeddingHealthCheck
from idis.services.rag.indexing import index_document_spans_for_deal
from idis.services.rag.pgvector_health import PgvectorHealthCheck
from idis.services.runs.orchestrator import RunStepBlockedError
from tests.test_slice63_rag_full_wiring import DEAL_ID, RUN_ID, TENANT_ID, _documents

_FAKE_VECTOR = [0.01] * VECTOR_EMBEDDING_DIMENSIONS

_LIVE_OPENAI_ENV = {
    "IDIS_ENABLE_VECTOR_SEARCH": "true",
    "IDIS_EMBEDDING_BACKEND": "openai",
    "OPENAI_API_KEY": "sk-test-not-real",
    "IDIS_EMBEDDING_MODEL": "text-embedding-3-small",
    "IDIS_EMBEDDING_DIMENSIONS": str(VECTOR_EMBEDDING_DIMENSIONS),
}
_DETERMINISTIC_ENV = {**_LIVE_OPENAI_ENV, "IDIS_EMBEDDING_BACKEND": "deterministic"}


class _RecordingVectorRepo:
    """Vector repository fake that records upserts and serves a probe match."""

    def __init__(self) -> None:
        self.upserts: list[dict[str, Any]] = []

    def upsert_embedding(self, **kwargs: Any) -> dict[str, Any]:
        self.upserts.append(kwargs)
        return {"embedding_id": f"emb-{len(self.upserts)}", **kwargs}

    def similarity_search(
        self, *, deal_id: str, query_embedding: list[float], limit: int = 5
    ) -> list[dict[str, Any]]:
        return [{"source_type": "document_span", "source_id": "span-1", "score": 0.9}]


def _fake_embed_batch(texts: list[str]) -> list[list[float]]:
    return [list(_FAKE_VECTOR) for _ in texts]


def _fake_indexing_service(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    documents: list[dict[str, Any]],
    repository: Any,
    embedding_model: str,
    embedding_dimensions: int,
) -> Any:
    """Run the REAL indexing pipeline with a fake embed batch (no OpenAI)."""
    return index_document_spans_for_deal(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        documents=documents,
        repository=repository,
        embed_batch=_fake_embed_batch,
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
    )


@pytest.fixture(autouse=True)
def _forbid_real_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail if the real OpenAI embed batch is ever constructed."""

    def _raise(**_kwargs: Any) -> Any:
        raise AssertionError("real OpenAI embed batch must not be constructed in tests")

    monkeypatch.setattr("idis.services.rag.indexing.create_openai_embed_batch", _raise)


def _set_env(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)


# --- Acceptance: strict + deterministic backend blocks safely, persists nothing ---


def test_strict_deterministic_backend_blocks_and_persists_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch, _DETERMINISTIC_ENV)
    repo = _RecordingVectorRepo()
    with pytest.raises(RunStepBlockedError) as exc_info:
        _run_full_rag_evidence(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=_documents(),
            db_conn=MagicMock(),
            strict_full_live=True,
            pgvector_health_checker=lambda _env: PgvectorHealthCheck.healthy(),
            # embedding_health_checker NOT injected → the REAL health check rejects "deterministic".
            vector_repository_factory=lambda _conn, _tenant: repo,
        )
    assert exc_info.value.code == "RAG_HEALTH_BLOCKED"
    assert repo.upserts == []  # no embeddings persisted


# --- Acceptance: strict + approved live backend persists embeddings (fake embed) ---


def test_strict_approved_live_backend_persists_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, _LIVE_OPENAI_ENV)
    repo = _RecordingVectorRepo()
    summary = _run_full_rag_evidence(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        documents=_documents(),
        db_conn=MagicMock(),
        strict_full_live=True,
        pgvector_health_checker=lambda _env: PgvectorHealthCheck.healthy(),
        embedding_health_checker=lambda _env: EmbeddingHealthCheck.healthy(
            model="text-embedding-3-small", dimensions=VECTOR_EMBEDDING_DIMENSIONS
        ),
        vector_repository_factory=lambda _conn, _tenant: repo,
        indexing_service=_fake_indexing_service,
    )

    assert summary["rag_status"] == "available"
    assert summary["embedding_health_status"] == "healthy"
    # Embeddings were persisted via the approved-live path...
    assert repo.upserts, "no embeddings persisted"
    assert all(upsert["source_type"] == "document_span" for upsert in repo.upserts)
    # ...using the injected fake vector, never a real OpenAI embedding.
    assert all(upsert["embedding"] == list(_FAKE_VECTOR) for upsert in repo.upserts)

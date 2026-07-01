"""Slice90 Task 4 — index persisted calc outputs into pgvector.

Adds `SOURCE_TYPE_CALC_OUTPUT` + `index_calc_outputs_for_deal` (mirroring the span pipeline) and
wires it into the FULL RAG step after durable calc outputs are available. Uses the existing vector
schema (source_type/source_id/content_hash). Tests inject a fake embed — no real OpenAI.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from idis.api.routes.runs import _run_full_rag_evidence
from idis.services.rag.constants import VECTOR_EMBEDDING_DIMENSIONS
from idis.services.rag.embedding_health import EmbeddingHealthCheck
from idis.services.rag.indexing import (
    SOURCE_TYPE_CALC_OUTPUT,
    index_calc_outputs_for_deal,
    index_document_spans_for_deal,
)
from idis.services.rag.pgvector_health import PgvectorHealthCheck
from tests.test_slice63_rag_full_wiring import DEAL_ID, RUN_ID, TENANT_ID, _documents

_FAKE_VECTOR = [0.02] * VECTOR_EMBEDDING_DIMENSIONS

_LIVE_OPENAI_ENV = {
    "IDIS_ENABLE_VECTOR_SEARCH": "true",
    "IDIS_EMBEDDING_BACKEND": "openai",
    "OPENAI_API_KEY": "sk-test-not-real",
    "IDIS_EMBEDDING_MODEL": "text-embedding-3-small",
    "IDIS_EMBEDDING_DIMENSIONS": str(VECTOR_EMBEDDING_DIMENSIONS),
}


class _RecordingVectorRepo:
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


def _calc_row(calc_id: str, *, reproducibility_hash: str) -> dict[str, Any]:
    """A calc row shaped like `CalculationsRepository.list_by_deal` returns."""
    return {
        "calc_id": calc_id,
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "calc_type": "GROSS_MARGIN",
        "output": {
            "primary_value": "60.0",
            "secondary_values": {},
            "unit": "percent",
            "currency": "",
        },
        "reproducibility_hash": reproducibility_hash,
    }


@pytest.fixture(autouse=True)
def _forbid_real_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(**_kwargs: Any) -> Any:
        raise AssertionError("real OpenAI embed batch must not be constructed in tests")

    monkeypatch.setattr("idis.services.rag.indexing.create_openai_embed_batch", _raise)


# --- Unit: index_calc_outputs_for_deal persists calc-output embeddings ---


def test_index_calc_outputs_persists_with_calc_source_type() -> None:
    assert SOURCE_TYPE_CALC_OUTPUT == "calc_output"
    repo = _RecordingVectorRepo()
    summary, probes = index_calc_outputs_for_deal(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        calculations=[_calc_row("calc-1", reproducibility_hash="rh-1")],
        repository=repo,
        embed_batch=_fake_embed_batch,
        embedding_model="text-embedding-3-small",
        embedding_dimensions=VECTOR_EMBEDDING_DIMENSIONS,
    )
    assert summary["status"] == "indexed"
    assert summary["indexed_calc_count"] == 1
    assert probes == [list(_FAKE_VECTOR)]
    upsert = repo.upserts[0]
    assert upsert["source_type"] == "calc_output"
    assert upsert["source_id"] == "calc-1"
    assert upsert["content_hash"] == "rh-1"  # reproducibility hash dedups
    assert upsert["embedding"] == list(_FAKE_VECTOR)
    assert upsert["run_id"] == RUN_ID


def test_index_calc_outputs_skips_rows_missing_id_or_hash() -> None:
    repo = _RecordingVectorRepo()
    summary, _ = index_calc_outputs_for_deal(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        calculations=[
            {"calc_id": "", "reproducibility_hash": "rh"},
            {"calc_id": "c", "reproducibility_hash": ""},
        ],
        repository=repo,
        embed_batch=_fake_embed_batch,
        embedding_model="text-embedding-3-small",
        embedding_dimensions=VECTOR_EMBEDDING_DIMENSIONS,
    )
    assert summary["status"] == "skipped"
    assert summary["indexed_calc_count"] == 0
    assert repo.upserts == []


# --- Wiring: FULL RAG step indexes calc outputs after spans ---


def _fake_span_indexing_service(**kwargs: Any) -> Any:
    return index_document_spans_for_deal(embed_batch=_fake_embed_batch, **kwargs)


def _fake_calc_indexing_service(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    calc_ids: list[str],
    repository: Any,
    embedding_model: str,
    embedding_dimensions: int,
) -> Any:
    rows = [_calc_row(calc_id, reproducibility_hash=f"rh-{calc_id}") for calc_id in calc_ids]
    return index_calc_outputs_for_deal(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        calculations=rows,
        repository=repository,
        embed_batch=_fake_embed_batch,
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
    )


def test_full_strict_rag_indexes_calc_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _LIVE_OPENAI_ENV.items():
        monkeypatch.setenv(key, value)
    repo = _RecordingVectorRepo()
    summary = _run_full_rag_evidence(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        documents=_documents(),
        db_conn=MagicMock(),
        strict_full_live=True,
        calc_ids=["calc-1"],
        pgvector_health_checker=lambda _env: PgvectorHealthCheck.healthy(),
        embedding_health_checker=lambda _env: EmbeddingHealthCheck.healthy(
            model="text-embedding-3-small", dimensions=VECTOR_EMBEDDING_DIMENSIONS
        ),
        vector_repository_factory=lambda _conn, _tenant: repo,
        indexing_service=_fake_span_indexing_service,
        calc_indexing_service=_fake_calc_indexing_service,
    )
    assert summary["rag_status"] == "available"
    calc_upserts = [u for u in repo.upserts if u["source_type"] == "calc_output"]
    assert [u["source_id"] for u in calc_upserts] == ["calc-1"]
    assert summary["rag_calc_indexing"]["indexed_calc_count"] == 1

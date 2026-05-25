"""Index persisted document spans into tenant-scoped pgvector storage."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from idis.persistence.repositories.vector_embeddings import PostgresVectorEmbeddingsRepository
from idis.services.rag.constants import VECTOR_EMBEDDING_DIMENSIONS
from idis.services.rag.embedding_health import (
    DEFAULT_EMBEDDING_MODEL,
    IDIS_EMBEDDING_MODEL_ENV,
    OPENAI_API_KEY_ENV,
    EmbeddingClientFactory,
    _default_openai_client_factory,
    _parse_dimensions,
)

SOURCE_TYPE_DOCUMENT_SPAN = "document_span"
MAX_PROBE_EMBEDDINGS = 3


class VectorEmbeddingsRepository(Protocol):
    """Minimal repository surface required for span indexing and probe retrieval."""

    def upsert_embedding(self, **kwargs: Any) -> dict[str, Any]:
        """Persist one embedding row."""
        ...

    def similarity_search(
        self,
        *,
        deal_id: str,
        query_embedding: list[float],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Return safe ranked matches."""
        ...


def index_document_spans_for_deal(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    documents: Sequence[dict[str, Any]],
    repository: VectorEmbeddingsRepository,
    embed_batch: Callable[[list[str]], list[list[float]]],
    embedding_model: str,
    embedding_dimensions: int = VECTOR_EMBEDDING_DIMENSIONS,
) -> tuple[dict[str, Any], list[list[float]]]:
    """Index eligible persisted span excerpts and return safe summary plus probe vectors.

    Args:
        tenant_id: Tenant scope for repository writes.
        deal_id: Deal scope for repository writes.
        run_id: Run provenance recorded on each upsert.
        documents: Run document corpus with nested span dicts.
        repository: Injectable vector repository for tests.
        embed_batch: Callable that embeds span text batches without exposing vectors upstream.
        embedding_model: Provider model identifier stored with each row.
        embedding_dimensions: Expected embedding width for schema alignment.

    Returns:
        Safe indexing summary and up to ``MAX_PROBE_EMBEDDINGS`` embeddings for probe retrieval.
    """
    eligible_spans: list[dict[str, str]] = []
    skipped_span_count = 0

    for document in documents:
        for span in document.get("spans") or []:
            if not isinstance(span, dict):
                skipped_span_count += 1
                continue
            span_id = str(span.get("span_id") or "").strip()
            content_hash = str(span.get("content_hash") or "").strip()
            text_excerpt = str(span.get("text_excerpt") or "").strip()
            if not span_id or not content_hash or not text_excerpt:
                skipped_span_count += 1
                continue
            eligible_spans.append(
                {
                    "span_id": span_id,
                    "content_hash": content_hash,
                    "text_excerpt": text_excerpt,
                }
            )

    if not eligible_spans:
        return (
            {
                "status": "skipped",
                "indexed_span_count": 0,
                "skipped_span_count": skipped_span_count,
            },
            [],
        )

    embeddings = embed_batch([span["text_excerpt"] for span in eligible_spans])
    if len(embeddings) != len(eligible_spans):
        msg = "Embedding provider returned an unexpected batch size."
        raise ValueError(msg)

    indexed_span_count = 0
    probe_embeddings: list[list[float]] = []
    for span, embedding in zip(eligible_spans, embeddings, strict=True):
        repository.upsert_embedding(
            deal_id=deal_id,
            source_type=SOURCE_TYPE_DOCUMENT_SPAN,
            source_id=span["span_id"],
            content_hash=span["content_hash"],
            embedding=embedding,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
            run_id=run_id,
        )
        indexed_span_count += 1
        if len(probe_embeddings) < MAX_PROBE_EMBEDDINGS:
            probe_embeddings.append(embedding)

    return (
        {
            "status": "indexed",
            "indexed_span_count": indexed_span_count,
            "skipped_span_count": skipped_span_count,
        },
        probe_embeddings,
    )


def build_postgres_vector_repository(
    conn: Any,
    tenant_id: str,
) -> PostgresVectorEmbeddingsRepository:
    """Construct the canonical Postgres vector repository for FULL runs."""
    return PostgresVectorEmbeddingsRepository(conn, tenant_id)


def create_openai_embed_batch(
    *,
    env: Mapping[str, str] | None = None,
    client_factory: EmbeddingClientFactory | None = None,
) -> Callable[[list[str]], list[list[float]]]:
    """Return a batch embedder backed by the configured OpenAI embedding provider."""
    values = os.environ if env is None else env
    model = str(values.get(IDIS_EMBEDDING_MODEL_ENV, DEFAULT_EMBEDDING_MODEL)).strip()
    dimensions = _parse_dimensions(values) or VECTOR_EMBEDDING_DIMENSIONS
    api_key = str(values[OPENAI_API_KEY_ENV]).strip()
    make_client = client_factory or _default_openai_client_factory

    def embed_batch(texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = make_client(api_key)
        response = client.embeddings.create(
            input=texts,
            model=model,
            dimensions=dimensions,
        )
        return [list(item.embedding) for item in response.data]

    return embed_batch

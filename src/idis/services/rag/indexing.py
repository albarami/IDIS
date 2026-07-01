"""Index persisted document spans into tenant-scoped pgvector storage."""

from __future__ import annotations

import hashlib
import os
import uuid
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
SOURCE_TYPE_CALC_OUTPUT = "calc_output"
SOURCE_TYPE_GRAPH_SUMMARY = "graph_summary"
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


def _calc_output_text(calc: dict[str, Any]) -> str:
    """Construct a deterministic, safe embed text from a calc's output (no raw private content)."""
    output = calc.get("output") or {}
    primary = str(output.get("primary_value") or "").strip()
    unit = str(output.get("unit") or output.get("currency") or "").strip()
    calc_type = str(calc.get("calc_type") or "calc").strip()
    return f"{calc_type}: {primary} {unit}".strip()


def index_calc_outputs_for_deal(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    calculations: Sequence[dict[str, Any]],
    repository: VectorEmbeddingsRepository,
    embed_batch: Callable[[list[str]], list[list[float]]],
    embedding_model: str,
    embedding_dimensions: int = VECTOR_EMBEDDING_DIMENSIONS,
) -> tuple[dict[str, Any], list[list[float]]]:
    """Index persisted calc outputs and return a safe summary plus probe vectors.

    Mirrors ``index_document_spans_for_deal`` for the ``calc_output`` source type on the existing
    vector schema: ``source_id`` is the calc_id, ``content_hash`` is the reproducibility hash
    (idempotent dedup). Only safe output text (calc type + primary value + unit) is embedded — never
    raw inputs or claim text.
    """
    eligible: list[dict[str, str]] = []
    skipped_calc_count = 0
    for calc in calculations:
        if not isinstance(calc, dict):
            skipped_calc_count += 1
            continue
        calc_id = str(calc.get("calc_id") or "").strip()
        content_hash = str(calc.get("reproducibility_hash") or "").strip()
        text = _calc_output_text(calc)
        if not calc_id or not content_hash or not text:
            skipped_calc_count += 1
            continue
        eligible.append({"calc_id": calc_id, "content_hash": content_hash, "text": text})

    if not eligible:
        return (
            {
                "status": "skipped",
                "indexed_calc_count": 0,
                "skipped_calc_count": skipped_calc_count,
            },
            [],
        )

    embeddings = embed_batch([calc["text"] for calc in eligible])
    if len(embeddings) != len(eligible):
        msg = "Embedding provider returned an unexpected batch size."
        raise ValueError(msg)

    indexed_calc_count = 0
    probe_embeddings: list[list[float]] = []
    for calc, embedding in zip(eligible, embeddings, strict=True):
        repository.upsert_embedding(
            deal_id=deal_id,
            source_type=SOURCE_TYPE_CALC_OUTPUT,
            source_id=calc["calc_id"],
            content_hash=calc["content_hash"],
            embedding=embedding,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
            run_id=run_id,
        )
        indexed_calc_count += 1
        if len(probe_embeddings) < MAX_PROBE_EMBEDDINGS:
            probe_embeddings.append(embedding)

    return (
        {
            "status": "indexed",
            "indexed_calc_count": indexed_calc_count,
            "skipped_calc_count": skipped_calc_count,
        },
        probe_embeddings,
    )


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _graph_claim_text(claim: dict[str, Any]) -> str:
    """Safe per-claim graph summary text (grades/statuses/counts only — no ids or raw payload)."""
    return (
        "Graph lineage: chain depth "
        f"{int(claim.get('chain_depth', 0) or 0)}, weakest grade "
        f"{claim.get('weakest_grade') or 'n/a'}, corroboration "
        f"{claim.get('corroboration_status') or 'n/a'}, "
        f"{int(claim.get('independent_source_count', 0) or 0)} independent source(s)"
    )


def _graph_defect_text(defect: dict[str, Any]) -> str:
    """Safe per-defect graph summary text (type/severity/counts only — no ids or raw payload)."""
    affected_claims = [
        claim_id for claim_id in (defect.get("affected_claim_ids") or []) if claim_id
    ]
    affected_calcs = [calc_id for calc_id in (defect.get("affected_calc_ids") or []) if calc_id]
    return (
        "Graph defect impact: "
        f"{defect.get('severity') or 'n/a'} {defect.get('defect_type') or 'defect'} "
        f"affecting {len(affected_claims)} claim(s) and {len(affected_calcs)} calculation(s)"
    )


def index_graph_summaries_for_deal(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    graph_conclusions: dict[str, Any] | None,
    repository: VectorEmbeddingsRepository,
    embed_batch: Callable[[list[str]], list[list[float]]],
    embedding_model: str,
    embedding_dimensions: int = VECTOR_EMBEDDING_DIMENSIONS,
) -> tuple[dict[str, Any], list[list[float]]]:
    """Index Slice89 ``graph_conclusions`` as ``graph_summary`` embeddings (per claim / per defect).

    Constructs SAFE text from safe fields only (grades, statuses, counts, types) — never raw graph
    rows, evidence text, ids, or private payloads. ``source_id`` is the claim/defect UUID; records
    without a safe UUID source id are skipped and counted. ``content_hash`` is a deterministic hash
    of the constructed text (idempotent dedup).
    """
    conclusions = graph_conclusions or {}
    records: list[dict[str, str]] = []
    skipped_graph_summary_count = 0

    for claim in conclusions.get("claims") or []:
        source_id = str(claim.get("claim_id") or "").strip() if isinstance(claim, dict) else ""
        if not _is_uuid(source_id):
            skipped_graph_summary_count += 1
            continue
        records.append({"source_id": source_id, "text": _graph_claim_text(claim)})

    for defect in conclusions.get("defect_impacts") or []:
        source_id = str(defect.get("defect_id") or "").strip() if isinstance(defect, dict) else ""
        if not _is_uuid(source_id):
            skipped_graph_summary_count += 1
            continue
        records.append({"source_id": source_id, "text": _graph_defect_text(defect)})

    if not records:
        return (
            {
                "status": "skipped",
                "indexed_graph_summary_count": 0,
                "skipped_graph_summary_count": skipped_graph_summary_count,
            },
            [],
        )

    embeddings = embed_batch([record["text"] for record in records])
    if len(embeddings) != len(records):
        msg = "Embedding provider returned an unexpected batch size."
        raise ValueError(msg)

    indexed_graph_summary_count = 0
    probe_embeddings: list[list[float]] = []
    for record, embedding in zip(records, embeddings, strict=True):
        content_hash = hashlib.sha256(record["text"].encode("utf-8")).hexdigest()
        repository.upsert_embedding(
            deal_id=deal_id,
            source_type=SOURCE_TYPE_GRAPH_SUMMARY,
            source_id=record["source_id"],
            content_hash=content_hash,
            embedding=embedding,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
            run_id=run_id,
        )
        indexed_graph_summary_count += 1
        if len(probe_embeddings) < MAX_PROBE_EMBEDDINGS:
            probe_embeddings.append(embedding)

    return (
        {
            "status": "indexed",
            "indexed_graph_summary_count": indexed_graph_summary_count,
            "skipped_graph_summary_count": skipped_graph_summary_count,
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

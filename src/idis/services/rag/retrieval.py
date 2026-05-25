"""Bounded probe retrieval over indexed pgvector rows."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from idis.services.rag.indexing import VectorEmbeddingsRepository

RETRIEVAL_MODE_PROBE = "probe"


def retrieve_rag_probe_evidence(
    *,
    deal_id: str,
    probe_embeddings: Sequence[list[float]],
    repository: VectorEmbeddingsRepository,
    limit: int = 5,
) -> dict[str, Any]:
    """Run bounded plumbing-proof retrieval using indexed span embeddings only.

    This is intentionally not semantic RAG: probe vectors prove indexing/search plumbing
    without exporting query text, span text, or raw vectors.

    Args:
        deal_id: Deal scope for similarity search.
        probe_embeddings: Indexed span embeddings used as bounded proof queries.
        repository: Injectable vector repository for tests.
        limit: Maximum matches per probe query.

    Returns:
        Safe retrieval summary with ``retrieval_mode=probe`` and source IDs/scores only.
    """
    if not probe_embeddings:
        return {
            "status": "skipped",
            "retrieval_mode": RETRIEVAL_MODE_PROBE,
            "probe_count": 0,
            "match_count": 0,
            "matches": [],
        }

    matches: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for probe_embedding in probe_embeddings:
        for match in repository.similarity_search(
            deal_id=deal_id,
            query_embedding=list(probe_embedding),
            limit=limit,
        ):
            source_type = str(match.get("source_type") or "")
            source_id = str(match.get("source_id") or "")
            if not source_type or not source_id:
                continue
            key = (source_type, source_id)
            if key in seen:
                continue
            seen.add(key)
            matches.append(
                {
                    "source_type": source_type,
                    "source_id": source_id,
                    "score": float(match.get("score") or 0.0),
                }
            )

    if not matches:
        return {
            "status": "failed",
            "retrieval_mode": RETRIEVAL_MODE_PROBE,
            "probe_count": len(probe_embeddings),
            "match_count": 0,
            "matches": [],
        }

    return {
        "status": "probed",
        "retrieval_mode": RETRIEVAL_MODE_PROBE,
        "probe_count": len(probe_embeddings),
        "match_count": len(matches),
        "matches": matches,
    }

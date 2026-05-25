"""Tenant-scoped pgvector embedding repository."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from idis.persistence.db import set_tenant_local
from idis.services.rag.constants import VECTOR_EMBEDDING_DIMENSIONS


class PostgresVectorEmbeddingsRepository:
    """Persist and query tenant-scoped vector embeddings in Postgres."""

    def __init__(self, conn: Connection, tenant_id: str) -> None:
        """Initialize repository with tenant-scoped connection."""
        self._conn = conn
        self._tenant_id = tenant_id
        set_tenant_local(conn, tenant_id)

    def upsert_embedding(
        self,
        *,
        deal_id: str,
        source_type: str,
        source_id: str,
        content_hash: str,
        embedding: list[float],
        embedding_model: str,
        embedding_dimensions: int,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert or update one embedding row for a tenant-scoped source."""
        if embedding_dimensions != VECTOR_EMBEDDING_DIMENSIONS:
            msg = (
                f"Configured dimensions {embedding_dimensions} do not match pgvector schema "
                f"dimension {VECTOR_EMBEDDING_DIMENSIONS}."
            )
            raise ValueError(msg)
        if len(embedding) != VECTOR_EMBEDDING_DIMENSIONS:
            msg = (
                f"Embedding length {len(embedding)} does not match pgvector schema "
                f"dimension {VECTOR_EMBEDDING_DIMENSIONS}."
            )
            raise ValueError(msg)

        now = datetime.now(UTC)
        embedding_id = str(uuid.uuid4())
        vector_literal = _vector_literal(embedding)
        row = self._conn.execute(
            text(
                """
                INSERT INTO vector_embeddings (
                    embedding_id, tenant_id, deal_id, run_id, source_type, source_id,
                    embedding_model, embedding_dimensions, content_hash, embedding,
                    created_at, updated_at
                )
                VALUES (
                    :embedding_id, :tenant_id, :deal_id, :run_id, :source_type, :source_id,
                    :embedding_model, :embedding_dimensions, :content_hash,
                    CAST(:embedding AS vector),
                    :created_at, :updated_at
                )
                ON CONFLICT (tenant_id, source_type, source_id, content_hash)
                DO UPDATE SET
                    embedding = EXCLUDED.embedding,
                    embedding_model = EXCLUDED.embedding_model,
                    embedding_dimensions = EXCLUDED.embedding_dimensions,
                    run_id = EXCLUDED.run_id,
                    updated_at = EXCLUDED.updated_at
                RETURNING embedding_id
                """
            ),
            {
                "embedding_id": embedding_id,
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "source_type": source_type,
                "source_id": source_id,
                "embedding_model": embedding_model,
                "embedding_dimensions": embedding_dimensions,
                "content_hash": content_hash,
                "embedding": vector_literal,
                "created_at": now,
                "updated_at": now,
            },
        ).one()
        stored_embedding_id = str(row.embedding_id)
        return {
            "embedding_id": stored_embedding_id,
            "tenant_id": self._tenant_id,
            "deal_id": deal_id,
            "source_type": source_type,
            "source_id": source_id,
            "content_hash": content_hash,
        }

    def similarity_search(
        self,
        *,
        deal_id: str,
        query_embedding: list[float],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Return safe ranked matches with source IDs and scores only."""
        if len(query_embedding) != VECTOR_EMBEDDING_DIMENSIONS:
            msg = (
                f"Query embedding length {len(query_embedding)} does not match pgvector schema "
                f"dimension {VECTOR_EMBEDDING_DIMENSIONS}."
            )
            raise ValueError(msg)

        rows = self._conn.execute(
            text(
                """
                SELECT
                    source_type,
                    source_id::text AS source_id,
                    1 - (embedding <=> CAST(:query_embedding AS vector)) AS score
                FROM vector_embeddings
                WHERE tenant_id = :tenant_id
                  AND deal_id = :deal_id
                ORDER BY embedding <=> CAST(:query_embedding AS vector)
                LIMIT :limit
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "query_embedding": _vector_literal(query_embedding),
                "limit": limit,
            },
        ).mappings()
        return [
            {
                "source_type": row["source_type"],
                "source_id": row["source_id"],
                "score": float(row["score"]),
            }
            for row in rows
        ]


def _vector_literal(values: list[float]) -> str:
    return json.dumps(values)

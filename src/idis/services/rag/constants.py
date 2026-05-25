"""Shared RAG/pgvector constants for schema and health alignment."""

from __future__ import annotations

VECTOR_EMBEDDING_DIMENSIONS = 1536
"""Fixed pgvector column width for ``vector_embeddings.embedding``.

Must stay aligned with ``0017_vector_embeddings.py`` migration schema dimension.
"""

ALLOWED_EMBEDDING_BACKENDS: frozenset[str] = frozenset({"openai"})

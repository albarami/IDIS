"""Synthetic deterministic fixtures for IDIS tests.

These fixtures provide reproducible test data for claim and extraction testing.
All IDs are stable UUIDs to ensure deterministic test behavior.
"""

from tests.fixtures.synthetic.claims_fixture import (
    SYNTHETIC_CLAIMS,
    SYNTHETIC_DEAL,
    SYNTHETIC_DOCUMENTS,
    SYNTHETIC_SANADS,
    SYNTHETIC_SPANS,
    SYNTHETIC_TENANT_ID,
    get_claim_by_id,
    get_document_by_id,
    get_span_by_id,
)

__all__ = [
    "SYNTHETIC_CLAIMS",
    "SYNTHETIC_DEAL",
    "SYNTHETIC_DOCUMENTS",
    "SYNTHETIC_SANADS",
    "SYNTHETIC_SPANS",
    "SYNTHETIC_TENANT_ID",
    "get_claim_by_id",
    "get_document_by_id",
    "get_span_by_id",
]

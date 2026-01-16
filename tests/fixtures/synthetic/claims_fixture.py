"""Synthetic deterministic fixtures for claim and extraction testing.

Provides stable test data including:
- One deal identifier
- Documents with spans (canonical minimal span representation)
- Expected claim payloads with evidence references to spans
- Sanad chains for No-Free-Facts validation

All UUIDs are fixed constants for determinism. Timestamps use stable values.
"""

from __future__ import annotations

from typing import Any

SYNTHETIC_TENANT_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
SYNTHETIC_TENANT_ID_OTHER = "b2c3d4e5-f6a7-8901-bcde-f12345678901"

SYNTHETIC_DEAL: dict[str, Any] = {
    "deal_id": "d1e2f3a4-b5c6-7890-def0-123456789abc",
    "tenant_id": SYNTHETIC_TENANT_ID,
    "deal_name": "Acme Corp Series A",
    "status": "ACTIVE",
    "created_at": "2025-01-15T10:00:00Z",
    "updated_at": None,
}

SYNTHETIC_DOCUMENTS: list[dict[str, Any]] = [
    {
        "document_id": "doc00001-0000-0000-0000-000000000001",
        "tenant_id": SYNTHETIC_TENANT_ID,
        "deal_id": SYNTHETIC_DEAL["deal_id"],
        "filename": "acme_pitch_deck.pdf",
        "mime_type": "application/pdf",
        "status": "PARSED",
        "created_at": "2025-01-15T10:01:00Z",
    },
    {
        "document_id": "doc00001-0000-0000-0000-000000000002",
        "tenant_id": SYNTHETIC_TENANT_ID,
        "deal_id": SYNTHETIC_DEAL["deal_id"],
        "filename": "acme_financials.xlsx",
        "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "status": "PARSED",
        "created_at": "2025-01-15T10:02:00Z",
    },
]

SYNTHETIC_SPANS: list[dict[str, Any]] = [
    {
        "span_id": "span0001-0000-0000-0000-000000000001",
        "tenant_id": SYNTHETIC_TENANT_ID,
        "document_id": SYNTHETIC_DOCUMENTS[0]["document_id"],
        "span_type": "PAGE_TEXT",
        "locator": {"page": 3, "bbox": [100, 200, 500, 250]},
        "text_excerpt": "2024 ARR reached $5M with 85% gross margin.",
        "created_at": "2025-01-15T10:05:00Z",
        "updated_at": "2025-01-15T10:05:00Z",
    },
    {
        "span_id": "span0001-0000-0000-0000-000000000002",
        "tenant_id": SYNTHETIC_TENANT_ID,
        "document_id": SYNTHETIC_DOCUMENTS[0]["document_id"],
        "span_type": "PAGE_TEXT",
        "locator": {"page": 5, "bbox": [100, 300, 500, 350]},
        "text_excerpt": "Customer base grew to 150 enterprise clients in 2024.",
        "created_at": "2025-01-15T10:05:01Z",
        "updated_at": "2025-01-15T10:05:01Z",
    },
    {
        "span_id": "span0001-0000-0000-0000-000000000003",
        "tenant_id": SYNTHETIC_TENANT_ID,
        "document_id": SYNTHETIC_DOCUMENTS[1]["document_id"],
        "span_type": "CELL",
        "locator": {"sheet": "P&L", "cell": "B12"},
        "text_excerpt": "$5,000,000",
        "created_at": "2025-01-15T10:05:02Z",
        "updated_at": "2025-01-15T10:05:02Z",
    },
    {
        "span_id": "span0001-0000-0000-0000-000000000004",
        "tenant_id": SYNTHETIC_TENANT_ID,
        "document_id": SYNTHETIC_DOCUMENTS[1]["document_id"],
        "span_type": "CELL",
        "locator": {"sheet": "Metrics", "cell": "C5"},
        "text_excerpt": "85%",
        "created_at": "2025-01-15T10:05:03Z",
        "updated_at": "2025-01-15T10:05:03Z",
    },
]

SYNTHETIC_SANADS: list[dict[str, Any]] = [
    {
        "sanad_id": "sanad001-0000-0000-0000-000000000001",
        "tenant_id": SYNTHETIC_TENANT_ID,
        "claim_id": "claim001-0000-0000-0000-000000000001",
        "deal_id": SYNTHETIC_DEAL["deal_id"],
        "primary_evidence_id": SYNTHETIC_SPANS[0]["span_id"],
        "corroborating_evidence_ids": [SYNTHETIC_SPANS[2]["span_id"]],
        "transmission_chain": [
            {
                "node_id": "node0001-0000-0000-0000-000000000001",
                "node_type": "EXTRACTION",
                "actor_type": "SYSTEM",
                "actor_id": "extractor-v1",
                "input_refs": [{"type": "span", "id": SYNTHETIC_SPANS[0]["span_id"]}],
                "output_refs": [{"type": "claim", "id": "claim001-0000-0000-0000-000000000001"}],
                "timestamp": "2025-01-15T10:10:00Z",
                "confidence": 0.97,
                "dhabt_score": 0.95,
                "verification_method": None,
                "notes": None,
            }
        ],
        "computed": {
            "grade": "B",
            "grade_rationale": "Primary doc + corroborating spreadsheet cell",
            "corroboration_level": "AHAD_2",
            "independent_chain_count": 2,
        },
        "created_at": "2025-01-15T10:10:00Z",
        "updated_at": None,
    },
    {
        "sanad_id": "sanad001-0000-0000-0000-000000000002",
        "tenant_id": SYNTHETIC_TENANT_ID,
        "claim_id": "claim001-0000-0000-0000-000000000002",
        "deal_id": SYNTHETIC_DEAL["deal_id"],
        "primary_evidence_id": SYNTHETIC_SPANS[1]["span_id"],
        "corroborating_evidence_ids": [],
        "transmission_chain": [
            {
                "node_id": "node0001-0000-0000-0000-000000000002",
                "node_type": "EXTRACTION",
                "actor_type": "SYSTEM",
                "actor_id": "extractor-v1",
                "input_refs": [{"type": "span", "id": SYNTHETIC_SPANS[1]["span_id"]}],
                "output_refs": [{"type": "claim", "id": "claim001-0000-0000-0000-000000000002"}],
                "timestamp": "2025-01-15T10:10:01Z",
                "confidence": 0.92,
                "dhabt_score": 0.88,
                "verification_method": None,
                "notes": None,
            }
        ],
        "computed": {
            "grade": "C",
            "grade_rationale": "Single source, no corroboration",
            "corroboration_level": "AHAD_1",
            "independent_chain_count": 1,
        },
        "created_at": "2025-01-15T10:10:01Z",
        "updated_at": None,
    },
]

SYNTHETIC_CLAIMS: list[dict[str, Any]] = [
    {
        "claim_id": "claim001-0000-0000-0000-000000000001",
        "tenant_id": SYNTHETIC_TENANT_ID,
        "deal_id": SYNTHETIC_DEAL["deal_id"],
        "claim_class": "FINANCIAL",
        "claim_text": "2024 ARR reached $5M with 85% gross margin.",
        "claim_type": "primary",
        "predicate": "ARR(2024) = $5M AND GM(2024) = 85%",
        "value": {
            "value": 5000000.0,
            "unit": "USD",
            "currency": "USD",
            "as_of": "2024-12-31",
            "time_window": {"start": "2024-01-01", "end": "2024-12-31"},
        },
        "sanad_id": SYNTHETIC_SANADS[0]["sanad_id"],
        "claim_grade": "B",
        "corroboration": {"level": "AHAD_2", "independent_chain_count": 2},
        "claim_verdict": "VERIFIED",
        "claim_action": "NONE",
        "defect_ids": [],
        "materiality": "HIGH",
        "ic_bound": True,
        "primary_span_id": SYNTHETIC_SPANS[0]["span_id"],
        "source_calc_id": None,
        "created_by": "system",
        "created_at": "2025-01-15T10:10:00Z",
        "updated_at": None,
    },
    {
        "claim_id": "claim001-0000-0000-0000-000000000002",
        "tenant_id": SYNTHETIC_TENANT_ID,
        "deal_id": SYNTHETIC_DEAL["deal_id"],
        "claim_class": "TRACTION",
        "claim_text": "Customer base grew to 150 enterprise clients in 2024.",
        "claim_type": "primary",
        "predicate": "customers(2024) = 150",
        "value": {
            "value": 150.0,
            "unit": "count",
            "currency": None,
            "as_of": "2024-12-31",
            "time_window": None,
        },
        "sanad_id": SYNTHETIC_SANADS[1]["sanad_id"],
        "claim_grade": "C",
        "corroboration": {"level": "AHAD_1", "independent_chain_count": 1},
        "claim_verdict": "UNVERIFIED",
        "claim_action": "REQUEST_DATA",
        "defect_ids": [],
        "materiality": "MEDIUM",
        "ic_bound": False,
        "primary_span_id": SYNTHETIC_SPANS[1]["span_id"],
        "source_calc_id": None,
        "created_by": "system",
        "created_at": "2025-01-15T10:10:01Z",
        "updated_at": None,
    },
]

CLAIM_WITHOUT_SANAD: dict[str, Any] = {
    "claim_id": "claim001-0000-0000-0000-000000000003",
    "tenant_id": SYNTHETIC_TENANT_ID,
    "deal_id": SYNTHETIC_DEAL["deal_id"],
    "claim_class": "MARKET_SIZE",
    "claim_text": "TAM is $10B.",
    "claim_type": "primary",
    "predicate": None,
    "value": None,
    "sanad_id": None,
    "claim_grade": "D",
    "corroboration": {"level": "NONE", "independent_chain_count": 0},
    "claim_verdict": "UNVERIFIED",
    "claim_action": "FLAG",
    "defect_ids": [],
    "materiality": "LOW",
    "ic_bound": False,
    "primary_span_id": None,
    "source_calc_id": None,
    "created_by": "system",
    "created_at": "2025-01-15T10:10:02Z",
    "updated_at": None,
}

LOW_CONFIDENCE_EXTRACTION: dict[str, Any] = {
    "claim_id": "claim001-0000-0000-0000-000000000004",
    "tenant_id": SYNTHETIC_TENANT_ID,
    "deal_id": SYNTHETIC_DEAL["deal_id"],
    "claim_class": "FINANCIAL",
    "claim_text": "Revenue might be around $3M.",
    "extraction_confidence": "0.80",
    "dhabt_score": "0.75",
    "span_id": SYNTHETIC_SPANS[0]["span_id"],
}


def get_claim_by_id(claim_id: str) -> dict[str, Any] | None:
    """Get a synthetic claim by ID."""
    for claim in SYNTHETIC_CLAIMS:
        if claim["claim_id"] == claim_id:
            return claim.copy()
    if CLAIM_WITHOUT_SANAD["claim_id"] == claim_id:
        return CLAIM_WITHOUT_SANAD.copy()
    return None


def get_document_by_id(document_id: str) -> dict[str, Any] | None:
    """Get a synthetic document by ID."""
    for doc in SYNTHETIC_DOCUMENTS:
        if doc["document_id"] == document_id:
            return doc.copy()
    return None


def get_span_by_id(span_id: str) -> dict[str, Any] | None:
    """Get a synthetic span by ID."""
    for span in SYNTHETIC_SPANS:
        if span["span_id"] == span_id:
            return span.copy()
    return None


def get_sanad_by_id(sanad_id: str) -> dict[str, Any] | None:
    """Get a synthetic sanad by ID."""
    for sanad in SYNTHETIC_SANADS:
        if sanad["sanad_id"] == sanad_id:
            return sanad.copy()
    return None


def get_extraction_request() -> dict[str, Any]:
    """Get a synthetic extraction request for testing."""
    return {
        "request_id": "req00001-0000-0000-0000-000000000001",
        "tenant_id": SYNTHETIC_TENANT_ID,
        "deal_id": SYNTHETIC_DEAL["deal_id"],
        "document_ids": [doc["document_id"] for doc in SYNTHETIC_DOCUMENTS],
        "span_ids": [span["span_id"] for span in SYNTHETIC_SPANS],
    }

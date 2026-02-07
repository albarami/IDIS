"""Regression tests for _retrieve_claims_for_debate.

Ensures full claim data flows from the in-memory store into DebateContext
with correct field mapping:
  claim_grade → sanad_grade
  primary_span_id → source_doc
  extraction_confidence (from sanad) → confidence
"""

from __future__ import annotations

import uuid

import pytest

from idis.persistence.repositories.claims import (
    InMemoryClaimsRepository,
    InMemorySanadsRepository,
    _claims_in_memory_store,
    _sanad_in_memory_store,
)


TENANT_ID = "00000000-0000-0000-0000-tenant000001"
DEAL_ID = "00000000-0000-0000-0000-deal00000001"


@pytest.fixture(autouse=True)
def _clear_stores() -> None:
    """Clear in-memory stores before each test."""
    _claims_in_memory_store.clear()
    _sanad_in_memory_store.clear()
    yield
    _claims_in_memory_store.clear()
    _sanad_in_memory_store.clear()


def _seed_claim(
    *,
    claim_id: str,
    claim_text: str = "ARR is $4.2M",
    claim_class: str = "FINANCIAL",
    claim_grade: str = "B",
    primary_span_id: str | None = "span-001",
) -> str:
    """Insert a claim into the in-memory store and return its ID."""
    repo = InMemoryClaimsRepository(TENANT_ID)
    repo.create(
        claim_id=claim_id,
        deal_id=DEAL_ID,
        claim_class=claim_class,
        claim_text=claim_text,
        claim_grade=claim_grade,
        primary_span_id=primary_span_id,
    )
    return claim_id


def _seed_sanad(
    *,
    claim_id: str,
    extraction_confidence: float = 0.95,
) -> str:
    """Insert a sanad for a claim and return the sanad ID."""
    repo = InMemorySanadsRepository(TENANT_ID)
    sanad_id = str(uuid.uuid4())
    repo.create(
        sanad_id=sanad_id,
        claim_id=claim_id,
        deal_id=DEAL_ID,
        primary_evidence_id=str(uuid.uuid4()),
        computed={"extraction_confidence": extraction_confidence},
    )
    return sanad_id


class TestRetrieveClaimsForDebate:
    """Verify _retrieve_claims_for_debate returns full claim data."""

    def test_claim_text_populated(self) -> None:
        """Claim text from extraction appears in the debate context dict."""
        from idis.api.routes.runs import _retrieve_claims_for_debate

        cid = _seed_claim(claim_id=str(uuid.uuid4()), claim_text="ARR is $4.2M")
        result = _retrieve_claims_for_debate(TENANT_ID, [cid])

        assert len(result) == 1
        assert result[0]["claim_text"] == "ARR is $4.2M"

    def test_claim_class_populated(self) -> None:
        """Claim class from extraction appears in the debate context dict."""
        from idis.api.routes.runs import _retrieve_claims_for_debate

        cid = _seed_claim(claim_id=str(uuid.uuid4()), claim_class="TRACTION")
        result = _retrieve_claims_for_debate(TENANT_ID, [cid])

        assert result[0]["claim_class"] == "TRACTION"

    def test_claim_grade_mapped_to_sanad_grade(self) -> None:
        """Repository claim_grade is mapped to sanad_grade for DebateContext."""
        from idis.api.routes.runs import _retrieve_claims_for_debate

        cid = _seed_claim(claim_id=str(uuid.uuid4()), claim_grade="A")
        result = _retrieve_claims_for_debate(TENANT_ID, [cid])

        assert result[0]["sanad_grade"] == "A"

    def test_primary_span_id_mapped_to_source_doc(self) -> None:
        """Repository primary_span_id is mapped to source_doc for DebateContext."""
        from idis.api.routes.runs import _retrieve_claims_for_debate

        cid = _seed_claim(claim_id=str(uuid.uuid4()), primary_span_id="span-xyz")
        result = _retrieve_claims_for_debate(TENANT_ID, [cid])

        assert result[0]["source_doc"] == "span-xyz"

    def test_confidence_from_sanad(self) -> None:
        """Confidence is extracted from the sanad's computed.extraction_confidence."""
        from idis.api.routes.runs import _retrieve_claims_for_debate

        cid = _seed_claim(claim_id=str(uuid.uuid4()))
        _seed_sanad(claim_id=cid, extraction_confidence=0.92)
        result = _retrieve_claims_for_debate(TENANT_ID, [cid])

        assert result[0]["confidence"] == pytest.approx(0.92)

    def test_confidence_defaults_when_no_sanad(self) -> None:
        """Confidence defaults to 0.0 when no sanad exists for the claim."""
        from idis.api.routes.runs import _retrieve_claims_for_debate

        cid = _seed_claim(claim_id=str(uuid.uuid4()))
        result = _retrieve_claims_for_debate(TENANT_ID, [cid])

        assert result[0]["confidence"] == 0.0

    def test_missing_claim_returns_empty_stub(self) -> None:
        """A claim_id not in the store produces a stub with empty fields."""
        from idis.api.routes.runs import _retrieve_claims_for_debate

        missing_id = str(uuid.uuid4())
        result = _retrieve_claims_for_debate(TENANT_ID, [missing_id])

        assert len(result) == 1
        assert result[0]["claim_id"] == missing_id
        assert result[0]["claim_text"] == ""
        assert result[0]["confidence"] == 0.0

    def test_multiple_claims_all_populated(self) -> None:
        """All claims in a batch are looked up and populated."""
        from idis.api.routes.runs import _retrieve_claims_for_debate

        ids = [
            _seed_claim(claim_id=str(uuid.uuid4()), claim_text=f"Claim {i}")
            for i in range(5)
        ]
        result = _retrieve_claims_for_debate(TENANT_ID, ids)

        assert len(result) == 5
        for i, claim in enumerate(result):
            assert claim["claim_text"] == f"Claim {i}"
            assert claim["claim_id"] == ids[i]

    def test_null_primary_span_id_mapped_to_empty_string(self) -> None:
        """A None primary_span_id maps to empty string, not 'None'."""
        from idis.api.routes.runs import _retrieve_claims_for_debate

        cid = _seed_claim(claim_id=str(uuid.uuid4()), primary_span_id=None)
        result = _retrieve_claims_for_debate(TENANT_ID, [cid])

        assert result[0]["source_doc"] == ""

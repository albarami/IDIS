"""Tests for LLMClaimExtractor — LLM-backed claim extraction with validation.

8 tests covering:
- Happy path: valid JSON from DeterministicLLMClient
- Invalid JSON triggers retry and structured error
- Schema mismatch (non-array) → error after retry
- Empty array is valid (NO_CLAIMS_FOUND)
- Missing required fields in claims are skipped
- Invalid claim_class values are skipped
- Confidence scoring applied to extracted drafts
- extract_from_chunk returns ChunkExtractionResult
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from idis.services.extraction.confidence.scorer import ConfidenceScorer
from idis.services.extraction.extractors.claim_extractor import (
    ChunkExtractionResult,
    LLMClaimExtractor,
)
from idis.services.extraction.extractors.llm_client import DeterministicLLMClient


def _build_extractor(
    llm_client: Any | None = None,
    max_retries: int = 3,
) -> LLMClaimExtractor:
    """Build an LLMClaimExtractor with defaults for testing."""
    client = llm_client or DeterministicLLMClient()
    prompt_text = (
        "Extract claims.\n"
        "## Input\nDocument Type: {{document_type}}\n"
        "Document Name: {{document_name}}\n"
        "Chunk Location: {{chunk_locator}}\n\n"
        "Content:\n{{chunk_content}}\n\n"
        "## Output Format\n{{output_schema}}"
    )
    output_schema = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["claim_text", "claim_class", "confidence"],
        },
    }
    return LLMClaimExtractor(
        llm_client=client,
        prompt_text=prompt_text,
        output_schema=output_schema,
        confidence_scorer=ConfidenceScorer(),
        max_retries=max_retries,
    )


class _InvalidJsonClient:
    """LLM client that always returns invalid JSON."""

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return "NOT VALID JSON {"


class _NonArrayClient:
    """LLM client that returns a JSON object instead of array."""

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return json.dumps({"error": "not an array"})


class _EmptyArrayClient:
    """LLM client that returns an empty JSON array."""

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return "[]"


class _MissingFieldsClient:
    """LLM client that returns claims with missing required fields."""

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return json.dumps(
            [
                {"claim_text": "Revenue was $5M"},
                {"claim_text": "Full claim", "claim_class": "FINANCIAL", "confidence": 0.9},
            ]
        )


class _InvalidClassClient:
    """LLM client that returns claims with invalid claim_class."""

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return json.dumps(
            [
                {"claim_text": "Claim A", "claim_class": "INVALID_TYPE", "confidence": 0.8},
                {"claim_text": "Claim B", "claim_class": "FINANCIAL", "confidence": 0.9},
            ]
        )


class TestLLMClaimExtractor:
    """Tests for LLMClaimExtractor."""

    def test_happy_path_deterministic_client(self) -> None:
        """DeterministicLLMClient produces valid claims from chunk content."""
        extractor = _build_extractor()
        result = extractor.extract_from_chunk(
            chunk_content="Revenue was $5M with 85% gross margin.",
            chunk_locator='{"page":1}',
            document_type="PDF",
            document_name="pitch_deck.pdf",
            span_ids=("span-001",),
        )

        assert isinstance(result, ChunkExtractionResult)
        assert len(result.drafts) >= 1
        assert len(result.errors) == 0
        for draft in result.drafts:
            assert draft.claim_text
            assert draft.claim_class in {
                "FINANCIAL",
                "TRACTION",
                "MARKET_SIZE",
                "COMPETITION",
                "TEAM",
                "LEGAL_TERMS",
                "TECHNICAL",
                "OTHER",
            }

    def test_invalid_json_returns_error(self) -> None:
        """Invalid JSON after retries produces structured error."""
        extractor = _build_extractor(llm_client=_InvalidJsonClient(), max_retries=2)
        result = extractor.extract_from_chunk(
            chunk_content="Some text.",
            chunk_locator='{"page":1}',
            document_type="PDF",
            document_name="doc.pdf",
            span_ids=("span-001",),
        )

        assert len(result.drafts) == 0
        assert len(result.errors) >= 1
        assert result.errors[0].code == "LLM_INVALID_JSON"

    def test_schema_mismatch_non_array(self) -> None:
        """Non-array JSON triggers schema mismatch error after retry."""
        extractor = _build_extractor(llm_client=_NonArrayClient(), max_retries=2)
        result = extractor.extract_from_chunk(
            chunk_content="Some text.",
            chunk_locator='{"page":1}',
            document_type="PDF",
            document_name="doc.pdf",
            span_ids=("span-001",),
        )

        assert len(result.drafts) == 0
        assert len(result.errors) >= 1
        assert result.errors[0].code == "SCHEMA_MISMATCH"

    def test_empty_array_is_valid(self) -> None:
        """Empty array (no claims found) is a valid result, not an error."""
        extractor = _build_extractor(llm_client=_EmptyArrayClient())
        result = extractor.extract_from_chunk(
            chunk_content="Some text.",
            chunk_locator='{"page":1}',
            document_type="PDF",
            document_name="doc.pdf",
            span_ids=("span-001",),
        )

        assert len(result.drafts) == 0
        assert len(result.errors) == 0

    def test_missing_fields_skipped(self) -> None:
        """Claims with missing fields trigger fail-closed: retry then error."""
        extractor = _build_extractor(llm_client=_MissingFieldsClient())
        result = extractor.extract_from_chunk(
            chunk_content="Some text.",
            chunk_locator='{"page":1}',
            document_type="PDF",
            document_name="doc.pdf",
            span_ids=("span-001",),
        )

        assert len(result.drafts) == 0
        assert len(result.errors) >= 1
        assert result.errors[0].code == "SCHEMA_MISMATCH"

    def test_invalid_claim_class_skipped(self) -> None:
        """Claims with invalid claim_class trigger fail-closed: retry then error."""
        extractor = _build_extractor(llm_client=_InvalidClassClient())
        result = extractor.extract_from_chunk(
            chunk_content="Some text.",
            chunk_locator='{"page":1}',
            document_type="PDF",
            document_name="doc.pdf",
            span_ids=("span-001",),
        )

        assert len(result.drafts) == 0
        assert len(result.errors) >= 1
        assert result.errors[0].code == "SCHEMA_MISMATCH"

    def test_confidence_scoring_applied(self) -> None:
        """Extracted drafts have Decimal confidence from scorer."""
        extractor = _build_extractor()
        result = extractor.extract_from_chunk(
            chunk_content="Revenue was $5M.",
            chunk_locator='{"page":1}',
            document_type="PDF",
            document_name="doc.pdf",
            span_ids=("span-001",),
        )

        assert len(result.drafts) >= 1
        for draft in result.drafts:
            assert isinstance(draft.extraction_confidence, Decimal)
            assert Decimal("0") <= draft.extraction_confidence <= Decimal("1")
            assert isinstance(draft.dhabt_score, Decimal)

    def test_span_ids_preserved(self) -> None:
        """Source span IDs are preserved in extracted drafts."""
        extractor = _build_extractor()
        result = extractor.extract_from_chunk(
            chunk_content="Revenue was $5M.",
            chunk_locator='{"page":1}',
            document_type="PDF",
            document_name="doc.pdf",
            span_ids=("span-001", "span-002"),
        )

        assert len(result.drafts) >= 1
        for draft in result.drafts:
            assert draft.span_id == "span-001"

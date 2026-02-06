"""LLM-backed claim extractor implementing the Extractor protocol.

Pipeline per chunk:
1. Build prompt from EXTRACT_CLAIMS_V1 template + chunk content
2. Call LLM with JSON mode
3. Validate output against schema (fail-closed on invalid JSON)
4. Score confidence via ConfidenceScorer
5. Convert to ExtractedClaimDraft objects

Failure handling per spec §7:
- Invalid JSON → retry up to max_retries, then return empty with structured error
- Schema mismatch → retry 1x, then return empty with structured error
- Empty array (NO_CLAIMS_FOUND) → return empty list, no error
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from idis.services.extraction.confidence.scorer import ConfidenceScorer, SourceTier
from idis.services.extraction.extractors.llm_client import LLMClient
from idis.services.extraction.service import ExtractedClaimDraft

logger = logging.getLogger(__name__)

REQUIRED_CLAIM_FIELDS = {"claim_text", "claim_class", "confidence"}
VALID_CLAIM_CLASSES = {
    "FINANCIAL",
    "TRACTION",
    "MARKET_SIZE",
    "COMPETITION",
    "TEAM",
    "LEGAL_TERMS",
    "TECHNICAL",
    "OTHER",
}


@dataclass
class ExtractionError:
    """Structured error from extraction attempt."""

    code: str
    message: str
    chunk_id: str | None = None
    attempt: int = 0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChunkExtractionResult:
    """Result of extracting claims from a single chunk."""

    drafts: list[ExtractedClaimDraft] = field(default_factory=list)
    errors: list[ExtractionError] = field(default_factory=list)


class LLMClaimExtractor:
    """LLM-backed claim extractor implementing the Extractor protocol.

    Uses an LLMClient to call the LLM, validates output, scores confidence,
    and converts to ExtractedClaimDraft objects.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        prompt_text: str,
        output_schema: dict[str, Any],
        confidence_scorer: ConfidenceScorer,
        *,
        max_retries: int = 3,
        source_tier: str = "TIER_4",
    ) -> None:
        """Initialize the LLM claim extractor.

        Args:
            llm_client: Provider-agnostic LLM client.
            prompt_text: Template text for EXTRACT_CLAIMS_V1.
            output_schema: JSON schema dict for output validation.
            confidence_scorer: Deterministic confidence scorer.
            max_retries: Maximum retry attempts for LLM failures.
            source_tier: Default source tier for confidence scoring.
        """
        self._llm_client = llm_client
        self._prompt_text = prompt_text
        self._output_schema = output_schema
        self._confidence_scorer = confidence_scorer
        self._max_retries = max_retries
        self._source_tier = SourceTier(source_tier)

    def extract(
        self,
        tenant_id: str,
        deal_id: str,
        spans: list[dict[str, Any]],
    ) -> list[ExtractedClaimDraft]:
        """Extract claim drafts from spans via LLM.

        Args:
            tenant_id: Tenant context.
            deal_id: Deal context.
            spans: List of span dicts with text_excerpt and metadata.

        Returns:
            List of extracted claim drafts.
        """
        all_drafts: list[ExtractedClaimDraft] = []

        for span in sorted(spans, key=lambda s: s.get("span_id", "")):
            text = span.get("text_excerpt", "")
            if not text:
                continue

            result = self._extract_from_span(span)
            all_drafts.extend(result.drafts)

        return all_drafts

    def extract_from_chunk(
        self,
        *,
        chunk_content: str,
        chunk_locator: str,
        document_type: str,
        document_name: str,
        span_ids: tuple[str, ...],
    ) -> ChunkExtractionResult:
        """Extract claims from a single chunk.

        Args:
            chunk_content: Combined text content of the chunk.
            chunk_locator: Canonical JSON locator string.
            document_type: PDF, XLSX, DOCX, PPTX.
            document_name: Original filename.
            span_ids: Source span UUIDs for provenance.

        Returns:
            ChunkExtractionResult with drafts and any errors.
        """
        prompt = self._build_prompt(
            document_type=document_type,
            document_name=document_name,
            chunk_locator=chunk_locator,
            chunk_content=chunk_content,
        )

        for attempt in range(self._max_retries):
            raw_response = self._llm_client.call(prompt, json_mode=True)

            parsed, parse_error = self._parse_json(raw_response, attempt)
            if parse_error is not None:
                if attempt == self._max_retries - 1:
                    return ChunkExtractionResult(errors=[parse_error])
                continue

            validated, schema_error = self._validate_schema(parsed, attempt)
            if schema_error is not None:
                if attempt >= 1:
                    return ChunkExtractionResult(errors=[schema_error])
                continue

            if not validated:
                return ChunkExtractionResult()

            drafts = self._convert_to_drafts(validated, span_ids=span_ids)
            return ChunkExtractionResult(drafts=drafts)

        return ChunkExtractionResult(
            errors=[
                ExtractionError(
                    code="MAX_RETRIES_EXCEEDED",
                    message="Extraction failed after max retries",
                    attempt=self._max_retries,
                )
            ]
        )

    def _extract_from_span(self, span: dict[str, Any]) -> ChunkExtractionResult:
        """Extract claims from a single span (used by Extractor protocol).

        Args:
            span: Span dict with text_excerpt and metadata.

        Returns:
            ChunkExtractionResult with drafts.
        """
        text = span.get("text_excerpt", "")
        span_id = span.get("span_id", "")
        locator = span.get("locator", {})

        prompt = self._build_prompt(
            document_type="UNKNOWN",
            document_name="unknown",
            chunk_locator=json.dumps(locator, sort_keys=True),
            chunk_content=text,
        )

        for attempt in range(self._max_retries):
            raw_response = self._llm_client.call(prompt, json_mode=True)

            parsed, parse_error = self._parse_json(raw_response, attempt)
            if parse_error is not None:
                if attempt == self._max_retries - 1:
                    return ChunkExtractionResult(errors=[parse_error])
                continue

            validated, schema_error = self._validate_schema(parsed, attempt)
            if schema_error is not None:
                if attempt >= 1:
                    return ChunkExtractionResult(errors=[schema_error])
                continue

            if not validated:
                return ChunkExtractionResult()

            drafts = self._convert_to_drafts(validated, span_ids=(span_id,))
            return ChunkExtractionResult(drafts=drafts)

        return ChunkExtractionResult()

    def _build_prompt(
        self,
        *,
        document_type: str,
        document_name: str,
        chunk_locator: str,
        chunk_content: str,
    ) -> str:
        """Build the full prompt from template and variables.

        Args:
            document_type: Document type string.
            document_name: Original filename.
            chunk_locator: Canonical JSON locator.
            chunk_content: Text content.

        Returns:
            Full prompt string with variables substituted.
        """
        schema_str = json.dumps(self._output_schema, indent=2)
        return (
            self._prompt_text.replace("{{document_type}}", document_type)
            .replace("{{document_name}}", document_name)
            .replace("{{chunk_locator}}", chunk_locator)
            .replace("{{chunk_content}}", chunk_content)
            .replace("{{output_schema}}", schema_str)
        )

    def _parse_json(self, raw: str, attempt: int) -> tuple[Any | None, ExtractionError | None]:
        """Parse raw LLM response as JSON.

        Args:
            raw: Raw response string.
            attempt: Current attempt number.

        Returns:
            Tuple of (parsed_data, error). One will be None.
        """
        try:
            parsed = json.loads(raw)
            return parsed, None
        except json.JSONDecodeError as e:
            logger.warning("LLM returned invalid JSON (attempt %d): %s", attempt + 1, e)
            return None, ExtractionError(
                code="LLM_INVALID_JSON",
                message=f"LLM returned invalid JSON: {e}",
                attempt=attempt + 1,
                details={"response_preview": raw[:500]},
            )

    def _validate_schema(
        self, parsed: Any, attempt: int
    ) -> tuple[list[dict[str, Any]] | None, ExtractionError | None]:
        """Validate parsed JSON against expected schema.

        Args:
            parsed: Parsed JSON data.
            attempt: Current attempt number.

        Returns:
            Tuple of (validated_claims, error). One may be None.
        """
        if not isinstance(parsed, list):
            return None, ExtractionError(
                code="SCHEMA_MISMATCH",
                message="Expected JSON array, got " + type(parsed).__name__,
                attempt=attempt + 1,
            )

        validated: list[dict[str, Any]] = []
        invalid_count = 0
        for item in parsed:
            if not isinstance(item, dict):
                invalid_count += 1
                continue
            missing = REQUIRED_CLAIM_FIELDS - set(item.keys())
            if missing:
                logger.warning("Claim missing required fields: %s", missing)
                invalid_count += 1
                continue
            if item.get("claim_class") not in VALID_CLAIM_CLASSES:
                logger.warning("Invalid claim_class: %s", item.get("claim_class"))
                invalid_count += 1
                continue
            validated.append(item)

        if invalid_count > 0:
            return None, ExtractionError(
                code="SCHEMA_MISMATCH",
                message=(
                    f"{invalid_count} item(s) failed schema validation "
                    f"(missing fields or invalid claim_class)"
                ),
                attempt=attempt + 1,
                details={"invalid_count": invalid_count, "valid_count": len(validated)},
            )

        return validated, None

    def _convert_to_drafts(
        self,
        claims: list[dict[str, Any]],
        *,
        span_ids: tuple[str, ...],
    ) -> list[ExtractedClaimDraft]:
        """Convert validated claim dicts to ExtractedClaimDraft objects.

        Args:
            claims: Validated claim dicts from LLM.
            span_ids: Source span UUIDs for provenance.

        Returns:
            List of ExtractedClaimDraft objects with confidence scored.
        """
        drafts: list[ExtractedClaimDraft] = []
        primary_span_id = span_ids[0] if span_ids else ""

        for claim in claims:
            model_conf = Decimal(str(claim.get("confidence", 0.5)))
            scored_confidence = self._confidence_scorer.score(
                source_tier=self._source_tier,
                extraction_clarity=Decimal("0.7"),
                value_precision=Decimal("0.6"),
                context_quality=Decimal("0.7"),
                model_confidence=model_conf,
            )

            value_struct = claim.get("value_struct")

            drafts.append(
                ExtractedClaimDraft(
                    claim_text=claim["claim_text"],
                    claim_class=claim["claim_class"],
                    extraction_confidence=scored_confidence,
                    dhabt_score=scored_confidence,
                    span_id=primary_span_id,
                    predicate=None,
                    value=value_struct,
                )
            )

        return drafts

"""No-Free-Facts Validator - enforces evidence-backed facts in IC-bound outputs.

HARD GATE: Any factual assertion in IC-bound outputs MUST reference:
- claim_id (with Sanad chain), OR
- calc_id (with Calc-Sanad lineage)

If not, the output MUST be labeled SUBJECTIVE or rejected.

Canonical deliverable structure (enforced):
{
  "deliverable_type": "IC_MEMO",
  "sections": [
    {
      "text": "Revenue grew 20% YoY.",
      "is_factual": true,
      "is_subjective": false,
      "referenced_claim_ids": ["<uuid>"],
      "referenced_calc_ids": []
    }
  ]
}

Rules:
- If is_subjective == true → No-Free-Facts does not apply to that section.
- If is_factual == true and both referenced_claim_ids and referenced_calc_ids are empty
  → ERROR and overall FAIL.
- Refs elsewhere in the document do NOT satisfy this section (per-section validation).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from idis.validators.schema_validator import ValidationError, ValidationResult

# Patterns that indicate factual assertions (conservative heuristics)
# Applied per-section when is_factual is missing or false
FACTUAL_PATTERNS = [
    # Currency amounts
    r"\$[\d,]+(?:\.\d+)?(?:\s*(?:M|B|K|million|billion|thousand))?",
    r"€[\d,]+(?:\.\d+)?(?:\s*(?:M|B|K|million|billion|thousand))?",
    # Percentages
    r"\d+(?:\.\d+)?%",
    r"\d+(?:\.\d+)?\s+percent",
    # Growth rates
    r"\d+x\s+(?:growth|increase|revenue|ARR)",
    r"grew\s+\d+(?:\.\d+)?%",
    # Market size claims
    r"(?:TAM|SAM|SOM)\s+(?:of\s+)?\$?[\d,]+",
    r"market\s+(?:size|opportunity)\s+(?:of\s+)?\$?[\d,]+",
    # Revenue/ARR/financial metrics
    r"(?:ARR|MRR|revenue|GM|gross\s+margin|churn)\s+(?:of\s+|is\s+)?\$?[\d,]+",
    r"\$?[\d,]+(?:\.\d+)?(?:\s*(?:M|B|K))?\s+(?:ARR|MRR|revenue)",
    # User/customer metrics
    r"[\d,]+\s+(?:users|customers|clients|subscribers)",
    r"(?:DAU|MAU|WAU)\s+(?:of\s+)?[\d,]+",
    # Time-based claims (specific dates/periods)
    r"(?:in|by|since|as of)\s+(?:Q[1-4]\s+)?\d{4}",
    r"(?:FY|CY)\s*\d{2,4}",
    # Explicit fact markers (plain text factual assertions)
    r"(?:the\s+)?fact\s+(?:is|that)",
    r"(?:it\s+is\s+)?(?:a\s+)?(?:known|established|confirmed)\s+fact",
    # Funding/investment cues
    r"(?:raised|funding|Series\s+[A-Z]|valuation)\s+(?:of\s+)?\$?[\d,]+",
    r"\$[\d,]+(?:\.\d+)?(?:\s*(?:M|B|K))?\s+(?:raised|valuation|funding)",
]

# Compiled patterns for efficiency
FACTUAL_REGEXES = [re.compile(p, re.IGNORECASE) for p in FACTUAL_PATTERNS]


# =============================================================================
# SEMANTIC RULE LIBRARY (Phase POST-5.2)
# =============================================================================
# Subject-predicate patterns for semantic factual assertion detection.
# These patterns are deterministic (rules only, no external models).
# Format: (subject_pattern, predicate_pattern, description)

SEMANTIC_SUBJECT_PREDICATES: list[tuple[str, str, str]] = [
    # Company performance predicates
    (
        r"\b(?:company|startup|firm|business)\b",
        r"\b(?:achieved|reached|hit|exceeded|generated|reported)\b",
        "company_achievement",
    ),
    (
        r"\b(?:revenue|ARR|MRR|sales)\b",
        r"\b(?:grew|increased|decreased|declined|reached|exceeded)\b",
        "revenue_change",
    ),
    (
        r"\b(?:company|startup|we|they)\b",
        r"\b(?:raised|secured|closed)\b.*\b(?:funding|round|capital)\b",
        "funding_event",
    ),
    # Metric predicates
    (
        r"\b(?:gross\s+margin|GM|margin)\b",
        r"\b(?:is|was|stands\s+at|improved\s+to)\b",
        "margin_state",
    ),
    (
        r"\b(?:churn|retention|NRR)\b",
        r"\b(?:is|was|improved|decreased|stands\s+at)\b",
        "retention_metric",
    ),
    (r"\b(?:CAC|LTV|payback)\b", r"\b(?:is|was|equals|improved)\b", "unit_economics"),
    # Market predicates
    (
        r"\b(?:TAM|SAM|SOM|market)\b",
        r"\b(?:is|was|estimated\s+at|valued\s+at|represents)\b",
        "market_size",
    ),
    (r"\b(?:market\s+share|share)\b", r"\b(?:is|was|reached|grew\s+to)\b", "market_share"),
    # Team predicates
    (
        r"\b(?:team|headcount|employees|staff)\b",
        r"\b(?:grew|increased|expanded|consists\s+of|numbers)\b",
        "team_growth",
    ),
    (
        r"\b(?:founder|CEO|CTO|CFO)\b",
        r"\b(?:previously|formerly|worked\s+at|founded|built)\b",
        "founder_background",
    ),
    # Customer predicates
    (
        r"\b(?:customers|clients|users|subscribers)\b",
        r"\b(?:grew|increased|reached|exceeded|number)\b",
        "customer_growth",
    ),
    (
        r"\b(?:DAU|MAU|WAU|active\s+users)\b",
        r"\b(?:is|was|reached|exceeded|grew)\b",
        "engagement_metric",
    ),
    # Valuation predicates
    (
        r"\b(?:valuation|pre-money|post-money)\b",
        r"\b(?:is|was|valued\s+at|set\s+at)\b",
        "valuation_claim",
    ),
    (r"\b(?:multiple|ratio)\b", r"\b(?:is|was|equals|stands\s+at)\b", "multiple_claim"),
    # Timeline predicates
    (
        r"\b(?:company|startup|business)\b",
        r"\b(?:founded|started|launched|incorporated)\s+(?:in|on)\b",
        "founding_date",
    ),
    (
        r"\b(?:product|platform|service)\b",
        r"\b(?:launched|released|went\s+live)\s+(?:in|on)\b",
        "launch_date",
    ),
    # Competitive predicates
    (r"\b(?:competitor|competition)\b", r"\b(?:has|have|holds|controls)\b", "competitive_position"),
    (r"\b(?:we|company|startup)\b", r"\b(?:outperform|beat|lead|dominate)\b", "competitive_claim"),
]

# Compile semantic patterns
SEMANTIC_RULES: list[tuple[re.Pattern[str], re.Pattern[str], str]] = [
    (re.compile(subj, re.IGNORECASE), re.compile(pred, re.IGNORECASE), desc)
    for subj, pred, desc in SEMANTIC_SUBJECT_PREDICATES
]


@dataclass
class SemanticMatch:
    """A semantic pattern match (subject + predicate)."""

    subject: str
    predicate: str
    rule_name: str
    position: int


@dataclass
class FactualAssertion:
    """A detected factual assertion in text."""

    text: str
    position: int
    pattern_matched: str


def validate_no_free_facts(data: Any) -> ValidationResult:
    """Validate a deliverable for No-Free-Facts compliance (public function API).

    Args:
        data: Deliverable JSON data

    Returns:
        ValidationResult with pass (bool), errors, warnings
    """
    validator = NoFreeFactsValidator()
    return validator.validate(data)


class NoFreeFactsValidator:
    """Validates that IC-bound outputs have no unreferenced factual assertions.

    This validator enforces the No-Free-Facts trust invariant:
    - All factual assertions MUST be backed by claim_id or calc_id
    - Outputs without proper references are REJECTED (fail closed)
    - Subjective sections are allowed if explicitly marked
    - Per-section validation: refs elsewhere do NOT satisfy a section

    Canonical input structure (preferred):
    {
      "deliverable_type": "IC_MEMO",
      "sections": [
        {
          "text": "...",
          "is_factual": true/false,
          "is_subjective": true/false,
          "referenced_claim_ids": [...],
          "referenced_calc_ids": [...]
        }
      ]
    }
    """

    def __init__(self, enable_semantic_rules: bool = True) -> None:
        """Initialize the validator.

        Args:
            enable_semantic_rules: If True, enable semantic subject-predicate
                pattern matching in addition to regex patterns. Defaults to True.
        """
        self._enable_semantic_rules = enable_semantic_rules

    def _extract_semantic_matches(self, text: str) -> list[SemanticMatch]:
        """Extract semantic subject-predicate pattern matches from text.

        Phase POST-5.2: Semantic rule library for enhanced factual detection.
        Uses deterministic rules only (no external models).

        Args:
            text: Text to analyze.

        Returns:
            List of semantic matches found.
        """
        if not self._enable_semantic_rules:
            return []

        matches: list[SemanticMatch] = []
        seen_positions: set[int] = set()

        for subj_pattern, pred_pattern, rule_name in SEMANTIC_RULES:
            # Find subject matches
            for subj_match in subj_pattern.finditer(text):
                subj_end = subj_match.end()
                # Look for predicate within 50 characters after subject
                search_window = text[subj_end : subj_end + 100]
                pred_match = pred_pattern.search(search_window)

                if pred_match:
                    pos = subj_match.start()
                    if pos not in seen_positions:
                        seen_positions.add(pos)
                        matches.append(
                            SemanticMatch(
                                subject=subj_match.group(),
                                predicate=pred_match.group(),
                                rule_name=rule_name,
                                position=pos,
                            )
                        )

        return sorted(matches, key=lambda m: m.position)

    def _extract_factual_assertions(self, text: str) -> list[FactualAssertion]:
        """Extract potential factual assertions from text using heuristic patterns.

        This is a FALLBACK method used only when is_factual field is not provided.
        """
        assertions: list[FactualAssertion] = []
        seen_positions: set[tuple[int, int]] = set()

        for regex in FACTUAL_REGEXES:
            for match in regex.finditer(text):
                pos_key = (match.start(), match.end())
                if pos_key not in seen_positions:
                    seen_positions.add(pos_key)
                    assertions.append(
                        FactualAssertion(
                            text=match.group(),
                            position=match.start(),
                            pattern_matched=regex.pattern,
                        )
                    )

        return sorted(assertions, key=lambda a: a.position)

    def _is_section_subjective(self, section: dict[str, Any]) -> bool:
        """Check if a section is explicitly marked as subjective.

        Returns computed boolean based on explicit checks - no default-pass.
        """
        # Explicit is_subjective flag takes precedence
        has_subjective_flag = section.get("is_subjective") is True

        # Check for subjective label/type
        section_type = section.get("type", "").upper()
        has_subjective_type = section_type == "SUBJECTIVE"

        label = section.get("label", "").upper()
        has_subjective_label = "SUBJECTIVE" in label

        # Return computed boolean - section is subjective if ANY condition is met
        is_subjective = has_subjective_flag or has_subjective_type or has_subjective_label
        return is_subjective

    def _get_section_refs(self, section: dict[str, Any]) -> tuple[list[str], list[str]]:
        """Get referenced_claim_ids and referenced_calc_ids from a section.

        Only returns refs that are LOCAL to this section (per-section validation).
        """
        claim_ids: list[str] = []
        calc_ids: list[str] = []

        # Primary fields for per-section refs
        if "referenced_claim_ids" in section:
            refs = section["referenced_claim_ids"]
            if isinstance(refs, list):
                claim_ids.extend(str(r) for r in refs if r)

        if "referenced_calc_ids" in section:
            refs = section["referenced_calc_ids"]
            if isinstance(refs, list):
                calc_ids.extend(str(r) for r in refs if r)

        # Also accept alternative field names for compatibility
        if "claim_ids" in section:
            refs = section["claim_ids"]
            if isinstance(refs, list):
                claim_ids.extend(str(r) for r in refs if r)

        if "calc_ids" in section:
            refs = section["calc_ids"]
            if isinstance(refs, list):
                calc_ids.extend(str(r) for r in refs if r)

        return claim_ids, calc_ids

    def _looks_like_fact(self, text: str) -> list[FactualAssertion]:
        """Check if text looks like it contains factual assertions.

        Conservative heuristic detection for:
        - Numeric values (%, $, years, metrics)
        - Explicit fact markers ("the fact is", etc.)
        - Finance cues (raised, funding, Series A/B, valuation)
        - Semantic subject-predicate patterns (Phase POST-5.2)

        Returns list of detected factual assertions (empty if none found).
        """
        # Get regex-based assertions
        assertions = self._extract_factual_assertions(text)

        # Get semantic matches and convert to FactualAssertion format
        semantic_matches = self._extract_semantic_matches(text)
        seen_positions = {a.position for a in assertions}

        for match in semantic_matches:
            if match.position not in seen_positions:
                seen_positions.add(match.position)
                assertions.append(
                    FactualAssertion(
                        text=f"{match.subject} {match.predicate}",
                        position=match.position,
                        pattern_matched=f"semantic:{match.rule_name}",
                    )
                )

        return sorted(assertions, key=lambda a: a.position)

    def _validate_section(self, section: dict[str, Any], index: int) -> list[ValidationError]:
        """Validate a single section for No-Free-Facts compliance.

        Per-section logic (applied to EVERY section):
        1. If is_subjective == true → skip (return ok)
        2. Else if is_factual == true → must have local refs or FAIL
        3. Else (is_factual missing OR is_factual == false) → run heuristics:
           - If text looks factual and no local refs → FAIL
           - If text does not look factual → allow without refs

        This ensures NO bypass via mixed structured/unstructured or mislabeling.
        """
        errors: list[ValidationError] = []

        # RULE 1: If is_subjective == true, skip No-Free-Facts for this section
        if self._is_section_subjective(section):
            return errors

        # Get local refs for this section
        claim_ids, calc_ids = self._get_section_refs(section)
        has_local_refs = bool(claim_ids or calc_ids)

        # Get text content
        text = section.get("text", "") or section.get("content", "")

        # RULE 2: If is_factual == true, must have local refs
        is_factual_explicit = section.get("is_factual")
        if is_factual_explicit is True:
            if not has_local_refs:
                display_text = text[:50] + "..." if len(text) > 50 else text
                errors.append(
                    ValidationError(
                        code="NO_FREE_FACTS_UNREFERENCED_FACT",
                        message=(
                            f"Factual section (is_factual=true) has no local "
                            f"referenced_claim_ids or referenced_calc_ids. "
                            f"Text: '{display_text}'"
                        ),
                        path=f"$.sections[{index}]",
                    )
                )
            return errors

        # RULE 3: is_factual missing OR is_factual == false → run heuristics
        # This catches mislabeling (is_factual=false but text is "$5M revenue")
        # and unstructured sections in mixed documents
        if text:
            assertions = self._looks_like_fact(text)
            if assertions and not has_local_refs:
                for assertion in assertions:
                    errors.append(
                        ValidationError(
                            code="NO_FREE_FACTS_VIOLATION",
                            message=(
                                f"Factual assertion '{assertion.text}' found "
                                f"without local referenced_claim_ids or "
                                f"referenced_calc_ids in this section"
                            ),
                            path=f"$.sections[{index}].text",
                        )
                    )

        return errors

    def _validate_sections(self, sections: list[Any]) -> list[ValidationError]:
        """Validate all sections with per-section enforcement.

        NO global skip of heuristics - each section is validated independently.
        """
        errors: list[ValidationError] = []

        for i, section in enumerate(sections):
            if not isinstance(section, dict):
                continue
            section_errors = self._validate_section(section, i)
            errors.extend(section_errors)

        return errors

    def _validate_top_level_content(self, data: dict[str, Any]) -> list[ValidationError]:
        """Validate top-level content field (legacy format)."""
        errors: list[ValidationError] = []

        top_content = data.get("content", "")
        if top_content and isinstance(top_content, str):
            assertions = self._looks_like_fact(top_content)
            if assertions:
                # Check for any top-level refs
                top_claim_ids = data.get("supported_claim_ids", []) or data.get("claim_ids", [])
                top_calc_ids = data.get("supported_calc_ids", []) or data.get("calc_ids", [])

                if not top_claim_ids and not top_calc_ids:
                    for assertion in assertions:
                        errors.append(
                            ValidationError(
                                code="NO_FREE_FACTS_VIOLATION",
                                message=(
                                    f"Factual assertion '{assertion.text}' found "
                                    f"without any claim_id or calc_id references"
                                ),
                                path="$.content",
                            )
                        )

        return errors

    def validate(self, data: Any) -> ValidationResult:
        """Validate a deliverable for No-Free-Facts compliance.

        FAIL-CLOSED BEHAVIOR (per-section, no global bypasses):
        - If is_subjective == true → skip that section
        - If is_factual == true and no local refs → FAIL
        - If is_factual missing/false → run heuristics, if factual and no refs → FAIL
        - Refs elsewhere in document do NOT satisfy a section

        Args:
            data: Deliverable JSON data with canonical structure

        Returns:
            ValidationResult - FAILS if unreferenced factual assertions found
        """
        # Fail closed on None or non-dict
        if data is None:
            return ValidationResult.fail_closed("Data is None - cannot validate")

        if not isinstance(data, dict):
            return ValidationResult.fail_closed("Data must be a dictionary")

        errors: list[ValidationError] = []

        # Validate sections with per-section enforcement
        # NO global skip of heuristics - each section validated independently
        sections = data.get("sections", [])
        if isinstance(sections, list) and sections:
            section_errors = self._validate_sections(sections)
            errors.extend(section_errors)

        # Also validate top-level content (legacy format)
        top_level_errors = self._validate_top_level_content(data)
        errors.extend(top_level_errors)

        if errors:
            return ValidationResult.fail(errors)

        return ValidationResult.success()

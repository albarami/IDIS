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

# Patterns that indicate factual assertions (used as FALLBACK heuristic only)
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
    # Revenue/ARR claims
    r"(?:ARR|MRR|revenue)\s+(?:of\s+)?\$?[\d,]+",
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
]

# Compiled patterns for efficiency
FACTUAL_REGEXES = [re.compile(p, re.IGNORECASE) for p in FACTUAL_PATTERNS]


@dataclass
class FactualAssertion:
    """A detected factual assertion in text."""

    text: str
    position: int
    pattern_matched: str


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

    def __init__(self) -> None:
        """Initialize the validator."""
        pass

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
        """Check if a section is explicitly marked as subjective."""
        # Explicit is_subjective flag takes precedence
        if section.get("is_subjective") is True:
            return True

        # Check for subjective label/type
        section_type = section.get("type", "").upper()
        if section_type == "SUBJECTIVE":
            return True

        label = section.get("label", "").upper()
        return "SUBJECTIVE" in label

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

    def _validate_structured_sections(
        self, sections: list[Any]
    ) -> tuple[list[ValidationError], bool]:
        """Validate sections with explicit is_factual/is_subjective fields.

        Returns:
            Tuple of (errors, has_structured_sections)
        """
        errors: list[ValidationError] = []
        has_structured = False

        for i, section in enumerate(sections):
            if not isinstance(section, dict):
                continue

            # Check if this section has explicit structure
            has_is_factual = "is_factual" in section
            has_is_subjective = "is_subjective" in section

            if has_is_factual or has_is_subjective:
                has_structured = True

            # RULE: If is_subjective == true, skip No-Free-Facts checks
            if self._is_section_subjective(section):
                continue

            # RULE: If is_factual == true, must have LOCAL refs
            is_factual = section.get("is_factual", False)
            if is_factual:
                claim_ids, calc_ids = self._get_section_refs(section)

                if not claim_ids and not calc_ids:
                    # HARD ERROR: Factual section without local refs
                    section_text = section.get("text", "<no text>")
                    # Truncate for display
                    display_text = (
                        section_text[:50] + "..." if len(section_text) > 50 else section_text
                    )
                    errors.append(
                        ValidationError(
                            code="NO_FREE_FACTS_UNREFERENCED_FACT",
                            message=(
                                f"Factual section (is_factual=true) has no local "
                                f"referenced_claim_ids or referenced_calc_ids. "
                                f"Text: '{display_text}'"
                            ),
                            path=f"$.sections[{i}]",
                        )
                    )

        return errors, has_structured

    def _validate_fallback_heuristic(self, data: dict[str, Any]) -> list[ValidationError]:
        """Fallback validation using heuristic pattern detection.

        Used when sections don't have explicit is_factual/is_subjective fields.
        This is more conservative - any detected factual assertion without
        local refs in the same section is an ERROR.
        """
        errors: list[ValidationError] = []

        # Check sections first
        sections = data.get("sections", [])
        if isinstance(sections, list):
            for i, section in enumerate(sections):
                if not isinstance(section, dict):
                    continue

                # Skip subjective sections
                if self._is_section_subjective(section):
                    continue

                # Get text content
                text = section.get("text", "") or section.get("content", "")
                if not text:
                    continue

                # Extract factual assertions using heuristics
                assertions = self._extract_factual_assertions(text)
                if not assertions:
                    continue

                # Get LOCAL refs for this section
                claim_ids, calc_ids = self._get_section_refs(section)

                # If factual assertions found but no local refs -> ERROR
                if not claim_ids and not calc_ids:
                    for assertion in assertions:
                        errors.append(
                            ValidationError(
                                code="NO_FREE_FACTS_VIOLATION",
                                message=(
                                    f"Factual assertion '{assertion.text}' found "
                                    f"without local referenced_claim_ids or "
                                    f"referenced_calc_ids in this section"
                                ),
                                path=f"$.sections[{i}].text",
                            )
                        )

        # Also check top-level content field (legacy format)
        top_content = data.get("content", "")
        if top_content and isinstance(top_content, str):
            assertions = self._extract_factual_assertions(top_content)
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

        FAIL-CLOSED BEHAVIOR:
        - If is_factual=true and no local refs → FAIL
        - If heuristic detects facts and no local refs → FAIL
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

        # Get sections
        sections = data.get("sections", [])

        if isinstance(sections, list) and sections:
            # Try structured validation first (preferred)
            structured_errors, has_structured = self._validate_structured_sections(sections)
            errors.extend(structured_errors)

            # If no structured fields found, use fallback heuristic
            if not has_structured:
                fallback_errors = self._validate_fallback_heuristic(data)
                errors.extend(fallback_errors)
        else:
            # No sections - use fallback heuristic on any content
            fallback_errors = self._validate_fallback_heuristic(data)
            errors.extend(fallback_errors)

        if errors:
            return ValidationResult.fail(errors)

        return ValidationResult.success()

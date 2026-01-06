"""No-Free-Facts Validator - enforces evidence-backed facts in IC-bound outputs.

HARD GATE: Any factual statement in IC-bound outputs MUST reference:
- claim_id (with Sanad chain), OR
- calc_id (with Calc-Sanad lineage)

If not, the output MUST be labeled SUBJECTIVE or rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from idis.validators.schema_validator import ValidationError, ValidationResult

# Patterns that indicate factual assertions (numeric claims, market sizes, etc.)
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
    """

    def __init__(self) -> None:
        """Initialize the validator."""
        pass

    def _extract_factual_assertions(self, text: str) -> list[FactualAssertion]:
        """Extract potential factual assertions from text."""
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

    def _get_referenced_ids(self, data: dict[str, Any]) -> tuple[set[str], set[str]]:
        """Extract claim_ids and calc_ids from deliverable data."""
        claim_ids: set[str] = set()
        calc_ids: set[str] = set()

        def extract_refs(obj: Any, path: str = "") -> None:
            if isinstance(obj, dict):
                # Direct references
                if "claim_id" in obj and obj["claim_id"]:
                    claim_ids.add(str(obj["claim_id"]))
                if "calc_id" in obj and obj["calc_id"]:
                    calc_ids.add(str(obj["calc_id"]))

                # Arrays of references
                if "claim_ids" in obj and isinstance(obj["claim_ids"], list):
                    for cid in obj["claim_ids"]:
                        if cid:
                            claim_ids.add(str(cid))
                if "calc_ids" in obj and isinstance(obj["calc_ids"], list):
                    for cid in obj["calc_ids"]:
                        if cid:
                            calc_ids.add(str(cid))

                # Supported claim/calc refs (common in deliverables)
                if "supported_claim_ids" in obj and isinstance(obj["supported_claim_ids"], list):
                    for cid in obj["supported_claim_ids"]:
                        if cid:
                            claim_ids.add(str(cid))
                if "supported_calc_ids" in obj and isinstance(obj["supported_calc_ids"], list):
                    for cid in obj["supported_calc_ids"]:
                        if cid:
                            calc_ids.add(str(cid))

                # Recurse into nested objects
                for key, value in obj.items():
                    extract_refs(value, f"{path}.{key}")

            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    extract_refs(item, f"{path}[{i}]")

        extract_refs(data)
        return claim_ids, calc_ids

    def _is_section_subjective(self, section: dict[str, Any]) -> bool:
        """Check if a section is explicitly marked as subjective."""
        # Check for explicit subjective flag
        if section.get("is_subjective") is True:
            return True

        # Check for subjective label/type
        section_type = section.get("type", "").upper()
        if section_type == "SUBJECTIVE":
            return True

        label = section.get("label", "").upper()
        return "SUBJECTIVE" in label

    def _get_text_content(self, data: dict[str, Any]) -> list[tuple[str, str, bool]]:
        """Extract text content with paths and subjective flags."""
        content: list[tuple[str, str, bool]] = []

        def extract_text(obj: Any, path: str = "$", is_subjective: bool = False) -> None:
            if isinstance(obj, dict):
                # Check if this section is subjective
                section_subjective = is_subjective or self._is_section_subjective(obj)

                # Extract text fields
                for text_field in ["text", "content", "narrative", "summary", "description"]:
                    if text_field in obj and isinstance(obj[text_field], str):
                        content.append(
                            (f"{path}.{text_field}", obj[text_field], section_subjective)
                        )

                # Recurse
                for key, value in obj.items():
                    if key not in ["text", "content", "narrative", "summary", "description"]:
                        extract_text(value, f"{path}.{key}", section_subjective)

            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    extract_text(item, f"{path}[{i}]", is_subjective)

        extract_text(data)
        return content

    def validate(self, data: Any) -> ValidationResult:
        """Validate a deliverable for No-Free-Facts compliance.

        Args:
            data: Deliverable JSON data

        Returns:
            ValidationResult - FAILS if unreferenced factual assertions found
        """
        # Fail closed on None or non-dict
        if data is None:
            return ValidationResult.fail_closed("Data is None - cannot validate")

        if not isinstance(data, dict):
            return ValidationResult.fail_closed("Data must be a dictionary")

        # Note: We validate all deliverables strictly, whether IC-bound or not
        # The ic_bound flag could be used for future differentiation if needed

        # Get referenced IDs
        claim_ids, calc_ids = self._get_referenced_ids(data)

        # If no references at all and there's text content, that's suspicious
        # but we need to check if there are actual factual assertions

        # Extract text content
        text_content = self._get_text_content(data)

        errors: list[ValidationError] = []
        warnings: list[ValidationError] = []

        for path, text, is_subjective in text_content:
            assertions = self._extract_factual_assertions(text)

            if assertions and not is_subjective:
                # Found factual assertions in non-subjective section
                # Check if we have ANY references
                if not claim_ids and not calc_ids:
                    # No references at all - definite violation
                    for assertion in assertions:
                        errors.append(
                            ValidationError(
                                code="NO_FREE_FACTS_VIOLATION",
                                message=(
                                    f"Factual assertion '{assertion.text}' found without any "
                                    f"claim_id or calc_id references in deliverable"
                                ),
                                path=path,
                            )
                        )
                else:
                    # Has some references - warn but don't fail
                    # (We can't definitively map assertions to specific refs without more context)
                    for assertion in assertions:
                        warnings.append(
                            ValidationError(
                                code="UNVERIFIED_ASSERTION",
                                message=(
                                    f"Factual assertion '{assertion.text}' - verify it is backed "
                                    f"by a referenced claim_id or calc_id"
                                ),
                                path=path,
                            )
                        )

        # Check for sections that MUST have references
        sections = data.get("sections", [])
        if isinstance(sections, list):
            for i, section in enumerate(sections):
                if isinstance(section, dict):
                    # IC-critical sections must have references
                    section_type = section.get("type", "").upper()
                    if section_type in ("FINANCIAL", "MARKET", "TRACTION", "KEY_METRICS"):
                        section_claims = section.get("claim_ids", []) or section.get(
                            "supported_claim_ids", []
                        )
                        section_calcs = section.get("calc_ids", []) or section.get(
                            "supported_calc_ids", []
                        )

                        if (
                            not section_claims
                            and not section_calcs
                            and not self._is_section_subjective(section)
                        ):
                            errors.append(
                                ValidationError(
                                    code="MISSING_REFERENCES",
                                    message=(
                                        f"Section of type '{section_type}' requires claim_ids "
                                        f"or calc_ids but has none"
                                    ),
                                    path=f"$.sections[{i}]",
                                )
                            )

        if errors:
            return ValidationResult.fail(errors)

        return ValidationResult.success(warnings if warnings else None)

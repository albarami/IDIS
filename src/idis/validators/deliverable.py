"""Deliverable Validator — v6.3 Phase 6.1

Validates deliverables for No-Free-Facts compliance at export time.

HARD GATE: Every DeliverableFact with is_factual=True MUST have non-empty claim_refs.

Trust invariants:
- Fail-closed: any factual output lacks refs → reject
- Per-section validation: refs elsewhere do NOT satisfy a section
- Narrative strings also validated using No-Free-Facts heuristics
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from idis.validators.no_free_facts import NoFreeFactsValidator
from idis.validators.schema_validator import ValidationError

if TYPE_CHECKING:
    pass


class DeliverableValidationError(Exception):
    """Deliverable validation failed (No-Free-Facts violation)."""

    def __init__(
        self,
        message: str,
        code: str = "DELIVERABLE_VALIDATION_FAILED",
        violations: list[ValidationError] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.violations = violations or []


@dataclass
class DeliverableValidationResult:
    """Result of deliverable validation."""

    passed: bool
    violations: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)

    @classmethod
    def success(cls) -> DeliverableValidationResult:
        """Create a successful validation result."""
        return cls(passed=True)

    @classmethod
    def fail(cls, violations: list[ValidationError]) -> DeliverableValidationResult:
        """Create a failed validation result."""
        return cls(passed=False, violations=violations)


class DeliverableValidator:
    """Validates deliverables for No-Free-Facts compliance.

    HARD GATE at export time:
    - Every DeliverableFact with is_factual=True must have non-empty claim_refs
    - Narrative strings are validated using No-Free-Facts heuristics
    - Dissent sections must have refs (enforced at build time, verified here)

    Fail-closed behavior:
    - If any factual output lacks refs, validation FAILS
    - Missing refs block export (no silent pass)
    """

    def __init__(self, enable_semantic_rules: bool = True) -> None:
        """Initialize the validator.

        Args:
            enable_semantic_rules: If True, enable semantic pattern matching
                for narrative validation. Defaults to True.
        """
        self._nff_validator = NoFreeFactsValidator(enable_semantic_rules=enable_semantic_rules)

    def _validate_fact(
        self,
        fact: Any,
        path: str,
    ) -> list[ValidationError]:
        """Validate a single DeliverableFact for No-Free-Facts compliance.

        Rules (HARD GATE - DG-DET-001):
        1. If is_factual=True, MUST have non-empty claim_refs or calc_refs
        2. is_subjective does NOT bypass this rule when is_factual=True
        3. Only is_factual=False facts can have empty refs
        """
        errors: list[ValidationError] = []

        is_factual = getattr(fact, "is_factual", True)
        claim_refs = getattr(fact, "claim_refs", []) or []
        calc_refs = getattr(fact, "calc_refs", []) or []
        text = getattr(fact, "text", "") or ""

        has_refs = bool(claim_refs or calc_refs)

        if is_factual and not has_refs:
            display_text = text[:50] + "..." if len(text) > 50 else text
            errors.append(
                ValidationError(
                    code="NO_FREE_FACTS_UNREFERENCED_FACT",
                    message=(
                        f"Factual assertion (is_factual=True) has no claim_refs "
                        f"or calc_refs. Text: '{display_text}'"
                    ),
                    path=path,
                )
            )

        return errors

    def _validate_section(
        self,
        section: Any,
        path: str,
    ) -> list[ValidationError]:
        """Validate a DeliverableSection for No-Free-Facts compliance.

        Rules (HARD GATE - DG-DET-001):
        1. section.is_subjective does NOT bypass fact validation
        2. Each fact with is_factual=True is validated independently
        3. Narrative is validated using No-Free-Facts heuristics
        """
        errors: list[ValidationError] = []

        facts = getattr(section, "facts", []) or []
        for i, fact in enumerate(facts):
            fact_errors = self._validate_fact(fact, f"{path}.facts[{i}]")
            errors.extend(fact_errors)

        narrative = getattr(section, "narrative", None)
        if narrative:
            section_claim_refs: list[str] = []
            section_calc_refs: list[str] = []
            for f in facts:
                section_claim_refs.extend(getattr(f, "claim_refs", []) or [])
                section_calc_refs.extend(getattr(f, "calc_refs", []) or [])

            nff_data = {
                "sections": [
                    {
                        "text": narrative,
                        "is_factual": True,
                        "is_subjective": False,
                        "referenced_claim_ids": section_claim_refs,
                        "referenced_calc_ids": section_calc_refs,
                    }
                ]
            }
            nff_result = self._nff_validator.validate(nff_data)
            if not nff_result.passed:
                for err in nff_result.errors:
                    errors.append(
                        ValidationError(
                            code=err.code,
                            message=err.message,
                            path=f"{path}.narrative",
                        )
                    )

        return errors

    def _validate_dissent(
        self,
        dissent: Any,
        path: str,
    ) -> list[ValidationError]:
        """Validate a DissentSection for No-Free-Facts compliance.

        Dissent sections MUST have non-empty claim_refs (per v6.3).
        """
        errors: list[ValidationError] = []

        if dissent is None:
            return errors

        claim_refs = getattr(dissent, "claim_refs", []) or []
        calc_refs = getattr(dissent, "calc_refs", []) or []

        if not claim_refs and not calc_refs:
            errors.append(
                ValidationError(
                    code="DISSENT_MISSING_REFS",
                    message="Dissent section must have non-empty claim_refs or calc_refs",
                    path=path,
                )
            )

        return errors

    def validate_screening_snapshot(
        self,
        snapshot: Any,
    ) -> DeliverableValidationResult:
        """Validate a ScreeningSnapshot for No-Free-Facts compliance."""
        errors: list[ValidationError] = []

        sections_to_validate = [
            ("$.summary_section", getattr(snapshot, "summary_section", None)),
            ("$.key_metrics_section", getattr(snapshot, "key_metrics_section", None)),
            ("$.red_flags_section", getattr(snapshot, "red_flags_section", None)),
        ]

        for path, section in sections_to_validate:
            if section is not None:
                section_errors = self._validate_section(section, path)
                errors.extend(section_errors)

        additional = getattr(snapshot, "additional_sections", []) or []
        for i, section in enumerate(additional):
            section_errors = self._validate_section(section, f"$.additional_sections[{i}]")
            errors.extend(section_errors)

        if errors:
            return DeliverableValidationResult.fail(errors)
        return DeliverableValidationResult.success()

    def validate_ic_memo(
        self,
        memo: Any,
    ) -> DeliverableValidationResult:
        """Validate an ICMemo for No-Free-Facts compliance."""
        errors: list[ValidationError] = []

        sections_to_validate = [
            ("$.executive_summary", getattr(memo, "executive_summary", None)),
            ("$.company_overview", getattr(memo, "company_overview", None)),
            ("$.market_analysis", getattr(memo, "market_analysis", None)),
            ("$.financials", getattr(memo, "financials", None)),
            ("$.team_assessment", getattr(memo, "team_assessment", None)),
            ("$.risks_and_mitigations", getattr(memo, "risks_and_mitigations", None)),
            ("$.recommendation", getattr(memo, "recommendation", None)),
            ("$.truth_dashboard_summary", getattr(memo, "truth_dashboard_summary", None)),
            ("$.scenario_analysis", getattr(memo, "scenario_analysis", None)),
        ]

        for path, section in sections_to_validate:
            if section is not None:
                section_errors = self._validate_section(section, path)
                errors.extend(section_errors)

        dissent = getattr(memo, "dissent_section", None)
        if dissent is not None:
            dissent_errors = self._validate_dissent(dissent, "$.dissent_section")
            errors.extend(dissent_errors)

        additional = getattr(memo, "additional_sections", []) or []
        for i, section in enumerate(additional):
            section_errors = self._validate_section(section, f"$.additional_sections[{i}]")
            errors.extend(section_errors)

        if errors:
            return DeliverableValidationResult.fail(errors)
        return DeliverableValidationResult.success()

    def validate(
        self,
        deliverable: Any,
    ) -> DeliverableValidationResult:
        """Validate any deliverable type for No-Free-Facts compliance.

        Automatically detects deliverable type and validates accordingly.
        """
        deliverable_type = getattr(deliverable, "deliverable_type", None)

        if deliverable_type == "SCREENING_SNAPSHOT":
            return self.validate_screening_snapshot(deliverable)
        elif deliverable_type == "IC_MEMO":
            return self.validate_ic_memo(deliverable)
        else:
            return DeliverableValidationResult.fail(
                [
                    ValidationError(
                        code="UNKNOWN_DELIVERABLE_TYPE",
                        message=f"Unknown deliverable type: {deliverable_type}",
                        path="$.deliverable_type",
                    )
                ]
            )


def validate_deliverable_no_free_facts(
    deliverable: Any,
    raise_on_failure: bool = True,
) -> DeliverableValidationResult:
    """Validate a deliverable for No-Free-Facts compliance.

    HARD GATE: Every factual assertion must have refs.

    Args:
        deliverable: The deliverable to validate (ScreeningSnapshot or ICMemo)
        raise_on_failure: If True, raise DeliverableValidationError on failure

    Returns:
        DeliverableValidationResult

    Raises:
        DeliverableValidationError: If validation fails and raise_on_failure=True
    """
    validator = DeliverableValidator()
    result = validator.validate(deliverable)

    if not result.passed and raise_on_failure:
        messages = [v.message for v in result.violations[:3]]
        summary = "; ".join(messages)
        if len(result.violations) > 3:
            summary += f" (and {len(result.violations) - 3} more violations)"
        raise DeliverableValidationError(
            message=summary,
            code="NO_FREE_FACTS_VIOLATION",
            violations=result.violations,
        )

    return result

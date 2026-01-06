"""Muḥāsabah Validator - enforces self-accounting rules for agent outputs.

HARD GATE: All agent outputs MUST carry a MuḥāsabahRecord with:
- supported_claim_ids / supported_calc_ids (non-empty unless SUBJECTIVE)
- uncertainty register (mandatory when confidence > 0.80 without falsifiability_tests)
- falsifiability tests (mandatory for recommendation-driving outputs)

Reject agent output if validation fails. FAIL-CLOSED.
"""

from __future__ import annotations

import re
from typing import Any

from idis.validators.schema_validator import ValidationError, ValidationResult

# UUID regex pattern (basic format check)
_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _is_uuid_like(value: str) -> bool:
    """Check if a string matches UUID format."""
    return bool(_UUID_PATTERN.match(value))


def validate_muhasabah(record: dict[str, Any]) -> ValidationResult:
    """Validate a Muḥāsabah record (public function API).

    Args:
        record: MuḥāsabahRecord as dict

    Returns:
        ValidationResult with pass (bool), errors, warnings
    """
    validator = MuhasabahValidator()
    return validator.validate(record)


class MuhasabahValidator:
    """Validates Muḥāsabah records for compliance with trust invariants.

    Rules (from spec v6.3):
    1. Required fields: agent_id, output_id, supported_claim_ids, confidence, timestamp
    2. If supported_claim_ids empty AND not subjective → REJECT
    3. If confidence > 0.80: must include non-empty uncertainties OR falsifiability_tests
    4. If recommendation/decision field present → must include falsifiability_tests
    5. All IDs must be UUID-like strings
    """

    # Confidence thresholds from spec
    HIGH_CONFIDENCE_THRESHOLD = 0.80
    MATERIAL_CONFIDENCE_THRESHOLD = 0.50

    def __init__(self) -> None:
        """Initialize the validator."""
        pass

    def validate(self, data: Any) -> ValidationResult:
        """Validate a Muḥāsabah record.

        Args:
            data: MuḥāsabahRecord JSON data

        Returns:
            ValidationResult - FAILS CLOSED on rule violations
        """
        # Fail closed on None or non-dict
        if data is None:
            return ValidationResult.fail_closed("Data is None - cannot validate")

        if not isinstance(data, dict):
            return ValidationResult.fail_closed("Data must be a dictionary")

        errors: list[ValidationError] = []
        warnings: list[ValidationError] = []

        # Extract fields with safe defaults
        agent_id = data.get("agent_id")
        output_id = data.get("output_id")
        timestamp = data.get("timestamp")
        supported_claim_ids = data.get("supported_claim_ids", [])
        supported_calc_ids = data.get("supported_calc_ids", [])
        falsifiability_tests = data.get("falsifiability_tests", [])
        uncertainties = data.get("uncertainties", [])
        confidence = data.get("confidence")
        is_subjective = data.get("is_subjective", False)
        has_recommendation = "recommendation" in data or "decision" in data

        # Required fields check
        if not agent_id:
            errors.append(
                ValidationError(
                    code="MISSING_AGENT_ID",
                    message="agent_id is required",
                    path="$.agent_id",
                )
            )
        elif isinstance(agent_id, str) and not _is_uuid_like(agent_id):
            errors.append(
                ValidationError(
                    code="INVALID_AGENT_ID_FORMAT",
                    message="agent_id must be a valid UUID format",
                    path="$.agent_id",
                )
            )

        if not output_id:
            errors.append(
                ValidationError(
                    code="MISSING_OUTPUT_ID",
                    message="output_id is required",
                    path="$.output_id",
                )
            )
        elif isinstance(output_id, str) and not _is_uuid_like(output_id):
            errors.append(
                ValidationError(
                    code="INVALID_OUTPUT_ID_FORMAT",
                    message="output_id must be a valid UUID format",
                    path="$.output_id",
                )
            )

        if not timestamp:
            errors.append(
                ValidationError(
                    code="MISSING_TIMESTAMP",
                    message="timestamp is required",
                    path="$.timestamp",
                )
            )

        if confidence is None:
            errors.append(
                ValidationError(
                    code="MISSING_CONFIDENCE",
                    message="confidence is required",
                    path="$.confidence",
                )
            )
        elif not isinstance(confidence, (int, float)):
            errors.append(
                ValidationError(
                    code="INVALID_CONFIDENCE_TYPE",
                    message="confidence must be a number",
                    path="$.confidence",
                )
            )
        elif confidence < 0.0 or confidence > 1.0:
            errors.append(
                ValidationError(
                    code="CONFIDENCE_OUT_OF_RANGE",
                    message=f"confidence must be between 0.0 and 1.0, got {confidence}",
                    path="$.confidence",
                )
            )

        # Validate supported_claim_ids is a list
        if not isinstance(supported_claim_ids, list):
            errors.append(
                ValidationError(
                    code="INVALID_CLAIM_IDS_TYPE",
                    message="supported_claim_ids must be an array",
                    path="$.supported_claim_ids",
                )
            )
            supported_claim_ids = []

        # Validate supported_calc_ids is a list
        if not isinstance(supported_calc_ids, list):
            supported_calc_ids = []

        # RULE 1: Non-subjective outputs must have claim/calc references
        if not is_subjective:
            has_claim_refs = len(supported_claim_ids) > 0
            has_calc_refs = len(supported_calc_ids) > 0

            if not has_claim_refs and not has_calc_refs:
                errors.append(
                    ValidationError(
                        code="NO_SUPPORTING_REFERENCES",
                        message=(
                            "Non-subjective output requires supported_claim_ids or "
                            "supported_calc_ids to be non-empty. Set is_subjective=true "
                            "if this output contains no factual assertions."
                        ),
                        path="$.supported_claim_ids",
                    )
                )

        # RULE 2: High confidence requires uncertainties OR falsifiability_tests
        has_uncertainties = isinstance(uncertainties, list) and len(uncertainties) > 0
        has_falsifiability = (
            isinstance(falsifiability_tests, list) and len(falsifiability_tests) > 0
        )

        if (
            isinstance(confidence, (int, float))
            and confidence > self.HIGH_CONFIDENCE_THRESHOLD
            and not has_uncertainties
            and not has_falsifiability
        ):
            errors.append(
                ValidationError(
                    code="HIGH_CONFIDENCE_NO_UNCERTAINTIES",
                    message=(
                        f"Confidence {confidence:.2f} > {self.HIGH_CONFIDENCE_THRESHOLD} "
                        f"requires non-empty uncertainties OR falsifiability_tests array."
                    ),
                    path="$.uncertainties",
                )
            )

        # RULE 3: Recommendation/decision requires falsifiability tests
        if has_recommendation and not has_falsifiability:
            errors.append(
                ValidationError(
                    code="RECOMMENDATION_NO_FALSIFIABILITY",
                    message=(
                        "Records with recommendation or decision field must include "
                        "non-empty falsifiability_tests array."
                    ),
                    path="$.falsifiability_tests",
                )
            )

        # Validate claim IDs are UUID-like
        if isinstance(supported_claim_ids, list):
            for i, claim_id in enumerate(supported_claim_ids):
                if isinstance(claim_id, str) and not _is_uuid_like(claim_id):
                    errors.append(
                        ValidationError(
                            code="INVALID_CLAIM_ID_FORMAT",
                            message=f"supported_claim_ids[{i}] must be a valid UUID format",
                            path=f"$.supported_claim_ids[{i}]",
                        )
                    )

        # Validate calc IDs are UUID-like
        if isinstance(supported_calc_ids, list):
            for i, calc_id in enumerate(supported_calc_ids):
                if isinstance(calc_id, str) and not _is_uuid_like(calc_id):
                    errors.append(
                        ValidationError(
                            code="INVALID_CALC_ID_FORMAT",
                            message=f"supported_calc_ids[{i}] must be a valid UUID format",
                            path=f"$.supported_calc_ids[{i}]",
                        )
                    )

        # Validate falsifiability test structure (if populated, validate structure)
        if isinstance(falsifiability_tests, list):
            for i, test in enumerate(falsifiability_tests):
                if not isinstance(test, dict):
                    errors.append(
                        ValidationError(
                            code="INVALID_FALSIFIABILITY_TEST",
                            message="Each falsifiability test must be an object",
                            path=f"$.falsifiability_tests[{i}]",
                        )
                    )
                    continue

                required_fields = ["test_description", "required_evidence", "pass_fail_rule"]
                for fld in required_fields:
                    if not test.get(fld):
                        errors.append(
                            ValidationError(
                                code="MISSING_FALSIFIABILITY_FIELD",
                                message=f"Falsifiability test missing required field: {fld}",
                                path=f"$.falsifiability_tests[{i}].{fld}",
                            )
                        )

        # Validate uncertainty structure
        if isinstance(uncertainties, list):
            for i, unc in enumerate(uncertainties):
                if not isinstance(unc, dict):
                    errors.append(
                        ValidationError(
                            code="INVALID_UNCERTAINTY",
                            message="Each uncertainty must be an object",
                            path=f"$.uncertainties[{i}]",
                        )
                    )
                    continue

                required_fields = ["uncertainty", "impact", "mitigation"]
                for field in required_fields:
                    if not unc.get(field):
                        errors.append(
                            ValidationError(
                                code="MISSING_UNCERTAINTY_FIELD",
                                message=f"Uncertainty missing required field: {field}",
                                path=f"$.uncertainties[{i}].{field}",
                            )
                        )

                # Validate impact enum
                impact = unc.get("impact")
                if impact and impact not in ("HIGH", "MEDIUM", "LOW"):
                    errors.append(
                        ValidationError(
                            code="INVALID_IMPACT_VALUE",
                            message=f"Impact must be HIGH, MEDIUM, or LOW, got: {impact}",
                            path=f"$.uncertainties[{i}].impact",
                        )
                    )

        # Additional warnings for edge cases
        if is_subjective and (len(supported_claim_ids) > 0 or len(supported_calc_ids) > 0):
            warnings.append(
                ValidationError(
                    code="SUBJECTIVE_WITH_REFS",
                    message=(
                        "Output marked as subjective but has claim/calc references. "
                        "Consider removing is_subjective flag."
                    ),
                    path="$.is_subjective",
                )
            )

        if errors:
            return ValidationResult.fail(errors)

        return ValidationResult.success(warnings if warnings else None)

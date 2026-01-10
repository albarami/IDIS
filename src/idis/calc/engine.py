"""Deterministic Calculation Engine.

Phase 4.1: CalcEngine with run() and verify_reproducibility() methods.
Phase 4.2: Extraction confidence gate enforcement.
All arithmetic uses Decimal exclusively; no float operations.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from idis.calc.formulas.registry import (
    FormulaRegistry,
    FormulaSpec,
    canonical_json_for_hash,
    compute_sha256,
)
from idis.models.calc_sanad import CalcSanad, GradeExplanationEntry, SanadGrade
from idis.models.deterministic_calculation import (
    CalcInputs,
    CalcOutput,
    CalcType,
    DeterministicCalculation,
)
from idis.validators.extraction_gate import (
    ExtractionGateBlockedError,
    ExtractionGateInput,
    VerificationMethod,
    evaluate_extraction_gate_batch,
)

if TYPE_CHECKING:
    pass

__version__ = "0.1.0"


class CalcMissingInputError(Exception):
    """Raised when a required input is missing.

    Fail-closed: calculations cannot proceed with missing inputs.
    """

    def __init__(self, missing_inputs: list[str], calc_type: CalcType) -> None:
        self.missing_inputs = missing_inputs
        self.calc_type = calc_type
        super().__init__(f"Missing required inputs for {calc_type.value}: {missing_inputs}")


class CalcUnsupportedValueError(Exception):
    """Raised when an unsupported unit/currency/time_window is provided.

    Fail-closed: calculations cannot proceed with unsupported values.
    """

    def __init__(self, field: str, value: str, supported: list[str]) -> None:
        self.field = field
        self.value = value
        self.supported = supported
        super().__init__(f"Unsupported {field}: '{value}'. Supported: {supported}")


class CalcIntegrityError(Exception):
    """Raised when reproducibility hash verification fails.

    Indicates potential tampering or data corruption.
    """

    def __init__(
        self,
        calc_id: str,
        expected_hash: str,
        computed_hash: str,
    ) -> None:
        self.calc_id = calc_id
        self.expected_hash = expected_hash
        self.computed_hash = computed_hash
        super().__init__(
            f"Integrity check failed for calc_id={calc_id}. "
            f"Expected hash: {expected_hash[:16]}..., "
            f"Computed hash: {computed_hash[:16]}..."
        )


@dataclass
class InputGradeInfo:
    """Grade information for a single input claim.

    Phase 4.2 additions: extraction_confidence, dhabt_score, is_human_verified
    for extraction gate enforcement.
    """

    claim_id: str
    grade: SanadGrade
    is_material: bool = True
    extraction_confidence: Decimal | None = None
    dhabt_score: Decimal | None = None
    is_human_verified: bool = False
    verification_method: VerificationMethod = VerificationMethod.NONE


@dataclass
class CalcEngineResult:
    """Result from CalcEngine.run().

    Contains both the DeterministicCalculation and its CalcSanad provenance.
    """

    calculation: DeterministicCalculation
    calc_sanad: CalcSanad


class CalcEngine:
    """Deterministic calculation engine with full provenance.

    All calculations use Decimal arithmetic exclusively.
    Every calculation produces a reproducibility hash for tamper detection.

    Phase 4.2: Enforces extraction confidence gate on all inputs.
    Inputs with extraction_confidence < 0.95 or dhabt_score < 0.90
    are blocked UNLESS human-verified.
    """

    def __init__(
        self,
        registry: FormulaRegistry | None = None,
        code_version: str | None = None,
        enforce_extraction_gate: bool = True,
    ) -> None:
        """Initialize the calc engine.

        Args:
            registry: Formula registry to use. Defaults to singleton.
            code_version: Code version string. Defaults to package __version__.
            enforce_extraction_gate: Whether to enforce extraction confidence gate.
                Defaults to True. Only set to False for legacy/migration scenarios.
        """
        self._registry = registry or FormulaRegistry()
        self._code_version = code_version or __version__
        self._enforce_extraction_gate = enforce_extraction_gate

    def run(
        self,
        tenant_id: str,
        deal_id: str,
        calc_type: CalcType,
        input_values: dict[str, Decimal],
        input_grades: list[InputGradeInfo],
        metadata: dict[str, str] | None = None,
    ) -> CalcEngineResult:
        """Run a deterministic calculation.

        Args:
            tenant_id: Tenant UUID for isolation.
            deal_id: Deal UUID this calculation belongs to.
            calc_type: Type of calculation to perform.
            input_values: Named numeric inputs (all Decimal).
            input_grades: Grade information for input claims.
            metadata: Optional metadata (units, currency, time_window).

        Returns:
            CalcEngineResult with DeterministicCalculation and CalcSanad.

        Raises:
            CalcMissingInputError: If required inputs are missing.
            ExtractionGateBlockedError: If any input fails extraction gate.
            KeyError: If calc_type is not registered.
            ValueError: If formula computation fails.
        """
        spec = self._registry.get_or_raise(calc_type)

        # Phase 4.2: Enforce extraction gate BEFORE any computation
        if self._enforce_extraction_gate:
            self._enforce_extraction_gate_on_inputs(input_grades, calc_type)

        self._validate_required_inputs(spec, input_values)

        merged_inputs = self._merge_with_defaults(spec, input_values)

        output_value = spec.fn(merged_inputs)

        output_value = output_value.quantize(
            Decimal(f"0.{'0' * spec.output_precision}"),
            rounding=ROUND_HALF_UP,
        )

        calc_id = str(uuid.uuid4())
        calc_sanad_id = str(uuid.uuid4())

        input_claim_ids = sorted([ig.claim_id for ig in input_grades])

        calc_inputs = CalcInputs(
            claim_ids=input_claim_ids,
            values=merged_inputs,
            metadata=metadata or {},
        )

        calc_output = CalcOutput(
            primary_value=output_value,
            secondary_values={},
            unit=metadata.get("unit") if metadata else None,
            currency=metadata.get("currency") if metadata else None,
        )

        reproducibility_hash = self._compute_reproducibility_hash(
            tenant_id=tenant_id,
            deal_id=deal_id,
            calc_type=calc_type,
            formula_hash=spec.formula_hash,
            code_version=self._code_version,
            inputs=calc_inputs,
            output=calc_output,
        )

        now = datetime.now(UTC)

        calculation = DeterministicCalculation(
            calc_id=calc_id,
            tenant_id=tenant_id,
            deal_id=deal_id,
            calc_type=calc_type,
            inputs=calc_inputs,
            formula_hash=spec.formula_hash,
            code_version=self._code_version,
            output=calc_output,
            reproducibility_hash=reproducibility_hash,
            created_at=now,
            updated_at=now,
        )

        calc_sanad = self._compute_calc_sanad(
            calc_sanad_id=calc_sanad_id,
            tenant_id=tenant_id,
            calc_id=calc_id,
            input_grades=input_grades,
            now=now,
        )

        return CalcEngineResult(calculation=calculation, calc_sanad=calc_sanad)

    def verify_reproducibility(self, calculation: DeterministicCalculation) -> None:
        """Verify the reproducibility hash of a calculation.

        Recomputes the hash and compares with the stored value.

        Args:
            calculation: The calculation to verify.

        Raises:
            CalcIntegrityError: If the hash doesn't match (tamper detected).
        """
        spec = self._registry.get_or_raise(calculation.calc_type)

        computed_hash = self._compute_reproducibility_hash(
            tenant_id=calculation.tenant_id,
            deal_id=calculation.deal_id,
            calc_type=calculation.calc_type,
            formula_hash=spec.formula_hash,
            code_version=calculation.code_version,
            inputs=calculation.inputs,
            output=calculation.output,
        )

        if computed_hash != calculation.reproducibility_hash:
            raise CalcIntegrityError(
                calc_id=calculation.calc_id,
                expected_hash=calculation.reproducibility_hash,
                computed_hash=computed_hash,
            )

    def _validate_required_inputs(
        self,
        spec: FormulaSpec,
        input_values: dict[str, Decimal],
    ) -> None:
        """Validate that all required inputs are present.

        Raises:
            CalcMissingInputError: If any required inputs are missing.
        """
        missing = [name for name in spec.required_inputs if name not in input_values]
        if missing:
            raise CalcMissingInputError(missing, spec.calc_type)

    def _merge_with_defaults(
        self,
        spec: FormulaSpec,
        input_values: dict[str, Decimal],
    ) -> dict[str, Decimal]:
        """Merge input values with optional defaults."""
        merged = dict(spec.optional_inputs)
        merged.update(input_values)
        return merged

    def _enforce_extraction_gate_on_inputs(
        self,
        input_grades: list[InputGradeInfo],
        calc_type: CalcType,
    ) -> None:
        """Enforce extraction confidence gate on all inputs.

        Phase 4.2: Blocks calculation if ANY input fails the extraction gate.
        Gate conditions (fail-closed):
        - extraction_confidence < 0.95 → blocked
        - dhabt_score < 0.90 → blocked
        - missing confidence or dhabt → blocked
        UNLESS the input is human-verified.

        Args:
            input_grades: List of input grade info with extraction metadata.
            calc_type: The calculation type (for error message).

        Raises:
            ExtractionGateBlockedError: If any input fails the gate.
        """
        if not input_grades:
            return

        # Convert InputGradeInfo to ExtractionGateInput
        gate_inputs = [
            ExtractionGateInput(
                claim_id=ig.claim_id,
                extraction_confidence=ig.extraction_confidence,
                dhabt_score=ig.dhabt_score,
                is_human_verified=ig.is_human_verified,
                verification_method=ig.verification_method,
            )
            for ig in input_grades
        ]

        # Evaluate all inputs
        _, blocked = evaluate_extraction_gate_batch(gate_inputs)

        # If any are blocked, raise error
        if blocked:
            raise ExtractionGateBlockedError(blocked, calc_type.value)

    def _compute_reproducibility_hash(
        self,
        tenant_id: str,
        deal_id: str,
        calc_type: CalcType,
        formula_hash: str,
        code_version: str,
        inputs: CalcInputs,
        output: CalcOutput,
    ) -> str:
        """Compute the reproducibility hash for a calculation.

        Hash is computed from canonical JSON of all deterministic inputs/outputs.
        """
        hash_input = {
            "calc_type": calc_type.value,
            "code_version": code_version,
            "deal_id": deal_id.lower(),
            "formula_hash": formula_hash,
            "inputs": {
                "claim_ids": sorted(inputs.claim_ids),
                "metadata": inputs.metadata,
                "values": {k: str(v) for k, v in sorted(inputs.values.items())},
            },
            "output": {
                "currency": output.currency,
                "primary_value": str(output.primary_value),
                "secondary_values": {k: str(v) for k, v in sorted(output.secondary_values.items())},
                "unit": output.unit,
            },
            "tenant_id": tenant_id.lower(),
        }

        canonical = canonical_json_for_hash(hash_input)
        return compute_sha256(canonical)

    def _compute_calc_sanad(
        self,
        calc_sanad_id: str,
        tenant_id: str,
        calc_id: str,
        input_grades: list[InputGradeInfo],
        now: datetime,
    ) -> CalcSanad:
        """Compute the CalcSanad with grade derivation.

        Grade derivation rules:
        - input_min_sanad_grade = minimum across ALL input grades
        - calc_grade = minimum across MATERIAL inputs only
        - If any material input has grade D -> calc_grade = D
        - If no material inputs, fall back to input_min_sanad_grade
        - Non-material inputs do NOT affect calc_grade
        """
        explanation: list[GradeExplanationEntry] = []

        if not input_grades:
            input_min_grade = SanadGrade.A
            calc_grade = SanadGrade.A
            explanation.append(
                GradeExplanationEntry(
                    step="No input claims; defaulting to grade A",
                    impact="grade = A",
                )
            )
        else:
            all_grades = [ig.grade for ig in input_grades]
            input_min_grade = SanadGrade.min_grade(all_grades)

            material_inputs = [ig for ig in input_grades if ig.is_material]
            non_material_inputs = [ig for ig in input_grades if not ig.is_material]

            for ig in input_grades:
                material_label = "material" if ig.is_material else "non-material"
                step_text = f"Input {ig.claim_id[:8]}... grade {ig.grade.value} ({material_label})"
                explanation.append(
                    GradeExplanationEntry(
                        step=step_text,
                        input_grade=ig.grade,
                        claim_id=ig.claim_id,
                        is_material=ig.is_material,
                    )
                )

            if non_material_inputs:
                excluded_msg = f"{len(non_material_inputs)} non-material input(s) excluded"
                explanation.append(
                    GradeExplanationEntry(
                        step=excluded_msg,
                        impact="non-material grades do not affect calc_grade",
                    )
                )

            if material_inputs:
                material_grades = [ig.grade for ig in material_inputs]
                calc_grade = SanadGrade.min_grade(material_grades)
                explanation.append(
                    GradeExplanationEntry(
                        step=f"calc_grade derived from {len(material_inputs)} material input(s)",
                        impact=f"calc_grade = {calc_grade.value} (min of material grades)",
                    )
                )
            else:
                calc_grade = input_min_grade
                explanation.append(
                    GradeExplanationEntry(
                        step="No material inputs; using min of all inputs as fallback",
                        impact=f"calc_grade = {calc_grade.value}",
                    )
                )

        return CalcSanad(
            calc_sanad_id=calc_sanad_id,
            tenant_id=tenant_id,
            calc_id=calc_id,
            input_claim_ids=sorted([ig.claim_id for ig in input_grades]),
            input_min_sanad_grade=input_min_grade,
            calc_grade=calc_grade,
            explanation=explanation,
            created_at=now,
            updated_at=now,
        )

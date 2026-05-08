"""Truthful deterministic calculation runner for run CALC steps."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from idis.calc.engine import CalcEngine, InputGradeInfo
from idis.calc.formulas.core import register_core_formulas
from idis.calc.formulas.registry import FormulaRegistry
from idis.models.calc_sanad import SanadGrade
from idis.models.deterministic_calculation import CalcType
from idis.validators.extraction_gate import ExtractionGateBlockedError

BLOCKED_NO_ELIGIBLE_INPUTS = "no_eligible_inputs"
BLOCKED_MISSING_REQUIRED_CLAIM = "missing_required_claim"
BLOCKED_MISSING_SANAD_GRADE = "missing_sanad_grade"
BLOCKED_MISSING_SOURCE_METADATA = "missing_source_metadata"
BLOCKED_BELOW_CONFIDENCE = "below_confidence_threshold"
BLOCKED_BELOW_DHABT = "below_dhabt_threshold"


@dataclass(frozen=True)
class CalcCandidate:
    """Candidate inputs for one deterministic calculation."""

    calc_type: CalcType
    input_values: dict[str, Decimal]
    input_grades: list[InputGradeInfo]


class ClaimsReader(Protocol):
    """Claim lookup behavior needed by CalcRunner."""

    def get(self, claim_id: str) -> dict[str, Any] | None:
        """Get a claim by ID."""


class SanadsReader(Protocol):
    """Sanad lookup behavior needed by CalcRunner."""

    def get_by_claim(self, claim_id: str) -> dict[str, Any] | None:
        """Get a Sanad by claim ID."""


class CalculationsWriter(Protocol):
    """Calculation persistence behavior needed by CalcRunner."""

    def create(
        self,
        *,
        calculation: Any,
        calc_sanad: Any,
    ) -> None:
        """Persist a calculation and CalcSanad."""


class CalcRunner:
    """Build eligible calc candidates and persist real deterministic outputs."""

    def __init__(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        claims_repo: ClaimsReader,
        sanads_repo: SanadsReader,
        calculations_repo: CalculationsWriter,
        engine: CalcEngine | None = None,
        registry: FormulaRegistry | None = None,
    ) -> None:
        """Initialize the runner."""
        self._tenant_id = tenant_id
        self._deal_id = deal_id
        self._claims_repo = claims_repo
        self._sanads_repo = sanads_repo
        self._calculations_repo = calculations_repo
        self._registry = register_core_formulas(registry)
        self._engine = engine or CalcEngine(registry=self._registry)

    def run(
        self,
        *,
        created_claim_ids: list[str],
        calc_types: list[CalcType] | None = None,
    ) -> dict[str, Any]:
        """Run eligible deterministic calculations.

        No eligible candidates are reported as blocked/skipped summary entries.
        System, formula, engine, and persistence errors are allowed to raise so
        the orchestrator marks CALC failed.
        """
        requested_calc_types = calc_types or self._registry.list_registered()
        claims = self._load_claims(created_claim_ids)

        calc_ids: list[str] = []
        hashes: list[str] = []
        blocked_candidates: list[dict[str, Any]] = []

        for calc_type in requested_calc_types:
            candidate, blocked = self._build_candidate(calc_type, claims)
            if blocked is not None:
                blocked_candidates.append(blocked)
                continue

            assert candidate is not None
            try:
                engine_result = self._engine.run(
                    tenant_id=self._tenant_id,
                    deal_id=self._deal_id,
                    calc_type=candidate.calc_type,
                    input_values=candidate.input_values,
                    input_grades=candidate.input_grades,
                )
            except ExtractionGateBlockedError as exc:
                blocked_candidates.append(_blocked_from_gate(calc_type, exc))
                continue

            self._calculations_repo.create(
                calculation=engine_result.calculation,
                calc_sanad=engine_result.calc_sanad,
            )
            calc_ids.append(engine_result.calculation.calc_id)
            hashes.append(engine_result.calculation.reproducibility_hash)

        if not calc_ids and not blocked_candidates:
            blocked_candidates.append(
                {
                    "calc_type": "ALL",
                    "reason": BLOCKED_NO_ELIGIBLE_INPUTS,
                    "claim_ids": sorted(created_claim_ids),
                }
            )

        return {
            "calc_ids": calc_ids,
            "reproducibility_hashes": hashes,
            "persisted_count": len(calc_ids),
            "blocked_candidates": blocked_candidates,
        }

    def _load_claims(self, claim_ids: list[str]) -> list[dict[str, Any]]:
        claims: list[dict[str, Any]] = []
        for claim_id in claim_ids:
            claim = self._claims_repo.get(claim_id)
            if claim is not None:
                claims.append(claim)
        return claims

    def _build_candidate(
        self,
        calc_type: CalcType,
        claims: list[dict[str, Any]],
    ) -> tuple[CalcCandidate | None, dict[str, Any] | None]:
        spec = self._registry.get_or_raise(calc_type)
        claims_by_input = _claims_by_input_key(claims)

        missing_inputs = [
            input_key for input_key in spec.required_inputs if input_key not in claims_by_input
        ]
        if missing_inputs:
            return None, {
                "calc_type": calc_type.value,
                "reason": BLOCKED_MISSING_REQUIRED_CLAIM,
                "missing_inputs": missing_inputs,
            }

        input_values: dict[str, Decimal] = {}
        input_grades: list[InputGradeInfo] = []
        candidate_claim_ids = sorted(
            str(claims_by_input[input_key]["claim_id"]) for input_key in spec.required_inputs
        )

        for input_key in spec.required_inputs:
            claim = claims_by_input[input_key]
            value = _extract_decimal_value(claim.get("value"))
            if value is None:
                return None, _blocked_candidate(
                    calc_type=calc_type,
                    reason=BLOCKED_MISSING_SOURCE_METADATA,
                    claim_ids=candidate_claim_ids,
                )
            input_values[input_key] = value

            input_grade, reason = self._input_grade_info(claim)
            if reason is not None:
                return None, _blocked_candidate(
                    calc_type=calc_type,
                    reason=reason,
                    claim_ids=candidate_claim_ids,
                )
            assert input_grade is not None
            input_grades.append(input_grade)

        return (
            CalcCandidate(
                calc_type=calc_type,
                input_values=input_values,
                input_grades=input_grades,
            ),
            None,
        )

    def _input_grade_info(
        self,
        claim: dict[str, Any],
    ) -> tuple[InputGradeInfo | None, str | None]:
        claim_id = str(claim["claim_id"])
        grade = claim.get("claim_grade")
        if not grade:
            return None, BLOCKED_MISSING_SANAD_GRADE

        sanad = self._sanads_repo.get_by_claim(claim_id)
        if sanad is None:
            return None, BLOCKED_MISSING_SOURCE_METADATA

        computed = sanad.get("computed") or {}
        confidence = _decimal_or_none(computed.get("extraction_confidence"))
        dhabt = _decimal_or_none(computed.get("dhabt_score"))
        if confidence is None or dhabt is None:
            return None, BLOCKED_MISSING_SOURCE_METADATA

        return (
            InputGradeInfo(
                claim_id=claim_id,
                grade=SanadGrade(str(grade)),
                is_material=str(claim.get("materiality", "MEDIUM")).upper()
                in {"HIGH", "CRITICAL", "MEDIUM"},
                extraction_confidence=confidence,
                dhabt_score=dhabt,
            ),
            None,
        )


def _claims_by_input_key(claims: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for claim in claims:
        input_key = _claim_input_key(claim)
        if input_key is not None:
            by_key[input_key] = claim
    return by_key


def _claim_input_key(claim: dict[str, Any]) -> str | None:
    predicate = claim.get("predicate")
    if isinstance(predicate, str) and predicate:
        return predicate.lower()

    haystack = " ".join(
        str(value).lower() for value in (claim.get("claim_class"), claim.get("claim_text")) if value
    )
    aliases = {
        "revenue": ("revenue", "sales"),
        "cogs": ("cogs", "cost_of_goods", "cost of goods", "cost of revenue"),
        "cash_balance": ("cash_balance", "cash balance", "cash on hand"),
        "monthly_burn_rate": ("monthly_burn_rate", "monthly burn", "burn rate"),
        "starting_cash": ("starting_cash", "starting cash"),
        "ending_cash": ("ending_cash", "ending cash"),
        "months": ("months", "month count"),
        "ltv": ("ltv", "lifetime value"),
        "cac": ("cac", "customer acquisition cost"),
    }
    for input_key, terms in aliases.items():
        if any(term in haystack for term in terms):
            return input_key
    return None


def _extract_decimal_value(value: Any) -> Decimal | None:
    if not isinstance(value, dict):
        return None
    raw_value = value.get("amount", value.get("value"))
    return _decimal_or_none(raw_value)


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _blocked_candidate(
    *,
    calc_type: CalcType,
    reason: str,
    claim_ids: list[str],
) -> dict[str, Any]:
    return {
        "calc_type": calc_type.value,
        "reason": reason,
        "claim_ids": sorted(claim_ids),
    }


def _blocked_from_gate(
    calc_type: CalcType,
    exc: ExtractionGateBlockedError,
) -> dict[str, Any]:
    reason = BLOCKED_MISSING_SOURCE_METADATA
    claim_ids: list[str] = []
    for decision in exc.blocked_inputs:
        claim_ids.append(decision.claim_id)
        if decision.reason is None:
            continue
        if decision.reason.value == "LOW_CONFIDENCE":
            reason = BLOCKED_BELOW_CONFIDENCE
        elif decision.reason.value == "LOW_DHABT":
            reason = BLOCKED_BELOW_DHABT
    return _blocked_candidate(calc_type=calc_type, reason=reason, claim_ids=claim_ids)

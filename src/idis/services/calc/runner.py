"""Truthful deterministic calculation runner for run CALC steps."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Generic, NamedTuple, Protocol, TypeVar

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
BLOCKED_CALCULATION_FAILED = "calculation_failed"


@dataclass(frozen=True)
class CalcCandidate:
    """Candidate inputs for one deterministic calculation."""

    calc_type: CalcType
    input_values: dict[str, Decimal]
    input_grades: list[InputGradeInfo]
    metadata: dict[str, str]


class ClaimsReader(Protocol):
    """Claim lookup behavior needed by CalcRunner."""

    def get(self, claim_id: str) -> dict[str, Any] | None:
        """Get a claim by ID."""


class SanadsReader(Protocol):
    """Sanad lookup behavior needed by CalcRunner."""

    def get_by_claim(self, claim_id: str) -> dict[str, Any] | None:
        """Get a Sanad by claim ID."""


class CalculationsWriter(Protocol):
    """Calculation persistence + lookup behavior needed by CalcRunner."""

    def create(
        self,
        *,
        calculation: Any,
        calc_sanad: Any,
    ) -> None:
        """Persist a calculation and CalcSanad."""

    def list_by_deal(self, deal_id: str) -> list[dict[str, Any]]:
        """List persisted calculations for a deal (methodology-authoritative dedup)."""


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

        # Methodology-authoritative dedup: an already-persisted calculation with the same
        # reproducibility hash IS the same calculation. Reuse its id and merge instead of
        # recomputing a parallel duplicate under a fresh id (also makes re-runs idempotent).
        existing_by_hash = {
            row["reproducibility_hash"]: row["calc_id"]
            for row in self._calculations_repo.list_by_deal(self._deal_id)
        }

        calc_ids: list[str] = []
        hashes: list[str] = []
        persisted_count = 0
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
                    metadata=candidate.metadata,
                )
            except ExtractionGateBlockedError as exc:
                blocked_candidates.append(_blocked_from_gate(calc_type, exc))
                continue

            repro_hash = engine_result.calculation.reproducibility_hash
            existing_id = existing_by_hash.get(repro_hash)
            if existing_id is not None:
                calc_ids.append(existing_id)
                hashes.append(repro_hash)
                continue

            self._calculations_repo.create(
                calculation=engine_result.calculation,
                calc_sanad=engine_result.calc_sanad,
            )
            existing_by_hash[repro_hash] = engine_result.calculation.calc_id
            calc_ids.append(engine_result.calculation.calc_id)
            hashes.append(repro_hash)
            persisted_count += 1

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
            "persisted_count": persisted_count,
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
        claims_by_input = _claims_grouped_by_input_key(claims)

        missing_inputs = [
            input_key for input_key in spec.required_inputs if input_key not in claims_by_input
        ]
        if missing_inputs:
            return None, {
                "calc_type": calc_type.value,
                "reason": BLOCKED_MISSING_REQUIRED_CLAIM,
                "missing_inputs": missing_inputs,
            }

        conflicting_ids = _operand_identity_conflicts(claims_by_input, spec.required_inputs)
        if conflicting_ids:
            return None, _blocked_candidate(
                calc_type=calc_type,
                reason=BLOCKED_CALCULATION_FAILED,
                claim_ids=conflicting_ids,
            )

        selected_claims: list[dict[str, Any]] = []
        competing_claim_ids: list[str] = []
        operand_blocked = False
        for input_key in spec.required_inputs:
            resolution = _resolve_unique_operand(claims_by_input[input_key])
            if resolution.claim is None:
                operand_blocked = True
                competing_claim_ids.extend(resolution.competing_claim_ids)
            else:
                selected_claims.append(resolution.claim)
        if operand_blocked:
            return None, _blocked_candidate(
                calc_type=calc_type,
                reason=BLOCKED_CALCULATION_FAILED,
                claim_ids=sorted(set(competing_claim_ids)),
            )

        input_values: dict[str, Decimal] = {}
        input_grades: list[InputGradeInfo] = []
        candidate_claim_ids = sorted(str(claim["claim_id"]) for claim in selected_claims)

        for input_key, claim in zip(spec.required_inputs, selected_claims, strict=True):
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

        # Local import avoids a module-level cycle (the methodology helpers import utilities from
        # this module). Shared metadata source so CALC and methodology hashes align (Slice87 G1).
        from idis.services.runs.methodology_deterministic_calculation_helpers import (
            metadata_for_calc,
            selected_pair_coordinate_defect,
        )

        candidate_claims = selected_claims
        coordinate_defect = selected_pair_coordinate_defect(calc_type, candidate_claims)
        if coordinate_defect is not None:
            return None, _blocked_candidate(
                calc_type=calc_type,
                reason=BLOCKED_CALCULATION_FAILED,
                claim_ids=coordinate_defect.claim_ids,
            )
        return (
            CalcCandidate(
                calc_type=calc_type,
                input_values=input_values,
                input_grades=input_grades,
                metadata=metadata_for_calc(calc_type, candidate_claims),
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


def _claims_grouped_by_input_key(claims: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group claims by their existing input key, keeping every observation."""
    by_key: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        input_key = _claim_input_key(claim)
        if input_key is not None:
            by_key.setdefault(input_key, []).append(claim)
    return by_key


_ClaimT = TypeVar("_ClaimT")


class _OperandResolution(NamedTuple, Generic[_ClaimT]):
    """Outcome of resolving one required input key to a single operand."""

    claim: _ClaimT | None
    competing_claim_ids: list[str]
    conflicting_identity: bool


def _operand_claim_id(claim: object) -> str | None:
    """Read the existing claim identity from a dict record or claim object."""
    raw = claim.get("claim_id") if isinstance(claim, dict) else getattr(claim, "claim_id", None)
    if raw is None:
        return None
    text = str(raw)
    return text or None


def _operand_identity_conflicts(
    grouped: Mapping[str, Sequence[_ClaimT]],
    required_keys: Iterable[str],
) -> list[str]:
    """Conflicting content for one claim identity across the required-key union.

    Only this calculation's already eligible, scope-filtered candidates for the
    requested keys participate — unrequested keys and out-of-scope claims never
    reach the check. Identical records under one id remain one observation;
    distinct ids across different keys do not compete here.
    """
    seen: dict[str, _ClaimT] = {}
    conflicts: set[str] = set()
    for input_key in required_keys:
        for claim in grouped.get(input_key, ()):
            claim_id = _operand_claim_id(claim)
            if claim_id is None:
                continue
            prior = seen.get(claim_id)
            if prior is None:
                seen[claim_id] = claim
            elif prior != claim:
                conflicts.add(claim_id)
    return sorted(conflicts)


def _resolve_unique_operand(candidates: Sequence[_ClaimT]) -> _OperandResolution[_ClaimT]:
    """Resolve grouped observations of one required input key to one operand.

    Repeated appearances of the same claim identity with identical record
    content are a single observation. Distinct claim identities compete and
    return ``claim=None`` with the sorted competing ids; the same claim id
    reappearing with conflicting record content is refused identically with
    ``conflicting_identity=True``. Callers never pick a first/last winner.
    """
    distinct: dict[str, _ClaimT] = {}
    anonymous: list[_ClaimT] = []
    conflict_ids: set[str] = set()
    for claim in candidates:
        claim_id = _operand_claim_id(claim)
        if claim_id is None:
            if all(claim != seen for seen in anonymous):
                anonymous.append(claim)
            continue
        prior = distinct.get(claim_id)
        if prior is None:
            distinct[claim_id] = claim
        elif prior != claim:
            conflict_ids.add(claim_id)
    if conflict_ids:
        return _OperandResolution(None, sorted(conflict_ids), True)
    if len(distinct) + len(anonymous) > 1:
        return _OperandResolution(None, sorted(distinct), False)
    if distinct:
        return _OperandResolution(next(iter(distinct.values())), [], False)
    if anonymous:
        return _OperandResolution(anonymous[0], [], False)
    return _OperandResolution(None, [], False)


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

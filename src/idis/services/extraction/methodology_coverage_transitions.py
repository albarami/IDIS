"""Pure, deterministic Task-6 methodology coverage projections.

Persistence owns fencing, locking, and atomicity.  This module owns only the
exact semantic projection so the same contract can be used by completion and
restart verification without trusting a run-step summary.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Never

METHODOLOGY_EVIDENCE_FOUND_ALL_TASKS_V1 = "METHODOLOGY_EVIDENCE_FOUND_ALL_TASKS_V1"
METHODOLOGY_EVIDENCE_FOUND_WITH_TASK_GAPS_V1 = "METHODOLOGY_EVIDENCE_FOUND_WITH_TASK_GAPS_V1"
METHODOLOGY_EVIDENCE_NOT_FOUND_ALL_TASKS_V1 = "METHODOLOGY_EVIDENCE_NOT_FOUND_ALL_TASKS_V1"

_FOUND = "EVIDENCE_FOUND"
_NOT_FOUND = "EVIDENCE_NOT_FOUND"
_TERMINAL_OUTCOMES = frozenset({_FOUND, _NOT_FOUND})


class MethodologyCoverageProjectionError(ValueError):
    """The durable source set cannot produce one exact coverage projection."""


def _fail() -> Never:
    raise MethodologyCoverageProjectionError(
        "methodology coverage projection source set is incomplete"
    )


@dataclass(frozen=True, order=True, slots=True)
class CoverageEvidenceLinkProjection:
    """One normalized claim/EvidenceItem pair stored on a coverage row."""

    claim_id: str
    evidence_id: str
    calc_id: str | None = None

    def to_payload(self) -> dict[str, str | None]:
        return {
            "claim_id": self.claim_id,
            "evidence_id": self.evidence_id,
            "calc_id": self.calc_id,
        }


@dataclass(frozen=True, slots=True)
class CoverageAnswerProjection:
    """Deterministic answer derived from canonical Task-6 claims."""

    answer_text: str
    claim_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    calc_ids: tuple[str, ...] = ()
    requires_calculation: bool = False

    def to_payload(self) -> dict[str, object]:
        return {
            "answer_text": self.answer_text,
            "claim_ids": list(self.claim_ids),
            "evidence_ids": list(self.evidence_ids),
            "calc_ids": list(self.calc_ids),
            "requires_calculation": self.requires_calculation,
        }


@dataclass(frozen=True, slots=True)
class CoverageProjection:
    """Complete mutable payload for one immutable coverage identity."""

    coverage_record_id: str
    methodology_question_record_id: str
    status: str
    reason_code: str
    evidence_links: tuple[CoverageEvidenceLinkProjection, ...] = ()
    answer: CoverageAnswerProjection | None = None
    conflict_ids: tuple[str, ...] = ()
    defect_ids: tuple[str, ...] = ()

    def evidence_payload(self) -> list[dict[str, str | None]]:
        return [link.to_payload() for link in self.evidence_links]

    def answer_payload(self) -> dict[str, object] | None:
        return None if self.answer is None else self.answer.to_payload()


def project_step7_coverage(
    terminal_rows: Iterable[Mapping[str, Any]],
    *,
    expected_coverage_count: int = 100,
) -> tuple[CoverageProjection, ...]:
    """Aggregate exact terminal task outcomes into the Step-7 projection."""
    if expected_coverage_count <= 0:
        _fail()
    outcomes_by_coverage: dict[str, list[str]] = defaultdict(list)
    question_by_coverage: dict[str, str] = {}
    coverage_by_question: dict[str, str] = {}
    seen_tasks: set[str] = set()
    for row in terminal_rows:
        coverage_id = _required_text(row, "coverage_record_id")
        question_id = _required_text(row, "methodology_question_record_id")
        task_id = _required_text(row, "extraction_task_id")
        outcome = _required_text(row, "outcome")
        if outcome not in _TERMINAL_OUTCOMES or task_id in seen_tasks:
            _fail()
        seen_tasks.add(task_id)
        if question_by_coverage.setdefault(coverage_id, question_id) != question_id:
            _fail()
        if coverage_by_question.setdefault(question_id, coverage_id) != coverage_id:
            _fail()
        outcomes_by_coverage[coverage_id].append(outcome)
    if (
        not seen_tasks
        or len(outcomes_by_coverage) != expected_coverage_count
        or len(question_by_coverage) != expected_coverage_count
    ):
        _fail()

    projections: list[CoverageProjection] = []
    for coverage_id in sorted(outcomes_by_coverage):
        outcomes = outcomes_by_coverage[coverage_id]
        distinct = frozenset(outcomes)
        if distinct == {_NOT_FOUND}:
            status = "evidence_missing"
            reason = METHODOLOGY_EVIDENCE_NOT_FOUND_ALL_TASKS_V1
        elif distinct == {_FOUND}:
            status = "extracted"
            reason = METHODOLOGY_EVIDENCE_FOUND_ALL_TASKS_V1
        elif distinct == _TERMINAL_OUTCOMES:
            status = "extracted"
            reason = METHODOLOGY_EVIDENCE_FOUND_WITH_TASK_GAPS_V1
        else:
            _fail()
        projections.append(
            CoverageProjection(
                coverage_record_id=coverage_id,
                methodology_question_record_id=question_by_coverage[coverage_id],
                status=status,
                reason_code=reason,
            )
        )
    return tuple(projections)


def project_step10_coverage(
    terminal_rows: Iterable[Mapping[str, Any]],
    *,
    factual_outputs: Sequence[Mapping[str, Any]],
    claims: Sequence[Mapping[str, Any]],
    evidence_items: Sequence[Mapping[str, Any]],
    sanads: Sequence[Mapping[str, Any]],
    defects: Sequence[Mapping[str, Any]],
    required_calculation_question_ids: Iterable[str] = (),
    expected_coverage_count: int = 100,
) -> tuple[CoverageProjection, ...]:
    """Build the final Task-6 projection from exact normalized lineage."""
    step7 = project_step7_coverage(terminal_rows, expected_coverage_count=expected_coverage_count)
    by_coverage = {item.coverage_record_id: item for item in step7}
    required_calculations = {str(item) for item in required_calculation_question_ids}
    if "" in required_calculations:
        _fail()

    factual_by_id = _unique_by(factual_outputs, "factual_output_record_id")
    claims_by_factual = _group_by_factual(claims)
    evidence_by_factual = _group_by_factual(evidence_items)
    sanads_by_factual = _group_by_factual(sanads)
    defects_by_factual = _group_by_factual(defects)
    canonical_factual_ids = (
        set(claims_by_factual)
        | set(evidence_by_factual)
        | set(sanads_by_factual)
        | set(defects_by_factual)
    )
    if canonical_factual_ids - set(factual_by_id):
        _fail()

    factual_by_coverage: dict[str, list[str]] = defaultdict(list)
    for factual_id, row in factual_by_id.items():
        coverage_id = _required_text(row, "coverage_record_id")
        question_id = _required_text(row, "methodology_question_record_id")
        projection = by_coverage.get(coverage_id)
        if (
            projection is None
            or projection.methodology_question_record_id != question_id
            or projection.status != "extracted"
        ):
            _fail()
        factual_by_coverage[coverage_id].append(factual_id)

    answer_inputs: dict[str, dict[str, str]] = defaultdict(dict)
    evidence_pairs: dict[str, set[tuple[str, str]]] = defaultdict(set)
    defect_ids: dict[str, list[str]] = defaultdict(list)
    seen_defect_ids: set[str] = set()
    for factual_id, factual in factual_by_id.items():
        coverage_id = _required_text(factual, "coverage_record_id")
        factual_claims = claims_by_factual.get(factual_id, [])
        factual_evidence = evidence_by_factual.get(factual_id, [])
        factual_sanads = sanads_by_factual.get(factual_id, [])
        if len(factual_claims) != 1 or not factual_evidence or len(factual_sanads) != 1:
            _fail()
        claim = factual_claims[0]
        claim_id = _required_text(claim, "claim_id")
        claim_text = _required_text(claim, "claim_text")
        previous_text = answer_inputs[coverage_id].setdefault(claim_id, claim_text)
        if previous_text != claim_text:
            _fail()

        factual_evidence_ids: set[str] = set()
        for evidence in factual_evidence:
            if _required_text(evidence, "claim_id") != claim_id:
                _fail()
            evidence_id = _required_text(evidence, "evidence_id")
            if evidence_id in factual_evidence_ids:
                _fail()
            factual_evidence_ids.add(evidence_id)
            evidence_pairs[coverage_id].add((claim_id, evidence_id))

        sanad = factual_sanads[0]
        if (
            _required_text(sanad, "claim_id") != claim_id
            or _required_text(sanad, "primary_evidence_id") not in factual_evidence_ids
            or not _required_text(sanad, "sanad_id")
        ):
            _fail()
        for defect in defects_by_factual.get(factual_id, []):
            if _required_text(defect, "claim_id") != claim_id:
                _fail()
            defect_id = _required_text(defect, "defect_id")
            if defect_id in seen_defect_ids:
                _fail()
            seen_defect_ids.add(defect_id)
            defect_ids[coverage_id].append(defect_id)

    result: list[CoverageProjection] = []
    for previous in step7:
        factual_ids = factual_by_coverage.get(previous.coverage_record_id, [])
        if previous.status == "evidence_missing":
            if factual_ids:
                _fail()
            result.append(previous)
            continue
        if not factual_ids:
            _fail()
        links = tuple(
            CoverageEvidenceLinkProjection(claim_id=claim_id, evidence_id=evidence_id)
            for claim_id, evidence_id in sorted(evidence_pairs[previous.coverage_record_id])
        )
        if not links:
            _fail()
        claims_for_answer = answer_inputs[previous.coverage_record_id]
        claim_ids = tuple(sorted(claims_for_answer))
        evidence_ids = tuple(sorted({link.evidence_id for link in links}))
        requires_calculation = previous.methodology_question_record_id in required_calculations
        answer = None
        status = "extracted" if requires_calculation else "answered"
        if not requires_calculation:
            answer = CoverageAnswerProjection(
                answer_text="\n".join(claims_for_answer[claim_id] for claim_id in claim_ids),
                claim_ids=claim_ids,
                evidence_ids=evidence_ids,
            )
        result.append(
            CoverageProjection(
                coverage_record_id=previous.coverage_record_id,
                methodology_question_record_id=previous.methodology_question_record_id,
                status=status,
                reason_code=previous.reason_code,
                evidence_links=links,
                answer=answer,
                defect_ids=tuple(sorted(defect_ids[previous.coverage_record_id])),
            )
        )
    return tuple(result)


def _required_text(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if value is None:
        _fail()
    cleaned = str(value).strip()
    if not cleaned:
        _fail()
    return cleaned


def _unique_by(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        key = _required_text(row, field)
        if key in result:
            _fail()
        result[key] = row
    return result


def _group_by_factual(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    result: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        result[_required_text(row, "factual_output_record_id")].append(row)
    return result


__all__ = [
    "CoverageAnswerProjection",
    "CoverageEvidenceLinkProjection",
    "CoverageProjection",
    "METHODOLOGY_EVIDENCE_FOUND_ALL_TASKS_V1",
    "METHODOLOGY_EVIDENCE_FOUND_WITH_TASK_GAPS_V1",
    "METHODOLOGY_EVIDENCE_NOT_FOUND_ALL_TASKS_V1",
    "MethodologyCoverageProjectionError",
    "project_step10_coverage",
    "project_step7_coverage",
]

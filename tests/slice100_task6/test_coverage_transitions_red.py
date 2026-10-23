"""RED-first contracts for Task-6 durable coverage projections.

The projector is deliberately pure: repository tests exercise locking and
atomic persistence separately, while these tests pin exact semantic outcomes.
"""

from __future__ import annotations

import pytest

from tests.slice100_task6._support import require_symbol

FOUND_ALL = "METHODOLOGY_EVIDENCE_FOUND_ALL_TASKS_V1"
FOUND_WITH_GAPS = "METHODOLOGY_EVIDENCE_FOUND_WITH_TASK_GAPS_V1"
NOT_FOUND_ALL = "METHODOLOGY_EVIDENCE_NOT_FOUND_ALL_TASKS_V1"


def _project_step7(rows: list[dict[str, str]], *, expected: int):
    projector = require_symbol(
        "idis.services.extraction.methodology_coverage_transitions",
        "project_step7_coverage",
    )
    return projector(rows, expected_coverage_count=expected)


def _project_step10(
    terminal_rows: list[dict[str, str]],
    *,
    factual_outputs: list[dict[str, str]],
    claims: list[dict[str, str]],
    evidence_items: list[dict[str, str]],
    sanads: list[dict[str, str]],
    defects: list[dict[str, str]] | None = None,
    required_calculation_question_ids: tuple[str, ...] = (),
):
    projector = require_symbol(
        "idis.services.extraction.methodology_coverage_transitions",
        "project_step10_coverage",
    )
    return projector(
        terminal_rows,
        factual_outputs=factual_outputs,
        claims=claims,
        evidence_items=evidence_items,
        sanads=sanads,
        defects=defects or [],
        required_calculation_question_ids=required_calculation_question_ids,
        expected_coverage_count=len({str(row["coverage_record_id"]) for row in terminal_rows}),
    )


def _terminal(
    coverage: str,
    question: str,
    task: str,
    outcome: str,
) -> dict[str, str]:
    return {
        "coverage_record_id": coverage,
        "methodology_question_record_id": question,
        "extraction_task_id": task,
        "outcome": outcome,
    }


def test_step7_projects_all_found_all_missing_and_mixed_multi_task_questions() -> None:
    rows = [
        _terminal("coverage-a", "question-a", "task-a1", "EVIDENCE_FOUND"),
        _terminal("coverage-b", "question-b", "task-b1", "EVIDENCE_NOT_FOUND"),
        _terminal("coverage-c", "question-c", "task-c1", "EVIDENCE_FOUND"),
        _terminal("coverage-c", "question-c", "task-c2", "EVIDENCE_NOT_FOUND"),
    ]

    projections = _project_step7(rows, expected=3)

    assert [item.coverage_record_id for item in projections] == [
        "coverage-a",
        "coverage-b",
        "coverage-c",
    ]
    assert [(item.status, item.reason_code) for item in projections] == [
        ("extracted", FOUND_ALL),
        ("evidence_missing", NOT_FOUND_ALL),
        ("extracted", FOUND_WITH_GAPS),
    ]
    assert all(item.evidence_links == () for item in projections)
    assert all(item.answer is None for item in projections)
    assert all(item.conflict_ids == () and item.defect_ids == () for item in projections)


@pytest.mark.parametrize(
    "rows, expected_count",
    (
        (
            [
                _terminal("coverage-a", "question-a", "task-1", "EVIDENCE_FOUND"),
                _terminal("coverage-b", "question-b", "task-1", "EVIDENCE_FOUND"),
            ],
            2,
        ),
        (
            [_terminal("coverage-a", "question-a", "task-1", "PARTIAL")],
            1,
        ),
        (
            [_terminal("coverage-a", "question-a", "task-1", "EVIDENCE_FOUND")],
            2,
        ),
    ),
)
def test_step7_rejects_duplicate_invalid_or_incomplete_source_sets(
    rows: list[dict[str, str]], expected_count: int
) -> None:
    error_cls = require_symbol(
        "idis.services.extraction.methodology_coverage_transitions",
        "MethodologyCoverageProjectionError",
    )

    with pytest.raises(error_cls):
        _project_step7(rows, expected=expected_count)


def test_step7_projection_is_order_independent() -> None:
    rows = [
        _terminal("coverage-b", "question-b", "task-b", "EVIDENCE_NOT_FOUND"),
        _terminal("coverage-a", "question-a", "task-a", "EVIDENCE_FOUND"),
    ]

    assert _project_step7(rows, expected=2) == _project_step7(list(reversed(rows)), expected=2)


def test_step10_projects_exact_answer_evidence_and_defect_lineage() -> None:
    terminal_rows = [
        _terminal("coverage-a", "question-a", "task-a1", "EVIDENCE_FOUND"),
        _terminal("coverage-a", "question-a", "task-a2", "EVIDENCE_NOT_FOUND"),
        _terminal("coverage-b", "question-b", "task-b1", "EVIDENCE_NOT_FOUND"),
    ]
    factual = {
        "factual_output_record_id": "factual-a",
        "coverage_record_id": "coverage-a",
        "methodology_question_record_id": "question-a",
    }
    claim = {
        "factual_output_record_id": "factual-a",
        "claim_id": "claim-a",
        "claim_text": "Verified fictional fact.",
    }
    evidence = {
        "factual_output_record_id": "factual-a",
        "claim_id": "claim-a",
        "evidence_id": "evidence-a",
    }
    sanad = {
        "factual_output_record_id": "factual-a",
        "claim_id": "claim-a",
        "sanad_id": "sanad-a",
        "primary_evidence_id": "evidence-a",
    }
    defect = {
        "factual_output_record_id": "factual-a",
        "claim_id": "claim-a",
        "defect_id": "defect-a",
    }

    projections = _project_step10(
        terminal_rows,
        factual_outputs=[factual],
        claims=[claim],
        evidence_items=[evidence],
        sanads=[sanad],
        defects=[defect],
    )

    answered, missing = projections
    assert answered.status == "answered"
    assert answered.reason_code == FOUND_WITH_GAPS
    assert answered.evidence_links[0].to_payload() == {
        "claim_id": "claim-a",
        "evidence_id": "evidence-a",
        "calc_id": None,
    }
    assert answered.answer is not None
    assert answered.answer.to_payload() == {
        "answer_text": "Verified fictional fact.",
        "claim_ids": ["claim-a"],
        "evidence_ids": ["evidence-a"],
        "calc_ids": [],
        "requires_calculation": False,
    }
    assert answered.defect_ids == ("defect-a",)
    assert answered.conflict_ids == ()
    assert missing.status == "evidence_missing"
    assert missing.reason_code == NOT_FOUND_ALL
    assert missing.evidence_links == ()
    assert missing.answer is None
    assert missing.defect_ids == ()


def test_step10_required_calculation_keeps_found_question_extracted_without_answer() -> None:
    terminal_rows = [_terminal("coverage-a", "question-a", "task-a", "EVIDENCE_FOUND")]
    common = {
        "factual_output_record_id": "factual-a",
        "coverage_record_id": "coverage-a",
        "methodology_question_record_id": "question-a",
    }
    projections = _project_step10(
        terminal_rows,
        factual_outputs=[common],
        claims=[
            {
                "factual_output_record_id": "factual-a",
                "claim_id": "claim-a",
                "claim_text": "Verified fictional fact.",
            }
        ],
        evidence_items=[
            {
                "factual_output_record_id": "factual-a",
                "claim_id": "claim-a",
                "evidence_id": "evidence-a",
            }
        ],
        sanads=[
            {
                "factual_output_record_id": "factual-a",
                "claim_id": "claim-a",
                "sanad_id": "sanad-a",
                "primary_evidence_id": "evidence-a",
            }
        ],
        required_calculation_question_ids=("question-a",),
    )

    assert projections[0].status == "extracted"
    assert projections[0].answer is None
    assert len(projections[0].evidence_links) == 1


def _reused_claim_step10_inputs(
    defect_ids: tuple[str, str],
) -> tuple[
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, str]],
]:
    factual_ids = ("factual-a", "factual-b")
    factual_outputs = [
        {
            "factual_output_record_id": factual_id,
            "coverage_record_id": "coverage-a",
            "methodology_question_record_id": "question-a",
        }
        for factual_id in factual_ids
    ]
    claims = [
        {
            "factual_output_record_id": factual_id,
            "claim_id": "claim-shared",
            "claim_text": "One converged fictional claim.",
        }
        for factual_id in factual_ids
    ]
    evidence_items = [
        {
            "factual_output_record_id": factual_id,
            "claim_id": "claim-shared",
            "evidence_id": evidence_id,
        }
        for factual_id, evidence_id in (
            ("factual-a", "evidence-a"),
            ("factual-b", "evidence-b"),
        )
    ]
    sanads = [
        {
            "factual_output_record_id": factual_id,
            "claim_id": "claim-shared",
            "sanad_id": sanad_id,
            "primary_evidence_id": evidence_id,
        }
        for factual_id, sanad_id, evidence_id in (
            ("factual-a", "sanad-a", "evidence-a"),
            ("factual-b", "sanad-b", "evidence-b"),
        )
    ]
    defects = [
        {
            "factual_output_record_id": factual_id,
            "claim_id": "claim-shared",
            "defect_id": defect_id,
        }
        for factual_id, defect_id in zip(factual_ids, defect_ids, strict=True)
    ]
    return factual_outputs, claims, evidence_items, sanads, defects


def test_step10_allows_reused_claim_with_distinct_evidence_and_defect_ownership() -> None:
    terminal_rows = [_terminal("coverage-a", "question-a", "task-a", "EVIDENCE_FOUND")]
    factual_outputs, claims, evidence_items, sanads, defects = _reused_claim_step10_inputs(
        ("defect-a", "defect-b")
    )

    projection = _project_step10(
        terminal_rows,
        factual_outputs=factual_outputs,
        claims=claims,
        evidence_items=evidence_items,
        sanads=sanads,
        defects=defects,
    )[0]

    assert projection.answer is not None
    assert projection.answer.claim_ids == ("claim-shared",)
    assert projection.answer.answer_text == "One converged fictional claim."
    assert tuple((link.claim_id, link.evidence_id) for link in projection.evidence_links) == (
        ("claim-shared", "evidence-a"),
        ("claim-shared", "evidence-b"),
    )
    assert projection.defect_ids == ("defect-a", "defect-b")


def test_step10_rejects_one_defect_owned_by_two_factual_outputs() -> None:
    error_cls = require_symbol(
        "idis.services.extraction.methodology_coverage_transitions",
        "MethodologyCoverageProjectionError",
    )
    factual_outputs, claims, evidence_items, sanads, defects = _reused_claim_step10_inputs(
        ("defect-shared", "defect-shared")
    )

    with pytest.raises(error_cls):
        _project_step10(
            [_terminal("coverage-a", "question-a", "task-a", "EVIDENCE_FOUND")],
            factual_outputs=factual_outputs,
            claims=claims,
            evidence_items=evidence_items,
            sanads=sanads,
            defects=defects,
        )


@pytest.mark.parametrize("missing_collection", ["claim", "evidence", "sanad"])
def test_step10_rejects_incomplete_canonical_lineage(missing_collection: str) -> None:
    terminal_rows = [_terminal("coverage-a", "question-a", "task-a", "EVIDENCE_FOUND")]
    factual = {
        "factual_output_record_id": "factual-a",
        "coverage_record_id": "coverage-a",
        "methodology_question_record_id": "question-a",
    }
    claim = {
        "factual_output_record_id": "factual-a",
        "claim_id": "claim-a",
        "claim_text": "Verified fictional fact.",
    }
    evidence = {
        "factual_output_record_id": "factual-a",
        "claim_id": "claim-a",
        "evidence_id": "evidence-a",
    }
    sanad = {
        "factual_output_record_id": "factual-a",
        "claim_id": "claim-a",
        "sanad_id": "sanad-a",
        "primary_evidence_id": "evidence-a",
    }
    error_cls = require_symbol(
        "idis.services.extraction.methodology_coverage_transitions",
        "MethodologyCoverageProjectionError",
    )

    with pytest.raises(error_cls):
        _project_step10(
            terminal_rows,
            factual_outputs=[factual],
            claims=[] if missing_collection == "claim" else [claim],
            evidence_items=[] if missing_collection == "evidence" else [evidence],
            sanads=[] if missing_collection == "sanad" else [sanad],
        )


def test_step10_rejects_any_canonical_lineage_for_all_not_found_question() -> None:
    terminal_rows = [_terminal("coverage-a", "question-a", "task-a", "EVIDENCE_NOT_FOUND")]
    error_cls = require_symbol(
        "idis.services.extraction.methodology_coverage_transitions",
        "MethodologyCoverageProjectionError",
    )

    with pytest.raises(error_cls):
        _project_step10(
            terminal_rows,
            factual_outputs=[
                {
                    "factual_output_record_id": "factual-a",
                    "coverage_record_id": "coverage-a",
                    "methodology_question_record_id": "question-a",
                }
            ],
            claims=[],
            evidence_items=[],
            sanads=[],
        )

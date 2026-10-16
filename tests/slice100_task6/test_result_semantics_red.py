"""RED tests for strict terminal result semantics and source admission."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from tests.slice100_task6._support import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
    candidate_span_kwargs,
    extractor_request_kwargs,
    factual_output_kwargs,
    require_symbol,
    terminal_response_kwargs,
)


def _response(**overrides: Any) -> Any:
    response_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorResponse"
    )
    values = terminal_response_kwargs()
    values.update(overrides)
    return response_cls(**values)


def test_evidence_found_is_terminal_nonempty_and_provider_proven() -> None:
    response = _response()

    assert response.outcome == "EVIDENCE_FOUND"
    assert len(response.factual_outputs) == 1
    assert response.gap_finding is None
    assert response.searched_document_ids
    assert response.searched_span_ids
    assert response.provider_receipt.real_call is True


def test_structured_evidence_not_found_is_terminal_without_a_fake_fact() -> None:
    response_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorResponse"
    )
    response = response_cls(**terminal_response_kwargs(outcome="EVIDENCE_NOT_FOUND"))

    assert response.outcome == "EVIDENCE_NOT_FOUND"
    assert response.factual_outputs == ()
    assert response.gap_finding.code == "GOVERNED_EVIDENCE_GAP"
    assert response.gap_finding.finding
    assert response.searched_document_ids
    assert response.searched_span_ids


@pytest.mark.parametrize(
    "internal_status",
    ["COMPLETED", "PARTIAL", "FAILED", "SKIPPED", "BLOCKED", "DEFERRED"],
)
def test_generic_or_internal_statuses_are_not_task6_terminal_outcomes(
    internal_status: str,
) -> None:
    response_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorResponse"
    )
    values = terminal_response_kwargs()
    values["outcome"] = internal_status

    with pytest.raises(ValueError):
        response_cls(**values)


def test_evidence_found_rejects_empty_factual_output() -> None:
    with pytest.raises(ValueError):
        _response(factual_outputs=[])


def test_evidence_not_found_rejects_empty_gap_or_empty_searched_scope() -> None:
    response_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorResponse"
    )
    empty_gap = terminal_response_kwargs(outcome="EVIDENCE_NOT_FOUND")
    empty_gap["gap_finding"] = None
    empty_scope = terminal_response_kwargs(outcome="EVIDENCE_NOT_FOUND")
    empty_scope["searched_span_ids"] = []

    with pytest.raises(ValueError):
        response_cls(**empty_gap)
    with pytest.raises(ValueError):
        response_cls(**empty_scope)


def test_locator_outside_admitted_candidate_set_fails_with_stable_code() -> None:
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    output_cls = require_symbol("idis.models.methodology_extraction", "MethodologyFactualOutput")
    validate = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "validate_methodology_response",
    )
    error_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "MethodologyExtractionValidationError",
    )
    output = output_cls(
        **factual_output_kwargs(
            accepted_source_span_ids=["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"],
            accepted_locator_sha256s=["9" * 64],
        )
    )
    response = _response(factual_outputs=[output])

    with pytest.raises(error_cls) as raised:
        validate(
            request=request_cls(**extractor_request_kwargs()),
            response=response,
        )

    assert raised.value.code == "METHODOLOGY_SOURCE_LOCATOR_REJECTED"


def test_each_accepted_span_must_retain_its_exact_admitted_locator_pair() -> None:
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    candidate_cls = require_symbol("idis.models.methodology_extraction", "MethodologyCandidateSpan")
    output_cls = require_symbol("idis.models.methodology_extraction", "MethodologyFactualOutput")
    validate = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "validate_methodology_response",
    )
    error_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "MethodologyExtractionValidationError",
    )
    first_span_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    second_span_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    first_locator = "f" * 64
    second_locator = "a" * 64
    first_values = candidate_span_kwargs()
    first_values["locator_sha256"] = first_locator
    second_values = candidate_span_kwargs()
    second_values.update(
        span_id=second_span_id,
        ordinal=2,
        locator_sha256=second_locator,
    )
    first = candidate_cls(**first_values)
    second = candidate_cls(**second_values)
    request = request_cls(**extractor_request_kwargs(candidate_spans=[first, second]))
    swapped = output_cls(
        **factual_output_kwargs(
            accepted_source_span_ids=[first_span_id, second_span_id],
            accepted_locator_sha256s=[second_locator, first_locator],
        )
    )
    scope_hash = require_symbol(
        "idis.models.methodology_extraction",
        "compute_methodology_searched_scope_sha256",
    )((first, second))

    with pytest.raises(error_cls) as raised:
        validate(
            request=request,
            response=_response(
                searched_document_ids=[first.document_id],
                searched_span_ids=[first_span_id, second_span_id],
                searched_scope_sha256=scope_hash,
                factual_outputs=[swapped],
            ),
        )

    assert raised.value.code == "METHODOLOGY_SOURCE_LOCATOR_REJECTED"


def test_factual_output_canonicalizes_span_locator_pairs_without_destroying_pairing() -> None:
    output_cls = require_symbol("idis.models.methodology_extraction", "MethodologyFactualOutput")
    first_span_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    second_span_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    first_locator = "f" * 64
    second_locator = "a" * 64

    output = output_cls(
        **factual_output_kwargs(
            accepted_source_span_ids=[second_span_id, first_span_id],
            accepted_locator_sha256s=[second_locator, first_locator],
        )
    )

    assert tuple(
        zip(
            output.accepted_source_span_ids,
            output.accepted_locator_sha256s,
            strict=True,
        )
    ) == (
        (first_span_id, first_locator),
        (second_span_id, second_locator),
    )


def test_provider_must_report_the_complete_admitted_search_scope() -> None:
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    candidate_cls = require_symbol("idis.models.methodology_extraction", "MethodologyCandidateSpan")
    validate = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "validate_methodology_response",
    )
    error_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "MethodologyExtractionValidationError",
    )
    second_values = candidate_span_kwargs()
    second_values.update(
        span_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        ordinal=2,
        locator_sha256="c" * 64,
    )
    first = candidate_cls(**candidate_span_kwargs())
    second = candidate_cls(**second_values)
    request = request_cls(**extractor_request_kwargs(candidate_spans=[first, second]))
    provider_subset = _response(
        searched_document_ids=[first.document_id],
        searched_span_ids=[first.span_id],
    )

    with pytest.raises(error_cls) as raised:
        validate(request=request, response=provider_subset)

    assert raised.value.code == "METHODOLOGY_SEARCH_SCOPE_MISMATCH"


def test_provider_chosen_search_scope_hash_is_rejected() -> None:
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    validate = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "validate_methodology_response",
    )
    error_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "MethodologyExtractionValidationError",
    )

    with pytest.raises(error_cls) as raised:
        validate(
            request=request_cls(**extractor_request_kwargs()),
            response=_response(searched_scope_sha256="9" * 64),
        )

    assert raised.value.code == "METHODOLOGY_SEARCH_SCOPE_MISMATCH"


@pytest.mark.parametrize(
    ("answer_type", "value", "unit"),
    [
        ("boolean", {"type": "monetary", "amount": "1", "currency": "USD"}, "USD"),
        ("numeric", {"type": "boolean", "value": True}, "boolean"),
        ("table", {"type": "text", "value": "not a table"}, "text"),
        ("narrative", {"type": "count", "value": 1}, "items"),
    ],
)
def test_frozen_answer_domain_rejects_a_different_typed_value_family(
    answer_type: str,
    value: dict[str, Any],
    unit: str,
) -> None:
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    output_cls = require_symbol("idis.models.methodology_extraction", "MethodologyFactualOutput")
    validate = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "validate_methodology_response",
    )
    error_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "MethodologyExtractionValidationError",
    )
    request_values = extractor_request_kwargs()
    request_values["expected_answer_schema"] = {"answer_type": answer_type}
    output = output_cls(**factual_output_kwargs(value=value, unit=unit))

    with pytest.raises(error_cls) as raised:
        validate(
            request=request_cls(**request_values),
            response=_response(factual_outputs=[output]),
        )

    assert raised.value.code == "METHODOLOGY_ANSWER_DOMAIN_MISMATCH"


def test_none_period_is_explicit_for_scalar_and_table_outputs() -> None:
    output_cls = require_symbol("idis.models.methodology_extraction", "MethodologyFactualOutput")
    scalar = output_cls(**factual_output_kwargs(period={"kind": "none"}))
    table = output_cls(
        **factual_output_kwargs(
            predicate="fictional_schedule_row",
            value={
                "type": "table_row",
                "contract_version": "methodology-table-contract-v1",
                "columns": {
                    "row_label": "Fictional row",
                    "metric": "fictional_metric",
                    "value": {"type": "count", "value": 1},
                    "unit": "items",
                    "period": {"kind": "none"},
                },
            },
            period={"kind": "none"},
            unit="table_row",
        )
    )

    assert scalar.period.normalized() == "none"
    assert table.value.columns.period.kind == "none"


def test_identical_table_rows_cannot_hide_behind_different_prose_or_citations() -> None:
    response_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorResponse"
    )
    output_cls = require_symbol("idis.models.methodology_extraction", "MethodologyFactualOutput")
    row_value = {
        "type": "table_row",
        "contract_version": "methodology-table-contract-v1",
        "columns": {
            "row_label": "Fictional row",
            "metric": "fictional_metric",
            "value": {"type": "count", "value": 1},
            "unit": "items",
            "period": {"kind": "none"},
        },
    }
    first = output_cls(
        **factual_output_kwargs(
            predicate="fictional_schedule_row",
            value=row_value,
            period={"kind": "none"},
            unit="table_row",
        )
    )
    second = output_cls(
        **factual_output_kwargs(
            predicate="fictional_schedule_row",
            value=row_value,
            period={"kind": "none"},
            unit="table_row",
            claim_text="Different prose for the identical fictional row.",
            accepted_source_span_ids=["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"],
            accepted_locator_sha256s=["c" * 64],
        )
    )
    values = terminal_response_kwargs()
    values["factual_outputs"] = [first, second]

    with pytest.raises(ValueError, match="duplicate methodology table row"):
        response_cls(**values)


def test_factual_output_identity_binds_span_locator_association() -> None:
    output_cls = require_symbol("idis.models.methodology_extraction", "MethodologyFactualOutput")
    span_a = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    span_b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    locator_a = "a" * 64
    locator_b = "b" * 64
    first_mapping = output_cls(
        **factual_output_kwargs(
            accepted_source_span_ids=[span_a, span_b],
            accepted_locator_sha256s=[locator_a, locator_b],
        )
    )
    swapped_mapping = output_cls(
        **factual_output_kwargs(
            accepted_source_span_ids=[span_a, span_b],
            accepted_locator_sha256s=[locator_b, locator_a],
        )
    )

    assert first_mapping.factual_output_id != swapped_mapping.factual_output_id


def test_provider_cannot_replace_the_durable_methodology_question_record_id() -> None:
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    output_cls = require_symbol("idis.models.methodology_extraction", "MethodologyFactualOutput")
    validate = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "validate_methodology_response",
    )
    error_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "MethodologyExtractionValidationError",
    )
    output = output_cls(
        **factual_output_kwargs(
            methodology_question_record_id="17171717-1717-4171-8171-171717171717"
        )
    )
    response = _response(factual_outputs=[output])

    with pytest.raises(error_cls) as raised:
        validate(request=request_cls(**extractor_request_kwargs()), response=response)

    assert raised.value.code == "METHODOLOGY_QUESTION_LINEAGE_MISMATCH"


def test_every_applicable_task_requires_one_nonempty_terminal_result() -> None:
    validate = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "validate_terminal_task_coverage",
    )
    error_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "MethodologyExtractionValidationError",
    )
    task_ids = ("met_fictional_0001", "met_fictional_0002")

    with pytest.raises(error_cls) as raised:
        validate(
            applicable_task_ids=task_ids,
            terminal_results_by_task_id={task_ids[0]: _response()},
        )

    assert raised.value.code == "METHODOLOGY_TASK_COVERAGE_INCOMPLETE"


def test_structured_evidence_not_found_counts_as_the_only_valid_nonfact_terminal() -> None:
    response_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorResponse"
    )
    validate = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "validate_terminal_task_coverage",
    )
    terminal = response_cls(**terminal_response_kwargs(outcome="EVIDENCE_NOT_FOUND"))

    validated = validate(
        applicable_task_ids=("met_fictional_0001",),
        terminal_results_by_task_id={"met_fictional_0001": terminal},
    )

    assert validated == ("met_fictional_0001",)


@pytest.mark.parametrize("phase", ["result_finalization", "evidence_materialization"])
def test_live_source_span_identity_is_rechecked_at_both_durable_phases(phase: str) -> None:
    candidate_cls = require_symbol("idis.models.methodology_extraction", "MethodologyCandidateSpan")
    validate = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "validate_live_source_span_identity",
    )
    error_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "MethodologyExtractionValidationError",
    )
    admitted = candidate_cls(**candidate_span_kwargs())
    drifted_text = "Different fictional source text."
    drifted = candidate_cls(
        **candidate_span_kwargs()
        | {
            "content_sha256": hashlib.sha256(drifted_text.encode("utf-8")).hexdigest(),
            "text": drifted_text,
        }
    )

    with pytest.raises(error_cls) as raised:
        validate(admitted=admitted, current=drifted, phase=phase)

    assert raised.value.code == "METHODOLOGY_SOURCE_LOCATOR_REJECTED"


def test_step7_exact_sets_distinguish_selected_questions_from_planned_tasks() -> None:
    validate = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "validate_step7_exact_terminal_set",
    )
    planned = {
        "task-001": "question-001",
        "task-002": "question-001",
        "task-003": "question-002",
    }

    result = validate(
        selected_question_ids=("question-001", "question-002"),
        planned_task_question_ids=planned,
        attempt_task_ids=tuple(planned),
        result_task_ids=tuple(reversed(planned)),
    )

    assert result.selected_question_ids == ("question-001", "question-002")
    assert result.planned_task_ids == ("task-001", "task-002", "task-003")


@pytest.mark.parametrize(
    "overrides",
    [
        {"selected_question_ids": ("question-001", "question-002", "question-003")},
        {"attempt_task_ids": ("task-001", "task-002")},
        {"attempt_task_ids": ("task-001", "task-002", "task-003", "task-extra")},
        {"result_task_ids": ("task-001", "task-003")},
    ],
)
def test_step7_exact_set_mismatch_fails_closed(overrides: dict[str, Any]) -> None:
    validate = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "validate_step7_exact_terminal_set",
    )
    error_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "MethodologyExtractionValidationError",
    )
    planned = {
        "task-001": "question-001",
        "task-002": "question-001",
        "task-003": "question-002",
    }
    values: dict[str, Any] = {
        "selected_question_ids": ("question-001", "question-002"),
        "planned_task_question_ids": planned,
        "attempt_task_ids": tuple(planned),
        "result_task_ids": tuple(planned),
    }
    values.update(overrides)

    with pytest.raises(error_cls) as raised:
        validate(**values)

    assert raised.value.code == "METHODOLOGY_TASK_COVERAGE_INCOMPLETE"


def test_durable_step7_result_is_typed_restart_stable_and_safe_to_summarize() -> None:
    result_cls = require_symbol(
        "idis.models.methodology_extraction", "DurableMethodologyExtractionExecutionResult"
    )
    terminal = _response()
    result = result_cls(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        selected_question_ids=("question-001",),
        planned_task_question_ids={"task-001": "question-001"},
        attempt_task_ids=("task-001",),
        result_task_ids=("task-001",),
        terminal_results_by_task_id={"task-001": terminal},
    )

    assert result.planned_task_ids == ("task-001",)
    assert result.terminal_results_by_task_id["task-001"] == terminal
    summary = result.to_run_step_summary()
    assert summary["status"] == "COMPLETED"
    assert summary["selected_question_ids"] == ["question-001"]
    assert summary["task_ids"] == ["task-001"]
    assert summary["outcomes"] == {"EVIDENCE_FOUND": 1}
    assert "terminal_results_by_task_id" not in summary

    with pytest.raises(ValueError):
        result_cls(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            selected_question_ids=("question-001",),
            planned_task_question_ids={"task-001": "question-001"},
            attempt_task_ids=("task-001",),
            result_task_ids=("task-001",),
            terminal_results_by_task_id={},
        )

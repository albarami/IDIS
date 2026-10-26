"""RED contracts for span/locator pairs when locators are legitimately shared."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from idis.models.methodology_extraction import (
    MethodologyCandidateSpan,
    MethodologyFactualOutput,
    MethodologyTaskExtractorRequest,
    compute_methodology_searched_scope_sha256,
)
from idis.services.extraction.canonical_methodology_materialization import (
    CanonicalMethodologyMaterializationError,
    validate_canonical_materialization_completion,
)
from tests.slice100_task6._support import (
    DOCUMENT_ID,
    QUESTION_RECORD_ID,
    SHA_B,
    SPAN_ID,
    candidate_span_kwargs,
    extractor_request_kwargs,
    factual_output_kwargs,
)

SECOND_SPAN_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
SECOND_TEXT = "A second fictional span at the same page locator."
SECOND_CONTENT_SHA256 = hashlib.sha256(SECOND_TEXT.encode("utf-8")).hexdigest()


def _shared_locator_candidates() -> tuple[MethodologyCandidateSpan, ...]:
    first = MethodologyCandidateSpan.model_validate(candidate_span_kwargs())
    second = MethodologyCandidateSpan(
        document_id=DOCUMENT_ID,
        span_id=SECOND_SPAN_ID,
        ordinal=2,
        content_sha256=SECOND_CONTENT_SHA256,
        locator_sha256=SHA_B,
        text=SECOND_TEXT,
    )
    return first, second


def test_searched_scope_hash_accepts_distinct_spans_at_one_locator() -> None:
    candidates = _shared_locator_candidates()

    forward = compute_methodology_searched_scope_sha256(candidates)
    reversed_input = compute_methodology_searched_scope_sha256(tuple(reversed(candidates)))

    assert forward == reversed_input


def test_extractor_request_accepts_distinct_spans_at_one_locator() -> None:
    candidates = _shared_locator_candidates()

    request = MethodologyTaskExtractorRequest.model_validate(
        extractor_request_kwargs(candidate_spans=list(candidates))
    )

    assert tuple((span.span_id, span.locator_sha256) for span in request.candidate_spans) == (
        (SPAN_ID, SHA_B),
        (SECOND_SPAN_ID, SHA_B),
    )


def test_factual_output_preserves_two_span_locator_pairs_with_shared_locator() -> None:
    output = MethodologyFactualOutput.model_validate(
        factual_output_kwargs(
            methodology_question_record_id=QUESTION_RECORD_ID,
            accepted_source_span_ids=[SECOND_SPAN_ID, SPAN_ID],
            accepted_locator_sha256s=[SHA_B, SHA_B],
        )
    )

    assert tuple(
        zip(
            output.accepted_source_span_ids,
            output.accepted_locator_sha256s,
            strict=True,
        )
    ) == ((SPAN_ID, SHA_B), (SECOND_SPAN_ID, SHA_B))


def test_governed_output_schema_accepts_shared_locator_in_distinct_pairs() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    schema = json.loads(
        (
            repository_root / "schemas/extraction/methodology_question_extraction_output.json"
        ).read_text(encoding="utf-8")
    )
    payload: dict[str, Any] = {
        "outcome": "EVIDENCE_FOUND",
        "searched_document_ids": [DOCUMENT_ID],
        "searched_span_ids": [SPAN_ID, SECOND_SPAN_ID],
        "searched_scope_sha256": "c" * 64,
        "factual_outputs": [
            {
                "subject": "Fictional Company",
                "predicate": "fictional_recurring_revenue",
                "claim_class": "FINANCIAL_METRIC",
                "value": {"type": "monetary", "amount": "1200.00", "currency": "USD"},
                "period": {"kind": "as_of", "date": "2026-06-30"},
                "unit": "USD",
                "claim_text": "Fictional recurring revenue was USD 1,200.",
                "accepted_source_span_ids": [SPAN_ID, SECOND_SPAN_ID],
                "accepted_locator_sha256s": [SHA_B, SHA_B],
            }
        ],
        "gap_finding": None,
    }

    assert list(Draft202012Validator(schema).iter_errors(payload)) == []


def test_canonical_completion_compares_span_locator_pairs_not_locator_set() -> None:
    output = MethodologyFactualOutput.model_validate(
        factual_output_kwargs(
            accepted_source_span_ids=[SPAN_ID],
            accepted_locator_sha256s=[SHA_B],
        )
    )
    output_id = str(output.factual_output_id)
    pairs = ((SPAN_ID, SHA_B), (SECOND_SPAN_ID, SHA_B))

    completed = validate_canonical_materialization_completion(
        factual_output_ids=[output_id],
        persisted_factual_output_ids=[output_id],
        claim_link_output_ids=[output_id],
        evidence_link_output_ids=[output_id],
        sanad_link_output_ids=[output_id],
        accepted_source_locator_pairs_by_output={output_id: pairs},
        evidence_link_source_locator_pairs_by_output={output_id: pairs},
        expected_defect_ids_by_output={output_id: ()},
        defect_link_pairs=[],
        rejected_count=0,
    )

    assert completed == (output_id,)


def test_canonical_completion_rejects_correct_locator_bound_to_wrong_span() -> None:
    output = MethodologyFactualOutput.model_validate(factual_output_kwargs())
    output_id = str(output.factual_output_id)

    with pytest.raises(CanonicalMethodologyMaterializationError) as exc_info:
        validate_canonical_materialization_completion(
            factual_output_ids=[output_id],
            persisted_factual_output_ids=[output_id],
            claim_link_output_ids=[output_id],
            evidence_link_output_ids=[output_id],
            sanad_link_output_ids=[output_id],
            accepted_source_locator_pairs_by_output={
                output_id: ((SPAN_ID, SHA_B), (SECOND_SPAN_ID, SHA_B))
            },
            evidence_link_source_locator_pairs_by_output={
                output_id: ((SPAN_ID, SHA_B), (DOCUMENT_ID, SHA_B))
            },
            expected_defect_ids_by_output={output_id: ()},
            defect_link_pairs=[],
            rejected_count=0,
        )

    assert exc_info.value.code == "METHODOLOGY_CANONICAL_MATERIALIZATION_INCOMPLETE"

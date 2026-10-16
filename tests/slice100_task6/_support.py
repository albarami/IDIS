"""Shared, synthetic-only fixtures for the Task 6 RED contract suite."""

from __future__ import annotations

import hashlib
import importlib
import uuid
from collections.abc import Mapping
from typing import Any

import pytest

TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEAL_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
SELECTION_ID = "44444444-4444-4444-8444-444444444444"
LEASE_OWNER_ID = "55555555-5555-4555-8555-555555555555"
TASK_RECORD_ID = "66666666-6666-4666-8666-666666666666"
ATTEMPT_ID = "77777777-7777-4777-8777-777777777777"
RESULT_ID = "88888888-8888-4888-8888-888888888888"
DOCUMENT_ID = "99999999-9999-4999-8999-999999999999"
SPAN_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
QUESTION_ID = "mq_financial_dd_fictional_0001"
QUESTION_RECORD_ID = "16161616-1616-4161-8161-161616161616"
VERSION_ID = "financial_dd:frozen-v1"
TASK_ID = "met_fictional_0001"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64
PRICE_CATALOG_ROW_SHA256 = "d2dcb26249b7be143bce3ba02d85dd501a95782cf7e006ade95790fe1240f321"
CANDIDATE_SOURCE_TEXT = "Fictional admitted source text."
CANDIDATE_CONTENT_SHA256 = hashlib.sha256(CANDIDATE_SOURCE_TEXT.encode("utf-8")).hexdigest()
FDD_SOURCE_BYTES = b"fictional-fdd-workbook-source-v1"
FDD_SOURCE_SHA256 = hashlib.sha256(FDD_SOURCE_BYTES).hexdigest()
CDD_SOURCE_BYTES = b"fictional-cdd-template-source-v1"
CDD_SOURCE_SHA256 = hashlib.sha256(CDD_SOURCE_BYTES).hexdigest()


def require_symbol(module_name: str, symbol_name: str) -> Any:
    """Load a wished-for Task 6 symbol without breaking test collection."""

    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        pytest.fail(f"Task 6 module {module_name!r} is missing: {exc}")
    symbol = getattr(module, symbol_name, None)
    assert symbol is not None, f"Task 6 symbol {module_name}.{symbol_name} is missing"
    return symbol


def candidate_span_kwargs() -> dict[str, Any]:
    return {
        "document_id": DOCUMENT_ID,
        "span_id": SPAN_ID,
        "ordinal": 1,
        "content_sha256": CANDIDATE_CONTENT_SHA256,
        "locator_sha256": SHA_B,
        "text": CANDIDATE_SOURCE_TEXT,
    }


def extractor_request_kwargs(*, candidate_spans: list[Any] | None = None) -> dict[str, Any]:
    if candidate_spans is None:
        candidate_cls = require_symbol(
            "idis.models.methodology_extraction", "MethodologyCandidateSpan"
        )
        candidate_spans = [candidate_cls(**candidate_span_kwargs())]
    return {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "extraction_task_id": TASK_ID,
        "task_input_sha256": SHA_C,
        "methodology_type": "financial_dd",
        "methodology_version_id": VERSION_ID,
        "methodology_version_sha256": SHA_D,
        "methodology_question_id": QUESTION_ID,
        "methodology_question_record_id": QUESTION_RECORD_ID,
        "methodology_question_sha256": SHA_E,
        "source_trace": {
            "kind": "fdd_workbook_row",
            "sheet_name": "Fictional P&L",
            "row_number": 4,
            "anchor_cell": "A4",
            "source_sha256": FDD_SOURCE_SHA256,
        },
        "question_text": "What is the fictional recurring revenue?",
        "expected_answer_schema": {"answer_type": "numeric"},
        "required_evidence": [{"evidence_type": "fictional_support", "min_count": 1}],
        "candidate_spans": candidate_spans,
        "predicates": ["fictional_recurring_revenue"],
        "validation_requirements": ["cite_admitted_span"],
        "required_calculations": [{"calc_type": "fictional_bridge"}],
    }


def provider_receipt_kwargs(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "provider": "anthropic",
        "backend": "live",
        "configured_model": "claude-sonnet-4-20250514",
        "observed_model": "claude-sonnet-4-20250514",
        "prompt_id": "METHODOLOGY_QUESTION_EXTRACTION_V1",
        "prompt_version": "1.0.0",
        "prompt_content_sha256": SHA_A,
        "prompt_input_schema_sha256": SHA_B,
        "prompt_output_schema_sha256": SHA_C,
        "request_payload_sha256": SHA_D,
        "provider_request_id": "req_fictional_0001",
        "provider_response_sha256": SHA_E,
        "real_call": True,
        "configured_max_tokens": 512,
        "configured_temperature": "0",
        "configured_json_mode": True,
        "input_tokens": 120,
        "output_tokens": 40,
        "stop_reason": "tool_use",
        "budget_policy_id": "budget-policy-fixture-v1",
        "budget_component": "anthropic",
        "budget_reservation_id": str(uuid.UUID(ATTEMPT_ID)),
        "billable_payload_sha256": SHA_C,
        "billable_payload_utf8_bytes": 512,
        "price_catalog_version": "anthropic-standard-price-catalog-2026-07-20-v1",
        "price_catalog_row_sha256": PRICE_CATALOG_ROW_SHA256,
        "input_usd_per_million_tokens": "3.00000000",
        "output_usd_per_million_tokens": "15.00000000",
        "reserved_calls": 1,
        "reserved_input_tokens": 512,
        "reserved_output_tokens": 512,
        "reserved_embedding_tokens": 0,
        "reserved_usd": "0.00921600",
        "budget_terminal_state": "CONSUMED",
        "receipt_sha256": SHA_F,
    }
    values.update(overrides)
    return values


def hermetic_provider_receipt_kwargs(**overrides: Any) -> dict[str, Any]:
    values = provider_receipt_kwargs(
        backend="hermetic",
        provider_request_id="hermetic_req_fictional_0001",
        real_call=False,
        budget_terminal_state="NOT_BILLABLE",
        reserved_calls=0,
        reserved_input_tokens=0,
        reserved_output_tokens=0,
        reserved_usd="0.00000000",
        input_tokens=0,
        output_tokens=0,
    )
    values.update(overrides)
    return values


def factual_output_kwargs(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "subject": "Fictional Company",
        "methodology_question_record_id": QUESTION_RECORD_ID,
        "predicate": "fictional_recurring_revenue",
        "claim_class": "FINANCIAL_METRIC",
        "value": {"type": "monetary", "amount": "1200.00", "currency": "USD"},
        "period": {"kind": "as_of", "date": "2026-06-30"},
        "unit": "USD",
        "claim_text": "Fictional recurring revenue was USD 1,200.",
        "accepted_source_span_ids": [SPAN_ID],
        "accepted_locator_sha256s": [SHA_B],
    }
    values.update(overrides)
    return values


def default_searched_scope_sha256() -> str:
    candidate_cls = require_symbol("idis.models.methodology_extraction", "MethodologyCandidateSpan")
    compute = require_symbol(
        "idis.models.methodology_extraction",
        "compute_methodology_searched_scope_sha256",
    )
    return compute((candidate_cls(**candidate_span_kwargs()),))


def terminal_response_kwargs(
    *, outcome: str = "EVIDENCE_FOUND", receipt_overrides: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    receipt_cls = require_symbol("idis.models.methodology_extraction", "MethodologyProviderReceipt")
    receipt = provider_receipt_kwargs(**dict(receipt_overrides or {}))
    values: dict[str, Any] = {
        "outcome": outcome,
        "searched_document_ids": [DOCUMENT_ID],
        "searched_span_ids": [SPAN_ID],
        "searched_scope_sha256": default_searched_scope_sha256(),
        "provider_receipt": receipt_cls(**receipt),
    }
    if outcome == "EVIDENCE_FOUND":
        output_cls = require_symbol(
            "idis.models.methodology_extraction", "MethodologyFactualOutput"
        )
        values["factual_outputs"] = [output_cls(**factual_output_kwargs())]
        values["gap_finding"] = None
    else:
        values["factual_outputs"] = []
        values["gap_finding"] = {
            "code": "GOVERNED_EVIDENCE_GAP",
            "finding": "The admitted fictional scope contains no responsive evidence.",
        }
    return values

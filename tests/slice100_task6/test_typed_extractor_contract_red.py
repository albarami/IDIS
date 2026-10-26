"""RED contracts for the task-aware extractor request and provider receipt."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tests.slice100_task6._support import (
    ATTEMPT_ID,
    CDD_SOURCE_SHA256,
    PRICE_CATALOG_ROW_SHA256,
    candidate_span_kwargs,
    default_searched_scope_sha256,
    extractor_request_kwargs,
    hermetic_provider_receipt_kwargs,
    provider_receipt_kwargs,
    require_symbol,
    terminal_response_kwargs,
)


def _prompt_artifact(
    prompt_cls: Any,
    *,
    content: str,
    status: str = "DRAFT",
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
) -> Any:
    exact_json = require_symbol("idis.models.methodology_extraction", "exact_canonical_json_bytes")
    admitted_input_schema = input_schema or {"type": "object"}
    admitted_output_schema = output_schema or {"type": "object"}
    return prompt_cls(
        prompt_id="METHODOLOGY_QUESTION_EXTRACTION_V1",
        version="1.0.0",
        status=status,
        content=content,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        input_schema=admitted_input_schema,
        input_schema_sha256=hashlib.sha256(exact_json(admitted_input_schema)).hexdigest(),
        output_schema=admitted_output_schema,
        output_schema_sha256=hashlib.sha256(exact_json(admitted_output_schema)).hexdigest(),
    )


def test_task_aware_request_carries_question_trace_schema_and_admitted_spans() -> None:
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )

    request = request_cls(**extractor_request_kwargs())

    assert request.methodology_type == "financial_dd"
    assert request.methodology_question_record_id == "16161616-1616-4161-8161-161616161616"
    assert request.source_sheet_or_section == "Fictional P&L"
    assert request.source_row_number == 4
    assert request.source_anchor_cell == "A4"
    assert request.question_text == "What is the fictional recurring revenue?"
    assert request.expected_answer_schema == {"answer_type": "numeric"}
    assert request.required_evidence[0]["evidence_type"] == "fictional_support"
    assert request.candidate_spans[0].ordinal == 1
    assert request.candidate_spans[0].text == "Fictional admitted source text."
    assert request.predicates == ("fictional_recurring_revenue",)
    assert request.validation_requirements == ("cite_admitted_span",)
    assert request.required_calculations[0]["calc_type"] == "fictional_bridge"


def test_task_aware_request_rejects_empty_candidate_scope() -> None:
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )

    with pytest.raises(ValueError):
        request_cls(**extractor_request_kwargs(candidate_spans=[]))


def test_candidate_source_text_is_preserved_verbatim_for_the_wire_request() -> None:
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    candidate_cls = require_symbol("idis.models.methodology_extraction", "MethodologyCandidateSpan")
    exact_text = "  Fictional heading\n\nFictional value: 1  \n"
    values = {
        "document_id": "99999999-9999-4999-8999-999999999999",
        "span_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "ordinal": 1,
        "content_sha256": hashlib.sha256(exact_text.encode("utf-8")).hexdigest(),
        "locator_sha256": "b" * 64,
        "text": exact_text,
    }

    request = request_cls(**extractor_request_kwargs(candidate_spans=[candidate_cls(**values)]))

    assert request.candidate_spans[0].text == exact_text


def test_candidate_source_text_requires_its_exact_utf8_content_hash() -> None:
    candidate_cls = require_symbol("idis.models.methodology_extraction", "MethodologyCandidateSpan")
    exact_text = " \tFictional source — café.\r\n\r\nRetain Ω exactly. \r\n"

    with pytest.raises(ValueError, match="source text does not match content SHA-256"):
        candidate_cls(
            **candidate_span_kwargs()
            | {
                "text": exact_text,
                "content_sha256": "9" * 64,
            }
        )


def test_raw_output_schema_rejects_unknown_fields_inside_range_scalar() -> None:
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "schemas/extraction/methodology_question_extraction_output.json"
        ).read_text(encoding="utf-8")
    )
    payload = {
        "outcome": "EVIDENCE_FOUND",
        "searched_document_ids": ["99999999-9999-4999-8999-999999999999"],
        "searched_span_ids": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
        "searched_scope_sha256": "a" * 64,
        "factual_outputs": [
            {
                "subject": "Fictional Company",
                "predicate": "fictional_range",
                "claim_class": "FINANCIAL_METRIC",
                "value": {
                    "type": "range",
                    "min_value": "10",
                    "max_value": "20",
                    "unit": "fictional_units",
                },
                "period": {"kind": "none"},
                "unit": "fictional_units",
                "claim_text": "The fictional range is 10 to 20 units.",
                "accepted_source_span_ids": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
                "accepted_locator_sha256s": ["b" * 64],
            }
        ],
        "gap_finding": None,
    }
    validator = Draft202012Validator(schema)
    assert list(validator.iter_errors(payload)) == []
    payload["factual_outputs"][0]["value"]["unreviewed_internal_note"] = "must reject"

    errors = list(validator.iter_errors(payload))
    nested_errors = [descendant for error in errors for descendant in (error, *error.context)]
    assert any(error.validator == "additionalProperties" for error in nested_errors)


def test_provider_receipt_records_real_anthropic_request_and_complete_proof() -> None:
    receipt_cls = require_symbol("idis.models.methodology_extraction", "MethodologyProviderReceipt")

    receipt = receipt_cls(**provider_receipt_kwargs())

    assert receipt.provider == "anthropic"
    assert receipt.backend == "live"
    assert receipt.real_call is True
    assert receipt.prompt_id == "METHODOLOGY_QUESTION_EXTRACTION_V1"
    assert receipt.provider_request_id == "req_fictional_0001"
    assert receipt.input_tokens <= receipt.reserved_input_tokens
    assert receipt.output_tokens <= receipt.reserved_output_tokens
    assert receipt.price_catalog_version == "anthropic-standard-price-catalog-2026-07-20-v1"
    assert receipt.price_catalog_row_sha256 == PRICE_CATALOG_ROW_SHA256
    assert receipt.billable_payload_utf8_bytes == receipt.reserved_input_tokens
    assert receipt.reserved_usd == "0.00921600"


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider_request_id": ""},
        {"provider_response_sha256": None},
        {"prompt_id": "GENERIC_EXTRACTION"},
        {"input_tokens": 513},
        {"output_tokens": 513},
        {"reserved_calls": 0},
        {"budget_terminal_state": "RESERVED"},
    ],
)
def test_provider_receipt_rejects_fake_incomplete_or_over_budget_proof(
    overrides: dict[str, Any],
) -> None:
    receipt_cls = require_symbol("idis.models.methodology_extraction", "MethodologyProviderReceipt")

    with pytest.raises(ValueError):
        receipt_cls(**provider_receipt_kwargs(**overrides))


def test_hermetic_fake_satisfies_the_nonoptional_typed_extractor_protocol() -> None:
    protocol = require_symbol(
        "idis.services.extraction.methodology_task_extractor", "MethodologyTaskExtractor"
    )
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    response_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorResponse"
    )
    calls: list[Any] = []

    class HermeticExtractor:
        persistence_backend = "hermetic"
        external_calls_enabled = False
        provider = "anthropic"

        def extract(self, request: Any) -> Any:
            calls.append(request)
            values = terminal_response_kwargs()
            receipt_cls = require_symbol(
                "idis.models.methodology_extraction", "MethodologyProviderReceipt"
            )
            values["provider_receipt"] = receipt_cls(**hermetic_provider_receipt_kwargs())
            return response_cls(**values)

    extractor = HermeticExtractor()
    request = request_cls(**extractor_request_kwargs())

    assert isinstance(extractor, protocol)
    response = extractor.extract(request)
    assert calls == [request]
    assert response.outcome == "EVIDENCE_FOUND"
    assert response.provider_receipt.real_call is False
    assert response.provider_receipt.backend == "hermetic"


def test_strict_semantic_completion_rejects_honestly_hermetic_receipt() -> None:
    receipt_cls = require_symbol("idis.models.methodology_extraction", "MethodologyProviderReceipt")
    validate = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "validate_strict_provider_receipt",
    )
    error_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "MethodologyExtractionValidationError",
    )
    receipt = receipt_cls(**hermetic_provider_receipt_kwargs())

    with pytest.raises(error_cls) as raised:
        validate(receipt)

    assert raised.value.code == "METHODOLOGY_REAL_PROVIDER_PROOF_REQUIRED"


def test_production_extractor_uses_one_hermetic_lower_transport_call_honestly() -> None:
    extractor_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "AnthropicMethodologyTaskExtractor",
    )
    prompt_cls = require_symbol("idis.models.methodology_extraction", "MethodologyPromptArtifact")
    transport_response_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "AnthropicMethodologyTransportResponse",
    )
    price_catalog = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "ANTHROPIC_STANDARD_PRICE_CATALOG",
    )
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    calls: list[dict[str, Any]] = []

    class HermeticAnthropicTransport:
        backend = "hermetic"

        def create_message(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return transport_response_cls(
                backend="hermetic",
                provider_request_id="hermetic_req_fictional_0001",
                observed_model="claude-sonnet-4-20250514",
                output_payload={
                    "outcome": "EVIDENCE_FOUND",
                    "searched_document_ids": ["99999999-9999-4999-8999-999999999999"],
                    "searched_span_ids": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
                    "searched_scope_sha256": default_searched_scope_sha256(),
                    "factual_outputs": [
                        {
                            "subject": "Fictional Company",
                            "predicate": "fictional_recurring_revenue",
                            "claim_class": "FINANCIAL_METRIC",
                            "value": {
                                "type": "monetary",
                                "amount": "1200.00",
                                "currency": "USD",
                            },
                            "period": {"kind": "as_of", "date": "2026-06-30"},
                            "unit": "USD",
                            "claim_text": "Fictional recurring revenue was USD 1,200.",
                            "accepted_source_span_ids": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
                            "accepted_locator_sha256s": ["b" * 64],
                        }
                    ],
                    "gap_finding": None,
                },
                input_tokens=120,
                output_tokens=40,
                latency_ms=7,
                stop_reason="tool_use",
                provider_response_sha256="e" * 64,
            )

    prompt = _prompt_artifact(
        prompt_cls,
        content="Extract only cited methodology facts as strict JSON.",
        output_schema={"type": "object", "required": ["outcome"]},
    )
    extractor = extractor_cls(
        transport=HermeticAnthropicTransport(),
        prompt_artifact=prompt,
        configured_model="claude-sonnet-4-20250514",
        max_tokens=512,
        temperature="0",
        price_catalog=price_catalog,
    )

    response = extractor.extract(request_cls(**extractor_request_kwargs()))

    assert len(calls) == 1
    assert calls[0]["retries"] == 0
    assert calls[0]["model"] == "claude-sonnet-4-20250514"
    assert calls[0]["max_tokens"] == 512
    assert calls[0]["temperature"] == 0.0
    assert calls[0]["system"] == prompt.content
    assert calls[0]["messages"]
    assert calls[0]["tools"][0]["input_schema"] == prompt.output_schema
    assert calls[0]["tools"][0]["strict"] is True
    assert "cache_control" not in calls[0]
    assert calls[0].get("batch_mode", False) is False
    assert response.provider_receipt.backend == "hermetic"
    assert response.provider_receipt.real_call is False

    with pytest.raises(ValueError):
        transport_response_cls(
            backend="hermetic",
            provider_request_id="hermetic_req_fictional_0002",
            observed_model="claude-sonnet-4-20250514",
            output_payload={},
            input_tokens=0,
            output_tokens=0,
            latency_ms=7,
            stop_reason="end_turn",
            provider_response_sha256="e" * 64,
            real_call=True,
        )


@pytest.mark.parametrize("stop_reason", ["end_turn", "max_tokens"])
def test_production_extractor_requires_forced_tool_stop_for_hermetic_transport(
    stop_reason: str,
) -> None:
    extractor_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "AnthropicMethodologyTaskExtractor",
    )
    prompt_cls = require_symbol("idis.models.methodology_extraction", "MethodologyPromptArtifact")
    transport_response_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "AnthropicMethodologyTransportResponse",
    )
    error_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "MethodologyExtractionValidationError",
    )
    price_catalog = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "ANTHROPIC_STANDARD_PRICE_CATALOG",
    )
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )

    class HermeticAnthropicTransport:
        backend = "hermetic"

        def create_message(self, **_kwargs: Any) -> Any:
            return transport_response_cls(
                backend="hermetic",
                provider_request_id="hermetic_req_stop_contract_0001",
                observed_model="claude-sonnet-4-20250514",
                output_payload={
                    "outcome": "EVIDENCE_NOT_FOUND",
                    "searched_document_ids": ["99999999-9999-4999-8999-999999999999"],
                    "searched_span_ids": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
                    "searched_scope_sha256": default_searched_scope_sha256(),
                    "factual_outputs": [],
                    "gap_finding": {
                        "code": "GOVERNED_EVIDENCE_GAP",
                        "finding": "No responsive fictional evidence was admitted.",
                    },
                },
                input_tokens=0,
                output_tokens=0,
                latency_ms=7,
                stop_reason=stop_reason,
                provider_response_sha256="e" * 64,
            )

    extractor = extractor_cls(
        transport=HermeticAnthropicTransport(),
        prompt_artifact=_prompt_artifact(
            prompt_cls,
            content="Extract only admitted methodology facts.",
            output_schema={"type": "object", "required": ["outcome"]},
        ),
        configured_model="claude-sonnet-4-20250514",
        max_tokens=512,
        temperature="0",
        price_catalog=price_catalog,
    )

    with pytest.raises(error_cls) as raised:
        extractor.extract(request_cls(**extractor_request_kwargs()))

    assert raised.value.code == "METHODOLOGY_RESPONSE_TRUNCATED"


def test_budget_reservation_hashes_the_full_serialized_billable_request() -> None:
    serialize = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "serialize_anthropic_billable_request",
    )
    reservation_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyBudgetReservation"
    )
    wire_cls = require_symbol(
        "idis.models.methodology_extraction", "AnthropicMethodologyWireRequest"
    )
    tool_cls = require_symbol("idis.models.methodology_extraction", "AnthropicMethodologyTool")
    choice_cls = require_symbol(
        "idis.models.methodology_extraction", "AnthropicMethodologyToolChoice"
    )
    catalog = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "ANTHROPIC_STANDARD_PRICE_CATALOG",
    )
    resolve_price = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "resolve_anthropic_standard_price",
    )
    tool = tool_cls(
        name="methodology_result",
        description="Return governed fictional methodology evidence.",
        input_schema={"type": "object", "required": ["outcome"]},
        strict=True,
    )
    wire_request = wire_cls(
        model="claude-sonnet-4-20250514",
        system="governed system prompt\nwith exact layout\n",
        messages=({"role": "user", "content": [{"type": "text", "text": "facts\n"}]},),
        tools=(tool,),
        tool_choice=choice_cls(
            type="tool", name="methodology_result", disable_parallel_tool_use=True
        ),
        max_tokens=512,
        temperature=0.0,
    )
    price = resolve_price(catalog=catalog, model_id=wire_request.model)
    payload = serialize(wire_request=wire_request)
    reservation = reservation_cls.from_anthropic_billable_payload(
        payload=payload,
        budget_policy_id="budget-policy-fixture-v1",
        model_id=wire_request.model,
        output_token_ceiling=512,
        context_window_limit=price.context_window_limit,
        price_catalog_version=price.catalog_version,
        price_catalog_mode=price.mode,
        price_catalog_row_sha256=price.row_sha256,
        input_usd_per_million_tokens=price.input_usd_per_million_tokens,
        output_usd_per_million_tokens=price.output_usd_per_million_tokens,
    )

    assert reservation.billable_payload_sha256 == hashlib.sha256(payload).hexdigest()
    assert reservation.billable_payload_utf8_bytes == len(payload)
    assert reservation.reserved_input_tokens == len(payload)
    assert reservation.reserved_output_tokens == 512
    expected_usd = len(payload) * 3 / 1_000_000 + 512 * 15 / 1_000_000
    assert float(reservation.worst_case_usd) >= expected_usd
    assert len(str(reservation.worst_case_usd).split(".")[1]) == 8

    changed_tool = tool.model_copy(update={"input_schema": {"type": "array"}})
    for field, replacement in (
        ("system", "different governed prompt"),
        ("messages", ({"role": "user", "content": [{"type": "text", "text": "other"}]},)),
        ("tools", (changed_tool,)),
        ("max_tokens", 511),
    ):
        changed = wire_request.model_copy(update={field: replacement})
        assert hashlib.sha256(serialize(wire_request=changed)).hexdigest() != (
            reservation.billable_payload_sha256
        )

    with pytest.raises(ValueError, match="context"):
        reservation_cls.from_anthropic_billable_payload(
            payload=b"x" * 199_500,
            budget_policy_id="budget-policy-fixture-v1",
            model_id=wire_request.model,
            output_token_ceiling=512,
            context_window_limit=200_000,
            price_catalog_version=price.catalog_version,
            price_catalog_mode=price.mode,
            price_catalog_row_sha256=price.row_sha256,
            input_usd_per_million_tokens="3.00000000",
            output_usd_per_million_tokens="15.00000000",
        )


def test_code_owned_price_catalog_is_exact_and_rejects_unknown_models() -> None:
    catalog = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "ANTHROPIC_STANDARD_PRICE_CATALOG",
    )
    resolve = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "resolve_anthropic_standard_price",
    )
    error_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "MethodologyExtractorConfigurationError",
    )

    assert catalog["version"] == "anthropic-standard-price-catalog-2026-07-20-v1"
    assert set(catalog["models"]) == {"claude-sonnet-4-20250514"}
    price = resolve(catalog=catalog, model_id="claude-sonnet-4-20250514")
    assert price.input_usd_per_million_tokens == "3.00000000"
    assert price.output_usd_per_million_tokens == "15.00000000"

    with pytest.raises(error_cls) as raised:
        resolve(catalog=catalog, model_id="claude-unknown")
    assert raised.value.code == "METHODOLOGY_MODEL_PRICE_UNAVAILABLE"

    drifted_catalog = dict(catalog)
    drifted_catalog["version"] = "unreviewed-price-catalog"
    with pytest.raises(error_cls) as drifted:
        resolve(catalog=drifted_catalog, model_id="claude-sonnet-4-20250514")
    assert drifted.value.code == "METHODOLOGY_MODEL_PRICE_UNAVAILABLE"


def test_source_trace_is_discriminated_between_cdd_section_and_fdd_workbook_row() -> None:
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    cdd_values = extractor_request_kwargs()
    cdd_values.update(
        methodology_type="commercial_dd",
        source_trace={
            "kind": "cdd_section",
            "source_section": "Fictional commercial template section",
            "source_sha256": CDD_SOURCE_SHA256,
        },
    )

    cdd = request_cls(**cdd_values)
    fdd = request_cls(**extractor_request_kwargs())

    assert cdd.source_trace.kind == "cdd_section"
    assert cdd.source_sheet_or_section == "Fictional commercial template section"
    assert cdd.source_row_number is None
    assert cdd.source_anchor_cell is None
    assert fdd.source_trace.kind == "fdd_workbook_row"
    assert fdd.source_row_number == 4
    assert fdd.source_anchor_cell == "A4"

    bad_cdd = dict(cdd_values)
    bad_cdd["source_trace"] = dict(cdd_values["source_trace"], row_number=4)
    with pytest.raises(ValueError):
        request_cls(**bad_cdd)

    bad_fdd = extractor_request_kwargs()
    bad_fdd["source_trace"] = dict(bad_fdd["source_trace"], anchor_cell="")
    with pytest.raises(ValueError):
        request_cls(**bad_fdd)


def test_task_aware_request_rejects_legacy_fdd_source_trace_fields() -> None:
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    values = extractor_request_kwargs()
    values.pop("source_trace", None)
    values.update(
        source_sheet_or_section="Fictional P&L",
        source_row_number=4,
        source_anchor_cell="A4",
    )

    with pytest.raises(ValueError):
        request_cls(**values)


def test_task_aware_request_rejects_absent_source_trace() -> None:
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    values = extractor_request_kwargs()
    values.pop("source_trace", None)
    values.pop("source_sheet_or_section", None)
    values.pop("source_row_number", None)
    values.pop("source_anchor_cell", None)

    with pytest.raises(ValueError):
        request_cls(**values)


def test_prepare_is_zero_io_and_dispatch_requires_matching_committed_admission() -> None:
    extractor_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "AnthropicMethodologyTaskExtractor",
    )
    prompt_cls = require_symbol("idis.models.methodology_extraction", "MethodologyPromptArtifact")
    admission_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyCallAdmissionProof"
    )
    prepared_cls = require_symbol("idis.models.methodology_extraction", "PreparedMethodologyCall")
    dispatched_cls = require_symbol(
        "idis.models.methodology_extraction", "DispatchedMethodologyCall"
    )
    terminalization_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyCallTerminalizationProof"
    )
    validated_cls = require_symbol(
        "idis.models.methodology_extraction", "ValidatedDispatchedMethodologyResult"
    )
    receipt_hash = require_symbol(
        "idis.models.methodology_extraction",
        "compute_methodology_provider_receipt_sha256",
    )
    transport_response_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "AnthropicMethodologyTransportResponse",
    )
    price_catalog = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "ANTHROPIC_STANDARD_PRICE_CATALOG",
    )
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    error_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "MethodologyExtractorConfigurationError",
    )
    calls: list[dict[str, Any]] = []

    class CountingTransport:
        backend = "hermetic"

        def create_message(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return transport_response_cls(
                backend="hermetic",
                provider_request_id="hermetic_req_prepare_0001",
                observed_model="claude-sonnet-4-20250514",
                output_payload={
                    "outcome": "EVIDENCE_NOT_FOUND",
                    "searched_document_ids": ["99999999-9999-4999-8999-999999999999"],
                    "searched_span_ids": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
                    "searched_scope_sha256": default_searched_scope_sha256(),
                    "factual_outputs": [],
                    "gap_finding": {
                        "code": "GOVERNED_EVIDENCE_GAP",
                        "finding": "No responsive fictional evidence was admitted.",
                    },
                },
                input_tokens=0,
                output_tokens=0,
                latency_ms=7,
                stop_reason="tool_use",
                provider_response_sha256="e" * 64,
            )

    prompt = _prompt_artifact(
        prompt_cls,
        content="Extract only admitted methodology facts.",
        output_schema={"type": "object", "required": ["outcome"]},
    )
    extractor = extractor_cls(
        transport=CountingTransport(),
        prompt_artifact=prompt,
        configured_model="claude-sonnet-4-20250514",
        max_tokens=512,
        temperature="0",
        price_catalog=price_catalog,
    )
    request = request_cls(**extractor_request_kwargs())

    prepared = extractor.prepare(request, budget_policy_id="hermetic")
    other_policy_prepared = extractor.prepare(
        request,
        budget_policy_id="hermetic-policy-other",
    )

    assert isinstance(prepared, prepared_cls)
    assert calls == []
    assert prepared.budget_reservation_id != other_policy_prepared.budget_reservation_id
    assert prepared.prepared_call_sha256 != other_policy_prepared.prepared_call_sha256
    assert prepared.billable_payload
    assert (
        prepared.reservation.billable_payload_sha256
        == hashlib.sha256(prepared.billable_payload).hexdigest()
    )
    admission = admission_cls(
        backend="hermetic",
        committed=True,
        attempt_id=ATTEMPT_ID,
        budget_reservation_id=prepared.budget_reservation_id,
        prepared_call_sha256=prepared.prepared_call_sha256,
        task_input_sha256=request.task_input_sha256,
        price_catalog_row_sha256=prepared.reservation.price_catalog_row_sha256,
        worker_fencing_token=1,
    )
    substituted_catalog_admission = admission.model_copy(
        update={"price_catalog_row_sha256": "f" * 64}
    )
    with pytest.raises(error_cls) as catalog_mismatch:
        extractor.dispatch(prepared, admission=substituted_catalog_admission)
    assert catalog_mismatch.value.code == "METHODOLOGY_PRECALL_ADMISSION_INVALID"
    assert calls == []

    dispatched = extractor.dispatch(prepared, admission=admission)
    assert isinstance(dispatched, dispatched_cls)
    assert dispatched.budget_state == "RESERVED"
    assert not hasattr(dispatched, "provider_receipt")
    assert len(calls) == 1
    assert calls[0]["retries"] == 0
    validated = extractor.validate_dispatched(dispatched, request=request)
    assert isinstance(validated, validated_cls)
    assert validated.budget_state == "RESERVED"
    assert validated.outcome == "EVIDENCE_NOT_FOUND"
    assert not hasattr(validated, "provider_receipt")
    prospective_receipt_sha256 = extractor.compute_prospective_receipt_sha256(
        dispatched,
        validated=validated,
        budget_terminal_state="NOT_BILLABLE",
        budget_policy_id="hermetic",
    )
    tampered_validated = validated.model_copy(update={"searched_scope_sha256": "7" * 64})
    with pytest.raises(error_cls) as invalid_validated:
        extractor.compute_prospective_receipt_sha256(
            dispatched,
            validated=tampered_validated,
            budget_terminal_state="NOT_BILLABLE",
            budget_policy_id="hermetic",
        )
    assert invalid_validated.value.code == "METHODOLOGY_TERMINALIZATION_PROOF_INVALID"
    terminalization = terminalization_cls.from_validated_result(
        dispatched_call=dispatched,
        validated_result=validated,
        admission=admission,
        backend="hermetic",
        budget_terminal_state="NOT_BILLABLE",
        budget_policy_id="hermetic",
        prospective_receipt_sha256=prospective_receipt_sha256,
    )
    with pytest.raises(ValueError, match="policy"):
        terminalization_cls.from_validated_result(
            dispatched_call=dispatched,
            validated_result=validated,
            admission=admission,
            backend="hermetic",
            budget_terminal_state="NOT_BILLABLE",
            budget_policy_id="hermetic-policy-other",
            prospective_receipt_sha256=prospective_receipt_sha256,
        )
    response = extractor.finalize(
        dispatched,
        validated=validated,
        terminalization_proof=terminalization,
    )
    assert response.outcome == "EVIDENCE_NOT_FOUND"
    assert response.provider_receipt.budget_terminal_state == "NOT_BILLABLE"
    assert response.provider_receipt.receipt_sha256 == prospective_receipt_sha256
    swapped_values = terminalization.model_dump()
    swapped_values["budget_policy_id"] = "hermetic-policy-other"
    swapped_draft = terminalization_cls.model_construct(
        **{**swapped_values, "terminalization_proof_sha256": "0" * 64}
    )
    swapped_values["terminalization_proof_sha256"] = (
        swapped_draft.expected_terminalization_proof_sha256()
    )
    swapped_proof = terminalization_cls(**swapped_values)
    with pytest.raises(error_cls) as swapped:
        extractor.finalize(
            dispatched,
            validated=validated,
            terminalization_proof=swapped_proof,
        )
    assert swapped.value.code == "METHODOLOGY_TERMINALIZATION_PROOF_INVALID"
    substituted_catalog_values = terminalization.model_dump()
    substituted_catalog_values["price_catalog_row_sha256"] = "f" * 64
    substituted_catalog_draft = terminalization_cls.model_construct(
        **{
            **substituted_catalog_values,
            "terminalization_proof_sha256": "0" * 64,
        }
    )
    substituted_catalog_values["terminalization_proof_sha256"] = (
        substituted_catalog_draft.expected_terminalization_proof_sha256()
    )
    substituted_catalog_proof = terminalization_cls(**substituted_catalog_values)
    with pytest.raises(error_cls) as substituted_catalog:
        extractor.finalize(
            dispatched,
            validated=validated,
            terminalization_proof=substituted_catalog_proof,
        )
    assert substituted_catalog.value.code == "METHODOLOGY_TERMINALIZATION_PROOF_INVALID"
    receipt_fields = response.provider_receipt.model_dump(exclude={"receipt_sha256"})
    assert (
        receipt_hash(
            receipt_fields=receipt_fields,
            validated_result_sha256=validated.validated_result_sha256,
        )
        == prospective_receipt_sha256
    )
    with pytest.raises(ValueError):
        receipt_hash(
            receipt_fields={**receipt_fields, "unreviewed_field": True},
            validated_result_sha256=validated.validated_result_sha256,
        )

    with pytest.raises(error_cls) as missing:
        extractor.dispatch(prepared, admission=None)
    assert missing.value.code == "METHODOLOGY_PRECALL_ADMISSION_REQUIRED"

    tampered = admission.model_copy(update={"prepared_call_sha256": "9" * 64})
    with pytest.raises(error_cls) as mismatch:
        extractor.dispatch(prepared, admission=tampered)
    assert mismatch.value.code == "METHODOLOGY_PRECALL_ADMISSION_INVALID"

    bad_terminalization = terminalization.model_copy(update={"dispatched_call_sha256": "8" * 64})
    with pytest.raises(error_cls) as preterminal:
        extractor.finalize(
            dispatched,
            validated=validated,
            terminalization_proof=bad_terminalization,
        )
    assert preterminal.value.code == "METHODOLOGY_TERMINALIZATION_PROOF_INVALID"


def test_transport_latency_is_measured_and_content_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = __import__(
        "idis.services.extraction.methodology_task_extractor",
        fromlist=["AnthropicSDKMethodologyTransport"],
    )
    transport_cls = module.AnthropicSDKMethodologyTransport
    response_cls = module.AnthropicMethodologyTransportResponse
    dispatched_cls = require_symbol(
        "idis.models.methodology_extraction", "DispatchedMethodologyCall"
    )
    admission_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyCallAdmissionProof"
    )

    provider_payload = {
        "id": "req_fictional_latency_0001",
        "model": "claude-sonnet-4-20250514",
        "content": [
            {
                "type": "tool_use",
                "name": "methodology_result",
                "input": {
                    "outcome": "EVIDENCE_NOT_FOUND",
                    "searched_document_ids": [],
                    "searched_span_ids": [],
                    "searched_scope_sha256": "a" * 64,
                    "factual_outputs": [],
                    "gap_finding": {
                        "code": "GOVERNED_EVIDENCE_GAP",
                        "finding": "No admitted evidence answered the fictional question.",
                    },
                },
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "stop_reason": "tool_use",
    }
    create_calls: list[dict[str, Any]] = []

    class FakeMessages:
        def create(self, **kwargs: Any) -> Any:
            create_calls.append(kwargs)
            return SimpleNamespace(model_dump=lambda **_kwargs: provider_payload)

    fake_client = SimpleNamespace(messages=FakeMessages())
    ticks = iter((1_000_000, 4_500_000))
    monkeypatch.setattr(module.time, "monotonic_ns", lambda: next(ticks))
    transport = transport_cls(
        api_key="fictional-test-key",
        client_factory=lambda **_kwargs: fake_client,
    )

    response = transport.create_message(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        temperature=0.0,
        system="governed fictional prompt",
        messages=[{"role": "user", "content": "fictional"}],
        tools=[
            {
                "name": "methodology_result",
                "description": "Return governed fictional methodology evidence.",
                "input_schema": {"type": "object"},
                "strict": True,
            }
        ],
        tool_choice={
            "type": "tool",
            "name": "methodology_result",
            "disable_parallel_tool_use": True,
        },
        retries=0,
        batch_mode=False,
    )

    assert create_calls
    assert response.latency_ms == 4
    with pytest.raises(ValueError):
        response_cls(
            **response.model_dump(exclude={"latency_ms"}),
            latency_ms=-1,
        )

    prompt_cls = require_symbol("idis.models.methodology_extraction", "MethodologyPromptArtifact")
    prompt = _prompt_artifact(prompt_cls, content="governed fictional prompt")
    prepared = module.AnthropicMethodologyTaskExtractor(
        transport=SimpleNamespace(backend="hermetic"),
        prompt_artifact=prompt,
        configured_model="claude-sonnet-4-20250514",
        max_tokens=512,
        temperature="0",
        price_catalog=module.ANTHROPIC_STANDARD_PRICE_CATALOG,
    ).prepare(
        require_symbol("idis.models.methodology_extraction", "MethodologyTaskExtractorRequest")(
            **extractor_request_kwargs()
        ),
        budget_policy_id="hermetic",
    )
    admission = admission_cls(
        backend="hermetic",
        committed=True,
        attempt_id=ATTEMPT_ID,
        budget_reservation_id=prepared.budget_reservation_id,
        prepared_call_sha256=prepared.prepared_call_sha256,
        task_input_sha256=prepared.request.task_input_sha256,
        price_catalog_row_sha256=prepared.reservation.price_catalog_row_sha256,
        worker_fencing_token=1,
    )
    dispatched = dispatched_cls.from_provider_response(
        prepared_call=prepared,
        admission=admission,
        backend="hermetic",
        provider_request_id="hermetic_req_latency_0001",
        observed_model="claude-sonnet-4-20250514",
        output_payload=provider_payload["content"][0]["input"],
        input_tokens=10,
        output_tokens=5,
        latency_ms=4,
        stop_reason="tool_use",
        provider_response_sha256="e" * 64,
    )
    changed = dispatched.model_copy(update={"latency_ms": 5})

    assert dispatched.expected_dispatched_call_sha256() == dispatched.dispatched_call_sha256
    assert changed.expected_dispatched_call_sha256() != dispatched.dispatched_call_sha256


def test_injected_sdk_client_factory_is_hermetic_and_never_claims_a_real_call() -> None:
    module = __import__(
        "idis.services.extraction.methodology_task_extractor",
        fromlist=["AnthropicSDKMethodologyTransport"],
    )
    raw_response = {
        "id": "req_fictional_hermetic_sdk_0001",
        "model": "claude-sonnet-4-20250514",
        "content": [],
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "stop_reason": "tool_use",
    }
    injected = module.AnthropicSDKMethodologyTransport(
        api_key="fictional-test-key",
        client_factory=lambda **_kwargs: SimpleNamespace(
            messages=SimpleNamespace(
                create=lambda **_sdk_kwargs: SimpleNamespace(
                    model_dump=lambda **_dump_kwargs: raw_response
                )
            )
        ),
    )

    assert injected.backend == "hermetic"
    response = injected.create_message(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        temperature=0.0,
        system="governed fictional prompt",
        messages=[{"role": "user", "content": "fictional"}],
        tools=[
            {
                "name": "methodology_result",
                "description": "Return governed fictional methodology evidence.",
                "input_schema": {"type": "object"},
                "strict": True,
            }
        ],
        tool_choice={
            "type": "tool",
            "name": "methodology_result",
            "disable_parallel_tool_use": True,
        },
        retries=0,
        batch_mode=False,
    )
    assert response.backend == "hermetic"
    assert response.real_call is False

    production = module.AnthropicSDKMethodologyTransport(api_key="not-read-before-dispatch")
    assert production.backend == "live"


def test_budget_payload_and_sdk_dispatch_use_one_exact_strict_wire_object() -> None:
    module = __import__(
        "idis.services.extraction.methodology_task_extractor",
        fromlist=["AnthropicSDKMethodologyTransport"],
    )
    model_module = __import__(
        "idis.models.methodology_extraction",
        fromlist=["MethodologyPromptArtifact"],
    )
    repo_root = Path(__file__).resolve().parents[2]
    raw_prompt = (repo_root / "prompts/methodology_question_extraction/1.0.0/prompt.md").read_text(
        encoding="utf-8"
    )
    input_schema = json.loads(
        (repo_root / "schemas/extraction/methodology_question_extraction_input.json").read_text(
            encoding="utf-8"
        )
    )
    output_schema = json.loads(
        (repo_root / "schemas/extraction/methodology_question_extraction_output.json").read_text(
            encoding="utf-8"
        )
    )
    prompt = model_module.MethodologyPromptArtifact(
        prompt_id="METHODOLOGY_QUESTION_EXTRACTION_V1",
        version="1.0.0",
        status="PROD",
        content=raw_prompt,
        content_sha256=hashlib.sha256(raw_prompt.encode("utf-8")).hexdigest(),
        input_schema=input_schema,
        input_schema_sha256=hashlib.sha256(
            model_module.canonical_json_bytes(input_schema)
        ).hexdigest(),
        output_schema=output_schema,
        output_schema_sha256=hashlib.sha256(
            model_module.canonical_json_bytes(output_schema)
        ).hexdigest(),
    )
    sdk_calls: list[dict[str, Any]] = []
    provider_payload = {
        "id": "req_fictional_wire_0001",
        "model": "claude-sonnet-4-20250514",
        "content": [
            {
                "type": "tool_use",
                "name": "methodology_result",
                "input": {
                    "outcome": "EVIDENCE_NOT_FOUND",
                    "searched_document_ids": ["99999999-9999-4999-8999-999999999999"],
                    "searched_span_ids": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
                    "searched_scope_sha256": default_searched_scope_sha256(),
                    "factual_outputs": [],
                    "gap_finding": {
                        "code": "GOVERNED_EVIDENCE_GAP",
                        "finding": "No responsive fictional evidence was admitted.",
                    },
                },
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "stop_reason": "tool_use",
    }

    class FakeMessages:
        def create(self, **kwargs: Any) -> Any:
            sdk_calls.append(kwargs)
            return SimpleNamespace(model_dump=lambda **_kwargs: provider_payload)

    transport = module.AnthropicSDKMethodologyTransport(
        api_key="fictional-test-key",
        client_factory=lambda **_kwargs: SimpleNamespace(messages=FakeMessages()),
    )
    extractor = module.AnthropicMethodologyTaskExtractor(
        transport=transport,
        prompt_artifact=prompt,
        configured_model="claude-sonnet-4-20250514",
        max_tokens=512,
        temperature="0",
        price_catalog=module.ANTHROPIC_STANDARD_PRICE_CATALOG,
    )
    request = model_module.MethodologyTaskExtractorRequest(**extractor_request_kwargs())
    prepared = extractor.prepare(
        request,
        budget_policy_id="budget-policy-fixture-v1",
    )
    admission = model_module.MethodologyCallAdmissionProof(
        backend="hermetic",
        committed=True,
        attempt_id=ATTEMPT_ID,
        budget_reservation_id=prepared.budget_reservation_id,
        prepared_call_sha256=prepared.prepared_call_sha256,
        task_input_sha256=request.task_input_sha256,
        price_catalog_row_sha256=prepared.reservation.price_catalog_row_sha256,
        worker_fencing_token=1,
        budget_policy_id="budget-policy-fixture-v1",
    )

    dispatched = extractor.dispatch(prepared, admission=admission)

    assert len(sdk_calls) == 1
    assert dispatched.backend == "hermetic"
    reserved_wire_object = json.loads(prepared.billable_payload.decode("utf-8"))
    assert reserved_wire_object == sdk_calls[0]
    assert reserved_wire_object["system"] == raw_prompt
    assert reserved_wire_object["tools"][0]["strict"] is True
    assert reserved_wire_object["tool_choice"]["disable_parallel_tool_use"] is True
    assert not ({"retries", "batch_mode", "cache_mode"} & set(reserved_wire_object))
    provider_document = json.loads(reserved_wire_object["messages"][0]["content"][0]["text"])
    assert set(provider_document) == set(input_schema["required"])
    assert not {
        "tenant_id",
        "deal_id",
        "run_id",
        "extraction_task_id",
        "task_input_sha256",
        "methodology_version_id",
    } & set(provider_document)
    assert prepared.request == request


@pytest.mark.parametrize(
    ("stop_reason", "block_count", "payload_kind"),
    [
        ("tool_use", 2, "valid"),
        ("max_tokens", 1, "valid"),
        ("tool_use", 1, "schema_invalid"),
    ],
)
def test_sdk_transport_captures_ambiguous_truncated_or_schema_invalid_output(
    stop_reason: str,
    block_count: int,
    payload_kind: str,
) -> None:
    module = __import__(
        "idis.services.extraction.methodology_task_extractor",
        fromlist=["AnthropicSDKMethodologyTransport"],
    )
    repo_root = Path(__file__).resolve().parents[2]
    output_schema = json.loads(
        (repo_root / "schemas/extraction/methodology_question_extraction_output.json").read_text(
            encoding="utf-8"
        )
    )
    valid_payload = {
        "outcome": "EVIDENCE_NOT_FOUND",
        "searched_document_ids": ["99999999-9999-4999-8999-999999999999"],
        "searched_span_ids": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
        "searched_scope_sha256": default_searched_scope_sha256(),
        "factual_outputs": [],
        "gap_finding": {
            "code": "GOVERNED_EVIDENCE_GAP",
            "finding": "No responsive fictional evidence was admitted.",
        },
    }
    payload = valid_payload if payload_kind == "valid" else {"outcome": "EVIDENCE_FOUND"}
    block = {"type": "tool_use", "name": "methodology_result", "input": payload}
    raw = {
        "id": "req_fictional_parser_0001",
        "model": "claude-sonnet-4-20250514",
        "content": [dict(block) for _ in range(block_count)],
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "stop_reason": stop_reason,
    }

    class FakeMessages:
        def create(self, **_kwargs: Any) -> Any:
            return SimpleNamespace(model_dump=lambda **_dump_kwargs: raw)

    transport = module.AnthropicSDKMethodologyTransport(
        api_key="fictional-test-key",
        client_factory=lambda **_kwargs: SimpleNamespace(messages=FakeMessages()),
    )

    response = transport.create_message(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        temperature=0.0,
        system="governed fictional prompt\n",
        messages=[{"role": "user", "content": "fictional"}],
        tools=[
            {
                "name": "methodology_result",
                "description": "Return governed fictional methodology evidence.",
                "input_schema": output_schema,
                "strict": True,
            }
        ],
        tool_choice={
            "type": "tool",
            "name": "methodology_result",
            "disable_parallel_tool_use": True,
        },
        retries=0,
        batch_mode=False,
    )

    assert response.stop_reason == stop_reason
    assert len(response.content_blocks) == block_count
    assert (
        response.provider_response_sha256
        == hashlib.sha256(
            require_symbol("idis.models.methodology_extraction", "exact_canonical_json_bytes")(raw)
        ).hexdigest()
    )


def test_live_extract_convenience_cannot_bypass_precall_admission() -> None:
    extractor_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "AnthropicMethodologyTaskExtractor",
    )
    prompt_cls = require_symbol("idis.models.methodology_extraction", "MethodologyPromptArtifact")
    price_catalog = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "ANTHROPIC_STANDARD_PRICE_CATALOG",
    )
    request_cls = require_symbol(
        "idis.models.methodology_extraction", "MethodologyTaskExtractorRequest"
    )
    error_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "MethodologyExtractorConfigurationError",
    )

    class NeverCalledLiveTransport:
        backend = "live"

        def create_message(self, **kwargs: Any) -> Any:
            raise AssertionError("live transport must not run without committed admission")

    extractor = extractor_cls(
        transport=NeverCalledLiveTransport(),
        prompt_artifact=_prompt_artifact(
            prompt_cls,
            status="PROD",
            content="Strict methodology prompt.",
        ),
        configured_model="claude-sonnet-4-20250514",
        max_tokens=512,
        temperature="0",
        price_catalog=price_catalog,
    )

    with pytest.raises(error_cls) as raised:
        extractor.extract(request_cls(**extractor_request_kwargs()))
    assert raised.value.code == "METHODOLOGY_PRECALL_ADMISSION_REQUIRED"


def test_live_extractor_rejects_a_draft_prompt_even_if_manually_injected() -> None:
    extractor_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "AnthropicMethodologyTaskExtractor",
    )
    prompt_cls = require_symbol("idis.models.methodology_extraction", "MethodologyPromptArtifact")
    price_catalog = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "ANTHROPIC_STANDARD_PRICE_CATALOG",
    )
    error_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "MethodologyExtractorConfigurationError",
    )

    class LiveTransport:
        backend = "live"

        def create_message(self, **kwargs: Any) -> Any:
            raise AssertionError("draft prompt must block before dispatch")

    with pytest.raises(error_cls) as raised:
        extractor_cls(
            transport=LiveTransport(),
            prompt_artifact=_prompt_artifact(
                prompt_cls,
                status="DRAFT",
                content="Unpromoted draft.",
            ),
            configured_model="claude-sonnet-4-20250514",
            max_tokens=512,
            temperature="0",
            price_catalog=price_catalog,
        )
    assert raised.value.code == "METHODOLOGY_PROMPT_NOT_PROMOTED"

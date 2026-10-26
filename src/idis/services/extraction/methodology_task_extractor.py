"""Strict task-aware Anthropic methodology extraction.

The production extractor owns request serialization, response parsing, budget
proof construction, and semantic validation.  Tests replace only the transport
below it, so a hermetic run exercises all of those production contracts without
reading credentials or making a network call.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, Protocol, cast, runtime_checkable

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError as JsonSchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from idis.models.methodology_extraction import (
    AnthropicMethodologyTool,
    AnthropicMethodologyToolChoice,
    AnthropicMethodologyWireRequest,
    DispatchedMethodologyCall,
    MethodologyBudgetReservation,
    MethodologyCallAdmissionProof,
    MethodologyCallTerminalizationProof,
    MethodologyCandidateSpan,
    MethodologyFactualOutput,
    MethodologyGapFinding,
    MethodologyPromptArtifact,
    MethodologyProviderReceipt,
    MethodologyTaskExtractorRequest,
    MethodologyTaskExtractorResponse,
    PreparedMethodologyCall,
    ValidatedDispatchedMethodologyResult,
    compute_methodology_provider_receipt_sha256,
    compute_methodology_searched_scope_sha256,
    exact_canonical_json_bytes,
)
from idis.models.methodology_pricing import (
    ANTHROPIC_STANDARD_PRICE_CATALOG as _ANTHROPIC_STANDARD_PRICE_CATALOG,
)
from idis.models.methodology_pricing import (
    AnthropicStandardPrice,
    MethodologyPriceCatalogError,
    compute_anthropic_usd,
)
from idis.models.methodology_pricing import (
    resolve_anthropic_standard_price as _resolve_anthropic_standard_price,
)
from idis.services.prompts.registry import RegistryPointer

ANTHROPIC_STANDARD_PRICE_CATALOG = _ANTHROPIC_STANDARD_PRICE_CATALOG

_PROMPT_ID: Literal["METHODOLOGY_QUESTION_EXTRACTION_V1"] = "METHODOLOGY_QUESTION_EXTRACTION_V1"
_PROMPT_VERSION = "1.0.0"
_PROMPT_FILE_PATH = "prompts/methodology_question_extraction/1.0.0/prompt.md"
_PROMPT_METADATA_PATH = "prompts/methodology_question_extraction/1.0.0/metadata.json"
_PROMPT_INPUT_SCHEMA_PATH = "schemas/extraction/methodology_question_extraction_input.json"
_PROMPT_OUTPUT_SCHEMA_PATH = "schemas/extraction/methodology_question_extraction_output.json"
_CONFIRMED_NONACCEPTANCE_HTTP_STATUSES = frozenset({400, 401, 402, 403, 404, 409, 413, 422, 429})
_SDK_LOCAL_STREAMING_GUARD_PREFIX = (
    "Streaming is required for operations that may take longer than 10 minutes"
)

MethodologyRequestNotSentCode = Literal[
    "METHODOLOGY_HIDDEN_RETRY_FORBIDDEN",
    "METHODOLOGY_UNPRICED_REQUEST_MODE",
    "METHODOLOGY_CLIENT_UNAVAILABLE",
    "METHODOLOGY_SDK_LOCAL_GUARD",
]
MethodologyRequestNotSentReason = Literal[
    "HIDDEN_RETRY_FORBIDDEN",
    "UNPRICED_BATCH_OR_CACHE",
    "UNPRICED_WIRE_REQUEST",
    "CLIENT_UNAVAILABLE",
    "SDK_LOCAL_GUARD",
]
METHODOLOGY_REQUEST_NOT_SENT_PROOFS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("METHODOLOGY_HIDDEN_RETRY_FORBIDDEN", "HIDDEN_RETRY_FORBIDDEN"),
        ("METHODOLOGY_UNPRICED_REQUEST_MODE", "UNPRICED_BATCH_OR_CACHE"),
        ("METHODOLOGY_UNPRICED_REQUEST_MODE", "UNPRICED_WIRE_REQUEST"),
        ("METHODOLOGY_CLIENT_UNAVAILABLE", "CLIENT_UNAVAILABLE"),
        ("METHODOLOGY_SDK_LOCAL_GUARD", "SDK_LOCAL_GUARD"),
    }
)


class MethodologyExtractionValidationError(ValueError):
    """Stable semantic failure raised before strict completion."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class MethodologyExtractorConfigurationError(ValueError):
    """Stable fail-closed production extractor configuration error."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class MethodologyProviderRequestNotAccepted(RuntimeError):
    """Typed proof that Anthropic explicitly rejected a request before execution."""

    code = "METHODOLOGY_PROVIDER_REQUEST_NOT_ACCEPTED"
    proof_kind: Literal["anthropic-http-4xx-v1"] = "anthropic-http-4xx-v1"

    def __init__(self, *, provider_status_code: int) -> None:
        if (
            type(provider_status_code) is not int
            or provider_status_code not in _CONFIRMED_NONACCEPTANCE_HTTP_STATUSES
        ):
            raise ValueError("provider status does not prove request nonacceptance")
        self.provider_status_code = provider_status_code
        super().__init__(self.code)


class MethodologyRequestNotSent(MethodologyExtractorConfigurationError):
    """Typed proof that a methodology request was rejected before any send."""

    proof_kind: Literal["PRE_SEND_LOCAL_FAILURE"] = "PRE_SEND_LOCAL_FAILURE"

    def __init__(
        self,
        *,
        code: MethodologyRequestNotSentCode,
        reason: MethodologyRequestNotSentReason,
    ) -> None:
        if (code, reason) not in METHODOLOGY_REQUEST_NOT_SENT_PROOFS:
            raise ValueError("request-not-sent proof is outside the closed set")
        super().__init__(code, code)
        self.reason = reason
        self.args = (code,)


@runtime_checkable
class MethodologyTaskExtractor(Protocol):
    persistence_backend: str
    external_calls_enabled: bool
    provider: str

    def extract(self, request: MethodologyTaskExtractorRequest) -> MethodologyTaskExtractorResponse:
        """Extract one terminal methodology result from one admitted task."""


@runtime_checkable
class AnthropicMethodologyTransport(Protocol):
    @property
    def backend(self) -> Literal["live", "hermetic"]: ...

    def create_message(self, **kwargs: Any) -> AnthropicMethodologyTransportResponse:
        """Execute exactly one Anthropic message request."""


class AnthropicMethodologyTransportResponse(BaseModel):
    """Provider response retained below the typed domain parser."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=False)

    backend: Literal["live", "hermetic"]
    provider_request_id: Any
    observed_model: Any
    content_blocks: tuple[Any, ...]
    input_tokens: Any
    output_tokens: Any
    latency_ms: int = Field(ge=0)
    stop_reason: Any
    provider_response_sha256: str
    real_call: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_hermetic_payload(cls, value: Any) -> Any:
        """Map the old hermetic-only test seam onto the received-block contract."""

        if isinstance(value, Mapping) and "content_blocks" not in value:
            payload = value.get("output_payload")
            if isinstance(payload, dict):
                normalized = dict(value)
                normalized.pop("output_payload", None)
                normalized["content_blocks"] = (
                    {
                        "type": "tool_use",
                        "name": "methodology_result",
                        "input": payload,
                    },
                )
                return normalized
        return value

    @model_validator(mode="after")
    def honest_backend(self) -> AnthropicMethodologyTransportResponse:
        if self.backend not in {"live", "hermetic"}:
            raise ValueError("transport backend must be live or hermetic")
        expected_real_call = self.backend == "live"
        if self.real_call is not None and self.real_call is not expected_real_call:
            raise ValueError("transport real_call contradicts backend")
        object.__setattr__(self, "real_call", expected_real_call)
        if len(self.provider_response_sha256) != 64:
            raise ValueError("provider_response_sha256 must be a SHA-256 digest")
        return self


@dataclass(frozen=True, slots=True)
class Step7ExactTerminalSet:
    selected_question_ids: tuple[str, ...]
    planned_task_ids: tuple[str, ...]


def resolve_anthropic_standard_price(
    *, catalog: Mapping[str, Any], model_id: str
) -> AnthropicStandardPrice:
    try:
        return _resolve_anthropic_standard_price(catalog=catalog, model_id=model_id)
    except MethodologyPriceCatalogError as exc:
        raise MethodologyExtractorConfigurationError(
            "METHODOLOGY_MODEL_PRICE_UNAVAILABLE",
            "the exact configured model price row is unavailable or malformed",
        ) from exc


def serialize_anthropic_billable_request(*, wire_request: AnthropicMethodologyWireRequest) -> bytes:
    """Serialize the exact immutable SDK request without altering text."""

    return wire_request.exact_payload()


class AnthropicSDKMethodologyTransport:
    """One-attempt Anthropic SDK transport; construction performs no I/O."""

    backend: Literal["live", "hermetic"]

    def __init__(
        self,
        *,
        api_key: str,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not api_key.strip():
            raise MethodologyExtractorConfigurationError(
                "EXTRACTOR_UNAVAILABLE", "Anthropic API key is absent"
            )
        self._api_key = api_key
        self._client_factory = client_factory
        self.backend = "hermetic" if client_factory is not None else "live"

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory(api_key=self._api_key, max_retries=0)
        import anthropic

        return anthropic.Anthropic(api_key=self._api_key, max_retries=0)

    def create_message(self, **kwargs: Any) -> AnthropicMethodologyTransportResponse:
        retries = kwargs.pop("retries", None)
        batch_mode = kwargs.pop("batch_mode", None)
        if retries != 0:
            hidden_retry = MethodologyRequestNotSent(
                code="METHODOLOGY_HIDDEN_RETRY_FORBIDDEN",
                reason="HIDDEN_RETRY_FORBIDDEN",
            )
            raise hidden_retry from None
        if batch_mode is not False or "cache_control" in kwargs:
            unpriced_mode = MethodologyRequestNotSent(
                code="METHODOLOGY_UNPRICED_REQUEST_MODE",
                reason="UNPRICED_BATCH_OR_CACHE",
            )
            raise unpriced_mode from None
        try:
            wire_request = AnthropicMethodologyWireRequest.model_validate(kwargs)
        except ValueError:
            wire_not_sent: MethodologyRequestNotSent | None = MethodologyRequestNotSent(
                code="METHODOLOGY_UNPRICED_REQUEST_MODE",
                reason="UNPRICED_WIRE_REQUEST",
            )
        else:
            wire_not_sent = None
        if wire_not_sent is not None:
            raise wire_not_sent from None
        sdk_kwargs = wire_request.as_sdk_kwargs()
        started_ns = time.monotonic_ns()
        try:
            client = self._client()
        except Exception:
            client_not_sent: MethodologyRequestNotSent | None = MethodologyRequestNotSent(
                code="METHODOLOGY_CLIENT_UNAVAILABLE",
                reason="CLIENT_UNAVAILABLE",
            )
        else:
            client_not_sent = None
        if client_not_sent is not None:
            raise client_not_sent from None
        local_guard: MethodologyRequestNotSent | None = None
        try:
            response = client.messages.create(**sdk_kwargs)
        except ValueError as exc:
            if type(exc) is ValueError and str(exc).startswith(_SDK_LOCAL_STREAMING_GUARD_PREFIX):
                local_guard = MethodologyRequestNotSent(
                    code="METHODOLOGY_SDK_LOCAL_GUARD",
                    reason="SDK_LOCAL_GUARD",
                )
            else:
                raise
        except Exception as exc:
            import anthropic

            status_code = getattr(exc, "status_code", None)
            if (
                isinstance(exc, anthropic.APIStatusError)
                and type(status_code) is int
                and status_code in _CONFIRMED_NONACCEPTANCE_HTTP_STATUSES
            ):
                raise MethodologyProviderRequestNotAccepted(
                    provider_status_code=status_code
                ) from exc
            raise
        if local_guard is not None:
            raise local_guard from None
        elapsed_ns = max(0, time.monotonic_ns() - started_ns)
        raw = response.model_dump(mode="json")
        blocks = raw.get("content")
        captured_blocks = tuple(blocks) if isinstance(blocks, list) else ()
        response_bytes = exact_canonical_json_bytes(raw)
        usage = raw.get("usage")
        input_tokens = usage.get("input_tokens") if isinstance(usage, Mapping) else None
        output_tokens = usage.get("output_tokens") if isinstance(usage, Mapping) else None
        return AnthropicMethodologyTransportResponse(
            backend=self.backend,
            provider_request_id=raw.get("id"),
            observed_model=raw.get("model"),
            content_blocks=captured_blocks,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=(elapsed_ns + 999_999) // 1_000_000,
            stop_reason=raw.get("stop_reason"),
            provider_response_sha256=hashlib.sha256(response_bytes).hexdigest(),
        )


class AnthropicMethodologyTaskExtractor:
    """Production serializer/parser with an injected one-attempt lower transport."""

    persistence_backend = "provider"
    provider = "anthropic"

    def __init__(
        self,
        *,
        transport: AnthropicMethodologyTransport,
        prompt_artifact: MethodologyPromptArtifact,
        configured_model: str,
        max_tokens: int,
        temperature: str,
        price_catalog: Mapping[str, Any],
    ) -> None:
        if max_tokens <= 0 or temperature != "0":
            raise MethodologyExtractorConfigurationError(
                "EXTRACTOR_UNAVAILABLE", "methodology model controls are not strict"
            )
        if transport.backend == "live" and prompt_artifact.status != "PROD":
            raise MethodologyExtractorConfigurationError(
                "METHODOLOGY_PROMPT_NOT_PROMOTED",
                "live methodology extraction requires the governed PROD prompt artifact",
            )
        try:
            Draft202012Validator.check_schema(prompt_artifact.input_schema)
            Draft202012Validator.check_schema(prompt_artifact.output_schema)
        except JsonSchemaError as exc:
            raise MethodologyExtractorConfigurationError(
                "METHODOLOGY_PROMPT_SCHEMA_INVALID",
                "the governed methodology prompt schema is not valid Draft 2020-12",
            ) from exc
        self._transport = transport
        self._prompt = prompt_artifact
        self._model = configured_model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._catalog = price_catalog
        self.external_calls_enabled = transport.backend == "live"

    def prepare(
        self,
        request: MethodologyTaskExtractorRequest,
        *,
        budget_policy_id: str,
    ) -> PreparedMethodologyCall:
        """Build the complete call and reservation vector without transport I/O."""

        price = resolve_anthropic_standard_price(catalog=self._catalog, model_id=self._model)
        request_document = {
            "methodology_question_record_id": request.methodology_question_record_id,
            "methodology_question_id": request.methodology_question_id,
            "source_row_trace": request.source_trace.model_dump(mode="json"),
            "question_text": request.question_text,
            "expected_answer_schema": request.expected_answer_schema,
            "required_evidence": list(request.required_evidence),
            "candidate_spans": [span.model_dump(mode="json") for span in request.candidate_spans],
            "predicates": list(request.predicates),
            "validation_requirements": list(request.validation_requirements),
            "required_calculations": list(request.required_calculations),
        }
        try:
            Draft202012Validator(self._prompt.input_schema).validate(request_document)
        except JsonSchemaValidationError as exc:
            raise MethodologyExtractorConfigurationError(
                "METHODOLOGY_PROMPT_SCHEMA_INVALID",
                "the governed input schema rejects the minimized provider wire view",
            ) from exc
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            request_document,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    }
                ],
            }
        ]
        wire_request = AnthropicMethodologyWireRequest(
            model=self._model,
            max_tokens=self._max_tokens,
            temperature=0.0,
            system=self._prompt.content,
            messages=tuple(messages),
            tools=(
                AnthropicMethodologyTool(
                    name="methodology_result",
                    description="Return one governed methodology extraction result.",
                    input_schema=self._prompt.output_schema,
                    strict=True,
                ),
            ),
            tool_choice=AnthropicMethodologyToolChoice(
                type="tool",
                name="methodology_result",
                disable_parallel_tool_use=True,
            ),
        )
        billable_payload = serialize_anthropic_billable_request(wire_request=wire_request)
        reservation = MethodologyBudgetReservation.from_anthropic_billable_payload(
            payload=billable_payload,
            budget_policy_id=budget_policy_id,
            model_id=self._model,
            output_token_ceiling=self._max_tokens,
            context_window_limit=price.context_window_limit,
            price_catalog_version=price.catalog_version,
            price_catalog_mode=price.mode,
            price_catalog_row_sha256=price.row_sha256,
            input_usd_per_million_tokens=price.input_usd_per_million_tokens,
            output_usd_per_million_tokens=price.output_usd_per_million_tokens,
        )
        reservation_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"idis:task6:budget:{request.tenant_id}:{request.run_id}:"
                f"{request.extraction_task_id}:{request.task_input_sha256}:"
                f"{budget_policy_id}",
            )
        )
        return PreparedMethodologyCall.from_components(
            request=request,
            prompt_artifact=self._prompt,
            wire_request=wire_request,
            budget_policy_id=budget_policy_id,
            billable_payload=billable_payload,
            reservation=reservation,
            budget_reservation_id=reservation_id,
        )

    def dispatch(
        self,
        prepared: PreparedMethodologyCall,
        *,
        admission: MethodologyCallAdmissionProof | None,
    ) -> DispatchedMethodologyCall:
        """Make one provider call only after durable admission has committed."""

        if admission is None:
            raise MethodologyExtractorConfigurationError(
                "METHODOLOGY_PRECALL_ADMISSION_REQUIRED",
                "provider dispatch requires a committed admission proof",
            )
        if (
            prepared.expected_prepared_call_sha256() != prepared.prepared_call_sha256
            or admission.prepared_call_sha256 != prepared.prepared_call_sha256
            or admission.budget_reservation_id != prepared.budget_reservation_id
            or admission.budget_policy_id != prepared.budget_policy_id
            or admission.task_input_sha256 != prepared.request.task_input_sha256
            or admission.price_catalog_row_sha256 != prepared.reservation.price_catalog_row_sha256
            or (self._transport.backend == "live" and admission.backend != "postgresql")
            or (self._transport.backend == "hermetic" and admission.backend != "hermetic")
        ):
            raise MethodologyExtractorConfigurationError(
                "METHODOLOGY_PRECALL_ADMISSION_INVALID",
                "admission proof does not bind the exact prepared call",
            )
        transport_response = self._transport.create_message(**prepared.transport_kwargs())
        return DispatchedMethodologyCall.from_provider_response(
            prepared_call=prepared,
            admission=admission,
            backend=transport_response.backend,
            provider_request_id=transport_response.provider_request_id,
            observed_model=transport_response.observed_model,
            content_blocks=transport_response.content_blocks,
            input_tokens=transport_response.input_tokens,
            output_tokens=transport_response.output_tokens,
            latency_ms=transport_response.latency_ms,
            stop_reason=transport_response.stop_reason,
            provider_response_sha256=transport_response.provider_response_sha256,
        )

    def finalize(
        self,
        dispatched: DispatchedMethodologyCall,
        *,
        validated: ValidatedDispatchedMethodologyResult,
        terminalization_proof: MethodologyCallTerminalizationProof,
    ) -> MethodologyTaskExtractorResponse:
        """Reconstruct and expose a final response only after committed proof exists."""

        prepared = dispatched.prepared_call
        admission = dispatched.admission
        proof = terminalization_proof
        expected_proof_backend = "postgresql" if dispatched.backend == "live" else "hermetic"
        if (
            dispatched.expected_dispatched_call_sha256() != dispatched.dispatched_call_sha256
            or proof.expected_terminalization_proof_sha256() != proof.terminalization_proof_sha256
            or proof.backend != expected_proof_backend
            or proof.attempt_id != admission.attempt_id
            or proof.budget_reservation_id != prepared.budget_reservation_id
            or proof.prepared_call_sha256 != prepared.prepared_call_sha256
            or proof.dispatched_call_sha256 != dispatched.dispatched_call_sha256
            or validated.expected_validated_result_sha256() != validated.validated_result_sha256
            or validated.dispatched_call_sha256 != dispatched.dispatched_call_sha256
            or proof.validated_result_sha256 != validated.validated_result_sha256
            or proof.task_input_sha256 != prepared.request.task_input_sha256
            or proof.worker_fencing_token != admission.worker_fencing_token
            or proof.budget_policy_id != admission.budget_policy_id
            or admission.budget_policy_id != prepared.budget_policy_id
            or proof.price_catalog_row_sha256 != admission.price_catalog_row_sha256
            or admission.price_catalog_row_sha256 != prepared.reservation.price_catalog_row_sha256
        ):
            raise MethodologyExtractorConfigurationError(
                "METHODOLOGY_TERMINALIZATION_PROOF_INVALID",
                "terminal proof does not bind the admitted provider response",
            )
        if dispatched.backend == "live" and (
            proof.consumed_input_tokens != dispatched.input_tokens
            or proof.consumed_output_tokens != dispatched.output_tokens
            or proof.consumed_usd
            != compute_anthropic_usd(
                input_tokens=dispatched.input_tokens,
                output_tokens=dispatched.output_tokens,
                input_usd_per_million_tokens=(prepared.reservation.input_usd_per_million_tokens),
                output_usd_per_million_tokens=(prepared.reservation.output_usd_per_million_tokens),
            )
        ):
            raise MethodologyExtractorConfigurationError(
                "METHODOLOGY_TERMINALIZATION_PROOF_INVALID",
                "terminal budget usage differs from provider usage",
            )
        receipt = self._provider_receipt(
            dispatched,
            validated=validated,
            budget_terminal_state=proof.budget_terminal_state,
            budget_policy_id=proof.budget_policy_id,
        )
        if receipt.receipt_sha256 != proof.prospective_receipt_sha256:
            raise MethodologyExtractorConfigurationError(
                "METHODOLOGY_TERMINALIZATION_PROOF_INVALID",
                "terminal proof does not bind the prospective final receipt hash",
            )
        return MethodologyTaskExtractorResponse(
            outcome=validated.outcome,
            searched_document_ids=validated.searched_document_ids,
            searched_span_ids=validated.searched_span_ids,
            searched_scope_sha256=validated.searched_scope_sha256,
            factual_outputs=validated.factual_outputs,
            gap_finding=validated.gap_finding,
            provider_receipt=receipt,
        )

    def validate_dispatched(
        self,
        dispatched: DispatchedMethodologyCall,
        *,
        request: MethodologyTaskExtractorRequest,
    ) -> ValidatedDispatchedMethodologyResult:
        """Parse and source-validate provider output without claiming terminal budget state."""

        if (
            dispatched.expected_dispatched_call_sha256() != dispatched.dispatched_call_sha256
            or request != dispatched.prepared_call.request
            or request.task_input_sha256 != dispatched.admission.task_input_sha256
        ):
            raise MethodologyExtractionValidationError(
                "METHODOLOGY_SOURCE_LOCATOR_REJECTED",
                "dispatched result does not bind the admitted request",
            )
        returned_text_fields = (
            dispatched.provider_request_id,
            dispatched.observed_model,
            dispatched.stop_reason,
        )
        if (
            dispatched.backend != self._transport.backend
            or any(
                type(value) is not str or not value or value != value.strip() or "\x00" in value
                for value in returned_text_fields
            )
            or dispatched.observed_model != dispatched.prepared_call.model
        ):
            raise MethodologyExtractionValidationError(
                "METHODOLOGY_PROVIDER_PROVENANCE_INVALID",
                "transport response provenance contradicts the prepared call",
            )
        if (
            type(dispatched.input_tokens) is not int
            or dispatched.input_tokens < 0
            or dispatched.input_tokens > dispatched.prepared_call.reservation.reserved_input_tokens
            or type(dispatched.output_tokens) is not int
            or dispatched.output_tokens < 0
            or dispatched.output_tokens
            > dispatched.prepared_call.reservation.reserved_output_tokens
        ):
            raise MethodologyExtractionValidationError(
                "METHODOLOGY_PROVIDER_USAGE_INVALID",
                "transport response usage is malformed or exceeds the admitted reservation",
            )
        if dispatched.stop_reason != "tool_use":
            raise MethodologyExtractionValidationError(
                "METHODOLOGY_RESPONSE_TRUNCATED",
                "methodology transport did not terminate at the forced tool",
            )
        if len(dispatched.content_blocks) != 1:
            raise MethodologyExtractionValidationError(
                "METHODOLOGY_RESPONSE_AMBIGUOUS",
                "Anthropic returned an ambiguous methodology content-block set",
            )
        block = dispatched.content_blocks[0]
        payload = block.get("input") if isinstance(block, Mapping) else None
        if (
            not isinstance(block, Mapping)
            or block.get("type") != "tool_use"
            or block.get("name") != "methodology_result"
            or not isinstance(payload, dict)
        ):
            raise MethodologyExtractionValidationError(
                "METHODOLOGY_EMPTY_RESULT",
                "Anthropic returned no methodology_result tool payload",
            )
        try:
            Draft202012Validator(
                dispatched.prepared_call.wire_request.tools[0].input_schema
            ).validate(payload)
        except JsonSchemaValidationError as exc:
            raise MethodologyExtractionValidationError(
                "METHODOLOGY_EMPTY_RESULT",
                "Anthropic methodology_result violates the governed output schema",
            ) from exc
        payload = dict(payload)
        facts = payload.get("factual_outputs")
        if isinstance(facts, list):
            payload["factual_outputs"] = [
                dict(
                    fact,
                    methodology_question_record_id=request.methodology_question_record_id,
                )
                if isinstance(fact, dict) and "methodology_question_record_id" not in fact
                else fact
                for fact in facts
            ]
        try:
            outcome = payload["outcome"]
            if outcome not in {"EVIDENCE_FOUND", "EVIDENCE_NOT_FOUND"}:
                raise ValueError("invalid methodology outcome")
            factual_outputs = tuple(
                MethodologyFactualOutput.model_validate(item)
                for item in payload.get("factual_outputs", [])
            )
            gap_raw = payload.get("gap_finding")
            gap_finding = (
                MethodologyGapFinding.model_validate(gap_raw) if gap_raw is not None else None
            )
            validated = ValidatedDispatchedMethodologyResult.from_terminal_payload(
                dispatched_call_sha256=dispatched.dispatched_call_sha256,
                task_input_sha256=request.task_input_sha256,
                outcome=cast(Literal["EVIDENCE_FOUND", "EVIDENCE_NOT_FOUND"], outcome),
                searched_document_ids=tuple(payload["searched_document_ids"]),
                searched_span_ids=tuple(payload["searched_span_ids"]),
                searched_scope_sha256=str(payload["searched_scope_sha256"]),
                factual_outputs=factual_outputs,
                gap_finding=gap_finding,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MethodologyExtractionValidationError(
                "METHODOLOGY_EMPTY_RESULT", "provider result violates the terminal schema"
            ) from exc
        _validate_result_scope(request=request, result=validated)
        return validated

    def compute_prospective_receipt_sha256(
        self,
        dispatched: DispatchedMethodologyCall,
        *,
        validated: ValidatedDispatchedMethodologyResult,
        budget_terminal_state: Literal["CONSUMED", "NOT_BILLABLE"],
        budget_policy_id: str,
    ) -> str:
        """Return the acyclic final receipt hash before terminal proof is created."""

        return self._provider_receipt(
            dispatched,
            validated=validated,
            budget_terminal_state=budget_terminal_state,
            budget_policy_id=budget_policy_id,
        ).receipt_sha256

    def _provider_receipt(
        self,
        dispatched: DispatchedMethodologyCall,
        *,
        validated: ValidatedDispatchedMethodologyResult,
        budget_terminal_state: Literal["CONSUMED", "NOT_BILLABLE"],
        budget_policy_id: str,
    ) -> MethodologyProviderReceipt:
        prepared = dispatched.prepared_call
        admission = dispatched.admission
        live = dispatched.backend == "live"
        if (
            prepared.expected_prepared_call_sha256() != prepared.prepared_call_sha256
            or dispatched.expected_dispatched_call_sha256() != dispatched.dispatched_call_sha256
            or validated.expected_validated_result_sha256() != validated.validated_result_sha256
            or validated.dispatched_call_sha256 != dispatched.dispatched_call_sha256
            or admission.prepared_call_sha256 != prepared.prepared_call_sha256
            or admission.task_input_sha256 != prepared.request.task_input_sha256
            or admission.budget_reservation_id != prepared.budget_reservation_id
            or admission.budget_policy_id != prepared.budget_policy_id
            or budget_policy_id != prepared.budget_policy_id
            or (live and budget_terminal_state != "CONSUMED")
            or (not live and budget_terminal_state != "NOT_BILLABLE")
        ):
            raise MethodologyExtractorConfigurationError(
                "METHODOLOGY_TERMINALIZATION_PROOF_INVALID",
                "prospective receipt inputs contradict the validated dispatch",
            )
        values: dict[str, Any] = {
            "provider": "anthropic",
            "backend": dispatched.backend,
            "configured_model": prepared.model,
            "observed_model": dispatched.observed_model,
            "prompt_id": _PROMPT_ID,
            "prompt_version": prepared.prompt_artifact.version,
            "prompt_content_sha256": prepared.prompt_artifact.content_sha256,
            "prompt_input_schema_sha256": prepared.prompt_artifact.input_schema_sha256,
            "prompt_output_schema_sha256": prepared.prompt_artifact.output_schema_sha256,
            "request_payload_sha256": prepared.reservation.billable_payload_sha256,
            "provider_request_id": dispatched.provider_request_id,
            "provider_response_sha256": dispatched.provider_response_sha256,
            "real_call": live,
            "configured_max_tokens": prepared.max_tokens,
            "configured_temperature": "0",
            "configured_json_mode": (
                prepared.wire_request.tools[0].strict
                and prepared.wire_request.tool_choice.disable_parallel_tool_use
            ),
            "input_tokens": dispatched.input_tokens,
            "output_tokens": dispatched.output_tokens,
            "stop_reason": dispatched.stop_reason,
            "budget_policy_id": budget_policy_id,
            "budget_component": "anthropic",
            "budget_reservation_id": prepared.budget_reservation_id,
            "billable_payload_sha256": prepared.reservation.billable_payload_sha256,
            "billable_payload_utf8_bytes": prepared.reservation.billable_payload_utf8_bytes,
            "price_catalog_version": prepared.reservation.price_catalog_version,
            "price_catalog_row_sha256": prepared.reservation.price_catalog_row_sha256,
            "input_usd_per_million_tokens": prepared.reservation.input_usd_per_million_tokens,
            "output_usd_per_million_tokens": prepared.reservation.output_usd_per_million_tokens,
            "reserved_calls": 1 if live else 0,
            "reserved_input_tokens": prepared.reservation.reserved_input_tokens if live else 0,
            "reserved_output_tokens": prepared.reservation.reserved_output_tokens if live else 0,
            "reserved_embedding_tokens": 0,
            "reserved_usd": prepared.reservation.worst_case_usd if live else "0.00000000",
            "budget_terminal_state": budget_terminal_state,
        }
        return MethodologyProviderReceipt.model_validate(
            {
                **values,
                "receipt_sha256": compute_methodology_provider_receipt_sha256(
                    receipt_fields=values,
                    validated_result_sha256=validated.validated_result_sha256,
                ),
            }
        )

    def extract(self, request: MethodologyTaskExtractorRequest) -> MethodologyTaskExtractorResponse:
        """Hermetic-only compatibility path; live execution must use all three phases."""

        if self._transport.backend == "live":
            raise MethodologyExtractorConfigurationError(
                "METHODOLOGY_PRECALL_ADMISSION_REQUIRED",
                "live extraction must use prepare, durable admission, dispatch, and finalize",
            )
        prepared = self.prepare(request, budget_policy_id="hermetic")
        attempt_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"idis:task6:hermetic-attempt:{prepared.prepared_call_sha256}",
            )
        )
        admission = MethodologyCallAdmissionProof(
            backend="hermetic",
            committed=True,
            attempt_id=attempt_id,
            budget_reservation_id=prepared.budget_reservation_id,
            prepared_call_sha256=prepared.prepared_call_sha256,
            task_input_sha256=request.task_input_sha256,
            price_catalog_row_sha256=prepared.reservation.price_catalog_row_sha256,
            worker_fencing_token=1,
            budget_policy_id="hermetic",
        )
        dispatched = self.dispatch(prepared, admission=admission)
        validated = self.validate_dispatched(dispatched, request=request)
        prospective_receipt_sha256 = self.compute_prospective_receipt_sha256(
            dispatched,
            validated=validated,
            budget_terminal_state="NOT_BILLABLE",
            budget_policy_id="hermetic",
        )
        terminalization = MethodologyCallTerminalizationProof.from_validated_result(
            dispatched_call=dispatched,
            validated_result=validated,
            admission=admission,
            backend="hermetic",
            budget_terminal_state="NOT_BILLABLE",
            budget_policy_id="hermetic",
            prospective_receipt_sha256=prospective_receipt_sha256,
        )
        return self.finalize(
            dispatched,
            validated=validated,
            terminalization_proof=terminalization,
        )


def validate_strict_provider_receipt(receipt: MethodologyProviderReceipt) -> None:
    if receipt.backend != "live" or not receipt.real_call:
        raise MethodologyExtractionValidationError(
            "METHODOLOGY_REAL_PROVIDER_PROOF_REQUIRED",
            "strict completion requires an honestly live Anthropic receipt",
        )


def validate_methodology_response(
    *,
    request: MethodologyTaskExtractorRequest,
    response: MethodologyTaskExtractorResponse,
) -> MethodologyTaskExtractorResponse:
    _validate_result_scope(request=request, result=response)
    return response


def _validate_result_scope(
    *,
    request: MethodologyTaskExtractorRequest,
    result: MethodologyTaskExtractorResponse | ValidatedDispatchedMethodologyResult,
) -> None:
    """Reject provider-selected lineage outside the exact admitted request scope."""

    admitted_span_locators = {span.span_id: span.locator_sha256 for span in request.candidate_spans}
    admitted_spans = set(admitted_span_locators)
    admitted_source_locator_pairs = set(admitted_span_locators.items())
    admitted_documents = {span.document_id for span in request.candidate_spans}
    answer_type = str(request.expected_answer_schema.get("answer_type", "")).strip().lower()
    admitted_value_types = {
        "numeric": {"monetary", "percentage", "count", "range"},
        "boolean": {"boolean"},
        "table": {"table_row"},
        "narrative": {"text", "date"},
    }
    if answer_type not in admitted_value_types:
        raise MethodologyExtractionValidationError(
            "METHODOLOGY_ANSWER_DOMAIN_MISMATCH",
            "frozen question answer type is unsupported",
        )
    expected_spans = tuple(sorted(admitted_spans))
    expected_documents = tuple(sorted(admitted_documents))
    expected_scope_sha256 = compute_methodology_searched_scope_sha256(request.candidate_spans)
    if (
        result.searched_span_ids != expected_spans
        or result.searched_document_ids != expected_documents
        or result.searched_scope_sha256 != expected_scope_sha256
    ):
        raise MethodologyExtractionValidationError(
            "METHODOLOGY_SEARCH_SCOPE_MISMATCH",
            "provider search proof differs from the complete admitted candidate scope",
        )
    for output in result.factual_outputs:
        if output.methodology_question_record_id != request.methodology_question_record_id:
            raise MethodologyExtractionValidationError(
                "METHODOLOGY_QUESTION_LINEAGE_MISMATCH",
                "provider output replaced the durable methodology question record ID",
            )
        value_type = str(output.value.type.value)
        if value_type not in admitted_value_types[answer_type]:
            raise MethodologyExtractionValidationError(
                "METHODOLOGY_ANSWER_DOMAIN_MISMATCH",
                "provider value family differs from the frozen question answer type",
            )
        accepted_source_locator_pairs = tuple(
            zip(
                output.accepted_source_span_ids,
                output.accepted_locator_sha256s,
                strict=True,
            )
        )
        if not set(accepted_source_locator_pairs) <= admitted_source_locator_pairs:
            raise MethodologyExtractionValidationError(
                "METHODOLOGY_SOURCE_LOCATOR_REJECTED",
                "accepted fact locator is outside admitted scope",
            )


def validate_terminal_task_coverage(
    *,
    applicable_task_ids: Sequence[str],
    terminal_results_by_task_id: Mapping[str, MethodologyTaskExtractorResponse],
) -> tuple[str, ...]:
    expected = tuple(sorted(set(applicable_task_ids)))
    actual = tuple(sorted(terminal_results_by_task_id))
    if len(expected) != len(applicable_task_ids) or expected != actual:
        raise MethodologyExtractionValidationError(
            "METHODOLOGY_TASK_COVERAGE_INCOMPLETE",
            "applicable task and terminal result sets differ",
        )
    return expected


def validate_live_source_span_identity(
    *, admitted: MethodologyCandidateSpan, current: MethodologyCandidateSpan, phase: str
) -> None:
    if phase not in {"result_finalization", "evidence_materialization"}:
        raise ValueError("unknown source identity validation phase")
    if admitted != current:
        raise MethodologyExtractionValidationError(
            "METHODOLOGY_SOURCE_LOCATOR_REJECTED",
            f"source span identity changed before {phase}",
        )


def validate_step7_exact_terminal_set(
    *,
    selected_question_ids: Sequence[str],
    planned_task_question_ids: Mapping[str, str],
    attempt_task_ids: Sequence[str],
    result_task_ids: Sequence[str],
) -> Step7ExactTerminalSet:
    selected = tuple(sorted(set(selected_question_ids)))
    planned_tasks = tuple(sorted(planned_task_question_ids))
    planned_questions = tuple(sorted(set(planned_task_question_ids.values())))
    attempts = tuple(sorted(set(attempt_task_ids)))
    results = tuple(sorted(set(result_task_ids)))
    if (
        len(selected) != len(selected_question_ids)
        or len(planned_tasks) != len(planned_task_question_ids)
        or selected != planned_questions
        or planned_tasks != attempts
        or planned_tasks != results
        or len(attempts) != len(attempt_task_ids)
        or len(results) != len(result_task_ids)
    ):
        raise MethodologyExtractionValidationError(
            "METHODOLOGY_TASK_COVERAGE_INCOMPLETE",
            "selected question, planned task, attempt, and result equations differ",
        )
    return Step7ExactTerminalSet(
        selected_question_ids=selected,
        planned_task_ids=planned_tasks,
    )


def resolve_strict_methodology_prompt(*, prompts_root: Path) -> MethodologyPromptArtifact:
    """Resolve only a governed PROD pointer; Task 6 intentionally has none."""

    pointer_path = prompts_root / "registry.prod.json"
    try:
        pointer = RegistryPointer.model_validate(
            json.loads(pointer_path.read_bytes().decode("utf-8", errors="strict"))
        )
        promoted_version = pointer.prompts[_PROMPT_ID]
        if pointer.env != "prod" or promoted_version != _PROMPT_VERSION:
            raise ValueError("methodology pointer is not the exact PROD version")
    except (
        OSError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise MethodologyExtractorConfigurationError(
            "METHODOLOGY_PROMPT_NOT_PROMOTED",
            "the methodology prompt has no governed production pointer",
        ) from exc
    try:
        registry = yaml.safe_load(
            (prompts_root / "registry.yaml").read_bytes().decode("utf-8", errors="strict")
        )
        entry = registry["prompts"][_PROMPT_ID]
        expected_entry = {
            "id": _PROMPT_ID,
            "version": _PROMPT_VERSION,
            "status": "PROD",
            "file_path": _PROMPT_FILE_PATH,
            "metadata_path": _PROMPT_METADATA_PATH,
            "input_schema": _PROMPT_INPUT_SCHEMA_PATH,
            "output_schema": _PROMPT_OUTPUT_SCHEMA_PATH,
        }
        if any(entry.get(key) != value for key, value in expected_entry.items()):
            raise ValueError("registry prompt identity or path differs from the governed artifact")
        repo_root = prompts_root.parent
        prompt_path = repo_root / entry["file_path"]
        metadata_path = repo_root / entry["metadata_path"]
        input_path = repo_root / entry["input_schema"]
        output_path = repo_root / entry["output_schema"]
        metadata = json.loads(metadata_path.read_bytes().decode("utf-8", errors="strict"))
        if (
            metadata.get("prompt_id") != entry.get("id")
            or metadata.get("name") != entry.get("name")
            or metadata.get("status") != entry.get("status")
            or metadata.get("version") != entry.get("version")
            or metadata.get("input_schema_ref") != entry.get("input_schema")
            or metadata.get("output_schema_ref") != entry.get("output_schema")
        ):
            raise ValueError("metadata identity or schema references differ from the registry")
        content_bytes = prompt_path.read_bytes()
        content = content_bytes.decode("utf-8", errors="strict")
        input_schema = json.loads(input_path.read_bytes().decode("utf-8", errors="strict"))
        output_schema = json.loads(output_path.read_bytes().decode("utf-8", errors="strict"))
        computed_hashes = {
            "prompt_content_sha256": hashlib.sha256(content_bytes).hexdigest(),
            "input_schema_sha256": hashlib.sha256(
                exact_canonical_json_bytes(input_schema)
            ).hexdigest(),
            "output_schema_sha256": hashlib.sha256(
                exact_canonical_json_bytes(output_schema)
            ).hexdigest(),
        }
        if any(
            entry.get(key) != value or metadata.get(key) != value
            for key, value in computed_hashes.items()
        ):
            raise ValueError("promoted prompt or schema hash differs from the governed pins")
    except (
        OSError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
        yaml.YAMLError,
    ) as exc:
        raise MethodologyExtractorConfigurationError(
            "METHODOLOGY_PROMPT_NOT_PROMOTED",
            "the promoted methodology prompt artifact is absent or invalid",
        ) from exc
    return MethodologyPromptArtifact(
        prompt_id=_PROMPT_ID,
        version=_PROMPT_VERSION,
        status="PROD",
        content=content,
        content_sha256=computed_hashes["prompt_content_sha256"],
        input_schema=input_schema,
        input_schema_sha256=computed_hashes["input_schema_sha256"],
        output_schema=output_schema,
        output_schema_sha256=computed_hashes["output_schema_sha256"],
    )

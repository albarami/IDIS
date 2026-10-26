"""Bounded Perplexity connector that materializes citation evidence only.

Answer prose is never exposed as a primary fact. The connector retains only
canonical cited sources and explicitly citation-linked support-only synthesis.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import httpx

from idis.services.enrichment.models import (
    CachePolicyConfig,
    EnrichmentContext,
    EnrichmentProvenance,
    EnrichmentRequest,
    EnrichmentResult,
    EnrichmentStatus,
    RightsClass,
)
from idis.services.enrichment.task9.bounded_probe_transport import (
    BoundedProbeResponse,
    BoundedProbeTransport,
    ProbeDeadline,
    ProbeTransportFailure,
)
from idis.services.enrichment.task9.perplexity_evidence import (
    PerplexityEvidenceBundle,
    PerplexityEvidenceError,
    PerplexityRetrievalProvenance,
    SynthesisUnitInput,
    build_perplexity_evidence,
    canonicalize_citation_url,
)
from idis.services.enrichment.task9.probe_contracts import PROBE_CONTRACTS
from idis.services.enrichment.task9.probe_runner import (
    ProbeConnectorError,
    ProbeConnectorExecution,
    ProbeHttpRequest,
    ProbeOperationEvidence,
    ProbeSchemaResult,
)

PERPLEXITY_PROVIDER_ID: Final = "perplexity"
PERPLEXITY_MODEL: Final = "sonar"
PERPLEXITY_CREDENTIAL_KEY: Final = "api_key"
PERPLEXITY_MAX_COMPANY_NAME_CHARS: Final = 160
PERPLEXITY_MAX_MARKET_SELECTOR_CHARS: Final = 160
PERPLEXITY_CACHE_TTL_SECONDS: Final = 0

_PROBE_COMPANY_NAME: Final = "Microsoft Corporation"
_PROBE_MARKET_SELECTOR: Final = "software"
_CONTRACT = PROBE_CONTRACTS[PERPLEXITY_PROVIDER_ID]
_OPERATION = _CONTRACT.operations[0]
_CITATION_MARKER_RE = re.compile(r"\[(\d+)\]")
_POST_PUNCTUATION_MARKERS_RE = re.compile(r"([.!?])((?:\[\d+\])+)(?=\s|$)")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _version_hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


PERPLEXITY_REQUEST_BUILDER_VERSION_HASH: Final = _version_hash(
    "perplexity-request-builder-v2|sonar|temperature=0|verified-company|market-selector|"
    "one-public-company-fact-with-citations"
)
PERPLEXITY_NORMALIZER_VERSION_HASH: Final = _version_hash(
    "perplexity-citations-only-normalizer-v1|explicit-numeric-markers"
)


class PerplexityConnectorError(RuntimeError):
    """Stable, payload-free rejection of malformed Perplexity evidence."""

    code = "ENRICHMENT_PROVENANCE_MISSING"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"{self.code}:{reason}")


@dataclass(frozen=True, slots=True)
class PerplexitySelector:
    """The complete conjunctive selector required by provider admission."""

    verified_company: str
    market_selector: str

    def __post_init__(self) -> None:
        _validate_company_name(self.verified_company)
        _validate_market_selector(self.market_selector)


class PerplexityConnector:
    """One-attempt Perplexity connector with citations-only evidence output."""

    def __init__(
        self,
        http_client: httpx.Client | None = None,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._http_client = http_client
        self._now = now

    @property
    def provider_id(self) -> str:
        return PERPLEXITY_PROVIDER_ID

    @property
    def rights_class(self) -> RightsClass:
        return RightsClass.RED

    @property
    def cache_policy(self) -> CachePolicyConfig:
        return CachePolicyConfig(ttl_seconds=PERPLEXITY_CACHE_TTL_SECONDS, no_store=True)

    @property
    def request_builder_version_hash(self) -> str:
        return PERPLEXITY_REQUEST_BUILDER_VERSION_HASH

    @property
    def normalizer_version_hash(self) -> str:
        return PERPLEXITY_NORMALIZER_VERSION_HASH

    def build_probe_request(self, *, api_key: str) -> ProbeHttpRequest:
        """Build the contract-pinned public-company probe through the runtime builder."""

        prepared = self.build_request(
            verified_company=_PROBE_COMPANY_NAME,
            market_selector=_PROBE_MARKET_SELECTOR,
            api_key=api_key,
        )
        if prepared.content != _OPERATION.body:
            raise PerplexityConnectorError("probe_request_contract_drift")
        return prepared

    def build_request(
        self,
        *,
        verified_company: str,
        market_selector: str,
        api_key: str,
    ) -> ProbeHttpRequest:
        """Build one deterministic, bounded Sonar request."""

        company = _validate_company_name(verified_company)
        market = _validate_market_selector(market_selector)
        secret = _validate_api_key(api_key)
        prompt = (
            f"For {company} in the {market} market, provide one public company fact with citations."
        )
        content = json.dumps(
            {
                "messages": [{"content": prompt, "role": "user"}],
                "model": PERPLEXITY_MODEL,
                "temperature": 0,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return ProbeHttpRequest(
            operation_id=_OPERATION.operation_id,
            method=_OPERATION.method,
            url=_OPERATION.safe_url,
            approved_host=_OPERATION.approved_host,
            expected_media_types=_OPERATION.expected_media_types,
            content=content,
            headers=(
                ("Accept", "application/json"),
                ("Authorization", f"Bearer {secret}"),
                ("Content-Type", "application/json"),
            ),
        )

    def normalize_probe_response(
        self,
        response: BoundedProbeResponse,
        *,
        retrieved_at: datetime,
    ) -> PerplexityEvidenceBundle:
        """Normalize probe bytes through the identical runtime parser."""

        return self.normalize_response(response, retrieved_at=retrieved_at)

    def normalize_response(
        self,
        response: BoundedProbeResponse,
        *,
        retrieved_at: datetime,
    ) -> PerplexityEvidenceBundle:
        """Validate one bounded response and materialize citation evidence."""

        payload = _parse_response_payload(response)
        provider_request_id = _required_response_text(payload, "id")
        if payload.get("model") != PERPLEXITY_MODEL:
            raise PerplexityConnectorError("response_model_invalid")

        raw_citations = payload.get("citations")
        if not isinstance(raw_citations, list) or not raw_citations:
            raise PerplexityConnectorError("citations_missing")
        if any(not isinstance(item, str) for item in raw_citations):
            raise PerplexityConnectorError("citations_invalid")

        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise PerplexityConnectorError("response_choices_invalid")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise PerplexityConnectorError("response_choices_invalid")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise PerplexityConnectorError("response_choices_invalid")
        answer = message.get("content")
        if not isinstance(answer, str):
            raise PerplexityConnectorError("response_choices_invalid")

        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise PerplexityConnectorError("retrieval_time_invalid")
        provenance = PerplexityRetrievalProvenance(
            provider_request_id=provider_request_id,
            retrieved_at=retrieved_at.astimezone(UTC),
            response_sha256=response.response_sha256,
        )
        synthesis_units = _citation_linked_synthesis(answer, tuple(raw_citations))
        try:
            return build_perplexity_evidence(
                citation_urls=tuple(raw_citations),
                provenance=provenance,
                synthesis_units=synthesis_units,
            )
        except PerplexityEvidenceError as exc:
            raise PerplexityConnectorError(exc.reason) from None

    def run_probe(
        self,
        *,
        transport: BoundedProbeTransport,
        deadline: ProbeDeadline,
        credentials: Mapping[str, str],
    ) -> ProbeConnectorExecution:
        """Execute the pinned operation for the generic hermetic probe runner."""

        api_key = credentials.get(PERPLEXITY_CREDENTIAL_KEY)
        if not isinstance(api_key, str) or not api_key.strip():
            raise ProbeConnectorError("credential_missing")
        try:
            prepared = self.build_probe_request(api_key=api_key)
            response = transport.execute(
                method=prepared.method,
                url=prepared.url,
                approved_host=prepared.approved_host,
                expected_media_types=prepared.expected_media_types,
                headers=prepared.header_mapping(),
                content=prepared.content,
                deadline=deadline,
            )
            evidence = self.normalize_probe_response(response, retrieved_at=self._now())
            transport.ensure_before_deadline(deadline)
        except ProbeTransportFailure as exc:
            raise ProbeConnectorError(exc.reason.value) from None
        except PerplexityConnectorError as exc:
            raise ProbeConnectorError(exc.reason) from None

        return ProbeConnectorExecution(
            provider_id=PERPLEXITY_PROVIDER_ID,
            status=ProbeSchemaResult.HIT,
            normalized_fields=(
                ("citations", evidence.citations),
                ("supported_synthesis", evidence.synthesis_units),
            ),
            operations=(
                ProbeOperationEvidence(
                    operation_id=prepared.operation_id,
                    approved_host=prepared.approved_host,
                    http_status_class="2xx",
                    response_sha256=response.response_sha256,
                    attempt_count=1,
                ),
            ),
            response_sha256=response.response_sha256,
            request_builder_version_hash=PERPLEXITY_REQUEST_BUILDER_VERSION_HASH,
            normalizer_version_hash=PERPLEXITY_NORMALIZER_VERSION_HASH,
        )

    def fetch(
        self,
        request: EnrichmentRequest,
        ctx: EnrichmentContext,
    ) -> EnrichmentResult:
        """Reject the legacy request shape, which cannot carry both selectors."""

        return _error_result("Typed Perplexity selector is required")

    def fetch_selected(
        self,
        selector: PerplexitySelector,
        ctx: EnrichmentContext,
    ) -> EnrichmentResult:
        """Execute one bounded call bound to the complete admitted selector."""

        if not isinstance(selector, PerplexitySelector):
            return _error_result("Typed Perplexity selector is required")
        api_key = (ctx.byol_credentials or {}).get(PERPLEXITY_CREDENTIAL_KEY)
        if not isinstance(api_key, str) or not api_key.strip():
            return EnrichmentResult(
                status=EnrichmentStatus.BLOCKED_MISSING_BYOL,
                normalized={"error": "Perplexity credential is unavailable"},
            )
        if self._http_client is None:
            return _error_result("Perplexity transport is not connected")

        try:
            prepared = self.build_request(
                verified_company=selector.verified_company,
                market_selector=selector.market_selector,
                api_key=api_key,
            )
            transport = BoundedProbeTransport(self._http_client)
            deadline = transport.start_deadline()
            bounded = transport.execute(
                method=prepared.method,
                url=prepared.url,
                approved_host=prepared.approved_host,
                expected_media_types=prepared.expected_media_types,
                headers=prepared.header_mapping(),
                content=prepared.content,
                deadline=deadline,
            )
            retrieved_at = self._now()
            evidence = self.normalize_response(bounded, retrieved_at=retrieved_at)
            transport.ensure_before_deadline(deadline)
        except (PerplexityConnectorError, ProbeTransportFailure):
            return _error_result("Perplexity provider fetch failed")

        provenance = EnrichmentProvenance(
            provider_id=PERPLEXITY_PROVIDER_ID,
            source_id=PERPLEXITY_PROVIDER_ID,
            retrieved_at=retrieved_at.astimezone(UTC),
            rights_class=RightsClass.RED,
            raw_ref_hash=bounded.response_sha256,
            identifiers_used={
                "market_selector": selector.market_selector,
                "verified_company": selector.verified_company,
            },
        )
        return EnrichmentResult(
            status=EnrichmentStatus.HIT,
            normalized={
                "citation_evidence": evidence.citations,
                "supported_synthesis": evidence.synthesis_units,
            },
            provenance=provenance,
            raw=None,
        )


def _parse_response_payload(response: BoundedProbeResponse) -> dict[str, Any]:
    if (
        not 200 <= response.status_code < 300
        or response.media_type != "application/json"
        or not response.body
        or _SHA256_RE.fullmatch(response.response_sha256) is None
        or hashlib.sha256(response.body).hexdigest() != response.response_sha256
    ):
        raise PerplexityConnectorError("response_envelope_invalid")
    try:
        payload = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PerplexityConnectorError("response_body_malformed") from None
    if not isinstance(payload, dict):
        raise PerplexityConnectorError("response_body_malformed")
    return payload


def _required_response_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 256
        or any(character in value for character in "\x00\r\n\t")
    ):
        raise PerplexityConnectorError("response_request_id_invalid")
    return value


def _citation_linked_synthesis(
    answer: str,
    citation_urls: tuple[str, ...],
) -> tuple[SynthesisUnitInput, ...]:
    index_to_citation_id: dict[int, str] = {}
    for index, raw_url in enumerate(citation_urls, start=1):
        try:
            canonical_url = canonicalize_citation_url(raw_url)
        except PerplexityEvidenceError:
            continue
        url_hash = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
        index_to_citation_id[index] = f"perplexity-citation-{url_hash}"

    normalized_answer = _POST_PUNCTUATION_MARKERS_RE.sub(r"\2\1", answer.strip())
    units: list[SynthesisUnitInput] = []
    for sentence in _SENTENCE_BOUNDARY_RE.split(normalized_answer):
        stripped_sentence = sentence.strip()
        terminal_punctuation = (
            stripped_sentence[-1] if stripped_sentence[-1:] in {".", "!", "?"} else ""
        )
        marker_material = stripped_sentence[:-1] if terminal_punctuation else stripped_sentence
        trailing_markers = re.search(r"(?:\[\d+\])+$", marker_material)
        if trailing_markers is None or _CITATION_MARKER_RE.search(
            marker_material[: trailing_markers.start()]
        ):
            continue
        indexes = tuple(int(item) for item in _CITATION_MARKER_RE.findall(trailing_markers.group()))
        if not indexes or any(index not in index_to_citation_id for index in indexes):
            continue
        text = marker_material[: trailing_markers.start()].rstrip() + terminal_punctuation
        text = re.sub(r"\s+([,.;:!?])", r"\1", text).strip()
        if not text:
            continue
        units.append(
            SynthesisUnitInput(
                text=text,
                citation_ids=tuple(sorted({index_to_citation_id[index] for index in indexes})),
            )
        )
    return tuple(units)


def _validate_company_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > PERPLEXITY_MAX_COMPANY_NAME_CHARS
        or any(character in value for character in "\x00\r\n\t")
    ):
        raise PerplexityConnectorError("company_selector_invalid")
    return value


def _validate_market_selector(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > PERPLEXITY_MAX_MARKET_SELECTOR_CHARS
        or any(character in value for character in "\x00\r\n\t")
    ):
        raise PerplexityConnectorError("market_selector_invalid")
    return value


def _validate_api_key(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(character in value for character in "\x00\r\n\t")
    ):
        raise PerplexityConnectorError("credential_invalid")
    return value


def _error_result(message: str) -> EnrichmentResult:
    return EnrichmentResult(
        status=EnrichmentStatus.ERROR,
        normalized={"error": message},
    )

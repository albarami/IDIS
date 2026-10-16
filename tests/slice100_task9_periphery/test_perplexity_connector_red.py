"""RED-first contracts for the bounded, citations-only Perplexity connector."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from idis.models.sanad import SanadGrade
from idis.services.enrichment.connectors.perplexity import (
    PERPLEXITY_MAX_COMPANY_NAME_CHARS,
    PERPLEXITY_REQUEST_BUILDER_VERSION_HASH,
    PerplexityConnector,
    PerplexityConnectorError,
    PerplexitySelector,
)
from idis.services.enrichment.models import (
    EnrichmentContext,
    EnrichmentQuery,
    EnrichmentRequest,
    EnrichmentStatus,
    EntityType,
    RightsClass,
)
from idis.services.enrichment.task9.bounded_probe_transport import (
    BoundedProbeResponse,
    BoundedProbeTransport,
    ProbeTransportFailure,
    ProbeTransportFailureReason,
)
from idis.services.enrichment.task9.perplexity_evidence import (
    PerplexityCitationEvidence,
    PerplexityEvidenceBundle,
)
from idis.services.enrichment.task9.probe_contracts import (
    PROBE_CONTRACTS,
    PROBE_MAX_RESPONSE_BYTES,
    PROBE_TIMEOUT_SECONDS,
)
from idis.services.enrichment.task9.probe_runner import (
    ProbeConnectorError,
    ProbeRunner,
    ProbeRunRequest,
    ProbeSchemaResult,
    ProviderProbeConnector,
)
from idis.services.sanad.source_tiers import SourceTier, TierUsage

_NOW = datetime(2026, 7, 21, 14, 30, tzinfo=UTC)
_API_KEY = "perplexity-secret-marker-never-report"
_FIXTURE = (Path(__file__).parent / "fixtures" / "perplexity.json").read_bytes()


def _request(company_name: str = "Microsoft Corporation") -> EnrichmentRequest:
    return EnrichmentRequest(
        tenant_id="tenant-001",
        entity_type=EntityType.COMPANY,
        query=EnrichmentQuery(company_name=company_name),
    )


def _selector(
    verified_company: str = "Microsoft Corporation",
    market_selector: str = "software",
) -> PerplexitySelector:
    return PerplexitySelector(
        verified_company=verified_company,
        market_selector=market_selector,
    )


def _context(*, api_key: str | None = _API_KEY) -> EnrichmentContext:
    credentials = None if api_key is None else {"api_key": api_key}
    return EnrichmentContext(
        timeout_seconds=999,
        max_retries=19,
        request_id="local-correlation-001",
        byol_credentials=credentials,
    )


def _body(
    *,
    citations: object = ("https://public.example/source",),
    content: object = "Supported public statement [1].",
    request_id: object = "provider-request-public-001",
    model: object = "sonar",
) -> bytes:
    return json.dumps(
        {
            "choices": [{"message": {"content": content}}],
            "citations": list(citations) if isinstance(citations, tuple) else citations,
            "id": request_id,
            "model": model,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _bounded_response(body: bytes) -> BoundedProbeResponse:
    return BoundedProbeResponse(
        status_code=200,
        media_type="application/json",
        body=body,
        response_sha256=hashlib.sha256(body).hexdigest(),
        redirect_count=0,
    )


def test_fixture_flows_through_actual_builder_bounded_transport_and_normalizer() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=_FIXTURE,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = PerplexityConnector(client, now=lambda: _NOW).fetch_selected(
            _selector(), _context()
        )

    contract = PROBE_CONTRACTS["perplexity"]
    operation = contract.operations[0]
    assert len(calls) == 1
    outbound = calls[0]
    assert outbound.method == operation.method
    assert str(outbound.url) == operation.safe_url
    assert outbound.content == operation.body
    assert outbound.headers["authorization"] == f"Bearer {_API_KEY}"
    assert outbound.headers["content-type"] == "application/json"
    assert outbound.extensions["timeout"] == {
        "connect": float(PROBE_TIMEOUT_SECONDS),
        "read": float(PROBE_TIMEOUT_SECONDS),
        "write": float(PROBE_TIMEOUT_SECONDS),
        "pool": float(PROBE_TIMEOUT_SECONDS),
    }

    assert result.status is EnrichmentStatus.HIT
    assert result.raw is None
    assert result.provenance is not None
    assert result.provenance.provider_id == "perplexity"
    assert result.provenance.rights_class is RightsClass.RED
    assert result.provenance.raw_ref_hash == hashlib.sha256(_FIXTURE).hexdigest()
    citations = result.normalized["citation_evidence"]
    assert len(citations) == 1
    assert citations[0].canonical_url == "https://example.org/source"
    assert result.normalized["supported_synthesis"] == ()
    assert "answer" not in result.normalized
    assert "facts" not in result.normalized
    assert _API_KEY not in repr(result)


def test_probe_request_is_exactly_the_runtime_builder_for_the_pinned_public_query() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500))
    ) as client:
        connector = PerplexityConnector(client)
        probe_request = connector.build_probe_request(api_key=_API_KEY)
        runtime_request = connector.build_request(
            verified_company="Microsoft Corporation",
            market_selector="software",
            api_key=_API_KEY,
        )

    operation = PROBE_CONTRACTS["perplexity"].operations[0]
    assert probe_request == runtime_request
    assert probe_request.method == operation.method
    assert probe_request.url == operation.safe_url
    assert probe_request.content == operation.body
    assert probe_request.approved_host == operation.approved_host
    assert probe_request.expected_media_types == operation.expected_media_types
    # The shared runner owns the request type; connector identity remains separate.
    assert connector.request_builder_version_hash == PERPLEXITY_REQUEST_BUILDER_VERSION_HASH
    assert json.loads(probe_request.content) == {
        "messages": [
            {
                "content": (
                    "For Microsoft Corporation in the software market, provide one public "
                    "company fact with citations."
                ),
                "role": "user",
            }
        ],
        "model": "sonar",
        "temperature": 0,
    }
    assert _API_KEY not in repr(probe_request)


def test_direct_probe_fails_when_normalization_exhausts_shared_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deadline_checks = 0

    def reject_expired_normalization(
        _transport: BoundedProbeTransport,
        _deadline: object,
    ) -> None:
        nonlocal deadline_checks
        deadline_checks += 1
        raise ProbeTransportFailure(ProbeTransportFailureReason.TIMEOUT)

    monkeypatch.setattr(
        BoundedProbeTransport,
        "ensure_before_deadline",
        reject_expired_normalization,
    )
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=_FIXTURE,
            )
        )
    ) as client:
        transport = BoundedProbeTransport(client)
        # Connector seams translate transport failures to the common safe probe error.
        with pytest.raises(ProbeConnectorError) as exc_info:
            PerplexityConnector(client, now=lambda: _NOW).run_probe(
                transport=transport,
                deadline=transport.start_deadline(),
                credentials={"api_key": _API_KEY},
            )

    assert exc_info.value.reason == ProbeTransportFailureReason.TIMEOUT.value
    assert deadline_checks == 1


def test_runtime_fetch_fails_when_normalization_exhausts_shared_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deadline_checks = 0

    def reject_expired_normalization(
        _transport: BoundedProbeTransport,
        _deadline: object,
    ) -> None:
        nonlocal deadline_checks
        deadline_checks += 1
        raise ProbeTransportFailure(ProbeTransportFailureReason.TIMEOUT)

    monkeypatch.setattr(
        BoundedProbeTransport,
        "ensure_before_deadline",
        reject_expired_normalization,
    )
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=_FIXTURE,
            )
        )
    ) as client:
        result = PerplexityConnector(client, now=lambda: _NOW).fetch_selected(
            _selector(), _context()
        )

    assert result.status is EnrichmentStatus.ERROR
    assert result.normalized == {"error": "Perplexity provider fetch failed"}
    assert deadline_checks == 1


def test_fetch_and_probe_delegate_to_the_same_response_normalizer() -> None:
    calls: list[bytes] = []

    class RecordingConnector(PerplexityConnector):
        def normalize_response(
            self,
            response: BoundedProbeResponse,
            *,
            retrieved_at: datetime,
        ) -> PerplexityEvidenceBundle:
            calls.append(response.body)
            return super().normalize_response(response, retrieved_at=retrieved_at)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=_FIXTURE,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = RecordingConnector(client, now=lambda: _NOW)
        fetch_result = connector.fetch_selected(_selector(), _context())
        probe_bundle = connector.normalize_probe_response(
            _bounded_response(_FIXTURE), retrieved_at=_NOW
        )

    assert calls == [_FIXTURE, _FIXTURE]
    assert tuple(fetch_result.normalized["citation_evidence"]) == probe_bundle.citations
    assert tuple(fetch_result.normalized["supported_synthesis"]) == probe_bundle.synthesis_units


def test_connector_conforms_to_generic_runner_and_probe_uses_actual_bounded_path() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=_FIXTURE,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        transport = BoundedProbeTransport(client)
        connector = PerplexityConnector(client, now=lambda: _NOW)
        assert isinstance(connector, ProviderProbeConnector)
        execution = connector.run_probe(
            transport=transport,
            deadline=transport.start_deadline(),
            credentials={"api_key": _API_KEY},
        )

    assert len(calls) == 1
    assert execution.provider_id == "perplexity"
    assert execution.status is ProbeSchemaResult.HIT
    normalized = dict(execution.normalized_fields)
    citations = normalized["citations"]
    assert isinstance(citations, tuple)
    assert len(citations) == 1
    assert isinstance(citations[0], PerplexityCitationEvidence)
    assert citations[0].canonical_url == "https://example.org/source"
    assert normalized["supported_synthesis"] == ()
    assert tuple(item.operation_id for item in execution.operations) == ("probe",)
    assert execution.operations[0].approved_host == "api.perplexity.ai"
    assert execution.operations[0].http_status_class == "2xx"
    assert execution.operations[0].attempt_count == 1
    assert execution.response_sha256 == hashlib.sha256(_FIXTURE).hexdigest()
    assert execution.request_builder_version_hash == PERPLEXITY_REQUEST_BUILDER_VERSION_HASH
    assert execution.normalizer_version_hash == connector.normalizer_version_hash


def test_fixture_reaches_connector_through_the_generic_probe_runner() -> None:
    calls: list[httpx.Request] = []
    clock = iter((_NOW, _NOW + timedelta(milliseconds=1)))

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=_FIXTURE,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = PerplexityConnector(client, now=lambda: _NOW)
        runner = ProbeRunner(
            transport=BoundedProbeTransport(client),
            connectors={"perplexity": connector},
            now=lambda: next(clock),
        )
        observation = runner.run_probe(
            ProbeRunRequest(
                tenant_id="tenant-hermetic-001",
                provider_id="perplexity",
                safe_request_id="safe-request-perplexity-001",
                rights_approval_id="rights-perplexity-001",
                credential_version_id="credential-perplexity-001",
            ),
            credentials={"api_key": _API_KEY},
        )

    assert len(calls) == 1
    assert observation.execution_mode == "HERMETIC_TEST"
    assert observation.acceptance_eligible is False
    assert observation.receipt.provider_id == "perplexity"
    assert observation.receipt.schema_result == "HIT"
    assert observation.receipt.attempt_count == 1
    assert observation.receipt.timeout_seconds == PROBE_TIMEOUT_SECONDS
    assert observation.receipt.response_sha256 == hashlib.sha256(_FIXTURE).hexdigest()
    assert dict(observation.normalized_fields)["citations"]


@pytest.mark.parametrize("credentials", [{}, {"api_key": ""}])
def test_probe_missing_credential_fails_before_transport(
    credentials: dict[str, str],
) -> None:
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        transport = BoundedProbeTransport(client)
        connector = PerplexityConnector(client)
        with pytest.raises(ProbeConnectorError) as exc_info:
            connector.run_probe(
                transport=transport,
                deadline=transport.start_deadline(),
                credentials=credentials,
            )
    assert exc_info.value.reason == "credential_missing"
    assert call_count == 0


def test_probe_citations_missing_is_a_secret_safe_preflight_failure() -> None:
    marker = "remote-content-marker-never-report"
    body = _body(citations=(), content=marker)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=body,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        transport = BoundedProbeTransport(client)
        connector = PerplexityConnector(client)
        with pytest.raises(ProbeConnectorError) as exc_info:
            connector.run_probe(
                transport=transport,
                deadline=transport.start_deadline(),
                credentials={"api_key": _API_KEY},
            )
    assert exc_info.value.code == "ENRICHMENT_PREFLIGHT_BLOCKED"
    assert marker not in str(exc_info.value)
    assert _API_KEY not in str(exc_info.value)


def test_citations_are_canonical_deduplicated_ordered_and_never_primary() -> None:
    response = _bounded_response(
        _body(
            citations=(
                "https://SECOND.example/report?b=2&a=1",
                "https://first.example:443/source",
                "https://second.example/report?a=1&b=2",
            ),
            content="First claim [2]. Second claim [1][3]. Unsupported prose.",
        )
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500))
    ) as client:
        connector = PerplexityConnector(client)
        bundle = connector.normalize_response(response, retrieved_at=_NOW)

    assert tuple(item.canonical_url for item in bundle.citations) == (
        "https://first.example/source",
        "https://second.example/report?a=1&b=2",
    )
    assert len(bundle.synthesis_units) == 2
    assert "Unsupported prose" not in repr(bundle)
    for citation in bundle.citations:
        assert citation.source_tier is SourceTier.MAQBUL
        assert citation.sanad_grade is SanadGrade.C
        assert citation.tier_usage is TierUsage.SUPPORT_ONLY
        assert citation.primary_eligible is False
    for unit in bundle.synthesis_units:
        assert unit.source_tier is SourceTier.MAQBUL
        assert unit.sanad_grade is SanadGrade.C
        assert unit.tier_usage is TierUsage.SUPPORT_ONLY
        assert unit.primary_eligible is False


def test_runtime_builder_binds_both_admitted_selectors_in_bytes_hash_and_provenance() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=_FIXTURE,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = PerplexityConnector(client, now=lambda: _NOW)
        base = connector.build_request(
            verified_company="Microsoft Corporation",
            market_selector="software",
            api_key=_API_KEY,
        )
        changed_company = connector.build_request(
            verified_company="Public Example Corporation",
            market_selector="software",
            api_key=_API_KEY,
        )
        changed_market = connector.build_request(
            verified_company="Microsoft Corporation",
            market_selector="cloud infrastructure",
            api_key=_API_KEY,
        )
        result = connector.fetch_selected(_selector(), _context())

    assert (
        len(
            {
                hashlib.sha256(item.content).hexdigest()
                for item in (
                    base,
                    changed_company,
                    changed_market,
                )
            }
        )
        == 3
    )
    body = json.loads(base.content)
    prompt = body["messages"][0]["content"]
    assert "Microsoft Corporation" in prompt
    assert "software" in prompt
    assert result.provenance is not None
    assert result.provenance.identifiers_used == {
        "market_selector": "software",
        "verified_company": "Microsoft Corporation",
    }
    assert captured[0].content == base.content


def test_legacy_fetch_without_the_compound_selector_fails_before_transport() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = PerplexityConnector(client).fetch(_request(), _context())

    assert result.status is EnrichmentStatus.ERROR
    assert result.normalized == {"error": "Typed Perplexity selector is required"}
    assert calls == 0


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not-json",
        b"{}",
        _body(citations=()),
        _body(citations=("http://unsafe.example/source",)),
        _body(citations=("https://127.0.0.1/private",)),
        _body(citations=("https://user:password@example.com/private",)),
        _body(citations=("not-a-url",)),
        _body(
            citations=(
                "https://valid.example/public-source",
                "https://valid.example/public-source#fragment",
            )
        ),
        _body(citations=(123,)),
        _body(request_id=""),
        _body(model="not-sonar"),
    ],
)
def test_empty_malformed_or_malicious_evidence_fails_closed_without_echo(body: bytes) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500))
    ) as client:
        connector = PerplexityConnector(client)
        with pytest.raises(PerplexityConnectorError) as exc_info:
            connector.normalize_response(_bounded_response(body), retrieved_at=_NOW)
    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"
    if body:
        assert body.decode(errors="ignore") not in str(exc_info.value)
    assert _API_KEY not in str(exc_info.value)


@pytest.mark.parametrize(
    "company_name",
    [
        "",
        "   ",
        "line\nbreak",
        "control\x00character",
        "x" * (PERPLEXITY_MAX_COMPANY_NAME_CHARS + 1),
    ],
)
def test_unbounded_or_control_character_selector_fails_before_transport(
    company_name: str,
) -> None:
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = PerplexityConnector(client)
        with pytest.raises(PerplexityConnectorError) as exc_info:
            connector.fetch_selected(_selector(verified_company=company_name), _context())
    assert exc_info.value.reason == "company_selector_invalid"
    assert call_count == 0
    if company_name:
        assert company_name not in str(exc_info.value)


@pytest.mark.parametrize("market_selector", ["", "   ", "line\nbreak", "x" * 161])
def test_invalid_market_selector_fails_before_transport(market_selector: str) -> None:
    with pytest.raises(PerplexityConnectorError) as exc_info:
        _selector(market_selector=market_selector)
    assert exc_info.value.reason == "market_selector_invalid"
    if market_selector:
        assert market_selector not in str(exc_info.value)


@pytest.mark.parametrize("api_key", [None, "", "   "])
def test_missing_bearer_credential_blocks_before_transport(api_key: str | None) -> None:
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = PerplexityConnector(client).fetch_selected(_selector(), _context(api_key=api_key))
    assert result.status is EnrichmentStatus.BLOCKED_MISSING_BYOL
    assert call_count == 0
    assert _API_KEY not in repr(result)


def test_fetch_without_injected_transport_fails_without_opening_a_live_client() -> None:
    connector = PerplexityConnector()
    result = connector.fetch_selected(_selector(), _context())
    assert result.status is EnrichmentStatus.ERROR
    assert result.normalized == {"error": "Perplexity transport is not connected"}


def test_transport_is_one_attempt_and_failure_is_secret_safe() -> None:
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        raise httpx.ReadTimeout(f"remote failure {_API_KEY}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = PerplexityConnector(client).fetch_selected(_selector(), _context())
    assert result.status is EnrichmentStatus.ERROR
    assert call_count == 1
    assert _API_KEY not in repr(result)


def test_declared_response_overflow_is_rejected_without_reading_or_retrying() -> None:
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "content-length": str(PROBE_MAX_RESPONSE_BYTES + 1),
            },
            content=b"{}",
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = PerplexityConnector(client).fetch_selected(_selector(), _context())
    assert result.status is EnrichmentStatus.ERROR
    assert call_count == 1


def test_supported_synthesis_is_explicitly_citation_linked_and_unsupported_is_dropped() -> None:
    response = _bounded_response(
        _body(
            citations=(
                "https://first.example/source",
                "https://second.example/source",
            ),
            content=(
                "Supported by both sources [2][1]. Unsupported answer sentence. "
                "Supported by source two [2]. Invalid reference [9]."
            ),
        )
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500))
    ) as client:
        connector = PerplexityConnector(client)
        bundle = connector.normalize_response(response, retrieved_at=_NOW)

    assert tuple(unit.text for unit in bundle.synthesis_units) == (
        "Supported by source two.",
        "Supported by both sources.",
    )
    citation_ids = {citation.citation_id for citation in bundle.citations}
    assert all(set(unit.citation_ids) <= citation_ids for unit in bundle.synthesis_units)
    assert "Unsupported answer sentence" not in repr(bundle)
    assert "Invalid reference" not in repr(bundle)


def test_post_punctuation_citation_marker_binds_only_the_preceding_factual_unit() -> None:
    response = _bounded_response(
        _body(
            citations=(
                "https://first.example/source",
                "https://second.example/source",
            ),
            content=(
                "Supported first claim.[1] Unsupported middle claim. Supported final claim.[2]"
            ),
        )
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500))
    ) as client:
        bundle = PerplexityConnector(client).normalize_response(response, retrieved_at=_NOW)

    assert {unit.text for unit in bundle.synthesis_units} == {
        "Supported first claim.",
        "Supported final claim.",
    }
    assert "Unsupported middle claim" not in repr(bundle)


def test_mixed_supported_and_unsupported_sentence_is_conservatively_dropped() -> None:
    response = _bounded_response(
        _body(content="Supported statement [1], unsupported continuation.")
    )
    bundle = PerplexityConnector().normalize_response(response, retrieved_at=_NOW)
    assert bundle.synthesis_units == ()
    assert "unsupported continuation" not in repr(bundle)

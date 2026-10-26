"""RED-first contracts for the production EPO OPS connector path."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from idis.services.enrichment.connectors.epo_open_patent import (
    EPO_NORMALIZER_IDENTITY,
    EPO_OPEN_PATENT_PROVIDER_ID,
    EPO_REQUEST_BUILDER_IDENTITY,
    EpoOpenPatentConnector,
    _normalize_oauth_response,
)
from idis.services.enrichment.models import (
    EnrichmentContext,
    EnrichmentQuery,
    EnrichmentRequest,
    EnrichmentStatus,
    EntityType,
    RightsClass,
)
from idis.services.enrichment.task9.bounded_probe_transport import BoundedProbeTransport
from idis.services.enrichment.task9.probe_contracts import PROBE_CONTRACTS
from idis.services.enrichment.task9.probe_runner import (
    ProbeConnectorError,
    ProbeSchemaResult,
)

_FIXTURES = Path(__file__).with_name("fixtures")
_OAUTH_BODY = (_FIXTURES / "epo_oauth.json").read_bytes()
_SEARCH_BODY = (_FIXTURES / "epo_search.xml").read_bytes()
_CONSUMER_KEY = "hermetic-consumer-key"
_CONSUMER_SECRET = "hermetic-consumer-secret"
_CREDENTIALS = {"consumer_key": _CONSUMER_KEY, "consumer_secret": _CONSUMER_SECRET}


class _AdvancingClock:
    def __init__(self, start: float = 99.5, increment: float = 0.5) -> None:
        self._value = start
        self._increment = increment

    def __call__(self) -> float:
        self._value += self._increment
        return self._value


def _request(company_name: str = "Microsoft") -> EnrichmentRequest:
    return EnrichmentRequest(
        tenant_id="tenant-epo-hermetic",
        entity_type=EntityType.COMPANY,
        query=EnrichmentQuery(company_name=company_name),
    )


def _context(credentials: dict[str, str] | None = None) -> EnrichmentContext:
    return EnrichmentContext(
        timeout_seconds=15.0,
        max_retries=0,
        request_id="request-epo-hermetic",
        byol_credentials=_CREDENTIALS if credentials is None else credentials,
    )


def _success_handler(
    captured: list[httpx.Request],
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path.endswith("/auth/accesstoken"):
            return httpx.Response(
                200,
                content=_OAUTH_BODY,
                headers={"content-type": "application/json"},
            )
        return httpx.Response(
            200,
            content=_SEARCH_BODY,
            headers={"content-type": "application/exchange+xml"},
        )

    return handler


def test_epo_probe_is_exact_oauth_then_search_under_one_shared_deadline() -> None:
    captured: list[httpx.Request] = []
    contract = PROBE_CONTRACTS[EPO_OPEN_PATENT_PROVIDER_ID]
    with httpx.Client(transport=httpx.MockTransport(_success_handler(captured))) as client:
        transport = BoundedProbeTransport(client, monotonic=_AdvancingClock())
        connector = EpoOpenPatentConnector(http_client=client)
        result = connector.run_probe(
            transport=transport,
            deadline=transport.start_deadline(),
            credentials=_CREDENTIALS,
        )

    assert [request.method for request in captured] == ["POST", "GET"]
    assert [str(request.url) for request in captured] == [
        contract.operations[0].safe_url,
        contract.operations[1].safe_url,
    ]
    assert captured[0].content == b"grant_type=client_credentials"
    expected_basic = base64.b64encode(f"{_CONSUMER_KEY}:{_CONSUMER_SECRET}".encode()).decode()
    assert captured[0].headers["authorization"] == f"Basic {expected_basic}"
    assert captured[1].headers["authorization"] == "Bearer hermetic-epo-token"
    assert captured[0].extensions["timeout"]["connect"] == 14.5
    # OAuth EOF and post-normalization checks both consume the shared test clock.
    assert captured[1].extensions["timeout"]["connect"] == 12.0
    assert result.status is ProbeSchemaResult.HIT
    assert dict(result.normalized_fields) == {
        "applicant": "MICROSOFT CORPORATION",
        "applicant_selector_match": True,
        "publication_country": "EP",
        "publication_doc_number": "0000001",
        "publication_kind": "A1",
        "publication_number": "EP0000001A1",
        "selector": "Microsoft",
        "title": "Public patent title",
    }
    assert tuple(evidence.operation_id for evidence in result.operations) == ("oauth", "search")
    assert all(evidence.attempt_count == 1 for evidence in result.operations)
    assert result.request_builder_version_hash == connector.request_builder_version_hash
    assert result.normalizer_version_hash == connector.normalizer_version_hash


@pytest.mark.parametrize(
    "credentials",
    [
        {},
        {"consumer_key": _CONSUMER_KEY},
        {"consumer_secret": _CONSUMER_SECRET},
        {"consumer_key": "", "consumer_secret": _CONSUMER_SECRET},
        {"consumer_key": _CONSUMER_KEY, "consumer_secret": ""},
    ],
)
def test_epo_credentials_are_one_atomic_non_empty_pair(credentials: dict[str, str]) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = EpoOpenPatentConnector(http_client=client)
        fetch_result = connector.fetch(_request(), _context(credentials))
        transport = BoundedProbeTransport(client)
        with pytest.raises(ProbeConnectorError) as exc_info:
            connector.run_probe(
                transport=transport,
                deadline=transport.start_deadline(),
                credentials=credentials,
            )

    assert fetch_result.status is EnrichmentStatus.BLOCKED_MISSING_BYOL
    assert exc_info.value.code == "ENRICHMENT_PROVIDER_INPUT_MISSING"
    assert exc_info.value.reason == "credential_bundle_incomplete"
    assert calls == 0


def test_epo_runtime_fetch_and_probe_share_builder_and_normalizer_methods() -> None:
    captured: list[httpx.Request] = []
    with httpx.Client(transport=httpx.MockTransport(_success_handler(captured))) as client:
        connector = EpoOpenPatentConnector(http_client=client)
        with (
            patch.object(
                connector,
                "build_operation_request",
                wraps=connector.build_operation_request,
            ) as builder,
            patch.object(
                connector,
                "normalize_search_response",
                wraps=connector.normalize_search_response,
            ) as normalizer,
        ):
            fetch_result = connector.fetch(_request(), _context())
            transport = BoundedProbeTransport(client)
            probe_result = connector.run_probe(
                transport=transport,
                deadline=transport.start_deadline(),
                credentials=_CREDENTIALS,
            )

    assert fetch_result.status is EnrichmentStatus.HIT
    assert probe_result.status is ProbeSchemaResult.HIT
    assert builder.call_count == 4
    assert normalizer.call_count == 2
    assert connector.request_builder_identity == EPO_REQUEST_BUILDER_IDENTITY
    assert connector.normalizer_identity == EPO_NORMALIZER_IDENTITY
    assert connector.rights_class is RightsClass.GREEN


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        (
            b'<ops:world-patent-data xmlns:ops="http://ops.epo.org" '
            b'xmlns:e="http://www.epo.org/exchange"><ops:biblio-search '
            b'total-result-count="0"><ops:query>pa=Microsoft</ops:query>'
            b'<ops:range begin="1" end="1"/><ops:search-result>'
            b"<e:exchange-documents/></ops:search-result></ops:biblio-search>"
            b"</ops:world-patent-data>",
            EnrichmentStatus.MISS,
        ),
        (
            b'<?xml version="1.0"?><ops:world-patent-data xmlns:ops="http://ops.epo.org" '
            b'xmlns:e="http://www.epo.org/exchange"></ops:world-patent-data>',
            EnrichmentStatus.ERROR,
        ),
        (b"<not-xml", EnrichmentStatus.ERROR),
        (
            b'<ops:world-patent-data xmlns:ops="http://ops.epo.org" '
            b'xmlns:e="http://www.epo.org/exchange"><e:exchange-document>'
            b"<e:invention-title>Incomplete</e:invention-title>"
            b"</e:exchange-document></ops:world-patent-data>",
            EnrichmentStatus.ERROR,
        ),
    ],
)
def test_epo_xml_has_strict_miss_and_failure_semantics(
    body: bytes,
    expected_status: EnrichmentStatus,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/accesstoken"):
            return httpx.Response(
                200,
                content=_OAUTH_BODY,
                headers={"content-type": "application/json"},
            )
        return httpx.Response(200, content=body, headers={"content-type": "application/xml"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = EpoOpenPatentConnector(http_client=client).fetch(_request(), _context())
    assert result.status is expected_status


@pytest.mark.parametrize(
    ("old", "new", "reason"),
    [
        (b"pa=Microsoft", b"pa=Unrelated", "search_selector_mismatch"),
        (b"MICROSOFT CORPORATION", b"UNRELATED HOLDINGS", "search_selector_mismatch"),
        (b'total-result-count="42"', b'total-result-count="0"', "search_result_count_invalid"),
        (
            b"<exchange:country>EP</exchange:country>",
            b"<exchange:country></exchange:country>",
            "search_publication_identifier_invalid",
        ),
        (
            b"<exchange:kind>A1</exchange:kind>",
            b"<exchange:kind></exchange:kind>",
            "search_publication_identifier_invalid",
        ),
    ],
)
def test_epo_response_is_bound_to_selector_count_and_global_docdb_identifier(
    old: bytes,
    new: bytes,
    reason: str,
) -> None:
    body = _SEARCH_BODY.replace(old, new)
    connector = EpoOpenPatentConnector()

    with pytest.raises(ProbeConnectorError) as exc_info:
        connector.normalize_search_response(body, selector="Microsoft")

    assert exc_info.value.reason == reason


def test_epo_root_only_payload_is_not_a_miss_and_bare_doc_number_is_never_global() -> None:
    root_only = (
        b'<ops:world-patent-data xmlns:ops="http://ops.epo.org" '
        b'xmlns:exchange="http://www.epo.org/exchange">'
        b"<exchange:exchange-document><exchange:publication-reference>"
        b"<exchange:doc-number>EP0000001</exchange:doc-number>"
        b"</exchange:publication-reference></exchange:exchange-document>"
        b"</ops:world-patent-data>"
    )

    with pytest.raises(ProbeConnectorError) as exc_info:
        EpoOpenPatentConnector().normalize_search_response(root_only, selector="Microsoft")

    assert exc_info.value.reason == "search_xml_schema_invalid"


def test_epo_applicant_binding_uses_token_sequence_not_arbitrary_substring() -> None:
    body = _SEARCH_BODY.replace(b"pa=Microsoft", b"pa=soft")

    with pytest.raises(ProbeConnectorError) as exc_info:
        EpoOpenPatentConnector().normalize_search_response(body, selector="soft")

    assert exc_info.value.reason == "search_selector_mismatch"


def test_epo_official_like_oauth_allows_empty_scope_list_and_bounded_extra_metadata() -> None:
    payload = json.loads(_OAUTH_BODY)
    assert payload["scope"] == ""
    assert payload["api_product_list"] == ["ops-v3.2"]
    assert payload["refresh_token_expires_in"] == "0"

    assert _normalize_oauth_response(_OAUTH_BODY) == "hermetic-epo-token"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({f"extra_{index}": index for index in range(40)}),
        lambda payload: payload.update({"extra": [[[["too-deep"]]]]}),
        lambda payload: payload.update({"extra": "x" * 70_000}),
        lambda payload: payload.update({"access_token": " hermetic-epo-token"}),
        lambda payload: payload.update({"access_token": "hermetic\u0000epo-token"}),
    ],
)
def test_epo_oauth_rejects_unbounded_nested_or_unclean_values_without_echo(
    mutate: Callable[[dict[str, object]], None],
) -> None:
    payload = json.loads(_OAUTH_BODY)
    mutate(payload)

    with pytest.raises(ProbeConnectorError) as exc_info:
        _normalize_oauth_response(json.dumps(payload).encode())

    assert exc_info.value.reason == "oauth_response_schema_invalid"
    assert "hermetic-epo-token" not in str(exc_info.value)


def test_epo_basic_bearer_and_remote_error_text_never_enter_safe_errors() -> None:
    marker = "credential-marker-never-report"
    responses: Iterator[httpx.Response] = iter(
        (
            httpx.Response(
                200,
                content=_OAUTH_BODY.replace(b"hermetic-epo-token", marker.encode()),
                headers={"content-type": "application/json"},
            ),
            httpx.Response(
                500,
                content=f"{marker}:{_CONSUMER_KEY}:{_CONSUMER_SECRET}".encode(),
                headers={"content-type": "text/plain"},
            ),
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = EpoOpenPatentConnector(http_client=client)
        transport = BoundedProbeTransport(client)
        with pytest.raises(ProbeConnectorError) as exc_info:
            connector.run_probe(
                transport=transport,
                deadline=transport.start_deadline(),
                credentials=_CREDENTIALS,
            )

    rendered = str(exc_info.value)
    assert marker not in rendered
    assert _CONSUMER_KEY not in rendered
    assert _CONSUMER_SECRET not in rendered

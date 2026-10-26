"""RED-first contracts for the production Semantic Scholar connector path."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from idis.services.enrichment.connectors.semantic_scholar import (
    SEMANTIC_SCHOLAR_NORMALIZER_IDENTITY,
    SEMANTIC_SCHOLAR_PROVIDER_ID,
    SEMANTIC_SCHOLAR_REQUEST_BUILDER_IDENTITY,
    SemanticScholarAuthorSelector,
    SemanticScholarConnector,
    SemanticScholarOrganizationSelector,
    SemanticScholarPaperSelector,
    SemanticScholarSelector,
    SemanticScholarTopicSelector,
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

_FIXTURE = (Path(__file__).with_name("fixtures") / "semantic_scholar.json").read_bytes()
_FIXTURE_DIR = Path(__file__).with_name("fixtures")
_API_KEY = "hermetic-semantic-key"
_CREDENTIALS = {"api_key": _API_KEY}


def _request(paper_id: str = "CorpusId:215416146") -> EnrichmentRequest:
    return EnrichmentRequest(
        tenant_id="tenant-semantic-hermetic",
        entity_type=EntityType.COMPANY,
        query=EnrichmentQuery(company_name=paper_id),
    )


def _context(credentials: dict[str, str] | None = None) -> EnrichmentContext:
    return EnrichmentContext(
        timeout_seconds=15.0,
        max_retries=0,
        request_id="request-semantic-hermetic",
        byol_credentials=_CREDENTIALS if credentials is None else credentials,
    )


def _json_handler(
    body: bytes,
    captured: list[httpx.Request],
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "application/json"},
        )

    return handler


def test_semantic_probe_uses_exact_url_fields_and_api_key_header_with_attribution() -> None:
    captured: list[httpx.Request] = []
    contract = PROBE_CONTRACTS[SEMANTIC_SCHOLAR_PROVIDER_ID]
    with httpx.Client(transport=httpx.MockTransport(_json_handler(_FIXTURE, captured))) as client:
        transport = BoundedProbeTransport(client)
        connector = SemanticScholarConnector(http_client=client)
        result = connector.run_probe(
            transport=transport,
            deadline=transport.start_deadline(),
            credentials=_CREDENTIALS,
        )

    assert len(captured) == 1
    assert captured[0].method == "GET"
    assert str(captured[0].url) == contract.operations[0].safe_url
    assert captured[0].headers["x-api-key"] == _API_KEY
    assert captured[0].headers["accept"] == "application/json"
    normalized = dict(result.normalized_fields)
    assert result.status is ProbeSchemaResult.HIT
    assert normalized["paper_id"] == "0123456789abcdef0123456789abcdef01234567"
    assert normalized["title"] == "Public scholarly paper"
    assert normalized["authors"] == (("1", "Public Author"),)
    assert normalized["external_ids"] == (
        ("CorpusId", 215416146),
        ("DOI", "10.0000/example"),
    )
    assert normalized["selector_type"] == "paper"
    assert normalized["attribution_text"] == "Data from Semantic Scholar"
    assert normalized["attribution_required"] is True
    assert result.operations[0].attempt_count == 1


@pytest.mark.parametrize("credentials", [{}, {"api_key": ""}])
def test_semantic_api_key_is_required_before_transport(credentials: dict[str, str]) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = SemanticScholarConnector(http_client=client)
        fetch_result = connector.fetch(_request(), _context(credentials))
        transport = BoundedProbeTransport(client)
        with pytest.raises(ProbeConnectorError) as exc_info:
            connector.run_probe(
                transport=transport,
                deadline=transport.start_deadline(),
                credentials=credentials,
            )

    assert fetch_result.status is EnrichmentStatus.ERROR
    assert exc_info.value.code == "ENRICHMENT_PROVIDER_INPUT_MISSING"
    assert exc_info.value.reason == "credential_bundle_incomplete"
    assert calls == 0


def test_semantic_legacy_fetch_refuses_to_guess_a_paper_id_from_company_name() -> None:
    captured: list[httpx.Request] = []
    with httpx.Client(transport=httpx.MockTransport(_json_handler(_FIXTURE, captured))) as client:
        connector = SemanticScholarConnector(http_client=client)
        fetch_result = connector.fetch(_request("Public Company Name"), _context())

    assert fetch_result.status is EnrichmentStatus.ERROR
    assert fetch_result.normalized == {"error": "typed Semantic Scholar selector is required"}
    assert captured == []
    assert connector.request_builder_identity == SEMANTIC_SCHOLAR_REQUEST_BUILDER_IDENTITY
    assert connector.normalizer_identity == SEMANTIC_SCHOLAR_NORMALIZER_IDENTITY
    assert connector.rights_class is RightsClass.YELLOW


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        (
            b'{"authors":[],"externalIds":{},"paperId":null,"title":null,"year":null}',
            EnrichmentStatus.ERROR,
        ),
        (b"{malformed", EnrichmentStatus.ERROR),
        (
            b'{"authors":[],"externalIds":{"DOI":"10.1/x"},'
            b'"paperId":"paper-1","title":"Title","year":2024}',
            EnrichmentStatus.ERROR,
        ),
        (
            b'{"authors":[{"authorId":"","name":"Name"}],"externalIds":{"DOI":"10.1/x"},'
            b'"paperId":"paper-1","title":"Title","year":2024}',
            EnrichmentStatus.ERROR,
        ),
        (
            b'{"authors":[{"authorId":"1","name":"Name"}],'
            b'"externalIds":{"CorpusId":"215416146"},'
            b'"paperId":"0123456789abcdef0123456789abcdef01234567",'
            b'"title":"Title","year":2024}',
            EnrichmentStatus.ERROR,
        ),
    ],
)
def test_semantic_json_has_strict_hit_miss_and_failure_semantics(
    body: bytes,
    expected_status: EnrichmentStatus,
) -> None:
    captured: list[httpx.Request] = []
    with httpx.Client(transport=httpx.MockTransport(_json_handler(body, captured))) as client:
        result = SemanticScholarConnector(http_client=client).fetch_selected(
            SemanticScholarPaperSelector("CorpusId:215416146"),
            _context(),
        )
    assert result.status is expected_status


_AUTHOR_PAPERS = (
    b'{"data":[{"authors":[{"authorId":"42","name":"Public Author"}],'
    b'"externalIds":{"CorpusId":215416146},'
    b'"paperId":"0123456789abcdef0123456789abcdef01234567",'
    b'"title":"Public author paper","year":2024}],"next":null,"offset":0}'
)
_TOPIC_SEARCH = (
    b'{"data":[{"authors":[{"authorId":"42","name":"Public Author"}],'
    b'"externalIds":{"CorpusId":215416146},'
    b'"paperId":"0123456789abcdef0123456789abcdef01234567",'
    b'"s2FieldsOfStudy":[{"category":"Computer Science","source":"s2-fos-model"}],'
    b'"title":"Public topic paper","year":2024}],"next":null,"offset":0,"total":1}'
)
_AUTHOR_DETAIL = (
    b'{"affiliations":["Public Research Organization"],"authorId":"42",'
    b'"name":"Public Author","normalizedAffiliations":['
    b'{"rorDisplayName":"Public Research Organization",'
    b'"rorId":"https://ror.org/03yrm5c26"}]}'
)


@pytest.mark.parametrize(
    ("selector", "expected_urls", "response_bodies", "expected_selector_type"),
    [
        (
            SemanticScholarPaperSelector("CorpusId:215416146"),
            (
                "https://api.semanticscholar.org/graph/v1/paper/CorpusId:215416146"
                "?fields=paperId,title,year,authors,externalIds",
            ),
            (_FIXTURE,),
            "paper",
        ),
        (
            SemanticScholarAuthorSelector("42"),
            (
                "https://api.semanticscholar.org/graph/v1/author/42/papers"
                "?offset=0&limit=1&fields=paperId,title,year,authors,externalIds",
            ),
            (_AUTHOR_PAPERS,),
            "author",
        ),
        (
            SemanticScholarTopicSelector("public systems", "Computer Science"),
            (
                "https://api.semanticscholar.org/graph/v1/paper/search"
                "?query=public%20systems&offset=0&limit=1"
                "&fields=paperId,title,year,authors,externalIds,s2FieldsOfStudy"
                "&fieldsOfStudy=Computer%20Science",
            ),
            (_TOPIC_SEARCH,),
            "topic",
        ),
        (
            SemanticScholarOrganizationSelector("https://ror.org/03yrm5c26", "42"),
            (
                "https://api.semanticscholar.org/graph/v1/author/42"
                "?fields=name,affiliations,normalizedAffiliations",
                "https://api.semanticscholar.org/graph/v1/author/42/papers"
                "?offset=0&limit=1&fields=paperId,title,year,authors,externalIds",
            ),
            (_AUTHOR_DETAIL, _AUTHOR_PAPERS),
            "organization",
        ),
    ],
)
def test_semantic_typed_dispatcher_serves_every_catalog_selector_branch(
    selector: SemanticScholarSelector,
    expected_urls: tuple[str, ...],
    response_bodies: tuple[bytes, ...],
    expected_selector_type: str,
) -> None:
    captured: list[httpx.Request] = []
    responses = iter(response_bodies)

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            content=next(responses),
            headers={"content-type": "application/json"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = SemanticScholarConnector(http_client=client)
        with (
            patch.object(
                connector,
                "build_operation_requests",
                wraps=connector.build_operation_requests,
            ) as builder,
            patch.object(
                connector,
                "normalize_selected_responses",
                wraps=connector.normalize_selected_responses,
            ) as normalizer,
        ):
            result = connector.fetch_selected(selector, _context())

    assert result.status is EnrichmentStatus.HIT
    assert result.normalized["selector_type"] == expected_selector_type
    assert tuple(str(request.url) for request in captured) == expected_urls
    assert builder.call_count == 1
    assert normalizer.call_count == 1


@pytest.mark.parametrize(
    ("selector", "bodies", "reason"),
    [
        (
            SemanticScholarPaperSelector("CorpusId:999"),
            (_FIXTURE,),
            "selector_response_mismatch",
        ),
        (
            SemanticScholarAuthorSelector("999"),
            (_AUTHOR_PAPERS,),
            "selector_response_mismatch",
        ),
        (
            SemanticScholarTopicSelector("public systems", "Medicine"),
            (_TOPIC_SEARCH,),
            "selector_response_mismatch",
        ),
        (
            SemanticScholarOrganizationSelector("https://ror.org/00wrong00", "42"),
            (_AUTHOR_DETAIL, _AUTHOR_PAPERS),
            "selector_response_mismatch",
        ),
    ],
)
def test_semantic_response_must_bind_exactly_to_the_typed_selector(
    selector: SemanticScholarSelector,
    bodies: tuple[bytes, ...],
    reason: str,
) -> None:
    connector = SemanticScholarConnector()
    with pytest.raises(ProbeConnectorError) as exc_info:
        connector.normalize_selected_responses(selector, bodies)
    assert exc_info.value.reason == reason


@pytest.mark.parametrize(
    ("selector", "bodies"),
    [
        (
            SemanticScholarAuthorSelector("42"),
            (b'{"data":[],"next":null,"offset":0}',),
        ),
        (
            SemanticScholarTopicSelector("public systems", "Computer Science"),
            (b'{"data":[],"next":null,"offset":0,"total":0}',),
        ),
        (
            SemanticScholarOrganizationSelector("https://ror.org/03yrm5c26", "42"),
            (_AUTHOR_DETAIL, b'{"data":[],"next":null,"offset":0}'),
        ),
    ],
)
def test_semantic_only_validated_empty_list_or_search_envelopes_can_be_miss(
    selector: SemanticScholarSelector,
    bodies: tuple[bytes, ...],
) -> None:
    status, fields = SemanticScholarConnector().normalize_selected_responses(selector, bodies)
    assert status is ProbeSchemaResult.MISS
    assert fields == ()


@pytest.mark.parametrize(
    ("selector", "fixture_name", "expected_status"),
    [
        (
            SemanticScholarAuthorSelector("42"),
            "semantic_scholar_author_terminal.json",
            ProbeSchemaResult.HIT,
        ),
        (
            SemanticScholarAuthorSelector("42"),
            "semantic_scholar_author_zero.json",
            ProbeSchemaResult.MISS,
        ),
        (
            SemanticScholarTopicSelector("public systems", "Computer Science"),
            "semantic_scholar_topic_terminal.json",
            ProbeSchemaResult.HIT,
        ),
        (
            SemanticScholarTopicSelector("public systems", "Computer Science"),
            "semantic_scholar_topic_zero.json",
            ProbeSchemaResult.MISS,
        ),
    ],
)
def test_semantic_official_terminal_envelopes_may_omit_next(
    selector: SemanticScholarSelector,
    fixture_name: str,
    expected_status: ProbeSchemaResult,
) -> None:
    status, _fields = SemanticScholarConnector().normalize_selected_responses(
        selector,
        ((_FIXTURE_DIR / fixture_name).read_bytes(),),
    )
    assert status is expected_status


def test_semantic_official_normalized_affiliation_keys_bind_ror_selector() -> None:
    status, fields = SemanticScholarConnector().normalize_selected_responses(
        SemanticScholarOrganizationSelector("https://ror.org/03yrm5c26", "42"),
        (
            (_FIXTURE_DIR / "semantic_scholar_organization.json").read_bytes(),
            (_FIXTURE_DIR / "semantic_scholar_author_terminal.json").read_bytes(),
        ),
    )
    assert status is ProbeSchemaResult.HIT
    assert dict(fields)["selector_type"] == "organization"


@pytest.mark.parametrize(
    ("selector", "body"),
    [
        (
            SemanticScholarAuthorSelector("42"),
            b'{"data":[],"next":1,"offset":0}',
        ),
        (
            SemanticScholarAuthorSelector("42"),
            b'{"data":[],"next":false,"offset":0}',
        ),
        (
            SemanticScholarTopicSelector("public systems", "Computer Science"),
            b'{"data":[],"offset":0,"total":1}',
        ),
        (
            SemanticScholarTopicSelector("public systems", "Computer Science"),
            b'{"data":[],"next":1,"offset":0,"total":0}',
        ),
        (
            SemanticScholarTopicSelector("public systems", "Computer Science"),
            _TOPIC_SEARCH.replace(b'"next":null', b'"next":1'),
        ),
        (
            SemanticScholarTopicSelector("public systems", "Computer Science"),
            _TOPIC_SEARCH.replace(b'"next":null', b'"next":null').replace(
                b'"total":1', b'"total":2'
            ),
        ),
    ],
)
def test_semantic_optional_next_does_not_admit_contradictory_envelopes(
    selector: SemanticScholarSelector,
    body: bytes,
) -> None:
    with pytest.raises(ProbeConnectorError) as exc_info:
        SemanticScholarConnector().normalize_selected_responses(selector, (body,))
    assert exc_info.value.reason == "response_json_schema_invalid"


@pytest.mark.parametrize(
    ("selector", "body"),
    [
        (
            SemanticScholarAuthorSelector("42"),
            b'{"data":[],"offset":0.0}',
        ),
        (
            SemanticScholarTopicSelector("public systems", "Computer Science"),
            b'{"data":[],"offset":0.0,"total":0}',
        ),
    ],
)
def test_semantic_list_offsets_must_be_exact_integers(
    selector: SemanticScholarSelector,
    body: bytes,
) -> None:
    with pytest.raises(ProbeConnectorError) as exc_info:
        SemanticScholarConnector().normalize_selected_responses(selector, (body,))
    assert exc_info.value.reason == "response_json_schema_invalid"


def test_semantic_identifier_detail_404_is_failure_not_miss() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = SemanticScholarConnector(http_client=client).fetch_selected(
            SemanticScholarPaperSelector("CorpusId:215416146"),
            _context(),
        )
    assert result.status is EnrichmentStatus.ERROR


def test_semantic_post_normalization_deadline_is_enforced() -> None:
    clock = iter((0.0, 1.0, 2.0, 3.0, 4.0, 16.0))
    with httpx.Client(transport=httpx.MockTransport(_json_handler(_FIXTURE, []))) as client:
        connector = SemanticScholarConnector(http_client=client)
        transport = BoundedProbeTransport(client, monotonic=lambda: next(clock))
        with pytest.raises(ProbeConnectorError) as exc_info:
            connector.run_probe(
                transport=transport,
                deadline=transport.start_deadline(),
                credentials=_CREDENTIALS,
            )
    assert exc_info.value.reason == "transport_timeout"


@pytest.mark.parametrize(
    "paper_id",
    [
        "paper-public-001",
        "0" * 39,
        "CorpusId:0",
        "CorpusId:-1",
        "corpusid:1",
        "Unknown:value",
        "DBLP:conf/example",
        "DOI:",
        " DOI:10.0000/example",
        "DOI:10.0000/exam\nple",
        f"DOI:{'x' * 513}",
    ],
)
def test_semantic_paper_selector_rejects_unsupported_unclean_or_unbounded_ids(
    paper_id: str,
) -> None:
    with pytest.raises(ProbeConnectorError) as exc_info:
        SemanticScholarPaperSelector(paper_id)
    assert exc_info.value.code == "ENRICHMENT_PROVIDER_INPUT_MISSING"
    assert exc_info.value.reason == "selector_invalid"


@pytest.mark.parametrize(
    "paper_id",
    [
        "0" * 40,
        "CorpusId:1",
        "DOI:10.0000/example",
        "ARXIV:2401.00001",
        "MAG:123456",
        "ACL:P20-1001",
        "PMID:123456",
        "PMCID:PMC123456",
    ],
)
def test_semantic_paper_selector_accepts_only_the_pinned_identifier_forms(
    paper_id: str,
) -> None:
    assert SemanticScholarPaperSelector(paper_id).paper_id == paper_id


@pytest.mark.parametrize(
    "construct",
    [
        lambda: SemanticScholarAuthorSelector(" 42"),
        lambda: SemanticScholarAuthorSelector("4\n2"),
        lambda: SemanticScholarTopicSelector(" public systems", "Computer Science"),
        lambda: SemanticScholarTopicSelector("public\nsystems", "Computer Science"),
        lambda: SemanticScholarTopicSelector("x" * 513, "Computer Science"),
        lambda: SemanticScholarTopicSelector("public systems", " Computer Science"),
        lambda: SemanticScholarOrganizationSelector(" https://ror.org/03yrm5c26", "42"),
    ],
)
def test_semantic_selector_and_query_values_are_trimmed_control_free_and_bounded(
    construct: Callable[[], object],
) -> None:
    with pytest.raises(ProbeConnectorError) as exc_info:
        construct()
    assert exc_info.value.code == "ENRICHMENT_PROVIDER_INPUT_MISSING"
    assert exc_info.value.reason == "selector_invalid"


@pytest.mark.parametrize("api_key", [" key", "key\nvalue", "x" * 4097])
def test_semantic_api_key_is_rejected_before_transport_when_unclean_or_unbounded(
    api_key: str,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            content=_FIXTURE,
            headers={"content-type": "application/json"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = SemanticScholarConnector(http_client=client)
        transport = BoundedProbeTransport(client)
        with pytest.raises(ProbeConnectorError) as exc_info:
            connector.run_probe(
                transport=transport,
                deadline=transport.start_deadline(),
                credentials={"api_key": api_key},
            )

    assert exc_info.value.code == "ENRICHMENT_PROVIDER_INPUT_MISSING"
    assert exc_info.value.reason == "credential_bundle_incomplete"
    assert calls == 0


def test_semantic_api_key_and_remote_payload_never_enter_safe_errors() -> None:
    marker = "credential-marker-never-report"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            content=f"{marker}:{_API_KEY}".encode(),
            headers={"content-type": "text/plain"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = SemanticScholarConnector(http_client=client)
        transport = BoundedProbeTransport(client)
        with pytest.raises(ProbeConnectorError) as exc_info:
            connector.run_probe(
                transport=transport,
                deadline=transport.start_deadline(),
                credentials=_CREDENTIALS,
            )

    assert marker not in str(exc_info.value)
    assert _API_KEY not in str(exc_info.value)

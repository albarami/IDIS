"""RED-first tests for the exact provider-probes-v1 contract inventory."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

import idis.services.enrichment.task9.probe_contracts as probe_contracts_module
from idis.services.enrichment.task9.probe_contracts import (
    PROBE_CONTRACT_VERSION,
    PROBE_CONTRACTS,
    PROBE_MAX_ATTEMPTS,
    PROBE_MAX_RESPONSE_BYTES,
    PROBE_TIMEOUT_SECONDS,
    FixtureContractError,
    FixtureManifest,
    parse_fixture_manifest,
    validate_probe_payload,
    verify_fixture_entry,
)
from idis.services.enrichment.task9.provider_catalog import PROVIDER_CATALOG, PROVIDER_IDS

_FIXTURES = Path(__file__).with_name("fixtures")

_EXPECTED_FINAL_URLS = {
    "sec_edgar": "https://data.sec.gov/submissions/CIK0000320193.json",
    "companies_house": (
        "https://api.company-information.service.gov.uk/search/companies?q=TESCO%20PLC"
    ),
    "github": "https://api.github.com/orgs/github",
    "fred": (
        "https://api.stlouisfed.org/fred/series/observations?series_id=GDP&file_type=json&limit=1"
    ),
    "finnhub": "https://finnhub.io/api/v1/stock/profile2?symbol=AAPL",
    "fmp": "https://financialmodelingprep.com/api/v3/profile/AAPL",
    "world_bank": (
        "https://api.worldbank.org/v2/country/US/indicator/NY.GDP.MKTP.CD?format=json&per_page=1"
    ),
    "escwa_catalog": ("https://data.unescwa.org/api/3/action/package_search?q=economy&rows=1"),
    "escwa_ispar": (
        "https://data.arabdevelopmentportal.org/datacatalog/un-agencies?sources=escwa&countries=qat"
    ),
    "qatar_open_data": (
        "https://www.data.gov.qa/api/explore/v2.1/catalog/datasets"
        "?where=search(default,%22economy%22)&limit=1"
    ),
    "hackernews": ("https://hn.algolia.com/api/v1/search?query=Microsoft&tags=story&hitsPerPage=1"),
    "gdelt": (
        "https://api.gdeltproject.org/api/v2/doc/doc"
        "?query=Microsoft&mode=ArtList&maxrecords=1&format=json"
    ),
    "patentsview": (
        "https://api.patentsview.org/patents/query"
        "?q=%7B%22_contains%22%3A%7B%22assignee_organization%22%3A%22Microsoft%20"
        "Technology%20Licensing%2C%20LLC%22%7D%7D"
        "&f=%5B%22patent_number%22%2C%22patent_title%22%2C%22patent_date%22%2C"
        "%22assignee_organization%22%5D&o=%7B%22per_page%22%3A1%7D"
    ),
    "wayback": ("https://web.archive.org/cdx/search/cdx?url=example.com&output=json&limit=1"),
    "google_news_rss": ("https://news.google.com/rss/search?q=Microsoft&hl=en-US&gl=US&ceid=US:en"),
    "epo_open_patent": (
        "https://ops.epo.org/3.2/rest-services/published-data/search/biblio"
        "?q=pa%3DMicrosoft&Range=1-1"
    ),
    "semantic_scholar": (
        "https://api.semanticscholar.org/graph/v1/paper/CorpusId:215416146"
        "?fields=paperId,title,year,authors,externalIds"
    ),
    "perplexity": "https://api.perplexity.ai/chat/completions",
}


def _manifest_payload() -> dict[str, object]:
    payload = json.loads((_FIXTURES / "manifest.json").read_bytes())
    assert isinstance(payload, dict)
    return payload


def test_contract_inventory_is_exactly_catalog_derived_and_uniformly_bounded() -> None:
    assert PROBE_CONTRACT_VERSION == "provider-probes-v1"
    assert frozenset(PROBE_CONTRACTS) == PROVIDER_IDS
    assert len(PROBE_CONTRACTS) == 18
    for provider_id, contract in PROBE_CONTRACTS.items():
        assert contract.provider_id == provider_id
        assert contract.contract_version == PROBE_CONTRACT_VERSION
        assert contract.fixture_id
        assert contract.timeout_seconds == PROBE_TIMEOUT_SECONDS == 15
        assert contract.max_attempts == PROBE_MAX_ATTEMPTS == 1
        assert contract.max_response_bytes == PROBE_MAX_RESPONSE_BYTES == 2_097_152
        assert contract.fail_closed_code == "ENRICHMENT_PREFLIGHT_BLOCKED"
        assert contract.operations
        assert contract.required_normalized_fields
        assert contract.empty_tuple_normalized_fields == (
            frozenset({"supported_synthesis"}) if provider_id == "perplexity" else frozenset()
        )


def test_probe_contract_mapping_has_no_module_accessible_mutable_backing() -> None:
    assert "_contracts" not in vars(probe_contracts_module)


def test_probe_contracts_are_the_catalog_owned_objects_not_a_second_18_row_table() -> None:
    assert "_CONTRACT_ROWS" not in vars(probe_contracts_module)
    for provider_id, contract in PROBE_CONTRACTS.items():
        assert contract is PROVIDER_CATALOG[provider_id].probe_contract


def test_all_final_probe_operations_match_the_plan_exactly() -> None:
    assert {
        provider_id: contract.operations[-1].safe_url
        for provider_id, contract in PROBE_CONTRACTS.items()
    } == _EXPECTED_FINAL_URLS
    assert all(
        operation.safe_url.startswith("https://")
        for contract in PROBE_CONTRACTS.values()
        for operation in contract.operations
    )


def test_patentsview_request_and_fixture_use_the_full_master_plan_assignee() -> None:
    operation = PROBE_CONTRACTS["patentsview"].operations[0]
    query = parse_qs(urlsplit(operation.safe_url).query)
    assert json.loads(query["q"][0]) == {
        "_contains": {"assignee_organization": "Microsoft Technology Licensing, LLC"}
    }
    fixture = json.loads((_FIXTURES / "patentsview.json").read_bytes())
    assert fixture["patents"][0]["assignee_organization"] == ("Microsoft Technology Licensing, LLC")


def test_epo_and_perplexity_multi_stage_and_body_contracts_are_pinned() -> None:
    epo = PROBE_CONTRACTS["epo_open_patent"]
    assert tuple(operation.operation_id for operation in epo.operations) == (
        "oauth",
        "search",
    )
    assert epo.operations[0].method == "POST"
    assert epo.operations[0].safe_url == "https://ops.epo.org/3.2/auth/accesstoken"
    assert epo.operations[0].body == b"grant_type=client_credentials"
    assert epo.operations[1].expected_media_types == (
        "application/exchange+xml",
        "application/xml",
        "text/xml",
    )

    perplexity = PROBE_CONTRACTS["perplexity"].operations[0]
    request_body = json.loads(perplexity.body)
    assert perplexity.method == "POST"
    assert request_body["model"] == "sonar"
    assert request_body["temperature"] == 0
    assert "citation" in request_body["messages"][0]["content"].lower()


def test_fixture_manifest_binds_every_operation_to_sanitized_hermetic_bytes() -> None:
    manifest = parse_fixture_manifest((_FIXTURES / "manifest.json").read_bytes())
    assert manifest.execution_mode == "HERMETIC_TEST"
    assert manifest.acceptance_eligible is False
    expected_keys = {
        (provider_id, operation.operation_id)
        for provider_id, contract in PROBE_CONTRACTS.items()
        for operation in contract.operations
    }
    assert {(entry.provider_id, entry.operation_id) for entry in manifest.entries} == expected_keys

    for entry in manifest.entries:
        body = (_FIXTURES / entry.file_name).read_bytes()
        verify_fixture_entry(entry, body)
        validate_probe_payload(
            PROBE_CONTRACTS[entry.provider_id],
            operation_id=entry.operation_id,
            media_type=entry.media_type,
            body=body,
        )


def test_fixture_manifest_constructor_cannot_represent_gate_a_evidence() -> None:
    manifest = FixtureManifest(entries=())
    assert manifest.execution_mode == "HERMETIC_TEST"
    assert manifest.acceptance_eligible is False
    with pytest.raises(TypeError):
        FixtureManifest(  # type: ignore[call-arg]
            entries=(), execution_mode="PROD", acceptance_eligible=True
        )


def test_manifest_parser_enforces_complete_operation_closure() -> None:
    payload = _manifest_payload()
    entries = payload["entries"]
    assert isinstance(entries, list)
    entries.pop()
    with pytest.raises(FixtureContractError) as exc_info:
        parse_fixture_manifest(json.dumps(payload).encode())
    assert exc_info.value.reason == "manifest_operation_closure_invalid"


def test_manifest_parser_rejects_extra_unknown_operation() -> None:
    payload = _manifest_payload()
    entries = payload["entries"]
    assert isinstance(entries, list)
    extra = dict(entries[0])
    extra["operation_id"] = "invented"
    entries.append(extra)
    with pytest.raises(FixtureContractError):
        parse_fixture_manifest(json.dumps(payload).encode())


def test_manifest_parser_rejects_duplicate_file_names() -> None:
    payload = _manifest_payload()
    entries = payload["entries"]
    assert isinstance(entries, list)
    entries[1]["file_name"] = entries[0]["file_name"]
    with pytest.raises(FixtureContractError) as exc_info:
        parse_fixture_manifest(json.dumps(payload).encode())
    assert exc_info.value.reason == "manifest_file_name_duplicate"


def test_manifest_parser_rejects_media_type_not_accepted_by_operation() -> None:
    payload = _manifest_payload()
    entries = payload["entries"]
    assert isinstance(entries, list)
    github = next(entry for entry in entries if entry["provider_id"] == "github")
    github["media_type"] = "text/plain"
    with pytest.raises(FixtureContractError) as exc_info:
        parse_fixture_manifest(json.dumps(payload).encode())
    assert exc_info.value.reason == "manifest_media_type_binding_invalid"


@pytest.mark.parametrize(
    ("provider_id", "body"),
    [
        ("escwa_ispar", b'{"datasets":[1]}'),
        ("hackernews", b'{"hits":[1]}'),
    ],
)
def test_adversarial_list_members_fail_with_the_stable_contract_error(
    provider_id: str, body: bytes
) -> None:
    with pytest.raises(FixtureContractError) as exc_info:
        validate_probe_payload(
            PROBE_CONTRACTS[provider_id],
            operation_id="probe",
            media_type="application/json",
            body=body,
        )
    assert exc_info.value.code == "ENRICHMENT_PREFLIGHT_BLOCKED"


def test_all_json_validators_are_total_over_adversarial_json_types() -> None:
    adversarial_values: tuple[object, ...] = (
        None,
        True,
        1,
        "text",
        [],
        [1],
        {},
        {"datasets": [1]},
        {"hits": [1]},
    )
    for contract in PROBE_CONTRACTS.values():
        for operation in contract.operations:
            if "application/json" not in operation.expected_media_types:
                continue
            for value in adversarial_values:
                with pytest.raises(FixtureContractError):
                    validate_probe_payload(
                        contract,
                        operation_id=operation.operation_id,
                        media_type="application/json",
                        body=json.dumps(value).encode(),
                    )


@pytest.mark.parametrize(
    "body",
    [
        (
            b"<other><channel><item><title>x</title><link>https://example.org</link>"
            b"<pubDate>today</pubDate></item></channel></other>"
        ),
        (
            b"<rss><item><title>x</title><link>https://example.org</link>"
            b"<pubDate>today</pubDate></item></rss>"
        ),
    ],
)
def test_google_news_requires_rss_channel_item_structure(body: bytes) -> None:
    with pytest.raises(FixtureContractError):
        validate_probe_payload(
            PROBE_CONTRACTS["google_news_rss"],
            operation_id="probe",
            media_type="application/rss+xml",
            body=body,
        )


def test_epo_requires_one_coherent_exchange_document_record() -> None:
    body = b"""\
<ops:world-patent-data xmlns:ops="http://ops.epo.org" xmlns:e="http://www.epo.org/exchange">
  <e:exchange-document><e:publication-reference><e:doc-number>EP1</e:doc-number></e:publication-reference></e:exchange-document>
  <e:exchange-document><e:invention-title>Title</e:invention-title><e:applicant><e:name>Applicant</e:name></e:applicant></e:exchange-document>
</ops:world-patent-data>"""
    with pytest.raises(FixtureContractError):
        validate_probe_payload(
            PROBE_CONTRACTS["epo_open_patent"],
            operation_id="search",
            media_type="application/xml",
            body=body,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "paperId": "paper",
            "title": "title",
            "authors": [1],
            "externalIds": {"DOI": "10.0/example"},
        },
        {
            "paperId": "paper",
            "title": "title",
            "authors": [{"name": "Author"}],
            "externalIds": {"DOI": "10.0/example"},
        },
    ],
)
def test_semantic_scholar_authors_must_be_attribution_ready_mappings(
    payload: dict[str, object],
) -> None:
    with pytest.raises(FixtureContractError):
        validate_probe_payload(
            PROBE_CONTRACTS["semantic_scholar"],
            operation_id="probe",
            media_type="application/json",
            body=json.dumps(payload).encode(),
        )


def test_semantic_scholar_fixture_accepts_typed_numeric_corpus_id() -> None:
    validate_probe_payload(
        PROBE_CONTRACTS["semantic_scholar"],
        operation_id="probe",
        media_type="application/json",
        body=(_FIXTURES / "semantic_scholar.json").read_bytes(),
    )


def test_escwa_ispar_fixture_flows_through_the_existing_connector_normalizer() -> None:
    from idis.services.enrichment.connectors.escwa_ispar import EscwaIsparConnector

    body = (_FIXTURES / "escwa_ispar.json").read_bytes()
    payload = json.loads(body)
    normalized = EscwaIsparConnector._normalize_response(payload, "qat")
    assert normalized["jurisdiction"] == "qat"
    assert normalized["source_filter"] == "escwa"
    assert normalized["datasets"] == [
        {
            "description": "Public indicator description",
            "format": "",
            "id": "dataset-001",
            "modified": "",
            "source": "ESCWA",
            "theme": "",
            "title": "Public indicator",
        }
    ]
    validate_probe_payload(
        PROBE_CONTRACTS["escwa_ispar"],
        operation_id="probe",
        media_type="application/json",
        body=body,
    )


def test_epo_oauth_fixture_matches_documented_provider_shape() -> None:
    payload = json.loads((_FIXTURES / "epo_oauth.json").read_bytes())
    assert {"access_token", "expires_in", "token_type"} < frozenset(payload)
    assert isinstance(payload["expires_in"], str)
    assert payload["scope"] == ""
    assert payload["api_product_list"] == ["ops-v3.2"]
    assert payload["refresh_token_expires_in"] == "0"
    validate_probe_payload(
        PROBE_CONTRACTS["epo_open_patent"],
        operation_id="oauth",
        media_type="application/json",
        body=(_FIXTURES / "epo_oauth.json").read_bytes(),
    )


def test_wayback_requires_the_exact_header_before_the_snapshot_row() -> None:
    body = json.dumps(
        [["wrong", "header", "columns"], ["20250101000000", "https://example.com", "200"]]
    ).encode()
    with pytest.raises(FixtureContractError):
        validate_probe_payload(
            PROBE_CONTRACTS["wayback"],
            operation_id="probe",
            media_type="application/json",
            body=body,
        )


@pytest.mark.parametrize(
    ("provider_id", "operation_id", "media_type", "body"),
    [
        ("github", "probe", "application/json", b"{"),
        ("github", "probe", "text/plain", b'{"login":"github","id":1}'),
        ("github", "probe", "application/json", b"{}"),
        ("google_news_rss", "probe", "application/xml", b"<rss>"),
        (
            "google_news_rss",
            "probe",
            "application/xml",
            b"<rss><channel></channel></rss>",
        ),
        ("epo_open_patent", "search", "application/xml", b"<ops:world-patent-data"),
    ],
)
def test_malformed_content_type_body_or_empty_schema_fails_closed(
    provider_id: str,
    operation_id: str,
    media_type: str,
    body: bytes,
) -> None:
    with pytest.raises(FixtureContractError) as exc_info:
        validate_probe_payload(
            PROBE_CONTRACTS[provider_id],
            operation_id=operation_id,
            media_type=media_type,
            body=body,
        )
    assert exc_info.value.code == "ENRICHMENT_PREFLIGHT_BLOCKED"
    assert body.decode("utf-8", errors="ignore") not in str(exc_info.value)

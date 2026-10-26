"""RED-first characterization of all legacy connector outbound request paths."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

import httpx
import pytest

from idis.services.enrichment.connectors.companies_house import CompaniesHouseConnector
from idis.services.enrichment.connectors.edgar import EdgarConnector
from idis.services.enrichment.connectors.escwa_catalog import EscwaCatalogConnector
from idis.services.enrichment.connectors.escwa_ispar import EscwaIsparConnector
from idis.services.enrichment.connectors.finnhub import FinnhubConnector
from idis.services.enrichment.connectors.fmp import FmpConnector
from idis.services.enrichment.connectors.fred import FredConnector
from idis.services.enrichment.connectors.gdelt import GdeltConnector
from idis.services.enrichment.connectors.github import GitHubConnector
from idis.services.enrichment.connectors.google_news_rss import GoogleNewsRssConnector
from idis.services.enrichment.connectors.hackernews import HackerNewsConnector
from idis.services.enrichment.connectors.patentsview import PatentsViewConnector
from idis.services.enrichment.connectors.qatar_open_data import QatarOpenDataConnector
from idis.services.enrichment.connectors.wayback import WaybackConnector
from idis.services.enrichment.connectors.world_bank import WorldBankConnector
from idis.services.enrichment.models import (
    EnrichmentConnector,
    EnrichmentContext,
    EnrichmentQuery,
    EnrichmentRequest,
    EnrichmentStatus,
    EntityType,
)
from idis.services.enrichment.task9.probe_contracts import (
    LEGACY_PROBE_BLOCKERS,
    PROBE_CONTRACTS,
    LegacyExternalBlocker,
    LegacyProbeBlockerCode,
    LegacyProbeCharacterizationError,
    LegacyProbeClassification,
    LegacyQueryDelta,
    characterize_legacy_probe_request,
)


@dataclass(frozen=True, slots=True)
class _LegacyConnectorCase:
    provider_id: str
    factory: Callable[[httpx.Client], EnrichmentConnector]
    query: EnrichmentQuery


_CASES = (
    _LegacyConnectorCase("sec_edgar", EdgarConnector, EnrichmentQuery(cik="320193")),
    _LegacyConnectorCase(
        "companies_house",
        CompaniesHouseConnector,
        EnrichmentQuery(company_name="TESCO PLC"),
    ),
    _LegacyConnectorCase("github", GitHubConnector, EnrichmentQuery(company_name="github")),
    _LegacyConnectorCase("fred", FredConnector, EnrichmentQuery(ticker="GDP")),
    _LegacyConnectorCase("finnhub", FinnhubConnector, EnrichmentQuery(ticker="AAPL")),
    _LegacyConnectorCase("fmp", FmpConnector, EnrichmentQuery(ticker="AAPL")),
    _LegacyConnectorCase(
        "world_bank",
        WorldBankConnector,
        EnrichmentQuery(jurisdiction="US"),
    ),
    _LegacyConnectorCase(
        "escwa_catalog",
        EscwaCatalogConnector,
        EnrichmentQuery(company_name="economy"),
    ),
    _LegacyConnectorCase(
        "escwa_ispar",
        EscwaIsparConnector,
        EnrichmentQuery(jurisdiction="qat"),
    ),
    _LegacyConnectorCase(
        "qatar_open_data",
        QatarOpenDataConnector,
        EnrichmentQuery(company_name="economy"),
    ),
    _LegacyConnectorCase(
        "hackernews",
        HackerNewsConnector,
        EnrichmentQuery(company_name="Microsoft"),
    ),
    _LegacyConnectorCase(
        "gdelt",
        GdeltConnector,
        EnrichmentQuery(company_name="Microsoft"),
    ),
    _LegacyConnectorCase(
        "patentsview",
        PatentsViewConnector,
        EnrichmentQuery(company_name="Microsoft Technology Licensing, LLC"),
    ),
    _LegacyConnectorCase(
        "wayback",
        WaybackConnector,
        EnrichmentQuery(company_name="example.com"),
    ),
    _LegacyConnectorCase(
        "google_news_rss",
        GoogleNewsRssConnector,
        EnrichmentQuery(company_name="Microsoft"),
    ),
)

_EXACT_MATCHES = frozenset(
    {
        "sec_edgar",
        "github",
        "finnhub",
        "fmp",
        "google_news_rss",
    }
)
_BLOCKS_LIVE = frozenset(
    {
        "companies_house",
        "fred",
        "world_bank",
        "escwa_catalog",
        "escwa_ispar",
        "qatar_open_data",
        "hackernews",
        "gdelt",
        "patentsview",
        "wayback",
    }
)


def _capture_request(case: _LegacyConnectorCase) -> httpx.Request:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(404, json={"hermetic": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = case.factory(client)
        result = connector.fetch(
            EnrichmentRequest(
                tenant_id="tenant-hermetic-characterization",
                entity_type=EntityType.COMPANY,
                query=case.query,
            ),
            EnrichmentContext(
                timeout_seconds=5.0,
                max_retries=0,
                request_id=f"request-{case.provider_id}",
                byol_credentials={
                    "api_key": "credential-marker-never-persist",
                    "token": "credential-marker-never-persist",
                },
            ),
        )

    assert result.status is EnrichmentStatus.MISS
    assert len(captured) == 1
    return captured[0]


def test_all_actual_legacy_paths_are_exactly_partitioned() -> None:
    characterizations = {}
    for case in _CASES:
        request = _capture_request(case)
        characterizations[case.provider_id] = characterize_legacy_probe_request(
            provider_id=case.provider_id,
            method=request.method,
            url=str(request.url),
            body=request.content,
        )

    assert set(characterizations) == _EXACT_MATCHES | _BLOCKS_LIVE
    assert {
        provider_id
        for provider_id, observation in characterizations.items()
        if observation.classification is LegacyProbeClassification.ALIGNED_REQUEST_SHAPE
    } == _EXACT_MATCHES
    assert {
        provider_id
        for provider_id, observation in characterizations.items()
        if observation.classification is LegacyProbeClassification.BLOCKS_LIVE
    } == _BLOCKS_LIVE
    assert set(LEGACY_PROBE_BLOCKERS) == _BLOCKS_LIVE
    assert all(
        observation.execution_mode == "HERMETIC_TEST" for observation in characterizations.values()
    )
    assert all(
        observation.acceptance_eligible is False for observation in characterizations.values()
    )


def test_blocker_inventory_is_typed_immutable_and_exact() -> None:
    expected = {
        "companies_house": (
            LegacyProbeBlockerCode.COMPANIES_HOUSE_ITEMS_PER_PAGE_10,
            (LegacyQueryDelta("items_per_page", (), ("10",)),),
            (),
        ),
        "fred": (
            LegacyProbeBlockerCode.FRED_LIMIT_20_PLUS_SORT,
            (
                LegacyQueryDelta("limit", ("1",), ("20",)),
                LegacyQueryDelta("sort_order", (), ("desc",)),
            ),
            (),
        ),
        "world_bank": (
            LegacyProbeBlockerCode.WORLD_BANK_PER_PAGE_10,
            (LegacyQueryDelta("per_page", ("1",), ("10",)),),
            (),
        ),
        "escwa_catalog": (
            LegacyProbeBlockerCode.ESCWA_CATALOG_ROWS_10,
            (LegacyQueryDelta("rows", ("1",), ("10",)),),
            (),
        ),
        "escwa_ispar": (
            LegacyProbeBlockerCode.ESCWA_ISPAR_LIVE_HTTP_403,
            (),
            (LegacyExternalBlocker.ESCWA_ISPAR_LIVE_HTTP_403,),
        ),
        "qatar_open_data": (
            LegacyProbeBlockerCode.QATAR_OPEN_DATA_LIMIT_10,
            (LegacyQueryDelta("limit", ("1",), ("10",)),),
            (),
        ),
        "hackernews": (
            LegacyProbeBlockerCode.HACKERNEWS_HITS_PER_PAGE_10,
            (LegacyQueryDelta("hitsPerPage", ("1",), ("10",)),),
            (),
        ),
        "gdelt": (
            LegacyProbeBlockerCode.GDELT_MODE_CASING_PLUS_MAXRECORDS_10,
            (
                LegacyQueryDelta("maxrecords", ("1",), ("10",)),
                LegacyQueryDelta("mode", ("ArtList",), ("artlist",)),
            ),
            (),
        ),
        "patentsview": (
            LegacyProbeBlockerCode.PATENTSVIEW_PER_PAGE_10_RETIRED_ENDPOINT,
            (
                LegacyQueryDelta(
                    "o",
                    (json.dumps({"per_page": 1}, sort_keys=True, separators=(",", ":")),),
                    (json.dumps({"per_page": 10}, sort_keys=True, separators=(",", ":")),),
                ),
            ),
            (LegacyExternalBlocker.PATENTSVIEW_RETIRED_HTTP_410,),
        ),
        "wayback": (
            LegacyProbeBlockerCode.WAYBACK_LIMIT_10,
            (LegacyQueryDelta("limit", ("1",), ("10",)),),
            (),
        ),
    }
    actual = {
        provider_id: (blocker.code, blocker.query_deltas, blocker.external_blockers)
        for provider_id, blocker in LEGACY_PROBE_BLOCKERS.items()
    }
    assert actual == expected
    assert all(
        blocker.classification is LegacyProbeClassification.BLOCKS_LIVE
        for blocker in LEGACY_PROBE_BLOCKERS.values()
    )
    assert all(blocker.acceptance_eligible is False for blocker in LEGACY_PROBE_BLOCKERS.values())

    with pytest.raises(TypeError):
        cast(Any, LEGACY_PROBE_BLOCKERS)["sec_edgar"] = LEGACY_PROBE_BLOCKERS["fred"]


def test_recorded_drift_disappearing_requires_review_instead_of_claiming_readiness() -> None:
    operation = PROBE_CONTRACTS["companies_house"].operations[0]
    with pytest.raises(LegacyProbeCharacterizationError) as exc_info:
        characterize_legacy_probe_request(
            provider_id="companies_house",
            method=operation.method,
            url=operation.safe_url,
            body=operation.body,
        )
    assert exc_info.value.reason == "recorded_legacy_drift_missing"
    assert "companies_house" not in str(exc_info.value)


def test_unrecorded_drift_fails_secret_safe() -> None:
    marker = "credential-marker-never-report"
    operation = PROBE_CONTRACTS["sec_edgar"].operations[0]
    with pytest.raises(LegacyProbeCharacterizationError) as exc_info:
        characterize_legacy_probe_request(
            provider_id="sec_edgar",
            method=operation.method,
            url=f"{operation.safe_url}?unexpected={marker}",
            body=operation.body,
        )
    assert exc_info.value.reason == "unrecorded_legacy_request_drift"
    assert marker not in str(exc_info.value)

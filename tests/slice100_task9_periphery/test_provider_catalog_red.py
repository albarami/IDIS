"""RED-first closure tests for the immutable Task 9 provider catalog."""

from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError

import pytest

import idis.services.enrichment.task9.provider_catalog as provider_catalog_module
from idis.services.enrichment.models import RightsClass
from idis.services.enrichment.task9.provider_catalog import (
    ANALYSIS_AGENT_IDS,
    BUDGET_COMPONENT_IDS,
    COMMON_EXTERNAL_CONTEXT_CONSUMERS,
    CREDENTIALED_PROVIDER_IDS,
    DELIVERABLE_CONSUMERS_BY_FACT_DOMAIN,
    NON_SECRET_CONFIG_ENV_VARS,
    PROVIDER_CATALOG,
    PROVIDER_CATALOG_VERSION,
    PROVIDER_IDS,
    RIGHTS_PROVIDER_IDS,
    SECRET_ENV_VARS,
    CredentialField,
    FactDomain,
    IdentitySelectorAlternative,
    IdentitySelectorType,
    ProviderCatalogRoutingError,
    RightsRequirement,
    deliverable_consumers_for,
)

EXPECTED_PROVIDER_IDS = frozenset(
    {
        "sec_edgar",
        "companies_house",
        "github",
        "fred",
        "finnhub",
        "fmp",
        "world_bank",
        "escwa_catalog",
        "escwa_ispar",
        "qatar_open_data",
        "hackernews",
        "gdelt",
        "patentsview",
        "wayback",
        "google_news_rss",
        "epo_open_patent",
        "semantic_scholar",
        "perplexity",
    }
)

EXPECTED_COMMON_CONSUMERS = (
    "RAG_EVIDENCE:external_context",
    "GRAPH_EVIDENCE:external_context",
    "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN",
    "METHODOLOGY_LAYER2_READINESS_PACKAGE",
    "SCORING",
    "LAYER2_IC_CHALLENGE:ic_advocate",
    "LAYER2_IC_CHALLENGE:ic_challenger",
    "LAYER2_IC_CHALLENGE:ic_arbiter",
)

COMMERCIAL_DELIVERABLES = (
    "DELIVERABLES:commercial_diligence",
    "DELIVERABLES:risk_register",
    "DELIVERABLES:evidence_index",
    "DELIVERABLES:provenance_appendix",
)
FINANCIAL_DELIVERABLES = (
    "DELIVERABLES:financial_diligence",
    "DELIVERABLES:risk_register",
    "DELIVERABLES:evidence_index",
    "DELIVERABLES:provenance_appendix",
)

_BOTH_DOMAINS = (FactDomain.COMMERCIAL_DILIGENCE, FactDomain.FINANCIAL_DILIGENCE)
_COMMERCIAL_ONLY = (FactDomain.COMMERCIAL_DILIGENCE,)

# Acceptance literals are intentionally independent of the production catalog. A
# field-level drift in any one of the 18 rows must fail this suite.
EXPECTED_PROVIDER_ROWS = {
    "sec_edgar": (
        "apple-cik-0000320193",
        ("cik", "us_reporting_identity"),
        (),
        ("IDIS_EDGAR_USER_AGENT",),
        (
            "ANALYSIS:financial_agent",
            "ANALYSIS:market_agent",
            "ANALYSIS:risk_officer_agent",
        ),
        _BOTH_DOMAINS,
    ),
    "companies_house": (
        "tesco-plc-search",
        ("uk_company_number", "legal_name"),
        (CredentialField("COMPANIES_HOUSE_API_KEY", "api_key"),),
        (),
        (
            "ANALYSIS:financial_agent",
            "ANALYSIS:market_agent",
            "ANALYSIS:risk_officer_agent",
        ),
        _BOTH_DOMAINS,
    ),
    "github": (
        "github-org",
        ("github_organization", "github_login"),
        (CredentialField("GITHUB_API_TOKEN", "token"),),
        (),
        (
            "ANALYSIS:technical_agent",
            "ANALYSIS:team_agent",
            "ANALYSIS:sector_specialist_agent",
        ),
        _COMMERCIAL_ONLY,
    ),
    "fred": (
        "fred-gdp",
        ("macro_series", "jurisdiction"),
        (CredentialField("FRED_API_KEY", "api_key"),),
        (),
        (
            "ANALYSIS:financial_agent",
            "ANALYSIS:market_agent",
            "ANALYSIS:sector_specialist_agent",
        ),
        _BOTH_DOMAINS,
    ),
    "finnhub": (
        "finnhub-aapl",
        ("listed_ticker",),
        (CredentialField("FINNHUB_API_KEY", "api_key"),),
        (),
        (
            "ANALYSIS:financial_agent",
            "ANALYSIS:market_agent",
            "ANALYSIS:risk_officer_agent",
        ),
        _BOTH_DOMAINS,
    ),
    "fmp": (
        "fmp-aapl",
        ("listed_ticker",),
        (CredentialField("FMP_API_KEY", "api_key"),),
        (),
        (
            "ANALYSIS:financial_agent",
            "ANALYSIS:market_agent",
            "ANALYSIS:risk_officer_agent",
        ),
        _BOTH_DOMAINS,
    ),
    "world_bank": (
        "world-bank-us-gdp",
        ("iso_jurisdiction", "indicator"),
        (),
        (),
        (
            "ANALYSIS:financial_agent",
            "ANALYSIS:market_agent",
            "ANALYSIS:sector_specialist_agent",
        ),
        _BOTH_DOMAINS,
    ),
    "escwa_catalog": (
        "escwa-catalog-economy",
        ("sector_term", "jurisdiction_term"),
        (),
        (),
        ("ANALYSIS:market_agent", "ANALYSIS:sector_specialist_agent"),
        _COMMERCIAL_ONLY,
    ),
    "escwa_ispar": (
        "escwa-ispar-qat",
        ("iso3_jurisdiction",),
        (),
        (),
        (
            "ANALYSIS:financial_agent",
            "ANALYSIS:market_agent",
            "ANALYSIS:sector_specialist_agent",
        ),
        _BOTH_DOMAINS,
    ),
    "qatar_open_data": (
        "qatar-open-data-economy",
        ("qatar_jurisdiction", "sector_selector"),
        (),
        (),
        ("ANALYSIS:market_agent", "ANALYSIS:sector_specialist_agent"),
        _COMMERCIAL_ONLY,
    ),
    "hackernews": (
        "hn-microsoft",
        ("legal_name", "trading_name"),
        (),
        (),
        (
            "ANALYSIS:market_agent",
            "ANALYSIS:sector_specialist_agent",
            "ANALYSIS:risk_officer_agent",
        ),
        _COMMERCIAL_ONLY,
    ),
    "gdelt": (
        "gdelt-microsoft",
        ("company_name",),
        (),
        (),
        (
            "ANALYSIS:market_agent",
            "ANALYSIS:sector_specialist_agent",
            "ANALYSIS:risk_officer_agent",
        ),
        _COMMERCIAL_ONLY,
    ),
    "patentsview": (
        "patentsview-microsoft",
        ("assignee", "legal_name"),
        (),
        (),
        ("ANALYSIS:technical_agent", "ANALYSIS:sector_specialist_agent"),
        _COMMERCIAL_ONLY,
    ),
    "wayback": (
        "wayback-example",
        ("verified_domain",),
        (),
        (),
        (
            "ANALYSIS:historian_agent",
            "ANALYSIS:market_agent",
            "ANALYSIS:risk_officer_agent",
        ),
        _COMMERCIAL_ONLY,
    ),
    "google_news_rss": (
        "google-news-microsoft",
        ("company_name",),
        (),
        (),
        (
            "ANALYSIS:market_agent",
            "ANALYSIS:sector_specialist_agent",
            "ANALYSIS:risk_officer_agent",
        ),
        _COMMERCIAL_ONLY,
    ),
    "epo_open_patent": (
        "epo-ops-microsoft-assignee",
        ("assignee", "legal_name"),
        (
            CredentialField("EPO_CONSUMER_KEY", "consumer_key"),
            CredentialField("EPO_CONSUMER_SECRET", "consumer_secret"),
        ),
        (),
        ("ANALYSIS:technical_agent", "ANALYSIS:sector_specialist_agent"),
        _COMMERCIAL_ONLY,
    ),
    "semantic_scholar": (
        "semantic-scholar-corpus-215416146",
        (
            "semantic_scholar_paper_id",
            "semantic_scholar_author_id",
            "semantic_scholar_topic_query",
            "semantic_scholar_field_of_study",
            "ror_id",
        ),
        (CredentialField("SEMANTIC_SCHOLAR_API_KEY", "api_key"),),
        (),
        ("ANALYSIS:technical_agent", "ANALYSIS:sector_specialist_agent"),
        _COMMERCIAL_ONLY,
    ),
    "perplexity": (
        "perplexity-public-cited-company-query",
        ("verified_company", "market_selector"),
        (CredentialField("PERPLEXITY_API_KEY", "api_key"),),
        (),
        ("ANALYSIS:market_agent", "ANALYSIS:sector_specialist_agent"),
        _COMMERCIAL_ONLY,
    ),
}


def test_catalog_is_the_single_source_for_providers_and_budget_components() -> None:
    assert PROVIDER_CATALOG_VERSION == "enrichment-provider-catalog-v1"
    assert PROVIDER_IDS == frozenset(PROVIDER_CATALOG) == EXPECTED_PROVIDER_IDS
    assert PROVIDER_IDS | {"anthropic", "openai_embeddings"} == BUDGET_COMPONENT_IDS


def test_catalog_and_nested_specs_are_immutable_without_a_mutable_backing_escape() -> None:
    with pytest.raises(TypeError):
        PROVIDER_CATALOG["invented"] = PROVIDER_CATALOG["sec_edgar"]  # type: ignore[index]

    with pytest.raises(FrozenInstanceError):
        PROVIDER_CATALOG["sec_edgar"].provider_id = "invented"  # type: ignore[misc]

    assert "_catalog" not in vars(provider_catalog_module)


def test_rights_inventories_are_derived_and_exact() -> None:
    assert RIGHTS_PROVIDER_IDS[RightsRequirement.ATTRIBUTION_PROD] == frozenset(
        {"wayback", "google_news_rss", "semantic_scholar"}
    )
    assert RIGHTS_PROVIDER_IDS[RightsRequirement.COMMERCIAL_PROD] == frozenset(
        {"finnhub", "fmp", "perplexity"}
    )
    assert RIGHTS_PROVIDER_IDS[RightsRequirement.NONE] == EXPECTED_PROVIDER_IDS - {
        "wayback",
        "google_news_rss",
        "semantic_scholar",
        "finnhub",
        "fmp",
        "perplexity",
    }

    for provider_id in RIGHTS_PROVIDER_IDS[RightsRequirement.ATTRIBUTION_PROD]:
        assert PROVIDER_CATALOG[provider_id].rights_class is RightsClass.YELLOW
    for provider_id in RIGHTS_PROVIDER_IDS[RightsRequirement.COMMERCIAL_PROD]:
        assert PROVIDER_CATALOG[provider_id].rights_class is RightsClass.RED
    for provider_id in RIGHTS_PROVIDER_IDS[RightsRequirement.NONE]:
        assert PROVIDER_CATALOG[provider_id].rights_class is RightsClass.GREEN


def test_secret_and_non_secret_environment_inventories_are_separate_and_derived() -> None:
    assert (
        frozenset(
            {
                "companies_house",
                "github",
                "fred",
                "finnhub",
                "fmp",
                "epo_open_patent",
                "semantic_scholar",
                "perplexity",
            }
        )
        == CREDENTIALED_PROVIDER_IDS
    )
    assert (
        frozenset(
            {
                "COMPANIES_HOUSE_API_KEY",
                "GITHUB_API_TOKEN",
                "FRED_API_KEY",
                "FINNHUB_API_KEY",
                "FMP_API_KEY",
                "EPO_CONSUMER_KEY",
                "EPO_CONSUMER_SECRET",
                "SEMANTIC_SCHOLAR_API_KEY",
                "PERPLEXITY_API_KEY",
            }
        )
        == SECRET_ENV_VARS
    )
    assert len(SECRET_ENV_VARS) == 9
    assert frozenset({"IDIS_EDGAR_USER_AGENT"}) == NON_SECRET_CONFIG_ENV_VARS
    assert SECRET_ENV_VARS.isdisjoint(NON_SECRET_CONFIG_ENV_VARS)


def test_every_provider_row_matches_the_exact_accepted_catalog_contract() -> None:
    assert frozenset(EXPECTED_PROVIDER_ROWS) == EXPECTED_PROVIDER_IDS
    for provider_id, expected in EXPECTED_PROVIDER_ROWS.items():
        spec = PROVIDER_CATALOG[provider_id]
        actual = (
            spec.probe_fixture_id,
            spec.required_identity_fields,
            spec.credential_fields,
            spec.non_secret_config_env_vars,
            spec.analysis_consumers,
            spec.allowed_fact_domains,
        )
        assert actual == expected, provider_id


def test_epo_credentials_are_one_immutable_atomic_bundle() -> None:
    epo_fields = PROVIDER_CATALOG["epo_open_patent"].credential_fields
    assert epo_fields == (
        CredentialField("EPO_CONSUMER_KEY", "consumer_key"),
        CredentialField("EPO_CONSUMER_SECRET", "consumer_secret"),
    )
    assert isinstance(epo_fields, tuple)


def test_semantic_scholar_selector_alternatives_are_typed_catalog_authority() -> None:
    spec = PROVIDER_CATALOG["semantic_scholar"]

    assert spec.identity_selector_alternatives == (
        IdentitySelectorAlternative(
            IdentitySelectorType.SEMANTIC_SCHOLAR_PAPER,
            ("semantic_scholar_paper_id",),
        ),
        IdentitySelectorAlternative(
            IdentitySelectorType.SEMANTIC_SCHOLAR_AUTHOR,
            ("semantic_scholar_author_id",),
        ),
        IdentitySelectorAlternative(
            IdentitySelectorType.SEMANTIC_SCHOLAR_TOPIC,
            ("semantic_scholar_topic_query", "semantic_scholar_field_of_study"),
        ),
        IdentitySelectorAlternative(
            IdentitySelectorType.SEMANTIC_SCHOLAR_ORGANIZATION,
            ("ror_id", "semantic_scholar_author_id"),
        ),
    )
    assert spec.required_identity_fields == (
        "semantic_scholar_paper_id",
        "semantic_scholar_author_id",
        "semantic_scholar_topic_query",
        "semantic_scholar_field_of_study",
        "ror_id",
    )


def test_runtime_lineage_is_pinned_and_derived_by_each_catalog_row() -> None:
    for provider_id, spec in PROVIDER_CATALOG.items():
        lineage = spec.runtime_lineage
        assert lineage.connector_identity == provider_id
        assert lineage.connector_version_contract
        assert (
            lineage.connector_version_hash
            == hashlib.sha256(lineage.connector_version_contract.encode()).hexdigest()
        )
        assert lineage.request_builder_identity
        assert lineage.request_builder_contract == lineage.request_builder_identity
        assert (
            lineage.request_builder_version_hash
            == hashlib.sha256(lineage.request_builder_contract.encode()).hexdigest()
        )

    assert (
        PROVIDER_CATALOG["epo_open_patent"].runtime_lineage.request_builder_identity
        == "epo-open-patent-request-builder-v2"
    )
    assert (
        PROVIDER_CATALOG["semantic_scholar"].runtime_lineage.request_builder_identity
        == "semantic-scholar-request-builder-v3"
    )
    assert (
        PROVIDER_CATALOG["perplexity"].runtime_lineage.request_builder_identity
        == "perplexity-request-builder-v2|sonar|temperature=0|verified-company|"
        "market-selector|one-public-company-fact-with-citations"
    )


def test_every_provider_routes_to_exact_common_external_context_consumers() -> None:
    assert COMMON_EXTERNAL_CONTEXT_CONSUMERS == EXPECTED_COMMON_CONSUMERS
    for spec in PROVIDER_CATALOG.values():
        assert spec.common_consumers == EXPECTED_COMMON_CONSUMERS
        assert spec.rag_external_context is True


def test_deliverables_are_resolved_by_one_closed_fact_domain() -> None:
    assert DELIVERABLE_CONSUMERS_BY_FACT_DOMAIN == {
        FactDomain.COMMERCIAL_DILIGENCE: COMMERCIAL_DELIVERABLES,
        FactDomain.FINANCIAL_DILIGENCE: FINANCIAL_DELIVERABLES,
    }
    assert (
        deliverable_consumers_for("sec_edgar", FactDomain.COMMERCIAL_DILIGENCE)
        == COMMERCIAL_DELIVERABLES
    )
    assert (
        deliverable_consumers_for("sec_edgar", FactDomain.FINANCIAL_DILIGENCE)
        == FINANCIAL_DELIVERABLES
    )
    assert "DELIVERABLES:financial_diligence" not in deliverable_consumers_for(
        "sec_edgar", FactDomain.COMMERCIAL_DILIGENCE
    )
    assert "DELIVERABLES:commercial_diligence" not in deliverable_consumers_for(
        "sec_edgar", FactDomain.FINANCIAL_DILIGENCE
    )


@pytest.mark.parametrize(
    ("provider_id", "fact_domain"),
    [
        ("invented", FactDomain.COMMERCIAL_DILIGENCE),
        ("github", FactDomain.FINANCIAL_DILIGENCE),
        ("sec_edgar", "combined_diligence"),
    ],
)
def test_unknown_provider_domain_or_disallowed_domain_fails_closed(
    provider_id: str, fact_domain: FactDomain | str
) -> None:
    with pytest.raises(ProviderCatalogRoutingError) as exc_info:
        deliverable_consumers_for(provider_id, fact_domain)
    assert exc_info.value.code == "ENRICHMENT_CONSUMPTION_MISSING"


def test_consumer_routes_are_closed_and_have_no_layer1_court_vep_or_debate() -> None:
    allowed_consumers = {
        *ANALYSIS_AGENT_IDS,
        *EXPECTED_COMMON_CONSUMERS,
        *COMMERCIAL_DELIVERABLES,
        *FINANCIAL_DELIVERABLES,
    }
    actual_consumers = {
        consumer
        for spec in PROVIDER_CATALOG.values()
        for consumer in (
            *spec.analysis_consumers,
            *spec.common_consumers,
            *(
                consumer
                for domain in spec.allowed_fact_domains
                for consumer in deliverable_consumers_for(spec.provider_id, domain)
            ),
        )
    }
    assert actual_consumers <= allowed_consumers
    assert not any(consumer.startswith("DEBATE:") for consumer in actual_consumers)
    assert not any("COURT" in consumer or "VEP" in consumer for consumer in actual_consumers)

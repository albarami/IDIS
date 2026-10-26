"""Immutable authority for the Slice100 18-provider enrichment surface.

This catalog is deliberately not wired into the active enrichment registry yet. It
contains only public, secret-free metadata and derives every count-bearing inventory
from the provider mapping.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from urllib.parse import urlsplit

from idis.services.enrichment.models import RightsClass

PROVIDER_CATALOG_VERSION: Final = "enrichment-provider-catalog-v1"
PROBE_CONTRACT_VERSION: Final = "provider-probes-v1"
PROBE_TIMEOUT_SECONDS: Final = 15
PROBE_MAX_ATTEMPTS: Final = 1
PROBE_MAX_RESPONSE_BYTES: Final = 2_097_152
PROBE_FAIL_CLOSED_CODE: Final = "ENRICHMENT_PREFLIGHT_BLOCKED"


class RightsRequirement(StrEnum):
    """Production authorization required before an applicable provider call."""

    NONE = "NONE"
    ATTRIBUTION_PROD = "ATTRIBUTION_PROD"
    COMMERCIAL_PROD = "COMMERCIAL_PROD"


class FactDomain(StrEnum):
    """Closed fact domains that may route to diligence deliverables."""

    COMMERCIAL_DILIGENCE = "commercial_diligence"
    FINANCIAL_DILIGENCE = "financial_diligence"


class IdentitySelectorType(StrEnum):
    """Closed selector alternatives resolved before provider admission."""

    VERIFIED_IDENTITY = "verified_identity"
    SEMANTIC_SCHOLAR_PAPER = "semantic_scholar_paper"
    SEMANTIC_SCHOLAR_AUTHOR = "semantic_scholar_author"
    SEMANTIC_SCHOLAR_TOPIC = "semantic_scholar_topic"
    SEMANTIC_SCHOLAR_ORGANIZATION = "semantic_scholar_organization"


class ProviderCatalogRoutingError(ValueError):
    """A catalog provider/fact-domain pair has no authorized consumer route."""

    code = "ENRICHMENT_CONSUMPTION_MISSING"


@dataclass(frozen=True, slots=True)
class CredentialField:
    """One bootstrap environment name mapped to an encrypted credential key."""

    env_var: str
    credential_key: str


@dataclass(frozen=True, slots=True)
class IdentitySelectorAlternative:
    """One complete, conjunctive identity-field group in a disjoint alternative set."""

    selector_type: IdentitySelectorType
    required_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not self.required_fields
            or len(set(self.required_fields)) != len(self.required_fields)
            or any(
                not isinstance(field, str) or not field.strip() for field in self.required_fields
            )
        ):
            raise ValueError("identity selector fields must be unique non-empty strings")


@dataclass(frozen=True, slots=True)
class ProviderProbeOperation:
    """One exact secret-free operation owned by the provider catalog."""

    operation_id: str
    method: str
    safe_url: str
    expected_media_types: tuple[str, ...] = ("application/json",)
    body: bytes = b""

    def __post_init__(self) -> None:
        parsed = urlsplit(self.safe_url) if isinstance(self.safe_url, str) else None
        if (
            not isinstance(self.operation_id, str)
            or not self.operation_id
            or not isinstance(self.method, str)
            or self.method not in {"GET", "POST"}
            or parsed is None
            or parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or not isinstance(self.expected_media_types, tuple)
            or not self.expected_media_types
            or any(
                not isinstance(media_type, str)
                or not media_type
                or media_type != media_type.strip().lower()
                for media_type in self.expected_media_types
            )
            or not isinstance(self.body, bytes)
        ):
            raise ValueError("provider probe operation is invalid")

    @property
    def approved_host(self) -> str:
        """Return the validated operation host."""

        host = urlsplit(self.safe_url).hostname
        if host is None:  # pragma: no cover - constructor establishes this invariant
            raise RuntimeError("provider probe operation has no host")
        return host


@dataclass(frozen=True, slots=True)
class ProviderProbeContract:
    """Complete provider-probes-v1 metadata owned by one catalog row."""

    provider_id: str
    fixture_id: str
    operations: tuple[ProviderProbeOperation, ...]
    required_normalized_fields: tuple[str, ...]
    empty_tuple_normalized_fields: frozenset[str] = frozenset()
    fail_closed_code: str = PROBE_FAIL_CLOSED_CODE
    contract_version: str = PROBE_CONTRACT_VERSION
    timeout_seconds: int = PROBE_TIMEOUT_SECONDS
    max_attempts: int = PROBE_MAX_ATTEMPTS
    max_response_bytes: int = PROBE_MAX_RESPONSE_BYTES

    def __post_init__(self) -> None:
        operation_ids = tuple(operation.operation_id for operation in self.operations)
        if (
            not isinstance(self.provider_id, str)
            or not self.provider_id
            or not isinstance(self.fixture_id, str)
            or not self.fixture_id
            or not self.operations
            or len(set(operation_ids)) != len(operation_ids)
            or not self.required_normalized_fields
            or len(set(self.required_normalized_fields)) != len(self.required_normalized_fields)
            or any(
                not isinstance(field, str) or not field for field in self.required_normalized_fields
            )
            or not self.empty_tuple_normalized_fields.issubset(self.required_normalized_fields)
            or self.fail_closed_code != PROBE_FAIL_CLOSED_CODE
            or self.contract_version != PROBE_CONTRACT_VERSION
            or self.timeout_seconds != PROBE_TIMEOUT_SECONDS
            or self.max_attempts != PROBE_MAX_ATTEMPTS
            or self.max_response_bytes != PROBE_MAX_RESPONSE_BYTES
        ):
            raise ValueError("provider probe contract is invalid")


@dataclass(frozen=True, slots=True)
class ProviderRuntimeLineage:
    """Catalog-pinned semantic lineage labels, not code-attestation claims."""

    connector_identity: str
    connector_version_contract: str
    request_builder_identity: str
    request_builder_contract: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or any(ord(character) < 32 for character in value)
            for value in (
                self.connector_identity,
                self.connector_version_contract,
                self.request_builder_identity,
                self.request_builder_contract,
            )
        ):
            raise ValueError("provider runtime lineage is invalid")

    @property
    def connector_version_hash(self) -> str:
        """Return the deterministic hash of the pinned connector contract label."""

        return hashlib.sha256(self.connector_version_contract.encode()).hexdigest()

    @property
    def request_builder_version_hash(self) -> str:
        """Return the deterministic hash of the pinned builder contract label."""

        return hashlib.sha256(self.request_builder_contract.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Secret-free provider admission, probe, and consumer-routing metadata."""

    provider_id: str
    rights_class: RightsClass
    rights_requirement: RightsRequirement
    credential_fields: tuple[CredentialField, ...]
    non_secret_config_env_vars: tuple[str, ...]
    identity_selector_alternatives: tuple[IdentitySelectorAlternative, ...]
    probe_contract: ProviderProbeContract
    runtime_lineage: ProviderRuntimeLineage
    analysis_consumers: tuple[str, ...]
    common_consumers: tuple[str, ...]
    allowed_fact_domains: tuple[FactDomain, ...]
    rag_external_context: bool

    @property
    def probe_fixture_id(self) -> str:
        """Return the catalog-owned probe fixture identifier."""

        return self.probe_contract.fixture_id

    @property
    def required_identity_fields(self) -> tuple[str, ...]:
        """Return the ordered union retained for read-only compatibility."""

        ordered: dict[str, None] = {}
        for alternative in self.identity_selector_alternatives:
            for field in alternative.required_fields:
                ordered.setdefault(field, None)
        return tuple(ordered)


ANALYSIS_AGENT_IDS: Final = frozenset(
    {
        "ANALYSIS:financial_agent",
        "ANALYSIS:market_agent",
        "ANALYSIS:technical_agent",
        "ANALYSIS:terms_agent",
        "ANALYSIS:team_agent",
        "ANALYSIS:risk_officer_agent",
        "ANALYSIS:historian_agent",
        "ANALYSIS:sector_specialist_agent",
    }
)

COMMON_EXTERNAL_CONTEXT_CONSUMERS: Final = (
    "RAG_EVIDENCE:external_context",
    "GRAPH_EVIDENCE:external_context",
    "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN",
    "METHODOLOGY_LAYER2_READINESS_PACKAGE",
    "SCORING",
    "LAYER2_IC_CHALLENGE:ic_advocate",
    "LAYER2_IC_CHALLENGE:ic_challenger",
    "LAYER2_IC_CHALLENGE:ic_arbiter",
)

DELIVERABLE_CONSUMERS_BY_FACT_DOMAIN: Final[Mapping[FactDomain, tuple[str, ...]]] = (
    MappingProxyType(
        {
            FactDomain.COMMERCIAL_DILIGENCE: (
                "DELIVERABLES:commercial_diligence",
                "DELIVERABLES:risk_register",
                "DELIVERABLES:evidence_index",
                "DELIVERABLES:provenance_appendix",
            ),
            FactDomain.FINANCIAL_DILIGENCE: (
                "DELIVERABLES:financial_diligence",
                "DELIVERABLES:risk_register",
                "DELIVERABLES:evidence_index",
                "DELIVERABLES:provenance_appendix",
            ),
        }
    )
)

_COMMERCIAL_ONLY: Final = (FactDomain.COMMERCIAL_DILIGENCE,)
_COMMERCIAL_AND_FINANCIAL: Final = (
    FactDomain.COMMERCIAL_DILIGENCE,
    FactDomain.FINANCIAL_DILIGENCE,
)


def _operation(
    operation_id: str,
    method: str,
    safe_url: str,
    media_types: tuple[str, ...] = ("application/json",),
    body: bytes = b"",
) -> ProviderProbeOperation:
    return ProviderProbeOperation(
        operation_id=operation_id,
        method=method,
        safe_url=safe_url,
        expected_media_types=media_types,
        body=body,
    )


def _probe(
    provider_id: str,
    fixture_id: str,
    operations: tuple[ProviderProbeOperation, ...],
    required_fields: tuple[str, ...],
    *,
    empty_tuple_fields: frozenset[str] = frozenset(),
) -> ProviderProbeContract:
    return ProviderProbeContract(
        provider_id=provider_id,
        fixture_id=fixture_id,
        operations=operations,
        required_normalized_fields=required_fields,
        empty_tuple_normalized_fields=empty_tuple_fields,
    )


_PERPLEXITY_BODY = (
    b'{"messages":[{"content":"For Microsoft Corporation in the software market, provide one '
    b'public company fact with citations.","role":"user"}],"model":"sonar","temperature":0}'
)

_PROBE_CONTRACT_ROWS = (
    _probe(
        "sec_edgar",
        "apple-cik-0000320193",
        (_operation("probe", "GET", "https://data.sec.gov/submissions/CIK0000320193.json"),),
        ("cik", "name", "filing_metadata"),
    ),
    _probe(
        "companies_house",
        "tesco-plc-search",
        (
            _operation(
                "probe",
                "GET",
                "https://api.company-information.service.gov.uk/search/companies?q=TESCO%20PLC",
            ),
        ),
        ("company_number", "company_name"),
    ),
    _probe(
        "github",
        "github-org",
        (_operation("probe", "GET", "https://api.github.com/orgs/github"),),
        ("login", "id"),
    ),
    _probe(
        "fred",
        "fred-gdp",
        (
            _operation(
                "probe",
                "GET",
                "https://api.stlouisfed.org/fred/series/observations"
                "?series_id=GDP&file_type=json&limit=1",
            ),
        ),
        ("observation_date", "observation_value"),
    ),
    _probe(
        "finnhub",
        "finnhub-aapl",
        (_operation("probe", "GET", "https://finnhub.io/api/v1/stock/profile2?symbol=AAPL"),),
        ("ticker", "company_name"),
    ),
    _probe(
        "fmp",
        "fmp-aapl",
        (_operation("probe", "GET", "https://financialmodelingprep.com/api/v3/profile/AAPL"),),
        ("symbol", "company_name"),
    ),
    _probe(
        "world_bank",
        "world-bank-us-gdp",
        (
            _operation(
                "probe",
                "GET",
                "https://api.worldbank.org/v2/country/US/indicator/NY.GDP.MKTP.CD"
                "?format=json&per_page=1",
            ),
        ),
        ("metadata", "indicator"),
    ),
    _probe(
        "escwa_catalog",
        "escwa-catalog-economy",
        (
            _operation(
                "probe",
                "GET",
                "https://data.unescwa.org/api/3/action/package_search?q=economy&rows=1",
            ),
        ),
        ("success", "dataset"),
    ),
    _probe(
        "escwa_ispar",
        "escwa-ispar-qat",
        (
            _operation(
                "probe",
                "GET",
                "https://data.arabdevelopmentportal.org/datacatalog/un-agencies"
                "?sources=escwa&countries=qat",
            ),
        ),
        ("jurisdiction", "source_filter", "dataset"),
    ),
    _probe(
        "qatar_open_data",
        "qatar-open-data-economy",
        (
            _operation(
                "probe",
                "GET",
                "https://www.data.gov.qa/api/explore/v2.1/catalog/datasets"
                "?where=search(default,%22economy%22)&limit=1",
            ),
        ),
        ("dataset_id", "dataset"),
    ),
    _probe(
        "hackernews",
        "hn-microsoft",
        (
            _operation(
                "probe",
                "GET",
                "https://hn.algolia.com/api/v1/search?query=Microsoft&tags=story&hitsPerPage=1",
            ),
        ),
        ("object_id", "title_or_url"),
    ),
    _probe(
        "gdelt",
        "gdelt-microsoft",
        (
            _operation(
                "probe",
                "GET",
                "https://api.gdeltproject.org/api/v2/doc/doc"
                "?query=Microsoft&mode=ArtList&maxrecords=1&format=json",
            ),
        ),
        ("article_url", "title", "seen_date"),
    ),
    _probe(
        "patentsview",
        "patentsview-microsoft",
        (
            _operation(
                "probe",
                "GET",
                "https://api.patentsview.org/patents/query"
                "?q=%7B%22_contains%22%3A%7B%22assignee_organization%22%3A%22Microsoft%20"
                "Technology%20Licensing%2C%20LLC%22%7D%7D"
                "&f=%5B%22patent_number%22%2C%22patent_title%22%2C%22patent_date%22%2C"
                "%22assignee_organization%22%5D&o=%7B%22per_page%22%3A1%7D",
            ),
        ),
        ("patent_number", "patent_title", "patent_date", "assignee"),
    ),
    _probe(
        "wayback",
        "wayback-example",
        (
            _operation(
                "probe",
                "GET",
                "https://web.archive.org/cdx/search/cdx?url=example.com&output=json&limit=1",
            ),
        ),
        ("timestamp", "original_url", "status_code"),
    ),
    _probe(
        "google_news_rss",
        "google-news-microsoft",
        (
            _operation(
                "probe",
                "GET",
                "https://news.google.com/rss/search?q=Microsoft&hl=en-US&gl=US&ceid=US:en",
                ("application/rss+xml", "application/xml", "text/xml"),
            ),
        ),
        ("title", "link", "published_at"),
    ),
    _probe(
        "epo_open_patent",
        "epo-ops-microsoft-assignee",
        (
            _operation(
                "oauth",
                "POST",
                "https://ops.epo.org/3.2/auth/accesstoken",
                body=b"grant_type=client_credentials",
            ),
            _operation(
                "search",
                "GET",
                "https://ops.epo.org/3.2/rest-services/published-data/search/biblio"
                "?q=pa%3DMicrosoft&Range=1-1",
                ("application/exchange+xml", "application/xml", "text/xml"),
            ),
        ),
        (
            "applicant",
            "applicant_selector_match",
            "publication_country",
            "publication_doc_number",
            "publication_kind",
            "publication_number",
            "selector",
            "title",
        ),
    ),
    _probe(
        "semantic_scholar",
        "semantic-scholar-corpus-215416146",
        (
            _operation(
                "probe",
                "GET",
                "https://api.semanticscholar.org/graph/v1/paper/CorpusId:215416146"
                "?fields=paperId,title,year,authors,externalIds",
            ),
        ),
        (
            "attribution_required",
            "attribution_text",
            "authors",
            "external_ids",
            "paper_id",
            "selector_type",
            "title",
            "year",
        ),
    ),
    _probe(
        "perplexity",
        "perplexity-public-cited-company-query",
        (
            _operation(
                "probe",
                "POST",
                "https://api.perplexity.ai/chat/completions",
                body=_PERPLEXITY_BODY,
            ),
        ),
        ("citations", "supported_synthesis"),
        empty_tuple_fields=frozenset({"supported_synthesis"}),
    ),
)

if len({contract.provider_id for contract in _PROBE_CONTRACT_ROWS}) != len(_PROBE_CONTRACT_ROWS):
    raise RuntimeError("duplicate provider_id in provider probe contracts")

_PROBE_CONTRACT_BY_PROVIDER: Final[Mapping[str, ProviderProbeContract]] = MappingProxyType(
    {contract.provider_id: contract for contract in _PROBE_CONTRACT_ROWS}
)


def _credential(env_var: str, credential_key: str) -> CredentialField:
    return CredentialField(env_var=env_var, credential_key=credential_key)


def _spec(
    provider_id: str,
    *,
    rights_class: RightsClass,
    rights_requirement: RightsRequirement,
    credentials: tuple[CredentialField, ...] = (),
    non_secret_config: tuple[str, ...] = (),
    identity: tuple[str, ...],
    identity_alternatives: tuple[IdentitySelectorAlternative, ...] | None = None,
    fixture: str,
    analysis: tuple[str, ...],
    allowed_fact_domains: tuple[FactDomain, ...],
    request_builder_identity: str | None = None,
) -> ProviderSpec:
    probe_contract = _PROBE_CONTRACT_BY_PROVIDER.get(provider_id)
    if probe_contract is None or probe_contract.fixture_id != fixture:
        raise RuntimeError("provider catalog probe contract is missing or mismatched")
    return ProviderSpec(
        provider_id=provider_id,
        rights_class=rights_class,
        rights_requirement=rights_requirement,
        credential_fields=credentials,
        non_secret_config_env_vars=non_secret_config,
        identity_selector_alternatives=identity_alternatives
        or (
            IdentitySelectorAlternative(
                selector_type=IdentitySelectorType.VERIFIED_IDENTITY,
                required_fields=identity,
            ),
        ),
        probe_contract=probe_contract,
        runtime_lineage=ProviderRuntimeLineage(
            connector_identity=provider_id,
            connector_version_contract=f"{provider_id}-connector-v1",
            request_builder_identity=request_builder_identity
            or f"{provider_id}-request-builder-v1",
            request_builder_contract=request_builder_identity
            or f"{provider_id}-request-builder-v1",
        ),
        analysis_consumers=analysis,
        common_consumers=COMMON_EXTERNAL_CONTEXT_CONSUMERS,
        allowed_fact_domains=allowed_fact_domains,
        rag_external_context=True,
    )


_PROVIDER_SPECS = (
    _spec(
        "sec_edgar",
        rights_class=RightsClass.GREEN,
        rights_requirement=RightsRequirement.NONE,
        non_secret_config=("IDIS_EDGAR_USER_AGENT",),
        identity=("cik", "us_reporting_identity"),
        fixture="apple-cik-0000320193",
        analysis=(
            "ANALYSIS:financial_agent",
            "ANALYSIS:market_agent",
            "ANALYSIS:risk_officer_agent",
        ),
        allowed_fact_domains=_COMMERCIAL_AND_FINANCIAL,
    ),
    _spec(
        "companies_house",
        rights_class=RightsClass.GREEN,
        rights_requirement=RightsRequirement.NONE,
        credentials=(_credential("COMPANIES_HOUSE_API_KEY", "api_key"),),
        identity=("uk_company_number", "legal_name"),
        fixture="tesco-plc-search",
        analysis=(
            "ANALYSIS:financial_agent",
            "ANALYSIS:market_agent",
            "ANALYSIS:risk_officer_agent",
        ),
        allowed_fact_domains=_COMMERCIAL_AND_FINANCIAL,
    ),
    _spec(
        "github",
        rights_class=RightsClass.GREEN,
        rights_requirement=RightsRequirement.NONE,
        credentials=(_credential("GITHUB_API_TOKEN", "token"),),
        identity=("github_organization", "github_login"),
        fixture="github-org",
        analysis=(
            "ANALYSIS:technical_agent",
            "ANALYSIS:team_agent",
            "ANALYSIS:sector_specialist_agent",
        ),
        allowed_fact_domains=_COMMERCIAL_ONLY,
    ),
    _spec(
        "fred",
        rights_class=RightsClass.GREEN,
        rights_requirement=RightsRequirement.NONE,
        credentials=(_credential("FRED_API_KEY", "api_key"),),
        identity=("macro_series", "jurisdiction"),
        fixture="fred-gdp",
        analysis=(
            "ANALYSIS:financial_agent",
            "ANALYSIS:market_agent",
            "ANALYSIS:sector_specialist_agent",
        ),
        allowed_fact_domains=_COMMERCIAL_AND_FINANCIAL,
    ),
    _spec(
        "finnhub",
        rights_class=RightsClass.RED,
        rights_requirement=RightsRequirement.COMMERCIAL_PROD,
        credentials=(_credential("FINNHUB_API_KEY", "api_key"),),
        identity=("listed_ticker",),
        fixture="finnhub-aapl",
        analysis=(
            "ANALYSIS:financial_agent",
            "ANALYSIS:market_agent",
            "ANALYSIS:risk_officer_agent",
        ),
        allowed_fact_domains=_COMMERCIAL_AND_FINANCIAL,
    ),
    _spec(
        "fmp",
        rights_class=RightsClass.RED,
        rights_requirement=RightsRequirement.COMMERCIAL_PROD,
        credentials=(_credential("FMP_API_KEY", "api_key"),),
        identity=("listed_ticker",),
        fixture="fmp-aapl",
        analysis=(
            "ANALYSIS:financial_agent",
            "ANALYSIS:market_agent",
            "ANALYSIS:risk_officer_agent",
        ),
        allowed_fact_domains=_COMMERCIAL_AND_FINANCIAL,
    ),
    _spec(
        "world_bank",
        rights_class=RightsClass.GREEN,
        rights_requirement=RightsRequirement.NONE,
        identity=("iso_jurisdiction", "indicator"),
        fixture="world-bank-us-gdp",
        analysis=(
            "ANALYSIS:financial_agent",
            "ANALYSIS:market_agent",
            "ANALYSIS:sector_specialist_agent",
        ),
        allowed_fact_domains=_COMMERCIAL_AND_FINANCIAL,
    ),
    _spec(
        "escwa_catalog",
        rights_class=RightsClass.GREEN,
        rights_requirement=RightsRequirement.NONE,
        identity=("sector_term", "jurisdiction_term"),
        fixture="escwa-catalog-economy",
        analysis=("ANALYSIS:market_agent", "ANALYSIS:sector_specialist_agent"),
        allowed_fact_domains=_COMMERCIAL_ONLY,
    ),
    _spec(
        "escwa_ispar",
        rights_class=RightsClass.GREEN,
        rights_requirement=RightsRequirement.NONE,
        identity=("iso3_jurisdiction",),
        fixture="escwa-ispar-qat",
        analysis=(
            "ANALYSIS:financial_agent",
            "ANALYSIS:market_agent",
            "ANALYSIS:sector_specialist_agent",
        ),
        allowed_fact_domains=_COMMERCIAL_AND_FINANCIAL,
    ),
    _spec(
        "qatar_open_data",
        rights_class=RightsClass.GREEN,
        rights_requirement=RightsRequirement.NONE,
        identity=("qatar_jurisdiction", "sector_selector"),
        fixture="qatar-open-data-economy",
        analysis=("ANALYSIS:market_agent", "ANALYSIS:sector_specialist_agent"),
        allowed_fact_domains=_COMMERCIAL_ONLY,
    ),
    _spec(
        "hackernews",
        rights_class=RightsClass.GREEN,
        rights_requirement=RightsRequirement.NONE,
        identity=("legal_name", "trading_name"),
        fixture="hn-microsoft",
        analysis=(
            "ANALYSIS:market_agent",
            "ANALYSIS:sector_specialist_agent",
            "ANALYSIS:risk_officer_agent",
        ),
        allowed_fact_domains=_COMMERCIAL_ONLY,
    ),
    _spec(
        "gdelt",
        rights_class=RightsClass.GREEN,
        rights_requirement=RightsRequirement.NONE,
        identity=("company_name",),
        fixture="gdelt-microsoft",
        analysis=(
            "ANALYSIS:market_agent",
            "ANALYSIS:sector_specialist_agent",
            "ANALYSIS:risk_officer_agent",
        ),
        allowed_fact_domains=_COMMERCIAL_ONLY,
    ),
    _spec(
        "patentsview",
        rights_class=RightsClass.GREEN,
        rights_requirement=RightsRequirement.NONE,
        identity=("assignee",),
        identity_alternatives=(
            IdentitySelectorAlternative(
                IdentitySelectorType.VERIFIED_IDENTITY,
                ("assignee",),
            ),
            IdentitySelectorAlternative(
                IdentitySelectorType.VERIFIED_IDENTITY,
                ("legal_name",),
            ),
        ),
        fixture="patentsview-microsoft",
        analysis=("ANALYSIS:technical_agent", "ANALYSIS:sector_specialist_agent"),
        allowed_fact_domains=_COMMERCIAL_ONLY,
    ),
    _spec(
        "wayback",
        rights_class=RightsClass.YELLOW,
        rights_requirement=RightsRequirement.ATTRIBUTION_PROD,
        identity=("verified_domain",),
        fixture="wayback-example",
        analysis=(
            "ANALYSIS:historian_agent",
            "ANALYSIS:market_agent",
            "ANALYSIS:risk_officer_agent",
        ),
        allowed_fact_domains=_COMMERCIAL_ONLY,
    ),
    _spec(
        "google_news_rss",
        rights_class=RightsClass.YELLOW,
        rights_requirement=RightsRequirement.ATTRIBUTION_PROD,
        identity=("company_name",),
        fixture="google-news-microsoft",
        analysis=(
            "ANALYSIS:market_agent",
            "ANALYSIS:sector_specialist_agent",
            "ANALYSIS:risk_officer_agent",
        ),
        allowed_fact_domains=_COMMERCIAL_ONLY,
    ),
    _spec(
        "epo_open_patent",
        rights_class=RightsClass.GREEN,
        rights_requirement=RightsRequirement.NONE,
        credentials=(
            _credential("EPO_CONSUMER_KEY", "consumer_key"),
            _credential("EPO_CONSUMER_SECRET", "consumer_secret"),
        ),
        identity=("assignee",),
        identity_alternatives=(
            IdentitySelectorAlternative(
                IdentitySelectorType.VERIFIED_IDENTITY,
                ("assignee",),
            ),
            IdentitySelectorAlternative(
                IdentitySelectorType.VERIFIED_IDENTITY,
                ("legal_name",),
            ),
        ),
        fixture="epo-ops-microsoft-assignee",
        analysis=("ANALYSIS:technical_agent", "ANALYSIS:sector_specialist_agent"),
        allowed_fact_domains=_COMMERCIAL_ONLY,
        request_builder_identity="epo-open-patent-request-builder-v2",
    ),
    _spec(
        "semantic_scholar",
        rights_class=RightsClass.YELLOW,
        rights_requirement=RightsRequirement.ATTRIBUTION_PROD,
        credentials=(_credential("SEMANTIC_SCHOLAR_API_KEY", "api_key"),),
        identity=("semantic_scholar_paper_id",),
        identity_alternatives=(
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
        ),
        fixture="semantic-scholar-corpus-215416146",
        analysis=("ANALYSIS:technical_agent", "ANALYSIS:sector_specialist_agent"),
        allowed_fact_domains=_COMMERCIAL_ONLY,
        request_builder_identity="semantic-scholar-request-builder-v3",
    ),
    _spec(
        "perplexity",
        rights_class=RightsClass.RED,
        rights_requirement=RightsRequirement.COMMERCIAL_PROD,
        credentials=(_credential("PERPLEXITY_API_KEY", "api_key"),),
        identity=("verified_company", "market_selector"),
        fixture="perplexity-public-cited-company-query",
        analysis=("ANALYSIS:market_agent", "ANALYSIS:sector_specialist_agent"),
        allowed_fact_domains=_COMMERCIAL_ONLY,
        request_builder_identity=(
            "perplexity-request-builder-v2|sonar|temperature=0|verified-company|"
            "market-selector|one-public-company-fact-with-citations"
        ),
    ),
)

if len({spec.provider_id for spec in _PROVIDER_SPECS}) != len(_PROVIDER_SPECS):
    raise RuntimeError("duplicate provider_id in provider catalog")
if frozenset(spec.provider_id for spec in _PROVIDER_SPECS) != frozenset(
    _PROBE_CONTRACT_BY_PROVIDER
):
    raise RuntimeError("provider catalog and probe contracts must have exact closure")

PROVIDER_CATALOG: Final[Mapping[str, ProviderSpec]] = MappingProxyType(
    {spec.provider_id: spec for spec in _PROVIDER_SPECS}
)
PROVIDER_IDS: Final = frozenset(PROVIDER_CATALOG)
BUDGET_COMPONENT_IDS: Final = PROVIDER_IDS | {"anthropic", "openai_embeddings"}

RIGHTS_PROVIDER_IDS: Final[Mapping[RightsRequirement, frozenset[str]]] = MappingProxyType(
    {
        requirement: frozenset(
            provider_id
            for provider_id, spec in PROVIDER_CATALOG.items()
            if spec.rights_requirement is requirement
        )
        for requirement in RightsRequirement
    }
)
CREDENTIALED_PROVIDER_IDS: Final = frozenset(
    provider_id for provider_id, spec in PROVIDER_CATALOG.items() if spec.credential_fields
)
SECRET_ENV_VARS: Final = frozenset(
    field.env_var for spec in PROVIDER_CATALOG.values() for field in spec.credential_fields
)
NON_SECRET_CONFIG_ENV_VARS: Final = frozenset(
    env_var for spec in PROVIDER_CATALOG.values() for env_var in spec.non_secret_config_env_vars
)


def deliverable_consumers_for(provider_id: str, fact_domain: FactDomain | str) -> tuple[str, ...]:
    """Return the exact deliverable consumers for one authorized fact domain."""

    spec = PROVIDER_CATALOG.get(provider_id)
    try:
        domain = FactDomain(fact_domain)
    except (TypeError, ValueError) as exc:
        raise ProviderCatalogRoutingError("fact domain is not cataloged") from exc
    if spec is None or domain not in spec.allowed_fact_domains:
        raise ProviderCatalogRoutingError("provider is not authorized for fact domain")
    return DELIVERABLE_CONSUMERS_BY_FACT_DOMAIN[domain]

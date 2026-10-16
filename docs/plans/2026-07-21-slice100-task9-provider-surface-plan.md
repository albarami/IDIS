# Slice100 Task 9 Provider Surface Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task, with `test-driven-development` and `verification-before-completion` applied literally.

**Goal:** Build the truthful, hermetically tested Task 9 provider-periphery surface for all 18 enrichment providers and the three new production connectors without claiming runtime or Gate-A readiness.

**Architecture:** One immutable catalog under `idis.services.enrichment.task9` is the only authority for provider, credential, rights, probe, secret-free runtime-lineage, budget, and Layer-2-side routing metadata. `probe_contracts` consumes the exact catalog-owned probe objects rather than maintaining a second 18-row table. Probe execution uses a bounded HTTP transport and the same request-builder/normalizer methods used by the three new connectors; normalized outputs become typed future-persistence intents, never in-memory “durability.” Existing runtime integration and durable schema remain explicit `blocks-live` handoffs because this track may create new files only.

**Tech Stack:** Python 3.11, frozen dataclasses/`StrEnum`, Pydantic v2 where boundary parsing benefits, `httpx`, deterministic canonical JSON/SHA-256, `pytest`, Ruff, mypy.

---

## 1. Authorization, ownership, and stop rules

This plan implements the approved Track B boundary in the Slice100 master plan §1.33. It may create or modify only:

- `docs/plans/2026-07-21-slice100-task9-provider-surface-plan.md`
- `src/idis/services/enrichment/task9/**`
- `src/idis/services/enrichment/connectors/epo_open_patent.py`
- `src/idis/services/enrichment/connectors/semantic_scholar.py`
- `src/idis/services/enrichment/connectors/perplexity.py`
- `tests/slice100_task9_periphery/**`, including `tests/slice100_task9_periphery/fixtures/**`

Do not edit any existing registry, service, rights/governance importer or repository, provider-budget module, migration, dependency probe, API, orchestrator, gate, launcher, worker, RAG module, contract lock, or legacy test. Any need to do so is a stop-trigger and a plan amendment, not implied authority. Do not create a table, migration, environment variable, feature flag, prompt artifact, live receipt, acceptance namespace, or live/provider/model call. Do not commit until the reviewer approves the Track B checkpoint; this overrides the generic frequent-commit advice in `writing-plans`.

RED discipline is literal: write one behavior test, run it and observe the expected failure, implement only enough to turn it green, rerun, then refactor while green. RED tests must not be committed in an intentionally failing state. Hermetic observations carry `execution_mode=HERMETIC_TEST` and `acceptance_eligible=false`; they can never be serialized as Gate-A readiness.

## 2. Target package and public contracts

Create exactly these production modules:

- `src/idis/services/enrichment/task9/__init__.py`: narrow exports only.
- `provider_catalog.py`: immutable provider authority and derived inventories.
- `probe_contracts.py`: exact `provider-probes-v1` request/result contracts.
- `bounded_probe_transport.py`: HTTPS/same-host/one-attempt/size/deadline enforcement over an injected `httpx.Client`.
- `probe_runner.py`: connector-adapter execution and safe receipt intents; no persistence.
- `provider_admission.py`: pure identity → applicability → identifier → rights → credential → budget → cache ordering and call/outcome intents.
- `normalized_facts.py`: exact typed provider-outcome, normalized-fact, provenance, RAG, and consumer-routing persistence intents.
- `perplexity_evidence.py`: citation canonicalization/deduplication and support-only Sanad rules.
- The three connector files named in §1. Their probe and runtime entry points share one builder and one normalizer; tests inject `httpx.MockTransport` beneath the real bounded transport.

The catalog API is:

```python
PROVIDER_CATALOG_VERSION = "enrichment-provider-catalog-v1"

class RightsRequirement(StrEnum):
    NONE = "NONE"
    ATTRIBUTION_PROD = "ATTRIBUTION_PROD"
    COMMERCIAL_PROD = "COMMERCIAL_PROD"

class FactDomain(StrEnum):
    COMMERCIAL_DILIGENCE = "commercial_diligence"
    FINANCIAL_DILIGENCE = "financial_diligence"

class IdentitySelectorType(StrEnum):
    VERIFIED_IDENTITY = "verified_identity"
    SEMANTIC_SCHOLAR_PAPER = "semantic_scholar_paper"
    SEMANTIC_SCHOLAR_AUTHOR = "semantic_scholar_author"
    SEMANTIC_SCHOLAR_TOPIC = "semantic_scholar_topic"
    SEMANTIC_SCHOLAR_ORGANIZATION = "semantic_scholar_organization"

@dataclass(frozen=True, slots=True)
class CredentialField:
    env_var: str
    credential_key: str

@dataclass(frozen=True, slots=True)
class IdentitySelectorAlternative:
    selector_type: IdentitySelectorType
    required_fields: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class ProviderSpec:
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
    def required_identity_fields(self) -> tuple[str, ...]: ...  # derived ordered union only

PROVIDER_CATALOG: Mapping[str, ProviderSpec]
PROVIDER_IDS = frozenset(PROVIDER_CATALOG)
BUDGET_COMPONENT_IDS = PROVIDER_IDS | {"anthropic", "openai_embeddings"}
```

No count-bearing surface gets a separate literal list. Tests assert the exact ID set below, then prove probe, rights, credential, non-secret configuration, budget, and routing inventories derive from `PROVIDER_CATALOG`. The resulting counts are 18 providers, 20 budget components, eight credentialed providers, nine secret environment values, and one separately classified non-secret required setting (`IDIS_EDGAR_USER_AGENT`).

| Provider ID | Rights | Credential/config contract | Required verified selector | Analysis routing |
|---|---|---|---|---|
| `sec_edgar` | GREEN/NONE | non-secret `IDIS_EDGAR_USER_AGENT` | CIK/US reporting identity | financial, market, risk |
| `companies_house` | GREEN/NONE | `COMPANIES_HOUSE_API_KEY→api_key` | UK company number/legal name | financial, market, risk |
| `github` | GREEN/NONE | `GITHUB_API_TOKEN→token` | organization/login | technical, team, sector |
| `fred` | GREEN/NONE | `FRED_API_KEY→api_key` | governed macro series + jurisdiction | financial, market, sector |
| `finnhub` | RED/COMMERCIAL_PROD | `FINNHUB_API_KEY→api_key` | verified listed ticker | financial, market, risk |
| `fmp` | RED/COMMERCIAL_PROD | `FMP_API_KEY→api_key` | verified listed ticker | financial, market, risk |
| `world_bank` | GREEN/NONE | none | ISO jurisdiction/indicator | financial, market, sector |
| `escwa_catalog` | GREEN/NONE | none | sector/jurisdiction term | market, sector |
| `escwa_ispar` | GREEN/NONE | none | ISO3 jurisdiction | financial, market, sector |
| `qatar_open_data` | GREEN/NONE | none | verified Qatar/sector selector | market, sector |
| `hackernews` | GREEN/NONE | none | verified legal/trading name | market, sector, risk |
| `gdelt` | GREEN/NONE | none | verified company name | market, sector, risk |
| `patentsview` | GREEN/NONE | none | verified assignee **or** legal name | technical, sector |
| `wayback` | YELLOW/ATTRIBUTION_PROD | none | verified domain | historian, market, risk |
| `google_news_rss` | YELLOW/ATTRIBUTION_PROD | none | verified company name | market, sector, risk |
| `epo_open_patent` | GREEN/NONE | `EPO_CONSUMER_KEY→consumer_key`, `EPO_CONSUMER_SECRET→consumer_secret` atomically | assignee **or** legal name | `ANALYSIS:technical_agent`, `ANALYSIS:sector_specialist_agent` |
| `semantic_scholar` | YELLOW/ATTRIBUTION_PROD | `SEMANTIC_SCHOLAR_API_KEY→api_key` | paper ID **or** author ID **or** topic query + field **or** ROR ID + author ID | `ANALYSIS:technical_agent`, `ANALYSIS:sector_specialist_agent` |
| `perplexity` | RED/COMMERCIAL_PROD | `PERPLEXITY_API_KEY→api_key` | verified company/market selector | `ANALYSIS:market_agent`, `ANALYSIS:sector_specialist_agent` |

Canonicalize the shorthand legacy analysis names in the table to the eight existing `ANALYSIS:<agent_type>` IDs. Every provider also routes to both `RAG_EVIDENCE:external_context` and `GRAPH_EVIDENCE:external_context`, conflict-check execution, `SCORING`, `METHODOLOGY_LAYER2_READINESS_PACKAGE`, all three `LAYER2_IC_CHALLENGE:{ic_advocate,ic_challenger,ic_arbiter}` roles, and applicable `DELIVERABLES:<artifact-role>` values. The catalog must contain no Court, VEP, or five-role Layer-1 `DEBATE` consumer. Deliverable routing is resolved by `deliverable_consumers_for(provider_id, fact_domain)` from a closed two-domain map: one fact is classified as `commercial_diligence` or `financial_diligence`, never a provider-wide union; the result contains that one diligence artifact plus `risk_register`, `evidence_index`, and `provenance_appendix`. Each provider carries an explicit domain allowlist. Unknown providers/domains and disallowed pairs raise the stable `ENRICHMENT_CONSUMPTION_MISSING` code.

## 3. Exact `provider-probes-v1` inventory

Every contract has `timeout_seconds=15`, `max_attempts=1`, `max_response_bytes=2_097_152`, HTTPS only, automatic redirects disabled, and at most one manually validated redirect to the same approved host. Non-2xx, cross-host redirect, TLS/timeout error, response overflow, malformed content type/body, schema mismatch, or empty required result maps to `ENRICHMENT_PREFLIGHT_BLOCKED`. No exception text, raw body, credential, authorization header, or sensitive query value enters a receipt/error.

| ID | Exact operation / fixture | Required normalized result |
|---|---|---|
| `sec_edgar` | `GET data.sec.gov/submissions/CIK0000320193.json`; `apple-cik-0000320193` | CIK `0000320193`, name and filing metadata |
| `companies_house` | `GET api.company-information.service.gov.uk/search/companies?q=TESCO%20PLC`; `tesco-plc-search` | non-empty items with company number/name |
| `github` | `GET api.github.com/orgs/github`; `github-org` | login `github`, numeric ID |
| `fred` | `GET api.stlouisfed.org/fred/series/observations?series_id=GDP&file_type=json&limit=1`; `fred-gdp` | observation date/value |
| `finnhub` | `GET finnhub.io/api/v1/stock/profile2?symbol=AAPL`; `finnhub-aapl` | ticker `AAPL`, company name |
| `fmp` | `GET financialmodelingprep.com/api/v3/profile/AAPL`; `fmp-aapl` | first matching symbol `AAPL` |
| `world_bank` | `GET api.worldbank.org/v2/country/US/indicator/NY.GDP.MKTP.CD?format=json&per_page=1`; `world-bank-us-gdp` | metadata plus indicator row |
| `escwa_catalog` | `GET data.unescwa.org/api/3/action/package_search?q=economy&rows=1`; `escwa-catalog-economy` | success and dataset |
| `escwa_ispar` | `GET data.arabdevelopmentportal.org/datacatalog/un-agencies?sources=escwa&countries=qat`; `escwa-ispar-qat` | non-empty `datasets`/`results` input accepted by the existing connector normalizer for `qat`; observed 403 remains blocking |
| `qatar_open_data` | `GET www.data.gov.qa/api/explore/v2.1/catalog/datasets?where=search(default,%22economy%22)&limit=1`; `qatar-open-data-economy` | dataset row |
| `hackernews` | `GET hn.algolia.com/api/v1/search?query=Microsoft&tags=story&hitsPerPage=1`; `hn-microsoft` | hit with object ID and title/URL |
| `gdelt` | `GET api.gdeltproject.org/api/v2/doc/doc?query=Microsoft&mode=ArtList&maxrecords=1&format=json`; `gdelt-microsoft` | article URL/title/time |
| `patentsview` | `GET api.patentsview.org/patents/query` with the plan-pinned Microsoft assignee `q/f/o(per_page=1)`; `patentsview-microsoft` | patent number/title/date/assignee; retired 410 remains blocking |
| `wayback` | `GET web.archive.org/cdx/search/cdx?url=example.com&output=json&limit=1`; `wayback-example` | header plus snapshot row |
| `google_news_rss` | `GET news.google.com/rss/search?q=Microsoft&hl=en-US&gl=US&ceid=US:en`; `google-news-microsoft` | RSS item title/link/pubDate |
| `epo_open_patent` | OAuth `POST ops.epo.org/3.2/auth/accesstoken`, then `GET ops.epo.org/3.2/rest-services/published-data/search/biblio?q=pa%3DMicrosoft&Range=1-1`; `epo-ops-microsoft-assignee` | OPS publication/title/applicant XML |
| `semantic_scholar` | `GET api.semanticscholar.org/graph/v1/paper/CorpusId:215416146?fields=paperId,title,year,authors,externalIds`; `semantic-scholar-corpus-215416146` | paper ID/title plus attribution metadata |
| `perplexity` | `POST api.perplexity.ai/chat/completions`, code-pinned commercial model `sonar`, bounded public cited-company question; `perplexity-public-cited-company-query` | non-empty normalized citations; synthesis alone fails |

`ProviderProbeReceiptIntent` mirrors only the already planned fields: tenant/provider, contract version, fixture, request-builder/version hash, host/operation, safe request ID, start/end, HTTP status class, schema result, rights approval ID, credential version ID, response SHA-256, attempt count, and timeout. A wrapper—not a schema field—carries execution mode and acceptance eligibility.

Fixtures are sanitized, deterministic response-shape samples under `tests/slice100_task9_periphery/fixtures/`, with a manifest binding provider ID, fixture ID, media type, byte count, and SHA-256. EPO has separate OAuth JSON and search XML files; Google News uses XML; all others use JSON. Tests read these bytes through `httpx.MockTransport` and the actual connector normalization path; no test-only normalizer substitutes for production behavior.

### 3.1 Provider-real B4 correction contract

The B4 implementation and fixtures must model documented provider responses rather than minimal invented payloads. The bounded transport checks the deadline after response EOF as well as between yielded chunks, and each connector checks the same deadline again after normalization. The generic runner also retains the exact deadline passed to an adapter and checks it immediately after the adapter returns, before completion timestamps or evidence validation, so no adapter can clear after its shared 15-second deadline. Request-construction failures are mapped to the same secret-safe typed transport code as send failures. The runner validates request, clock, execution, operation, and hash values type-first, requires exact normalized-field closure for the provider contract, rejects duplicate/unknown/empty values, and independently derives the top response hash from the ordered operation evidence. The sole typed empty-value exception is Perplexity's `supported_synthesis=()`: the probe still requires non-empty governed citation evidence, while an empty tuple honestly records that every unsupported synthesis unit was dropped. `None`, empty strings, empty citations, and undeclared empty collections remain invalid. Connector-declared hashes remain lineage, not self-authenticating proof of response integrity.

EPO requires only the documented OAuth security subset: a trimmed, control-free, non-empty bearer `access_token`, `token_type=Bearer`, and a trimmed positive decimal string `expires_in`. Ignored provider metadata may vary, include empty `scope`, lists, and additional JSON-safe keys, but remains bounded by code constants: at most 65,536 response bytes, 32 top-level fields, 64 characters per key, 8,192 characters per string, 32 items per collection, and two collection levels. Non-finite numbers, excessive/deeper metadata, non-string keys, and control characters fail closed without echoing the token or metadata. The production search operation is amended to:

`GET https://ops.epo.org/3.2/rest-services/published-data/search/biblio?q=pa%3DMicrosoft&Range=1-1`

with `Accept: application/exchange+xml`. Only the pinned XML media types are accepted. The normalizer requires the complete `ops:world-patent-data -> ops:biblio-search -> ops:query/ops:range/ops:search-result -> exchange-documents` structure, a query semantically equal to the requested `pa=<selector>`, range `1..1`, and a result/count-consistent record set. A HIT has exactly one complete exchange document; a MISS is possible only when the validated response reports `total-result-count=0` and contains no exchange document. A root-only document, count contradiction, selector mismatch, or incomplete record fails closed.

The EPO publication identifier is derived from exactly one DOCDB `document-id`: `upper(country + doc_number + kind)`. The normalized result also retains `publication_country`, `publication_doc_number`, and `publication_kind`; the first bare `doc-number` is never treated as a global identifier. Applicant selection prefers the lowest-sequence `data-format=epodoc` value and title selection prefers English, with a deterministic language/order fallback. The response must retain a canonical applicant-to-selector match in addition to the echoed-query binding. Applicant binding is contiguous token-sequence containment after canonical tokenization; arbitrary substrings such as `soft` inside `Microsoft` never bind.

Semantic Scholar exposes a frozen discriminated selector union in the connector module. Strict Task-9 composition calls `fetch_selected`; legacy `fetch(EnrichmentRequest, ...)` never guesses a selector from `company_name`. All variants use one typed request-builder dispatcher and one typed normalizer dispatcher, shared by runtime and `run_probe`:

- `SemanticScholarPaperSelector(paper_id)`: `GET /graph/v1/paper/{paper_id}?fields=paperId,title,year,authors,externalIds`. The Gate-A probe uses `CorpusId:215416146`. S2 IDs bind to `paperId`; `CorpusId:n` binds to numeric `externalIds.CorpusId`; DOI/ACL/arXiv/PMID-style selectors bind to the corresponding canonical external ID.
- `SemanticScholarAuthorSelector(author_id)`: the identifier is a verified Semantic Scholar author ID, not a fuzzy name. It uses `GET /graph/v1/author/{author_id}/papers?offset=0&limit=1&fields=paperId,title,year,authors,externalIds`, and the returned paper must contain that exact author ID.
- `SemanticScholarTopicSelector(query, field_of_study)`: `field_of_study` belongs to the provider's closed field list. It uses `GET /graph/v1/paper/search?query=<encoded>&offset=0&limit=1&fields=paperId,title,year,authors,externalIds,s2FieldsOfStudy&fieldsOfStudy=<encoded>`. The selected paper must carry the requested field-of-study category, and provenance binds the canonical query hash.
- `SemanticScholarOrganizationSelector(ror_id, author_id)`: because the Graph API has no organization-search endpoint, an organization selector is valid only as the compound of a verified ROR ID and verified Semantic Scholar author ID. Under one shared deadline it first calls `GET /graph/v1/author/{author_id}?fields=name,affiliations,normalizedAffiliations`, requires exact ROR membership, then calls the bounded author-papers operation above and requires exact author membership.

All Semantic Scholar selector, query, and API-key values are trimmed and control-free before request construction. Paper IDs are limited to 512 characters and admit only a 40-hex S2 ID or the exact prefixes `CorpusId`, `DOI`, `ARXIV`, `MAG`, `ACL`, `PMID`, and `PMCID`; `CorpusId` must be positive. Topic queries are limited to 512 characters, author IDs to 32, ROR IDs to 64, fields of study to 64, and API keys to 4,096. Invalid values fail with `ENRICHMENT_PROVIDER_INPUT_MISSING` before transport.

Semantic Scholar paper normalization emits a common attribution-ready paper schema. `externalIds.CorpusId` is a positive integer; other admitted external IDs are non-empty strings and retain their value type in the canonical normalized representation. A HIT requires one selector-bound, complete paper. Author/topic terminal and zero envelopes may omit the documented optional `next`; when present it must be a valid forward offset, and count/offset contradictions remain invalid. Organization binding reads official `normalizedAffiliations[].rorId` plus `rorDisplayName`, never synthetic `id`/`name` keys. Search/list variants may return MISS only from a schema-valid HTTP-200 envelope with an empty `data` collection and a provider-consistent zero count; the organization variant additionally requires the author/ROR binding to have succeeded. Paper/author 404, ambiguity, selector mismatch, malformed/contradictory envelopes, or missing attribution metadata is a provider failure, not a MISS.

The catalog's authoritative Semantic Scholar identifier alternatives are exactly one of:

1. `semantic_scholar_paper_id`;
2. `semantic_scholar_author_id`;
3. the complete pair `semantic_scholar_topic_query` + `semantic_scholar_field_of_study`;
4. the complete pair `ror_id` + `semantic_scholar_author_id`.

Alternatives and compound groups are typed catalog authority, not connector-private literals, so admission can select one complete alternative and reject partial or mixed groups before rights, credentials, budget, cache, or transport.

## 4. Admission, normalized facts, and Perplexity evidence

`provider_admission.py` exposes frozen `VerifiedIdentitySnapshot`, `ApplicabilityDecision`, `RightsSnapshot`, `CredentialVersionSnapshot`, `BudgetReservationSnapshot`, `ProviderCallIntent`, and `ProviderOutcomePersistenceIntent`. It evaluates exactly: verified identity/version → immutable applicability → required identifier → PROD rights/entitlement → exact encrypted credential version → active signed budget reservation → rights-compatible cache decision → call intent. Collaborators are injected protocols so tests assert call order.

For positively verified `PRIVATE`/`UNLISTED` companies, Finnhub/FMP may return identity/rule-bound `NOT_APPLICABLE` before rights. That branch proves zero rights lookup, credential access, budget/cache access, transport, fact, RAG, or consumer intent. Missing ticker, `UNKNOWN`, operator seed, deal UUID, wrong identity version, or applicable/listed provider with revoked rights cannot become N/A. EPO's two credential fields are one versioned bundle; a partial bundle fails before transport.

`normalized_facts.py` emits typed intents only; it writes nothing. A `HIT` requires at least one deterministic fact with tenant/deal/run/provider/outcome scope, normalized subject/predicate/value hash, retrieved time, identifiers-used hash, response hash, safe source reference, rights/credential/builder lineage, and exact provider request ID. A real `MISS` and verified N/A produce outcome intents but no facts. Errors/partial/empty/provenance-less results cannot look terminal. Every persistable fact also produces an external-context RAG intent when rights allow and exact catalog-derived consumer-routing intents; no Layer-1 intent is constructible.

`perplexity_evidence.py` canonicalizes HTTPS citation URLs, rejects user-info/fragments/invalid hosts, hashes and deduplicates canonical citation records, and labels each as `SourceTier.MAQBUL`, base Sanad grade `C`, `SUPPORT_ONLY`, `primary_eligible=false`. Perplexity answer prose is never a normalized primary fact. Retained synthesis is a separate intent requiring non-empty citation IDs for every retained factual unit; unsupported prose is dropped. An answer, answer hash, request ID, or provider invocation without citation evidence fails No-Free-Facts and cannot satisfy probe, persistence, RAG, or consumer completion.

## 5. RED → GREEN execution tasks

Use this declared interpreter for every command:

```powershell
Set-Location -LiteralPath 'C:\Projects\IDIS\IDIS-slice100'
$Python = 'C:\Projects\IDIS\IDIS\.venv\Scripts\python.exe'
$env:PYTHONPATH = 'C:\Projects\IDIS\IDIS-slice100\src'
```

### Task B1: Catalog closure and derived inventories

**Files:** Create `task9/__init__.py`, `task9/provider_catalog.py`, `tests/slice100_task9_periphery/__init__.py`, and `test_provider_catalog_red.py`.

1. Write tests for the exact 18 rows (fixtures, identity selectors, credential key mappings, analysis routes, fact domains), 20 derived budget IDs, rights filters, nine secret env values plus the separately derived SEC non-secret setting, EPO atomic pair, immutable catalog backing, closed fact-domain deliverable routing, common Graph/RAG/conflict/scoring/readiness/Layer-2 consumers, and zero Layer-1 routes.
2. Run `& $Python -m pytest tests/slice100_task9_periphery/test_provider_catalog_red.py -q`; expect import/missing-catalog failure.
3. Implement the immutable catalog and derivation functions; no connector construction or ambient env reads.
4. Rerun the command; expect all green.

### Task B2: Probe contracts, fixtures, and bounded transport

**Files:** Create `task9/probe_contracts.py`, `task9/bounded_probe_transport.py`, fixture manifest/response files, `test_probe_contracts_red.py`, and `test_bounded_probe_transport_red.py`.

1. RED-test exact closure with the catalog, all operations/fixtures/results, fixture hashes, 15/1/2,097,152 bounds, HTTPS, same-host-only redirect, overflow-by-one, timeout/TLS/non-2xx/malformed/schema-empty failures, and secret-safe errors.
2. Run both test files; expect missing contract/transport failures, not fixture syntax errors.
3. Implement canonical request/response models and streaming bounded transport over injected `httpx.Client`; no test transport in production code.
4. Rerun both files; expect green.

### Task B3: Truthful legacy actual-path characterization

**Files:** Create `test_legacy_probe_actual_path_red.py`; extend only new `task9/probe_contracts.py` with a typed blocker inventory.

1. Drive all 15 existing connectors through injected `httpx.MockTransport`, capture their actual outbound request, and compare it to probe v1. Assert five exact matches and ten `blocks-live` paths: Companies House `items_per_page=10`; FRED `limit=20` plus sort versus 1; World Bank `per_page=10`; ESCWA Catalog `rows=10`; ESCWA ISPAR's correctly-associated external 403 despite an aligned request shape; Qatar `limit=10`; HN `hitsPerPage=10`; GDELT casing plus `maxrecords=10`; PatentsView `per_page=10` plus retired-410 endpoint; Wayback `limit=10`.
2. First run must fail because no typed characterization exists. Implement only the blocker records and observation comparator; do not copy alternate “production” builders.
3. Rerun green. A drift disappearing unexpectedly fails until the runtime integration diff is reviewed; a blocker row never counts as readiness.

### Task B4: EPO and Semantic Scholar connectors through the real probe path

**Files:** Create both connector modules, `task9/probe_runner.py`, `test_epo_open_patent_connector_red.py`, `test_semantic_scholar_connector_red.py`, and `test_probe_runner_red.py`.

1. RED-test exact EPO OAuth→search order under one 15-second deadline, Basic/Bearer secret non-leakage, complete two-field credentials, XML schema/HIT/MISS/failure; RED-test exact Semantic URL/fields/header, attribution metadata, JSON schema/HIT/MISS/failure.
2. RED-test that connector `fetch` and `run_probe` invoke the same builder/normalizer identities, one attempt only, fixture result non-empty, safe receipt fields exact, hermetic output ineligible, and unknown/unconnected connectors cannot appear ready.
3. Run the three files and observe missing classes. Implement minimal connectors/runner using the bounded transport and existing enrichment models.
4. Rerun green; also run existing connector tests listed in §7.

### Task B5: Perplexity citations-only connector

**Files:** Create the connector, `task9/perplexity_evidence.py`, `test_perplexity_evidence_red.py`, and `test_perplexity_connector_red.py`.

1. RED-test pinned `sonar` request, the compound verified-company + market selector in request bytes/hash/provenance, legacy one-field fail-closed behavior, bearer secrecy, exact bound, citations required, URL canonicalization/dedup/order, malicious/empty citation rejection, MAQBUL/C/support-only/non-primary labels, prose never becoming a fact, supported-synthesis citation linkage, mixed supported/unsupported sentence rejection, and unsupported synthesis dropped.
2. Run both files; expect missing implementation failures.
3. Implement the citation parser first, then the connector using that parser for both probe and runtime results.
4. Rerun green and verify the fixture reaches the same actual builder/transport/normalizer path.

### Task B6: Ordered admission and typed persistence intents

**Files:** Create `task9/provider_admission.py`, `task9/normalized_facts.py`, `test_provider_admission_red.py`, and `test_normalized_facts_red.py`.

1. RED-test exact collaborator order; PROD-only rights; governed YELLOW/RED scopes; encrypted-version metadata; budget before call; EPO atomic credentials; identity-bound Finnhub/FMP N/A with zero downstream calls; and every listed false-N/A adversary in the master plan.
2. RED-test HIT/MISS/N/A intent semantics, deterministic IDs/hashes, provenance completeness, fact non-discard, RAG-rights behavior, exact consumer derivation, unknown fact-domain failure, and Layer-1 construction rejection. Include Perplexity citation facts and supported synthesis.
3. Run both files; observe missing-type failures. Implement pure functions and injected protocols only—no repositories, SQL, cache, audit, or HTTP.
4. Rerun green, then run all Track B tests.

## 6. Known `blocks-live` handoffs outside Track B ownership

These are mandatory before live acceptance but must not be “fixed” or hidden in this new-files-only track:

1. The ten blocked legacy paths in Task B3: nine reviewed request-shape drifts, the correctly-associated live ISPAR 403, and the PatentsView retired-410 condition (PatentsView carries both request and external blockers).
2. Existing `EnrichmentService` evaluates rights before verified identity/applicability, uses in-memory cache/audit-compatible paths, and does not durably preserve all facts.
3. Existing runtime registry has 15 connectors and insufficient identity, rights, probe, budget, and routing metadata; it is not composed from this new catalog.
4. Governance import/migration `0033` constraints enumerate four governed providers and do not admit Semantic Scholar/Perplexity.
5. BYOL bootstrap supports five single-variable credentials and cannot atomically version EPO key+secret or the other new providers.
6. Provider budget policy literals contain 17 components, not the catalog-derived 20.
7. No authorized durable `provider_probe_receipts`, `enrichment_provider_outcomes`, or `enrichment_normalized_facts` tables/repositories exist. Future design requires tenant/run/provider-scoped immutable outcome roots and deterministic fact roots, but this track adds no schema.
8. No enrichment normalized-fact RAG projector or durable consumer-receipt integration exists. Task 7 owns receipt/edge schema and Task 10 owns consumption closure; this track supplies routing intents only.
9. Dependency probes, Gate-A checklist, launcher/governance import, worker/orchestrator, and final verifier do not consume the new catalog.

A checkpoint may be green for this bounded periphery while every item above remains explicitly `blocks-live`; it may not call Task 9, Gate A, or enrichment end-to-end complete.

## 7. Checkpoint verification and materiality

Run in a clean declared environment with strict-profile/provider secrets absent and verify imports resolve to this worktree:

```powershell
& $Python -c "import idis; print(idis.__file__)"
& $Python -m pytest tests/slice100_task9_periphery -q
& $Python -m pytest tests/test_enrichment_connectors_registry.py tests/test_enrichment_registry_fail_closed.py tests/test_enrichment_rights_gate.py tests/test_enrichment_service_orchestration.py tests/test_enrichment_connector_edgar.py tests/test_enrichment_connector_finnhub.py tests/test_enrichment_connector_fmp.py -q
& $Python -m pytest -q
& $Python -m ruff format --check src/idis/services/enrichment/task9 src/idis/services/enrichment/connectors/epo_open_patent.py src/idis/services/enrichment/connectors/semantic_scholar.py src/idis/services/enrichment/connectors/perplexity.py tests/slice100_task9_periphery
& $Python -m ruff check src/idis/services/enrichment/task9 src/idis/services/enrichment/connectors/epo_open_patent.py src/idis/services/enrichment/connectors/semantic_scholar.py src/idis/services/enrichment/connectors/perplexity.py tests/slice100_task9_periphery
& $Python -m mypy src/idis/services/enrichment/task9 src/idis/services/enrichment/connectors/epo_open_patent.py src/idis/services/enrichment/connectors/semantic_scholar.py src/idis/services/enrichment/connectors/perplexity.py --no-incremental
& $Python scripts/contract_lock.py verify --repo-root .
& $Python scripts/forbidden_scan.py --repo-root .
git diff --check
```

Inspect the shared `git status --short`, but prove scope with a path-scoped Track-B status/diff over the exact §1 allowlist. Track A's master-plan and Task-7 design files coexist in this worktree and must be acknowledged rather than attributed to Track B. Grep only the Track-B diff for live calls, migrations/tables, Task-7 schema names, new `IDIS_*` settings, registry/gate/worker/orchestrator edits, test skips/xfails, and fake readiness. Produce SHA-256 for every Track-B changed file plus a deterministic sorted path/hash manifest, exact pass/skip/fail counts, and the review/materiality table. Audit suggestions never silently expand scope.

Stop uncommitted for independent Track B review. Only after explicit approval may these files receive their own atomic conventional commits, separate from Track A and any later integration/schema work.

## 8. Plan self-review

- Exact Track-B allowed paths only; unrelated Track-A/master-plan changes may coexist and are excluded by path-scoped proof rather than a whole-worktree cleanliness claim.
- All 18 providers and exact probe fixtures are enumerated; every derived surface starts from one catalog.
- Eighteen provider components plus `anthropic` and `openai_embeddings` derive 20 budget rows.
- The ten blocked legacy paths—nine request drifts plus the correctly-associated ISPAR external blocker, with PatentsView also externally retired—are observable blockers, not intentionally failing final tests or fake green probes.
- New connectors share the actual builder/transport/normalizer path under hermetic HTTP interception; no live call is authorized.
- Perplexity citations—not answer prose—become low-tier support evidence, preserving No-Free-Facts.
- Rights/applicability order, N/A zero-side-effect proof, EPO atomic credentials, and external-context-only routing are explicit.
- Persistence remains typed intent/design only; no migration, repository, receipt/edge schema, or durability claim is introduced.
- Separate RED/GREEN commands, full verification, materiality classification, hashes, review stop, and no-commit rule are explicit.

## 9. Binding B6 audit-closure amendment

This section supersedes conflicting shorthand above and is binding for the Track-B checkpoint.

- Verified identity is `verified-company-identity-v1`: evidence and provenance IDs are non-empty unique sets with domain-separated canonical set hashes; the complete identity hash binds tenant, deal, identity/version/rule, listing state, verification basis, identifiers, both ID sets, and both set hashes. Call, cache-hit, N/A, outcome, and fact-provenance intents retain those IDs and hashes. Finnhub/FMP N/A permits no ticker only after that positively evidenced `PRIVATE`/`UNLISTED` state is bound; a contradictory `listed_ticker`, unknown/seed basis, bad version, or noncanonical lineage fails before rights.
- Budget snapshots bind tenant/deal/run/provider and cache decisions bind tenant/deal/run/provider/catalog/identity/identifier/rights/credential scope. Every collaborator exception is translated at its stage to a constant payload-free code with suppressed chaining. Noncommercial and attribution rights rows carry no credential-version link; provider credential lineage remains separately mandatory where the catalog requires it.
- Admission binds the catalog-owned connector and request-builder identities and version hashes. `ProviderExecutionOperationEvidence` is ephemeral: each ordered operation carries safe canonical request bytes and response bytes whose SHA-256 values are independently recomputed; top request/response hashes are rederived from the ordered operation hashes, and only hashes enter persistence intents. Wiring this proof from all 15 legacy production connectors remains a blocks-live runtime-integration requirement; the three new connectors and hermetic probe runner prove their bounded byte path without claiming live readiness.
- Persistable source references use a closed safe form: canonical public HTTPS URL or bounded opaque public reference. User-info, fragments, local paths, control characters, and credential/token/signature query parameters, including AWS signed-query fields, fail `ENRICHMENT_PROVENANCE_MISSING` and never enter fact, RAG, Graph, or provenance intents.
- Normalized fact content advances to `external-context-fact-v2`, binding citation ID and every Sanad/source-tier/usage/primary-eligibility field. Cache HIT revalidates those fields, the citation URL/ID relation, retrieval time, complete provenance, fact content, and source outcome before emitting fresh current-run intents. A cached Perplexity citation cannot be elevated from `MAQBUL/C/SUPPORT_ONLY/non-primary` without invalidating the fact.
- When rights prohibit external-context indexing, both RAG and Graph external-context intents and edges are absent. When rights permit it, the catalog derives both routes plus the approved analysis, conflict, scoring, readiness, Layer-2, and deliverable consumers; Layer 1 remains impossible.
- Cross-version cache reuse after a renewed rights approval or a distinct but semantically equal applicability-decision ID is deliberately unsupported at this checkpoint. Exact-lineage reuse is fail-closed and otherwise degrades to a provider call/cache miss; a future reviewed compatibility-proof contract may broaden it. This is recorded hardening, not permission to treat an incompatible cache entry as success.

## 9. Binding audit-correction amendment

This section supersedes any conflicting wording above and is binding for the Track-B checkpoint.

- `PROVIDER_CATALOG` is the single immutable authority for probe operations, fixture IDs, exact normalized-field closure, typed empty-field exceptions, bounds, and the common `ENRICHMENT_PREFLIGHT_BLOCKED` code. `probe_contracts.PROBE_CONTRACTS` consumes the exact nested catalog objects by identity and contains no second hand-authored 18-row table.
- Each provider row owns a frozen `ProviderRuntimeLineage`: exact connector identity, connector semantic-version contract plus derived SHA-256, and request-builder identity/contract plus derived SHA-256. These are deterministic lineage labels, not source-code-attestation claims. EPO and Semantic Scholar use their connector-declared builder identities; Perplexity uses its compound-selector builder-v2 label; the 15 legacy rows use provider-derived v1 labels until runtime integration exposes and proves the same values.
- Every provider routes external context to both `RAG_EVIDENCE:external_context` and `GRAPH_EVIDENCE:external_context`, as well as the already listed Layer-2-side consumers. Neither route authorizes Layer 1 to consume enrichment.
- The reviewed live HTTP 403 belongs to `escwa_ispar`, not `escwa_catalog`. ISPAR's fixture and validator use the existing connector's real `datasets`/`results` normalizer. Legacy characterization keeps the Catalog `rows=10` drift separate and records ISPAR as a zero-query-delta external blocker. The truthful result is five aligned and ten blocked legacy paths: nine request-shape drifts, plus ISPAR's external 403; PatentsView has both request drift and retired-410 blockers.
- Semantic Scholar author/topic terminal and zero envelopes may omit the documented optional `next`. When present, `next` must be a valid forward offset; empty data with a non-null next, non-zero topic total, or another count/offset contradiction remains invalid. `normalizedAffiliations` uses official `rorId`/`rorDisplayName`; synthetic `id`/`name` rows are rejected. Official-shaped hermetic fixtures cover terminal HIT, zero MISS, ROR binding, and contradictions.
- Unexpected connector/request-builder exceptions are wrapped by `ProbeRunner` as payload-free `ENRICHMENT_PREFLIGHT_BLOCKED:connector_execution_failed`. Known `ProbeConnectorError` and `ProbeTransportFailure` retain their typed reason. Before parsing/building, the bounded transport type-checks URL, approved host, method, media-type tuple, deadline, and monotonic values so malformed collaborators cannot escape as raw Python exceptions or echo marker values.
- Perplexity runtime selection is the conjunctive typed pair `verified_company + market_selector`. Both values bind canonical request bytes, therefore the request-content hash, and provenance. The legacy one-field `fetch` path fails before transport. Gate-A's fixture selector is `Microsoft Corporation + software`, using the same request builder. A synthesis sentence is retained only when all citation markers form one contiguous suffix; mixed prose such as `Supported [1], unsupported.` is conservatively dropped in full.
- Hermetic fixtures flow through actual connector normalizers. They do not prove live provider status, and the ISPAR 403 remains `blocks-live` until Gate A.
- The earlier whole-worktree `git status` scope sentence is superseded. Inspect shared status for awareness, but prove Track-B scope with a path-scoped status/diff over the exact section-1 allowlist. Track A's master-plan and Task-7 design files coexist in this worktree and must be acknowledged, not attributed to Track B. Hash manifests likewise include only Track-B-owned paths, while the forbidden-surface scan proves Track B did not edit migrations, Task-7 schema, runtime registry/gate/worker/orchestrator files, or add environment/flag surfaces.

## 10. Uncommitted Track-B checkpoint evidence — 2026-07-21

This is a bounded periphery checkpoint, not Task 9, Gate A, or live-enrichment completion. The source files remain uncommitted pending review.

The final independent audit found and RED-reproduced three in-scope defects before approval: a self-consistent connector builder hash could differ from the catalog-pinned lineage; Perplexity's direct probe and runtime paths omitted the post-normalization deadline check; and generic source references admitted private, noncanonical, or credential-bearing HTTPS values. The REDs failed on the old bytes with exactly those behaviors. The implementation now:

- compares the connector and execution builder hash to `PROVIDER_CATALOG[provider_id].runtime_lineage.request_builder_version_hash`;
- enforces the shared deadline after Perplexity normalization on both direct probe and runtime fetch paths; and
- requires a source URL to equal the canonical public-HTTPS result, rejecting private hosts, noncanonical paths/hosts/query order, `key`, `AWSAccessKeyId`, `X-Amz-*`, and the previously covered secret/signature fields through `materialize_provider_intents`.

The same independent auditor re-ran the former exploits on the fixed bytes and returned `APPROVE` with no residual in-scope blocker. No live call was made.

Declared-environment verification on the fixed source bytes, with the strict profile, database URLs, and provider/model credentials absent:

- Track-B suite: `518 passed, 0 failed, 0 skipped`;
- legacy enrichment regression set from section 7: `89 passed, 0 failed, 0 skipped`;
- Ruff format/check: clean; mypy: clean for all 11 Track-B source files; contract lock, forbidden scan, and `git diff --check`: clean;
- full repository: `8,501 passed, 490 skipped, 1 failed` of `8,992` in 621.18 seconds.

The scanner amendment was subsequently approved. RED produced exactly two failures: the real
offline scan omitted Perplexity, and a decoy connector constant displaced the two-provider test
catalog. `scripts/audit_full_system_wiring.py::_discover_provider_ids` now statically parses the
literal `_PROVIDER_SPECS` tuple with Python AST, imports/executes no provider code, rejects an
invalid catalog, and ignores connector constants. The legacy test compares the full result to
`PROVIDER_IDS` and derives its count. A complete test-tree sweep also removed every applicable
provider/budget count literal: active legacy counts derive from exact membership sets, Task-2
budget row counts derive from `EXPECTED_BUDGET_COMPONENT_IDS`, and legacy probe coverage derives
from its exact case partition. This does not wire the production policy to 20 components; that
remains the section-6 blocks-live runtime integration.

On the implementation bytes, the scanner test passed 5/5, the directly affected set passed
244/244, and the full declared-environment tree passed 8,503 with 490 skipped and zero failures
from 8,993 collected. The scanner exception changed no provider/runtime/gate/worker/orchestrator,
migration, environment, or durable schema surface.

All section-6 integration handoffs remain `blocks-live`; none was silently reclassified. There is still no runtime-registry composition, governance migration change, credential bootstrap change, durable fact/probe schema, RAG/Graph projector integration, orchestrator/gate/worker edit, live provider call, commit, push, or PR in this track.

A fresh bounded follow-up audit found one final provider-schema type hole inside the owned Semantic Scholar connector: JSON list/search envelopes with `offset: 0.0` compared equal to integer zero and were accepted as valid terminal MISS responses. RED reproduced both author and topic variants (`2 failed`), after which the connector began requiring an exact non-boolean integer offset. The regression cases passed `2/2`, the complete Semantic Scholar connector suite passed `65/65`, and the complete Track-B suite passed `520/520`. This correction changes no request, provider, runtime-composition, persistence, environment, or live-call surface.

The next explicitly approved bounded audit correction completed cached normalized-fact provenance revalidation before any current-run replacement occurs. RED mutated all 20 formerly omitted identity/rule, applicability, budget, cache, admission, catalog, and cache-ancestry fields: the original 16 mutations failed `16/16`, then the four ancestry mutations failed `4/20` while the first 16 were green. A second RED proved six malformed, partial, or mismatched re-cache ancestry shapes were accepted. The minimal implementation now compares every source provenance field to the signed source outcome, requires fresh-source ancestry to be empty, and requires re-cached outcome/fact ancestry to be complete UUIDv5/SHA-256 pairs consistent with the source outcome and fact content. The 26 adversarial cases passed, a positive cache-of-cache lineage case passed, the normalized-facts suite passed `138/138`, and the complete Track-B suite passed `547/547`; the legacy enrichment regression set remained `89/89`. No repository, cache-store, persistence, runtime-composition, environment, or live-call behavior changed.

The final two approved bounded audit gaps were closed RED-first. Two transport tests proved that an injected collaborator could raise a raw non-HTTPX exception during request send or streamed-body iteration, leaking past the common error boundary (`2/2` failed). Both paths now map unexpected `Exception` values to payload-free `ENRICHMENT_PREFLIGHT_BLOCKED:transport_error`; the complete transport suite passed `27/27`. Eight runner tests proved that opaque objects, non-finite floats, mutable byte/list/mapping values, unordered sets, and nested opaque values were accepted as normalized fields (`8/8` failed). An exact-type follow-up then proved three subclass bypasses: a hostile string subclass escaped as a raw exception while tuple/int subclasses were accepted (`3/11` failed with the original eight green). `ProbeRunner` now accepts only exact non-empty canonical immutable scalar/tuple values plus independently reconstructed, exact Perplexity citation/supported-synthesis record types; the runner plus real typed Perplexity connector path passed `75/75`. Declared-environment regression then passed the complete Track-B suite `560/560` and legacy enrichment set `89/89`. Ruff format/check passed all 23 owned Python files, mypy passed all 11 owned source files, and contract lock, forbidden scan, and `git diff --check` were clean. This correction added no provider call, schema, repository, environment/flag surface, runtime composition, commit, push, or PR.

The next incomplete plan-specified B6 unit was Perplexity cache-HIT synthesis preservation. A schema RED first proved that `CachedProviderOutcomePayload` had no synthesis field (`1/1` failed). After adding the required typed field, the real Perplexity citation-plus-supported-synthesis source bundle proved that cache reuse silently reduced one valid synthesis intent to zero (`1/1` failed). Fourteen missing/corrupt synthesis mutations plus one non-Perplexity injection were then all accepted (`15/15` failed). Cache payload validation now requires the exact source outcome-bound synthesis-ID set; revalidates scope, content hash, unique citations, source fact links, deterministic ID, and fixed `MAQBUL/C/SUPPORT_ONLY/non-primary` labels; and rematerializes a fresh current-run synthesis intent linked to the fresh current-run citation fact. The focused contracts passed `17/17`, the complete normalized-facts suite passed `154/154`, the complete Track-B suite passed `576/576`, and the legacy enrichment regression remained `89/89`. This is a typed intent correction only: no durable schema/repository, provider call, environment/flag, runtime composition, commit, push, or PR was added.

A final independent secret-boundary review found that hostile `str` and `tuple` subclasses could
escape cached-synthesis validation and expose their exception payload. Three exact RED cases
reproduced both synthesis-field paths and the top-level synthesis collection (`3/3` failed with
the private marker in a raw `RuntimeError`). The cache boundary now admits only the exact frozen
synthesis record and exact built-in string/tuple field types before any string operation,
iteration, set construction, or sorting. Malformed synthesis fields terminate as the stable,
payload-free `ENRICHMENT_PROVENANCE_MISSING:cached_synthesis_invalid`; a malformed top-level
synthesis tuple terminates as `ENRICHMENT_PROVENANCE_MISSING:cached_outcome_invalid`. The three
regressions passed `3/3`; the complete normalized-facts suite passed `157/157`; the complete
Track-B suite passed `579/579`; and the legacy enrichment regression set passed `89/89`. Ruff
format/check passed all 23 owned Python files, mypy passed all 11 owned source files, and contract
lock, forbidden scan, and `git diff --check` were clean. The full repository was not rerun for
this bounded follow-up and is not claimed on these final bytes. No durable schema/repository,
provider call, environment/flag, runtime composition, commit, push, or PR was added.

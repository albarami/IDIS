# IDIS Data Layer Architecture v3.1

_API Selection • Licensing • Integration Strategy • BYOL Model_

_February 2026 • Confidential_

# Executive Summary

This document defines the complete data layer for IDIS, organized in two stages that reflect the licensing reality of financial data APIs: a development and prototype phase using personal-use tiers, followed by a commercial production phase with properly licensed data sources.

Financial market data providers (Finnhub, FMP, EODHD) and corporate data aggregators (OpenCorporates) offer generous personal-use tiers for development, but explicitly require commercial licenses for production SaaS deployment. This architecture respects that boundary.

| ARCHITECTURE OVERVIEW | ARCHITECTURE OVERVIEW |
| --- | --- |
| Phase 1 — Development & Prototype | 21 APIs  •  $0/month  •  Personal-use tiers for building + testing |
| Phase 2A — Commercial Launch Pack | Upgrade 3 APIs to commercial tiers + optionally add a licensed market data feed (EODHD) at launch |
| Phase 2B — Premium Stack (BYOL-First) | 9 APIs  •  5 BYOL + 4 IDIS-funded  •  Enterprise data layer |
| Total Platform | 31 APIs  •  No redundancy in the same role  •  Licensing verified per source |


# Design Principles

1. No Redundancy (Same Role):  If two APIs serve the same role, only the stronger one is selected. Overlap is permitted only for validation (cross-checks) or different time horizons (e.g., real-time vs historical).

2. Unique Capability:  Every API creates a gap if removed. If removing it changes nothing in IDIS output, it has no place in the architecture.

3. Dollar Density:  Each paid API is evaluated by unique data per dollar. Every dollar must unlock a capability that nothing else can provide.

4. Licensing Honesty:  Every API’s terms are verified. Phase 1 uses personal-use tiers for development only. No personal-use API is deployed in client-facing production without upgrading to the appropriate commercial license. The Data Rights Matrix in this document tracks each source’s production status.

5. Compounding Cache:  Every external API call is cached and normalized into IDIS’s internal data layer. Over time, the internal dataset becomes the primary asset and cost per analysis trends toward zero.

## Caching & Refresh Policy (Defaults)

# Data Rights & Licensing Matrix

This matrix classifies every Phase 1 API by its production readiness. It is the operational enforcement of Principle 4 (Licensing Honesty). No API moves from development to production without clearing this matrix.

✅ GREEN = Free tier explicitly permits commercial/production use.

⚠️ YELLOW = Usable but with ToS ambiguity, stability risk, or specific compliance requirements.

🛑 RED = Free/cheap tier is personal use only. Requires commercial license upgrade before production deployment.

Last licensing review: 06 Feb 2026. Re-verify YELLOW/RED sources quarterly.

| API Source | Status | Terms Basis | Action Required |
| --- | --- | --- | --- |
| SEC EDGAR | ✅ GREEN | Public US government API. Must declare User-Agent header per access policy. | Production-ready. Add User-Agent compliance. |
| Companies House | ✅ GREEN | UK government official API. Free with auth key. No commercial restrictions. | Production-ready. |
| GDELT Project | ✅ GREEN | Explicitly states datasets available for “unlimited and unrestricted” use including commercial. | Production-ready. |
| GitHub API | ✅ GREEN | Official API with documented rate limits (5,000 req/hr authenticated). Standard ToS. | Production-ready. |
| HackerNews API | ✅ GREEN | Official Firebase-based API. Publicly documented. No commercial restrictions. | Production-ready. |
| FRED API | ✅ GREEN | US Federal Reserve public data. Free API key. Standard government data terms. | Production-ready. |
| World Bank API | ✅ GREEN | International organization open data. Creative Commons Attribution 4.0. | Production-ready. |
| ESCWA Data Catalog | ✅ GREEN | UN agency open data portal. Standard UN open data terms. | Production-ready. |
| ESCWA ISPAR | ✅ GREEN | UN agency tool. Public access. | Production-ready. |
| Qatar Open Data | ✅ GREEN | Government open data portal. Opendatasoft platform. | Production-ready. |
| USPTO Open Data | ✅ GREEN | US government patent data. Public domain. | Production-ready. |
| EPO Open Patent | ✅ GREEN | European Patent Office. Open Patent Services with registered access. | Production-ready. Register for API access. |
| Supabase | ✅ GREEN | Standard SaaS terms. Free tier permits commercial use. | Production-ready. |
| Upstash Redis | ✅ GREEN | Standard SaaS terms. Free tier permits commercial use. | Production-ready. |
| Cloudflare Workers | ✅ GREEN | Standard SaaS terms. Free tier permits commercial use. | Production-ready. |
| Wayback Machine | ⚠️ YELLOW | Internet Archive terms apply. API is public but subject to access policies and rate limiting. | Usable with caution. Respect rate limits. Not mission-critical. |
| Google News RSS | ⚠️ YELLOW | RSS feeds are public but Google’s ToS have broad language. No explicit commercial license. | Use for development. At production, replace or supplement with GDELT event feeds. |
| Google Trends (pytrends) | ⚠️ YELLOW | pytrends is an unofficial scraper, not a Google API. Subject to blocking and ToS risk. | Use for development and internal analysis only. Do not build client-facing features on pytrends. |
| Finnhub Free | 🛑 RED | Free plan is personal use. Commercial use + redistribution requires startup/enterprise license. | Phase 2A: upgrade to Finnhub commercial license. |
| Financial Modeling Prep Free | 🛑 RED | Display/redistribution requires separate Data Display & Licensing Agreement. Enterprise tier. | Phase 2A: upgrade to FMP commercial license. |
| OpenCorporates Free | 🛑 RED | Free keys are share-alike for Permitted Users only. Financial institutions and corporations excluded. Commercial use requires paid non-share-alike key. | Phase 2A: purchase non-share-alike API key. |


## Deployment Guardrails (Production Gating)

- Environment gating: DEV may use GREEN/YELLOW/RED. PROD may use GREEN and approved YELLOW only.

- Build-time enforcement: CI fails if any RED adapter is enabled in production configuration.

- Data lineage: every normalized field carries source_id and rights_class (GREEN/YELLOW/RED).

- UI gating: client-facing features render only fields permitted for client display.

- Compliance cadence: re-verify YELLOW/RED terms quarterly and record the verification date.

# 1. Entity & Company Intelligence

Every deal anchors to a verified legal entity. The entity spine is the foundation that every other data source maps to.

| Role | API | What It Provides | Why It’s Selected |
| --- | --- | --- | --- |
| Entity Spine | OpenCorporates  🛑 | 200M+ companies globally. Directors, filings, legal status, jurisdiction, incorporation dates. | Broadest global entity verification source. Anchors cross-API entity resolution. Requires paid non-share-alike key for commercial production. |
| UK Filing Depth | Companies House  ✅ | UK company financials, confirmation statements, officer appointment histories, charges, persons of significant control. | UK-specific depth not available through OpenCorporates: full financial statements and officer histories. Government API, commercially safe. |
| US Public Filings | SEC EDGAR  ✅ | All US public company filings: 10-K, 10-Q, 8-K, S-1, 13-F. Real-time XBRL data. | Direct government source. Must declare User-Agent header per SEC access policy. Commercially safe. |
| Engineering Quality | GitHub API  ✅ | 5,000 req/hr. Repositories, commits, contributors, PR velocity, language breakdown. | Only way to verify a startup’s technical claims. Commit frequency and contributor diversity are hard-to-fake signals. |


# 2. Financial Data & Market Comps

Two complementary APIs with minimal functional overlap: Finnhub for breadth and real-time signals, and Financial Modeling Prep for long historical depth. Both require commercial upgrades before client-facing production.

| Role | API | What It Provides | Why It’s Selected |
| --- | --- | --- | --- |
| Breadth + Signals | Finnhub.io  🛑 | 60 calls/min free. Real-time quotes, financial statements, ESG scores, patent data, insider transactions, social sentiment, news with sentiment. | One API combining market data, ESG, IP intelligence, insider activity, and news sentiment. Free tier is personal use; commercial license required at production. |
| Historical Depth | Financial Modeling Prep  🛑 | 250 req/day free. 30 years of income statements, balance sheets, cash flow. Financial ratios, DCF valuations, stock screener. | 30 years of depth for valuation modeling. Free tier is personal use; display/redistribution requires commercial Data Display & Licensing Agreement. |


# 3. Macroeconomic Context

Both sources are government/international organization open data. Commercially safe at any stage.

| Role | API | What It Provides | Why It’s Selected |
| --- | --- | --- | --- |
| US + Global Macro | FRED API  ✅ | 800,000+ economic time series. Interest rates, GDP, CPI, employment, money supply, trade balance. | Consolidates BLS, US Treasury, and OECD data in one source. Government API, commercially safe. |
| International / EM | World Bank API  ✅ | 200+ countries. GDP, trade, development indicators, human capital index. | Definitive non-US macro context. CC BY 4.0 license. Commercially safe. |


# 4. MENA/GCC Regional Intelligence

IDIS’s competitive moat. All three sources are UN or government open data. Commercially safe at any stage. No Western VC platform integrates this data.

| Role | API | What It Provides | Why It’s Selected |
| --- | --- | --- | --- |
| Arab Statistics | ESCWA Data Catalog  ✅ | Banking, labor, trade, demography, education, SDG tracking across all 22 Arab states. | Definitive Arab region data. UN open data terms. Commercially safe. |
| Policy Indices | ESCWA ISPAR  ✅ | AI Readiness, E-Government, Digital Accessibility, Doing Business, Innovation indices. Policy simulation. | No other source provides Arab-state policy readiness indices with simulation. Commercially safe. |
| Qatar Local | Qatar Open Data  ✅ | Official Qatar government data: employment, economic indicators, demographics, trade statistics. | Home market intelligence. Government open data. Commercially safe. |


🎯 MOAT:  PitchBook, CB Insights, and Carta have zero Arab-region data. This layer is commercially safe from day one and makes IDIS the only viable platform for MENA-focused funds.

# 5. Alternative Signals & Verification

Three signal sources with varying production readiness. HackerNews is GREEN. Wayback and pytrends are YELLOW — usable for development and internal analysis but should not be sole dependencies in client-facing features.

| Role | API | What It Provides | Why It’s Selected |
| --- | --- | --- | --- |
| Traction Validation | Google Trends (pytrends)  ⚠️ | Search interest over time, geographic breakdown, related queries, rising topics. | Only free method to validate brand traction at population scale. YELLOW: pytrends is an unofficial scraper. Use for internal analysis; do not build client-facing features solely on this. |
| Tech Sentiment | HackerNews API  ✅ | Stories, comments, user profiles. Free, unlimited, real-time. | Highest signal-to-noise tech community source. Official API. Commercially safe. |
| Claim Verification | Wayback Machine API  ⚠️ | Historical website snapshots. Check any URL at any date. | Verify founding claims, catch pivot history. YELLOW: subject to Internet Archive access policies. Not mission-critical dependency. |


# 6. News & Event Intelligence

GDELT is the strongest commercially safe news source. Google News RSS is usable for development but has ToS ambiguity that should be noted.

| Role | API | What It Provides | Why It’s Selected |
| --- | --- | --- | --- |
| Global Events | GDELT Project  ✅ | News from virtually every country in 100+ languages. Event detection, tone analysis, entity extraction. Explicitly open for any use. | The only truly open, commercially safe global news source at scale. Batchable into the IDIS data lake. Production-ready. |
| Company Monitoring | Google News RSS  ⚠️ | Free RSS feeds by company name or keyword. Real-time headlines. No API key. | Real-time pipeline monitoring. YELLOW: RSS is public but Google’s broad ToS create ambiguity. Supplement with GDELT for production safety. |


NOTE:  Finnhub also provides company-specific news with sentiment. IDIS has three news layers: GDELT (structured events), RSS (real-time alerts), Finnhub (financial sentiment).

# 7. IP & Patent Intelligence

Both sources are government patent databases. Commercially safe at any stage.

| Role | API | What It Provides | Why It’s Selected |
| --- | --- | --- | --- |
| US Patents | USPTO Open Data  ✅ | US patent filings, trademark registrations, patent assignments. Government source. | Primary jurisdiction for VC-backed IP. Public domain. Commercially safe. |
| International | EPO Open Patent Services  ✅ | 100M+ patent documents. European + international PCT applications. | Global IP landscape outside the US. Commercially safe with registered access. |


# 8. Infrastructure: The Compounding Engine

All three services have standard SaaS free tiers that permit commercial use. Infrastructure is commercially safe from day one.

| Role | API | What It Provides | Why It’s Selected |
| --- | --- | --- | --- |
| Data Lake + Auth | Supabase  ✅ | PostgreSQL + auth + object storage + real-time + vector embeddings. 500MB DB, 1GB storage free. | Normalized data lake. Vector embeddings for semantic search. Multi-tenant auth. Commercially safe. |
| Speed Layer | Upstash Redis  ✅ | Serverless Redis. 10K commands/day, 256MB free. Sub-millisecond reads. | Hot data cache. Makes 18 external APIs feel instant. Commercially safe. |
| Edge Gateway | Cloudflare Workers  ✅ | 100K req/day free. Edge compute + KV storage + R2 object storage. | API routing, cache-first logic, rate-limit protection. Commercially safe. |


# Phase 1: Complete Reference

| # | API | Layer | Rights | Role & Production Status |
| --- | --- | --- | --- | --- |
| 1 | OpenCorporates | Entity | 🛑 | Entity spine. Paid non-share-alike key needed for production. |
| 2 | Companies House | Entity | ✅ | UK filing depth. Government API. Production-ready. |
| 3 | SEC EDGAR | Entity | ✅ | US public filings. Must declare User-Agent. Production-ready. |
| 4 | GitHub API | Entity | ✅ | Engineering quality signals. Production-ready. |
| 5 | Finnhub.io | Financial | 🛑 | Breadth: quotes + ESG + patents + insider + sentiment. Commercial license needed. |
| 6 | Financial Modeling Prep | Financial | 🛑 | Depth: 30yr statements, DCF. Commercial license needed. |
| 7 | FRED API | Macro | ✅ | US/global macro. Government API. Production-ready. |
| 8 | World Bank API | Macro | ✅ | International 200+ countries. CC BY 4.0. Production-ready. |
| 9 | ESCWA Data Catalog | MENA ★ | ✅ | Arab statistics across 22 states. Production-ready. |
| 10 | ESCWA ISPAR | MENA ★ | ✅ | Arab policy indices + simulation. Production-ready. |
| 11 | Qatar Open Data | MENA ★ | ✅ | Qatar government data. Production-ready. |
| 12 | Google Trends | Signal | ⚠️ | Traction validation. Unofficial scraper — internal use only. |
| 13 | HackerNews API | Signal | ✅ | Tech sentiment. Official API. Production-ready. |
| 14 | Wayback Machine | Signal | ⚠️ | Claim verification. Usable with caution. |
| 15 | GDELT Project | News | ✅ | Global events. Explicitly unrestricted. Production-ready. |
| 16 | Google News RSS | News | ⚠️ | Company monitoring. ToS ambiguity — supplement with GDELT. |
| 17 | USPTO Open Data | IP | ✅ | US patents. Government source. Production-ready. |
| 18 | EPO Open Patent | IP | ✅ | European + international patents. Production-ready. |
| 19 | Supabase | Infra | ✅ | Data lake + auth + vector. Production-ready. |
| 20 | Upstash Redis | Infra | ✅ | Speed cache. Production-ready. |
| 21 | Cloudflare Workers | Infra | ✅ | Edge gateway + rate limiting. Production-ready. |


Production scorecard:  15 GREEN (production-ready)  •  3 YELLOW (usable with caution)  •  3 RED (upgrade at launch)

PHASE 2A

Commercial Licensing Upgrades

Trigger: before IDIS serves its first paying client

Phase 2A upgrades the three RED Phase 1 APIs to commercial licenses and, optionally, activates a licensed market data feed (EODHD) depending on whether market data is used internally only or displayed/redistributed to clients.

| API | Personal Tier | Commercial Tier | What Changes |
| --- | --- | --- | --- |
| Finnhub | Free (60 calls/min) | Startup/Enterprise (contact sales) | Grants commercial use + redistribution rights. Higher rate limits. Access to premium endpoints (company profile, etc.). Pricing is flexible for startups. |
| Financial Modeling Prep | Free (250 req/day) | Enterprise (contact sales) | Grants Data Display & Licensing Agreement for redistribution. Higher call limits. Required for displaying FMP-sourced data to IDIS clients. |
| EODHD (optional add) | N/A (not a Phase 1 dependency) | Internal Use $399/mo or Enterprise $2,499/mo | Add only if market data volume/coverage is required. Internal Use permits internal analytics only; Enterprise is required for client display/redistribution. |
| OpenCorporates | Free share-alike key | Paid non-share-alike key (contact sales) | Free keys require share-alike and exclude corporations. Paid key removes share-alike restriction and permits use in commercial/proprietary applications. |


## Phase 2A Cost Estimate

Exact pricing for Finnhub, FMP, and OpenCorporates commercial tiers requires contacting their sales teams. Based on published information and comparable startup-stage pricing:

| PHASE 2A ESTIMATED ANNUAL COST | PHASE 2A ESTIMATED ANNUAL COST |
| --- | --- |
| Core licensing upgrades (Finnhub + FMP + OpenCorporates) | $5,200–$26,000/yr (est.) |
| Optional: EODHD Internal Use (internal analytics only) | +$4,788/yr |
| Optional: EODHD Enterprise (client redistribution) | +$29,988/yr |
| Total with EODHD Internal | $9,988–$30,788/yr (est.) |
| Total with EODHD Enterprise | $35,188–$55,988/yr (est.) |


NOTE:  These are estimates. Contact each provider’s sales team for exact pricing. Finnhub and FMP both market startup-friendly commercial tiers. Negotiate before launch.

PHASE 2B

Premium Stack

9 APIs  •  BYOL-first model  •  Enterprise-grade data layer

# The BYOL Model (Bring Your Own License)

Most institutional VCs already pay for PitchBook, Capital IQ, LinkedIn Sales Navigator, Affinity, and Carta. IDIS builds connectors. Clients plug in their credentials. IDIS adds intelligence on top.

BYOL Connectors (5 APIs):  Client enters their credentials. IDIS provides integration and intelligence. Zero data cost to IDIS.

IDIS-Funded APIs (4 APIs):  Capabilities no client brings: web traffic validation, MENA startup coverage, compliance screening, entity resolution.

Native Enrichment (Always):  Phase 1’s 21 APIs (commercially licensed in 2A) + infrastructure run for every client at near-zero marginal cost.

## BYOL Security & Tenant Isolation

- Per-tenant credentials: API keys/tokens stored encrypted at rest and scoped to a single client.

- Least privilege: read-only scopes wherever possible; no write access unless required.

- Isolation: no cross-tenant caching or data blending for BYOL sources.

- Rotation & revocation: support client-driven token rotation and immediate revocation.

Capability:  4.8M company profiles, 450K+ investor records, funding terms, valuations, cap tables, M&A transactions.

Rationale:  Institutional standard for private market intelligence. No substitute at this depth.

### 2. Dealroom  —  $10–25K/yr  —  IDIS-funded

Access path:  Self-serve. Published pricing on website (USD). API access included in subscription.

Capability:  European + MENA startup coverage. Growth metrics, funding data, market maps.

Rationale:  Fills PitchBook’s MENA and European early-stage gaps. Critical for IDIS regional positioning.

### 3. S&P Capital IQ  —  $25–60K/yr  —  BYOL

Access path:  Contract-only. Enterprise sales process. Clients with existing Capital IQ seats may need to add API access as a contract amendment.

Capability:  Institutional-grade financials, consensus estimates, M&A comps, credit ratings, supply chain mapping.

Rationale:  Institutional depth beyond Finnhub/FMP/EODHD: credit analysis, M&A comps, supply chain.

### 4. SimilarWeb  —  $14–35K/yr  —  IDIS-funded

Access path:  Sales-assisted. Contact sales for API pricing. Published packages exist but API access is typically custom.

Capability:  Website traffic volumes, engagement metrics, traffic sources, app downloads, competitive positioning.

Rationale:  Google Trends shows direction; SimilarWeb provides absolute numbers. Powers the IDIS Truth Dashboard.

### 5. LinkedIn Sales Navigator  —  $10–20K/yr  —  BYOL

Access path:  Partner-approved. LinkedIn API access requires partner program approval for most data extraction permissions. Sales Navigator subscriptions alone do not grant API access. Plan for partner application timeline.

Capability:  Headcount trends, hiring velocity, employee backgrounds, organizational structure.

Rationale:  Hiring velocity is the strongest leading indicator of startup trajectory.

### 6. ComplyAdvantage  —  $5–15K/yr  —  IDIS-funded

Access path:  Self-serve. Published starter pricing (~$99/mo). API-first product with clear commercial terms.

Capability:  AML/KYC screening: sanctions lists, PEP checks, adverse media, risk scoring.

Rationale:  Regulated fund structures legally require AML/KYC screening. Non-negotiable for institutional clients.

### 7. Affinity CRM  —  $10–20K/yr  —  BYOL

Access path:  Self-serve. Well-documented public API. Standard OAuth integration.

Capability:  Relationship intelligence CRM: deal pipeline, contact management, relationship scoring.

Rationale:  Without CRM integration, analysts must manually input deals, killing adoption.

### 8. Diffbot  —  $5–10K/yr  —  IDIS-funded

Access path:  Self-serve. Published pricing starting at $299/mo for Startup plan. Clear commercial API terms.

Capability:  Knowledge graph with 10B+ entities. Entity extraction, relationship mapping, web crawling.

Rationale:  Connective tissue for entity resolution across all sources. Critical at scale.

### 9. Carta  —  $5–15K/yr  —  BYOL

Access path:  Partner/API program. Carta offers Issuer, Investor, Portfolio, and Launch APIs. Access and permissions may depend on partnership agreements and specific use cases.

Capability:  Cap table data: equity ownership, 409A valuations, fund administration, waterfall analysis.

Rationale:  Direct cap table verification from the source of truth for startup ownership.

# Phase 2B: Complete Reference

| # | API | Cost | Paid By | Access | Role | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | PitchBook | $20–70K/yr | BYOL | Contract | Private Markets | Client brings license. API is separate contract. |
| 2 | Dealroom | $10–25K/yr | IDIS | Self-serve | MENA/Europe | IDIS funds. Published pricing. |
| 3 | S&P Capital IQ | $25–60K/yr | BYOL | Contract | Financial Depth | Client brings. API may need contract amendment. |
| 4 | SimilarWeb | $14–35K/yr | IDIS | Sales | Traction Proof | IDIS funds. Contact sales for API. |
| 5 | LinkedIn Sales Nav | $10–20K/yr | BYOL | Partner | People Intel | Client brings. API requires partner approval. |
| 6 | ComplyAdvantage | $5–15K/yr | IDIS | Self-serve | Compliance | IDIS funds. Published starter pricing. |
| 7 | Affinity CRM | $10–20K/yr | BYOL | Self-serve | Workflow Hub | Client brings. Well-documented API. |
| 8 | Diffbot | $5–10K/yr | IDIS | Self-serve | Knowledge Graph | IDIS funds. Published $299/mo startup. |
| 9 | Carta | $5–15K/yr | BYOL | Partner | Cap Tables | Client brings. API program access varies. |


| TOTAL COST STRUCTURE | TOTAL COST STRUCTURE |
| --- | --- |
| Phase 1 (development) | $0 |
| Phase 2A (commercial licensing upgrades) | $10–31K/yr (est.) |
| Phase 2B BYOL (client pays): PitchBook + CapIQ + LinkedIn + Affinity + Carta | $0 to IDIS |
| Phase 2B IDIS-funded: Dealroom + SimilarWeb + ComplyAdvantage + Diffbot | $34–85K/yr |
| TOTAL ANNUAL COST TO IDIS AT FULL SCALE | $44–116K/yr |


# Integration Roadmap

| When | APIs | Cost | Milestone |
| --- | --- | --- | --- |
| Week 1–2 | Supabase + Upstash Redis + Cloudflare Workers + SEC EDGAR + Companies House | $0 | Infrastructure + commercially-safe entity sources |
| Week 3–4 | Finnhub (personal) + FMP (personal) + FRED + World Bank | $0 | Financial data + macro context (personal tiers for dev) |
| Week 5–6 | ESCWA Data Catalog + ESCWA ISPAR + Qatar Open Data | $0 | MENA moat layer (commercially safe from day one) |
| Week 7–8 | GitHub + HackerNews + Wayback + Google Trends + OpenCorporates (share-alike) | $0 | Signals + entity spine (dev tiers) |
| Week 9–10 | GDELT + Google News RSS + USPTO + EPO | $0 | News + IP = Phase 1 complete |
| Pre-Launch | Phase 2A: Finnhub commercial + FMP commercial + EODHD $399/mo + OpenCorporates paid | ~$10–31K/yr | Commercial license upgrades before first paying client |
| Post-Revenue | BYOL connectors: PitchBook, Capital IQ, LinkedIn, Affinity, Carta | $0 to IDIS | Build connectors. Factor in partner approval timelines. |
| Post-Revenue | IDIS-funded: ComplyAdvantage + Diffbot (self-serve, immediate) | ~$5–10K/yr | First IDIS-funded APIs. Published pricing, fast onboarding. |
| Post-Revenue | IDIS-funded: Dealroom + SimilarWeb (sales-assisted) | ~$24–60K/yr | Larger commitments. Negotiate after revenue traction. |


| Layer | Examples | Default TTL | Refresh Trigger / Notes |
| --- | --- | --- | --- |
| Entity/Registry | OpenCorporates, Companies House | 30 days | Refresh on filing change or manual trigger |
| Filings | SEC EDGAR | 24 hours | New filing detection |
| Financial Markets | Finnhub, FMP | 5–60 min (quotes); 24h (fundamentals) | Per endpoint and license |
| Macro | FRED, World Bank | Daily–Monthly | Publisher cadence |
| MENA/GCC | ESCWA, ISPAR, Qatar Open Data | Monthly–Quarterly | Publisher cadence |
| Signals | GitHub, HN | Daily | Activity-based |
| News/Events | GDELT, RSS | 6–12 hours | Event ingestion batches |
| IP/Patents | USPTO, EPO | Monthly | New filings |

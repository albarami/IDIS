# IDIS API Phased Integration Plan v3.1

_Derived from IDIS Data Layer Architecture v3.1_

This document extracts the **phased API / connector integration plan** and the **Phase‑1 licensing matrix** into a standalone reference.

## Phase‑1 API Source Licensing Matrix (21 sources)

| API Source | Status | Terms Basis | Action Required |
|---|---|---|---|
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

## Phased integration timeline

| When | APIs / Integrations | Cost Range | Notes / Milestone |
|---|---|---|---|
| Week 1–2 | Supabase + Upstash Redis + Cloudflare Workers + SEC EDGAR + Companies House | $0 | Infrastructure + commercially-safe entity sources |
| Week 3–4 | Finnhub (personal) + FMP (personal) + FRED + World Bank | $0 | Financial data + macro context (personal tiers for dev) |
| Week 5–6 | ESCWA Data Catalog + ESCWA ISPAR + Qatar Open Data | $0 | MENA moat layer (commercially safe from day one) |
| Week 7–8 | GitHub + HackerNews + Wayback + Google Trends + OpenCorporates (share-alike) | $0 | Signals + entity spine (dev tiers) |
| Week 9–10 | GDELT + Google News RSS + USPTO + EPO | $0 | News + IP = Phase 1 complete |
| Pre-Launch | Phase 2A: Finnhub commercial + FMP commercial + EODHD $399/mo + OpenCorporates paid | ~$10–31K/yr | Commercial license upgrades before first paying client |
| Post-Revenue | BYOL connectors: PitchBook, Capital IQ, LinkedIn, Affinity, Carta | $0 to IDIS | Build connectors. Factor in partner approval timelines. |
| Post-Revenue | IDIS-funded: ComplyAdvantage + Diffbot (self-serve, immediate) | ~$5–10K/yr | First IDIS-funded APIs. Published pricing, fast onboarding. |
| Post-Revenue | IDIS-funded: Dealroom + SimilarWeb (sales-assisted) | ~$24–60K/yr | Larger commitments. Negotiate after revenue traction. |

## Phase mapping guidance

- **GREEN sources**: safe to ship in Phase‑1 as-is (minimal licensing risk).
- **YELLOW sources**: can be shipped with clear attribution/retention rules and a license review gate.
- **RED sources**: defer until Phase‑2A commercial upgrades or implement as **BYOL** in Phase‑2B.

## Notes

- Status labels are **licensing / compliance** gates, not technical difficulty gates.
- Where a provider’s terms restrict caching or redistribution, enforce via the shared connector caching policy (TTL + “no-store” modes).

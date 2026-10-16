# IDIS implementation and go-live master plan

Updated 2026-10-16. This is the authoritative delivery scope and proposal register, not permission to implement, spend, activate or deploy. Current authority is in [RUN_STATE](RUN_STATE.md); baseline identity is in [BASELINE_MANIFEST](BASELINE_MANIFEST.md).

## 1. Product, people and definition of done

Deliver the existing IDIS architecture to VC funds using their own methodologies. The owner is the product owner, decision-maker and operator. AI agents perform bounded planning, implementation, verification and independent technical review where supported. There is no assumed hired engineering team, reviewer, consultant or 24-hour support staff. Owner/fund subject-matter validation remains necessary; AI cannot approve itself into factual correctness.

Four outputs share the same reviewed findings:

1. Completed response workbook: every original financial sheet/row and each child obligation linked to its answer; preserve the original workbook separately.
2. Narrative financial, market and commercial report: findings, material risks, competing sources, assumptions, unknowns and required follow-up.
3. Evidence/calculation annex: accessible authorized source locators, input coordinates, reproducible rules/results and support links.
4. IDIS IC memo: same reviewed findings, qualifications, scorecard and challenge outcomes; not a separate untraceable synthesis.

This package is the proposed output format pending fund confirmation. No actual final fund report template was established by the previous inspection. If one is provided or located, register its version and map destinations without discarding the four deliverable capabilities.

Final completion requires the entire financial/commercial scope, market intelligence, trust and debate layers, governed investigation, basic correction/reuse, qualified role configuration, full canonical execution, fund acceptance and operational recovery. Later adaptive routing, autonomous repetition within delegated limits and learning from validated corrections remain tracked obligations; an earlier fund release is explicitly an **interim milestone**, not full modernization completion.

## 2. Evidence status and preserved architecture

Prior assessments found substantial foundations, not evidence for replacing the system. Task1–7 ingestion, methodology selection, governed extraction and durable receipt work supersede a GitHub-main-only assertion that these capabilities do not exist. They do not establish full analytical quality. Current code and local amendments are newer than the historical runs.

The May22 retained run used real input files but mocked/deterministic model behavior; it did not demonstrate satisfactory answers to the two pilot questions. A later ingestion record reports 265 parsed files and two exceptions, not a complete current diligence execution. These are historical report claims, not fresh execution evidence or expert gold answers. Prior service-access failures/empty stores are not a current health check. Service/data contents remain unverified for implementation admission until an authorized preflight.

```text
Fund methodology/version + private source package
  -> governed intake / parsing / OCR / extraction / task coverage
  -> internal claims + evidence + Sanad + deterministic calculations / Calc-Sanad
  -> graph/RAG + conflicts -> Layer 1 Evidence Trust Court -> VEP
  -> separate five-role FULL DEBATE
  -> Layer 2 specialists -> methodology/stage scoring -> readiness -> three-role IC challenge
  -> analyst review / corrections -> fund workbook + narrative + annex + IC memo

Verified identity -> rights/budget/applicability -> external market APIs -> persisted external facts
  ------------------------------------------------> specialists / scoring / IC / deliverables
  X no promotion into internal Court, VEP or FULL Layer-1 debate

Material uncertainty -> proposed bounded investigation -> permitted execution -> new governed evidence
  -> affected calculations / trust / analysis / review rerun -> versioned revised deliverables
```

| Component retained | Actual substrate / substantive payload to complete | Work and consumers |
|---|---|---|
| Intake and fund methodology | `src/idis/methodology/importers/fdd_excel.py`, `src/idis/methodology/importers/cdd_json.py`, `src/idis/methodology/models.py`; source traces, version/hash, raw wording, expected answer, calculations and report mapping | GL-02/03/06; planner/extractor must preserve disjoint formula policy shapes and child obligations |
| Task planning / extraction | `src/idis/services/extraction/task_planner.py`, `src/idis/services/extraction/methodology_task_extractor.py`, `src/idis/services/runs/methodology_extraction_task_execution.py` | Questions and source passages/tables to typed observations, spans and coverage; preserve prepare/admit/dispatch/terminal/finalize ordering |
| Sanad and contradictions | `src/idis/services/runs/methodology_sanad_creation_linking_grading.py`, `src/idis/services/runs/methodology_postgres_materialization_runtime.py` | Source identity, independence, chain, grade, defects, conflict observations, coordinates and uncertainty—not an unexplained confidence number; Court and specialists consume them |
| Deterministic calculations / Calc-Sanad | `src/idis/calc/engine.py`, `src/idis/services/calc/runner.py`, `src/idis/services/runs/methodology_deterministic_calculation.py` | Governed operands, formulas, adjustments and results with lineage; preserve all calculation IDs across later stages |
| Court / VEP | `src/idis/services/runs/methodology_evidence_trust_court.py`, `src/idis/services/runs/methodology_validated_evidence_package.py` | Actual internal support, decisions and unresolved defects to debate and readiness; IDs-only or empty shells are insufficient |
| Separate FULL DEBATE | `src/idis/debate/`, `src/idis/api/routes/runs.py` | Five roles receive source-bearing internal findings, calculations and conflicts; findings/challenges persist separately from Court and IC |
| Market intelligence | `src/idis/services/enrichment/service.py`, `src/idis/services/enrichment/task9/` | Verified company identity, applicability, rights, freshness, normalized external facts and source references; persist before consumption by Layer2 |
| Specialists / scores / IC | `src/idis/analysis/runner.py`, `src/idis/analysis/scoring/`, `src/idis/services/runs/layer2_ic_challenge.py` | Actual specialist reports, methodology/stage-aware scorecard, full debate findings, VEP and authorized external facts; three IC roles, not only current two |
| Muhasabi / Muhasabah | `src/idis/validators/`, `prompts/registry.yaml`, `docs/IDIS_Muhasabi_Prompt_Enhancement_v1.md` | Structured uncertainty/falsifiability; Nafs labels the default interpretation; Mujahada challenges the single most verdict-consequential assumption; insight types remain `conventional`, `deal-specific`, `contradictory`. GL-11 owns detailed acceptance; these are observable outputs, not hidden model reasoning |
| Analyst and delivery | `src/idis/api/routes/human_gates.py`, `src/idis/api/routes/overrides.py`, `src/idis/deliverables/generator.py`, `src/idis/deliverables/product_bundle.py`, `ui/src/app/deals/[dealId]/truth-dashboard/page.tsx` | Question/obligation-specific review, correction, dependent invalidation/regeneration and explicit template mapping |
| PostgreSQL | Existing migrations/repositories | Authoritative tenant-scoped runs, claims, evidence, calculations, governance, attempts, receipts, defects, reviews and versioned findings |
| Neo4j / pgvector | `src/idis/services/graph/retrieval.py`, `src/idis/services/rag/retrieval.py`, `src/idis/services/rag/indexing.py` | Derived graph chains/independence and semantic retrieval; hydrate substantive authorized content, preserve origins and consistent versions; existing vector dimension 1536 |
| Redis / document-artifact storage | Existing rate-limit/cache and document/artifact services | Redis is not authoritative diligence evidence; immutable/versioned originals and outputs need checksums, tenant authorization, retention and accessible annex references |
| Execution controls | `src/idis/pipeline/`, `src/idis/runtime/`, `src/idis/providers/`, audit and compliance modules | Leases, fencing, idempotency, rights, budgets, signed proofs, redaction, erasure and recovery stay authoritative |

Do not equate more agents, newer models, extra tests or infrastructure with better analysis. Measure completed answers and correction effort. References to substantive analysis are not a claim that every current context is IDs-only: existing specialist contexts already include some claim text, grades, calculation values and lineage. The work closes specific missing consumers and structures, not a blanket replacement.

## 3. Requirement contract and complete coverage

### Financial source register

The authoritative private workbook is identified in the baseline manifest. Its 79 rows are **items**, not necessarily 79 atomic obligations. Written calculation guidance is not executable Excel logic. Public GitHub contains the mapping specification and aggregate source coordinates, never the fund workbook or confidential wording.

| Stable public source-coordinate namespace | Original sheet / item rows | Items | Minimum private mapping obligations |
|---|---|---:|---|
| FDD-PL-004 through FDD-PL-032 | P&L, rows 4–32 | 29 | Historical/forecast revenue and cost evidence; margin/EBITDA/EBIT/tax/net income; forecast assumptions; business-plan and cash reconciliation |
| FDD-CF-004 through FDD-CF-016 | Cash Flow, rows 4–16 | 13 | Cash conversion, working capital, taxes/capex/financing, opening/closing cash and bank reconciliation |
| FDD-LIAB-004 through FDD-LIAB-024 | Liabilities, rows 4–24 | 21 | Equity instruments, debt, short-term financing, accruals/deferred revenue and contingent/off-balance obligations |
| FDD-ASSET-004 through FDD-ASSET-019 | Assets, rows 4–19 | 16 | Cash/receivables/prepayments/assets, recoverability, schedules and balance-sheet reconciliation |

GL-02 must create the complete **private** row-to-child-obligation register for every coordinate above. It must retain original raw sheet/row/cell text and merged-cell instruction ancestry; normalized wording is a separate field. Seven previously observed edge-whitespace differences must not replace raw wording. Shared Liabilities E4:E6 and E17:E18 instructions apply to all affected rows; the P&L historical/business-plan/cash reconciliation instruction must not be lost when splitting questions. No public redaction may erase the private audit trail.

Every child record has: stable ID and parent methodology question/version; source hash and cell/merge anchors; exact private wording/shared instructions; required facts; deterministic calculation rules; judgment sought; acceptable evidence and independence; completeness conditions; applicability decision; entity/period/currency/unit/adjustment coordinates; producer/consumer; responsible specialist and analyst review; expected output destination; current capability/gap/dependencies; acceptance case and evidence references; fund feedback/status. Completion means adequate answers, not imported-row counts. GL-02 verifies all 79 parents and all substantive child requirements with owner/fund review; the child count is **not invented here**.

Ten policy-v2 formula anchors are already planned: gross margin, EBITDA, EBIT, tax, net income, working-capital change, free-cash-flow/cash bridge, debt roll-forward, asset summation and liabilities/equity tie-out. Preserve the reviewed 79-question/107-binding policy, version discriminant and exact legacy bytes. Decimal precision 50 and ROUND_HALF_UP/exact-zero conservation apply where required by that policy. No silent currency conversion, unit coercion or missing-as-zero operands. Preserve conflicting observations rather than last-value-wins selection. Tax/debt completeness requires supported evidence, not a residual that makes the equation balance.

Additional derived rules remain explicit GL-08 work: retained earnings, DSO/DPO, cash conversion, bank/accrual/prepayment/deferred-revenue/lease/benefit/conversion schedules, unit economics and forecast sensitivities. Rules require versioned definitions and independent reference calculations. Clarifications include conditional cash-flow tax treatment, FCF conversion direction/denominator and zero EBITDA, short-term financing labels, apparent missing negation in Assets row19, jurisdictional tax, and the D&A/capex heuristic. Do not silently amend source instructions. Debt-policy exclusion of rows17–18 and off-balance exclusion of rows19–24 from one tie-out does **not** remove those diligence requirements.

EBITDA calculation and forecast realism are different obligations. Carry entity, period, currency, units, consolidation scope, actual/forecast status and every adjustment across documents. A correct EBITDA/revenue ratio does not validate volume, pricing, staffing, gross margin, capex or operating leverage assumptions.

### Commercial and market register

Existing source: `src/idis/methodology/templates/commercial_dd_v1.json`, 21 questions. Each suffix below corresponds to `mq_commercial_dd_<suffix>_0001`; use the exact source ID, not a newly generated substitute. Internal evidence means permitted fund/company documents; external evidence means rights-checked, dated independent facts, with management estimates labelled separately.

| Question suffix | Required evidence / calculation or judgment | Specialist and destination | Acceptance beyond reference existence |
|---|---|---|---|
| market_size | Management model plus independent market inputs; TAM/SAM/SOM scope, geography, period, bottom-up/top-down reconciliation | Market/sector; Market / MarketSize | Reproducible sizing, overlaps excluded, source independence and uncertainty clear |
| market_growth | Dated time series, drivers, CAGR/base effects and forecast assumptions | Market; Market / Growth | Same scope/base years, scenarios and unsupported extrapolation disclosed |
| market_structure | Segment/value-chain evidence, concentration and bargaining power | Market/sector; Market / Structure | Relevant market boundaries and counterevidence considered |
| customer_segments | Customer/cohort data, use cases and segment economics | Market/financial; Customers / Segments | Material segments covered; no unsupported generalization from anecdotes |
| customer_concentration | Revenue/customer ledger, groups and time periods; top-N exposure | Financial/risk; Customers / Concentration | Reconciles to revenue; related customers grouped transparently |
| customer_quality | Contracts, payment/usage history, references and tenure | Financial/market; Customers / Quality | Quality claims have specific support and selection bias disclosed |
| churn_retention | Cohort ledger and contractual definitions; logo/gross/net retention | Financial; Customers / Retention | Reproducible denominators, cohorts, expansions and churn reasons |
| sales_pipeline | Dated CRM stages, conversion history, aging and capacity | Market/financial; SalesGTM / Pipeline | Weighted assumptions checked against history, not management probabilities alone |
| pricing | Contract prices, discounts, willingness-to-pay and comparable offerings | Market/financial; SalesGTM / Pricing | Realized versus list price and segment comparability explicit |
| unit_economics | Revenue/gross profit, acquisition spend, cohorts; CAC/LTV/payback with approved rules | Financial; RevenueUnitEconomics / UnitEconomics | CAC allocation, retention horizon and uncertainty explicit; not universal SaaS thresholds |
| gtm_model | Channels, funnel, sales cycle and repeatability evidence | Market/sector; SalesGTM / GTM | Mechanism and constraints tied to observed results |
| distribution | Channel contracts, dependence, incentives and reach | Market/risk; SalesGTM / Distribution | Material intermediaries, economics and channel risks covered |
| competition | Verified competitors, substitutes, dated products/pricing and win/loss | Market/sector; Competition / Landscape | Comparable dimensions, independent corroboration and contradictions |
| pmf | Retention, usage, customer feedback and cohort behavior | Market/technical; Product / PMF | Evidence threshold follows fund methodology; anecdotes not conclusive |
| technology_diff | Architecture/product demonstrations, IP and competitor capability | Technical/sector; Product / Differentiation | Observable differentiation, defensibility and limitations |
| contracts | Material agreements, renewal/termination/liability terms and customer exposure | Terms/risk; Customers / Contracts | Exceptions and linked financial exposure visible; expert legal advice not implied |
| revenue_drivers | Units, prices, customer/cohort movement and revenue bridge | Financial/market; RevenueUnitEconomics / Drivers | Bridge reconciles to financials; assumptions separated |
| expansion | Cross-sell, geography/product opportunities and costs/evidence | Market/financial; RevenueUnitEconomics / Expansion | Addressable expansion not double counted; execution constraints |
| management | Verified roles, track record, incentives and gaps | Team/risk; Management / TeamQuality | Evidence-based assessment, conflicts and missing validation |
| risks | Cross-question risk/defect/contradiction register and mitigations | Risk plus specialists; CommercialRisks / Summary | Every material known risk dispositioned, not diluted by averaging |
| growth_assumptions | Operating plan drivers, capacity and market evidence; sensitivity calculations | Financial/market; BusinessPlan / GrowthAssumptions | Explicit falsifiers, downside cases and unresolved dependencies |

The template's minimum-one-evidence rule is not a completeness standard. Fifteen of its 21 assignments use six names absent from or mismatched with the actual eight-specialist registry; this is not fifteen distinct missing agents. GL-12 maps assignments to implemented specialists and adds a role only with a justified decision. Fund-specific output mappings must reach the renderer, not merely be copied into a task.

### Four substantive boundaries

1. **Question:** facts, calculations, judgment, evidence standards, completeness and destination are separate, version-bound fields. Extend existing methodology/planner types, not a parallel question framework.
2. **Evidence:** a prepared package contains relevant passages/tables and locators, structured operands, competing observations, calculation inputs/results and allowed external facts. Bounded retrieval is also needed for follow-up; it must hydrate authorized content and log scope/version/results. Retrieval cannot bypass internal/external boundaries.
3. **Verified answer:** every material assertion links to particular evidence or a calculation. Check numerical reproduction, source-coordinate agreement and actual support; reference existence alone fails. Label observed, inferred, disputed and missing information. Model review is useful evidence, never a guarantee. Remove unsupported citation backfilling in `src/idis/deliverables/generator.py` and specialist risk fallbacks.
4. **Fund destination:** stable question/child IDs and template destinations survive analysis, review, corrections and export, retaining qualifications, formulas and accessible support. A partial answer cannot silently become a complete score or investment conclusion.

## 4. External-provider plan

Task9's 18-provider catalog is local implementation work, not proof of active production wiring. Extend `src/idis/services/enrichment/task9/provider_catalog.py`, `provider_admission.py`, `normalized_facts.py`, `rights_index_policy.py` and existing connector/service persistence. Verified identity precedes applicability, then rights, credentials, budget, cache admission, transport and durable fact/receipt storage. Never use an opaque deal ID as a company name.

| Provider | Case applicability and substantive use | Catalog cache TTL / rights class at assessment |
|---|---|---|
| SEC EDGAR | Verified CIK; filings, financials, ownership, public peers | 24h / GREEN |
| Companies House | Verified UK company number; corporate/filing facts | 24h / GREEN |
| GitHub | Verified organization; public development signals, not proof of commercial quality | 1h / GREEN |
| FRED | Relevant jurisdiction/series; macro assumptions | 24h / GREEN |
| Finnhub | Verified public ticker; market/public-peer facts | 5m / RED |
| FMP | Verified public ticker; public financial/comparable data | 5m / RED |
| World Bank | Country/indicator; market denominators and macro history | 7d / GREEN |
| ESCWA catalog | Relevant regional sector/catalog query | 7d / GREEN |
| ESCWA ISPAR | Relevant ISO3 country indicators | 7d / GREEN |
| Qatar open data | Relevant Qatar sector/geography | 24h / GREEN |
| Hacker News | Verified company names; weak qualitative signals | 30m / GREEN |
| GDELT | Verified entity/news events; corroboration and risk signals | 1h / GREEN |
| PatentsView | Verified assignee; IP landscape | 7d / GREEN |
| Wayback | Verified domain; historical representations | 24h / YELLOW |
| Google News RSS | Verified names; dated leads requiring original-source checks | 30m / YELLOW |
| EPO open patent | Verified assignee/legal identity; patent evidence | 7d / GREEN |
| Semantic Scholar | Relevant disjoint research selectors; scientific/market context | 24h / YELLOW |
| Perplexity | Bounded original-source discovery and external fact synthesis | 0 / RED, no-store |

GREEN/YELLOW/RED are configured policy labels, **not verified subscriptions or licenses**. Cache TTL is not an accepted maximum age of a fact. GL-09 adds methodology-specific freshness and rights/downstream-use checks: origin URL/provider, source/published/retrieved dates, entity/scope, observations, allowed retention/index/export and expiry. Perplexity no-store means no raw response cache; the current policy prohibits graph/RAG external-context indexing, not merely indexing without a local toggle. A permissible provenance receipt is not a license to retain all source content. Changing that policy needs separate authority. Original-source rights still apply.

All 18 providers need a legitimate applicability/status disposition for the full gate; this does not mean 18 calls per question. Required providers for each private case are unresolved until identities and methodology needs are verified. Current explicit N/A handling for Finnhub/FMP requires verified private/unlisted/no-ticker evidence; unknown identity is not N/A. New jurisdiction-based exceptions need the applicable ruling. Unavailable required sources produce a visible blocker or approved alternative, never a hidden success; optional sources can be omitted only with recorded reason and unchanged completeness standard.

Market sizing must distinguish management estimates from independently corroborated estimates. The fund determines the necessary corroboration and freshness. Persist substantive facts so market/financial specialists, scoring, IC and deliverables actually consume them. External disagreement may open investigation, but does not contaminate internal Court/VEP/FULL evidence.

## 5. Models, transport, pricing and prompt modernization

### Observed versus proposed

Current code uses ordinary Anthropic API clients with old Sonnet/Opus4 defaults and shared client construction, including a narrow methodology pricing allowlist. The assessed strict-runtime subset has 18 DRAFT 1.0.0 prompt families, not a qualified PROD set; the broader registry has 28 DRAFT entries. Task12 requires 19 runtime families including IC advocate. These facts are not fixed by changing a model string.

The following are **proposed candidates, not models qualified on IDIS**. Official capability sources were checked on 2026-10-16; revalidate model availability, residency and prices before authorization. Development-agent selection is separate: choose high reasoning for contract/security diagnosis and independent review; use less costly capable agents for bounded mechanical changes only after evidence shows suitability. No mandatory vendor or productivity multiplier.

| Runtime role | Proposed exact model ID | Reasoning / output ceiling | Behavior to qualify |
|---|---|---|---|
| Methodology extraction | `claude-sonnet-5-5` | medium / 16,384 tokens | Strict typed output; exact schema/version routing; no arbitrary research tools |
| Market, technical, sector specialists | `claude-sonnet-5-5` | high / 16,384 | Evidence package plus permission-bounded tools |
| Team, historian | `claude-sonnet-5-5` | medium / 12,288 | Provenance-aware claims/reuse; historian is not learning |
| Financial specialist | `claude-opus-5-5` | high / 32,768 | Structured findings and governed calculations, not model arithmetic as authority |
| Terms specialist | `claude-opus-5-5` | high / 16,384 | Evidence-grounded terms/limitations |
| Risk specialist | `claude-opus-5-5` | high / 24,576 | Material uncertainty and counterevidence |
| FULL advocate / Sanad breaker / contradiction finder | `claude-sonnet-5-5` | medium / high / high; 16,384 each | Internal-only substantive contexts, structured challenges |
| FULL risk officer / arbiter | `claude-opus-5-5` | high; 24,576 / 32,768 | Findings adjudication with explicit unresolved issues |
| Scoring / assertion-support check | `claude-sonnet-5-5` | medium; 16,384 / 8,192 | Methodology-stage configuration; statement-specific support; not truth guarantee |
| IC advocate | `claude-sonnet-5-5` | high / 16,384 | Actual reports, scores, internal findings and permitted external facts |
| IC challenger / arbiter | `claude-opus-5-5` | high; 24,576 / 32,768 | Bounded actionable challenge, resolution status and rationale |
| Narrative / IC memo drafting | `claude-sonnet-5-5` | medium / 16,384 | Reviewed findings only; preserve qualifications |
| External research through Perplexity Agent API | `anthropic/claude-sonnet-5-5` | medium / 8,192 | Explicit web search/fetch allowlist; no sandbox tool |
| Same-evidence comparator, later qualified fallback | `gpt-6.1-sol` | medium extraction/support, high analysis; same role ceilings | Responses transport for tools; no automatic fallback initially |
| Later cheap classification candidate | `claude-haiku-5-5` | low/medium / 4,096 | Non-material classification only until evaluated |
| Embeddings | `text-embedding-3-small` | 1536 dimensions | Retain existing index compatibility; no initial embedding migration |

GL-05 separates role-specific client factories from the current shared analysis/scoring/debate client. Pin provider, model, role, prompt/schema hash, effort, token/tool/cost limits and fallback policy in an admitted configuration. Extend existing `src/idis/api/routes/runs.py`, `src/idis/services/extraction/extractors/anthropic_client.py`, `src/idis/models/methodology_pricing.py`, `src/idis/providers/budget.py` and `prompts/registry.yaml`; no hidden per-role global overrides.

For Claude5.5, migrate to supported adaptive thinking/effort and strict JSON or auto tool selection; remove unsupported sampling controls and forced-tool assumptions. Parse typed response blocks, preserve tool-loop transport state, and treat refusal/truncation/schema failure as non-success. Do not require hidden chain-of-thought as evidence. [Sonnet migration](https://platform.claude.com/docs/en/models/sonnet-5-5/migration-guide), [Opus migration](https://platform.claude.com/docs/en/models/opus-5-5/migration-guide).

For `gpt-6.1-sol`, use Responses for tool calling with supported medium/high reasoning; qualify structured outputs and tool/result parsing independently. Standard input/output pricing is $2/$10 per million tokens, with distinct cache, long-context, regional and service-tier rates; admission must price the actual request, not a nominal average. [Official model specification](https://developers.openai.com/api/docs/models/gpt-6.1-sol).

Claude candidate standard input/output planning rates are Sonnet5.5 $2/$10 and Opus5.5 $4/$20 per million tokens; cache/long-context and platform differences must be explicit. They are not evidence of a negotiated rate or account entitlement. [Official Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing).

The existing local Perplexity connector uses Sonar-era transport. GL-09 must migrate/version request hashing, typed results, source references and budget pricing for the Agent API; do not send old payloads to a new endpoint. Tool pricing is separate from tokens (published search $0.0025/call and fetch $0.0005/call at review). [Migration](https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/overview), [models](https://docs.perplexity.ai/docs/agent-api/models), [pricing](https://docs.perplexity.ai/docs/getting-started/pricing).

Prompt promotion requires role-specific fixtures, failure/refusal/tool behavior, source-support and financial/commercial rubrics, private real-case comparison after permission, cost/latency and prompt/version receipts. The controlling Slice100 plan requires HIGH-risk governance gates 1–4 plus genuine OWNER and SECURITY/COMPLIANCE approvals; MEDIUM requires gates 1–2 plus OWNER approval. Solo ownership does not silently collapse distinct approval roles or permit placeholder/model-generated approvals. The owner must identify legitimate approval coverage before promotion; no hired reviewer is assumed, but unavailable required approval remains a blocker. Same-evidence/same-model comparisons isolate prompt or skill benefit. Proposed fallbacks fail closed initially; after qualification, fallback is a separately admitted and budgeted attempt. Never automatically replay a provider request with an unknown billing/execution outcome.

## 6. Anthropic financial-services reuse

The assessed IDIS runtime does **not** load/invoke Anthropic financial-services SKILL files, plugins or Managed Agent templates. The actual extraction client is `src/idis/services/extraction/extractors/anthropic_client.py`; ordinary Claude API calls and IDIS prompt registry entries are not financial-skill execution. No skill/plugin runtime path was established. A future adaptation must be explicit and evaluated.

Reference repository: [anthropics/financial-services](https://github.com/anthropics/financial-services), previously inspected commit `574ed3624aebd0418c7e96cd101262f30210ab26`. Pin and verify the selected content at adoption; do not track a moving main branch invisibly. Apache-2.0 code/content license requires applicable notices/attribution and modified-file notice; it does not grant rights to paid data, private files or third-party templates.

| Candidate | Disposition / fund fit | Integration and acceptance |
|---|---|---|
| `plugins/vertical-plugins/financial-analysis/skills/audit-xls/SKILL.md` | Adapt spreadsheet-audit rubric; retain existing parser and calculation engine | Formula/hardcode/sign/unit/cross-sheet checks become governed findings; compare with current audit on same workbook |
| `plugins/vertical-plugins/financial-analysis/skills/3-statement-model/SKILL.md` | Adapt schedule/reconciliation guidance | Review all accounting assumptions; fund rules override generic/public-US heuristics; deterministic rules and Calc-Sanad remain authoritative |
| `plugins/vertical-plugins/private-equity/skills/dd-checklist/SKILL.md` | Adapt optional checklist coverage suggestions | Map to private child-obligation ledger; no replacement of 79 fund items or PE/LOI assumptions imposed on VC |
| `plugins/vertical-plugins/private-equity/skills/unit-economics/SKILL.md` | Adapt cohort/CAC/LTV review guidance | Fund- and business-model-specific definitions; avoid universal SaaS thresholds |
| `plugins/vertical-plugins/financial-analysis/skills/competitive-analysis/SKILL.md` | Adapt research decomposition and comparability rubric | Existing market-intelligence rights/freshness/persistence route, not uncontrolled browsing |
| `plugins/vertical-plugins/private-equity/skills/ic-memo/SKILL.md` | Adapt section/review rubric; retain IDIS generator | Use same reviewed findings and template map; avoid PE leverage/100-day-plan defaults |
| Managed-agent market-researcher / model-builder examples | Retain IDIS orchestration; borrow useful planning and builder/auditor separation only | No new hosted agent runtime or blanket MCP permissions; public-market/ticker and paid-data assumptions need adaptation |
| GL reconciliation example | Defer where it concerns fund accounting rather than target-company diligence | Adopt only if an actual fund obligation requires it |

GL-17 performs a controlled comparison: baseline IDIS versus adapted content on the same real evidence, same model where practical, expert/fund rubric and blinded answer ordering. Measure corrections, unsupported assertions, missed obligations, calculation errors, elapsed effort and actual charges. Schema-valid output alone is not substantive validation. A skill-produced calculation must enter the governed rule/operand/Calc-Sanad verification route; no spreadsheet script can become an unreviewed authority.

Potential savings are **unmeasured**: the candidates may avoid drafting generic rubrics, not the integration, rights, verification or fund review. Record avoided drafting work and net adaptation/review work during GL-17; do not subtract speculative human person-days from the solo schedule or cash budget. No adoption is assumed to improve accuracy until the comparison does.

## 7. Completion versus behavioral improvements

| Topic | Existing design completion | Proposed behavior change / timing |
|---|---|---|
| Financial compatibility and calculations | GL-P01/GL-03, GL-07/08: disjoint version-bound schemas, coordinate reconciliation, all formula lineage | First pilot contains one complete path; full workbook obligations remain |
| Substantive contexts | GL-09/10/12: persistent external facts, VEP/graph/RAG hydration, specialist reports and scorecards into IC | Prepared package plus authorized retrieval; same architecture |
| Fund planning | Existing frozen methodology/tasks/report mapping | GL-02/18 makes facts, judgment, completeness and fund-specific investigation explicit; pilot only two selected questions |
| Actionable challenge | Existing debate, IC and review structures | GL-13: system identifies material uncertainty and proposes specific evidence/research/calculation with reason and expected effect; pilot analyst approval is a control, not the eventual product's permanent retrieval manager |
| Reassessment | Existing execution IDs/receipts/leases | One bounded pilot cycle; record proposed, approved, executed, failed and reassessed separately; no automatic repeated loops initially |
| Correction and reuse | Existing review/override and calculation dedup foundations | GL-P03 basic dependency invalidation and regeneration is release-critical; GL-19 cross-run valid reuse requires evidence/prompt/schema/rights freshness checks |
| Model selection | Existing providers/client boundaries | GL-05 fixed role configurations first; adaptive routing later GL-19 after measured benefit |
| Learning | Historian is not validated learning | GL-20 later curated correction examples/evaluations with consent, provenance and rollback; no silent self-training |

Known gaps remain identifiable: calculation-ID replacement risk from generic accumulated result updates (prove with multi-coordinate tests, not claim historical data loss); hard-coded Seed scorepack; FULL UI missing methodology selection; citation backfill; incomplete Nafs/Mujahada/insight enforcement. GL-12, GL-14 and GL-11 respectively close these with observable evidence in the backlog.

## 8. Pilot route and genuine dependencies

Initial direction is **complete production-relevant seams and use replay**, not build separate admission infrastructure now. First coherent sequence: GL-P01 financial-schema compatibility -> GL-P02 one governed calculation and assertion-support path -> GL-P03 reviewed answer propagation, correction and regeneration. These are component evaluations, not canonical FULL acceptance.

The two pilot questions are: projected long-term EBITDA margin realism; addressable market size and support. Use one already inspected private deal with an opaque ID. A reference calculation and expert-reviewed judgment/evidence rubric still need creation. Management market estimates are not independent corroboration; retained runs are not reference answers.

Production extraction is legitimately admitted only through the existing preparation, committed authorization/budget reservation, provider dispatch, validation, committed terminal persistence and finalization path. Current durable persistence binds native run/step roots, fences, proofs and foreign keys. The strict contract remains INACTIVE. Replay must not claim a live provider call, native terminal receipt or completed full run.

| Route | Work and benefit | Decision |
|---|---|---|
| Common production seams + replay | Early schema/math/support/output feedback using the same computational components; no fabricated native roots | Preferred first; explicit replay adapters only at dependency boundaries, not a new orchestration framework |
| Finish canonical production admission | Remaining strict contract/producer/consumer/prompts/services/gates and approved migration/activation; all work carries directly to product | Default route to legitimate live production extraction and terminal persistence |
| Separate live component-evaluation admission | Requires separately approved contract identity, admission/budget/lease authority, typed terminal persistence, root/FK-compatible adapters, recovery, security and evidence semantics | GL-04 remains conditional. Do not implement until a comparison shows a material question cannot be answered by replay and a separately approved path is smaller/useful |

Do not exploit nullable legacy columns, invent run/step IDs, route through a permissive legacy FULL order or weaken strict guards. Compare the complete remaining work of both live routes after the first three packets. A separate pilot route is not automatically cheaper; owner decides with concrete dependency evidence. If selected, reuse production pure builders and services through reviewed interfaces; preserve the production admission contracts.

Canonical strict execution order and stage labels are in the verification runbook. Tasks8–15, 27 restart cuts/48-rule equivalence, complete manifests, prompt promotion, GateA/GateB and activation conditions remain obligations. The pilot does not require unrelated backlog completion for offline feedback, but cannot claim live canonical FULL without its actual prerequisites.

## 9. Milestones, effort and cash

### Solo execution and schedule

No staffed engineering budget or human person-day-to-AI-calendar conversion is valid. Begin with at most two non-overlapping implementation writers plus coordination and fresh review; serialize shared schema/contract/orchestrator integration. The owner retains product/financial decisions. A second AI review is technical review, not independent human financial assurance.

| Milestone from separate implementation authorization | Deliverable / dependency | Provisional timing |
|---|---|---|
| M0 | This documentation PR; publication is not implementation acceptance | Current task |
| M1 | Baseline reconciled, bounded authority, GL-P01/02/03 calibration | Reserve two weeks plus one contingency week; low confidence, not a completion promise |
| M2 | Production-relevant two-question evidence path and route comparison | Forecast only after measured M1; models/data/fund answers may constrain live evidence |
| M3 | Full requirement mapping, remaining formula/provider/context/IC/UI integration | Rolling four-week forecast from measured packet classes; parallel only with disjoint ownership |
| M4 | Canonical full execution and real-case/fund acceptance gates | Critical path includes contract rulings, services, prompts, live spending and review |
| M5 | Qualified deployment, restore drill, owner operational handover | Requires M4 plus region/security/support agreement |
| M6 | Adaptive optimization and validated-correction learning completed/evaluated | Later tracked capability; do not relabel an M5 interim release as complete modernization |

Critical path: baseline/authority -> schema -> governed calculation/support -> correction/output -> qualified complete context/agent chain -> canonical gates -> real-case/fund acceptance -> operations. Work that can run in parallel includes private requirements review, provider rights/freshness design, model adapter qualification and non-overlapping UI/export work. Integration gates still serialize. Waiting for the fund is not engineering effort; novel migration/provider/recovery work is not calibrated by a simple schema packet.

Measure each packet's wall-clock span, agent active time, verification/rework/integration time, owner active review time, external waiting, charges and escaped defects. Re-estimate similar packets after M1, preserving uncertainty. Owner scheduling assumption is 6–10 active hours/week, to confirm; initially reserve 30–60 minutes per packet decision/review **plus** substantive financial/commercial validation as actually required. No guaranteed go-live date exists yet.

### Incremental cash, not staffing cost

These are planning allowances, not vendor quotes, actual spending or authorization. Existing subscriptions/hardware are baseline and excluded; only upgrades/overage beyond them count. Included development-agent use is not IDIS API inference usage. Do not double count bundled hosting/storage or provider token/tool charges.

| Category | Planning allowance | Admission / measurement |
|---|---:|---|
| Development model usage beyond existing subscriptions | $0–300 for initial calibration cycle | Check actual plan limits/credits; record incremental charges, not a notional salary |
| Offline IDIS runtime evaluation | $0 provider spend | No provider call; replay must be labelled |
| First separately approved live evaluation batch | $200–800 reserve | Illustrative ceiling: six bounded evaluations at $50 plus two full runs at $250 = $800, only if those runs are legitimately admitted |
| New data subscriptions | $0 assumed initially; required paid rights unpriced | Required subscriptions can block a case; owner must approve actual terms/price |
| Temporary evaluation hosting | $0–100/month | Local/existing resources may suffice; no service starts implied |
| Evaluation storage/backups | $0–25/month | Private evidence retention and access need verification |
| Initial production compute/databases | $300–1,500/month placeholder | One fund, low concurrency; region/topology/recovery quote still required |
| Production objects/backups/egress | $20–100/month placeholder | Volume/retention/download behavior still unknown |
| Build/deployment/domain/monitoring extras | $0–100/month if not included | Itemize only incremental services |
| Optional outside assistance | Excluded; separately scoped/quoted if requested | Legal/accounting/security expertise is optional procurement, not assumed staffing |

Initial offline cash envelope is $0–425 using the development, temporary hosting and storage allowances; it is not a promise of completing the cycle at that price. There is no defensible total-program cash figure yet, no assumed 20 cases/month and no paid support headcount. Per-case caps are not expected average costs. Record retries, failed calls, ambiguous billing outcomes and research tools. Stop at the approved limit; no automatic increase.

Owner time is recorded separately as product decisions, technical review, source/calculation checking, fund coordination and operations. If neither owner nor fund can validate a material judgment, it stays unvalidated; hiring is not silently assumed, and another model does not close that gap.

## 10. Decisions and supersessions

| ID | Decision / owner / deadline |
|---|---|
| D-01 | Solo-owner/frontier-AI execution replaces staffed-team assumptions and salary budget; accepted direction, effective now |
| D-02 | Withdraw prior 18–24-week forecast and person-day productivity conversion; use M1 calibration and rolling forecasts |
| D-03 | Separate live pilot admission is conditional, not a prerequisite; owner chooses after M1 route comparison |
| D-04 | Fund workbook authoritative financial scope; raw private source preserved; child obligations reviewed by owner/fund before coverage acceptance |
| D-05 | Four-part delivery package proposed; fund confirms final template/format by M4, earlier mapping starts without waiting |
| D-06 | Quality/effort/latency/cost/recovery targets in runbooks are provisional where fund-dependent; owner/fund agree before live release |
| D-07 | Exact financial-policy ambiguities and model/prompt/contract changes require the applicable ruling before affected implementation; owner, at packet admission |
| D-08 | Residency, provider rights, required case providers, budget and service access remain unknown until admission; owner, before live evaluation |
| D-09 | Documentation may be published now; implementation history, private inputs, activation, deployment and merge authority do not follow from that permission |
| D-10 | Basic correction/invalidation/reuse required for release; adaptive routing, autonomous repeated investigation and learning remain later tracked work |

This package supersedes only prior conversational planning statements that assumed a hired team, salary/person-day budget, fixed 18–24-week schedule, unconditional separate pilot infrastructure, or a two-question demonstration as the full product. It consolidates the accepted full scope, not frozen technical authority. It does not supersede Slice100 Tasks8–15, Tier1/Tier2 rulings, the 0039 hold, activation gates, model/data permissions or unresolved source-schema ownership. Historical documents are not rewritten to conceal prior decisions.

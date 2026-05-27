# IDIS Full-Live Master Plan v2

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:using-git-worktrees` before each implementation slice, `superpowers:writing-plans` for read-only truth passes, `superpowers:test-driven-development` before implementation, `superpowers:receiving-code-review` for review feedback, `superpowers:verification-before-completion` before any completion claim, and `superpowers:finishing-a-development-branch` before commit or PR. Use every relevant available skill, plugin, MCP/app connector, subagent/review function, and local tool for the slice; do not skip a relevant capability because it was not top-of-mind. Use `code-reviewer` and `validation-review` before PRs that touch strict readiness, runtime proof, private gates, live providers, Postgres/RLS, package export, or VC output.

**Goal:** Provide the single working order from the current strict/synthetic readiness state to an honest strict full-live `real_example` run and VC-reviewable output.

**Architecture:** This plan keeps the public API/worker FULL run path as the only production spine. Every slice must make that path more real, more durable, more observable, or more reviewable. Private `real_example` is used only as a local acceptance gate and must never leak raw content, private filenames, secrets, prompt transcripts, object keys, vectors, embeddings, or provider payloads.

**Tech Stack:** Python/FastAPI, Postgres/pgvector, filesystem or configured object storage, Neo4j, Anthropic/OpenAI-compatible model clients, BYOL enrichment connectors, OCR/media tooling, IDIS deliverables/export APIs, UI, GitHub CI, Docker/K8s/Terraform gates.

---

## 1. Source Of Truth

This file supersedes the ordering ambiguity between the older plans. It reconciles:

- `C:\Users\baram\.cursor\plans\slice56-full-live_84cbdd45.plan.md`
- `.local_reports/deep_system_assessment/deep_assessment.md`
- `.local_reports/exhaustive_read_audit/complete_system_assessment.md`
- `docs/IDIS_COMPLETE_SYSTEM_GO_LIVE_PLAN.md`
- merged Slice56 through Slice72 work, plus the Slice73 boundary currently being planned.

Use this file as the master order until it is explicitly replaced.

## 2. Current Proven Baseline

As of 2026-05-27 after PR #82:

- Synthetic GDBS corpus inspection, API upload, strict-block run attempt, non-strict FULL execution, same-run package rows/manifest/download verification, and strict provisioning truth reporting have been proven in narrow slices.
- Product package surfaces have been proven for synthetic same-run package export when local Postgres plus filesystem object store are configured.
- Strict readiness still must not be cleared by synthetic reports.
- `real_example` has not been run as strict full-live acceptance.
- Live providers, BYOL enrichment, OCR/media, Neo4j runtime retrieval, RAG/vector runtime retrieval, true Layer 2 IC challenge, worker parity, enterprise controls, and fund-reviewable final output are not yet proven end to end.

## 3. Non-Negotiable Rules

- `IDIS_REQUIRE_FULL_LIVE=1` is mandatory for any final full-live claim.
- Strict readiness must block before run creation when any required component is missing, fallback, disabled, not health-checked, not runtime-proven, or not output-visible.
- No deterministic LLM fallback, fake embedding, mocked enrichment, hidden private script success, in-memory product persistence, or swallowed provider failure may count as strict live.
- Every material output must be source-backed by safe IDs: document/span/evidence/claim/sanad/calc/enrichment/graph/RAG/provenance IDs.
- Every private gate must emit safe aggregate summaries only.
- Every slice must be TDD-first and scoped. No slice may quietly broaden into `real_example`, live providers, strict readiness clearing, or VC-ready claims.
- Reuse before create: before adding any new script, helper, module, CLI, route, service, test fixture, or workflow, the agent must search the local repo for existing code and scripts that already solve or nearly solve the problem. Do not duplicate prior slice work.
- New scripts are last resort. If a new script is still required, the PR description must name the repo search performed, the existing entry points considered, and why extension/reuse was not enough.
- Capability check is mandatory: before implementation, the agent must identify the relevant skills, plugins, MCP/app connectors, subagents/review functions, shell functions, and local scripts for the slice. The handoff or PR body must state what was used, and why any obvious capability was not applicable or unavailable.
- If a component is health-checked but not used in FULL output, say so. Health is not output visibility.
- If a component is output-visible but not live-provider proven, say so. Output is not live proof.

## 4. Missing Items Added To The Old Slice56 Plan

The Slice56 full-live plan remains the runtime spine, but it missed or under-specified these production surfaces. They are now included in the ordered plan below:

| Missing Surface | Where This Plan Covers It |
| --- | --- |
| API/worker parity, queue, retry, resume, cancel | Phase I |
| Authenticated audit actor identity | Phase I |
| Durable ABAC/RBAC/SSO/MFA/break-glass controls | Phase J |
| Webhooks and lifecycle events | Phase I |
| Observability, SLOs, dashboards, incident runbooks | Phase I and Phase J |
| Prompt registry promotion and model audit linkage | Phase J |
| Evaluation/GDBS governance and drift tracking | Phase J |
| Release/deployment/promotion gates | Phase J |
| Data residency, retention, legal hold, deletion/export workflows | Phase J |
| OpenAPI/schema/client contract lock | Phase J |
| Full UI review workflow, not only download links | Phase H |
| Fund review gates after private real_example | Phase K |

## 5. Remaining Ordered Work

The numbered slices below are not guesses about exact PR count. They are the required order. A slice can split if RED tests reveal more risk, but it must not skip ahead.

### Phase A: Strict Local Health And Runtime Profile

#### Slice 73: Opt-In Local Strict Health Probes

**Goal:** Convert safe local dependencies from static provisioning truth to opt-in local health-checked status.

**Scope:**
- Postgres connectivity and pgvector extension presence.
- Neo4j connectivity only.
- Filesystem object-store health only with explicit temp/local base dir.
- Config-shape checks for canonical env names.

**Non-goals:** Anthropic/OpenAI/BYOL calls, external APIs, `real_example`, strict FULL, readiness clearing, VC-ready claim.

**Acceptance:**
- Default report does not probe.
- Explicit opt-in records `local_probe_attempted` and `local_probe_passed`.
- pgvector is labeled connectivity/extension only, not RAG proof.
- Neo4j is labeled local health only, not graph runtime proof.
- Object store never touches ambient/global paths.

#### Slice 74: Secret-Safe Strict Runtime Profile

**Goal:** Provide a supported local/deployment runtime profile that loads strict env safely and reproducibly.

**Scope:**
- `IDIS_STRICT_DOTENV_PATH` or equivalent CLI-supported dotenv loading.
- Process env wins over dotenv.
- Safe inventory reports only names, presence, source, and validation status.
- Canonical `IDIS_API_KEYS_JSON` validation.
- Strict model env inventory for extraction/default debate/arbiter.

**Acceptance:**
- Strict readiness can be run from a documented command without printing values.
- Missing/malformed env fails with reason codes.
- `.env.example` matches code expectations.

#### Slice 75: Canonical API/Worker FULL Path Parity

**Goal:** Ensure API and worker run paths delegate to the same canonical execution service and strict gate.

**Scope:**
- API FULL, queued FULL, retry/resume/cancel share one run execution service.
- Strict readiness runs before run creation in strict mode.
- Worker cannot bypass tenant/RLS, run-step ledger, object store, or strict blockers.

**Acceptance:**
- API and worker produce the same step ledger and safe summaries for equivalent selected docs.
- Strict mode cannot succeed through worker or script bypass.
- No duplicate execution race.

#### Slice 76: Strict Audit And Observability Baseline

**Goal:** Make strict run provenance and operator diagnostics durable and safe.

**Scope:**
- Authenticated actor identity in audit events.
- Step provenance includes component mode, env-source class, health/probe status, runtime-use status, and output-visibility status.
- JSONL/Postgres audit sink policy in strict mode.
- Operator-safe failure summaries.

**Acceptance:**
- No placeholder actor identity in strict paths.
- Every strict blocker has safe operator evidence.
- No raw content, secrets, paths, object keys, or provider payloads in logs/audit.

### Phase B: Data-Room Intake, OCR, Media, And Private Parse Gate

#### Slice 77: Durable Data-Room Package And File Ledger

**Goal:** Represent a complete data room as a durable tenant-scoped package, not loose uploads.

**Scope:**
- Package/file/artifact ledger with folder paths safely redacted or policy-controlled.
- Batch/manifest upload or repeated upload grouping.
- Parser triage persisted per file.
- Object-store bootstrap for raw artifacts.

**Acceptance:**
- Public API can create a data-room package from supported generated fixtures.
- Private `real_example` inventory can run locally and emit safe aggregate counts.
- Unsupported/deferred files have deterministic reason codes.

#### Slice 78: Parser Capability And Conversion Workflow

**Goal:** Close non-OCR/media parser gaps before private full ingestion.

**Scope:**
- HTML/TXT support or explicit supported conversion.
- DOCX/PPTX/XLSX/PDF capability triage.
- Conversion-required policy and remediation reasons.

**Acceptance:**
- No `real_example` supported text-like class is silently deferred.
- Unsupported classes are user-visible blockers with reason codes.

#### Slice 79: OCR/Image Ingestion

**Goal:** Enable scanned PDFs and images to produce durable OCR spans.

**Scope:**
- Tesseract/poppler/image parser config.
- OCR diagnostics, confidence, page/image locators.
- Strict health checks for OCR binaries and config.

**Acceptance:**
- Generated scanned PDF/image fixtures produce OCR spans.
- Private `real_example` OCR-required file counts go to zero or are explicitly blocked with accepted reason.

#### Slice 80: Media/STT Ingestion

**Goal:** Enable MP4/media evidence to produce transcript/timecode spans or strict blockers.

**Scope:**
- ffmpeg/ffprobe checks.
- faster-whisper or approved STT model provisioning.
- Duration and resource limits.
- Timecode span persistence.

**Acceptance:**
- Generated media fixture produces transcript/timecode spans.
- Private `real_example` media files are transcribed or explicitly blocked with safe reasons.

#### Slice 81: Private Data-Room Parse Readiness Gate

**Goal:** Run a local private gate over `real_example` for upload/triage/parse readiness only.

**Scope:**
- No FULL run yet.
- Safe aggregate counts by parser status, evidence class, and blocker reason.
- Resume ledger and per-file timeout/memory controls.

**Acceptance:**
- `real_example` parse readiness has zero unintended deferrals before downstream live work is accepted.

### Phase C: Live LLM Runtime

#### Slice 82: Anthropic Model Health And Policy

**Goal:** Prove configured live model health without running FULL.

**Scope:**
- Extract/default debate/arbiter/scoring model envs.
- Minimal safe health checks with no private data.
- Request IDs/provider metadata captured safely.
- Prompt/model registry linkage.

**Acceptance:**
- Strict readiness distinguishes configured, health-checked, runtime-call-proven, and not-yet-FULL-used.

#### Slice 83: Strict Live Extraction

**Goal:** Make strict extraction use only live approved extractor backends.

**Scope:**
- `IDIS_EXTRACT_BACKEND=anthropic`.
- No deterministic extractor in strict mode.
- Provider provenance and prompt/model version in step summary.

**Acceptance:**
- Synthetic selected FULL uses live extraction under opt-in strict profile.
- Missing/failed provider blocks before or during strict run with safe reason.

#### Slice 84: Strict Live Analysis, Debate Layer 1, And Scoring

**Goal:** Make analysis, Layer 1 debate, and scoring live-provider backed and output-visible.

**Scope:**
- `IDIS_DEBATE_BACKEND=anthropic`.
- Live role runners only in strict mode.
- Round counts, dissent, arbiter result, scoring provenance.

**Acceptance:**
- No deterministic LLM role/scoring path is used in strict mode.
- Outputs include safe model/prompt provenance and source references.

### Phase D: BYOL And External Enrichment

#### Slice 85: BYOL Credential Loading

**Goal:** Load provider credentials into durable tenant credential storage safely.

**Scope:**
- `IDIS_ENRICHMENT_ENCRYPTION_KEY`.
- Companies House, GitHub, FRED, Finnhub, FMP credentials.
- Secret-safe bootstrap for strict local tenant.

**Acceptance:**
- Enrichment services read tenant credentials, not raw ambient env.
- Missing BYOL credentials block strict enrichment before run.

#### Slice 86: Enrichment Execution And Provenance

**Goal:** Execute approved enrichment providers in FULL and feed outputs forward.

**Scope:**
- Rights/BYOL policy.
- Hit/miss/error/cache/blocked ledger.
- Provider provenance, source-grade mapping, conflict checks.
- Feed enrichment into analysis/debate/scoring/deliverables.

**Acceptance:**
- Provider errors are fatal in strict mode unless policy says optional.
- Enrichment provenance is visible in VC package.

### Phase E: Calculations

#### Slice 87: Calculation Path Unification And Persistence

**Goal:** Replace stubs/parallel calc paths with one production CalcEngine/CalcSanad path.

**Scope:**
- CalcEngine execution in FULL.
- Persist deterministic calculations and CalcSanads.
- Reproducibility hashes and formula versions.
- Financial tables for deliverables.

**Acceptance:**
- Financial claims produce calc IDs and CalcSanads.
- Calc outputs feed analysis, debate, graph, RAG, and VC package.

### Phase F: Graph And RAG

#### Slice 88: Neo4j Projection

**Goal:** Project claims, evidence, Sanads, defects, calculations, and deliverables into Neo4j.

**Scope:**
- GraphProjectionService after durable Postgres writes.
- Tenant isolation and consistency saga.
- Fail-closed graph writes in strict mode when configured.

**Acceptance:**
- FULL strict run writes graph projections or blocks safely.

#### Slice 89: Graph Retrieval In FULL Context

**Goal:** Use graph retrieval in analysis, debate, scoring, Layer 2, and deliverables.

**Scope:**
- Chain, weakest-link, independence cluster, contradiction/co-occurrence, defect-impact queries.
- Persist/cite graph retrieval outputs.

**Acceptance:**
- VC package contains graph-derived conclusions and provenance.

#### Slice 90: RAG/pgvector Indexing

**Goal:** Persist embeddings and searchable retrieval records for tenant-scoped evidence.

**Scope:**
- pgvector migrations.
- Live embedding provider abstraction.
- Index parsed spans, OCR text, transcripts, enrichment records, calc outputs, graph summaries.

**Acceptance:**
- Strict run persists embeddings using approved live provider only.
- No fake/deterministic embeddings in strict mode.

#### Slice 91: RAG Retrieval In FULL Context

**Goal:** Feed retrieved evidence chunks into extraction/debate/analysis/scoring and exports.

**Scope:**
- Retriever API/service.
- Prompt context integration.
- Source IDs, scores, and retrieval provenance.

**Acceptance:**
- Final package lists retrieved evidence with IDs/scores.
- RAG runtime proof is separate from pgvector connectivity.

### Phase G: Evidence Court And Investment Committee

#### Slice 92: Durable Layer 1 Evidence Trust Court

**Goal:** Produce a durable Validated Evidence Package candidate from evidence, Sanad, defects, calculations, enrichment, graph, and RAG.

**Scope:**
- Layer 1 evidence integrity, contradictions, sanad strength, No-Free-Facts checks.
- Persist VEP candidate, dissent, unresolved uncertainties, and Muhasabah records.

**Acceptance:**
- Layer 1 output is durable and referenced by Layer 2.

#### Slice 93: Distinct Layer 2 IC Challenge

**Goal:** Implement a real second IC challenge layer, not readiness metadata.

**Scope:**
- IC advocate/challenger/arbiter roles.
- Stage-specific weighting.
- Live agents, rounds, challenge categories, dissent, NFF/Muhasabah validation.
- Consume VEP plus enrichment/graph/RAG/calc context.

**Acceptance:**
- Layer 2 outputs are distinct, durable, live-provider-proven, and visible in IC memo/QA brief.

### Phase H: VC Package And UI Review Workflow

#### Slice 94: Full VC Bundle Content

**Goal:** Generate the whole investor-readable package.

**Scope:**
- Executive summary.
- Commercial diligence.
- Financial diligence.
- Risk register.
- IC memo.
- Truth dashboard.
- QA brief.
- Evidence index.
- Run summary JSON.
- Source/provenance appendix.

**Acceptance:**
- Every material assertion links to safe evidence/provenance IDs.
- Financial tables and assumptions are reproducible.

#### Slice 95: API/UI Review Experience

**Goal:** Make the strict run and package reviewable without private reports.

**Scope:**
- Strict readiness UI.
- Run monitor, component modes, blocker details.
- Data-room upload UI.
- Evidence/claim explorer.
- Truth dashboard.
- Debate transcript.
- Human approval/override UI.
- Download/review package UI.

**Acceptance:**
- Fund reviewer can inspect the package and evidence through product API/UI.
- UI/backend contracts are locked by tests.

### Phase I: Production Runtime Reliability

#### Slice 96: Queue, Retry, Idempotency, Rate Limits, And Redis Decision

**Goal:** Make long expensive runs reliable and controllable.

**Scope:**
- Queue/resume/retry/cancel.
- Idempotency on expensive mutation endpoints.
- Duplicate-run safety.
- Rate limits/provider budgets.
- Redis/cache/worker role decision.

**Acceptance:**
- API and worker paths are consistent, tenant-scoped, retry-safe, and observable.

#### Slice 97: Webhooks And Lifecycle Events

**Goal:** Emit lifecycle events for runs, packages, gates, deliverables, and failures.

**Scope:**
- Durable outbox.
- Signing/retry policy.
- Safe payload schemas.
- Audit metadata.

**Acceptance:**
- No webhook creation can break audit after a successful mutation.
- Events contain no raw private content or secrets.

### Phase J: Enterprise, Governance, And Release

#### Slice 98: Auth, ABAC, Compliance, And Data Governance

**Goal:** Close enterprise controls that affect go-live readiness.

**Scope:**
- SSO/JWT/MFA decision.
- Durable ABAC assignments/groups.
- Break-glass workflow.
- Data residency.
- BYOK/KMS.
- Retention, legal hold, deletion/export workflows.

**Acceptance:**
- Tenant isolation and compliance workflows are durable and tested.

#### Slice 99: Prompt, Dataset, OpenAPI, Release, And Observability Governance

**Goal:** Lock production governance around prompts, datasets, contracts, and deploys.

**Scope:**
- Prompt registry validation and promotion.
- Prompt/model audit linkage.
- GDBS benchmark command and drift thresholds.
- `.local_reports` reconciliation log.
- Quarantine policy.
- OpenAPI/schema/client contract lock.
- Monitoring/alerts/SLOs.
- Backup/restore.
- Incident runbooks.
- Release manifest, Docker, K8s, Terraform promotion gates.

**Acceptance:**
- Production release has contract, observability, and recovery evidence.

### Phase K: Private Real Example And Fund Acceptance

#### Slice 100: Strict Real Example Dry Run

**Goal:** Run private `real_example` locally in strict mode and capture only safe summaries.

**Scope:**
- Product APIs and strict readiness only.
- `scripts/run_real_example_gate.py --root real_example --dotenv .env --require-full-live --bundle-format all --no-secret-output`.
- Resume ledger, timeouts, resource controls.
- Safe blocker ledger.

**Acceptance:**
- `strict_full_live.may_proceed=true` before execution.
- All canonical steps complete.
- Zero unintended deferred evidence classes.
- Live health/provenance for every external component.
- Persisted export URIs.
- No secret or private content leakage.

#### Slice 101: Real Example Blocker Fixes

**Goal:** Fix only blockers observed in Slice 100, without broadening scope.

**Scope:**
- One blocker class per patch unless strongly coupled.
- Re-run strict private gate after each fix.

**Acceptance:**
- Private gate passes structurally and safely.

#### Slice 102: Final VC Quality Review

**Goal:** Confirm the output is actually VC-reviewable, not merely technically complete.

**Scope:**
- Manual review of executive summary, thesis, merits, risks, financial diligence, commercial diligence, IC recommendation, evidence appendix, challenge/dissent, and unresolved uncertainties.
- Fund-review checklist.
- No raw private data in shareable artifacts unless explicitly approved.

**Acceptance:**
- A VC reviewer can inspect the output and judge whether the financial and commercial conclusions are correct.

## 6. Execution Protocol For Every Slice

Each slice must follow this protocol:

1. Start with a read-only truth pass from merged `origin/main`.
2. Use a fresh worktree with `superpowers:using-git-worktrees`.
3. Run a local reuse/discovery pass before writing tests or creating files:
   - Search scripts: `rg --files scripts`, `rg --files | rg "(script|cli|runner|gate|probe|health|export|package|ingest|run)"`.
   - Search implementation: `rg "<feature keyword>" src tests docs scripts`.
   - Search prior slices and reports: `rg "<slice/component keyword>" .local_reports docs tests src`.
   - Identify existing APIs, services, fixtures, runners, and validation commands that can be extended.
   - Record the chosen existing entry point in the slice handoff or PR body.
4. Run a capability/tooling check before implementation:
   - Load and follow all relevant Superpowers skills for the task type, not only the one that is easiest to remember.
   - Use Supabase and Supabase Postgres guidance for any Supabase, Postgres, RLS, pgvector, storage, migration, tenant isolation, or database performance work.
   - Use GitHub plugin/app tooling for PRs, CI status/logs, review comments, merge verification, and branch/PR metadata.
   - Use available MCP/app connectors when they provide safer or more authoritative project, GitHub, Supabase, or documentation access than ad hoc shell/API calls.
   - Use subagent/review functions when the slice has separable review surfaces, such as leakage, semantic honesty, validation practicality, security, tenant isolation, or UI/API contract review.
   - Use local functions/tools appropriately: `rg` for discovery, `multi_tool_use.parallel` for independent reads/checks, `update_plan` for substantial multi-step work, and existing repo scripts before creating new ones.
   - Record the capability check in the slice handoff or PR body: tools used, tools intentionally skipped, and why.
5. Use the relevant plugin/skill:
   - Superpowers for brainstorming, planning, TDD, review, verification, and branch finish.
   - Supabase/Postgres guidance for Postgres/RLS/pgvector/storage design.
   - GitHub tools only for PR, CI, merge, and review comments.
6. Write RED tests first.
7. Verify RED failure.
8. Implement the smallest production-path change by extending existing code where possible.
9. Run focused, adjacent, static, forbidden, and diff checks.
10. Use code-reviewer and validation-review before commit, and explicitly ask them to check for duplicate scripts/helpers, bypassed existing code paths, missed plugins/skills/MCPs, and missing validation tools.
11. Commit only intended files.
12. Open narrow PR.
13. Wait for CI.
14. Merge only when green.
15. Verify merge commit, origin/main, clean worktree, and no next slice artifacts.

## 7. Allowed Final Claim

Only after Phase K passes may the project claim:

"IDIS ran a strict full-live private `real_example` data room through the product API/worker path with live providers, durable tenant-scoped storage, OCR/media handling, BYOL enrichment, calculations, Neo4j, RAG, Layer 1 and Layer 2 debate, persisted VC package exports, and safe reviewable output."

Before that, every claim must name the exact boundary proven and the exact boundaries not proven.

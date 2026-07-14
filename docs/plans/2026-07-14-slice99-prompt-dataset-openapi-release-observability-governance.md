# Slice99 Implementation Plan - Prompt, Dataset, OpenAPI, Release, And Observability Governance

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans / task-by-task RED-first TDD.
> Every Python command runs with PYTHONPATH/MYPYPATH pinned to `C:/Projects/IDIS/IDIS-slice99/src`.
> STOP after each task for explicit acceptance. No commit/push/PR without explicit approval.

**Status:** Tasks 1-8 IMPLEMENTED (RED-first, per-task gates green; Tasks 1-7 individually
accepted, Task 8 awaiting acceptance). Uncommitted in the IDIS-slice99 worktree. Consolidated
decisions: docs/architecture/slice99_decisions.md. The Q1-Q6 open questions were answered at
plan approval and are recorded there.

**Goal:** Lock production governance around prompts, datasets, contracts, and deploys so the later
strict `real_example` run (Slice 100) is honest, observable, recoverable, and release-gated.
Master plan source: `docs/IDIS_FULL_LIVE_MASTER_PLAN_V2.md` Phase J, Slice 99 (lines 457-474).
Acceptance (master plan): "Production release has contract, observability, and recovery evidence."

**Base:** fresh worktree `C:\Projects\IDIS\IDIS-slice99`, branch
`slice99-prompt-dataset-openapi-release-observability-governance`, exactly `origin/main` `f701be9`
(the Slice98 merge). Zero drift verified at plan time.

**Architecture:** governance-lock slice: no new runtime product features. Every mechanism must be
WIRED into the real path (CI job, strict-readiness check, API endpoint, or release gate) and proven
by tests - an unwired library is not done. All emitters follow the compliance core-audit convention
(schema-valid event, `{safe,hashes,refs}` payload, UUID tenant, `validate_audit_event()` BEFORE
emit, fail-closed). ASCII-only edits. Deny-by-default / fail-closed throughout.

---

## Discovery truth table (read-only pass at f701be9)

| Scope bullet | Exists today | Gap Slice99 closes |
| --- | --- | --- |
| Prompt registry validation + promotion | `idis/services/prompts/registry.py` (fail-closed loader, SemVer, gates) + `versioning.py` (promote/rollback/retire, approvals, atomic pointer, audit events); on-disk `prompts/<id>/<ver>/{prompt.md,metadata.json}` + `prompts/registry.yaml`; `tests/test_prompt_registry.py` | Registry code is UNWIRED (zero constructions in src/); dual formats (`registry.yaml` runtime index vs `registry.{env}.json` governance pointers); no validate command/CI; `evals/` referenced by every `evaluation_results_ref` DOES NOT EXIST; all prompts `DRAFT` |
| Prompt/model audit linkage | Runtime step provenance already records prompt_id/prompt_version (from registry.yaml) + model + sanitized provider_request_id (Slices 82-84, `runs.py:3511-3579`); `prompt.version.{promoted,rolledback,retired}` events in versioning.py + taxonomy 4.12 | Versioning audit events are NOT schema-valid: top-level payload keys (schema requires `{safe,hashes,refs}` additionalProperties:false), `tenant_id="system"` (not UUID), no `validate_audit_event()`; `prompt` resource_type in Python validator but MISSING from `schemas/audit_event.schema.json` enum (deferred Slice98 nit - closes here) |
| GDBS benchmark command + drift thresholds | `python -m idis test gdbs-{s,f,a}` CLI; `evaluation/harness.py` + `benchmarks/gdbs.py` (fail-closed loader, dataset hash); CI `evaluation-harness` job runs GDBS-S validate-mode on `tests/fixtures/gdbs_mini` | NO drift thresholds / pinned baseline / delta gating anywhere (grep: zero matches in harness); eval doc section 9 requires drift tracking (e.g., sanad grade distribution drift) |
| `.local_reports` reconciliation log | `.local_reports/` gitignored; only `real_example_gate_ledger.json` lives there | No reconciliation log exists (repo-wide grep: no matching semantics). Underspecified - see Open Question 1 |
| Quarantine policy | PipelineExecutor quarantine enforced by scattered pins (`test_slice75b...py:2034`, `test_process_queued_runs_canonical.py:60`); RB-02 mentions quarantined documents | No consolidated quarantine policy (doc + single registry + repo-wide guard). Document/malware quarantine has NO runtime implementation - see Open Question 2 |
| OpenAPI/schema/client contract lock | Runtime request validation middleware + `test_api_openapi_validation.py`; Slice95 pins review surfaces to the contract; `ui/src/lib/openapi.ts`; release manifest checksums spec+schemas (go-live line 944) | No contract LOCK: nothing fails when `openapi/IDIS_OpenAPI_v6_3.yaml` or `schemas/*.json` drift without review; no breaking-change guard; no UI-client sync check |
| Monitoring / alerts / SLOs | `monitoring/alerts.py` (8 core alerts per SLO doc 8.2, runbook-annotated, tested), `monitoring/slo_dashboard.py` (10 golden dashboards, tenant-scoped, tested), `observability/metrics.py` (thread-safe counters + `render_prometheus_text`, built for a future scrape endpoint) | ALL UNWIRED: no `/metrics` endpoint, no exports under `deploy/`, k8s `deployment.yaml:30-32` annotates `prometheus.io/path: /metrics` FOR AN ENDPOINT THAT DOES NOT EXIST (deploy claims a surface the app does not serve) |
| Backup/restore | SLO doc 7.1 RPO/RTO targets; go-live 6.2 checklist all UNCHECKED | No backup/restore scripts, no restore-drill test, no runbook |
| Incident runbooks | `docs/runbooks/RB-01..RB-10` exist; SEV-1 alerts pin runbook refs (test_alert_rules) | No backup/restore drill runbook; runbook coverage reconciliation vs alert set |
| Release manifest / Docker / K8s / Terraform gates | CI: `container-build` (digest-pinned Dockerfile, health-check, generates + uploads `release_manifest.json` via `scripts/release_build.py`), `k8s-validate` (kubeconform strict), `terraform-validate`; `deploy/k8s` (8 files) + `deploy/terraform` (4 files) | No PROMOTION gate tying them together: nothing verifies manifest completeness (all sha256 sections), no aggregate release-gate job, no drift guard on the CI wiring itself |

Reuse-before-create verdict: every bullet except backup/restore and the reconciliation log has a
substantial existing entry point to extend. New scripts are limited to backup/restore (none exist)
and are justified in-plan.

---

## Proposed sub-units (each RED-first, each gated, each stops for acceptance)

### Task 1 - Prompt governance integrity + promotion audit-core repair
**Files:** `src/idis/services/prompts/versioning.py`, `schemas/audit_event.schema.json`,
`src/idis/validators/audit_event_validator.py` (confirm), `src/idis/cli.py`,
new `tests/test_slice99_prompt_governance.py`.
- RED: (a) every `prompt.version.*` event validates via `validate_audit_event()` (method POST,
  `/internal/prompts/...` path, `{safe,hashes,refs}` payload, UUID tenant) and emission is
  fail-closed BEFORE the pointer write is considered durable; (b) `prompt` resource_type accepted
  by BOTH the Python validator and the JSON schema enum (closes the deferred Slice98 nit);
  (c) `python -m idis prompts validate` walks `prompts/registry.yaml` + on-disk artifacts through
  the PromptRegistry models and fails closed on any invalid artifact, SemVer violation, dangling
  schema ref, or registry.yaml <-> metadata.json mismatch; (d) every prompt_id referenced by
  runtime code (extraction/debate/scoring/analysis constants) must be registered and valid.
- GREEN: reshape `_emit_audit_event` to the core-audit convention + validate-before-emit; add
  `prompt` to the schema enum; add the `prompts validate` CLI subcommand reusing
  `validate_all_prompts()` + a registry.yaml consistency checker; wire the command into CI `check`.
- Note: dangling `evaluation_results_ref` (no `evals/`) is surfaced by validation as a WARN-class
  finding, not a hard fail, until Open Question 3 is decided.

### Task 2 - Prompt/model runtime linkage lock + promoted-prompt policy (flag-gated)
**Files:** `src/idis/services/runs/strict_provisioning_truth.py` (or the strict-readiness module
discovery names), `src/idis/api/routes/runs.py` (provenance pins only),
new `tests/test_slice99_prompt_model_linkage.py`.
- RED: (a) strict-run step provenance for extraction/debate/scoring includes prompt_id +
  prompt_version + model, and each (id, version) resolves to a valid registered on-disk artifact;
  (b) under new env flag `IDIS_REQUIRE_PROMOTED_PROMPTS=1` (default OFF), strict readiness blocks
  before run creation unless every runtime prompt_id is pointed to by `prompts/registry.prod.json`
  (the governed pointer written by the promotion service) with status PROD - fail-closed, safe
  reason codes; flag off = behavior unchanged.
- GREEN: minimal policy check in strict readiness reading the governed pointer through
  PromptRegistry (first real runtime wiring of the governed loader); no agent-loading rewrite.

### Task 3 - GDBS benchmark command + drift thresholds
**Files:** `src/idis/evaluation/harness.py`, `src/idis/cli.py`, new
`tests/fixtures/gdbs_baseline/gdbs_mini_baseline.json`, `.github/workflows/ci.yml`,
new `tests/test_slice99_gdbs_drift_gate.py`.
- RED: `python -m idis test gdbs-s --dataset ... --baseline <file>` compares the suite report
  (case counts, pass/blocked distribution, dataset_hash, sanad-grade distribution where present)
  against a pinned baseline with explicit thresholds; exceeding a threshold exits non-zero with a
  deterministic drift report; missing/malformed baseline fails closed; the CI evaluation-harness
  job invocation is parsed (capstone-style) to prove the drift gate actually runs in CI.
- GREEN: baseline compare + thresholds in harness; commit the hermetic gdbs_mini baseline; wire
  `--baseline` into the CI job. Scope is hermetic validate-mode drift only (no live-LLM in CI).

### Task 4 - `.local_reports` reconciliation log + quarantine policy consolidation
**Files:** new `src/idis/evaluation/local_reports_log.py` (or extension of the real_example_gate
ledger), `docs/architecture/slice99_quarantine_policy.md`, new
`tests/test_slice99_local_reports_reconciliation.py`, `tests/test_slice99_quarantine_policy.py`.
- RED: (a) a reconciliation log module appends safe, schema-validated entries (artifact name,
  sha256, created_at, safe aggregate counts - never raw content or private paths) to
  `.local_reports/reconciliation_log.jsonl`, and the private-gate entry points write through it;
  (b) a single quarantine registry pins every quarantined module (PipelineExecutor today) and a
  repo-wide guard test fails if any canonical path imports/references a quarantined entry,
  consolidating the scattered slice75b/process_queued_runs pins (which remain).
- GREEN: small module + policy doc + guard test. Pending Open Questions 1-2 before RED.

### Task 5 - OpenAPI/schema/client contract lock
**Files:** new `contracts/contract_lock.json` (committed lock: sha256 of
`openapi/IDIS_OpenAPI_v6_3.yaml` + every `schemas/*.json`), new `scripts/contract_lock.py`
(regen + verify), new `tests/test_slice99_contract_lock.py`.
- RED: (a) lock verification fails when the spec or any schema changes without the lock being
  intentionally regenerated (forces review); (b) breaking-change guard vs the locked spec fails on
  removed paths/operations/response codes and on newly-required request fields; (c) UI client sync:
  every path/operation referenced by `ui/src/lib/openapi.ts` exists in the locked spec.
- GREEN: lock file + verifier + pytest wiring (runs in the hermetic suite, hence in CI `check`).

### Task 6 - Monitoring/alerts/SLOs wiring (honest /metrics)
**Files:** `src/idis/api/main.py` (+ small route module), `src/idis/observability/metrics.py`
(consume as-is), new `deploy/monitoring/` exports, `deploy/k8s/deployment.yaml` truth test,
new `tests/test_slice99_metrics_endpoint.py`.
- RED: (a) `GET /metrics` serves `render_prometheus_text()` output (existing webhook counters +
  new minimal HTTP request/5xx/latency counters recorded by middleware), no tenant content, no
  secrets, no auth bypass of /v1 semantics (endpoint is non-/v1 operational surface like /health);
  (b) alert rules + 10 golden dashboards export deterministically to `deploy/monitoring/` and a
  test pins the export matches the in-code definitions; (c) a deploy-truth test asserts the k8s
  scrape annotation path is actually served by the app (closes the /metrics honesty gap);
  (d) a mapping doc states explicitly which SLO-doc metrics are LIVE vs NOT-YET-EMITTED.
- GREEN: metrics middleware + endpoint + export command; no fake metrics - only measured ones.

### Task 7 - Backup/restore (env-gated Postgres proof)
**Files:** new `scripts/db_backup_restore.py`, `docs/runbooks/RB-11_backup_restore.md`, new
`tests/test_slice99_backup_restore_postgres.py` (env-gated `IDIS_REQUIRE_POSTGRES=1`).
- RED: round-trip drill against the disposable Postgres: backup (schema+data via pg_dump), wipe,
  restore, then verify (a) tenant-scoped row counts match, (b) RLS is still ENABLED+FORCED on
  guarded tables (0024-form), (c) audit_events content intact, (d) app-role (non-superuser,
  NOBYPASSRLS) still denied cross-tenant. Secret-safe: connection info via env only, never logged.
- GREEN: script + runbook (RPO/RTO from SLO doc 7.1) + object-store backup manifest note.
- New disposable container for this slice: `idis-slice99-pgtest`, port 15499 (same recipe as 15498).

### Task 8 - Release promotion gate + acceptance capstone + docs closeout
**Files:** `.github/workflows/ci.yml` (new `release-gate` aggregate job), `scripts/release_build.py`
(completeness), new `tests/test_slice99_acceptance_capstone.py`, both traceability matrices,
this plan doc status, `docs/architecture/slice99_decisions.md`.
- RED: (a) manifest completeness test: `release_manifest.json` must contain sha256 sections for
  source/schemas/openapi/Dockerfile/K8s/Terraform (go-live line 944) - missing section fails;
  (b) capstone parses the ACTUAL ci.yml (not echo text) and asserts: evaluation-harness runs the
  drift-gated command, contract-lock + prompts-validate run in `check`, `release-gate` job needs
  every other job; (c) capstone pins the prompt.version.* emitters to the core-audit convention
  (extends the Slice98 pin class); (d) migration-linearity + audit-contract surface re-pinned.
- GREEN: release-gate job (aggregation + manifest verification), docs reconciliation, decision
  record consolidating Tasks 1-7 decisions.

Task splits are allowed if RED reveals more risk (master plan section 5 rule); no task may broaden
into Slice 100 / real_example / live providers / full-live claims.

---

## RED-first test strategy (cross-task invariants)

- Every task: write failing tests first, verify RED with the exact failure reason, then minimal
  GREEN, then the full per-task gate: import proof, full hermetic pytest, `ruff format --check`,
  `ruff check`, `mypy src/idis --no-incremental` clean, `scripts/forbidden_scan.py --repo-root .`,
  `git diff --check`, change-surface non-ASCII sweep - all with PYTHONPATH/MYPYPATH pinned to this
  worktree's src and reported as pinned.
- Env-gated Postgres tests (Task 7) run under `IDIS_REQUIRE_POSTGRES=1` against the disposable
  container BEFORE closeout (never skipped-and-called-green).
- Full-suite reporting is always relative to the media/OCR dependency baseline (~6 known
  environment-dependent tests; +/-1 flake) - no unqualified "all green" claims.
- No mechanism counts as done until wired into the real path (CI invocation, strict-readiness
  check, served endpoint, or release gate) and proven there.
- Audit emitters follow the compliance core-audit convention and are capstone-pinned.
- Reviewer subagents (at review time) are pinned to Opus - never Fable - per session discipline.

## Explicit open questions (need answers before the affected task starts)

1. **`.local_reports` reconciliation log semantics (Task 4):** proposed interpretation = an
   append-only safe JSONL log inside `.local_reports/` recording every private-gate artifact
   (name, sha256, created_at, safe aggregate counts) so repo-side claims can be reconciled against
   private evidence without leaking content. Confirm or correct.
2. **Quarantine policy scope (Task 4):** proposed = consolidate MODULE quarantine (PipelineExecutor
   et al.) into one policy doc + registry + repo-wide guard test; document/malware quarantine of
   uploaded files is declared an explicit NON-GOAL of Slice99 (no runtime scanning exists; building
   it is a product feature, not governance). Confirm or redirect.
3. **Prompt promotion evidence (Tasks 1-2):** `evals/` does not exist, so no prompt can honestly be
   promoted today. Proposed: `IDIS_REQUIRE_PROMOTED_PROMPTS` ships default-OFF; promotion mechanism
   + validation land now; actually promoting the 15 runtime prompts (which requires real evaluation
   evidence, plausibly generated via the Task 3 benchmark for extraction) is deferred to the
   Slice100 preparation or a follow-up decision. Confirm default-off, and whether generating
   minimal honest eval evidence for promotion is IN or OUT of Slice99.
4. **Drift scope (Task 3):** hermetic validate-mode drift only (gdbs_mini baseline in CI); live
   execute-mode drift explicitly out (no live LLM in CI). Confirm.
5. **/metrics scope (Task 6):** only genuinely measured counters (webhook delivery + new HTTP
   request/5xx/latency), plus an explicit LIVE vs NOT-YET-EMITTED mapping doc against the SLO
   dashboards; unmeasured SLO metrics are NOT faked. Confirm.
6. **Contract lock depth (Task 5):** hash-pin + breaking-change guard + ui/openapi.ts sync check;
   NO OpenAPI client codegen in CI. Confirm.

## Non-goals (hard boundaries)

- No Slice 100 work: no `real_example` execution, no strict full-live claims, no live-provider
  calls in CI, no readiness clearing.
- No new product endpoints beyond the operational `/metrics` surface.
- No document/malware quarantine runtime (pending Q2), no promotion of DRAFT prompts without
  evidence (pending Q3), no Redis/queue changes, no migration (none anticipated; next number 0031
  remains free).

## Acceptance gates (slice-level)

1. Every scope bullet maps to a merged, wired, tested mechanism or an explicitly accepted
   documented decision (no silent drops).
2. Slice99 capstone green and parsing the real CI wiring; release-gate job green on the PR.
3. Full hermetic suite green (vs media/OCR baseline); Task 7 PG tests green under
   `IDIS_REQUIRE_POSTGRES=1`; all statics clean; non-ASCII 0 on the change surface.
4. Both traceability matrices + decision record + plan-doc status reconciled (docs tell the truth).
5. Two independent reviewers (runtime/real-path; docs/CI/footprint honesty) before PR, per the
   Slice98 pattern; stop on any Important/Critical finding.

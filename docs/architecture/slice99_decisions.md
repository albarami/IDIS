# Slice99 - Prompt, Dataset, OpenAPI, Release, And Observability Governance: decision record

Single index of the decisions made across Slice99 Tasks 1-8 (master plan Phase J, Slice 99).
Approved open-question boundaries (Q1-Q6) are recorded inline. Everything is fail-closed,
hermetic where the scope demanded it, and wired into a real path (CI job, strict-readiness
gate, served endpoint, or release gate) - unwired mechanisms do not count as done.

## Task 1 - Prompt governance integrity + promotion audit-core repair
`python -m idis prompts validate` (CI check job) walks `prompts/registry.yaml` + on-disk
artifacts through the governed `PromptArtifact` model. Policy: materialized artifacts must be
fully valid (ERROR), declared-only registry entries and yaml-declared-but-unauthored schema
refs WARN, missing evaluation evidence WARNs (never fabricated - Q3). All 17 on-disk prompt
families are registered (9 runtime scoring/analysis families added with registry keys equal to
the exact runtime id strings, so no runtime code changed). `prompt.version.*` events follow the
compliance core-audit convention (POST `/internal/prompts/...` + status_code,
`{safe,hashes,refs}`, `validate_audit_event` BEFORE emit, pointer rollback on failure) with the
platform sentinel tenant `SYSTEM_TENANT_ID` (all-zeros UUID). `prompt` resource_type is
registered in BOTH the Python validator and `schemas/audit_event.schema.json` (closes the
deferred Slice98 nit); full enum parity is test-pinned.

## Task 2 - Prompt/model runtime linkage + promoted-prompts policy
Canonical runtime prompt surface `RUNTIME_PROMPT_IDS` (extraction + 5 debate + scoring,
test-synced to `runs.py` constants). Provenance builders stamp prompt_id + prompt_version +
model; stamped versions must match the governed registry. `IDIS_REQUIRE_PROMOTED_PROMPTS`
(LITERAL "1" only, default OFF - Q3): when on, strict full-live readiness gains a blocking
`prompt_governance` component unless every runtime prompt resolves through
`prompts/registry.prod.json` at the stamped version with PROD status; flag off leaves the
readiness report byte-identical. Nothing is promoted today (no `evals/` evidence exists), so
the flag blocks the real tree - honest by design.

## Task 3 - GDBS drift gate (hermetic validate-mode only - Q4)
`python -m idis test gdbs-* --baseline <file>`: pinned baseline + EXPLICIT thresholds
(case_count/status_count/expected_grade deltas 0, manifest match required). Dataset identity is
pinned via the portable canonical-manifest sha256 (`evaluation/baseline.py`) because the
loader's `dataset_hash` embeds the resolved filesystem path and can never match cross-machine.
The expected sanad-grade distribution comes from the dataset's declared
`expected_outcomes/*.json`. Missing/malformed baselines fail closed; drift exits non-zero with
a deterministic path-free report; `--execute` with `--baseline` is rejected. CI
evaluation-harness runs the drift-gated command against the committed
`tests/fixtures/gdbs_baseline/gdbs_mini_gdbs_s_baseline.json`.

## Task 4 - Reconciliation log + module quarantine (Q1/Q2)
`.local_reports/reconciliation_log.jsonl`: append-only canonical JSONL, fields restricted to
logical artifact type/id, sha256, created_at, safe integer counts (redaction-blocklisted keys
rejected), SCREAMING_SNAKE codes; path-like values rejected fail-closed with no partial
writes. `run_real_example_gate` appends an entry (summary sha256 + safe counts) at completion.
Module quarantine consolidated in `src/idis/quarantine.py` (registry: `idis.pipeline.executor`
/ `PipelineExecutor`) with a repo-wide guard over `src/` + `scripts/` matching import and
instantiation forms; prose mentions allowed; per-file legacy pins remain as defense in depth.
Document/malware quarantine is explicitly NOT implemented in Slice99 (Q2) - see
`docs/architecture/slice99_quarantine_policy.md`.

## Task 5 - OpenAPI/schema/client contract lock (Q6: no codegen)
Committed `contracts/contract_lock.json`: CANONICAL-content sha256 (CRLF/CR normalized to LF
before hashing - a raw working-tree byte hash proved platform-dependent under git autocrlf and
false-drifted every file on Linux CI; CI fix) of the OpenAPI spec + every
`schemas/**/*.json` (13 locked files total: 12 schemas + the spec) plus a surface snapshot
(59 paths -> methods -> operation id,
response codes, required request fields with one-level `$ref` resolution). Fail-closed verify
(`src/idis/contracts.py`, `scripts/contract_lock.py verify` in the CI check job): hash drift,
unlocked/deleted files, BREAKING changes vs the LOCKED surface (removed paths/operations/
response codes, newly-required request fields), and stale `ui/src/lib/openapi.ts` references.
Drift is resolved only by intentional regeneration (`regen`). `/metrics` is deliberately NOT in
the public OpenAPI contract (operational surface like `/health`).

## Task 6 - Honest /metrics + monitoring exports (Q5)
`GET /metrics` serves the in-process counter registry; `HttpMetricsMiddleware` (outermost)
records `http_requests_total{method,status_class}`, `http_request_5xx_total{method}`, and
`http_request_duration_ms_total{method}` - labels NEVER include request paths (paths embed
tenant/deal ids). REVIEWER REMEDIATION: because `/metrics` is unauthenticated, the webhook
delivery counters are GLOBAL aggregates (the `tenant_id` label was removed - no tenant UUID or
per-tenant volume is scrapeable; per-tenant delivery evidence lives in audit events), the
affected dashboard panel queries the global rate, and label values are escaped per the
Prometheus exposition spec. LIVE metrics = exactly `LIVE_METRIC_NAMES` (3 HTTP + 2 webhook);
`docs/architecture/slice99_metrics_mapping.md` mirrors it (test-enforced) and declares the 31
referenced-but-unmeasured SLO metrics NOT YET EMITTED - unmeasured metrics can never be marked
live. Alert rules + 10 golden dashboards are exported deterministically to `deploy/monitoring/`
(byte-matched to in-code definitions by test). Deploy truth: the k8s `prometheus.io/path`
annotation must be a served route.

## Task 7 - Backup/restore drill (env-gated Postgres)
`scripts/db_backup_restore.py`: env-only connections (never DSN flags, never echoed), per-table
CSV COPY dumps + safe manifest (schema revision, row counts, sha256 - database NAME only).
Restore verifies dump hashes BEFORE touching the database, replays the schema via alembic to
the manifest revision (migrations are the schema source of truth, so RLS ENABLE+FORCE and the
audit-immutability trigger restore exactly), reloads under `session_replication_role=replica`,
and verifies row counts. The drill (seed -> backup -> downgrade-base wipe -> restore) proves
tenant counts, RLS flags, audit JSONB content, and app-role isolation survive. RB-11 documents
RPO 15 minutes / RTO 2 hours (SLO section 7.1) with the honest caveat that the RPO additionally
requires deployment-level WAL/PITR. Disposable container: `idis-slice99-pgtest`, port 15499
(use `postgresql+psycopg2://` URLs in this environment). No migration changes (0031 free).

## Task 8 - Release promotion gate + acceptance capstone
`scripts/release_build.py --require-complete` fails closed unless every checksum section
(source, schemas, openapi, dockerfile, kubernetes, terraform) is a real sha256. New CI
`release-gate` job needs EVERY other job (ui-check, check, postgres-integration,
evaluation-harness, container-build, k8s-validate, terraform-validate) and regenerates +
completeness-checks the manifest. `tests/test_slice99_acceptance_capstone.py` parses the ACTUAL
workflow structure (yaml run steps, echo lines excluded) to pin: the drift-gated GDBS command,
`prompts validate` and contract-lock verification in check, the release-gate dependency set,
the `prompt.version.*` core-audit convention, migration linearity (single head), and
validator/schema resource-type parity.

# Slice 98: Auth, ABAC, Compliance, And Data Governance - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: use `superpowers:executing-plans` to implement this plan
> **task-by-task**. Follow TDD (`superpowers:test-driven-development`): RED -> verify RED -> minimal
> GREEN -> verify. **STOP for explicit approval after every task.** Never commit/push/PR/merge/
> cleanup or start the next task unless explicitly told. **Do NOT start Task 1 until explicitly
> approved** (stop-before-Task-1 gate below).

**Goal (master plan section 442):** Close enterprise controls that affect go-live readiness.
**Scope (verbatim):** SSO/JWT/MFA decision; durable ABAC assignments/groups; break-glass workflow
; data residency; BYOK/KMS; retention, legal hold, deletion/export workflows.
**Acceptance (verbatim):** Tenant isolation and compliance workflows are durable and tested.

**Status:** Tasks 1-9 + the audit-core repair IMPLEMENTED and ACCEPTED (uncommitted; see the STATUS
section below). Docs reconciliation + acceptance capstone + CI wiring complete.

---

## Base state (verified)

- `origin/main = caedadcc570f34674dc4f9068e61c09f03da4ee2` (fetched + pruned; exact match).
- PR #115 (Slice97) **MERGED**; merge commit `caedadc`; main CI run `29203400135` **success** at
  `caedadc` (7/7 jobs).
- Worktree: `C:/Projects/IDIS/IDIS-slice98`, branch `slice98-auth-abac-compliance-data-governance`,
  clean at `caedadc`. The dirty `C:/Projects/IDIS/IDIS` checkout was not touched.

## Instructions followed

- No repo-level agent instruction file exists (no `CLAUDE.md`/`AGENTS.md`); the governing protocol
  is master plan section 521-544 (read at `caedadc`): read-only truth pass from merged `origin/main`,
  fresh worktree, reuse/discovery pass before creating files, capability check recorded, RED-first
  TDD, smallest production-path change extending existing code, focused/adjacent/static/forbidden/
  diff gates, independent review before commit, narrow PR, merge only when green.
- Session disciplines carried forward: pin `PYTHONPATH`/`MYPYPATH` to this worktree's `src` for
  every gate command and say so in reports; run the env-gated `*_postgres.py` set under
  `IDIS_REQUIRE_POSTGRES=1` against a real disposable Postgres locally before any PR (the full
  suite silently skips them); never report unqualified "full suite green" (media/OCR dep baseline);
  durable + cross-replica for any go-live state (never in-memory per-process); deliver + WIRE +
  prove through the real request/run flow; `emit_run_signal` is not a sanitizer.

## Discovery result - what already EXISTS (do not rebuild)

The headline: **most of Slice98's machinery already exists as working, tested modules.** The
genuine gap is durability + management workflows + one enforcement runner, plus two decisions.

| Area | Exists (file refs) | State |
|---|---|---|
| SSO / OIDC / JWT | `src/idis/api/auth_sso.py` - `load_oidc_config`, JWKS fetch+cache, RS/EC verify, `validate_jwt`, `SsoIdentity` (claims: tenant_id, user_id, roles, data_region, policy_tags); dual Bearer/API-key path in `api/auth.py` | **Working, wired** |
| API-key auth | `api/auth.py` - `IDIS_API_KEYS_JSON` registry, `TenantContext(tenant_id, actor_id, roles, data_region, actor_type)`, fail-closed 401 | Working (env-config identity) |
| RBAC | `api/policy.py` (`POLICY_RULES`, 6 roles, deny-by-default), `api/middleware/rbac.py` | Working, wired |
| ABAC decision engine | `api/abac.py` - `check_deal_access(_with_break_glass)`, assigned/group->allow, unassigned-ADMIN->break-glass, AUDITOR-mutation->deny; `DealAssignmentStore` **Protocol**; claim->deal + run->deal resolvers (Postgres resolver exists) | **Logic working; store in-memory only** |
| Break-glass core | `api/break_glass.py` - HMAC time-bound token (<=3600s), actor+deal binding, mandatory `break_glass.used` CRITICAL audit (fail-closed), consumed in `rbac.py` | Working; **no issuance route, no durable record** |
| Data residency | `compliance/residency.py` + `api/middleware/residency.py` (registered `main.py`) - fail-closed 403 incl. unset service region; no existence leak | **Working, wired**; `data_region` is a **JWT claim with `"default"` fallback - not durable** |
| BYOK | `compliance/byok.py` - `BYOKPolicyRegistry` (**in-memory module-global**), configure/rotate/revoke (audit-fatal), `require_key_active` Class2/3 gate; `storage/compliant_store.py` `ComplianceEnforcedStore` wraps ObjectStore, wired into ingestion + document reads; `.byok-evidence.json` sidecar | Working, wired; **in-memory; no real KMS/crypto (alias metadata only)** |
| Retention / legal hold | `compliance/retention.py` - `RetentionPolicy` + `DEFAULT_RETENTION_POLICIES` (audit 7y, `hard_delete_allowed=False`), `evaluate_retention()` (**pure fn, zero callers**), `LegalHoldRegistry` (**in-memory**), apply/lift (CRITICAL audit, fatal), `block_deletion_if_held` -> 403 | Primitives working; **no enforcement runner; holds not durable** |
| Deletion | `api/routes/documents.py::delete_document` - hold-checked, via `ComplianceEnforcedStore.delete` (the single non-bypassable hold-aware boundary) | Single-document only |
| Export | `deliverables/export.py` (PDF/DOCX + audit appendix), `storage/object_store.py` ABC, data-room packaging (`services/data_room/*`, migration 0020) | Reusable; **no compliance/tenant-data export bundle** |
| Audit immutability | `audit_events` append-only DB trigger (migration `0001:144`); INSERT-only sinks | Working - deletion workflows cannot erase their own trail |
| `overrides` table/route | migration 0009 + `routes/overrides.py` - PARTNER business override (ADR-012) | Working - distinct from security break-glass |
| Durable identity | `tenants` table = `tenant_id, name, created_at` **only** (migration 0005); runs carry `created_by_actor_id/_type` (0019) | **No `data_region` column; no users/groups/assignments tables** |
| MFA | Docs only (`docs/07_Tech_Stack:100` "MFA enforced via IdP MUST", threat model, audit taxonomy `auth.mfa.failed`) | **Zero code - the genuine "decision" item** |

## Genuinely missing (the real Slice98 work)

1. **Durable ABAC assignments/groups** - no `PostgresDealAssignmentStore`, no
   `deal_assignments`/`groups`/`group_memberships` migrations; assignments vanish on restart.
2. **Durable compliance state** - BYOK policies and legal holds are process-local dicts; no
   `byok_policies`/`legal_holds` tables; not cross-replica, not RLS-isolated.
3. **Durable `tenants.data_region`** - residency enforces a JWT claim against env config with a
   `"default"` fallback; no DB source of truth.
4. **Management workflows (routes)** - nothing can configure assignments, groups, BYOK keys, or
   legal holds through the product API; primitives are test-only.
5. **Break-glass issuance workflow** - no route to request a token, no durable record of grants.
6. **Retention enforcement** - `evaluate_retention()` has zero callers; no janitor exists (the
   Slice96 `delete_expired` is opportunistic-per-request; Slice97 `delete_terminal` is unwired).
7. **Deletion/erasure + compliance export workflows** - beyond single-document delete.
8. **MFA decision** - undecided in code.
9. **Matrix staleness** - DR-001/SEC-001/SEC-002 marked "Planned" though partially implemented;
   DR-001 cites a non-existent `src/idis/models/tenant.py`; no requirement IDs exist for
   ABAC/SSO/MFA/break-glass.

## Whole-Codebase Reuse Inventory (read from `C:/Projects/IDIS/IDIS-slice98` @ `caedadc`)

Discovery ran over the entire local worktree (`rg --files` map: 421 src, 467 tests, 99 docs, 22
scripts, 12 deploy, 13 schemas, 1 openapi spec + keyword sweeps for auth/jwt/sso/mfa/rbac/abac/
role/permission/policy; tenant/org/workspace/isolation; break_glass/override/admin/emergency; 
audit/compliance/retention/legal_hold/deletion/export; residency/region/kms/byok/encryption/key; 
rls/migration/metrics/event/webhook/outbox), not only Slice96/97 knowledge. Verdicts:

| Existing thing (exact path) | Verdict for Slice98 |
|---|---|
| `src/idis/api/auth_sso.py` (OIDC/JWT: JWKS, verify, `SsoIdentity`) | **REUSE as-is**; extend only with the MFA-claim check (Task 4) |
| `src/idis/api/auth.py` (API-key registry, `TenantContext`, dual Bearer/key path) | **REUSE**; touch only the `data_region` `"default"` fallback (Task 3) |
| `src/idis/api/policy.py` (`POLICY_RULES`, deny-by-default) | **EXTEND** with new admin operationIds; never add a second policy engine |
| `src/idis/api/middleware/rbac.py` (RBAC->ABAC->break-glass pipeline) | **REUSE unchanged**; it already calls the assignment-store seam |
| `src/idis/api/abac.py` (`DealAssignmentStore` Protocol, `set_deal_assignment_store`, decision rules, Postgres claim/run resolvers) | **EXTEND**: add the missing `PostgresDealAssignmentStore` + default selection; do NOT re-implement decision logic |
| `src/idis/api/break_glass.py` (HMAC token create/validate, CRITICAL audit) | **EXTEND** with issuance route + durable grants; do NOT rewrite token mechanics |
| `src/idis/api/routes/overrides.py` + migration `0009` (`overrides` table - PARTNER business override, ADR-012) | **AVOID overlap**: distinct from security break-glass; leave untouched |
| `src/idis/api/routes/tenancy.py` (`GET /v1/tenants/me` only) | **EXTEND** if a tenant-region admin surface is needed (Task 3); no tenant CRUD exists to collide with |
| `src/idis/compliance/residency.py` + `src/idis/api/middleware/residency.py` (fail-closed pin, wired in `main.py`) | **REUSE**; extend only the region *source* (durable column precedence) |
| `src/idis/compliance/byok.py` (`BYOKPolicyRegistry` in-memory, audit-fatal ops, `require_key_active`) | **EXTEND**: durable backend behind the SAME interface; keep the in-memory twin for tests |
| `src/idis/storage/compliant_store.py` (`ComplianceEnforcedStore` - the single hold/BYOK-aware storage boundary, wired into ingestion + reads) | **REUSE as the only deletion/storage gate**; erasure (Task 8) must route through it, never bypass |
| `src/idis/compliance/retention.py` (`evaluate_retention` pure fn, `LegalHoldRegistry` in-memory, `block_deletion_if_held`) | **EXTEND**: durable holds + the janitor caller; keep primitives |
| `src/idis/persistence/repositories/enrichment_credentials.py` + migration `0011` (**existing encryption-at-rest pattern**: `IDIS_ENRICHMENT_ENCRYPTION_KEY`, `encrypt_credentials()`, `ciphertext` column, fail-closed when PG configured, `rotated_at`/`revoked_at`) | **REUSE the pattern verbatim** for any Slice98 secret-bearing table; this is the repo's KMS-boundary precedent (env-supplied key = the seam a real KMS would fill) |
| `src/idis/services/enrichment/byol_credentials.py` (`ByolCredentialRepository` Protocol, durable/in-memory twins, provider health) | **MIRROR the shape** for new durable registries; do not duplicate its helpers |
| Slice96/97 durable-store twins (`src/idis/idempotency/postgres_store.py`, `src/idis/persistence/repositories/webhook_outbox.py`) | **MIRROR** (conn standalone-or-in-tx, RLS, unique-index idempotency) |
| `src/idis/services/webhooks/dispatcher.py` (`WebhookDispatcherWorker`: off-loop `asyncio.to_thread`, fail-safe tenant scoping, startup wiring in `main.py`) | **MIRROR** for the retention janitor (Task 7) |
| Slice96 `delete_expired` (opportunistic) + Slice97 `delete_terminal` (unwired) | **WIRE** into the janitor rather than adding new cleanup paths |
| `src/idis/audit/*` + migration `0001:144` append-only trigger; `validate_audit_event`; dispatcher `_record_outcome` v6.3 event shape | **REUSE** for every new admin/compliance mutation event |
| `src/idis/observability/{runtime_signals,metrics}.py` | **REUSE** for janitor/erasure safe-shape counts (callers pass IDs/counts/codes only) |
| `src/idis/services/webhooks/safe_payload.py` | **REUSE** for any run-derived data crossing a boundary |
| `openapi/IDIS_OpenAPI_v6_3.yaml` | **EXTEND** with new admin operations (the OpenAPI middleware validates operationIds against it) |
| `deploy/k8s/configmap.yaml` / `deploy/terraform/*` (env incl. `IDIS_SERVICE_REGION`) | **READ-ONLY reference** for env names; deployment changes out of slice scope |
| Env-gated PG test harness (`tests/test_slice97_webhook_outbox_postgres.py` pattern: fail-not-skip, alembic-to-head, admin-truncate, app-role invariant, `pg_policies` checks) + `scripts/pg_bootstrap_ci.py` | **MIRROR** for every new `tests/test_slice98_*_postgres.py` |
| `scripts/run_postgres_integration_local.py` (hardcoded list omits slice96/97 files - pre-existing Minor) | **AVOID relying on it**; use the CI file list; optionally note for a future fix |
| `.cursor/` (rules/commands for "Whale Hunter v3.1" - a **foreign project's** files committed here) | **AVOID/IGNORE**: not IDIS instructions, not followed, not touched by Slice98 (pre-existing repo artifact; flag to owner separately) |

**Pattern-conflict check (required):** no two conflicting implementations were found - one policy
engine, one residency enforcement path, one break-glass mechanism (distinct by design from the
`overrides` business feature per ADR-012), one encryption-at-rest pattern. The single design
*tension* found - `auth.py`'s `data_region="default"` fallback vs. fail-closed residency - is a
known gap addressed by Task 3, not a pattern conflict; no stop required.

## Local codebase reconciliation (`C:/Projects/IDIS/IDIS`, read-only)

Reconciled against the actual local checkout, not only this worktree:

- **Local state (verified):** branch `phase-2-0-full-system-wiring-baseline` @
  `76c5fe565d17a309e3a5878c040c0e69b9859862`, **251 commits behind** `origin/main`
  (`caedadcc...`), **dirty (29 files)** - treated strictly as a reuse/discovery source, never an
  implementation base. The dirty files touch **no** auth/ABAC/BYOK/retention/residency/break-glass
  surfaces (grep-verified), so no unmerged local work is relevant to Slice98.
- **Workspace invariants (`.windsurf/rules/idis-workspace-rules.md`, local-only/untracked -
  absent from this worktree but binding):** No-Free-Facts; deterministic numerics; sanad
  integrity; audit completeness (mutation audit failure = request failure; append-only); tenant
  isolation structurally via RLS with no existence oracle; **fail-closed everywhere** (deny by
  default, no `return True` defaults in validators/gates, no bare `pass` in `except` in `src/`);
  layer flow `api/ -> services/ -> persistence/ -> models/` never reversed; middleware order
  preserved; `/v1` prefix + Idempotency-Key; UUID PKs; **Alembic for every schema change**;
  forbidden in `src/`: `print()`, `import *`, raw (non-parameterized) SQL, naive `datetime.now()`,
  unvalidated `json.loads` on LLM output, `TODO` without task ID; pre-commit command set
  `make format && make lint && make typecheck && make test && make check`. All Slice98 tasks must
  honor these; they reinforce (and none contradict) this plan. `.cursor/` is foreign Whale-Hunter
  material (ignored); `.claude/settings.local.json` is permissions only.
- **Per-finding verification (local tree @ 76c5fe5):** SSO/JWT present (`api/auth_sso.py`,
  `api/auth.py`) [x]; ABAC present with in-memory-only `InMemoryDealAssignmentStore`
  (`api/abac.py`, RBACMiddleware) [x]; break-glass core present, no durable grant/issuance
  workflow [x]; ResidencyMiddleware wired in `api/main.py` [x]; BYOK/legal-hold/retention
  primitives present (`compliance/{byok,retention,residency}.py`, `storage/compliant_store.py`) [x]
; document GET/DELETE already enforce BYOK/hold (`routes/documents.py::delete_document` calls
  `block_deletion_if_held`; **Task 8 erasure must extend/reuse this path, never duplicate
  document deletion**) [x]; deliverable export exists (`deliverables/export.py`; **no second
  export layer**) [x]; local migrations end at `0011_enrichment_credentials.py` (no durable
  ABAC/BYOK/legal-hold/`data_region` migrations exist anywhere - local or `caedadc`) [x]; 
  encryption-at-rest precedent = `persistence/repositories/enrichment_credentials.py` +
  migration 0011 [x]; MFA still docs-only (no `amr`/`acr`/`IDIS_REQUIRE_MFA` enforcement code;
  grep hits are pycache/substring false-positives) [x]; OpenAPI already has `Tenant.data_region`
  (schema line ~1353) and `deleteDocument` (~381); tenancy route is `GET /v1/tenants/me` only [x].
- **Conclusion:** the local tree is a strict subset of this worktree's state (`caedadc` carries
  everything local has, plus migrations 0012-0025 and Slices 96/97). **No finding changes the
  task order or the reuse plan**; the reconciliation strengthens two already-planned constraints:
  Task 8 routes erasure through the existing `delete_document`/`ComplianceEnforcedStore` path,
  and export work reuses `deliverables/export.py` + data-room packaging. Implementation base
  remains this worktree @ `caedadc`.

## Reuse map from Slice96/97 (conventions to apply verbatim)

- **Durable store twins:** in-memory + Postgres repositories behind one Protocol, standalone-or-
  in-tx conn handling - mirror `idempotency/postgres_store.py` and
  `persistence/repositories/webhook_outbox.py`. ABAC/BYOK/legal-hold registries already expose
  clean interfaces, so backends swap without touching callers (the `set_deal_assignment_store`
  seam already exists).
- **Migrations:** next revision `0026` (verify head `0025` at implementation time); the canonical
  RLS form is migration `0024`/`0025`: `ENABLE` + `FORCE ROW LEVEL SECURITY`, `DROP POLICY IF
  EXISTS`, explicit `USING` **and** `WITH CHECK` with the `NULLIF(current_setting('idis.tenant_id',
  true), '') IS NOT NULL AND tenant_id = NULLIF(...)::uuid` guard.
- **Background runner:** mirror `services/webhooks/dispatcher.py::WebhookDispatcherWorker` -
  asyncio poll loop, **`await asyncio.to_thread(...)` for blocking work** (the Slice97 F1 lesson),
  `get_worker_tenant_ids()` fail-safe scoping, errors swallowed, started in `main.py` startup when
  Postgres is configured.
- **Caller-conn discipline:** any best-effort work on a request connection runs inside a
  **SAVEPOINT** (`conn.begin_nested()`) so SQL failures cannot poison the caller transaction, and
  writes commit/roll back WITH the caller (the Slice97 F2/F3 lesson).
- **Audit:** strict v6.3 events built like `dispatcher._record_outcome` (actor SERVICE, validated
  by `validate_audit_event`, safe metadata only); fatal-audit pattern already used by
  `byok.py`/`retention.py` for compliance mutations.
- **Env-gated Postgres tests:** the harness from `tests/test_slice97_webhook_outbox_postgres.py`
  (env-gate fail-not-skip under `IDIS_REQUIRE_POSTGRES=1`, alembic-to-head module fixture,
  admin-truncate isolation, **app-role non-superuser/NOBYPASSRLS invariant test first**,
  `pg_policies` guard-text assertions).
- **Safe shapes:** `emit_run_signal` (not a sanitizer - callers pass IDs/counts/codes only);
  `services/webhooks/safe_payload.py` for any run-derived data crossing a trust boundary.
- **Tests must be plugin-portable:** no `pytest.mark.asyncio` (pytest-asyncio is not a dependency);
  drive event loops with `asyncio.run(...)` inside sync tests (the Slice97 portability lesson).

## Capability check (per protocol section 538)

- **Superpowers skills:** using-git-worktrees (done), writing-plans (this doc),
  test-driven-development + verification-before-completion + systematic-debugging (every task),
  dispatching-parallel-agents + requesting-code-review (closeout reviews), finishing-a-development-
  branch (only on explicit merge approval).
- **Postgres/RLS:** all new tables follow the 0024-form guarded RLS and are proven env-gated
  against a disposable `pgvector/pgvector:pg16` container on a non-5432 port (the user's own
  `idis-postgres` is never mutated), bootstrapped via `scripts/pg_bootstrap_ci.py`.
- **GitHub tooling:** `gh` for PR/CI/merge; at PR time confirm the `check` job's real-Redis test
  runs-not-skips and `postgres-integration` runs the new Slice98 `*_postgres.py` files.
- **Intentionally skipped:** Supabase MCP (self-hosted Postgres via SQLAlchemy); browser/UI tools
  (no UI surface); real IdP/KMS cloud SDKs (decisions recorded as docs/ADR + enforced claims/
  policy seams - see Open Questions; no external cloud dependency added without approval).

## Files likely to be touched during implementation

- **New:** `src/idis/persistence/repositories/deal_assignments.py`; 
  `src/idis/persistence/repositories/compliance.py` (BYOK/legal-hold durable stores; may split); 
  `src/idis/persistence/migrations/versions/0026_*.py` (+ possibly `0027_*` if split by task); 
  `src/idis/api/routes/access_admin.py` (assignments/groups admin); 
  `src/idis/api/routes/compliance_admin.py` (BYOK/legal-hold/break-glass-issuance admin; may
  split); `src/idis/compliance/janitor.py` (retention enforcement worker); 
  `src/idis/compliance/erasure.py` + export-bundle module; 
  `docs/architecture/slice98_auth_abac_compliance.md` (incl. the SSO/MFA + KMS decisions); 
  `tests/test_slice98_*.py` (unit + `*_postgres.py` env-gated twins per task).
- **Modified:** `src/idis/api/abac.py` (default-store selection -> Postgres when configured); 
  `src/idis/api/policy.py` (new operationIds -> role policies); `src/idis/api/main.py` (routers,
  janitor startup); `src/idis/api/middleware/audit.py` (`OPERATION_ID_TO_EVENT_TYPE` entries); 
  `src/idis/validators/audit_event_validator.py` (only if new event-type prefixes are needed); 
  `src/idis/compliance/{byok,retention}.py` (registry backend seams; keep interfaces); 
  `src/idis/api/auth.py`/`auth_sso.py` (MFA-claim enforcement hook; remove/justify the
  `data_region` `"default"` fallback); `.github/workflows/ci.yml` (postgres-integration list); 
  `docs/11_IDIS_Traceability_Matrix_v6_3.md` (SEC-001/SEC-002/DR-001 + new AUTH/ABAC/BG rows); 
  `openapi/IDIS_OpenAPI_v6_3.yaml` (new admin operations - the OpenAPI middleware validates
  operationIds against it).

## Migration and data-risk notes

- **New tables (low risk):** `deal_assignments`, `groups`, `group_memberships`, `byok_policies`,
  `legal_holds`, `break_glass_grants` - all net-new, tenant-RLS (0024 form), no existing rows.
  Unique indexes for idempotent writes (e.g. `(tenant_id, deal_id, actor_id)` on assignments).
- **`tenants.data_region` column (medium risk):** additive nullable column + explicit backfill
  decision. Danger: residency is fail-closed - a NULL region for an existing tenant must not brick
  live traffic. Plan: nullable column; enforcement precedence = durable column when present, else
  current claim behavior (flag-gated cutover); the `"default"` fallback removal is its own guarded
  step. No destructive change; downgrade drops the column only.
- **Deletion/erasure (high inherent risk):** all deletion flows MUST route through
  `ComplianceEnforcedStore.delete` (hold-aware, single boundary); audit rows are protected by the
  DB immutability trigger and `hard_delete_allowed=False` retention class - erasure must prove the
  audit trail survives. The janitor ships **disabled-by-default / dry-run-first** behind an
  explicit env flag; destructive sweeps require the flag AND log safe-shape counts.
- **In-memory -> durable cutover (BYOK/holds/assignments):** nothing durable exists today, so there
  is no data migration - but tests seeded via module-global registries must keep working
  (interfaces preserved; in-memory stays the hermetic dev/test twin).

## Test strategy

- **Unit (hermetic):** in-memory twins for every store; ABAC decision matrix vs the durable store
  interface; MFA-claim enforcement (present/absent/misconfigured); break-glass issuance+consume
  round-trip; janitor logic with fake clock/stores (sync tests, `asyncio.run` where a loop is
  needed); erasure/export logic with fake object store; safe-shape assertions on every new audit
  event (`validate_audit_event` + no url/secret/path leakage).
- **Env-gated Postgres (`IDIS_REQUIRE_POSTGRES=1`, fail-not-skip, disposable container, app-role
  invariant first):** per new table - RLS `pg_policies` guard-text, cross-tenant invisibility,
  no-tenant write block, unique-index idempotency; assignment store proven through the REAL
  RBAC/ABAC middleware path (wire-and-prove: request with/without assignment -> 403/200);
  savepoint discipline for any caller-conn writes; janitor sweep against real rows incl.
  hold-blocked and `hard_delete_allowed=False` classes; erasure workflow proving audit survival.
- **Real-Redis:** not directly exercised by Slice98 features; the full suite still runs the
  Slice96 cross-replica test (keep `IDIS_TEST_REDIS_URL` set for closeout so it runs, not skips).
- **Capstone:** compose the controls end-to-end (SSO-authenticated request -> ABAC durable
  assignment -> residency pin -> BYOK-gated storage -> legal hold blocks erasure -> hold lifted ->
  erasure executes -> audit trail intact) + docs/CI pins.
- **Full suite:** qualified against the known media/OCR dependency baseline; PYTHONPATH pinned.

## CI expectations

- `check` job: full suite green (new unit tests run here; everything plugin-portable);
  real-Redis test runs-not-skips (unchanged service wiring).
- `postgres-integration`: extended with every new `tests/test_slice98_*_postgres.py` (echo list +
  pytest command, mirroring the Slice97 entries) under `IDIS_REQUIRE_POSTGRES: "1"`; capstone pin
  asserts the wiring like Slice97's.
- No new CI services expected (no IdP/KMS cloud calls in tests - decisions are enforced at the
  claim/policy seam with hermetic fakes).

## Task breakdown (TDD; STOP for approval after every task)

1. **Task 1 - Durable ABAC assignments/groups (the core gap).** Migration `0026`
   (`deal_assignments`, `groups`, `group_memberships`; 0024-form RLS; unique indexes);
   `PostgresDealAssignmentStore` satisfying the existing `DealAssignmentStore` Protocol; default-
   store selection (Postgres when configured, in-memory otherwise) via the existing
   `set_deal_assignment_store`/factory seam. RED: in-memory-parity unit tests + env-gated PG tests
   (RLS, idempotency) + **wire-and-prove through the real RBACMiddleware request path** (assigned
   -> 200, unassigned -> 403, group membership -> 200, restart-survival via two store instances).
2. **Task 2 - Assignment/group management API.** Admin-only routes (create/revoke assignment,
   create group, manage membership, list - minimal set), RBAC policy entries, audit mappings,
   OpenAPI entries. RED: route tests incl. deny-by-default for non-ADMIN, audit events validated.
3. **Task 3 - Durable tenant `data_region` + residency source of truth.** Migration adds
   `tenants.data_region` (nullable); residency enforcement prefers the durable value (flag-gated
   cutover, fail-closed semantics preserved); justify-or-remove the `"default"` claim fallback.
   RED: env-gated PG + middleware-path tests for match/mismatch/NULL/flag states.
4. **Task 4 - SSO/JWT/MFA decision (decision + enforcement hook, not an MFA build).** Record the
   decision (expected: MFA enforced at the IdP; IDIS verifies an MFA/`amr`-style claim when
   `IDIS_REQUIRE_MFA` is enabled) in the architecture note; implement the claim check in the
   existing `auth_sso.py` validation path, fail-closed when enabled. RED: claim present/absent/
   disabled-flag tests. *(Blocked on Open Question 1 if the answer differs.)*
5. **Task 5 - Break-glass workflow completion.** Durable `break_glass_grants` (issued/expires/
   consumed, RLS), ADMIN-only issuance route reusing `create_break_glass_token`, consumption marks
   the grant, CRITICAL audit preserved. RED: issuance->use->single-use, expiry, non-ADMIN deny, PG
   durability.
6. **Task 6 - Durable BYOK registry + legal holds + management APIs.** Migrations
   (`byok_policies`, `legal_holds`); Postgres registries behind the existing interfaces (audit-
   fatal semantics preserved); default selection Postgres-when-configured; admin routes
   (configure/rotate/revoke key; apply/lift hold). RED: registry parity, PG RLS, revoked-key
   storage denial through the REAL `ComplianceEnforcedStore` path, hold blocks delete durably
   across instances.
7. **Task 7 - Retention enforcement janitor.** `ComplianceJanitorWorker` (dispatcher-worker
   pattern, off-loop, disabled-by-default env flag, dry-run mode): applies `evaluate_retention`
   per class, wires the existing orphans (`idempotency.delete_expired` beyond opportunistic,
   `webhook_outbox.delete_terminal`), deletes only via the hold-aware store, never touches
   `hard_delete_allowed=False` classes, safe-shape observability counts. RED: unit with fake
   clock; env-gated PG sweep incl. hold-blocked rows.
8. **Task 8 - Deletion/erasure + compliance export workflows (MVP).** Durable erasure-request
   workflow (request -> ADMIN execution; hold-aware; audit-preserving; per-deal scope MVP) and a
   compliance export bundle (tenant/deal documents+claims+deliverables manifest via the ObjectStore
   + data-room packaging reuse). RED: request/execute lifecycle, hold blocks, audit-trail
   survival proof, export bundle content pins. *(Scope boundaries per Open Question 3.)*
9. **Task 9 - Docs reconciliation + acceptance capstone + CI wiring.** Architecture note (all
   decisions incl. MFA + KMS boundary); traceability matrix: fix SEC-002/DR-001/SEC-001 staleness,
   add AUTH/ABAC/break-glass requirement rows; plan status; add all Slice98 `*_postgres.py` files
   to `postgres-integration`; capstone test composing the controls + docs/CI pins.

**Then (post-tasks, each on explicit approval only):** fresh closeout gate -> independent
Reviewer A (runtime/real-path: RLS, ABAC enforcement, janitor safety, erasure safety) + Reviewer B
(docs/CI/footprint honesty, no overclaim) -> stop on Important/Critical -> PR flow -> CI watch
(Redis runs-not-skips; postgres-integration runs the Slice98 durable tests) -> merge and cleanup
only on separate approvals.

## STATUS (updated at Task 9 closeout)

**Tasks 1-8 + the audit-core repair are IMPLEMENTED and ACCEPTED** (each RED-first, per-task
gated, accepted individually). Task 9 (docs reconciliation + acceptance capstone + CI wiring) is
in progress. Work is deliberately UNCOMMITTED on branch
`slice98-auth-abac-compliance-data-governance`, based on `origin/main` `caedadcc` (zero drift).

- Task 1 - durable ABAC assignments/groups (migration 0026). ACCEPTED.
- Task 2 / 2.5 / 2.6 - access-admin API, deal-endpoint ABAC enforcement, authorized-test
  migration. ACCEPTED.
- Task 3 - durable `tenants.data_region` (0027) + JWT "default" fallback removed. ACCEPTED.
- Task 4 - MFA verification hook (`IDIS_REQUIRE_MFA`). ACCEPTED.
- Task 5 - break-glass single-use workflow (0028). ACCEPTED.
- Task 6 - durable BYOK + legal holds + mgmt API (0029), incl. the core-audit repair. ACCEPTED.
- Task 7 - retention enforcement janitor (double opt-in, no migration). ACCEPTED.
- Task 8 - per-deal erasure + per-tenant export (0030). ACCEPTED.

Decisions recorded in `docs/architecture/slice98_decisions.md` (+ the MFA and BYOK/KMS notes).
Open Questions 1 (MFA), 2 (KMS boundary), 3 (erasure/export scope) are resolved there. Next:
closeout gate -> two independent reviewers -> PR -> CI watch -> merge, each on separate approval.

## STOP-BEFORE-TASK-1 GATE (historical - superseded by STATUS above)

**Implementation has NOT started.** This plan is discovery/planning output only. Task 1 begins
only on explicit approval, and every task ends at a STOP for approval.

## Open questions / blockers (need answers before or at the affected task)

1. **MFA decision (Task 4):** confirm the recommended posture - MFA enforced at the IdP, IDIS
   fail-closed verifying an MFA claim when `IDIS_REQUIRE_MFA` is enabled - vs. building any
   first-party MFA (not recommended; contradicts docs' "MFA enforced via IdP").
2. **KMS boundary (Task 6):** today BYOK is policy/metadata + storage gating with no real
   KMS/envelope crypto - but the repo already has an encryption-at-rest precedent
   (`enrichment_credentials`: env-supplied `IDIS_ENRICHMENT_ENCRYPTION_KEY` +
   `encrypt_credentials()`, fail-closed). Recommended: durable BYOK registry follows that exact
   pattern (env-key seam = where a real KMS plugs in), KMS decision recorded in the architecture
   note, no cloud SDK dependency this slice. Confirm, or name the target KMS if real integration
   is in scope.
3. **Erasure/export MVP scope (Task 8):** per-deal erasure + per-tenant export bundle is the
   proposed MVP. Confirm, or widen/narrow (tenant-wide erasure is higher risk and likely needs an
   approval workflow).
4. **Task 2/5/6 route surface:** acceptance says workflows must be "durable and tested" - the plan
   includes minimal ADMIN-only management routes to make that true through the product API.
   Confirm routes are in scope (alternative: repository-level workflows only, weaker claim).
5. **Sequencing note:** Tasks 1-2 (ABAC durability) are the highest-value core and independent of
   the open questions; they can be approved first while 1-4 above are decided.

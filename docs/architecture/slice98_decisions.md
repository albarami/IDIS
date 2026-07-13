# Slice98 - Auth, ABAC, Compliance & Data Governance: consolidated decision record

Single index of the decisions made across Slice98 (Tasks 1-8 + the audit-core repair). Two topic
notes hold the long-form rationale and are linked below; the remaining decisions are recorded here.
All controls are durable + cross-replica (Postgres/RLS), deny-by-default, and fail-closed.

## Linked decision notes
- MFA / SSO: [slice98_mfa_decision.md](slice98_mfa_decision.md) - IdP-enforced MFA, IDIS verifies
  an `amr` claim when `IDIS_REQUIRE_MFA` is enabled; no first-party MFA.
- BYOK / KMS boundary: [slice98_byok_kms_decision.md](slice98_byok_kms_decision.md) - durable
  policy METADATA only (no raw aliases, no crypto, no cloud SDK); documented KMS seam.

## Task 1-2 - Durable ABAC assignments/groups + management API
Deal-scoped ABAC (`api/abac.py`) is deny-by-default: analysts/partners access only when assigned,
unassigned ADMIN requires break-glass, AUDITOR mutation always denied. Assignments/groups are
durable (migration 0026, guarded RLS, composite tenant-scoped keys) behind the `DealAssignmentStore`
seam (in-memory + Postgres twins). ADMIN-only management routes (`routes/access_admin.py`); tenancy
from `RequireTenantContext` only; unknown/cross-tenant resources return a uniform 404 (no existence
oracle, ADR-011). DB resolution error denies (403 `ABAC_RESOLUTION_FAILED`), never allow.

## Task 2.5-2.6 - Deal-endpoint ABAC enforcement
`RBACMiddleware` extracts `deal_id` from the path (Starlette empties `path_params`) so plain
deal-scoped endpoints are ABAC-gated. Authorized tests seed assignments through the real store
seam; cross-tenant reads became a uniform 403 (was a 200/empty pre-ABAC bypass).

## Task 3 - Durable tenant data_region (residency source of truth)
`tenants.data_region` (migration 0027, nullable) is the durable region. Under
`IDIS_ENABLE_DURABLE_RESIDENCY` the residency middleware enforces the durable value (claim
ignored); NULL/missing/DB-error fail closed (403). Flag off = legacy claim behavior unchanged.
**Decision (Open Question 1-adjacent):** the JWT `data_region="default"` fallback was REMOVED -
a missing claim yields `None` and residency denies, instead of a silent "default" sentinel.

## Task 4 - MFA verification hook
`IDIS_REQUIRE_MFA` (default off) + `IDIS_MFA_AMR_VALUES` (default `mfa`). `validate_jwt` requires
the token `amr` to intersect the accepted set after signature/standard/IDIS-claim validation;
missing/non-array/empty-set/no-intersection all deny 401 `mfa_required`. API keys (SERVICE) exempt.
Denial emits one schema-valid `auth.mfa.failed` (resource_type `session`); sink failure still denies.

## Task 5 - Break-glass single-use workflow
Durable `break_glass_grants` (migration 0028); `POST /v1/break-glass/grants` (ADMIN, deal required,
self-issuance). Under `IDIS_ENABLE_DURABLE_BREAK_GLASS` a grant is strictly single-use (atomic
conditional consume). **Decision:** both consumption AND the `break_glass.used` CRITICAL audit are
keyed on `AbacDecisionCode.ALLOWED_BREAK_GLASS` - the override actually supplying access - so an
assigned admin presenting a token neither burns the grant nor emits a false event. Enforcement uses
the full 64-char token SHA-256 (unique per tenant); consume-then-audit (audit failure denies, grant
stays burned).

## Task 6 - Durable BYOK + legal holds + management API
BYOK policies and legal holds are durable (migration 0029) behind store seams; 5 ADMIN-only routes
(`routes/compliance_admin.py`). **Decisions:** metadata-only KMS boundary (see linked note); NO raw
aliases in the DB (hash+length only); dual-layer audit (core domain event + validated
request-shaped middleware event); `lift_hold` is tenant-scoped (`get_for_tenant`) so a cross-tenant
hold id is a uniform 404. Resolution errors deny; audit-before-write.

## Task 7 - Retention enforcement janitor
`services/compliance/janitor.py` (off-loop worker). **Decisions:** destructive work = infra orphans
only (expired idempotency records + terminal webhook-outbox rows); retention-class deletion is inert
under default policies (AUDIT_EVENTS unconditionally protected). LITERAL double opt-in:
`IDIS_ENABLE_COMPLIANCE_JANITOR=1` AND `IDIS_COMPLIANCE_JANITOR_DRY_RUN` exactly `0` (every other
value fail-safe dry-run). `retention.sweep.executed` (HIGH) is emitted+validated before destruction;
failure aborts all of it. No migration.

## Task 8 - Per-deal erasure + per-tenant export
**Decisions (Open Question 3):** per-deal erasure (no tenant-wide) + per-tenant export; erasure
depth = FULL removal including the `deals` row (audit events survive with deal_id references).
Durable `erasure_requests` (migration 0030) with guarded RLS and NO FK to deals (evidence must
outlive the erased deal). The deal-scoped deletion surface is pinned by
`DEAL_SCOPED_TABLE_CLASSIFICATION` (erased/retained/out-of-scope) with a Postgres tripwire test that
fails if a new deal_id table is added unclassified. Holds abort execution before any deletion;
export manifest is sanitized (sensitive-key blocklist). Graph nodes/edges are out of scope (no graph
store exists in code).

## Compliance core-audit convention (Task 6 repair, enforced in the acceptance capstone)
Every core compliance domain emitter (BYOK, legal hold, erasure, export, retention janitor) builds a
schema-valid event with `method: "POST"`, an `/internal/...` path, and a `{safe, hashes, refs}`
payload, and calls `validate_audit_event()` BEFORE `audit_sink.emit` (fail-closed before the state
change). `tests/test_slice98_acceptance_capstone.py` pins this so the class of bug cannot return.

## Audit taxonomy additions (Slice98)
Event prefixes: `auth.` (mfa.failed), `rbac.` (assignment/group management), `break_glass.`,
`byok.`, `legal_hold.`, `retention.`, `erasure.`, `export.` - the event TYPES are enumerated in the
taxonomy (sections 4.13-4.16). Resource types: `session`, `group`, `byok_key`, `legal_hold`,
`retention_sweep`, `erasure_request`, `compliance_export` - registered in BOTH the Python validator
(`VALID_RESOURCE_TYPES`) and `schemas/audit_event.schema.json` (the `resource_type` enum), which are
validated together at emit time.

## Migrations
0026 ABAC assignments/groups; 0027 tenants.data_region; 0028 break_glass_grants; 0029
byok_policies + legal_holds; 0030 erasure_requests. Single linear head = 0030.

# Slice100 Task 7 V19 Erasure Composition Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task by task.

**Goal:** Replace the PostgreSQL production erasure path's app-credential direct deletion with
the frozen `full-strict-v19` dedicated erasure authority, while preserving complete immutable
audit lineage, deterministic crash recovery, least privilege, and the approved Task 7/Task 11
proof boundary.

**Architecture:** The authenticated API route delegates PostgreSQL erasure to one Task-7
composition service. The service writes deterministic attempted/failed/succeeded audit events
through the application transaction, holds the existing deterministic tenant advisory lock on
one dedicated erasure connection across object reconciliation, and calls the hash-bound
`public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)` function in a short transaction on
that same connection. The database function validates the open attempted event, request state,
holds, object-target digest, and reconciled-object count; deletes the CURRENT48 relation set;
and atomically persists `EXECUTED`, `executed_by`, `executed_at`, and exact counts. A retry after
the function commits but before the succeeded event uses those persisted values to reconstruct
the same deterministic terminal event without deleting, reconciling, or recounting.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2.12.5, SQLAlchemy 2, PostgreSQL/Alembic, pytest,
Ed25519-governed Task-7 contract materialization.

---

## Binding constraints

- `full-strict-v18` remains append-frozen, immutable, `INACTIVE`, and superseded.
- V19 changes only the approved erasure composition/authority bytes plus version-dependent
  hashes. The complete invariance wall remains byte-identical: 28 steps, 48 edge rules, 1,756
  routes, 19 extractors, 18 providers, rights routing, table census, erasure ordering, and every
  prior semantic ruling.
- No new table, column, role, environment variable, or flag.
- No live provider call, priced call, commit, push, or PR.
- Every PostgreSQL census, readiness proof, and upgrade -> downgrade -> upgrade assertion runs
  as the application role except setup that inherently requires administrative DDL.
- `0038` consumes only a signed, append-frozen v19 artifact and fixture.
- The erasure-function owner receives no EXECUTE grant on guard-owner hashing helpers. The
  runner carries a hash-bound local realization of the already-reviewed canonical-event hash
  and SHA-1/UUIDv5 algorithms, proved against the Python fixed vectors before publication.
- Task 7 does not claim the complete 27-cut/all-48-rule proof. Task 11 owns that proof after the
  missing Tasks 9-11 producers and FUTURE38 receipt relations exist.

## Task 1: Record the approved authority and proof boundary

**Files:**

- Modify:
  `docs/plans/2026-07-15-slice100-strict-real-example-live-dry-run.md`
- Modify:
  `docs/plans/2026-07-21-slice100-task7-receipt-edge-schema-design.md`
- Modify:
  `.local_reports/slice100/task7_contract_reconciliation_log.jsonl`
- Modify:
  `.local_reports/slice100/task7_v17_contract_history.jsonl`

1. Record v18 as immutable/inactive/superseded and name v19 as the only successor candidate.
2. Record the exact audit event names, outcomes, UUIDv5 namespaces, ordinal/prior-event chain,
   object-target digest, `object_rows_reconciled`, advisory-lock scope, function persistence
   semantics, crash reconstruction, route-only generic-event suppression, production
   unreachability of the legacy executor, and application-role proof requirement.
3. Record the Task 7 checkpoint boundary:
   - schema, guards, ACL, admission, derivation, and existing-producer proofs belong to Task 7;
   - full 27-cut restart plus three-way equality for all 48 rules belongs to Task 11;
   - the exact producer-blocked rule list is
     `E028_PROVIDER_OUTCOME_COMMON`,
     `E029_PROVIDER_FACT_SEMANTIC`,
     `E030_PROVIDER_FACT_DELIVERABLES`,
     `E031_GRAPH_INTERNAL_DOWNSTREAM`,
     `E032_RAG_INTERNAL_DOWNSTREAM`,
     `E033_GRAPH_EXTERNAL_DOWNSTREAM`,
     `E034_RAG_EXTERNAL_DOWNSTREAM`,
     `E035_CONFLICT_INTERNAL_DOWNSTREAM`,
     `E036_CONFLICT_EXTERNAL_DOWNSTREAM`, and
     `E048_PROVIDER_FACT_EXTERNAL_CONTEXT`.
4. Record that the existing final-0038 attempt honestly failed
   `CONSUMER_RECEIPT_INCOMPLETE` because `step_receipt_input_references` is FUTURE38.
5. Validate both JSONL files line-by-line and run `git diff --check`.

## Task 2: Write lineage and contract RED tests

**Files:**

- Create:
  `tests/slice100_task7/test_v19_erasure_lineage_red.py`
- Create:
  `tests/slice100_task7/test_full_strict_v19_materialization_red.py`
- Create:
  `tests/slice100_task7/test_task7_v19_checkpoint_boundary_red.py`
- Modify:
  `tests/slice100_task7/test_task7_void_contract_hash_blocklist.py`

1. Add exact-name validator cases for only:
   `task7.erasure.attempted`, `task7.erasure.failed`, and
   `task7.erasure.succeeded`.
2. Add fixed UUIDv5 vectors using:
   - attempt namespace `4520d8ed-e929-5486-821c-82f8901ae7fa`;
   - event namespace `dd76cee1-28d5-55f8-b21a-eb64bc2e4be4`.
3. Assert first-attempt ordinal `1` with explicit JSON null prior coordinates.
4. Assert an open attempt is reused; a retry after `FAILED` increments the ordinal by exactly
   one and points to the immediate failed terminal event ID/hash.
5. Assert the event hash is SHA-256 over the exact canonical validated stored event, and changes
   for actor, tenant, deal, outcome, timestamp, lineage, counts, or target-set mutation.
6. Assert the object-target digest uses the ordered SHA-256 sequence of URIs without recording
   raw URIs in safe audit/report fields.
7. Assert v18 remains byte-identical and v18 -> v19 differs only in the approved authority,
   ACL/read-closure, version, and dependent-hash paths.
8. Assert all invariance vectors are byte-identical.
9. Assert the Task 11 carry-forward list is generator-derived from v19 selectors whose producer
   relations intersect the frozen FUTURE38 producer relation set and equals the exact ten rules.
10. Run the new tests and retain the expected failures before implementation.

Command:

```powershell
$env:PYTHONPATH = 'C:\Projects\IDIS\IDIS-slice100\src'
python -m pytest -q `
  tests/slice100_task7/test_v19_erasure_lineage_red.py `
  tests/slice100_task7/test_full_strict_v19_materialization_red.py `
  tests/slice100_task7/test_task7_v19_checkpoint_boundary_red.py
```

## Task 3: Write production-composition RED tests

**Files:**

- Create:
  `tests/slice100_task7/test_v19_erasure_service_composition_red.py`
- Create:
  `tests/slice100_task7/test_v19_erasure_api_postgres_red.py`
- Create:
  `tests/slice100_task7/test_v19_erasure_production_reachability_red.py`
- Modify only if shared setup is required:
  `tests/slice100_task7/_postgres_support.py`

1. Prove one dedicated connection acquires the existing deterministic session advisory lock.
   While that lock remains held, a separate short application-role/RLS transaction enumerates
   the complete ordered object target set and digest. The dedicated role retains zero standing
   table privileges and issues only lock/unlock and the admitted function call; passing the
   locked-connection token to the application-role port proves lock-scoped enumeration without
   claiming a cross-connection or cross-system ACID transaction.
2. Prove the attempted event containing that immutable digest is durable before any object
   mutation and before the dedicated callable sees it.
3. Prove no database transaction remains open during external object I/O; the function call
   occurs in a short transaction on that same locked backend; unlock/close releases the session
   lock.
4. Prove one present and one already-absent object both reconcile, producing
   `object_rows_reconciled=2`, with no dishonest `objects_deleted` field.
5. Prove an invocation failure writes the deterministic failed terminal and preserves the retry
   chain.
6. Inject a failure only while persisting the succeeded event after the function committed.
   Assert the first call leaves the request `EXECUTED` with exact persisted counts and an open
   attempt. On retry, assert the same succeeded event is reconstructed from persisted values,
   with zero object operations, zero callable invocation, zero recount, and exactly one
   attempted plus one succeeded event.
7. Through authenticated API + real PostgreSQL:
   - neither legacy PostgreSQL nor in-memory executor is selected;
   - the app role issues no target-table `DELETE`;
   - the app role cannot call the dedicated function (`42501`, zero mutation);
   - direct-SQL app-role cases continue to prove the existing canonical RLS/guard behavior,
     without inventing a blanket DELETE revocation outside the reviewed ACL;
   - the dedicated role calls only the admitted function;
   - attempted and succeeded events are durable.
8. Prove field-level replacement-lineage coverage for actor, tenant, deal, outcome, and
   timestamp before suppressing generic `erasure.executed`.
9. Prove suppression requires the exact route, operation ID, method/path, and durable terminal
   proof. Other mutation routes still emit their existing event, and an execute response without
   replacement lineage fails closed.
10. Add a source/reachability tripwire:
   - PostgreSQL production composition has exactly one Task-7 erasure authority;
   - no production route/factory constructs `PostgresErasureExecutor`;
   - the in-memory executor is selectable only when PostgreSQL is absent.
11. Run the new suites and retain the expected failures before implementation.

## Task 4: Materialize and execute the v19 authority RED gate

**Files:**

- Create:
  `scripts/materialize_full_strict_v19_preimage.py`
- Create:
  `scripts/generate_task7_v19_contract_fixture.py`
- Create:
  `scripts/run_task7_v19_current_guard_census.py`
- Create:
  `scripts/run_task7_v19_signed_census.py`
- Create:
  `scripts/authorize_task7_v19_contract_publication.py`
- Create:
  `scripts/publish_task7_v19_contract.py`
- Create:
  `contracts/full_strict_v19_reviewed_preimage_v1.json`
- Create:
  `tests/slice100_task7/test_run_task7_v19_signed_census.py`
- Create:
  `tests/slice100_task7/test_authorize_task7_v19_contract_publication_red.py`
- Create:
  `tests/slice100_task7/test_publish_task7_v19_contract_atomic_red.py`

1. Transform v18 into an unfrozen v19 candidate with exact forward and reverse projection.
2. Add literal hash-bound bytes for:
   - column-limited `audit_events` read closure;
   - owner update privilege only on
     `request_id,status,executed_by,executed_at,counts`;
   - attempted-event validation and exact three-event/outcome closure;
   - ordinal/prior-event chain and both UUIDv5 domains;
   - a closed local canonical-event-hash and SHA-1/UUIDv5 implementation, with no new helper
     EXECUTE privilege;
   - object-target-set digest and integer `object_rows_reconciled`;
   - atomic `EXECUTED`/actor/timestamp/count persistence inside the deletion transaction.
3. Extend admission guards so every SQL read column, update column, event literal, outcome,
   UUID namespace, and target-set/count field used by the authority exists in the artifact and
   is neither missing nor orphaned.
4. Run legal, illegal, direct-SQL, app-role ACL, crash-state, and no-mutation census cases on
   disposable PostgreSQL.
5. Do not freeze until every application-role census and constructor test is green.
6. Sign the census with the existing governance key, authorize publication, and atomically
   publish fixture-first/artifact-last.
7. Record candidate, receipt, fixture, artifact, and contract hashes in the reconciliation and
   successor-history logs.

## Task 5: Retarget migration 0038 and readiness to frozen v19

**Files:**

- Modify:
  `src/idis/persistence/migrations/versions/0038_full_strict_v16_receipts_and_edges.py`
- Modify:
  `src/idis/runtime/dependency_probes.py`
- Modify:
  `src/idis/runtime/dependency_readiness.py`
- Modify:
  `src/idis/pipeline/contract_registry.py`
- Modify:
  `src/idis/pipeline/task7_edges.py`
- Modify:
  `src/idis/pipeline/task7_native_registry.py`
- Create:
  `tests/slice100_task7/test_migration_0038_v19_authority_red.py`
- Create or modify:
  `tests/slice100_task7/test_task7_v19_erasure_readiness.py`

1. Retarget all v18 runtime and migration authority identities to the published v19 fixture.
2. Install exactly the frozen v19 current function/ACL/read-closure bytes.
3. Prove PUBLIC and app role have no helper/function execute privilege.
4. Prove the owner remains `NOLOGIN`, `NOSUPERUSER`, `NOBYPASSRLS`, has no memberships, has only
   the reviewed column-level reads/updates, and remains constrained by FORCE RLS.
5. Prove readiness through the application role; administrative connections are setup-only.
6. Prove empty and populated upgrade -> downgrade -> upgrade and exact restoration.
7. Prove downgrade refuses before mutation when v19 run-graph/output evidence exists.

## Task 6: Implement the dedicated product composition

**Files:**

- Create:
  `src/idis/compliance/task7_erasure.py`
- Create:
  `src/idis/persistence/erasure_db.py`
- Modify:
  `src/idis/api/routes/erasure_export.py`
- Modify:
  `src/idis/api/middleware/audit.py`
- Modify:
  `src/idis/compliance/erasure.py`
- Modify:
  `src/idis/validators/audit_event_validator.py`
- Modify only if needed for conflict-verifying deterministic insert:
  `src/idis/audit/postgres_sink.py`

1. Add immutable typed lineage/event/value objects and pure canonical UUID/hash helpers.
2. Add a dedicated engine/connection factory from the already-approved
   `IDIS_ERASURE_DATABASE_URL`, with no app/admin fallback.
3. Implement deterministic audit insert-or-verify:
   - absent event ID inserts;
   - existing event ID must match canonical bytes exactly;
   - conflict never silently succeeds.
4. Implement the PostgreSQL orchestration:
   - resolve/reconstruct request and attempt lineage before ordinary already-executed conflict;
   - acquire the session advisory lock on one dedicated connection;
   - rehydrate request state and lineage through the application role after acquiring the lock,
     so a waiter never acts on a stale pre-lock snapshot;
   - enumerate and bind the exact object target set in a short application-role/RLS read
     transaction while the dedicated connection retains the session lock;
   - persist the attempted event with that digest before any object mutation;
   - reconcile objects idempotently;
   - stage only `object_rows_reconciled`;
   - invoke the function in a short transaction on that connection;
   - emit deterministic succeeded, or deterministic failed before deletion commit;
   - release/close the dedicated connection.
5. Implement post-commit terminal reconstruction from `EXECUTED`, `executed_at`, and persisted
   counts without touching objects or invoking the function.
6. Route PostgreSQL execution only through this service. Preserve the historical in-memory twin
   solely for non-PostgreSQL hermetic tests.
7. Prevent process-global overrides from selecting either legacy direct-delete or in-memory
   execution whenever PostgreSQL is configured.
8. Suppress generic middleware `erasure.executed` only after the exact execute route proves its
   Task-7 succeeded lineage is durable.
9. Admit only the exact three Task-7 erasure event names in the audit validator.

## Task 7: Verification and Task 7 checkpoint package

1. Run focused lineage, materialization, publication, migration, readiness, composition, API,
   reachability, and FUTURE38-boundary suites.
2. Run the full Task-7 suite.
3. Run the full PostgreSQL suite in the declared environment.
4. Run the full repository suite in the declared environment.
5. Run:

```powershell
python -m ruff format --check .
python -m ruff check .
python -m mypy src/idis --no-incremental
python scripts/forbidden_scan.py --repo-root .
git diff --check
```

6. Record:
   - every v19 candidate/frozen hash and signed receipt hash;
   - app-role census counts;
   - migration upgrade -> downgrade -> upgrade results;
   - readiness/ACL results;
   - composition/crash-reconstruction/audit-suppression results;
   - declared-environment focused, PostgreSQL, full-tree, and offline-gate numbers;
   - exact Task 11 carry-forward rule list and honest
     `CONSUMER_RECEIPT_INCOMPLETE` boundary.
7. Stop for Task 7 review. Do not commit, push, open a PR, activate v19, fabricate FUTURE38
   producers, or proceed to the next task.

## Supersession record

The app-role census found two PostgreSQL-mechanics defects after this plan was approved:
the isolated scaffold omitted the separately reviewed approved-erasure helper ownership
installation, then the corrected composition proved the runner's atomic `RETURNING` required
SELECT on `executed_by` and `executed_at`. V19 was never signed, frozen, fixture-published,
migration-consumed, or runtime-selected. It is void and permanently inadmissible; its complete
diagnosis and bounded v20 successor plan are recorded in
`2026-07-24-slice100-task7-v20-erasure-returning-acl-closure-implementation.md`.

# Slice100 Task 7 V20 Erasure RETURNING ACL Closure Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task by task.

**Goal:** Correct the two mechanically proven v19 census/ACL defects without changing any
approved erasure, evidence, routing, isolation, or retention semantics.

**Architecture:** The v19 census composition is corrected to install the already-hash-bound
external erasure-helper owner/ACL before executing the dedicated runner. The superseding v20
contract then adds only `executed_by` and `executed_at` to the erasure function owner's
column-level SELECT closure because the atomic terminal `UPDATE ... RETURNING` reads those
persisted columns. A generation-time guard derives every read column from the runner SQL,
including `RETURNING`, and proves it is covered by the reviewed SELECT ACL before a candidate
can be signed or frozen.

**Tech Stack:** Python 3.12, SQLAlchemy 2, PostgreSQL 16, Alembic, pytest, Ed25519-governed
Task-7 contract materialization.

---

## Approved design

The selected design is the least-privilege two-column SELECT correction. It preserves the
single-transaction proof that the returned actor and timestamp are the values actually
persisted with the deletion counts. Two alternatives are rejected:

1. A table-wide SELECT grant would expose unrelated present and future request metadata.
2. Removing the two columns from `RETURNING`, synthesizing them in Python, or querying after
   commit would weaken the atomic persisted-result evidence and the approved crash
   reconstruction contract.

This amendment is inside the standing contract-materialization autonomy because both defects
are already-ruled PostgreSQL privilege/composition mechanics. It introduces no table, column,
role, environment variable, flag, provider call, evidence disposition, or semantic-vector
change.

## Binding invariants

- V19 remains unfrozen, unsigned, unconsumed, and permanently blocklisted.
- V20 differs from v19 only in:
  - artifact/version identity and dependent hashes;
  - the owner SELECT column tuple for `erasure_requests`;
  - the exact `GRANT SELECT (...)` statement containing the two added columns;
  - the generation-time RETURNING-to-SELECT closure authority and its dependent hashes.
- The runner body, update-column tuple, DELETE ordering, request persistence, audit lineage,
  object reconciliation, roles, RLS policies, and erasure relation sets remain byte-identical.
- The invariance wall remains byte-identical: 28 steps, 48 edge rules, 1,756 routes,
  19 extractors, 18 providers, rights routing, table census, CURRENT48/FUTURE38/RUNNER86
  ordering, UUID namespaces, event/outcome set, and every prior semantic ruling.
- Every behavioral PostgreSQL proof runs through the application role or dedicated executor;
  admin is setup-only.
- No migration or runtime consumes v20 until its signed census is green and publication is
  atomic.

## Task 1: Preserve the v19 diagnosis and void the candidate

**Files:**

- Modify:
  `docs/plans/2026-07-15-slice100-strict-real-example-live-dry-run.md`
- Modify:
  `docs/plans/2026-07-21-slice100-task7-receipt-edge-schema-design.md`
- Modify:
  `docs/plans/2026-07-24-slice100-task7-v19-erasure-composition-implementation.md`
- Modify:
  `.local_reports/slice100/task7_contract_reconciliation_log.jsonl`
- Modify:
  `.local_reports/slice100/task7_v17_contract_history.jsonl`
- Modify:
  `tests/slice100_task7/test_task7_void_contract_hash_blocklist.py`

1. Record the first exact failure:
   `TASK7_SOURCE_FREEZE_FENCED` on `document_artifacts`, caused by the isolated census omitting
   the artifact's external helper-owner installation.
2. Record the corrected composition RED/GREEN proof and final runner/tool pin chain.
3. Record the second exact failure:
   SQLSTATE `42501` because `RETURNING executed_by,executed_at` lacked SELECT privilege.
4. Record the disposable hypothesis proof: adding only the reviewed helper owner correction
   and the two SELECT columns makes the unchanged runner complete; rollback restores
   `REQUESTED` and all seeded rows.
5. Add the v19 candidate, prospective-frozen, and prospective-contract hashes to the permanent
   blocklist with `NEVER_FROZEN`, `NEVER_SIGNED`, and `NEVER_CONSUMED`.
6. Name `full-strict-v20` as the only successor candidate.

## Task 2: Add RED admission and exact-diff tests

**Files:**

- Create:
  `tests/slice100_task7/test_full_strict_v20_materialization_red.py`
- Create:
  `tests/slice100_task7/test_task7_v20_returning_acl_closure_red.py`
- Modify:
  `tests/slice100_task7/test_run_task7_v19_census_red.py`

1. Construct a negative v19 fixture from the real candidate bytes and prove the admission
   guard reports a distinct `TASK7_RETURNING_SELECT_CLOSURE_INVALID` code for exactly
   `executed_by` and `executed_at`.
2. Parse the hash-bound runner SQL and derive all `RETURNING` columns without a handwritten
   duplicate list.
3. Prove every column read by predicates, validation queries, and `RETURNING` is present in the
   reviewed owner SELECT closure.
4. Prove every written column remains in the reviewed UPDATE closure.
5. Prove v19 -> v20 differs only in the enumerated version/hash, two-column ACL, and closure
   authority coordinates.
6. Prove the runner body, event/UUID vectors, relation ordering, and complete invariance wall
   are byte-identical.
7. Corrupt each added column and the GRANT statement independently; each corruption must fail
   before fixture generation.

Run the focused RED tests in a clean declared environment and retain the expected closure
failures before materializing v20.

## Task 3: Materialize the unfrozen v20 candidate

**Files:**

- Create:
  `scripts/materialize_full_strict_v20_preimage.py`
- Create:
  `contracts/full_strict_v20_reviewed_preimage_v1.json`
- Create:
  `scripts/generate_task7_v20_contract_fixture.py`

1. Hash-pin the unfrozen v19 source candidate.
2. Project metadata to `full-strict-v20-reviewed-preimage-v1` / `full-strict-v20`.
3. Add only `executed_by` and `executed_at` to the `erasure_requests` SELECT column tuple and
   canonical SELECT GRANT.
4. Add the generation-time SQL-read/RETURNING-to-SELECT closure guard.
5. Recompute only dependent row, section, artifact, and prospective contract hashes.
6. Assert exact reverse projection to v19 after removing the enumerated v20 changes.
7. Assert the full invariance wall and byte-identical runner body.
8. Leave `append_frozen=false`; do not create a fixture yet.

## Task 4: Build and execute the v20 census chain

**Files:**

- Create:
  `scripts/_task7_v20_census_scaffold.py`
- Create:
  `scripts/run_task7_v20_current_guard_census.py`
- Create:
  `scripts/run_task7_v20_signed_census.py`
- Create:
  `scripts/authorize_task7_v20_contract_publication.py`
- Create:
  `scripts/publish_task7_v20_contract.py`
- Create:
  `tests/slice100_task7/test_run_task7_v20_census_red.py`
- Create:
  `tests/slice100_task7/test_run_task7_v20_signed_census.py`
- Create:
  `tests/slice100_task7/test_authorize_task7_v20_contract_publication_red.py`
- Create:
  `tests/slice100_task7/test_publish_task7_v20_contract_atomic_red.py`

1. Reuse the corrected, hash-pinned v19 census mechanics.
2. Prove helper ownership/SECURITY DEFINER/search path before the first guarded delete.
3. Prove the owner has SELECT on exactly the reviewed seven request columns, UPDATE on exactly
   the reviewed five columns, no table-wide privilege, no membership, and no extra relation
   access.
4. Execute missing-attempt, app-role direct denial, rollback atomicity, legal commit,
   malformed retry, legal failed retry, and post-commit terminal reconstruction cases.
5. Prove returned status/actor/timestamp/counts exactly equal the persisted request row.
6. Run the complete 219-case application-role guard census and four production constructors.
7. Sign the green census, verify it against the pinned public key, and publish
   fixture-first/artifact-last atomically.

## Task 5: Retarget uncommitted 0038 and active runtime

**Files:**

- Modify the active v18/v19-bound migration, registry, repository, worker, orchestration,
  completion, readiness, and dependency-probe files identified by the existing Task-7 active
  surface inventory.
- Preserve every historical v2-v19 artifact, script, and test as immutable evidence.
- Add/modify v20 migration, readiness, registry, and active-composition tests under
  `tests/slice100_task7/`.

1. Retarget only active selectors and uncommitted migration 0038 to the published v20 fixture.
2. Install exactly the frozen v20 runner, ACL, helper ownership, and reviewed RLS policies.
3. Prove empty and populated upgrade -> downgrade -> upgrade with exact restoration.
4. Prove downgrade refuses before mutation for persisted Task-7 graph/output evidence.
5. Prove readiness and all catalog observations through the application role.
6. Preserve the Task-11 carry-forward list for the ten producer-blocked rules; do not claim the
   complete 27-cut/all-48-rule proof in Task 7.

## Task 6: Verification and checkpoint evidence

1. Run v20 materialization, census, publication, migration, readiness, erasure composition,
   audit, and Task-11-boundary suites.
2. Run the full Task-7 suite.
3. Run the complete PostgreSQL suite in the declared environment.
4. Run the full repository suite in the declared environment.
5. Run Ruff format/check, mypy, forbidden scan, `git diff --check`, and secret/private-name
   scans.
6. Record the consolidated v2 -> v20 version history, every void/frozen hash, signed receipt,
   census count, migration proof, and exact Task-11 carry-forward rule list.
7. Stop at the Task-7 checkpoint. Do not commit, push, open a PR, make a live provider call, or
   proceed to Task 8/9/10/11 implementation.

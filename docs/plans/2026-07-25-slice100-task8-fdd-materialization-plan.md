# Slice100 Task 8.1 FDD contract materialization implementation plan

> **For Codex:** REQUIRED SUB-SKILL: use `executing-plans` to execute this
> plan task by task. The Slice100 reviewer cadence overrides any generic commit
> advice: stop uncommitted at the literal-artifact review gate.

**Goal:** Preserve the rejected/unconsumed policy-v1 and full-strict-v22/v23
bytes, then materialize and prove the approved append-frozen but `INACTIVE`
FDD formula-policy-v2 and `full-strict-v24` contract bytes without implementing
formula execution, migration `0039`, runtime composition, dashboard, or
deliverable behavior.

**Architecture:** Task 8.1 uses hermetic artifact builders, independent
admission oracles, a whole-artifact identity/hash dependency-graph gate, an
independent fixture verifier, SQL-to-recipe equality, and a literal semantics
conservation census. The read-only operator workbook supplies question text and
source coordinates only. Frozen v21 remains the sole runtime contract-lock
target throughout this gate.

**Tech stack:** Python 3.12, canonical JSON/Unicode/SHA-256, pytest,
OpenPyXL for operator-backed comparison only, Ruff, mypy, Git. PostgreSQL
remains a later changed-authority proof dependency and is held at this gate.

## Boundaries

- Work only in new Task-8 contracts, scripts, tests, fixtures, reviewer-map,
  the three approved Task-8/Slice100 plan documents, the local reconciliation
  log, the narrow Task-8 void/collection-boundary helpers, and approved LF
  entries in `.gitattributes`.
- Do not edit `src/idis/**`, any migration, `contracts/contract_lock.json`,
  frozen v21 bytes, preserved policy-v1/v22/v23 bytes, any other Task-7
  collection/contract surface, or Track-B-owned paths.
- Do not make live provider/model calls, write the operator workbook, mutate a
  durable application database, commit, push, or open a PR.
- Any semantic ambiguity, new table/role/environment variable/flag, weakened
  guard, rights/money/evidence change, or Track-B overlap is a stop-trigger.

## Binding v24 amendment (supersedes v23 execution steps below)

The v23 tasks below are retained as historical execution provenance, not as
instructions to regenerate or publish v23. The rendered v23 artifact, fixture,
contract, and graph identifiers are permanently void and byte-frozen. V24 is
the only successor candidate. Until this amendment's hermetic evidence returns
for review, the previously planned PostgreSQL census is held and publication,
migration `0039`, formula/runtime code, commits, and live calls are forbidden.

Execute RED-first in this order:

1. Extend the permanent void scanner and collection boundary so exact v23
   bytes remain present as rejection evidence but no v23 pair or identifier
   can satisfy publication, migration, or runtime admission. Only an exact
   frozen and fixture-bound v24 pair may retire the pinned v22/v23 test
   inventories.
2. Build v24 only from exact frozen-v21 artifact/contract bytes. Record the
   parent version and hashes in v24 and apply the enumerated six section-3.1
   deltas, section-3.2.2 identity amendment, approved mechanical corrections,
   and policy-v2 binding. Parent mismatch and rejected-parent inputs fail with
   named codes.
3. Replace dashboard/synthetic-special-cased graph discovery with a complete
   walk of every UUID tuple and hash/digest preimage. Keep all four
   non-dashboard injection vectors. The historical non-dashboard cycle was a
   synthetic mutation, not present in unmodified v23 bytes.
4. Create an independent fixture-verification path that does not import the
   production materializer. Add hand-authored golden vectors for row UUID,
   projection hash, payload hash, native identity/output hashes, and final
   output UUID.
5. Generate executable dashboard SQL references from the frozen policy-v2
   recipes and prove decoded-body equality. Any projection-v1, own-output-ID,
   or other superseded field fails admission.
6. Add one literal approved-semantics conservation manifest with named
   bindings for strict period ordering, positive magnitude,
   source-normalization preservation, exact erasure restoration, 100/100
   dashboard completeness, acyclic identity, and policy-v2 identity. Test
   missing and one-byte-drift cases for every entry.
7. Generate the v24 artifact and independent fixture identifiers, run the
   hermetic graph/SQL/conservation census, and stop. Do not run the operator
   PostgreSQL census or atomic publication gate at this boundary.

## Task 1: Pin the approved gate and implementation boundary

**Files:**

- Modify:
  `docs/plans/2026-07-15-slice100-strict-real-example-live-dry-run.md`
- Modify:
  `docs/plans/2026-07-25-slice100-task8-fdd-formula-lineage-design.md`
- Create:
  `docs/plans/2026-07-25-slice100-task8-fdd-materialization-plan.md`

1. Record all eleven approved rulings and the five binding conditions.
2. Pin the Task-8.1-only stop: artifacts, fixtures, mapping, and authority
   census are allowed; formula/runtime/migration work is not.
3. Record the exact actionable incomplete-binding schema, documentary
   completeness requests, direct-signed conventions, and debt scope.
4. Reconfirm the Track-B file boundary and v21 runtime-lock boundary.

**Verification:**

```powershell
git diff --check -- `
  docs/plans/2026-07-15-slice100-strict-real-example-live-dry-run.md `
  docs/plans/2026-07-25-slice100-task8-fdd-formula-lineage-design.md `
  docs/plans/2026-07-25-slice100-task8-fdd-materialization-plan.md
```

## Task 2: Write independent policy admission tests and capture RED

**Files:**

- Create:
  `tests/slice100_task8/_artifact_oracle.py`
- Create:
  `tests/slice100_task8/test_fdd_formula_policy_materialization_red.py`
- Create:
  `tests/slice100_task8/test_fdd_formula_policy_workbook_mapping_operator.py`
- Create:
  `tests/slice100_task8/test_task8_artifact_gate_boundary.py`

1. Define an independent canonical JSON/hash oracle without importing the
   materializer.
2. Assert the protected workbook hash, 79 exact source rows, zero workbook
   formulas, four sheet counts, and before/after byte identity.
3. Assert literal admission for:
   - all 79 rows;
   - exactly ten required anchors and 69 empty requirements;
   - exact formula equations, DAG, expanded bindings, roles, predicates,
     source coordinates, economic coordinates, and no default zero;
   - explicit debt scope and off-balance-sheet exclusions;
   - cited documentary completeness requests;
   - every `DIRECT_SIGNED` convention;
   - the complete deterministic
     `METHODOLOGY_CALCULATION_INCOMPLETE` payload;
   - per-section corruption and all governed hash fields.
4. Assert the mapping projection contains every anchor/binding beside the
   exact workbook question text and regenerates byte-identically.
5. Assert frozen v21, runtime contract lock, migrations, production source,
   and Track-B files remain outside Task-8.1.

**RED command:**

```powershell
$Python = 'C:\Projects\IDIS\IDIS\.venv\Scripts\python.exe'
$env:PYTHONPATH = 'C:\Projects\IDIS\IDIS-slice100\src'
& $Python -m pytest `
  tests/slice100_task8/test_fdd_formula_policy_materialization_red.py `
  tests/slice100_task8/test_fdd_formula_policy_workbook_mapping_operator.py `
  tests/slice100_task8/test_task8_artifact_gate_boundary.py -q
```

Expected RED: named Task-8 policy artifact, fixture, and reviewer map are
absent. Syntax, collection, workbook access, or unrelated imports must not be
the reason for failure.

## Task 3: Preserve policy v1 and materialize corrected policy v2

**Files:**

- Preserve byte-for-byte:
  `contracts/fdd_formula_policy_v1.json`
- Preserve byte-for-byte:
  `tests/slice100_task8/fixtures/fdd_formula_policy_v1_hash_v1.json`
- Preserve byte-for-byte:
  `docs/plans/2026-07-25-slice100-task8-fdd-formula-policy-v1-review-map.md`
- Create:
  `scripts/materialize_fdd_formula_policy_v2.py`
- Create:
  `contracts/fdd_formula_policy_v2.json`
- Create:
  `tests/slice100_task8/fixtures/fdd_formula_policy_v2_hash_v1.json`
- Create:
  `docs/plans/2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md`
- Modify: `.gitattributes`

1. Implement a hermetic builder that reads no environment and imports no
   runtime code, Pydantic, SQLAlchemy, OpenPyXL, or Task-9 module.
2. Derive v2 only from the exact preserved v1 bytes plus the approved bounded
   amendment: v2 identity/hash domains, the acyclic financial row-payload
   recipe, and every policy-bearing methodology external-ID reference.
3. Emit the literal approved v2 policy bytes with pinned canonicalization,
   section hashes, formula/policy hash preimages, and no self-hash cycle.
4. Generate the v2 deterministic reviewer map as a projection of policy bytes;
   verify it independently against the read-only workbook.
5. Assert preserved v1 artifact, fixture, and reviewer-map SHA-256 values before
   and after v2 generation; no v1 byte may change.
6. Publish v2 fixture first and v2 policy artifact second without clobbering
   pre-existing differing bytes.
7. Add LF normalization for the v2 policy artifact.

**GREEN command:**

```powershell
& $Python -m pytest `
  tests/slice100_task8/test_fdd_formula_policy_materialization_red.py `
  tests/slice100_task8/test_fdd_formula_policy_workbook_mapping_operator.py `
  tests/slice100_task8/test_task8_artifact_gate_boundary.py -q
```

## Task 4: Write v23 materialization and fixture tests and capture RED

**Files:**

- Create:
  `tests/slice100_task8/test_full_strict_v23_materialization_red.py`
- Create:
  `tests/slice100_task8/test_task8_fixture_derivation_red.py`
- Create:
  `tests/slice100_task8/test_task8_atomic_publication_red.py`

1. Preserve and blocklist the rejected v22 artifact, fixture, and contract
   identifiers; reconstruct frozen v21 and assert the exact bounded v21-to-v23
   delta plus the enumerated rejected-v22-to-v23 identity correction.
2. Prove the invariance wall: 28 steps, 48 rules, 1,756 routes, 19
   extractors, 18 providers, rights routing, complete relation universe,
   erasure ordering, and Task-11 obligation.
3. Admit only the approved v23 deltas:
   - separate FDD calculation/value/input/output schemas;
   - version-aware Step-7/Step-11/Step-15 selection;
   - dashboard-root equation v2;
   - monotonic partial exact-one guard;
   - CURRENT48 dashboard-row erasure refusal;
   - exact FDD policy artifact/hash binding.
4. Build the dependency graph from every UUID name tuple and every hash/digest
   preimage declared anywhere in the full-strict artifact and bound policy.
   Qualify nodes by artifact, definition/runtime layer, scope, output kind,
   symbol, and source pointer; parse only the closed canonical
   `H`/`CJ`/`ORDERED` grammar; and require an exact source-pointer manifest.
   Reject uncovered/unknown recipe shapes, duplicate or ambiguous producers,
   unresolved governed identity/hash inputs, self-edges, and every directed
   cycle at generation time. The test must not special-case dashboard names.
   Bind ordered-node, edge, typed-leaf, and source-pointer counts plus the
   `identity-hash-dependency-graph-v1` digest into the v23 fixture and census.
5. Require the approved dashboard-row namespace/name tuple, projection-v2,
   row-payload-v2, and five-step acyclic derivation order. Preserve the
   unchanged generic Task-7 output UUID recipe.
6. Prove byte corruption for each changed section alters the final contract
   hash.
7. Prove fixture derivation is byte-identical under `python -I`, an empty
   current directory, copied inputs, and poisoned environment values.
8. Prove fixture-first, policy-before-v23, no-clobber, partial-publication
   recovery, reparse/symlink rejection, stable lock, and v23-last commit point.

**RED command:**

```powershell
& $Python -m pytest `
  tests/slice100_task8/test_full_strict_v23_materialization_red.py `
  tests/slice100_task8/test_task8_fixture_derivation_red.py `
  tests/slice100_task8/test_task8_atomic_publication_red.py -q
```

Expected RED: the named v23 materializer, fixture generator/publisher, artifact,
and fixture are absent.

## Task 5: Prove v23 changed SQL authorities on disposable PostgreSQL

**Files:**

- Create:
  `scripts/run_task8_v23_sql_authority_census.py`
- Create:
  `tests/slice100_task8/test_v23_sql_authority_postgres_census_red.py`

1. Consume exact staged append-frozen v23 bytes, and run the artifact-wide
   dependency-graph admission before loading any strict profile value or
   creating a database. Bind the staged artifact and graph digests in the
   safe census receipt.
2. Load operator credentials, when explicitly enabled, from a private mapping
   containing only `IDIS_STRICT_DOTENV_PATH`; read the admin, application, and
   erasure DSNs only from the ACL-verified profile and never export or print
   them. An absent marker may skip in ordinary CI; a present invalid profile
   fails closed.
3. Migrate a disposable database through `0038`, install only the exact
   artifact-decoded Task-8 authorities, and seed the deterministic 100-row
   graph (`21 CDD + 79 FDD`) from reviewed coverage/policy rows. Dashboard-row,
   root-link, projection, payload, native, and generic output identities use
   the approved v23 recipes; UUID4 and placeholder-hash seeds are forbidden.
4. Execute the preserved 25-case legal/illegal/direct-SQL matrix under the
   application role or dedicated erasure executor as declared. Prove the full
   100-row/set equation, exact-one replacement, CURRENT48 dashboard-row
   erasure refusal, all 19 targets/57 event coordinates, 69 FORCE-RLS tables,
   wrong/unset-tenant read/write denial, exact least-privilege ACLs, zero
   mutation, and both advisory-lock race directions.
5. Execute both authority restoration contracts: restore the exact v21
   current-output helper/guard and erasure function/ACL including revocation of
   the dashboard read grant, then reinstall v23 and require the final catalog
   digest to equal the initial v23 digest. This authority U->D->U proof is not
   migration `0039` and does not discharge Task 8.3's migration proof.
6. Keep admin credentials setup/seed/teardown-only. Catalog, ACL, RLS, legal,
   illegal, and direct-SQL evidence produced as admin is forbidden. Persist
   only stable codes, counts, and hashes—never DSNs, usernames, paths,
   workbook prose, SQL bodies, or raw PostgreSQL messages.

**RED then GREEN:**

```powershell
& $Python -m pytest `
  tests/slice100_task8/test_v23_sql_authority_postgres_census_red.py -q
```

## Task 6: Materialize v23 and atomically publish the four-file gate

**Files:**

- Create:
  `scripts/materialize_full_strict_v23_preimage.py`
- Create:
  `scripts/generate_task8_contract_fixtures.py`
- Create:
  `scripts/publish_task8_contract_artifact_gate.py`
- Create:
  `contracts/full_strict_v23_reviewed_preimage_v1.json`
- Create:
  `tests/slice100_task8/fixtures/full_strict_v23_contract_hash_v1.json`
- Modify: `.gitattributes`

1. Rebuild v23 from frozen v21, policy v2, and only the approved deltas.
2. Bind the final policy-artifact SHA-256 and
   `FDD_FORMULA_POLICY_V2_SHA256`; fixtures are never hash inputs.
3. Run every admission, invariance, corruption, workbook-map, and
   application-role SQL-authority proof before publication.
4. Complete the canonical gate atomically without rewriting append-frozen
   policy-v2 bytes. Before any write, verify the exact pinned policy-v2
   fixture, artifact, and reviewer-map binding as immutable pre-admitted
   prerequisites. Then publish only v23 fixture → v23 artifact, with the v23
   artifact link as the sole commit point. A missing, partial, byte-different,
   aliased, or non-regular prerequisite fails before any v23 write.
5. Keep v23 `INACTIVE` and absent from runtime contract locking.
6. Append safe hash/count-only publication evidence to the local ignored
   reconciliation log.
7. Permanently reject and blocklist the design §3.2.2 v22
   artifact/fixture/contract identifiers and prove none can be selected or
   consumed.
8. The publication receipt distinguishes `preexisting_verified` from
   `created_publication_order`; it must never claim that the already-canonical
   policy-v2 pair was newly created. Fresh disposable-target tests still prove
   the complete four-file ordering and exact final-byte binding, while the
   canonical no-clobber path proves the two-step v23 completion and every
   process-death recovery window.

**GREEN command:**

```powershell
& $Python -m pytest tests/slice100_task8 -q
```

## Task 7: Verification and literal-artifact checkpoint

Run only from the declared environment with the strict live profile absent.

```powershell
Remove-Item Env:IDIS_STRICT_DOTENV_PATH -ErrorAction SilentlyContinue
$env:PYTHONPATH = 'C:\Projects\IDIS\IDIS-slice100\src'
& $Python -m pytest tests/slice100_task8 -q
& $Python -m pytest tests/slice100_task5 tests/slice100_task6 tests/slice100_task7 -q
& $Python -m pytest tests/slice100_task9_periphery -q
& $Python -m ruff format --check scripts tests/slice100_task8
& $Python -m ruff check scripts tests/slice100_task8
& $Python -m mypy src
git diff --check
```

Also run the repository's standard forbidden scan, contract-lock/import
identity checks, and focused artifact hash verification. Confirm:

- no `src/idis/**` or migration edit attributable to Task 8;
- no runtime selection of rejected v22/v23 or inactive v24;
- no Track-B overlap;
- no PostgreSQL census or database mutation at the v24 structural gate;
- no live call, acceptance namespace, commit, push, or PR.

The binding v24 amendment supersedes the historical Task-7 command block above
at this checkpoint. Stop and submit the v24 artifact and independently derived
fixture bytes, policy-v2/reviewer-map bindings, hashes, exact test
counts/timings, RED evidence, hermetic graph/SQL/conservation census, and
boundary proof for explicit review. PostgreSQL authority evidence remains held
for the next authorization.

## Task 8: Full-strict-v24 second-gate checkpoint

The 2026-08-14 final audit is `PASS` for the authorized hermetic,
no-PostgreSQL materialization gate. The verified immutable identifiers are:

- artifact SHA-256
  `70834e33249a414e5dcc744cdc60934d9d2ed6c8ab3f09c64cf3edfa3f05e447`;
- independently derived fixture SHA-256
  `444e4c573e7ba44185efe0b4c39339797b2df91a0fcbf7841129e131e406526e`;
- contract SHA-256
  `f05f17f98ea6a8d0356cef5939b7395fef294eb490a77cf8aeecf1ece25dd164`;
- graph SHA-256
  `5c8b00d2e0930d4c1d5e5a4fbcc1db9ca74f9b28515e48da3a7021a835d84c5f`
  with count vector `67/35/834/67` for
  node/edge/leaf/source-pointer;
- conservation SHA-256
  `635035b2e2a9e88c4cf1a90a1fb4023197f0c0df0bb84e181db2daefddfb36de`
  with `7` semantics and `18` byte receipts; and
- policy/reviewer-map binding SHA-256
  `6e6841d07b1891b6692ba210bc1be55aaee7f40a3bb6ffafdad56e5a21212bb3`.

The derivation parent is exact frozen v21 artifact
`2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd`
and contract
`46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794`.
Policy-v2 is pinned by artifact
`e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064`,
policy
`2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9`,
prerequisite fixture
`b95c426da57a64336da990002201f4d5fdfe616dbae095a93e3aaf9372cf242e`,
and reviewer-map
`98cf84d4498f57465cd61501ea8bbbd93f7507d8628a61d7cbcf9cede459fe2a`
SHA-256 values.

The independent and CLI verification suite passed `65/65`; the CLI result was
`VERIFIED`. Its hermetic census proved `28` steps, `48` rules, `1,756` routes,
`19` extractors, `18` providers, `68` schemas, `34` current-DDL tables, `69`
tenant-RLS tables, and the complete `48 + 38 = 86` erasure relation split.
Task 11 retains `27` restart cuts and `10` undischarged rules.

The consolidated blocker/fix chain is closed:

1. SQL authorities require full-segment consumption rather than prefix
   acceptance.
2. Every identity/hash graph coordinate has complete governed producer and
   source-pointer closure.
3. All seven approved semantic vectors are bound to eighteen literal byte
   receipts.
4. The exact reviewer-map raw bytes participate in every admission and
   independent verification path without prose disclosure.
5. Digest, identity, and reference parsers use a closed, full-consumption
   grammar with differential parity.
6. Generator derive-only is fully admitted and verified but writes nothing;
   write mode verifies the exact immutable policy fixture and exclusively
   creates only an absent v24 fixture.

The canonical v22 fixture path was removed after exact archive SHA-256 proof
of
`4368d86b6bdd270f03d94bbc591a9ee2f6fbc6c36c9c65076cd1549d2a4da56b`.
Its historical void evidence remains blocklisted and reconciled.

Artifact and fixture remain `INACTIVE`; no gate lock or atomic publication
exists. This gate performed no PostgreSQL census or other database call, and
created no migration `0039`, formula/runtime implementation, live call, or
commit.

## Task 9: Task-8.2 requirement-model pre-slice

**Goal:** make the frozen policy-v2 FDD requirement tuple producible and
reloadable through the existing Task-5 production repository without changing
any full-strict-v24 contract byte or any legacy methodology payload byte.

**Files:**

- Modify: `src/idis/methodology/models.py`
- Create: `src/idis/methodology/fdd_formula_policy_v2.py`
- Test: `tests/slice100_task8/test_fdd_formula_requirement_model_preslice_red.py`
- Reuse without changing semantics:
  `src/idis/persistence/repositories/methodologies.py`

1. RED: prove the current model rejects an exact non-empty frozen policy-v2
   requirement at `required_calculations.0.calc_type`.
2. RED: require a frozen, extra-forbid
   `FddFormulaRequirement(formula_ordinal, formula_type, required, scope)`;
   formula type is exactly the ten policy-v2 values and scope exactly
   `cash_flow_control|debt_control|line_item|p_and_l_control|sheet_control`.
3. RED: legacy versions reject the four-field form, the policy-v2 successor
   rejects the two-field form, every mixed tuple fails, and neither shape is
   coerced into the other.
4. RED: pin the actual frozen Task-5 CDD and legacy-FDD deterministic registry
   JSON, every question payload and payload SHA-256, source-trace digest,
   version/registry hashes, and production rehydration bytes before changing
   the model.
5. Implement the disjoint model while keeping the legacy two-field model and
   its default serialization byte-identical.
6. Implement one named successor constructor. It verifies the exact frozen
   workbook SHA-256, policy artifact SHA-256, policy SHA-256, and literal
   79-row coordinate/question join before returning the first successor
   question. Failures expose only stable codes.
7. Feed the returned registry through the unchanged production
   `import_registry -> freeze_version -> get_frozen_version ->
   create_run_selection` path. Prove all four requirement fields survive
   import/freeze/reload byte-for-byte.
8. Regenerate and compare all `68` full-strict-v24 schema snapshots and rerun
   contract-lock/import selection tests. Exact v24 artifact, fixture,
   contract, graph, conservation, and policy-binding identities must remain
   unchanged.
9. Run focused RED/GREEN, Task-5 methodology, Task-8 materialization/schema,
   Ruff, mypy, and diff checks. Do not commit at this pre-slice.

**Focused commands:**

```powershell
$env:PYTHONPATH = 'C:\Projects\IDIS\IDIS-slice100\src'
& $Python -m pytest `
  tests/slice100_task8/test_fdd_formula_requirement_model_preslice_red.py -q
& $Python -m pytest tests/slice100_task5 -q
& $Python -m pytest `
  tests/slice100_task8/test_v24_structural_admission_red.py `
  tests/slice100_task8/test_v24_independent_fixture_verification_red.py `
  tests/slice100_task8/test_v23_fdd_schema_execution_red.py -q
```

This is the only authorized Task-8.2 runtime slice before census. It contains
no formula evaluation, planning, dashboard behavior, migration `0039`, or
activation.

**Execution status (2026-08-14): COMPLETE.** The focused hermetic suite is
`12/12` green, the real migration-`0038` application-role repository
round-trip is `1/1` green in a fresh disposable database, legacy Task-5 bytes
and all `68` v24 schema authorities are unchanged, contract-lock verification
is clean, and two independent reviews report no blocker. No v24 identity
changed and no production selector/planner/runtime composition was wired.

## Task 10: Resume the signed v24 PostgreSQL census

After Task 9 is independently green, continue Decision Record
`T1-20260814-001`:

1. Replace the census's direct question SQL with the named policy-v2
   constructor and unchanged production import/freeze/selection path.
2. Seed one deterministic `21 CDD + 79 FDD` plan as the sole source of every
   database insert and bind both the plan SHA-256 and application-observed
   projection SHA-256 in the signed receipt.
3. Keep every FK/constraint trigger enabled. Use the production source-freeze
   composition for post-`0038` RUNTIME package/file/artifact/document/attempt/
   span roots and prove application-role reload.
4. Create the exact governed Task-7 step coordinates and every required native
   root/output once with final hashes and UUIDs; never bootstrap then update an
   append-only output.
5. Remove every replica-mode bypass and placeholder hash. Independently
   rederive every persisted governed hash and UUID and prove complete FK
   anti-join closure under the application role.
6. Rerun the hermetic census suite and independent review, then run the signed
   operator census only in fresh namespace `v24-postgres-census-003`. Seal
   earlier failed attempts as safe hash/count-only evidence. Always drop only
   a disposable database positively created by the current run.
7. Do not publish, activate, write migration `0039`, or commit until the signed
   receipt verifies independently.

**Tier-2 hold (2026-08-14):** frozen `dashboard-root-equation-v2` reads the
legacy `required_calculations[*].calc_type` identity while the approved
policy-v2 production model contains only the exact four-field
`formula_ordinal|formula_type|required|scope` identity. The named constructor
produces ten required rows and zero `calc_type` keys, so the frozen guard
cannot admit a production-valid result. Permanent RED must stop before strict
profile or database access. No operator census or publication may run until a
reviewer approves the corrected semantic binding.

Delegated decision `T1-20260814-003` applies the settled rendered-candidate
precedent independently of that semantic ruling: full-strict-v24 and its six
identities are permanently void, byte-preserved evidence, and the corrected
candidate label is full-strict-v25. The only pending reviewer decision is the
new version-aware requirement binding itself; no v25 artifact is rendered
before that approval.

Non-dependent root-aware repository composition continues under delegated
decision `T1-20260814-002`: add only the minimal INSERT-time seam needed for
the six already-governed coverage/task/claim/evidence/Sanad/defect relations
to receive final prederived Task-7 root IDs. Preserve every identifier/hash
recipe, trigger predicate, ACL, legacy caller, and schema byte exactly.

Condition-1 classification is closed in master-plan section 1.80. Exactly one
function body changes: the three requirement-JSON reads inside
`dashboard-root-equation-v2`. Its three persisted `c.calc_type` reads and the
three other function bodies that contain only persisted calculation-column
reads remain byte-identical. Eight ACL calc-type column statements also remain
unchanged. Delegated decision `T1-20260814-004` separately adds only the
missing `methodology_questions.question_payload` column SELECT/revoke closure
already required by the frozen equation body; the version-ID columns are
already granted.

Delegated decision `T1-20260814-005` corrects publication topology before any
v25 bytes are rendered. The materializer and independent fixture generator
may return bytes and use explicit noncanonical candidate/temp paths only.
The census hashes those exact staged bytes. Canonical v25 fixture and artifact
paths remain absent until the atomic publisher creates fixture first and
links artifact last as the sole commit point; no transient lock file is used
as historical publication evidence.

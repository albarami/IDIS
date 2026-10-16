# Slice100 Task 8 — FDD formula, lineage, dashboard, and financial-output design gate

> **Status:** ALL ELEVEN SEMANTIC RULINGS APPROVED on 2026-07-25, subject to
> the five binding conditions in section 1.1. The Task-8.1 second-gate
> identity amendment is APPROVED on 2026-08-13: policy v1 is immutable and
> unconsumed and corrected policy v2 is frozen. The 2026-08-14 structural
> ruling permanently voids both rendered full-strict v22 and v23 candidates;
> the corrected successor is full-strict v24 under section 3.2.3. Only
> Task-8.1 RED materialization and hermetic admission/conservation evidence are
> authorized. Fixture publication and the disposable application-role
> PostgreSQL census are held. Formula
> implementation, migration `0039`, repository/runtime composition, durable
> application-data mutation, live provider/model calls,
> commit, push, and PR remain blocked. The permanent Task-7 materialization
> rule still requires an isolated disposable-PostgreSQL application-role
> execution census for each changed SQL authority before any later v24
> publication, after this review gate returns.
>
> **Required protocol after approval:** `test-driven-development`,
> `verification-before-completion`, `receiving-code-review`, `writing-plans`,
> `executing-plans`, and `supabase-postgres-best-practices`. The existing
> Slice100 per-task stop remains binding.

## 1. Outcome and stop condition

Task 8 must replace the current sheet-wide optional calculation hints with a
frozen row-specific financial policy, execute every required calculation from
real governed facts, persist complete calculation and dashboard lineage, and
produce an exact 79-row `financial_diligence.json`.

The design gate was approved on 2026-07-25. The workbook is a diligence
questionnaire, not an executable financial model: it contains no formula cells
and by itself cannot authorize equations, signs, input bindings, tie-out
anchors, or assumptions. The reviewer supplied that explicit semantic
authority. This checkpoint now proceeds only through Task-8.1 RED
materialization and returns the literal bytes for the second review gate.

### 1.1 Approved reviewer conditions

The 2026-07-25 ruling approves all eleven decisions in section 10 and adds
five conditions. Each condition is a literal, hash-bound Task-8.1 artifact
input, not implementation guidance that a runtime may reinterpret:

1. the debt roll-forward balance scope is only `Liabilities:9`; rows 17 and 18
   remain required diligence answers but are deliberately excluded from the
   opening/closing balance. Short-term financing movements in Cash Flow rows
   11 and 12 can therefore produce a governed `OUT_OF_BALANCE` result;
2. `METHODOLOGY_CALCULATION_INCOMPLETE` carries the complete ordered diagnostic
   payload defined in section 6.2.1;
3. each `*_set_complete=true` fact requires the exact documentary-basis
   extractor request in section 5.4.1. Silence, balancing arithmetic, or an
   LLM assertion is never completeness;
4. every `DIRECT_SIGNED` input uses the version-bound sign convention in
   section 5.4.2; and
5. the literal-artifact gate includes the deterministic human-readable
   workbook map in section 3.2.1.

The reviewer additionally recorded that a real Step-11 failure caused by an
absent mandatory binding or documentary completeness fact is intended. The
only remedies are source-data supplementation or a separately reviewed policy
version; runtime weakening, default zero, or unsupported completeness remains
forbidden.

## 2. Read-only ground truth

### 2.1 Operator workbook

The protected workbook
`C:\Projects\IDIS\IDIS\financial Due Diligence.xlsx` was inspected read-only
through the production importer and the installed spreadsheet inspection
runtime.

| Property | Observed value |
|---|---|
| Byte count | `24,905` |
| SHA-256 | `a3b83addfff02815c9fd44c4a3dcb7f56ce39410fe182faff12acfb845bd4352` |
| Current external version ID | `financial_dd:a3b83addfff02815c9fd44c4a3dcb7f56ce39410fe182faff12acfb845bd4352` |
| Sheets | `P&L`, `Cash Flow`, `Liabilities`, `Assets` |
| Rows | `29 + 13 + 21 + 16 = 79` |
| Formula cells | `0` |
| Safe row-coordinate digest | `fab5ab40ab300602b5d9ee59eeb9c86451048d02e419054b0675e753a0ab0bc0` |
| Safe header digest | `88c4ea73409b768686dd9dce7d05efe3ccb846bdb13850499caddcd55bc3229c` |

The workbook is authoritative for the 79 questions and their source traces.
It is not authoritative for calculation policy.

### 2.2 Current implementation defects

1. `fdd_excel.py` assigns optional `GROSS_MARGIN` to all 29 P&L rows and
   optional `BURN_RATE` to all 13 Cash Flow rows. Assets and Liabilities have
   no calculation mapping. This is a coarse sheet fallback, not row-specific
   FDD execution.
2. `FormulaSpec.formula_hash` binds only calculation type, semantic version,
   and expression ID. A changed equation, input set, sign, precision, rounding,
   currency rule, or period rule can retain the same hash.
3. The methodology calculation service restricts candidate claims to the same
   extraction task and derives input keys by parsing claim prose. The named
   FDD bridges require cross-row bindings and must use the durable factual
   output's typed predicate/period, never prose parsing.
4. The strict runtime still invokes an in-memory calculation service and
   persists calculations before the Task-7 completion transaction. The legacy
   calculation repository can fall back to memory and lists by deal rather
   than by exact run/coverage/question coordinate.
5. The current truth dashboard is in-memory and claim-oriented. It omits a row
   when evidence is absent, whereas the target contract requires an explicit
   governed row for every selected methodology coverage record, including
   structured gaps.
6. The current `financial_diligence.json` is a generic memo/calculation package,
   not an ordered 79-row FDD result.

### 2.3 Frozen Task-7 boundary

Frozen `full-strict-v21` already defines the exact future schemas for:

- `methodology_truth_dashboard_rows`; and
- `methodology_truth_dashboard_root_links`.

The current tenant/RLS inventory is 67; those two tables make the Task-8
inventory 69. Task 8 may not add a third table, role, environment variable, or
flag.

The frozen dashboard equation also fixes these semantics:

- `GAP` requires a non-empty structured `EVIDENCE_NOT_FOUND` result and no
  evidence, Sanad, calculation, or adverse roots.
- `ANSWERED` and `RED_FLAG` require result, evidence, and Sanad roots.
- Every `required=true` calculation declared on the frozen methodology
  question must have a calculation root of that exact type, and no undeclared
  calculation root is allowed.
- `RED_FLAG` additionally requires an adverse/contradiction root.

Two further frozen-v21 constraints are material:

1. `task7-deterministic-calculation-v1` hash-binds the current ten-value
   `CalcType` enum and the legacy three-field `CalcInputs` model. Adding the
   nine named FDD types or widening `CalcInputs` in place would make the
   installed Pydantic schema drift from the frozen v21 bytes.
2. `dashboard-root-equation-v1` permits `ANSWERED`/`RED_FLAG` only when every
   task result for the coverage row is `EVIDENCE_FOUND`, and permits `GAP` only
   when every task result is a governed `EVIDENCE_NOT_FOUND`. Task 6
   deliberately supports `METHODOLOGY_EVIDENCE_FOUND_WITH_TASK_GAPS_V1`; that
   valid mixed state has no legal v21 dashboard disposition.

Task 8 therefore cannot add the named formulas, call one “required” while also
permitting the same row to finish as `GAP`, or faithfully materialize mixed
Task-6 results under v21 without a reviewed successor pipeline contract.

## 3. Proposed normative artifact

After approval, add one append-frozen UTF-8/LF artifact:

`contracts/fdd_formula_policy_v1.json`

It is the sole normative byte source for Task-8 formula policy. It contains:

1. the exact workbook hash, safe trace digest, ordered sheet registry, and 79
   literal `(ordinal, sheet, physical_row, methodology_question_id)` rows;
2. the exact row-to-calculation requirement map;
3. every formula type, version, normalized expression AST, topological
   dependencies, ordered input-binding tuple, sign rule, cardinality, period
   rule, currency/unit rule, denominator rule, output tuple, precision,
   rounding, and permitted assumption;
4. the exact calculation-anchor row and whether an output is a line-item
   result or a sheet-level control;
5. the exact output schema/version for `financial_diligence.json`; and
6. independent component hashes plus one `FDD_FORMULA_POLICY_V2_SHA256`.

No runtime discovery, workbook formula, LLM choice, free-form predicate,
callable-source hash, wildcard, or environment value participates in policy
derivation. A change to any governed byte is `fdd-formula-policy-v2`.

The installed implementation maps each frozen `expression_id` to code and
must prove its normalized expression and golden vectors match the artifact.
Unknown or drifted expressions fail closed.

### 3.1 Approved successor-contract boundary

This section records the original v23 successor ruling. Section 3.2.3
supersedes its executable version choice and structural admission procedure;
the six semantic deltas remain the approved delta set applied from frozen v21
to v24.

The approved resolution is append-frozen `full-strict-v23`, never an edit
or alias of v21. V21 remains immutable, `INACTIVE`, and historically
verifiable. V23 conserves the 28 steps, 48 edge rules, 1,756 routes, 19
extractors, 18 providers, rights routing, producer/consumer coordinates,
complete reviewed relation universe, erasure ordering, and Task-11 27-cut
obligation. Its installed tenant/RLS partition deliberately moves from 67 to
69 when the two already-reviewed dashboard tables are materialized; the
complete erasure universe remains CURRENT48 plus frozen FUTURE38.

The only approved v23 semantic deltas are:

1. add a new, separately named `FddDeterministicCalculationV1` schema with its
   own `FddCalcTypeV1`, `FddCalcInputsV1`, and `FddCalcOutputV1`; do not mutate
   the legacy calculation classes frozen by v21;
2. add FDD-only `FddSignedRatioValueV1`/factual-output schema variants so a
   real negative margin is representable as a fractional ratio; do not widen
   or reinterpret the legacy nonnegative `PercentageValue`;
3. bind the Step-11 FDD native-output selector to the new calculation schema
   and Step-7 FDD factual-output selector to the signed-ratio-capable schema,
   while
   generic Step-15 `CALC` selector continues to use the legacy schema;
4. replace the dashboard equation with the mixed-result truth table in
   section 7.1 while retaining the same two v21 table shapes;
5. add the partial native-root exact-one authority and the fail-closed erasure
   precondition in section 8.2; and
6. bind the exact policy, artifact, output, and hash contracts in this design.

No existing version, artifact, fixture, model, row, or run is rewritten.
Migration `0039` may consume only a reviewed, append-frozen successor artifact
and fixture. Encoding the new formula families as misleading aliases of an
existing `CalcType`, hiding structured lineage in legacy string metadata, or
relaxing the v21 schema-drift guard is explicitly rejected.

V23 remains `INACTIVE` after artifact freeze and migration. It cannot become
selectable until the complete activation census, Task-9/10/11 producers, full
48-rule three-way equality at all 27 cuts, and a separate reviewer-approved
activation change are all complete.

### 3.2 Literal-artifact review gate

The v23 path described in this subsection is historical. Section 3.2.3 is the
binding v24 materialization and stop boundary.

The approved semantic ruling authorizes RED materialization tests, not runtime
implementation or migration consumption. Those tests then produce:

- `contracts/fdd_formula_policy_v2.json`;
- `contracts/full_strict_v23_reviewed_preimage_v1.json`; and
- hash fixtures for both.

The literal artifacts must normalize every nested calculation, dashboard,
artifact-output, hash-preimage, SQL-authority, ACL, and selector tuple. They
return for a second reviewer artifact/fixture gate before formula code,
migration `0039`, repository, worker, dashboard, or deliverable implementation
may be written. This mirrors the Task-7 defense against narrative fields being
silently completed during implementation.

#### 3.2.2 Approved second-gate amendment: acyclic dashboard identity

The held v22 candidate is not admissible. Its proposed native-output payload
contains `root_projection_sha256` and `row_payload_sha256`; the frozen v21
`output_object_id` UUID name contains that native-output hash; and both held
hash preimages contain the same dashboard output-object ID. That dependency is
a cycle, not a value that can be deterministically materialized. The held
candidate also uses `dashboard_row_id` as a native key without a frozen UUID
namespace/name tuple. Operator SQL execution and artifact publication remain
blocked until the approved v23 bytes resolve both defects.

Approved exact replacement:

1. Add one dashboard-row UUIDv5 authority. The proposed namespace is
   `6aef4159-c49a-560a-9f92-6c6089d7f7c3`, derived once as
   `UUIDv5(NAMESPACE_URL,
   "https://idis.local/contracts/methodology-truth-dashboard-row-v2")`. Its
   exact name preimage is
   `CJ(["methodology-truth-dashboard-row-v2", tenant_id, deal_id, run_id,
   pipeline_contract_version, pipeline_contract_hash, coverage_record_id,
   methodology_version_record_id, methodology_question_record_id,
   question_payload_hash])`; the UUIDv5 name is the lowercase SHA-256 of those
   canonical bytes. This row ID is derived before every row/root/output hash.
   The existing `TRUTH_DASHBOARD_ROW_NAMESPACE`
   (`0f9a8dd2-013b-50ff-a882-8efebc1f6e10`) is deliberately not reused: its
   production name tuple is the legacy Slice-10
   `(dashboard_id, claim_id, sanad_id, evidence_ids, calc_ids)` contract, not a
   methodology-coverage identity. Reuse would create an unreviewed alias
   between two different row domains.
2. Replace the self-referential projection recipe with
   `H(["methodology-truth-dashboard-root-projection-v2", tenant_id, deal_id,
   run_id, dashboard_row_id, coverage_record_id, row_disposition, count,
   ORDERED[(root_kind, root_ordinal, root_output_object_id,
   root_output_object_kind, dashboard_root_link_id)]])`. It deliberately omits
   only the dashboard's own `run_output_object_id`; every evidence root and its
   deterministic link ID remain bound.
3. Replace the row-payload recipe with
   `CJ(["financial-diligence-row-v2", identity_and_trace_without_self_output,
   disposition, disposition_reason_code, result_records, answer_values,
   task_gaps, calculations, root_projection_sha256])`, where
   `identity_and_trace_without_self_output` is the current ordered tuple with
   `dashboard_output_object_id` removed and every other field retained.
4. Derive, in order: dashboard-row ID; root-link IDs; projection hash; row
   payload hash; frozen Task-7 native identity/output hashes over
   `(coverage_record_id, root_projection_sha256, row_disposition,
   row_payload_sha256)`; then the unchanged generic Task-7 `output_object_id`
   UUID. Parent, children, and output root are inserted in one deferred
   transaction. This ordering has no self-edge and retains all four immutable
   dashboard fields in the native output hash.
5. Add an independent dependency-graph admission test that rejects every
   cycle, plus deterministic replay and one-field mutation vectors for the
   new row ID, both hashes, and the final generic output UUID.

The existing `fdd-formula-policy-v1` bytes contain the old row-payload recipe
and are preserved immutable and unconsumed. The design's append-freeze rule
forbids editing them in place. The corrected row/output recipe is
`fdd-formula-policy-v2`, and every identity-bearing reference, including the
methodology external-ID template and reviewer map, uses v2's ID and hash. The
corrected full-strict artifact is v23, leaving the rejected v22 identifiers
permanently unambiguous in the audit record. The rejected v22
artifact SHA-256
`94edc7b19fe73ed75872ad052c84b7a9fb341d32b9741f98a1a4c048a058b507`,
fixture SHA-256
`4368d86b6bdd270f03d94bbc591a9ee2f6fbc6c36c9c65076cd1549d2a4da56b`,
and contract hash
`a8294752ae392f72cbdf83ced9107df12243f3f26010347457f86603a302dc23`
are held invalid and may never be migration-consumed or runtime-selected.
They join the permanent void blocklist.

The independently regenerated append-frozen v2 policy package is pinned as:

- artifact SHA-256
  `e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064`;
- policy SHA-256
  `2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9`;
- fixture SHA-256
  `b95c426da57a64336da990002201f4d5fdfe616dbae095a93e3aaf9372cf242e`;
- reviewer-map SHA-256
  `98cf84d4498f57465cd61501ea8bbbd93f7507d8628a61d7cbcf9cede459fe2a`.

Its materializer derives from the exact pinned v1 artifact bytes, separately
reimports the read-only workbook to prove those preserved bytes, and fails
closed on missing or mutated v1 artifact, fixture, reviewer map, or
materializer input. This records the v1-to-v2 lineage without modifying v1.

The dependency-cycle admission rule is general. The hermetic generator builds
one directed graph from every UUID name tuple and every digest/hash preimage
declared anywhere in the full-strict artifact and its bound policy artifact.
Nodes are qualified by artifact, contract-definition versus runtime-value
layer, scope, output kind, output symbol, and source pointer. An identity/hash
output node has an edge to each declared identity/hash input it consumes;
literal/scalar fields are typed leaves. Generic output names are scoped by
output kind, so a dashboard's own `run_output_object_id` cannot alias an
upstream root's `root_output_object_id`. Runtime `pipeline_contract_hash` is a
pinned contract leaf rather than a false back-edge to the artifact's own
definition hash.

Admission uses a closed canonical `H`/`CJ`/`ORDERED` recipe grammar and an
exact source-pointer manifest. A declaration outside that manifest, an
unsupported recipe form, duplicate or ambiguous producer, unresolved governed
reference, self-edge, or directed cycle fails before fixture generation. The
canonical graph receipt hashes ordered nodes, edges, typed leaves, and source
pointers under `identity-hash-dependency-graph-v1`; its digest and exact counts
are fixture fields and PostgreSQL-census inputs. Dashboard-specific and
synthetic non-dashboard cycles are regression vectors, but admission never
special-cases dashboard names or locations.

#### 3.2.3 Approved v24 structural materialization amendment

The rendered v23 draft is permanently void, not an unpublished label that may
be rebound to new bytes. Its artifact
`ddbe234bfbac246e65b2b1c0dd30eae018e88754446d713f1defdb4d70232dfc`,
fixture
`b7782392676e884560840fc8314dbc4a7673800e5cbeaa04a85eabdb329879c5`,
pipeline-contract
`ce413d234aff579a4cabec8ae81e903be3fc8e70f843e3ef08ebea44907a88d7`,
and graph
`1d0b374fb7e4ea3a3874c7d2c1ac1eb5d74701ef31603379ee42d753a84292bd`
identifiers are rejection evidence only. The v23 artifact and rendered
fixture remain byte-frozen on disk with no publication lock. All four
identifiers are blocklisted and may never be published, migration-consumed,
runtime-selected, relabeled, or reused. The corrected candidate is
`full-strict-v24`.

V24 must record `full-strict-v21` as its only parent, together with frozen-v21
artifact SHA-256
`2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd`
and pipeline-contract SHA-256
`46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794`.
Generation begins from those exact v21 bytes and applies only this complete
enumerated delta:

1. the six Task-8 successor deltas in section 3.1;
2. the section-3.2.2 dashboard-row namespace, self-ID-free projection and
   payload recipes, and acyclic derivation order;
3. strict `period_start < period_end`, positive-magnitude checks,
   source-to-normalized value preservation, the exact `0039 -> 0038`
   erasure-restoration tuple, and the literal 100/100 dashboard guard; and
4. the exact frozen policy-v2 artifact/policy/reviewer-map binding.

Admission rejects any other parent or delta. V22/v23 may be read only by
historical rejection tests and never as successor inputs. This prevents the
three approved FDD constraints from disappearing when a rejected candidate is
used as a convenient base.

The identity/hash dependency graph is artifact-wide and grammar-driven. It
walks every UUID name tuple and every digest/hash preimage, not a dashboard
allowlist, and rejects every duplicate producer, unresolved governed input,
self-edge, and directed cycle. The four non-dashboard cycle/mutation vectors
remain permanent regressions. The phrase "accepted non-dashboard cycle" in
the v23 stop record means a **synthetic guard-bypass demonstration**: tests
mutated existing `step_id`/`source_lineage_object_id` tuples (and separately
introduced synthetic nodes) to create a cycle which the incomplete guard
failed to reject. No such non-dashboard cycle exists in the unmodified v23
draft; v24 fixes the guard class rather than deleting a real draft edge.

Fixture hashes are independently recomputed without importing the production
materializer's derivation implementation. A second canonical verifier consumes
only frozen bytes and emits independently comparable component/final hashes.
Hand-written golden vectors bind every value in the dashboard identity chain:
dashboard-row UUID, root-projection hash, row-payload hash, Task-7 native
identity/output hashes, and generic output UUID. A production generator
quoting its own result is not fixture evidence.

Executable dashboard SQL cannot carry an independently authored recipe.
Generation derives its projection and preimage references from the frozen
policy-v2 recipes, and admission decodes every SQL body and proves exact field
equality. Any projection-v1 token, own-output-ID reference, or other
superseded recipe field fails before fixture creation.

Finally, an append-frozen literal conservation manifest names and locates
every approved Task-8 semantic vector: strict period ordering,
positive-magnitude enforcement, source-normalization preservation, exact
erasure restoration, 100/100 dashboard completeness, acyclic identity, and
policy-v2 binding. Each entry hashes the implementing artifact bytes and has
a named missing/drift failure. Every successor regeneration must pass this
census; it is not a one-time v24 checklist.

Policy v2 is unchanged. V24 remains `INACTIVE`. Before the next reviewer gate,
only hermetic materialization, independent fixture derivation, graph/SQL
closure, blocklist, and conservation-census work is allowed. The PostgreSQL
census is held; migration `0039`, formula/runtime code, durable application
mutation, commits, and live calls remain forbidden.

### 3.2.1 Human-readable mapping projection

Task 8.1 also renders
`docs/plans/2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md`
as a deterministic projection of `fdd_formula_policy_v2.json`. For every one
of the ten anchors and every expanded predicate binding it shows:

- policy/formula and binding ordinal;
- role, predicate, sign transform, cardinality, and source coordinate;
- exact workbook term, nature, line-item text, question text, and resolved
  question-anchor cell; and
- methodology question ID and the hashes binding the workbook row and mapping.

The map is not an independent authority. Admission regenerates it byte-for-byte
from the frozen policy artifact, rejects drift, and separately reimports the
operator workbook read-only to prove the artifact's literal row text and
anchors still match the pinned workbook bytes. Tests and checkpoint summaries
may report only hashes/counts; they never print workbook prose to a terminal or
safe-aggregate report.

## 4. Proposed FDD methodology-version transition

The existing Task-5 FDD version is frozen and carries the wrong sheet-wide
optional mappings. It must remain immutable.

The recommended transition is:

1. Preserve the existing version and its 79 question rows unchanged for
   historical runs.
2. Import the same workbook bytes as a new immutable version whose external ID
   is:

   `financial_dd:<workbook_sha256>:fdd-formula-policy-v2:<policy_sha256>`

3. Keep `source_artifact_sha256` equal to the actual workbook SHA-256. The
   policy hash is carried in the external version ID and the complete
   question-payload/registry/version hashes.
4. Preserve the 79 existing `methodology_question_id` values because the
   source questions did not change. Change only the version identity and exact
   `required_calculations` tuples.
5. Strict new-run selection accepts only the newly frozen FDD version. Existing
   immutable run selections are not rewritten.

This uses the Task-5 tables as designed and adds no schema.

## 5. Proposed row-specific formula policy

### 5.1 Formula set and anchors

The recommended Task-8 set is the nine master-plan families plus the one
clearly applicable existing core formula, `GROSS_MARGIN`.

| Order | Calculation type | Required anchor | Scope |
|---:|---|---|---|
| 1 | `GROSS_MARGIN` | `P&L:11` | line-item ratio |
| 2 | `FDD_EBITDA_BRIDGE` | `P&L:23` | P&L control |
| 3 | `FDD_EBIT_BRIDGE` | `P&L:27` | P&L control |
| 4 | `FDD_TAX_RECONCILIATION` | `P&L:30` | P&L control |
| 5 | `FDD_NET_INCOME_BRIDGE` | `P&L:32` | P&L control |
| 6 | `FDD_WORKING_CAPITAL_MOVEMENT` | `Cash Flow:8` | cash-flow control |
| 7 | `FDD_FREE_CASH_FLOW_CASH_BRIDGE` | `Cash Flow:16` | cash-flow control |
| 8 | `FDD_DEBT_ROLL_FORWARD` | `Cash Flow:11` | debt control |
| 9 | `FDD_ASSET_TIE_OUT` | `Assets:19` | sheet control |
| 10 | `FDD_LIABILITY_TIE_OUT` | `Liabilities:24` | sheet control |

All ten entries are proposed as `required=true`; the other 69 FDD rows carry
an empty calculation requirement. `BURN_RATE`, `RUNWAY`, and the remaining
core formulas remain registered for their existing generic/CALC uses but are
not attached to an FDD row without a separately reviewed input contract.

`Assets:19` and `Liabilities:24` are proposed only because the 79-row contract
has no total/control row and frozen v21 requires every methodology calculation
to carry one coverage/question anchor. They must be labeled
`scope=sheet_control` in lineage so the calculation never masquerades as the
cash or lease question itself.

### 5.2 Sign and numeric rules

1. All arithmetic is `Decimal`; binary float, NaN, positive/negative infinity,
   and booleans are rejected before formula execution.
2. Negative zero canonicalizes to `0`.
3. The arithmetic context is precision 50 with `ROUND_HALF_UP`.
4. Monetary outputs are quantized once, at the final boundary, to `0.01` of
   the common declared unit. Percent/ratio outputs are quantized to `0.0001`.
   Intermediate values are not quantized. Ratios use the production
   `PercentageValue` fractional convention (`0.25` means 25 percent); formula
   arithmetic never multiplies or divides by 100 unless an explicitly
   reviewed percentage-point transform is added in a later policy.
5. Revenue, income, asset balances, financing inflows, and equity injections
   are positive magnitudes. Costs, operating expenses, tax expense, interest,
   capex, debt repayment, and dividends are positive magnitudes and are
   subtracted by the equation. A policy field explicitly marked
   `DIRECT_SIGNED` is the only permitted signed adjustment.
   The complete sign-transform enum is
   `POSITIVE_MAGNITUDE|DIRECT_SIGNED`; there is no inferred or implicit
   transform.
6. The input record preserves source value, normalized value, and the
   hash-bound sign transform. Sign is never inferred from a label at runtime.
7. A denominator required to be positive fails on zero or a negative value.
   No epsilon or tolerance is used.
8. A non-zero bridge/tie-out variance is a valid
   `formula_disposition=OUT_OF_BALANCE` result, not a calculation-engine
   failure and not, by itself, a Task-6 adverse/contradiction root.

### 5.3 Currency, unit, and period rules

1. Every monetary input in one formula instance has the same ISO-4217 currency
   and the same declared scale unit. Task 8 performs no FX conversion and no
   silent thousands/millions conversion.
2. P&L and cash-flow instances use one exact canonical `range` period.
   Balance-sheet tie-outs use one exact canonical `as_of` date.
3. A roll-forward has exact opening and closing `as_of` dates and a flow
   `range` whose boundaries match them. `opening < closing` is mandatory.
4. The policy derives an exact economic coordinate
   `(period_kind, dates, base_currency, monetary_scale_unit)` from the
   governed monetary bindings. It defines the required instance set. A ratio
   formula retains that economic coordinate for parent joins even though its
   primary output has unit `ratio` and null output currency. Every required
   binding must resolve exactly once at every economic coordinate. Missing,
   duplicate, conflicting, or unconsumed candidate facts fail closed.
5. The durable `MethodologyFactualOutput.predicate`, typed value, and
   `MethodologyPeriod` are authoritative. Claim text and human-readable
   `time_window` labels are never parsed to select inputs.
6. No actual/budget/forecast scenario is inferred. If two facts share the
   complete governed coordinate and differ only by an unmodeled scenario, the
   input is ambiguous and the run fails. Adding a scenario dimension would be
   a separate semantic amendment.

#### 5.3.1 Signed-ratio factual value

The existing `PercentageValue` is intentionally nonnegative and stores a
fraction. V23 does not mutate it. The FDD-only
`FddSignedRatioValueV1` field set is exactly
`(type="signed_ratio", value, as_of, time_window)`. `value` is a finite
canonical Decimal fraction with no lower or upper bound; binary float, bool,
NaN, infinity, and percentage-point input are rejected. Exactly one of
`as_of` or `time_window` is present as required by the formula period rule,
and the canonical unit is literal `ratio`.

It participates in the same factual-output ID, claim, evidence, Sanad, source
span/locator, request/response, and native-output hash contracts as every
other Task-6 factual value. Only a v23 FDD extractor/result can emit it; CDD,
legacy runs, and v21 remain bound to the old `ValueStruct` union.

### 5.4 Exact proposed predicate and row bindings

The policy artifact contains every row below literally. A range means the
expanded ordered physical-row tuple, never a runtime range expression.
`opening` and `closing` are exact `as_of` periods; `flow` and P&L values are
the exact matching `range` period. Each role resolves exactly once unless the
table explicitly says `ordered component set` or `zero-or-more`. Artifact
binding ordinals are the consecutive table order after range expansion.
“Same-coordinate” below always means the economic coordinate from section
5.3, never equality of primary output units.
Unmarked income, balance, rate, reported-value, expense, and outflow bindings
use `POSITIVE_MAGNITUDE`; only entries marked `DIRECT_SIGNED` accept a signed
source value. `POSITIVE_MAGNITUDE` rejects a negative source value and
preserves it; `DIRECT_SIGNED` accepts any finite Decimal and preserves it.

| Formula | Input role | Literal source trace/predicate |
|---|---|---|
| Gross margin | recurring revenue | `P&L:4 / fdd.recurring_revenue` |
|  | non-recurring revenue | `P&L:5 / fdd.nonrecurring_revenue` |
|  | cost-of-sales components | ordered `P&L:7,8,9,10 / fdd.cost_of_sales_component` |
|  | reported gross-margin ratio | `P&L:11 / fdd.reported_gross_margin_ratio`, `FddSignedRatioValueV1`, `DIRECT_SIGNED` |
| EBITDA bridge | operating-income components | ordered `P&L:4,5,6 / fdd.operating_income_component` |
|  | cost-of-sales components | ordered `P&L:7,8,9,10 / fdd.cost_of_sales_component` |
|  | operating-expense components | ordered `P&L:12..22 / fdd.operating_expense_component` |
|  | parent operating revenue | exact same-coordinate `GROSS_MARGIN` |
|  | reported EBITDA | `P&L:23 / fdd.reported_ebitda`, `DIRECT_SIGNED` |
| EBIT bridge | parent EBITDA | exact same-coordinate `FDD_EBITDA_BRIDGE` |
|  | parent operating revenue | exact same-coordinate `GROSS_MARGIN` |
|  | depreciation | `P&L:24 / fdd.depreciation` |
|  | amortization/impairment | `P&L:25 / fdd.amortization_impairment` |
|  | other non-cash adjustment | `P&L:26 / fdd.other_non_cash_adjustment`, `DIRECT_SIGNED` |
|  | reported EBIT | `P&L:27 / fdd.reported_ebit`, `DIRECT_SIGNED` |
| Tax reconciliation | parent EBIT | exact same-coordinate `FDD_EBIT_BRIDGE` |
|  | bank and lease interest | ordered `P&L:28,29 / fdd.interest_expense_component` |
|  | statutory tax rate | `P&L:30 / fdd.statutory_tax_rate_ratio` as canonical fraction |
|  | permanent/rate/discrete differences | ordered zero-or-more `P&L:30 / fdd.tax_reconciling_item`, `DIRECT_SIGNED` |
|  | reconciling-set completeness | exactly one Boolean-true `P&L:30 / fdd.tax_reconciling_item_set_complete` with evidence/Sanad |
|  | deferred-tax adjustment | `P&L:31 / fdd.deferred_tax_adjustment`, `DIRECT_SIGNED` |
|  | reported current/deferred tax | ordered `P&L:30,31 / fdd.reported_tax_component`, `DIRECT_SIGNED` |
| Net-income bridge | parent EBIT, calculated tax, and operating revenue | exact same-coordinate `FDD_EBIT_BRIDGE`, `FDD_TAX_RECONCILIATION`, `GROSS_MARGIN` |
|  | bank and lease interest | ordered `P&L:28,29 / fdd.interest_expense_component` |
|  | reported net income | `P&L:32 / fdd.reported_net_income`, `DIRECT_SIGNED` |
| Working-capital movement | operating current assets | ordered opening+closing values from `Assets:13,14,15,16 / fdd.operating_current_asset_balance` |
|  | operating current liabilities | ordered opening+closing values from `Liabilities:12,13,14,15,16 / fdd.operating_current_liability_balance` |
|  | reported NWC movement | `Cash Flow:8 / fdd.reported_delta_nwc`, `DIRECT_SIGNED` |
| FCF/cash bridge | cash EBITDA | `Cash Flow:4 / fdd.cash_ebitda`, `POSITIVE_MAGNITUDE`, exactly one |
|  | cash tax/non-recurring outflow | `Cash Flow:5 / fdd.cash_tax_and_nonrecurring`, `POSITIVE_MAGNITUDE`, exactly one |
|  | tangible capex | `Cash Flow:6 / fdd.tangible_capex`, `POSITIVE_MAGNITUDE`, exactly one |
|  | intangible capex | `Cash Flow:7 / fdd.intangible_capex`, `POSITIVE_MAGNITUDE`, exactly one |
|  | parent NWC movement | exact same-coordinate `FDD_WORKING_CAPITAL_MOVEMENT` |
|  | non-operating working capital | `Cash Flow:9 / fdd.nonoperating_working_capital_cash_effect`, `DIRECT_SIGNED` |
|  | reported FCF | `Cash Flow:10 / fdd.reported_free_cash_flow`, `DIRECT_SIGNED` |
|  | bank/lease financing | `Cash Flow:11 / fdd.bank_lease_financing_cash_flow`, `DIRECT_SIGNED`, exactly one |
|  | other financing | `Cash Flow:12 / fdd.other_financing_cash_flow`, `DIRECT_SIGNED`, exactly one |
|  | recapitalization | `Cash Flow:13 / fdd.recapitalization_cash_flow`, `DIRECT_SIGNED`, exactly one |
|  | dividends | `Cash Flow:14 / fdd.dividends_paid`, `POSITIVE_MAGNITUDE`, exactly one and subtracted |
|  | other equity flows | `Cash Flow:15 / fdd.other_equity_cash_flow`, `DIRECT_SIGNED`, exactly one |
|  | reported net cash flow | `Cash Flow:16 / fdd.reported_net_cash_flow`, `DIRECT_SIGNED` |
|  | opening and closing cash | `Assets:19 / fdd.unrestricted_cash_balance` at the exact opening/closing dates |
| Debt roll-forward | opening and closing interest-bearing debt | `Liabilities:9 / fdd.interest_bearing_debt_balance` at exact opening/closing dates |
|  | draws | ordered zero-or-more `Cash Flow:11,12 / fdd.debt_draw`, `POSITIVE_MAGNITUDE` |
|  | capitalized interest/accrual | ordered zero-or-more `Cash Flow:11,12 / fdd.debt_capitalized_interest`, `POSITIVE_MAGNITUDE` |
|  | FX/other noncash changes | ordered zero-or-more `Cash Flow:11,12 / fdd.debt_fx_other_noncash`, `DIRECT_SIGNED` |
|  | repayments | ordered zero-or-more `Cash Flow:11,12 / fdd.debt_repayment`, `POSITIVE_MAGNITUDE` |
|  | conversions/writeoffs | ordered zero-or-more `Cash Flow:11,12 / fdd.debt_conversion_writeoff`, `POSITIVE_MAGNITUDE` |
|  | movement-set completeness | exactly one Boolean-true `Cash Flow:11 / fdd.debt_rollforward_component_set_complete` with evidence/Sanad |
| Asset tie-out | component balances | ordered closing values from every `Assets:4..19 / fdd.asset_component_balance` |
| Liability/equity tie-out | component balances | ordered closing values from `Liabilities:4..18 / fdd.liability_equity_component_balance` |
|  | parent calculated total assets | exact same-date `FDD_ASSET_TIE_OUT` |

Liabilities rows 19–24 are off-balance-sheet obligations and are deliberately
excluded from the balance-sheet component sum. Their 79-row diligence answers
remain mandatory; they are not silently discarded.

The debt roll-forward has a separate deliberate scope exclusion. Its opening
and closing interest-bearing-debt balance is sourced only from
`Liabilities:9`. `Liabilities:17` and `Liabilities:18` are short-term financing
diligence rows, remain mandatory in the 79-row output, and are not balance
inputs to `FDD_DEBT_ROLL_FORWARD`. Cash Flow rows 11 and 12 remain eligible
movement sources. If short-term funding appears in those cash-flow movements
without a corresponding `Liabilities:9` balance movement, the exact debt
variance is `OUT_OF_BALANCE`; that governed finding is not repaired by silently
expanding the balance scope.

The workbook has no total-assets or total-liabilities/equity control row.
Accordingly, Task 8 does not manufacture either reported total, does not attach
one to the unrelated cash/lease anchor, and does not claim a source-row tie-out
that the workbook cannot support. `Assets:19` and `Liabilities:24` are only
stable dashboard coordinates for the two derived sheet controls. All numeric
lineage comes from the expanded component rows above.

Every listed predicate is added only to its literal source-row Task-6
extractor request from the version-bound formula policy, never wholesale to
the calculation-anchor task. Existing line-item predicates remain present.
The exact request hash, response, factual-output predicate, row, period, unit,
and evidence/Sanad roots must all agree. No label matching, keyword inference,
claim-prose parsing, or cross-row substitution is allowed.

#### 5.4.1 Documentary set-completeness requests

The policy stores these request texts as exact NFC UTF-8 literals and binds
them into the extraction-request hash:

- `fdd.tax_reconciling_item_set_complete`: “Return Boolean true only when a
  cited tax reconciliation or tax schedule explicitly enumerates the complete
  set of permanent, rate, and discrete reconciling items for the stated period
  and ties that set to the reported current/deferred tax. If no cited document
  explicitly establishes completeness, return EVIDENCE_NOT_FOUND. Never infer
  completeness from silence, absence, a calculated residual, or model
  judgment.”
- `fdd.debt_rollforward_component_set_complete`: “Return Boolean true only
  when a cited loan or debt schedule explicitly enumerates all draws,
  capitalized interest or accrual, FX or other noncash changes, repayments,
  conversions, and writeoffs for the stated period and ties the schedule's
  opening and closing balances to Liabilities:9. If no cited document
  explicitly establishes completeness, return EVIDENCE_NOT_FOUND. Never infer
  completeness from silence, absence, balancing arithmetic, or model
  judgment.”

The request result may be `true` or governed `EVIDENCE_NOT_FOUND`; `false`,
uncited `true`, or prose-only completeness is not an admissible factual output.

#### 5.4.2 Direct-signed conventions

Every `DIRECT_SIGNED` binding carries exactly one of these version-bound
conventions. Positive and negative always mean:

| Predicate | Positive value | Negative value |
|---|---|---|
| `fdd.reported_gross_margin_ratio` | reported gross profit as a positive fraction of revenue | reported gross loss as a negative fraction |
| `fdd.reported_ebitda` | reported EBITDA profit | reported EBITDA loss |
| `fdd.other_non_cash_adjustment` | adjustment added in the EBIT bridge | adjustment subtracted in the EBIT bridge |
| `fdd.reported_ebit` | reported EBIT profit | reported EBIT loss |
| `fdd.tax_reconciling_item` | increases calculated tax expense | decreases calculated tax expense |
| `fdd.deferred_tax_adjustment` | increases calculated tax expense | decreases calculated tax expense |
| `fdd.reported_tax_component` | reported tax expense | reported tax benefit |
| `fdd.reported_net_income` | reported net profit | reported net loss |
| `fdd.reported_delta_nwc` | movement convention: closing operating NWC exceeds opening operating NWC | movement convention: closing operating NWC is below opening operating NWC |
| `fdd.nonoperating_working_capital_cash_effect` | cash inflow/increase | cash outflow/decrease |
| `fdd.reported_free_cash_flow` | positive free-cash-flow generation | free-cash-flow outflow |
| `fdd.bank_lease_financing_cash_flow` | financing cash inflow/draw | financing repayment/outflow |
| `fdd.other_financing_cash_flow` | financing cash inflow | financing cash outflow |
| `fdd.recapitalization_cash_flow` | recapitalization cash inflow | recapitalization cash outflow |
| `fdd.other_equity_cash_flow` | other equity cash inflow | other equity cash outflow |
| `fdd.reported_net_cash_flow` | reported increase in cash | reported decrease in cash |
| `fdd.debt_fx_other_noncash` | noncash change increasing closing debt | noncash change decreasing closing debt |

For `fdd.reported_delta_nwc`, the formula derives the separate cash effect as
`-reported_delta_nwc`; the extractor must never return a cash-effect sign under
the movement predicate. Every request repeats its exact convention text. A
fact lacking or contradicting the convention fails binding rather than being
silently negated.

### 5.5 Exact proposed equations

1. **Gross margin**

   `operating_revenue = recurring_revenue + nonrecurring_revenue`

   `cost_of_sales = SUM(cost_of_sales_components)`

   `gross_profit = operating_revenue - cost_of_sales`

   `gross_margin_ratio = gross_profit / operating_revenue`

   Primary output is `gross_margin_ratio`; secondary outputs are
   `gross_profit`, `operating_revenue`, `cost_of_sales`,
   `reported_gross_margin_ratio`, and
   `gross_margin_ratio_variance =
   gross_margin_ratio - reported_gross_margin_ratio`.
   `operating_revenue > 0`.

2. **EBITDA bridge**

   `calculated_ebitda = SUM(operating_income_components)
   - SUM(cost_of_sales_components)
   - SUM(operating_expense_components)`

   `ebitda_margin_ratio = calculated_ebitda / operating_revenue`

   Secondary outputs are `reported_ebitda`, `ebitda_margin_ratio`, and
   `ebitda_variance = calculated_ebitda - reported_ebitda`.
   Parent `operating_revenue > 0`.

3. **EBIT bridge**

   `calculated_ebit = calculated_ebitda
   - depreciation
   - amortization_and_impairment
   + other_non_cash_adjustment`

   `ebit_margin_ratio = calculated_ebit / operating_revenue`

   Secondary outputs are `reported_ebit`, `ebit_margin_ratio`, and
   `ebit_variance = calculated_ebit - reported_ebit`.

4. **Tax reconciliation**

   `profit_before_tax = calculated_ebit - SUM(interest_expense_components)`

   `calculated_tax = profit_before_tax * statutory_tax_rate_ratio
   + SUM(tax_reconciling_items) + deferred_tax_adjustment`

   `reported_total_tax = SUM(reported_tax_components)`

   Secondary output is
   `tax_variance = calculated_tax - reported_total_tax`.
   `statutory_tax_rate_ratio` must be in `[0,1]`.

5. **Net-income bridge**

   `calculated_net_income = calculated_ebit
   - SUM(interest_expense_components) - calculated_tax`

   `net_income_margin_ratio = calculated_net_income / operating_revenue`

   Secondary outputs are `reported_net_income`, `net_income_margin_ratio`, and
   `net_income_variance = calculated_net_income - reported_net_income`.

6. **Working-capital movement**

   `opening_operating_working_capital =
   SUM(opening_operating_current_assets)
   - SUM(opening_operating_current_liabilities)`

   `closing_operating_working_capital =
   SUM(closing_operating_current_assets)
   - SUM(closing_operating_current_liabilities)`

   `calculated_delta_nwc =
   closing_operating_working_capital - opening_operating_working_capital`

   `nwc_cash_effect = -calculated_delta_nwc`

   Secondary outputs are `reported_delta_nwc` and
   `nwc_variance = calculated_delta_nwc - reported_delta_nwc`.

7. **Free-cash-flow/cash bridge**

   `calculated_fcf = cash_ebitda - cash_tax_and_nonrecurring
   - tangible_capex - intangible_capex + nwc_cash_effect
   + nonoperating_working_capital_cash_effect`

   `calculated_net_cash_flow = calculated_fcf
   + bank_lease_financing_cash_flow
   + other_financing_cash_flow
   + recapitalization_cash_flow
   - dividends_paid
   + other_equity_cash_flow`

   `calculated_closing_cash = opening_cash + calculated_net_cash_flow`

   Secondary outputs are reported FCF, reported net cash flow, reported closing
   cash, and the three corresponding variances.

8. **Debt roll-forward**

   `calculated_closing_debt = opening_debt + draws
   + capitalized_interest + fx_and_other_noncash_changes
   - repayments - conversions_and_writeoffs`

   Secondary outputs are `reported_closing_debt` and
   `debt_variance = calculated_closing_debt - reported_closing_debt`.

9. **Asset tie-out**

   `calculated_total_assets = SUM(asset_component_balances)`

   This is a derived sheet total with disposition `CALCULATED`; it makes no
   reported-total comparison.

10. **Liability/equity tie-out**

    `calculated_total_liabilities_and_equity =
    SUM(liability_equity_component_balances)`

    `balance_sheet_variance =
    calculated_total_assets - calculated_total_liabilities_and_equity`

    Zero variance is `IN_BALANCE`; any nonzero exact variance is
    `OUT_OF_BALANCE`.

There is no default-zero factual input. Every fixed-cardinality component row
must produce exactly one governed value for the coordinate. A variable
component set, such as tax reconciling items or debt-roll-forward movements,
is ordered by source row, predicate, subtype, period, and factual-output ID and
must carry its exact Boolean-true `*_set_complete` factual output with
evidence/Sanad. With that completeness proof, zero components is a governed
empty set and its additive identity is zero; it is not a fabricated numeric
zero item. Without the completeness proof the set is incomplete and Step 11
fails. If a fixed component is genuinely zero, a governed zero claim with
evidence/Sanad is required.
The closed `permitted_assumptions` tuple is literal `[]` for all ten v1
formulas.

## 6. Calculation lineage and reproducibility

### 6.1 Formula hash

The Task-8 formula hash is:

`SHA256(CJ(["fdd-formula-spec-v1", formula_type, version, expression_id,
expression_ast, dependency_tuple, ordered_input_specs, sign_rules,
cardinality_rules, period_rule, currency_rule, unit_rule, denominator_rule,
output_specs, precision, rounding, permitted_assumptions]))`

Changing any semantic field changes the hash. The old three-field hash remains
valid only for historical core calculations and cannot identify a Task-8 FDD
calculation.

`CJ` is exactly the frozen
`idis-task7-canonical-json-sha256-v1` canonicalization: UTF-8, no BOM, Unicode
NFC strings, object keys in Unicode-codepoint lexical order, `,`/`:`
separators with no insignificant whitespace, JSON floats forbidden, and
ordered arrays retained. Decimal values are finite non-exponent base-10
strings with insignificant trailing fractional zeroes removed and negative
zero rendered as `0`. UUIDs and SHA-256 values are lowercase canonical
strings. Timestamps, where admitted by an output schema, are UTC RFC3339 with
exactly six fractional digits and `Z`.

### 6.2 Input record

The existing `deterministic_calculations.inputs`/`output` JSONB columns are
physically sufficient, but the legacy Pydantic model is frozen by v21 and is
not widened. V23 adds a separate, extra-forbid, frozen
`FddDeterministicCalculationV1` constructor with:

- `calc_type`: exactly the ten Task-8 types in section 5.1;
- `inputs`: `FddCalcInputsV1`, containing formula-policy ID/hash, calculation
  coordinate, ordered input bindings, ordered parent calculations, and the
  closed governed assumption tuple;
- each input binding: ordinal, role, source and normalized Decimal strings,
  sign transform, source trace, coverage/version/question records and external
  IDs, factual-output/claim/evidence/Sanad roots, period, currency, unit, and
  value hash; and
- `output`: `FddCalcOutputV1`, containing one finite primary Decimal string,
  the closed ordered secondary-output mapping, unit/currency, formula
  disposition, and no untyped extension field.

The exact `FddCalcInputsV1` field set is
`(schema_version, formula_policy_id, formula_policy_sha256, formula_sha256,
coordinate, bindings, parent_calculations, assumptions)`.
`coordinate` is the economic coordinate exactly
`(period_kind, period_start, period_end, as_of_date, base_currency,
monetary_scale_unit)`, with date nullability determined by the period rule.
Output `(unit, currency, scale)` is separate and never participates in parent
coordinate equality. `bindings` is a discriminated union of
`FddNumericBindingV1` and `FddSetCompletenessBindingV1`. Each ordered
numeric binding is exactly
`(binding_kind=NUMERIC, ordinal, role, source_decimal, normalized_decimal,
sign_transform,
source_sheet, source_row, predicate, coverage_record_id,
methodology_version_record_id, methodology_question_record_id,
factual_output_record_id, claim_id, evidence_ids, sanad_ids, period, currency,
unit, value_sha256)`. Each set-completeness binding is exactly
`(binding_kind=SET_COMPLETENESS, ordinal, role, confirmed=true, source_sheet,
source_row, predicate, coverage_record_id, methodology_version_record_id,
methodology_question_record_id, factual_output_record_id, claim_id,
evidence_ids, sanad_ids, period, value_sha256)`; `false` is never admitted as
completeness. Each ordered parent is exactly
`(ordinal, calc_type, calc_id, economic_coordinate_sha256, formula_sha256,
reproducibility_sha256)`.
Assumptions are an ordered closed tuple of `(assumption_id, value)` entries
declared by the policy; the proposed v1 formulas declare no discretionary
assumption.

The exact `FddCalcOutputV1` field set is
`(primary_name, primary_decimal, primary_scale, secondary_values, unit,
currency, formula_disposition)`. `secondary_values` is the formula-declared
ordered tuple of `(name, decimal, scale, unit, currency)` entries, and
`formula_disposition` is exactly
`CALCULATED|IN_BALANCE|OUT_OF_BALANCE`. `FDD_ASSET_TIE_OUT` is
`CALCULATED`; formulas with a governed comparison define zero exact variance
as `IN_BALANCE` and every nonzero exact Decimal variance as
`OUT_OF_BALANCE`. Monetary scale is literal `2`; percent/ratio scale is
literal `4`. The scale field preserves the reviewed rounding contract even
though canonical Decimal strings omit insignificant trailing zeroes.

`calc_sanads.input_claim_ids` is the exact sorted union of direct and
transitive source claims. Parent calculation IDs and hashes are additionally
bound in the calculation input record. A caller cannot supply lineage IDs
separately from the resolved durable facts.

Missing/ambiguous input and zero/negative denominator conditions do not create
a fabricated numeric calculation. They fail Step 11 with a typed
`METHODOLOGY_CALCULATION_INCOMPLETE` reason before persistence. A future
nonnumeric `UNDEFINED` output would require a separately reviewed schema
amendment.

### 6.2.1 Actionable incomplete-binding payload

The failure payload schema is literal
`methodology-calculation-incomplete-v1` with the exact field set:

`(schema_version, code, formula_policy_id, formula_policy_sha256,
formula_type, formula_ordinal, formula_sha256, anchor_sheet, anchor_row,
economic_coordinate, issue_count, issues)`.

`code` is literal `METHODOLOGY_CALCULATION_INCOMPLETE`. Each issue is exactly:

`(issue_ordinal, issue_kind, binding_ordinal, role, predicate, source_sheet,
source_row, economic_coordinate, candidate_factual_output_ids)`.

`issue_kind` is exactly
`MISSING|DUPLICATE|AMBIGUOUS|UNCONSUMED`. Missing bindings have an empty
candidate tuple. Duplicate and ambiguous bindings enumerate every conflicting
factual-output record ID. Unconsumed issues enumerate every governed candidate
that matched the formula/coordinate source domain but no declared binding.
Issues sort by `(formula_ordinal, binding_ordinal, issue_kind,
candidate_factual_output_ids)` and the payload enumerates all issues found in
one complete resolution pass; it never stops after the first. Role, predicate,
sheet/row, and economic coordinate are present even when no candidate exists.
Private fact values, claim prose, and source-document names are not included.

### 6.3 IDs, hashes, replay

The deterministic calculation UUID name tuple includes tenant, deal, run,
anchor coverage/question, formula type/hash, policy hash, complete coordinate,
ordered binding hash, and ordered parent-calculation hash. The
reproducibility hash includes the complete typed inputs, complete outputs,
formula hash, policy hash, literal engine ID, and engine-manifest hash.

Replay resolves the historical formula by exact formula hash, recomputes from
the persisted typed inputs without querying mutable source data, and requires
identical outputs and reproducibility hash. A second pass also re-resolves the
durable claim/Sanad/parent-calculation roots and proves their current hashes
still match the persisted binding.

The engine identity is the literal `fdd-formula-engine-v1` plus
`engine_manifest_sha256`. That manifest digest is `SHA256(CJ(...))` over the
ordered tuples `(expression_id, normalized_expression_ast,
implementation_symbol, ordered_golden_vector_sha256s)`. It is not a mutable
package version or an implementation-source-file hash. Admission proves every
installed symbol produces the artifact AST's golden vectors before the engine
identity can participate in a calculation or reproducibility hash.

## 7. Dashboard and `financial_diligence.json`

### 7.1 Durable dashboard

Recommended scope is exactly one durable dashboard row for each of the 100
selected CDD+FDD coverage records. This replaces the current claim-row
semantics and makes structured gaps first-class. The financial artifact then
selects the exact 79 FDD rows by the frozen financial version.

Each row and its complete ordered root-link set are derived from durable
results, factual outputs, evidence links, Sanad links, defect links, and
required calculations. Parent plus children are inserted atomically and the
successor `dashboard-root-equation-v2` validates completeness, disjointness,
and the root-projection hash.

The proposed v2 disposition algorithm is deterministic:

| Complete result set for one coverage row | Disposition | Required roots |
|---|---|---|
| Every task is governed `EVIDENCE_NOT_FOUND` | `GAP` | every result root; no evidence, Sanad, calculation, or adverse root |
| At least one task is `EVIDENCE_FOUND`, all remaining tasks are either governed found or governed not-found, and no factual output has a defect | `ANSWERED` | every result root; every evidence/Sanad root for every found factual output; exact required calculations; no adverse root |
| Same mixed/all-found result rule, with one or more governed defects | `RED_FLAG` | the complete `ANSWERED` root set plus every adverse/contradiction root |

No task may be absent, nonterminal, empty, or omitted from the row. A mixed
found/not-found row remains `ANSWERED` or `RED_FLAG`; its governed subordinate
gaps are retained in the row payload and result-root set. It is never called
`PARTIAL`, and no semantic step status is weakened. A row with a required
formula and insufficient inputs fails Step 11 before dashboard materialization
and therefore cannot use `GAP` to bypass the formula requirement.

### 7.2 Financial artifact

`financial_diligence.json` uses
`financial-diligence-row-set-v1`. Its exact top-level field set is:

| Field | Type/invariant |
|---|---|
| `schema_version` | literal `financial-diligence-row-set-v1` |
| `tenant_id`, `deal_id`, `run_id` | lowercase canonical UUID strings |
| `pipeline_contract` | exactly `{version, sha256}` |
| `financial_methodology` | exactly `{version_record_id, version_id, source_sha256, registry_hash, version_hash}` |
| `formula_policy` | exactly `{policy_id, sha256, engine_id, engine_manifest_sha256}` |
| `row_count` | literal integer `79` |
| `rows` | exact ordered 79-element `FddDashboardRowV1` array |
| `reconciliation` | exactly the counters and three hashes enumerated below |

There is no open extension map and no embedded self-hash. The durable
deliverable row and product manifest hash the exact artifact bytes.

`FddDashboardRowV1` has exactly:

- `row_ordinal`, `sheet_ordinal`, `source_sheet`, `source_row_number`, and
  `source_anchor_cell`;
- `methodology_version_record_id`, `methodology_version_id`,
  `methodology_question_record_id`, `methodology_question_id`,
  `question_payload_hash`, source-bound `question_text`, and
  `coverage_record_id`;
- `dashboard_row_id`, `dashboard_output_object_id`, `disposition`, and
  `disposition_reason_code`;
- ordered `result_records`, ordered `answer_values`, ordered `task_gaps`,
  ordered `root_links`, and ordered `calculations`;
- `row_payload_sha256` and `root_projection_sha256`.

Each `result_record` is exactly
`(task_record_id, result_id, output_object_id, output_identity_sha256,
output_value_sha256, outcome, result_sha256, searched_scope_sha256,
provider_provenance_sha256)`.

Each `answer_value` is exactly
`(output_ordinal, factual_output_record_id, factual_output_object_id,
output_identity_sha256, output_value_sha256, predicate, typed_value, period,
unit, value_sha256, claim_link_id, claim_id, evidence_links, sanad_links)`.
Each evidence-link tuple is
`(evidence_link_id, evidence_id, output_object_id, output_identity_sha256,
output_value_sha256)`; each Sanad-link tuple is
`(sanad_link_id, sanad_id, grade, output_object_id, output_identity_sha256,
output_value_sha256)`.

Each `task_gap` is exactly
`(task_record_id, result_id, result_output_object_id,
result_output_identity_sha256, result_output_value_sha256, code, finding,
searched_scope_sha256, provider_provenance_sha256)`. Each `root_link` is
exactly `(root_kind, root_ordinal, output_object_id, output_kind,
output_identity_sha256, output_value_sha256, root_link_id)`. Each calculation
is the complete `FddDeterministicCalculationV1` package plus the exact
`(calc_sanad_id, input_claim_ids, sanad_sha256)` and native-output identity and
value hashes. No representative-root collapsing and no unrooted factual text
or number is allowed.

The reconciliation field set is exactly:

`(answered_row_count, gap_row_count, red_flag_row_count, result_root_count,
evidence_root_count, sanad_root_count, calculation_root_count,
adverse_root_count, calculation_count, calculated_only_count,
in_balance_count, out_of_balance_count, row_set_sha256, root_set_sha256,
calculation_set_sha256)`.

The approved policy-v2 row-payload preimage is exactly
`CJ(["financial-diligence-row-v2", identity_and_trace_without_self_output,
disposition,
disposition_reason_code, result_records, answer_values, task_gaps,
calculations, root_projection_sha256])`. The reconciliation preimages contain
the ordered row `(ordinal, dashboard_row_id, row_payload_sha256,
root_projection_sha256)` tuples, the ordered root-link tuples, and the ordered
calculation `(policy_ordinal, coordinate, calc_id, reproducibility_sha256)`
tuples respectively.

`identity_and_trace_without_self_output` is exactly
`(row_ordinal, sheet_ordinal, source_sheet, source_row_number,
source_anchor_cell, methodology_version_record_id, methodology_version_id,
methodology_question_record_id, methodology_question_id,
question_payload_hash, coverage_record_id, dashboard_row_id,
and deliberately excludes only `dashboard_output_object_id`. The serialized
row field remains present and is bound later by the unchanged generic Task-7
output identity/value chain. The literal artifacts in section 3.2 replace
these narrative tuple declarations with normalized schemas and byte-exact
hash recipes before any implementation may consume them.

Rows are ordered by frozen sheet ordinal, physical row, and question ID.
Results are ordered by task-record ID then result ID; answer values by factual
output ordinal then record ID; task gaps by task/result ID; root links by
kind/ordinal/output/root-link ID; calculations by policy ordinal, coordinate,
and calculation ID; inputs by their literal binding ordinal. No database,
mapping, or set iteration order participates.

Missing, duplicate, extra, reordered, cross-version, or cross-run rows fail
`METHODOLOGY_TRUTH_DASHBOARD_INCOMPLETE` before deliverable persistence.

A source-derived defect may produce dashboard `RED_FLAG`. A non-zero formula
variance is proposed as `formula_disposition=OUT_OF_BALANCE`, backed by its
calculation root, while the dashboard row remains `ANSWERED` unless it already
has the frozen adverse/contradiction root. Calling a later calculation result
a Task-6 defect would mutate a sealed prior-step output and is forbidden.

## 8. PostgreSQL and transaction design

### 8.1 Proposed migration `0039`

Migration `0039`, after explicit design approval, creates only the two frozen
v21 dashboard table shapes byte-for-byte and consumes the reviewed v23
authorities for their guards/ACLs. It installs:

- exact keys, FKs, uniques, checks, indexes, deterministic UUID rules, and
  root-projection rules from v21;
- `ENABLE` and `FORCE` RLS with the canonical null-guarded predicate in both
  `USING` and `WITH CHECK`;
- app-role CRUD and the hash-bound least-privilege guard-owner closure;
- lease-fencing and append-only behavior; and
- the deferred dashboard-root equation.

The RLS inventory changes `67 -> 69`. Migration `0034` remains historical and
unchanged. Downgrade refuses before mutation if either new table or its output
graph is populated; empty `0038 -> 0039 -> 0038 -> 0039` must reproduce exact
catalog and ACL state. Migration source may not be written until the v23
artifact, fixture, schema-regeneration proofs, and SQL-authority execution
census are green.

### 8.2 Partial-FUTURE38 blocker

Frozen `native-root-exact-one-v1` references all 18 future native tables in one
SQL body and cannot execute when only the dashboard table exists. The frozen
erasure runner likewise activates only when all 38 FUTURE38 relations exist;
CURRENT48 cannot delete dashboard rows.

Recommended Task-8 disposition:

1. Authorize one monotonic, hash-bound partial exact-one guard that extends the
   installed current-native census with
   `methodology_truth_dashboard_rows`, while preserving the frozen complete
   future guard for final activation.
2. Do not create a partial privileged eraser. The v23 CURRENT48 runner retains
   its exact delete set/order but, after taking the existing hash-bound
   tenant/deal advisory lock and before its first mutation, executes an exact
   tenant/deal existence check against
   `methodology_truth_dashboard_rows`. Any row raises stable code
   `TASK8_DASHBOARD_ERASURE_FUTURE38_REQUIRED` with SQLSTATE `23514`; the
   transaction deletes and updates zero rows.
3. Grant the dedicated erasure function owner only column-level `SELECT
   (tenant_id, deal_id)` on the dashboard parent for that precondition. It
   receives no dashboard `DELETE`, `INSERT`, or `UPDATE`; the executor role
   receives no table privilege; the application role retains no runner
   `EXECUTE`. Readiness observes that exact ACL and rejects any superset.
4. Dashboard completion takes the same deterministic tenant/deal advisory lock
   in its short insertion transaction. Thus a dashboard write cannot race
   between the erasure precondition and CURRENT48 deletion. If erasure owns the
   lock first, the later dashboard insert revalidates its now-missing durable
   parents and fails atomically.
5. Direct dedicated-function invocation, the API route, and the application-
   role catalog proof each exercise the refusal with pre-existing CURRENT48
   data plus dashboard rows and prove zero mutation. The check is not an API-
   only convention.
6. Keep both immutable v21 and approved v23 `INACTIVE`; no production strict
   run can write these rows before the complete activation census and separate
   activation ruling.

Without item 1, Task 8 can create schemas but cannot honestly claim the native
root invariant. Without the database-enforced items 2–5, CURRENT48 could
silently omit durable dashboard evidence.

### 8.3 Completion boundaries

1. **Calculation step:** stage every calculation, CalcSanad, and native output
   root. One lease-fenced Task-7 completion transaction validates the complete
   formula plan, inserts all rows/roots, derives expectations/receipts, marks
   the step complete, and writes audit. A missing or invalid calculation rolls
   back the entire step.
2. **Dashboard step:** one lease-fenced completion transaction inserts the
   complete dashboard parent/root-link set, native roots, expectations,
   receipts, step completion, and audit. Deferred guards run before commit.
3. **Deliverable step:** a repeatable-read, tenant-scoped collector rehydrates
   the 79 FDD rows and every referenced root, rederives hashes, builds the
   artifact, and persists it through the existing deliverable boundary.
4. **Restart:** rehydrate only by exact
   `(tenant, deal, run, coverage, question, output-root)` coordinates. No
   process-local record and no deal-wide “already exists” list is accepted.

Strict PostgreSQL composition can never select the in-memory calculation or
dashboard repository.

## 9. RED-first implementation sequence

### Task 8.1 — policy and successor-contract materialization RED

- Real-workbook test proving 79 rows, zero workbook formulas, exact source
  hashes, and failure of the current 29/13 sheet-wide mapping.
- Artifact admission tests for all 79 literal rows, the exact ten anchors,
  69 empty requirements, every expanded predicate/row binding, formula DAG,
  the deliberate debt/off-balance-sheet exclusions, documentary completeness
  requests, direct-signed conventions, actionable failure schema, hashes, and
  per-section corruption.
- Deterministically regenerate the human-readable mapping from the policy
  artifact and prove every rendered row is an exact join to the pinned
  read-only workbook; no workbook prose is emitted in test output.
- Exact-diff test proving the new FDD version changes only version/policy-bound
  fields and `required_calculations`; source traces/question identities remain
  unchanged.
- Legacy v21 contract bytes regenerate byte-identically; the proposed
  FDD-specific schema snapshots cannot be selected by v21, and v23 remains
  `INACTIVE` and absent from runtime contract locking.
- Snapshot the complete closed FDD signed-ratio and formula schemas, including
  their legal/illegal case vectors, but do not write the production Pydantic
  models or formula executor before the literal gate returns.
- Hash tests proving every governed field changes the formula/policy hash.
- Changed SQL-authority bodies pass legal, illegal, and direct-SQL execution
  census cases on disposable PostgreSQL as the application role before the
  v23 fixture embeds; no migration or durable application database is used.
- Complete the four-file gate atomically with v23 last as the commit point.
  Because the append-frozen policy-v2 fixture and artifact are already
  canonical, the production path verifies those exact bytes and their reviewer
  map as immutable pre-admitted prerequisites before writing anything, then
  links the v23 fixture and finally the v23 artifact. The receipt reports the
  verified prerequisite set separately from the created publication order; it
  never rewrites or misreports the policy-v2 pair. Fresh disposable targets
  retain the full four-file order proof. Keep v23 `INACTIVE`, then stop for
  review.

### Task 8.2 — durable calculation RED

- Cross-row typed binding through real Task-6 durable factual outputs; prose
  parsing is rejected.
- Exact instance-set/cardinality and required-formula coverage.
- Deterministic IDs, parent-calc DAG, CalcSanad, atomic rollback, replay, hash
  drift, restart, lease fencing, and zero in-memory strict fallback.
- Production composition-root test entering through the canonical worker step
  with hermetic Task-6 facts and zero provider/model call.

### Task 8.3 — migration/dashboard RED

- Exact frozen two-table schema, RLS `69`, ACL/owner posture, same-tenant CRUD,
  cross/unset-tenant read/write denial, zombie fencing, and append-only direct
  SQL rejection.
- Empty upgrade/downgrade/upgrade, populated downgrade refusal, and exact
  restoration.
- Legal/illegal/direct-SQL dashboard equation cases for GAP, ANSWERED, RED_FLAG,
  all-found and mixed found/not-found results, required calculations,
  wrong/extra roots, projection drift, and all 100 selected coverage rows.
- Explicit partial-FUTURE38 guard plus direct-function/API/catalog
  fail-closed erasure tests, same-lock race tests in both directions, exact
  least-privilege ACLs, stable refusal code, and zero mutation.

### Task 8.4 — financial artifact RED

- Exact 79-row ordered artifact from the production deliverable composition
  root.
- Answer, gap, source-red-flag, and formula-out-of-balance branches.
- Mixed found/not-found row preserving every result and subordinate gap.
- Missing/extra/duplicate/reordered/cross-run roots, ungoverned number/text,
  absent CalcSanad, formula mismatch, and replay mismatch all fail closed.
- Unknown/partial fields, wrong nullability/cardinality/order, hash-preimage
  drift, and any noncanonical Decimal/UUID/SHA/timestamp/Unicode value fail.
- Durable artifact/manifest/hash/download and restart rehydration proof using
  hermetic facts only. No live provider/model call occurs in Task 8.

### Task 8.5 — verification checkpoint

- Focused Task-8 suites.
- Task-5, Task-6, Task-7 and Task-9 Track-B regressions.
- Disposable PostgreSQL application-role migration/RLS/ACL proofs.
- Full declared-environment repository suite.
- Ruff format/check, mypy, forbidden scan, contract lock, import identity, and
  `git diff --check`.
- Stop uncommitted for Task-8 review with hashes and exact counts.

## 10. Approved reviewer rulings

On 2026-07-25 the reviewer approved all eleven rulings below, subject to the
five binding conditions in section 1.1. The approval permits Task-8.1 literal
materialization only:

1. The original ruling approved append-frozen `full-strict-v22`; the
   2026-08-13 second gate permanently rejected that candidate and approved
   `full-strict-v23` with the acyclic identity amendment in section 3.2.2.
   V21 remains immutable and `INACTIVE`.
2. Approve the separate FDD calculation models, FDD-only signed-ratio factual
   value, and Step-7/Step-11/Step-15 version-aware schema split. The legacy
   v21 `ValueStruct`, `CalcType`, and `CalcInputs` classes are not widened or
   aliased.
3. Approve the ten formula types, exact expanded predicate/row bindings,
   equations, DAG, signs, precision, rounding, currency/unit/period rules,
   canonical/hash preimages, and no-default-zero rule.
4. Approve the ten anchors, especially `Assets:19` and `Liabilities:24` as
   coordinate-only sheet-control anchors; approve component-derived totals
   with no fabricated reported-total predicate, plus the deliberate exclusion
   of off-balance-sheet Liabilities rows 19–24 from the balance-sheet sum.
5. Approve `GROSS_MARGIN` as the only attached existing core formula and the
   removal of the coarse sheet-wide optional `BURN_RATE` mapping.
6. Approve the new immutable FDD methodology-version identity and strict
   selection transition; the Task-5 version stays frozen.
7. Confirm that all ten formula anchors are `required=true`, and therefore
   cannot finish as `GAP`; a governed gap in any mandatory source binding
   prevents the complete 79-row artifact and fails Step 11 rather than
   weakening formula applicability at runtime.
8. Approve `dashboard-root-equation-v2`, including preservation of every
   mixed found/not-found Task-6 result under `ANSWERED`/`RED_FLAG`, one durable
   row for all 100 CDD+FDD coverage records, and exactly 79 FDD rows in the
   financial artifact.
9. Approve `OUT_OF_BALANCE` as calculation-backed output rather than
   retroactively fabricating a Task-6 adverse root; dashboard `RED_FLAG`
   remains reserved for rows with a real adverse/contradiction root.
10. Approve the monotonic partial exact-one guard and the exact database-level
    interim erasure refusal/lock/ACL contract in section 8.2. No partial
    privileged erasure runner is proposed.
11. Approve the second literal artifact/fixture review gate in section 3.2;
    semantic approval permits RED materialization only.

## 11. File ownership and unchanged boundaries

Task 8 owns the future Task-8 policy/formula/dashboard/deliverable modules,
tests, migration `0039`, and narrowly required existing calculation,
methodology, worker-composition, readiness/RLS, erasure refusal, and plan
surfaces.

Track B retains its declared Task-9 new-file provider modules/tests and the
approved registry-derived scanner/count cleanup. Any path overlap is a
stop-trigger before editing.

Unchanged:

- `full-strict-v21` remains immutable and `INACTIVE`; rejected v22 and v23 are
  permanently blocklisted; candidate v24 cannot be selected before its
  reviewed freeze/activation gates;
- Task 11 still owns the complete 27-cut E028–E036/E048 proof;
- no Task-9 provider/model live call is allowed;
- no prompt promotion occurs before Task 12;
- no commit, push, PR, or merge occurs before Task-8 checkpoint approval; and
- no new table, role, environment variable, or flag outside this design may be
  introduced without an amendment.

## 12. Full-strict-v24 second-gate verification record

The 2026-08-14 second-gate audit result is `PASS`. Per the ruling, this is a
hermetic, no-PostgreSQL materialization census only. It does not activate the
contract or satisfy the later database/runtime gates.

Exact authorities:

- v24 artifact SHA-256:
  `70834e33249a414e5dcc744cdc60934d9d2ed6c8ab3f09c64cf3edfa3f05e447`;
- independently derived v24 fixture SHA-256:
  `444e4c573e7ba44185efe0b4c39339797b2df91a0fcbf7841129e131e406526e`;
- v24 pipeline-contract SHA-256:
  `f05f17f98ea6a8d0356cef5939b7395fef294eb490a77cf8aeecf1ece25dd164`;
- identity/hash graph SHA-256:
  `5c8b00d2e0930d4c1d5e5a4fbcc1db9ca74f9b28515e48da3a7021a835d84c5f`
  (`67` nodes, `35` edges, `834` leaves, `67` source pointers);
- conservation SHA-256:
  `635035b2e2a9e88c4cf1a90a1fb4023197f0c0df0bb84e181db2daefddfb36de`
  (`7` approved semantics, `18` literal byte receipts); and
- policy/reviewer-map binding SHA-256:
  `6e6841d07b1891b6692ba210bc1be55aaee7f40a3bb6ffafdad56e5a21212bb3`.

The exact frozen-v21 parent is artifact SHA-256
`2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd`
and contract SHA-256
`46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794`.
The policy-v2 pins are artifact SHA-256
`e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064`,
policy SHA-256
`2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9`,
immutable fixture SHA-256
`b95c426da57a64336da990002201f4d5fdfe616dbae095a93e3aaf9372cf242e`,
and reviewer-map SHA-256
`98cf84d4498f57465cd61501ea8bbbd93f7507d8628a61d7cbcf9cede459fe2a`.

Independent verification and the CLI verification path passed `65/65`, with
CLI result `VERIFIED`. The hermetic census is: `28` steps, `48` rules, `1,756`
routes, `19` extractors, `18` providers, `68` schemas, `34` current-DDL
tables, `69` tenant-RLS tables, and `48 current + 38 future = 86` erasure
relations. The `27` Task-11 restart cuts and `10` undischarged rules remain
explicitly outside this result.

The final audit closed six materialization defects without changing approved
semantics: complete SQL-segment consumption, full graph-producer closure,
literal byte-receipt conservation, raw reviewer-map binding, closed
digest/identity/reference grammar, and immutable generator-prerequisite
handling. The last item means derive-only is a fully verified zero-write path,
while write mode treats the exact policy-v2 fixture as a prerequisite and
creates only an absent v24 fixture.

The canonical void-v22 fixture was removed only after exact archive proof of
SHA-256
`4368d86b6bdd270f03d94bbc591a9ee2f6fbc6c36c9c65076cd1549d2a4da56b`;
its blocklist and reconciliation evidence remain permanent.

The v24 artifact and fixture are both `INACTIVE`. There is no atomic gate lock
or publication, migration `0039`, formula/runtime implementation, PostgreSQL
or other database call, live call, or commit at this checkpoint.

## 13. Installed-RLS count-domain correction and v27 disposition

The 2026-08-15 pre-census source audit supersedes only the installed-RLS count
statements in sections 2.3, 3.1, 8.1, and the historical v24 record above. It
does not rewrite those historical records or change any approved semantic
vector.

The exact migration chain establishes the installed authority independently:

- migration `0034` normalizes and forces `42` tenant tables;
- migration `0036` adds `4`, yielding `46`;
- migration `0037` adds `9`, yielding `55`;
- migration `0038` adds `9`, yielding `64`; and
- the two already-reviewed dashboard tables yield `66`.

The former `67 -> 69` statement conflated this installed authority with Task
7's separate frozen **future physical census**: `34 current-DDL + 18
future-native + 15 future-reference = 67`. That physical baseline remains `67`
and later materializes to `72`; identity-graph node/source counts that also
equal `67` remain unchanged. Neither value is an installed-RLS input.

The exact sorted 66-relation application inventory is independently bound by
SHA-256
`e06330cdf5eabf5e1f95da4e66537de60476460ff9af6c0ebd7fd920a5ae6c39`
over canonical JSON domain
`task8-v27-post-0038-plus-scaffold-public-ordinary-table-inventory-v1`.
Alembic's `public.alembic_version` is a separate excluded metadata relation:
the physical public ordinary-table catalog therefore contains `67` rows, while
the application grant and FORCE-RLS domain contains exactly `66`.

Standing Delegation decision `T1-20260815-010` classifies `64 -> 66` as a
mechanical admission-literal count correction. The defective hash-bound v26
draft is permanently void and non-selectable; v27 derives from frozen v21 plus
the complete approved Task-8 delta set and this correction. Equation-v3, its
ten requirement tuples, schemas, predicates, ACL semantics, UUID/hash recipes,
graph rules, event/outcome sets, erasure schedules, and conservation semantic
rows remain byte-identical. V27 stays `INACTIVE`; census, publication,
migration `0039`, activation, commit, and live/provider calls remain held at
their existing gates.

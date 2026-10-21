# GL-02 obligation register: design (version 2.4)

Date: 2026-10-20 (revised 2026-10-21). Status: version 2.4, after the independent code review of the ledger, review-pack and audit-input packet R (four corrections, sections 5.14, 7 and 11) and the two-step omission-audit method (section 8), and before that after an independent documentation review (changes required, all points taken), a builder check, the first run of the harness on the real workbook (three corrections, section 6), the independent code review of the harness (three corrections and ten hardening items, sections 6 and 7) and the first commercial shard (six commercial derived-rule codes). Scope owner: the go-live program, item GL-02 ("complete fund requirements and output mapping").

This document is privacy-cleared. It names sheets, rows, cell coordinates, identifiers, counts and hashes. It never quotes or paraphrases the fund workbook. Everything fund-derived lives in a git-ignored private directory described in section 4.

## 1. Intent

The fund gave IDIS a financial due-diligence workbook and IDIS carries a commercial due-diligence template. Together they hold 100 top-level items: 79 financial items (P&L 29, Cash Flow 13, Liabilities 21, Assets 16) and 21 commercial questions. Importing them as 100 rows is not the same as answering them. Most items hold several separate things an analyst must do or decide, and some items inherit instructions from a merged cell or from another row.

GL-02 turns every item into a complete, reviewable list of child obligations. A child obligation is one thing a finished analysis must show: a fact to establish, a number to compute, a judgment to make, an evidence standard to meet, or a completeness condition to satisfy. The register is the contract that later work (retrieval, calculation, specialist analysis, scoring, reporting, correction) is measured against. A finished diligence answer means every child obligation has an adequate answer or an explicit disposition. It does not mean 100 rows were imported.

Why now: the pilot, the formula work (GL-07/GL-08), the specialist work (GL-12), the report mapping (GL-14/GL-15) and the full acceptance run (GL-22) all need stable obligation identifiers and a way to prove that nothing in the source instructions was dropped.

## 2. Goals, non-goals, anti-goals

Goals.

- Every one of the 100 parent items has at least one active child obligation, and every material stretch of source wording, in every text field, is either quoted by a focused child obligation or explicitly dispositioned.
- Merged-cell instructions and cross-row instructions reach every row they apply to.
- Source wording is stored exactly as found, with merge ancestry. Normalized wording is a separate field.
- The policy-v2 formula bindings (79 questions, ten formula anchors, 107 source bindings) are carried by child obligations and none is lost.
- A mechanical check can find a dropped instruction without anyone reading fund text, and an independent reviewer can read the private files locally and say what is missing.
- The register can be filtered. The two-question diagnostic subset is a view over the register, not a separate artifact.

Non-goals.

- No numerical answer keys. The workbook has no executable formulas (zero formula cells) and none is invented.
- No change to the 79/21 parent counts, parent identifiers or the frozen methodology. Children never replace or renumber parents.
- No new question framework. The register extends the existing methodology types by reference and reuses their field names.
- No contract-lock or `schemas/` file. A schema under `schemas/` needs contract review first (backlog GL-02). Models and any JSON schema live inside the package, next to the existing category-map precedent.
- No change to the source workbook, the commercial template or the specialist registry.
- No definitive answers, scores or recommendations. Unresolved guidance stays visible and blocks affected definitive answers.

Anti-goals (a register that technically passes and is still wrong).

- A register that looks complete because it has many children but silently dropped an instruction that sits in a merged cell, a cross-reference, a short qualifier or a label.
- One child that quotes a whole cell so that every list item inside it is "covered".
- Fund wording leaking into the public tree, a commit message, a status record, a reviewer prompt or a hash that can be guessed.
- A child count chosen in advance. The count is whatever the analysis finds.
- Analyst additions presented as if the fund had written them.
- Source instructions silently "fixed" where the workbook is ambiguous or apparently wrong.

## 3. Reuse first

| Existing asset | Used for | Not used for |
|---|---|---|
| `src/idis/methodology/importers/fdd_excel.py` projection (`validated_production_fdd_workbook_projection`, `PRODUCTION_COLUMNS`, `EXPECTED_ROW_COUNTS`) | The 79 row coordinates, source anchors, row ordinals and `methodology_question_id` values. The register must reproduce them exactly. | Wording. The importer strips edge whitespace and keeps one resolved string per role. The register keeps raw text and per-cell merge ancestry. |
| `src/idis/methodology/ids.py` (`normalize_text`) | The normalized-wording field. | Storage of source wording. |
| `src/idis/methodology/models.py` (`RequiredEvidence`, `RequiredCalculation`, `FddFormulaRequirement`, `AssignedAgent`, `ReportMapping`, `MethodologySourceTrace`) | Field names and meaning: `evidence_type`, `min_count`, `report_section`, `report_subsection`, `role`, `responsibility`. Register records reuse these names. | A second parallel question model. |
| `idis.models.document_classification` (`FddDocumentCategory`, `CddDocumentCategory`) and the category aliases of `src/idis/services/extraction/task_planner.py` | The governed document categories that span admission uses. Each evidence requirement links to the categories whose documents can carry it. | Anything other than the productive categories (never `unsupported` or `unknown`). |
| Policy v2 (`contracts/fdd_formula_policy_v2.json`, git-ignored; loader code in `fdd_formula_policy_v2.py`) | The ten formula anchors and 107 source bindings, read as plain JSON to build a per-parent binding view. | Importing or calling the policy constructor. A repository test forbids any other source module from naming it. |
| `src/idis/methodology/templates/commercial_dd_v1.json` | The 21 commercial question identifiers, one-sentence questions, required-evidence stubs, template role names, report mapping. | Completeness. The template states a minimum of one evidence item per question, which is not a completeness standard. |
| `src/idis/methodology/mappings/commercial_dd_v1_question_categories.json` and `category_mapping.py` | The governed question-to-document-category map for the commercial questions. | Anything beyond document categories. |
| Master plan section 3 (`docs/go-live/GO_LIVE_MASTER_PLAN.md`) | Source-coordinate namespaces, the minimum private mapping obligations per sheet, the ten formula anchors, the named clarifications, the 21-row commercial table, the child-record field list. | Wording. It is the specification, not the fund text. |
| The eight registered specialist agents (`financial_agent`, `historian_agent`, `market_agent`, `risk_officer_agent`, `sector_specialist_agent`, `team_agent`, `technical_agent`, `terms_agent`) | The only allowed values for the responsible specialist of a child. The template's own role names are recorded separately as `template_role`, with a proposed alias map (section 5.11). | Changing the registry or the template. GL-12 owns that. |
| `tests/slice100_task5/_workbook_factory.py` | A fictional production-shaped workbook for tests. New fixtures extend it by import. | Real workbook content. |

## 4. Privacy and authorization

Authorization. On 2026-10-20 the program owner answered "Yes. The GL-02 agent may read the private workbook locally, emitting only counts and hashes, and the register stays private." and then instructed the coordinator to read the private workbook locally and continue GL-02 under the existing authorization, to read the substantive questions, child requirements, formulas and template structure needed to reconcile the complete scope (79 financial parents, 21 commercial questions and their substantive child obligations), to keep the two-question subset as a diagnostic only, to keep original inputs and fund-derived contents private, to publish only privacy-cleared implementation and documentation, and not to ask again for routine local reads that the work needs. Both are recorded verbatim in RUN_STATE record 169. The harness packets (A, B, R) never read fund content: they use fictional fixtures only. Authoring packets (C) read the private derived files in a worktree copy. Reviewers read the private files with local read tools.

Public (committed, reviewable by anyone with repository access):

- Package code, CLI, tests with fictional fixtures, this document, the runbook and the public coverage manifest.
- The public manifest holds identifiers, sheet names, row numbers, cell coordinates, counts, controlled-vocabulary counts and SHA-256 values at file level plus one aggregate digest per sheet. It holds no per-text hash of a short field and no free text taken from, or written about, the fund's wording. Parent identifiers are safe to publish: they are SHA-256 values computed over the text and the workbook hash together, so they cannot be reproduced without the workbook. The manifest builder checks every key against an allowlist and every value against the exact format that key permits (64 lowercase hex, coordinate, identifier, vocabulary value from a code-pinned enumeration, integer, boolean). Any other string is rejected.

Private (never committed; git-ignored):

- Default root `.local_reports/gl02-private/` in the main checkout, and a copy of that directory at the same relative path inside any authoring worktree. `.local_reports/` is already ignored, so no `.gitignore` change is needed. The main-checkout copy is canonical. A worktree copy is a working area, synced back by a script that refuses when any other shard changed, and removed after a verified sync.
- The private root has these parts. `derived/` is regenerated by the extractor from the workbook, the template and the policy file and must be byte-identical on every run: `source_cells.jsonl`, `parents.jsonl`, `merged_instructions.jsonl`, `policy_view.jsonl`, `extraction_report.private.json`. `authored/` holds what analysts and the coordinator write, one file per shard under each kind: `profiles/`, `children/`, `cross_row/`, `dispositions/`, `ambiguities/`, `omission_audit/`, `role_aliases/`, `baseline/`, `views/`, each with one `<SHARD>.jsonl` (kinds `baseline` and `views` use the shard code `XS`). The shard codes are `PL`, `CF`, `LIAB`, `ASSET` (financial sheets), `CDD` (commercial) and `XS` (coordinator-owned, cross-shard). `ledger/` holds the append-only issued-identifier ledger and `seals.jsonl`. `manifest/` holds the generated `register_manifest.private.json`. `authoring/` keeps the authors' own generator scripts for provenance. The register is the set of shard files; there is no merge step. The validator and the manifest read every shard.
- Backup. The register is kept on this machine only. Sealing writes a timestamped local snapshot next to the root. No off-machine copy is made without a decision (section 13).

Handling rules.

- The coordinator and the authoring agents read the workbook only on this machine and write fund-derived content only into the private root. The authoring agents run under the existing routing (Cursor Agent with `grok-4.7-xhigh`; independent reviewers on actual Claude Opus 5.5 sessions), so the workbook text is processed by those model providers. This follows from the owner's instruction and the standing routing ruling. It is listed in section 13 as a disclosure.
- RUN_STATE, START_HERE, commit messages, reviewer prompts and the public summaries of reviewer reports carry identifiers, coordinates, counts and hashes only. Reviewers read private files with local read tools. A reviewer returns a public summary (no fund wording) and a private findings list that stays in the private evidence folder.
- A coordinator-only leak scan builds the word 5-grams of every workbook string and scans the public tree, status records, prompts and commit text. Any match is triaged by hand before anything is published.
- No key, token, dotenv or credential is read. The CLI takes explicit paths and never reads the environment for them.
- The CLI prints text only when given `--show-text`, and never writes text anywhere except the private root.

## 5. Register data model

All records are one JSON object per line (`.jsonl`), UTF-8, LF, keys sorted, one deterministic writer. Hash values are lowercase SHA-256 hex. Models are strict (`extra="forbid"`, frozen). Strings preserve exact characters, including newlines and edge whitespace.

### 5.1 Identifiers

| Thing | Identifier | Example shape |
|---|---|---|
| Financial parent | The existing `methodology_question_id` from the importer (full 64-hex form). | `mq_financial_dd_<64 hex>` |
| Commercial parent | The exact template question id. | `mq_commercial_dd_market_size_0001` |
| Public coordinate of a financial parent | Master-plan namespace plus source row, three digits. | `FDD-PL-004`, `FDD-CF-016`, `FDD-LIAB-024`, `FDD-ASSET-019` |
| Public coordinate of a commercial parent | `CDD-` plus the upper-case suffix. | `CDD-MARKET_SIZE` |
| Child | `GL02-` plus the parent public coordinate plus `-C` plus a three-digit sequence. | `GL02-FDD-PL-004-C001` |
| Merged instruction | `GL02-SI-` plus sheet code plus anchor cell. | `GL02-SI-LIAB-E4` |

Child identifiers are stable. A child is never renumbered or deleted. It is retired (`status: retired`, with a reason) and its number is not reused. New children take the next number. The issued-identifier ledger (section 5.14) makes a deletion or a parent change detectable.

### 5.2 Source cell (`derived/source_cells.jsonl`)

One record per cell anywhere on the four sheets that has a stored value or lies inside a merged range, including empty cells that are merge children. Nothing outside the production rectangle is skipped: row 1, column A, columns beyond the question column and rows below the last data row are read too.

| Field | Meaning |
|---|---|
| `cell_id` | `sheet!A1` form, for example `Liabilities!E5`. |
| `sheet`, `sheet_ordinal`, `row`, `column`, `column_letter` | Coordinates. |
| `role` | `header`, `term`, `nature`, `line_item`, `question`, `other`. A cell with a stored value and role `other` is an error until a person decides how to treat it. |
| `raw_text` | The stored cell string, unmodified. `null` for an empty cell that is a merge child. |
| `raw_sha256` | Hash of the UTF-8 bytes of `raw_text`, `null` when `raw_text` is `null`. Private file only. |
| `merge_range`, `merge_anchor`, `is_merge_anchor` | Merge ancestry (`null`/`false` when not merged). |
| `resolved_from` | For a merge child, the anchor cell whose text applies. |
| `edge_whitespace` | True when `raw_text` differs from `raw_text.strip()`. |
| `newline_count` | Count of `\n` and `\r` characters in `raw_text`. |

The extractor also records, as counts, any workbook feature that could carry instructions outside cell text: comments, hyperlinks, hidden rows or columns, data validations, conditional formats, images, defined names, non-string cell values, formula cells, sheets that are not visible. Any non-zero count is an error until a person decides how to treat it. At the first local read of the approved workbook every such count was zero and every stored value was a string.

### 5.3 Parent (`derived/parents.jsonl`)

| Field | Meaning |
|---|---|
| `parent_id`, `public_coordinate`, `parent_kind` (`financial` or `commercial`) | Section 5.1. |
| `methodology_version_id`, `source_hash` | Financial: the workbook version and workbook SHA-256. Commercial: the template version and the governed source hash recorded in the category map. |
| `sheet`, `source_row`, `row_ordinal`, `sheet_ordinal` | Financial coordinates. `null` for commercial except `row_ordinal` (1 to 21). |
| `term_raw`, `nature_raw`, `line_item_raw`, `question_raw` | Exact text of the resolved cell for each role. `question_raw` is the exact question text. For a merge child it is the anchor's exact text. |
| `term_cell`, `nature_cell`, `line_item_cell`, `question_cell` | Each `{cell_id, merge_anchor, merge_range}` so a merged term group is traceable from every row in it. |
| `question_raw_sha256`, `question_normalized`, `question_normalized_sha256` | Normalized text is `normalize_text(question_raw)` from `ids.py`. |
| `merged_instruction_ids` | Merged instructions (section 5.4) whose text applies to this parent. Cross-row instructions are authored and are not listed here. |
| `edge_whitespace_roles` | Roles whose raw text has edge whitespace. |
| `commercial_context` | Commercial parents only: the template `report_mapping`, template role names (`template_role`), target document categories and the governed category-map categories. |

### 5.4 Shared instructions

Two kinds, in two files with two owners.

- `derived/merged_instructions.jsonl` (extractor-owned): every merged range in a question column that spans more than one row. Records the anchor, the range, the covered rows and the covered parent ids. The baseline records two such ranges, in Liabilities (E4 to E6 and E17 to E18). The extractor derives the list from the workbook and must find them.
- `authored/cross_row/<SHARD>.jsonl` (author-owned): an instruction that the source text applies to other rows or to another sheet (for example a statement that something holds for all rows of the sheet, a pointer to a row above, or a pointer to a reconciliation on another sheet). The extractor cannot detect these. Each record has `instruction_id`, `kind` (`same_as_above`, `valid_for_all`, `cross_sheet`, `other`), `source_parent_id`, `source_field`, `source_quote` (resolved like a child quote), `target_parent_ids` (non-empty), `note`. The author who owns the target parents declares and applies it: the record lives in the shard of the targets, the source text may live in any shard, and each applied child cites the source parent's text. A record whose targets span shards is split into one record per target shard. The master plan names one instruction that must not be lost when questions are split: the P&L historical, business-plan and cash reconciliation instruction.

### 5.5 Policy view (`derived/policy_view.jsonl`)

Generated by the CLI from the private policy-v2 file, read as plain JSON. One record per parent that carries a formula anchor or a source binding: `parent_id`, `anchors` (formula ordinal and type for each of the ten anchors at that row), `bindings` (formula ordinal, binding ordinal, role, predicate, sign transform, cardinality, period role for each source binding that cites the row). Input specs that are not source bindings are counted and reported. If the policy file is absent the file is empty and every check that needs it reports `SKIPPED_POLICY_ABSENT`, which never counts as a pass.

### 5.6 Parent profile (`authored/profiles/<SHARD>.jsonl`)

One record per parent, authored. It holds defaults so that child records do not repeat what is common. Fields: `parent_id`, `default_coordinates` (section 5.7), `default_producer`, `default_consumer`, `default_specialists`, `default_output_destination`, `default_analyst_review`, `default_capability`, `default_fund_feedback_status`, `parent_summary_note` (private, optional). A child field overrides the profile default when present.

### 5.7 Child obligation (`authored/children/<SHARD>.jsonl`)

Every child record is resolved against its parent profile by one function. Validators and exports use the resolved view.

| Field | Required | Meaning |
|---|---|---|
| `child_id`, `parent_id`, `status` (`active`, `retired`), `retired_reason` | yes | Identity. |
| `derivation` | yes | `explicit` (the source text states it), `enumerated_item` (one item of a list the text gives), `cross_reference_applied` (an instruction from another cell applied to this parent), `plan_minimum` (a master-plan minimum private mapping obligation for the sheet, not stated in the cell), `formula_policy` (carried from a policy-v2 anchor or binding), `template_field` (from a commercial template field or the master-plan commercial table), `analyst_inference` (needed for an adequate answer but not stated). |
| `derivation_rationale` | for every derivation except `explicit` and `enumerated_item` | Why this obligation exists. For `analyst_inference` it must say what in the source makes it necessary. |
| `source_quotes` | at least one for `explicit`, `enumerated_item`, `cross_reference_applied` | Each `{parent_id (default: own), field (question, line_item, term, nature; default question), text, occurrence (0-based index among non-overlapping exact matches; default 0)}`. The validator resolves the exact `[start, end)` offsets and rejects a quote that is not an exact substring. |
| `joint_answer_reason` | when the child's quotes reach more than two material segments of one field of the parent | Why one answer must cover them together. Counted in the manifest and read first by the reviewer. |
| `shared_instruction_ids` | when the obligation comes from a merged instruction | Links the child to the merged instruction. |
| `cross_row_instruction_ids` | for `cross_reference_applied` | Links the child to the declared cross-row instruction. |
| `plan_minimum_codes` | list, may be empty | Plan-minimum codes (section 7) that this child carries, whatever its derivation. |
| `obligation_statement` | yes | One or two sentences in analyst wording saying what a finished analysis must show. Private. |
| `required_facts` | at least one of facts, calculation rules or judgment | Each fact: `fact_id` (`F01`, `F02`, ...), description, and which coordinates it needs. |
| `calculation_rules` | see left | Each rule: `kind` (`policy_v2_anchor`, `policy_v2_binding`, `derived_gl08`, `none_needed`), `formula_ordinal` and `binding_ordinal` for policy kinds, `rule_code` for `derived_gl08` from the list in section 7, and a short note. |
| `judgment_sought` | see above | What a person or specialist must conclude, when the obligation is judgment rather than fact or arithmetic. EBITDA calculation and forecast realism are separate obligations (master plan section 3). |
| `acceptable_evidence` | yes, at least one | Each: `evidence_type` (analyst vocabulary, at most 30 values plus `other_specified`, versioned), `document_categories` (list of `FddDocumentCategory` values for financial children or `CddDocumentCategory` values for commercial children; may be empty for evidence that is not a document), `description`, `independence` (`internal`, `external_independent`, `management_estimate`, `either`), `min_count`. |
| `completeness_conditions` | yes, at least one | When the obligation counts as covered. |
| `applicability` | yes | `decision` (`always`, `conditional`, `not_applicable_by_default`) and, for the latter two, the condition. A non-applicable decision is never silent deletion. |
| `coordinates` | profile default or explicit | Seven keys: `entity`, `consolidation_scope`, `period`, `actual_forecast_status`, `currency`, `unit`, `adjustment`. Each is `required`, `inherit_from_deal` or `not_applicable`, with an optional note. |
| `producer`, `consumer` | profile default or explicit | `kind` from a pinned vocabulary plus a short detail. Producer kinds: `methodology_extraction`, `deterministic_calculation`, `specialist_analysis`, `analyst_review`, `management_inquiry`, `external_enrichment`, `cross_check`. Consumer kinds: `reconciliation_check`, `scorecard_input`, `report_section`, `downstream_calculation`, `challenge_layer`, `defect_register`, `fund_deliverable`. |
| `responsible_specialist` | profile default or explicit | Subset of the eight registered agent types. For commercial children also `template_role`. |
| `analyst_review` | profile default or explicit | `{required, reason}`. |
| `output_destination` | profile default or explicit | `{template, report_section, report_subsection, deliverable_ref, status, deviation_reason}`. Financial default: template `financial_dd`, `report_section` the sheet name, `report_subsection` the line-item label (the existing importer mapping), `deliverable_ref` null, `status` `provisional` until the fund template is confirmed. Commercial default: the template's `report_mapping` strings (they are canonical; the master plan's CamelCase destinations are shorthand for them), `status` `confirmed`. A deviation needs a reason. |
| `capability` | profile default or explicit | `{status: unassessed | supported | partial | gap, dependencies: [GL ids], note}`. `unassessed` is allowed at register time. Children whose calculation rules are policy kinds start as `partial`. |
| `acceptance_case` | yes | What an adequate answer shows, in analyst wording, beyond "a reference exists". |
| `acceptance_evidence_refs` | list, empty at register time | References to test cases or evidence packages that demonstrate acceptance. |
| `ambiguity_ids` | when the source is unclear | Links to the ambiguity records. |
| `fund_feedback` | profile default or explicit | `{status: proposed | under_review | accepted | changed | rejected, note}`. Every child starts as `proposed`. |

### 5.8 Dispositions (`authored/dispositions/<SHARD>.jsonl`)

The conservation mechanism for "no wording or instruction lost" (section 6, rules I-06 and I-07). Two kinds of record. Kind `run`: one record per uncovered run that the validator reports as material. Kind `marker`: one record per cross-reference marker occurrence that is not accounted for by a child quote. Fields: `kind`, `parent_id`, `field`, `start`, `end`, `text_sha256` (hash of the run or marker text, so a change to the source makes the record stale), `disposition`, `child_ids`, `note`. Run dispositions: `connective`, `rationale_only`, `restates_child`, `heading_or_label`, `not_an_obligation`. Marker dispositions: `intra_cell_reference`, `not_a_cross_reference`. `child_ids` is required for `restates_child` and must name active children of the same parent. A merged instruction gives no shortcut: each covered parent accounts for the shared text on its own.

### 5.9 Ambiguities (`authored/ambiguities/<SHARD>.jsonl`)

One record per workbook ambiguity or apparent error: `ambiguity_id`, `topic_code`, `affected_parent_ids`, `description`, `decision_owner` (`fund`, `analyst`, `program_owner`), `blocks_definitive_answer` (boolean), `status`. Topic codes are a pinned enumeration: `CF_TAX_CONDITIONAL`, `FCF_CONVERSION_DIRECTION`, `ZERO_EBITDA`, `ST_FINANCING_LABELS`, `ASSETS_ROW19_NEGATION`, `JURISDICTIONAL_TAX`, `DA_CAPEX_HEURISTIC`, `OTHER`. Source instructions are never silently amended. Children affected by an ambiguity link to it.

### 5.10 Omission audit (`authored/omission_audit/<SHARD>.jsonl`)

Written from a second, independent pass that did not author the children (in two steps, blind and compare, described in section 8; the records below hold the result of the last comparison): per parent, `verdict` (`complete` or `gaps`), `missing_obligations` (each a proposed child skeleton in analyst wording, or a reference to existing child ids that should widen), `notes`. Findings turn into new children or dispositions, then the audit is re-run.

### 5.11 Role aliases (`authored/role_aliases/CDD.jsonl`)

The backlog asks GL-02 to map the 21 commercial identifiers and the specialist aliases to registered roles. One record per template role name used by the 21 questions: `template_role`, `proposed_registered_types` (one or more of the eight), `rationale`, `decision_owner` (`GL-12` or `program_owner`), `status` (`proposed`). The template and the registry are not edited.

### 5.12 Baseline expectations (`authored/baseline/XS.jsonl`, coordinator-owned)

Facts about the source that the coordinator establishes by reading the workbook locally and that authors cannot be trusted to find: the expected merged instructions (by coordinate), the declared cross-row instruction sources and the parents they must reach, and the parents that must carry each named clarification. Each record has `expectation_id`, `kind`, `source_coordinate`, `target_coordinates` (or `all_rows_of_sheet`) and a note without wording. The validator checks the register against them. Public code carries no real coordinate; fixtures use fictional ones.

### 5.13 Views (`authored/views/XS.jsonl`)

A view names a subset of parents: `view_id`, `parent_ids`, `closure` (`none` or `policy_and_cross_reference`), `status` (`proposed`, `agreed`), `agreed_by`. The two-question diagnostic subset is the view `pilot_two_question` with primary parents `FDD-PL-023` (the EBITDA ratio row; the master plan names the topic as long-term EBITDA margin realism) and `CDD-MARKET_SIZE` (addressable market size and support), status `proposed`, `agreed_by` null. Its closure is computed from the policy bindings and cross-reference instructions of the primary parents' children. `validate --view` limits the per-parent rules to the view and its closure. The subset is a diagnostic. It is not the delivery scope.

### 5.14 Ledger and seals (`ledger/`)

`ledger/issued_ids.jsonl` is append-only, one canonical JSON object per line, sorted by child id within a seal: `child_id`, `parent_id`, `public_coordinate` and `first_seal` (the sequence number of the seal at which the id first appeared; retired children keep their ids and stay ledgered). `ledger/seals.jsonl` holds one record per seal: `seq` (1, 2, 3 without a gap), `tree_hash`, `created_utc` (supplied by the command line, never read by the library), `scope` (`full` or a sorted list of shard codes), `counts` (parents, children, active and retired children, new ids, ambiguities, cross-row instructions, dispositions), `snapshot` (the directory name), `workbook_sha256` and `register_complete` (the validation result from before the seal line was written). The tree hash is SHA-256 over the lines `<relative posix path> NUL <sha256 of the file> LF` for every file under `derived/` and `authored/` in sorted path order; nothing under `ledger/`, `manifest/`, `authoring/`, the review pack or a snapshot enters it. `seal` runs the same validation as `validate` and refuses (exit 1, nothing written) on any error finding: a full seal also refuses on any pending cross-shard item and on any skipped check other than the absent ledger, so it needs the workbook, the policy file, the baseline and the views; a shard seal (`--shard`, repeatable) ignores only errors whose every owner is outside the selection, so an error that names no shard blocks, and every ledger error blocks every seal. A seal of an unchanged tree prints `UNCHANGED seq=N` and writes nothing. Otherwise it appends the not-yet-ledgered ids of the parents in scope, copies `derived/`, `authored/` and `ledger/` into a snapshot directory `<snapshot-root>/<UTC time>-<first 12 characters of the tree hash>/` (default snapshot root: a sibling of the private root, never inside it) and appends the seal record; ledger files are written to temporary files and replaced, and a seal never writes over an invalid ledger. Rule I-16 fails when a ledgered id is missing (`GL02_LEDGER_ID_MISSING`), now under another parent (`GL02_LEDGER_ID_MOVED`), or when the ledger files do not parse, have a gap in `seq`, hold a duplicate or an unsorted id, or disagree with each other (`GL02_LEDGER_FILE_INVALID`, `GL02_LEDGER_INTEGRITY`); a register that changed after its last seal is reported as the informational `GL02_LEDGER_TREE_CHANGED`. When `ledger/` does not exist the check is skipped with `SKIPPED_LEDGER_ABSENT`, a required check, so `register_complete` stays false until the first seal. The register version that downstream consumers bind to is the sealed tree hash (`sealed_version`).

## 6. Accounting: segments, runs and markers

The mechanism that makes "no instruction lost" testable without reading fund text. All constants are pinned in code and in tests.

Fields. Every parent has four text fields. `question` is the instruction text. `line_item`, `term` and `nature` are labels. All four are accounted. Labels follow a lighter rule (below).

Segments. A field's text is split into segments: first at every line break (`\r\n` as one terminator, a lone `\r`, or `\n`; the terminator stays with the left piece), then after every `.`, `?`, `!` or `;` that is followed, after zero or more closing characters (`)`, `]`, `"`, `'`, U+2019, U+201D), by whitespace (but not after the pinned abbreviations `i.e.`, `e.g.`, `vs.`, `approx.`, `incl.`, `excl.` and `no.`, matched case-insensitively), then, inside the outermost balanced parentheses of a segment that holds at least three top-level comma-separated items, one segment per item. Segments tile the text exactly. Each is trimmed of leading and trailing whitespace for counting (offsets are kept in the original text).

Tokens. A content token is a casefolded match of `[^\W_]+` of length at least 2 that is not in `STOPWORDS` (a frozen list of at most 120 English function words, pinned by hash, containing none of the qualifier tokens). `QUALIFIER_TOKENS` is a pinned set of words that change what an instruction demands: negations (`not`, `no`, `never`, `without`), quantifiers (`any`, `all`, `each`, `every`, `only`, `least`, `most`, `more`, `less`), conditions (`if`, `unless`, `except`, `where`, `when`, `whether`), scope (`other`, `others`, `etc`, `both`, `excluding`, `including`, `excl`, `incl`, `net`, `gross`), periodicity (`monthly`, `quarterly`, `annual`, `annually`, `yearly`, `per`), and any token made of digits. A segment or run containing a qualifier token or a digit is material at any length. `DIRECTIVE_TOKENS` is a second pinned set of words that make a text an instruction: `check`, `confirm`, `ensure`, `make`, `sure`, `understand`, `assess`, `verify`, `reconcile`, `identify`, `compare`, `review`, `test`, `look`, `read`, `ask`, `request`, `find`, `consider`, `evaluate`, `determine`, `analyse`, `analyze`, `challenge`, `validate`, `obtain`, `align`, `should`, `must`, `need`, `needs`, `required`, `requires`, `important`, `key`, `careful`. None is a stopword.

Materiality. For the `question` field a segment or run is material when it ends with a question mark, contains a directive token, a qualifier token or a digit, or has at least `MATERIAL_RUN_MIN_CONTENT_TOKENS` (4) content tokens. For a label field it is material when it ends with a question mark, contains a directive token, a qualifier token or a digit, or has at least 5 content tokens. A short question or a short instruction is therefore never skipped for being short.

Quote resolution. A child's quotes are resolved to `[start, end)` offsets in the cited parent's cited field. The k-th match means the k-th non-overlapping exact match from the left.

Runs. For each field, take the union of the resolved quotes of all active children of any parent that cite this parent and field. The complement within the text, intersected with each segment and trimmed, gives the uncovered runs. Each run records `start`, `end`, `text_sha256` and its content-token and qualifier counts.

Rule I-06 (wording accounting). Every material uncovered run needs a disposition of kind `run` that matches it exactly (parent, field, start, end, text hash). A disposition that matches no current run is stale and fails. Smaller runs are counted, not required.

Rule I-06b (focus). A child covers a segment when its quotes cover at least half of the segment's content tokens. A material segment that no child covers is reported by rule I-06 (its run needs a disposition), not by this rule; the focus rule applies to covered segments. A child's reach is the number of material segments it covers in one text field of one parent (the quotes of each field are counted separately). Every material segment must be covered by at least one child whose reach is at most two, or all children that cover it must carry a `joint_answer_reason`. One child quoting a whole cell therefore cannot be the only cover of a list or of several sentences. Independently of the focus of any segment, a child whose reach in one field exceeds two must carry a non-empty `joint_answer_reason`, whether or not another child also covers those segments (rule I-08, code `GL02_JOINT_REASON_MISSING`).

Markers. A reference marker is a word or phrase that makes a text depend on other text: `above`, `below`, `same as`, `as per`, `see`, `refer`, `previous`, `following`, `valid for`. A scope-pointer marker names another place or a reconciliation: the sheet names (`p&l`, `profit and loss`, `cash flow`, `balance sheet`, `liabilities`, `assets`), `business plan`, `bp`, `reconcil` (prefix), `consistent with`, `tie`. Matching is case-insensitive on word boundaries. The lists are starting sets: the first run on the real workbook counts the occurrences, and the lists and their pinned hash are adjusted if the noise is out of proportion. Every occurrence in any field must be accounted: a reference marker must lie inside a quote of an active `cross_reference_applied` child, a scope-pointer marker must lie inside a quote of any active child, or the occurrence has a `marker` disposition.

Warnings and metrics (not errors, in the manifest and read first by the reviewer): per parent, the covered share of characters, the longest single quote as a share of the text, children per hundred words, dispositions by kind. `GL02_QUOTE_TOO_BROAD` when one quote spans more than half of a text that has three or more segments. `GL02_DISPOSITION_HEAVY` when dispositions of kind `not_an_obligation` or `rationale_only` cover more than a third of a field's characters (a disposition that matches no current run or marker is stale and is not counted). Two dispositions with the same parent, field, kind, start and end are an error (`GL02_DISPOSITION_DUPLICATE`).

## 7. Invariants the validator enforces

Each rule has a stable error code (`GL02_...`), reports counts and identifiers only, and has a passing test and a mutation test that breaks a register copy and shows the rule fails with the right code. A skipped check is reported as skipped and never counts as a pass. `register_complete` is false while any required check is skipped; rule I-16 (the ledger) is required, so it is false until a ledger exists, that is until the first seal.

| Id | Rule |
|---|---|
| I-01 | Source coverage: the extracted workbook has exactly the four sheets, 79 data rows (29, 13, 21, 16), 33 merged ranges (11, 5, 10, 7), zero formula cells, and the workbook hash and byte count equal the pinned values. The census of stored cells has no cell with role `other`. Every feature count from section 5.2 is zero or explicitly accepted. |
| I-02 | Importer parity: for each of the 79 rows, coordinates, row ordinal, anchor and `methodology_question_id` equal the importer projection, and the stripped raw text equals the projection text. The only permitted differences are edge whitespace. The list of cells with edge whitespace is reported. |
| I-03 | Parent coverage: exactly 79 financial and 21 commercial parents with unique identifiers. The commercial identifiers equal the template and the category map, in order. |
| I-04 | Every parent has at least one active child. |
| I-05 | Raw-text integrity: stored raw text equals the source cell; hashes recompute; normalized text recomputes. |
| I-06 | Wording accounting (section 6). |
| I-06b | Focus (section 6). |
| I-07 | Shared instruction and cross-reference propagation: every covered parent of a merged instruction has at least one active child linked to it and accounts for the shared text itself. Every target of a declared cross-row instruction has at least one active `cross_reference_applied` child whose quote cites the source parent's text. The marker census of section 6 holds. |
| I-08 | Field completeness and vocabulary: required fields present after profile resolution, vocabularies valid, derivation rules from section 5.7 hold, quotes resolve exactly (a quote that matches only after folding whitespace or typography is `GL02_QUOTE_FOLD_MATCH_ONLY`), `analyst_inference` and `plan_minimum` carry rationales, specialists are registered types, document categories valid for the parent kind. |
| I-09 | Plan-minimum coverage: every code in the list below is carried by at least one active child on the right sheet. |
| I-10 | Policy coverage (when the policy file is present, otherwise skipped and not green): every one of the ten anchors and every source binding is carried by at least one active child of the cited parent through a policy `calculation_rules` entry. In a validation limited to shards or a view only the parents in scope are checked and the uncarried items of the others are informational `PENDING_CROSS_SHARD`, and the invariant list of such a run shows I-10, I-17 and I-19 as skipped (pending) while a pending item of that rule exists; a policy path given for an extraction made without one is the error `GL02_POLICY_GIVEN_NOT_EXTRACTED`. |
| I-11 | Named clarifications: an ambiguity record exists for each topic code of section 5.9 other than `OTHER`. A record may state that the text is clear, but may not be absent. |
| I-12 | Scope retention: Liabilities rows 17 to 24 each keep an active child whose applicability is not `not_applicable_by_default`, even though the debt-policy tie-out excludes them. |
| I-13 | Commercial alignment: every commercial child's destination equals the template report mapping or carries a deviation reason, specialists are registered types, document categories are drawn from the governed category map for that question or the evidence is marked external, every master-plan column (required evidence, calculation or judgment, specialist and destination, acceptance) is carried by at least one child per question, and every template role name used by the 21 questions has a role alias record with registered targets. |
| I-14 | Determinism and idempotence: extracting twice yields byte-identical files; the manifest is byte-identical for identical inputs. |
| I-15 | Public manifest privacy: keys on the allowlist, values in the exact format for their key, no per-text hash, no free text. |
| I-16 | Ledger integrity: every ledgered child id still exists, under the same parent; retired children remain present; the ledger files are well formed and consistent (section 5.14). |
| I-17 | Baseline expectations: the register satisfies every record in `authored/baseline/XS.jsonl`. |
| I-18 | Views: every view's parents exist; a validation limited to a view uses its closure. |
| I-19 | Reference resolution: every child id (`GL02-<coordinate>-Cnnn`) and every public coordinate cited in any authored text field must resolve to an existing child or parent. A token that resolves to a retired child is the warning `GL02_REFERENCE_RETIRED`; one that resolves to nothing is the error `GL02_REFERENCE_UNRESOLVED` (ids: the referencing record and the token, never text). In a validation limited to shards or a view a target outside the scope is informational `PENDING_CROSS_SHARD`. A token followed by a letter or digit is not a token, and a coordinate inside a child id is not a second token. Uppercase words that look like a commercial coordinate in prose are a known limitation. |

Plan-minimum codes (from master plan section 3, split to its own enumeration; each must be carried by at least one active child):

- P&L: `PL_HIST_REV_COST_EVIDENCE`, `PL_FCST_REV_COST_EVIDENCE`, `PL_MARGIN`, `PL_EBITDA`, `PL_EBIT`, `PL_TAX`, `PL_NET_INCOME`, `PL_FCST_ASSUMPTIONS`, `PL_BP_RECONCILIATION`, `PL_CASH_RECONCILIATION`.
- Cash Flow: `CF_CASH_CONVERSION`, `CF_WORKING_CAPITAL`, `CF_TAXES`, `CF_CAPEX`, `CF_FINANCING`, `CF_OPENING_CLOSING_CASH`, `CF_BANK_RECONCILIATION`.
- Liabilities: `LIAB_EQUITY_INSTRUMENTS`, `LIAB_DEBT`, `LIAB_ST_FINANCING`, `LIAB_ACCRUALS`, `LIAB_DEFERRED_REVENUE`, `LIAB_CONTINGENT`, `LIAB_OFF_BALANCE`.
- Assets: `ASSET_CASH`, `ASSET_RECEIVABLES`, `ASSET_PREPAYMENTS`, `ASSET_OTHER_ASSETS`, `ASSET_RECOVERABILITY`, `ASSET_SCHEDULES`, `ASSET_BS_RECONCILIATION`.

Derived-rule codes (`rule_code` for `derived_gl08`; coverage is reported, not required): `RETAINED_EARNINGS`, `DSO`, `DPO`, `CASH_CONVERSION`, `BANK_SCHEDULE`, `ACCRUAL_SCHEDULE`, `PREPAYMENT_SCHEDULE`, `DEFERRED_REVENUE_SCHEDULE`, `LEASE_SCHEDULE`, `BENEFIT_SCHEDULE`, `CONVERSION_SCHEDULE`, `UNIT_ECONOMICS`, `FORECAST_SENSITIVITY`, and for the commercial register `MARKET_SIZE_RECONCILIATION`, `CAGR`, `CONCENTRATION_TOP_N`, `RETENTION_COHORT`, `CAC_PAYBACK`, `REVENUE_BRIDGE` (19 codes). GL-08 owns the definitions of all of them.

The pinned values for I-01 are the workbook SHA-256 `a3b83addfff02815c9fd44c4a3dcb7f56ce39410fe182faff12acfb845bd4352`, byte count 24,905, and the row and merge counts above.

## 8. How the register is authored

Persona for authoring: a senior buy-side financial due-diligence analyst (transaction services background) who is also a requirements engineer writing a specification for an automated diligence system. For commercial parents the persona is a commercial and market due-diligence lead.

Per parent, in order:

1. Read the raw wording of the term, nature, line item and question, plus any merged instruction that reaches the row and any cross-row instruction that applies to it.
2. List what a competent analyst would have to establish, compute or judge to answer it. Quote the source for each. Where the text enumerates items, make each item its own child unless two items cannot be answered separately (then say why in `joint_answer_reason`).
3. Apply cross-row and cross-sheet instructions to this row as `cross_reference_applied` children and declare the instruction in the shard of the target parents.
4. Add children for the master plan's plan-minimum obligations that the text does not state, and tag every child that carries one with its codes.
5. Add `formula_policy` content so that every anchor and source binding listed for the row in the policy view is carried by a `calculation_rules` entry.
6. Add `analyst_inference` children only for something an adequate answer cannot do without. State why. These go to fund review first.
7. Fill the profile defaults once per parent, then only the fields that differ per child.
8. Log ambiguities. Never resolve them by editing the instruction.
9. Run the validator for the shard, then disposition every material run and marker occurrence honestly. A disposition is not a shortcut: a run that states something the analysis must show gets a child.

Authors build their records with a Python script that calls the package's JSONL writer, not by hand-writing JSON. The script is kept under the private root's `authoring/` folder.

Shards and ownership. One shard per financial sheet and one for the commercial parents, each in its own file set, so writers never share a file. Each author may read every parent's text for context and writes only the records of the parents in their shard. A shard-scoped validation checks the shard-local rules and reports requirements that depend on another shard as `PENDING_CROSS_SHARD` (informational). The full validation across all shards resolves them. The coordinator owns the `XS` shard.

Writer cap. The program allows at most two isolated writers at a time plus coordination and review, one writer per worktree. Authoring runs in waves of two. The harness code packets count as writers.

Reviews. Each shard is reviewed by an independent actual Claude Opus 5.5 session that reads the private files locally and returns a public summary and a private findings list. A separate omission-audit pass (section 5.10) tries to find a missing obligation for every parent without having authored it, in two steps by one independent auditor session per shard: step 1 (blind) reads only the derived files and the coordinator's baseline and writes its own list of the obligations a complete answer needs, with the basis of each (text, instruction, plan, policy or inference); step 2 (compare) reads the authored files and marks each expected obligation covered, partly covered or missing, proposes the missing children and names surplus children; the comparison is repeated after each revision against the same list. A shard review returns PASS when no finding of severity high or medium remains; its low findings and the audit's leftovers are taken in one last revision before the shard is considered final. The review and the audit findings are merged into the revision brief of the next round. The register then goes to owner and fund review as a private review pack generated from the register. Sending anything to the fund is an owner action and is outside this work.

## 9. Commercial register

The 21 parents are the template questions. Children come from the template fields, the category map and the master-plan commercial table (required evidence and calculation or judgment, specialist and destination, acceptance beyond reference existence), plus analyst inference where an adequate answer needs more. Template role names are kept as `template_role` and mapped to registered types in the role alias file. Fifteen of the template's 21 role assignments use six names that are absent from or mismatched with the registry; the register records the mismatch and proposes the mapping, and GL-12 decides. The commercial register contains no workbook text. Whether it may be published is an owner decision (section 13). Until the owner decides, it follows the private rules.

## 10. Verification

- Unit tests with fictional fixtures, run in the hosted offline suite: extraction (merge ancestry, edge whitespace preserved, newlines preserved, feature census, cells outside the rectangle), parent construction and importer parity, quote resolution, segments, runs, qualifier tokens, focus, marker census, every invariant I-01 to I-19 with a passing case and at least one mutated failing case, manifest determinism and privacy scan, ledger and seal, views, CLI behavior.
- Local real-register tests that skip with a stated reason when the private files are absent: the same invariants against the real workbook and real register.
- Repository scans that already exist and could be tripped by new source files stay green (listed in the packet brief).
- Mutation checks by the coordinator on a copy of the real register: delete a child, a quote, a link, a disposition, a policy carry and a ledgered id, and show the validator fails with the right code.
- The leak scan over the public tree, status records, prompts and commit text before every publication.
- Independent review: actual Opus 5.5 on the code, then on each register shard and on the full register.

## 11. Packets and order

| Packet | Content | Visibility | Depends on |
|---|---|---|---|
| A and B | Models, vocabularies, deterministic IO, workbook extractor, parent builders with importer parity, segments, runs, markers, quote helper, policy view, validator (I-01 to I-15, I-17 and I-18 hooks), manifest with the public-safe scan, CLI (`extract`, `validate`, `runs`, `quote`, `manifest`), tests. One worktree and one reviewed candidate. | Public | none |
| R | `seal` and the ledger (I-16), the views listing, the private review pack (markdown and one workbook per shard, formula-safe cells), the audit-input exporter (blind and compare stages), reference resolution (I-19), shard-limited policy coverage, the status additions and tests. Delivered with checkpoint 36 after one correction round. The review pack writes workbook cells with control characters escaped and every cell capped at 32,767 characters, and the blind audit-input stage carries no authored cross-row note or quote. | Public | A and B |
| C1 to C5 | Register shards for P&L, Cash Flow, Liabilities, Assets and the 21 commercial parents (with the role aliases). Waves of two writers. P&L first, because it holds the diagnostic subset. | Private | A and B (the validator semantics must be final) |
| XS | Baseline expectations and views, written by the coordinator after reading the workbook locally. | Private | A and B |
| D | Cross-shard validation, omission audit, ambiguity review, full validation, seal, public manifest, private review pack. | Private plus public manifest | C1 to C5, R |
| E | Documentation front, runbook, backlog status, documentation review. | Public | D |

## 12. What is left to the builder

- The module split inside `src/idis/methodology/obligations/`, the exact pydantic models, the stopword list, the evidence-type vocabulary, error-code text, the CLI flags, test layout.
- How to read a merged range robustly with the existing library version, and how to guarantee byte-identical output across operating systems.
- How to keep every new source file clear of the repository scans listed in the packet brief.

## 13. Decisions

Calls made by the coordinator (mine; the owner can overrule):

1. Private root under `.local_reports/gl02-private/`, so no `.gitignore` or contract-lock change.
2. Models and any JSON schema live in `src/idis/methodology/obligations/`; nothing under `schemas/` before contract review.
3. Identifier scheme in section 5.1.
4. The accounting mechanism of section 6 (segments, runs, focus, markers) as the mechanical proof of no wording loss, with its thresholds.
5. Derivation classes and the rule that analyst additions are labelled and prioritized for fund review.
6. The two-question diagnostic subset is a view; its primary parents are proposed in section 5.13.
7. The public coverage manifest is committed under `docs/go-live/gl02/` with counts, coordinates and file-level hashes only.
8. The commercial register follows the private rules until the owner decides otherwise.
9. Capability starts as `unassessed` except for policy-bound calculation children, because a truthful capability assessment needs the code read against each child, which is a separate pass.
10. Financial output destinations default to the importer's mapping and stay `provisional` until the fund template is confirmed.
11. Private copies in worktrees are removed after a verified sync back; the register is never copied off this machine without a decision.
12. Authoring runs in waves of two writers, under the program's writer cap.

Decisions that belong to the owner or the fund (the existing authorization does not cover them):

1. Fund and owner review of the register content. Acceptance of GL-02 as fund-complete needs it. Nothing here can substitute for it.
2. Disclosure to confirm: the workbook text is processed by the model providers behind the agents (Cursor with xAI Grok, and Anthropic). The owner's instruction and the standing routing ruling name these agents and no instruction restricts third-party model processing, so authoring proceeds. If the fund's confidentiality terms forbid it, the owner says so and authoring stops.
3. Whether the commercial register, which contains no workbook wording, may be published.
4. Who resolves each logged workbook ambiguity (decision owner per record).
5. Whether the program wants the manifest schema under `schemas/` after contract review.
6. Whether a backup of the private register outside this machine is wanted. None is made.
7. Agreement of the diagnostic subset (`agreed_by`).

## 14. Observations to report

- The master plan says seven edge-whitespace differences were observed. The coordinator's first local read found eleven cells with leading or trailing whitespace (six question cells, three line-item cells, two nature cells, by role). The extractor reports the exact list as an I-02 output, and the master plan wording should be corrected from that output.
- The importer derives question identifiers from whitespace-normalized text, so edge whitespace does not affect identifiers. Raw text must nonetheless be kept.
- The workbook has no comments, hyperlinks, hidden rows or columns, validations, conditional formats, images or defined names, every value is a string, and the question text is plain ASCII plus nine line breaks.
- The first local read found 27 occurrences of the simplest reference words in 17 rows. The broader marker lists of section 6 will find more; the first run calibrates them.

## 15. Acceptance of GL-02

GL-02 is accepted as register-complete when the validator is green with no skipped required check on the full register for all 100 parents, every shard and the full register have an independent Opus review with no open finding, the omission audit has no unresolved gap, every ambiguity has a decision owner, the register is sealed, and the public manifest and documentation are published and reviewed. GL-02 is accepted as fund-complete only after the fund review in section 13. Until then every child is `proposed`, and the program reports the register as complete pending fund review, not as fund-approved.

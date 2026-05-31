# Slice77 — Durable Data-Room Package And File Ledger — Implementation Plan

> **For Claude / agentic workers:** REQUIRED SUB-SKILL: use `superpowers:executing-plans` (or `superpowers:subagent-driven-development`) to implement task-by-task. Per task: `superpowers:test-driven-development` (RED → verify red → minimal GREEN → verify → refactor), `superpowers:verification-before-completion` before any status claim, `superpowers:using-git-worktrees` already done, `superpowers:finishing-a-development-branch` before commit/PR. Apply Postgres/RLS judgment for the new tenant-scoped ledger. Use `code-reviewer` + validation-review before any PR. **Reuse before create.**

**Goal:** Represent a complete data room as a **durable, tenant-scoped package with a per-file ledger** (package + file + artifact-link rows), created via the public API from supported generated fixtures, with deterministic per-file parser-triage reason codes — without leaking raw folder paths, filenames, or content.

**Architecture:** Add a durable tenant-scoped `data_room_packages` + `data_room_package_files` table pair (RLS-isolated, idempotent migration `0020`) with a paired Postgres + in-memory repository (factory parity, exactly like `run_steps`). Group existing uploads/ingestion under a `package_id` and persist per-file triage from the existing `parser_capabilities.triage_document` path using the **existing** reason-code vocabulary. Expose a small public package API and lock it in OpenAPI. Reuse `IngestionService`, the object store, and the `_safe_summary` aggregate pattern; the private `real_example` acceptance reuses the existing safe-aggregate gate. No run execution, no providers, no real content.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, SQLAlchemy + Alembic (Postgres + pgvector), pytest, ruff, mypy, filesystem object store, GitHub CI (`postgres-integration` for DB/RLS runtime proof).

**Base:** branch `slice77-durable-data-room-package-file-ledger` @ `8b95fa462f0a4418a2a019ebfcbc9728ab10bbb7` (merged `origin/main`, Slice76), worktree `C:/Projects/IDIS/IDIS-slice77` (clean; baseline green via Slice76 CI).

---

## 1. Skills / plugins / functions used
- **superpowers:using-git-worktrees** — created/validated worktree (done; HEAD `8b95fa4`, clean).
- **superpowers:writing-plans** — this document.
- **superpowers:test-driven-development** — RED test design only here (no implementation).
- **superpowers:verification-before-completion** — gates all completion claims during execution.
- **Explore subagents (read-only)** — mapped existing surfaces (this discovery).
- Postgres/RLS guidance — applied to the new ledger table design (no dedicated Supabase skill is installed; the stack is plain Postgres/pgvector, so RLS/migration judgment is applied directly).
- Repo tooling at execution time: `pytest`, `ruff format --check`/`ruff check`, `mypy --no-incremental`, `scripts/forbidden_scan.py`, the JSON/YAML contract parse, `git diff --check`.

## 2. Verified facts from discovery (file:line, at `8b95fa4`)
**Models / documents**
- `Document` (`src/idis/models/document.py:46-85`): `document_id` (parsed), `doc_id` → `DocumentArtifact`, `doc_type`, `parse_status` enum (`PENDING|PARSED|FAILED`, `:35-43`), `metadata`.
- `DocumentArtifact` (`src/idis/models/document_artifact.py:31-81`): `doc_id` (uploaded), `sha256`, `uri`, `metadata`.
- `DocumentSpan` (`src/idis/models/document_span.py:66-109`): reuse as-is (spans are not package-scoped).
- Existing per-file durable precedent: `RunScopedDataRoomIngestionHandoffFileResult` (`src/idis/models/data_room_ingestion_handoff.py:46-95`) — fields `inventory_file_id, relative_path, path_hash, sha256, file_status, handoff_status, reason_codes (sorted/unique), durable_artifact_id, durable_document_id, storage_uri, parse_status, error_codes`, plus `to_safe_dict()`. **This is the field shape to mirror in the durable file ledger.**

**Parser triage + reason codes (REUSE, do not reinvent)**
- Triage entry point: `parser_capabilities.triage_document(...)` → `capability_for_document(...)` (`src/idis/services/documents/parser_capabilities.py:169-282`, `:105-166`) returns `ParserCapability` (`src/idis/models/document_classification.py:107-139`) with `support_status`, `triage_status`, `reason_codes: list[str]`, `requires_ocr`, `requires_conversion`.
- Existing reason-code vocabulary: static (`file_too_large`, `unsupported_format`, `unknown_format`, `conversion_required`, `ocr_required`, `encrypted_pdf`, `corrupted_file`, `no_text_extracted`) + OCR/media maps (`parser_capabilities.py:84-102`) + `ParseErrorCode` (`src/idis/parsers/base.py:17-39`).
- Cross-domain reason enums to stay consistent with: `DocumentPreflightReason` (`src/idis/models/document_preflight.py:28-43`), `DataRoomIngestionHandoffReason` (`src/idis/models/data_room_ingestion_handoff.py:27-37`), `ExtractionTaskBlockerReason` (`src/idis/models/extraction_task.py:31-44`).
- Ingestion already persists triage into `Document.metadata` (`src/idis/services/ingestion/service.py:1116-1148`: `parser_support_status`, `parser_triage_status`, `parser_reason_codes`, `parser_requires_ocr/conversion`). **Slice77 reads these / re-triages and persists them durably on the file-ledger row.**

**Ingestion + object store (REUSE unchanged)**
- `IngestionService.ingest_bytes` (`src/idis/services/ingestion/service.py:715-938`), `get_artifact/get_document/get_spans` (`:1447-1484`).
- Object store: `FilesystemObjectStore` (`src/idis/storage/filesystem_store.py:113-521`) wrapped by `ComplianceEnforcedStore` (`src/idis/storage/compliant_store.py:39-150`); env `IDIS_OBJECT_STORE_BACKEND`/`IDIS_OBJECT_STORE_BASE_DIR` (`src/idis/services/ingestion/defaults.py`). Storage key today: `deals/{deal_id}/artifacts/{sha256}/{filename}` (`service.py:1008`). No explicit bootstrap; dirs created on first write.

**Migration / RLS / repo template (COPY)**
- Next migration: **`0020`** (single head after `0019`).
- RLS runtime: `set_tenant_local(conn, tenant_id)` (`src/idis/persistence/db.py:215-237`), called in Postgres repo `__init__` (`repositories/runs.py:43`).
- RLS policy precedent (per-table, idempotent): `0010_run_steps_evidence_items.py:26-71` and `0009_...py:50-67` — `ENABLE ROW LEVEL SECURITY` → `DROP POLICY IF EXISTS` → `CREATE POLICY ... USING (NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid)`.
- Repo precedent: `run_steps.py` (Postgres `:144-337`, in-memory `:47-141`, factory `get_*_repository` `:344-361`, JSONB `CAST(:x AS JSONB)` + `json.dumps`/`json.loads`, module-global store + `clear_*_store()`). Model precedent `run_step.py:212-241`.

**Private real_example safe-aggregate gate (REUSE)**
- `scripts/run_real_example_gate.py` → `src/idis/evaluation/real_example_gate.py` (`run_real_example_gate` `:83`, `GateMode` `:64`, `_safe_summary` `:771`; gate ledger `real_example_gate_ledger.py`). `_safe_summary()` emits ONLY: `safe_summary:true`, `total_files`, `processed_files`, `ledger_entry_count`, `counts_by_extension`, `counts_by_status`, `counts_by_parser_outcome`, `counts_by_reason_code` (no paths, filenames, content). Run-scoped inventory service precedent: `RunDataRoomInventoryPackageService` / `InMemoryRunDataRoomInventoryPackageService` exercised by `tests/test_run_data_room_inventory_package_service.py` (with `_write_fixture_tree()`, `create_test_xlsx()`, and leak assertions `"ARR was $5M"/"text_excerpt"/"file_contents" not in str(summary)`).

**OpenAPI + fixtures**
- Spec `openapi/IDIS_OpenAPI_v6_3.yaml`; contract-lock tests `tests/test_openapi_loader.py`, `tests/test_api_openapi_validation.py`, `tests/test_run_step_schema_contract.py`.
- Synthetic fixtures: `_write_fixture_tree()` + `create_test_xlsx()` (`tests/test_run_data_room_inventory_package_service.py`), GDBS-mini (`tests/fixtures/gdbs_mini/manifest.json`), `tests/fixtures/synthetic/claims_fixture.py`, `build_bounded_synthetic_api_upload_rehearsal(...)`.

## 3. Plan answers to the required design questions

### 3.1 Proposed data model & migrations
Two new durable tenant-scoped tables (migration `0020_data_room_packages_and_files.py`, additive/idempotent/downgrade-safe, copying `0010` + `0009` style):

- `data_room_packages` — package ledger header (**keyed by `package_id` only; no user-supplied `name`** — R7):
  `package_id UUID PK · tenant_id UUID NOT NULL · deal_id UUID NOT NULL REFERENCES deals(deal_id) · status VARCHAR(20) CHECK in ('OPEN','SEALED') · created_by_actor_id VARCHAR(255) · created_by_actor_type VARCHAR(20) CHECK NULL OR in ('HUMAN','SERVICE') · manifest_uri TEXT (internal) · metadata JSONB NOT NULL DEFAULT '{}' · created_at/updated_at TIMESTAMPTZ` · indexes on `(tenant_id)`, `(tenant_id, deal_id)` · RLS policy. **No package-level UNIQUE constraint**; create de-dup is via the `Idempotency-Key` middleware. (Reuse the Slice76 originating-actor columns/pattern for `created_by_actor_*`.)
- `data_room_package_files` — per-file ledger (**path stored as `path_hash` + safe `extension` only — no raw path/filename ever**, R3):
  `file_entry_id UUID PK · tenant_id UUID NOT NULL · package_id UUID NOT NULL REFERENCES data_room_packages(package_id) ON DELETE CASCADE · deal_id UUID NOT NULL · sequence INTEGER NOT NULL · path_hash VARCHAR(64) NOT NULL · extension VARCHAR(16) · sha256 VARCHAR(64) · file_status VARCHAR(20) (supported|deferred|blocked) · support_status VARCHAR · triage_status VARCHAR · parse_status VARCHAR(20) · reason_codes JSONB NOT NULL DEFAULT '[]' · error_codes JSONB NOT NULL DEFAULT '[]' · doc_id UUID NULL · document_id UUID NULL · storage_uri TEXT NULL (internal) · created_at TIMESTAMPTZ` · `UNIQUE(tenant_id, package_id, path_hash)` · indexes on `(tenant_id, package_id)` · RLS policy.

### 3.2 Tenant / RLS strategy
Both tables get `ENABLE ROW LEVEL SECURITY` + a single `tenant_isolation_<table>` policy using the canonical `NULLIF(current_setting('idis.tenant_id', true), '')::uuid` pattern (fail-closed on unset tenant). Repos call `set_tenant_local()` in `__init__`; SELECT/INSERT bind `:tenant_id` and rely on RLS (no tenant predicate omission). In-memory repos filter every read by `tenant_id`. No cross-table tenant leakage; FK to `deals` stays within tenant via RLS on `deals`.

### 3.3 Package / file / artifact ledger boundaries
- **Package** = durable tenant+deal-scoped grouping (header row). **File ledger** = per-file triage/state rows (the "ledger"). **Artifact link** = optional `doc_id`/`document_id` FKs populated when a file is ingested via the existing `IngestionService` (artifacts/spans remain owned by the existing document tables — Slice77 does **not** duplicate them). The package is the grouping + triage ledger over existing artifacts, not a new artifact store.
- **R1 (LOCKED):** this is a **new durable tenant/deal-scoped product package**, intentionally **distinct** from the existing *run-scoped* inventory service (`RunDataRoomInventoryPackageService`) and the handoff model. Slice77 **reuses field shapes** (from `RunScopedDataRoomIngestionHandoffFileResult`) but **does not refactor or replace** the run-scoped service.

### 3.4 Safe folder-path redaction (R3 LOCKED — redact only, no opt-in)
- **Redact, always.** Store `path_hash` (stable SHA-256 of the relative path) + the safe `extension` only. **Never** store raw folder paths or filenames — **no policy-controlled raw-path column and no opt-in retention in Slice77.** All summaries emit aggregates only (counts_by_*). Mirrors `RunScopedDataRoomIngestionHandoffFileResult.path_hash` + `to_safe_dict()`.

### 3.5 Batch / manifest grouping (bounded, CREATE-ONLY — R8)
- **The create endpoint's `document_ids` list IS the batch/manifest grouping surface for Slice77.** Body `{ document_ids: list[str] }` (no `name` — R7), validated exactly like `RunSource` (`models/run_source.py:28-44`) — reject anything path/URI-like. The package **groups existing already-ingested artifacts** and persists per-file triage; **no** base64/file batch-upload endpoint and **no** separate add-files endpoint in this slice (`addDataRoomPackageFiles` deferred — R8). Large-file upload stays the existing `documents/upload` + `ingest` path.
- **Generated fixtures are tests-only:** fixtures create documents first via existing upload/ingest helpers (`_write_fixture_tree`/`create_test_xlsx` → `IngestionService`), then create the package referencing those `document_ids`.
- **De-dup:** duplicate `document_ids` within a create collapse to one ledger row via `UNIQUE(tenant_id, package_id, path_hash)` (`ON CONFLICT DO NOTHING`); repeated create POSTs are de-duplicated by the existing `Idempotency-Key` middleware.

### 3.6 Parser-triage reason-code model
- Reuse `ParserCapability` (`support_status`, `triage_status`, `reason_codes`, `requires_ocr/conversion`) and the **existing** reason-code strings; persist `reason_codes`/`error_codes` (sorted, unique) per file-ledger row. No new reason vocabulary unless a genuinely new condition exists (then document why in the PR).
- **File-status rollup — intentionally STRICTER than the run-scoped inventory precedent (decision recorded here per the "document why" rule).** `_rollup_file_status` (`src/idis/services/data_room/package_service.py`) maps:
  - `supported` **only** when `support_status == SUPPORTED` **and** `triage_status == READY` **and** `parse_status == PARSED`;
  - `blocked` when support is `unsupported`/`encrypted`/`corrupted`/`too_large`, **or** triage is `unsupported_source`/`blocked`/`too_large`, **or** parse is `FAILED`;
  - `deferred` for everything else (partial / conversion-required / OCR-required / unknown).
  This diverges from `RunDataRoomInventoryPackageService` (`src/idis/services/runs/data_room_inventory_package.py`), which treats unsupported/too-large as *deferred* and partial-support as *supported*. The stricter mapping is deliberate for a **durable product package**: it never labels a partial/unsupported file "supported", and surfaces unsupported/too-large as **user-visible blockers** (aligned with the master-plan "user-visible blockers with reason codes"). Nothing is silently dropped: unknown/partial degrade to `deferred`, never `supported`. The Slice77 tests lock this mapping (`test_supported_deferred_blocked_documents_map_to_deterministic_status`); change it only if a RED test proves it violates Slice77 acceptance.

### 3.7 Object-store bootstrap (safe)
- Reuse `FilesystemObjectStore`/`ComplianceEnforcedStore` and `IDIS_OBJECT_STORE_BACKEND`/`_BASE_DIR`. "Bootstrap" = verify the store is **configured + writable within the configured base dir only** — **no probe or write outside the configured object store**, and **never** touch ambient/global paths (same rule as the Slice73 object-store probe).
- **The durable ledger lives in Postgres** (source of truth). An object-store package manifest is **optional and internal**; if written, it contains **safe-aggregate + safe-id content only** (no raw paths, filenames, object keys, content, DSNs, or env values). `manifest_uri`/`storage_uri` are **internal columns**, never returned in API/audit/logs (see §3.8/§3.11).
- Raw artifacts continue **only** through the existing `IngestionService` (`deals/{deal_id}/artifacts/{sha256}/{filename}`); object keys remain internal and are never exposed.

### 3.8 Public API surface & OpenAPI impact — DEAL-SCOPED (ABAC-safe), SAFE responses
All routes are **deal-scoped** so the existing RBAC/ABAC machinery resolves `deal_id` from the path and enforces deal assignment + masked 404 (no `packageId`-only existence oracle, no new resolver needed):
- `POST /v1/deals/{dealId}/data-room-packages` (op `createDataRoomPackage`) → 201 `DataRoomPackageRef`. Body `{ document_ids: list[str] }` (no `name` — R7) validated like `RunSource`; path/URI-like values → 400 `INVALID_REQUEST`.
- `GET /v1/deals/{dealId}/data-room-packages` (op `listDataRoomPackages`) → 200 list of refs.
- `GET /v1/deals/{dealId}/data-room-packages/{packageId}` (op `getDataRoomPackage`) → 200 `DataRoomPackageRecord`. The repo `get(package_id)` is scoped to `(tenant_id, deal_id)` and returns `None` for not-found / cross-deal / cross-tenant → identical masked 404 `DATA_ROOM_PACKAGE_NOT_FOUND` (mirrors `get_run` `routes/runs.py:231-256`, `get_document` `routes/documents.py:1045-1081`, ADR-011).

**Response schemas (SAFE — whitelist only):**
- `DataRoomPackageRef`: `{ package_id, deal_id, status, file_count, counts_by_status, counts_by_reason_code, created_at }`.
- `DataRoomPackageRecord`: the ref **plus** `files: [ { file_entry_id, path_hash, extension, file_status, support_status, triage_status, parse_status, reason_codes, error_codes, sha256, doc_id?, document_id? } ]`.
- **Forbidden in any public response / audit / log:** `storage_uri`, `manifest_uri`, object keys, raw filenames, raw folder paths, raw content / `text_excerpt`, DSNs, env values. A whitelist response-builder (pattern: `_safe_public_run_summary` / `_build_run_ref_step_responses`, `routes/runs.py`) constructs responses; internal-only columns are never serialized.
- **OpenAPI:** add the **deal-scoped** paths + safe schemas to `IDIS_OpenAPI_v6_3.yaml` with `additionalProperties: false`; the request schema must **not** accept `uri`/`paths`/`files`/`folder_path`/`storage_uri`/`object_key` (reuse the `PATH_LIKE_RUN_FIELDS` rejection at `routes/runs.py:57,147,166`). Contract-lock via `test_openapi_loader.py` / `test_api_openapi_validation.py`.

### 3.9 Fixture strategy (supported generated fixtures)
- Reuse `_write_fixture_tree()` + `create_test_xlsx()` and synthetic descriptors; generate supported fixtures (DOCX/XLSX/PDF text) plus deferred (OCR `.png`, media `.mp4`) and blocked (corrupt) cases to exercise reason codes. **Never** read `real_example` raw content in fixtures.

### 3.10 Private `real_example` inventory safety boundary (module CONFIRMED)
- **Confirmed entry point:** `src/idis/evaluation/real_example_gate.py` — `run_real_example_gate(...)` (`:83`) with `GateMode` (`:64`, use `INVENTORY_ONLY`) and `_safe_summary(...)` (`:771`) emitting **only** `counts_by_extension/status/parser_outcome/reason_code`, `total/processed/ledger_entry_count`, `safe_summary:true`. A gate ledger already exists at `src/idis/evaluation/real_example_gate_ledger.py` — align the durable file ledger's safe-aggregate shape with it. CLI: `scripts/run_real_example_gate.py`.
- Slice77's private acceptance runs this gate **local-only, aggregate-only**: no FULL run, no provider/network calls, no raw path/filename/content, no readiness clearing. Assert `safe_summary is true` and absence of seeded path/name/content markers.

### 3.11 Security: RBAC / ABAC / audit / idempotency (explicit)
- **RBAC (deny-by-default, `policy.py`):** register the three ops in `POLICY_RULES` (`src/idis/api/policy.py:114`), **all with `is_deal_scoped=True`** — that flag is what makes `RBACMiddleware` run the deal-level ABAC check (`PolicyRule` fields are `allowed_roles`/`is_mutation`/`is_deal_scoped`, `policy.py:87-93`; precedent `"getDeal": PolicyRule(..., is_deal_scoped=True)` `:118`). Mutation (`createDataRoomPackage`): `allowed_roles=MUTATOR_ROLES` (`ANALYST, PARTNER, IC_MEMBER, ADMIN, INTEGRATION_SERVICE`), `is_mutation=True, is_deal_scoped=True`. Reads (`listDataRoomPackages`, `getDataRoomPackage`): `allowed_roles=ALL_ROLES` (incl. `AUDITOR`, read-only), `is_mutation=False, is_deal_scoped=True`. `RBACMiddleware` (`middleware/rbac.py:47-138`) enforces via `operation_id`; **unregistered ops are denied**, so OpenAPI `operationId`s must match exactly.
- **ABAC (deal-scoped) — CORRECTED, see known platform limitation.** Slice77 registers all three operations with `is_deal_scoped=True` (the correct, intended wiring). **However, deal-*assignment* ABAC is NOT actually enforced for `/v1/deals/{dealId}/...` routes today, and this is a platform-wide pre-existing gap (NOT Slice77-specific, NOT patched here):** `RBACMiddleware` is a `BaseHTTPMiddleware`, so `request.path_params` is empty when it runs, and `_extract_resource_context` (`src/idis/api/middleware/rbac.py:351-419`) only URL-regex-extracts `claim_id`/`run_id`, **not `deal_id`** (see its own comment at `:403-405`). With `deal_id == None`, `requires_abac` is false and the deal-assignment check is skipped. (Earlier wording here — "ABAC assignment verified present, enforced — not deferred" — was inaccurate and is retracted.)
  - **What actually protects Slice77 package isolation today:** (1) tenant RLS (`ENABLE` + `FORCE`) — no cross-tenant access; (2) the deal-scoped route shape; (3) the repo's `deal_id`-scoped reads — `get_package(package_id, deal_id)` and `list_files_by_package(package_id, deal_id)` return `None`/empty for cross-deal/cross-tenant → identical masked 404, no existence oracle; (4) no `packageId`-only route; (5) RBAC role gates (e.g. AUDITOR cannot create). The residual gap is intra-tenant: an actor not assigned to a deal is not blocked by ABAC at the API layer (same as every other deal-scoped route).
  - **Follow-up (OUTSIDE Slice77):** add a `deal_id` extraction path (URL regex fallback in `_extract_resource_context`, or a routing-aware ABAC dependency) so deal-assignment is enforced for deal-scoped routes. Tracked as a separate platform security task — **no GitHub issue created yet**; documented here and in PR notes only.
- **Audit:** add to `OPERATION_ID_TO_EVENT_TYPE` (`middleware/audit.py:41-62`): `createDataRoomPackage → ("data_room_package.created","MEDIUM","data_room_package")`. Routes set `request.state.audit_resource_id = package_id`; the middleware emits the `resource` block `{resource_type, resource_id}` and **fails closed (500 `AUDIT_EMIT_FAILED`)** if `resource_id` is missing on a successful mutation. Audit payloads carry only safe ids/counts.
- **Idempotency:** create POSTs honor the existing `Idempotency-Key` middleware (`middleware/idempotency.py:176-241`; scope `(tenant_id, actor_id, method, operation_id, key)` + payload sha256 → replay / `409 IDEMPOTENCY_KEY_CONFLICT`). Package-level create de-dup relies on this middleware (**no name-based unique — R7**); the file ledger `UNIQUE(tenant_id, package_id, path_hash)` gives a true no-duplicate-row guarantee for files within a package.

## 4. RED tests to add first (design only — not implemented here)
**Functional**
- T1 **Create from fixtures** (`tests/test_slice77_data_room_package.py`): deal-scoped POST with `document_ids` of generated-fixture docs creates a package + N ledger rows; 201 ref carries safe aggregates only.
- T2 **Per-file triage + deterministic reason codes:** each row has `file_status`/`support_status`/`triage_status` + sorted-unique `reason_codes` from the existing vocabulary; supported/deferred/blocked fixtures → expected codes (`ocr_required`, `conversion_required`, `unsupported_format`, `corrupted_file`, …).
- T3 **No unsupported class silently dropped:** unsupported file → visible blocker row with a deterministic reason code.
- T4 **Idempotent create + intra-create dedup:** duplicate `document_ids` within one create collapse to a single ledger row (`UNIQUE(tenant_id, package_id, path_hash)`); a repeated create POST with the same `Idempotency-Key`+payload replays (no second package).

**Security / safety**
- T5 **ABAC masked 404 / isolation:** `getDataRoomPackage` for (a) other-tenant, (b) same-tenant-other-deal, (c) unassigned actor all return the **identical** 404 `DATA_ROOM_PACKAGE_NOT_FOUND` (no existence oracle); a valid assigned actor in the owning deal gets 200.
- T6 **RBAC role gate:** `AUDITOR`-only actor POST create → 403 `RBAC_DENIED`; a `MUTATOR_ROLES` actor assigned to the deal → allowed; list/read allowed for AUDITOR.
- T7 **No storage/object-key leak:** the public 201/200 responses **and** the emitted audit event contain **no** `storage_uri`, `manifest_uri`, object key, raw filename, raw folder path, `text_excerpt`/content, DSN, or env value — only safe ids/hashes/counts/reason codes. Seed a fixture path/filename/marker; assert absence in `json.dumps(response)` and in the audit event.
- T8 **Audit resource metadata:** a successful create emits an event with `resource.resource_type == "data_room_package"` and `resource.resource_id == package_id`; a create path that fails to set `audit_resource_id` fails closed (500 `AUDIT_EMIT_FAILED`).
- T9 **OpenAPI rejects raw path/object-key fields:** the create schema is `additionalProperties:false`; a body containing `uri`/`paths`/`files`/`folder_path`/`storage_uri`/`object_key` → 400 `INVALID_REQUEST`; spec/loader contract-lock passes.
- T10 **Tenant RLS (Postgres SQL-shape, skipped without DB):** both tables have the `tenant_isolation_*` RLS policy + expected columns/constraints; single head after `0019`→`0020`.

**Persistence**
- T11 **Repo parity & JSONB roundtrip:** model `model_validate` roundtrip; in-memory `create/list_by_package/list_by_deal` parity; `reason_codes`/`error_codes` list roundtrip.
- T12 **Migration `0020`:** additive/nullable/idempotent; downgrade drops both tables CASCADE (static/skip-without-DB shape test, mirroring `test_run_step_schema_contract.py`).

**Private gate**
- T13 **Private `real_example` safe inventory:** `run_real_example_gate(..., GateMode.INVENTORY_ONLY)` over a synthetic "private" tree emits `safe_summary:true` aggregates only; seeded path/name/content markers absent; readiness untouched; no provider/network calls.

## 5. Exact files expected to touch (subject to Task 0 confirmation)
**Create**
- `src/idis/persistence/migrations/versions/0020_data_room_packages_and_files.py`
- `src/idis/models/data_room_package.py` (`DataRoomPackage`, `DataRoomPackageFile`, enums for `file_status`/`status`)
- `src/idis/persistence/repositories/data_room_packages.py` (Postgres + in-memory + factory + `clear_*_store`)
- `src/idis/services/data_room/package_service.py` (group + triage + optional ingest + safe manifest)
- `src/idis/api/routes/data_room_packages.py` (the new routes)
- `tests/test_slice77_data_room_package.py` (primary RED suite)
- `tests/test_slice77_data_room_package_postgres.py` (Postgres SQL-shape, gated/skipped without DB)

**Modify**
- `src/idis/api/main.py` — register the new router.
- `src/idis/api/policy.py` — add `POLICY_RULES` (RBAC) entries for the 3 new operation ids (1 mutation + 2 reads).
- `src/idis/api/middleware/audit.py` — add the `OPERATION_ID_TO_EVENT_TYPE` entry for the 1 mutation (`createDataRoomPackage`).
- `src/idis/persistence/repositories/__init__.py` — export the new repo/factory.
- `openapi/IDIS_OpenAPI_v6_3.yaml` — add the **deal-scoped** package paths + safe schemas (`additionalProperties:false`).
- `src/idis/evaluation/real_example_gate.py` — optional thin hook for the durable file-ledger inventory (safe aggregates only; reuse `_safe_summary`).
- Possibly `src/idis/services/documents/parser_capabilities.py` — only if a missing triage mapping is proven by a RED test (prefer reuse).

**Likely NOT changed:** `IngestionService`, object store, `Document`/`DocumentArtifact`/`DocumentSpan` tables, run execution, strict readiness, debate/analysis/scoring.

## 6. Migration / schema needs
- **YES** — one additive migration `0020` for the two new tables (+ RLS + indexes). Nullable/idempotent; safe CASCADE downgrade. No change to existing tables. No pgvector change.

## 7. Local verification plan (execution time)
```
$env:PYTHONPATH="C:\Projects\IDIS\IDIS-slice77\src"
python -m pytest tests/test_slice77_data_room_package.py -q                 # focused RED/GREEN
python -m pytest tests/test_run_data_room_inventory_package_service.py tests/test_api_documents.py \
  tests/test_openapi_loader.py tests/test_api_openapi_validation.py tests/test_run_step_schema_contract.py \
  tests/test_api_runs.py -q                                                 # affected suites
# Contracts (parse only): json.loads(schemas/audit_event.schema.json) + yaml.safe_load(openapi/IDIS_OpenAPI_v6_3.yaml)
python -m ruff format --check src tests scripts ; python -m ruff check src tests scripts
python -m mypy --no-incremental <touched source files>
python scripts/forbidden_scan.py --repo-root . ; git diff --check
```
Postgres RLS/persistence runtime proof is deferred to CI `postgres-integration` (skipped locally without `IDIS_DATABASE_ADMIN_URL`/`IDIS_DATABASE_URL`).

## 8. Explicit non-goals / deferred items
- **No** FULL run / providers / live LLM / enrichment / OCR-or-media execution; **no** `real_example` raw content; **no** strict-readiness clearing; **no** VC-ready claim.
- **No** changes to `IngestionService`, object store, or the existing document tables beyond linking by id.
- **No** new artifact storage; packages group existing artifacts.
- Slice76 Minor (provenance UNKNOWN-fallback naming reconciliation) is out of scope.

## 9. Task sequence (bite-sized, TDD)
- **Task 0 (discovery/verify, no code):** confirm — `deals` table + RLS exist for the FK; the precise object-store readiness check to reuse; existing `parser_capabilities` triage covers all fixture classes; the RBAC/ABAC operation-id registration points; OpenAPI path-style validation hook. (The `real_example` module is already confirmed — §3.10.) Adjust §3/§5 accordingly.
- **Task 1:** `DataRoomPackage`/`DataRoomPackageFile` models + enums — RED T11 (roundtrip) → minimal model → green → commit.
- **Task 2:** Migration `0020` + repos (Postgres + in-memory + factory) — RED T11/T12 (+ T10 SQL-shape) → green → commit.
- **Task 3:** `package_service` create-from-`document_ids` triage + grouping (in-memory) — RED T2/T3/T4 → green → commit.
- **Task 4:** Public API routes (create/list/get) + OpenAPI + RBAC (`policy.py`) + audit (`audit.py`) — RED T1/T5/T6/T8/T9 → green → commit.
- **Task 5:** Redaction/leakage safety — RED T7 → green → commit.
- **Task 6:** Tenant/RLS (in-memory + Postgres SQL-shape) — RED T10 → green → commit.
- **Task 7:** Private `real_example` safe inventory hook — RED T13 → green → commit.
- **Task 8:** Full local verification gate + Postgres static/skip coverage; `code-reviewer` + validation-review.

## 10. Risks / open questions
- **R1 (LOCKED): new durable tenant/deal-scoped product package, distinct from the run-scoped inventory service.** Reuse field shapes only; do not refactor the run-scoped service. (See §3.3.)
- **R2 (RESOLVED): create input is durable `document_ids` only** (validated like `RunSource`); no arbitrary base64/batch-upload API. Fixtures create docs via existing ingest helpers in tests, then reference ids.
- **R3 (LOCKED): redact-only.** `path_hash` + safe `extension` only; no raw-path retention, no opt-in policy column in Slice77. (See §3.4.)
- **R4 (LOCKED): extend `IDIS_OpenAPI_v6_3.yaml` in place, no version bump.**
- **R5 (Postgres-only proof):** RLS/persistence is CI-only locally; ensure SQL-shape + in-memory tests give high confidence before `postgres-integration`.
- **R6 (RESOLVED): `src/idis/evaluation/real_example_gate.py::run_real_example_gate` + `GateMode.INVENTORY_ONLY` + `_safe_summary` (`:771`)** is the confirmed safe-aggregate inventory entry point (gate ledger at `real_example_gate_ledger.py`).
- **R7 (LOCKED): no user-supplied `name`.** Packages are keyed by `package_id` only — `name` removed from request/response/table/unique constraint (eliminates a user-controlled leakage surface).
- **R8 (LOCKED): create-only.** Slice77 ships package creation from `document_ids` (that list is the batch/manifest grouping surface). `addDataRoomPackageFiles` is **deferred** to a later slice — removed from routes/RBAC/audit/OpenAPI/tests here.

---

**Awaiting approval before implementation.** Per `writing-plans`, after approval: (1) subagent-driven in this session, or (2) a separate session via `executing-plans`. No code edited besides this plan doc; no commit, no PR.

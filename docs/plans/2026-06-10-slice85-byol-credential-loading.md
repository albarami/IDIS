# Slice85 — BYOL Credential Loading — Implementation Plan

> **For Claude / agentic workers:** REQUIRED SUB-SKILL: `superpowers:executing-plans` (or `subagent-driven-development`) task-by-task. Per task: `test-driven-development` (RED → verify red → minimal GREEN → verify), `verification-before-completion` before any status claim, `using-git-worktrees` already done, `finishing-a-development-branch` before commit/PR. **Reuse before create. STOP for approval after each task.** The §8 decisions are locked (D-A VERIFY+CLOSE-RESIDUALS · D-B HARDEN · D-C existing bootstrap · D-D/D-E confirmed · D-F out-of-scope confirmed). **Status: Tasks 1–3 complete; Task 4 (docs/gate/review) in progress; Task 5 = PR only.** §0 records the as-built result; the sections below are preserved as the original discovery/planning record.

**Goal:** Load BYOL provider credentials (Companies House, GitHub, FRED, Finnhub, FMP) into **durable tenant credential storage safely** (keyed by `IDIS_ENRICHMENT_ENCRYPTION_KEY`), with a secret-safe bootstrap for the strict local tenant — so enrichment services read tenant credentials (never raw ambient env) and missing BYOL credentials block strict enrichment **before** the run.

**Architecture (headline discovery):** Unlike Slices 83/84 (which built new seams), **most of Slice85's master-plan text is ALREADY DELIVERED** — primarily by Slice57 (`tests/test_slice57_byol_enrichment.py` + `docs/SLICE_56_STRICT_RUNTIME_ROADMAP.md:29-39` explicitly names Slice57's deferred work as what master plan v2 renamed Slice85) and later wiring. Durable encrypted storage, env→store bootstrap, connector credential injection, and two-layer strict blocking all exist and are green. The slice therefore takes the **Slice82 shape**: characterize/pin the existing truth, close only **verified residual gaps** (the one code-flagged production gap is encryption strength), prove the master-plan acceptance end-to-end, and reconcile stale docs. **No new DB migration is needed** (migration 0011 already created the table — verified). No real provider call in CI; no Layer-2 / Slice86 work.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, pytest, ruff (CI-parity), mypy, Postgres + RLS. Encryption today: stdlib XOR+HMAC-SHA256 (`cryptography` NOT in pyproject — verified). Tests use injected fake repos/health-checkers + env patching — **no real provider call**.

**Base:** branch `slice85-byol-credential-loading` @ `d58fee6448eca17bc0653dd0c1136ab8abbd2b7a` (= `origin/main`, Slice84 merged via PR #96, pypdf pin via PR #97), worktree `C:/Projects/IDIS/IDIS-slice85`. Baseline green: import proof pinned to this worktree's `src` · `ruff format --check .` 753 ok · `ruff check .` ok · clean-cache `mypy src/idis` 393 files ok · smoke (slice57 BYOL + enrichment registry/rights/orchestration/connectors-registry + strict readiness) = **68 passed**.

---

## 0. AS-BUILT (Tasks 1–3 complete)

- **Task 1 — Characterization** (`tests/test_slice85_byol_credential_loading_characterization.py`, 15 tests): pinned the Slice57-delivered truth (connectors never read ambient env; service loads tenant-scoped repo credentials by `request.tenant_id`; preflight blocks on missing credentials — zero-env yields `TENANT_CREDENTIAL_MISSING`, not `ENV_KEY_MISSING`; execution backstop; preflight/execution repo-factory parity; durability contracts; migration 0011; leak-safety) plus the two Task 2 gap pins, drift-flipped after Task 2.
- **Task 2 — AES-256-GCM hardening** (the one production change): `encrypt_credentials`/`decrypt_credentials` in `src/idis/persistence/repositories/enrichment_credentials.py` swapped from XOR+HMAC to **AES-256-GCM** with a **versioned ciphertext format** (`v2:` + base64(12-byte fresh nonce + sealed payload)); same env var (SHA256-derived 32-byte key); fail-closed preserved (missing key → `EncryptionKeyMissingError` on encrypt AND decrypt; tamper/garbage/legacy-unversioned/wrong key → `ValueError` with fixed messages); repository interfaces unchanged; **no data migration** (no stored ciphertexts existed; legacy format fails closed). `pyproject.toml` gained `cryptography>=42.0.0,<47` (bounded — pypdf lesson), formalizing the implicit dependency auth_sso.py's JWKS verification already relied on. Suite: `tests/test_slice85_aes_gcm_credential_encryption.py` (11 tests); 2 Slice57-era format-internals tests drift-updated.
- **Task 3 — Acceptance** (`tests/test_slice85_byol_credential_loading_acceptance.py`, 7 tests, no production change): composes the master-plan acceptance — tenant-credential reads never ambient env; the REAL admission funnel blocks strict before run (run **hermetically** against an empty environment after a 3-skeptic adversarial review caught an env-bleed hygiene defect); durability differential (transient blocks / durable clears); secret-safe whitelist bootstrap into durable **encrypted** storage with round-trip; execution backstop fail-closed (strict) and unchanged (non-strict); planted markers absent from readiness JSON, exceptions, summaries, reprs.
- **Review-noted follow-ups (NOT Slice85 defects, verified CONTAINED today):**
  1. FRED/Finnhub/FMP place the API key in the request URL and their internal `FetchError` messages interpolate httpx errors containing that URL; today every such error is caught inside the connector and surfaced as a fixed-string ERROR result. A future refactor could leak via those messages — candidate hardening for Slice86-adjacent work.
  2. Pre-existing on `main` (not introduced here): the `httpx` library logs `HTTP Request: GET <full URL>` at INFO — including the URL-embedded key for those three providers — *if* an operator attaches a root/`httpx` log handler at INFO. Under the documented uvicorn entrypoint no such handler exists (no `basicConfig`/`dictConfig` in `src/`), so it is dormant; candidate hardening: redact via httpx event hooks or move keys to headers where the provider supports it.
  3. Defense-in-depth nits from review (all non-blocking): `decrypt_credentials` does not re-validate the decrypted JSON is a `dict`; the internal "too short" message is shadowed by the fixed "authentication failed" wrapper; SHA256 KDF is unsalted/unstretched (acceptable for an operator-supplied high-entropy key — `.env.example` now says to use ≥32 random bytes).

**Guarantees:** no real provider call in any test (hermetic real-funnel test; fake checkers/registries elsewhere); no DB migration; no credential lifecycle API/rotation/operator script; no Slice86 execution/provenance work; Layer 2 untouched.

---

## 1. Master Plan text (verbatim, `docs/IDIS_FULL_LIVE_MASTER_PLAN_V2.md:250-261`)
> #### Slice 85: BYOL Credential Loading
>
> **Goal:** Load provider credentials into durable tenant credential storage safely.
>
> **Scope:**
> - `IDIS_ENRICHMENT_ENCRYPTION_KEY`.
> - Companies House, GitHub, FRED, Finnhub, FMP credentials.
> - Secret-safe bootstrap for strict local tenant.
>
> **Acceptance:**
> - Enrichment services read tenant credentials, not raw ambient env.
> - Missing BYOL credentials block strict enrichment before run.

(Slice 86 — Enrichment Execution And Provenance — follows, consuming these credentials: rights/BYOL policy, hit/miss/error/cache/blocked ledger, provider provenance. OUT of Slice85 scope.)

---

## 2. Discovery — what ALREADY exists (verified; exact refs at d58fee6)

### 2.1 Durable tenant credential storage — EXISTS (no migration needed)
- **Migration 0011** `src/idis/persistence/migrations/versions/0011_enrichment_credentials.py:26-56`: `enrichment_credentials` table (`tenant_id UUID, connector_id TEXT, ciphertext TEXT` — never plaintext — `created_at/rotated_at/revoked_at`, composite PK, **RLS policy** scoping rows via `idis.tenant_id`).
- **`PostgresCredentialRepository`** (`src/idis/persistence/repositories/enrichment_credentials.py:213-362`): `is_durable=True`; `store/load/rotate/revoke/exists`; encrypts before INSERT, decrypts on load; `set_tenant_local()` RLS in `__init__` (same pattern as runs/deals repos).
- **`InMemoryCredentialRepository`** (:74-211): plaintext, `is_durable=False`, dev/test only.
- **Factory** `get_enrichment_credentials_repository(conn, tenant_id)` (:364-371): Postgres repo when `conn is not None and is_postgres_configured()`, else in-memory. The in-memory fallback is **fail-closed for strict** via the durability check (§2.4) — strict can never pass on a non-durable repo.
- **Encryption** (:374-451): key from `IDIS_ENRICHMENT_ENCRYPTION_KEY` (SHA256-derived 32-byte), XOR stream + **HMAC-SHA256 auth** (verify-before-decrypt, fail-closed `ValueError` on tamper), `EncryptionKeyMissingError` (:46-53) when Postgres configured but key missing. **Code comment at :393: "For production, replace with AES-GCM via cryptography library"** — the one code-flagged production gap (D-B).

### 2.2 Env→store loading + provider specs — EXISTS
- `BYOL_PROVIDER_ENV_SPECS` (`src/idis/services/enrichment/byol_credentials.py:131-157`): `companies_house→COMPANIES_HOUSE_API_KEY→api_key`, `github→GITHUB_API_TOKEN→token`, `fred→FRED_API_KEY→api_key`, `finnhub→FINNHUB_API_KEY→api_key`, `fmp→FMP_API_KEY→api_key`.
- `load_byol_credentials_from_env()` (:208-266): whitelist-only (the 5 specs), reads process env + strict dotenv, `credential_repo.store(...)` (→ encrypted in Postgres); `EncryptionKeyMissingError` caught → secret-safe `ENV_KEY_PRESENT_NOT_LOADED` status. Never echoes values.
- `assess_byol_provider_readiness()` (:269-351): per-provider secret-safe statuses `ENV_KEY_MISSING → ENV_KEY_PRESENT_NOT_LOADED → TENANT_CREDENTIAL_MISSING → TENANT_CREDENTIAL_LOADED → HEALTH_PASSED/FAILED`; `byol_all_health_passed()` (:354-358) requires all 5 `HEALTH_PASSED`.
- `SafeByolProviderHealthChecker` (:68-87): live check **only with safe public identifiers** (`'TESCO PLC'`, `'github'`, `'GDP'`, `'AAPL'`) and only when a credential is loaded — in CI (no env keys) it never fires; tests inject fakes.

### 2.3 Acceptance (1) "tenant credentials, not raw ambient env" — MET at connector level
- All 5 connectors read **only** `ctx.byol_credentials` at fetch time and return `BLOCKED_MISSING_BYOL` when absent; constructors take only an optional `httpx.Client` (`connectors/companies_house.py:101-106`, `github.py:101-106`, `fred.py:102-107`, `finnhub.py:101-106`, `fmp.py:101-106`).
- `EnrichmentService.enrich()` (`services/enrichment/service.py:134-205`): `requires_byol` → `credential_repo.load(tenant_id, connector_id)` → decrypted dict into `EnrichmentContext.byol_credentials`; `CredentialNotFoundError` → `BLOCKED_MISSING_BYOL`. Registry marks exactly the 5 as `requires_byol=True` (:369-409). Rights gate: RED providers (Finnhub, FMP) blocked in PROD without BYOL (`rights_gate.py:110-143`).

### 2.4 Acceptance (2) "missing BYOL blocks strict enrichment before run" — MET (two layers)
- **Preflight (before run):** API `start_run` admission (`api/routes/runs.py:179-245`, 409 `STRICT_FULL_LIVE_BLOCKED`) → `build_strict_full_live_admission_report` (`services/runs/strict_full_live.py:339-348`) **already passes** `byol_credential_repo=get_enrichment_credentials_repository(db_conn, tenant_id)` + `SafeByolProviderHealthChecker()`; `_external_enrichment_apis()` (:703-732) `may_proceed=False` unless `byol_all_health_passed AND byol_credentials_durable` (`_byol_credentials_durable` :1994-1997 checks `is_durable`). `IDIS_ENRICHMENT_ENCRYPTION_KEY` is in `TRACKED_ENV_VARS` + `STRICT_ENV_EXAMPLE_REQUIRED_NAMES` (:126,184).
- **Execution backstop:** `_run_full_enrichment` (`api/routes/runs.py:1231-1327`) already builds `create_default_enrichment_service(credential_repo=get_enrichment_credentials_repository(db_conn, tenant_id), strict_full_live=..., tenant_id=..., strict_dotenv_path=...)` and raises `RuntimeError` in strict when any provider returns `BLOCKED_MISSING_BYOL`/`BLOCKED_RIGHTS`/`ERROR`; orchestrator records FAILED step.

### 2.5 Secret-safe bootstrap for strict local tenant — EXISTS (two trigger points)
- **At preflight:** `build_strict_full_live_readiness_report(load_byol_env_credentials: bool = True)` (`strict_full_live.py:428`) → `assess_byol_provider_readiness(load_env_credentials=True)` loads env creds into the (durable) repo during admission.
- **At execution:** `create_default_enrichment_service` (`service.py:327-366`) calls `load_byol_credentials_from_env()` when `strict_full_live and tenant_id`.
- No dedicated operator-run bootstrap script exists (`scripts/` has `pg_bootstrap_ci.py` precedent only) — D-C.

### 2.6 Secret-safety toolbox + env docs — EXISTS
- `.env.example:149-158`: BYOL section with `IDIS_ENRICHMENT_ENCRYPTION_KEY=dev-placeholder-not-real` + all 5 provider vars (`placeholder-not-real`).
- Sanitizers: `_sanitize_error`/`_sanitize_request_id` (`llm_model_health.py:164-176` + identical copies in ocr/media/embedding health); `forbidden_scan.py` secret patterns (sk-, AKIA, tokens, PGPASSWORD…) with redacted output; BYOK compliance module hashes aliases (`compliance/byok.py:136-188`).
- `test_slice57_byol_enrichment.py:116-162` already pins loader whitelist + **redaction of secret values in JSON serialization**.

### 2.7 Tests already pinning this area
`tests/test_slice57_byol_enrichment.py` (loader/readiness/durable-vs-transient, 288-342), `tests/test_enrichment_credentials_postgres.py` (repo CRUD + encryption round-trip + `EncryptionKeyMissingError`, 179-257), 5 connector tests (credential injection + `BLOCKED_MISSING_BYOL`), `test_enrichment_rights_gate.py` (RED+PROD+no-BYOL deny + HIGH audit), `test_enrichment_registry_fail_closed.py`, `test_enrichment_service_orchestration.py` (full rights→cache→credentials→fetch→audit flow). Baseline: all green (68 passed in smoke).

---

## 3. Gap analysis — what is genuinely LEFT for Slice85

| # | Candidate gap | Status | Disposition |
|---|---|---|---|
| G1 | **Encryption strength**: XOR+HMAC with explicit in-code production note "replace with AES-GCM via cryptography library"; `cryptography` not in pyproject | Verified real (only code-flagged production gap; goal says "safely") | **D-B decision** — the one substantive production change on offer |
| G2 | **Slice85 acceptance proof**: no single suite proves the master-plan acceptance end-to-end under the strict profile (Slice57 tests cover pieces; nothing composes preflight-block + execution-backstop + never-ambient-env + durability + leak-safety as THE acceptance) | Verified (by test inventory) | Acceptance task (mirrors Slice83/84 Task-5 style) |
| G3 | **Stale docs**: `docs/architecture/strict_full_live_readiness.md:35,96,98,145` still lists BYOL tenant-credential provisioning as missing — contradicts shipped reality | Verified stale | Docs reconciliation task |
| G4 | Dedicated operator bootstrap script for the strict local tenant | Bootstrap already runs at preflight+execution (§2.5); a script is operator convenience only | **D-C decision** (default: not needed) |
| G5 | Credential lifecycle API (upload/rotate/revoke endpoints), key rotation, external secret managers | Master plan does not ask | OUT (note as Slice86+/follow-up) |
| G6 | New DB migration | **Not needed** — migration 0011 exists | Confirmed; pin in Task 1 |

**Risk-shaped unknowns for Task 1 to pin (characterization, not change):** exact strict behavior when `IDIS_ENRICHMENT_ENCRYPTION_KEY` is missing at preflight (expected: `ENV_KEY_PRESENT_NOT_LOADED` → blocked, secret-safe); in-memory fallback always non-durable-blocked in strict; readiness report JSON never contains a credential value (leak test with planted markers); preflight + execution use the same repo factory (parity).

---

## 4. No-real-call / safety boundary
No real provider HTTP in any test (inject fake `ByolProviderHealthChecker` + fake/in-memory repos; never set real env keys); no real Anthropic call; no real-data FULL; no DB migration; no prompt-registry mutation; no Layer 2; no Slice86 (enrichment EXECUTION/provenance). `SafeByolProviderHealthChecker` live checks remain opt-in-by-credentials-present (existing behavior, untouched unless D-B/D-C say otherwise). Planted-secret leak tests must assert markers absent from every surfaced status/report/exception repr.

## 5. Reuse map
**Reuse unchanged (verify only):** migration 0011; both credential repositories + factory; `byol_credentials.py` (specs/loader/assessment/health-checker); strict readiness components (`_external_enrichment_apis`, `_byol_credentials_durable`, provider matrix); `_run_full_enrichment` wiring; rights gate; sanitizers; `.env.example` BYOL block; Slice57 + connector + postgres-credential tests.
**Touch (production) — narrow, decision-gated:** ONLY `encrypt_credentials`/`decrypt_credentials` (+ pyproject dependency) if D-B=HARDEN. Everything else is tests + docs.

## 6. Verification gate (CI parity — from worktree root, `PYTHONPATH=src`)
`python -c "import idis; print(idis.__file__)"` · `ruff format --check .` · `ruff check .` · clear `.mypy_cache` then `mypy src/idis` · `python scripts/forbidden_scan.py --repo-root .` · `git diff --check` · targeted `pytest` (fakes only; **no real provider/network call**; DB-backed `*_postgres.py` unit-style fakes run locally, real Postgres only in CI). Contract/OpenAPI parse = N/A (no gate script).

## 7. Task breakdown (TDD; STOP after each)
- **Task 1 — Characterization (no prod change):** `tests/test_slice85_byol_credential_loading_characterization.py` pinning §2/§3 truth: (1) the 5 connectors never read ambient env (planted env key + empty `byol_credentials` → still `BLOCKED_MISSING_BYOL`); (2) service loads via repo and decrypts; (3) preflight blocks when credentials missing / repo non-durable / encryption key missing — with secret-safe statuses; (4) execution backstop raises in strict on `BLOCKED_MISSING_BYOL`; (5) preflight and execution both use `get_enrichment_credentials_repository` (parity); (6) bootstrap loads whitelist-only env creds into the durable repo (fake-Postgres) and readiness then reports `TENANT_CREDENTIAL_LOADED`/`HEALTH_PASSED` with a fake health checker; (7) encryption round-trip + tamper fail-closed + `EncryptionKeyMissingError`; (8) planted-marker leak test over readiness report JSON; (9) migration 0011 exists (no new migration needed). GREEN-on-arrival expected; any RED = STOP and report.
- **Task 2 — (decision-gated, D-B) Encryption hardening:** RED-first swap of `encrypt_credentials`/`decrypt_credentials` to AES-256-GCM via `cryptography` (new pinned dependency), keeping: same key env var (SHA256-derived key), same fail-closed contracts (`EncryptionKeyMissingError`, tamper → `ValueError`), same repo interface; ciphertext format versioned (prefix) so the swap is explicit; existing XOR ciphertexts need no migration (no production data exists — pin that assumption in RED). If D-B=DEFER, Task 2 collapses into a documented residual note.
- **Task 3 — Slice85 acceptance proof:** `tests/test_slice85_byol_credential_loading_acceptance.py` composing the master-plan acceptance under the strict profile with injected fakes: (1) enrichment reads tenant credentials, not ambient env; (2) missing BYOL blocks strict **before** run (preflight 409 component truth) + execution backstop; plus durability requirement, secret-safe bootstrap, and leak-safety. GREEN-on-arrival from Tasks 1–2 unless a true blocker emerges.
- **Task 4 — Docs/config reconciliation + full gate + independent review:** update `docs/architecture/strict_full_live_readiness.md` stale BYOL lines (dated note, census preserved — Slice84 precedent); plan doc AS-BUILT section; `.env.example` only if inaccurate (currently accurate); full gate; 5 independent reviews (storage/encryption correctness; strict gating/no-bypass; leak-safety; docs accuracy; test sufficiency).
- **Task 5 — Finish branch: open PR only.**

## 8. Decisions — confirm BEFORE Task 1
- **D-A — SCOPE (key).** **VERIFY+CLOSE-RESIDUALS (recommended):** characterize the already-shipped Slice57 infrastructure as Slice85's substance, close only G1–G3, prove acceptance, reconcile docs. **BROAD:** also add credential lifecycle API / operator bootstrap script / key rotation (master plan does not require them).
- **D-B — Encryption (key).** **HARDEN (recommended):** replace XOR+HMAC with AES-256-GCM via the `cryptography` library — the only code-flagged production gap, and the goal says "safely"; cost: one new pinned dependency (container build impact) + versioned ciphertext format. **DEFER:** keep XOR+HMAC (it is HMAC-authenticated, fail-closed), record as explicit follow-up. No data migration either way (no real ciphertexts exist yet — Task 1/2 pins this).
- **D-C — Bootstrap surface.** **EXISTING (recommended):** preflight+execution `load_byol_credentials_from_env` IS the secret-safe bootstrap (verified, default-on). **SCRIPT:** add an operator-run `scripts/bootstrap_byol_credentials.py` for ahead-of-time loading.
- **D-D — Acceptance shape.** Mirror Slice83/84: builder/seam-level acceptance with injected fakes + strict-profile readiness checks; no TestClient FULL run needed (preflight component truth is asserted at the report level). Confirm.
- **D-E — Leak-test markers.** Planted values (e.g. `sk-byol-LEAK`, path markers) must be absent from readiness/report/exception reprs — same discipline as Slice83/84. Confirm.
- **D-F — Out of scope.** Credential rotation/expiry policies, external secret managers, per-provider optionality policy, enrichment EXECUTION/provenance (Slice86), Layer 2. Confirm.

## 9. Risks
- **Hidden divergence between explorer claims and code** — all load-bearing claims re-verified by hand (§2 refs checked at d58fee6); Task 1 re-pins them as executable truth. (Mitigated.)
- **Encryption swap breaking existing ciphertexts** — no production data exists; versioned format + round-trip tests; in-memory repo unaffected. (D-B HARDEN only.)
- **Accidental real provider call** — health checker fires only when credentials are loaded; tests must never set real keys and must inject fake checkers. CI has no keys (verified by existing green CI).
- **`cryptography` dependency drift** — pin with an upper bound (learned from pypdf 6.13.0 incident, PR #97).
- **Scope creep into Slice86** — enrichment execution/provenance/ledger explicitly OUT.

## 10. Open questions for you
1. **D-A:** VERIFY+CLOSE-RESIDUALS (recommended) or BROAD?
2. **D-B:** HARDEN to AES-256-GCM via `cryptography` (recommended; new pinned dependency) or DEFER?
3. **D-C:** existing preflight/execution bootstrap suffices (recommended) or add an operator script?
4. **D-D/D-E:** confirm acceptance shape + leak-marker discipline.
5. **D-F:** confirm the out-of-scope list.

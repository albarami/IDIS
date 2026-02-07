# IDIS Enrichment Connector Framework (Draft v0.1)

This document defines the **minimum production-ready connector framework** needed to support the phased external API plan (GREEN/YELLOW/RED) and BYOL connectors.

## 1) Objectives

1. Provide a **uniform adapter contract** for all enrichment providers.
2. Enforce **rights-class gating** (GREEN/YELLOW/RED) and BYOL constraints.
3. Centralize **caching policy** (TTL, “no-store” modes, and provenance).
4. Persist **normalized facts + provenance**, and retain raw payloads only when allowed.

## 2) Core concepts

### Provider
A named external source (e.g., SEC EDGAR, Companies House, Crunchbase).

### Rights class
- **GREEN**: safe to ship; caching allowed with reasonable TTL.
- **YELLOW**: ship with attribution and additional license review; constrain caching/redistribution as needed.
- **RED**: do not ship without commercial terms or **BYOL** (customer supplies their key and agrees to terms).

### BYOL
Bring-your-own-license connectors:
- Keys are tenant-scoped secrets.
- Provider calls are allowed only when the tenant has configured credentials and accepted terms.
- Cache/storage behavior must default to the strictest interpretation (often “no-store” unless explicitly permitted).

## 3) Adapter contract

### Interface (language-agnostic)

Each connector MUST implement:

- `provider_id: str`
- `rights_class: RightsClass`
- `cache_policy: CachePolicy`
- `fetch(request: EnrichmentRequest, ctx: EnrichmentContext) -> EnrichmentResult`

Where:

- `EnrichmentRequest`
  - `tenant_id`
  - `entity_type` (e.g., COMPANY, PERSON, DEAL)
  - `query` (structured: identifiers + free text)
  - `requested_fields` (optional)
  - `purpose` (e.g., “SANAD_EVIDENCE”, “KYC”, “DUE_DILIGENCE”)

- `EnrichmentContext`
  - timeouts, retries
  - tracing/audit correlation ids
  - access to http client, cache, persistence

- `EnrichmentResult`
  - `status` (HIT, MISS, ERROR, BLOCKED_RIGHTS, BLOCKED_MISSING_BYOL)
  - `normalized` (dict / pydantic model)
  - `provenance` (source, timestamps, identifiers used, confidence)
  - `raw` (optional, only if allowed by policy)
  - `expires_at` (derived from cache policy)

## 4) Caching policy

### Cache keys
Cache MUST be scoped by:
- `tenant_id`
- `provider_id`
- stable hash of request query (normalized)

### TTL
TTL is defined per provider (and sometimes per endpoint). Default to:
- short TTL for volatile sources
- long TTL for stable registries (e.g., LEI)

### No-store mode
If rights class/policy forbids caching:
- allow **in-memory per-request memoization** only
- do not write to Redis or Postgres
- do not persist raw payloads

## 5) Persistence model (recommended)

Two-layer persistence:

1. **Fact table(s)** (Postgres): normalized fields, provenance, request hash, fetched_at, expires_at
2. **Raw payload store** (object storage): only for providers/policies that allow it; store encrypted, referenced by hash

Minimal tables:
- `enrichment_fetches` (immutable log): provider_id, request_hash, status, fetched_at, expires_at, provenance json, raw_ref
- `enrichment_facts_company` (projection): company_id, fields..., source_provider_id, fetched_at

## 6) Audit & compliance

Emit audit events on:
- provider call attempts (including blocked)
- cache hits/misses
- persisted writes (facts and raw payloads)

Audit MUST include:
- tenant_id
- provider_id
- rights_class
- request hash (not raw sensitive query text)

## 7) Testing strategy

- No live external calls in CI.
- Use recorded fixtures (VCR-like) or contract stubs.
- Add “rights gate” tests:
  - GREEN providers allowed without BYOL
  - RED providers blocked without BYOL
  - YELLOW providers require explicit feature flag / allowlist

## 8) Implementation checklist

- `EnrichmentProviderRegistry` describing: rights class, cache policy, required credentials
- `EnrichmentService` orchestrating: rights check → cache → provider call → normalize → persist → audit
- Per-provider connectors under `src/idis/services/enrichment/connectors/`


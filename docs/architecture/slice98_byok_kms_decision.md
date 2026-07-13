# Slice98 Task 6 - BYOK/KMS boundary decision: durable metadata + documented seam

## Decision (locked)

IDIS stores BYOK POLICY METADATA only. Customer key material lives solely in the customer's
KMS and never enters IDIS - not in the database, not in audit events, not in logs, not in
responses. This slice ships NO encryption code, NO fake KMS, and NO cloud KMS SDK.

Evidence basis: the BYOK core (`idis.compliance.byok.BYOKPolicy`) has never held key
material - a validated key ALIAS (a reference into the customer's KMS), a state
(ACTIVE/REVOKED), and timestamps. Enforcement (`require_key_active`) needs only the state;
evidence sidecars and audit events need only the alias hash. There is no secret to encrypt,
so local/mock envelope encryption of the registry would protect nothing, and a fake-KMS
integration would exercise crypto that no production path performs.

## What is durable (migration 0029)

`byok_policies` (one row per tenant, guarded+forced RLS): `key_alias_sha256` (full 64-hex),
`key_alias_length`, `key_state`, `created_at`, `rotated_at`, `revoked_at`. The RAW ALIAS IS
NEVER PERSISTED - raw aliases exist only transiently in process memory during
configure/rotate; every durable, audited, logged, or returned representation is hash+length
(`policy_alias_sha256` / `policy_alias_length` in `compliance/byok.py`). A policy loaded from
Postgres carries `key_alias=""` with the hash fields populated, and every consumer
(`get_key_metadata`, evidence sidecars, audit builders, management responses) resolves
identity through the helpers, so hashes are identical whichever twin served the policy.

## The KMS seam (where a real KMS plugs in later)

The integration point is the `BYOKPolicyStore` seam consulted by `require_key_active` and
`get_key_metadata` (`get_byok_policy_registry()`; Postgres/in-memory twins today). A future
real-KMS deployment implements the same read surface backed by live key-state lookups (e.g.
verifying alias liveness/grants against AWS KMS or Azure Key Vault) without touching the
enforcement call sites or the `ComplianceEnforcedStore` boundary. If IDIS ever stores actual
secret material, the established pattern is the enrichment-credentials envelope
(`idis.persistence.repositories.enrichment_credentials`: AES-GCM under an env-supplied key,
fresh nonce per seal, fail-closed decrypt) - the env-key derivation is precisely where a KMS
data-key call would substitute.

## Rejected alternatives

- Mock/local envelope encryption of the registry: encrypts non-secret metadata; protects
  nothing; adds key-management surface for no reduction in risk.
- Provider abstraction with a fake KMS performing object-byte encryption: real crypto
  plumbing through ingestion/document reads, far beyond "durable registry", unverifiable
  against a real KMS in CI, and misleading about what production actually does.
- Real cloud KMS integration this slice: requires a cloud SDK dependency and live
  credentials CI does not have; deferred to a deployment-driven slice via the seam above.

## Enforcement invariants (proven by tests)

- Resolution failure DENIES: a backend error in the policy store surfaces as 403
  `BYOK_RESOLUTION_FAILED`, never as "no policy = allow" (`require_key_active` treats a
  missing policy as BYOK-not-configured, so the distinction is security-critical).
- Revocation is durable and cross-replica: a key revoked through the management route denies
  Class2/3 storage reads through the real `ComplianceEnforcedStore` path on any instance.
- Scope note: `ComplianceEnforcedStore.head()` defaults to CLASS_1 (object METADATA, not Class2/3
  content), so metadata reads are intentionally NOT BYOK-revocation-gated; `get()`/`put()` remain
  gated for Class2/3.
- Audit-before-write: the core emits its audit-fatal domain event before the registry write;
  a write failure fails the request (500) and leaves no durable state - only the truthful
  records of the attempt.

Proof: `tests/test_slice98_byok_legal_hold_durable.py` and
`tests/test_slice98_byok_legal_hold_postgres.py`.

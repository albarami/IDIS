# Slice98 Task 4 - MFA decision: IdP-enforced, IDIS-verified

## Decision

IDIS builds NO first-party MFA (no TOTP enrollment, no verification codes, no MFA secret
storage). Multi-factor authentication is enforced at the identity provider, per the
existing mandates ("MFA enforced via IdP (MUST)" - docs/07_IDIS_Tech_Stack_v6_3.md:100;
"MFA required" - docs/IDIS_Data_Residency_and_Compliance_Model_v6_3.md:207). IDIS's
responsibility is to VERIFY the IdP's proof and fail closed when verification is required
but absent.

## Enforcement hook (implemented this slice)

- `IDIS_REQUIRE_MFA` (default off): when enabled, `idis.api.auth_sso.validate_jwt`
  requires the token's RFC 8176 `amr` (Authentication Methods References) array to
  intersect the accepted MFA values. The check runs after signature, standard-claim, and
  IDIS-claim validation, before the identity is returned.
- `IDIS_MFA_AMR_VALUES` (default `mfa`): comma-separated accepted `amr` values, normalized
  lowercase. A configured set REPLACES the default (IdPs that emit method tokens such as
  `otp`/`hwk`/`fido` instead of the literal `mfa` are accommodated by configuration).
- Fail-closed matrix: missing `amr`, non-array `amr`, an empty accepted set (config
  error), or no accepted intersection each deny with 401 `mfa_required` and a generic
  message. No token contents, raw claims, or the `amr` array are logged.
- Scope: the flag gates ONLY Bearer/JWT (HUMAN) SSO auth. API-key (SERVICE)
  authentication never passes through JWT validation and is unchanged - MFA is a
  human-session control; service-to-service integrations are governed by API-key
  handling, RBAC, and ABAC.

## Audit

An MFA-required denial emits exactly one `auth.mfa.failed` audit event (severity MEDIUM,
`resource_type` `session` - added to both the Python validator and
`schemas/audit_event.schema.json`). Emission happens at the request boundary
(`OpenAPIValidationMiddleware`), where the request ID and the app's audit sink exist; the
JWT helper itself stays side-effect free and carries only safe identifiers
(tenant_id/actor_id) on the typed `MfaRequiredError`. The event contains no token or
raw-claim material. An audit-sink failure is logged loudly and the request is still
denied - auth fails closed regardless of audit availability.

## Rejected alternatives

- First-party MFA (TOTP/SMS/codes): contradicts the IdP-MUST posture, adds secret
  storage and recovery flows IDIS does not need, duplicates the IdP's job.
- `acr`-based assurance levels: `acr` values are IdP-specific URIs/levels and less
  uniform than `amr`; can be revisited via configuration if a deployment's IdP only
  expresses step-up through `acr`.
- Custom namespaced claim (e.g. `https://idis.io/mfa`): non-standard and requires
  bespoke IdP claim mapping; `amr` is the standards-based default.

## Proof

`tests/test_slice98_mfa_enforcement.py` - the fail-closed matrix through the REAL
`validate_jwt` (only the signature-crypto boundary mocked), the full-request-path denial
with single schema-valid audit emission and no-claim-material pins, API-key exemption,
and sink-failure-still-denies.

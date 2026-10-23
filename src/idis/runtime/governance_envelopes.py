"""Schema-restricted RFC8785-compatible Ed25519 governance envelopes.

This module deliberately supports only the JSON value domain used by the four
reviewed governance schemas. It does not claim to implement general ECMAScript
number serialization: floating-point values are rejected before canonicalization.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

GOVERNANCE_SIGNATURE_INVALID = "GOVERNANCE_SIGNATURE_INVALID"
GOVERNANCE_SCHEMA_INVALID = "GOVERNANCE_SCHEMA_INVALID"
GOVERNANCE_SIGNER_UNTRUSTED = "GOVERNANCE_SIGNER_UNTRUSTED"

RIGHTS_SCHEMA_VERSION = "enrichment-rights-approvals-v1"
BUDGET_SCHEMA_VERSION = "provider-budget-policy-v1"
NO_SUBSTANTIVE_CONTENT_ATTESTATION_SCHEMA_VERSION = "document-no-substantive-content-attestation-v1"
WATERMARK_BATCH_ATTESTATION_SCHEMA_VERSION = (
    "document-no-substantive-content-watermark-batch-attestation-v1"
)

_ENVELOPE_FIELDS = frozenset({"schema_version", "signer_id", "payload", "signature_base64"})
_SIGNABLE_FIELDS = frozenset({"schema_version", "signer_id", "payload"})
_RIGHTS_PAYLOAD_FIELDS = frozenset({"tenant_id", "approvals"})
_RIGHTS_ROW_FIELDS = frozenset(
    {
        "provider_id",
        "environment",
        "terms_version_hash",
        "status",
        "rights_class",
        "license_scope",
        "attribution_policy_version",
        "no_store_policy_version",
        "evidence_hash",
        "approved_at",
        "expires_at",
        "revoked_at",
        "credential_version_id",
    }
)
_RIGHTS_PROVIDER_CONTRACT = {
    "finnhub": ("RED", "commercial_prod", True),
    "fmp": ("RED", "commercial_prod", True),
    "wayback": ("YELLOW", "attribution_redistribution", False),
    "google_news_rss": ("YELLOW", "attribution_redistribution", False),
}
_NO_SUBSTANTIVE_CONTENT_PAYLOAD_FIELDS = frozenset(
    {
        "tenant_id",
        "source_tree_fingerprint",
        "safe_alias",
        "content_sha256",
        "disposition",
        "variant",
        "attestation_text_sha256",
        "reviewer_approval",
    }
)
_NO_SUBSTANTIVE_CONTENT_REVIEWER_FIELDS = frozenset(
    {"decision", "reviewer_id", "approved_at", "expires_at"}
)
NO_SUBSTANTIVE_CONTENT_VARIANTS = frozenset({"blank", "illegible", "watermark_only"})
_WATERMARK_BATCH_PAYLOAD_FIELDS = frozenset(
    {
        "tenant_id",
        "source_tree_fingerprint",
        "disposition",
        "variant",
        "classifier_policy_id",
        "classifier_policy_sha256",
        "exemplar_safe_alias",
        "exemplar_content_sha256",
        "exemplar_envelope_sha256",
        "exemplar_canonical_bitmap_sha256",
        "prescan_receipt_sha256",
        "candidate_set_sha256",
        "candidates",
        "sample_set_sha256",
        "attestation_text_sha256",
        "reviewer_approval",
    }
)
_WATERMARK_BATCH_CANDIDATE_FIELDS = frozenset(
    {
        "safe_alias",
        "content_sha256",
        "page_number",
        "canonical_render_sha256",
        "similarity_score",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ALIAS_RE = re.compile(r"^company-00[12]-document-[0-9]{5}\.pdf$")
_REVIEWER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_SIMILARITY_SCORE_RE = re.compile(r"^(?:0\.[0-9]{6}|1\.000000)$")
_MAX_SAFE_INTEGER = (1 << 53) - 1


class GovernanceEnvelopeError(ValueError):
    """Safe typed failure for governance envelope operations."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class VerifiedGovernanceEnvelope:
    """Verified envelope metadata safe to persist or audit."""

    schema_version: str
    signer_id: str
    payload: Mapping[str, object]
    payload_sha256: str
    envelope_sha256: str
    public_key_sha256: str


def _schema_error() -> GovernanceEnvelopeError:
    return GovernanceEnvelopeError(GOVERNANCE_SCHEMA_INVALID)


def _validate_string(value: str) -> None:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise _schema_error()


def _escape_string(value: str) -> str:
    _validate_string(value)
    pieces = ['"']
    short_escapes = {
        "\b": "\\b",
        "\t": "\\t",
        "\n": "\\n",
        "\f": "\\f",
        "\r": "\\r",
    }
    for character in value:
        codepoint = ord(character)
        if character == '"':
            pieces.append('\\"')
        elif character == "\\":
            pieces.append("\\\\")
        elif character in short_escapes:
            pieces.append(short_escapes[character])
        elif codepoint <= 0x1F:
            pieces.append(f"\\u{codepoint:04x}")
        else:
            pieces.append(character)
    pieces.append('"')
    return "".join(pieces)


def _utf16_sort_key(value: str) -> bytes:
    _validate_string(value)
    return value.encode("utf-16-be")


def _canonical_text(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if not -_MAX_SAFE_INTEGER <= value <= _MAX_SAFE_INTEGER:
            raise _schema_error()
        return str(value)
    if isinstance(value, float):
        raise _schema_error()
    if isinstance(value, str):
        return _escape_string(value)
    if isinstance(value, list):
        return "[" + ",".join(_canonical_text(item) for item in value) + "]"
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise _schema_error()
        keys = sorted(value, key=_utf16_sort_key)
        return (
            "{"
            + ",".join(f"{_escape_string(key)}:{_canonical_text(value[key])}" for key in keys)
            + "}"
        )
    raise _schema_error()


def canonicalize_json(value: object) -> bytes:
    """Canonicalize the restricted governance JSON domain to UTF-8 bytes."""

    return _canonical_text(value).encode("utf-8")


def _canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value.lower()
    except ValueError:
        return False


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _validate_rights_payload(payload: object) -> None:
    if not isinstance(payload, Mapping) or frozenset(payload) != _RIGHTS_PAYLOAD_FIELDS:
        raise _schema_error()
    if not _canonical_uuid(payload.get("tenant_id")):
        raise _schema_error()
    approvals = payload.get("approvals")
    if not isinstance(approvals, list) or len(approvals) != len(_RIGHTS_PROVIDER_CONTRACT):
        raise _schema_error()
    seen: set[str] = set()
    for approval in approvals:
        if not isinstance(approval, Mapping) or frozenset(approval) != _RIGHTS_ROW_FIELDS:
            raise _schema_error()
        provider_id = approval.get("provider_id")
        if not isinstance(provider_id, str) or provider_id in seen:
            raise _schema_error()
        contract = _RIGHTS_PROVIDER_CONTRACT.get(provider_id)
        if contract is None:
            raise _schema_error()
        seen.add(provider_id)
        rights_class, license_scope, credential_required = contract
        status = approval.get("status")
        if (
            approval.get("environment") != "PROD"
            or status not in {"APPROVED", "REVOKED"}
            or approval.get("rights_class") != rights_class
            or approval.get("license_scope") != license_scope
        ):
            raise _schema_error()
        if not all(
            isinstance(approval.get(field), str) and bool(approval.get(field))
            for field in ("attribution_policy_version", "no_store_policy_version")
        ):
            raise _schema_error()
        if not all(
            isinstance(approval.get(field), str)
            and _SHA256_RE.fullmatch(str(approval.get(field))) is not None
            for field in ("terms_version_hash", "evidence_hash")
        ):
            raise _schema_error()
        approved_at = _parse_timestamp(approval.get("approved_at"))
        expires_at = _parse_timestamp(approval.get("expires_at"))
        if approved_at is None or expires_at is None or expires_at <= approved_at:
            raise _schema_error()
        raw_revoked_at = approval.get("revoked_at")
        revoked_at = None if raw_revoked_at is None else _parse_timestamp(raw_revoked_at)
        if (status == "APPROVED" and raw_revoked_at is not None) or (
            status == "REVOKED" and (revoked_at is None or revoked_at < approved_at)
        ):
            raise _schema_error()
        credential_version = approval.get("credential_version_id")
        if credential_required:
            if not _canonical_uuid(credential_version):
                raise _schema_error()
        elif credential_version is not None:
            raise _schema_error()
    if seen != set(_RIGHTS_PROVIDER_CONTRACT):
        raise _schema_error()


def _validate_no_substantive_content_attestation_payload(payload: object) -> None:
    """Validate the exact signed one-document terminal-exclusion payload."""

    if (
        not isinstance(payload, Mapping)
        or frozenset(payload) != _NO_SUBSTANTIVE_CONTENT_PAYLOAD_FIELDS
    ):
        raise _schema_error()
    tenant_id = payload.get("tenant_id")
    if (
        not _canonical_uuid(tenant_id)
        or not isinstance(tenant_id, str)
        or tenant_id != tenant_id.lower()
    ):
        raise _schema_error()
    if not all(
        isinstance(payload.get(field), str)
        and _SHA256_RE.fullmatch(str(payload.get(field))) is not None
        for field in (
            "source_tree_fingerprint",
            "content_sha256",
            "attestation_text_sha256",
        )
    ):
        raise _schema_error()
    safe_alias = payload.get("safe_alias")
    if not isinstance(safe_alias, str) or _SAFE_ALIAS_RE.fullmatch(safe_alias) is None:
        raise _schema_error()
    variant = payload.get("variant")
    if (
        payload.get("disposition") != "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"
        or not isinstance(variant, str)
        or variant not in NO_SUBSTANTIVE_CONTENT_VARIANTS
    ):
        raise _schema_error()

    approval = payload.get("reviewer_approval")
    if (
        not isinstance(approval, Mapping)
        or frozenset(approval) != _NO_SUBSTANTIVE_CONTENT_REVIEWER_FIELDS
    ):
        raise _schema_error()
    reviewer_id = approval.get("reviewer_id")
    if (
        approval.get("decision") != "APPROVED"
        or not isinstance(reviewer_id, str)
        or _REVIEWER_ID_RE.fullmatch(reviewer_id) is None
    ):
        raise _schema_error()
    approved_at = _parse_timestamp(approval.get("approved_at"))
    expires_at = _parse_timestamp(approval.get("expires_at"))
    if approved_at is None or expires_at is None or expires_at <= approved_at:
        raise _schema_error()


def _valid_sha256_field(payload: Mapping[str, object], field: str) -> bool:
    value = payload.get(field)
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _valid_reviewer_approval(value: object) -> bool:
    if (
        not isinstance(value, Mapping)
        or frozenset(value) != _NO_SUBSTANTIVE_CONTENT_REVIEWER_FIELDS
    ):
        return False
    reviewer_id = value.get("reviewer_id")
    approved_at = _parse_timestamp(value.get("approved_at"))
    expires_at = _parse_timestamp(value.get("expires_at"))
    return (
        value.get("decision") == "APPROVED"
        and isinstance(reviewer_id, str)
        and _REVIEWER_ID_RE.fullmatch(reviewer_id) is not None
        and approved_at is not None
        and expires_at is not None
        and expires_at > approved_at
    )


def _watermark_candidate_order_key(candidate: Mapping[str, object]) -> tuple[object, ...]:
    return (
        candidate["safe_alias"],
        candidate["content_sha256"],
        candidate["page_number"],
        candidate["canonical_render_sha256"],
        candidate["similarity_score"],
    )


def _validate_watermark_batch_attestation_payload(payload: object) -> None:
    """Validate the exact signed candidate-batch governance payload."""

    if not isinstance(payload, Mapping) or frozenset(payload) != _WATERMARK_BATCH_PAYLOAD_FIELDS:
        raise _schema_error()
    tenant_id = payload.get("tenant_id")
    if (
        not _canonical_uuid(tenant_id)
        or not isinstance(tenant_id, str)
        or tenant_id != tenant_id.lower()
        or payload.get("disposition") != "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"
        or payload.get("variant") != "watermark_only"
        or payload.get("classifier_policy_id") != "exemplar-watermark-page-v1"
        or not _valid_reviewer_approval(payload.get("reviewer_approval"))
    ):
        raise _schema_error()
    if not all(
        _valid_sha256_field(payload, field)
        for field in (
            "source_tree_fingerprint",
            "classifier_policy_sha256",
            "exemplar_content_sha256",
            "exemplar_envelope_sha256",
            "exemplar_canonical_bitmap_sha256",
            "prescan_receipt_sha256",
            "candidate_set_sha256",
            "sample_set_sha256",
            "attestation_text_sha256",
        )
    ):
        raise _schema_error()
    exemplar_alias = payload.get("exemplar_safe_alias")
    if not isinstance(exemplar_alias, str) or _SAFE_ALIAS_RE.fullmatch(exemplar_alias) is None:
        raise _schema_error()

    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates or len(candidates) > 267:
        raise _schema_error()
    validated: list[Mapping[str, object]] = []
    identities: set[tuple[str, str, int]] = set()
    for candidate in candidates:
        if (
            not isinstance(candidate, Mapping)
            or frozenset(candidate) != _WATERMARK_BATCH_CANDIDATE_FIELDS
        ):
            raise _schema_error()
        safe_alias = candidate.get("safe_alias")
        page_number = candidate.get("page_number")
        similarity_score = candidate.get("similarity_score")
        if (
            not isinstance(safe_alias, str)
            or _SAFE_ALIAS_RE.fullmatch(safe_alias) is None
            or not _valid_sha256_field(candidate, "content_sha256")
            or not _valid_sha256_field(candidate, "canonical_render_sha256")
            or isinstance(page_number, bool)
            or not isinstance(page_number, int)
            or page_number < 1
            or not isinstance(similarity_score, str)
            or _SIMILARITY_SCORE_RE.fullmatch(similarity_score) is None
            or not (similarity_score == "1.000000" or similarity_score >= "0.800000")
        ):
            raise _schema_error()
        content_sha256 = candidate["content_sha256"]
        assert isinstance(content_sha256, str)
        identity = (safe_alias, content_sha256, page_number)
        if identity in identities:
            raise _schema_error()
        identities.add(identity)
        validated.append(candidate)
    if validated != sorted(validated, key=_watermark_candidate_order_key):
        raise _schema_error()


def _validate_payload(schema_version: object, payload: object) -> Mapping[str, object]:
    if not isinstance(schema_version, str) or not isinstance(payload, Mapping):
        raise _schema_error()
    canonicalize_json(payload)
    if schema_version == RIGHTS_SCHEMA_VERSION:
        _validate_rights_payload(payload)
    elif schema_version == BUDGET_SCHEMA_VERSION:
        try:
            from idis.providers.budget_policy import validate_provider_budget_policy_payload

            validate_provider_budget_policy_payload(payload)
        except GovernanceEnvelopeError:
            raise
        except Exception as exc:
            raise _schema_error() from exc
    elif schema_version == NO_SUBSTANTIVE_CONTENT_ATTESTATION_SCHEMA_VERSION:
        _validate_no_substantive_content_attestation_payload(payload)
    elif schema_version == WATERMARK_BATCH_ATTESTATION_SCHEMA_VERSION:
        _validate_watermark_batch_attestation_payload(payload)
    else:
        raise _schema_error()
    return payload


def sign_envelope(
    *,
    schema_version: str,
    signer_id: str,
    payload: Mapping[str, object],
    private_key_pem: bytes,
) -> dict[str, object]:
    """Validate and sign one reviewed governance envelope."""

    if not isinstance(signer_id, str) or not signer_id:
        raise _schema_error()
    _validate_string(signer_id)
    _validate_payload(schema_version, payload)
    signable: dict[str, object] = {
        "schema_version": schema_version,
        "signer_id": signer_id,
        "payload": payload,
    }
    try:
        private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    except (TypeError, ValueError) as exc:
        raise GovernanceEnvelopeError(GOVERNANCE_SIGNATURE_INVALID) from exc
    if not isinstance(private_key, Ed25519PrivateKey):
        raise GovernanceEnvelopeError(GOVERNANCE_SIGNATURE_INVALID)
    signature = private_key.sign(canonicalize_json(signable))
    return {
        **signable,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }


def verify_envelope(
    *,
    envelope: Mapping[str, object],
    trusted_signer_id: str,
    trusted_public_key_pem: bytes,
) -> VerifiedGovernanceEnvelope:
    """Verify schema, signer, Ed25519 signature, and safe hashes."""

    if not isinstance(envelope, Mapping) or frozenset(envelope) != _ENVELOPE_FIELDS:
        raise _schema_error()
    schema_version = envelope.get("schema_version")
    signer_id = envelope.get("signer_id")
    signature_base64 = envelope.get("signature_base64")
    payload = envelope.get("payload")
    if (
        not isinstance(schema_version, str)
        or not isinstance(signer_id, str)
        or not isinstance(signature_base64, str)
    ):
        raise _schema_error()
    _validate_payload(schema_version, payload)
    if not isinstance(trusted_signer_id, str) or not hmac.compare_digest(
        signer_id, trusted_signer_id
    ):
        raise GovernanceEnvelopeError(GOVERNANCE_SIGNER_UNTRUSTED)
    try:
        signature = base64.b64decode(signature_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise GovernanceEnvelopeError(GOVERNANCE_SIGNATURE_INVALID) from exc
    if len(signature) != 64:
        raise GovernanceEnvelopeError(GOVERNANCE_SIGNATURE_INVALID)
    try:
        public_key = serialization.load_pem_public_key(trusted_public_key_pem)
    except (TypeError, ValueError) as exc:
        raise GovernanceEnvelopeError(GOVERNANCE_SIGNATURE_INVALID) from exc
    if not isinstance(public_key, Ed25519PublicKey):
        raise GovernanceEnvelopeError(GOVERNANCE_SIGNATURE_INVALID)
    signable = {field: envelope[field] for field in _SIGNABLE_FIELDS}
    try:
        public_key.verify(signature, canonicalize_json(signable))
    except InvalidSignature as exc:
        raise GovernanceEnvelopeError(GOVERNANCE_SIGNATURE_INVALID) from exc
    assert isinstance(payload, Mapping)
    return VerifiedGovernanceEnvelope(
        schema_version=schema_version,
        signer_id=signer_id,
        payload=payload,
        payload_sha256=hashlib.sha256(canonicalize_json(payload)).hexdigest(),
        envelope_sha256=hashlib.sha256(canonicalize_json(envelope)).hexdigest(),
        public_key_sha256=hashlib.sha256(trusted_public_key_pem).hexdigest(),
    )


__all__ = [
    "BUDGET_SCHEMA_VERSION",
    "GOVERNANCE_SCHEMA_INVALID",
    "GOVERNANCE_SIGNATURE_INVALID",
    "GOVERNANCE_SIGNER_UNTRUSTED",
    "GovernanceEnvelopeError",
    "NO_SUBSTANTIVE_CONTENT_ATTESTATION_SCHEMA_VERSION",
    "NO_SUBSTANTIVE_CONTENT_VARIANTS",
    "RIGHTS_SCHEMA_VERSION",
    "VerifiedGovernanceEnvelope",
    "WATERMARK_BATCH_ATTESTATION_SCHEMA_VERSION",
    "canonicalize_json",
    "sign_envelope",
    "verify_envelope",
]

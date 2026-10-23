"""RED contract for the truthful no-substantive-content terminal disposition."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from idis.runtime import governance_envelopes
from idis.services.ingestion import no_substantive_content_attestation as attestation

SCHEMA_VERSION = "document-no-substantive-content-attestation-v1"
RECEIPT_SCHEMA_VERSION = "document-no-substantive-content-receipt-v1"
DISPOSITION = "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"
MANIFEST_PATH_ENV = "IDIS_REAL_EXAMPLE_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_PATH"
VARIANTS = frozenset({"blank", "illegible", "watermark_only"})
REVIEWER_STATEMENT = (
    "Diagonal VDR confidentiality stamp (recipient email + timestamp) only; no substantive "
    "content; determined visually by reviewer after deterministic contrast enhancement, "
    "2026-07-17."
)
REVIEWER_STATEMENT_SHA256 = "2444a956745c250d6e4189dc1d65d72ca79dd12eae9e60d43149dc40eb850b2a"


def _private_pem() -> bytes:
    return Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _payload(*, variant: str = "watermark_only") -> dict[str, object]:
    return {
        "tenant_id": "30000000-0000-4000-8000-000000000001",
        "source_tree_fingerprint": "1" * 64,
        "safe_alias": "company-002-document-00053.pdf",
        "content_sha256": "2" * 64,
        "disposition": DISPOSITION,
        "variant": variant,
        "attestation_text_sha256": REVIEWER_STATEMENT_SHA256,
        "reviewer_approval": {
            "decision": "APPROVED",
            "reviewer_id": "fictional-reviewer-001",
            "approved_at": "2026-07-17T10:00:00Z",
            "expires_at": "2026-07-18T10:00:00Z",
        },
    }


def test_reviewer_statement_hash_is_exact_and_contains_no_stamp_values() -> None:
    assert hashlib.sha256(REVIEWER_STATEMENT.encode("utf-8")).hexdigest() == (
        REVIEWER_STATEMENT_SHA256
    )
    assert "@" not in REVIEWER_STATEMENT
    assert "T00:" not in REVIEWER_STATEMENT


def test_truthful_contract_replaces_illegible_only_symbols() -> None:
    assert governance_envelopes.NO_SUBSTANTIVE_CONTENT_ATTESTATION_SCHEMA_VERSION == (
        SCHEMA_VERSION
    )
    assert not hasattr(governance_envelopes, "ILLEGIBILITY_ATTESTATION_SCHEMA_VERSION")
    assert attestation.NO_SUBSTANTIVE_CONTENT_ATTESTATION_PATH_ENV == MANIFEST_PATH_ENV
    assert attestation.NO_SUBSTANTIVE_CONTENT_RECEIPT_SCHEMA_VERSION == RECEIPT_SCHEMA_VERSION
    assert attestation.DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED == DISPOSITION
    assert attestation.NO_SUBSTANTIVE_CONTENT_VARIANTS == VARIANTS
    assert not hasattr(attestation, "DOCUMENT_ILLEGIBLE_CONFIRMED")


@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_governance_signer_accepts_each_exact_reviewed_variant(variant: str) -> None:
    envelope = governance_envelopes.sign_envelope(
        schema_version=SCHEMA_VERSION,
        signer_id="fictional-governance-signer",
        payload=_payload(variant=variant),
        private_key_pem=_private_pem(),
    )

    assert envelope["payload"] == _payload(variant=variant)


@pytest.mark.parametrize("variant", ["watermark", "watermark-only", "", "unknown"])
def test_governance_signer_rejects_unknown_variant(variant: str) -> None:
    with pytest.raises(governance_envelopes.GovernanceEnvelopeError) as caught:
        governance_envelopes.sign_envelope(
            schema_version=SCHEMA_VERSION,
            signer_id="fictional-governance-signer",
            payload=_payload(variant=variant),
            private_key_pem=_private_pem(),
        )

    assert caught.value.code == governance_envelopes.GOVERNANCE_SCHEMA_INVALID


def test_superseded_illegible_only_schema_and_disposition_are_rejected() -> None:
    old_payload = _payload()
    old_payload.pop("variant")
    old_payload["disposition"] = "DOCUMENT_ILLEGIBLE_CONFIRMED"

    with pytest.raises(governance_envelopes.GovernanceEnvelopeError):
        governance_envelopes.sign_envelope(
            schema_version="document-illegibility-attestation-v1",
            signer_id="fictional-governance-signer",
            payload=old_payload,
            private_key_pem=_private_pem(),
        )


def test_openapi_and_example_env_publish_only_truthful_names() -> None:
    root = Path(__file__).resolve().parents[2]
    openapi = (root / "openapi" / "IDIS_OpenAPI_v6_3.yaml").read_text(encoding="utf-8")
    env_example = (root / ".env.example").read_text(encoding="utf-8")

    assert DISPOSITION in openapi
    assert "DOCUMENT_ILLEGIBLE_CONFIRMED" not in openapi
    assert MANIFEST_PATH_ENV in env_example
    assert "IDIS_REAL_EXAMPLE_ILLEGIBILITY_ATTESTATION_MANIFEST_PATH" not in env_example

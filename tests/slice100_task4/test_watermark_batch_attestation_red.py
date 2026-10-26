"""RED contracts for a signed, exemplar-bound watermark candidate batch.

All fixture identities, hashes, review text, and paths are fictional.  The
existing one-document v1 envelope remains a supported compatibility input;
the new v2 manifest binds that signed exemplar to exactly one signed batch.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib
import json
from datetime import UTC, datetime
from functools import lru_cache
from types import ModuleType
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from idis.runtime import governance_envelopes
from idis.runtime.governance_envelopes import canonicalize_json
from tests._linux_poppler_gate import requires_linux_governed_poppler

MANIFEST_SCHEMA_VERSION = "document-no-substantive-content-attestation-manifest-v2"
BATCH_SCHEMA_VERSION = "document-no-substantive-content-watermark-batch-attestation-v1"
EXEMPLAR_SCHEMA_VERSION = "document-no-substantive-content-attestation-v1"
SIGNER_ID = "fictional-watermark-governance-signer-v1"
TENANT_ID = "30000000-0000-4000-8000-000000000001"
SOURCE_TREE_FINGERPRINT = "1" * 64
POLICY_VERSION = "exemplar-watermark-page-v1"
POLICY_SHA256 = "2" * 64
ATTESTATION_TEXT_SHA256 = "4" * 64
EXEMPLAR_ALIAS = "company-002-document-00053.pdf"
MANDATORY_ALIAS = "company-002-document-00054.pdf"
APPROVED_AT = "2026-07-17T09:00:00Z"
EXPIRES_AT = "2026-10-15T00:00:00Z"
ACTIVE_NOW = datetime(2026, 7, 18, tzinfo=UTC)

PRIVATE_PATH_CANARY = r"C:\fictional-private-room\private-watermark-page.pdf"
PRIVATE_TEXT_CANARY = "fictional recipient address and confidential timestamp"
PRIVATE_BITMAP_SEED_CANARY = "fictional-packed-canonical-bitmap-private"

BATCH_PAYLOAD_FIELDS = (
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
)
CANDIDATE_FIELDS = (
    "safe_alias",
    "content_sha256",
    "page_number",
    "canonical_render_sha256",
    "similarity_score",
)
MANIFEST_FIELDS = (
    "schema_version",
    "exemplar_attestation_envelope",
    "exemplar_canonical_bitmap_base64",
    "watermark_prescan_receipt",
    "watermark_batch_attestation_envelope",
)


def _target() -> ModuleType:
    name = "idis.services.ingestion.no_substantive_content_attestation"
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        missing = str(exc)
    pytest.fail(f"TASK4_WATERMARK_BATCH_API_MISSING: {name}: {missing}", pytrace=False)


def _required_attr(name: str) -> Any:
    module = _target()
    value = getattr(module, name, None)
    if value is None:
        pytest.fail(
            f"TASK4_WATERMARK_BATCH_API_MISSING: {module.__name__}.{name}",
            pytrace=False,
        )
    return value


def _error_type() -> type[Exception]:
    value = _required_attr("DocumentNoSubstantiveContentAttestationError")
    assert isinstance(value, type) and issubclass(value, Exception)
    return value


def _code(error: BaseException) -> str | None:
    value = getattr(error, "code", None)
    return str(value) if value is not None else None


def _private_and_public_pem() -> tuple[bytes, bytes]:
    private_key = Ed25519PrivateKey.generate()
    return (
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
    )


def _sign(
    *, schema_version: str, payload: dict[str, object], private_pem: bytes
) -> dict[str, object]:
    """Sign directly so malformed-payload tests reach manifest verification."""

    private_key = serialization.load_pem_private_key(private_pem, password=None)
    assert isinstance(private_key, Ed25519PrivateKey)
    signable: dict[str, object] = {
        "schema_version": schema_version,
        "signer_id": SIGNER_ID,
        "payload": payload,
    }
    return {
        **signable,
        "signature_base64": base64.b64encode(private_key.sign(canonicalize_json(signable))).decode(
            "ascii"
        ),
    }


def _candidate(
    alias: str,
    *,
    content_digit: str,
    render_digit: str,
    score: str,
    page_number: int = 1,
) -> dict[str, object]:
    return {
        "safe_alias": alias,
        "content_sha256": content_digit * 64,
        "page_number": page_number,
        "canonical_render_sha256": render_digit * 64,
        "similarity_score": score,
    }


def _candidates() -> list[dict[str, object]]:
    return [
        {
            "safe_alias": EXEMPLAR_ALIAS,
            "content_sha256": _exemplar_content_sha256(),
            "page_number": 1,
            "canonical_render_sha256": hashlib.sha256(_canonical_bitmap()).hexdigest(),
            "similarity_score": "1.000000",
        },
        _candidate(
            MANDATORY_ALIAS,
            content_digit="9",
            render_digit="a",
            score="0.819404",
        ),
    ]


def _sha256(value: object) -> str:
    return hashlib.sha256(canonicalize_json(value)).hexdigest()


@lru_cache(maxsize=1)
def _exemplar_pdf_bytes() -> bytes:
    from tests.slice100_task4.test_watermark_composition_root_red import _fixture_pdf

    return _fixture_pdf()


def _exemplar_content_sha256() -> str:
    return hashlib.sha256(_exemplar_pdf_bytes()).hexdigest()


def _exemplar_payload() -> dict[str, object]:
    return {
        "tenant_id": TENANT_ID,
        "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        "safe_alias": EXEMPLAR_ALIAS,
        "content_sha256": _exemplar_content_sha256(),
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": "watermark_only",
        "attestation_text_sha256": "b" * 64,
        "reviewer_approval": {
            "decision": "APPROVED",
            "reviewer_id": "fictional-reviewer-v1",
            "approved_at": APPROVED_AT,
            "expires_at": EXPIRES_AT,
        },
    }


@lru_cache(maxsize=1)
def _canonical_bitmap() -> bytes:
    """Build the valid base exemplar through the real deterministic raster pipeline."""

    # Mutation tests below may deliberately corrupt this value, but every valid
    # manifest starts with a project-authored PDF rendered at 200 DPI and then
    # downscaled, binarized, and one-bit packed exactly like the signed runtime
    # exemplar.  Arbitrary 2,048-byte strings are not valid fixture exemplars.
    from pdf2image import convert_from_bytes

    from idis.parsers.poppler_runtime import resolve_pinned_poppler_runtime
    from tests.slice100_task4.test_watermark_composition_root_red import (
        _packed_canonical_bitmap,
    )

    binding = resolve_pinned_poppler_runtime()
    pages = convert_from_bytes(
        _exemplar_pdf_bytes(),
        dpi=200,
        first_page=1,
        last_page=1,
        fmt="png",
        thread_count=1,
        poppler_path=str(binding.binary_dir),
    )
    assert len(pages) == 1
    with pages[0] as rendered_page:
        return _packed_canonical_bitmap(rendered_page)


def _prescan_receipt(
    *,
    status: str = "COMPLETE",
    unresolved_count: int = 0,
) -> dict[str, object]:
    candidates = _candidates()
    return {
        "schema_version": "watermark-page-prescan-v1",
        "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        "classifier_policy_id": POLICY_VERSION,
        "classifier_policy_sha256": POLICY_SHA256,
        "total_file_count": 267,
        "company_file_counts": {"company-001": 58, "company-002": 209},
        "candidate_count": len(candidates),
        "candidate_set_sha256": _sha256(candidates),
        "sample_set_sha256": _sha256(candidates),
        "unresolved_count": unresolved_count,
        "status": status,
        "blocker_code": None if status == "COMPLETE" else "WATERMARK_PRESCAN_INCOMPLETE",
    }


def _batch_payload(
    *,
    exemplar_envelope_sha256: str,
    exemplar_bitmap_sha256: str,
    prescan_receipt: dict[str, object] | None = None,
) -> dict[str, object]:
    candidates = _candidates()
    exact_prescan_receipt = prescan_receipt or _prescan_receipt()
    return {
        "tenant_id": TENANT_ID,
        "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": "watermark_only",
        "classifier_policy_id": POLICY_VERSION,
        "classifier_policy_sha256": POLICY_SHA256,
        "exemplar_safe_alias": EXEMPLAR_ALIAS,
        "exemplar_content_sha256": _exemplar_content_sha256(),
        "exemplar_envelope_sha256": exemplar_envelope_sha256,
        "exemplar_canonical_bitmap_sha256": exemplar_bitmap_sha256,
        "prescan_receipt_sha256": _sha256(exact_prescan_receipt),
        "candidate_set_sha256": _sha256(candidates),
        "candidates": candidates,
        "sample_set_sha256": _sha256(candidates),
        "attestation_text_sha256": ATTESTATION_TEXT_SHA256,
        "reviewer_approval": {
            "decision": "APPROVED",
            "reviewer_id": "fictional-reviewer-v1",
            "approved_at": APPROVED_AT,
            "expires_at": EXPIRES_AT,
        },
    }


def _manifest(
    private_pem: bytes,
    *,
    batch_payload: dict[str, object] | None = None,
    prescan_receipt: dict[str, object] | None = None,
) -> dict[str, object]:
    exemplar = _sign(
        schema_version=EXEMPLAR_SCHEMA_VERSION,
        payload=_exemplar_payload(),
        private_pem=private_pem,
    )
    canonical_bitmap = _canonical_bitmap()
    exact_prescan_receipt = prescan_receipt or _prescan_receipt()
    exact_batch_payload = batch_payload or _batch_payload(
        exemplar_envelope_sha256=_sha256(exemplar),
        exemplar_bitmap_sha256=hashlib.sha256(canonical_bitmap).hexdigest(),
        prescan_receipt=exact_prescan_receipt,
    )
    batch = _sign(
        schema_version=BATCH_SCHEMA_VERSION,
        payload=exact_batch_payload,
        private_pem=private_pem,
    )
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "exemplar_attestation_envelope": exemplar,
        "exemplar_canonical_bitmap_base64": base64.b64encode(canonical_bitmap).decode("ascii"),
        "watermark_prescan_receipt": exact_prescan_receipt,
        "watermark_batch_attestation_envelope": batch,
    }


def _valid_batch_payload(private_pem: bytes) -> dict[str, object]:
    exemplar = _sign(
        schema_version=EXEMPLAR_SCHEMA_VERSION,
        payload=_exemplar_payload(),
        private_pem=private_pem,
    )
    return _batch_payload(
        exemplar_envelope_sha256=_sha256(exemplar),
        exemplar_bitmap_sha256=hashlib.sha256(_canonical_bitmap()).hexdigest(),
    )


def _verify(
    manifest: dict[str, object],
    *,
    public_pem: bytes,
    trusted_signer_id: str = SIGNER_ID,
    now: datetime = ACTIVE_NOW,
) -> object:
    _required_attr("NO_SUBSTANTIVE_CONTENT_MANIFEST_SCHEMA_VERSION")
    return _required_attr("verify_document_no_substantive_content_attestation_manifest")(
        manifest=manifest,
        trusted_signer_id=trusted_signer_id,
        trusted_public_key_pem=public_pem,
        now=now,
    )


@requires_linux_governed_poppler
def test_batch_contract_pins_exact_schema_fields_order_and_governance_signing() -> None:
    module = _target()
    assert _required_attr("NO_SUBSTANTIVE_CONTENT_MANIFEST_SCHEMA_VERSION") == (
        MANIFEST_SCHEMA_VERSION
    )
    assert _required_attr("WATERMARK_BATCH_ATTESTATION_SCHEMA_VERSION") == (BATCH_SCHEMA_VERSION)
    assert tuple(_required_attr("WATERMARK_BATCH_PAYLOAD_FIELDS")) == BATCH_PAYLOAD_FIELDS
    assert tuple(_required_attr("WATERMARK_BATCH_CANDIDATE_FIELDS")) == CANDIDATE_FIELDS
    assert tuple(_required_attr("NO_SUBSTANTIVE_CONTENT_MANIFEST_FIELDS")) == MANIFEST_FIELDS
    assert module.NO_SUBSTANTIVE_CONTENT_ATTESTATION_PATH_ENV == (
        "IDIS_REAL_EXAMPLE_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_PATH"
    )

    private_pem, public_pem = _private_and_public_pem()
    envelope = governance_envelopes.sign_envelope(
        schema_version=BATCH_SCHEMA_VERSION,
        signer_id=SIGNER_ID,
        payload=_valid_batch_payload(private_pem),
        private_key_pem=private_pem,
    )
    verified = governance_envelopes.verify_envelope(
        envelope=envelope,
        trusted_signer_id=SIGNER_ID,
        trusted_public_key_pem=public_pem,
    )
    assert verified.schema_version == BATCH_SCHEMA_VERSION
    assert frozenset(verified.payload) == frozenset(BATCH_PAYLOAD_FIELDS)


@requires_linux_governed_poppler
def test_v2_manifest_verifies_both_envelopes_and_preserves_ordered_digests() -> None:
    private_pem, public_pem = _private_and_public_pem()
    manifest = _manifest(private_pem)

    verified = _verify(manifest, public_pem=public_pem)

    assert verified.schema_version == MANIFEST_SCHEMA_VERSION
    assert verified.batch.schema_version == BATCH_SCHEMA_VERSION
    assert frozenset(verified.batch.payload) == frozenset(BATCH_PAYLOAD_FIELDS)
    assert tuple(verified.batch.candidates) == tuple(_candidates())
    assert verified.batch.candidate_set_sha256 == _sha256(_candidates())
    assert verified.batch.prescan_receipt_sha256 == _sha256(_prescan_receipt())
    assert verified.batch.sample_set_sha256 == _sha256(_candidates())
    assert verified.exemplar.content_sha256 == _exemplar_content_sha256()
    assert verified.exemplar_envelope_sha256 == _sha256(manifest["exemplar_attestation_envelope"])
    assert dict(verified.company_file_counts) == {
        "company-001": 58,
        "company-002": 209,
    }
    runtime_summary = verified.runtime_summary()
    assert runtime_summary["exemplar_safe_alias"] == verified.exemplar.safe_alias
    assert runtime_summary["exemplar_page_number"] == 1
    assert runtime_summary["company_file_counts"] == {
        "company-001": 58,
        "company-002": 209,
    }


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (
            lambda payload: payload["candidates"].reverse(),
            "WATERMARK_BATCH_ATTESTATION_INVALID",
        ),
        (
            lambda payload: payload["candidates"].append(copy.deepcopy(payload["candidates"][0])),
            "WATERMARK_BATCH_ATTESTATION_INVALID",
        ),
        (
            lambda payload: payload.__setitem__("unexpected", True),
            "WATERMARK_BATCH_ATTESTATION_INVALID",
        ),
        (
            lambda payload: payload.pop("sample_set_sha256"),
            "WATERMARK_BATCH_ATTESTATION_INVALID",
        ),
    ],
)
@requires_linux_governed_poppler
def test_batch_rejects_order_duplicates_and_schema_drift(
    mutation: Any,
    expected_code: str,
) -> None:
    private_pem, public_pem = _private_and_public_pem()
    payload = _valid_batch_payload(private_pem)
    mutation(payload)
    # Re-signing proves this is schema/order admission, not signature failure.
    manifest = _manifest(private_pem, batch_payload=payload)

    with pytest.raises(_error_type()) as caught:
        _verify(manifest, public_pem=public_pem)
    assert _code(caught.value) == expected_code


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("candidate_set_sha256", "c" * 64),
        ("prescan_receipt_sha256", "d" * 64),
        ("sample_set_sha256", "e" * 64),
        ("classifier_policy_sha256", "f" * 64),
        ("exemplar_safe_alias", MANDATORY_ALIAS),
        ("exemplar_content_sha256", "0" * 64),
        ("exemplar_envelope_sha256", "a" * 64),
        ("exemplar_canonical_bitmap_sha256", "b" * 64),
    ],
)
@requires_linux_governed_poppler
def test_batch_rejects_digest_or_exemplar_drift(field: str, replacement: str) -> None:
    private_pem, public_pem = _private_and_public_pem()
    payload = _valid_batch_payload(private_pem)
    payload[field] = replacement
    manifest = _manifest(private_pem, batch_payload=payload)

    with pytest.raises(_error_type()) as caught:
        _verify(manifest, public_pem=public_pem)
    assert _code(caught.value) == "WATERMARK_BATCH_ATTESTATION_MISMATCH"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        pytest.param("page_number", 2, id="exemplar-not-page-one"),
        pytest.param("canonical_render_sha256", "f" * 64, id="exemplar-canonical-drift"),
        pytest.param("similarity_score", "0.900000", id="exemplar-not-exact-self-match"),
    ],
)
@requires_linux_governed_poppler
def test_batch_rejects_signed_exemplar_candidate_identity_drift(
    field: str,
    replacement: object,
) -> None:
    """A re-signed, internally rehashed manifest must still bind the exact exemplar."""

    private_pem, public_pem = _private_and_public_pem()
    candidates = copy.deepcopy(_candidates())
    candidates[0][field] = replacement
    candidate_set_sha256 = _sha256(candidates)
    prescan = _prescan_receipt()
    prescan["candidate_set_sha256"] = candidate_set_sha256
    prescan["sample_set_sha256"] = candidate_set_sha256
    payload = _valid_batch_payload(private_pem)
    payload["candidates"] = candidates
    payload["candidate_set_sha256"] = candidate_set_sha256
    payload["sample_set_sha256"] = candidate_set_sha256
    payload["prescan_receipt_sha256"] = _sha256(prescan)
    manifest = _manifest(
        private_pem,
        batch_payload=payload,
        prescan_receipt=prescan,
    )

    with pytest.raises(_error_type()) as caught:
        _verify(manifest, public_pem=public_pem)
    assert _code(caught.value) == "WATERMARK_BATCH_ATTESTATION_MISMATCH"


@requires_linux_governed_poppler
def test_batch_rejects_tampered_signature_and_untrusted_signer() -> None:
    private_pem, public_pem = _private_and_public_pem()
    manifest = _manifest(private_pem)
    batch = manifest["watermark_batch_attestation_envelope"]
    assert isinstance(batch, dict)
    batch["signature_base64"] = base64.b64encode(b"\x00" * 64).decode("ascii")

    with pytest.raises(_error_type()) as tampered:
        _verify(manifest, public_pem=public_pem)
    assert _code(tampered.value) == "GOVERNANCE_SIGNATURE_INVALID"

    manifest = _manifest(private_pem)
    with pytest.raises(_error_type()) as untrusted:
        _verify(
            manifest,
            public_pem=public_pem,
            trusted_signer_id="fictional-other-signer-v1",
        )
    assert _code(untrusted.value) == "GOVERNANCE_SIGNER_UNTRUSTED"


@requires_linux_governed_poppler
def test_batch_rejects_an_unresolved_prescan_even_when_its_hash_and_signature_match() -> None:
    private_pem, public_pem = _private_and_public_pem()
    prescan = _prescan_receipt(status="BLOCKED_PENDING_REVIEW", unresolved_count=1)
    exemplar = _sign(
        schema_version=EXEMPLAR_SCHEMA_VERSION,
        payload=_exemplar_payload(),
        private_pem=private_pem,
    )
    payload = _batch_payload(
        exemplar_envelope_sha256=_sha256(exemplar),
        exemplar_bitmap_sha256=hashlib.sha256(_canonical_bitmap()).hexdigest(),
        prescan_receipt=prescan,
    )
    manifest = _manifest(
        private_pem,
        batch_payload=payload,
        prescan_receipt=prescan,
    )

    with pytest.raises(_error_type()) as caught:
        _verify(manifest, public_pem=public_pem)
    assert _code(caught.value) == "WATERMARK_PRESCAN_INCOMPLETE"


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 7, 17, 8, 59, 59, tzinfo=UTC),
        datetime(2026, 10, 15, 0, 0, 0, tzinfo=UTC),
        datetime(2026, 11, 1, tzinfo=UTC),
    ],
)
@requires_linux_governed_poppler
def test_batch_requires_active_half_open_reviewer_interval(now: datetime) -> None:
    private_pem, public_pem = _private_and_public_pem()
    with pytest.raises(_error_type()) as caught:
        _verify(_manifest(private_pem), public_pem=public_pem, now=now)
    assert _code(caught.value) == "WATERMARK_BATCH_ATTESTATION_NOT_ACTIVE"


@requires_linux_governed_poppler
def test_verified_batch_safe_summary_suppresses_private_material() -> None:
    private_pem, public_pem = _private_and_public_pem()
    verified = _verify(_manifest(private_pem), public_pem=public_pem)
    summary = verified.safe_summary()

    assert summary["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert summary["status"] == "verified"
    assert summary["manual_attestation_count"] == 1
    assert summary["watermark_batch_candidate_count"] == 2
    assert summary["variant"] == "watermark_only"
    assert summary["classifier_policy_id"] == POLICY_VERSION
    assert summary["classifier_policy_sha256"] == POLICY_SHA256
    assert summary["candidate_set_sha256"] == _sha256(_candidates())
    assert summary["prescan_receipt_sha256"] == _sha256(_prescan_receipt())
    assert summary["sample_set_sha256"] == _sha256(_candidates())
    assert summary["signature_verified"] is True
    serialized = json.dumps(summary, sort_keys=True)
    # The verified object must not project any private envelope material.
    raw_manifest = _manifest(private_pem)
    exemplar = raw_manifest["exemplar_attestation_envelope"]
    batch = raw_manifest["watermark_batch_attestation_envelope"]
    assert isinstance(exemplar, dict) and isinstance(batch, dict)
    exemplar_signature = exemplar["signature_base64"]
    manifest_signature = batch["signature_base64"]
    bitmap_base64 = raw_manifest["exemplar_canonical_bitmap_base64"]
    assert isinstance(exemplar_signature, str)
    assert isinstance(manifest_signature, str)
    assert isinstance(bitmap_base64, str)
    for canary in (
        PRIVATE_PATH_CANARY,
        PRIVATE_TEXT_CANARY,
        PRIVATE_BITMAP_SEED_CANARY,
        bitmap_base64,
        exemplar_signature,
        manifest_signature,
        "signature_base64",
        "exemplar_canonical_bitmap_base64",
        "attestation_text",
        "source_path",
    ):
        assert canary not in serialized


def test_existing_manual_v1_envelope_remains_a_supported_governance_schema() -> None:
    """Compatibility must stay green while the new batch path starts RED."""

    private_pem, public_pem = _private_and_public_pem()
    envelope = governance_envelopes.sign_envelope(
        schema_version=EXEMPLAR_SCHEMA_VERSION,
        signer_id=SIGNER_ID,
        payload=_exemplar_payload(),
        private_key_pem=private_pem,
    )
    verified = governance_envelopes.verify_envelope(
        envelope=envelope,
        trusted_signer_id=SIGNER_ID,
        trusted_public_key_pem=public_pem,
    )
    assert verified.schema_version == EXEMPLAR_SCHEMA_VERSION
    assert verified.payload["safe_alias"] == EXEMPLAR_ALIAS

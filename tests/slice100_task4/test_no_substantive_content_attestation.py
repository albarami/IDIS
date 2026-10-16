"""RED contract for the one-document signed no-substantive-content attestation.

This module is intentionally hermetic.  All identities, aliases, hashes, and
review text are fictional, and no source-room path or private filename is used.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from idis.parsers import ocr as ocr_module
from idis.parsers.base import ParseError, ParseErrorCode, ParseResult, SpanDraft
from idis.runtime.governance_envelopes import canonicalize_json

SCHEMA_VERSION = "document-no-substantive-content-attestation-v1"
RECEIPT_SCHEMA_VERSION = "document-no-substantive-content-receipt-v1"
MANIFEST_PATH_ENV = "IDIS_REAL_EXAMPLE_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_PATH"
SIGNER_ID = "fictional-illegibility-governance-signer-v1"
TENANT_ID = "30000000-0000-4000-8000-000000000001"
SOURCE_TREE_FINGERPRINT = "1" * 64
SAFE_ALIAS = "company-001-document-00001.pdf"
CONTENT_SHA256 = "2" * 64
ATTESTATION_TEXT_SHA256 = "3" * 64
VARIANT = "watermark_only"
POLICY_SHA256 = ocr_module.OCR_RESCUE_POLICY_SHA256
APPROVED_AT = "2026-07-17T10:00:00Z"
EXPIRES_AT = "2026-07-18T10:00:00Z"
ACTIVE_NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
REVIEWED_TEXT_CANARY = "fictional reviewed illegibility statement"
PRIVATE_NAME_CANARY = "fictional-private-board-scan.pdf"
PRIVATE_PATH_CANARY = r"C:\fictional-private-room\fictional-private-board-scan.pdf"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class _NoConsensusCode(StrEnum):
    OCR_RESCUE_NO_CONSENSUS = "ocr_rescue_no_consensus"


def _complete_file_proof(page_count: int) -> ocr_module.OcrNoConsensusFileProof:
    receipts = tuple(
        ocr_module._no_consensus_page_execution_receipt(
            page_number=page_number,
            candidate_receipt_sha256=hashlib.sha256(
                f"fictional-candidate-receipt-{page_number}".encode()
            ).hexdigest(),
        )
        for page_number in range(1, page_count + 1)
    )
    return ocr_module._ocr_no_consensus_file_proof(receipts)


OCR_RESULT_SHA256 = _complete_file_proof(2).ocr_result_sha256


def _target() -> ModuleType:
    """Import the planned module or produce an intentional, focused RED."""

    name = "idis.services.ingestion.no_substantive_content_attestation"
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        pytest.fail(f"TASK4_NO_SUBSTANTIVE_CONTENT_API_MISSING: {name}: {exc}", pytrace=False)


def _required_attr(name: str) -> Any:
    module = _target()
    value = getattr(module, name, None)
    if value is None:
        pytest.fail(
            f"TASK4_NO_SUBSTANTIVE_CONTENT_API_MISSING: {module.__name__}.{name}",
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


def _payload() -> dict[str, object]:
    return {
        "tenant_id": TENANT_ID,
        "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        "safe_alias": SAFE_ALIAS,
        "content_sha256": CONTENT_SHA256,
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": VARIANT,
        "attestation_text_sha256": ATTESTATION_TEXT_SHA256,
        "reviewer_approval": {
            "decision": "APPROVED",
            "reviewer_id": "fictional-reviewer-001",
            "approved_at": APPROVED_AT,
            "expires_at": EXPIRES_AT,
        },
    }


def _private_and_public_pem() -> tuple[bytes, bytes]:
    key = Ed25519PrivateKey.generate()
    return (
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
    )


def _manually_sign(
    payload: dict[str, object],
    *,
    private_pem: bytes,
    signer_id: str = SIGNER_ID,
) -> dict[str, object]:
    """Sign without schema validation so loader RED cases reach their seam."""

    private_key = serialization.load_pem_private_key(private_pem, password=None)
    assert isinstance(private_key, Ed25519PrivateKey)
    signable: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "signer_id": signer_id,
        "payload": payload,
    }
    signature = private_key.sign(canonicalize_json(signable))
    return {
        **signable,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }


def _seal(path: Path) -> None:
    from idis.runtime.secure_files import protect_path_for_current_user

    protect_path_for_current_user(path.parent, directory=True)
    protect_path_for_current_user(path)
    path.chmod(stat.S_IREAD if os.name == "nt" else stat.S_IRUSR)


@dataclass(frozen=True, slots=True)
class _SignedFixture:
    env: dict[str, str]
    payload: dict[str, object]
    envelope: dict[str, object]
    manifest_path: Path
    public_key_path: Path
    public_pem: bytes


def _write_signed_fixture(
    tmp_path: Path,
    *,
    payload: dict[str, object] | None = None,
    signer_id: str = SIGNER_ID,
    trusted_signer_id: str = SIGNER_ID,
    corrupt_signature: bool = False,
    seal: bool = True,
) -> _SignedFixture:
    private_pem, public_pem = _private_and_public_pem()
    exact_payload = copy.deepcopy(payload if payload is not None else _payload())
    envelope = _manually_sign(
        exact_payload,
        private_pem=private_pem,
        signer_id=signer_id,
    )
    if corrupt_signature:
        envelope["signature_base64"] = base64.b64encode(b"\x00" * 64).decode("ascii")

    public_path = tmp_path / "fictional-governance-public.pem"
    manifest_path = tmp_path / "fictional-illegibility-envelope.json"
    public_path.write_bytes(public_pem)
    manifest_path.write_text(json.dumps(envelope, sort_keys=True), encoding="utf-8")
    if seal:
        _seal(public_path)
        _seal(manifest_path)

    env = {
        MANIFEST_PATH_ENV: str(manifest_path.resolve()),
        "IDIS_GOVERNANCE_TRUSTED_SIGNER_ID": trusted_signer_id,
        "IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH": str(public_path.resolve()),
    }
    return _SignedFixture(
        env=env,
        payload=exact_payload,
        envelope=envelope,
        manifest_path=manifest_path,
        public_key_path=public_path,
        public_pem=public_pem,
    )


def _load(fixture: _SignedFixture, *, now: datetime = ACTIVE_NOW) -> object:
    return _required_attr("load_document_no_substantive_content_attestation")(
        env=fixture.env,
        now=now,
    )


def _complete_no_consensus_result(*, page_count: int = 2) -> ParseResult:
    proof = _complete_file_proof(page_count)
    return ParseResult(
        doc_type="PDF",
        success=False,
        spans=[],
        metadata={
            "page_count": page_count,
            "span_count": 0,
            "total_text_length": 0,
            "ocr_performed": True,
            "ocr_page_count": page_count,
            "ocr_required_page_count": page_count,
            "native_text_page_count": 0,
            "blank_page_count": page_count,
            "primary_completed_page_count": page_count,
            "rescue_completed_page_count": page_count,
            "rescue_candidate_attempt_count": page_count * 24,
            "rescue_candidate_completed_count": page_count * 24,
            "rescue_accepted_clique_page_count": 0,
            "accepted_text_page_count": 0,
            "parser_runtime": "tesseract",
            "parser_runtime_status": "completed_no_extractable_text",
            "ocr_rescue_policy_version": "pdf-ocr-rescue-v1",
            "ocr_rescue_policy_sha256": POLICY_SHA256,
            "ocr_no_consensus_page_receipts": [
                receipt.to_payload() for receipt in proof.page_receipts
            ],
            "ocr_result_sha256": proof.ocr_result_sha256,
        },
        errors=[
            ParseError(
                # A real StrEnum preserves the parser error API while allowing
                # this RED to reach the new seam before its enum member lands.
                code=cast(ParseErrorCode, _NoConsensusCode.OCR_RESCUE_NO_CONSENSUS),
                message="Complete OCR rescue found no corroborated text",
                details={
                    "page_count": page_count,
                    "attempted_candidate_count": page_count * 24,
                    "completed_candidate_count": page_count * 24,
                    "no_consensus_page_count": page_count,
                },
            )
        ],
        warnings=[],
    )


def _build_receipt(
    attestation: object | None,
    result: ParseResult,
    *,
    now: datetime = ACTIVE_NOW,
) -> dict[str, object]:
    value = _required_attr("build_document_no_substantive_content_receipt")(
        attestation=attestation,
        tenant_id=TENANT_ID,
        source_tree_fingerprint=SOURCE_TREE_FINGERPRINT,
        safe_alias=SAFE_ALIAS,
        content_sha256=CONTENT_SHA256,
        parse_result=result,
        now=now,
    )
    assert isinstance(value, dict)
    return value


def test_task2_helper_signs_and_verifies_exact_new_governance_schema(
    tmp_path: Path,
) -> None:
    """The reviewed Task 2 helper remains the offline operator signing path."""

    from idis.runtime.governance_envelopes import verify_envelope
    from idis.tools.governance_signing import (
        generate_ed25519_keypair,
        sign_governance_payload,
    )

    payload_path = tmp_path / "fictional-illegibility-payload.json"
    payload_path.write_text(json.dumps(_payload(), sort_keys=True), encoding="utf-8")
    key_dir = tmp_path / "fictional-operator-keys"
    key_dir.mkdir()
    private_path = key_dir / "private.pem"
    public_path = key_dir / "public.pem"
    output_path = tmp_path / "fictional-illegibility-envelope.json"

    generate_ed25519_keypair(
        private_key_path=private_path,
        public_key_path=public_path,
    )
    result = sign_governance_payload(
        schema_version=SCHEMA_VERSION,
        signer_id=SIGNER_ID,
        payload_path=payload_path,
        private_key_path=private_path,
        output_path=output_path,
    )
    envelope = json.loads(output_path.read_text(encoding="utf-8"))
    verified = verify_envelope(
        envelope=envelope,
        trusted_signer_id=SIGNER_ID,
        trusted_public_key_pem=public_path.read_bytes(),
    )

    assert set(envelope) == {
        "schema_version",
        "signer_id",
        "payload",
        "signature_base64",
    }
    assert envelope["payload"] == _payload()
    assert verified.schema_version == SCHEMA_VERSION
    assert verified.payload_sha256 == result.payload_sha256
    assert verified.envelope_sha256 == result.envelope_sha256


def test_secure_loader_returns_only_verified_secret_safe_summary(tmp_path: Path) -> None:
    fixture = _write_signed_fixture(tmp_path)

    attestation = _load(fixture)
    summary = attestation.safe_summary()

    assert summary == {
        "schema_version": SCHEMA_VERSION,
        "status": "verified",
        "entry_count": 1,
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": VARIANT,
        "payload_sha256": hashlib.sha256(canonicalize_json(fixture.payload)).hexdigest(),
        "envelope_sha256": hashlib.sha256(canonicalize_json(fixture.envelope)).hexdigest(),
        "public_key_sha256": hashlib.sha256(fixture.public_pem).hexdigest(),
        "signature_verified": True,
    }
    rendered = json.dumps(summary, sort_keys=True) + repr(attestation)
    for forbidden in (
        SAFE_ALIAS,
        CONTENT_SHA256,
        REVIEWED_TEXT_CANARY,
        PRIVATE_NAME_CANARY,
        PRIVATE_PATH_CANARY,
        str(fixture.manifest_path),
        str(fixture.public_key_path),
        str(fixture.envelope["signature_base64"]),
    ):
        assert forbidden not in rendered


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.update({"unexpected": True}),
        lambda value: value.update({"tenant_id": "30000000-0000-4000-8000-00000000000A"}),
        lambda value: value.update({"safe_alias": PRIVATE_NAME_CANARY}),
        lambda value: value.update({"content_sha256": "A" * 64}),  # Letter case must drift.
        lambda value: value.update({"attestation_text": REVIEWED_TEXT_CANARY}),
        lambda value: value.update({"variant": "watermark-only"}),
        lambda value: value.update({"documents": [copy.deepcopy(value)]}),
        lambda value: cast(dict[str, object], value["reviewer_approval"]).update(
            {"unexpected": True}
        ),
        lambda value: cast(dict[str, object], value["reviewer_approval"]).update(
            {"decision": "REJECTED"}
        ),
    ],
    ids=(
        "extra-root-member",
        "noncanonical-tenant",
        "private-name-instead-of-safe-alias",
        "uppercase-content-hash",
        "raw-review-text",
        "unknown-variant",
        "multiple-document-container",
        "extra-reviewer-member",
        "review-not-approved",
    ),
)
def test_exact_one_document_payload_schema_fails_closed(
    tmp_path: Path,
    mutator: Any,
) -> None:
    payload = _payload()
    mutator(payload)
    fixture = _write_signed_fixture(tmp_path, payload=payload)

    with pytest.raises(_error_type()) as caught:
        _load(fixture)

    assert _code(caught.value) == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_INVALID"
    assert REVIEWED_TEXT_CANARY not in str(caught.value)
    assert PRIVATE_NAME_CANARY not in str(caught.value)


def test_secure_manifest_file_is_owner_only_read_only_under_secure_parent(
    tmp_path: Path,
) -> None:
    fixture = _write_signed_fixture(tmp_path, seal=False)

    with pytest.raises(_error_type()) as caught:
        _load(fixture)

    assert _code(caught.value) == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_INVALID"
    assert str(fixture.manifest_path) not in str(caught.value)


@pytest.mark.parametrize(
    ("fixture_options", "expected_code"),
    [
        ({"trusted_signer_id": "fictional-untrusted-signer"}, "GOVERNANCE_SIGNER_UNTRUSTED"),
        ({"corrupt_signature": True}, "GOVERNANCE_SIGNATURE_INVALID"),
    ],
    ids=("untrusted-signer", "invalid-signature"),
)
def test_signer_and_signature_fail_with_existing_governance_codes(
    tmp_path: Path,
    fixture_options: dict[str, object],
    expected_code: str,
) -> None:
    fixture = _write_signed_fixture(tmp_path, **fixture_options)  # type: ignore[arg-type]

    with pytest.raises(_error_type()) as caught:
        _load(fixture)

    assert _code(caught.value) == expected_code
    assert str(fixture.envelope["signature_base64"]) not in str(caught.value)


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 7, 17, 9, 59, 59, tzinfo=UTC),
        datetime(2026, 7, 18, 10, 0, tzinfo=UTC),
    ],
    ids=("before-approval", "at-expiry"),
)
def test_approval_window_is_active_at_approval_and_exclusive_at_expiry(
    tmp_path: Path,
    now: datetime,
) -> None:
    fixture = _write_signed_fixture(tmp_path)
    assert _load(fixture, now=datetime(2026, 7, 17, 10, 0, tzinfo=UTC)) is not None

    with pytest.raises(_error_type()) as caught:
        _load(fixture, now=now)

    assert _code(caught.value) == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_NOT_ACTIVE"


def test_exact_tenant_tree_alias_and_content_binding_is_required(tmp_path: Path) -> None:
    fixture = _write_signed_fixture(tmp_path)
    attestation = _load(fixture)
    assert (
        attestation.require_match(
            tenant_id=TENANT_ID,
            source_tree_fingerprint=SOURCE_TREE_FINGERPRINT,
            safe_alias=SAFE_ALIAS,
            content_sha256=CONTENT_SHA256,
        )
        is attestation
    )

    mismatches = (
        {"tenant_id": "30000000-0000-4000-8000-000000000002"},
        {"source_tree_fingerprint": "a" * 64},
        {"safe_alias": "company-001-document-00002.pdf"},
        {"content_sha256": "b" * 64},
    )
    baseline = {
        "tenant_id": TENANT_ID,
        "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        "safe_alias": SAFE_ALIAS,
        "content_sha256": CONTENT_SHA256,
    }
    for mismatch in mismatches:
        with pytest.raises(_error_type()) as caught:
            attestation.require_match(**{**baseline, **mismatch})
        assert _code(caught.value) == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MISMATCH"
        assert not any(str(value) in str(caught.value) for value in mismatch.values())


def test_missing_attestation_cannot_turn_complete_no_text_into_success() -> None:
    with pytest.raises(_error_type()) as caught:
        _build_receipt(None, _complete_no_consensus_result())

    assert _code(caught.value) == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_REQUIRED"


def test_complete_primary_and_24_candidate_rescue_proof_builds_exact_safe_receipt(
    tmp_path: Path,
) -> None:
    fixture = _write_signed_fixture(tmp_path)
    attestation = _load(fixture)

    receipt = _build_receipt(attestation, _complete_no_consensus_result())

    expected = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": VARIANT,
        "safe_alias": SAFE_ALIAS,
        "content_sha256": CONTENT_SHA256,
        "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        "attestation_text_sha256": ATTESTATION_TEXT_SHA256,
        "reviewer_id": "fictional-reviewer-001",
        "approved_at": APPROVED_AT,
        "expires_at": EXPIRES_AT,
        "signer_id": SIGNER_ID,
        "payload_sha256": hashlib.sha256(canonicalize_json(fixture.payload)).hexdigest(),
        "envelope_sha256": hashlib.sha256(canonicalize_json(fixture.envelope)).hexdigest(),
        "public_key_sha256": hashlib.sha256(fixture.public_pem).hexdigest(),
        "signature_verified": True,
        "parser_error_code": "ocr_rescue_no_consensus",
        "parser_runtime": "tesseract",
        "parser_runtime_status": "completed_no_extractable_text",
        "page_count": 2,
        "ocr_required_page_count": 2,
        "primary_completed_page_count": 2,
        "rescue_completed_page_count": 2,
        "accepted_text_page_count": 0,
        "span_count": 0,
        "ocr_result_sha256": OCR_RESULT_SHA256,
    }
    assert receipt == expected
    assert all(
        _SHA256_RE.fullmatch(str(receipt[field]))
        for field in (
            "content_sha256",
            "source_tree_fingerprint",
            "attestation_text_sha256",
            "payload_sha256",
            "envelope_sha256",
            "public_key_sha256",
            "ocr_result_sha256",
        )
    )


@pytest.mark.parametrize(
    ("field", "replacement", "expected_code"),
    [
        ("ocr_required_page_count", 1, "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE"),
        ("primary_completed_page_count", 1, "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE"),
        ("rescue_completed_page_count", 1, "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE"),
        ("rescue_candidate_attempt_count", 47, "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE"),
        (
            "rescue_candidate_completed_count",
            47,
            "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE",
        ),
        ("parser_runtime", "synthetic", "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE"),
        ("native_text_page_count", 1, "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED"),
        ("accepted_text_page_count", 1, "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED"),
        (
            "rescue_accepted_clique_page_count",
            1,
            "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED",
        ),
        ("span_count", 1, "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED"),
    ],
)
def test_partial_or_contradictory_technical_proof_never_builds_a_receipt(
    tmp_path: Path,
    field: str,
    replacement: object,
    expected_code: str,
) -> None:
    fixture = _write_signed_fixture(tmp_path)
    attestation = _load(fixture)
    result = _complete_no_consensus_result()
    result.metadata[field] = replacement

    with pytest.raises(_error_type()) as caught:
        _build_receipt(attestation, result)

    assert _code(caught.value) == expected_code


@pytest.mark.parametrize(
    "mutator",
    [
        lambda result: result.metadata.update({"ocr_rescue_policy_sha256": "0" * 64}),
        lambda result: result.metadata.update({"ocr_rescue_policy_version": "stale-policy-v0"}),
        lambda result: result.metadata.pop("ocr_no_consensus_page_receipts"),
        lambda result: result.metadata.update(
            {"raw_text": "fictional text that must never enter durable proof metadata"}
        ),
        lambda result: cast(
            list[dict[str, object]], result.metadata["ocr_no_consensus_page_receipts"]
        ).pop(0),
        lambda result: cast(
            list[dict[str, object]], result.metadata["ocr_no_consensus_page_receipts"]
        ).append(
            copy.deepcopy(
                cast(
                    list[dict[str, object]],
                    result.metadata["ocr_no_consensus_page_receipts"],
                )[0]
            )
        ),
        lambda result: cast(
            list[dict[str, object]], result.metadata["ocr_no_consensus_page_receipts"]
        )[0].update({"raw_text": "fictional text that must never enter a proof"}),
        lambda result: cast(
            list[dict[str, object]], result.metadata["ocr_no_consensus_page_receipts"]
        )[0].update({"candidate_receipt_sha256": "9" * 64}),
        lambda result: cast(
            list[dict[str, object]], result.metadata["ocr_no_consensus_page_receipts"]
        ).reverse(),
        lambda result: result.metadata.update({"ocr_result_sha256": "8" * 64}),
    ],
    ids=(
        "stale-policy-hash",
        "stale-policy-version",
        "missing-receipt-list",
        "extra-metadata-raw-text",
        "page-gap",
        "duplicate-page",
        "receipt-field-drift",
        "receipt-content-digest-drift",
        "reordered-pages",
        "file-result-digest-drift",
    ),
)
def test_attestation_revalidates_exact_canonical_all_page_ocr_proof(
    tmp_path: Path,
    mutator: Any,
) -> None:
    fixture = _write_signed_fixture(tmp_path)
    result = _complete_no_consensus_result()
    mutator(result)

    with pytest.raises(_error_type()) as caught:
        _build_receipt(_load(fixture), result)

    assert _code(caught.value) == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE"


def test_valid_mixed_text_and_no_consensus_receipts_are_contradictory_evidence(
    tmp_path: Path,
) -> None:
    fixture = _write_signed_fixture(tmp_path)
    result = _complete_no_consensus_result()
    primary_text = "fictional primary evidence text"
    mixed_proof = ocr_module._ocr_no_consensus_file_proof(
        (
            ocr_module._accepted_page_execution_receipt(
                page_number=1,
                outcome="primary_text",
                text=primary_text,
                primary_token_count=len(primary_text.split()),
                candidate_receipt_sha256=None,
            ),
            _complete_file_proof(2).page_receipts[1],
        )
    )
    result.metadata["ocr_no_consensus_page_receipts"] = [
        receipt.to_payload() for receipt in mixed_proof.page_receipts
    ]
    result.metadata["ocr_result_sha256"] = mixed_proof.ocr_result_sha256

    with pytest.raises(_error_type()) as caught:
        _build_receipt(_load(fixture), result)

    assert _code(caught.value) == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED"


def test_warning_timeout_or_nonempty_span_cannot_be_attested(tmp_path: Path) -> None:
    fixture = _write_signed_fixture(tmp_path)
    attestation = _load(fixture)

    warned = _complete_no_consensus_result()
    warned.warnings.append("fictional partial OCR warning")
    timeout = _complete_no_consensus_result()
    timeout.errors = [
        ParseError(
            code=ParseErrorCode.OCR_TIMEOUT,
            message="OCR timed out",
            details={},
        )
    ]
    nonempty = _complete_no_consensus_result()
    nonempty.spans = [
        SpanDraft(
            span_type="PAGE_TEXT",
            locator={"page": 1, "source": "ocr_rescue"},
            text_excerpt="fictional extracted evidence",
            content_hash=hashlib.sha256(b"fictional extracted evidence").hexdigest(),
        )
    ]

    for result, expected_code in (
        (warned, "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE"),
        (timeout, "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE"),
        (nonempty, "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED"),
    ):
        with pytest.raises(_error_type()) as caught:
            _build_receipt(attestation, result)
        assert _code(caught.value) == expected_code


def test_receipt_revalidation_rejects_tampering_and_expiry(tmp_path: Path) -> None:
    fixture = _write_signed_fixture(tmp_path)
    attestation = _load(fixture)
    receipt = _build_receipt(attestation, _complete_no_consensus_result())
    validate = _required_attr("validate_document_no_substantive_content_receipt")

    validate(
        receipt=receipt,
        attestation=attestation,
        tenant_id=TENANT_ID,
        source_tree_fingerprint=SOURCE_TREE_FINGERPRINT,
        safe_alias=SAFE_ALIAS,
        content_sha256=CONTENT_SHA256,
        now=ACTIVE_NOW,
    )

    tampered = dict(receipt)
    tampered["span_count"] = 1
    with pytest.raises(_error_type()) as caught:
        validate(
            receipt=tampered,
            attestation=attestation,
            tenant_id=TENANT_ID,
            source_tree_fingerprint=SOURCE_TREE_FINGERPRINT,
            safe_alias=SAFE_ALIAS,
            content_sha256=CONTENT_SHA256,
            now=ACTIVE_NOW,
        )
    assert _code(caught.value) == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_CONTRADICTED"

    with pytest.raises(_error_type()) as caught:
        validate(
            receipt=receipt,
            attestation=attestation,
            tenant_id=TENANT_ID,
            source_tree_fingerprint=SOURCE_TREE_FINGERPRINT,
            safe_alias=SAFE_ALIAS,
            content_sha256=CONTENT_SHA256,
            now=datetime(2026, 7, 18, 10, 0, tzinfo=UTC),
        )
    assert _code(caught.value) == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_NOT_ACTIVE"


def test_receipt_never_mints_text_or_persists_private_attestation_material(
    tmp_path: Path,
) -> None:
    fixture = _write_signed_fixture(tmp_path)
    receipt = _build_receipt(_load(fixture), _complete_no_consensus_result())
    rendered = json.dumps(receipt, sort_keys=True)

    assert receipt["span_count"] == 0
    assert "spans" not in receipt
    assert "text" not in receipt
    for forbidden in (
        REVIEWED_TEXT_CANARY,
        PRIVATE_NAME_CANARY,
        PRIVATE_PATH_CANARY,
        str(fixture.manifest_path),
        str(fixture.public_key_path),
        str(fixture.envelope["signature_base64"]),
    ):
        assert forbidden not in rendered


def test_expired_attestation_blocks_cached_201_before_stale_replay(tmp_path: Path) -> None:
    """Unit seam for the middleware's pre-return replay invariant check."""

    fixture = _write_signed_fixture(tmp_path)
    attestation = _load(fixture)
    receipt = _build_receipt(attestation, _complete_no_consensus_result())
    replay_module_name = "idis.api.document_upload_replay"
    try:
        replay_module = importlib.import_module(replay_module_name)
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"TASK4_NO_SUBSTANTIVE_CONTENT_API_MISSING: {replay_module_name}: {exc}",
            pytrace=False,
        )
    validate_replay = getattr(replay_module, "validate_upload_replay_invariant", None)
    if not callable(validate_replay):
        pytest.fail(
            "TASK4_NO_SUBSTANTIVE_CONTENT_API_MISSING: "
            f"{replay_module_name}.validate_upload_replay_invariant",
            pytrace=False,
        )

    document_id = "40000000-0000-4000-8000-000000000001"
    cached_response = {
        "status_code": 201,
        "media_type": "application/json",
        "body": {
            "document_id": document_id,
            "terminal_disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        },
    }
    durable_document = {
        "tenant_id": TENANT_ID,
        "document_id": document_id,
        "parse_status": "FAILED",
        "span_count": 0,
        "metadata": {"document_no_substantive_content_receipt": receipt},
    }

    with pytest.raises(Exception) as caught:
        validate_replay(
            cached_response=cached_response,
            durable_document=durable_document,
            attestation=attestation,
            tenant_id=TENANT_ID,
            source_tree_fingerprint=SOURCE_TREE_FINGERPRINT,
            safe_alias=SAFE_ALIAS,
            content_sha256=CONTENT_SHA256,
            now=datetime(2026, 7, 18, 10, 0, tzinfo=UTC),
        )

    assert _code(caught.value) == "IDEMPOTENCY_REPLAY_INVARIANT_FAILED"

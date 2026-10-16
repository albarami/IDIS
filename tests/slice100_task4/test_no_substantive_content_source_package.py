"""RED-first source-package admission for one signed terminal exclusion."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from idis.evaluation import real_example_source_package as source_package
from idis.runtime.governance_envelopes import NO_SUBSTANTIVE_CONTENT_ATTESTATION_SCHEMA_VERSION
from idis.services.ingestion.no_substantive_content_attestation import (
    DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
    VerifiedDocumentNoSubstantiveContentAttestation,
)
from tests.slice100_task3._support import (
    FakePreparationApi,
    build_source_fixture,
    contract_kwargs,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
TREE_SHA256 = "a" * 64
CONTENT_SHA256 = "b" * 64
ALIAS = "company-002-document-00053.pdf"


def _attestation() -> VerifiedDocumentNoSubstantiveContentAttestation:
    return VerifiedDocumentNoSubstantiveContentAttestation(
        schema_version=NO_SUBSTANTIVE_CONTENT_ATTESTATION_SCHEMA_VERSION,
        signer_id="slice100-reviewer",
        tenant_id=TENANT_ID,
        source_tree_fingerprint=TREE_SHA256,
        safe_alias=ALIAS,
        content_sha256=CONTENT_SHA256,
        disposition=DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
        variant="watermark_only",
        attestation_text_sha256="c" * 64,
        reviewer_id="operator-reviewer",
        approved_at_text="2020-01-01T00:00:00Z",
        expires_at_text="2099-01-01T00:00:00Z",
        approved_at=datetime(2020, 1, 1, tzinfo=UTC),
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        payload_sha256="d" * 64,
        envelope_sha256="e" * 64,
        public_key_sha256="f" * 64,
    )


def _receipt() -> dict[str, object]:
    return {
        "schema_version": "document-no-substantive-content-receipt-v1",
        "disposition": DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
        "variant": "watermark_only",
        "safe_alias": ALIAS,
        "content_sha256": CONTENT_SHA256,
        "source_tree_fingerprint": TREE_SHA256,
        "attestation_text_sha256": "c" * 64,
        "reviewer_id": "operator-reviewer",
        "approved_at": "2020-01-01T00:00:00Z",
        "expires_at": "2099-01-01T00:00:00Z",
        "signer_id": "slice100-reviewer",
        "payload_sha256": "d" * 64,
        "envelope_sha256": "e" * 64,
        "public_key_sha256": "f" * 64,
        "signature_verified": True,
        "parser_error_code": "ocr_rescue_no_consensus",
        "parser_runtime": "tesseract",
        "parser_runtime_status": "completed_no_extractable_text",
        "page_count": 1,
        "ocr_required_page_count": 1,
        "primary_completed_page_count": 1,
        "rescue_completed_page_count": 1,
        "accepted_text_page_count": 0,
        "span_count": 0,
        "ocr_result_sha256": "1" * 64,
    }


def _source_file() -> source_package.SourceFileContract:
    return source_package.SourceFileContract(
        sequence=52,
        generated_upload_name=ALIAS,
        source_locator_sha256="2" * 64,
        content_sha256=CONTENT_SHA256,
        size_bytes=123,
        extension="pdf",
    )


def test_all_uploads_bind_the_source_tree_fingerprint_header(tmp_path: Path) -> None:
    fixture = build_source_fixture(tmp_path)
    api = FakePreparationApi()

    result = source_package.prepare_two_company_source_packages(
        api_client=api,
        api_key="test-only-secret",
        ledger_path=tmp_path / "ledger" / "prepare.jsonl",
        **contract_kwargs(fixture),
    )

    upload_calls = [
        call
        for call in api.calls
        if call["method"] == "POST" and call["url"].endswith("/documents/upload")
    ]
    assert upload_calls
    assert all("X-IDIS-Source-Tree-Fingerprint" in call["header_names"] for call in upload_calls)
    assert len(result["source_tree_fingerprint"]) == 64


def test_exact_failed_zero_span_terminal_receipt_is_admitted_as_package_membership() -> None:
    response = {
        "deal_id": "33333333-3333-3333-3333-333333333333",
        "document_id": "44444444-4444-4444-4444-444444444444",
        "doc_id": "55555555-5555-5555-5555-555555555555",
        "sha256": CONTENT_SHA256,
        "parse_status": "FAILED",
        "terminal_disposition": DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
        "metadata": {"document_no_substantive_content_receipt": _receipt()},
        "source_metadata": {"upload_filename": ALIAS, "size_bytes": 123},
    }

    admitted = source_package._validated_upload_receipt(
        response=response,
        deal_id="33333333-3333-3333-3333-333333333333",
        source_file=_source_file(),
        source_tree_fingerprint=TREE_SHA256,
        no_substantive_content_attestation=_attestation(),
    )

    assert admitted["document_id"] == response["document_id"]
    assert admitted["doc_id"] == response["doc_id"]


def test_failed_row_without_current_exact_attestation_still_fails_closed() -> None:
    response = {
        "deal_id": "33333333-3333-3333-3333-333333333333",
        "document_id": "44444444-4444-4444-4444-444444444444",
        "doc_id": "55555555-5555-5555-5555-555555555555",
        "sha256": CONTENT_SHA256,
        "parse_status": "FAILED",
        "terminal_disposition": DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
        "metadata": {"document_no_substantive_content_receipt": _receipt()},
        "source_metadata": {"upload_filename": ALIAS, "size_bytes": 123},
    }

    with pytest.raises(source_package.SourcePackageContractError) as exc:
        source_package._validated_upload_receipt(
            response=response,
            deal_id="33333333-3333-3333-3333-333333333333",
            source_file=_source_file(),
            source_tree_fingerprint=TREE_SHA256,
            no_substantive_content_attestation=None,
        )

    assert exc.value.code == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_REQUIRED"


@pytest.mark.parametrize(
    ("terminal_disposition", "metadata"),
    [
        (
            None,
            {
                "document_illegibility_receipt": {
                    "disposition": "DOCUMENT_ILLEGIBLE_CONFIRMED",
                }
            },
        ),
        ("DOCUMENT_ILLEGIBLE_CONFIRMED", {}),
    ],
    ids=("legacy-receipt", "legacy-disposition"),
)
def test_parsed_source_row_rejects_every_superseded_terminal_contract(
    terminal_disposition: object,
    metadata: dict[str, object],
) -> None:
    response = {
        "deal_id": "33333333-3333-3333-3333-333333333333",
        "document_id": "44444444-4444-4444-4444-444444444444",
        "doc_id": "55555555-5555-5555-5555-555555555555",
        "sha256": CONTENT_SHA256,
        "parse_status": "PARSED",
        "terminal_disposition": terminal_disposition,
        "metadata": metadata,
        "source_metadata": {"upload_filename": ALIAS, "size_bytes": 123},
    }

    with pytest.raises(source_package.SourcePackageContractError) as caught:
        source_package._validated_upload_receipt(
            response=response,
            deal_id="33333333-3333-3333-3333-333333333333",
            source_file=_source_file(),
            source_tree_fingerprint=TREE_SHA256,
            no_substantive_content_attestation=_attestation(),
        )

    assert caught.value.code == "DATA_ROOM_RECONCILIATION_INCOMPLETE"


def test_upload_idempotency_key_binds_attestation_envelope_hash(tmp_path: Path) -> None:
    fixture = build_source_fixture(tmp_path)
    contract = source_package.build_two_company_source_contract(**contract_kwargs(fixture))
    company = contract.companies[0]

    without = source_package._preparation_idempotency_key(
        contract,
        company,
        operation="document-upload",
        discriminator="0:content",
    )
    with_attestation = source_package._preparation_idempotency_key(
        contract,
        company,
        operation="document-upload",
        discriminator="0:content",
        attestation_envelope_sha256="e" * 64,
    )

    assert without != with_attestation
    assert "e" * 64 not in with_attestation
    assert hashlib.sha256(("e" * 64).encode()).hexdigest() not in with_attestation

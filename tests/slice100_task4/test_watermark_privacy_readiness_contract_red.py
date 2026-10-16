"""RED contracts for v2 readiness, v4 safe proof, and public contract hygiene."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from idis.evaluation import real_example_gate as gate
from idis.runtime import dependency_readiness as readiness
from tests.slice100_task4.test_ingestion_readiness import _facts, _ingestion_env
from tests.slice100_task4.test_no_substantive_content_security_red import (
    _safe_gate_receipt,
)
from tests.slice100_task4.test_watermark_terminal_receipt_red import (
    BATCH_ENVELOPE_SHA256,
    CANDIDATE_SET_SHA256,
    EXEMPLAR_BITMAP_SHA256,
    EXEMPLAR_ENVELOPE_SHA256,
    MANIFEST_SHA256,
    POLICY_ID,
    POLICY_SHA256,
    PUBLIC_KEY_SHA256,
)

RUNTIME_API_MISSING = "TASK4_WATERMARK_RUNTIME_API_MISSING"
MANIFEST_SCHEMA_V2 = "document-no-substantive-content-attestation-manifest-v2"
DURABLE_PROOF_SCHEMA_V4 = "slice100-task4-durable-ingestion-proof-v4"
PRESCAN_RECEIPT_SHA256 = hashlib.sha256(b"fictional-prescan-receipt").hexdigest()
SAMPLE_SET_SHA256 = hashlib.sha256(b"fictional-reviewer-sample-set").hexdigest()
CLASSIFICATION_RECEIPTS_SHA256 = hashlib.sha256(
    b"fictional-ordered-watermark-classification-receipts"
).hexdigest()
# Task 5 deliberately adds the reviewed methodology-version and run-selection surface.
OPENAPI_SURFACE_SHA256 = "c15705b54afa00b3a423ced8f4969871e75453c5c62b340a4952e7da868d2046"


def _v2_readiness_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "no_substantive_content_attestation_status": "verified",
        "no_substantive_content_attestation_entry_count": 2,
        "no_substantive_content_attestation_schema_version": MANIFEST_SCHEMA_V2,
        "no_substantive_content_attestation_disposition": (
            "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"
        ),
        "no_substantive_content_attestation_variant": "watermark_only",
        "no_substantive_content_attestation_payload_sha256": "a" * 64,
        "no_substantive_content_attestation_envelope_sha256": EXEMPLAR_ENVELOPE_SHA256,
        "no_substantive_content_attestation_public_key_sha256": PUBLIC_KEY_SHA256,
        "no_substantive_content_attestation_manifest_sha256": MANIFEST_SHA256,
        "no_substantive_content_attestation_classifier_policy_sha256": POLICY_SHA256,
        "no_substantive_content_attestation_exemplar_envelope_sha256": (EXEMPLAR_ENVELOPE_SHA256),
        "no_substantive_content_attestation_exemplar_canonical_bitmap_sha256": (
            EXEMPLAR_BITMAP_SHA256
        ),
        "no_substantive_content_attestation_prescan_receipt_sha256": (PRESCAN_RECEIPT_SHA256),
        "no_substantive_content_attestation_candidate_set_sha256": CANDIDATE_SET_SHA256,
        "no_substantive_content_attestation_sample_set_sha256": SAMPLE_SET_SHA256,
        "no_substantive_content_attestation_batch_envelope_sha256": (BATCH_ENVELOPE_SHA256),
        "no_substantive_content_attestation_manual_count": 1,
        "no_substantive_content_attestation_watermark_candidate_count": 2,
        "no_substantive_content_attestation_runtime_unseen_count": 0,
    }
    values.update(overrides)
    return values


def _v2_facts(**overrides: object) -> object:
    try:
        return _facts(readiness, **_v2_readiness_values(**overrides))
    except TypeError as error:
        pytest.fail(f"{RUNTIME_API_MISSING}: {error}", pytrace=False)


def _configured_env() -> dict[str, str]:
    return _ingestion_env(
        IDIS_REAL_EXAMPLE_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_PATH=(
            "C:/fictional-secure/watermark-admission.json"
        )
    )


def test_exact_manifest_v2_readiness_is_typed_hash_bound_and_allowed() -> None:
    facts = _v2_facts()

    check = readiness.check_ingestion_readiness(
        env=_configured_env(),
        probe=lambda: facts,
    )

    assert check.may_proceed is True
    assert check.code is None


@pytest.mark.parametrize(
    "field",
    [
        "no_substantive_content_attestation_manifest_sha256",
        "no_substantive_content_attestation_classifier_policy_sha256",
        "no_substantive_content_attestation_exemplar_envelope_sha256",
        "no_substantive_content_attestation_exemplar_canonical_bitmap_sha256",
        "no_substantive_content_attestation_prescan_receipt_sha256",
        "no_substantive_content_attestation_candidate_set_sha256",
        "no_substantive_content_attestation_sample_set_sha256",
        "no_substantive_content_attestation_batch_envelope_sha256",
    ],
)
def test_manifest_v2_readiness_rejects_each_missing_or_malformed_binding(field: str) -> None:
    facts = _v2_facts(**{field: "not-a-sha256"})

    check = readiness.check_ingestion_readiness(
        env=_configured_env(),
        probe=lambda: facts,
    )

    assert check.may_proceed is False
    assert check.code == "WATERMARK_BATCH_ATTESTATION_MISMATCH"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("no_substantive_content_attestation_entry_count", 3),
        ("no_substantive_content_attestation_manual_count", 0),
        ("no_substantive_content_attestation_watermark_candidate_count", 1),
        ("no_substantive_content_attestation_runtime_unseen_count", 1),
    ],
)
def test_manifest_v2_readiness_rejects_count_or_review_state_drift(
    field: str,
    value: int,
) -> None:
    facts = _v2_facts(**{field: value})

    check = readiness.check_ingestion_readiness(
        env=_configured_env(),
        probe=lambda: facts,
    )

    assert check.may_proceed is False
    assert check.code == "WATERMARK_BATCH_ATTESTATION_MISMATCH"


def _v2_admission_summary() -> dict[str, object]:
    return {
        "schema_version": MANIFEST_SCHEMA_V2,
        "status": "verified",
        "entry_count": 2,
        "manual_attestation_count": 1,
        "watermark_batch_candidate_count": 2,
        "disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "variant": "watermark_only",
        "classifier_policy_id": POLICY_ID,
        "classifier_policy_sha256": POLICY_SHA256,
        "manifest_sha256": MANIFEST_SHA256,
        "exemplar_envelope_sha256": EXEMPLAR_ENVELOPE_SHA256,
        "exemplar_canonical_bitmap_sha256": EXEMPLAR_BITMAP_SHA256,
        "prescan_receipt_sha256": PRESCAN_RECEIPT_SHA256,
        "candidate_set_sha256": CANDIDATE_SET_SHA256,
        "sample_set_sha256": SAMPLE_SET_SHA256,
        "batch_envelope_sha256": BATCH_ENVELOPE_SHA256,
        "public_key_sha256": PUBLIC_KEY_SHA256,
        "signature_verified": True,
    }


def _safe_v4_proof(*, runtime_unseen_count: int = 0) -> dict[str, Any]:
    attestation = _v2_admission_summary()
    proof = _safe_gate_receipt(
        attestation=attestation,
        variant_counts={"blank": 0, "illegible": 0, "watermark_only": 2},
    )
    proof["schema_version"] = DURABLE_PROOF_SCHEMA_V4
    proof["no_substantive_content_attestation_sha256"] = gate._canonical_json_sha256(attestation)
    proof["confirmed_no_substantive_content_receipts_sha256"] = CLASSIFICATION_RECEIPTS_SHA256
    parser_counts = proof["parser_counts"]
    assert isinstance(parser_counts, dict)
    parser_counts.update(
        {
            "parsed_file_count": 265,
            "parsed_span_bearing_file_count": 265,
            "confirmed_no_substantive_content_file_count": 2,
            "durable_span_file_count": 265,
            "ocr_file_count": 42,
            "pdf_ocr_file_count": 40,
            "pdf_ocr_admission_file_count": 42,
            "pdf_ocr_admission_page_count": 284,
            "pdf_ocr_execution_page_count": 282,
            "pdf_classifier_first_file_count": 2,
            "pdf_classifier_only_file_count": 2,
            "pdf_classifier_only_page_count": 2,
            "pdf_classifier_first_page_count": 2,
            "pdf_classifier_terminal_file_count": 2,
            "manual_v1_confirmed_document_count": 0,
            "watermark_classifier_document_count": 2,
            "watermark_classified_page_count": 2,
            "watermark_batch_attested_page_count": 2 - runtime_unseen_count,
            "watermark_runtime_unseen_page_count": runtime_unseen_count,
        }
    )
    proof.update(
        {
            "watermark_classified_pages_by_company": {
                "company-001": 1,
                "company-002": 1,
            },
            "watermark_classification_receipts_sha256": (CLASSIFICATION_RECEIPTS_SHA256),
            "watermark_manifest_bound_receipt_count": 2,
            "watermark_manifest_runtime_summary_sha256": hashlib.sha256(
                b"fictional-verified-watermark-runtime-summary"
            ).hexdigest(),
            "watermark_runtime_review_required": runtime_unseen_count > 0,
        }
    )
    return proof


def test_safe_v4_durable_proof_accepts_only_derived_hash_bound_watermark_aggregates() -> None:
    proof = _safe_v4_proof()

    gate._validate_safe_durable_ingestion_receipt(proof)

    assert proof["schema_version"] == DURABLE_PROOF_SCHEMA_V4
    assert proof["parser_counts"]["watermark_classified_page_count"] == 2
    assert proof["parser_counts"]["watermark_batch_attested_page_count"] == 2
    assert proof["parser_counts"]["manual_v1_confirmed_document_count"] == 0
    assert proof["watermark_runtime_review_required"] is False


def test_runtime_unseen_page_keeps_final_v4_acceptance_fail_closed() -> None:
    proof = _safe_v4_proof(runtime_unseen_count=1)

    with pytest.raises(gate.DurableIngestionGateError) as caught:
        gate._validate_safe_durable_ingestion_receipt(proof)

    assert caught.value.code == "DATA_ROOM_RECONCILIATION_INCOMPLETE"


def _walk(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)
    elif isinstance(value, str):
        yield value


def test_safe_v4_proof_contains_no_private_watermark_material() -> None:
    proof = _safe_v4_proof()
    private_canary = "fictional-private-recipient@example.invalid"
    serialized = json.dumps(proof, sort_keys=True)

    for forbidden_key in (
        "source_path",
        "private_filename",
        "raw_text",
        "attestation_text",
        "signature_base64",
        "exemplar_canonical_bitmap_base64",
        "stamp_email",
        "stamp_timestamp",
    ):
        assert forbidden_key not in set(_walk(proof))
    assert private_canary not in serialized

    poisoned = copy.deepcopy(proof)
    poisoned["no_substantive_content_attestation"]["signature_base64"] = private_canary
    with pytest.raises(gate.DurableIngestionGateError) as caught:
        gate._validate_safe_durable_ingestion_receipt(poisoned)
    assert caught.value.code == "DATA_ROOM_RECONCILIATION_INCOMPLETE"


def test_openapi_refreshes_singular_wording_and_locks_reviewed_surface_expansion() -> None:
    root = Path(__file__).resolve().parents[2]
    spec_path = root / "openapi" / "IDIS_OpenAPI_v6_3.yaml"
    lock_path = root / "contracts" / "contract_lock.json"
    spec_text = spec_path.read_text(encoding="utf-8")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))

    assert "signed one-document" not in spec_text
    assert "signed no-substantive-content admission" in spec_text
    assert (
        hashlib.sha256(spec_path.read_bytes()).hexdigest()
        == lock["files"]["openapi/IDIS_OpenAPI_v6_3.yaml"]
    )
    surface_sha256 = hashlib.sha256(
        json.dumps(
            lock["openapi_surface"],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert surface_sha256 == OPENAPI_SURFACE_SHA256


def test_manifest_v2_reuses_existing_profile_path_without_new_watermark_env() -> None:
    root = Path(__file__).resolve().parents[2]
    example_env = (root / ".env.example").read_text(encoding="utf-8")

    assert "IDIS_REAL_EXAMPLE_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MANIFEST_PATH" in (example_env)
    assert "IDIS_WATERMARK_" not in example_env

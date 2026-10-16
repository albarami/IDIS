"""Focused RED contracts for the truthful no-substantive-content boundary."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from idis.api.routes.documents import _document_summary_from_durable_row
from idis.evaluation import real_example_gate as gate
from idis.runtime import governance_envelopes
from idis.services.ingestion.no_substantive_content_attestation import (
    DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
    VerifiedDocumentNoSubstantiveContentAttestation,
    build_document_no_substantive_content_receipt_verifier,
    project_safe_document_no_substantive_content_receipt,
)
from idis.services.ingestion.service import IngestionService
from idis.services.runs import document_preflight as document_preflight_module
from idis.services.runs.orchestrator import RunContext, RunOrchestrator

_TENANT_ID = "30000000-0000-4000-8000-000000000001"
_DEAL_ID = "40000000-0000-4000-8000-000000000001"
_DOCUMENT_ID = "50000000-0000-4000-8000-000000000001"
_ARTIFACT_ID = "60000000-0000-4000-8000-000000000001"
_SAFE_ALIAS = "company-002-document-00053.pdf"


def _private_pem() -> bytes:
    return Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _governance_payload(*, variant: object) -> dict[str, object]:
    return {
        "tenant_id": _TENANT_ID,
        "source_tree_fingerprint": "1" * 64,
        "safe_alias": _SAFE_ALIAS,
        "content_sha256": "2" * 64,
        "disposition": DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
        "variant": variant,
        "attestation_text_sha256": "3" * 64,
        "reviewer_approval": {
            "decision": "APPROVED",
            "reviewer_id": "fictional-reviewer-001",
            "approved_at": "2026-07-17T10:00:00Z",
            "expires_at": "2026-07-18T10:00:00Z",
        },
    }


def _terminal_receipt() -> dict[str, object]:
    return {
        "schema_version": "document-no-substantive-content-receipt-v1",
        "disposition": DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
        "variant": "watermark_only",
        "safe_alias": _SAFE_ALIAS,
        "content_sha256": "2" * 64,
        "source_tree_fingerprint": "1" * 64,
        "attestation_text_sha256": "3" * 64,
        "reviewer_id": "fictional-reviewer-001",
        "approved_at": "2026-07-17T10:00:00Z",
        "expires_at": "2026-07-18T10:00:00Z",
        "signer_id": "fictional-governance-signer",
        "payload_sha256": "4" * 64,
        "envelope_sha256": "5" * 64,
        "public_key_sha256": "6" * 64,
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
        "ocr_result_sha256": "7" * 64,
    }


def _verified_attestation() -> VerifiedDocumentNoSubstantiveContentAttestation:
    return VerifiedDocumentNoSubstantiveContentAttestation(
        schema_version="document-no-substantive-content-attestation-v1",
        signer_id="fictional-governance-signer",
        tenant_id=_TENANT_ID,
        source_tree_fingerprint="1" * 64,
        safe_alias=_SAFE_ALIAS,
        content_sha256="2" * 64,
        disposition=DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
        variant="watermark_only",
        attestation_text_sha256="3" * 64,
        reviewer_id="fictional-reviewer-001",
        approved_at_text="2026-07-17T10:00:00Z",
        expires_at_text="2026-07-18T10:00:00Z",
        approved_at=datetime(2026, 7, 17, 10, tzinfo=UTC),
        expires_at=datetime(2026, 7, 18, 10, tzinfo=UTC),
        payload_sha256="4" * 64,
        envelope_sha256="5" * 64,
        public_key_sha256="6" * 64,
    )


def _safe_gate_receipt(
    *,
    attestation: Mapping[str, object],
    variant_counts: Mapping[str, int],
) -> dict[str, object]:
    isolation = {
        "status": "passed",
        "company_count": 2,
        "denial_count": 18,
        "surface_count": 9,
        "post_mutation_state_verified": True,
    }
    return {
        "schema_version": "slice100-task4-durable-ingestion-proof-v3",
        "status": "ready",
        "phase": "prepared",
        "total_file_count": 267,
        "total_bytes": gate.DEFAULT_EXPECTED_TOTAL_BYTES,
        "bytes_by_company": {
            "company-001": gate.DEFAULT_EXPECTED_COMPANY_BYTES[0],
            "company-002": gate.DEFAULT_EXPECTED_COMPANY_BYTES[1],
        },
        "source_tree_fingerprint": "a" * 64,
        "preparation_ledger_sha256": "b" * 64,
        "terminal_rows_sha256": "c" * 64,
        "plan_sha256": "d" * 64,
        "code_sha256": "e" * 64,
        "reconciliation_sha256": "f" * 64,
        "profile_hmac_sha256": "1" * 64,
        "local_runtime_readiness_sha256": "2" * 64,
        "no_substantive_content_attestation": dict(attestation),
        "no_substantive_content_attestation_sha256": gate._canonical_json_sha256(attestation),
        "confirmed_no_substantive_content_receipts_sha256": "9" * 64,
        "confirmed_no_substantive_content_variant_counts": dict(variant_counts),
        "api_boot_sha256": "3" * 64,
        "previous_api_boot_sha256": None,
        "authenticated_download_company_count": 2,
        "authenticated_download_receipts_sha256": "4" * 64,
        "parser_counts": {
            "parsed_file_count": 266,
            "parsed_span_bearing_file_count": 266,
            "confirmed_no_substantive_content_file_count": 1,
            "durable_span_file_count": 266,
            "durable_object_file_count": 267,
            "ocr_file_count": 44,
            "pdf_ocr_file_count": 42,
            "stt_file_count": 8,
            "media_dual_receipt_file_count": 8,
            "stt_positive_file_count": 5,
            "frame_ocr_positive_file_count": 3,
            "large_streamed_file_count": 3,
        },
        "upload_post_count": 267,
        "tenant_isolation": isolation,
        "tenant_isolation_receipt_sha256": gate._canonical_json_sha256(isolation),
        "restart_survival_verified": False,
    }


def test_confirmation_audit_persists_the_exact_reviewed_variant() -> None:
    captured: list[dict[str, Any]] = []

    class Capture:
        @staticmethod
        def _emit_audit_event(event: dict[str, Any], *, db_conn: object | None = None) -> None:
            del db_conn
            captured.append(event)

    ctx = SimpleNamespace(
        tenant_id=_TENANT_ID,
        actor_id="fictional-reviewer-001",
        actor_type="HUMAN",
        actor_roles=("REVIEWER",),
        request_id="70000000-0000-4000-8000-000000000001",
        idempotency_key="fictional-idempotency-key",
    )
    document = SimpleNamespace(document_id=_DOCUMENT_ID, deal_id=_DEAL_ID)
    receipt = _terminal_receipt()

    IngestionService._emit_no_substantive_content_confirmed(
        Capture(),  # type: ignore[arg-type]
        ctx,
        document,
        receipt,
        str(receipt["content_sha256"]),
    )

    assert len(captured) == 1
    assert captured[0]["event_type"] == "document.no_substantive_content.confirmed"
    assert captured[0]["payload"]["safe"]["variant"] == "watermark_only"


@pytest.mark.parametrize(
    "attestation",
    [
        {
            "schema_version": "document-no-substantive-content-attestation-v1",
            "status": "verified",
            "entry_count": 1,
            "disposition": DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
            "variant": "blank",
            "payload_sha256": "6" * 64,
            "envelope_sha256": "7" * 64,
            "public_key_sha256": "8" * 64,
            "signature_verified": True,
        },
        {"status": "not_configured", "entry_count": 0},
    ],
    ids=("variant-drift", "unconfigured-with-confirmed-row"),
)
def test_safe_gate_receipt_cross_binds_attestation_variant_to_variant_census(
    attestation: Mapping[str, object],
) -> None:
    receipt = _safe_gate_receipt(
        attestation=attestation,
        variant_counts={"blank": 0, "illegible": 0, "watermark_only": 1},
    )

    with pytest.raises(gate.DurableIngestionGateError) as caught:
        gate._validate_safe_durable_ingestion_receipt(receipt)

    assert caught.value.code == "DATA_ROOM_RECONCILIATION_INCOMPLETE"


@pytest.mark.parametrize("variant", [None, True, 7, [], {}])
def test_non_string_attestation_variant_fails_with_stable_schema_code(variant: object) -> None:
    with pytest.raises(governance_envelopes.GovernanceEnvelopeError) as caught:
        governance_envelopes.sign_envelope(
            schema_version="document-no-substantive-content-attestation-v1",
            signer_id="fictional-governance-signer",
            payload=_governance_payload(variant=variant),
            private_key_pem=_private_pem(),
        )

    assert caught.value.code == governance_envelopes.GOVERNANCE_SCHEMA_INVALID


@pytest.mark.parametrize(
    "mutation",
    [
        {"safe_alias": "fictional-private-original-name.pdf"},
        {"reviewer_id": ""},
        {"signer_id": ""},
        {
            "approved_at": "2026-07-18T10:00:00Z",
            "expires_at": "2026-07-17T10:00:00Z",
        },
    ],
    ids=("private-alias", "empty-reviewer", "empty-signer", "inverted-window"),
)
def test_public_receipt_projector_rejects_noncanonical_or_incoherent_receipt(
    mutation: Mapping[str, object],
) -> None:
    receipt = {**_terminal_receipt(), **mutation}

    assert project_safe_document_no_substantive_content_receipt(receipt) is None

    row = {
        "doc_id": _ARTIFACT_ID,
        "document_id": _DOCUMENT_ID,
        "deal_id": _DEAL_ID,
        "artifact_doc_type": "DATA_ROOM_FILE",
        "doc_type": "PDF",
        "document_name": _SAFE_ALIAS,
        "created_at": datetime.now(UTC).isoformat(),
        "parse_status": "FAILED",
        "sha256": "2" * 64,
        "uri": "tenant-safe/object.pdf",
        "source_metadata": {},
        "metadata": {"document_no_substantive_content_receipt": receipt},
    }
    summary = _document_summary_from_durable_row(row)
    assert summary.terminal_disposition is None
    assert summary.metadata == {}


def test_public_summary_suppresses_legacy_and_private_attestation_fields() -> None:
    private_canary = "reviewer-private@example.invalid"
    row = {
        "doc_id": _ARTIFACT_ID,
        "document_id": _DOCUMENT_ID,
        "deal_id": _DEAL_ID,
        "artifact_doc_type": "DATA_ROOM_FILE",
        "doc_type": "PDF",
        "document_name": _SAFE_ALIAS,
        "created_at": datetime.now(UTC).isoformat(),
        "parse_status": "PARSED",
        "sha256": "2" * 64,
        "uri": "tenant-safe/object.pdf",
        "source_metadata": {
            "source_system": "slice100",
            "document_no_substantive_content_attestation_manifest_path": private_canary,
        },
        "metadata": {
            "safe_field": "kept",
            "document_illegibility_receipt": {
                "disposition": "DOCUMENT_ILLEGIBLE_CONFIRMED",
                "reviewer_email": private_canary,
            },
            "document_no_substantive_content_attestation": {
                "safe_alias": "private-original-name.pdf",
                "attestation_text": private_canary,
            },
            "document_no_substantive_content_attestation_envelope": {
                "signature": private_canary,
            },
            "document_no_substantive_content_attestation_signature": private_canary,
            "document_no_substantive_content_attestation_manifest_path": private_canary,
        },
    }

    summary = _document_summary_from_durable_row(row)

    assert summary.metadata == {"safe_field": "kept"}
    assert summary.source_metadata == {"source_system": "slice100"}
    assert private_canary not in summary.model_dump_json()


def test_default_orchestrator_passes_current_receipt_verifier_to_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def verifier(_document: Mapping[str, Any]) -> bool:
        return True

    class Result:
        @staticmethod
        def to_run_step_summary() -> dict[str, object]:
            return {"status": "COMPLETED", "excluded_confirmed_documents": 1}

    class Preflight:
        def __init__(
            self,
            *,
            no_substantive_content_receipt_verifier: object,
        ) -> None:
            observed["verifier"] = no_substantive_content_receipt_verifier

        @staticmethod
        def run(**kwargs: object) -> tuple[Result, list[dict[str, object]]]:
            corpus = kwargs["corpus"]
            assert isinstance(corpus, list)
            return Result(), corpus

    monkeypatch.setattr(
        document_preflight_module,
        "InMemoryRunDocumentPreflightService",
        Preflight,
    )
    ctx = RunContext(
        run_id="80000000-0000-4000-8000-000000000001",
        tenant_id=_TENANT_ID,
        deal_id=_DEAL_ID,
        mode="FULL",
        documents=[{"document_id": _DOCUMENT_ID}],
        preflight_corpus=[{"document_id": _DOCUMENT_ID}],
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
        no_substantive_content_receipt_verifier=verifier,
    )

    summary = RunOrchestrator._execute_document_preflight(
        object.__new__(RunOrchestrator),
        ctx,
    )

    assert summary["status"] == "COMPLETED"
    assert observed["verifier"] is verifier


def test_current_receipt_verifier_binds_the_signed_variant_and_source_row() -> None:
    verifier = build_document_no_substantive_content_receipt_verifier(
        _verified_attestation(),
        clock=lambda: datetime(2026, 7, 17, 12, tzinfo=UTC),
    )
    document = {
        "tenant_id": _TENANT_ID,
        "document_name": _SAFE_ALIAS,
        "sha256": "2" * 64,
        "source_metadata": {
            "source_tree_fingerprint": "1" * 64,
            "upload_filename": _SAFE_ALIAS,
        },
        "metadata": {"document_no_substantive_content_receipt": _terminal_receipt()},
    }

    assert verifier(document) is True
    drifted = {
        **document,
        "metadata": {
            "document_no_substantive_content_receipt": {
                **_terminal_receipt(),
                "variant": "blank",
            }
        },
    }
    assert verifier(drifted) is False


def test_worker_composition_loads_and_threads_the_profile_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.services.ingestion.no_substantive_content_attestation as attestation_module
    from idis.pipeline import worker
    from idis.services.runs import steps

    attestation = _verified_attestation()

    def verifier(_document: object) -> bool:
        return True

    observed: dict[str, object] = {}
    profile = SimpleNamespace(values={"IDIS_ACCEPTANCE_TENANT_ID": _TENANT_ID})
    monkeypatch.setattr(worker, "_load_worker_preflight_corpus", lambda **_kwargs: [])
    monkeypatch.setattr(worker, "_load_worker_deal_metadata", lambda **_kwargs: None)
    monkeypatch.setattr(
        attestation_module,
        "load_document_no_substantive_content_attestation",
        lambda *, env: observed.setdefault("load_env", env) and attestation,
    )
    monkeypatch.setattr(
        attestation_module,
        "build_document_no_substantive_content_receipt_verifier",
        lambda value: observed.setdefault("attestation", value) and verifier,
        raising=False,
    )
    monkeypatch.setattr(
        steps,
        "build_run_context",
        lambda **kwargs: observed.setdefault("context", kwargs),
    )
    monkeypatch.setattr(
        steps,
        "extraction_ready_documents_from_preflight_corpus",
        lambda _corpus: [],
    )

    result = worker._default_run_context_factory(
        db_conn=object(),
        tenant_id=_TENANT_ID,
        run_data={
            "run_id": "80000000-0000-4000-8000-000000000001",
            "deal_id": _DEAL_ID,
            "mode": "SNAPSHOT",
            "created_by_actor_id": None,
            "created_by_actor_type": None,
            "strict_preflight_proof_id": None,
            "lease_owner_id": None,
            "lease_fencing_token": None,
        },
        audit_sink=object(),
        strict_profile=profile,  # type: ignore[arg-type]
    )

    assert result is observed["context"]
    assert observed["load_env"] is profile.values
    assert observed["attestation"] is attestation
    assert observed["context"]["no_substantive_content_receipt_verifier"] is verifier  # type: ignore[index]

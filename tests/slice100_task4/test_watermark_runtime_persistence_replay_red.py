"""RED contracts for watermark admission persistence, audit, and replay binding.

The fixtures are fictional and exercise only local post-parser boundaries.  No
API server, database, network, or private data-room path is used.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from idis.api.auth import TenantContext
from idis.api.document_upload_replay import (
    DocumentUploadReplayInvariantError,
    validate_upload_replay_invariant,
)
from idis.api.middleware.idempotency import _cached_confirmed_no_substantive_content_body
from idis.audit.sink import InMemoryAuditSink
from idis.evaluation import real_example_source_package as source_package
from idis.evaluation.real_example_source_package import _preparation_idempotency_key
from idis.idempotency.store import IdempotencyRecord
from idis.parsers.base import ParseResult, SpanDraft
from idis.persistence.repositories.runs import _resolve_run_contract_identity
from idis.services.ingestion.service import (
    IngestionContext,
    IngestionResult,
    IngestionService,
)
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.slice100_task4.test_no_substantive_content_ingestion_persistence import (
    OCR_RESULT_SHA256,
    _complete_no_consensus_result,
)
from tests.slice100_task4.test_watermark_batch_attestation_red import (
    ACTIVE_NOW,
    MANDATORY_ALIAS,
    POLICY_SHA256,
    POLICY_VERSION,
    SIGNER_ID,
    SOURCE_TREE_FINGERPRINT,
    _manifest,
    _prescan_receipt,
    _private_and_public_pem,
    _sha256,
    _valid_batch_payload,
)

RUNTIME_API_MISSING = "TASK4_WATERMARK_RUNTIME_API_MISSING"
TENANT_ID = UUID("30000000-0000-4000-8000-000000000001")
DEAL_ID = UUID("40000000-0000-4000-8000-000000000001")
DOCUMENT_ID = "50000000-0000-4000-8000-000000000001"
ARTIFACT_ID = "60000000-0000-4000-8000-000000000001"
# These bytes exercise storage and an explicit post-parser component input. They
# are not represented as an actually parsed PDF/OCR result or a native corpus.
DOCUMENT_BYTES = b"PUBLIC SYNTHETIC POST-PARSER WATERMARK COMPONENT OBJECT\n"
MANDATORY_CONTENT_SHA256 = hashlib.sha256(DOCUMENT_BYTES).hexdigest()
MANDATORY_RENDER_SHA256 = "a" * 64


def _runtime_api(name: str) -> Any:
    module = importlib.import_module("idis.services.ingestion.no_substantive_content_attestation")
    value = getattr(module, name, None)
    if value is None:
        pytest.fail(f"{RUNTIME_API_MISSING}: {module.__name__}.{name}", pytrace=False)
    return value


def _verified_manifest() -> object:
    private_pem, public_pem = _private_and_public_pem()
    batch_payload = _valid_batch_payload(private_pem)
    candidates = batch_payload["candidates"]
    assert isinstance(candidates, list)
    mandatory = [row for row in candidates if row["safe_alias"] == MANDATORY_ALIAS]
    assert len(mandatory) == 1
    mandatory[0]["content_sha256"] = MANDATORY_CONTENT_SHA256
    candidate_hash = _sha256(candidates)
    prescan = _prescan_receipt()
    prescan.update(
        candidate_count=len(candidates),
        candidate_set_sha256=candidate_hash,
        sample_set_sha256=candidate_hash,
    )
    batch_payload.update(
        candidate_set_sha256=candidate_hash,
        sample_set_sha256=candidate_hash,
        prescan_receipt_sha256=_sha256(prescan),
    )
    verifier = _runtime_api("verify_document_no_substantive_content_attestation_manifest")
    return verifier(
        manifest=_manifest(private_pem, batch_payload=batch_payload, prescan_receipt=prescan),
        trusted_signer_id=SIGNER_ID,
        trusted_public_key_pem=public_pem,
        now=ACTIVE_NOW,
    )


def _classification(admission: object) -> dict[str, object]:
    exemplar_envelope_sha256 = getattr(admission, "exemplar_envelope_sha256", None)
    exemplar = getattr(admission, "exemplar", None)
    exemplar_content_sha256 = getattr(exemplar, "content_sha256", None)
    batch = getattr(admission, "batch", None)
    batch_payload = getattr(batch, "payload", None)
    if (
        not isinstance(exemplar_envelope_sha256, str)
        or not isinstance(exemplar_content_sha256, str)
        or not isinstance(batch_payload, dict)
    ):
        pytest.fail(
            f"{RUNTIME_API_MISSING}: verified manifest omitted runtime exemplar bindings",
            pytrace=False,
        )
    exemplar_bitmap_sha256 = batch_payload.get("exemplar_canonical_bitmap_sha256")
    if not isinstance(exemplar_bitmap_sha256, str):
        pytest.fail(
            f"{RUNTIME_API_MISSING}: verified manifest omitted canonical exemplar hash",
            pytrace=False,
        )
    return {
        "schema_version": "watermark-page-classification-v1",
        "classification": "watermark_only",
        "content_sha256": MANDATORY_CONTENT_SHA256,
        "page_number": 1,
        "policy_id": POLICY_VERSION,
        "policy_sha256": POLICY_SHA256,
        "exemplar_reference": {
            "exemplar_id": "company-002-document-00053.pdf:page-1",
            "content_sha256": exemplar_content_sha256,
            "page_number": 1,
            "attestation_envelope_sha256": exemplar_envelope_sha256,
            "canonical_bitmap_sha256": exemplar_bitmap_sha256,
            "classifier_policy_sha256": POLICY_SHA256,
        },
        "transform_chain": [
            "poppler-200dpi",
            "convert-grayscale-L",
            "measure-near-white-ge-245",
            "autocontrast-cutoff-0",
            "measure-foreground-lt-224",
            "resize-grayscale-128x128-LANCZOS",
            "binary-foreground-lt-224",
            "foreground-dice",
        ],
        "confidence_distribution": {
            "near_white_fraction": "0.978048",
            "foreground_ink_fraction": "0.022133",
        },
        "transform_bounds": {
            "near_white_fraction_min": "0.970000",
            "near_white_fraction_max": "0.985000",
            "foreground_ink_fraction_min": "0.015000",
            "foreground_ink_fraction_max": "0.030000",
            "similarity_min": "0.800000",
        },
        "similarity_metric": "foreground_dice",
        "similarity_score": "0.819404",
        "similarity_numerator": 13_424,
        "similarity_denominator": 16_383,
        "canonical_render_sha256": MANDATORY_RENDER_SHA256,
        "ocr_terminal_proof_sha256": OCR_RESULT_SHA256,
    }


def _terminal_parse_result(admission: object) -> ParseResult:
    result = _complete_no_consensus_result()
    result.metadata["watermark_page_classifications"] = [_classification(admission)]
    return result


def _mixed_parse_result(admission: object) -> ParseResult:
    text = "Fictional substantive business evidence on page two."
    return ParseResult(
        doc_type="PDF",
        success=True,
        spans=[
            SpanDraft(
                span_type="PAGE_TEXT",
                locator={"page_number": 2, "source": "native_text"},
                text_excerpt=text,
            )
        ],
        metadata={
            "page_count": 2,
            "span_count": 1,
            "native_text_page_count": 1,
            "accepted_text_page_count": 1,
            "watermark_page_classifications": [_classification(admission)],
        },
    )


def _service(
    tmp_path: Path,
) -> tuple[IngestionService, InMemoryAuditSink, object]:
    audit = InMemoryAuditSink()
    admission = _verified_manifest()
    service = IngestionService(
        compliant_store=ComplianceEnforcedStore(
            inner_store=FilesystemObjectStore(base_dir=tmp_path / "objects")
        ),
        audit_sink=audit,
        no_substantive_content_attestation=admission,
    )
    return service, audit, admission


def _materialize(
    service: IngestionService,
    parse_result: ParseResult,
) -> IngestionResult:
    import idis.services.ingestion.service as ingestion_module

    tenant_ctx = TenantContext(
        tenant_id=str(TENANT_ID),
        actor_id="fictional-slice100-worker",
        name="Synthetic component",
        timezone="UTC",
        data_region="me-south-1",
    )
    key = f"synthetic-components/{TENANT_ID}/{MANDATORY_CONTENT_SHA256}/{MANDATORY_ALIAS}"
    stored = service._compliant_store.put(
        tenant_ctx=tenant_ctx,
        key=key,
        data=DOCUMENT_BYTES,
        content_type="application/octet-stream",
    )
    assert stored.key == key and stored.sha256 == MANDATORY_CONTENT_SHA256
    assert stored.size_bytes == len(DOCUMENT_BYTES)
    assert stored.content_type == "application/octet-stream"
    assert str(UUID(stored.version_id)) == stored.version_id
    assert (
        service._compliant_store.verify_integrity(
            tenant_ctx=tenant_ctx,
            key=key,
            version_id=stored.version_id,
        )
        == stored
    )
    # Honest brackets around an already supplied component ParseResult, not a
    # claim that a parser/OCR process ran. The injected fixed clock gives equality.
    started_at = ingestion_module.datetime.now(UTC)
    finished_at = ingestion_module.datetime.now(UTC)
    assert started_at == finished_at == ACTIVE_NOW
    identity = service._runtime_parser_identity(parse_result)
    attempts: list[dict[str, Any]] = []
    actual_attempt = service._persist_runtime_attempt

    def observe_attempt(**kwargs: Any) -> str:
        attempts.append(kwargs)
        return actual_attempt(**kwargs)

    service._persist_runtime_attempt = observe_attempt  # type: ignore[method-assign]
    try:
        result = service._materialize_parse_result(
            ctx=IngestionContext(
                tenant_id=TENANT_ID,
                actor_id="fictional-slice100-worker",
                request_id="fictional-watermark-runtime-request",
                idempotency_key="fictional-watermark-runtime-key",
            ),
            deal_id=DEAL_ID,
            filename=MANDATORY_ALIAS,
            metadata={
                "upload_filename": MANDATORY_ALIAS,
                "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
            },
            sha256=MANDATORY_CONTENT_SHA256,
            storage_key=key,
            parse_result=parse_result,
            stored_object=stored,
            parser_started_at=started_at,
            parser_finished_at=finished_at,
            phase_recorder=None,
            db_conn=None,
        )
    finally:
        service._persist_runtime_attempt = actual_attempt  # type: ignore[method-assign]
        # Even a rejected attempted invocation is forbidden for these supplied
        # post-parser component results; restoration must also survive failure.
        assert attempts == []
    assert result.artifact_id is not None and result.document_id is not None
    artifact = service.get_artifact(TENANT_ID, result.artifact_id)
    document = service.get_document(TENANT_ID, result.document_id)
    assert artifact is not None and document is not None
    assert artifact.metadata["durable_object_integrity"]["version_id"] == stored.version_id
    assert artifact.metadata["size_bytes"] == len(DOCUMENT_BYTES)
    if identity is None:
        prerequisite = "PARSER_NATIVE_IDENTITY_UNAVAILABLE"
    else:
        prerequisite = "PARSER_TERMINAL_RECEIPT_UNAVAILABLE"
    assert document.metadata["source_preparation_prerequisite"] == prerequisite
    assert artifact.metadata["source_preparation_prerequisite"] == prerequisite
    for metadata in (document.metadata, artifact.metadata):
        assert (
            not {
                "parser_started_at",
                "parser_finished_at",
                "parser_duration_seconds",
                "duration_seconds",
            }
            & metadata.keys()
        )
    return result


def _persisted_receipt(
    tmp_path: Path,
    *,
    parse_result_factory: Callable[[object], ParseResult] = _terminal_parse_result,
) -> tuple[IngestionService, InMemoryAuditSink, IngestionResult, dict[str, Any]]:
    service, audit, admission = _service(tmp_path)
    result = _materialize(service, parse_result_factory(admission))
    assert result.document_id is not None
    document = service.get_document(TENANT_ID, result.document_id)
    assert document is not None
    receipt = document.metadata.get("document_no_substantive_content_receipt")
    if not isinstance(receipt, dict):
        pytest.fail(f"{RUNTIME_API_MISSING}: durable receipt v2 was not persisted", pytrace=False)
    return service, audit, result, receipt


def _event_types(audit: InMemoryAuditSink) -> list[str]:
    return [str(event["event_type"]) for event in audit.events]


def test_terminal_watermark_classification_persists_v2_and_safe_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "idis.services.ingestion.service.datetime",
        type("FixedDateTime", (), {"now": staticmethod(lambda _tz: ACTIVE_NOW)}),
    )
    service, audit, result, receipt = _persisted_receipt(tmp_path)

    assert result.success is False
    assert result.terminal_disposition == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"
    assert result.span_count == 0
    assert service.get_spans(TENANT_ID, result.document_id) == []
    assert receipt["schema_version"] == "document-no-substantive-content-receipt-v2"
    assert receipt["whole_document_terminal"] is True
    assert receipt["classified_page_count"] == 1
    assert receipt["substantive_span_count"] == 0
    assert receipt["watermark_page_classifications"][0]["batch_membership"] == "ATTESTED"

    assert "document.created" in _event_types(audit)
    assert "document.ingestion.failed" in _event_types(audit)
    assert "document.watermark_page.classified" in _event_types(audit)
    assert "document.no_substantive_content.confirmed" in _event_types(audit)
    serialized = json.dumps(audit.events, sort_keys=True)
    for forbidden in ("signature_base64", "exemplar_canonical_bitmap_base64", "raw_text"):
        assert forbidden not in serialized


def test_mixed_document_excludes_only_watermark_page_and_keeps_substantive_span(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "idis.services.ingestion.service.datetime",
        type("FixedDateTime", (), {"now": staticmethod(lambda _tz: ACTIVE_NOW)}),
    )
    service, audit, result, receipt = _persisted_receipt(
        tmp_path,
        parse_result_factory=_mixed_parse_result,
    )

    assert result.success is True
    assert result.parse_status is not None and result.parse_status.value == "PARSED"
    assert result.terminal_disposition is None
    assert result.span_count == 1
    assert len(service.get_spans(TENANT_ID, result.document_id)) == 1
    assert receipt["whole_document_terminal"] is False
    assert receipt["page_count"] == 2
    assert receipt["classified_page_count"] == 1
    assert receipt["substantive_span_count"] == 1
    assert "document.ingestion.completed" in _event_types(audit)
    assert "document.watermark_page.classified" in _event_types(audit)
    assert "document.no_substantive_content.confirmed" not in _event_types(audit)


def _replay_state(receipt: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    source_metadata = {
        "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        "upload_filename": MANDATORY_ALIAS,
    }
    body = {
        "document_id": DOCUMENT_ID,
        "doc_id": ARTIFACT_ID,
        "deal_id": str(DEAL_ID),
        "parse_status": "FAILED",
        "terminal_disposition": "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED",
        "title": MANDATORY_ALIAS,
        "sha256": MANDATORY_CONTENT_SHA256,
        "metadata": {"document_no_substantive_content_receipt": receipt},
        "source_metadata": source_metadata,
    }
    return (
        {"status_code": 201, "media_type": "application/json", "body": body},
        {
            "tenant_id": str(TENANT_ID),
            "document_id": DOCUMENT_ID,
            "doc_id": ARTIFACT_ID,
            "deal_id": str(DEAL_ID),
            "parse_status": "FAILED",
            "span_count": 0,
            "spans": [],
            "metadata": {"document_no_substantive_content_receipt": receipt},
        },
    )


def _validate_replay(
    *,
    cached_response: dict[str, Any],
    durable_document: dict[str, Any],
    admission: object,
) -> None:
    validate_upload_replay_invariant(
        cached_response=cached_response,
        durable_document=durable_document,
        attestation=admission,
        tenant_id=str(TENANT_ID),
        source_tree_fingerprint=SOURCE_TREE_FINGERPRINT,
        safe_alias=MANDATORY_ALIAS,
        content_sha256=MANDATORY_CONTENT_SHA256,
        now=ACTIVE_NOW,
    )


def test_cached_terminal_response_replays_only_against_current_v2_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "idis.services.ingestion.service.datetime",
        type("FixedDateTime", (), {"now": staticmethod(lambda _tz: ACTIVE_NOW)}),
    )
    service, _audit, _result, receipt = _persisted_receipt(tmp_path)
    admission = service._no_substantive_content_attestation
    cached, durable = _replay_state(receipt)

    _validate_replay(
        cached_response=cached,
        durable_document=durable,
        admission=admission,
    )


def test_cached_mixed_document_replays_only_with_durable_substantive_span(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "idis.services.ingestion.service.datetime",
        type("FixedDateTime", (), {"now": staticmethod(lambda _tz: ACTIVE_NOW)}),
    )
    service, _audit, _result, receipt = _persisted_receipt(
        tmp_path,
        parse_result_factory=_mixed_parse_result,
    )
    source_metadata = {
        "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        "upload_filename": MANDATORY_ALIAS,
    }
    body = {
        "document_id": DOCUMENT_ID,
        "doc_id": ARTIFACT_ID,
        "deal_id": str(DEAL_ID),
        "parse_status": "PARSED",
        "terminal_disposition": None,
        "title": MANDATORY_ALIAS,
        "sha256": MANDATORY_CONTENT_SHA256,
        "metadata": {"document_no_substantive_content_receipt": receipt},
        "source_metadata": source_metadata,
    }
    cached = {"status_code": 201, "media_type": "application/json", "body": body}
    durable = {
        "tenant_id": str(TENANT_ID),
        "document_id": DOCUMENT_ID,
        "doc_id": ARTIFACT_ID,
        "deal_id": str(DEAL_ID),
        "parse_status": "PARSED",
        "span_count": 1,
        "spans": [
            {
                "span_id": "fictional-durable-substantive-span",
                "locator": {"page": 2, "source": "native_text"},
            }
        ],
        "metadata": {"document_no_substantive_content_receipt": receipt},
    }

    _validate_replay(
        cached_response=cached,
        durable_document=durable,
        admission=service._no_substantive_content_attestation,
    )

    routed = _cached_confirmed_no_substantive_content_body(
        IdempotencyRecord(
            payload_sha256="sha256:" + "f" * 64,
            status_code=201,
            media_type="application/json",
            body_bytes=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            created_at="2026-07-17T12:00:00Z",
        )
    )
    assert routed is not None
    assert routed["parse_status"] == "PARSED"

    span_on_excluded_page = copy.deepcopy(durable)
    span_on_excluded_page["spans"][0]["locator"]["page"] = 1
    with pytest.raises(DocumentUploadReplayInvariantError):
        _validate_replay(
            cached_response=cached,
            durable_document=span_on_excluded_page,
            admission=service._no_substantive_content_attestation,
        )


@pytest.mark.parametrize(
    "field",
    [
        "manifest_sha256",
        "classifier_policy_sha256",
        "candidate_set_sha256",
        "batch_envelope_sha256",
    ],
)
def test_replay_rejects_current_admission_binding_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    monkeypatch.setattr(
        "idis.services.ingestion.service.datetime",
        type("FixedDateTime", (), {"now": staticmethod(lambda _tz: ACTIVE_NOW)}),
    )
    service, _audit, _result, receipt = _persisted_receipt(tmp_path)
    drifted = copy.deepcopy(receipt)
    drifted[field] = "0" * 64
    cached, durable = _replay_state(drifted)

    with pytest.raises(DocumentUploadReplayInvariantError) as caught:
        _validate_replay(
            cached_response=cached,
            durable_document=durable,
            admission=service._no_substantive_content_attestation,
        )

    assert caught.value.code == "IDEMPOTENCY_REPLAY_INVARIANT_FAILED"


def _preparation_key(**bindings: str) -> str:
    try:
        key = _preparation_idempotency_key(
            SimpleNamespace(source_tree_fingerprint=SOURCE_TREE_FINGERPRINT),
            SimpleNamespace(company_alias="company-002"),
            operation="upload",
            discriminator=MANDATORY_ALIAS,
            **bindings,
        )
    except TypeError as error:
        pytest.fail(f"{RUNTIME_API_MISSING}: {error}", pytrace=False)
    if not isinstance(key, str):
        pytest.fail(f"{RUNTIME_API_MISSING}: idempotency key was not a string", pytrace=False)
    return key


def test_preparation_idempotency_binds_all_current_watermark_admission_digests() -> None:
    bindings = {
        "watermark_manifest_sha256": "1" * 64,
        "watermark_classifier_policy_sha256": "2" * 64,
        "watermark_candidate_set_sha256": "3" * 64,
        "watermark_batch_envelope_sha256": "4" * 64,
    }
    baseline = _preparation_key(**bindings)
    observed = {baseline}
    for field in bindings:
        drifted = {**bindings, field: "f" * 64}
        observed.add(_preparation_key(**drifted))

    assert len(observed) == 5


@pytest.mark.parametrize(
    ("parse_result_factory", "parse_status", "terminal_disposition"),
    [
        (_terminal_parse_result, "FAILED", "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"),
        (_mixed_parse_result, "PARSED", None),
    ],
    ids=("whole-document-terminal", "mixed-document-page-exclusion"),
)
def test_source_package_revalidates_v2_receipt_before_membership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    parse_result_factory: Callable[[object], ParseResult],
    parse_status: str,
    terminal_disposition: str | None,
) -> None:
    monkeypatch.setattr(
        "idis.services.ingestion.service.datetime",
        type("FixedDateTime", (), {"now": staticmethod(lambda _tz: ACTIVE_NOW)}),
    )
    # The same newly signed component authority is revalidated at its declared
    # fixture instant, not the wall clock. Its signed expiry is not extended.
    monkeypatch.setattr(
        source_package,
        "datetime",
        type("FixedDateTime", (), {"now": staticmethod(lambda _tz: ACTIVE_NOW)}),
    )
    service, _audit, _result, receipt = _persisted_receipt(
        tmp_path,
        parse_result_factory=parse_result_factory,
    )
    source_file = source_package.SourceFileContract(
        sequence=54,
        generated_upload_name=MANDATORY_ALIAS,
        source_locator_sha256="7" * 64,
        content_sha256=MANDATORY_CONTENT_SHA256,
        size_bytes=len(DOCUMENT_BYTES),
        extension="pdf",
    )
    response = {
        "deal_id": str(DEAL_ID),
        "document_id": DOCUMENT_ID,
        "doc_id": ARTIFACT_ID,
        "sha256": MANDATORY_CONTENT_SHA256,
        "parse_status": parse_status,
        "terminal_disposition": terminal_disposition,
        "metadata": {"document_no_substantive_content_receipt": receipt},
        "source_metadata": {"upload_filename": MANDATORY_ALIAS, "size_bytes": len(DOCUMENT_BYTES)},
    }

    admitted = source_package._validated_upload_receipt(
        response=response,
        deal_id=str(DEAL_ID),
        source_file=source_file,
        source_tree_fingerprint=SOURCE_TREE_FINGERPRINT,
        no_substantive_content_attestation=service._no_substantive_content_attestation,
    )

    assert admitted["document_id"] == DOCUMENT_ID
    assert admitted["doc_id"] == ARTIFACT_ID
    admission = service._no_substantive_content_attestation
    assert admission is not None
    monkeypatch.setattr(
        source_package,
        "datetime",
        type(
            "ExpiredDateTime",
            (),
            {
                "now": staticmethod(lambda _tz: admission.exemplar.expires_at),
            },
        ),
    )
    with pytest.raises(source_package.SourcePackageContractError) as expired:
        source_package._validated_upload_receipt(
            response=response,
            deal_id=str(DEAL_ID),
            source_file=source_file,
            source_tree_fingerprint=SOURCE_TREE_FINGERPRINT,
            no_substantive_content_attestation=admission,
        )
    assert expired.value.code == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_NOT_ACTIVE"


def test_worker_threads_current_v2_verifier_into_preflight_and_rejects_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idis.services.ingestion.no_substantive_content_attestation as attestation_module
    from idis.pipeline import worker
    from idis.services.runs import steps
    from idis.services.runs.document_preflight import InMemoryRunDocumentPreflightService

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return cls.fromtimestamp(ACTIVE_NOW.timestamp(), tz)  # type: ignore[arg-type]

    fixed_datetime = FixedDateTime
    monkeypatch.setattr("idis.services.ingestion.service.datetime", fixed_datetime)
    service, _audit, _result, receipt = _persisted_receipt(tmp_path)
    admission = service._no_substantive_content_attestation
    monkeypatch.setattr(attestation_module, "datetime", fixed_datetime)

    document = {
        "tenant_id": str(TENANT_ID),
        "deal_id": str(DEAL_ID),
        "document_id": DOCUMENT_ID,
        "document_name": MANDATORY_ALIAS,
        "sha256": MANDATORY_CONTENT_SHA256,
        "parse_status": "FAILED",
        "metadata": {"document_no_substantive_content_receipt": receipt},
        "source_metadata": {
            "upload_filename": MANDATORY_ALIAS,
            "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        },
        "spans": [],
    }
    observed: dict[str, object] = {}
    # Explicit injected verifier-wiring component, not a real worker claim or
    # source admission. Forward the actual existing closed compatibility tuple.
    version, contract_hash, resume_state, _error = _resolve_run_contract_identity(
        mode="SNAPSHOT",
        pipeline_contract_version=None,
        pipeline_contract_hash=None,
        pipeline_resume_state=None,
        pipeline_resume_error_code=None,
    )
    profile = SimpleNamespace(values={"IDIS_ACCEPTANCE_TENANT_ID": str(TENANT_ID)})
    monkeypatch.setattr(worker, "_load_worker_preflight_corpus", lambda **_kwargs: [document])
    monkeypatch.setattr(worker, "_load_worker_deal_metadata", lambda **_kwargs: None)
    monkeypatch.setattr(
        attestation_module,
        "load_document_no_substantive_content_attestation",
        lambda *, env: observed.setdefault("load_env", env) and admission,
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

    worker._default_run_context_factory(
        db_conn=object(),
        tenant_id=str(TENANT_ID),
        run_data={
            "run_id": "80000000-0000-4000-8000-000000000001",
            "deal_id": str(DEAL_ID),
            "mode": "SNAPSHOT",
            "created_by_actor_id": None,
            "created_by_actor_type": None,
            "strict_preflight_proof_id": None,
            "lease_owner_id": None,
            "lease_fencing_token": None,
            "pipeline_contract_version": version,
            "pipeline_contract_hash": contract_hash,
            "pipeline_resume_state": resume_state,
        },
        audit_sink=object(),
        strict_profile=profile,  # type: ignore[arg-type]
    )

    context = observed["context"]
    assert isinstance(context, dict)
    assert (
        context["pipeline_contract_version"],
        context["pipeline_contract_hash"],
        context["pipeline_resume_state"],
    ) == (version, contract_hash, resume_state)
    verifier = context["no_substantive_content_receipt_verifier"]
    assert callable(verifier)
    assert verifier(document) is True

    preflight, eligible = InMemoryRunDocumentPreflightService(
        no_substantive_content_receipt_verifier=verifier
    ).run(
        tenant_id=str(TENANT_ID),
        deal_id=str(DEAL_ID),
        run_id="80000000-0000-4000-8000-000000000001",
        corpus=[document],
    )
    assert eligible == []
    assert preflight.decisions[0].reason.value == "DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"

    drifted = copy.deepcopy(document)
    drifted_receipt = drifted["metadata"]["document_no_substantive_content_receipt"]
    assert isinstance(drifted_receipt, dict)
    drifted_receipt["manifest_sha256"] = "0" * 64
    assert verifier(drifted) is False

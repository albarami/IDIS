"""RED composition contracts for the pre-persistence watermark sanity cap."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pdf2image import convert_from_bytes

from idis.audit.sink import InMemoryAuditSink
from idis.parsers.base import ParseError, ParseErrorCode, ParseResult
from idis.parsers.watermark_page_classifier import (
    WatermarkPageRuntimeConfig,
    classify_pre_ocr_runtime_watermark_candidate,
)
from idis.services.ingestion import defaults
from idis.services.ingestion.no_substantive_content_attestation import (
    verify_document_no_substantive_content_attestation_manifest,
)
from idis.services.ingestion.service import IngestionContext, IngestionResult, IngestionService
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.slice100_task4.test_watermark_composition_root_red import (
    SIGNER_ID,
    SOURCE_TREE_FINGERPRINT,
    _fixture_pdf,
    _signed_manifest,
)

TENANT_ID = UUID("30000000-0000-4000-8000-000000000001")
DEAL_ID = UUID("40000000-0000-4000-8000-000000000001")
CAP_EXCEEDED = "WATERMARK_PAGE_SANITY_CAP_EXCEEDED"


def _verified_material() -> tuple[object, WatermarkPageRuntimeConfig]:
    fixture_pdf = _fixture_pdf()
    manifest, public_key = _signed_manifest(fixture_pdf)
    verified = verify_document_no_substantive_content_attestation_manifest(
        manifest=manifest,
        trusted_signer_id=SIGNER_ID,
        trusted_public_key_pem=public_key,
        now=datetime(2026, 7, 18, tzinfo=UTC),
    )
    runtime_config = defaults._watermark_page_runtime_config(verified)  # noqa: SLF001
    assert runtime_config is not None
    return verified, runtime_config


def _runtime_candidate_pdf_page() -> object:
    pages = convert_from_bytes(
        _fixture_pdf(),
        first_page=1,
        last_page=1,
        size=(512, 512),
        grayscale=True,
        fmt="png",
        thread_count=1,
    )
    assert len(pages) == 1
    return pages[0]


def _classified_terminal_result(
    *,
    content_sha256: str,
    runtime_config: WatermarkPageRuntimeConfig,
) -> ParseResult:
    page = _runtime_candidate_pdf_page()
    try:
        classification = classify_pre_ocr_runtime_watermark_candidate(
            candidate_image=page,  # type: ignore[arg-type]
            page_number=1,
            content_sha256=content_sha256,
            native_text_character_count=0,
            runtime_config=runtime_config,
        ).to_dict()
    finally:
        page.close()  # type: ignore[attr-defined]
    return ParseResult(
        doc_type="PDF",
        success=False,
        spans=[],
        metadata={
            "page_count": 1,
            "span_count": 0,
            "total_text_length": 0,
            "ocr_performed": False,
            "ocr_page_count": 0,
            "ocr_required_page_count": 1,
            "native_text_page_count": 0,
            "blank_page_count": 1,
            "watermark_preclassified_page_count": 1,
            "watermark_page_classifications": [classification],
        },
        errors=[
            ParseError(
                code=ParseErrorCode.OCR_NO_TEXT_EXTRACTED,
                message="Bounded pre-OCR classifier admitted the watermark-only page",
            )
        ],
    )


def _service(tmp_path: Path) -> IngestionService:
    verified, runtime_config = _verified_material()
    return IngestionService(
        compliant_store=ComplianceEnforcedStore(
            inner_store=FilesystemObjectStore(base_dir=tmp_path / "objects")
        ),
        audit_sink=InMemoryAuditSink(),
        no_substantive_content_attestation=verified,  # type: ignore[arg-type]
        watermark_page_config=runtime_config,
    )


def _ingest_runtime_match(
    service: IngestionService,
    *,
    sequence: int,
    runtime_config: WatermarkPageRuntimeConfig,
    db_conn: object | None = None,
) -> IngestionResult:
    alias = f"company-001-document-{sequence:05d}.pdf"
    data = f"fictional-watermark-runtime-match-{sequence}".encode()
    content_sha256 = hashlib.sha256(data).hexdigest()
    result = _classified_terminal_result(
        content_sha256=content_sha256,
        runtime_config=runtime_config,
    )
    service._parse_document = lambda *_args, **_kwargs: result  # type: ignore[method-assign]
    return service.ingest_bytes(
        IngestionContext(
            tenant_id=TENANT_ID,
            actor_id="slice100-operator",
            request_id=f"watermark-cap-{sequence}",
        ),
        DEAL_ID,
        filename=alias,
        media_type="application/pdf",
        data=data,
        metadata={
            "upload_filename": alias,
            "source_tree_fingerprint": SOURCE_TREE_FINGERPRINT,
        },
        db_conn=db_conn,  # type: ignore[arg-type]
    )


def test_ninth_company_one_classifier_page_fails_before_document_persistence(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    runtime_config = service._watermark_page_config  # noqa: SLF001
    assert isinstance(runtime_config, WatermarkPageRuntimeConfig)

    admitted = [
        _ingest_runtime_match(service, sequence=sequence, runtime_config=runtime_config)
        for sequence in range(1, 9)
    ]
    assert all(result.document_id is not None for result in admitted)

    blocked = _ingest_runtime_match(service, sequence=9, runtime_config=runtime_config)

    assert blocked.artifact_id is None
    assert blocked.document_id is None
    assert blocked.errors[0].details == {"attestation_error_code": CAP_EXCEEDED}
    assert len(service._documents) == 8  # noqa: SLF001


class _RestartRepository:
    def __init__(self, prior_rows: list[dict[str, Any]]) -> None:
        self.prior_rows = prior_rows
        self.create_calls = 0

    def list_documents_by_deal(self, _deal_id: str, *, parsed_only: bool) -> list[dict[str, Any]]:
        assert parsed_only is False
        return self.prior_rows

    def acquire_watermark_company_admission_lock(
        self,
        *,
        deal_id: str,
        company_alias: str,
    ) -> None:
        assert deal_id == str(DEAL_ID)
        assert company_alias == "company-001"

    def create_artifact(self, **_kwargs: object) -> None:
        self.create_calls += 1

    def create_document(self, **_kwargs: object) -> None:
        self.create_calls += 1

    def create_document_span(self, **_kwargs: object) -> None:
        self.create_calls += 1


def test_fresh_service_counts_durable_classifier_receipts_before_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = _service(tmp_path / "seed")
    runtime_config = seed._watermark_page_config  # noqa: SLF001
    assert isinstance(runtime_config, WatermarkPageRuntimeConfig)
    for sequence in range(1, 9):
        result = _ingest_runtime_match(seed, sequence=sequence, runtime_config=runtime_config)
        assert result.document_id is not None

    prior_rows = [
        {
            "metadata": document.metadata,
            "deal_id": str(DEAL_ID),
            "tenant_id": str(TENANT_ID),
        }
        for document in seed._documents.values()  # noqa: SLF001
    ]
    restarted = _service(tmp_path / "restart")
    repository = _RestartRepository(prior_rows)
    monkeypatch.setattr(
        restarted,
        "_documents_repo",
        lambda _tenant_id, *, db_conn: repository,
    )

    blocked = _ingest_runtime_match(
        restarted,
        sequence=9,
        runtime_config=runtime_config,
        db_conn=object(),
    )

    assert blocked.document_id is None
    assert blocked.errors[0].details == {"attestation_error_code": CAP_EXCEEDED}
    assert repository.create_calls == 0

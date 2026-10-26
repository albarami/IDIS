"""Task 4 parser integration for reviewed non-text image-only PDF pages.

The proof adapter below is intentionally test-only.  The production/default
path is separately asserted to retain the real process-isolated Tesseract
adapter; a signed page admission never replaces OCR execution.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Mapping
from typing import Any, cast

import pytest
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from idis.parsers import ocr
from idis.parsers.base import ParseErrorCode
from idis.parsers.pdf import parse_pdf
from idis.services.ingestion.defaults import build_default_ocr_config


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _pdf(*page_kinds: str) -> bytes:
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=letter)
    for page_number, page_kind in enumerate(page_kinds, start=1):
        if page_kind == "native":
            document.drawString(72, 720, f"FICTIONAL NATIVE PAGE {page_number}")
        elif page_kind == "image":
            image_bytes = io.BytesIO()
            Image.new(
                "RGB",
                (32, 32),
                color=(20 * page_number, 40, 60),
            ).save(image_bytes, format="PNG")
            image_bytes.seek(0)
            document.drawImage(ImageReader(image_bytes), 72, 650, width=96, height=96)
        else:  # pragma: no cover - fixture authoring guard
            raise AssertionError(page_kind)
        document.showPage()
    document.save()
    return output.getvalue()


def _accepted_receipt(*, page_number: int, text: str) -> ocr.OcrPageExecutionReceipt:
    return ocr._accepted_page_execution_receipt(
        page_number=page_number,
        outcome="primary_text",
        text=text,
        primary_token_count=len(text.split()),
        candidate_receipt_sha256=None,
    )


def _no_consensus_receipt(*, page_number: int) -> ocr.OcrPageExecutionReceipt:
    return ocr._no_consensus_page_execution_receipt(
        page_number=page_number,
        candidate_receipt_sha256=hashlib.sha256(
            f"fictional-candidate-receipt:{page_number}".encode("ascii")
        ).hexdigest(),
    )


def _runtime_page_content_sha256(page_number: int) -> str:
    return hashlib.sha256(
        f"fictional-current-canonical-page:{page_number}".encode("ascii")
    ).hexdigest()


class _TestOnlyPartialProofAdapter:
    """Raise one validated file proof while retaining accepted OCR pages in-memory."""

    def __init__(
        self,
        *,
        receipts: tuple[ocr.OcrPageExecutionReceipt, ...],
        accepted_pages: tuple[ocr.OcrPageText, ...] = (),
        page_content_sha256s: tuple[tuple[int, str], ...] | None = None,
    ) -> None:
        self._proof = ocr._ocr_no_consensus_file_proof(receipts)
        self._accepted_pages = accepted_pages
        self._page_content_sha256s = page_content_sha256s or tuple(
            (receipt.page_number, _runtime_page_content_sha256(receipt.page_number))
            for receipt in receipts
            if receipt.outcome == "no_consensus"
        )

    def extract_pdf_text(
        self,
        data: bytes,
        *,
        max_pages: int,
        timeout_seconds: float,
    ) -> list[ocr.OcrPageText]:
        del data, max_pages, timeout_seconds
        raise ocr.OcrRescueNoConsensusError(
            "OCR rescue produced no unique consensus",
            file_proof=self._proof,
            accepted_pages=self._accepted_pages,
            page_content_sha256s=self._page_content_sha256s,
        )

    def extract_image_text(
        self,
        data: bytes,
        *,
        timeout_seconds: float,
    ) -> list[ocr.OcrPageText]:
        del data, timeout_seconds
        raise AssertionError("PDF integration must not call image OCR")


def _admission_record(
    *,
    content_sha256: str,
    page_receipt: ocr.OcrPageExecutionReceipt,
    page_content_marker: str | None = None,
) -> dict[str, object]:
    page_number = page_receipt.page_number
    return {
        "safe_alias": "company-002-document-00123.pdf",
        "content_sha256": content_sha256,
        "page_number": page_number,
        "page_content_sha256": hashlib.sha256(page_content_marker.encode("ascii")).hexdigest()
        if page_content_marker is not None
        else _runtime_page_content_sha256(page_number),
        "ocr_terminal_proof_sha256": ocr.ocr_page_execution_receipt_sha256(page_receipt),
        "watermark_classifier_receipt_sha256": hashlib.sha256(
            f"fictional-below-threshold-proof:{page_number}".encode("ascii")
        ).hexdigest(),
        "reviewer_disposition": "SUBSTANTIVE_IMAGE",
    }


def _admission_set(
    records: list[dict[str, object]],
) -> ocr.TrustedImageOnlyPageAdmissionSet:
    return ocr.build_trusted_image_only_page_admission_set(
        records=records,
        expected_set_sha256=_digest(records),
    )


def _config(
    *,
    adapter: _TestOnlyPartialProofAdapter,
    admissions: ocr.TrustedImageOnlyPageAdmissionSet,
    max_pages: int,
) -> ocr.OcrConfig:
    return ocr.OcrConfig(
        enabled=True,
        adapter=cast(ocr.OcrAdapter, adapter),
        max_pages=max_pages,
        timeout_seconds=5.0,
        image_only_page_admissions=admissions,
    )


def test_mixed_pdf_preserves_native_and_ocr_spans_but_emits_none_for_reviewed_page() -> None:
    data = _pdf("native", "image", "image")
    content_sha256 = hashlib.sha256(data).hexdigest()
    ocr_text = "FICTIONAL REAL OCR PAGE TWO"
    ocr_receipt = _accepted_receipt(page_number=2, text=ocr_text)
    reviewed_receipt = _no_consensus_receipt(page_number=3)
    adapter = _TestOnlyPartialProofAdapter(
        # Production OCR is admitted only for pages with zero native text, so
        # this proof domain is exactly the OCR-required pages (2 and 3).
        receipts=(ocr_receipt, reviewed_receipt),
        accepted_pages=(ocr.OcrPageText(page_number=2, text=ocr_text, confidence=0.91),),
    )
    admission = _admission_record(
        content_sha256=content_sha256,
        page_receipt=reviewed_receipt,
    )

    result = parse_pdf(
        data,
        ocr_config=_config(
            adapter=adapter,
            admissions=_admission_set([admission]),
            max_pages=3,
        ),
    )

    assert result.success is True
    assert result.errors == []
    assert [span.locator["page"] for span in result.spans] == [1, 2]
    assert result.spans[0].locator.get("source") is None
    assert result.spans[1].locator["source"] == "ocr"
    assert ocr_text in result.spans[1].text_excerpt
    assert all(span.locator["page"] != 3 for span in result.spans)

    receipts = result.metadata["image_only_page_receipts"]
    assert isinstance(receipts, list) and len(receipts) == 1
    assert receipts[0] == {
        "schema_version": "image-only-page-receipt-v1",
        "classification": "image_only_page",
        "content_sha256": content_sha256,
        "page_number": 3,
        "page_content_sha256": admission["page_content_sha256"],
        "safe_page_locator": {
            "scheme": "safe-pdf-page-v1",
            "page_number": 3,
            "locator_sha256": hashlib.sha256(
                f"{content_sha256}\x00safe-pdf-page-v1\x003".encode("ascii")
            ).hexdigest(),
        },
        "native_text_character_count": 0,
        "primary_ocr_token_count": 0,
        "rescue_accepted_token_count": 0,
        "ocr_terminal_proof_sha256": admission["ocr_terminal_proof_sha256"],
        "watermark_classifier_outcome": "BELOW_THRESHOLD",
        "watermark_classifier_receipt_sha256": admission["watermark_classifier_receipt_sha256"],
        "emitted_span_count": 0,
        "emitted_text_character_count": 0,
        "occurrence_scope": "REVIEWER_CONFIRMED",
        "review_status": "REVIEWED_SUBSTANTIVE_IMAGE",
    }
    assert not ({"text", "text_excerpt", "spans"} & set(receipts[0]))
    evaluation = result.metadata["image_only_page_evaluation"]
    assert evaluation["parse_status"] == "PARSED"
    assert evaluation["may_proceed"] is True
    assert evaluation["terminal_code"] is None
    assert evaluation["image_only_page_count"] == 1
    assert evaluation["substantive_span_count"] == 2
    assert evaluation["reviewer_confirmed_page_count"] == 1
    assert evaluation["post_run_spot_check_required_page_count"] == 0


def test_all_reviewed_image_only_pdf_stays_failed_for_document_review() -> None:
    data = _pdf("image", "image")
    content_sha256 = hashlib.sha256(data).hexdigest()
    page_receipts = (
        _no_consensus_receipt(page_number=1),
        _no_consensus_receipt(page_number=2),
    )
    records = [
        _admission_record(content_sha256=content_sha256, page_receipt=receipt)
        for receipt in page_receipts
    ]

    result = parse_pdf(
        data,
        ocr_config=_config(
            adapter=_TestOnlyPartialProofAdapter(receipts=page_receipts),
            admissions=_admission_set(records),
            max_pages=2,
        ),
    )

    assert result.success is False
    assert result.spans == []
    assert [error.code for error in result.errors] == [ParseErrorCode.OCR_NO_TEXT_EXTRACTED]
    assert result.errors[0].details["terminal_code"] == "IMAGE_ONLY_DOCUMENT_REVIEW_REQUIRED"
    evaluation = result.metadata["image_only_page_evaluation"]
    assert evaluation["parse_status"] == "FAILED"
    assert evaluation["may_proceed"] is False
    assert evaluation["terminal_code"] == "IMAGE_ONLY_DOCUMENT_REVIEW_REQUIRED"
    assert evaluation["image_only_page_count"] == evaluation["page_count"] == 2


def test_partial_worker_transport_cross_binds_retained_text_to_file_proof() -> None:
    accepted_text = "FICTIONAL PROOF BOUND OCR TEXT"
    accepted_receipt = _accepted_receipt(page_number=1, text=accepted_text)
    no_consensus_receipt = _no_consensus_receipt(page_number=2)
    payload = ocr._ocr_pdf_worker_payload(
        [
            {
                "page_number": 1,
                "text": accepted_text,
                "confidences": ["91"] * len(accepted_text.split()),
                "execution_receipt": accepted_receipt.to_payload(),
            },
            {
                "page_number": 2,
                "status": "no_consensus",
                "execution_receipt": no_consensus_receipt.to_payload(),
                "page_content_sha256": _runtime_page_content_sha256(2),
            },
        ],
        retain_accepted_pages=True,
    )

    assert payload["status"] == "partial_no_consensus"
    with pytest.raises(ocr.OcrRescueNoConsensusError) as caught:
        ocr._pages_from_worker_payload(payload)

    assert caught.value.file_proof is not None
    assert caught.value.accepted_pages == (
        ocr.OcrPageText(page_number=1, text=accepted_text, confidence=0.91),
    )
    assert caught.value.page_content_sha256s == ((2, _runtime_page_content_sha256(2)),)
    assert ocr.ocr_page_execution_receipt_sha256(
        caught.value.file_proof.page_receipts[1]
    ) == ocr.ocr_page_execution_receipt_sha256(no_consensus_receipt)


@pytest.mark.parametrize(
    "failure",
    (
        "incomplete",
        "raw-untrusted",
        "document-mismatch",
        "page-mismatch",
        "page-content-mismatch",
        "ocr-proof-mismatch",
    ),
)
def test_incomplete_untrusted_or_mismatched_page_admissions_fail_closed(failure: str) -> None:
    data = _pdf("image", "image")
    content_sha256 = hashlib.sha256(data).hexdigest()
    page_receipts = (
        _no_consensus_receipt(page_number=1),
        _no_consensus_receipt(page_number=2),
    )
    records = [
        _admission_record(content_sha256=content_sha256, page_receipt=receipt)
        for receipt in page_receipts
    ]
    if failure == "incomplete":
        records.pop()
    elif failure == "document-mismatch":
        for record in records:
            record["content_sha256"] = "0" * 64
    elif failure == "page-mismatch":
        records[1]["page_number"] = 3
    elif failure == "page-content-mismatch":
        records[1]["page_content_sha256"] = "e" * 64
    elif failure == "ocr-proof-mismatch":
        records[1]["ocr_terminal_proof_sha256"] = "f" * 64

    if failure == "raw-untrusted":
        admissions: object = tuple(records)
    else:
        admissions = _admission_set(records)
    config = ocr.OcrConfig(
        enabled=True,
        adapter=cast(
            ocr.OcrAdapter,
            _TestOnlyPartialProofAdapter(receipts=page_receipts),
        ),
        max_pages=2,
        timeout_seconds=5.0,
        image_only_page_admissions=cast(Any, admissions),
    )

    result = parse_pdf(data, ocr_config=config)

    assert result.success is False
    assert result.spans == []
    expected_code = (
        ParseErrorCode.OCR_RESCUE_NO_CONSENSUS
        if failure == "document-mismatch"
        else ParseErrorCode.OCR_FAILED
    )
    assert [error.code for error in result.errors] == [expected_code]
    assert "image_only_page_receipts" not in result.metadata
    assert "image_only_page_evaluation" not in result.metadata


def test_admission_set_rejects_digest_drift_and_extra_private_fields() -> None:
    receipt = _no_consensus_receipt(page_number=1)
    records = [
        _admission_record(
            content_sha256="a" * 64,
            page_receipt=receipt,
        )
    ]
    with pytest.raises(ocr.OcrError):
        ocr.build_trusted_image_only_page_admission_set(
            records=records,
            expected_set_sha256="b" * 64,
        )

    poisoned = [dict(records[0])]
    poisoned[0]["source_path"] = "private-source-canary.pdf"
    with pytest.raises(ocr.OcrError):
        ocr.build_trusted_image_only_page_admission_set(
            records=poisoned,
            expected_set_sha256=_digest(poisoned),
        )


def test_default_admission_path_keeps_the_real_tesseract_adapter() -> None:
    receipt = _no_consensus_receipt(page_number=1)
    records = [
        _admission_record(
            content_sha256="a" * 64,
            page_receipt=receipt,
        )
    ]
    admissions = _admission_set(records)

    config = build_default_ocr_config(
        {
            "IDIS_OCR_ENABLED": "true",
            "IDIS_OCR_ADAPTER": "tesseract",
            "IDIS_OCR_MAX_PAGES": "72",
            "IDIS_OCR_TIMEOUT_SECONDS": "120",
        },
        image_only_page_admissions=admissions,
    )

    assert config is not None
    assert isinstance(config.adapter, ocr.TesseractOcrAdapter)
    assert config.image_only_page_admissions is admissions
    assert isinstance(config.adapter, ocr.TesseractOcrAdapter)


def test_admission_payloads_are_safe_and_exact() -> None:
    receipt = _no_consensus_receipt(page_number=1)
    record = _admission_record(content_sha256="a" * 64, page_receipt=receipt)
    admissions = _admission_set([record])

    payload = admissions.admissions[0].to_payload()

    assert payload == cast(Mapping[str, object], record)
    encoded = json.dumps(payload, sort_keys=True)
    assert "source_path" not in encoded
    assert "reviewer email" not in encoded


def test_canonical_page_content_hash_is_pinned_one_bit_white_is_one() -> None:
    with Image.new("L", (128, 128), color=255) as page:
        digest = ocr.canonical_image_only_page_content_sha256(page)

    assert digest == hashlib.sha256(b"\xff" * 2048).hexdigest()


def test_ocr_page_hash_matches_the_current_watermark_classifier_bitmap_policy() -> None:
    from idis.parsers import watermark_page_classifier as classifier

    with Image.new("L", (320, 480), color=252) as page:
        draw = ImageDraw.Draw(page)
        draw.line((20, 440, 300, 40), fill=180, width=7)
        prepared = classifier._prepare_page(page, classifier.default_watermark_page_policy())
        expected = classifier._canonical_bitmap_sha256(  # noqa: SLF001
            prepared.canonical_bitmap,
            classifier.default_watermark_page_policy(),
        )
        actual = ocr.canonical_image_only_page_content_sha256(page)

    assert actual == expected

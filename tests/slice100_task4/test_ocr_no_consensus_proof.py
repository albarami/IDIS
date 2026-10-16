"""Task 4 RED contract for complete, secret-safe OCR no-consensus proof."""

from __future__ import annotations

import copy
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from PIL import Image

from idis.parsers import ocr
from idis.parsers import pdf as pdf_parser
from idis.parsers.base import ParseErrorCode


class _Output:
    DICT = "dict"


class _CaptureQueue:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    def put(self, payload: dict[str, object]) -> None:
        self.items.append(payload)


def _empty_payload() -> dict[str, object]:
    return {
        "text": [],
        "conf": [],
        "block_num": [],
        "par_num": [],
        "line_num": [],
        "word_num": [],
    }


def _text_payload(text: str) -> dict[str, object]:
    words = text.split()
    return {
        "text": words,
        "conf": ["95"] * len(words),
        "block_num": [1] * len(words),
        "par_num": [1] * len(words),
        "line_num": [1] * len(words),
        "word_num": list(range(1, len(words) + 1)),
    }


def _page_result(
    *,
    page_number: int,
    image_to_data: Any,
) -> dict[str, object]:
    with Image.new("L", (24, 24), color=245) as image:
        return ocr._ocr_pdf_page_from_data(
            image=image,
            page_number=page_number,
            language="eng",
            deadline=time.monotonic() + 5.0,
            image_to_data=image_to_data,
            output=_Output,
        )


def _no_consensus_exception(*, page_number: int = 1) -> ocr.OcrRescueNoConsensusError:
    calls = 0

    def image_to_data(_image: Image.Image, **_kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 2:
            return _text_payload("PRIVATE_RAW_OCR_CANARY alpha beta gamma delta")
        return _empty_payload()

    with pytest.raises(ocr.OcrRescueNoConsensusError) as caught:
        _page_result(page_number=page_number, image_to_data=image_to_data)
    assert calls == 25
    return caught.value


def test_page_no_consensus_error_carries_frozen_safe_complete_proof() -> None:
    error = _no_consensus_exception(page_number=7)

    proof = error.page_proof
    assert proof is not None
    assert proof.page_number == 7
    assert proof.outcome == "no_consensus"
    assert proof.primary_completed is True
    assert proof.primary_token_count == 0
    assert proof.rescue_attempted_candidate_count == 24
    assert proof.rescue_completed_candidate_count == 24
    assert proof.accepted_text is False
    assert proof.accepted_text_token_count == 0
    assert len(proof.candidate_receipt_sha256 or "") == 64
    with pytest.raises(FrozenInstanceError):
        proof.page_number = 8  # type: ignore[misc]

    encoded = json.dumps(proof.to_payload(), sort_keys=True)
    assert "PRIVATE_RAW_OCR_CANARY" not in encoded
    assert "text" not in proof.to_payload()


def test_image_batch_finishes_every_page_after_no_consensus() -> None:
    calls = 0
    lock = threading.Lock()

    def image_to_data(_image: Image.Image, **_kwargs: object) -> dict[str, object]:
        nonlocal calls
        with lock:
            calls += 1
        return _empty_payload()

    images = [Image.new("L", (24, 24), color=245) for _ in range(4)]
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = ocr._ocr_pdf_image_batch(
                images=images,
                first_page=1,
                language="eng",
                deadline=time.monotonic() + 10.0,
                image_to_data=image_to_data,
                output=_Output,
                executor=executor,
            )
    finally:
        for image in images:
            image.close()

    assert calls == 4 * 25
    assert [result["page_number"] for result in results] == [1, 2, 3, 4]
    assert [result["status"] for result in results] == ["no_consensus"] * 4


def test_file_worker_emits_only_complete_no_consensus_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "fictional.pdf"
    source_path.write_bytes(b"fictional-pdf")
    images: list[Image.Image] = []

    def convert_from_path(_path: str, **kwargs: object) -> list[Image.Image]:
        rendered = [
            Image.new("L", (24, 24), color=245)
            for _page in range(int(kwargs["first_page"]), int(kwargs["last_page"]) + 1)
        ]
        images.extend(rendered)
        return rendered

    pdf2image = ModuleType("pdf2image")
    pdf2image.convert_from_path = convert_from_path  # type: ignore[attr-defined]
    pdf2image_exceptions = ModuleType("pdf2image.exceptions")
    pdf2image_exceptions.PDFInfoNotInstalledError = type(
        "PDFInfoNotInstalledError", (Exception,), {}
    )  # type: ignore[attr-defined]
    pdf2image_exceptions.PDFPopplerTimeoutError = type("PDFPopplerTimeoutError", (Exception,), {})  # type: ignore[attr-defined]
    pytesseract = ModuleType("pytesseract")
    pytesseract.Output = _Output  # type: ignore[attr-defined]
    pytesseract.TesseractError = type("TesseractError", (Exception,), {})  # type: ignore[attr-defined]
    pytesseract.TesseractNotFoundError = type("TesseractNotFoundError", (Exception,), {})  # type: ignore[attr-defined]
    pytesseract.image_to_data = lambda _image, **_kwargs: _empty_payload()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pdf2image", pdf2image)
    monkeypatch.setitem(sys.modules, "pdf2image.exceptions", pdf2image_exceptions)
    monkeypatch.setitem(sys.modules, "pytesseract", pytesseract)

    queue = _CaptureQueue()
    ocr._tesseract_pdf_file_ocr_worker(
        source_path,
        2,
        200,
        "eng",
        time.monotonic() + 10.0,
        queue,
    )

    assert len(queue.items) == 1
    payload = queue.items[0]
    assert payload["status"] == "no_consensus"
    assert set(payload) == {"status", "proof"}
    assert "pages" not in payload
    assert '"text":' not in json.dumps(payload, sort_keys=True)
    with pytest.raises(ocr.OcrRescueNoConsensusError) as caught:
        ocr._pages_from_worker_payload(payload)
    assert caught.value.file_proof is not None
    assert len(caught.value.file_proof.page_receipts) == 2


def test_file_payload_reduces_successful_pages_to_count_and_hash() -> None:
    private_primary_text = "PRIVATE_SUCCESSFUL_PAGE_CANARY alpha beta gamma delta"
    primary_page = _page_result(
        page_number=1,
        image_to_data=lambda _image, **_kwargs: _text_payload(private_primary_text),
    )
    no_consensus = _no_consensus_exception(page_number=2)
    assert no_consensus.page_proof is not None
    failed_page = {
        "page_number": 2,
        "status": "no_consensus",
        "execution_receipt": no_consensus.page_proof.to_payload(),
    }

    payload = ocr._ocr_pdf_worker_payload([primary_page, failed_page])

    assert payload["status"] == "no_consensus"
    encoded = json.dumps(payload, sort_keys=True)
    assert private_primary_text not in encoded
    assert "PRIVATE_RAW_OCR_CANARY" not in encoded
    proof_payload = cast(dict[str, object], payload["proof"])
    receipts = cast(list[dict[str, object]], proof_payload["page_receipts"])
    assert [receipt["outcome"] for receipt in receipts] == ["primary_text", "no_consensus"]
    assert receipts[0]["accepted_text"] is True
    assert receipts[0]["accepted_text_token_count"] == 8
    assert len(cast(str, receipts[0]["normalized_page_text_sha256"])) == 64

    with pytest.raises(ocr.OcrRescueNoConsensusError) as caught:
        ocr._pages_from_worker_payload(payload)
    assert caught.value.file_proof is not None
    assert caught.value.file_proof.to_payload() == proof_payload


def test_no_consensus_worker_payload_rejects_raw_or_incomplete_receipts() -> None:
    error = _no_consensus_exception()
    assert error.page_proof is not None
    payload = ocr._ocr_pdf_worker_payload(
        [
            {
                "page_number": 1,
                "status": "no_consensus",
                "execution_receipt": error.page_proof.to_payload(),
            }
        ]
    )

    raw_leak = copy.deepcopy(payload)
    raw_receipt = cast(
        list[dict[str, object]],
        cast(dict[str, object], raw_leak["proof"])["page_receipts"],
    )[0]
    raw_receipt["text"] = "PRIVATE_RAW_OCR_CANARY"
    with pytest.raises(ocr.OcrError) as raw_caught:
        ocr._pages_from_worker_payload(raw_leak)
    assert type(raw_caught.value) is ocr.OcrError

    incomplete = copy.deepcopy(payload)
    incomplete_receipt = cast(
        list[dict[str, object]],
        cast(dict[str, object], incomplete["proof"])["page_receipts"],
    )[0]
    incomplete_receipt["rescue_completed_candidate_count"] = 23
    with pytest.raises(ocr.OcrError) as incomplete_caught:
        ocr._pages_from_worker_payload(incomplete)
    assert type(incomplete_caught.value) is ocr.OcrError


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "stale-file-proof-schema"),
        ("ocr_rescue_policy_version", "stale-rescue-policy"),
        ("ocr_rescue_policy_sha256", "0" * 64),
    ],
)
def test_adapter_proof_revalidation_rejects_stale_top_level_provenance(
    field: str,
    value: str,
) -> None:
    error = _no_consensus_exception()
    assert error.page_proof is not None
    proof = ocr._ocr_no_consensus_file_proof((error.page_proof,))
    stale = replace(proof, **{field: value})

    with pytest.raises(ocr.OcrError):
        ocr.validate_ocr_no_consensus_file_proof(stale)


class _ProofAdapter:
    def __init__(self, proof: ocr.OcrNoConsensusFileProof) -> None:
        self._proof = proof

    def extract_pdf_text(
        self,
        _data: bytes,
        *,
        max_pages: int,
        timeout_seconds: float,
    ) -> list[ocr.OcrPageText]:
        assert max_pages == 2
        assert timeout_seconds == 5.0
        raise ocr.OcrRescueNoConsensusError(
            "OCR rescue produced no unique consensus",
            file_proof=self._proof,
        )

    def extract_image_text(
        self,
        _data: bytes,
        *,
        timeout_seconds: float,
    ) -> list[ocr.OcrPageText]:
        raise AssertionError(timeout_seconds)


def test_pdf_no_consensus_result_is_exact_complete_and_secret_safe() -> None:
    first = _no_consensus_exception(page_number=1)
    second = _no_consensus_exception(page_number=2)
    assert first.page_proof is not None and second.page_proof is not None
    proof = ocr._ocr_no_consensus_file_proof((first.page_proof, second.page_proof))

    result = pdf_parser._parse_pdf_with_ocr(
        data=b"fictional-pdf",
        source_path=None,
        page_count=2,
        ocr_config=ocr.OcrConfig(
            enabled=True,
            adapter=cast(ocr.OcrAdapter, _ProofAdapter(proof)),
            max_pages=2,
            timeout_seconds=5.0,
        ),
        required_page_numbers={1, 2},
        native_spans=[],
        native_text_length=0,
        native_text_page_count=0,
        blank_page_count=2,
    )

    assert result.success is False
    assert result.spans == []
    assert result.warnings == []
    assert [error.code for error in result.errors] == [ParseErrorCode.OCR_RESCUE_NO_CONSENSUS]
    assert result.errors[0].details == {
        "page_count": 2,
        "attempted_candidate_count": 48,
        "completed_candidate_count": 48,
        "no_consensus_page_count": 2,
    }
    expected_metadata = {
        "page_count": 2,
        "span_count": 0,
        "total_text_length": 0,
        "ocr_performed": True,
        "ocr_page_count": 2,
        "ocr_required_page_count": 2,
        "native_text_page_count": 0,
        "blank_page_count": 2,
        "primary_completed_page_count": 2,
        "rescue_completed_page_count": 2,
        "rescue_candidate_attempt_count": 48,
        "rescue_candidate_completed_count": 48,
        "rescue_accepted_clique_page_count": 0,
        "accepted_text_page_count": 0,
        "parser_runtime": "tesseract",
        "parser_runtime_status": "completed_no_extractable_text",
        "ocr_rescue_policy_version": ocr.OCR_RESCUE_POLICY_VERSION,
        "ocr_rescue_policy_sha256": ocr.OCR_RESCUE_POLICY_SHA256,
        "ocr_no_consensus_page_receipts": [
            first.page_proof.to_payload(),
            second.page_proof.to_payload(),
        ],
        "ocr_result_sha256": proof.ocr_result_sha256,
    }
    assert result.metadata == expected_metadata
    encoded = json.dumps(result.to_dict(), sort_keys=True)
    assert "PRIVATE_RAW_OCR_CANARY" not in encoded

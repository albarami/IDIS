"""Private-content-safe acceptance proof for the real 72-page OCR blocker."""

from __future__ import annotations

import math
import os
import time
from pathlib import Path

import pytest

from idis.evaluation.real_example_source_package import (
    DEFAULT_SOURCE_ROOT,
    build_two_company_source_contract,
)
from idis.parsers.ocr import TesseractOcrAdapter
from idis.runtime.profile import _parse_dotenv


def _strict_values() -> dict[str, str]:
    dotenv_path = os.environ.get("IDIS_STRICT_DOTENV_PATH", "").strip()
    if not dotenv_path:
        pytest.skip("strict operator profile is not configured")
    try:
        return _parse_dotenv(dotenv_path)
    except Exception:
        pytest.fail("strict operator profile could not be loaded", pytrace=False)


@pytest.mark.skipif(not DEFAULT_SOURCE_ROOT.is_dir(), reason="frozen real data room is absent")
def test_real_72_page_scanned_pdf_completes_inside_the_pinned_ocr_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _strict_values()
    tessdata_prefix = values.get("TESSDATA_PREFIX", "").strip()
    if tessdata_prefix:
        monkeypatch.setenv("TESSDATA_PREFIX", tessdata_prefix)
    contract = build_two_company_source_contract(root=Path(DEFAULT_SOURCE_ROOT))
    company = contract.companies[0]
    source_file = company.files[52]
    assert source_file.generated_upload_name == "company-001-document-00053.pdf"

    started_at = time.monotonic()
    pages = TesseractOcrAdapter(dpi=200, language="eng").extract_pdf_file(
        contract.source_path(company.company_alias, source_file.sequence),
        max_pages=72,
        timeout_seconds=120,
    )
    elapsed_seconds = time.monotonic() - started_at

    page_count = len(pages)
    ordered_page_count = sum(
        page.page_number == expected_page for expected_page, page in enumerate(pages, start=1)
    )
    nonempty_page_count = sum(bool(page.text.strip()) for page in pages)
    finite_confidence_count = sum(
        page.confidence is not None and math.isfinite(page.confidence) for page in pages
    )
    assert page_count == 72
    assert ordered_page_count == 72
    assert nonempty_page_count == 72
    assert finite_confidence_count == 72
    assert elapsed_seconds < 120

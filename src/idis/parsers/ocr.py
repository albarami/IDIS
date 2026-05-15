"""Config-gated OCR adapter boundary for parser tests and future provisioning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class OcrError(Exception):
    """Base class for safe OCR adapter failures."""


class OcrTimeoutError(OcrError):
    """Raised by OCR adapters when OCR exceeds a configured runtime budget."""


@dataclass(frozen=True, slots=True)
class OcrPageText:
    """OCR text extracted for one PDF page."""

    page_number: int
    text: str


class OcrAdapter(Protocol):
    """Adapter interface for opt-in OCR implementations."""

    def extract_pdf_text(
        self,
        data: bytes,
        *,
        max_pages: int,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        """Return OCR text by page for PDF bytes."""


@dataclass(frozen=True, slots=True)
class OcrConfig:
    """Config-gated OCR execution settings."""

    enabled: bool = False
    adapter: OcrAdapter | None = None
    max_pages: int = 10
    timeout_seconds: float = 30.0

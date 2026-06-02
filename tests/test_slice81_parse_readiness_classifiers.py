"""Slice81 Task 2 — pure evidence-class + deferral-class classifiers.

TDD RED-first: these import functions that do not exist yet. Pure functions only — no
parse execution, file IO, or ledger writes. Locked decisions (Slice81 plan §4):
  * Evidence classes PDF/SPREADSHEET/DOCUMENT/PRESENTATION/WEB_TEXT/IMAGE/MEDIA/OTHER,
    derived from extension only (never filename/content).
  * Unintended = RETRYABLE_REASON_CODES + "unknown_format" + media_transcription_failed/
    _timeout; everything not in the locked intended set is unintended (fail-safe default).
"""

from __future__ import annotations

import pytest

from idis.evaluation.real_example_gate import (
    _UNINTENDED_REASON_CODES,
    _deferral_class_for_reason_code,
    _evidence_class_for_extension,
)
from idis.evaluation.real_example_gate_ledger import RETRYABLE_REASON_CODES

# --- evidence class (extension only) ---


@pytest.mark.parametrize(
    ("extension", "expected"),
    [
        (".pdf", "PDF"),
        ("PDF", "PDF"),
        (".PDF", "PDF"),
        ("pdf", "PDF"),
        (".xlsx", "SPREADSHEET"),
        (".xlsm", "SPREADSHEET"),
        (".docx", "DOCUMENT"),
        (".pptx", "PRESENTATION"),
        (".html", "WEB_TEXT"),
        (".htm", "WEB_TEXT"),
        (".txt", "WEB_TEXT"),
        (".png", "IMAGE"),
        (".jpg", "IMAGE"),
        (".jpeg", "IMAGE"),
        (".tif", "IMAGE"),
        (".tiff", "IMAGE"),
        (".bmp", "IMAGE"),
        (".mp4", "MEDIA"),
        (".csv", "OTHER"),  # unsupported in the parser matrix -> OTHER, not SPREADSHEET
        (".zip", "OTHER"),
        (".unknown", "OTHER"),
        ("", "OTHER"),
        ("noext", "OTHER"),
    ],
)
def test_evidence_class_for_extension(extension: str, expected: str) -> None:
    assert _evidence_class_for_extension(extension) == expected


def test_evidence_class_is_case_insensitive() -> None:
    assert _evidence_class_for_extension(".JpEg") == "IMAGE"
    assert _evidence_class_for_extension(".MP4") == "MEDIA"


def test_evidence_class_derives_from_extension_not_filename() -> None:
    # A full filename is NOT split for an embedded extension -> OTHER (no filename peeking).
    assert _evidence_class_for_extension("confidential-deal.pdf") == "OTHER"
    assert _evidence_class_for_extension("board-call.mp4") == "OTHER"


# --- deferral class ---


@pytest.mark.parametrize("reason_code", [*sorted(RETRYABLE_REASON_CODES), "unknown_format"])
def test_retryable_and_unknown_format_are_unintended(reason_code: str) -> None:
    assert _deferral_class_for_reason_code(reason_code) == "unintended"


@pytest.mark.parametrize(
    "reason_code",
    [
        "parsed",
        "conversion_required",
        "ocr_required",
        "media_transcription_unavailable",
        "unsupported_format",
        "file_too_large",
        "encrypted_pdf",
        "no_text_extracted",
        "corrupted_file",
        "inventory_only",
    ],
)
def test_locked_intended_blockers_are_intended(reason_code: str) -> None:
    assert _deferral_class_for_reason_code(reason_code) == "intended"


@pytest.mark.parametrize("reason_code", ["", "   ", None])
def test_empty_or_missing_reason_is_unintended(reason_code: str | None) -> None:
    assert _deferral_class_for_reason_code(reason_code) == "unintended"


def test_unknown_future_reason_is_unintended_failsafe() -> None:
    # Parse readiness must never silently accept an unrecognized blocker.
    assert _deferral_class_for_reason_code("some_new_future_blocker_v9") == "unintended"


@pytest.mark.parametrize(
    "reason_code",
    [
        "ocr_no_text_extracted",
        "media_no_text_extracted",
        "media_duration_exceeded",
        "unsupported_in_slice_29",
    ],
)
def test_promoted_known_codes_are_intended(reason_code: str) -> None:
    # Locked Task 3 disposition: these terminal content/scope outcomes are intended blockers.
    assert _deferral_class_for_reason_code(reason_code) == "intended"


@pytest.mark.parametrize(
    "reason_code",
    ["media_transcription_failed", "media_transcription_timeout"],
)
def test_media_runtime_failures_are_explicitly_unintended(reason_code: str) -> None:
    # Locked disposition: media transcription runtime failures/timeouts are EXPLICITLY in the
    # unintended set (not merely caught by the fail-safe default).
    assert reason_code in _UNINTENDED_REASON_CODES
    assert _deferral_class_for_reason_code(reason_code) == "unintended"

"""Private local gate for the fund-supplied real_example data room."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from idis.evaluation.real_example_gate_ledger import (
    ledger_entry_count,
    load_ledger,
    record_ledger_entry,
    save_ledger,
    terminal_ledger_entry,
)
from idis.evaluation.real_example_gate_runtime import (
    ParseAttempt,
    ParseAttemptFn,
    memory_exceeded,
    run_injected_parse_with_timeout,
    run_parse_subprocess,
)
from idis.models.document_classification import DocumentSupportStatus, DocumentTriageStatus
from idis.services.documents.parser_capabilities import capability_for_document

DEFAULT_REAL_EXAMPLE_ROOT = Path("real_example")
DEFAULT_LEDGER_PATH = Path(".local_reports") / "real_example_gate_ledger.json"
DEFAULT_PER_FILE_TIMEOUT_SECONDS = 30.0
HASH_CHUNK_SIZE_BYTES = 1024 * 1024
MAX_OCR_PAGES = 10
MAX_OCR_TIMEOUT_SECONDS = 120.0
MIN_OCR_DPI = 72
MAX_OCR_DPI = 300
SUPPORTED_PARSE_EXTENSIONS = frozenset({".pdf", ".xlsx", ".docx", ".pptx"})
SUPPORTED_PARSER_NAMES = frozenset({"pdf", "xlsx", "docx", "pptx"})


class GateMode(StrEnum):
    """Supported private gate execution modes."""

    INVENTORY_ONLY = "inventory_only"
    PARSE_SUPPORTED = "parse_supported"


@dataclass(frozen=True, slots=True)
class _InventoryFile:
    path: Path
    extension: str
    size_bytes: int
    sha256: str
    read_error_reason: str | None = None


ProgressFn = Callable[[dict[str, object]], None]


def run_real_example_gate(
    *,
    root: str | Path = DEFAULT_REAL_EXAMPLE_ROOT,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
    mode: GateMode = GateMode.INVENTORY_ONLY,
    safe_summary: bool = True,
    per_file_timeout_seconds: float = DEFAULT_PER_FILE_TIMEOUT_SECONDS,
    max_runtime_seconds: float | None = None,
    max_memory_mb: int | None = None,
    emit_progress: bool = True,
    parse_attempt_fn: ParseAttemptFn | None = None,
    progress_fn: ProgressFn | None = None,
    ocr_enabled: bool = False,
    ocr_max_pages: int = 10,
    ocr_timeout_seconds: float = 30.0,
    ocr_dpi: int = 200,
) -> dict[str, object]:
    """Run the local private real_example gate and return a safe aggregate summary.

    Args:
        root: Local data room root. The returned summary never includes it.
        ledger_path: Local resumable ledger path. The returned summary never includes it.
        mode: Inventory-only or supported-file parsing mode.
        safe_summary: Must remain true for parse-supported runs.
        per_file_timeout_seconds: Maximum seconds for one parse attempt.
        max_runtime_seconds: Optional total runtime budget.
        max_memory_mb: Optional process memory budget checked before each file.
        emit_progress: Whether to emit safe per-file progress to stderr.
        parse_attempt_fn: Optional test seam for parse attempts.
        progress_fn: Optional test seam for progress emission.
        ocr_enabled: Whether to run the opt-in OCR adapter for OCR-required PDFs.
        ocr_max_pages: Maximum leading PDF pages the OCR adapter may process.
        ocr_timeout_seconds: Maximum OCR runtime inside the parser subprocess.
        ocr_dpi: PDF rasterization DPI for OCR.

    Returns:
        Aggregate counts by extension, status, parser outcome, and reason code only.

    Raises:
        ValueError: If arguments would allow unsafe output.
        FileNotFoundError: If the root does not exist.
        NotADirectoryError: If the root is not a directory.
    """
    resolved_mode = GateMode(mode)
    if resolved_mode == GateMode.PARSE_SUPPORTED and not safe_summary:
        raise ValueError("--parse-supported requires --safe-summary")
    _validate_ocr_options(
        ocr_enabled=ocr_enabled,
        ocr_max_pages=ocr_max_pages,
        ocr_timeout_seconds=ocr_timeout_seconds,
        ocr_dpi=ocr_dpi,
    )

    root_path = Path(root)
    _validate_root(root_path)
    files = _inventory_files(root_path)
    ledger_file = Path(ledger_path)
    ledger = load_ledger(ledger_file)
    resume_entries = dict(ledger["entries"])
    progress = progress_fn or _emit_progress
    started_at = time.monotonic()
    records: list[dict[str, object]] = []

    for index, file in enumerate(files, start=1):
        attempt = _attempt_for_file(
            file=file,
            mode=resolved_mode,
            resume_entries=resume_entries,
            started_at=started_at,
            max_runtime_seconds=max_runtime_seconds,
            max_memory_mb=max_memory_mb,
            per_file_timeout_seconds=per_file_timeout_seconds,
            parse_attempt_fn=parse_attempt_fn,
            ocr_enabled=ocr_enabled,
            ocr_max_pages=ocr_max_pages,
            ocr_timeout_seconds=ocr_timeout_seconds,
            ocr_dpi=ocr_dpi,
        )
        records.append(
            {
                "extension": file.extension,
                "status": attempt.status,
                "parser_outcome": attempt.parser_outcome,
                "reason_code": attempt.reason_code,
            }
        )
        _record_ledger_entry(
            ledger=ledger,
            file=file,
            attempt=attempt,
            mode=resolved_mode,
        )
        save_ledger(ledger_file, ledger)
        if emit_progress:
            progress(
                {
                    "index": index,
                    "total": len(files),
                    "extension": file.extension,
                    "status": attempt.status,
                    "reason_code": attempt.reason_code,
                }
            )

    save_ledger(ledger_file, ledger)
    return _safe_summary(mode=resolved_mode, records=records, ledger=ledger)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for the private real_example gate."""
    parser = argparse.ArgumentParser(
        description="Run the local-only private real_example gate.",
        epilog=(
            "Examples:\n"
            "  python scripts/run_real_example_gate.py --inventory-only\n"
            "  python scripts/run_real_example_gate.py --parse-supported --safe-summary\n"
            "  python scripts/run_real_example_gate.py --parse-supported --safe-summary "
            "--per-file-timeout-seconds 20 --max-runtime-seconds 900"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--inventory-only", action="store_true")
    modes.add_argument("--parse-supported", action="store_true")
    parser.add_argument("--safe-summary", action="store_true")
    parser.add_argument("--root", default=str(DEFAULT_REAL_EXAMPLE_ROOT))
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER_PATH))
    parser.add_argument(
        "--per-file-timeout-seconds",
        type=float,
        default=DEFAULT_PER_FILE_TIMEOUT_SECONDS,
    )
    parser.add_argument("--max-runtime-seconds", type=float)
    parser.add_argument("--max-memory-mb", type=int)
    parser.add_argument("--ocr-enabled", action="store_true")
    parser.add_argument("--ocr-max-pages", type=int, default=MAX_OCR_PAGES)
    parser.add_argument("--ocr-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--ocr-dpi", type=int, default=200)
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args(argv)

    mode = GateMode.PARSE_SUPPORTED if args.parse_supported else GateMode.INVENTORY_ONLY
    if mode == GateMode.PARSE_SUPPORTED and not args.safe_summary:
        parser.error("--parse-supported requires --safe-summary")

    try:
        summary = run_real_example_gate(
            root=args.root,
            ledger_path=args.ledger,
            mode=mode,
            safe_summary=True,
            per_file_timeout_seconds=args.per_file_timeout_seconds,
            max_runtime_seconds=args.max_runtime_seconds,
            max_memory_mb=args.max_memory_mb,
            emit_progress=not args.no_progress,
            ocr_enabled=args.ocr_enabled,
            ocr_max_pages=args.ocr_max_pages,
            ocr_timeout_seconds=args.ocr_timeout_seconds,
            ocr_dpi=args.ocr_dpi,
        )
    except (FileNotFoundError, NotADirectoryError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "gate": "real_example_private_v1",
                    "safe_summary": True,
                    "status": "failed",
                    "reason_code": _safe_cli_reason(exc),
                },
                sort_keys=True,
                indent=2,
            )
        )
        return 1
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


def _attempt_for_file(
    *,
    file: _InventoryFile,
    mode: GateMode,
    resume_entries: dict[str, object],
    started_at: float,
    max_runtime_seconds: float | None,
    max_memory_mb: int | None,
    per_file_timeout_seconds: float,
    parse_attempt_fn: ParseAttemptFn | None,
    ocr_enabled: bool,
    ocr_max_pages: int,
    ocr_timeout_seconds: float,
    ocr_dpi: int,
) -> ParseAttempt:
    if mode == GateMode.INVENTORY_ONLY:
        return ParseAttempt(
            status="inventoried",
            parser_outcome="not_attempted",
            reason_code="inventory_only",
        )
    if file.read_error_reason is not None:
        return ParseAttempt(
            status="failed",
            parser_outcome="not_attempted",
            reason_code=file.read_error_reason,
        )

    existing = terminal_ledger_entry(
        entries=resume_entries,
        sha256=file.sha256,
        extension=file.extension,
        ocr_enabled=ocr_enabled,
    )
    if existing is not None:
        return ParseAttempt(
            status=str(existing["status"]),
            parser_outcome="resumed",
            reason_code=str(existing["reason_code"]),
        )

    capability = capability_for_document(
        filename=f"file{file.extension}",
        file_size_bytes=file.size_bytes,
    )
    if file.extension not in SUPPORTED_PARSE_EXTENSIONS:
        reason_code = (
            "unsupported_in_slice_29"
            if file.extension == ".xlsm"
            else _capability_reason_code(
                support_status=capability.support_status,
                triage_status=capability.triage_status,
                reason_codes=capability.reason_codes,
            )
        )
        if capability.support_status == DocumentSupportStatus.UNSUPPORTED:
            return ParseAttempt.unsupported(reason_code=reason_code)
        return ParseAttempt.deferred(reason_code=reason_code)

    if not _should_parse(capability.support_status, capability.parser_name):
        reason_code = _capability_reason_code(
            support_status=capability.support_status,
            triage_status=capability.triage_status,
            reason_codes=capability.reason_codes,
        )
        if capability.support_status == DocumentSupportStatus.UNSUPPORTED:
            return ParseAttempt.unsupported(reason_code=reason_code)
        return ParseAttempt.deferred(reason_code=reason_code)

    if _runtime_exceeded(started_at, max_runtime_seconds):
        return ParseAttempt.deferred(reason_code="max_runtime_exceeded")
    if memory_exceeded(max_memory_mb):
        return ParseAttempt.deferred(reason_code="max_memory_exceeded")

    if parse_attempt_fn is not None:
        return run_injected_parse_with_timeout(
            file.path,
            parse_attempt_fn=parse_attempt_fn,
            timeout_seconds=per_file_timeout_seconds,
        )
    return run_parse_subprocess(
        file.path,
        timeout_seconds=per_file_timeout_seconds,
        max_memory_mb=max_memory_mb,
        ocr_enabled=ocr_enabled,
        ocr_max_pages=ocr_max_pages,
        ocr_timeout_seconds=ocr_timeout_seconds,
        ocr_dpi=ocr_dpi,
    )


def _inventory_files(root: Path) -> list[_InventoryFile]:
    records: list[_InventoryFile] = []
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=_sort_key(root)):
        extension = path.suffix.lower() or ".unknown"
        try:
            sha256, size_bytes = _hash_file_streaming(path)
            records.append(
                _InventoryFile(
                    path=path,
                    extension=extension,
                    size_bytes=size_bytes,
                    sha256=sha256,
                )
            )
        except OSError:
            records.append(
                _InventoryFile(
                    path=path,
                    extension=extension,
                    size_bytes=0,
                    sha256=_unreadable_file_key(root=root, path=path),
                    read_error_reason="file_scan_failed",
                )
            )
    return records


def _validate_ocr_options(
    *,
    ocr_enabled: bool,
    ocr_max_pages: int,
    ocr_timeout_seconds: float,
    ocr_dpi: int,
) -> None:
    if not ocr_enabled:
        return
    if not 1 <= ocr_max_pages <= MAX_OCR_PAGES:
        raise ValueError("OCR max pages is outside the allowed range")
    if not 0 < ocr_timeout_seconds <= MAX_OCR_TIMEOUT_SECONDS:
        raise ValueError("OCR timeout is outside the allowed range")
    if not MIN_OCR_DPI <= ocr_dpi <= MAX_OCR_DPI:
        raise ValueError("OCR DPI is outside the allowed range")


def _hash_file_streaming(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as file:
        while chunk := file.read(HASH_CHUNK_SIZE_BYTES):
            size_bytes += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size_bytes


def _sort_key(root: Path) -> Callable[[Path], str]:
    def key(path: Path) -> str:
        return path.relative_to(root).as_posix().lower()

    return key


def _should_parse(support_status: DocumentSupportStatus, parser_name: str | None) -> bool:
    return (
        support_status
        in {
            DocumentSupportStatus.SUPPORTED,
            DocumentSupportStatus.PARTIALLY_SUPPORTED,
        }
        and parser_name in SUPPORTED_PARSER_NAMES
    )


def _capability_reason_code(
    *,
    support_status: DocumentSupportStatus,
    triage_status: DocumentTriageStatus,
    reason_codes: list[str],
) -> str:
    if support_status == DocumentSupportStatus.CONVERSION_REQUIRED:
        return "conversion_required"
    if triage_status == DocumentTriageStatus.OCR_REQUIRED:
        return "ocr_required"
    if support_status == DocumentSupportStatus.UNSUPPORTED:
        return "unsupported_format"
    if support_status == DocumentSupportStatus.TOO_LARGE:
        return "file_too_large"
    return _first_reason_code(reason_codes, default="unknown_format")


def _first_reason_code(reason_codes: list[str], *, default: str) -> str:
    if not reason_codes:
        return default
    return sorted(reason_codes)[0]


def _runtime_exceeded(started_at: float, max_runtime_seconds: float | None) -> bool:
    return max_runtime_seconds is not None and time.monotonic() - started_at >= max_runtime_seconds


def _record_ledger_entry(
    *,
    ledger: dict[str, Any],
    file: _InventoryFile,
    attempt: ParseAttempt,
    mode: GateMode,
) -> None:
    if mode == GateMode.PARSE_SUPPORTED and attempt.parser_outcome == "resumed":
        return
    record_ledger_entry(
        ledger=ledger,
        sha256=file.sha256,
        extension=file.extension,
        size_bytes=file.size_bytes,
        status=attempt.status,
        parser_outcome=attempt.parser_outcome,
        reason_code=attempt.reason_code,
    )


def _safe_summary(
    *,
    mode: GateMode,
    records: list[dict[str, object]],
    ledger: dict[str, Any],
) -> dict[str, object]:
    return {
        "gate": "real_example_private_v1",
        "safe_summary": True,
        "mode": mode.value,
        "total_files": len(records),
        "processed_files": len(records),
        "ledger_entry_count": ledger_entry_count(ledger),
        "counts_by_extension": _count(records, "extension"),
        "counts_by_status": _count(records, "status"),
        "counts_by_parser_outcome": _count(records, "parser_outcome"),
        "counts_by_reason_code": _count(records, "reason_code"),
    }


def _count(records: list[dict[str, object]], key: str) -> dict[str, int]:
    return dict(sorted(Counter(str(record[key]) for record in records).items()))


def _validate_root(root: Path) -> None:
    if not root.exists():
        raise FileNotFoundError("real_example root does not exist")
    if not root.is_dir():
        raise NotADirectoryError("real_example root is not a directory")


def _safe_cli_reason(exc: BaseException) -> str:
    if isinstance(exc, FileNotFoundError):
        return "root_not_found"
    if isinstance(exc, NotADirectoryError):
        return "root_not_directory"
    if isinstance(exc, RuntimeError):
        return "ledger_write_failed"
    if isinstance(exc, ValueError):
        return "invalid_arguments"
    return "internal_error"


def _unreadable_file_key(*, root: Path, path: Path) -> str:
    relative = path.relative_to(root).as_posix().encode("utf-8", errors="replace")
    return f"unreadable:{hashlib.sha256(relative).hexdigest()}"


def _emit_progress(event: dict[str, object]) -> None:
    print(json.dumps({"progress": event}, sort_keys=True), file=sys.stderr)


__all__ = [
    "GateMode",
    "ParseAttempt",
    "main",
    "run_real_example_gate",
]

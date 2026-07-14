"""Private local gate for the fund-supplied real_example data room."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from idis.evaluation.real_example_gate_ledger import (
    RETRYABLE_REASON_CODES,
    ledger_entry_count,
    load_ledger,
    media_policy_key,
    ocr_policy_key,
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
from idis.parsers.media import (
    FASTER_WHISPER_ADAPTER_NAME,
    MAX_MEDIA_DURATION_SECONDS,
    FasterWhisperMediaConfig,
    probe_faster_whisper_model,
)
from idis.services.documents.parser_capabilities import capability_for_document

DEFAULT_REAL_EXAMPLE_ROOT = Path("real_example")
DEFAULT_LEDGER_PATH = Path(".local_reports") / "real_example_gate_ledger.json"
DEFAULT_PER_FILE_TIMEOUT_SECONDS = 30.0
HASH_CHUNK_SIZE_BYTES = 1024 * 1024
MAX_OCR_PAGES = 10
MAX_OCR_TIMEOUT_SECONDS = 120.0
MIN_OCR_DPI = 72
MAX_OCR_DPI = 300
MAX_MEDIA_TIMEOUT_SECONDS = 120.0
MEDIA_ADAPTER_NONE = "none"
MEDIA_ADAPTER_CHOICES = frozenset({MEDIA_ADAPTER_NONE, FASTER_WHISPER_ADAPTER_NAME})
DEFAULT_MEDIA_LANGUAGE = "en"
DEFAULT_MEDIA_COMPUTE_TYPE = "int8"
SUPPORTED_PARSE_EXTENSIONS = frozenset({".pdf", ".xlsx", ".docx", ".pptx"})
SUPPORTED_PARSER_NAMES = frozenset({"pdf", "xlsx", "docx", "pptx"})
OCR_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"})
TEXT_PARSE_EXTENSIONS = frozenset({".html", ".htm", ".txt"})
MEDIA_EXTENSIONS = frozenset({".mp4"})


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
    emit_progress: bool = False,
    parse_attempt_fn: ParseAttemptFn | None = None,
    progress_fn: ProgressFn | None = None,
    ocr_enabled: bool = False,
    ocr_max_pages: int = 10,
    ocr_timeout_seconds: float = 30.0,
    ocr_dpi: int = 200,
    media_enabled: bool = False,
    media_timeout_seconds: float = 30.0,
    media_adapter: str = MEDIA_ADAPTER_NONE,
    media_model_name: str | None = None,
    media_model_path: str | None = None,
    media_allow_model_download: bool = False,
    media_language: str = DEFAULT_MEDIA_LANGUAGE,
    media_compute_type: str = DEFAULT_MEDIA_COMPUTE_TYPE,
    media_max_duration_seconds: float = MAX_MEDIA_DURATION_SECONDS,
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
        emit_progress: Whether to emit safe per-file progress to stderr. Defaults off.
        parse_attempt_fn: Optional test seam for parse attempts.
        progress_fn: Optional test seam for progress emission.
        ocr_enabled: Whether to run the opt-in OCR adapter for OCR-required PDFs.
        ocr_max_pages: Maximum leading PDF pages the OCR adapter may process.
        ocr_timeout_seconds: Maximum OCR runtime inside the parser subprocess.
        ocr_dpi: PDF rasterization DPI for OCR.
        media_enabled: Whether to run the opt-in private media adapter boundary.
        media_timeout_seconds: Maximum media transcription runtime inside the parser subprocess.
        media_adapter: Private media adapter name. Defaults to disabled.
        media_model_name: faster-whisper model name, used only when downloads are explicit.
        media_model_path: Local faster-whisper model path.
        media_allow_model_download: Whether faster-whisper may download a named model.
        media_language: Transcription language hint.
        media_compute_type: faster-whisper compute type.
        media_max_duration_seconds: Maximum media duration eligible for transcription.

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
    _validate_media_options(
        media_enabled=media_enabled,
        media_timeout_seconds=media_timeout_seconds,
        media_adapter=media_adapter,
        media_max_duration_seconds=media_max_duration_seconds,
    )

    root_path = Path(root)
    _validate_root(root_path)
    files = _inventory_files(root_path)
    ledger_file = Path(ledger_path)
    ledger = load_ledger(ledger_file)
    resume_entries = dict(ledger["entries"])
    current_ocr_policy_key = ocr_policy_key(
        ocr_enabled=ocr_enabled,
        ocr_max_pages=ocr_max_pages,
        ocr_timeout_seconds=ocr_timeout_seconds,
        ocr_dpi=ocr_dpi,
    )
    current_media_policy_key = media_policy_key(
        media_enabled=media_enabled,
        media_adapter_available=_media_adapter_attemptable(
            media_adapter=media_adapter,
            media_model_name=media_model_name,
            media_model_path=media_model_path,
            media_allow_model_download=media_allow_model_download,
        ),
        media_adapter_name=media_adapter,
        media_timeout_seconds=media_timeout_seconds,
        media_model_key=_media_model_policy_key(
            media_model_name=media_model_name,
            media_model_path=media_model_path,
        ),
        media_allow_model_download=media_allow_model_download,
        media_language=media_language,
        media_compute_type=media_compute_type,
        media_max_duration_seconds=media_max_duration_seconds,
    )
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
            ocr_policy_key=current_ocr_policy_key,
            media_enabled=media_enabled,
            media_timeout_seconds=media_timeout_seconds,
            media_adapter=media_adapter,
            media_model_name=media_model_name,
            media_model_path=media_model_path,
            media_allow_model_download=media_allow_model_download,
            media_language=media_language,
            media_compute_type=media_compute_type,
            media_max_duration_seconds=media_max_duration_seconds,
            media_policy_key=current_media_policy_key,
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
            ocr_policy_key=current_ocr_policy_key,
            media_policy_key=current_media_policy_key,
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
    summary = _safe_summary(mode=resolved_mode, records=records, ledger=ledger)
    _append_gate_reconciliation_entry(
        summary=summary,
        mode=resolved_mode,
        records=records,
        ledger_file=ledger_file,
    )
    return summary


def _append_gate_reconciliation_entry(
    *,
    summary: dict[str, object],
    mode: GateMode,
    records: list[dict[str, object]],
    ledger_file: Path,
) -> None:
    """Record the completed gate run in the append-only reconciliation log (Slice99 Task 4).

    Carries only the safe summary's sha256, aggregate counts, and status codes - never the
    data-room root, ledger path, or any private filename. The log lives next to the ledger
    (``.local_reports/reconciliation_log.jsonl`` under the default layout). Fail-closed: a
    reconciliation failure fails the gate run.
    """
    from idis.evaluation.local_reports_log import (
        RECONCILIATION_LOG_FILENAME,
        append_reconciliation_entry,
    )

    summary_sha256 = hashlib.sha256(
        json.dumps(summary, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    counts: dict[str, int] = {"files_total": len(records)}
    for status, count in sorted(Counter(str(r["status"]) for r in records).items()):
        counts[f"status_{status.lower()}"] = count

    append_reconciliation_entry(
        artifact_type="real_example_gate_summary",
        artifact_id=f"real_example_gate:{mode.value}",
        sha256=summary_sha256,
        counts=counts,
        status_code="GATE_COMPLETED",
        log_path=ledger_file.parent / RECONCILIATION_LOG_FILENAME,
    )


def build_data_room_package_inventory_summary(
    *,
    root: str | Path = DEFAULT_REAL_EXAMPLE_ROOT,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
) -> dict[str, object]:
    """Safe data-room package inventory over a private tree (Slice77 thin hook).

    Runs the private real_example gate in INVENTORY_ONLY mode -- no parsing, OCR,
    media, provider/network calls, FULL run, or readiness change -- and projects the
    gate's safe aggregate onto the durable data-room package summary shape. Emits safe
    aggregates only (``file_count`` + ``counts_by_*`` + ``ledger_entry_count``); never
    raw paths, filenames, content, object keys, or manifest/storage URIs.
    """
    gate_summary = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.INVENTORY_ONLY,
        safe_summary=True,
        emit_progress=False,
    )
    return {
        "safe_summary": gate_summary["safe_summary"],
        "source": "real_example_private_inventory",
        "mode": gate_summary["mode"],
        "file_count": gate_summary["total_files"],
        "ledger_entry_count": gate_summary["ledger_entry_count"],
        "counts_by_extension": gate_summary["counts_by_extension"],
        "counts_by_status": gate_summary["counts_by_status"],
        "counts_by_reason_code": gate_summary["counts_by_reason_code"],
    }


def build_real_example_parse_readiness_summary(
    *,
    root: str | Path = DEFAULT_REAL_EXAMPLE_ROOT,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
    per_file_timeout_seconds: float = DEFAULT_PER_FILE_TIMEOUT_SECONDS,
    max_runtime_seconds: float | None = None,
    max_memory_mb: int | None = None,
    parse_attempt_fn: ParseAttemptFn | None = None,
    ocr_enabled: bool = False,
    ocr_max_pages: int = MAX_OCR_PAGES,
    ocr_timeout_seconds: float = 30.0,
    ocr_dpi: int = 200,
    media_enabled: bool = False,
    media_timeout_seconds: float = 30.0,
    media_adapter: str = MEDIA_ADAPTER_NONE,
    media_model_name: str | None = None,
    media_model_path: str | None = None,
    media_allow_model_download: bool = False,
    media_language: str = DEFAULT_MEDIA_LANGUAGE,
    media_compute_type: str = DEFAULT_MEDIA_COMPUTE_TYPE,
    media_max_duration_seconds: float = MAX_MEDIA_DURATION_SECONDS,
) -> dict[str, object]:
    """Safe PARSE_SUPPORTED parse-readiness projection over a private tree (Slice81).

    Wraps ``run_real_example_gate`` in PARSE_SUPPORTED mode (never INVENTORY_ONLY) and
    re-projects its safe aggregate, adding ``counts_by_evidence_class`` (extension-derived),
    ``counts_by_deferral_class`` (intended vs unintended), the unintended reason-code
    breakdown, and a ``parse_ready`` verdict (True only when zero unintended deferrals).
    Does NOT mutate or extend ``_safe_summary``. Emits safe aggregates only -- never raw
    paths, filenames, content, object keys, model paths, env values, command output, or
    secrets; media bytes are never read (the gate's media-no-read behavior is preserved).
    """
    gate_summary = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        per_file_timeout_seconds=per_file_timeout_seconds,
        max_runtime_seconds=max_runtime_seconds,
        max_memory_mb=max_memory_mb,
        emit_progress=False,
        parse_attempt_fn=parse_attempt_fn,
        ocr_enabled=ocr_enabled,
        ocr_max_pages=ocr_max_pages,
        ocr_timeout_seconds=ocr_timeout_seconds,
        ocr_dpi=ocr_dpi,
        media_enabled=media_enabled,
        media_timeout_seconds=media_timeout_seconds,
        media_adapter=media_adapter,
        media_model_name=media_model_name,
        media_model_path=media_model_path,
        media_allow_model_download=media_allow_model_download,
        media_language=media_language,
        media_compute_type=media_compute_type,
        media_max_duration_seconds=media_max_duration_seconds,
    )
    counts_by_extension = gate_summary["counts_by_extension"]
    counts_by_reason_code = gate_summary["counts_by_reason_code"]
    assert isinstance(counts_by_extension, dict)
    assert isinstance(counts_by_reason_code, dict)
    counts_by_evidence_class = _aggregate_counts_by(
        counts_by_extension, _evidence_class_for_extension
    )
    counts_by_deferral_class = _aggregate_counts_by(
        counts_by_reason_code, _deferral_class_for_reason_code
    )
    unintended_deferral_reason_codes = {
        reason_code: count
        for reason_code, count in sorted(counts_by_reason_code.items())
        if _deferral_class_for_reason_code(reason_code) == _DEFERRAL_CLASS_UNINTENDED
    }
    parse_ready = counts_by_deferral_class.get(_DEFERRAL_CLASS_UNINTENDED, 0) == 0
    return {
        "safe_summary": gate_summary["safe_summary"],
        "source": "real_example_private_parse_readiness",
        "mode": gate_summary["mode"],
        "total_files": gate_summary["total_files"],
        "processed_files": gate_summary["processed_files"],
        "ledger_entry_count": gate_summary["ledger_entry_count"],
        "counts_by_extension": counts_by_extension,
        "counts_by_status": gate_summary["counts_by_status"],
        "counts_by_parser_outcome": gate_summary["counts_by_parser_outcome"],
        "counts_by_reason_code": counts_by_reason_code,
        "counts_by_evidence_class": counts_by_evidence_class,
        "counts_by_deferral_class": counts_by_deferral_class,
        "unintended_deferral_reason_codes": unintended_deferral_reason_codes,
        "parse_ready": parse_ready,
    }


def _run_parse_readiness_cli(args: argparse.Namespace) -> int:
    """Run the Slice81 PARSE_SUPPORTED parse-readiness projection and print safe JSON."""
    try:
        summary = build_real_example_parse_readiness_summary(
            root=args.root,
            ledger_path=args.ledger,
            per_file_timeout_seconds=args.per_file_timeout_seconds,
            max_runtime_seconds=args.max_runtime_seconds,
            max_memory_mb=args.max_memory_mb,
            ocr_enabled=args.ocr_enabled,
            ocr_max_pages=args.ocr_max_pages,
            ocr_timeout_seconds=args.ocr_timeout_seconds,
            ocr_dpi=args.ocr_dpi,
            media_enabled=args.media_enabled,
            media_timeout_seconds=args.media_timeout_seconds,
            media_adapter=args.media_adapter,
            media_model_name=args.media_model_name,
            media_model_path=args.media_model_path,
            media_allow_model_download=args.media_allow_model_download,
            media_language=args.media_language,
            media_compute_type=args.media_compute_type,
            media_max_duration_seconds=args.media_max_duration_seconds,
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


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for the private real_example gate."""
    parser = argparse.ArgumentParser(
        description="Run the local-only private real_example gate.",
        epilog=(
            "Examples:\n"
            "  python scripts/run_real_example_gate.py --inventory-only\n"
            "  python scripts/run_real_example_gate.py --parse-supported --safe-summary\n"
            "  python scripts/run_real_example_gate.py --root real_example --parse-readiness\n"
            "  python scripts/run_real_example_gate.py --parse-supported --safe-summary "
            "--per-file-timeout-seconds 20 --max-runtime-seconds 900"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--inventory-only", action="store_true")
    modes.add_argument("--parse-supported", action="store_true")
    modes.add_argument(
        "--parse-readiness",
        action="store_true",
        help="Run the PARSE_SUPPORTED parse-readiness projection (evidence/deferral class + "
        "parse_ready). Always emits the safe projection.",
    )
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
    parser.add_argument("--media-enabled", action="store_true")
    parser.add_argument("--media-timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--media-adapter",
        choices=sorted(MEDIA_ADAPTER_CHOICES),
        default=os.environ.get("IDIS_MEDIA_ADAPTER", MEDIA_ADAPTER_NONE),
    )
    parser.add_argument("--media-model-name", default=os.environ.get("IDIS_MEDIA_STT_MODEL_NAME"))
    parser.add_argument("--media-model-path", default=os.environ.get("IDIS_MEDIA_STT_MODEL_PATH"))
    parser.add_argument(
        "--media-allow-model-download",
        action="store_true",
        default=_media_allow_model_download_default(),
    )
    parser.add_argument(
        "--media-language",
        default=os.environ.get("IDIS_MEDIA_LANGUAGE", DEFAULT_MEDIA_LANGUAGE),
    )
    parser.add_argument(
        "--media-compute-type",
        default=os.environ.get("IDIS_MEDIA_COMPUTE_TYPE", DEFAULT_MEDIA_COMPUTE_TYPE),
    )
    parser.add_argument(
        "--media-max-duration-seconds",
        type=float,
        default=_env_float(
            "IDIS_MEDIA_MAX_DURATION_SECONDS",
            default=MAX_MEDIA_DURATION_SECONDS,
        ),
    )
    parser.add_argument("--emit-progress", action="store_true")
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args(argv)

    if args.parse_readiness:
        return _run_parse_readiness_cli(args)

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
            emit_progress=args.emit_progress and not args.no_progress,
            ocr_enabled=args.ocr_enabled,
            ocr_max_pages=args.ocr_max_pages,
            ocr_timeout_seconds=args.ocr_timeout_seconds,
            ocr_dpi=args.ocr_dpi,
            media_enabled=args.media_enabled,
            media_timeout_seconds=args.media_timeout_seconds,
            media_adapter=args.media_adapter,
            media_model_name=args.media_model_name,
            media_model_path=args.media_model_path,
            media_allow_model_download=args.media_allow_model_download,
            media_language=args.media_language,
            media_compute_type=args.media_compute_type,
            media_max_duration_seconds=args.media_max_duration_seconds,
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
    ocr_policy_key: str | None,
    media_enabled: bool,
    media_timeout_seconds: float,
    media_adapter: str,
    media_model_name: str | None,
    media_model_path: str | None,
    media_allow_model_download: bool,
    media_language: str,
    media_compute_type: str,
    media_max_duration_seconds: float,
    media_policy_key: str | None,
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
        ocr_policy_key=ocr_policy_key,
        media_enabled=media_enabled,
        media_policy_key=media_policy_key,
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
    if capability.support_status == DocumentSupportStatus.TOO_LARGE:
        return ParseAttempt.deferred(reason_code="file_too_large")

    should_parse_image_ocr = _should_parse_image_with_ocr(
        extension=file.extension,
        support_status=capability.support_status,
        ocr_enabled=ocr_enabled,
    )
    should_parse_text = file.extension in TEXT_PARSE_EXTENSIONS
    should_parse_media = _should_parse_media(
        extension=file.extension,
        support_status=capability.support_status,
        media_enabled=media_enabled,
    )
    if (
        file.extension not in SUPPORTED_PARSE_EXTENSIONS
        and not should_parse_image_ocr
        and not should_parse_text
        and not should_parse_media
    ):
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

    if (
        not should_parse_image_ocr
        and not should_parse_text
        and not should_parse_media
        and not _should_parse(
            capability.support_status,
            capability.parser_name,
        )
    ):
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

    if should_parse_media and not _media_adapter_attemptable(
        media_adapter=media_adapter,
        media_model_name=media_model_name,
        media_model_path=media_model_path,
        media_allow_model_download=media_allow_model_download,
    ):
        return ParseAttempt.media_required(reason_code="media_transcription_unavailable")

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
        media_enabled=media_enabled,
        media_timeout_seconds=media_timeout_seconds,
        media_adapter=media_adapter,
        media_model_name=media_model_name,
        media_model_path=media_model_path,
        media_allow_model_download=media_allow_model_download,
        media_language=media_language,
        media_compute_type=media_compute_type,
        media_max_duration_seconds=media_max_duration_seconds,
    )


def _inventory_files(root: Path) -> list[_InventoryFile]:
    records: list[_InventoryFile] = []
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=_sort_key(root)):
        extension = path.suffix.lower() or ".unknown"
        try:
            if extension in MEDIA_EXTENSIONS:
                file_stat = path.stat()
                size_bytes = file_stat.st_size
                sha256 = _media_no_read_file_key(
                    extension=extension,
                    size_bytes=size_bytes,
                    modified_ns=file_stat.st_mtime_ns,
                )
            else:
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


def _validate_media_options(
    *,
    media_enabled: bool,
    media_timeout_seconds: float,
    media_adapter: str,
    media_max_duration_seconds: float,
) -> None:
    if media_adapter not in MEDIA_ADAPTER_CHOICES:
        raise ValueError("Media adapter is outside the allowed choices")
    if not media_enabled:
        return
    if not 0 < media_timeout_seconds <= MAX_MEDIA_TIMEOUT_SECONDS:
        raise ValueError("Media timeout is outside the allowed range")
    if not 0 < media_max_duration_seconds <= MAX_MEDIA_DURATION_SECONDS:
        raise ValueError("Media max duration is outside the allowed range")


def _media_adapter_attemptable(
    *,
    media_adapter: str,
    media_model_name: str | None,
    media_model_path: str | None,
    media_allow_model_download: bool,
) -> bool:
    if media_adapter != FASTER_WHISPER_ADAPTER_NAME:
        return False
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        return False
    if media_model_path:
        return probe_faster_whisper_model(
            FasterWhisperMediaConfig(
                model_path=media_model_path,
                allow_model_download=False,
            )
        ).can_attempt
    return probe_faster_whisper_model(
        FasterWhisperMediaConfig(
            model_name=media_model_name,
            allow_model_download=media_allow_model_download,
        )
    ).can_attempt


def _media_model_policy_key(
    *,
    media_model_name: str | None,
    media_model_path: str | None,
) -> str:
    if media_model_path:
        model_path = Path(media_model_path).expanduser()
        model_digest = hashlib.sha256()
        try:
            model_stat = model_path.stat()
        except OSError:
            model_digest.update(str(model_path).encode())
            return f"path-missing-sha256:{model_digest.hexdigest()[:16]}"
        _update_model_signature(
            digest=model_digest,
            label="root",
            stat_payload=f"{model_stat.st_mode}:{model_stat.st_size}:{model_stat.st_mtime_ns}",
        )
        if model_path.is_dir():
            for child_path in sorted(
                model_path.rglob("*"),
                key=lambda path: path.relative_to(model_path).as_posix().lower(),
            ):
                try:
                    child_stat = child_path.stat()
                except OSError:
                    continue
                relative_hash = hashlib.sha256(
                    child_path.relative_to(model_path).as_posix().encode()
                ).hexdigest()
                _update_model_signature(
                    digest=model_digest,
                    label=relative_hash,
                    stat_payload=(
                        f"{child_stat.st_mode}:{child_stat.st_size}:{child_stat.st_mtime_ns}"
                    ),
                )
            return f"dir-sha256:{model_digest.hexdigest()[:16]}"
        return f"file-sha256:{model_digest.hexdigest()[:16]}"
    if media_model_name:
        return f"name:{media_model_name}"
    return "none"


def _update_model_signature(
    *,
    digest: Any,
    label: str,
    stat_payload: str,
) -> None:
    digest.update(f"{label}:{stat_payload};".encode())


def _hash_file_streaming(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as file:
        while chunk := file.read(HASH_CHUNK_SIZE_BYTES):
            size_bytes += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size_bytes


def _media_no_read_file_key(*, extension: str, size_bytes: int, modified_ns: int) -> str:
    payload = f"{extension}:{size_bytes}:{modified_ns}".encode()
    return f"media-no-read:{hashlib.sha256(payload).hexdigest()}"


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


def _should_parse_image_with_ocr(
    *,
    extension: str,
    support_status: DocumentSupportStatus,
    ocr_enabled: bool,
) -> bool:
    return (
        ocr_enabled
        and extension in OCR_IMAGE_EXTENSIONS
        and support_status == DocumentSupportStatus.SCANNED_OR_IMAGE_ONLY
    )


def _should_parse_media(
    *,
    extension: str,
    support_status: DocumentSupportStatus,
    media_enabled: bool,
) -> bool:
    return (
        media_enabled
        and extension in MEDIA_EXTENSIONS
        and support_status == DocumentSupportStatus.CONVERSION_REQUIRED
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


# --- Slice81 parse-readiness classifiers (pure; extension/reason-code only; no IO) ---

_EVIDENCE_CLASS_BY_EXTENSION: dict[str, str] = {
    "pdf": "PDF",
    "xlsx": "SPREADSHEET",
    "xlsm": "SPREADSHEET",
    "docx": "DOCUMENT",
    "pptx": "PRESENTATION",
    "html": "WEB_TEXT",
    "htm": "WEB_TEXT",
    "txt": "WEB_TEXT",
    "png": "IMAGE",
    "jpg": "IMAGE",
    "jpeg": "IMAGE",
    "tif": "IMAGE",
    "tiff": "IMAGE",
    "bmp": "IMAGE",
    "mp4": "MEDIA",
}
_EVIDENCE_CLASS_OTHER = "OTHER"

# Locked intended terminal blockers (safe, expected). Everything else - retryable/transient
# codes, ``unknown_format``, empty/missing, or any unrecognized future code - is an UNINTENDED
# deferral (fail-safe: parse readiness must never silently accept an unknown blocker).
_INTENDED_REASON_CODES: frozenset[str] = frozenset(
    {
        "parsed",
        "conversion_required",
        "ocr_required",
        "ocr_no_text_extracted",
        "media_transcription_unavailable",
        "media_no_text_extracted",
        "media_duration_exceeded",
        "unsupported_format",
        "unsupported_in_slice_29",
        "file_too_large",
        "encrypted_pdf",
        "no_text_extracted",
        "corrupted_file",
        "inventory_only",
    }
)
_UNINTENDED_REASON_CODES: frozenset[str] = frozenset(
    RETRYABLE_REASON_CODES
    | {"unknown_format", "media_transcription_failed", "media_transcription_timeout"}
)
_DEFERRAL_CLASS_INTENDED = "intended"
_DEFERRAL_CLASS_UNINTENDED = "unintended"


def _evidence_class_for_extension(extension: str) -> str:
    """Map a file extension to a safe evidence class.

    Derived from the extension token only - never a filename or content. Case-insensitive;
    a leading dot is optional. Unknown/empty tokens map to ``OTHER``.
    """
    token = str(extension).strip().lower().lstrip(".")
    return _EVIDENCE_CLASS_BY_EXTENSION.get(token, _EVIDENCE_CLASS_OTHER)


def _deferral_class_for_reason_code(reason_code: str | None) -> str:
    """Classify a parse reason code as an intended blocker or an unintended deferral.

    Fail-safe: only the locked intended set is ``intended``; retryable/transient codes,
    ``unknown_format``, empty/missing, and any unrecognized future code are ``unintended``
    so parse readiness never silently accepts an unknown blocker.
    """
    code = (reason_code or "").strip()
    if code in _UNINTENDED_REASON_CODES:
        return _DEFERRAL_CLASS_UNINTENDED
    if code in _INTENDED_REASON_CODES:
        return _DEFERRAL_CLASS_INTENDED
    return _DEFERRAL_CLASS_UNINTENDED


def _aggregate_counts_by(
    counts: dict[str, int], classifier: Callable[[str], str]
) -> dict[str, int]:
    """Re-bucket an existing ``{key: count}`` aggregate by a classifier, count-weighted.

    Emits only classifier labels (evidence class / deferral class) - never the original
    keys - so reason/extension strings are not echoed beyond the gate's already-safe counts.
    """
    aggregated: Counter[str] = Counter()
    for key, count in counts.items():
        aggregated[classifier(key)] += count
    return dict(sorted(aggregated.items()))


def _runtime_exceeded(started_at: float, max_runtime_seconds: float | None) -> bool:
    return max_runtime_seconds is not None and time.monotonic() - started_at >= max_runtime_seconds


def _record_ledger_entry(
    *,
    ledger: dict[str, Any],
    file: _InventoryFile,
    attempt: ParseAttempt,
    mode: GateMode,
    ocr_policy_key: str | None,
    media_policy_key: str | None,
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
        ocr_policy_key=ocr_policy_key if file.extension == ".pdf" else None,
        media_policy_key=media_policy_key if file.extension in MEDIA_EXTENSIONS else None,
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


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, *, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _media_allow_model_download_default() -> bool:
    return _env_bool("IDIS_MEDIA_STT_ALLOW_DOWNLOAD", default=False)


__all__ = [
    "GateMode",
    "ParseAttempt",
    "build_data_room_package_inventory_summary",
    "main",
    "run_real_example_gate",
]

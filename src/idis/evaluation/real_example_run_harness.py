"""Private production-style real_example upload-to-FULL-run harness."""

from __future__ import annotations

import contextlib
import json
import logging
import multiprocessing
import os
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.models.document_classification import DocumentSupportStatus, DocumentTriageStatus
from idis.services.documents.parser_capabilities import capability_for_document
from idis.services.runs.strict_full_live import (
    STRICT_FULL_LIVE_BLOCKED,
    build_strict_full_live_readiness_report,
)

PUBLIC_UPLOAD_EXTENSIONS = frozenset({".pdf", ".xlsx", ".docx", ".pptx"})
MEDIA_EXTENSIONS = frozenset({".mp4"})
UPLOAD_CONTENT_TYPE = "application/octet-stream"
DEFAULT_API_KEY = "slice42-private-real-example-harness-key"
DEFAULT_TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEFAULT_ACTOR_ID = "slice42-private-harness"
RUN_TIMEOUT_REASON = "RUN_TIMEOUT"
UPLOAD_THROUGHPUT_LIMIT_REASON = "UPLOAD_THROUGHPUT_LIMIT"
RESUME_UNSUPPORTED_REASON = "RESUME_UNSUPPORTED"
CONCURRENCY_DISABLED_REASON = "CONCURRENCY_DISABLED_BY_DEFAULT"
UPLOAD_TIMING_PHASES = ("read", "upload_api", "run_start")
INTERNAL_UPLOAD_API_PHASES = frozenset(
    {
        "route_body_read/hash_validation",
        "object_store_write",
        "parse",
        "span_generation",
        "persistence",
        "audit",
    }
)
ELAPSED_BUCKETS = frozenset(
    {
        "under_1s",
        "1_to_5s",
        "5_to_30s",
        "30_to_60s",
        "60_to_300s",
        "over_300s",
    }
)
PARSER_DIAGNOSTIC_EXTENSIONS = frozenset({".pdf", ".xlsx", ".docx", ".pptx", ".unknown", ".other"})
PARSER_DIAGNOSTIC_OUTCOMES = frozenset({"parsed", "failed"})
PDF_DIAGNOSTIC_OUTCOME_REASONS = frozenset(
    {
        "parsed_text",
        "parsed_empty_password_encrypted",
        "parsed_ocr",
        "failed_encrypted",
        "failed_no_text",
        "failed_ocr_no_text",
        "failed_corrupted",
        "failed_ocr_unavailable",
        "failed_ocr_timeout",
        "failed_ocr_failed",
        "failed_max_size",
        "failed_max_pages",
        "failed_other",
    }
)
PDF_PARSE_SUBPHASES = frozenset(
    {
        "reader_init",
        "decrypt_empty_password",
        "page_count",
        "text_extraction/span_build",
    }
)
logger = logging.getLogger(__name__)
T = TypeVar("T")


class _ResponseLike(Protocol):
    status_code: int

    def json(self) -> dict[str, Any]:
        """Return the JSON response body."""


class _ApiClientLike(Protocol):
    def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        content: bytes | None = None,
        json: dict[str, Any] | None = None,
    ) -> _ResponseLike:
        """Post to a public API route."""


@dataclass(frozen=True, slots=True)
class RealExampleFullRunHarnessOptions:
    """Options for the private production-style real_example run harness."""

    root: str | Path
    private_run: bool = True
    safe_summary: bool = True
    output_path: str | Path | None = None
    api_client: _ApiClientLike | None = None
    api_key: str = DEFAULT_API_KEY
    deal_name: str = "Private real_example controlled run"
    company_name: str = "Private real_example company"
    local_stt_model_configured: bool = False
    max_upload_files: int | None = None
    max_upload_concurrency: int = 1
    run_timeout_seconds: float | None = None
    run_poll_interval_seconds: float = 1.0
    resume_state_output_path: str | Path | None = None
    checkpoint_output_path: str | Path | None = None
    require_full_live: bool = False


@dataclass(frozen=True, slots=True)
class _FileDecision:
    path: Path
    extension: str
    upload_status: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class _RunPostAttempt:
    response: _ResponseLike | None
    timed_out: bool
    elapsed_seconds: float
    timeout_snapshot: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class _TimedCallAttempt(Generic[T]):
    result: T | None
    timed_out: bool
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class _UploadPayload:
    extension: str
    headers: dict[str, str]
    params: dict[str, Any]
    content: bytes


class _UploadTimingRecorder:
    """Collect aggregate-only timing data for the private upload harness."""

    def __init__(
        self,
        *,
        max_upload_concurrency: int,
        internal_upload_phase_summary_provider: Callable[[], dict[str, Any] | None] | None = None,
    ) -> None:
        self._max_upload_concurrency = max_upload_concurrency
        self._internal_upload_phase_summary_provider = internal_upload_phase_summary_provider
        self._phase_elapsed_seconds: dict[str, list[float]] = {
            phase: [] for phase in UPLOAD_TIMING_PHASES
        }
        self._outcomes: Counter[str] = Counter()
        self._attempted_count = 0
        self._partial = False

    def start_attempt(self) -> None:
        self._attempted_count += 1

    def record_phase(self, phase: str, elapsed_seconds: float) -> None:
        if phase not in self._phase_elapsed_seconds:
            self._phase_elapsed_seconds[phase] = []
        self._phase_elapsed_seconds[phase].append(max(0.0, elapsed_seconds))

    def record_outcome(self, outcome: str) -> None:
        self._outcomes[outcome] += 1

    def mark_partial(self) -> None:
        self._partial = True

    def to_summary(self) -> dict[str, Any]:
        total_observed_upload_seconds = sum(
            sum(self._phase_elapsed_seconds[phase]) for phase in ("read", "upload_api")
        )
        internal_upload_api = self._internal_upload_api_summary()
        phase_observability = {
            "read": "observed",
            "upload_api": "observed",
            "parse": "included_in_upload_api",
            "span_generation": "included_in_upload_api",
            "run_start": "observed" if self._phase_elapsed_seconds["run_start"] else "not_observed",
        }
        if internal_upload_api is not None:
            internal_phases = internal_upload_api.get("phase_counts_by_elapsed_bucket")
            if isinstance(internal_phases, dict):
                if "parse" in internal_phases:
                    phase_observability["parse"] = "observed"
                if "span_generation" in internal_phases:
                    phase_observability["span_generation"] = "observed"

        summary: dict[str, Any] = {
            "enabled": True,
            "partial": self._partial,
            "attempted_count": self._attempted_count,
            "counts_by_outcome": dict(sorted(self._outcomes.items())),
            "phase_counts_by_elapsed_bucket": {
                phase: _elapsed_bucket_counts(values)
                for phase, values in self._phase_elapsed_seconds.items()
                if values
            },
            "phase_total_elapsed_bucket": {
                phase: _elapsed_seconds_bucket(sum(values))
                for phase, values in self._phase_elapsed_seconds.items()
                if values
            },
            "phase_max_elapsed_bucket": {
                phase: _elapsed_seconds_bucket(max(values))
                for phase, values in self._phase_elapsed_seconds.items()
                if values
            },
            "observable_slowest_phase": self._observable_slowest_phase(),
            "phase_observability": phase_observability,
            "throughput": {
                "attempted_per_minute_bucket": _rate_per_minute_bucket(
                    self._attempted_count,
                    total_observed_upload_seconds,
                ),
                "uploaded_per_minute_bucket": _rate_per_minute_bucket(
                    self._outcomes["uploaded"],
                    total_observed_upload_seconds,
                ),
            },
            "concurrency": {
                "enabled": False,
                "max_workers": self._max_upload_concurrency,
                "reason_code": CONCURRENCY_DISABLED_REASON,
            },
        }
        if internal_upload_api is not None:
            summary["internal_upload_api"] = internal_upload_api
        return summary

    def _observable_slowest_phase(self) -> str | None:
        phase_totals = {
            phase: sum(values) for phase, values in self._phase_elapsed_seconds.items() if values
        }
        if not phase_totals:
            return None
        return max(sorted(phase_totals), key=lambda phase: phase_totals[phase])

    def _internal_upload_api_summary(self) -> dict[str, Any] | None:
        if self._internal_upload_phase_summary_provider is None:
            return None
        summary = self._internal_upload_phase_summary_provider()
        return _safe_internal_upload_api_summary(summary)


def run_real_example_full_run_harness(
    options: RealExampleFullRunHarnessOptions,
) -> dict[str, Any]:
    """Run a private aggregate-only real_example upload and selected FULL attempt.

    Args:
        options: Harness configuration. ``private_run`` and ``safe_summary`` must both
            remain true; the harness never exposes private filenames, paths, text, or spans.

    Returns:
        Aggregate-only safe metadata about upload selection and the FULL run attempt.

    Raises:
        FileNotFoundError: If the root does not exist.
        NotADirectoryError: If the root is not a directory.
        ValueError: If private/safe-summary controls are disabled.
    """
    _validate_options(options)
    root = Path(options.root)
    _validate_root(root)

    strict_full_live_report: dict[str, Any] | None = None
    if options.require_full_live:
        files = _inventory_files(root)
        report = build_strict_full_live_readiness_report(
            data_room_file_extensions=[path.suffix for path in files],
        )
        strict_full_live_report = report.model_dump(mode="json")
        if not report.may_proceed:
            summary = _strict_full_live_block_summary(
                files=files,
                strict_full_live_report=strict_full_live_report,
            )
        elif options.api_client is not None:
            summary = _run_with_client(options=options, root=root, client=options.api_client)
        else:
            with _default_api_client(options) as client:
                summary = _run_with_client(options=options, root=root, client=client)
        summary["strict_full_live"] = strict_full_live_report
    elif options.api_client is not None:
        summary = _run_with_client(options=options, root=root, client=options.api_client)
    else:
        with _default_api_client(options) as client:
            summary = _run_with_client(options=options, root=root, client=client)

    if options.output_path is not None:
        destination = Path(options.output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(summary, sort_keys=True, indent=2), encoding="utf-8")
    if options.resume_state_output_path is not None:
        destination = Path(options.resume_state_output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(_resume_state_for_output(summary), sort_keys=True, indent=2),
            encoding="utf-8",
        )
    return summary


def run_real_example_full_run_harness_process(
    options: RealExampleFullRunHarnessOptions,
    *,
    process_factory: Callable[..., Any] = multiprocessing.Process,
) -> dict[str, Any]:
    """Run the private harness in a child process with parent-enforced deadline."""
    _validate_options(options)
    if options.api_client is not None:
        raise ValueError("process harness cannot accept an in-process api_client")
    if options.run_timeout_seconds is None:
        raise ValueError("process harness requires run_timeout_seconds")

    with tempfile.TemporaryDirectory(prefix="idis_slice45_process_") as work_dir:
        work_path = Path(work_dir)
        output_path = (
            Path(options.output_path) if options.output_path else work_path / "summary.json"
        )
        checkpoint_path = (
            Path(options.checkpoint_output_path)
            if options.checkpoint_output_path
            else work_path / "checkpoint.json"
        )
        child_options = replace(
            options,
            api_client=None,
            output_path=output_path,
            checkpoint_output_path=checkpoint_path,
        )
        process = process_factory(target=_run_harness_process_child, args=(child_options,))
        process.start()
        process.join(options.run_timeout_seconds)
        if process.is_alive():
            process.terminate()
            process.join(5)
            summary = _process_timeout_summary_from_checkpoint(checkpoint_path)
            _write_summary_outputs(
                summary=summary,
                output_path=options.output_path,
                resume_state_output_path=options.resume_state_output_path,
            )
            return summary

        if output_path.exists():
            return _load_json_object(output_path)
        if checkpoint_path.exists():
            return _strip_checkpoint_metadata(_load_json_object(checkpoint_path))
        return _minimal_process_timeout_summary(stage="process")


def _run_harness_process_child(options: RealExampleFullRunHarnessOptions) -> None:
    run_real_example_full_run_harness(options)


def _validate_options(options: RealExampleFullRunHarnessOptions) -> None:
    if not options.private_run:
        raise ValueError("real_example run harness requires private_run=True")
    if not options.safe_summary:
        raise ValueError("real_example run harness requires safe_summary=True")
    if options.max_upload_files is not None and options.max_upload_files <= 0:
        raise ValueError("max_upload_files must be positive when provided")
    if options.max_upload_concurrency != 1:
        raise ValueError(
            "max_upload_concurrency must remain 1 until upload concurrency is proven safe"
        )
    if options.run_timeout_seconds is not None and options.run_timeout_seconds <= 0:
        raise ValueError("run_timeout_seconds must be positive when provided")
    if options.run_poll_interval_seconds <= 0:
        raise ValueError("run_poll_interval_seconds must be positive")


def _validate_root(root: Path) -> None:
    if not root.exists():
        raise FileNotFoundError("real_example root does not exist")
    if not root.is_dir():
        raise NotADirectoryError("real_example root is not a directory")


def _run_with_client(
    *,
    options: RealExampleFullRunHarnessOptions,
    root: Path,
    client: _ApiClientLike,
) -> dict[str, Any]:
    harness_started_at = time.monotonic()
    upload_timing = _UploadTimingRecorder(
        max_upload_concurrency=options.max_upload_concurrency,
        internal_upload_phase_summary_provider=lambda: _internal_upload_phase_summary(client),
    )
    inventory_attempt = _call_with_optional_deadline(
        call=lambda: _inventory_files(root),
        timeout_seconds=_remaining_timeout_seconds(harness_started_at, options.run_timeout_seconds),
        poll_interval_seconds=options.run_poll_interval_seconds,
    )
    if inventory_attempt.timed_out:
        return _summary(
            files=[],
            decisions=[],
            uploaded_document_ids=[],
            status="blocked",
            run_body=None,
            run_http_status=None,
            blocker=_blocker(
                stage="inventory",
                reason_code=RUN_TIMEOUT_REASON,
                http_status=None,
            ),
            run_elapsed_seconds=time.monotonic() - harness_started_at,
            upload_profile=upload_timing.to_summary(),
        )
    files = _completed_call_result_or_raise(inventory_attempt)
    _write_checkpoint(
        options=options,
        stage="inventory",
        files=files,
        decisions=[],
        uploaded_document_ids=[],
        upload_timing=upload_timing,
    )
    deal_attempt = _call_response_with_optional_deadline(
        call=lambda: _client_post(
            client,
            "/v1/deals",
            headers=_headers(options.api_key),
            json={"name": options.deal_name, "company_name": options.company_name},
        ),
        timeout_seconds=_remaining_timeout_seconds(harness_started_at, options.run_timeout_seconds),
        poll_interval_seconds=options.run_poll_interval_seconds,
    )
    if deal_attempt.timed_out:
        return _summary(
            files=files,
            decisions=[],
            uploaded_document_ids=[],
            status="blocked",
            run_body=None,
            run_http_status=None,
            blocker=_blocker(
                stage="deal_create",
                reason_code=RUN_TIMEOUT_REASON,
                http_status=None,
            ),
            run_elapsed_seconds=time.monotonic() - harness_started_at,
            upload_profile=upload_timing.to_summary(),
        )
    deal_response = _completed_response_or_raise(deal_attempt)
    if deal_response.status_code != 201:
        return _summary(
            files=files,
            decisions=[],
            uploaded_document_ids=[],
            status="blocked",
            run_body=None,
            run_http_status=None,
            blocker=_blocker(
                stage="deal_create",
                reason_code=_response_reason(deal_response),
                http_status=deal_response.status_code,
            ),
            upload_profile=upload_timing.to_summary(),
        )

    deal_id = _safe_required_string(deal_response.json(), "deal_id", default="deal")
    decisions: list[_FileDecision] = []
    uploaded_document_ids: list[str] = []
    upload_index = 0
    _write_checkpoint(
        options=options,
        stage="deal_create",
        files=files,
        decisions=decisions,
        uploaded_document_ids=uploaded_document_ids,
        upload_timing=upload_timing,
    )

    for file_path in files:
        triage_path = file_path

        def triage_current_file(path: Path = triage_path) -> _FileDecision:
            return _decision_for_file(
                path=path,
                local_stt_model_configured=options.local_stt_model_configured,
            )

        triage_attempt = _call_with_optional_deadline(
            call=triage_current_file,
            timeout_seconds=_remaining_timeout_seconds(
                harness_started_at,
                options.run_timeout_seconds,
            ),
            poll_interval_seconds=options.run_poll_interval_seconds,
        )
        if triage_attempt.timed_out:
            return _summary(
                files=files,
                decisions=decisions,
                uploaded_document_ids=uploaded_document_ids,
                status="blocked",
                run_body=None,
                run_http_status=None,
                blocker=_blocker(
                    stage="file_triage",
                    reason_code=RUN_TIMEOUT_REASON,
                    http_status=None,
                ),
                run_elapsed_seconds=time.monotonic() - harness_started_at,
                upload_profile=upload_timing.to_summary(),
            )
        decision = _completed_call_result_or_raise(triage_attempt)
        if decision.upload_status != "upload_candidate":
            decisions.append(decision)
            continue
        if options.max_upload_files is not None and upload_index >= options.max_upload_files:
            decisions.append(
                _FileDecision(
                    path=file_path,
                    extension=decision.extension,
                    upload_status="deferred",
                    reason_code="upload_limit_reached",
                )
            )
            continue

        upload_index += 1
        upload_path = file_path
        current_upload_index = upload_index

        def read_current_file(
            path: Path = upload_path,
            index: int = current_upload_index,
        ) -> _UploadPayload:
            return _read_upload_payload(
                api_key=options.api_key,
                path=path,
                upload_index=index,
            )

        upload_timing.start_attempt()
        read_started_at = time.monotonic()
        try:
            payload_attempt = _call_with_optional_deadline(
                call=read_current_file,
                timeout_seconds=_remaining_timeout_seconds(
                    harness_started_at,
                    options.run_timeout_seconds,
                ),
                poll_interval_seconds=options.run_poll_interval_seconds,
            )
        except OSError:
            upload_timing.record_phase("read", time.monotonic() - read_started_at)
            upload_timing.record_outcome("failed")
            decisions.append(
                _FileDecision(
                    path=file_path,
                    extension=decision.extension,
                    upload_status="failed",
                    reason_code="upload_read_failed",
                )
            )
            _write_checkpoint(
                options=options,
                stage="upload",
                files=files,
                decisions=decisions,
                uploaded_document_ids=uploaded_document_ids,
                upload_timing=upload_timing,
            )
            continue
        upload_timing.record_phase("read", payload_attempt.elapsed_seconds)
        if payload_attempt.timed_out:
            upload_timing.record_outcome("timeout")
            upload_timing.mark_partial()
            return _summary(
                files=files,
                decisions=decisions,
                uploaded_document_ids=uploaded_document_ids,
                status="blocked",
                run_body=None,
                run_http_status=None,
                blocker=_blocker(
                    stage="upload",
                    reason_code=UPLOAD_THROUGHPUT_LIMIT_REASON,
                    http_status=None,
                ),
                run_elapsed_seconds=time.monotonic() - harness_started_at,
                upload_profile=upload_timing.to_summary(),
            )

        payload = _completed_call_result_or_raise(payload_attempt)

        def upload_current_payload(upload_payload: _UploadPayload = payload) -> _ResponseLike:
            return _post_upload_payload(
                client=client,
                deal_id=deal_id,
                payload=upload_payload,
            )

        upload_attempt = _call_response_with_optional_deadline(
            call=upload_current_payload,
            timeout_seconds=_remaining_timeout_seconds(
                harness_started_at,
                options.run_timeout_seconds,
            ),
            poll_interval_seconds=options.run_poll_interval_seconds,
        )
        upload_timing.record_phase("upload_api", upload_attempt.elapsed_seconds)
        if upload_attempt.timed_out:
            upload_timing.record_outcome("timeout")
            upload_timing.mark_partial()
            _write_checkpoint(
                options=options,
                stage="upload",
                files=files,
                decisions=decisions,
                uploaded_document_ids=uploaded_document_ids,
                upload_timing=upload_timing,
            )
            return _summary(
                files=files,
                decisions=decisions,
                uploaded_document_ids=uploaded_document_ids,
                status="blocked",
                run_body=None,
                run_http_status=None,
                blocker=_blocker(
                    stage="upload",
                    reason_code=UPLOAD_THROUGHPUT_LIMIT_REASON,
                    http_status=None,
                ),
                run_elapsed_seconds=time.monotonic() - harness_started_at,
                upload_profile=upload_timing.to_summary(),
            )

        upload_response = _completed_response_or_raise(upload_attempt)
        if upload_response.status_code != 201:
            upload_timing.record_outcome("failed")
            decisions.append(
                _FileDecision(
                    path=file_path,
                    extension=decision.extension,
                    upload_status="failed",
                    reason_code=_response_reason(upload_response),
                )
            )
            _write_checkpoint(
                options=options,
                stage="upload",
                files=files,
                decisions=decisions,
                uploaded_document_ids=uploaded_document_ids,
                upload_timing=upload_timing,
            )
            continue

        body = upload_response.json()
        parse_status = body.get("parse_status")
        if parse_status != "PARSED":
            upload_timing.record_outcome("uploaded_not_parsed")
            reason_code = (
                "upload_parse_status_failed"
                if isinstance(parse_status, str)
                else "upload_parse_status_missing"
            )
            decisions.append(
                _FileDecision(
                    path=file_path,
                    extension=decision.extension,
                    upload_status="uploaded_not_parsed",
                    reason_code=reason_code,
                )
            )
            _write_checkpoint(
                options=options,
                stage="upload",
                files=files,
                decisions=decisions,
                uploaded_document_ids=uploaded_document_ids,
                upload_timing=upload_timing,
            )
            continue
        document_id = body.get("document_id")
        if not isinstance(document_id, str) or not document_id.strip():
            upload_timing.record_outcome("failed")
            decisions.append(
                _FileDecision(
                    path=file_path,
                    extension=decision.extension,
                    upload_status="failed",
                    reason_code="missing_document_id",
                )
            )
            _write_checkpoint(
                options=options,
                stage="upload",
                files=files,
                decisions=decisions,
                uploaded_document_ids=uploaded_document_ids,
                upload_timing=upload_timing,
            )
            continue
        upload_timing.record_outcome("uploaded")
        uploaded_document_ids.append(document_id)
        decisions.append(
            _FileDecision(
                path=file_path,
                extension=decision.extension,
                upload_status="uploaded",
                reason_code="uploaded",
            )
        )
        _write_checkpoint(
            options=options,
            stage="upload",
            files=files,
            decisions=decisions,
            uploaded_document_ids=uploaded_document_ids,
            upload_timing=upload_timing,
        )
        if _deadline_expired(harness_started_at, options.run_timeout_seconds):
            return _summary(
                files=files,
                decisions=decisions,
                uploaded_document_ids=uploaded_document_ids,
                status="blocked",
                run_body=None,
                run_http_status=None,
                blocker=_blocker(
                    stage="upload",
                    reason_code=UPLOAD_THROUGHPUT_LIMIT_REASON,
                    http_status=None,
                ),
                run_elapsed_seconds=time.monotonic() - harness_started_at,
                upload_profile=upload_timing.to_summary(),
            )

    if not uploaded_document_ids:
        return _summary(
            files=files,
            decisions=decisions,
            uploaded_document_ids=uploaded_document_ids,
            status="blocked",
            run_body=None,
            run_http_status=None,
            blocker=_blocker(
                stage="upload",
                reason_code="NO_PUBLIC_UPLOADABLE_DOCUMENTS",
                http_status=None,
            ),
            upload_profile=upload_timing.to_summary(),
        )

    run_payload = {
        "mode": "FULL",
        "source": {
            "type": "deal_documents",
            "document_ids": uploaded_document_ids,
        },
    }
    remaining_timeout_seconds = _remaining_timeout_seconds(
        harness_started_at,
        options.run_timeout_seconds,
    )
    if remaining_timeout_seconds is not None and remaining_timeout_seconds <= 0:
        return _summary(
            files=files,
            decisions=decisions,
            uploaded_document_ids=uploaded_document_ids,
            status="blocked",
            run_body=None,
            run_http_status=None,
            blocker=_blocker(
                stage="run_start",
                reason_code=RUN_TIMEOUT_REASON,
                http_status=None,
            ),
            run_elapsed_seconds=time.monotonic() - harness_started_at,
            upload_profile=upload_timing.to_summary(),
        )
    _write_checkpoint(
        options=options,
        stage="run_start",
        files=files,
        decisions=decisions,
        uploaded_document_ids=uploaded_document_ids,
        upload_timing=upload_timing,
    )
    run_started_at = time.monotonic()
    run_attempt = _post_run_with_optional_deadline(
        client=client,
        url=f"/v1/deals/{deal_id}/runs",
        api_key=options.api_key,
        payload=run_payload,
        timeout_seconds=remaining_timeout_seconds,
        poll_interval_seconds=options.run_poll_interval_seconds,
    )
    upload_timing.record_phase("run_start", time.monotonic() - run_started_at)
    if run_attempt.timed_out:
        _write_checkpoint(
            options=options,
            stage="full_run",
            files=files,
            decisions=decisions,
            uploaded_document_ids=uploaded_document_ids,
            upload_timing=upload_timing,
            run_timeout_snapshot=run_attempt.timeout_snapshot,
            run_elapsed_seconds=run_attempt.elapsed_seconds,
            run_attempted_on_timeout=True,
        )
        return _summary(
            files=files,
            decisions=decisions,
            uploaded_document_ids=uploaded_document_ids,
            status="blocked",
            run_body=None,
            run_http_status=None,
            blocker=_blocker(
                stage="full_run",
                reason_code=RUN_TIMEOUT_REASON,
                http_status=None,
            ),
            run_timeout_snapshot=run_attempt.timeout_snapshot,
            run_elapsed_seconds=run_attempt.elapsed_seconds,
            run_attempted_on_timeout=True,
            upload_profile=upload_timing.to_summary(),
        )

    run_response = run_attempt.response
    if run_response is None:
        raise RuntimeError("run attempt completed without a response")
    run_body = run_response.json()
    if run_response.status_code != 202:
        _write_checkpoint(
            options=options,
            stage="run_start",
            files=files,
            decisions=decisions,
            uploaded_document_ids=uploaded_document_ids,
            upload_timing=upload_timing,
            run_body=run_body,
            run_http_status=run_response.status_code,
            blocker=_blocker(
                stage="run_start",
                reason_code=_response_reason(run_response),
                http_status=run_response.status_code,
            ),
        )
        return _summary(
            files=files,
            decisions=decisions,
            uploaded_document_ids=uploaded_document_ids,
            status="blocked",
            run_body=run_body,
            run_http_status=run_response.status_code,
            blocker=_blocker(
                stage="run_start",
                reason_code=_response_reason(run_response),
                http_status=run_response.status_code,
            ),
            upload_profile=upload_timing.to_summary(),
        )

    block_reason = _run_block_reason(run_body)
    if block_reason is not None or str(run_body.get("status")) != "SUCCEEDED":
        _write_checkpoint(
            options=options,
            stage="full_run",
            files=files,
            decisions=decisions,
            uploaded_document_ids=uploaded_document_ids,
            upload_timing=upload_timing,
            run_body=run_body,
            run_http_status=run_response.status_code,
            blocker=_blocker(
                stage="full_run",
                reason_code=block_reason or _safe_status_reason(run_body),
                http_status=run_response.status_code,
            ),
        )
        return _summary(
            files=files,
            decisions=decisions,
            uploaded_document_ids=uploaded_document_ids,
            status="blocked",
            run_body=run_body,
            run_http_status=run_response.status_code,
            blocker=_blocker(
                stage="full_run",
                reason_code=block_reason or _safe_status_reason(run_body),
                http_status=run_response.status_code,
            ),
            upload_profile=upload_timing.to_summary(),
        )

    _write_checkpoint(
        options=options,
        stage="full_run",
        files=files,
        decisions=decisions,
        uploaded_document_ids=uploaded_document_ids,
        upload_timing=upload_timing,
        status="succeeded",
        run_body=run_body,
        run_http_status=run_response.status_code,
    )
    return _summary(
        files=files,
        decisions=decisions,
        uploaded_document_ids=uploaded_document_ids,
        status="succeeded",
        run_body=run_body,
        run_http_status=run_response.status_code,
        blocker=None,
        upload_profile=upload_timing.to_summary(),
    )


def _inventory_files(root: Path) -> list[Path]:
    return sorted((path for path in root.rglob("*") if path.is_file()), key=_sort_key(root))


def _sort_key(root: Path) -> Any:
    def key(path: Path) -> str:
        return path.relative_to(root).as_posix().lower()

    return key


def _decision_for_file(*, path: Path, local_stt_model_configured: bool) -> _FileDecision:
    extension = path.suffix.lower() or ".unknown"
    if extension in MEDIA_EXTENSIONS:
        reason = (
            "media_public_upload_unsupported"
            if local_stt_model_configured
            else ("media_transcription_unavailable")
        )
        return _FileDecision(
            path=path,
            extension=extension,
            upload_status="deferred",
            reason_code=reason,
        )

    size_bytes = _safe_size(path)
    header = _read_header(path)
    capability = capability_for_document(
        filename=f"document{extension}",
        file_size_bytes=size_bytes,
        data=header,
    )
    reason = _capability_reason_code(
        support_status=capability.support_status,
        triage_status=capability.triage_status,
        reason_codes=capability.reason_codes,
    )
    if extension in PUBLIC_UPLOAD_EXTENSIONS and reason not in {
        "file_too_large",
        "conversion_required",
        "unsupported_format",
    }:
        return _FileDecision(
            path=path,
            extension=extension,
            upload_status="upload_candidate",
            reason_code="upload_candidate",
        )
    return _FileDecision(
        path=path,
        extension=extension,
        upload_status="deferred",
        reason_code=reason,
    )


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _read_header(path: Path) -> bytes:
    try:
        with path.open("rb") as file:
            return file.read(64)
    except OSError:
        return b""


def _capability_reason_code(
    *,
    support_status: DocumentSupportStatus,
    triage_status: DocumentTriageStatus,
    reason_codes: list[str],
) -> str:
    if support_status == DocumentSupportStatus.TOO_LARGE:
        return "file_too_large"
    if support_status == DocumentSupportStatus.CONVERSION_REQUIRED:
        return "conversion_required"
    if triage_status == DocumentTriageStatus.OCR_REQUIRED:
        return "ocr_required"
    if support_status == DocumentSupportStatus.UNSUPPORTED:
        return "unsupported_format"
    if reason_codes:
        return sorted(reason_codes)[0]
    return "unsupported_format"


def _read_upload_payload(
    *,
    api_key: str,
    path: Path,
    upload_index: int,
) -> _UploadPayload:
    data = path.read_bytes()
    extension = path.suffix.lower() or ".bin"
    synthetic_filename = f"document-{upload_index:05d}{extension}"
    return _UploadPayload(
        extension=extension,
        headers={**_headers(api_key), "Content-Type": UPLOAD_CONTENT_TYPE},
        params={
            "filename": synthetic_filename,
            "doc_type": "DATA_ROOM_FILE",
            "source_system": "real-example-production-harness",
        },
        content=data,
    )


def _post_upload_payload(
    *,
    client: _ApiClientLike,
    deal_id: str,
    payload: _UploadPayload,
) -> _ResponseLike:
    return _client_post(
        client,
        f"/v1/deals/{deal_id}/documents/upload",
        headers=payload.headers,
        params=payload.params,
        content=payload.content,
    )


def _client_post(client: _ApiClientLike, url: str, **kwargs: Any) -> _ResponseLike:
    with _suppress_output():
        return client.post(url, **kwargs)


def _internal_upload_phase_summary(client: _ApiClientLike) -> dict[str, Any] | None:
    app = getattr(client, "app", None)
    app_state = getattr(app, "state", None)
    recorder = getattr(app_state, "upload_ingestion_phase_recorder", None)
    try:
        to_summary = getattr(recorder, "to_summary", None)
    except Exception as error:
        logger.warning(
            "Private upload phase recorder lookup failed: exception_type=%s",
            type(error).__name__,
        )
        return None
    if not callable(to_summary):
        return None
    try:
        summary = to_summary()
    except Exception as error:
        logger.warning(
            "Private upload phase recorder summary failed: exception_type=%s",
            type(error).__name__,
        )
        return None
    return summary if isinstance(summary, dict) else None


def _safe_internal_upload_api_summary(summary: object) -> dict[str, Any] | None:
    if not isinstance(summary, dict) or summary.get("enabled") is not True:
        return None

    phase_counts = _safe_phase_bucket_counts(summary.get("phase_counts_by_elapsed_bucket"))
    if not phase_counts:
        return None

    safe_summary: dict[str, Any] = {
        "enabled": True,
        "phase_counts_by_elapsed_bucket": phase_counts,
    }
    phase_totals = _safe_phase_bucket_values(summary.get("phase_total_elapsed_bucket"))
    if phase_totals:
        safe_summary["phase_total_elapsed_bucket"] = phase_totals
    phase_max = _safe_phase_bucket_values(summary.get("phase_max_elapsed_bucket"))
    if phase_max:
        safe_summary["phase_max_elapsed_bucket"] = phase_max
    slowest_phase = summary.get("observable_slowest_phase")
    if isinstance(slowest_phase, str) and slowest_phase in INTERNAL_UPLOAD_API_PHASES:
        safe_summary["observable_slowest_phase"] = slowest_phase
    parser_diagnostics = _safe_parser_diagnostics(summary.get("parser_diagnostics"))
    if parser_diagnostics:
        safe_summary["parser_diagnostics"] = parser_diagnostics
    return safe_summary


def _safe_phase_bucket_counts(value: object) -> dict[str, dict[str, int]]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, dict[str, int]] = {}
    for phase, bucket_counts in value.items():
        if not isinstance(phase, str) or phase not in INTERNAL_UPLOAD_API_PHASES:
            continue
        if not isinstance(bucket_counts, dict):
            continue
        safe_counts = {
            bucket: count
            for bucket, count in bucket_counts.items()
            if isinstance(bucket, str)
            and bucket in ELAPSED_BUCKETS
            and isinstance(count, int)
            and count >= 0
        }
        if safe_counts:
            safe[phase] = dict(sorted(safe_counts.items()))
    return dict(sorted(safe.items()))


def _safe_phase_bucket_values(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    safe = {
        phase: bucket
        for phase, bucket in value.items()
        if isinstance(phase, str)
        and phase in INTERNAL_UPLOAD_API_PHASES
        and isinstance(bucket, str)
        and bucket in ELAPSED_BUCKETS
    }
    return dict(sorted(safe.items()))


def _safe_parser_diagnostics(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    counts_by_extension = _safe_count_values(
        value.get("counts_by_extension"),
        allowed_keys=PARSER_DIAGNOSTIC_EXTENSIONS,
    )
    counts_by_outcome = _safe_count_values(
        value.get("counts_by_outcome"),
        allowed_keys=PARSER_DIAGNOSTIC_OUTCOMES,
    )
    elapsed_by_extension = _safe_nested_bucket_counts(
        value.get("parse_elapsed_by_extension"),
        allowed_outer_keys=PARSER_DIAGNOSTIC_EXTENSIONS,
    )
    elapsed_by_outcome = _safe_nested_bucket_counts(
        value.get("parse_elapsed_by_outcome"),
        allowed_outer_keys=PARSER_DIAGNOSTIC_OUTCOMES,
    )
    if not (counts_by_extension or counts_by_outcome or elapsed_by_extension or elapsed_by_outcome):
        return {}

    safe: dict[str, Any] = {}
    if counts_by_extension:
        safe["counts_by_extension"] = counts_by_extension
    if counts_by_outcome:
        safe["counts_by_outcome"] = counts_by_outcome
    if elapsed_by_extension:
        safe["parse_elapsed_by_extension"] = elapsed_by_extension
    if elapsed_by_outcome:
        safe["parse_elapsed_by_outcome"] = elapsed_by_outcome

    pdf_diagnostics = _safe_pdf_diagnostics(value.get("pdf_diagnostics"))
    if pdf_diagnostics:
        safe["pdf_diagnostics"] = pdf_diagnostics

    total_by_extension = _safe_bucket_values_for_keys(
        value.get("parse_total_elapsed_bucket_by_extension"),
        allowed_keys=PARSER_DIAGNOSTIC_EXTENSIONS,
    )
    if total_by_extension:
        safe["parse_total_elapsed_bucket_by_extension"] = total_by_extension
    max_by_extension = _safe_bucket_values_for_keys(
        value.get("parse_max_elapsed_bucket_by_extension"),
        allowed_keys=PARSER_DIAGNOSTIC_EXTENSIONS,
    )
    if max_by_extension:
        safe["parse_max_elapsed_bucket_by_extension"] = max_by_extension
    slowest_extension = value.get("observable_slowest_extension")
    if isinstance(slowest_extension, str) and slowest_extension in PARSER_DIAGNOSTIC_EXTENSIONS:
        safe["observable_slowest_extension"] = slowest_extension
    return safe


def _safe_pdf_diagnostics(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    counts_by_outcome_reason = _safe_count_values(
        value.get("counts_by_outcome_reason"),
        allowed_keys=PDF_DIAGNOSTIC_OUTCOME_REASONS,
    )
    elapsed_by_outcome_reason = _safe_nested_bucket_counts(
        value.get("parse_elapsed_by_outcome_reason"),
        allowed_outer_keys=PDF_DIAGNOSTIC_OUTCOME_REASONS,
    )
    if not (counts_by_outcome_reason or elapsed_by_outcome_reason):
        return {}
    safe: dict[str, Any] = {}
    if counts_by_outcome_reason:
        safe["counts_by_outcome_reason"] = counts_by_outcome_reason
    if elapsed_by_outcome_reason:
        safe["parse_elapsed_by_outcome_reason"] = elapsed_by_outcome_reason
    total_by_outcome_reason = _safe_bucket_values_for_keys(
        value.get("parse_total_elapsed_bucket_by_outcome_reason"),
        allowed_keys=PDF_DIAGNOSTIC_OUTCOME_REASONS,
    )
    if total_by_outcome_reason:
        safe["parse_total_elapsed_bucket_by_outcome_reason"] = total_by_outcome_reason
    max_by_outcome_reason = _safe_bucket_values_for_keys(
        value.get("parse_max_elapsed_bucket_by_outcome_reason"),
        allowed_keys=PDF_DIAGNOSTIC_OUTCOME_REASONS,
    )
    if max_by_outcome_reason:
        safe["parse_max_elapsed_bucket_by_outcome_reason"] = max_by_outcome_reason
    subphase_elapsed_by_outcome_reason = _safe_pdf_subphase_nested_bucket_counts(
        value.get("parse_subphase_elapsed_by_outcome_reason")
    )
    if subphase_elapsed_by_outcome_reason:
        safe["parse_subphase_elapsed_by_outcome_reason"] = subphase_elapsed_by_outcome_reason
    subphase_total_by_outcome_reason = _safe_pdf_subphase_bucket_values(
        value.get("parse_subphase_total_elapsed_bucket_by_outcome_reason")
    )
    if subphase_total_by_outcome_reason:
        safe["parse_subphase_total_elapsed_bucket_by_outcome_reason"] = (
            subphase_total_by_outcome_reason
        )
    subphase_max_by_outcome_reason = _safe_pdf_subphase_bucket_values(
        value.get("parse_subphase_max_elapsed_bucket_by_outcome_reason")
    )
    if subphase_max_by_outcome_reason:
        safe["parse_subphase_max_elapsed_bucket_by_outcome_reason"] = subphase_max_by_outcome_reason
    slowest_subphase_by_outcome_reason = _safe_pdf_slowest_subphase_by_outcome_reason(
        value.get("observable_slowest_subphase_by_outcome_reason")
    )
    if slowest_subphase_by_outcome_reason:
        safe["observable_slowest_subphase_by_outcome_reason"] = slowest_subphase_by_outcome_reason
    slowest_reason = value.get("observable_slowest_outcome_reason")
    if isinstance(slowest_reason, str) and slowest_reason in PDF_DIAGNOSTIC_OUTCOME_REASONS:
        safe["observable_slowest_outcome_reason"] = slowest_reason
    return safe


def _safe_count_values(value: object, *, allowed_keys: frozenset[str]) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    safe = {
        key: count
        for key, count in value.items()
        if isinstance(key, str) and key in allowed_keys and isinstance(count, int) and count >= 0
    }
    return dict(sorted(safe.items()))


def _safe_nested_bucket_counts(
    value: object,
    *,
    allowed_outer_keys: frozenset[str],
) -> dict[str, dict[str, int]]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, dict[str, int]] = {}
    for outer_key, bucket_counts in value.items():
        if not isinstance(outer_key, str) or outer_key not in allowed_outer_keys:
            continue
        if not isinstance(bucket_counts, dict):
            continue
        safe_counts = {
            bucket: count
            for bucket, count in bucket_counts.items()
            if isinstance(bucket, str)
            and bucket in ELAPSED_BUCKETS
            and isinstance(count, int)
            and count >= 0
        }
        if safe_counts:
            safe[outer_key] = dict(sorted(safe_counts.items()))
    return dict(sorted(safe.items()))


def _safe_bucket_values_for_keys(
    value: object,
    *,
    allowed_keys: frozenset[str],
) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    safe = {
        key: bucket
        for key, bucket in value.items()
        if isinstance(key, str)
        and key in allowed_keys
        and isinstance(bucket, str)
        and bucket in ELAPSED_BUCKETS
    }
    return dict(sorted(safe.items()))


def _safe_pdf_subphase_nested_bucket_counts(value: object) -> dict[str, dict[str, dict[str, int]]]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, dict[str, dict[str, int]]] = {}
    for reason, subphase_values in value.items():
        if not isinstance(reason, str) or reason not in PDF_DIAGNOSTIC_OUTCOME_REASONS:
            continue
        if not isinstance(subphase_values, dict):
            continue
        safe_subphases: dict[str, dict[str, int]] = {}
        for subphase, bucket_counts in subphase_values.items():
            if not isinstance(subphase, str) or subphase not in PDF_PARSE_SUBPHASES:
                continue
            if not isinstance(bucket_counts, dict):
                continue
            safe_counts = {
                bucket: count
                for bucket, count in bucket_counts.items()
                if isinstance(bucket, str)
                and bucket in ELAPSED_BUCKETS
                and isinstance(count, int)
                and count >= 0
            }
            if safe_counts:
                safe_subphases[subphase] = dict(sorted(safe_counts.items()))
        if safe_subphases:
            safe[reason] = dict(sorted(safe_subphases.items()))
    return dict(sorted(safe.items()))


def _safe_pdf_subphase_bucket_values(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, dict[str, str]] = {}
    for reason, subphase_values in value.items():
        if not isinstance(reason, str) or reason not in PDF_DIAGNOSTIC_OUTCOME_REASONS:
            continue
        if not isinstance(subphase_values, dict):
            continue
        safe_subphases = {
            subphase: bucket
            for subphase, bucket in subphase_values.items()
            if isinstance(subphase, str)
            and subphase in PDF_PARSE_SUBPHASES
            and isinstance(bucket, str)
            and bucket in ELAPSED_BUCKETS
        }
        if safe_subphases:
            safe[reason] = dict(sorted(safe_subphases.items()))
    return dict(sorted(safe.items()))


def _safe_pdf_slowest_subphase_by_outcome_reason(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    safe = {
        reason: subphase
        for reason, subphase in value.items()
        if isinstance(reason, str)
        and reason in PDF_DIAGNOSTIC_OUTCOME_REASONS
        and isinstance(subphase, str)
        and subphase in PDF_PARSE_SUBPHASES
    }
    return dict(sorted(safe.items()))


def _post_run_with_optional_deadline(
    *,
    client: _ApiClientLike,
    url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout_seconds: float | None,
    poll_interval_seconds: float,
) -> _RunPostAttempt:
    attempt = _call_response_with_optional_deadline(
        call=lambda: _client_post(client, url, headers=_headers(api_key), json=payload),
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    if attempt.timed_out:
        return _RunPostAttempt(
            response=None,
            timed_out=True,
            elapsed_seconds=attempt.elapsed_seconds,
            timeout_snapshot=_latest_safe_run_snapshot(),
        )
    return _RunPostAttempt(
        response=_completed_response_or_raise(attempt),
        timed_out=False,
        elapsed_seconds=attempt.elapsed_seconds,
    )


def _call_response_with_optional_deadline(
    *,
    call: Callable[[], _ResponseLike],
    timeout_seconds: float | None,
    poll_interval_seconds: float,
) -> _RunPostAttempt:
    _ = poll_interval_seconds
    started_at = time.monotonic()
    if timeout_seconds is not None and timeout_seconds <= 0:
        return _RunPostAttempt(
            response=None,
            timed_out=True,
            elapsed_seconds=time.monotonic() - started_at,
        )
    response = call()
    elapsed_seconds = time.monotonic() - started_at
    return _RunPostAttempt(
        response=response,
        timed_out=timeout_seconds is not None and elapsed_seconds >= timeout_seconds,
        elapsed_seconds=elapsed_seconds,
    )


def _call_with_optional_deadline(
    *,
    call: Callable[[], T],
    timeout_seconds: float | None,
    poll_interval_seconds: float,
) -> _TimedCallAttempt[T]:
    _ = poll_interval_seconds
    started_at = time.monotonic()
    if timeout_seconds is not None and timeout_seconds <= 0:
        return _TimedCallAttempt(
            result=None,
            timed_out=True,
            elapsed_seconds=time.monotonic() - started_at,
        )
    result = call()
    elapsed_seconds = time.monotonic() - started_at
    return _TimedCallAttempt(
        result=result,
        timed_out=timeout_seconds is not None and elapsed_seconds >= timeout_seconds,
        elapsed_seconds=elapsed_seconds,
    )


def _completed_response_or_raise(attempt: _RunPostAttempt) -> _ResponseLike:
    if attempt.response is None:
        raise RuntimeError("timed response attempt completed without a response")
    return attempt.response


def _completed_call_result_or_raise(attempt: _TimedCallAttempt[T]) -> T:
    if attempt.result is None:
        raise RuntimeError("timed call completed without a result")
    return attempt.result


def _deadline_expired(started_at: float, timeout_seconds: float | None) -> bool:
    if timeout_seconds is None:
        return False
    return time.monotonic() - started_at >= timeout_seconds


def _remaining_timeout_seconds(started_at: float, timeout_seconds: float | None) -> float | None:
    if timeout_seconds is None:
        return None
    return max(0.0, timeout_seconds - (time.monotonic() - started_at))


def _suppress_output() -> contextlib.AbstractContextManager[None]:
    @contextlib.contextmanager
    def suppress() -> Iterator[None]:
        with (
            open(os.devnull, "w", encoding="utf-8") as devnull,
            contextlib.redirect_stdout(devnull),
            contextlib.redirect_stderr(devnull),
        ):
            yield

    return suppress()


def _headers(api_key: str) -> dict[str, str]:
    return {"X-IDIS-API-Key": api_key}


def _write_checkpoint(
    *,
    options: RealExampleFullRunHarnessOptions,
    stage: str,
    files: list[Path],
    decisions: list[_FileDecision],
    uploaded_document_ids: list[str],
    upload_timing: _UploadTimingRecorder,
    status: str = "running",
    run_body: dict[str, Any] | None = None,
    run_http_status: int | None = None,
    blocker: dict[str, Any] | None = None,
    run_timeout_snapshot: dict[str, Any] | None = None,
    run_elapsed_seconds: float | None = None,
    run_attempted_on_timeout: bool = False,
) -> None:
    if options.checkpoint_output_path is None:
        return
    checkpoint = _summary(
        files=files,
        decisions=decisions,
        uploaded_document_ids=uploaded_document_ids,
        status=status,
        run_body=run_body,
        run_http_status=run_http_status,
        blocker=blocker,
        run_timeout_snapshot=run_timeout_snapshot,
        run_elapsed_seconds=run_elapsed_seconds,
        run_attempted_on_timeout=run_attempted_on_timeout,
        upload_profile=upload_timing.to_summary(),
    )
    checkpoint["checkpoint"] = {"stage": stage}
    _write_json_atomic(Path(options.checkpoint_output_path), checkpoint)


def _write_summary_outputs(
    *,
    summary: dict[str, Any],
    output_path: str | Path | None,
    resume_state_output_path: str | Path | None,
) -> None:
    if output_path is not None:
        _write_json_atomic(Path(output_path), summary)
    if resume_state_output_path is not None:
        _write_json_atomic(Path(resume_state_output_path), _resume_state_for_output(summary))


def _write_json_atomic(destination: Path, payload: dict[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_destination = destination.with_name(f"{destination.name}.{os.getpid()}.tmp")
    tmp_destination.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    tmp_destination.replace(destination)


def _load_json_object(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(loaded, dict):
        return loaded
    return _minimal_process_timeout_summary(stage="process")


def _strip_checkpoint_metadata(summary: dict[str, Any]) -> dict[str, Any]:
    stripped = dict(summary)
    stripped.pop("checkpoint", None)
    return stripped


def _process_timeout_summary_from_checkpoint(checkpoint_path: Path) -> dict[str, Any]:
    if not checkpoint_path.exists():
        return _minimal_process_timeout_summary(stage="process")
    checkpoint = _load_json_object(checkpoint_path)
    stage = _checkpoint_stage(checkpoint)
    summary = _strip_checkpoint_metadata(checkpoint)
    summary["status"] = "blocked"
    summary["blocker"] = _blocker(
        stage=stage,
        reason_code=(UPLOAD_THROUGHPUT_LIMIT_REASON if stage == "upload" else RUN_TIMEOUT_REASON),
        http_status=None,
    )
    upload_profile = summary.get("upload_profile")
    if isinstance(upload_profile, dict):
        upload_profile["partial"] = True
    else:
        summary["upload_profile"] = _UploadTimingRecorder(max_upload_concurrency=1).to_summary()
        summary["upload_profile"]["partial"] = True
    summary["resume"] = _resume_summary(
        uploaded_document_count=int(summary.get("uploaded_document_count") or 0),
        selected_document_count=int(summary.get("selected_document_count") or 0),
        run_snapshot=None,
    )
    if stage == "run_start":
        summary["run"] = {
            "attempted": True,
            "status": "TIMEOUT",
            "run_id": None,
            "step_count": 0,
            "completed_step_count": 0,
            "failed_step_count": 0,
            "blocked_step_count": 0,
            "block_reason": RUN_TIMEOUT_REASON,
            "last_completed_step": None,
            "current_step": None,
            "failed_step": None,
            "step_counts_by_status": {},
        }
    return summary


def _checkpoint_stage(checkpoint: dict[str, Any]) -> str:
    checkpoint_meta = checkpoint.get("checkpoint")
    raw_stage = checkpoint_meta.get("stage") if isinstance(checkpoint_meta, dict) else None
    if raw_stage in {"inventory", "deal_create", "upload", "run_start", "full_run"}:
        return str(raw_stage)
    return "process"


def _minimal_process_timeout_summary(*, stage: str) -> dict[str, Any]:
    upload_profile = _UploadTimingRecorder(max_upload_concurrency=1).to_summary()
    upload_profile["partial"] = True
    return {
        "harness": "real_example_production_full_run_private_v1",
        "safe_summary": True,
        "private_run": True,
        "status": "blocked",
        "mode": "FULL",
        "total_files": 0,
        "uploaded_document_count": 0,
        "selected_document_count": 0,
        "skipped_file_count": 0,
        "counts_by_extension": {},
        "counts_by_upload_status": {},
        "counts_by_deferred_reason": {},
        "run": {
            "attempted": False,
            "status": None,
            "step_count": 0,
            "completed_step_count": 0,
            "failed_step_count": 0,
            "blocked_step_count": 0,
            "block_reason": None,
        },
        "upload_profile": upload_profile,
        "resume": _resume_summary(
            uploaded_document_count=0,
            selected_document_count=0,
            run_snapshot=None,
        ),
        "blocker": _blocker(
            stage=stage,
            reason_code=RUN_TIMEOUT_REASON,
            http_status=None,
        ),
    }


def _summary(
    *,
    files: list[Path],
    decisions: list[_FileDecision],
    uploaded_document_ids: list[str],
    status: str,
    run_body: dict[str, Any] | None,
    run_http_status: int | None,
    blocker: dict[str, Any] | None,
    run_timeout_snapshot: dict[str, Any] | None = None,
    run_elapsed_seconds: float | None = None,
    run_attempted_on_timeout: bool = False,
    upload_profile: dict[str, Any] | None = None,
    strict_full_live_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    uploaded_count = sum(1 for decision in decisions if decision.upload_status == "uploaded")
    skipped_count = sum(1 for decision in decisions if decision.upload_status != "uploaded")
    resume = _resume_summary(
        uploaded_document_count=uploaded_count,
        selected_document_count=len(uploaded_document_ids),
        run_snapshot=run_timeout_snapshot,
    )
    summary = {
        "harness": "real_example_production_full_run_private_v1",
        "safe_summary": True,
        "private_run": True,
        "status": status,
        "mode": "FULL",
        "total_files": len(files),
        "uploaded_document_count": uploaded_count,
        "selected_document_count": len(uploaded_document_ids),
        "skipped_file_count": skipped_count,
        "counts_by_extension": _count_extensions(files),
        "counts_by_upload_status": _count_decisions(decisions, "upload_status"),
        "counts_by_deferred_reason": _deferred_reasons(decisions),
        "run": _run_summary(
            run_body,
            http_status=run_http_status,
            timeout_snapshot=run_timeout_snapshot,
            elapsed_seconds=run_elapsed_seconds,
            attempted_on_timeout=run_attempted_on_timeout,
        ),
        "upload_profile": upload_profile
        if upload_profile is not None
        else _UploadTimingRecorder(max_upload_concurrency=1).to_summary(),
        "resume": resume,
        "blocker": blocker,
    }
    if strict_full_live_report is not None:
        summary["strict_full_live"] = strict_full_live_report
    return summary


def _strict_full_live_block_summary(
    *,
    files: list[Path],
    strict_full_live_report: dict[str, Any],
) -> dict[str, Any]:
    return _summary(
        files=files,
        decisions=[],
        uploaded_document_ids=[],
        status="blocked",
        run_body=None,
        run_http_status=None,
        blocker=_blocker(
            stage="strict_full_live_preflight",
            reason_code=STRICT_FULL_LIVE_BLOCKED,
            http_status=None,
        ),
        upload_profile={"enabled": False, "reason_code": STRICT_FULL_LIVE_BLOCKED},
        strict_full_live_report=strict_full_live_report,
    )


def _resume_summary(
    *,
    uploaded_document_count: int,
    selected_document_count: int,
    run_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "supported": False,
        "reason_code": RESUME_UNSUPPORTED_REASON,
        "uploaded_document_count": uploaded_document_count,
        "selected_document_count": selected_document_count,
        "run_id": _optional_string((run_snapshot or {}).get("run_id")),
    }


def _resume_state_for_output(summary: dict[str, Any]) -> dict[str, Any]:
    resume = summary.get("resume")
    if isinstance(resume, dict):
        return dict(resume)
    return {"supported": False, "reason_code": RESUME_UNSUPPORTED_REASON, "run_id": None}


def _count_extensions(files: list[Path]) -> dict[str, int]:
    return dict(sorted(Counter(path.suffix.lower() or ".unknown" for path in files).items()))


def _count_decisions(decisions: list[_FileDecision], field: str) -> dict[str, int]:
    values = [getattr(decision, field) for decision in decisions]
    return dict(sorted(Counter(values).items()))


def _deferred_reasons(decisions: list[_FileDecision]) -> dict[str, int]:
    reasons = [
        decision.reason_code
        for decision in decisions
        if decision.upload_status != "uploaded" and decision.reason_code != "uploaded"
    ]
    return dict(sorted(Counter(reasons).items()))


def _run_summary(
    run_body: dict[str, Any] | None,
    *,
    http_status: int | None,
    timeout_snapshot: dict[str, Any] | None = None,
    elapsed_seconds: float | None = None,
    attempted_on_timeout: bool = False,
) -> dict[str, Any]:
    _ = http_status
    if timeout_snapshot is not None:
        steps = timeout_snapshot.get("steps")
        step_list = steps if isinstance(steps, list) else []
        return {
            "attempted": True,
            "status": _optional_string(timeout_snapshot.get("status")) or "RUNNING",
            "run_id": _optional_string(timeout_snapshot.get("run_id")),
            "step_count": len(step_list),
            "completed_step_count": _step_status_count(step_list, "COMPLETED"),
            "failed_step_count": _step_status_count(step_list, "FAILED"),
            "blocked_step_count": _step_status_count(step_list, "BLOCKED"),
            "block_reason": RUN_TIMEOUT_REASON,
            "last_completed_step": _last_step_with_status(step_list, "COMPLETED"),
            "current_step": _first_step_with_status(step_list, "RUNNING"),
            "failed_step": _first_failed_step(step_list),
            "elapsed_seconds_bucket": _elapsed_seconds_bucket(elapsed_seconds),
            "step_counts_by_status": _step_counts_by_status(step_list),
        }
    if run_body is None:
        if attempted_on_timeout:
            return {
                "attempted": True,
                "status": "TIMEOUT",
                "run_id": None,
                "step_count": 0,
                "completed_step_count": 0,
                "failed_step_count": 0,
                "blocked_step_count": 0,
                "block_reason": RUN_TIMEOUT_REASON,
                "last_completed_step": None,
                "current_step": None,
                "failed_step": None,
                "elapsed_seconds_bucket": _elapsed_seconds_bucket(elapsed_seconds),
                "step_counts_by_status": {},
            }
        summary: dict[str, Any] = {
            "attempted": False,
            "status": None,
            "step_count": 0,
            "completed_step_count": 0,
            "failed_step_count": 0,
            "blocked_step_count": 0,
            "block_reason": None,
        }
        if elapsed_seconds is not None:
            summary["elapsed_seconds_bucket"] = _elapsed_seconds_bucket(elapsed_seconds)
        return summary
    steps = run_body.get("steps")
    step_list = steps if isinstance(steps, list) else []
    return {
        "attempted": True,
        "status": _optional_string(run_body.get("status")),
        "step_count": len(step_list),
        "completed_step_count": _step_status_count(step_list, "COMPLETED"),
        "failed_step_count": _step_status_count(step_list, "FAILED"),
        "blocked_step_count": _step_status_count(step_list, "BLOCKED"),
        "block_reason": _run_block_reason(run_body),
    }


def _step_counts_by_status(steps: list[Any]) -> dict[str, int]:
    statuses = [
        str(step.get("status")).upper()
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("status"), str)
    ]
    return dict(sorted(Counter(statuses).items()))


def _last_step_with_status(steps: list[Any], status: str) -> str | None:
    matching = [
        _optional_string(step.get("step_name"))
        for step in steps
        if isinstance(step, dict) and str(step.get("status")).upper() == status
    ]
    return next((step for step in reversed(matching) if step is not None), None)


def _first_step_with_status(steps: list[Any], status: str) -> str | None:
    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("status")).upper() == status:
            return _optional_string(step.get("step_name"))
    return None


def _first_failed_step(steps: list[Any]) -> str | None:
    for status in ("FAILED", "BLOCKED"):
        step_name = _first_step_with_status(steps, status)
        if step_name is not None:
            return step_name
    return None


def _elapsed_seconds_bucket(elapsed_seconds: float | None) -> str | None:
    if elapsed_seconds is None:
        return None
    if elapsed_seconds < 1:
        return "under_1s"
    if elapsed_seconds < 5:
        return "1_to_5s"
    if elapsed_seconds < 30:
        return "5_to_30s"
    if elapsed_seconds < 60:
        return "30_to_60s"
    if elapsed_seconds < 300:
        return "60_to_300s"
    return "over_300s"


def _elapsed_bucket_counts(elapsed_seconds_values: list[float]) -> dict[str, int]:
    buckets = [
        bucket
        for bucket in (_elapsed_seconds_bucket(value) for value in elapsed_seconds_values)
        if bucket is not None
    ]
    return dict(sorted(Counter(buckets).items()))


def _rate_per_minute_bucket(count: int, elapsed_seconds: float) -> str | None:
    if count <= 0 or elapsed_seconds <= 0:
        return None
    rate = count / (elapsed_seconds / 60)
    if rate < 30:
        return "under_30_per_minute"
    if rate < 60:
        return "30_to_60_per_minute"
    if rate < 120:
        return "60_to_120_per_minute"
    if rate < 240:
        return "120_to_240_per_minute"
    return "over_240_per_minute"


def _step_status_count(steps: list[Any], status: str) -> int:
    return sum(
        1 for step in steps if isinstance(step, dict) and str(step.get("status")).upper() == status
    )


def _run_block_reason(run_body: dict[str, Any]) -> str | None:
    block_reason = run_body.get("block_reason")
    if isinstance(block_reason, str) and block_reason.strip():
        return block_reason
    steps = run_body.get("steps")
    if not isinstance(steps, list):
        return None
    for step in steps:
        if not isinstance(step, dict):
            continue
        error = step.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, str) and code.strip():
                return code
    return None


def _latest_safe_run_snapshot() -> dict[str, Any] | None:
    """Return the latest no-DB run/step state without result summaries."""
    try:
        from idis.persistence.repositories.run_steps import get_run_steps_repository
        from idis.persistence.repositories.runs import _in_memory_runs_store
    except ImportError:
        return None

    runs = [
        run
        for run in _in_memory_runs_store.values()
        if run.get("tenant_id") == DEFAULT_TENANT_ID and run.get("mode") == "FULL"
    ]
    if not runs:
        return None

    latest = max(runs, key=lambda run: str(run.get("created_at") or ""))
    run_id = _optional_string(latest.get("run_id"))
    if run_id is None:
        return None

    steps_repo = get_run_steps_repository(None, DEFAULT_TENANT_ID)
    steps = []
    for step in steps_repo.get_by_run_id(run_id):
        step_name = step.step_name.value if hasattr(step.step_name, "value") else step.step_name
        step_status = step.status.value if hasattr(step.status, "value") else step.status
        steps.append(
            {
                "step_name": step_name,
                "status": step_status,
                "error": {"code": step.error_code} if step.error_code else None,
            }
        )
    return {
        "run_id": run_id,
        "status": _optional_string(latest.get("status")),
        "block_reason": _optional_string(latest.get("block_reason")),
        "steps": steps,
    }


def _blocker(*, stage: str, reason_code: str, http_status: int | None) -> dict[str, Any]:
    return {"stage": stage, "reason_code": reason_code, "http_status": http_status}


def _response_reason(response: _ResponseLike) -> str:
    body = response.json()
    code = body.get("code") or body.get("reason_code") or body.get("block_reason")
    if isinstance(code, str) and code.strip():
        return code
    return f"http_{response.status_code}"


def _safe_status_reason(body: dict[str, Any]) -> str:
    status = body.get("status")
    if isinstance(status, str) and status.strip():
        return f"RUN_{status.upper()}"
    return "RUN_BLOCKED"


def _safe_required_string(body: dict[str, Any], key: str, *, default: str) -> str:
    value = body.get(key)
    return value if isinstance(value, str) and value.strip() else default


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


@contextlib.contextmanager
def _default_api_client(options: RealExampleFullRunHarnessOptions) -> Iterator[_ApiClientLike]:
    from fastapi.testclient import TestClient

    from idis.api.main import create_app
    from idis.audit.sink import InMemoryAuditSink
    from idis.idempotency.store import SqliteIdempotencyStore
    from idis.services.ingestion.service import UploadIngestionPhaseRecorder

    previous_api_keys = os.environ.get(IDIS_API_KEYS_ENV)
    previous_object_store = os.environ.get("IDIS_OBJECT_STORE_BASE_DIR")
    with tempfile.TemporaryDirectory(prefix="idis_slice42_objects_") as object_store_dir:
        os.environ[IDIS_API_KEYS_ENV] = json.dumps(
            {
                options.api_key: {
                    "tenant_id": DEFAULT_TENANT_ID,
                    "actor_id": DEFAULT_ACTOR_ID,
                    "name": "Slice 42 Private Harness",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST", "ADMIN"],
                }
            }
        )
        os.environ["IDIS_OBJECT_STORE_BASE_DIR"] = object_store_dir
        try:
            app = create_app(
                audit_sink=InMemoryAuditSink(),
                idempotency_store=SqliteIdempotencyStore(in_memory=True),
                service_region="me-south-1",
            )
            app.state.upload_ingestion_phase_recorder = UploadIngestionPhaseRecorder()
            yield TestClient(app, raise_server_exceptions=False)
        finally:
            _restore_env(IDIS_API_KEYS_ENV, previous_api_keys)
            _restore_env("IDIS_OBJECT_STORE_BASE_DIR", previous_object_store)


def _restore_env(name: str, previous: str | None) -> None:
    if previous is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = previous


__all__ = [
    "RealExampleFullRunHarnessOptions",
    "run_real_example_full_run_harness",
]

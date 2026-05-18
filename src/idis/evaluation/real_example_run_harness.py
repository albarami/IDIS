"""Private production-style real_example upload-to-FULL-run harness."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar, cast

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.models.document_classification import DocumentSupportStatus, DocumentTriageStatus
from idis.services.documents.parser_capabilities import capability_for_document

PUBLIC_UPLOAD_EXTENSIONS = frozenset({".pdf", ".xlsx", ".docx", ".pptx"})
MEDIA_EXTENSIONS = frozenset({".mp4"})
UPLOAD_CONTENT_TYPE = "application/octet-stream"
DEFAULT_API_KEY = "slice42-private-real-example-harness-key"
DEFAULT_TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEFAULT_ACTOR_ID = "slice42-private-harness"
RUN_TIMEOUT_REASON = "RUN_TIMEOUT"
RESUME_UNSUPPORTED_REASON = "RESUME_UNSUPPORTED"
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
    run_timeout_seconds: float | None = None
    run_poll_interval_seconds: float = 1.0
    resume_state_output_path: str | Path | None = None


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

    if options.api_client is not None:
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


def _validate_options(options: RealExampleFullRunHarnessOptions) -> None:
    if not options.private_run:
        raise ValueError("real_example run harness requires private_run=True")
    if not options.safe_summary:
        raise ValueError("real_example run harness requires safe_summary=True")
    if options.max_upload_files is not None and options.max_upload_files <= 0:
        raise ValueError("max_upload_files must be positive when provided")
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
        )
    files = _completed_call_result_or_raise(inventory_attempt)
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
        )

    deal_id = _safe_required_string(deal_response.json(), "deal_id", default="deal")
    decisions: list[_FileDecision] = []
    uploaded_document_ids: list[str] = []
    upload_index = 0

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

        def upload_current_file(
            path: Path = upload_path,
            index: int = current_upload_index,
        ) -> _ResponseLike:
            return _upload_file(
                client=client,
                api_key=options.api_key,
                deal_id=deal_id,
                path=path,
                upload_index=index,
            )

        try:
            upload_attempt = _call_response_with_optional_deadline(
                call=upload_current_file,
                timeout_seconds=_remaining_timeout_seconds(
                    harness_started_at,
                    options.run_timeout_seconds,
                ),
                poll_interval_seconds=options.run_poll_interval_seconds,
            )
        except OSError:
            decisions.append(
                _FileDecision(
                    path=file_path,
                    extension=decision.extension,
                    upload_status="failed",
                    reason_code="upload_read_failed",
                )
            )
            continue
        if upload_attempt.timed_out:
            return _summary(
                files=files,
                decisions=decisions,
                uploaded_document_ids=uploaded_document_ids,
                status="blocked",
                run_body=None,
                run_http_status=None,
                blocker=_blocker(
                    stage="upload",
                    reason_code=RUN_TIMEOUT_REASON,
                    http_status=None,
                ),
                run_elapsed_seconds=time.monotonic() - harness_started_at,
            )

        upload_response = _completed_response_or_raise(upload_attempt)
        if upload_response.status_code != 201:
            decisions.append(
                _FileDecision(
                    path=file_path,
                    extension=decision.extension,
                    upload_status="failed",
                    reason_code=_response_reason(upload_response),
                )
            )
            continue

        body = upload_response.json()
        parse_status = body.get("parse_status")
        if parse_status != "PARSED":
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
            continue
        document_id = body.get("document_id")
        if not isinstance(document_id, str) or not document_id.strip():
            decisions.append(
                _FileDecision(
                    path=file_path,
                    extension=decision.extension,
                    upload_status="failed",
                    reason_code="missing_document_id",
                )
            )
            continue
        uploaded_document_ids.append(document_id)
        decisions.append(
            _FileDecision(
                path=file_path,
                extension=decision.extension,
                upload_status="uploaded",
                reason_code="uploaded",
            )
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
                    reason_code=RUN_TIMEOUT_REASON,
                    http_status=None,
                ),
                run_elapsed_seconds=time.monotonic() - harness_started_at,
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
        )
    run_attempt = _post_run_with_optional_deadline(
        client=client,
        url=f"/v1/deals/{deal_id}/runs",
        api_key=options.api_key,
        payload=run_payload,
        timeout_seconds=remaining_timeout_seconds,
        poll_interval_seconds=options.run_poll_interval_seconds,
    )
    if run_attempt.timed_out:
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
        )

    run_response = run_attempt.response
    if run_response is None:
        raise RuntimeError("run attempt completed without a response")
    run_body = run_response.json()
    if run_response.status_code != 202:
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
        )

    block_reason = _run_block_reason(run_body)
    if block_reason is not None or str(run_body.get("status")) != "SUCCEEDED":
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
        )

    return _summary(
        files=files,
        decisions=decisions,
        uploaded_document_ids=uploaded_document_ids,
        status="succeeded",
        run_body=run_body,
        run_http_status=run_response.status_code,
        blocker=None,
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


def _upload_file(
    *,
    client: _ApiClientLike,
    api_key: str,
    deal_id: str,
    path: Path,
    upload_index: int,
) -> _ResponseLike:
    data = path.read_bytes()
    extension = path.suffix.lower() or ".bin"
    synthetic_filename = f"document-{upload_index:05d}{extension}"
    return _client_post(
        client,
        f"/v1/deals/{deal_id}/documents/upload",
        headers={**_headers(api_key), "Content-Type": UPLOAD_CONTENT_TYPE},
        params={
            "filename": synthetic_filename,
            "doc_type": "DATA_ROOM_FILE",
            "sha256": hashlib.sha256(data).hexdigest(),
            "source_system": "real-example-production-harness",
        },
        content=data,
    )


def _client_post(client: _ApiClientLike, url: str, **kwargs: Any) -> _ResponseLike:
    with _suppress_output():
        return client.post(url, **kwargs)


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
    attempt = _call_with_optional_deadline(
        call=call,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    return _RunPostAttempt(
        response=attempt.result,
        timed_out=attempt.timed_out,
        elapsed_seconds=attempt.elapsed_seconds,
    )


def _call_with_optional_deadline(
    *,
    call: Callable[[], T],
    timeout_seconds: float | None,
    poll_interval_seconds: float,
) -> _TimedCallAttempt[T]:
    started_at = time.monotonic()
    if timeout_seconds is None:
        immediate_result = call()
        return _TimedCallAttempt(
            result=immediate_result,
            timed_out=False,
            elapsed_seconds=time.monotonic() - started_at,
        )

    completed = threading.Event()
    result_box: dict[str, Any] = {}

    def post_run() -> None:
        try:
            result_box["response"] = call()
        except BaseException as exc:  # pragma: no cover - re-raised in caller
            result_box["exception"] = exc
        finally:
            completed.set()

    thread = threading.Thread(target=post_run, name="idis-real-example-full-run", daemon=True)
    thread.start()
    deadline = started_at + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if completed.wait(min(poll_interval_seconds, remaining)):
            break

    elapsed_seconds = time.monotonic() - started_at
    if not completed.is_set():
        return _TimedCallAttempt(
            result=None,
            timed_out=True,
            elapsed_seconds=elapsed_seconds,
        )

    if "exception" in result_box:
        exc = result_box["exception"]
        if isinstance(exc, BaseException):
            raise exc
        raise RuntimeError("run post failed with a non-exception error")
    completed_result = cast(T | None, result_box.get("response"))
    if completed_result is None:
        raise RuntimeError("timed call completed without a result")
    return _TimedCallAttempt(
        result=completed_result,
        timed_out=False,
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
) -> dict[str, Any]:
    uploaded_count = sum(1 for decision in decisions if decision.upload_status == "uploaded")
    skipped_count = sum(1 for decision in decisions if decision.upload_status != "uploaded")
    resume = _resume_summary(
        uploaded_document_count=uploaded_count,
        selected_document_count=len(uploaded_document_ids),
        run_snapshot=run_timeout_snapshot,
    )
    return {
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
        "resume": resume,
        "blocker": blocker,
    }


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

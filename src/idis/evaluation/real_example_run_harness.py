"""Private production-style real_example upload-to-FULL-run harness."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.models.document_classification import DocumentSupportStatus, DocumentTriageStatus
from idis.services.documents.parser_capabilities import capability_for_document

PUBLIC_UPLOAD_EXTENSIONS = frozenset({".pdf", ".xlsx", ".docx", ".pptx"})
MEDIA_EXTENSIONS = frozenset({".mp4"})
UPLOAD_CONTENT_TYPE = "application/octet-stream"
DEFAULT_API_KEY = "slice42-private-real-example-harness-key"
DEFAULT_TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEFAULT_ACTOR_ID = "slice42-private-harness"


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


@dataclass(frozen=True, slots=True)
class _FileDecision:
    path: Path
    extension: str
    upload_status: str
    reason_code: str


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
    return summary


def _validate_options(options: RealExampleFullRunHarnessOptions) -> None:
    if not options.private_run:
        raise ValueError("real_example run harness requires private_run=True")
    if not options.safe_summary:
        raise ValueError("real_example run harness requires safe_summary=True")
    if options.max_upload_files is not None and options.max_upload_files <= 0:
        raise ValueError("max_upload_files must be positive when provided")


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
    files = _inventory_files(root)
    deal_response = _client_post(
        client,
        "/v1/deals",
        headers=_headers(options.api_key),
        json={"name": options.deal_name, "company_name": options.company_name},
    )
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
        decision = _decision_for_file(
            path=file_path,
            local_stt_model_configured=options.local_stt_model_configured,
        )
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
        try:
            upload_response = _upload_file(
                client=client,
                api_key=options.api_key,
                deal_id=deal_id,
                path=file_path,
                upload_index=upload_index,
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

    run_response = _client_post(
        client,
        f"/v1/deals/{deal_id}/runs",
        headers=_headers(options.api_key),
        json={
            "mode": "FULL",
            "source": {
                "type": "deal_documents",
                "document_ids": uploaded_document_ids,
            },
        },
    )
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
) -> dict[str, Any]:
    uploaded_count = sum(1 for decision in decisions if decision.upload_status == "uploaded")
    skipped_count = sum(1 for decision in decisions if decision.upload_status != "uploaded")
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
        "run": _run_summary(run_body, http_status=run_http_status),
        "blocker": blocker,
    }


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


def _run_summary(run_body: dict[str, Any] | None, *, http_status: int | None) -> dict[str, Any]:
    _ = http_status
    if run_body is None:
        return {
            "attempted": False,
            "status": None,
            "step_count": 0,
            "completed_step_count": 0,
            "failed_step_count": 0,
            "blocked_step_count": 0,
            "block_reason": None,
        }
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

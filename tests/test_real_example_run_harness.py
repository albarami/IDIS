"""Slice 42 tests for the private production-style real_example run harness."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import idis.evaluation.real_example_run_harness as harness_module
from idis.evaluation.real_example_run_harness import (
    DEFAULT_TENANT_ID,
    RealExampleFullRunHarnessOptions,
    run_real_example_full_run_harness,
    run_real_example_full_run_harness_process,
)
from idis.models.run_step import STEP_ORDER, RunStep, StepName, StepStatus
from idis.persistence.repositories.run_steps import (
    clear_run_steps_store,
    get_run_steps_repository,
)
from idis.persistence.repositories.runs import clear_in_memory_runs_store, get_runs_repository


@pytest.fixture(autouse=True)
def clear_in_memory_run_state() -> Any:
    clear_in_memory_runs_store()
    clear_run_steps_store()
    yield
    clear_in_memory_runs_store()
    clear_run_steps_store()


class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeApiClient:
    def __init__(
        self,
        *,
        run_response: dict[str, Any] | None = None,
        upload_statuses: list[tuple[int, dict[str, Any]]] | None = None,
    ) -> None:
        self.uploads: list[dict[str, Any]] = []
        self.run_requests: list[dict[str, Any]] = []
        self.created_deals = 0
        self._run_response = run_response or {"status": "SUCCEEDED", "steps": []}
        self._upload_statuses = list(upload_statuses or [])

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        content: bytes | None = None,
        json: dict[str, Any] | None = None,
    ) -> _FakeResponse:
        if url == "/v1/deals":
            self.created_deals += 1
            return _FakeResponse(201, {"deal_id": "deal-private-1"})
        if url == "/v1/deals/deal-private-1/documents/upload":
            self.uploads.append(
                {
                    "headers": headers or {},
                    "params": params or {},
                    "content": content or b"",
                }
            )
            if self._upload_statuses:
                status_code, body = self._upload_statuses.pop(0)
                return _FakeResponse(status_code, body)
            document_number = len(self.uploads)
            return _FakeResponse(
                201,
                {
                    "document_id": f"document-{document_number}",
                    "doc_id": f"artifact-{document_number}",
                    "parse_status": "PARSED",
                },
            )
        if url == "/v1/deals/deal-private-1/runs":
            self.run_requests.append(json or {})
            return _FakeResponse(202, self._run_response)
        raise AssertionError(f"unexpected URL: {url}")


class _SlowRunApiClient(_FakeApiClient):
    def __init__(self, *, delay_seconds: float = 0.2, seed_run_snapshot: bool = True) -> None:
        super().__init__(run_response={"status": "SUCCEEDED", "steps": []})
        self.delay_seconds = delay_seconds
        self.seed_run_snapshot = seed_run_snapshot

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        content: bytes | None = None,
        json: dict[str, Any] | None = None,
    ) -> _FakeResponse:
        if url == "/v1/deals/deal-private-1/runs":
            self.run_requests.append(json or {})
            if self.seed_run_snapshot:
                _seed_running_run_with_safe_steps()
            print("PRIVATE_API_DIAGNOSTIC_SHOULD_BE_SUPPRESSED")
            time.sleep(self.delay_seconds)
            return _FakeResponse(202, self._run_response)
        return super().post(
            url,
            headers=headers,
            params=params,
            content=content,
            json=json,
        )


class _SlowUploadApiClient(_FakeApiClient):
    def __init__(self, *, delay_seconds: float = 0.05) -> None:
        super().__init__()
        self.delay_seconds = delay_seconds

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        content: bytes | None = None,
        json: dict[str, Any] | None = None,
    ) -> _FakeResponse:
        if url == "/v1/deals/deal-private-1/documents/upload":
            print("PRIVATE_UPLOAD_DIAGNOSTIC_SHOULD_BE_SUPPRESSED")
            time.sleep(self.delay_seconds)
        return super().post(
            url,
            headers=headers,
            params=params,
            content=content,
            json=json,
        )


def _seed_running_run_with_safe_steps() -> None:
    run_id = "00000000-0000-4000-8000-000000000044"
    runs_repo = get_runs_repository(None, DEFAULT_TENANT_ID)
    runs_repo.create(
        run_id=run_id,
        deal_id="deal-private-1",
        mode="FULL",
        source={"type": "deal_documents", "document_ids": ["document-1"]},
    )
    runs_repo.try_mark_running(run_id)
    steps_repo = get_run_steps_repository(None, DEFAULT_TENANT_ID)
    steps_repo.create(
        RunStep(
            step_id="00000000-0000-4000-8000-000000000101",
            run_id=run_id,
            tenant_id=DEFAULT_TENANT_ID,
            step_name=StepName.INGEST_CHECK,
            step_order=STEP_ORDER[StepName.INGEST_CHECK],
            status=StepStatus.COMPLETED,
            started_at="2026-01-01T00:00:00Z",
            finished_at="2026-01-01T00:00:01Z",
            result_summary={"document_count": 1, "private_path": "SECRET_PATH"},
        )
    )
    steps_repo.create(
        RunStep(
            step_id="00000000-0000-4000-8000-000000000102",
            run_id=run_id,
            tenant_id=DEFAULT_TENANT_ID,
            step_name=StepName.DOCUMENT_PREFLIGHT,
            step_order=STEP_ORDER[StepName.DOCUMENT_PREFLIGHT],
            status=StepStatus.RUNNING,
            started_at="2026-01-01T00:00:01Z",
            result_summary={"text_excerpt": "SECRET_TEXT"},
        )
    )


def test_harness_rejects_non_private_or_unsafe_summary(tmp_path: Path) -> None:
    root = tmp_path / "real_example"
    root.mkdir()

    with pytest.raises(ValueError, match="private"):
        run_real_example_full_run_harness(
            RealExampleFullRunHarnessOptions(
                root=root,
                private_run=False,
                safe_summary=True,
                api_client=_FakeApiClient(),
            )
        )

    with pytest.raises(ValueError, match="safe_summary"):
        run_real_example_full_run_harness(
            RealExampleFullRunHarnessOptions(
                root=root,
                private_run=True,
                safe_summary=False,
                api_client=_FakeApiClient(),
            )
        )


def test_local_reports_are_gitignored_private_artifacts() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    assert ".local_reports/" in (repo_root / ".gitignore").read_text(encoding="utf-8")

    result = subprocess.run(
        ["git", "check-ignore", "--quiet", ".local_reports/slice42_summary.json"],
        cwd=repo_root,
        check=False,
    )

    assert result.returncode == 0


def test_harness_never_emits_private_paths_filenames_or_content(tmp_path: Path) -> None:
    root = tmp_path / "real_example"
    confidential_dir = root / "Nexx Secret Board Pack"
    confidential_dir.mkdir(parents=True)
    (confidential_dir / "secret_pipeline.pdf").write_bytes(
        b"%PDF-1.4\nPRIVATE_CONTENT_SLICE42_REVENUE\n%%EOF"
    )
    output_path = tmp_path / "safe_summary.json"

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(
            root=root,
            output_path=output_path,
            api_client=_FakeApiClient(),
        )
    )

    encoded_summary = json.dumps(summary, sort_keys=True)
    encoded_output = output_path.read_text(encoding="utf-8")
    for forbidden in (
        str(root),
        "Nexx",
        "Secret",
        "secret_pipeline",
        "PRIVATE_CONTENT_SLICE42_REVENUE",
        "safe_summary.json",
    ):
        assert forbidden not in encoded_summary
        assert forbidden not in encoded_output


def test_harness_suppresses_api_parser_diagnostics(tmp_path: Path, capsys: Any) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "private.pdf").write_bytes(b"%PDF-1.4\nsafe\n%%EOF")

    class NoisyApiClient(_FakeApiClient):
        def post(self, *args: Any, **kwargs: Any) -> _FakeResponse:
            print("PRIVATE_PARSER_DIAGNOSTIC")
            return super().post(*args, **kwargs)

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(root=root, api_client=NoisyApiClient())
    )

    captured = capsys.readouterr()
    assert summary["status"] == "succeeded"
    assert "PRIVATE_PARSER_DIAGNOSTIC" not in captured.out
    assert "PRIVATE_PARSER_DIAGNOSTIC" not in captured.err


def test_harness_starts_full_run_with_selected_document_ids_not_folder_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "first-private.pdf").write_bytes(b"%PDF-1.4\nfirst\n%%EOF")
    (root / "second-private.xlsx").write_bytes(b"PK\x03\x04fake workbook")
    client = _FakeApiClient()

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(root=root, api_client=client)
    )

    assert summary["selected_document_count"] == 2
    assert len(client.run_requests) == 1
    assert client.run_requests[0] == {
        "mode": "FULL",
        "source": {
            "type": "deal_documents",
            "document_ids": ["document-1", "document-2"],
        },
    }
    encoded_request = json.dumps(client.run_requests[0])
    assert "folder_path" not in encoded_request
    assert "data_room_root_path" not in encoded_request
    assert str(root) not in encoded_request


def test_harness_selects_only_parsed_uploaded_documents(tmp_path: Path) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "failed.pdf").write_bytes(b"%PDF-1.4\nfailed\n%%EOF")
    (root / "parsed.pdf").write_bytes(b"%PDF-1.4\nparsed\n%%EOF")
    client = _FakeApiClient(
        upload_statuses=[
            (
                201,
                {
                    "document_id": "document-failed",
                    "doc_id": "artifact-failed",
                    "parse_status": "FAILED",
                },
            ),
            (
                201,
                {
                    "document_id": "document-parsed",
                    "doc_id": "artifact-parsed",
                    "parse_status": "PARSED",
                },
            ),
        ]
    )

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(root=root, api_client=client)
    )

    assert summary["uploaded_document_count"] == 1
    assert summary["selected_document_count"] == 1
    assert summary["counts_by_upload_status"] == {
        "uploaded": 1,
        "uploaded_not_parsed": 1,
    }
    assert summary["counts_by_deferred_reason"] == {"upload_parse_status_failed": 1}
    assert client.run_requests[0]["source"]["document_ids"] == ["document-parsed"]


def test_harness_requires_explicit_parsed_upload_status(tmp_path: Path) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "missing-status.pdf").write_bytes(b"%PDF-1.4\nmissing\n%%EOF")
    client = _FakeApiClient(
        upload_statuses=[
            (
                201,
                {
                    "document_id": "document-missing-status",
                    "doc_id": "artifact-missing-status",
                },
            ),
        ]
    )

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(root=root, api_client=client)
    )

    assert summary["status"] == "blocked"
    assert summary["uploaded_document_count"] == 0
    assert summary["selected_document_count"] == 0
    assert summary["counts_by_upload_status"] == {"uploaded_not_parsed": 1}
    assert summary["counts_by_deferred_reason"] == {"upload_parse_status_missing": 1}
    assert summary["blocker"] == {
        "http_status": None,
        "reason_code": "NO_PUBLIC_UPLOADABLE_DOCUMENTS",
        "stage": "upload",
    }
    assert client.run_requests == []


def test_harness_rejects_non_exact_parsed_upload_status(tmp_path: Path) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "lowercase-status.pdf").write_bytes(b"%PDF-1.4\nlowercase\n%%EOF")
    client = _FakeApiClient(
        upload_statuses=[
            (
                201,
                {
                    "document_id": "document-lowercase-status",
                    "doc_id": "artifact-lowercase-status",
                    "parse_status": "parsed",
                },
            ),
        ]
    )

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(root=root, api_client=client)
    )

    assert summary["selected_document_count"] == 0
    assert summary["counts_by_upload_status"] == {"uploaded_not_parsed": 1}
    assert summary["counts_by_deferred_reason"] == {"upload_parse_status_failed": 1}
    assert client.run_requests == []


def test_harness_reports_upload_read_errors_without_private_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    private_file = root / "private-unreadable.pdf"
    private_file.write_bytes(b"%PDF-1.4\nprivate\n%%EOF")
    original_read_bytes = Path.read_bytes

    def read_bytes_or_fail(path: Path) -> bytes:
        if path == private_file:
            raise OSError(f"cannot read {private_file}")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes_or_fail)

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(root=root, api_client=_FakeApiClient())
    )

    encoded = json.dumps(summary, sort_keys=True)
    assert summary["status"] == "blocked"
    assert summary["counts_by_upload_status"] == {"failed": 1}
    assert summary["counts_by_deferred_reason"] == {"upload_read_failed": 1}
    assert str(private_file) not in encoded
    assert "private-unreadable" not in encoded
    assert "cannot read" not in encoded


def test_harness_counts_deferred_files_safely_and_keeps_mp4_unavailable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "uploadable.pdf").write_bytes(b"%PDF-1.4\nsafe\n%%EOF")
    (root / "founder-call.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42private audio")
    (root / "notes.txt").write_text("PRIVATE TXT CONTENT", encoding="utf-8")

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(
            root=root,
            api_client=_FakeApiClient(),
            local_stt_model_configured=False,
        )
    )

    assert summary["uploaded_document_count"] == 1
    assert summary["skipped_file_count"] == 2
    assert summary["counts_by_deferred_reason"]["media_transcription_unavailable"] == 1
    assert summary["counts_by_deferred_reason"]["unsupported_format"] == 1
    assert summary["counts_by_upload_status"] == {"deferred": 2, "uploaded": 1}
    assert "parsed" not in summary["counts_by_deferred_reason"]
    encoded = json.dumps(summary, sort_keys=True)
    assert "founder-call" not in encoded
    assert "PRIVATE TXT CONTENT" not in encoded


def test_harness_reports_full_run_blocker_as_structured_safe_blocker(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "uploadable.pdf").write_bytes(b"%PDF-1.4\nsafe\n%%EOF")
    client = _FakeApiClient(
        run_response={
            "status": "FAILED",
            "block_reason": "NO_ELIGIBLE_EXTRACTION_TASKS",
            "steps": [
                {"step_name": "INGEST_CHECK", "status": "COMPLETED"},
                {
                    "step_name": "METHODOLOGY_EXTRACTION_TASK_PLANNING",
                    "status": "BLOCKED",
                    "error": {"code": "NO_ELIGIBLE_EXTRACTION_TASKS"},
                },
            ],
        }
    )

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(root=root, api_client=client)
    )

    assert summary["status"] == "blocked"
    assert summary["blocker"] == {
        "stage": "full_run",
        "reason_code": "NO_ELIGIBLE_EXTRACTION_TASKS",
        "http_status": 202,
    }
    assert summary["run"]["status"] == "FAILED"
    assert summary["run"]["blocked_step_count"] == 1


def test_harness_hard_timeout_returns_structured_safe_run_timeout(
    tmp_path: Path,
    capsys: Any,
) -> None:
    root = tmp_path / "real_example"
    confidential_dir = root / "Private Target Data Room"
    confidential_dir.mkdir(parents=True)
    (confidential_dir / "secret-board-pack.pdf").write_bytes(
        b"%PDF-1.4\nPRIVATE_CONTENT_SLICE44\n%%EOF"
    )
    output_path = tmp_path / "timeout_summary.json"
    resume_state_output_path = tmp_path / "resume_state.json"

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(
            root=root,
            output_path=output_path,
            resume_state_output_path=resume_state_output_path,
            api_client=_SlowRunApiClient(delay_seconds=0.3),
            run_timeout_seconds=0.1,
            run_poll_interval_seconds=0.001,
        )
    )

    captured = capsys.readouterr()
    assert summary["status"] == "blocked"
    assert summary["blocker"] == {
        "stage": "full_run",
        "reason_code": "RUN_TIMEOUT",
        "http_status": None,
    }
    assert summary["run"]["attempted"] is True
    assert summary["run"]["status"] == "RUNNING"
    assert summary["run"]["run_id"] == "00000000-0000-4000-8000-000000000044"
    assert summary["run"]["last_completed_step"] == "INGEST_CHECK"
    assert summary["run"]["current_step"] == "DOCUMENT_PREFLIGHT"
    assert summary["run"]["failed_step"] is None
    assert summary["run"]["elapsed_seconds_bucket"] == "under_1s"
    assert summary["run"]["step_counts_by_status"] == {"COMPLETED": 1, "RUNNING": 1}
    assert summary["resume"] == {
        "supported": False,
        "reason_code": "RESUME_UNSUPPORTED",
        "uploaded_document_count": 1,
        "selected_document_count": 1,
        "run_id": "00000000-0000-4000-8000-000000000044",
    }

    encoded_summary = json.dumps(summary, sort_keys=True)
    encoded_output = output_path.read_text(encoding="utf-8")
    encoded_resume = resume_state_output_path.read_text(encoding="utf-8")
    for encoded in (encoded_summary, encoded_output, encoded_resume, captured.out, captured.err):
        assert str(root) not in encoded
        assert "Private Target" not in encoded
        assert "secret-board-pack" not in encoded
        assert "PRIVATE_CONTENT_SLICE44" not in encoded
        assert "SECRET_PATH" not in encoded
        assert "SECRET_TEXT" not in encoded
        assert "PRIVATE_API_DIAGNOSTIC_SHOULD_BE_SUPPRESSED" not in encoded


def test_harness_deadline_returns_structured_timeout_during_upload(
    tmp_path: Path,
    capsys: Any,
) -> None:
    root = tmp_path / "real_example"
    confidential_dir = root / "Private Upload Data Room"
    confidential_dir.mkdir(parents=True)
    (confidential_dir / "slow-secret.pdf").write_bytes(b"%PDF-1.4\nPRIVATE_UPLOAD\n%%EOF")

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(
            root=root,
            api_client=_SlowUploadApiClient(delay_seconds=0.1),
            run_timeout_seconds=0.04,
            run_poll_interval_seconds=0.001,
        )
    )

    captured = capsys.readouterr()
    assert summary["status"] == "blocked"
    assert summary["blocker"] == {
        "stage": "upload",
        "reason_code": "UPLOAD_THROUGHPUT_LIMIT",
        "http_status": None,
    }
    assert summary["run"]["attempted"] is False
    assert summary["run"]["elapsed_seconds_bucket"] == "under_1s"
    assert summary["selected_document_count"] == 0

    encoded = json.dumps(summary, sort_keys=True) + captured.out + captured.err
    assert str(root) not in encoded
    assert "slow-secret" not in encoded
    assert "PRIVATE_UPLOAD" not in encoded
    assert "PRIVATE_UPLOAD_DIAGNOSTIC_SHOULD_BE_SUPPRESSED" not in encoded


def test_harness_upload_profile_reports_safe_timing_counts_by_outcome(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    confidential_dir = root / "Private Upload Profile Room"
    confidential_dir.mkdir(parents=True)
    (confidential_dir / "parsed-secret.pdf").write_bytes(b"%PDF-1.4\nPRIVATE_PARSED\n%%EOF")
    (confidential_dir / "failed-secret.pdf").write_bytes(b"%PDF-1.4\nPRIVATE_FAILED\n%%EOF")
    client = _FakeApiClient(
        upload_statuses=[
            (
                201,
                {
                    "document_id": "document-parsed",
                    "doc_id": "artifact-parsed",
                    "parse_status": "PARSED",
                },
            ),
            (
                400,
                {
                    "code": "DOCUMENT_UPLOAD_FAILED",
                    "details": {"private_filename": "failed-secret.pdf"},
                },
            ),
        ]
    )

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(root=root, api_client=client)
    )

    profile = summary["upload_profile"]
    assert profile["attempted_count"] == 2
    assert profile["counts_by_outcome"] == {"failed": 1, "uploaded": 1}
    assert profile["phase_counts_by_elapsed_bucket"]["read"] == {"under_1s": 2}
    assert profile["phase_counts_by_elapsed_bucket"]["upload_api"] == {"under_1s": 2}
    assert profile["observable_slowest_phase"] in {"read", "upload_api"}
    assert profile["phase_observability"]["parse"] == "included_in_upload_api"
    assert profile["phase_observability"]["span_generation"] == "included_in_upload_api"
    assert profile["concurrency"] == {
        "enabled": False,
        "max_workers": 1,
        "reason_code": "CONCURRENCY_DISABLED_BY_DEFAULT",
    }

    encoded = json.dumps(summary, sort_keys=True)
    assert str(root) not in encoded
    assert "Private Upload Profile" not in encoded
    assert "parsed-secret" not in encoded
    assert "failed-secret" not in encoded
    assert "PRIVATE_PARSED" not in encoded
    assert "PRIVATE_FAILED" not in encoded


def test_harness_upload_profile_identifies_upload_api_as_slowest_phase_at_scale(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    confidential_dir = root / "Private Upload Scale Room"
    confidential_dir.mkdir(parents=True)
    for index in range(4):
        (confidential_dir / f"scale-secret-{index}.pdf").write_bytes(
            b"%PDF-1.4\nPRIVATE_SCALE\n%%EOF"
        )

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(
            root=root,
            api_client=_SlowUploadApiClient(delay_seconds=0.02),
            run_timeout_seconds=5,
            run_poll_interval_seconds=0.001,
        )
    )

    profile = summary["upload_profile"]
    assert summary["status"] == "succeeded"
    assert profile["attempted_count"] == 4
    assert profile["counts_by_outcome"] == {"uploaded": 4}
    assert profile["observable_slowest_phase"] == "upload_api"
    assert profile["phase_observability"]["parse"] == "included_in_upload_api"
    assert profile["phase_observability"]["span_generation"] == "included_in_upload_api"
    assert profile["concurrency"] == {
        "enabled": False,
        "max_workers": 1,
        "reason_code": "CONCURRENCY_DISABLED_BY_DEFAULT",
    }

    encoded = json.dumps(summary, sort_keys=True)
    assert str(root) not in encoded
    assert "scale-secret" not in encoded
    assert "PRIVATE_SCALE" not in encoded


def test_harness_upload_timeout_returns_partial_timing_profile(
    tmp_path: Path,
    capsys: Any,
) -> None:
    root = tmp_path / "real_example"
    confidential_dir = root / "Private Slow Upload Room"
    confidential_dir.mkdir(parents=True)
    (confidential_dir / "first-secret.pdf").write_bytes(b"%PDF-1.4\nPRIVATE_FIRST\n%%EOF")
    (confidential_dir / "second-secret.pdf").write_bytes(b"%PDF-1.4\nPRIVATE_SECOND\n%%EOF")

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(
            root=root,
            api_client=_SlowUploadApiClient(delay_seconds=0.05),
            run_timeout_seconds=0.02,
            run_poll_interval_seconds=0.001,
        )
    )

    captured = capsys.readouterr()
    profile = summary["upload_profile"]
    assert summary["status"] == "blocked"
    assert summary["blocker"] == {
        "stage": "upload",
        "reason_code": "UPLOAD_THROUGHPUT_LIMIT",
        "http_status": None,
    }
    assert profile["attempted_count"] == 1
    assert profile["counts_by_outcome"] == {"timeout": 1}
    assert profile["phase_counts_by_elapsed_bucket"]["read"] == {"under_1s": 1}
    assert profile["phase_counts_by_elapsed_bucket"]["upload_api"] == {"under_1s": 1}
    assert profile["partial"] is True
    assert profile["observable_slowest_phase"] == "upload_api"
    assert summary["run"]["attempted"] is False

    encoded = json.dumps(summary, sort_keys=True) + captured.out + captured.err
    assert str(root) not in encoded
    assert "Private Slow Upload" not in encoded
    assert "first-secret" not in encoded
    assert "second-secret" not in encoded
    assert "PRIVATE_FIRST" not in encoded
    assert "PRIVATE_SECOND" not in encoded
    assert "PRIVATE_UPLOAD_DIAGNOSTIC_SHOULD_BE_SUPPRESSED" not in encoded


def test_harness_upload_concurrency_is_disabled_by_default_and_order_is_stable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "a-private.pdf").write_bytes(b"%PDF-1.4\nfirst\n%%EOF")
    (root / "b-private.pdf").write_bytes(b"%PDF-1.4\nsecond\n%%EOF")
    client = _FakeApiClient()

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(root=root, api_client=client)
    )

    assert summary["upload_profile"]["concurrency"] == {
        "enabled": False,
        "max_workers": 1,
        "reason_code": "CONCURRENCY_DISABLED_BY_DEFAULT",
    }
    assert client.run_requests[0]["source"]["document_ids"] == ["document-1", "document-2"]


def test_harness_public_upload_contract_omits_optional_sha_without_folder_bypass(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    confidential_dir = root / "Private Public Contract Room"
    confidential_dir.mkdir(parents=True)
    (confidential_dir / "a-secret.pdf").write_bytes(b"%PDF-1.4\nfirst\n%%EOF")
    (confidential_dir / "b-secret.pdf").write_bytes(b"%PDF-1.4\nsecond\n%%EOF")
    client = _FakeApiClient()

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(root=root, api_client=client)
    )

    assert summary["selected_document_count"] == 2
    assert len(client.uploads) == 2
    assert client.run_requests[0]["source"]["document_ids"] == ["document-1", "document-2"]
    for upload in client.uploads:
        assert upload["params"]["doc_type"] == "DATA_ROOM_FILE"
        assert upload["params"]["source_system"] == "real-example-production-harness"
        assert "sha256" not in upload["params"]
        encoded_upload = json.dumps(upload["params"], sort_keys=True)
        assert "folder_path" not in encoded_upload
        assert "data_room_root_path" not in encoded_upload
        assert str(root) not in encoded_upload


def test_in_process_upload_api_calls_are_not_executed_in_background_threads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "private.pdf").write_bytes(b"%PDF-1.4\nsafe\n%%EOF")

    def fail_if_thread_created(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("upload path must not create background threads")

    monkeypatch.setattr(threading, "Thread", fail_if_thread_created)

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(
            root=root,
            api_client=_FakeApiClient(),
            run_timeout_seconds=5,
        )
    )

    assert summary["status"] == "succeeded"
    assert summary["upload_profile"]["attempted_count"] == 1


def test_harness_rejects_upload_concurrency_until_thread_safety_is_proven(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()

    with pytest.raises(ValueError, match="max_upload_concurrency"):
        run_real_example_full_run_harness(
            RealExampleFullRunHarnessOptions(
                root=root,
                api_client=_FakeApiClient(),
                max_upload_concurrency=2,
            )
        )


def test_expired_deadline_does_not_start_side_effectful_call() -> None:
    called = False

    def side_effectful_call() -> str:
        nonlocal called
        called = True
        return "started"

    attempt = harness_module._call_with_optional_deadline(
        call=side_effectful_call,
        timeout_seconds=0,
        poll_interval_seconds=0.001,
    )

    assert attempt.timed_out is True
    assert attempt.result is None
    assert called is False


def test_process_wrapper_returns_structured_timeout_from_latest_checkpoint(
    tmp_path: Path,
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    output_path = tmp_path / "summary.json"
    checkpoint = {
        "harness": "real_example_production_full_run_private_v1",
        "safe_summary": True,
        "private_run": True,
        "status": "running",
        "mode": "FULL",
        "total_files": 3,
        "uploaded_document_count": 1,
        "selected_document_count": 1,
        "skipped_file_count": 1,
        "counts_by_extension": {".pdf": 3},
        "counts_by_upload_status": {"deferred": 1, "uploaded": 1},
        "counts_by_deferred_reason": {"ocr_required": 1},
        "run": {"attempted": False, "status": None},
        "upload_profile": {
            "enabled": True,
            "partial": False,
            "attempted_count": 1,
            "counts_by_outcome": {"uploaded": 1},
            "phase_counts_by_elapsed_bucket": {
                "read": {"under_1s": 1},
                "upload_api": {"1_to_5s": 1},
            },
            "phase_observability": {
                "read": "observed",
                "upload_api": "observed",
                "parse": "included_in_upload_api",
                "span_generation": "included_in_upload_api",
                "run_start": "not_observed",
            },
            "observable_slowest_phase": "upload_api",
            "concurrency": {
                "enabled": False,
                "max_workers": 1,
                "reason_code": "CONCURRENCY_DISABLED_BY_DEFAULT",
            },
        },
        "resume": {
            "supported": False,
            "reason_code": "RESUME_UNSUPPORTED",
            "uploaded_document_count": 1,
            "selected_document_count": 1,
            "run_id": None,
        },
        "blocker": None,
        "checkpoint": {"stage": "upload"},
    }
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    class NeverFinishesProcess:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.terminated = False

        def start(self) -> None:
            return None

        def join(self, timeout: float | None = None) -> None:
            return None

        def is_alive(self) -> bool:
            return not self.terminated

        def terminate(self) -> None:
            self.terminated = True

    summary = run_real_example_full_run_harness_process(
        RealExampleFullRunHarnessOptions(
            root=tmp_path,
            output_path=output_path,
            checkpoint_output_path=checkpoint_path,
            run_timeout_seconds=0.01,
        ),
        process_factory=NeverFinishesProcess,
    )

    assert summary["status"] == "blocked"
    assert summary["blocker"] == {
        "stage": "upload",
        "reason_code": "UPLOAD_THROUGHPUT_LIMIT",
        "http_status": None,
    }
    assert summary["uploaded_document_count"] == 1
    assert summary["selected_document_count"] == 1
    assert summary["skipped_file_count"] == 1
    assert summary["run"]["attempted"] is False
    assert summary["upload_profile"]["partial"] is True
    assert output_path.read_text(encoding="utf-8")


def test_process_wrapper_timeout_after_run_start_checkpoint_reports_attempted_run(
    tmp_path: Path,
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint = {
        "harness": "real_example_production_full_run_private_v1",
        "safe_summary": True,
        "private_run": True,
        "status": "running",
        "mode": "FULL",
        "total_files": 1,
        "uploaded_document_count": 1,
        "selected_document_count": 1,
        "skipped_file_count": 0,
        "counts_by_extension": {".pdf": 1},
        "counts_by_upload_status": {"uploaded": 1},
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
        "upload_profile": {
            "enabled": True,
            "partial": False,
            "attempted_count": 1,
            "counts_by_outcome": {"uploaded": 1},
            "phase_counts_by_elapsed_bucket": {
                "read": {"under_1s": 1},
                "upload_api": {"under_1s": 1},
            },
            "phase_observability": {
                "read": "observed",
                "upload_api": "observed",
                "parse": "included_in_upload_api",
                "span_generation": "included_in_upload_api",
                "run_start": "not_observed",
            },
            "concurrency": {
                "enabled": False,
                "max_workers": 1,
                "reason_code": "CONCURRENCY_DISABLED_BY_DEFAULT",
            },
        },
        "resume": {
            "supported": False,
            "reason_code": "RESUME_UNSUPPORTED",
            "uploaded_document_count": 1,
            "selected_document_count": 1,
            "run_id": None,
        },
        "blocker": None,
        "checkpoint": {"stage": "run_start"},
    }
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    class NeverFinishesProcess:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.terminated = False

        def start(self) -> None:
            return None

        def join(self, timeout: float | None = None) -> None:
            return None

        def is_alive(self) -> bool:
            return not self.terminated

        def terminate(self) -> None:
            self.terminated = True

    summary = run_real_example_full_run_harness_process(
        RealExampleFullRunHarnessOptions(
            root=tmp_path,
            checkpoint_output_path=checkpoint_path,
            run_timeout_seconds=0.01,
        ),
        process_factory=NeverFinishesProcess,
    )

    assert summary["blocker"] == {
        "stage": "run_start",
        "reason_code": "RUN_TIMEOUT",
        "http_status": None,
    }
    assert summary["run"]["attempted"] is True
    assert summary["run"]["status"] == "TIMEOUT"
    assert summary["run"]["block_reason"] == "RUN_TIMEOUT"


def test_process_checkpoint_is_aggregate_only_and_private_content_safe(
    tmp_path: Path,
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    root = tmp_path / "Private Checkpoint Room"
    root.mkdir()
    private_file = root / "secret-checkpoint.pdf"
    private_file.write_bytes(b"%PDF-1.4\nPRIVATE_CHECKPOINT\n%%EOF")

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(
            root=root,
            api_client=_FakeApiClient(),
            checkpoint_output_path=checkpoint_path,
        )
    )

    encoded = json.dumps(summary, sort_keys=True) + checkpoint_path.read_text(encoding="utf-8")
    assert summary["status"] == "succeeded"
    assert "checkpoint" in checkpoint_path.read_text(encoding="utf-8")
    assert str(root) not in encoded
    assert "Private Checkpoint" not in encoded
    assert "secret-checkpoint" not in encoded
    assert "PRIVATE_CHECKPOINT" not in encoded


def test_process_wrapper_bounded_synthetic_run_succeeds(tmp_path: Path) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "private.pdf").write_bytes(b"%PDF-1.4\nsafe\n%%EOF")

    summary = run_real_example_full_run_harness_process(
        RealExampleFullRunHarnessOptions(
            root=root,
            output_path=tmp_path / "summary.json",
            checkpoint_output_path=tmp_path / "checkpoint.json",
            run_timeout_seconds=30,
        )
    )

    assert summary["status"] in {"succeeded", "blocked"}
    assert summary["upload_profile"]["attempted_count"] >= 1
    assert summary["private_run"] is True
    assert summary["safe_summary"] is True


def test_harness_deadline_bounds_inventory_without_private_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()

    def slow_inventory(path: Path) -> list[Path]:
        assert path == root
        time.sleep(0.3)
        return [root / "secret-inventory.pdf"]

    monkeypatch.setattr(harness_module, "_inventory_files", slow_inventory)

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(
            root=root,
            api_client=_FakeApiClient(),
            run_timeout_seconds=0.1,
            run_poll_interval_seconds=0.001,
        )
    )

    encoded = json.dumps(summary, sort_keys=True)
    assert summary["status"] == "blocked"
    assert summary["blocker"] == {
        "stage": "inventory",
        "reason_code": "RUN_TIMEOUT",
        "http_status": None,
    }
    assert summary["total_files"] == 0
    assert str(root) not in encoded
    assert "secret-inventory" not in encoded


def test_harness_deadline_bounds_file_triage_without_private_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "real_example"
    confidential_dir = root / "Private Triage Data Room"
    confidential_dir.mkdir(parents=True)
    (confidential_dir / "secret-triage.pdf").write_bytes(b"%PDF-1.4\nPRIVATE_TRIAGE\n%%EOF")

    def slow_read_header(path: Path) -> bytes:
        assert path.name == "secret-triage.pdf"
        time.sleep(0.3)
        return b"%PDF-1.4"

    monkeypatch.setattr(harness_module, "_read_header", slow_read_header)

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(
            root=root,
            api_client=_FakeApiClient(),
            run_timeout_seconds=0.1,
            run_poll_interval_seconds=0.001,
        )
    )

    encoded = json.dumps(summary, sort_keys=True)
    assert summary["status"] == "blocked"
    assert summary["blocker"] == {
        "stage": "file_triage",
        "reason_code": "RUN_TIMEOUT",
        "http_status": None,
    }
    assert summary["selected_document_count"] == 0
    assert str(root) not in encoded
    assert "secret-triage" not in encoded
    assert "PRIVATE_TRIAGE" not in encoded


def test_harness_run_timeout_without_snapshot_still_reports_attempted_timeout(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "private.pdf").write_bytes(b"%PDF-1.4\nsafe\n%%EOF")

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(
            root=root,
            api_client=_SlowRunApiClient(delay_seconds=0.3, seed_run_snapshot=False),
            run_timeout_seconds=0.1,
            run_poll_interval_seconds=0.001,
        )
    )

    assert summary["status"] == "blocked"
    assert summary["blocker"] == {
        "stage": "full_run",
        "reason_code": "RUN_TIMEOUT",
        "http_status": None,
    }
    assert summary["run"]["attempted"] is True
    assert summary["run"]["status"] == "TIMEOUT"
    assert summary["run"]["block_reason"] == "RUN_TIMEOUT"
    assert summary["run"]["step_counts_by_status"] == {}


def test_successful_synthetic_mini_data_room_run_produces_safe_aggregate_status(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "mini.pdf").write_bytes(b"%PDF-1.4\nsafe\n%%EOF")
    (root / "mini.docx").write_bytes(b"PK\x03\x04fake docx")
    (root / "demo.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42private audio")
    client = _FakeApiClient(
        run_response={
            "status": "SUCCEEDED",
            "steps": [
                {"step_name": "INGEST_CHECK", "status": "COMPLETED"},
                {"step_name": "DOCUMENT_PREFLIGHT", "status": "COMPLETED"},
            ],
        }
    )

    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(root=root, api_client=client)
    )

    assert summary["harness"] == "real_example_production_full_run_private_v1"
    assert summary["safe_summary"] is True
    assert summary["status"] == "succeeded"
    assert summary["run"] == {
        "attempted": True,
        "status": "SUCCEEDED",
        "step_count": 2,
        "completed_step_count": 2,
        "failed_step_count": 0,
        "blocked_step_count": 0,
        "block_reason": None,
    }
    assert summary["uploaded_document_count"] == 2
    assert summary["selected_document_count"] == 2
    assert summary["skipped_file_count"] == 1
    assert summary["counts_by_deferred_reason"] == {"media_transcription_unavailable": 1}
    encoded = json.dumps(summary, sort_keys=True)
    assert str(root) not in encoded
    assert "demo.mp4" not in encoded

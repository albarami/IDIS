"""Slice 42 tests for the private production-style real_example run harness."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from idis.evaluation.real_example_run_harness import (
    RealExampleFullRunHarnessOptions,
    run_real_example_full_run_harness,
)


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
